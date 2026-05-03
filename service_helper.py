#!/usr/bin/env python3
"""
service_helper.py — Generate and manage all systemd units for the AukLab
video recording pipeline.

This script is the single entry point for setting up, updating, and
operating the full fleet of recording services. It writes generated
unit files into ./services and ./timers (no sudo needed), then
optionally symlinks them into /etc/systemd/system.

Unit types managed
------------------
  record_camera_<STATION>.service   One per camera in cameras.json.
                                    Runs record_camera.py forever under
                                    systemd with Restart=always.
  organize_video.{service,timer}    Moves finished segments into date
                                    folders every segment_time seconds.
  backup_video.{service,timer}      Rsyncs ready segments to the NAS.
  cleanup_video.{service,timer}     Deletes local segments already on NAS.
  monitor_recordings.{service,timer} Watchdog: restarts stalled recorders,
                                    sends camera-down/recovered emails, and
                                    a daily 8AM summary of down cameras.

Actions
-------
  generate          Write all unit files into ./services and ./timers.
                    Safe – no sudo required. Always run this first after
                    changing cameras.json.

  link              Symlink generated units into /etc/systemd/system and
                    run daemon-reload. Requires sudo. Skips units that are
                    already correctly linked.

  deploy            generate + link + enable + start in one shot.
                    Use --station to scope to specific cameras only.
                    Use --new to target cameras added in config but not
                    yet linked/deployed.
                    Use --changed to target cameras whose deployed unit
                    content differs from current config-derived content.
                    Use --force to restart units that are already active
                    (required when picking up config changes).

  start             Start all units, skipping those already active.
                    Auxiliary timers are started in staggered order
                    (organize → backup → cleanup, 2 min apart) to avoid
                    disk contention on the first run.

  stop              Stop all units.
  enable            Enable units to start at boot.
  disable           Disable units from starting at boot.

  status            Print a compact per-unit state table (active/inactive/
                    failed) instead of raw systemctl output.

  prune             Stop, disable, unlink, and delete unit files for
                    cameras that have been removed from cameras.json.
                    Run after removing a camera from the config.

Common workflows
----------------
  # Initial setup on a fresh machine:
  python3 service_helper.py generate
  sudo python3 service_helper.py deploy

  # Add two new cameras (edit cameras.json first):
  python3 service_helper.py generate
  sudo python3 service_helper.py deploy --station NEWCAM1 NEWCAM2

  # Deploy only cameras newly added to cameras.json:
  sudo python3 service_helper.py deploy --new

  # Redeploy only cameras with changed settings (e.g. IP updates):
  sudo python3 service_helper.py deploy --changed

  # Push a config change and reload all running services:
  python3 service_helper.py generate
  sudo python3 service_helper.py deploy --force

  # Push a config change for one camera only:
  sudo python3 service_helper.py deploy --force --station ROST2

  # Remove a camera from cameras.json then clean up:
  sudo python3 service_helper.py prune

  # Check fleet health:
  sudo python3 service_helper.py status

Privilege requirements
----------------------
  generate                  No sudo – writes only inside the repo.
  link, deploy, prune       sudo – writes symlinks into /etc/systemd/system.
  start/stop/enable/disable sudo – calls systemctl.
  status                    sudo recommended (some units may be hidden).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as _mp
import os
import pathlib
import subprocess
import sys
from typing import List, Tuple
from itertools import cycle
import time


# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------
REPO_DIR = pathlib.Path(__file__).resolve().parent       # repo root
CAMERAS_CONFIG_PATH = REPO_DIR / "cameras.json"          # camera fleet config
BACKUP_CONFIG_PATH  = REPO_DIR / "backup.json"           # NAS / housekeeping config
LOCAL_SERVICE_DIR   = REPO_DIR / "services"              # generated .service files
LOCAL_TIMER_DIR     = REPO_DIR / "timers"                # generated .timer files
SYSTEMD_DIR         = pathlib.Path("/etc/systemd/system") # live systemd unit dir

# ---------------------------------------------------------------------------
# Unit file templates
# ---------------------------------------------------------------------------
# All templates use str.format() placeholders filled in by the create_*
# functions below.  The generated files are committed to the repo so they
# can be inspected and diffed before linking.

# One instance per camera.  Runs record_camera.py forever; systemd restarts
# it automatically after 5 s if it crashes (e.g. RTSP timeout).
CAMERA_UNIT_TEMPLATE = """[Unit]
Description=Record camera {station}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
WorkingDirectory={logs_dir}
User=bsp
Group=bsp
ExecStart=/usr/bin/python3 {script_path} \
          --ip {ip} --station {station} --user {user} --password {password} \
          --segment_time {segment_time} --loglevel {loglevel} \
          --segment_format {segment_format} \
          --output_dir {output_dir} --logs_dir {logs_dir} \
          --rtsp_port {rtsp_port} --core {core}

[Install]
WantedBy=multi-user.target
"""

# Shared template for the three one-shot auxiliary jobs:
#   organize_video  – move segments into date folders
#   backup_video    – rsync to NAS
#   cleanup_video   – delete locally once confirmed on NAS
GENERIC_UNIT_TEMPLATE = """[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=bsp
Group=bsp
ExecStart=/usr/bin/python3 {exec_path} --backup_config {backup_config_path} --cameras_config {cameras_config_path}

[Install]
WantedBy=multi-user.target
"""

# Generic timer template used for all three auxiliary jobs.
# OnBootSec is staggered (2/4/6 min) so organize runs before backup
# runs before cleanup, avoiding disk contention on boot.
TIMER_TEMPLATE = """[Unit]
Description=Run {unit_name} every {interval}s

[Timer]
OnBootSec={on_boot}
OnUnitActiveSec={interval}

[Install]
WantedBy=timers.target
"""

# Watchdog service.  Runs every 5 min via its timer.  Checks whether each
# camera directory has a fresh .mkv file; if not, restarts the camera
# service and sends email alerts (down / restart-failed / recovered).
# Also sends a daily summary at summary_hour (default 08:00) if any
# camera is still down.  Email credentials are loaded from
# /etc/monitor_email.conf via EnvironmentFile.
MONITOR_UNIT_TEMPLATE = """[Unit]
Description=Restart dead camera services if no new file appears

[Service]
Type=oneshot
User=bsp
Group=bsp
WorkingDirectory={logs_dir}
EnvironmentFile=/etc/monitor_email.conf
ExecStart=/usr/bin/python3 {script_path} \
          --recording_dir {recording_dir} --segment_time {segment_time} \
          --segment_format {segment_format} --logs_dir {logs_dir}
"""

MONITOR_TIMER_TEMPLATE = """[Unit]
Description=Run monitor_recordings.service every 5 min

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
"""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def load_json(path: pathlib.Path):
    if not path.exists():
        print(f"[ERROR] Config file {path} not found", file=sys.stderr)
        sys.exit(1)
    with open(path) as fh:
        return json.load(fh)


def ensure_dir(path: pathlib.Path):
    path.mkdir(parents=True, exist_ok=True)


def write_file(path: pathlib.Path, content: str):
    ensure_dir(path.parent)
    with open(path, "w") as fh:
        fh.write(content)
    print(f"[GENERATE] {path.relative_to(REPO_DIR)}")


# ---------------------------------------------------------------------------
# Unit generation
# ---------------------------------------------------------------------------

def create_camera_units(config: dict) -> List[Tuple[pathlib.Path, str]]:
    """Return (path, content) for every record_camera_<STATION>.service.

    CPU cores are assigned in round-robin order so each ffmpeg process
    is pinned to its own core when possible.
    """
    cores = list(range(_mp.cpu_count()))
    core_cycle = cycle(cores)
    script_path = str((REPO_DIR / "record_camera.py").resolve())
    defaults = config["defaults"]

    units = []
    for cam in config["cameras"]:
        core = next(core_cycle)
        content = CAMERA_UNIT_TEMPLATE.format(
            station=cam["station"],
            ip=cam["ip"],
            user=defaults["user"],
            password=defaults["password"],
            segment_time=defaults["segment_time"],
            loglevel=defaults["loglevel"],
            segment_format=defaults.get("segment_format", "mkv"),
            output_dir=defaults["output_dir"],
            logs_dir=defaults["logs_dir"],
            rtsp_port=defaults["rtsp_port"],
            script_path=script_path,
            core=core,
        )
        path = LOCAL_SERVICE_DIR / f"record_camera_{cam['station']}.service"
        units.append((path, content))
    return units


def create_aux_units(config: dict) -> List[Tuple[pathlib.Path, str]]:
    """Return (path, content) for organize/backup/cleanup services and timers.

    Timer intervals match segment_time so the pipeline runs at the same
    cadence as the recording segments.  Timers are staggered 2 min apart
    to sequence the pipeline: organize → backup → cleanup.
    """
    defaults = config["defaults"]
    seg = defaults["segment_time"]
    interval = seg

    jobs = [
        ("organize_video", "Organize finished camera segments"),
        ("backup_video", "Rsync camera archive to NAS"),
        ("cleanup_video", "Remove local files already synced to NAS"),
    ]
    units = []
    for idx, (name, desc) in enumerate(jobs, start=1):
        service_path = LOCAL_SERVICE_DIR / f"{name}.service"
        timer_path   = LOCAL_TIMER_DIR   / f"{name}.timer"
        exec_path    = str((REPO_DIR / f"{name}.py").resolve())

        service_content = GENERIC_UNIT_TEMPLATE.format(
            description=desc,
            user=defaults["user"],
            exec_path=exec_path,
            backup_config_path=str(BACKUP_CONFIG_PATH.resolve()),
            cameras_config_path=str(CAMERAS_CONFIG_PATH.resolve()),
        )
        timer_content = TIMER_TEMPLATE.format(
            unit_name=f"{name}.service",
            interval=interval,
            on_boot=f"{idx*2}min"  # spread them 2 min apart (2,4,6 …)
        )
        units.extend([
            (service_path, service_content),
            (timer_path, timer_content),
        ])
    return units

def create_monitor_units(config: dict) -> List[Tuple[pathlib.Path, str]]:
    """Return (path, content) for the monitor service and its 5-min timer."""
    defaults = config["defaults"]
    units = []

    mon_service = LOCAL_SERVICE_DIR / "monitor_recordings.service"
    mon_timer   = LOCAL_TIMER_DIR   / "monitor_recordings.timer"
    script_path = str((REPO_DIR / "monitor_recordings.py").resolve())

    units.append((
        mon_service,
        MONITOR_UNIT_TEMPLATE.format(
            user=defaults["user"],
            script_path=script_path,
            recording_dir=defaults["output_dir"],
            segment_time=defaults["segment_time"],
            segment_format=defaults.get("segment_format", "mkv"),
            logs_dir=defaults["logs_dir"],
        ),
    ))
    units.append((mon_timer, MONITOR_TIMER_TEMPLATE))
    return units


def generate_all() -> List[pathlib.Path]:
    """Write all unit/timer files to ./services and ./timers and return their paths.

    Safe to run without sudo.  Idempotent – re-running overwrites files
    in place with the latest content derived from cameras.json.
    """
    cam_cfg = load_json(CAMERAS_CONFIG_PATH)

    units = create_camera_units(cam_cfg) + create_aux_units(cam_cfg) + create_monitor_units(cam_cfg)
    for path, content in units:
        write_file(path, content)
    return [p for p, _ in units]

# ---------------------------------------------------------------------------
# systemd helpers
# ---------------------------------------------------------------------------

def filter_units_by_station(
    local_paths: List[pathlib.Path], stations: List[str]
) -> List[pathlib.Path]:
    """Filter unit paths to only those matching the given station names.

    Non-camera units (aux timers, monitor) are always kept so that
    infrastructure services are never accidentally excluded from an action.
    If stations is empty, all units are returned unchanged.
    """
    if not stations:
        return local_paths
    keep = []
    for p in local_paths:
        # always keep non-camera units (timers, aux services, monitor)
        if not p.name.startswith("record_camera_"):
            keep.append(p)
            continue
        # record_camera_STATION.service → extract STATION
        station = p.stem.removeprefix("record_camera_")
        if station in stations:
            keep.append(p)
    return keep


def is_unit_active(unit_name: str) -> bool:
    """Return True if the unit is currently active (running or waiting)."""
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit_name],
        check=False,
    )
    return result.returncode == 0


def symlink_units(local_paths: List[pathlib.Path]):
    """Symlink each unit file into /etc/systemd/system, then daemon-reload.

    Already-correct symlinks are reported and skipped.  Stale symlinks
    pointing elsewhere are replaced.  Requires sudo.
    """
    ensure_dir(SYSTEMD_DIR)
    for src in local_paths:
        dest = SYSTEMD_DIR / src.name
        if dest.is_symlink() or dest.exists():
            try:
                if dest.resolve() == src.resolve():
                    print(f"[ALREADY LINKED] {src.name}")
                    continue
                dest.unlink()
            except PermissionError:
                print(f"[WARN] Cannot replace {dest}")
                continue
        try:
            os.symlink(src.resolve(), dest)
            print(f"[LINK] {dest} → {src.relative_to(REPO_DIR)}")
        except PermissionError:
            print(f"[ERROR] Need sudo to create {dest}", file=sys.stderr)
            sys.exit(1)
    subprocess.run(["systemctl", "daemon-reload"], check=False)


def systemctl_cmd(cmd: str, local_paths: List[pathlib.Path]):
    """Run a systemctl command against the given units.

    For 'start', units that are already active are skipped to avoid
    unnecessary restarts.  Auxiliary timers (organize/backup/cleanup)
    are started in pipeline order with a 2-min stagger between them so
    the first organize run completes before backup begins.
    """
    unit_names = [p.name for p in local_paths if p.suffix in {".service", ".timer"}]
    if not unit_names:
        return

    # Fixed pipeline order: organize must finish before backup; backup before cleanup.
    aux_timer_order = [
        "organize_video.timer",
        "backup_video.timer",
        "cleanup_video.timer",
    ]

    if cmd == "start":
        service_units = [n for n in unit_names if n.endswith(".service")]
        for unit in service_units:
            if is_unit_active(unit):
                print(f"[SKIP] {unit} already active")
            else:
                print(f"[START] {unit}")
                subprocess.run(["systemctl", "start", unit], check=False)

        # Start auxiliary timers in the specified order, with 2 min delay between
        for idx, timer in enumerate(aux_timer_order):
            if timer not in unit_names:
                continue
            if is_unit_active(timer):
                print(f"[SKIP] {timer} already active")
            else:
                print(f"[START] {timer}")
                subprocess.run(["systemctl", "start", timer], check=False)
                if idx < len(aux_timer_order) - 1:
                    print("[INFO] Waiting 2 minutes before starting next timer...")
                    time.sleep(120)

        other_timers = [n for n in unit_names if n.endswith(".timer") and n not in aux_timer_order]
        for timer in other_timers:
            if is_unit_active(timer):
                print(f"[SKIP] {timer} already active")
            else:
                print(f"[START] {timer}")
                subprocess.run(["systemctl", "start", timer], check=False)
    else:
        subprocess.run(["systemctl", cmd, *unit_names], check=False)


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def unit_state(unit_name: str) -> str:
    """Return systemctl is-active output: active/inactive/failed/activating/…"""
    r = subprocess.run(
        ["systemctl", "is-active", unit_name],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() or "unknown"


def load_monitor_health() -> dict:
    """Load camera health from monitor_recordings_state.json if available."""
    try:
        cam_cfg = load_json(CAMERAS_CONFIG_PATH)
        logs_dir = cam_cfg["defaults"]["logs_dir"]
        state_path = pathlib.Path(logs_dir) / "monitor_recordings_state.json"
        if not state_path.exists():
            return {}
        with state_path.open() as fh:
            state = json.load(fh)
        return state.get("cameras", {})
    except (KeyError, OSError, json.JSONDecodeError):
        return {}


def status_marker(unit_name: str, state: str) -> str:
    """Map systemd states to a readable marker for the compact status table."""
    if state in {"active", "waiting"}:
        return "✓"
    if state == "activating":
        return "~"
    if unit_name.endswith(".service") and not unit_name.startswith("record_camera_") and state == "inactive":
        return "✓"
    return "✗"


def print_status_summary(local_paths: List[pathlib.Path]):
    """Print systemd state plus monitor health for cameras when available."""
    monitor_health = load_monitor_health()
    camera_services = sorted(
        [p for p in local_paths if p.name.startswith("record_camera_") and p.suffix == ".service"],
        key=lambda p: p.name,
    )
    other_units = sorted(
        [p for p in local_paths if not p.name.startswith("record_camera_")],
        key=lambda p: p.name,
    )

    col = 36
    health_col = 16
    print(f"\n{'UNIT':<{col}} {'SYSTEMD':<12} {'MONITOR':<{health_col}}")
    print("-" * (col + 12 + health_col + 2))
    for p in camera_services:
        state = unit_state(p.name)
        marker = status_marker(p.name, state)
        station = p.stem.removeprefix("record_camera_")
        monitor_state = monitor_health.get(station, {}).get("status", "unknown")
        print(f"  {marker} {p.name:<{col-4}} {state:<12} {monitor_state:<{health_col}}")
    if other_units:
        print()
        for p in other_units:
            state = unit_state(p.name)
            marker = status_marker(p.name, state)
            print(f"  {marker} {p.name:<{col-4}} {state:<12} {'n/a':<{health_col}}")
    print()


# ---------------------------------------------------------------------------
# Prune & deploy
# ---------------------------------------------------------------------------

def prune(stations: List[str]):
    """Remove units for cameras that no longer exist in cameras.json.

    For each stale record_camera_<STATION>.service found in ./services:
      1. systemctl stop
      2. systemctl disable
      3. Remove /etc/systemd/system symlink
      4. Delete the local service file
      5. daemon-reload

    Run with sudo after removing a camera from cameras.json.
    The stations argument is accepted but currently unused – prune always
    operates on all stale units (the diff between disk and config).
    """
    cam_cfg = load_json(CAMERAS_CONFIG_PATH)
    known_stations = {c["station"] for c in cam_cfg["cameras"]}

    stale = []
    for path in LOCAL_SERVICE_DIR.glob("record_camera_*.service"):
        station = path.stem.removeprefix("record_camera_")
        if station not in known_stations:
            stale.append((station, path))

    if not stale:
        print("[PRUNE] Nothing to prune – all units match cameras.json")
        return

    for station, path in sorted(stale):
        unit = path.name
        print(f"[PRUNE] Stale unit: {unit}")
        subprocess.run(["systemctl", "stop", unit], check=False)
        subprocess.run(["systemctl", "disable", unit], check=False)
        symlink = SYSTEMD_DIR / unit
        if symlink.exists() or symlink.is_symlink():
            try:
                symlink.unlink()
                print(f"[PRUNE] Removed symlink {symlink}")
            except PermissionError:
                print(f"[WARN] Could not remove {symlink} – need sudo", file=sys.stderr)
        path.unlink(missing_ok=True)
        print(f"[PRUNE] Deleted {path.relative_to(REPO_DIR)}")

    subprocess.run(["systemctl", "daemon-reload"], check=False)
    print(f"[PRUNE] Done – removed {len(stale)} stale unit(s)")


def is_station_linked(station: str) -> bool:
    """Return True if record_camera_<station>.service is linked from /etc/systemd/system."""
    dest = SYSTEMD_DIR / f"record_camera_{station}.service"
    expected_src = (LOCAL_SERVICE_DIR / f"record_camera_{station}.service").resolve()
    if not dest.is_symlink():
        return False
    try:
        return dest.resolve() == expected_src
    except OSError:
        return False


def compute_new_stations(cam_cfg: dict) -> List[str]:
    """Return stations present in cameras.json but not linked/deployed yet."""
    stations = [cam["station"] for cam in cam_cfg["cameras"]]
    return sorted([s for s in stations if not is_station_linked(s)])


def compute_changed_stations(all_units: List[Tuple[pathlib.Path, str]]) -> List[str]:
    """Return stations whose deployed unit content differs from generated content.

    This compares generated record_camera_<STATION>.service content (derived from
    current cameras.json) against the currently deployed unit file behind
    /etc/systemd/system/record_camera_<STATION>.service.

    Notes:
      - Cameras not yet linked are treated as "new" (handled by --new), not changed.
      - If a linked unit cannot be read, it is treated as changed to be safe.
    """
    expected_by_station = {}
    for path, content in all_units:
        if path.name.startswith("record_camera_") and path.suffix == ".service":
            station = path.stem.removeprefix("record_camera_")
            expected_by_station[station] = content

    changed = []
    for station, expected in sorted(expected_by_station.items()):
        dest = SYSTEMD_DIR / f"record_camera_{station}.service"
        if not dest.is_symlink():
            continue

        try:
            deployed_path = dest.resolve()
            deployed_content = deployed_path.read_text()
        except OSError:
            changed.append(station)
            continue

        if deployed_content != expected:
            changed.append(station)

    return changed


def deploy(
    stations: List[str],
    force: bool = False,
    only_new: bool = False,
    only_changed: bool = False,
):
    """Full deployment pipeline: generate → link → daemon-reload → enable → start.

    Parameters
    ----------
    stations : list of str
        If non-empty, only the named camera services are linked/started.
        Infrastructure units (aux timers, monitor) are always included.
    force : bool
        If True, restart units even if already active.  Use this whenever
        cameras.json or a script has changed and you need the running
        service to pick up the new configuration.
    only_new : bool
        If True, auto-select stations that exist in cameras.json but are not
        yet linked into /etc/systemd/system by this helper.
    only_changed : bool
        If True, auto-select stations where deployed camera unit content
        differs from current config-derived content.

    Examples
    --------
    # First-time setup:
      sudo python3 service_helper.py deploy

    # Add new cameras (edit cameras.json first):
      python3 service_helper.py generate
      sudo python3 service_helper.py deploy --station NEWCAM1 NEWCAM2

    # Reload config for all units after a settings change:
      sudo python3 service_helper.py deploy --force

    # Reload config for one camera only:
      sudo python3 service_helper.py deploy --force --station ROST2

    # Deploy only cameras newly added to cameras.json:
      sudo python3 service_helper.py deploy --new

    # Redeploy only cameras with changed settings (e.g. IP updates):
      sudo python3 service_helper.py deploy --changed
    """
    cam_cfg = load_json(CAMERAS_CONFIG_PATH)
    all_units = create_camera_units(cam_cfg) + create_aux_units(cam_cfg) + create_monitor_units(cam_cfg)

    selectors = int(bool(stations)) + int(only_new) + int(only_changed)
    if selectors > 1:
        print("[ERROR] Use only one selector: --station OR --new OR --changed.", file=sys.stderr)
        sys.exit(1)

    if only_new:
        stations = compute_new_stations(cam_cfg)
        if not stations:
            print("[DEPLOY] No new cameras found (all are already linked).")
            return
        print(f"[DEPLOY] Auto-selected new camera(s): {', '.join(stations)}")

    if only_changed:
        stations = compute_changed_stations(all_units)
        if not stations:
            print("[DEPLOY] No changed cameras found.")
            return
        print(f"[DEPLOY] Auto-selected changed camera(s): {', '.join(stations)}")
        if not force:
            print("[DEPLOY] --changed implies restart of selected camera units; enabling --force.")
            force = True

    if stations:
        known = {c["station"] for c in cam_cfg["cameras"]}
        unknown = set(stations) - known
        if unknown:
            print(f"[ERROR] Unknown station(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"        Known: {', '.join(sorted(known))}", file=sys.stderr)
            sys.exit(1)

    for path, content in all_units:
        write_file(path, content)

    all_paths = [p for p, _ in all_units]
    target_paths = filter_units_by_station(all_paths, stations)

    symlink_units(target_paths)

    unit_names = [p.name for p in target_paths if p.suffix in {".service", ".timer"}]
    subprocess.run(["systemctl", "enable", *unit_names], check=False)

    if force:
        print(f"[DEPLOY] Force-restarting: {', '.join(unit_names)}")
        subprocess.run(["systemctl", "restart", *unit_names], check=False)
    else:
        print(f"[DEPLOY] Starting (skipping already active): {', '.join(unit_names)}")
        systemctl_cmd("start", target_paths)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Manage camera/NAS systemd units")
    parser.add_argument("action", choices=[
        "generate", "link", "start", "stop", "enable", "disable", "status", "deploy", "prune"
    ], help="Action to perform")
    parser.add_argument(
        "--station", metavar="STATION", nargs="+", default=[],
        help="Limit action to these station names (e.g. --station ROST2 TRI6). "
             "Non-camera units (timers, aux services, monitor) are always included.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With deploy: restart units even if already active (picks up config changes).",
    )
    parser.add_argument(
        "--new", action="store_true",
        help="With deploy: target only cameras that are in config but not yet linked/deployed.",
    )
    parser.add_argument(
        "--changed", action="store_true",
        help="With deploy: target only cameras whose deployed unit differs from current config.",
    )
    args = parser.parse_args()

    if (args.new or args.changed) and args.action != "deploy":
        parser.error("--new and --changed can only be used with the deploy action")

    if args.action == "generate":
        generate_all()
        return

    if args.action == "prune":
        prune(args.station)
        return

    if args.action == "deploy":
        deploy(
            args.station,
            force=args.force,
            only_new=args.new,
            only_changed=args.changed,
        )
        return

    # Every other action requires units to exist first
    local_units = list(LOCAL_SERVICE_DIR.glob("*.service")) + \
                  list(LOCAL_TIMER_DIR.glob("*.timer"))
    if not local_units:
        print("[ERROR] No local units found – run 'generate' first", file=sys.stderr)
        sys.exit(1)

    local_units = filter_units_by_station(local_units, args.station)

    if args.action == "link":
        symlink_units(local_units)
    elif args.action == "status":
        print_status_summary(local_units)
    elif args.action in {"start", "stop", "enable", "disable"}:
        systemctl_cmd(args.action, local_units)
    else:
        parser.error("Unknown action")


if __name__ == "__main__":
    main()
