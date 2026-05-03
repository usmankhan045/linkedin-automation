#!/bin/bash
set -e
echo "Deploying AI Employee..."
cd /home/usman/linkedin-automation
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt --quiet
pm2 restart pm2_ecosystem_do.config.js
pm2 status
echo "Deploy complete — $(date)"
