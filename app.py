from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
from database import get_all_leads, update_lead_status, init_db, get_lead_by_phone, get_conversation
from agent import get_agent_response, send_quote_to_customer, openai_client
import os

load_dotenv()

app = Flask(__name__)
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

OWNER_PHONE = os.getenv("OWNER_PHONE", "")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")

print("APP V4 LOADED - with owner commands")

def is_owner(phone):
    """Check if the sender is the business owner."""
    return phone.replace("+", "").replace(" ", "") in OWNER_PHONE.replace("+", "").replace(" ", "")

def handle_owner_command(command, body):
    """Process commands from Mike."""
    cmd = body.strip().upper()
    
    # QUOTE <phone> — generate quote for a customer
    if cmd.startswith("QUOTE"):
        parts = body.strip().split(" ", 1)
        if len(parts) < 2:
            return "Usage: QUOTE +34655174298"
        customer_phone = parts[1].strip()
        lead = get_lead_by_phone(customer_phone)
        if not lead:
            return f"No lead found for {customer_phone}"
        # Send quote request to customer asking for job details
        message = (
            f"Hi {lead['name']}, this is {BUSINESS_NAME}. "
            f"To prepare your quote, can you tell me: "
            f"1) How long has the issue been happening? "
            f"2) Have you tried anything to fix it?"
        )
        try:
            twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=customer_phone)
            return f"Quote questions sent to {lead['name']} at {customer_phone}"
        except Exception as e:
            return f"Error: {e}"
    
    # APPROVE <phone> <low> <high> — send quote to customer
    if cmd.startswith("APPROVE"):
        parts = body.strip().split(" ")
        if len(parts) < 4:
            return "Usage: APPROVE +34655174298 150 300"
        customer_phone = parts[1]
        try:
            low = int(parts[2])
            high = int(parts[3])
        except:
            return "Price must be numbers. Usage: APPROVE +34655174298 150 300"
        lead = get_lead_by_phone(customer_phone)
        name = lead['name'] if lead else "mate"
        result = send_quote_to_customer(customer_phone, name, low, high)
        if result:
            return f"Quote sent to {name}: ${low}-${high} AUD"
        return "Failed to send quote"
    
    # DONE <phone> — mark lead as completed
    if cmd.startswith("DONE"):
        parts = body.strip().split(" ", 1)
        if len(parts) < 2:
            return "Usage: DONE +34655174298"
        customer_phone = parts[1].strip()
        lead = get_lead_by_phone(customer_phone)
        if lead:
            update_lead_status(lead['id'], 'done')
            return f"Lead marked as done for {lead['name']}"
        return f"No lead found for {customer_phone}"

    # LEADS — show summary
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

    return None


@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    print(f"Incoming from {from_number}: {incoming_msg}")

    resp = MessagingResponse()

    # Check if it's Mike sending a command
    if is_owner(from_number):
        result = handle_owner_command(from_number, incoming_msg)
        if result:
            resp.message(result)
            return str(resp)

    # Otherwise it's a customer
    reply = get_agent_response(from_number, incoming_msg)
    print(f"Reply: {reply[:100]}")
    resp.message(reply)
    return str(resp)


@app.route("/health", methods=["GET"])
def health():
    return "Tradie Agent v4 - running!", 200


@app.route("/leads", methods=["GET"])
def leads_dashboard():
    leads = get_all_leads()
    total = len(leads)
    urgent = sum(1 for l in leads if l['urgent'])
    new = sum(1 for l in leads if l['status'] == 'new')

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Tradie Agent - Leads</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        .lead {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .urgent {{ border-left: 4px solid #e74c3c; }}
        .new {{ border-left: 4px solid #3498db; }}
        .done {{ border-left: 4px solid #2ecc71; opacity: 0.7; }}
        .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; margin-left: 8px; }}
        .badge-urgent {{ background: #e74c3c; color: white; }}
        .badge-new {{ background: #3498db; color: white; }}
        .badge-done {{ background: #2ecc71; color: white; }}
        .meta {{ color: #888; font-size: 13px; margin-top: 8px; }}
        .stats {{ display: flex; gap: 20px; margin-bottom: 20px; }}
        .stat {{ background: white; padding: 15px 25px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-number {{ font-size: 32px; font-weight: bold; color: #333; }}
        .stat-label {{ color: #888; font-size: 13px; }}
        .commands {{ background: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; }}
        .commands code {{ background: #eee; padding: 2px 6px; border-radius: 3px; }}
    </style>
</head>
<body>
    <h1>Tradie Agent Dashboard</h1>
    <div class="stats">
        <div class="stat"><div class="stat-number">{total}</div><div class="stat-label">Total Leads</div></div>
        <div class="stat"><div class="stat-number">{urgent}</div><div class="stat-label">Urgent</div></div>
        <div class="stat"><div class="stat-number">{new}</div><div class="stat-label">New</div></div>
    </div>
    <div class="commands">
        <strong>SMS Commands (send from your mobile):</strong><br>
        <code>LEADS</code> — see new leads &nbsp;|&nbsp;
        <code>QUOTE +61xxxxxxxxx</code> — request job details &nbsp;|&nbsp;
        <code>APPROVE +61xxxxxxxxx 150 300</code> — send quote $150-$300 &nbsp;|&nbsp;
        <code>DONE +61xxxxxxxxx</code> — mark complete
    </div>
"""

    if not leads:
        html += "<p>No leads yet. Waiting for the first customer...</p>"

    for lead in leads:
        urgent_class = "urgent" if lead['urgent'] else ("done" if lead['status'] == 'done' else "new")
        badge_class = "badge-urgent" if lead['urgent'] else ("badge-done" if lead['status'] == 'done' else "badge-new")
        badge_text = "URGENT" if lead['urgent'] else lead['status'].upper()

        html += f"""
    <div class="lead {urgent_class}">
        <strong>{lead['name'] or 'Unknown'}</strong>
        <span class="badge {badge_class}">{badge_text}</span>
        <div style="margin-top:5px">{lead['problem'] or 'No problem description'}</div>
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
            body="Test from Render",
            from_=TWILIO_PHONE,
            to=OWNER_PHONE
        )
        return f"SMS sent! SID: {result.sid}", 200
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/debug/<phone>", methods=["GET"])
def debug_lead(phone):
    from database import get_conversation
    from agent import extract_lead_data
    history = get_conversation(phone)
    data = extract_lead_data(phone)
    return jsonify({"messages": len(history), "extractor": data})

@app.route("/test-db", methods=["GET"])
def test_db():
    try:
        import psycopg2
        import os
        url = os.environ.get("DATABASE_URL", "NOT SET")
        conn = psycopg2.connect(url)
        c = conn.cursor()
        c.execute("INSERT INTO messages (phone, role, content) VALUES (%s, %s, %s)", ("+34000", "user", "test"))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM messages")
        count = c.fetchone()[0]
        conn.close()
        return jsonify({"success": True, "count": count, "url_prefix": url[:30]})
    except Exception as e:
        return jsonify({"error": str(e), "url": os.environ.get("DATABASE_URL", "NOT SET")[:30]}), 500

if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))