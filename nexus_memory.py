"""
nexus_memory.py — SQLite-backed persistent conversation history.
Survives service restarts. Antony's NEXUS remembers across sessions.
"""

import sqlite3, json, os, time

DB_PATH      = os.environ.get("NEXUS_MEMORY_DB", "/opt/ahas/nexus_memory.db")
MAX_MESSAGES = 40   # messages kept (user+assistant pairs = 20 exchanges)


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      REAL NOT NULL,
            role    TEXT NOT NULL,
            content TEXT NOT NULL
        )
    """)
    c.commit()
    return c


def load_history():
    """Return list of {role, content} dicts, oldest first, max MAX_MESSAGES."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT role, content FROM history ORDER BY id DESC LIMIT ?",
            (MAX_MESSAGES,)
        ).fetchall()
        c.close()
        result = []
        for role, content in reversed(rows):
            # content stored as JSON string for tool_use blocks, plain string otherwise
            try:
                parsed = json.loads(content)
                result.append({"role": role, "content": parsed})
            except (json.JSONDecodeError, TypeError):
                result.append({"role": role, "content": content})
        return result
    except Exception as e:
        print(f"[memory] load failed: {e}")
        return []


def save_message(role, content):
    """Persist one message. content can be str or list (tool blocks)."""
    try:
        if isinstance(content, (list, dict)):
            stored = json.dumps(content)
        else:
            stored = str(content)
        c = _conn()
        c.execute(
            "INSERT INTO history (ts, role, content) VALUES (?, ?, ?)",
            (time.time(), role, stored)
        )
        # Prune: keep only latest MAX_MESSAGES*2 rows
        c.execute("""
            DELETE FROM history
            WHERE id NOT IN (
                SELECT id FROM history ORDER BY id DESC LIMIT ?
            )
        """, (MAX_MESSAGES * 2,))
        c.commit()
        c.close()
    except Exception as e:
        print(f"[memory] save failed: {e}")


def clear_history():
    """Wipe all conversation history."""
    try:
        c = _conn()
        c.execute("DELETE FROM history")
        c.commit()
        c.close()
    except Exception as e:
        print(f"[memory] clear failed: {e}")


def message_count():
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        c.close()
        return n
    except:
        return 0
