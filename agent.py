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

If we have all three, respond with ONLY this JSON (no other text):
{"captured": true, "name": "...", "address": "...", "phone": "...", "problem": "...", "urgent": true/false}

If we are missing any of the three, respond with ONLY:
{"captured": false}

urgent is true if the problem involves: flooding, burst pipe, no hot water, gas leak, sewage."""

conversation_history = {}


def check_lead_captured(phone_number):
    """Use a separate AI call to extract lead data from conversation."""
    if phone_number not in conversation_history or len(conversation_history[phone_number]) < 2:
        return None

    history_text = "\n".join([
        f"{m['role'].upper()}: {m['content']}"
        for m in conversation_history[phone_number]
    ])

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


def notify_owner(lead_data, customer_phone):
    if not OWNER_PHONE:
        print("ERROR: OWNER_PHONE not set")
        return
    if not TWILIO_PHONE:
        print("ERROR: TWILIO_PHONE_NUMBER not set")
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
        print(f"Sending notification to {OWNER_PHONE} from {TWILIO_PHONE}")
        result = twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE,
            to=OWNER_PHONE
        )
        print(f"Notification sent. SID: {result.sid}")
    except Exception as e:
        print(f"Failed to notify owner: {e}")


# Track which conversations have already triggered notification
notified_conversations = set()


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
    print(f"Agent reply: {agent_reply}")

    conversation_history[phone_number].append({
        "role": "assistant",
        "content": agent_reply
    })

    # Check if lead is captured (only notify once per conversation)
    if phone_number not in notified_conversations:
        lead_data = check_lead_captured(phone_number)
        if lead_data:
            print(f"Lead captured: {lead_data}")
            notify_owner(lead_data, phone_number)
            notified_conversations.add(phone_number)

    return agent_reply