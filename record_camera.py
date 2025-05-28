"""
record_camera.py – thin wrapper that launches one FFmpeg process to capture a
specific camera. Intended to be run via systemd so that crashes auto‑restart.

Usage (manual test):
    python3 record_camera.py --ip 192.168.1.76 --station ROST2 --core 2

When launched through the generated record_camera_<station>.service unit, all
arguments are filled in automatically.
"""
import argparse
import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument("--station", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--segment_time", type=int, default=600)
    p.add_argument("--loglevel", default="info")
    p.add_argument("--output_dir", default="/home/recordingpi/cameras")
    p.add_argument("--rtsp_port", type=int, default=554)
    p.add_argument("--ffmpeg_path", default="ffmpeg")
    p.add_argument("--core", type=int, default=None,
                   help="Bind this process to a given CPU core (optional)")
    return p.parse_args()


def set_cpu_affinity(core: int):
    """Bind the current process to a single CPU core (Linux only)."""
    try:
        os.sched_setaffinity(0, {core})
    except AttributeError:
        pass  # Not supported on this OS
    except PermissionError:
        print("[WARN] Could not set CPU affinity – needs CAP_SYS_NICE", file=sys.stderr)


def main():
    args = parse_args()

    if args.core is not None:
        set_cpu_affinity(args.core)

    # Output directory e.g. /home/recordingpi/cameras/ROST2
    out_dir = pathlib.Path(args.output_dir) / args.station
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build filename pattern with station prefix
    fname_pattern = str(out_dir / (f"{args.station}_%Y%m%dT%H%M%S.mkv"))

    rtsp_url = f"rtsp://{args.user}:{args.password}@{args.ip}:{args.rtsp_port}/Streaming/Channels/101"

    ffmpeg_cmd = [
        args.ffmpeg_path,

        # ───────── logging ─────────
        "-hide_banner",
        "-loglevel", args.loglevel,
        "-report",                       # writes ffmpeg-20250528-140000.log in CWD

        # ───────── RTSP robustness ─────────
        # quit if nothing arrives for 30 s → systemd restarts us
        "-rw_timeout", "30000000",
        # reconnect helpers (works for TCP & UDP)
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_at_eof", "1",

        # ───────── your original options ─────────
        "-rtsp_transport", "tcp",
        "-allowed_media_types", "video+audio",
        "-use_wallclock_as_timestamps", "1",
        "-max_delay", "100000",
        "-i", rtsp_url,
        "-map", "0:v", "-map", "0:a",
        "-c:v", "copy", "-c:a", "copy",

        # ───────── segmentation ─────────
        "-f", "segment", "-reset_timestamps", "1",
        "-segment_time", str(args.segment_time),
        "-segment_atclocktime", "1",
        "-segment_format", "mkv",
        "-strftime", "1",
        fname_pattern,
    ]

    print("[INFO] Launching FFmpeg:", " ".join(ffmpeg_cmd))
    os.execvp(ffmpeg_cmd[0], ffmpeg_cmd)  # Replace our process with ffmpeg


if __name__ == "__main__":
    main()