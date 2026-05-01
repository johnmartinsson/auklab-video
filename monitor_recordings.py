#!/usr/bin/env python3
"""
Restart recorders that have not produced a new file within THRESHOLD seconds.
Also send an alert if disk usage goes above a specified percentage.

Usage:
    python3 monitor_recordings.py \
        --recording_dir /home/bsp/auklab-video/recording_directory \
        --segment_time 600
"""

import argparse
import pathlib
import subprocess
import time
import sys
import os
import smtplib
import ssl
import shutil
import logging
from email.message import EmailMessage

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
)

# --- Constants ---
DISK_USAGE_THRESHOLD = 85
DISK_WARNING_FLAG = "/tmp/.disk_warning_sent"

# --- Email Function ---
def send_email(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", 587))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    rcpt_env = os.getenv("ALERT_TO")
    if rcpt_env:
        rcpt =[addr.strip() for addr in rcpt_env.split(",") if addr.strip()]
    else:
        rcpt = [user]

    if not (host and user and pwd):
        logging.warning("Email config incomplete — skipping alert.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(rcpt)
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.send_message(msg)
        logging.info(f"Alert email sent: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# --- Disk Space Monitor ---
def check_disk_space(path="/", threshold=DISK_USAGE_THRESHOLD):
    usage = shutil.disk_usage(path)
    percent_used = (usage.used / usage.total) * 100

    if percent_used > threshold:
        if not os.path.exists(DISK_WARNING_FLAG):
            subject = f"[DISK] Usage Warning ({percent_used:.1f}%)"
            body = (
                f"Disk usage has exceeded {threshold}%.\n"
                f"Current usage: {percent_used:.1f}%\n"
                f"Host: {os.uname().nodename}\n"
                f"Time: {time.ctime()}"
            )
            send_email(subject=subject, body=body)
            pathlib.Path(DISK_WARNING_FLAG).touch()
        logging.warning(f"Disk usage high: {percent_used:.1f}%")
    else:
        if os.path.exists(DISK_WARNING_FLAG):
            os.remove(DISK_WARNING_FLAG)
            logging.info(f"Disk usage back to normal: {percent_used:.1f}%")

# --- File Modification Check ---
def newest_mtime(dir_: pathlib.Path, segment_format: str):
    mts =[f.stat().st_mtime for f in dir_.glob(f"*.{segment_format}")]
    return max(mts) if mts else 0

# --- Main Logic ---
def main():
    logging.info("Running monitor_recordings check...")

    p = argparse.ArgumentParser()
    p.add_argument("--recording_dir", required=True)
    p.add_argument("--segment_time", type=int, default=600)
    p.add_argument("--multiplier", type=int, default=2,
                   help="threshold = segment_time × multiplier")
    p.add_argument("--segment_format", default="mkv")
    p.add_argument("--disk_usage_threshold", type=int, default=DISK_USAGE_THRESHOLD)
    args = p.parse_args()

    check_disk_space(args.recording_dir, threshold=args.disk_usage_threshold)

    thresh = args.segment_time * args.multiplier
    now = time.time()

    root = pathlib.Path(args.recording_dir)
    if not root.is_dir():
        logging.error(f"Recording directory not found: {root}")
        return 1

    for station_dir in root.iterdir():
        if not station_dir.is_dir():
            continue
            
        last = newest_mtime(station_dir, args.segment_format)
        age = now - last
        
        # Define a unique flag file for this specific camera
        flag_file = pathlib.Path(f"/tmp/.camera_warning_sent_{station_dir.name}")

        if age > thresh:
            unit = f"record_camera_{station_dir.name}.service"
            warn = f"{station_dir.name} idle for {age:.0f}s → restarting {unit}"
            logging.warning(warn)

            # We continue to restart the service every time the script runs (e.g. every 5 mins)
            subprocess.run(["/usr/bin/systemctl", "restart", unit], check=False)
            logging.info(f"Restarted {unit}")

            # Only send the email if we haven't already sent one
            if not flag_file.exists():
                send_email(
                    subject=f"[CAMERA DOWN] Auto-restart {station_dir.name}",
                    body=f"{warn}\nHost: {os.uname().nodename}\nTime: {time.ctime(now)}",
                )
                flag_file.touch() # Create the flag to silence future emails
                
        else:
            # Camera is producing files normally (age <= thresh)
            if flag_file.exists():
                # The camera was down, but has now recovered!
                flag_file.unlink() # Delete the flag file so it can alert again next time
                logging.info(f"{station_dir.name} has recovered. Cleared warning flag.")
                
                # Optional: Send a helpful "All clear" email
                send_email(
                    subject=f"[CAMERA RECOVERED] {station_dir.name} is back online",
                    body=f"Camera {station_dir.name} is producing files normally again.\nHost: {os.uname().nodename}\nTime: {time.ctime(now)}",
                )

    logging.info("monitor_recordings check complete.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
