sudo tee /etc/logrotate.d/auklab-ffmpeg >/dev/null <<'EOF'
/home/bsp/auklab-video/logs/*.log {
    su bsp bsp
    daily
    rotate 5 
    compress
    delaycompress
    dateext
    missingok
    notifempty
    copytruncate
}
EOF

