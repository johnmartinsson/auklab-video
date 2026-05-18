#!/usr/bin/env python3
"""
Rsync every file in <ready_for_backup> that is NOT yet listed
in <success_log_dir>/YYYY-MM-DD.synced

After a successful rsync run the transferred paths are appended to today’s log.
"""
import argparse, datetime as _dt, fcntl, json, os, pathlib, resource, subprocess, sys, time

def _report_stats(label: str, t0: float, ru0: resource.struct_rusage) -> None:
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    wall = time.perf_counter() - t0
    cpu  = (ru1.ru_utime + ru1.ru_stime) - (ru0.ru_utime + ru0.ru_stime)
    rss  = ru1.ru_maxrss
    rio  = ru1.ru_inblock - ru0.ru_inblock
    wio  = ru1.ru_oublock - ru0.ru_oublock
    print(
        f"[{label}][stats] wall={wall:.1f}s cpu={cpu:.2f}s "
        f"rss={rss}kB reads={rio} writes={wio}"
    )

def load_json(p): 
    with open(p) as fh: return json.load(fh)

def gather_synced(success_dir: pathlib.Path):
    synced = set()
    for log in success_dir.glob("*.synced"):
        synced.update(p.strip() for p in log.read_text().splitlines() if p.strip())
    return synced

def main():
    ap = argparse.ArgumentParser()
    t0  = time.perf_counter()
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    ap.add_argument("--backup_config", default="/home/bsp/Gits/auklab-video/backup.json")
    ap.add_argument("--cameras_config", default="/home/bsp/Gits/auklab-video/cameras.json")
    args = ap.parse_args()
    cfg = load_json(args.backup_config)
    cam_defaults = load_json(args.cameras_config)["defaults"]
    extension = cam_defaults.get("segment_format", "mkv")

    ready   = pathlib.Path(cfg["ready_for_backup_dir"])
    target  = cfg["nas_target_dir"]
    success_dir = pathlib.Path(cfg["success_log_dir"])
    success_dir.mkdir(parents=True, exist_ok=True)
    today_log = success_dir / ( _dt.date.today().isoformat() + ".synced" )

    # -------- simple lock so two timers never collide -------------
    lock_dir = pathlib.Path(cfg["lock_dir"])
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (lock_dir / "backup.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[backup] another instance is running – abort")
        sys.exit(0)

    already = gather_synced(success_dir)

    to_sync = []
    for f in ready.rglob(f"*.{extension}"):
        rel = f.relative_to(ready)
        if str(rel) not in already:
            to_sync.append(rel)
    # Always re-sync manifests: they grow over time and must stay current on NAS.
    for f in ready.rglob("*_manifest.csv"):
        to_sync.append(f.relative_to(ready))

    if not to_sync:
        print("[backup] nothing new to sync")
        return

    rsync_cmd = ["rsync", *cfg["rsync_options"], "--files-from=-", str(ready) + "/", str(target)]
    print("[backup] running:", " ".join(rsync_cmd))
    proc = subprocess.run(rsync_cmd, input="\n".join(map(str,to_sync)).encode(), check=False)
    if proc.returncode == 0:
        with open(today_log, "a") as fh:
            for rel in to_sync:
                fh.write(str(rel) + "\n")
        print(f"[backup] synced {len(to_sync)} file(s)")
    else:
        print("[backup] rsync failed with code", proc.returncode, file=sys.stderr)
        sys.exit(proc.returncode)
    _report_stats("backup", t0, ru0)

if __name__ == "__main__":
    main()
