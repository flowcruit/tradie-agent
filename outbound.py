import sys
sys.stdout = sys.stderr

import os
import threading
import time
from datetime import datetime, timedelta
from twilio.rest import Client as TwilioClient
from database import (
    get_all_outbound_leads, get_leads_due_followup,
    get_leads_no_answer_demo, update_outbound_lead, log_outbound_event,
    create_demo_session, delete_demo_session
)

twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
OUTBOUND_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
BASE_URL = os.getenv("BASE_URL", "")

# ── SMS Templates ──────────────────────────────────────────────────────────

SMS_INITIAL = (
    "Hi {owner_name}, quick question — how many calls does {business_name} miss every week?\n\n"
    "Every missed call in HVAC goes straight to your competitor.\n\n"
    "We built an AI that only kicks in when you don't answer — captures the lead and texts you instantly.\n\n"
    "Your competitors in Ontario are already using it.\n\n"
    "Want to hear it answer as {business_name} right now? Reply YES"
)

SMS_FOLLOWUP_1 = (
    "Hi {owner_name}, still thinking about it?\n\n"
    "Last week alone, HVAC contractors in Ontario lost an average of 8 calls to voicemail.\n\n"
    "Each one is a lead your competitor picked up.\n\n"
    "Takes 2 minutes to hear how it works. Reply YES and we'll demo it as {business_name} right now."
)

SMS_FOLLOWUP_2 = (
    "Last message from us, {owner_name}.\n\n"
    "If missed calls aren't a problem for {business_name}, no worries at all.\n\n"
    "But if you're losing even 2-3 leads a week, that's $2,000-$5,000 CAD/month walking out the door.\n\n"
    "Reply YES for a 2-minute live demo — no commitment, no card needed."
)

SMS_YES_RECEIVED = (
    "Perfect! Calling {business_name} right now.\n\n"
    "Answer and pretend you're a customer calling in with a problem — "
    "you'll hear exactly what your customers would hear."
)

SMS_NO_ANSWER_RETRY = (
    "Hi {owner_name}, we tried calling but you must be on a job.\n\n"
    "Reply YES again when you have 2 minutes and we'll call right back."
)

SMS_AFTER_DEMO = (
    "That's what your customers hear when they can't reach you — "
    "instead of going to voicemail and calling your competitor.\n\n"
    "Start your 7-day free trial — no card needed:\n"
    "Reply TRIAL or visit: {trial_link}"
)

SMS_TRIAL_DAY5 = (
    "Hi {owner_name}, how's the trial going at {business_name}?\n\n"
    "You have 2 days left. If you've forwarded your missed calls, "
    "check your leads dashboard — every captured lead is money saved.\n\n"
    "Any questions? Just reply here."
)

SMS_TRIAL_DAY7 = (
    "Hi {owner_name}, your free trial ends today.\n\n"
    "Keep your AI receptionist for {business_name} for $299 CAD/month — no setup fee.\n\n"
    "Activate now: {stripe_link}\n\n"
    "Takes 60 seconds."
)


# ── Send functions ─────────────────────────────────────────────────────────

def send_sms(to, body):
    try:
        result = twilio.messages.create(body=body, from_=OUTBOUND_NUMBER, to=to)
        print(f"SMS sent to {to}: {result.sid}")
        return result.sid
    except Exception as e:
        print(f"SMS error to {to}: {e}")
        return None


def send_initial_sms(lead):
    msg = SMS_INITIAL.format(
        owner_name=lead["owner_name"] or "there",
        business_name=lead["business_name"]
    )
    sid = send_sms(lead["phone"], msg)
    if sid:
        update_outbound_lead(
            lead["phone"],
            sms_sent=True,
            sms_sent_at="NOW()",
            status="contacted",
            next_follow_up_at=datetime.utcnow() + timedelta(days=5)
        )
        log_outbound_event(lead["phone"], "sms_initial", f"SID: {sid}")
        return True
    return False


def send_followup(lead):
    count = lead.get("follow_up_count", 0)

    if count == 0:
        msg = SMS_FOLLOWUP_1.format(
            owner_name=lead["owner_name"] or "there",
            business_name=lead["business_name"]
        )
        next_followup = datetime.utcnow() + timedelta(days=5)
        event = "sms_followup_1"
    elif count == 1:
        msg = SMS_FOLLOWUP_2.format(
            owner_name=lead["owner_name"] or "there",
            business_name=lead["business_name"]
        )
        next_followup = None
        event = "sms_followup_2"
    else:
        # Max follow-ups reached — mark dead
        update_outbound_lead(lead["phone"], status="dead")
        log_outbound_event(lead["phone"], "marked_dead", "Max follow-ups reached")
        return False

    sid = send_sms(lead["phone"], msg)
    if sid:
        new_count = count + 1
        update_outbound_lead(
            lead["phone"],
            follow_up_count=new_count,
            last_follow_up_at="NOW()",
            status="dead" if new_count >= 2 else "contacted"
        )
        if next_followup:
            update_outbound_lead(lead["phone"], next_follow_up_at=next_followup)
        log_outbound_event(lead["phone"], event, f"SID: {sid}")
        return True
    return False


def handle_yes_response(lead):
    """Called when prospect replies YES."""
    # Send confirmation SMS
    msg = SMS_YES_RECEIVED.format(business_name=lead["business_name"])
    send_sms(lead["phone"], msg)

    update_outbound_lead(
        lead["phone"],
        responded=True,
        responded_at="NOW()",
        status="responded"
    )
    log_outbound_event(lead["phone"], "responded_yes")

    # Call in background after short delay
    threading.Thread(
        target=_make_demo_call,
        args=(lead,),
        daemon=True
    ).start()


def _make_demo_call(lead):
    """Place outbound demo call to prospect."""
    time.sleep(4)  # SMS arrives first

    # Clear any previous messages for this prospect phone
    # so the demo agent starts fresh with no history
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        c = conn.cursor()
        c.execute("DELETE FROM messages WHERE phone = %s", (lead["phone"],))
        conn.commit()
        conn.close()
        print(f"Cleared history for {lead['phone']}")
    except Exception as e:
        print(f"Clear history error: {e}")

    ws_url = (BASE_URL.replace("https://", "wss://") + "/demo-ws") if BASE_URL else "wss://tradie-agent.onrender.com/demo-ws"
    business_name = lead["business_name"]
    owner_name = lead["owner_name"] or "our technician"

    # Register demo session — agent will look this up by prospect phone
    create_demo_session(lead["phone"], business_name, owner_name)

    welcome = (
        f"Thank you for calling {business_name}. "
        f"You've reached our answering service — {owner_name} is currently on a job. "
        f"I can take your details and have someone call you right back. "
        f"What's your first name please?"
    )

    try:
        call = twilio.calls.create(
            to=lead["phone"],
            from_=OUTBOUND_NUMBER,
            twiml=f"""<Response><Connect>
                <ConversationRelay url="{ws_url}" language="en-US" interruptible="true"
                    hints="furnace,boiler,HVAC,heat pump,thermostat,hot water tank,no heat,frozen pipes"
                    welcomeGreeting="{welcome}" />
            </Connect></Response>"""
        )
        update_outbound_lead(
            lead["phone"],
            demo_called=True,
            demo_called_at="NOW()",
            status="demo_called"
        )
        log_outbound_event(lead["phone"], "demo_called", f"SID: {call.sid}")
        print(f"Demo call to {business_name} ({lead['phone']}): {call.sid}")

        # Wait for call to complete then send after-demo SMS
        time.sleep(180)  # Wait 3 min
        delete_demo_session(lead['phone'])
        _send_after_demo_sms(lead)

    except Exception as e:
        print(f"Demo call error: {e}")
        update_outbound_lead(lead["phone"], status="responded")


def _send_after_demo_sms(lead):
    """Send trial link after demo call."""
    trial_link = f"{BASE_URL}/trial?phone={lead['phone']}" if BASE_URL else "https://tradie-agent.onrender.com/trial"
    msg = SMS_AFTER_DEMO.format(trial_link=trial_link)
    sid = send_sms(lead["phone"], msg)
    if sid:
        update_outbound_lead(lead["phone"], status="demo_done")
        log_outbound_event(lead["phone"], "sms_after_demo", f"SID: {sid}")


def handle_demo_answered(phone):
    """Called when prospect picks up the demo call."""
    update_outbound_lead(phone, demo_answered=True, status="demo_answered")
    log_outbound_event(phone, "demo_answered")


def handle_demo_no_answer(phone):
    """Called when prospect didn't pick up demo call."""
    lead = {"phone": phone, "owner_name": "", "business_name": ""}
    from database import get_outbound_lead_by_phone
    lead = get_outbound_lead_by_phone(phone) or lead

    update_outbound_lead(phone, status="no_answer", last_follow_up_at="NOW()")
    msg = SMS_NO_ANSWER_RETRY.format(owner_name=lead.get("owner_name") or "there")
    sid = send_sms(phone, msg)
    log_outbound_event(phone, "demo_no_answer", f"Retry SMS: {sid}")


# ── Batch operations ───────────────────────────────────────────────────────

def send_batch(limit=20):
    """Send initial SMS to pending leads."""
    leads = get_all_outbound_leads()
    pending = [l for l in leads if not l["sms_sent"] and l["status"] == "pending"][:limit]

    sent = 0
    for lead in pending:
        if send_initial_sms(lead):
            sent += 1
            time.sleep(1)  # 1 second between sends — avoid carrier spam flags

    print(f"Batch sent: {sent}/{len(pending)}")
    return sent


def process_followups():
    """Send follow-up SMS to leads due for one."""
    due = get_leads_due_followup()
    processed = 0
    for lead in due:
        if send_followup(lead):
            processed += 1
            time.sleep(1)
    print(f"Follow-ups processed: {processed}")
    return processed


def retry_no_answers():
    """Retry demo call to leads that said YES but didn't answer."""
    no_answers = get_leads_no_answer_demo()
    for lead in no_answers:
        msg = SMS_NO_ANSWER_RETRY.format(owner_name=lead.get("owner_name") or "there")
        send_sms(lead["phone"], msg)
        update_outbound_lead(lead["phone"], last_follow_up_at="NOW()")
        log_outbound_event(lead["phone"], "no_answer_retry")
        time.sleep(1)
    return len(no_answers)


# ── Scheduler ─────────────────────────────────────────────────────────────

def start_scheduler():
    """Background scheduler — runs every 30 minutes."""
    def _run():
        while True:
            try:
                print("Scheduler tick — processing follow-ups")
                process_followups()
                retry_no_answers()
            except Exception as e:
                print(f"Scheduler error: {e}")
            time.sleep(1800)  # 30 minutes

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print("Outbound scheduler started")