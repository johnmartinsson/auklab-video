#!/usr/bin/env python3
"""
Move finished segments from <output_dir>/<station>/FILE.mkv
               to  <ready_for_backup>/<station>/<YYYY-MM-DD>/FILE.mkv

A file is “finished” when mtime is older than 2×segment_time seconds.
"""
import argparse, json, os, pathlib, shutil, sys, time, datetime as _dt
import re

def load_json(path):
    with open(path) as fh: return json.load(fh)

def extract_date_from_filename(filename, station):
    # Pattern: <station>_YYYYMMDDTHHMMSS.mkv
    pattern = rf"^{re.escape(station)}_(\d{{8}})T\d{{6}}\.mkv$"
    m = re.match(pattern, filename)
    if m:
        return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}"
    return None

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config",  default="/home/bsp/Gits/auklab-video/backup.json")
    p.add_argument("--cameras", default="/home/bsp/Gits/auklab-video/cameras.json")
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
            date = extract_date_from_filename(f.name, station)
            if not date:
                print(f"[organize][WARN] Could not extract date from filename '{f.name}', using mtime instead.", file=sys.stderr)
                date = _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
            dest_dir = ready / station / date
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), dest_dir / f.name)
            moved += 1
    print(f"[organize] moved {moved} file(s) → {ready}")
    sys.exit(0)

if __name__ == "__main__":
    main()
