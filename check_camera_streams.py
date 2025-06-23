#!/usr/bin/env python3
import json
import subprocess
import argparse

def get_rtsp_stream_info(rtsp_url, ffprobe_path="ffprobe", timeout=15):
    """
    Probes an RTSP stream and returns audio and video stream information.
    Returns a tuple: (video_stream_info, audio_stream_info, error_message)
    Stream info is a dictionary, or None if not found.
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        # "-select_streams", "v:0,a:0", # Select first video and first audio
        "-timeout", str(timeout * 1000000), # RTSP timeout in microseconds
        rtsp_url
    ]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout + 5)
        if process.returncode != 0:
            return None, None, f"ffprobe error: {process.stderr.strip()}"

        data = json.loads(process.stdout)
        video_stream = None
        audio_stream = None

        if data and "streams" in data:
            for stream in data["streams"]:
                if stream.get("codec_type") == "video" and video_stream is None: # Take first video
                    video_stream = {
                        "codec_name": stream.get("codec_name"),
                        "profile": stream.get("profile"),
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                        "r_frame_rate": stream.get("r_frame_rate"),
                        "avg_frame_rate": stream.get("avg_frame_rate"),
                        # You can add other fields like "pix_fmt" if needed
                    }
                elif stream.get("codec_type") == "audio" and audio_stream is None: # Take first audio
                    audio_stream = {
                        "codec_name": stream.get("codec_name"),
                        "sample_rate": int(stream.get("sample_rate", 0)),
                        "channels": stream.get("channels"),
                        "channel_layout": stream.get("channel_layout")
                    }
        
        if not video_stream and not audio_stream:
             return None, None, "No video or audio streams found in ffprobe output."
        
        return video_stream, audio_stream, None

    except subprocess.TimeoutExpired:
        return None, None, f"ffprobe command timed out after {timeout+5} seconds."
    except json.JSONDecodeError:
        return None, None, "Failed to decode ffprobe JSON output."
    except Exception as e:
        return None, None, f"An unexpected error occurred with ffprobe: {e}"

def compare_and_print(stream_type, actual_info, expected_info, station_ip_str):
    """Helper function to compare and print stream info."""
    if not expected_info:
        print(f"  {stream_type.capitalize()}: No expected configuration.")
        return True # No expectation, so it's "OK" in a sense

    if not actual_info:
        print(f"  {stream_type.capitalize()}: Actual stream not found.")
        if expected_info: # If we expected it but didn't find it
            return False
        return True # If we didn't expect it and didn't find it

    mismatches = []
    actual_details = []
    expected_details = []

    if stream_type == "video":
        fields_to_check = ["codec_name", "profile", "width", "height", "r_frame_rate"]
        for field in fields_to_check:
            actual_val = actual_info.get(field)
            expected_val = expected_info.get(field)
            
            # Special handling for frame rate if expected is int/float
            if field == "r_frame_rate" and isinstance(expected_val, (int, float)):
                expected_val = f"{int(expected_val)}/1" # Convert to "25/1" format

            actual_details.append(f"{field.replace('_', ' ').capitalize()}: {actual_val}")
            expected_details.append(f"{field.replace('_', ' ').capitalize()}: {expected_val}")

            if str(actual_val) != str(expected_val): # Compare as strings for simplicity here
                mismatches.append(field.replace("_", " ").capitalize())
        
    elif stream_type == "audio":
        fields_to_check = {"codec_name": "Codec", "sample_rate": "Rate", "channels_text": "Channels"}
        # Normalize actual channels info
        actual_channels_text = actual_info.get('channel_layout', str(actual_info.get('channels', '')))
        if actual_channels_text == '1': actual_channels_text = 'mono'

        actual_vals_map = {
            "codec_name": actual_info.get('codec_name'),
            "sample_rate": actual_info.get('sample_rate'),
            "channels_text": actual_channels_text.lower()
        }
        
        for key, display_name in fields_to_check.items():
            actual_val = actual_vals_map.get(key)
            expected_val = expected_info.get(key)
            
            unit = " Hz" if key == "sample_rate" else ""
            actual_details.append(f"{display_name}: {actual_val}{unit}")
            expected_details.append(f"{display_name}: {expected_val}{unit if expected_val is not None else ''}")

            # Handle comparison, ensuring expected_val is lowercased for channels_text
            if key == "channels_text" and expected_val is not None:
                expected_val_cmp = expected_val.lower()
            else:
                expected_val_cmp = expected_val

            if str(actual_val) != str(expected_val_cmp):
                mismatches.append(display_name)

    print(f"  Expected {stream_type.capitalize()}: {', '.join(expected_details)}")
    print(f"  Actual   {stream_type.capitalize()}: {', '.join(actual_details)}")

    if not mismatches:
        print(f"  Status   {stream_type.capitalize()}: OK")
        return True
    else:
        print(f"  Status   {stream_type.capitalize()}: MISMATCH ({', '.join(mismatches)})")
        return False


def main():
    parser = argparse.ArgumentParser(description="Check camera RTSP audio and video stream configurations.")
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
        # IMPORTANT: Replace "YOUR_DEFAULT_PASSWORD" or ensure password is in config
        password = cam_config.get("password", defaults.get("password", "YOUR_DEFAULT_PASSWORD")) 
        rtsp_port = cam_config.get("rtsp_port", defaults.get("rtsp_port", 554))
        ffprobe_path = cam_config.get("ffprobe_path", defaults.get("ffprobe_path", "ffprobe"))

        # Determine expected settings
        default_expected_audio = defaults.get("expected_audio", {})
        camera_expected_audio_override = cam_config.get("expected_audio", {})
        expected_audio = {**default_expected_audio, **camera_expected_audio_override}

        default_expected_video = defaults.get("expected_video", {})
        camera_expected_video_override = cam_config.get("expected_video", {})
        expected_video = {**default_expected_video, **camera_expected_video_override}

        rtsp_url = f"rtsp://{user}:{password}@{ip}:{rtsp_port}/Streaming/Channels/101" # Main stream

        station_ip_str = f"[{station} ({ip})]"
        print(station_ip_str)

        actual_video_info, actual_audio_info, error_msg = get_rtsp_stream_info(rtsp_url, ffprobe_path)

        if error_msg:
            print(f"  Error probing stream: {error_msg}")
            overall_ok = False
            print("-" * 40)
            continue
        
        # Check Video
        if expected_video: # Only check if there's an expectation
            video_ok = compare_and_print("video", actual_video_info, expected_video, station_ip_str)
            if not video_ok:
                overall_ok = False
        elif actual_video_info: # Has video but not expected
             print(f"  Video: Found but not defined in expected_video.")


        # Check Audio
        if expected_audio: # Only check if there's an expectation
            audio_ok = compare_and_print("audio", actual_audio_info, expected_audio, station_ip_str)
            if not audio_ok:
                overall_ok = False
        elif actual_audio_info: # Has audio but not expected
            print(f"  Audio: Found but not defined in expected_audio.")
            
        print("-" * 40)

    if overall_ok:
        print("\nAll checked cameras meet their expected stream configurations or had no specific expectations.")
    else:
        print("\nSome cameras do not meet their expected stream configurations or had errors.")

if __name__ == "__main__":
    main()
