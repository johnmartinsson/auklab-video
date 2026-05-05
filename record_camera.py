#!/usr/bin/env python3
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
import os
import pathlib
import sys


def parse_bool(value: str) -> bool:
    """Parse common true/false string forms for CLI flags."""
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument("--station", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--segment_time", type=int, default=600)
    p.add_argument("--segment_format", default="mkv", choices=["mkv", "mp4"], help="Container format for each segment")
    p.add_argument("--loglevel", default="info")
    p.add_argument("--output_dir", default="/home/recordingpi/cameras")
    p.add_argument("--rtsp_port", type=int, default=554)
    p.add_argument("--rtsp_path", default="/Streaming/Channels/101",
                   help="RTSP path suffix, e.g. /Streaming/Channels/101 or /profile1")
    p.add_argument("--ffmpeg_path", default="ffmpeg")
    p.add_argument("--audio_optional", type=parse_bool, default=True,
                   help="If true, map audio optionally with 0:a:0? so no-audio cameras still record")
    p.add_argument("--copy_timestamps", type=parse_bool, default=True,
                   help="If true, include -copyts/-copytb for legacy timestamp behavior")
    p.add_argument("--core", type=int, default=None,
                   help="Bind this process to a given CPU core (optional)")
    p.add_argument("--logs_dir",   default=None,  # use cwd if None
                   help="Where ffmpeg -report files will be written")
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

    # ------------------------------------------------------------------
    #  Select working directory for ffmpeg -report
    # ------------------------------------------------------------------
    if args.logs_dir:
        os.makedirs(args.logs_dir, exist_ok=True)
        os.chdir(args.logs_dir)                         # <-- cwd for report

    #os.environ["FFREPORT"] = (
    #    f"file={args.station}_%Y%m%dT%H%M%S.log:level=32"
    #) # FFmpeg log file pattern

    # Output directory e.g. /home/recordingpi/cameras/ROST2
    out_dir = pathlib.Path(args.output_dir) / args.station
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build filename pattern with station prefix
    fname_pattern = str(out_dir / (f"{args.station}_%Y%m%dT%H%M%S.mkv"))
    # Add sub-second precision and PID so rapid restart loops never reuse a manifest name.
    session_stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    segment_list = str(out_dir / f"{args.station}_{session_stamp}_{os.getpid()}_manifest.csv")

    rtsp_url = f"rtsp://{args.user}:{args.password}@{args.ip}:{args.rtsp_port}{args.rtsp_path}"

    ffmpeg_cmd = [
        args.ffmpeg_path,

        # ───────── logging ─────────
        "-hide_banner",
        "-loglevel", args.loglevel,
        #"-report",                       # writes ffmpeg-20250528-140000.log in CWD

        # ───────── RTSP robustness ─────────
        # quit if nothing arrives for 30 s → systemd restarts us
        "-timeout", "30000000",
        # reconnect helpers (works for TCP & UDP)
        #"-reconnect", "1",
        #"-reconnect_streamed", "1",
        #"-reconnect_at_eof", "1",

        # ───────── your original options ─────────
        "-rtsp_transport", "tcp",
        "-allowed_media_types", "video+audio",
        "-fflags", "+genpts+igndts+discardcorrupt",
        "-use_wallclock_as_timestamps", "1",
        "-max_delay", "100000",
        "-i", rtsp_url,
        "-map", "0:v",
    ]

    if args.audio_optional:
        ffmpeg_cmd.extend(["-map", "0:a:0?"])
    else:
        ffmpeg_cmd.extend(["-map", "0:a"])

    ffmpeg_cmd.extend(["-c:v", "copy", "-c:a", "copy"])
    if args.copy_timestamps:
        ffmpeg_cmd.extend(["-copyts", "-copytb", "1"])
    ffmpeg_cmd.extend(["-avoid_negative_ts", "disabled"])

    ffmpeg_cmd.extend([
        # ───────── segmentation ─────────
        "-f", "segment", "-reset_timestamps", "1",
        "-segment_time", str(args.segment_time),
        "-segment_time_delta", "0.05",
        "-segment_atclocktime", "1",
        "-segment_list", segment_list,
        "-segment_list_type", "csv",
        "-segment_format", args.segment_format,
        "-strftime", "1",
        fname_pattern,
    ])

    print("[INFO] Launching FFmpeg:", " ".join(ffmpeg_cmd))
    os.execvp(ffmpeg_cmd[0], ffmpeg_cmd)  # Replace our process with ffmpeg


if __name__ == "__main__":
    main()
