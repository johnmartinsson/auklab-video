#!/usr/bin/env python3
import json
import subprocess
import re
import argparse

def get_rtsp_audio_stream_info(rtsp_url, ffprobe_path="ffprobe", timeout=15):
    """
    Probes an RTSP stream and returns audio stream information.
    Returns a dictionary for the audio stream, or None if not found or error.
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "a", # Select only audio streams
        "-timeout", str(timeout * 1000000), # RTSP timeout in microseconds
        rtsp_url
    ]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout + 5)
        if process.returncode != 0:
            # print(f"  Error running ffprobe for {rtsp_url}: {process.stderr.strip()}")
            return None, process.stderr.strip()

        data = json.loads(process.stdout)
        if data and "streams" in data and len(data["streams"]) > 0:
            # Assuming the first audio stream is the one we care about
            audio_stream = data["streams"][0]
            # Ensure essential keys are present, providing defaults if not
            return {
                "codec_name": audio_stream.get("codec_name"),
                "sample_rate": int(audio_stream.get("sample_rate", 0)),
                "channels": audio_stream.get("channels"), # This is usually an int
                "channel_layout": audio_stream.get("channel_layout") # This is usually a string 'mono' or 'stereo'
            }, None
        else:
            return None, "No audio streams found in ffprobe output."
    except subprocess.TimeoutExpired:
        return None, f"ffprobe command timed out after {timeout+5} seconds."
    except json.JSONDecodeError:
        return None, "Failed to decode ffprobe JSON output."
    except Exception as e:
        return None, f"An unexpected error occurred with ffprobe: {e}"


def main():
    parser = argparse.ArgumentParser(description="Check camera RTSP audio stream configurations.")
    parser.add_argument(
        "--config",
        default="cameras.json",
        help="Path to the camera configuration JSON file (default: cameras.json)"
    )
    args = parser.parse_args()

    try:
        with open(args.config, 'r') as f:
            config_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{args.config}' not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{args.config}'.")
        return

    defaults = config_data.get("defaults", {})
    cameras = config_data.get("cameras", [])

    if not cameras:
        print("No cameras found in the configuration file.")
        return

    print(f"Checking {len(cameras)} camera(s) based on '{args.config}':\n")

    overall_ok = True

    for cam_config in cameras:
        station = cam_config.get("station", "UnknownStation")
        ip = cam_config.get("ip")
        
        if not ip:
            print(f"[WARN] Skipping camera entry for '{station}' due to missing IP.")
            continue

        # Merge defaults with camera-specific settings
        user = cam_config.get("user", defaults.get("user", "admin"))
        password = cam_config.get("password", defaults.get("password", ""))
        rtsp_port = cam_config.get("rtsp_port", defaults.get("rtsp_port", 554))
        ffprobe_path = cam_config.get("ffprobe_path", defaults.get("ffprobe_path", "ffprobe"))

        # Determine expected audio settings, camera-specific overrides defaults
        default_expected_audio = defaults.get("expected_audio", {})
        camera_expected_audio_override = cam_config.get("expected_audio", {})
        expected_audio = {**default_expected_audio, **camera_expected_audio_override}

        if not expected_audio:
            print(f"[{station} ({ip})] SKIPPING: No 'expected_audio' configuration found.")
            continue

        rtsp_url = f"rtsp://{user}:{password}@{ip}:{rtsp_port}/Streaming/Channels/101" # Assuming main stream

        print(f"[{station} ({ip})]")
        print(f"  Expected: Codec: {expected_audio.get('codec_name')}, Rate: {expected_audio.get('sample_rate')} Hz, Channels: {expected_audio.get('channels_text')}")

        actual_audio_info, error_msg = get_rtsp_audio_stream_info(rtsp_url, ffprobe_path)

        if error_msg:
            print(f"  Error probing stream: {error_msg}")
            overall_ok = False
            print("-" * 30)
            continue
        
        if not actual_audio_info: # Should be caught by error_msg, but defensive
            print(f"  Could not retrieve audio stream information.")
            overall_ok = False
            print("-" * 30)
            continue

        # Normalize actual channels info (ffprobe gives 'channels' as int, 'channel_layout' as string)
        actual_channels_text = actual_audio_info.get('channel_layout', str(actual_audio_info.get('channels', '')))
        if actual_channels_text == '1': # ffprobe might return channels=1 and no channel_layout for some mono
             actual_channels_text = 'mono'


        print(f"  Actual  : Codec: {actual_audio_info.get('codec_name')}, Rate: {actual_audio_info.get('sample_rate')} Hz, Channels: {actual_channels_text}")

        mismatches = []
        if actual_audio_info.get('codec_name') != expected_audio.get('codec_name'):
            mismatches.append("Codec")
        if actual_audio_info.get('sample_rate') != expected_audio.get('sample_rate'):
            mismatches.append("Sample Rate")
        if actual_channels_text.lower() != expected_audio.get('channels_text','').lower() : # Case-insensitive compare for 'mono'/'Mono'
            mismatches.append("Channels")

        if not mismatches:
            print("  Status  : OK")
        else:
            print(f"  Status  : MISMATCH ({', '.join(mismatches)})")
            overall_ok = False
        print("-" * 30)

    if overall_ok:
        print("\nAll checked cameras meet their expected audio stream configurations.")
    else:
        print("\nSome cameras do not meet their expected audio stream configurations or had errors.")

if __name__ == "__main__":
    main()
