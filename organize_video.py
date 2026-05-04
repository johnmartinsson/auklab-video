#!/usr/bin/env python3
"""
Move finished segments from <output_dir>/<station>/FILE.<ext>
               to  <ready_for_backup>/<station>/<YYYY-MM-DD>/FILE.<ext>

A file is "finished" when mtime is older than 2xsegment_time seconds.
"""
import argparse, json, os, pathlib, shutil, sys, time, datetime as _dt
import re

def load_json(path):
    with open(path) as fh: return json.load(fh)

def extract_date_from_filename(filename, station, extension):
    # Pattern: <station>_YYYYMMDDTHHMMSS.<ext>
    pattern = rf"^{re.escape(station)}_(\d{{8}})T\d{{6}}\.{re.escape(extension)}$"
    m = re.match(pattern, filename)
    if m:
        return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}"
    return None

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backup_config",  default="/home/bsp/Gits/auklab-video/backup.json")
    p.add_argument("--cameras_config", default="/home/bsp/Gits/auklab-video/cameras.json")
    args = p.parse_args()

    cfg      = load_json(args.backup_config)
    cam_cfg  = load_json(args.cameras_config)["defaults"]

    out_root = pathlib.Path(cam_cfg["output_dir"])
    ready    = pathlib.Path(cfg["ready_for_backup_dir"])
    ready.mkdir(parents=True, exist_ok=True)
    thresh   = cam_cfg["segment_time"] + 60
    extension = cam_cfg.get("segment_format", "mkv")

    now = time.time()
    moved = 0
    skipped_missing = 0
    for station_dir in out_root.iterdir():
        if not station_dir.is_dir(): continue
        station = station_dir.name
        for f in station_dir.glob(f"*.{extension}"):
            try:
                st = f.stat()
            except FileNotFoundError:
                # Another process may remove a segment between glob and stat.
                skipped_missing += 1
                continue

            if (now - st.st_mtime) < thresh:       # still being written
                continue
            date = extract_date_from_filename(f.name, station, extension)
            if not date:
                print(f"[organize][WARN] Could not extract date from filename '{f.name}', using mtime instead.", file=sys.stderr)
                date = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            dest_dir = ready / station / date
            dest_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(f), dest_dir / f.name)
                moved += 1
            except FileNotFoundError:
                # Source vanished after scan (e.g., concurrent cleanup/source prune).
                skipped_missing += 1
                continue

        # Keep all manifests per station at the station level (not per date).
        # Manifests are now session-scoped so recorder restarts do not overwrite history.
        station_ready = ready / station
        manifest_sources = sorted(station_dir.glob("*_manifest.csv"))
        for manifest_src in manifest_sources:
            station_ready.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_src, station_ready / manifest_src.name)
    print(f"[organize] moved {moved} video file(s), skipped_missing={skipped_missing} → {ready}")
    sys.exit(0)

if __name__ == "__main__":
    main()
