import os
import psycopg2
import psycopg2.extras
from psycopg2 import pool

DATABASE_URL = os.environ.get("DATABASE_URL")

# Connection pool
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        if DATABASE_URL:
            _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL)
        else:
            raise Exception("DATABASE_URL not set")
    return _pool

def get_db():
    return get_pool().getconn()

def release_db(conn):
    get_pool().putconn(conn)

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                phone TEXT NOT NULL UNIQUE,
                name TEXT,
                address TEXT,
                contact_phone TEXT,
                problem TEXT,
                urgent INTEGER DEFAULT 0,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id SERIAL PRIMARY KEY,
                lead_id INTEGER,
                phone TEXT NOT NULL,
                problem TEXT,
                estimate_low INTEGER DEFAULT 0,
                estimate_high INTEGER DEFAULT 0,
                details TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("Database initialized (PostgreSQL)")
    except Exception as e:
        conn.rollback()
        print(f"DB init error: {e}")
    finally:
        release_db(conn)

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
        release_db(conn)

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
        release_db(conn)

def save_lead(phone, lead_data):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO leads (phone, name, address, contact_phone, problem, urgent)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name=EXCLUDED.name, address=EXCLUDED.address,
                contact_phone=EXCLUDED.contact_phone, problem=EXCLUDED.problem,
                urgent=EXCLUDED.urgent, status='new', updated_at=CURRENT_TIMESTAMP
            RETURNING id
        """, (
            phone, lead_data.get("name"), lead_data.get("address"),
            lead_data.get("phone"), lead_data.get("problem"),
            1 if lead_data.get("urgent") else 0
        ))
        lead_id = c.fetchone()[0]
        conn.commit()
        return lead_id
    except Exception as e:
        conn.rollback()
        print(f"save_lead error: {e}")
        return None
    finally:
        release_db(conn)

def get_all_leads():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, phone, name, address, contact_phone, problem, urgent, status, created_at FROM leads ORDER BY created_at DESC")
        rows = c.fetchall()
        return [{"id": r[0], "phone": r[1], "name": r[2], "address": r[3],
                 "contact_phone": r[4], "problem": r[5], "urgent": r[6],
                 "status": r[7], "created_at": str(r[8])} for r in rows]
    except Exception as e:
        print(f"get_all_leads error: {e}")
        return []
    finally:
        release_db(conn)

def get_lead_by_phone(phone):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, phone, name, address, contact_phone, problem, urgent, status FROM leads WHERE phone = %s", (phone,))
        r = c.fetchone()
        if not r:
            return None
        return {"id": r[0], "phone": r[1], "name": r[2], "address": r[3],
                "contact_phone": r[4], "problem": r[5], "urgent": r[6], "status": r[7]}
    except Exception as e:
        print(f"get_lead_by_phone error: {e}")
        return None
    finally:
        release_db(conn)

def update_lead_status(lead_id, status):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE leads SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s", (status, lead_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"update_lead_status error: {e}")
    finally:
        release_db(conn)

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
        release_db(conn)

init_db()
