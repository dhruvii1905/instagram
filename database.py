import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "instagram.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            user_id TEXT,
            full_name TEXT,
            bio TEXT,
            follower_count INTEGER,
            following_count INTEGER,
            post_count INTEGER,
            is_private INTEGER DEFAULT 0,
            source TEXT,          -- e.g. 'follower:@competitor' or 'reel:123'
            scraped_at TEXT DEFAULT (datetime('now')),
            followed_at TEXT,
            dm_sent_at TEXT,
            converted INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS follow_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            user_id TEXT,
            source TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',   -- pending | done | skipped
            processed_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            user_id TEXT,
            message TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            sent_at TEXT,
            error TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,   -- follow | unfollow | like | dm | story_view | scrape
            target TEXT,            -- username or media_id
            status TEXT,            -- success | failed | skipped
            detail TEXT,
            ts TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            user_id TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            last_scraped_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ---------- leads ----------

def upsert_lead(username: str, user_id: str = None, full_name: str = None,
                bio: str = None, follower_count: int = None,
                following_count: int = None, post_count: int = None,
                is_private: bool = False, source: str = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO leads (username, user_id, full_name, bio, follower_count,
                           following_count, post_count, is_private, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            user_id=excluded.user_id,
            full_name=excluded.full_name,
            bio=excluded.bio,
            follower_count=excluded.follower_count,
            following_count=excluded.following_count,
            post_count=excluded.post_count,
            is_private=excluded.is_private
    """, (username, user_id, full_name, bio, follower_count,
          following_count, post_count, int(is_private), source))
    conn.commit()
    conn.close()


def get_leads(limit: int = 100, followed: bool = None):
    conn = get_conn()
    c = conn.cursor()
    if followed is None:
        c.execute("SELECT * FROM leads ORDER BY scraped_at DESC LIMIT ?", (limit,))
    elif followed:
        c.execute("SELECT * FROM leads WHERE followed_at IS NOT NULL LIMIT ?", (limit,))
    else:
        c.execute("SELECT * FROM leads WHERE followed_at IS NULL LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ---------- follow queue ----------

def add_to_follow_queue(username: str, user_id: str = None, source: str = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO follow_queue (username, user_id, source)
        VALUES (?, ?, ?)
    """, (username, user_id, source))
    conn.commit()
    conn.close()


def get_pending_follows(limit: int = 20):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM follow_queue WHERE status = 'pending'
        ORDER BY added_at ASC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_follow_done(follow_id: int, status: str = "done"):
    conn = get_conn()
    conn.execute("""
        UPDATE follow_queue SET status=?, processed_at=datetime('now')
        WHERE id=?
    """, (status, follow_id))
    conn.commit()
    conn.close()


def follow_queue_count(status: str = "pending") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM follow_queue WHERE status=?", (status,)
    ).fetchone()
    conn.close()
    return row[0]


# ---------- DM queue ----------

def add_to_dm_queue(username: str, user_id: str, message: str):
    conn = get_conn()
    conn.execute("""
        INSERT INTO dm_queue (username, user_id, message)
        VALUES (?, ?, ?)
    """, (username, user_id, message))
    conn.commit()
    conn.close()


def get_pending_dms(limit: int = 10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM dm_queue WHERE status='pending'
        ORDER BY added_at ASC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_dm_done(dm_id: int, status: str = "sent", error: str = None):
    conn = get_conn()
    conn.execute("""
        UPDATE dm_queue SET status=?, sent_at=datetime('now'), error=?
        WHERE id=?
    """, (status, error, dm_id))
    conn.commit()
    conn.close()


# ---------- actions log ----------

def log_action(action: str, target: str, status: str, detail: str = None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO actions_log (action, target, status, detail)
        VALUES (?, ?, ?, ?)
    """, (action, target, status, detail))
    conn.commit()
    conn.close()


def actions_today(action: str) -> int:
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) FROM actions_log
        WHERE action=? AND status='success'
          AND date(ts)=date('now')
    """, (action,)).fetchone()
    conn.close()
    return row[0]


# ---------- competitors ----------

def add_competitor(username: str):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO competitor_accounts (username) VALUES (?)
    """, (username,))
    conn.commit()
    conn.close()


def get_competitors():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM competitor_accounts"
    ).fetchall()]
    conn.close()
    return rows


def update_competitor_scraped(username: str):
    conn = get_conn()
    conn.execute("""
        UPDATE competitor_accounts SET last_scraped_at=datetime('now')
        WHERE username=?
    """, (username,))
    conn.commit()
    conn.close()
