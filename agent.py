from openai import OpenAI
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
from database import save_message, get_conversation, save_lead
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

Your job is to collect 3 pieces of information from the customer:
1. Their full name
2. Their full address
3. Their contact phone number

Once you have all 3, confirm that {BUSINESS_OWNER} will call them back within 15 minutes.

RULES:
- Casual Australian English. Use: mate, no worries, cheers, reckon etc.
- Max 2 sentences per reply. This is SMS.
- Never reveal you are an AI.
- If the problem is urgent (flooding, burst pipe, no hot water, gas leak, sewage) start your reply with [URGENT]"""

EXTRACTOR_PROMPT = """You are a data extractor. Analyze the conversation and determine if we have collected the customer's name, address AND phone number.

If we have all three, respond with ONLY this JSON:
{"captured": true, "name": "...", "address": "...", "phone": "...", "problem": "...", "urgent": true}

If missing any, respond with ONLY:
{"captured": false}

urgent is true if: flooding, burst pipe, no hot water, gas leak, sewage."""

notified_conversations = set()


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
        print(f"Notification sent. SID: {result.sid}")
    except Exception as e:
        print(f"Failed to notify owner: {e}")


def check_lead_captured(phone):
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
        result = json.loads(response.choices[0].message.content)
        if result.get("captured"):
            return result
    except Exception as e:
        print(f"Extractor error: {e}")
    return None


def get_agent_response(phone_number, customer_message):
    # Save incoming message
    save_message(phone_number, "user", customer_message)
    
    # Get full conversation history from DB
    history = get_conversation(phone_number)

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
    )

    agent_reply = response.choices[0].message.content
    
    # Save agent reply
    save_message(phone_number, "assistant", agent_reply)

    # Check if lead captured (only notify once)
    if phone_number not in notified_conversations:
        lead_data = check_lead_captured(phone_number)
        if lead_data:
            save_lead(phone_number, lead_data)
            notify_owner(lead_data, phone_number)
            notified_conversations.add(phone_number)
            print(f"Lead saved and owner notified: {lead_data.get('name')}")

    return agent_reply