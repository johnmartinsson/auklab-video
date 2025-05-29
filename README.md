# auklab-video
All video recording scripts and rutines for the Auklab.


# Notes during the recording season
- 20250528T153000: recording stopped at May 23 03:30, probable cause: camera stopped sending data and ffmpeg process hanged forever waiting without restart
- 20250528T153002: noticed at May 28 12:00, restarted before lunch, then patched and restarted during the afternoon until 15:27. Will be gaps in data during this day.
- 20250528T153005: Should record audio nicely again from May 28 15:27, and notice if this happens again and restart automatically using new monitor_recordings.service
  - ffmpeg should die if no data comes from camera during 30 seconds => camera service restarting the ffmpeg process
  - if no recordings are in the recording_dir or the last one is older than segment_time * 2 minutes, then the monitor_recording.service will try to restart the camera recording
- 20250528T153800: Had to restart all record_camera*.service, because of change to the log level from "info" to "warning" because the -report flag made ffmpeg create too large logs. Should now be smaller.
- 20250528T153800: Should have continuous .mkv recordings again from May 28 at 15:40, so gap between 20250523T033000 to 20250528T154000
- 20250529T074000: The log lavel change did not take, restarted all recordings at 073900, may have lost a minute here.
