"""Plans he mentions with times -> an offer to set reminders (Milestone 5's first, opt-in piece).

"At 2 pm I'm going to the barber, by midnight a movie with friends" ->
"Shall I remind you 15 minutes before: the barber at 2 PM, and a movie with friends at 12 AM?"

Rules (CLAUDE.md: never invent reminder details):
  - only times he actually said become reminders; a plan without a clear time is mentioned, never guessed
  - nothing is set until he says yes; times already past today are skipped
  - plain code, no AI: it can't make up a plan
"""
import re
from datetime import datetime, timedelta

LEAD = timedelta(minutes=15)   # remind this long before; said out loud in the offer

_TIME = re.compile(r"\b(?:at|by|around|before|for)\s+(?:(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?|(noon|midnight))(?!\w)"
                   r"|\b(midnight|noon)\b", re.I)
_VAGUE = re.compile(r"\b(?:tonight|this evening|in the evening|evening|this afternoon|afternoon|this morning|later today)\b", re.I)
_PLAN = re.compile(r"\b(?:going|go|have|having|has|will|'ll|plan(?:ning)?|meeting|meet|need to|got|attend(?:ing)?|"
                   r"call|visit|pick up|watch(?:ing)?|leave|leaving|appointment|class|event|workshop|movie|flight)\b", re.I)
_PAST = re.compile(r"\b(?:went|was|were|did|had(?! to)|yesterday|last (?:night|week))\b", re.I)
_LEAD_IN = re.compile(r"^(?:\W*(?:so|then|also|and|actually|you know|yes|yeah|okay|ok|today|tonight|tomorrow|"
                      r"after that|by|at|around)\W+)+", re.I)
_NOT_A_TASK = {"there", "it", "that", "this", "home", "out", "go", "going", "them"}
_SUBJECT = re.compile(r"^(?:i'?m going to|i am going to|i'?m going|i am going|i'?m having|i am having|i have to|i need to|"
                      r"i'?ve got (?:a |to )?|i have got|i have|i'?ll|i will|i'?m planning to|i am planning to|planning to|"
                      r"i'?m|i am|i)\s+", re.I)


def extract(text, now=None):
    """([(task, when)], [vague tasks]) for plans with a time. Nothing for questions or past events."""
    now = now or datetime.now()
    if text.strip().endswith("?"):
        return [], []
    # Whisper writes "2 p.m. I'm going..." - the dots would split the sentence before the plan
    text = re.sub(r"\b([ap])\.\s?m\.(?=\s|$|,)", lambda m: m[1].lower() + "m", text, flags=re.I)
    items, vague = [], []
    for clause in re.split(r"(?<=[.!?])\s+|,?\s+(?:and then|and|then|after that)\s+", text):
        if not clause.strip() or not _PLAN.search(clause) or _PAST.search(clause):
            continue
        m = _TIME.search(clause)
        if not m:
            if _VAGUE.search(clause):
                task = _task(_VAGUE.sub("", clause))
                if task and task not in vague:
                    vague.append(task)
            continue
        when = _when(m, clause, now)
        task = _task(clause[:m.start()] + " " + clause[m.end():])
        if when and task:
            items.append((task, when))
    return items, vague


def _when(m, clause, now):
    word = (m[4] or m[5] or "").lower()
    if word:
        hour, minute, ampm = (0, 0, "am") if word == "midnight" else (12, 0, "pm")
    else:
        hour, minute, ampm = int(m[1]), int(m[2] or 0), re.sub(r"[^ap]", "", (m[3] or "").lower())
        if hour > 23 or minute > 59:
            return None
        if ampm == "p" and hour < 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
    day = now + timedelta(days=1) if re.search(r"\btomorrow\b", clause, re.I) else now
    when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if word == "midnight" and when <= now:
        when += timedelta(days=1)          # "by midnight" = the coming midnight
    elif not ampm and not word and hour <= 12 and when <= now:
        when += timedelta(hours=12)        # "at 7" in the afternoon means 7 pm
    return when if when > now else None    # already past: no reminder


def _task(part):
    part = re.sub(r"\s+", " ", part).strip(" ,.!")
    part = _LEAD_IN.sub("", part)
    part = _SUBJECT.sub("", part)
    part = re.split(r"\b(?:where|which|who|because|so that|with my friends today|today)\b", part, maxsplit=1)[0]
    part = re.sub(r"\b(?:my)\b", "your", part, flags=re.I).strip(" ,.!")
    words = part.split()
    if not words or all(w.lower() in _NOT_A_TASK for w in words):   # "I'm going there tonight" is not a plan
        return ""
    return " ".join(words[:9])


def remind_at(when, now=None):
    """LEAD before the plan, or at the plan itself if that's too close. None if it's too late for either."""
    now = now or datetime.now()
    soon = now + timedelta(minutes=1)
    if when - LEAD > soon:
        return when - LEAD
    return when if when > soon else None


def offer(items, vague, now=None):
    now = now or datetime.now()

    def clock(d):
        if (d.hour, d.minute) == (0, 0):
            return "at midnight"                       # the coming midnight: no "tomorrow" needed
        day = " tomorrow" if d.date() > now.date() else ""
        return f"{day.strip()} at noon".strip() if (d.hour, d.minute) == (12, 0) else \
            f"{day.strip()} at {d.strftime('%I:%M %p').lstrip('0').replace(':00 ', ' ')}".strip()
    listed = [f"{task} {clock(when)}" for task, when in items]
    said = ", ".join(listed[:-1]) + (" and " if len(listed) > 1 else "") + listed[-1]
    text = f"Shall I remind you {int(LEAD.total_seconds() // 60)} minutes before: {said}?"
    if vague:
        text += f" {vague[0][0].upper() + vague[0][1:]} has no time yet; tell me when, if you'd like a reminder for it."
    return text


YES = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|please|ok(?:ay)?|do it|go ahead|of course|why not|absolutely|"
                 r"please do|yes please|sounds good)\b", re.I)
NO = re.compile(r"^\W*(?:no|nope|nah|don'?t|never ?mind|not now|no thanks|skip it|leave it)\b", re.I)
