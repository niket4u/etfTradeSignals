from twilio.rest import Client
from config import TWILIO_SID, TWILIO_AUTH, TWILIO_FROM, DASHBOARD_URL

def send_alert(msg, to_number):
    client = Client(TWILIO_SID, TWILIO_AUTH)
    full_msg = f"{msg}\nView dashboard: {DASHBOARD_URL}" if DASHBOARD_URL else msg
    client.messages.create(
        from_=TWILIO_FROM,
        body=full_msg,
        to=to_number
    )
