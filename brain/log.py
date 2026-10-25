"""Conversation log: every turn, saved locally in SQLite (data/jarvis.db, never leaves this PC).

The start of Milestone 2's memory: session summaries and facts will live in the same database.
Read it any time:  python -m brain.log            (last 20 turns)
                   python -m brain.log 50         (last 50)
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "jarvis.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,      -- local time, ISO
    session    TEXT NOT NULL,      -- one per Jarvis start
    user       TEXT NOT NULL,      -- what he said (as heard)
    reply      TEXT NOT NULL,      -- what Jarvis said
    path       TEXT,               -- smalltalk / router / companion / tools
    tools      TEXT,               -- JSON list of [name, args]
    ai_calls   INTEGER,
    prompt_tok INTEGER,
    first_s    REAL,               -- first sentence ready (companion)
    total_s    REAL
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
"""


class ConversationLog:
    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # check_same_thread=False: voice mode may call from another thread; writes are tiny and serial
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S")

    def add(self, user, reply, path, tools, calls, total_s):
        """Never let logging break Jarvis: any error is printed and ignored."""
        try:
            first = next((c["first_sentence_s"] for c in calls if c.get("first_sentence_s")), None)
            self.db.execute(
                "INSERT INTO turns (ts, session, user, reply, path, tools, ai_calls, prompt_tok, first_s, total_s)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), self.session, user, reply or "", path,
                 json.dumps(tools, ensure_ascii=False) if tools else None, len(calls),
                 sum(c.get("prompt_tok", 0) for c in calls), first, round(total_s, 3)))
            self.db.commit()
        except Exception as e:
            print(f"   [log error: {e}]")


def show(n=20):
    if not os.path.exists(DB):
        print("No conversations logged yet.")
        return
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT ts, path, user, reply, total_s FROM turns ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    for ts, path, user, reply, total in reversed(rows):
        print(f"{ts}  [{path}, {total:.1f}s]\n  You:    {user}\n  Jarvis: {reply}")


if __name__ == "__main__":
    show(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
