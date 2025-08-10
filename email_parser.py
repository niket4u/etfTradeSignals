import imaplib
import email
import re
from config import GMAIL_USER, GMAIL_PASS, MODE

def fetch_emails():
    """Fetch trade emails from Gmail for account XXXXX-0647"""
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_USER, GMAIL_PASS)
    imap.select("inbox")

    status, messages = imap.search(None, '(UNSEEN)')
    mail_ids = messages[0].split()
    trades = []

    for num in mail_ids:
        res, msg_data = imap.fetch(num, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        subject = msg["Subject"]
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body += part.get_payload(decode=True).decode()
        else:
            body = msg.get_payload(decode=True).decode()

        # Skip non-test or test-only messages depending on mode
        if MODE == "test" and "TEST" not in subject.upper():
            continue
        if MODE == "production" and "TEST" in subject.upper():
            continue

        # Ensure correct account
        if "XXXXX-0647" not in body:
            continue

        trade_type = "BUY" if "Buy" in subject else "SELL"
        ticker_match = re.search(r'([A-Z]{2,5}) @', subject)
        price_match = re.search(r'@ \$([\d\.]+)', subject)
        qty_match = re.search(r'(\d+)\s[A-Z]{2,5}', subject)

        if ticker_match and price_match and qty_match:
            trades.append({
                "type": trade_type,
                "ticker": ticker_match.group(1),
                "price": float(price_match.group(1)),
                "quantity": int(qty_match.group(1)),
                "source": "email"
            })

    imap.logout()
    return trades
