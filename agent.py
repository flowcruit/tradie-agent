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

SYSTEM_PROMPT = f"""You are the assistant for {BUSINESS_NAME}, run by {BUSINESS_OWNER}, an emergency plumber in Australia.

Your job:
1. Collect customer full name, address and contact phone number
2. Once collected, ask 1 follow-up question about the job (e.g. how long, severity)
3. Tell them Mike will call back within 15 minutes to confirm price after inspection

RULES:
- Casual Australian English: mate, no worries, cheers, reckon, arvo
- Max 2 sentences. This is SMS.
- Never reveal you are an AI.
- If urgent (flooding, burst pipe, no hot water, gas leak, sewage) start with [URGENT]
- Never give specific prices. Mike confirms after seeing the job."""

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
urgent is true if: flooding, burst pipe, no hot water, gas leak, sewage."""

notified_conversations = set()


def notify_owner(lead_data, customer_phone):
    if not OWNER_PHONE or not TWILIO_PHONE:
        print("ERROR: Missing OWNER_PHONE or TWILIO_PHONE")
        return
    urgent_tag = "URGENT" if lead_data.get("urgent") else "New Lead"
    message = (
        f"{urgent_tag} - {BUSINESS_NAME}\n"
        f"Problem: {lead_data.get('problem', 'Unknown')}\n"
        f"Name: {lead_data.get('name', 'Unknown')}\n"
        f"Address: {lead_data.get('address', 'Unknown')}\n"
        f"Phone: {lead_data.get('phone', customer_phone)}\n"
        f"Customer SMS: {customer_phone}\n\n"
        f"Reply: QUOTE {customer_phone} to request details\n"
        f"Reply: APPROVE {customer_phone} 150 300 to send quote"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=OWNER_PHONE)
        print(f"Lead notification sent. SID: {result.sid}")
    except Exception as e:
        print(f"Failed to notify owner: {e}")


def send_quote_to_customer(customer_phone, name, low, high):
    if not TWILIO_PHONE:
        return False
    message = (
        f"Hi {name}, this is {BUSINESS_NAME}.\n"
        f"Based on what you've described, the estimated cost is ${low}-${high} AUD.\n"
        f"This is subject to physical inspection. Mike will confirm the exact price on arrival.\n"
        f"He'll call you within 15 minutes to confirm. No worries!"
    )
    try:
        result = twilio_client.messages.create(body=message, from_=TWILIO_PHONE, to=customer_phone)
        print(f"Quote sent to customer. SID: {result.sid}")
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

    if phone_number not in notified_conversations:
        data = extract_lead_data(phone_number)
        if data and data.get("lead_captured"):
            save_lead(phone_number, data)
            notify_owner(data, phone_number)
            notified_conversations.add(phone_number)
            print(f"Lead captured and owner notified: {data.get('name')}")

    return agent_reply