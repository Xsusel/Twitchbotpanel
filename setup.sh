#!/bin/bash

# XSUS Sentinel Setup Script
# Run as root or with sudo

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit
fi

APP_DIR="/opt/xsus_sentinel"
USER="www-data"
DB_URL="postgresql://sentry:tytanic232@localhost:5432/xsus_sentinel"

echo "=========================================="
echo "   XSUS Sentinel - Installation Script    "
echo "=========================================="

# 1. Install System Dependencies
echo "[*] Installing system dependencies..."
apt-get update
apt-get install -y python3-venv python3-pip python3-dev build-essential libpq-dev redis-server nginx git postgresql postgresql-contrib

# 2. Setup Application Directory
echo "[*] Setting up application directory..."
if [ ! -d "$APP_DIR" ]; then
    echo "Directory $APP_DIR does not exist. Assuming current directory is the source."
    mkdir -p $APP_DIR
    cp -r . $APP_DIR/
else
    echo "Directory exists. Updating permissions..."
fi

cd $APP_DIR

# 3. Setup Virtual Environment
echo "[*] Creating Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configuration (Secrets)
echo "[*] Configuring secrets..."
if [ -f ".env" ]; then
    echo ".env file already exists. Skipping secret prompt."
else
    echo "Please enter your Twitch Application Credentials:"
    read -p "Twitch Client ID: " TWITCH_CLIENT_ID
    read -p "Twitch Client Secret: " TWITCH_CLIENT_SECRET

    echo ""
    echo "[!] INFO: To get the Twitch IRC Token, visit: https://twitchtokengenerator.com/"
    echo "    Select 'Custom Scope Token' and check: chat:read, chat:edit"
    echo ""
    read -p "Twitch IRC Token (Access Token): " TWITCH_IRC_TOKEN
    read -p "Discord Webhook URL (optional, press Enter to skip): " DISCORD_WEBHOOK_URL

    cat > .env <<EOF
FLASK_APP=app
FLASK_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
DATABASE_URL=$DB_URL
TWITCH_CLIENT_ID=$TWITCH_CLIENT_ID
TWITCH_CLIENT_SECRET=$TWITCH_CLIENT_SECRET
TWITCH_IRC_TOKEN=$TWITCH_IRC_TOKEN
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
ADMIN_PASSWORD=tytanic232@
DISCORD_WEBHOOK_URL=$DISCORD_WEBHOOK_URL
EOF
    echo ".env created."
fi

# 5. Database Initialization
echo "[*] Initializing Database..."
# Ensure permissions for postgres user (if local)
# This part assumes the DB user 'sentry' exists. If not, we might fail.
# For simplicity in this script, we assume the user followed the requirement
# "Użytkownik skonfiguruje ją samodzielnie" OR we try to initialize tables.
export FLASK_APP=app
python3 init_db.py

# 6. Permissions
echo "[*] Setting permissions..."
chown -R $USER:$USER $APP_DIR
chmod 600 $APP_DIR/.env

# 7. Systemd Services
echo "[*] Installing Systemd services..."
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable xsus-web xsus-bot xsus-celery
systemctl restart xsus-web xsus-bot xsus-celery

# 8. Nginx
echo "[*] Configuring Nginx..."
cp nginx/xsus_sentinel.conf /etc/nginx/sites-available/
# Remove default if it exists
if [ -f /etc/nginx/sites-enabled/default ]; then
    rm /etc/nginx/sites-enabled/default
fi
ln -sf /etc/nginx/sites-available/xsus_sentinel.conf /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx

echo "=========================================="
echo "   Installation Complete!                 "
echo "=========================================="
echo "Access the dashboard at: http://$(curl -s ifconfig.me) or http://localhost"
echo "Login Password: tytanic232@"
echo "=========================================="
