#!/usr/bin/env python3
"""
Delete any file that is (a) inside <ready_for_backup> AND
(b) has already been logged as successfully synced.
"""
import argparse, json, pathlib
import re
import time

def load_json(p): 
    with open(p) as fh: return json.load(fh)

def extract_date_from_filename(filename, station, extension):
    pattern = rf"^{re.escape(station)}_(\d{{8}})T\d{{6}}\.{re.escape(extension)}$"
    m = re.match(pattern, filename)
    if not m:
        return None
    d = m.group(1)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

def gather_synced(success_dir):
    synced = set()
    for log in pathlib.Path(success_dir).glob("*.synced"):
        synced.update(p.strip() for p in log.read_text().splitlines())
    return synced

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup_config", default="/home/bsp/Gits/auklab-video/backup.json")
    ap.add_argument("--cameras_config", default="/home/bsp/Gits/auklab-video/cameras.json")
    args = ap.parse_args()
    cfg = load_json(args.backup_config)
    cam_defaults = load_json(args.cameras_config)["defaults"]
    extension = cam_defaults.get("segment_format", "mkv")
    out_root = pathlib.Path(cam_defaults["output_dir"])
    thresh = cam_defaults["segment_time"] * 2

    ready = pathlib.Path(cfg["ready_for_backup_dir"])
    synced = gather_synced(cfg["success_log_dir"])
    removed = 0
    for f in ready.rglob(f"*.{extension}"):
        rel = str(f.relative_to(ready))
        if rel in synced:
            try:
                f.unlink()
                removed +=1
            except FileNotFoundError:
                pass

    # Safety cleanup in source tree: if a stale source segment still exists
    # but the same segment is already present in staging, remove the source copy.
    now = time.time()
    source_removed = 0
    if out_root.is_dir():
        for station_dir in out_root.iterdir():
            if not station_dir.is_dir():
                continue
            station = station_dir.name
            for f in station_dir.glob(f"*.{extension}"):
                if (now - f.stat().st_mtime) < thresh:
                    continue
                date = extract_date_from_filename(f.name, station, extension)
                if not date:
                    continue
                rel = pathlib.Path(station) / date / f.name
                if (ready / rel).exists() or str(rel) in synced:
                    try:
                        f.unlink()
                        source_removed += 1
                    except FileNotFoundError:
                        pass
    # Manifests are never deleted locally – they are living files that grow
    # across the season and are always re-synced by backup_video.py.
    print(f"[remove] deleted {removed} staged and {source_removed} source files")
    # prune empty dirs
    for d in sorted(ready.rglob("*"), reverse=True):
        if d.is_dir():
            try: d.rmdir()
            except OSError: pass

if __name__ == "__main__":
    main()
