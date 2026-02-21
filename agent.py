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

Your job:
1. Greet warmly in casual Australian English
2. Find out the exact plumbing problem
3. Get their full name, address and best contact number
4. Let them know {BUSINESS_OWNER} will call back within 15 minutes

Rules:
- Max 2-3 sentences per reply. This is SMS.
- Never say you are an AI. Never include [LEAD_CAPTURED] or LEAD_DATA in messages to the customer.
- Use casual Australian English (mate, no worries, reckon, etc.)
- If urgent (flooding, burst pipe, no hot water, gas leak, sewage) start reply with [URGENT]
- Once you have their name, address AND phone number, add LEAD_DATA on a new line

When lead is captured, your reply must end with this on a new line:
LEAD_DATA:{{"name":"<n>","address":"<address>","phone":"<phone>","problem":"<problem>","urgent":<true/false>}}"""

conversation_history = {}


def notify_owner(lead_data, customer_phone):
    if not OWNER_PHONE:
        print("ERROR: OWNER_PHONE not set, skipping notification")
        return
    if not TWILIO_PHONE:
        print("ERROR: TWILIO_PHONE_NUMBER not set, skipping notification")
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


def extract_lead_data(response_text):
    if "LEAD_DATA:" not in response_text:
        return None
    try:
        json_str = response_text.split("LEAD_DATA:")[1].strip().split("\n")[0]
        return json.loads(json_str)
    except Exception as e:
        print(f"Failed to extract lead data: {e}")
        return None


def clean_response(response_text):
    if "LEAD_DATA:" in response_text:
        response_text = response_text.split("LEAD_DATA:")[0].strip()
    response_text = response_text.replace("[LEAD_CAPTURED]", "").strip()
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
    print(f"Agent raw reply: {agent_reply[:200]}")

    lead_data = extract_lead_data(agent_reply)
    if lead_data:
        print(f"Lead captured: {lead_data}")
        notify_owner(lead_data, phone_number)

    clean_reply = clean_response(agent_reply)

    conversation_history[phone_number].append({
        "role": "assistant",
        "content": clean_reply
    })

    return clean_reply