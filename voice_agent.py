import sys
sys.stdout = sys.stderr

import os
import json
from openai import OpenAI
from database import save_message, save_lead, get_conversation

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Tracks calls already processed — keyed by caller_phone:twilio_number
notified_conversations = set()


def build_voice_prompt(client):
    """Build dynamic system prompt per client."""
    business_name = client["business_name"]
    owner_name = client["owner_name"]

    return f"""You are the virtual receptionist for {business_name}, answering on behalf of {owner_name} who is currently on a job.

## YOUR ONLY JOB
Collect these 4 things, in this order:
1. Full name — ask first name, then last name SEPARATELY
2. Full address (street number, street name, city, province)
3. Best callback number — ALWAYS read it back digit by digit to confirm
4. Description of the problem

## STRICT CONVERSATION RULES
- MAX one to two short sentences per response. This is a phone call, not a chat.
- Never use bullet points, lists, or formatting. Speak in plain natural sentences.
- Never say you are an AI or a bot. You are the answering service for {business_name}.
- Never give prices. {owner_name} confirms pricing on-site.
- Warm Canadian English: "for sure", "absolutely", "no problem", "sounds good", "sorry to hear that"
- Spell out all numbers as words — say "six four seven" not "647", "one forty two" not "142"
- Do not repeat the same opening phrase twice in a row

## HANDLING INCOMPLETE ANSWERS
- If caller says only "yeah" or "uh huh" without giving info — ask again: "Sorry, I didn't catch that — could you repeat it for me?"
- If caller gives a partial address — ask: "And what city and province is that in?"
- Ask for first name and last name SEPARATELY to avoid transcription errors

## PHONE NUMBER HANDLING
- When caller gives a phone number — group and confirm digit by digit: "Got it, so that's six-four-seven, five-five-five, zero-one-nine-two — is that correct?"
- Wait for explicit confirmation before moving on
- If they correct any digit — repeat the FULL corrected number back again

## EMERGENCY DETECTION
If caller mentions: no heat, furnace not working, burst pipe, flooding, water leak, gas smell, sewage, no hot water, frozen pipes, carbon monoxide — say immediately: "That sounds urgent — I'll make sure {owner_name} calls you back within the next five minutes."

## FILLER PHRASES — use when you need a moment
- "Let me make a note of that."
- "Got it, just a moment."
- "Sure, bear with me one second."

## CONVERSATION FLOW
1. Ask for first name — then last name separately
2. Ask for full address — confirm city and province if missing
3. Ask for best callback number — confirm digit by digit — wait for confirmation
4. Ask to describe the problem briefly
5. If urgent — say urgency line
6. Confirm everything and ask if anything else: "Alright, so I have [full name] at [address], callback number [number], regarding [problem]. I'll make sure {owner_name} gets back to you right away. Is there anything else I should pass on?"
7. WAIT for caller response — if they say no or nothing else — THEN say goodbye: "Perfect — thanks for calling {business_name}. You'll hear back very soon. Have a great day!"
8. NEVER combine the confirmation and the goodbye in the same response. They are always two separate turns."

## HVAC AND TRADES VOCABULARY
furnace, boiler, HVAC, heat pump, air conditioner, AC unit, ductwork, thermostat, hot water tank, water heater, sump pump, backflow valve, drain, pipe, leak, flood, plumbing, electrical panel, breaker, carbon monoxide, CO detector"""


def build_extractor_prompt():
    return """Extract lead data from this conversation. Respond ONLY with valid JSON, no other text.

{
  "lead_captured": true or false,
  "name": "full name or null",
  "address": "full address or null",
  "phone": "phone number or null",
  "problem": "problem description or null",
  "urgent": true or false
}

lead_captured is true only when we have name AND address AND phone AND problem.
urgent is true if caller mentioned: no heat, furnace, flooding, burst pipe, gas leak, sewage, no hot water, frozen pipes, carbon monoxide."""


# ── Main WebSocket handler ─────────────────────────────────────────────────

def handle_conversation_relay(ws, caller_phone, client):
    """
    Handles a ConversationRelay WebSocket session.
    client dict comes from database.get_client_by_twilio_number()
    """
    session_key = f"{caller_phone}:{client['twilio_number']}"
    print(f"Voice session — caller: {caller_phone}, business: {client['business_name']}")

    conversation_history = []
    voice_prompt = build_voice_prompt(client)

    try:
        while True:
            raw = ws.receive(timeout=30)
            if raw is None:
                print("WebSocket closed")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"Invalid JSON: {raw[:100]}")
                continue

            msg_type = data.get("type")
            print(f"Event: {msg_type} — {str(data)[:120]}")

            if msg_type == "setup":
                caller_phone = data.get("from", caller_phone)
                print(f"Setup — caller: {caller_phone}, sid: {data.get('callSid')}")
                continue

            elif msg_type == "prompt":
                caller_text = data.get("voicePrompt", "").strip()
                if not caller_text:
                    continue

                print(f"Caller: {caller_text}")
                save_message(caller_phone, "user", caller_text)
                conversation_history.append({"role": "user", "content": caller_text})

                agent_response = stream_voice_response(conversation_history, voice_prompt, ws)
                print(f"Agent: {agent_response}")

                save_message(caller_phone, "assistant", agent_response)
                conversation_history.append({"role": "assistant", "content": agent_response})

                if should_end_call(agent_response):
                    print("Call complete — sending end signal")
                    ws.send(json.dumps({"type": "end"}))
                    break

            elif msg_type == "end":
                print(f"Call ended — reason: {data.get('reason')}")
                _process_call_end(caller_phone, session_key, client)
                break

            elif msg_type == "dtmf":
                print(f"DTMF: {data.get('digit')} — ignored")

            else:
                print(f"Unknown event: {msg_type}")

    except Exception as e:
        print(f"handle_conversation_relay error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if caller_phone and caller_phone != "unknown":
            _process_call_end(caller_phone, session_key, client)


# ── OpenAI streaming ───────────────────────────────────────────────────────

def stream_voice_response(conversation_history, voice_prompt, ws):
    """
    Stream tokens directly to ConversationRelay.
    ElevenLabs TTS starts speaking before GPT-4o finishes generating.
    Reduces perceived latency ~60%.
    """
    full_response = []
    buffer = ""

    try:
        stream = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": voice_prompt}] + conversation_history,
            temperature=0.7,
            max_tokens=200,
            stream=True
        )

        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is None:
                continue

            buffer += delta
            full_response.append(delta)

            # Send on natural speech boundaries for smooth TTS
            if any(buffer.endswith(p) for p in [".", "!", "?", ",", " —", " -"]):
                if buffer.strip():
                    ws.send(json.dumps({
                        "type": "text",
                        "token": buffer,
                        "last": False
                    }))
                    buffer = ""

        # Final token
        ws.send(json.dumps({
            "type": "text",
            "token": buffer.strip() if buffer.strip() else "",
            "last": True
        }))

        return "".join(full_response).strip()

    except Exception as e:
        print(f"Streaming error: {e}")
        fallback = "Sorry about that — let me get someone to call you right back."
        ws.send(json.dumps({"type": "text", "token": fallback, "last": True}))
        return fallback


# ── Call end processing ────────────────────────────────────────────────────

def should_end_call(agent_text):
    """End call only when agent has said the full farewell after confirmation.
    Requires both a thank-you AND a day-wish in the same response.
    This prevents cutting off mid-conversation."""
    text_lower = agent_text.lower()
    has_thanks = "thanks for calling" in text_lower or "thank you for calling" in text_lower
    has_day = "have a great day" in text_lower or "have a good day" in text_lower
    return has_thanks and has_day


def _process_call_end(caller_phone, session_key, client):
    """Extract lead from conversation and notify owner. Runs once per call."""
    if session_key in notified_conversations:
        return
    notified_conversations.add(session_key)

    print(f"Processing end — {caller_phone} for {client['business_name']}")
    data = _extract_lead(caller_phone)
    print(f"Extraction result: {data}")

    if data and data.get("lead_captured"):
        lead_id = save_lead(caller_phone, data, client_id=client["id"])
        if lead_id:
            _notify_owner(data, caller_phone, client)
            print(f"Full lead saved: {data.get('name')}")
    else:
        history = get_conversation(caller_phone)
        if history:
            partial = {
                "name": (data.get("name") if data else None) or "Unknown caller",
                "problem": (data.get("problem") if data else None) or "Called — details incomplete",
                "address": (data.get("address") if data else None) or "",
                "phone": caller_phone,
                "urgent": (data.get("urgent") if data else False),
                "channel": "voice"
            }
            _notify_owner(partial, caller_phone, client)
            print("Partial lead — owner notified")


def _extract_lead(caller_phone):
    """Run GPT-4o extractor on full conversation history."""
    history = get_conversation(caller_phone)
    if len(history) < 2:
        return None

    history_text = "\n".join(
        [f"{m['role'].upper()}: {m['content']}" for m in history]
    )
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": build_extractor_prompt()},
                {"role": "user", "content": f"Conversation:\n{history_text}"}
            ],
            temperature=0
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Extractor error: {e}")
        return None


def _notify_owner(lead_data, customer_phone, client):
    """Send lead SMS to business owner from their assigned Twilio number."""
    from twilio.rest import Client as TwilioClient
    twilio = TwilioClient(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN")
    )

    urgent_tag = "URGENT" if lead_data.get("urgent") else "New Lead"
    message = (
        f"{urgent_tag}: {lead_data.get('name')}\n"
        f"Problem: {lead_data.get('problem', '')}\n"
        f"Address: {lead_data.get('address', '')}\n"
        f"Tel: {lead_data.get('phone', customer_phone)}\n"
        f"SMS: {customer_phone}\n\n"
        f"Reply:\n"
        f"APPROVE {customer_phone} 150 300 - send quote\n"
        f"DONE {customer_phone} - mark complete"
    )

    try:
        result = twilio.messages.create(
            body=message,
            from_=client["twilio_number"],
            to=client["owner_phone"]
        )
        print(f"Owner SMS sent: {result.sid}")
    except Exception as e:
        print(f"Notify owner error: {e}")