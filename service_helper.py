#!/usr/bin/env python3
"""
Generate ALL systemd unit & timer files for this repository **into the local
./services and ./timers folders** (no root needed) and optionally symlink them
into /etc/systemd/system so that `systemctl` can see them.

The script now manages two kinds of units:
• One `record_camera_<STATION>.service` per camera in *cameras.json*
• Three auxiliary jobs – organize / backup / cleanup – **plus** their timers

Usage examples
--------------
1. Generate / refresh units inside the repo (safe – no sudo):

   $ python3 camera_service.py generate

2. Inspect what was generated, then link everything into the system:

   $ sudo python3 camera_service.py link

3. Afterwards you can manage the whole fleet in one go, e.g.:

   $ sudo python3 camera_service.py start     # start every unit
   $ sudo python3 camera_service.py status    # show overall state

Root privileges are **only** required for the *link*, *start*, *stop*, *enable*,
*disable*, and *status* sub‑commands – because these interact with
`/etc/systemd/system` or `systemctl`.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as _mp
import os
import pathlib
import subprocess
import sys
import textwrap
from typing import List, Tuple
from itertools import cycle
import time


# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------
REPO_DIR = pathlib.Path(__file__).resolve().parent
CAMERAS_CONFIG_PATH = REPO_DIR / "cameras.json"
BACKUP_CONFIG_PATH = REPO_DIR / "backup.json"
LOCAL_SERVICE_DIR = REPO_DIR / "services"
LOCAL_TIMER_DIR = REPO_DIR / "timers"
SYSTEMD_DIR = pathlib.Path("/etc/systemd/system")

# Template for camera capture services
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
          --output_dir {output_dir} --logs_dir {logs_dir} \
          --rtsp_port {rtsp_port} --core {core}

[Install]
WantedBy=multi-user.target
"""

# Template for organize / backup / cleanup services
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

# Template for simple recurring timers
TIMER_TEMPLATE = """[Unit]
Description=Run {unit_name} every {interval}s

[Timer]
OnBootSec={on_boot}
OnUnitActiveSec={interval}

[Install]
WantedBy=timers.target
"""

# -------------------------------------------------------------------
# Watchdog for "freshness" of recordings
# -------------------------------------------------------------------
MONITOR_UNIT_TEMPLATE = """[Unit]
Description=Restart dead camera services if no new file appears

[Service]
Type=oneshot
EnvironmentFile=/etc/monitor_email.conf
ExecStart=/usr/bin/python3 {script_path} \
          --recording_dir {recording_dir} --segment_time {segment_time}
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
# Helpers
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


def create_camera_units(config: dict) -> List[Tuple[pathlib.Path, str]]:
    """Return list of (unit_path, content) for every camera."""
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
    """organize / backup / cleanup services + timers derived from config."""
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
        ),
    ))
    units.append((mon_timer, MONITOR_TIMER_TEMPLATE))
    return units


def generate_all() -> List[pathlib.Path]:
    """Generate every unit/timer file and return the list of local paths."""
    cam_cfg = load_json(CAMERAS_CONFIG_PATH)

    units = create_camera_units(cam_cfg) + create_aux_units(cam_cfg) + create_monitor_units(cam_cfg)
    for path, content in units:
        write_file(path, content)
    return [p for p, _ in units]

# ---------------------------------------------------------------------------
# systemd helper
# ---------------------------------------------------------------------------

def symlink_units(local_paths: List[pathlib.Path]):
    ensure_dir(SYSTEMD_DIR)
    for src in local_paths:
        dest = SYSTEMD_DIR / src.name
        if dest.is_symlink() or dest.exists():
            try:
                if dest.resolve() == src.resolve():
                    continue  # already linked to right place
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
    unit_names = [p.name for p in local_paths if p.suffix == ".service" or p.suffix == ".timer"]
    if not unit_names:
        return
    
    # Hardcoded order for auxiliary timers
    aux_timer_order = [
        "organize_video.timer",
        "backup_video.timer",
        "cleanup_video.timer",
    ]

    if cmd == "start":
        # Start all services immediately
        service_units = [n for n in unit_names if n.endswith(".service") and not n.endswith(".timer")]
        if service_units:
            print(f"[INFO] Starting {len(service_units)} services...")
            # Start all services first
            subprocess.run(["systemctl", cmd, *service_units], check=False)

        # Start auxiliary timers in the specified order, with 2 min delay between
        for idx, timer in enumerate(aux_timer_order):
            if timer in unit_names:
                print(f"[INFO] Starting {timer}...")
                subprocess.run(["systemctl", cmd, timer], check=False)
                if idx < len(aux_timer_order) - 1:
                    print(f"[INFO] Waiting 2 minutes before starting next timer...")
                    time.sleep(120)
        # Start any other timers not in the list
        other_timers = [n for n in unit_names if n.endswith(".timer") and n not in aux_timer_order]
        if other_timers:
            subprocess.run(["systemctl", cmd, *other_timers], check=False)
    else:
        subprocess.run(["systemctl", cmd, *unit_names], check=False)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Manage camera/NAS systemd units")
    parser.add_argument("action", choices=[
        "generate", "link", "start", "stop", "enable", "disable", "status"
    ], help="Action to perform")
    args = parser.parse_args()

    if args.action == "generate":
        generate_all()
        return

    # Every other action requires units to exist first
    local_units = list(LOCAL_SERVICE_DIR.glob("*.service")) + \
                  list(LOCAL_TIMER_DIR.glob("*.timer"))
    if not local_units:
        print("[ERROR] No local units found – run 'generate' first", file=sys.stderr)
        sys.exit(1)

    if args.action == "link":
        symlink_units(local_units)
    elif args.action in {"start", "stop", "enable", "disable", "status"}:
        systemctl_cmd(args.action, local_units)
    else:
        parser.error("Unknown action")


if __name__ == "__main__":
    main()
