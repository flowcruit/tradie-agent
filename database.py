import sys
sys.stdout = sys.stderr

import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                phone TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                phone TEXT NOT NULL UNIQUE,
                client_id INTEGER,
                name TEXT,
                address TEXT,
                contact_phone TEXT,
                problem TEXT,
                urgent BOOLEAN DEFAULT FALSE,
                channel TEXT DEFAULT 'sms',
                status TEXT DEFAULT 'new',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id SERIAL PRIMARY KEY,
                lead_id INTEGER REFERENCES leads(id),
                phone TEXT NOT NULL,
                problem TEXT,
                estimate_low INTEGER DEFAULT 0,
                estimate_high INTEGER DEFAULT 0,
                details TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                business_name TEXT NOT NULL,
                owner_name TEXT,
                owner_phone TEXT UNIQUE,
                twilio_number TEXT UNIQUE,
                province TEXT DEFAULT 'ON',
                plan TEXT DEFAULT 'trial',
                trial_ends_at TIMESTAMPTZ,
                stripe_customer_id TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone);
            CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_clients_twilio_number ON clients(twilio_number);
            CREATE INDEX IF NOT EXISTS idx_clients_owner_phone ON clients(owner_phone);
        """)
        conn.commit()
        print("Database initialized (PostgreSQL)")
    except Exception as e:
        conn.rollback()
        print(f"DB init error: {e}")
    finally:
        conn.close()


# ── Client lookup ──────────────────────────────────────────────────────────

def get_client_by_twilio_number(twilio_number):
    """Look up client config by their assigned Twilio number.
    Called on every inbound call/SMS to know which business we're serving."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, business_name, owner_name, owner_phone, twilio_number, province, plan, active
            FROM clients WHERE twilio_number = %s AND active = TRUE
        """, (twilio_number,))
        r = c.fetchone()
        if not r:
            return None
        return {
            "id": r[0],
            "business_name": r[1],
            "owner_name": r[2],
            "owner_phone": r[3],
            "twilio_number": r[4],
            "province": r[5],
            "plan": r[6],
            "active": r[7]
        }
    except Exception as e:
        print(f"get_client_by_twilio_number error: {e}")
        return None
    finally:
        conn.close()

def get_client_by_owner_phone(owner_phone):
    """Look up client by owner's personal mobile number."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, business_name, owner_name, owner_phone, twilio_number, province, plan, active
            FROM clients WHERE owner_phone = %s AND active = TRUE
        """, (owner_phone,))
        r = c.fetchone()
        if not r:
            return None
        return {
            "id": r[0],
            "business_name": r[1],
            "owner_name": r[2],
            "owner_phone": r[3],
            "twilio_number": r[4],
            "province": r[5],
            "plan": r[6],
            "active": r[7]
        }
    except Exception as e:
        print(f"get_client_by_owner_phone error: {e}")
        return None
    finally:
        conn.close()

def create_client(business_name, owner_name, owner_phone, twilio_number, province="ON"):
    """Create a new client record when they sign up."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO clients (business_name, owner_name, owner_phone, twilio_number, province)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_phone) DO UPDATE SET
                business_name=EXCLUDED.business_name,
                owner_name=EXCLUDED.owner_name,
                twilio_number=EXCLUDED.twilio_number,
                province=EXCLUDED.province,
                updated_at=NOW()
            RETURNING id
        """, (business_name, owner_name, owner_phone, twilio_number, province))
        client_id = c.fetchone()[0]
        conn.commit()
        return client_id
    except Exception as e:
        conn.rollback()
        print(f"create_client error: {e}")
        return None
    finally:
        conn.close()


# ── Messages ───────────────────────────────────────────────────────────────

def save_message(phone, role, content):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO messages (phone, role, content) VALUES (%s, %s, %s)",
            (phone, role, content)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"save_message error: {e}")
    finally:
        conn.close()

def get_conversation(phone):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT role, content FROM messages WHERE phone = %s ORDER BY created_at ASC",
            (phone,)
        )
        rows = c.fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]
    except Exception as e:
        print(f"get_conversation error: {e}")
        return []
    finally:
        conn.close()


# ── Leads ──────────────────────────────────────────────────────────────────

def save_lead(phone, lead_data, client_id=None):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO leads (phone, client_id, name, address, contact_phone, problem, urgent, channel)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name=EXCLUDED.name,
                address=EXCLUDED.address,
                contact_phone=EXCLUDED.contact_phone,
                problem=EXCLUDED.problem,
                urgent=EXCLUDED.urgent,
                channel=EXCLUDED.channel,
                client_id=EXCLUDED.client_id,
                status='new',
                updated_at=NOW()
            RETURNING id
        """, (
            phone,
            client_id or lead_data.get("client_id"),
            lead_data.get("name"),
            lead_data.get("address"),
            lead_data.get("phone"),
            lead_data.get("problem"),
            bool(lead_data.get("urgent")),
            lead_data.get("channel", "sms")
        ))
        lead_id = c.fetchone()[0]
        conn.commit()
        return lead_id
    except Exception as e:
        conn.rollback()
        print(f"save_lead error: {e}")
        return None
    finally:
        conn.close()

def get_all_leads(client_id=None):
    conn = get_db()
    try:
        c = conn.cursor()
        if client_id:
            c.execute("""
                SELECT id, phone, name, address, contact_phone, problem, urgent, channel, status, created_at
                FROM leads WHERE client_id = %s ORDER BY created_at DESC
            """, (client_id,))
        else:
            c.execute("""
                SELECT id, phone, name, address, contact_phone, problem, urgent, channel, status, created_at
                FROM leads ORDER BY created_at DESC
            """)
        rows = c.fetchall()
        return [{
            "id": r[0], "phone": r[1], "name": r[2], "address": r[3],
            "contact_phone": r[4], "problem": r[5], "urgent": r[6],
            "channel": r[7], "status": r[8], "created_at": str(r[9])
        } for r in rows]
    except Exception as e:
        print(f"get_all_leads error: {e}")
        return []
    finally:
        conn.close()

def get_lead_by_phone(phone):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, phone, name, address, contact_phone, problem, urgent, status, client_id
            FROM leads WHERE phone = %s
        """, (phone,))
        r = c.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "phone": r[1], "name": r[2], "address": r[3],
            "contact_phone": r[4], "problem": r[5], "urgent": r[6],
            "status": r[7], "client_id": r[8]
        }
    except Exception as e:
        print(f"get_lead_by_phone error: {e}")
        return None
    finally:
        conn.close()

def update_lead_status(lead_id, status):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE leads SET status=%s, updated_at=NOW() WHERE id=%s",
            (status, lead_id)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"update_lead_status error: {e}")
    finally:
        conn.close()

def save_quote(phone, lead_id, problem, low, high, details):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO quotes (lead_id, phone, problem, estimate_low, estimate_high, details)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (lead_id, phone, problem, low, high, details))
        quote_id = c.fetchone()[0]
        conn.commit()
        return quote_id
    except Exception as e:
        conn.rollback()
        print(f"save_quote error: {e}")
        return None
    finally:
        conn.close()

init_db()