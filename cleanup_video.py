#!/usr/bin/env python3
"""
Delete any file that is (a) inside <ready_for_backup> AND
(b) has already been logged as successfully synced.
"""
import argparse
import json
import pathlib
import resource
import time

def load_json(p): 
    with open(p) as fh:
        return json.load(fh)

def gather_synced(success_dir):
    synced = set()
    for log in pathlib.Path(success_dir).glob("*.synced"):
        synced.update(p.strip() for p in log.read_text().splitlines())
    return synced

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

    # Manifests are never deleted locally – they are living files that grow
    # across the season and are always re-synced by backup_video.py.
    print(f"[remove] deleted {removed} staged files")
    # prune empty dirs
    for d in sorted(ready.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
    _report_stats("cleanup", t0, ru0)

if __name__ == "__main__":
    main()
