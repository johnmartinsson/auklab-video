#!/usr/bin/env python3
"""Periodic RAM-disk usage logger.

Writes one CSV row per run with current /mnt/ramdisk usage and a rough
camera-capacity estimate. Also appends one JSONL record per run with the
full per-camera breakdown for later analysis.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
from typing import Any


def load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def ensure_csv_header(csv_path: pathlib.Path, fieldnames: list[str]) -> None:
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()


def ramdisk_usage(path: pathlib.Path) -> tuple[int, int, int, float]:
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    used_pct = (used / total * 100.0) if total > 0 else 0.0
    return total, used, free, used_pct


def camera_stats(
    recording_dir: pathlib.Path,
    segment_format: str,
    active_within_seconds: int,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Return (total_station_dirs, active_count, per_camera_list).

    Each per-camera dict has: station, files, size_bytes, active (bool).
    """
    if not recording_dir.is_dir():
        return 0, 0, []

    now = dt.datetime.now(dt.timezone.utc).timestamp()
    per_cam: list[dict[str, Any]] = []
    active = 0

    for station_dir in sorted(recording_dir.iterdir()):
        if not station_dir.is_dir():
            continue
        newest = 0.0
        total_size = 0
        file_count = 0
        for f in station_dir.glob(f"*.{segment_format}"):
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            total_size += st.st_size
            file_count += 1
            if st.st_mtime > newest:
                newest = st.st_mtime
        is_active = newest > 0 and (now - newest) <= active_within_seconds
        if is_active:
            active += 1
        per_cam.append(
            {
                "station": station_dir.name,
                "files": file_count,
                "size_bytes": total_size,
                "active": is_active,
            }
        )

    return len(per_cam), active, per_cam


def append_row(csv_path: pathlib.Path, row: dict[str, Any], fieldnames: list[str]) -> None:
    ensure_csv_header(csv_path, fieldnames)
    with csv_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writerow(row)


def append_jsonl(jsonl_path: pathlib.Path, record: dict[str, Any]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
        fh.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Log RAM-disk usage and camera headroom estimates")
    parser.add_argument("--ramdisk_path", default="/mnt/ramdisk")
    parser.add_argument("--recording_dir", default="/mnt/ramdisk")
    parser.add_argument("--segment_time", type=int, default=600)
    parser.add_argument("--segment_format", default="mkv")
    parser.add_argument("--risk_threshold_pct", type=float, default=85.0)
    parser.add_argument("--output_csv", default="/home/bsp/auklab-video/logs/ramdisk_stats.csv")
    parser.add_argument("--output_jsonl", default=None)
    parser.add_argument("--cameras_config", default=None)
    args = parser.parse_args()

    if args.cameras_config:
        cfg = load_json(pathlib.Path(args.cameras_config))
        defaults = cfg.get("defaults", {})
        args.ramdisk_path = defaults.get("output_dir", args.ramdisk_path)
        args.recording_dir = defaults.get("output_dir", args.recording_dir)
        args.segment_time = int(defaults.get("segment_time", args.segment_time))
        args.segment_format = str(defaults.get("segment_format", args.segment_format))

    now = dt.datetime.now().astimezone()
    ramdisk = pathlib.Path(args.ramdisk_path)
    recording_dir = pathlib.Path(args.recording_dir)
    output_csv = pathlib.Path(args.output_csv)
    output_jsonl = (
        pathlib.Path(args.output_jsonl)
        if args.output_jsonl
        else output_csv.with_name("ramdisk_per_camera_stats.jsonl")
    )

    if not ramdisk.exists():
        raise FileNotFoundError(f"RAM-disk path not found: {ramdisk}")

    total, used, free, used_pct = ramdisk_usage(ramdisk)
    station_dirs, active_cameras, per_cam = camera_stats(
        recording_dir=recording_dir,
        segment_format=args.segment_format,
        active_within_seconds=max(args.segment_time * 2, 300),
    )

    avg_per_active_camera = int(used / active_cameras) if active_cameras > 0 else 0
    safe_limit = int(total * (args.risk_threshold_pct / 100.0))
    safe_headroom = max(0, safe_limit - used)

    if avg_per_active_camera > 0:
        est_additional = int(safe_headroom // avg_per_active_camera)
        projected_plus_one = ((used + avg_per_active_camera) / total * 100.0) if total > 0 else 0.0
    else:
        est_additional = -1
        projected_plus_one = used_pct

    fieldnames = [
        "timestamp",
        "hour",
        "ramdisk_path",
        "ramdisk_total_bytes",
        "ramdisk_used_bytes",
        "ramdisk_free_bytes",
        "ramdisk_used_pct",
        "risk_threshold_pct",
        "safe_headroom_bytes",
        "camera_station_dirs",
        "active_cameras_est",
        "avg_bytes_per_active_camera",
        "est_additional_cameras_safe",
        "projected_used_pct_plus_one_camera",
    ]

    row = {
        "timestamp": now.isoformat(timespec="seconds"),
        "hour": now.strftime("%H"),
        "ramdisk_path": str(ramdisk),
        "ramdisk_total_bytes": total,
        "ramdisk_used_bytes": used,
        "ramdisk_free_bytes": free,
        "ramdisk_used_pct": f"{used_pct:.3f}",
        "risk_threshold_pct": f"{args.risk_threshold_pct:.2f}",
        "safe_headroom_bytes": safe_headroom,
        "camera_station_dirs": station_dirs,
        "active_cameras_est": active_cameras,
        "avg_bytes_per_active_camera": avg_per_active_camera,
        "est_additional_cameras_safe": est_additional,
        "projected_used_pct_plus_one_camera": f"{projected_plus_one:.3f}",
    }

    append_row(output_csv, row, fieldnames)
    append_jsonl(
        output_jsonl,
        {
            "timestamp": row["timestamp"],
            "hour": row["hour"],
            "ramdisk": {
                "path": str(ramdisk),
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_pct": round(used_pct, 3),
                "risk_threshold_pct": round(args.risk_threshold_pct, 2),
                "safe_headroom_bytes": safe_headroom,
            },
            "camera_station_dirs": station_dirs,
            "active_cameras_est": active_cameras,
            "avg_bytes_per_active_camera": avg_per_active_camera,
            "est_additional_cameras_safe": est_additional,
            "projected_used_pct_plus_one_camera": round(projected_plus_one, 3),
            "cameras": per_cam,
        },
    )

    print(
        "[RAMDISK_LOG]"
        f" ts={row['timestamp']}"
        f" used_pct={row['ramdisk_used_pct']}"
        f" active_cameras={active_cameras}"
        f" est_additional={est_additional}"
        f" csv={output_csv}"
        f" jsonl={output_jsonl}"
    )

    if per_cam:
        per_cam_sorted = sorted(per_cam, key=lambda c: c["size_bytes"], reverse=True)
        print(f"  {'station':<12} {'files':>5} {'size_MiB':>10} {'active':>7}")
        print(f"  {'-'*12} {'-'*5} {'-'*10} {'-'*7}")
        for c in per_cam_sorted:
            flag = "yes" if c["active"] else "-"
            print(
                f"  {c['station']:<12} {c['files']:>5}"
                f" {c['size_bytes']/1024/1024:>10.1f} {flag:>7}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
