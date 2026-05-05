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
import json
import logging
import pathlib
import shutil
import socket
import subprocess
import time
import sys
import os
import smtplib
import ssl
from email.message import EmailMessage

LOGGER = logging.getLogger("monitor_recordings")

# --- Constants ---
DISK_USAGE_THRESHOLD = 45
DISK_WARNING_FLAG = "/tmp/.disk_warning_sent"
DEFAULT_LOG_FILE = "monitor_recordings.log"
DEFAULT_STATE_FILE = "monitor_recordings_state.json"


def setup_logging(logs_dir: str | None) -> pathlib.Path | None:
    handlers = [logging.StreamHandler()]
    log_path = None

    if logs_dir:
        log_dir = pathlib.Path(logs_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / DEFAULT_LOG_FILE
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] %(message)s',
        handlers=handlers,
        force=True,
    )
    return log_path


def state_timestamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def load_state(state_path: pathlib.Path) -> dict:
    if not state_path.exists():
        return {"cameras": {}, "last_daily_summary_date": None}

    try:
        with state_path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to load state file %s: %s", state_path, exc)
        return {"cameras": {}, "last_daily_summary_date": None}

    state.setdefault("cameras", {})
    state.setdefault("last_daily_summary_date", None)
    return state


def save_state(state_path: pathlib.Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    tmp_path.replace(state_path)


def restart_service(unit: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/systemctl", "restart", unit],
        check=False,
        capture_output=True,
        text=True,
    )

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
        LOGGER.warning("Email config incomplete - skipping alert.")
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
        LOGGER.info("Alert email sent: %s", subject)
    except Exception as e:
        LOGGER.error("Failed to send email: %s", e)

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
        LOGGER.warning("event=DISK_USAGE_HIGH path=%s percent_used=%.1f threshold=%s", path, percent_used, threshold)
    else:
        if os.path.exists(DISK_WARNING_FLAG):
            os.remove(DISK_WARNING_FLAG)
            LOGGER.info("event=DISK_USAGE_RECOVERED path=%s percent_used=%.1f", path, percent_used)

# --- File Modification Check ---
def newest_mtime(dir_: pathlib.Path, segment_format: str):
    mts =[f.stat().st_mtime for f in dir_.glob(f"*.{segment_format}")]
    return max(mts) if mts else 0


def compose_summary_body(hostname: str, now_ts: float, down_cameras: list[dict]) -> str:
    lines = [
        "The following cameras are currently down:",
        "",
    ]
    for cam in down_cameras:
        lines.append(
            f"- {cam['station']}: age={cam['age_seconds']:.0f}s, down_since={cam['down_since']}, last_restart={cam['last_restart_at']}"
        )

    lines.extend([
        "",
        f"Host: {hostname}",
        f"Time: {time.ctime(now_ts)}",
    ])
    return "\n".join(lines)

# --- Main Logic ---
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording_dir", required=True)
    p.add_argument("--segment_time", type=int, default=600)
    p.add_argument("--multiplier", type=int, default=2,
                   help="threshold = segment_time × multiplier")
    p.add_argument("--segment_format", default="mkv")
    p.add_argument("--logs_dir", default=None)
    p.add_argument("--state_dir", default=None)
    p.add_argument("--summary_hour", type=int, default=8)
    p.add_argument("--disk_usage_threshold", type=int, default=DISK_USAGE_THRESHOLD)
    args = p.parse_args()

    log_path = setup_logging(args.logs_dir)
    LOGGER.info("Running monitor_recordings check...")
    if log_path is not None:
        LOGGER.info("event=MONITOR_LOGFILE path=%s", log_path)

    check_disk_space(args.recording_dir, threshold=args.disk_usage_threshold)

    thresh = args.segment_time * args.multiplier
    now = time.time()
    now_local = time.localtime(now)
    hostname = socket.gethostname()

    root = pathlib.Path(args.recording_dir)
    if not root.is_dir():
        LOGGER.error("Recording directory not found: %s", root)
        return 1

    state_dir = pathlib.Path(args.state_dir or args.logs_dir or args.recording_dir)
    state_path = state_dir / DEFAULT_STATE_FILE
    state = load_state(state_path)
    current_down_cameras = []

    for station_dir in root.iterdir():
        if not station_dir.is_dir():
            continue

        station = station_dir.name
        last = newest_mtime(station_dir, args.segment_format)
        age = now - last
        cam_state = state["cameras"].setdefault(station, {})
        unit = f"record_camera_{station}.service"
        was_down = cam_state.get("status") == "down"

        if age > thresh:
            restart_result = restart_service(unit)
            restart_ok = restart_result.returncode == 0
            stderr = (restart_result.stderr or "").strip()
            stdout = (restart_result.stdout or "").strip()

            if not was_down:
                down_since = state_timestamp(now)
                cam_state.update({
                    "status": "down",
                    "down_since": down_since,
                    "down_since_epoch": now,
                    "restart_failed_notified": False,
                })
                LOGGER.warning(
                    "event=CAMERA_DOWN station=%s age_seconds=%.0f unit=%s action=restart returncode=%s",
                    station,
                    age,
                    unit,
                    restart_result.returncode,
                )
                send_email(
                    subject=f"[CAMERA DOWN] Auto restart {station}",
                    body=(
                        f"Camera {station} has not produced a new .{args.segment_format} file for {age:.0f} seconds.\n"
                        f"Attempted restart of {unit} with return code {restart_result.returncode}.\n"
                        f"Host: {hostname}\n"
                        f"Time: {time.ctime(now)}"
                    ),
                )
            else:
                LOGGER.warning(
                    "event=CAMERA_STILL_DOWN station=%s age_seconds=%.0f unit=%s action=restart returncode=%s",
                    station,
                    age,
                    unit,
                    restart_result.returncode,
                )

            if not restart_ok and not cam_state.get("restart_failed_notified"):
                cam_state["restart_failed_notified"] = True
                LOGGER.error(
                    "event=CAMERA_DOWN_AUTO_RESTART_FAILED station=%s unit=%s returncode=%s stderr=%s stdout=%s",
                    station,
                    unit,
                    restart_result.returncode,
                    stderr or "-",
                    stdout or "-",
                )
                send_email(
                    subject=f"[CAMERA DOWN] Auto restart failed {station}",
                    body=(
                        f"Camera {station} is down and the automatic restart command failed.\n"
                        f"Service: {unit}\n"
                        f"Return code: {restart_result.returncode}\n"
                        f"stderr: {stderr or '-'}\n"
                        f"stdout: {stdout or '-'}\n"
                        f"Host: {hostname}\n"
                        f"Time: {time.ctime(now)}"
                    ),
                )
            elif was_down and restart_ok and not cam_state.get("restart_failed_notified"):
                cam_state["restart_failed_notified"] = True
                LOGGER.error(
                    "event=CAMERA_DOWN_AUTO_RESTART_FAILED station=%s unit=%s reason=still_down_after_previous_restart age_seconds=%.0f",
                    station,
                    unit,
                    age,
                )
                send_email(
                    subject=f"[CAMERA DOWN] Auto restart failed {station}",
                    body=(
                        f"Camera {station} is still down after a previous automatic restart attempt.\n"
                        f"Current file age: {age:.0f} seconds\n"
                        f"Service: {unit}\n"
                        f"First detected down: {cam_state.get('down_since', 'unknown')}\n"
                        f"Host: {hostname}\n"
                        f"Time: {time.ctime(now)}"
                    ),
                )

            cam_state["last_restart_at"] = state_timestamp(now)
            cam_state["last_restart_returncode"] = restart_result.returncode
            current_down_cameras.append({
                "station": station,
                "age_seconds": age,
                "down_since": cam_state.get("down_since", state_timestamp(now)),
                "last_restart_at": cam_state.get("last_restart_at", "unknown"),
            })
        else:
            if cam_state.get("status") == "down":
                cam_state.update({
                    "status": "healthy",
                    "last_recovered_at": state_timestamp(now),
                    "last_seen_file_at": state_timestamp(last),
                })
                LOGGER.info(
                    "event=CAMERA_RECOVERED station=%s last_file_time=%s age_seconds=%.0f",
                    station,
                    state_timestamp(last),
                    age,
                )
                send_email(
                    subject=f"[CAMERA RECOVERED] {station}",
                    body=(
                        f"Camera {station} is producing files normally again.\n"
                        f"Latest file time: {state_timestamp(last)}\n"
                        f"Host: {hostname}\n"
                        f"Time: {time.ctime(now)}"
                    ),
                )
            else:
                cam_state["status"] = "healthy"
                cam_state["last_seen_file_at"] = state_timestamp(last)

    today = time.strftime("%Y-%m-%d", now_local)
    never_sent = state.get("last_daily_summary_date") is None
    if never_sent or now_local.tm_hour == args.summary_hour:
        if state.get("last_daily_summary_date") != today and current_down_cameras:
            LOGGER.warning(
                "event=CAMERA_DOWN_SUMMARY count=%s summary_hour=%s",
                len(current_down_cameras),
                args.summary_hour,
            )
            send_email(
                subject=f"[CAMERA DOWN SUMMARY] {len(current_down_cameras)} camera(s) down",
                body=compose_summary_body(hostname, now, current_down_cameras),
            )
            state["last_daily_summary_date"] = today
        elif state.get("last_daily_summary_date") != today:
            LOGGER.info("event=CAMERA_DOWN_SUMMARY_SKIPPED reason=no_cameras_down summary_hour=%s", args.summary_hour)
            state["last_daily_summary_date"] = today

    save_state(state_path, state)
    LOGGER.info("event=MONITOR_STATE_SAVED path=%s", state_path)

    LOGGER.info("monitor_recordings check complete.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
