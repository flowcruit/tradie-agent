import sys
import os
import json
sys.stdout = sys.stderr

from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv
from database import (
    get_all_leads, update_lead_status, init_db, get_lead_by_phone,
    get_client_by_twilio_number, create_client,
    create_outbound_lead, get_outbound_lead_by_phone,
    update_outbound_lead, get_all_outbound_leads
)
from agent_sms import get_agent_response, send_quote_to_customer

load_dotenv()

try:
    from simple_websocket import Server as WSServer
    WS_LIB = "simple_websocket"
except ImportError:
    WS_LIB = None

app = Flask(__name__)
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

OWNER_PHONE   = os.getenv("OWNER_PHONE", "")
TWILIO_PHONE  = os.getenv("TWILIO_PHONE_NUMBER", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")
OWNER_NAME    = os.getenv("BUSINESS_OWNER", "Mike")
BASE_URL      = os.getenv("BASE_URL", "")

print("APP V7 — MULTI-CLIENT VOICE")
print(f"BASE_URL: {BASE_URL}")


# ── Client lookup ──────────────────────────────────────────────────────────

def get_default_client():
    return {
        "id": None,
        "business_name": BUSINESS_NAME,
        "owner_name": OWNER_NAME,
        "owner_phone": OWNER_PHONE,
        "twilio_number": TWILIO_PHONE,
        "province": "ON",
        "plan": "active",
        "active": True
    }

def get_client_for_number(twilio_number):
    client = get_client_by_twilio_number(twilio_number)
    if client:
        print(f"Client found: {client['business_name']}")
        return client
    print(f"No client for {twilio_number} — using default")
    return get_default_client()


# ── Owner commands ─────────────────────────────────────────────────────────

def handle_owner_command(from_number, body, client):
    cmd   = body.strip().upper()
    parts = body.strip().split(" ")

    if cmd == "LEADS":
        leads = get_all_leads(client_id=client.get("id"))
        new_leads = [l for l in leads if l['status'] == 'new']
        if not new_leads:
            return "No new leads."
        summary = f"{len(new_leads)} new leads:\n"
        for l in new_leads[:5]:
            prefix = "URGENT " if l['urgent'] else ""
            summary += f"{prefix}{l['name']} - {l['contact_phone'] or l['phone']}\n"
        return summary.strip()

    if cmd.startswith("APPROVE") and len(parts) >= 4:
        customer_phone = parts[1]
        try:
            low, high = int(parts[2]), int(parts[3])
        except:
            return "Usage: APPROVE +1xxxxxxxxxx 150 300"
        lead = get_lead_by_phone(customer_phone)
        name = lead['name'] if lead else "there"
        result = send_quote_to_customer(customer_phone, name, low, high,
                                        from_number=client["twilio_number"])
        return f"Quote sent to {name}: ${low}-${high} CAD" if result else "Failed to send quote"

    if cmd.startswith("DONE") and len(parts) >= 2:
        customer_phone = parts[1]
        lead = get_lead_by_phone(customer_phone)
        if lead:
            update_lead_status(lead['id'], 'done')
            return f"Done: {lead['name']}"
        return f"No lead found for {customer_phone}"

    return None


# ── SMS ────────────────────────────────────────────────────────────────────

@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "")
    from_number  = request.form.get("From", "")
    to_number    = request.form.get("To", "")
    print(f"SMS from {from_number} to {to_number}: {incoming_msg}")

    client = get_client_for_number(to_number)
    resp   = MessagingResponse()

    owner_clean    = client["owner_phone"].replace("+", "").replace(" ", "")
    incoming_clean = from_number.replace("+", "").replace(" ", "")

    if incoming_clean == owner_clean:
        result = handle_owner_command(from_number, incoming_msg, client)
        if result:
            resp.message(result)
            return str(resp)

    # Check if this is a YES response from an outbound prospect
    lead = get_outbound_lead_by_phone(from_number)
    if lead and incoming_msg.strip().upper() in ["YES", "SI", "Y", "YEP", "YEAH", "SURE", "OK"]:
        update_outbound_lead(from_number, responded=True, status="responded")
        # Send confirmation SMS
        resp.message(
            f"Perfect! Calling {lead['business_name']} right now — answer and pretend you're a customer calling in with a problem."
        )
        # Trigger demo call in background thread
        import threading
        threading.Thread(
            target=_make_demo_call,
            args=(from_number, lead),
            daemon=True
        ).start()
        return str(resp)

    reply = get_agent_response(from_number, incoming_msg)
    resp.message(reply)
    return str(resp)


def _make_demo_call(prospect_phone, lead):
    """Call the prospect and run demo agent as their business."""
    import time
    time.sleep(3)  # Small delay so SMS arrives first

    if BASE_URL:
        ws_url = BASE_URL.replace("https://", "wss://") + "/voice-ws"
    else:
        ws_url = f"wss://tradie-agent.onrender.com/voice-ws"

    business_name = lead["business_name"]
    owner_name    = lead["owner_name"] or "our technician"

    welcome = (
        f"Thank you for calling {business_name}. "
        f"You've reached our answering service — {owner_name} is currently on a job. "
        f"I can take your details and have someone call you right back. "
        f"What's your first name please?"
    )

    try:
        # Register lead as temp client so voice agent uses their business name
        temp_client = {
            "id": None,
            "business_name": business_name,
            "owner_name": owner_name,
            "owner_phone": prospect_phone,
            "twilio_number": OUTBOUND_NUMBER,
            "province": "ON",
            "plan": "demo",
            "active": True
        }

        call = twilio_client.calls.create(
            to=prospect_phone,
            from_=OUTBOUND_NUMBER,
            twiml=f"""<Response><Connect>
                <ConversationRelay url="{ws_url}" language="en-US" interruptible="true"
                    hints="furnace,boiler,HVAC,heat pump,thermostat,hot water tank,no heat,frozen pipes"
                    welcomeGreeting="{welcome}" />
            </Connect></Response>"""
        )
        update_outbound_lead(prospect_phone, demo_called=True, status="demo_done")
        print(f"Demo call initiated to {business_name}: {call.sid}")
    except Exception as e:
        print(f"Demo call error: {e}")


# ── VOICE ──────────────────────────────────────────────────────────────────

@app.route("/voice", methods=["POST"])
def voice_entry():
    caller    = request.form.get("From", "unknown")
    to_number = request.form.get("To", "")
    call_sid  = request.form.get("CallSid", "")
    print(f"Call from {caller} to {to_number} — SID: {call_sid}")

    client        = get_client_for_number(to_number)
    business_name = client["business_name"]
    owner_name    = client["owner_name"]

    if BASE_URL:
        ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/voice-ws"
    else:
        ws_url = f"wss://{request.host}/voice-ws"

    welcome = (
        f"Thank you for calling {business_name}. "
        f"You've reached our answering service — {owner_name} is currently on a job. "
        f"I can take your details and have someone call you right back. "
        f"What's your first name please?"
    )

    hints = (
        "furnace,boiler,HVAC,heat pump,air conditioner,thermostat,"
        "hot water tank,water heater,sump pump,drain,pipe,leak,flood,"
        "plumbing,electrical panel,breaker,carbon monoxide,no heat,frozen pipes"
    )

    response = VoiceResponse()
    connect  = Connect()
    connect.conversation_relay(
        url=ws_url,
        language="en-US",
        welcome_greeting=welcome,
        hints=hints,
        interruptible="true"
    )
    response.append(connect)
    return str(response), 200, {"Content-Type": "text/xml"}


# ── WebSocket ──────────────────────────────────────────────────────────────

try:
    from flask_sock import Sock
    sock = Sock(app)

    @sock.route("/voice-ws")
    def voice_ws_sock(ws):
        caller_phone  = "unknown"
        twilio_number = TWILIO_PHONE
        print("WebSocket connected")

        try:
            raw = ws.receive(timeout=10)
            if raw:
                setup = json.loads(raw)
                if setup.get("type") == "setup":
                    caller_phone  = setup.get("from", "unknown")
                    twilio_number = setup.get("to", TWILIO_PHONE)
                    print(f"Setup — caller: {caller_phone}, to: {twilio_number}")

            client = get_client_for_number(twilio_number)
            print(f"CLIENT LOADED: {client['business_name']} / {client['owner_name']}")
            from voice_agent import handle_conversation_relay
            handle_conversation_relay(ws, caller_phone, client)

        except Exception as e:
            print(f"WebSocket error: {e}")
            import traceback
            traceback.print_exc()

    print("flask-sock registered at /voice-ws")

except ImportError:
    print("flask-sock not installed")


# ── Onboarding ─────────────────────────────────────────────────────────────

@app.route("/onboard", methods=["POST"])
def onboard_client():
    """Register a new client. POST JSON: {business_name, owner_name, owner_phone, twilio_number, province}"""
    data    = request.json or {}
    required = ["business_name", "owner_name", "owner_phone", "twilio_number"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing: {missing}"}), 400

    client_id = create_client(
        business_name=data["business_name"],
        owner_name=data["owner_name"],
        owner_phone=data["owner_phone"],
        twilio_number=data["twilio_number"],
        province=data.get("province", "ON")
    )

    if client_id:
        welcome = (
            f"Welcome to Tradie Agent!\n\n"
            f"Your AI receptionist for {data['business_name']} is now active.\n\n"
            f"To forward missed calls:\n"
            f"Rogers/Bell: dial *21*{data['twilio_number']}#\n"
            f"Telus: dial *62*{data['twilio_number']}#\n\n"
            f"Test it: call your business number and don't answer.\n\n"
            f"Reply LEADS anytime to see your leads."
        )
        try:
            twilio_client.messages.create(
                body=welcome,
                from_=data["twilio_number"],
                to=data["owner_phone"]
            )
        except Exception as e:
            print(f"Welcome SMS error: {e}")

        return jsonify({"success": True, "client_id": client_id}), 201

    return jsonify({"error": "Failed to create client"}), 500


# ── Standard routes ────────────────────────────────────────────────────────



# ── OUTBOUND SMS SYSTEM ────────────────────────────────────────────────────

OUTBOUND_SMS = (
    "Hi {owner_name}, quick question — how many calls does {business_name} miss every week?\n\n"
    "Every missed call in HVAC goes straight to your competitor.\n\n"
    "We built an AI that only kicks in when you don\'t answer — captures the lead and texts you instantly.\n\n"
    "Your competitors in Ontario are already using it.\n\n"
    "Want to hear it answer as {business_name} right now? Reply YES"
)

OUTBOUND_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")


@app.route("/outbound/add-lead", methods=["POST"])
def add_outbound_lead():
    """Add a single prospect. POST JSON: {business_name, owner_name, phone, city}"""
    data = request.json or {}
    required = ["business_name", "phone"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing: {missing}"}), 400

    lead_id = create_outbound_lead(
        business_name=data["business_name"],
        owner_name=data.get("owner_name", ""),
        phone=data["phone"],
        city=data.get("city", "Ontario")
    )
    return jsonify({"success": bool(lead_id), "lead_id": lead_id}), 201


@app.route("/outbound/add-leads", methods=["POST"])
def add_outbound_leads_bulk():
    """Bulk add prospects. POST JSON: [{business_name, owner_name, phone, city}, ...]"""
    leads = request.json or []
    created = 0
    for lead in leads:
        if lead.get("business_name") and lead.get("phone"):
            result = create_outbound_lead(
                business_name=lead["business_name"],
                owner_name=lead.get("owner_name", ""),
                phone=lead["phone"],
                city=lead.get("city", "Ontario")
            )
            if result:
                created += 1
    return jsonify({"success": True, "created": created, "total": len(leads)}), 201


@app.route("/outbound/send", methods=["POST"])
def send_outbound_sms():
    """Send SMS to one lead by phone. POST JSON: {phone}"""
    data = request.json or {}
    phone = data.get("phone")
    if not phone:
        return jsonify({"error": "Missing phone"}), 400

    lead = get_outbound_lead_by_phone(phone)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    msg = OUTBOUND_SMS.format(
        owner_name=lead["owner_name"] or "there",
        business_name=lead["business_name"]
    )

    try:
        result = twilio_client.messages.create(
            body=msg,
            from_=OUTBOUND_NUMBER,
            to=phone
        )
        update_outbound_lead(phone, sms_sent=True, sms_sent_at="NOW()", status="contacted")
        print(f"Outbound SMS sent to {lead['business_name']}: {result.sid}")
        return jsonify({"success": True, "sid": result.sid}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/outbound/send-batch", methods=["POST"])
def send_outbound_batch():
    """Send SMS to all pending leads. Optional POST JSON: {limit: 10}"""
    data = request.json or {}
    limit = data.get("limit", 10)

    leads = get_all_outbound_leads()
    pending = [l for l in leads if not l["sms_sent"]][:limit]

    sent = 0
    failed = 0
    for lead in pending:
        msg = OUTBOUND_SMS.format(
            owner_name=lead["owner_name"] or "there",
            business_name=lead["business_name"]
        )
        try:
            twilio_client.messages.create(
                body=msg,
                from_=OUTBOUND_NUMBER,
                to=lead["phone"]
            )
            update_outbound_lead(lead["phone"], sms_sent=True, status="contacted")
            sent += 1
            print(f"Sent to {lead['business_name']} ({lead['phone']})")
        except Exception as e:
            failed += 1
            print(f"Failed {lead['phone']}: {e}")

    return jsonify({"sent": sent, "failed": failed, "pending_remaining": len(pending) - sent}), 200


@app.route("/outbound/leads", methods=["GET"])
def outbound_dashboard():
    """View all outbound leads and their status."""
    leads = get_all_outbound_leads()
    total     = len(leads)
    contacted = sum(1 for l in leads if l["sms_sent"])
    responded = sum(1 for l in leads if l["responded"])
    demoed    = sum(1 for l in leads if l["demo_called"])
    converted = sum(1 for l in leads if l["trial_activated"])

    html = f"""<!DOCTYPE html>
<html><head>
    <title>Outbound Pipeline</title>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{{font-family:Arial,sans-serif;margin:20px;background:#f5f5f5}}
        h1{{color:#333}}
        .stats{{display:flex;gap:15px;margin-bottom:20px;flex-wrap:wrap}}
        .stat{{background:white;padding:15px 20px;border-radius:8px;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,.1)}}
        .stat-number{{font-size:28px;font-weight:bold;color:#333}}
        .stat-label{{color:#888;font-size:12px}}
        table{{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 4px rgba(0,0,0,.1)}}
        th{{background:#333;color:white;padding:10px;text-align:left;font-size:13px}}
        td{{padding:10px;border-bottom:1px solid #eee;font-size:13px}}
        .badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold}}
        .pending{{background:#eee;color:#666}}
        .contacted{{background:#3498db;color:white}}
        .responded{{background:#f39c12;color:white}}
        .demoed{{background:#9b59b6;color:white}}
        .converted{{background:#2ecc71;color:white}}
    </style>
</head><body>
    <h1>📤 Outbound Pipeline</h1>
    <div class="stats">
        <div class="stat"><div class="stat-number">{total}</div><div class="stat-label">Total</div></div>
        <div class="stat"><div class="stat-number">{contacted}</div><div class="stat-label">SMS Sent</div></div>
        <div class="stat"><div class="stat-number">{responded}</div><div class="stat-label">Responded</div></div>
        <div class="stat"><div class="stat-number">{demoed}</div><div class="stat-label">Demo Done</div></div>
        <div class="stat"><div class="stat-number">{converted}</div><div class="stat-label">Trial Active</div></div>
    </div>
    <table>
        <tr><th>Business</th><th>Owner</th><th>Phone</th><th>City</th><th>Status</th></tr>"""

    for l in leads:
        status = l["status"]
        html += f"""
        <tr>
            <td>{l['business_name']}</td>
            <td>{l['owner_name'] or '—'}</td>
            <td>{l['phone']}</td>
            <td>{l['city'] or '—'}</td>
            <td><span class="badge {status}">{status.upper()}</span></td>
        </tr>"""

    html += "</table></body></html>"
    return html


@app.route("/health", methods=["GET"])
def health():
    return f"Tradie Agent v7 — Multi-client. WS_LIB: {WS_LIB}", 200


@app.route("/leads", methods=["GET"])
def leads_dashboard():
    leads  = get_all_leads()
    total  = len(leads)
    urgent = sum(1 for l in leads if l['urgent'])
    new    = sum(1 for l in leads if l['status'] == 'new')

    html = f"""<!DOCTYPE html>
<html><head>
    <title>Tradie Agent Dashboard</title>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{{font-family:Arial,sans-serif;margin:20px;background:#f5f5f5}}
        h1{{color:#333}}.lead{{background:white;padding:15px;margin:10px 0;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1)}}
        .urgent{{border-left:4px solid #e74c3c}}.new{{border-left:4px solid #3498db}}.done{{border-left:4px solid #2ecc71;opacity:.7}}
        .badge{{display:inline-block;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-left:8px}}
        .badge-urgent{{background:#e74c3c;color:white}}.badge-new{{background:#3498db;color:white}}
        .badge-done{{background:#2ecc71;color:white}}.badge-voice{{background:#9b59b6;color:white}}
        .meta{{color:#888;font-size:13px;margin-top:8px}}
        .stats{{display:flex;gap:20px;margin-bottom:20px;flex-wrap:wrap}}
        .stat{{background:white;padding:15px 25px;border-radius:8px;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,.1)}}
        .stat-number{{font-size:32px;font-weight:bold;color:#333}}.stat-label{{color:#888;font-size:13px}}
        .commands{{background:#fff3cd;padding:15px;border-radius:8px;margin-bottom:20px;font-size:13px}}
        code{{background:#eee;padding:2px 6px;border-radius:3px}}
    </style>
</head><body>
    <h1>🔧 Tradie Agent Dashboard</h1>
    <div class="stats">
        <div class="stat"><div class="stat-number">{total}</div><div class="stat-label">Total Leads</div></div>
        <div class="stat"><div class="stat-number">{urgent}</div><div class="stat-label">🚨 Urgent</div></div>
        <div class="stat"><div class="stat-number">{new}</div><div class="stat-label">New</div></div>
    </div>
    <div class="commands"><strong>SMS Commands:</strong><br>
        <code>LEADS</code> &nbsp;|&nbsp; <code>APPROVE +1xxx 150 300</code> &nbsp;|&nbsp; <code>DONE +1xxx</code>
    </div>"""

    for lead in leads:
        uc  = "urgent" if lead['urgent'] else ("done" if lead['status'] == 'done' else "new")
        bc  = "badge-urgent" if lead['urgent'] else ("badge-done" if lead['status'] == 'done' else "badge-new")
        bt  = "URGENT" if lead['urgent'] else lead['status'].upper()
        chb = '<span class="badge badge-voice">📞 VOICE</span>' if lead.get('channel') == 'voice' else '<span class="badge badge-new">💬 SMS</span>'
        html += f"""
    <div class="lead {uc}">
        <strong>{lead['name'] or 'Unknown'}</strong>
        <span class="badge {bc}">{bt}</span>{chb}
        <div style="margin-top:5px">{lead['problem'] or ''}</div>
        <div class="meta">📍 {lead['address'] or 'No address'} &nbsp;|&nbsp; 📞 {lead['contact_phone'] or lead['phone']} &nbsp;|&nbsp; 🕐 {lead['created_at']}</div>
    </div>"""

    html += "</body></html>"
    return html


@app.route("/test-voice", methods=["GET"])
def test_voice():
    try:
        ws_url = (BASE_URL.replace("https://", "wss://") + "/voice-ws") if BASE_URL else f"wss://{request.host}/voice-ws"

        # Load client from DB so test uses real config
        client        = get_client_for_number(TWILIO_PHONE)
        business_name = client["business_name"]
        owner_name    = client["owner_name"]
        owner_phone   = client["owner_phone"]

        call = twilio_client.calls.create(
            to=owner_phone,
            from_=TWILIO_PHONE,
            twiml=f"""<Response><Connect>
                <ConversationRelay url="{ws_url}" language="en-US" interruptible="true"
                    hints="furnace,boiler,HVAC,heat pump,thermostat,hot water tank,water heater,sump pump,drain,pipe,leak,flood,no heat"
                    welcomeGreeting="Thank you for calling {business_name}. You've reached our answering service — {owner_name} is currently on a job. What's your first name please?" />
            </Connect></Response>"""
        )
        return f"Test call! SID: {call.sid} — calling as {business_name}", 200
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/clear-test", methods=["GET"])
def clear_test():
    """Clear test messages for default Twilio number only."""
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        c = conn.cursor()
        c.execute("DELETE FROM messages WHERE phone = %s", (TWILIO_PHONE,))
        c.execute("DELETE FROM leads WHERE phone = %s", (TWILIO_PHONE,))
        conn.commit()
        conn.close()
        return "Test history cleared — ready for next call", 200
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/clear-all", methods=["GET"])
def clear_all():
    """Nuke ALL messages and leads — use only during development."""
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        c = conn.cursor()
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM leads")
        conn.commit()
        conn.close()
        return "All messages and leads cleared", 200
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/migrate", methods=["GET"])
def migrate():
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        c = conn.cursor()
        c.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS client_id INTEGER")
        c.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'sms'")
        c.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_phone TEXT")
        c.execute("""CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY, business_name TEXT NOT NULL,
            owner_name TEXT, owner_phone TEXT UNIQUE, twilio_number TEXT UNIQUE,
            province TEXT DEFAULT 'ON', plan TEXT DEFAULT 'trial',
            trial_ends_at TIMESTAMPTZ, stripe_customer_id TEXT,
            active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_twilio ON clients(twilio_number)")
        conn.commit()
        conn.close()
        return "Migration done", 200
    except Exception as e:
        return f"Error: {e}", 500


if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
