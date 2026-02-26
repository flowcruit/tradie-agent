import sys
sys.stdout = sys.stderr

import os
import json
from openai import OpenAI
from database import save_message, save_lead, get_conversation
from agent import notify_owner, extract_lead_data, BUSINESS_NAME, BUSINESS_OWNER, notified_conversations

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── System prompt for voice ────────────────────────────────────────────────

VOICE_SYSTEM_PROMPT = f"""You are the virtual receptionist for {BUSINESS_NAME}, answering on behalf of {BUSINESS_OWNER} who is currently on a job.

## YOUR ONLY JOB
Collect these 4 things, in this order:
1. Full name
2. Full address (street, city, province)
3. Best callback number — ALWAYS read it back digit by digit to confirm
4. Description of the problem

## STRICT CONVERSATION RULES
- MAX 1-2 short sentences per response. This is a phone call, not a chat.
- Never use bullet points, lists, or formatting. Speak in plain natural sentences.
- Never say you are an AI or a bot. You are the answering service.
- Never give prices. {BUSINESS_OWNER} confirms pricing on-site.
- Warm Canadian English: "for sure", "absolutely", "no problem", "sounds good", "sorry to hear that"

## HANDLING INCOMPLETE ANSWERS
- If caller says only "yeah" or "uh huh" without giving info → ask again politely: "Sorry, I didn't catch that — could you repeat it for me?"
- If caller gives a partial address → ask: "And what city and province is that in?"
- ALWAYS confirm phone number by reading it back: "Just to confirm, that's [number] — is that right?"

## PHONE NUMBER HANDLING
- When caller gives a phone number digit by digit (e.g. "6 4 7 5 5 5 0 1 9 2") → group into standard format and confirm: "Got it, so that's six-four-seven, five-five-five, zero-one-nine-two — is that correct?"
- Wait for confirmation before moving on.

## EMERGENCY DETECTION
If caller mentions: no heat, furnace not working, burst pipe, flooding, water leak, gas smell, sewage, no hot water, frozen pipes → say: "That sounds urgent — I'll make sure {BUSINESS_OWNER} calls you back within the next five minutes."

## FLOW
1. Caller gives name → "Thanks [name]! And what's the address for the job?"
2. Caller gives address → "Perfect. What's the best number for {BUSINESS_OWNER} to reach you at?"
3. Caller gives number → confirm it digit by digit → "Got it. And can you briefly describe what's going on?"
4. Caller describes problem → if emergency say urgency line → then confirm everything: "Alright, so I have [name] at [address], callback number [number], regarding [problem]. I'll make sure {BUSINESS_OWNER} gets back to you right away."
5. End: "Thanks for calling {BUSINESS_NAME} — you'll hear back very soon. Have a great day!"

## HVAC VOCABULARY (recognize these correctly)
furnace, boiler, HVAC, heat pump, AC, air conditioner, ductwork, thermostat, hot water tank, water heater, sump pump, backflow, drain, pipe, leak, flood"""


# ── Main handler called from app.py ───────────────────────────────────────

def handle_conversation_relay(ws, caller_phone):
    """
    Handles a ConversationRelay WebSocket session.

    ConversationRelay protocol:
    - Sends us: {"type": "prompt", "voicePrompt": "what caller said", "last": false}
    - We send back: {"type": "text", "token": "agent response text", "last": true}

    ConversationRelay handles all STT and TTS — we just do text in/out.
    """
    print(f"ConversationRelay session — caller: {caller_phone}")
    conversation_history = []

    try:
        while True:
            raw = ws.receive(timeout=30)
            if raw is None:
                print("WebSocket closed by Twilio")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"Invalid JSON from Twilio: {raw[:100]}")
                continue

            msg_type = data.get("type")
            print(f"ConversationRelay event: {msg_type} — {str(data)[:120]}")

            # ── Setup message (first message of every call) ────────────────
            if msg_type == "setup":
                caller_phone = data.get("from", caller_phone)
                call_sid = data.get("callSid", "")
                print(f"Call setup — from: {caller_phone}, sid: {call_sid}")
                continue  # No response needed for setup

            # ── Caller spoke ───────────────────────────────────────────────
            elif msg_type == "prompt":
                caller_text = data.get("voicePrompt", "").strip()
                if not caller_text:
                    continue

                print(f"Caller ({caller_phone}): {caller_text}")
                save_message(caller_phone, "user", caller_text)
                conversation_history.append({"role": "user", "content": caller_text})

                # Stream response tokens directly to ConversationRelay
                # TTS starts speaking before OpenAI finishes generating
                agent_response = get_voice_response_streaming(conversation_history, ws)
                print(f"Agent: {agent_response}")

                save_message(caller_phone, "assistant", agent_response)
                conversation_history.append({"role": "assistant", "content": agent_response})

                # End the call if agent said goodbye
                if should_end_call(agent_response):
                    print("Agent finished — sending end signal")
                    ws.send(json.dumps({"type": "end"}))
                    break

            # ── Call ended ─────────────────────────────────────────────────
            elif msg_type == "end":
                reason = data.get("reason", "unknown")
                print(f"Call ended — reason: {reason}")
                _process_call_end(caller_phone)
                break

            elif msg_type == "dtmf":
                print(f"DTMF: {data.get('digit', '')} — ignored")
                continue

            else:
                print(f"Unknown event: {msg_type}")

    except Exception as e:
        print(f"handle_conversation_relay error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if caller_phone and caller_phone != "unknown":
            _process_call_end(caller_phone)


def get_voice_response_streaming(conversation_history, ws):
    """
    Stream tokens from OpenAI directly to ConversationRelay.
    Each token is sent immediately as it arrives — TTS starts before response is complete.
    Returns the full response text for saving to DB.
    """
    full_response = []
    buffer = ""

    try:
        stream = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}] + conversation_history,
            temperature=0.7,
            max_tokens=150,
            stream=True
        )

        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is None:
                continue

            buffer += delta
            full_response.append(delta)

            # Send on sentence boundaries for natural TTS pacing
            # This lets ConversationRelay start speaking mid-response
            if any(buffer.endswith(p) for p in [".", "!", "?", ",", " —"]):
                if buffer.strip():
                    ws.send(json.dumps({
                        "type": "text",
                        "token": buffer,
                        "last": False
                    }))
                    buffer = ""

        # Send any remaining buffer as the final token
        final_text = buffer.strip()
        if final_text:
            ws.send(json.dumps({
                "type": "text",
                "token": final_text,
                "last": True
            }))
        else:
            # Send empty final token to signal end
            ws.send(json.dumps({
                "type": "text",
                "token": "",
                "last": True
            }))

        return "".join(full_response).strip()

    except Exception as e:
        print(f"OpenAI streaming error: {e}")
        fallback = f"Sorry about that — let me get {BUSINESS_OWNER} to call you right back."
        ws.send(json.dumps({"type": "text", "token": fallback, "last": True}))
        return fallback


def get_voice_response(conversation_history):
    """Non-streaming fallback — used when ws not available"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}] + conversation_history,
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"OpenAI voice error: {e}")
        return f"Sorry about that — let me get {BUSINESS_OWNER} to call you right back."


def should_end_call(agent_text):
    """Detect if agent has wrapped up and said goodbye"""
    end_phrases = ["have a great day", "have a good day", "talk soon",
                   "thanks for calling", "goodbye", "take care"]
    text_lower = agent_text.lower()
    matches = sum(1 for phrase in end_phrases if phrase in text_lower)
    return matches >= 2  # Need 2+ goodbye signals to avoid false positives


def _process_call_end(caller_phone):
    """Extract lead from conversation and notify owner. Runs once per call."""
    if caller_phone in notified_conversations:
        return

    print(f"Processing call end for {caller_phone}")
    data = extract_lead_data(caller_phone)
    print(f"Voice lead extraction: {data}")

    if data and data.get("lead_captured"):
        data["channel"] = "voice"
        lead_id = save_lead(caller_phone, data)
        if lead_id:
            notified_conversations.add(caller_phone)
            notify_owner(data, caller_phone)
            print(f"Voice lead captured: {data.get('name')}")
    else:
        # Partial lead — notify anyway so Mike doesn't miss the call
        history = get_conversation(caller_phone)
        if len(history) >= 1:
            notified_conversations.add(caller_phone)
            partial = {
                "name": (data.get("name") if data else None) or "Unknown caller",
                "problem": (data.get("problem") if data else None) or "Called — details incomplete",
                "address": (data.get("address") if data else None) or "",
                "phone": caller_phone,
                "urgent": (data.get("urgent") if data else False),
                "channel": "voice"
            }
            notify_owner(partial, caller_phone)
            print(f"Partial voice lead — Mike notified: " + str(partial.get("name")))