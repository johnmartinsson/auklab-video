"""
Generate systemd unit files for every camera listed in cameras.json and enable /
start / stop / disable them in bulk.
Requires root privileges to write to /etc/systemd/system and run systemctl.

Usage:
    sudo python3 camera_service.py generate   # create / reload units
    sudo python3 camera_service.py start      # start all camera services
    sudo python3 camera_service.py stop       # stop all camera services
    sudo python3 camera_service.py enable     # enable (start at boot)
    sudo python3 camera_service.py disable    # disable

Each service file is named record_camera_<STATION>.service
"""
import argparse
import json
import multiprocessing
import os
import pathlib
import subprocess
import sys

CONFIG_PATH = pathlib.Path(__file__).with_name("cameras.json")
SERVICE_DIR = pathlib.Path("/etc/systemd/system")
UNIT_TEMPLATE = """[Unit]
Description=Record camera {station}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/usr/bin/python3 {script_path} \
          --ip {ip} --station {station} --user {user} --password {password} \
          --segment_time {segment_time} --loglevel {loglevel} \
          --output_dir {output_dir} --rtsp_port {rtsp_port} --core {core}

[Install]
WantedBy=multi-user.target
"""
def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config file {CONFIG_PATH} not found", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as fh:
        return json.load(fh)

def build_services(config):
    cores = list(range(multiprocessing.cpu_count()))
    core_cycle = (core for core in cores)
    services = []
    for cam in config["cameras"]:
        station = cam["station"]
        ip = cam["ip"]
        core = next(core_cycle)
        unit_content = UNIT_TEMPLATE.format(
            station=station,
            ip=ip,
            user=config["defaults"]["user"],
            password=config["defaults"]["password"],
            segment_time=config["defaults"]["segment_time"],
            loglevel=config["defaults"]["loglevel"],
            output_dir=config["defaults"]["output_dir"],
            rtsp_port=config["defaults"]["rtsp_port"],
            script_path=str(pathlib.Path(__file__).with_name("record_camera.py")),
            core=core,
        )
        unit_path = SERVICE_DIR / f"record_camera_{station}.service"
        services.append((unit_path, unit_content))
    return services

def write_units(services):
    for path, content in services:
        with open(path, "w") as fh:
            fh.write(content)
        print(f"[INFO] Wrote {path}")
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def systemctl_cmd(cmd, services):
    unit_names = [str(p.name) for p, _ in services]
    if not unit_names:
        return
    subprocess.run(["systemctl", cmd, *unit_names], check=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "start", "stop", "enable", "disable", "status"], help="Action to perform on all camera services")
    args = parser.parse_args()

    config = load_config()
    services = build_services(config)

    if args.action == "generate":
        write_units(services)
    elif args.action in {"start", "stop", "enable", "disable", "status"}:
        systemctl_cmd(args.action, services)
    else:
        parser.error("Unknown action")

if __name__ == "__main__":
    main()