import os
import json
from datetime import datetime

# Use PostgreSQL if DATABASE_URL is set, otherwise SQLite
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    PLACEHOLDER = "%s"
else:
    import sqlite3
    def get_db():
        conn = sqlite3.connect("tradie_agent.db")
        conn.row_factory = sqlite3.Row
        return conn
    PLACEHOLDER = "?"

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
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
    
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
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
    conn.close()
    print(f"Database initialized ({'PostgreSQL' if DATABASE_URL else 'SQLite'})")

def save_message(phone, role, content):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        f"INSERT INTO messages (phone, role, content) VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})",
        (phone, role, content)
    )
    conn.commit()
    conn.close()

def get_conversation(phone):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        f"SELECT role, content FROM messages WHERE phone = {PLACEHOLDER} ORDER BY created_at ASC",
        (phone,)
    )
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]

def save_lead(phone, lead_data):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM leads WHERE phone = {PLACEHOLDER}", (phone,))
    existing = c.fetchone()
    
    if existing:
        c.execute(f"""
            UPDATE leads SET name={PLACEHOLDER}, address={PLACEHOLDER}, contact_phone={PLACEHOLDER}, 
            problem={PLACEHOLDER}, urgent={PLACEHOLDER}, status='new', updated_at=CURRENT_TIMESTAMP 
            WHERE phone={PLACEHOLDER}
        """, (
            lead_data.get("name"), lead_data.get("address"),
            lead_data.get("phone"), lead_data.get("problem"),
            1 if lead_data.get("urgent") else 0, phone
        ))
        lead_id = existing[0]
    else:
        c.execute(f"""
            INSERT INTO leads (phone, name, address, contact_phone, problem, urgent)
            VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
            RETURNING id
        """, (
            phone, lead_data.get("name"), lead_data.get("address"),
            lead_data.get("phone"), lead_data.get("problem"),
            1 if lead_data.get("urgent") else 0
        ))
        lead_id = c.fetchone()[0]
    
    conn.commit()
    conn.close()
    return lead_id

def get_all_leads():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, phone, name, address, contact_phone, problem, urgent, status, created_at FROM leads ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "phone": r[1], "name": r[2], "address": r[3], 
             "contact_phone": r[4], "problem": r[5], "urgent": r[6], 
             "status": r[7], "created_at": str(r[8])} for r in rows]

def get_lead_by_phone(phone):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id, phone, name, address, contact_phone, problem, urgent, status FROM leads WHERE phone = {PLACEHOLDER}", (phone,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "phone": r[1], "name": r[2], "address": r[3], 
            "contact_phone": r[4], "problem": r[5], "urgent": r[6], "status": r[7]}

def update_lead_status(lead_id, status):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE leads SET status={PLACEHOLDER}, updated_at=CURRENT_TIMESTAMP WHERE id={PLACEHOLDER}", (status, lead_id))
    conn.commit()
    conn.close()

def save_quote(phone, lead_id, problem, low, high, details):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"""
        INSERT INTO quotes (lead_id, phone, problem, estimate_low, estimate_high, details)
        VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
        RETURNING id
    """, (lead_id, phone, problem, low, high, details))
    quote_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return quote_id

init_db()