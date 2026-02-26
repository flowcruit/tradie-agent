import sys
import os
sys.stdout = sys.stderr

import asyncio
import threading
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv
from database import get_all_leads, update_lead_status, init_db, get_lead_by_phone
from agent import get_agent_response, send_quote_to_customer

load_dotenv()

# ── WebSocket server (gevent or simple_websocket) ──────────────────────────
try:
    from simple_websocket import Server as WSServer
    WS_LIB = "simple_websocket"
except ImportError:
    WS_LIB = None

app = Flask(__name__)
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

OWNER_PHONE = os.getenv("OWNER_PHONE", "")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")
BASE_URL = os.getenv("BASE_URL", "")        # e.g. https://tradie-agent.onrender.com

print("APP V6 — VOICE LOADED")
print(f"OWNER_PHONE: {OWNER_PHONE}")
print(f"TWILIO_PHONE: {TWILIO_PHONE}")
print(f"BASE_URL: {BASE_URL}")
print(f"WS_LIB: {WS_LIB}")


# ── Helpers ────────────────────────────────────────────────────────────────

def is_owner(phone):
    clean_incoming = phone.replace("+", "").replace(" ", "")
    clean_owner = OWNER_PHONE.replace("+", "").replace(" ", "")
    return clean_incoming == clean_owner


def handle_owner_command(from_number, body):
    cmd = body.strip().upper()
    parts = body.strip().split(" ")

    if cmd == "LEADS":
        leads = get_all_leads()
        new_leads = [l for l in leads if l['status'] == 'new']
        if not new_leads:
            return "No new leads."
        summary = f"{len(new_leads)} new leads:\n"
        for l in new_leads[:5]:
            urgent = "URGENT " if l['urgent'] else ""
            summary += f"{urgent}{l['name']} - {l['contact_phone'] or l['phone']}\n"
        return summary.strip()

    if cmd.startswith("APPROVE") and len(parts) >= 4:
        customer_phone = parts[1]
        try:
            low = int(parts[2])
            high = int(parts[3])
        except:
            return "Usage: APPROVE +1xxxxxxxxxx 150 300"
        lead = get_lead_by_phone(customer_phone)
        name = lead['name'] if lead else "there"
        result = send_quote_to_customer(customer_phone, name, low, high)
        return f"Quote sent to {name}: ${low}-${high} CAD" if result else "Failed to send quote"

    if cmd.startswith("DONE") and len(parts) >= 2:
        customer_phone = parts[1]
        lead = get_lead_by_phone(customer_phone)
        if lead:
            update_lead_status(lead['id'], 'done')
            return f"Done: {lead['name']}"
        return f"No lead found for {customer_phone}"

    if cmd.startswith("QUOTE") and len(parts) >= 2:
        customer_phone = parts[1]
        lead = get_lead_by_phone(customer_phone)
        if not lead:
            return f"No lead found for {customer_phone}"
        msg = f"Hi {lead['name']}, {BUSINESS_NAME} here. How long has the issue been happening and have you tried anything to fix it?"
        try:
            twilio_client.messages.create(body=msg, from_=TWILIO_PHONE, to=customer_phone)
            return f"Quote questions sent to {lead['name']}"
        except Exception as e:
            return f"Error: {e}"

    return None


# ── SMS ────────────────────────────────────────────────────────────────────

@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    print(f"SMS from {from_number}: {incoming_msg}")

    resp = MessagingResponse()

    if is_owner(from_number):
        print(f"Owner command: {incoming_msg}")
        result = handle_owner_command(from_number, incoming_msg)
        if result:
            resp.message(result)
            return str(resp)

    reply = get_agent_response(from_number, incoming_msg)
    print(f"Reply: {reply[:80]}")
    resp.message(reply)
    return str(resp)


# ── VOICE — Entry point ────────────────────────────────────────────────────

@app.route("/voice", methods=["POST"])
def voice_entry():
    """
    Twilio calls this when a call arrives.
    We return TwiML that connects to ConversationRelay via WebSocket.
    """
    caller = request.form.get("From", "unknown")
    call_sid = request.form.get("CallSid", "")
    print(f"Incoming call from {caller} — CallSid: {call_sid}")

    # Build the WebSocket URL for ConversationRelay
    # Render uses https — we need wss://
    if BASE_URL:
        ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/voice-ws"
    else:
        host = request.host
        ws_url = f"wss://{host}/voice-ws"

    print(f"ConversationRelay WebSocket URL: {ws_url}")

    response = VoiceResponse()
    connect = Connect()
    # ConversationRelay handles STT+TTS and gives us text via WebSocket
    connect.conversation_relay(
        url=ws_url,
        language="en-CA",
        tts_provider="google",       # or "amazon" — google has good en-CA
        voice="en-CA-Neural2-C",     # female Canadian English voice
        transcription_provider="google",
        speech_model="telephony",
        welcome_greeting=(
            f"Thank you for calling {BUSINESS_NAME}, you've reached our answering service. "
            f"I can take your details and have someone call you right back. "
            f"What's your name please?"
        )
    )
    response.append(connect)

    twiml = str(response)
    print(f"TwiML: {twiml}")
    return twiml, 200, {"Content-Type": "text/xml"}


# ── VOICE — WebSocket handler ──────────────────────────────────────────────

@app.route("/voice-ws", websocket=True)
def voice_ws():
    """
    ConversationRelay WebSocket endpoint.
    Receives transcribed text from caller, sends back agent responses as text
    (ConversationRelay handles TTS).
    """
    if WS_LIB == "simple_websocket":
        return _handle_ws_simple_websocket()
    else:
        return _handle_ws_flask_sock()


def _handle_ws_simple_websocket():
    """Handler using simple_websocket library"""
    from simple_websocket import Server as WSServer
    ws = WSServer(request.environ)
    caller_phone = "unknown"

    try:
        from voice_agent import handle_conversation_relay
        handle_conversation_relay(ws, caller_phone)
    except Exception as e:
        print(f"WebSocket error: {e}")
    return ""


def _handle_ws_flask_sock():
    """Handler using flask-sock library"""
    try:
        from flask_sock import Sock
        # This branch is handled by the sock decorator below if flask-sock is installed
        pass
    except ImportError:
        pass
    return "WebSocket library not available", 500


# ── flask-sock alternative (install flask-sock in requirements) ────────────
try:
    from flask_sock import Sock
    sock = Sock(app)

    @sock.route("/voice-ws")
    def voice_ws_sock(ws):
        """
        Main WebSocket handler for Twilio ConversationRelay.
        ConversationRelay sends JSON messages with transcribed caller text.
        We respond with JSON containing agent text → ConversationRelay does TTS.
        """
        caller_phone = "unknown"
        print("ConversationRelay WebSocket connected")

        try:
            # First message from ConversationRelay is always 'setup'
            raw = ws.receive(timeout=10)
            if raw:
                setup = json.loads(raw)
                if setup.get("type") == "setup":
                    caller_phone = setup.get("from", "unknown")
                    call_sid = setup.get("callSid", "")
                    print(f"Call setup — caller: {caller_phone}, sid: {call_sid}")

            # Main conversation loop
            from voice_agent import handle_conversation_relay
            handle_conversation_relay(ws, caller_phone)

        except Exception as e:
            print(f"voice_ws_sock error: {e}")
            import traceback
            traceback.print_exc()

    print("flask-sock WebSocket handler registered at /voice-ws")

except ImportError:
    print("flask-sock not installed — install it: pip install flask-sock")
    import json  # ensure json is imported for other routes


import json  # safety import


# ── Standard routes ────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return f"Tradie Agent v6 — Voice enabled. WS_LIB: {WS_LIB}", 200


@app.route("/leads", methods=["GET"])
def leads_dashboard():
    leads = get_all_leads()
    total = len(leads)
    urgent = sum(1 for l in leads if l['urgent'])
    new = sum(1 for l in leads if l['status'] == 'new')

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Tradie Agent Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        .lead {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .urgent {{ border-left: 4px solid #e74c3c; }}
        .new {{ border-left: 4px solid #3498db; }}
        .done {{ border-left: 4px solid #2ecc71; opacity: 0.7; }}
        .voice {{ border-top: 2px dashed #9b59b6; }}
        .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; margin-left: 8px; }}
        .badge-urgent {{ background: #e74c3c; color: white; }}
        .badge-new {{ background: #3498db; color: white; }}
        .badge-done {{ background: #2ecc71; color: white; }}
        .badge-voice {{ background: #9b59b6; color: white; }}
        .meta {{ color: #888; font-size: 13px; margin-top: 8px; }}
        .stats {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
        .stat {{ background: white; padding: 15px 25px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-number {{ font-size: 32px; font-weight: bold; color: #333; }}
        .stat-label {{ color: #888; font-size: 13px; }}
        .commands {{ background: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; }}
        code {{ background: #eee; padding: 2px 6px; border-radius: 3px; }}
    </style>
</head>
<body>
    <h1>🔧 Tradie Agent Dashboard</h1>
    <div class="stats">
        <div class="stat"><div class="stat-number">{total}</div><div class="stat-label">Total Leads</div></div>
        <div class="stat"><div class="stat-number">{urgent}</div><div class="stat-label">🚨 Urgent</div></div>
        <div class="stat"><div class="stat-number">{new}</div><div class="stat-label">New</div></div>
    </div>
    <div class="commands">
        <strong>SMS Commands from your mobile:</strong><br>
        <code>LEADS</code> — view new leads &nbsp;|&nbsp;
        <code>QUOTE +1xxx</code> — ask job details &nbsp;|&nbsp;
        <code>APPROVE +1xxx 150 300</code> — send quote &nbsp;|&nbsp;
        <code>DONE +1xxx</code> — mark complete
    </div>
"""

    if not leads:
        html += "<p>No leads yet.</p>"

    for lead in leads:
        urgent_class = "urgent" if lead['urgent'] else ("done" if lead['status'] == 'done' else "new")
        badge_class = "badge-urgent" if lead['urgent'] else ("badge-done" if lead['status'] == 'done' else "badge-new")
        badge_text = "URGENT" if lead['urgent'] else lead['status'].upper()
        channel = lead.get('channel', 'sms')
        channel_badge = f'<span class="badge badge-voice">📞 VOICE</span>' if channel == 'voice' else '<span class="badge badge-new">💬 SMS</span>'
        html += f"""
    <div class="lead {urgent_class}">
        <strong>{lead['name'] or 'Unknown'}</strong>
        <span class="badge {badge_class}">{badge_text}</span>
        {channel_badge}
        <div style="margin-top:5px">{lead['problem'] or ''}</div>
        <div class="meta">
            📍 {lead['address'] or 'No address'} &nbsp;|&nbsp;
            📞 {lead['contact_phone'] or lead['phone']} &nbsp;|&nbsp;
            📱 {lead['phone']} &nbsp;|&nbsp;
            🕐 {lead['created_at']}
        </div>
    </div>"""

    html += "</body></html>"
    return html


@app.route("/leads/<int:lead_id>/done", methods=["POST"])
def mark_done(lead_id):
    update_lead_status(lead_id, "done")
    return jsonify({"status": "ok"})


@app.route("/test-sms", methods=["GET"])
def test_sms():
    try:
        result = twilio_client.messages.create(
            body="Test from Tradie Agent v6 — Voice enabled",
            from_=TWILIO_PHONE,
            to=OWNER_PHONE
        )
        return f"SMS sent! SID: {result.sid}", 200
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route("/test-voice", methods=["GET"])
def test_voice():
    """Outbound call to owner using ConversationRelay — real agent test"""
    try:
        if BASE_URL:
            ws_url = BASE_URL.replace("https://", "wss://") + "/voice-ws"
        else:
            ws_url = f"wss://{request.host}/voice-ws"

        call = twilio_client.calls.create(
            to=OWNER_PHONE,
            from_=TWILIO_PHONE,
            twiml=f"""<Response>
                <Connect>
                    <ConversationRelay url="{ws_url}"
                        language="en-CA"
                        ttsProvider="google"
                        voice="en-CA-Neural2-C"
                        transcriptionProvider="google"
                        speechModel="telephony"
                        welcomeGreeting="Thank you for calling {BUSINESS_NAME}, you've reached our answering service. I can take your details and have someone call you right back. What's your name please?" />
                </Connect>
            </Response>"""
        )
        return f"Test call initiated! SID: {call.sid}", 200
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route("/test-db", methods=["GET"])
def test_db():
    try:
        import psycopg2
        url = os.environ.get("DATABASE_URL", "NOT SET")
        conn = psycopg2.connect(url)
        c = conn.cursor()
        c.execute("INSERT INTO messages (phone, role, content) VALUES (%s, %s, %s)", ("+34000", "user", "test"))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM messages")
        count = c.fetchone()[0]
        conn.close()
        return jsonify({"success": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug/<path:phone>", methods=["GET"])
def debug_lead(phone):
    from database import get_conversation
    from agent import extract_lead_data
    history = get_conversation(phone)
    data = extract_lead_data(phone)
    return jsonify({"messages": len(history), "extractor": data})


@app.route("/migrate", methods=["GET"])
def migrate():
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        c = conn.cursor()
        # Add channel column to leads if not exists
        c.execute("""
            ALTER TABLE leads
            ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'sms'
        """)
        conn.commit()
        conn.close()
        return "Migration done — channel column added", 200
    except Exception as e:
        return f"Done (may already exist): {str(e)}", 200


if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))