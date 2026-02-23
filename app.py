from flask import Flask, request, jsonify, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
from database import get_all_leads, update_lead_status, init_db
from agent import get_agent_response
import os

load_dotenv()

app = Flask(__name__)
print("APP V3 LOADED - with database")

@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    print(f"Incoming from {from_number}: {incoming_msg}")
    reply = get_agent_response(from_number, incoming_msg)
    print(f"Reply: {reply[:100]}")
    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route("/health", methods=["GET"])
def health():
    return "Tradie Agent v3 - running!", 200

@app.route("/leads", methods=["GET"])
def leads_dashboard():
    leads = get_all_leads()
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Tradie Agent - Leads</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
            h1 { color: #333; }
            .lead { background: white; padding: 15px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .urgent { border-left: 4px solid #e74c3c; }
            .new { border-left: 4px solid #3498db; }
            .done { border-left: 4px solid #2ecc71; opacity: 0.7; }
            .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
            .badge-urgent { background: #e74c3c; color: white; }
            .badge-new { background: #3498db; color: white; }
            .badge-done { background: #2ecc71; color: white; }
            .meta { color: #888; font-size: 13px; margin-top: 5px; }
            .stats { display: flex; gap: 20px; margin-bottom: 20px; }
            .stat { background: white; padding: 15px 25px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .stat-number { font-size: 32px; font-weight: bold; color: #333; }
            .stat-label { color: #888; font-size: 13px; }
        </style>
    </head>
    <body>
        <h1>Tradie Agent Dashboard</h1>
    """
    
    total = len(leads)
    urgent = sum(1 for l in leads if l['urgent'])
    new = sum(1 for l in leads if l['status'] == 'new')
    
    html += f"""
        <div class="stats">
            <div class="stat"><div class="stat-number">{total}</div><div class="stat-label">Total Leads</div></div>
            <div class="stat"><div class="stat-number">{urgent}</div><div class="stat-label">Urgent</div></div>
            <div class="stat"><div class="stat-number">{new}</div><div class="stat-label">New</div></div>
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
            <div>{lead['problem'] or 'No problem description'}</div>
            <div class="meta">
                📍 {lead['address'] or 'No address'} &nbsp;|&nbsp;
                📞 {lead['contact_phone'] or lead['phone']} &nbsp;|&nbsp;
                🕐 {lead['created_at']}
            </div>
        </div>
        """
    
    html += "</body></html>"
    return html

@app.route("/leads/<int:lead_id>/done", methods=["POST"])
def mark_done(lead_id):
    update_lead_status(lead_id, "done")
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
