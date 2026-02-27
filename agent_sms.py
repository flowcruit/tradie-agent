import sys
sys.stdout = sys.stderr

import os
from openai import OpenAI
from twilio.rest import Client as TwilioClient
from database import save_message, get_conversation, save_lead

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

BUSINESS_NAME  = os.getenv("BUSINESS_NAME", "Mike's Emergency Plumbing")
BUSINESS_OWNER = os.getenv("BUSINESS_OWNER", "Mike")
TWILIO_PHONE   = os.getenv("TWILIO_PHONE_NUMBER", "")
OWNER_PHONE    = os.getenv("OWNER_PHONE", "")

SMS_SYSTEM_PROMPT = f"""You are the virtual receptionist for {BUSINESS_NAME}, texting on behalf of {BUSINESS_OWNER}.

Collect: full name, full address, best callback number, description of problem.
Keep replies short — 1-2 sentences max. This is SMS, not a chat.
Never give prices. {BUSINESS_OWNER} confirms pricing on-site.
Warm Canadian English: "for sure", "no problem", "sounds good".

If they mention: no heat, burst pipe, flooding, gas smell, no hot water, frozen pipes — say:
"That sounds urgent — I'll make sure {BUSINESS_OWNER} calls you back within 5 minutes."

Once you have all 4 details, confirm: "Got it — I have [name] at [address], callback [number], re: [problem]. {BUSINESS_OWNER} will be in touch shortly."
"""


def get_agent_response(from_number, incoming_msg):
    """Handle inbound SMS from a customer."""
    save_message(from_number, "user", incoming_msg)
    history = get_conversation(from_number)

    messages = [{"role": "system", "content": SMS_SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in history
    ]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=150
        )
        reply = response.choices[0].message.content.strip()
        save_message(from_number, "assistant", reply)
        return reply
    except Exception as e:
        print(f"SMS agent error: {e}")
        return f"Thanks for reaching out — {BUSINESS_OWNER} will call you back shortly."


def send_quote_to_customer(customer_phone, name, low, high, from_number=None):
    """Send a price quote SMS to the customer."""
    twilio = TwilioClient(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN")
    )
    msg = (
        f"Hi {name}, {BUSINESS_NAME} here.\n"
        f"Based on what you've described, we estimate ${low}-${high} CAD.\n"
        f"{BUSINESS_OWNER} will confirm the exact price on-site. "
        f"Reply YES to confirm or call us to discuss."
    )
    try:
        result = twilio.messages.create(
            body=msg,
            from_=from_number or TWILIO_PHONE,
            to=customer_phone
        )
        print(f"Quote sent to {name}: {result.sid}")
        return True
    except Exception as e:
        print(f"send_quote error: {e}")
        return False