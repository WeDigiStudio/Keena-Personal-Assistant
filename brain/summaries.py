"""Conversation summaries: continuity across sessions ("what did we talk about yesterday?").

A conversation = turns in the log with no gap longer than GAP. Once it's been quiet for GAP, it gets ONE short
summary, written in the background while Jarvis is idle (never during a turn):
  - commands only ("open chrome", "volume 30") -> his own words listed, no AI: exact and free
  - real conversation -> the local model writes 1-2 sentences; every name and number in it must appear in the
    conversation, otherwise his own words are stored instead (a summary must never invent a past)

  python -m brain.summaries     list them
"""
from config import ASSISTANT_NAME, USER_NAME
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

from brain.log import DB, SCHEMA as LOG_SCHEMA

GAP = timedelta(minutes=5)   # this much silence ends a conversation

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    id         INTEGER PRIMARY KEY,
    first_turn INTEGER NOT NULL,   -- turns.id range it covers
    last_turn  INTEGER NOT NULL,
    start_ts   TEXT NOT NULL,
    end_ts     TEXT NOT NULL,
    summary    TEXT NOT NULL,
    how        TEXT NOT NULL,      -- 'ai' or 'words' (his own words, no AI)
    created    TEXT NOT NULL
);
"""

PROMPT = (f"Summarise this conversation between {USER_NAME} and his AI companion {ASSISTANT_NAME} in one or two short sentences, "
          "in the third person ('He talked about ...'). Include only what was actually said: topics, plans, decisions, "
          "feelings he mentioned. No details that are not in the conversation, no advice, no commentary.")


class Summaries:
    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(LOG_SCHEMA + SCHEMA)

    # ------------------------------------------------------------------ which conversations need a summary
    def pending(self, now=None):
        """Finished conversations (quiet for GAP) that have no summary yet: [[(id, ts, user, reply, path), ...]]."""
        now = now or datetime.now()
        done = self.db.execute("SELECT COALESCE(MAX(last_turn), 0) FROM summaries").fetchone()[0]
        rows = self.db.execute("SELECT id, ts, user, reply, path FROM turns WHERE id > ? ORDER BY id", (done,)).fetchall()
        segments, current = [], []
        for row in rows:
            if current and datetime.fromisoformat(row[1]) - datetime.fromisoformat(current[-1][1]) > GAP:
                segments.append(current)
                current = []
            current.append(row)
        if current and now - datetime.fromisoformat(current[-1][1]) > GAP:
            segments.append(current)   # the last one only once it's really over
        return segments

    def write_pending(self, client, model, options, now=None):
        """Summarise every finished conversation. Returns how many were written."""
        written = 0
        for seg in self.pending(now):
            text, how = summarise(seg, client, model, options)
            self.db.execute("INSERT INTO summaries (first_turn, last_turn, start_ts, end_ts, summary, how, created)"
                            " VALUES (?,?,?,?,?,?,?)", (seg[0][0], seg[-1][0], seg[0][1], seg[-1][1], text, how,
                                                        datetime.now().isoformat(timespec="seconds")))
            self.db.commit()
            written += 1
        return written

    # ------------------------------------------------------------------ using them
    def all(self):
        return self.db.execute("SELECT id, start_ts, summary FROM summaries ORDER BY start_ts DESC").fetchall()

    def relevant(self, text, k=2, now=None):
        """The past conversations he's asking about: by date words ('yesterday', 'last week', 'on Monday'),
        then by shared keywords, newest first. [(start_ts, summary)]"""
        from brain.memory import _keywords
        now = now or datetime.now()
        rows = self.all()
        start, end = _window(text, now)
        if start:
            rows = [r for r in rows if start <= datetime.fromisoformat(r[1]) < end]
        # compare stemmed to stemmed: "previous" is stemmed to "previou", so the raw word list never matched it
        query = _keywords(text) - _keywords(" ".join(_PAST_WORDS))
        scored = []
        for rank, (_, ts, summary) in enumerate(rows):          # newest first
            overlap = len(query & _keywords(summary))
            scored.append((overlap, -rank, ts, summary))
        if query:
            # he named a topic: only conversations about it. Nothing about it -> nothing (an unrelated summary
            # made the model "remember" his wedding, his apartment in T Nagar...)
            scored = [s for s in scored if s[0]]
        scored.sort(reverse=True)
        return [(ts, summary) for _, _, ts, summary in scored[:k]]

    def forget_about(self, topic):
        from brain.memory import _keywords
        words = _keywords(topic)
        ids = [i for i, _, s in self.all() if words and words <= _keywords(s)]
        self.db.executemany("DELETE FROM summaries WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        return len(ids)


# words that point at the past but say nothing about the topic
_PAST_WORDS = {"talk", "talked", "discuss", "discussed", "spoke", "chat", "chatted", "said", "say", "tell", "told",
               "mention", "mentioned", "ask", "asked", "yesterday", "last", "time", "week", "month", "earlier",
               "before", "previous", "previously", "conversation", "morning", "evening", "night", "remember",
               "other", "day", "ago", "about", "anything", "something", "were", "we", "us", "our", "ours", "any",
               "the", "had", "have", "this", "that", "these", "those", "there", "thing", "things", "stuff", "all",
               "no", "yes", "okay", "ok", "so", "is", "are", "was", "it", "conversations", "chats", "talks"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _window(text, now):
    """Date range a question points at, or (None, None) for 'any time'."""
    t = text.lower()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "day before yesterday" in t:
        return day - timedelta(days=2), day - timedelta(days=1)
    if "yesterday" in t or "last night" in t:
        return day - timedelta(days=1), day
    if re.search(r"\b(?:today|this morning|this afternoon|earlier|tonight)\b", t):
        return day, day + timedelta(days=1)
    if re.search(r"\b(?:last|this|past) week\b|\bfew days\b", t):
        return day - timedelta(days=7), day + timedelta(days=1)
    if re.search(r"\b(?:last|this|past) month\b", t):
        return day - timedelta(days=31), day + timedelta(days=1)
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b(?:on |last )?{name}\b", t):
            back = (day.weekday() - i) % 7 or 7
            return day - timedelta(days=back), day - timedelta(days=back - 1)
    return None, None


def summarise(seg, client, model, options):
    """(summary, how). Commands only -> his words. Otherwise the model, checked against the conversation."""
    said = [user for _, _, user, _, _ in seg if user.strip()]
    if all(path in ("router", "smalltalk") for *_, path in seg):
        return "He asked Jarvis to: " + "; ".join(said[:8]) + ".", "words"
    transcript = "\n".join(f"{USER_NAME}: {u[:200]}\n{ASSISTANT_NAME}: {r[:200]}" for _, _, u, r, _ in seg[-20:])
    try:
        resp = client.chat(model=model, messages=[{"role": "system", "content": PROMPT},
                                                  {"role": "user", "content": transcript}],
                           options=dict(options, temperature=0.2, num_predict=80), think=False, keep_alive="30m")
        text = re.sub(r"<think>.*?</think>", "", resp.message.content or "", flags=re.S).strip()
    except Exception as e:
        print(f"   [summary failed: {e}]")
        text = ""
    if text and not _invented(text, transcript):
        return text, "ai"
    return "He said: " + " / ".join(s[:120] for s in said[:4]) + ".", "words"


def _invented(summary, transcript):
    """Names or numbers in the summary that never came up in the conversation."""
    from brain.companion import _ungrounded
    have = transcript.lower()
    names = [w for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b", summary)
             if w.lower() not in (USER_NAME.lower(), ASSISTANT_NAME.lower(), "jarvis", "he", "his") and w.lower() not in have]
    return bool(names or _ungrounded(summary, transcript))


def when(iso):
    d = datetime.fromisoformat(iso)
    return d.strftime("%A %d %B, %I:%M %p").replace(" 0", " ")


_store = None


def store():
    global _store
    if _store is None:
        _store = Summaries()
    return _store


if __name__ == "__main__":
    rows = store().all() if os.path.exists(DB) else []
    print("No conversation summaries yet." if not rows else "")
    for _, ts, summary in rows:
        print(f"{when(ts)}\n  {summary}")
    sys.exit(0)
