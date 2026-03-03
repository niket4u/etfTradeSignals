from flask import Flask, request, jsonify, render_template
import csv, schedule, threading, time
from config import get_free_port, MODE, ALLOWED_SMS_NUMBERS
from trade_manager import add_ticker, log_trade, TICKERS_FILE
from alerts import send_alert

app = Flask(__name__)


# --- Web dashboard ---

@app.route("/", methods=["GET"])
def dashboard():
    return render_template("index.html")


@app.route("/api/tickers", methods=["GET"])
def api_tickers():
    tickers = []
    try:
        with open(TICKERS_FILE, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tickers.append({
                    "ticker": row["Ticker"],
                    "name": row["Name"],
                    "strategy": row["Strategy"],
                })
    except FileNotFoundError:
        pass
    return jsonify({"tickers": tickers})


@app.route("/api/add-ticker", methods=["POST"])
def api_add_ticker():
    data = request.get_json(silent=True) or {}
    symbol = (data.get("ticker") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "Ticker symbol is required."}), 400
    try:
        name, strategy = add_ticker(symbol)
        return jsonify({"ticker": symbol, "name": name, "strategy": strategy}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- SMS webhook ---

@app.route("/sms", methods=["POST"])
def sms_webhook():
    from_number = request.form.get("From")
    body = request.form.get("Body").strip()
    if from_number not in ALLOWED_SMS_NUMBERS:
        return "Not authorized", 403

    response_msg = handle_sms_command(body)
    send_alert(response_msg, from_number)
    return "OK", 200

def handle_sms_command(body):
    parts = body.split(":")
    cmd = parts[0].strip().upper()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "ADD":
        name, strategy = add_ticker(arg)
        return f"✅ Added {arg} — {name}, {strategy}"
    elif cmd in ["BUY", "SELL"]:
        log_trade(cmd, arg, 0.0, 0)  # Price/qty for manual logging only
        return f"✅ Manual {cmd} signal sent for {arg}"
    elif cmd == "LIST":
        return list_tickers()
    else:
        return "Unknown command"

def list_tickers():
    import csv
    with open("tickers.csv") as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1:
        return "No tickers currently tracked."
    return "\n".join([f"{r[0]} — {r[1]} — {r[2]}" for r in rows[1:]])

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    port = get_free_port()
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
