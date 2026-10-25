"""Long-term memory: facts he ASKED Jarvis to remember, with his exact words and the date.

Lives in data/jarvis.db next to the conversation log; never leaves this PC.
about_me.txt stays his hand-written profile; this is what Jarvis learned on top of it.

  python -m brain.memory        list everything remembered
"""
from config import USER_NAME
_ME = USER_NAME.lower()   # his name as written in facts ('Sam's car')
import os
import re
import sqlite3
import sys
from datetime import datetime

from brain.log import DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id       INTEGER PRIMARY KEY,
    fact     TEXT NOT NULL,      -- third person, as Jarvis uses it: "His mom likes jasmine"
    subject  TEXT,               -- "his mom like" -> a newer fact with the same subject replaces it
    said     TEXT,               -- his exact words when he asked ("remember that my mom likes jasmine")
    created  TEXT NOT NULL,
    updated  TEXT NOT NULL
);
"""


def subject_of(fact):
    """'His mom's name is Lakshmi' -> 'his mom name'. Two facts with the same subject = an update."""
    m = re.split(r"\s+(?:is|was|are|were|=)\s+", fact.strip().lower(), maxsplit=1)
    if len(m) < 2:
        return None
    subj = re.sub(r"'s\b|\bthe\b", "", m[0])
    # "Sam's mother's name" and "his mom's name" are the same thing: the newer one replaces the older
    words = [{_ME: "his", "he": "his"}.get(w, _SYNONYMS.get(w, w)) for w in subj.split()]
    return " ".join(words).strip()


class Memory:
    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def add(self, fact, said=""):
        """Returns 'updated' if it replaced an older fact about the same thing, else 'added'."""
        from brain.emails import normalize
        fact = normalize(fact.strip()).rstrip(".")   # "arjun at pixelcraftstudio dot com" -> arjun@pixelcraftstudio.com
        fact = fact[0].upper() + fact[1:]
        now = datetime.now().isoformat(timespec="seconds")
        subject = subject_of(fact)
        old = [row[0] for row in self.db.execute("SELECT id, subject FROM facts") if subject and row[1] == subject]
        if old:
            self.db.execute("UPDATE facts SET fact=?, said=?, updated=? WHERE id=?", (fact, said, now, old[0]))
            self.db.executemany("DELETE FROM facts WHERE id=?", [(i,) for i in old[1:]])
        else:
            self.db.execute("INSERT INTO facts (fact, subject, said, created, updated) VALUES (?,?,?,?,?)",
                            (fact, subject, said, now, now))
        self.db.commit()
        return "updated" if old else "added"

    def all(self):
        """[(id, fact, updated)] newest first."""
        return self.db.execute("SELECT id, fact, updated FROM facts ORDER BY updated DESC, id DESC").fetchall()

    def about(self, topic):
        """Facts mentioning every word of the topic ('arjun', 'my mom' -> 'his mom')."""
        words = [w for w in re.findall(r"[a-z0-9']+", _third_person(topic.lower())) if w not in STOP]
        if not words:
            return []
        return [r for r in self.all() if all(re.search(rf"\b{re.escape(w)}", r[1].lower()) for w in words)]

    def relevant(self, text, k=3):
        """The k facts that matter for this message, best first (keyword overlap + whose fact it is).
        'What's my favourite food?' -> 'His favourite food is biryani', not 'Arjun's favourite food is pizza'."""
        query = _keywords(text)
        if not query:
            return []
        names = {w.lower() for w in re.findall(r"\b[A-Z][a-z]+", text)} - {"what", "when", "where", "who", "how", "tell", "do"}
        about_him = bool(re.search(r"\b(?:my|i|me|myself|i'm|am i)\b", text, re.I))
        scored = []
        for rank, (i, fact, updated) in enumerate(self.all()):   # newest first: ties go to the newer fact
            overlap = len(query & _keywords(fact))
            if not overlap:
                continue
            owner = re.match(r"\s*(\w+)", fact)[1].lower()       # "His" / "Sam's" / "Arjun's" / "Mohan"
            owner = re.sub(r"'s$", "", owner)
            score = overlap
            if owner in names:
                score += 2                                       # he asked about Arjun: Arjun's facts first
            elif about_him and owner in ("his", "he", _ME):
                score += 1.5                                     # he asked about himself: his own facts first
            elif about_him and not names:
                score -= 1                                       # "my food" vs "Arjun's food"
            scored.append((score, -rank, i, fact, updated))
        scored.sort(reverse=True)
        return [(i, fact, updated) for score, _, i, fact, updated in scored[:k] if score > 0]

    def forget(self, ids):
        self.db.executemany("DELETE FROM facts WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        return len(ids)


STOP = {"the", "a", "an", "about", "his", "him", "he", "of", "my", "me", "i", "everything", "all", "that", "this"}

# words that carry no meaning for matching a fact to a question
_NOISE = STOP | {"what", "whats", "when", "where", "who", "whom", "which", "how", "why", "is", "are", "was", "were",
                 "do", "does", "did", "have", "has", "had", "to", "on", "in", "at", "for", "and", "or", "it", "its",
                 "you", "your", "tell", "know", "remember", "jarvis", "sir", "please", "can", "could", "would",
                 _ME, _ME + "s", "am", "be", "been", "go", "get", "got", "with", "from", "she", "her", "they",
                 "there", "some", "any", "much", "many", "now", "today", "again", "s"}
# same thing, different words: question and fact must meet in the middle
_SYNONYMS = {"mother": "mom", "mum": "mom", "amma": "mom", "father": "dad", "appa": "dad", "old": "birthday",
             "age": "birthday", "born": "birthday", "birth": "birthday", "drive": "car", "favorite": "favourite",
             "job": "work", "office": "work", "company": "work", "phone": "number", "mobile": "number"}


def _keywords(text):
    """'When does BrightNest launch?' -> {'brightnest', 'launch'}; 'launches' -> 'launch'."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("'s", "")):
        if w in _NOISE or len(w) < 2:
            continue
        s = _stem(w)
        out.add(_SYNONYMS.get(s, _SYNONYMS.get(w, s)))   # mothers -> mother -> mom; old -> birthday
    return out


def _stem(w):
    """Plurals / verb -s: works -> work, launches -> launch, names -> name, hobbies -> hobby, glass stays."""
    if len(w) <= 3 or w.endswith("ss"):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    return w[:-1] if w.endswith("s") else w


def _third_person(text):
    return re.sub(r"\b(my|me|i)\b", lambda m: {"my": "his", "me": "him", "i": "he"}[m[1]], text)


# "he <verb>": the verb needs an -s ("he loves"); "you <verb>" doesn't ("you love")
_AUX = {"is", "was", "has", "had", "will", "would", "can", "could", "should", "shall", "may", "might", "must", "did",
        "does", "and", "or", "too", "not", "never", "also", "really", "always", "usually", "often", "still", "just"}
_HE_VERB = re.compile(r"\b(he|He)\s+((?:really|also|always|usually|often|still|just|never)\s+)?([a-z]+)\b")
_IRREGULAR = {"have": "has", "do": "does", "go": "goes"}   # "he is" -> "you are" is a separate swap


def _third(verb):
    if verb in _AUX or verb.endswith(("ed", "ing")) or (verb.endswith("s") and not verb.endswith("ss")):
        return verb
    if verb in _IRREGULAR:
        return _IRREGULAR[verb]
    if verb.endswith(("s", "sh", "ch", "x", "z", "o")):
        return verb + "es"
    if verb.endswith("y") and verb[-2:-1] not in "aeiou":
        return verb[:-1] + "ies"
    return verb + "s"


def _base(verb):
    back = {v: k for k, v in _IRREGULAR.items()}
    if verb in back:
        return back[verb]
    if verb in _AUX or not verb.endswith("s") or verb.endswith("ss"):
        return verb
    if verb.endswith("ies"):
        return verb[:-3] + "y"
    if verb.endswith(("shes", "ches", "xes", "zes", "oes", "sses")):
        return verb[:-2]
    return verb[:-1]


def conjugate_he(fact):
    """'he love BMW M4' -> 'he loves BMW M4', 'he really like tea' -> 'he really likes tea'."""
    return _HE_VERB.sub(lambda m: f"{m[1]} {m[2] or ''}{_third(m[3])}", fact)


def to_you(fact):
    """'His mom likes jasmine' -> 'your mom likes jasmine', 'He loves the M4' -> 'you love the M4'
    (Jarvis says it TO him)."""
    fact = _HE_VERB.sub(lambda m: f"{m[1]} {m[2] or ''}{_base(m[3])}", fact)
    swaps = [(rf"\b{re.escape(USER_NAME)}'s\b", "your"), (rf"\b{re.escape(USER_NAME)}\b", "you"), (r"\bhe is\b", "you are"), (r"\bhe was\b", "you were"),
             (r"\bhe has\b", "you have"), (r"\bhis\b", "your"), (r"\bhimself\b", "yourself"), (r"\bhim\b", "you"),
             (r"\bhe\b", "you")]
    for pattern, repl in swaps:
        fact = re.sub(pattern, repl, fact, flags=re.I)
    return re.sub(r"^(Your|You)\b", lambda m: m[1].lower(), fact)   # mid-sentence; names keep their capital


def your(topic):
    """His words about a topic, said back to him: 'my car' -> 'your car', 'Arjun' -> 'Arjun'."""
    return re.sub(r"\b(my|myself|me)\b", lambda m: {"my": "your", "myself": "yourself", "me": "you"}[m[1].lower()],
                  topic, flags=re.I)


def when(iso):
    """'2026-10-10T11:20:00' -> 'today' / 'yesterday' / 'on 3 October'."""
    d = datetime.fromisoformat(iso).date()
    days = (datetime.now().date() - d).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"on {d.day} {d.strftime('%B')}"


_store = None


def store():
    """The one Memory for this process (created on first use)."""
    global _store
    if _store is None:
        _store = Memory()
    return _store


if __name__ == "__main__":
    # python -m brain.memory                          list what she remembers
    # python -m brain.memory --replace WORDS "FACT"   remove every fact containing all WORDS, then save FACT
    #   (a fact saved wrong by voice: "arjun at Pixelcraftstudi0.com" -> arjun@pixelcraftstudio.com)
    if len(sys.argv) == 4 and sys.argv[1] == "--replace":
        words, fact = sys.argv[2].lower().split(), sys.argv[3]
        m = store()
        for i, old, _ in m.all():
            if all(w in old.lower() for w in words):
                m.db.execute("DELETE FROM facts WHERE id=?", (i,))
                print(f"removed  #{i}  {old}")
        m.db.commit()
        print(f"{m.add(fact, said=fact)}: {fact}")
        sys.exit(0)
    rows = store().all() if os.path.exists(DB) else []
    if not rows:
        print("Nothing remembered yet (only about_me.txt).")
    for i, fact, updated in rows:
        print(f"{updated[:16]}  #{i}  {fact}")
    sys.exit(0)
