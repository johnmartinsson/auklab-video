sudo tee /etc/monitor_email.conf >/dev/null <<'EOF'
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=auklab2025@gmail.com
SMTP_PASS=app-password-or-smtp-token
ALERT_TO=john.martinsson@ri.se,john.martinsson@gmail.com,jonas.sundberg@slu.se,delia.fano.yela@ri.se,olof.mogren@ri.se
EOF
sudo chmod 600 /etc/monitor_email.conf
sudo chown root:root /etc/monitor_email.conf
