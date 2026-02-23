from openai import OpenAI
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
from database import save_message, get_conversation, save_lead, save_quote, get_lead_by_phone
import os
import json

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")
BUSINESS_OWNER = os.getenv("BUSINESS_OWNER", "Mike")
OWNER_PHONE = os.getenv("OWNER_PHONE")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")

print(f"Agent initialized: {BUSINESS_NAME}")
print(f"Owner phone: {OWNER_PHONE}")
print(f"Twilio phone: {TWILIO_PHONE}")

SYSTEM_PROMPT = f"""You are the assistant for {BUSINESS_NAME}, run by {BUSINESS_OWNER}, an emergency plumber in Australia.

PHASE 1 - LEAD CAPTURE: Collect these 3 pieces of information:
1. Customer full name
2. Full address
3. Contact phone number

PHASE 2 - QUOTE: Once you have all 3, ask 1-2 quick questions to understand the job better so you can give a rough estimate. For example: "How long has the pipe been leaking?" or "Is the water currently shut off?"

PHASE 3 - CONFIRM: Tell them Mike will call back within 15 minutes to confirm the exact price after seeing the job.

RULES:
- Casual Australian English. Use: mate, no worries, cheers, reckon, arvo etc.
- Max 2 sentences per reply. This is SMS.
- Never reveal you are an AI.
- If urgent (flooding, burst pipe, no hot water, gas leak, sewage) start reply with [URGENT]
- Never give a specific price, only say Mike will confirm after inspection"""

EXTRACTOR_PROMPT = """Analyze this conversation and extract data.

Respond with ONLY this JSON, no other text:
{
  "lead_captured": true/false,
  "name": "full name or null",
  "address": "full address or null", 
  "phone": "phone number or null",
  "problem": "problem description or null",
  "urgent": true/false,
  "ready_for_quote": true/false,
  "job_details": "any additional details about the job or null"
}

lead_captured is true only when we have name, address AND phone.
ready_for_quote is true when we have lead_captured AND at least one follow-up question about the job has been answered.
urgent is true if: flooding, burst pipe, no hot water, gas leak, sewage."""

notified_conversations = set()
quoted_conversations = set()


def notify_owner(lead_data, customer_phone):
    if not OWNER_PHONE or not TWILIO_PHONE:
        print("ERROR: OWNER_PHONE or TWILIO_PHONE not set")
        return
    urgent_tag = "URGENT" if lead_data.get("urgent") else "New Lead"
    message = (
        f"{urgent_tag} - {BUSINESS_NAME}\n"
        f"Problem: {lead_data.get('problem', 'Unknown')}\n"
        f"Name: {lead_data.get('name', 'Unknown')}\n"
        f"Address: {lead_data.get('address', 'Unknown')}\n"
        f"Phone: {lead_data.get('phone', customer_phone)}\n"
        f"Reply to: {customer_phone}"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=OWNER_PHONE)
        print(f"Lead notification sent. SID: {result.sid}")
    except Exception as e:
        print(f"Failed to notify owner: {e}")


def send_quote_to_owner(quote_data, customer_phone, lead_id):
    if not OWNER_PHONE or not TWILIO_PHONE:
        return
    message = (
        f"QUOTE REQUEST - {BUSINESS_NAME}\n"
        f"Customer: {quote_data.get('name')}\n"
        f"Job: {quote_data.get('problem')}\n"
        f"Details: {quote_data.get('job_details', 'None')}\n"
        f"Address: {quote_data.get('address')}\n"
        f"Reply APPROVE to send quote, or DECLINE"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=OWNER_PHONE)
        print(f"Quote request sent. SID: {result.sid}")
    except Exception as e:
        print(f"Failed to send quote request: {e}")


def extract_conversation_data(phone):
    history = get_conversation(phone)
    if len(history) < 2:
        return None
    history_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history])
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": EXTRACTOR_PROMPT},
                {"role": "user", "content": f"Conversation:\n{history_text}"}
            ],
            temperature=0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Extractor error: {e}")
    return None


def get_agent_response(phone_number, customer_message):
    save_message(phone_number, "user", customer_message)
    history = get_conversation(phone_number)

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
    )

    agent_reply = response.choices[0].message.content
    save_message(phone_number, "assistant", agent_reply)

    data = extract_conversation_data(phone_number)
    if not data:
        return agent_reply

    # Phase 1: Lead captured - notify owner once
    if data.get("lead_captured") and phone_number not in notified_conversations:
        lead_id = save_lead(phone_number, data)
        notify_owner(data, phone_number)
        notified_conversations.add(phone_number)
        print(f"Lead saved: {data.get('name')}")

    # Phase 2: Ready for quote - send quote request to owner once
    if data.get("ready_for_quote") and phone_number not in quoted_conversations:
        lead = get_lead_by_phone(phone_number)
        lead_id = lead["id"] if lead else None
        quote_id = save_quote(
            phone_number, lead_id,
            data.get("problem"), 0, 0,
            data.get("job_details", "")
        )
        send_quote_to_owner(data, phone_number, lead_id)
        quoted_conversations.add(phone_number)
        print(f"Quote request sent for: {data.get('name')}")

    return agent_reply