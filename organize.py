#!/usr/bin/env python3
"""
Move finished segments from <output_dir>/<station>/FILE.mkv
               to  <ready_for_backup>/<station>/<YYYY-MM-DD>/FILE.mkv

A file is “finished” when mtime is older than 2×segment_time seconds.
"""
import argparse, json, os, pathlib, shutil, sys, time, datetime as _dt

def load_json(path):
    with open(path) as fh: return json.load(fh)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config",  default="backup.config")
    p.add_argument("--cameras", default="/etc/camera_recorder/cameras.json")
    args = p.parse_args()

    cfg      = load_json(args.config)
    cam_cfg  = load_json(args.cameras)["defaults"]

    out_root = pathlib.Path(cam_cfg["output_dir"])
    ready    = pathlib.Path(cfg["ready_for_backup_dir"])
    ready.mkdir(parents=True, exist_ok=True)
    thresh   = cam_cfg["segment_time"] * 2

    now = time.time()
    moved = 0
    for station_dir in out_root.iterdir():
        if not station_dir.is_dir(): continue
        station = station_dir.name
        for f in station_dir.glob("*.mkv"):
            if (now - f.stat().st_mtime) < thresh:       # still being written
                continue
            date = _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
            dest_dir = ready / station / date
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), dest_dir / f.name)
            moved += 1
    print(f"[organize] moved {moved} file(s) → {ready}")
    sys.exit(0)

if __name__ == "__main__":
    main()
