#!/usr/bin/env python3
"""Summarize RAM-disk usage by hour of day.

Reads the CSV written by log_ramdisk_stats.py and prints a per-hour
min/avg/max table covering the full date range in the file.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
import pathlib
from collections import defaultdict
from typing import Any


def parse_logger_csv(csv_path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            ts = dt.datetime.fromisoformat(r["timestamp"])
            rows.append(
                {
                    "timestamp": ts,
                    "hour": ts.strftime("%H"),
                    "used_bytes": int(r["ramdisk_used_bytes"]),
                    "total_bytes": int(r["ramdisk_total_bytes"]),
                    "used_pct": float(r["ramdisk_used_pct"]),
                    "active_cameras_est": int(r.get("active_cameras_est") or 0),
                    "est_additional_cameras_safe": int(r.get("est_additional_cameras_safe") or -1),
                }
            )
    return rows


def summarize_by_hour(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[r["hour"]].append(r)

    out: list[dict[str, Any]] = []
    for hour in sorted(grouped.keys()):
        bucket = grouped[hour]
        used_vals = [r["used_bytes"] for r in bucket]
        pct_vals = [r["used_pct"] for r in bucket if r["used_pct"] >= 0]
        cam_vals = [r["active_cameras_est"] for r in bucket if r["active_cameras_est"] >= 0]
        add_vals = [r["est_additional_cameras_safe"] for r in bucket if r["est_additional_cameras_safe"] >= 0]

        out.append(
            {
                "hour": hour,
                "samples": len(bucket),
                "used_min_bytes": min(used_vals),
                "used_avg_bytes": int(statistics.mean(used_vals)),
                "used_max_bytes": max(used_vals),
                "used_min_pct": min(pct_vals) if pct_vals else None,
                "used_avg_pct": statistics.mean(pct_vals) if pct_vals else None,
                "used_max_pct": max(pct_vals) if pct_vals else None,
                "active_cameras_avg": statistics.mean(cam_vals) if cam_vals else None,
                "est_additional_avg": statistics.mean(add_vals) if add_vals else None,
            }
        )
    return out


def format_bytes(n: int) -> str:
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for unit in units:
        if x < step or unit == units[-1]:
            return f"{x:.2f} {unit}"
        x /= step
    return f"{x:.2f} TiB"


def print_table(summary: list[dict[str, Any]], date_range: str | None = None) -> None:
    if date_range:
        print(f"Period : {date_range}")
    print(
        f"{'hour':>4}  {'samples':>7}  "
        f"{'min_used':>10}  {'avg_used':>10}  {'max_used':>10}  "
        f"{'min%':>6}  {'avg%':>6}  {'max%':>6}  "
        f"{'avg_cams':>8}  {'avg_headroom':>12}"
    )
    print("-" * 98)
    for s in summary:
        min_pct  = f"{s['used_min_pct']:.2f}"  if s["used_min_pct"]  is not None else "n/a"
        avg_pct  = f"{s['used_avg_pct']:.2f}"  if s["used_avg_pct"]  is not None else "n/a"
        max_pct  = f"{s['used_max_pct']:.2f}"  if s["used_max_pct"]  is not None else "n/a"
        cams     = f"{s['active_cameras_avg']:.1f}" if s["active_cameras_avg"] is not None else "n/a"
        headroom = f"{s['est_additional_avg']:.1f}" if s["est_additional_avg"]  is not None else "n/a"
        print(
            f"{s['hour']:>4}  {s['samples']:>7}  "
            f"{format_bytes(s['used_min_bytes']):>10}  "
            f"{format_bytes(s['used_avg_bytes']):>10}  "
            f"{format_bytes(s['used_max_bytes']):>10}  "
            f"{min_pct:>6}  {avg_pct:>6}  {max_pct:>6}  "
            f"{cams:>8}  {headroom:>12}"
        )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Summarize RAM-disk usage by hour of day from log_ramdisk_stats CSV"
    )
    p.add_argument("log_csv", help="CSV file written by log_ramdisk_stats.py")
    p.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include rows on or after this date",
    )
    args = p.parse_args()

    rows = parse_logger_csv(pathlib.Path(args.log_csv))
    if not rows:
        print("No rows found in CSV.")
        return 1

    if args.since:
        try:
            since = dt.date.fromisoformat(args.since)
        except ValueError:
            raise SystemExit(f"Invalid date '{args.since}'. Use YYYY-MM-DD.")
        rows = [r for r in rows if r["timestamp"].date() >= since]
        if not rows:
            print(f"No rows on or after {since}.")
            return 1

    first = min(r["timestamp"].date() for r in rows)
    last  = max(r["timestamp"].date() for r in rows)
    date_range = f"{first.isoformat()} → {last.isoformat()}"

    summary = summarize_by_hour(rows)
    print_table(summary, date_range=date_range)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
