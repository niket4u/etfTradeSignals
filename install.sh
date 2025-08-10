#!/bin/bash
echo "📦 Installing ETF Trade Signals Bot..."
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Installation complete."
echo "Run the bot with: source venv/bin/activate && python3 etf_bot.py"
