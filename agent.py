from openai import OpenAI
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
import os
import json

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")
BUSINESS_OWNER = os.getenv("BUSINESS_OWNER", "Mike")
OWNER_PHONE = os.getenv("OWNER_PHONE")  # Mike's personal mobile
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")

SYSTEM_PROMPT = f"""You are the assistant for {BUSINESS_NAME}, run by {BUSINESS_OWNER}, an emergency plumber in Australia.

Your job:
1. Greet warmly in casual Australian English
2. Find out the exact plumbing problem
3. Get their full address and best contact number
4. Let them know {BUSINESS_OWNER} will call back within 15 minutes

Rules:
- Max 2-3 sentences per reply. This is SMS.
- Never say you are an AI.
- Use casual Australian English (mate, no worries, reckon, etc.)
- If urgent (flooding, burst pipe, no hot water, gas leak, sewage) start reply with [URGENT]
- Once you have their name, address and phone number, end with [LEAD_CAPTURED]

After capturing a lead, include this JSON on a new line (hidden from customer):
LEAD_DATA:{{"name":"<name>","address":"<address>","phone":"<phone>","problem":"<problem>","urgent":<true/false>}}"""

conversation_history = {}

def notify_owner(lead_data, customer_phone):
    """Send SMS to Mike with lead details."""
    if not OWNER_PHONE:
        return
    
    urgent_tag = "🚨 URGENT" if lead_data.get("urgent") else "📞 New Lead"
    message = (
        f"{urgent_tag} - {BUSINESS_NAME}\n"
        f"Problem: {lead_data.get('problem', 'Unknown')}\n"
        f"Name: {lead_data.get('name', 'Unknown')}\n"
        f"Address: {lead_data.get('address', 'Unknown')}\n"
        f"Phone: {lead_data.get('phone', customer_phone)}\n"
        f"Reply to customer: {customer_phone}"
    )
    
    try:
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE,
            to=OWNER_PHONE
        )
    except Exception as e:
        print(f"Failed to notify owner: {e}")

def extract_lead_data(response_text):
    """Extract lead JSON from agent response if present."""
    if "LEAD_DATA:" not in response_text:
        return None
    try:
        json_str = response_text.split("LEAD_DATA:")[1].strip().split("\n")[0]
        return json.loads(json_str)
    except:
        return None

def clean_response(response_text):
    """Remove internal data from customer-facing message."""
    if "LEAD_DATA:" in response_text:
        response_text = response_text.split("LEAD_DATA:")[0].strip()
    return response_text

def get_agent_response(phone_number, customer_message):
    if phone_number not in conversation_history:
        conversation_history[phone_number] = []

    conversation_history[phone_number].append({
        "role": "user",
        "content": customer_message
    })

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT}
        ] + conversation_history[phone_number]
    )

    agent_reply = response.choices[0].message.content

    # Check if lead was captured and notify Mike
    lead_data = extract_lead_data(agent_reply)
    if lead_data:
        notify_owner(lead_data, phone_number)

    # Clean response before sending to customer
    clean_reply = clean_response(agent_reply)

    conversation_history[phone_number].append({
        "role": "assistant",
        "content": clean_reply
    })

    return clean_reply
