import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "tradie_agent.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            name TEXT,
            address TEXT,
            contact_phone TEXT,
            problem TEXT,
            urgent INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            phone TEXT NOT NULL,
            problem TEXT,
            estimate_low INTEGER,
            estimate_high INTEGER,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)
    
    conn.commit()
    conn.close()
    print("Database initialized")

def save_message(phone, role, content):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (phone, role, content) VALUES (?, ?, ?)",
        (phone, role, content)
    )
    conn.commit()
    conn.close()

def get_conversation(phone):
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE phone = ? ORDER BY created_at ASC",
        (phone,)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]

def save_lead(phone, lead_data):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM leads WHERE phone = ?", (phone,)
    ).fetchone()
    
    if existing:
        conn.execute("""
            UPDATE leads SET name=?, address=?, contact_phone=?, problem=?, urgent=?,
            status='new', updated_at=CURRENT_TIMESTAMP WHERE phone=?
        """, (
            lead_data.get("name"), lead_data.get("address"),
            lead_data.get("phone"), lead_data.get("problem"),
            1 if lead_data.get("urgent") else 0, phone
        ))
        lead_id = existing["id"]
    else:
        cur = conn.execute("""
            INSERT INTO leads (phone, name, address, contact_phone, problem, urgent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            phone, lead_data.get("name"), lead_data.get("address"),
            lead_data.get("phone"), lead_data.get("problem"),
            1 if lead_data.get("urgent") else 0
        ))
        lead_id = cur.lastrowid
    
    conn.commit()
    conn.close()
    return lead_id

def save_quote(phone, lead_id, problem, low, high, details):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO quotes (lead_id, phone, problem, estimate_low, estimate_high, details)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (lead_id, phone, problem, low, high, details))
    quote_id = cur.lastrowid
    conn.commit()
    conn.close()
    return quote_id

def get_pending_quotes():
    conn = get_db()
    rows = conn.execute("""
        SELECT q.*, l.name, l.address FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        WHERE q.status = 'pending'
        ORDER BY q.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_quote_status(quote_id, status):
    conn = get_db()
    conn.execute("UPDATE quotes SET status=? WHERE id=?", (status, quote_id))
    conn.commit()
    conn.close()

def get_all_leads():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_lead_by_phone(phone):
    conn = get_db()
    row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_lead_status(lead_id, status):
    conn = get_db()
    conn.execute(
        "UPDATE leads SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, lead_id)
    )
    conn.commit()
    conn.close()

init_db()