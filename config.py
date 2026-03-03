import os
import socket
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("MODE", "production").lower()
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")
ALLOWED_SMS_NUMBERS = os.getenv("ALLOWED_SMS_NUMBERS", "").split(",")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_FROM = os.getenv("TWILIO_FROM")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
LOSS_LIMIT_DAILY = float(os.getenv("LOSS_LIMIT_DAILY", 50))
GAIN_TARGET_MONTHLY = float(os.getenv("GAIN_TARGET_MONTHLY", 2000))
LOSS_LIMIT_MONTHLY = float(os.getenv("LOSS_LIMIT_MONTHLY", 500))
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_free_port(start_port=5000):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
            port += 1
