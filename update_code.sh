#!/bin/bash

# XSUS Sentinel Update Script
# Run as root or with sudo

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit
fi

APP_DIR="/opt/xsus_sentinel"
USER="www-data"

echo "[*] Updating Source Code..."
cd $APP_DIR
# Fix permissions temporarily to allow git pull if owned by www-data
chown -R root:root .
git pull
chown -R $USER:$USER .

echo "[*] Updating Dependencies..."
source venv/bin/activate
pip install -r requirements.txt

echo "[*] Running Database Migrations..."
python3 migrate_db.py

echo "[*] Restarting Services..."
systemctl restart xsus-web xsus-bot xsus-celery

echo "[*] Update Complete."
