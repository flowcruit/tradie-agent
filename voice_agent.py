import sys
sys.stdout = sys.stderr

import os
import json
from openai import OpenAI
from database import save_message, save_lead, get_conversation
from agent import notify_owner, extract_lead_data, BUSINESS_NAME, BUSINESS_OWNER, notified_conversations

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── System prompt for voice ────────────────────────────────────────────────

VOICE_SYSTEM_PROMPT = f"""You are the virtual receptionist answering the phone for {BUSINESS_NAME}, run by {BUSINESS_OWNER}.

{BUSINESS_OWNER} is currently on another job and cannot take the call. Your job is to take a message.

GOAL — collect all 4 of these:
1. Caller's full name
2. Full address including city and province
3. Best callback phone number
4. Brief description of the problem

FLOW:
- Start by asking their name (already done in the welcome greeting)
- After name → ask their address
- After address → ask best callback number
- After number → ask about the problem briefly
- Once you have all 4 → confirm back: "Great, I have [name] at [address], I'll have {BUSINESS_OWNER} call you back at [number] shortly about [problem]. Is there anything else I should pass on?"
- Then end warmly: "Perfect, {BUSINESS_OWNER} will be in touch very soon. Thanks for calling {BUSINESS_NAME}, have a great day!"

RULES:
- Warm, natural Canadian English. Say: "for sure", "absolutely", "no problem", "sounds good", "sorry to hear that"
- SHORT responses — you are on a phone call. Max 2 sentences per turn.
- Never say you are an AI or a bot.
- If it sounds like an emergency (no heat in winter, burst pipe, flooding, gas smell, sewage backup, no hot water) — acknowledge it immediately: "Oh no, that sounds urgent — let me get {BUSINESS_OWNER} to call you right back as soon as possible, within the next few minutes."
- Never give prices. {BUSINESS_OWNER} confirms pricing after seeing the job.
- If caller says they'll call back later, say: "Of course! Though I can take your number now so {BUSINESS_OWNER} can reach out when he's free — saves you waiting!"
- Keep the whole conversation under 3 minutes."""


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

                # Get agent response from OpenAI
                agent_response = get_voice_response(conversation_history)
                print(f"Agent: {agent_response}")

                save_message(caller_phone, "assistant", agent_response)
                conversation_history.append({"role": "assistant", "content": agent_response})

                # Send text back — ConversationRelay will TTS it
                ws.send(json.dumps({
                    "type": "text",
                    "token": agent_response,
                    "last": True
                }))

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


def get_voice_response(conversation_history):
    """Call OpenAI with voice system prompt and return short response"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}] + conversation_history,
            temperature=0.7,
            max_tokens=150  # Short for natural voice
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
            print(f"Partial voice lead — Mike notified: {partial['name']}")
