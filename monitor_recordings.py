#!/usr/bin/env python3
"""
Restart recorders that have not produced a new file within THRESHOLD seconds.

Usage:
    python3 monitor_recordings.py \
        --recording_dir /home/bsp/auklab-video/recording_directory \
        --segment_time 600
"""

import argparse, pathlib, subprocess, time, sys

def newest_mtime(dir_: pathlib.Path):
    mts = [f.stat().st_mtime for f in dir_.glob("*.mkv")]
    return max(mts) if mts else 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording_dir", required=True)
    p.add_argument("--segment_time", type=int, default=600)
    p.add_argument("--multiplier", type=int, default=2,
                   help="threshold = segment_time × multiplier")
    args = p.parse_args()

    thresh = args.segment_time * args.multiplier
    now = time.time()

    root = pathlib.Path(args.recording_dir)
    for station_dir in root.iterdir():
        if not station_dir.is_dir():
            continue
        last = newest_mtime(station_dir)
        age  = now - last
        if age > thresh:
            unit = f"record_camera_{station_dir.name}.service"
            print(f"[WARN] {station_dir.name} idle for {age:.0f}s → restarting {unit}")
            subprocess.run(["/usr/bin/systemctl", "restart", unit], check=False)

if __name__ == "__main__":
    sys.exit(main())
