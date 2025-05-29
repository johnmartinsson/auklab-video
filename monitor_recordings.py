#!/usr/bin/env python3
"""
Restart recorders that have not produced a new file within THRESHOLD seconds.

Usage:
    python3 monitor_recordings.py \
        --recording_dir /home/bsp/auklab-video/recording_directory \
        --segment_time 600
"""

import argparse, pathlib, subprocess, time, sys

import os, smtplib, ssl
from email.message import EmailMessage

def send_email(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", 587))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    rcpt = os.getenv("ALERT_TO", user).split(",")

    if not (host and user and pwd):
        # quietly skip if config is missing
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(rcpt)
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=10) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)

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
            warn = f"{station_dir.name} idle for {age:.0f}s → restarting {unit}"
            print(f"[WARN] {warn}")
            subprocess.run(["/usr/bin/systemctl", "restart", unit], check=False)

            send_email(
                subject=f"[CAMERA] Auto-restart {station_dir.name}",
                body=f"{warn}\nHost: {os.uname().nodename}\nTime: {time.ctime(now)}",
            )

if __name__ == "__main__":
    sys.exit(main())
