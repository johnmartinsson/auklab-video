#!/usr/bin/env python3
"""
Rsync every file in <ready_for_backup> that is NOT yet listed
in <success_log_dir>/YYYY-MM-DD.synced

After a successful rsync run the transferred paths are appended to today’s log.
"""
import argparse, datetime as _dt, json, os, pathlib, subprocess, sys, fcntl

def load_json(p): 
    with open(p) as fh: return json.load(fh)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="backup.config")
    ap.add_argument("--cameras", default="/etc/camera_recorder/cameras.json")  # only to locate ready dir if you prefer
    args = ap.parse_args()
    cfg = load_json(args.config)

    ready   = pathlib.Path(cfg["ready_for_backup_dir"])
    target  = pathlib.Path(cfg["nas_target_dir"])
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

    already = set()
    if today_log.exists():
        already.update(p.strip() for p in today_log.read_text().splitlines())

    to_sync = []
    for f in ready.rglob("*.mkv"):
        rel = f.relative_to(ready)
        if str(rel) not in already:
            to_sync.append(rel)

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

if __name__ == "__main__":
    main()
