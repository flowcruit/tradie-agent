import sys
sys.stdout = sys.stderr

from openai import OpenAI
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
from database import save_message, get_conversation, save_lead, get_lead_by_phone
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

SYSTEM_PROMPT = f"""You are the assistant for {BUSINESS_NAME}, run by {BUSINESS_OWNER}, an emergency plumber serving Canadian homeowners.

Your job:
1. Collect customer full name, full address (including city and province), and contact phone number
2. Once collected, ask 1 brief follow-up question about the job to help {BUSINESS_OWNER} prepare
3. Let them know {BUSINESS_OWNER} will call back within 15 minutes to confirm pricing after assessment

RULES:
- Friendly, professional Canadian English. You can say: thanks, sorry to hear that, absolutely, for sure, no problem
- Keep responses to 2-3 sentences max. This is SMS.
- Never reveal you are an AI.
- If urgent (flooding, burst pipe, no hot water, gas leak, sewage, frozen pipes, water heater failure) start reply with [URGENT]
- Never give specific prices. {BUSINESS_OWNER} confirms pricing after seeing the job.
- Use CAD when referencing currency if needed.
- Always confirm the city and province if the customer only gives a street address."""

EXTRACTOR_PROMPT = """Extract data from this conversation. Respond ONLY with JSON, no other text:
{
  "lead_captured": true/false,
  "name": "full name or null",
  "address": "full address or null",
  "phone": "phone number or null",
  "problem": "problem description or null",
  "urgent": true/false
}
lead_captured is true only when we have name AND address AND phone.
urgent is true if: flooding, burst pipe, no hot water, gas leak, sewage, frozen pipes, water heater failure."""

notified_conversations = set()


def normalize_phone(phone):
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


def notify_owner(lead_data, customer_phone):
    if not OWNER_PHONE or not TWILIO_PHONE:
        print("ERROR: Missing OWNER_PHONE or TWILIO_PHONE")
        return False
    urgent = "URGENT" if lead_data.get("urgent") else "New Lead"
    message = (
        f"{urgent}: {lead_data.get('name')}\n"
        f"Problem: {lead_data.get('problem', '')}\n"
        f"Address: {lead_data.get('address', '')}\n"
        f"Tel: {lead_data.get('phone', customer_phone)}\n"
        f"SMS: {customer_phone}\n\n"
        f"Reply: APPROVE {customer_phone} 150 300 — send quote\n"
        f"DONE {customer_phone} — mark complete"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=OWNER_PHONE)
        print(f"Owner notified. SID: {result.sid}")
        return True
    except Exception as e:
        print(f"Failed to notify owner: {e}")
        return False


def send_quote_to_customer(customer_phone, name, low, high):
    if not TWILIO_PHONE:
        return False
    message = (
        f"Hi {name}, {BUSINESS_NAME} here.\n"
        f"Based on your description, we estimate ${low}-${high} CAD for the job (subject to on-site assessment).\n"
        f"{BUSINESS_OWNER} will confirm the exact price when he arrives. He'll call you shortly to confirm the time!"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=customer_phone)
        print(f"Quote sent. SID: {result.sid}")
        return True
    except Exception as e:
        print(f"Failed to send quote: {e}")
        return False


def extract_lead_data(phone):
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
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Extractor error: {e}")
    return None


def get_agent_response(phone_number, customer_message):
    phone_number = normalize_phone(phone_number)
    print(f"Processing message from {phone_number}")

    save_message(phone_number, "user", customer_message)
    history = get_conversation(phone_number)
    print(f"Conversation history: {len(history)} messages")

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
    )

    agent_reply = response.choices[0].message.content
    save_message(phone_number, "assistant", agent_reply)

    if phone_number not in notified_conversations:
        data = extract_lead_data(phone_number)
        print(f"Extractor result: {data}")
        if data and data.get("lead_captured"):
            save_lead(phone_number, data)
            success = notify_owner(data, phone_number)
            if success:
                notified_conversations.add(phone_number)
                print(f"Lead captured and owner notified: {data.get('name')}")

    return agent_reply