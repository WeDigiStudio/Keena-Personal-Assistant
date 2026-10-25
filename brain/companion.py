"""Companion brain: conversation only. Same model as the tool brain (no extra VRAM, no reload),
but a small persona prompt and NO tools, so it can't fake actions and reads ~1/3 of the tokens.

Cache-friendly layout: [persona + profile] [recent chat] [time + new message].
Everything before the new message is identical to last turn, so Ollama only reads the new part.
"""
import re
import time
from datetime import datetime
from difflib import SequenceMatcher

from config import CONTEXT_TOKENS, MODEL, USER_NAME, USE_MMAP
from brain import summaries
from brain.persona import profile_note, system_prompt

# Same num_ctx as the tool brain: a different value makes Ollama reload the model (seconds).
# Chat gets some life (0.7); questions get facts (0.2): at 0.7 it happily invents his favourite bands.
OPTIONS = {"num_ctx": CONTEXT_TOKENS, "temperature": 0.7, "top_p": 0.8, "num_predict": 120,  # ~3 spoken sentences
           "use_mmap": USE_MMAP}   # load option: must match the tool brain's or Ollama reloads the model
QUESTION_OPTIONS = dict(OPTIONS, temperature=0.2)
QUESTION = re.compile(r"\?\s*$|^(?:what|who|where|when|why|how|which|whose|do|does|did|is|are|am|was|were|can|could|"
                      r"tell me about|have|has)\b", re.I)
SELF = re.compile(r"\b(?:my|i|i'm|i've|mine|myself|about me)\b", re.I)   # not "me": "tell me a joke" isn't about him
DONT_KNOW = re.compile(r"you haven'?t told me|not (?:listed|mentioned) in", re.I)
SENTENCE_END = re.compile(r"[.!?][\"'”’)]*(?:\s|$)")   # also 'he said "no."'
MAX_SENTENCES = 4   # spoken replies: anything after the fourth sentence is dropped (3 cut 'But you know what?' off)
# a sentence that only leads into the next one: never the last thing she says
LEAD_IN = re.compile(r"^\W*(?:but |and |so |oh,? )?(?:you know what|guess what|here'?s the thing|listen|look|honestly)\W*$", re.I)
# The persona forbids these, but the model still slips them in: drop the sentence in code.
# Pep-talk lines only as short standalone sentences: "know when to keep going and when to quit" is fine.
BANNED = re.compile(r"let me know if|"
                    # a tic it used twice in a row (voice log 2026-10-25): "X, sir. Or as I prefer to call it, X."
                    r"\bas i (?:prefer|like|choose) to call (?:it|them)\b", re.I)

# "I've opened...", "I'll set a reminder", "let me check", "I'll keep the checklist ready": claims it can't keep
CLAIM = re.compile(r"\b(?:i'?ve|i have|i'?ll|i will|i'?m going to|let me|i can)\s+(?:\w+\s+){0,2}?"
                   r"(?:open(?:ed)?|set|sen[dt]|e-?mail(?:ed)?|remind(?:ed)?|schedul(?:e|ed)|updat(?:e|ed)|add(?:ed)?|"
                   r"book(?:ed)?|search(?:ed)?|look(?:ed)? (?:it |that )?up|check(?:ed)?|play(?:ed)?|sav(?:e|ed)|"
                   r"not(?:e|ed) (?:it|that|down)|launch(?:ed)?|creat(?:e|ed)|draft(?:ed)?|remember|prepare|make sure|"
                   r"make (?:a )?note|keep (?:an eye|track|tabs)|track|monitor|follow up|look into|watch (?:it|that|for)|"
                   r"ensure|arrange|organi[sz]e|handle (?:it|that|this)|take care of|sort (?:it|that|this) out|"
                   r"get (?:it|that|this) (?:done|sorted)|dim|turn (?:on|off|up|down)|order|prepare|book|"
                   r"keep (?:\w+ ){0,2}ready|have (?:it|that|them|\w+ \w+) ready|"
                   # "The link is broken, sir. Let me try again." - and nothing was tried
                   r"try|retry|reload|refresh|fix|find|fetch|pull (?:it |that )?up|navigate|go back)\b", re.I)

# "The page is not loading, sir." / "The link is broken": the chat side can't see his screen or any page
SEES = re.compile(r"\b(?:the |this |that |your )?(?:page|link|site|website|tab|video|screen|browser|url|result)s?\b(?:\s+\w+){0,2}?\s+"
                  r"(?:is|are|isn'?t|aren'?t|was|wasn'?t|seems?|appears?|looks?)\s+(?:to be\s+)?(?:still\s+)?(?:not\s+)?"
                  r"(?:loading|loaded|broken|found|opening|open|down|working|blank|empty|available|up|showing)\b", re.I)

# "I don't have personal preferences, sir": the persona allows taste, the model keeps disclaiming it
# (only preferences/opinions: "As an AI, I don't get bored" is simply true and must stay allowed)
DISCLAIM = re.compile(r"\bi (?:do not|don'?t) have (?:any )?(?:real )?(?:personal )?(?:preferences?|opinions?|"
                      r"inclinations?|favou?rites?|tastes?)\b", re.I)

# "Content updated." / "A more concise version is ready in Notepad." - done-claims with no "I" in them.
# The chat side can't type or save anything, so every one of these is false.
DONE_CLAIM = re.compile(r"\b(?:content|draft|email|mail|text|note|notes|version|document|file|message|letter|it|that)\s+"
                        r"(?:is |has been |was |have been )?(?:now )?(?:updated|typed|saved|rewritten|revised|written|drafted|"
                        r"changed|sent|edited|ready)\b|\bready in (?:notepad|gmail|the (?:notepad|document|draft))\b|"
                        r"\b(?:typed|updated|saved|done) (?:in|to) (?:notepad|gmail)\b", re.I)

# "I can't browse for venues": it's refusing something the tool brain CAN do -> hand the turn over
CANT = re.compile(r"\bi (?:can'?t|cannot|am unable to|'m unable to|am not able to|don'?t have the ability to)\s+(?:\w+\s+){0,2}?"
                  r"(?:browse|search|look (?:it |that |them )?up|open|access|check|find|play|set|send|draft|remind|book|"
                  r"go online|use the internet|provide (?:real-?time|live|current|the latest)|list|fetch|get (?:you )?(?:the )?"
                  r"(?:latest|current|live|real-?time))\b|\b(?:real-?time|live) (?:data|prices|information|info)\b", re.I)

# "The first time I met your mother...", "I remember when you...": invented shared past
MEMORY = re.compile(r"\b(?:the (?:first|last) time (?:i|we) (?:met|saw|talked)|when i (?:met|saw)|i remember (?:when|the time|you)|"
                    r"we (?:once|used to)|last (?:week|month|year|time) you (?:told|said)|"
                    r"you'?ve (?:mentioned|told me|said|talked about) (?:it|that|this|her|him|them)?\s*before|"
                    r"as you (?:mentioned|said|told me) (?:before|earlier)|i'?m aware(?=[,.]? (?:sir|you'?ve))|"
                    r"my (?:birthday|childhood|family|mother|father|parents))\b", re.I)   # an AI has none of these

# He's having a hard time (heartbreak, sadness, stress, fear)
LOW = re.compile(r"\b(?:sad|sadness|upset|depress(?:ed|ing)|lonely|alone|heart ?broken|broke(?:n)? (?:up|my heart)|"
                 r"rejected|dumped|cheated|crying|cried|cry|hurt(?:s|ing)?|anxious|anxiety|stressed|scared|afraid|"
                 r"hopeless|miserable|lost (?:my|a) |worthless|feel(?:ing)? (?:terrible|awful|empty))\b", re.I)
# Tired, drained, doubting himself: she fires him up (his ask), she doesn't go quiet and gentle
TIRED = re.compile(r"\b(?:tired|exhausted|drained|sleepy|burn(?:ed|t) out|no energy|lazy|bored|demotivated|unmotivated|"
                   r"can'?t do (?:this|it)|give up|giving up|want to quit|fed up|stuck|failed|failing|feel(?:ing)? (?:low|down|bad)|"
                   r"not in the mood|don'?t feel like (?:working|doing)|overwhelmed|stressed|"
                   r"(?:rough|bad|long|hard|tough|terrible|hectic|crazy|horrible) day|day (?:is|was) (?:rough|bad|long|hard|tough|"
                   r"terrible|hectic|horrible)|nothing is working|so much work|too much work)\b", re.I)
MOVING_ON = re.compile(r"\b(?:let'?s (?:get started|start|move on|get to work)|anyway|i'?m (?:fine|okay|good) now|"
                       r"feeling better)\b", re.I)
LOW_NOTE = ("He is having a hard time right now. Drop the jokes entirely. Be warm and steady, like a "
            "trusted friend: acknowledge what he feels in your own words (not 'I'm sorry to hear that'), then offer one "
            "gentle question or one genuinely helpful thought. Two or three sentences. Never brush it off.")
# Only "he's a founder": with his profile in view it listed projects ("you built an ERP for a college"), which he
# found irrelevant on a rough day. Energy and warmth, not his CV.
MOTIVATE_NOTE = ("He is tired, drained or having a rough day. Fire him up, personally and warmly, in two or three short "
                 "sentences: use his name, remind him who he is in a few words (he's a founder, building his own thing; "
                 f"nothing more specific, no projects, clients or people), and push him, like \"Hey {USER_NAME}, don't you "
                 "remember who you are? Come on, you can do this.\" Then one small, practical nudge (water, a five minute "
                 "break, the next tiny step). No pity, no lecture.")
# "suggest a good Vijay movie": two or three real picks, then her favourite (his ask, for the reel)
RECOMMEND = re.compile(r"\b(?:suggest|recommend|recommendation|what should i (?:watch|listen|read|eat|play)|any good|"
                       r"something (?:good|nice) to (?:watch|listen|read|eat)|best (?:movies?|films?|songs?|books?|shows?) of)\b", re.I)
RECOMMEND_NOTE = ("He wants recommendations. Name two or three specific, REAL picks you are sure exist (for an actor: his "
                  "well-known films), each with a few words on why, in flowing spoken sentences (no list). End with: "
                  "\"I think you'll love <one of them>, especially <a specific reason>.\" Up to four sentences.")


# He's referring to an earlier conversation
PAST = re.compile(r"\b(?:last time|yesterday|earlier today|this morning|the other day|day before yesterday|"
                  r"last (?:night|week|month)|(?:we|i) (?:talked|spoke|discussed|chatted)|"
                  r"did (?:i|we) (?:tell|say|mention|ask|talk|discuss)|what did (?:i|we)|remember when|"
                  r"we were talking|our (?:last|previous) (?:chat|conversation)|(?:i|we) told you|"
                  r"previous (?:chats?|conversations?|talks?)|conversations? we (?:had|have had)|"
                  r"what (?:have|had) we (?:talked|discussed|spoken)|what (?:were|was) we talking)\b", re.I)


# Claims about what was said before. With no record of it, every one of these is invented.
PAST_CLAIM = re.compile(r"\b(?:you (?:mentioned|said|told me|asked|were (?:saying|talking)|talked about|shared)|"
                        r"we (?:discussed|talked|spoke|chatted|covered)|i do,? sir|i remember|yes,? sir,? (?:i|you|we)|"
                        r"(?:it|that) was going to)\b", re.I)


def _past_note(logged):
    if not logged:
        return ("PAST CONVERSATIONS: there is NO record of any earlier conversation matching what he is asking about. "
                "Say plainly that you have no record of that; never invent or guess a past conversation.")
    from brain.memory import to_you
    # "Sam talked about a beach house in ECR" -> "you talked about ...": in the third person the model said
    # "I did mention the beach house" (he did)
    lines = "\n".join(f"- {summaries.when(ts)}: {to_you(s)}" for ts, s in logged)
    return ("FROM YOUR CONVERSATION LOG (real earlier conversations between him and you; 'you' below means him):\n"
            f"{lines}\nAnswer his question from this log. If he asks whether he told you something and it is in the "
            "log, say yes and remind him what he said. If the log doesn't cover it, say so. Never add details that "
            "are not in the log.")


def _repeats(said, earlier):
    """The first sentence (3+ words) it already said in the remembered conversation, or None
    ("That's not good." twice in a row)."""
    for s in re.split(r"(?<=[.!?])\s+", said):
        if len(s.split()) >= 3 and any(SequenceMatcher(None, s.lower(), e.lower()).ratio() > 0.8 for e in earlier):
            return s
    return None


# When every retry still repeats: a short line that fits the mood and hasn't been said yet
# (it used to end on "I'm afraid I lost my train of thought" right after he said he was sad)
LOW_FALLBACKS = [f"Take your time, {USER_NAME}. I'm right here.", "I'm listening, whenever you're ready.",
                 "That's alright. We can just sit with it for a moment."]
CALM_FALLBACKS = ["Go on, I'm listening.", "Hmm, tell me more.", f"I'm with you, {USER_NAME}.", "Okay, and then?"]


# Facts with a date, number or time in them: the kind small models invent ("Arjun's birthday is 23 April")
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
CLAIM_TOKENS = re.compile(rf"\b(\d+)(?:st|nd|rd|th)?\b|\b({MONTHS})\b|\b((?:next|last|this|coming) "
                          r"(?:week|weekend|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                          r"tomorrow|tonight|yesterday)\b", re.I)


WHEN_Q = re.compile(r"^\W*(?:when|what (?:date|day|year|time)|how (?:old|long|many|much)|which (?:year|month|date))\b", re.I)


def _ungrounded(said, sources):
    """Dates, numbers and time phrases in the reply that appear nowhere in what he wrote, told Jarvis, or just
    said. "Your CTO's birthday is on the 23rd of April" -> ['23', 'april'] (it was never told)."""
    have = sources.lower()
    out = []
    for m in CLAIM_TOKENS.finditer(said):
        token = (m[1] or m[2] or m[3]).lower()
        if m[2] and token[:3] in {w[:3] for w in re.findall(MONTHS, have)}:
            continue   # "Oct" vs "October"
        boundary = r"\b" if m[3] else r"(?!\d)"   # whole numbers only: "20" is not in "2003"
        if not re.search(rf"\b{re.escape(token)}{boundary}", have):
            out.append(token)
    return out


BIRTH = re.compile(rf"(?:birthday|born|birth ?date|date of birth)\D{{0,25}}?(?:(\d{{1,2}})(?:st|nd|rd|th)? ({MONTHS})\w*,? "
                   rf"(\d{{4}})|({MONTHS})\w* (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}}))", re.I)


def _age_note(sources):
    """'My birthday is August 11, 2003' -> 'He was born on 11 August 2003, so he is 23 years old now ...'
    Small models get ages wrong ("you're 20 now"); the code does the arithmetic."""
    m = None
    for found in BIRTH.finditer(sources):   # the last mention wins (a correction comes later)
        before = sources[max(0, found.start() - 25):found.start()]
        if not re.search(r"(?:'s|\b(?:mom|mum|dad|mother|father|wife|sister|brother|friend|son|daughter))\W*$", before, re.I):
            m = found   # his own birthday, not "his mom's birthday is ..."
    if not m:
        return ""
    day, month, year = (m[1], m[2], m[3]) if m[1] else (m[5], m[4], m[6])
    try:
        born = datetime.strptime(f"{int(day)} {month[:3]} {year}", "%d %b %Y").date()
    except ValueError:
        return ""
    today = datetime.now().date()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return f"He was born on {born.day} {born.strftime('%B %Y')}, so he is {age} years old now (calculated, not a guess)."


RELATION = {"mom": "mom", "mum": "mom", "mother": "mom", "amma": "mom", "dad": "dad", "father": "dad", "appa": "dad",
            "wife": "wife", "girlfriend": "girlfriend", "sister": "sister", "brother": "brother", "son": "son",
            "daughter": "daughter", "friend": "friend", "cto": "cto", "parents": "parents"}


def _relations(text):
    return {RELATION[w] for w in re.findall(r"[a-z]+", text.lower()) if w in RELATION}


def _misattributed(question, said, lines):
    """'When is MY birthday?' -> '7 October': a real date, but from his MOM's line. Dates in the reply must come
    from a line about the same person the question is about."""
    if not re.search(r"\b(?:my|i|me)\b", question, re.I):
        return []
    asked = _relations(question)
    bad = []
    for m in CLAIM_TOKENS.finditer(said):
        token = (m[1] or m[2] or "").lower()
        if not token:
            continue
        where = [ln for ln in lines if re.search(rf"\b{re.escape(token[:3] if m[2] else token)}", ln.lower())]
        if where and all(_relations(ln) - asked for ln in where):
            bad.append(token)   # every line with this date is about someone else
    return bad


# Phrases that are TRUE when a real summary of that conversation is in front of it
LOGGED_OK = re.compile(r"you'?ve (?:mentioned|told me|said|talked about)|as you (?:mentioned|said|told me)|i'?m aware|"
                       r"last (?:week|month|year|time) you (?:told|said)", re.I)


def _invented_past(said, has_log=False):
    """Invented shared history. 'You mentioned it before' is fine if the log says so; 'when I met your mother' never."""
    for m in MEMORY.finditer(said):
        if not (has_log and LOGGED_OK.match(m[0])):
            return True
    return False


def _problems(said, personal, earlier=(), sources=None, asks_when=False, question="", lines=(), has_log=False,
              no_record=False):
    out = []
    if no_record and PAST_CLAIM.search(said):
        out.append("There is NO record of that conversation. Do not describe what he said or what you discussed. "
                   f"Say \"I don't have any record of that, {USER_NAME}.\" and, if you like, ask him to tell you.")
    if personal and lines and (wrong := _misattributed(question, said, lines)):
        out.append(f"'{', '.join(wrong)}' belongs to someone else in his profile, not to what he asked about. "
                   f"If you don't know his own, say \"You haven't told me that yet, {USER_NAME}.\"")
    if personal and sources is not None and (bad := _ungrounded(said, sources)):
        out.append(f"'{', '.join(bad)}' is not in his profile, his memory or anything he said, so you do not know it. "
                   f"Do not guess dates, ages or numbers about him or his people: say \"You haven't told me that yet, {USER_NAME}.\"")
    elif asks_when and sources is not None and not DONT_KNOW.search(said) and not CLAIM_TOKENS.search(said):
        # "When did Ravi move in?" -> "when the office space was ready...": invented, just without a date in it
        out.append("He asked when / how many about his own life and nothing you know answers it. "
                   f"Say \"You haven't told me that yet, {USER_NAME}.\"")
    if said.strip() and all(BANNED.search(s) for s in re.split(r"(?<=[.!?])\s+", said.strip())):
        out.append("No stock phrases or pep-talk lines; say something specific.")
    if repeated := _repeats(said, earlier):
        out.append(f"You already said \"{repeated}\" earlier. Don't say it again: respond to what he just said, "
                   "in completely different words.")
    if DISCLAIM.search(said):
        out.append("No 'as an AI I have no preferences' disclaimers: you have taste and opinions. "
                   "Give your actual pick or view and one reason.")
    if CLAIM.search(said) or DONE_CLAIM.search(said):
        out.append("You cannot do actions in this mode and nothing was done; don't claim, promise or offer any.")
    if SEES.search(said):
        out.append("You cannot see his screen, the browser or any page. Don't describe them; ask him what he sees "
                   "or what he wants done.")
    if question and len(said.split()) >= 3 and SequenceMatcher(None, said.lower().strip(" ."),
                                                                question.lower().strip(" .")).ratio() > 0.8:
        out.append("You just repeated his words back to him. Respond to them instead.")   # "You never did that. Okay, bye."
    if _invented_past(said, has_log):
        out.append("You are an AI with no past, no birthday and no family, and no memories with him or his family; "
                   "don't invent any.")
    if DONT_KNOW.search(said) and not personal:
        out.append("It is not a question about him, so answer from your general knowledge of the world.")
    return out


def _about_him(text, profile):
    """Is this about him or his people? 'my', 'I', or a name from his profile (Arjun, PixelCraft, Ravi...)."""
    if SELF.search(text):
        return True
    names = {w.lower() for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", profile)}
    return any(re.sub(r"'s$", "", w.lower()) in names for w in re.findall(r"[A-Za-z']+", text) if w[0].isupper())


def chat_history(turns):
    """Brain history (with tool calls) -> plain you-said / I-said pairs the companion can read."""
    msgs = []
    for turn in turns:
        user = next((m["content"] for m in turn if m["role"] == "user"), "")
        said = next((m["content"] for m in reversed(turn) if m["role"] == "assistant" and m.get("content")), "")
        if user and said:
            msgs += [{"role": "user", "content": user}, {"role": "assistant", "content": said}]
    return msgs


def _tidy(s):
    """Spoken text only: '*Shakes head*' stage directions out, '*Mersal*' -> 'Mersal', no stray quotes."""
    # *pauses* / *Shakes head* / *laughs softly*: an action (a verb, or words after the first all lowercase)
    s = re.sub(r"\*(?:(?:[A-Za-z]+ )?(?:pauses?|laughs?|smiles?|sighs?|winks?|giggles?|grins?|nods?|chuckles?|shrugs?)\w*"
               r"(?: [a-z]+){0,4}|[A-Za-z][a-z]+(?: [a-z]+){1,5})\*\s*", "", s, flags=re.I)
    s = s.replace("*", "").replace("_", " ")
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", s)   # emoji: a voice can't say them
    return re.sub(r"\s+", " ", s).strip(" \"'")


class Companion:
    def __init__(self, client):
        self.client = client
        self.low = 0   # turns left in "he's having a hard time" mode

    def reply(self, text, history, profile, stats, on_sentence=None):
        """Returns the reply, or None if this is really an action request (hand it to the tool brain).
        on_sentence(s): called with each checked sentence as soon as it's ready, so it can be spoken
        while the rest is still being written. Everything returned has been passed to it."""
        now = datetime.now().strftime("%A %I:%M %p").replace(" 0", " ")
        personal = _about_him(text, profile)
        # Register, enforced in code: "she rejected me" got "A pity, sir." The model doesn't notice the
        # moment on its own, so it's told. Lasts this turn + 2 more ("Yeah." after "I feel sad" is still sad).
        if not history:
            self.low = 0
        if LOW.search(text):
            self.low = 3
        elif MOVING_ON.search(text):
            self.low = 0
        low, self.low = self.low > 0, max(0, self.low - 1)
        motivate = bool(TIRED.search(text)) and not low
        recommend = bool(RECOMMEND.search(text)) and not low
        show_profile = personal and not motivate   # a rough day gets warmth, not his profile read back to him
        # "what did we talk about yesterday?": the real summaries, or an explicit "no record" (never invent a past)
        logged = summaries.store().relevant(text) if PAST.search(text) else []
        past_note = _past_note(logged) if PAST.search(text) else ""
        no_record = bool(PAST.search(text)) and not logged   # asked about the past, nothing on record
        # the log goes WITH his question: as a separate system note the 8B model ignored it ("No, sir")
        messages = [{"role": "system", "content": system_prompt()}] + chat_history(history) + \
            ([{"role": "system", "content": profile_note(profile)}] if show_profile else []) + \
            ([{"role": "system", "content": LOW_NOTE}] if low else []) + \
            ([{"role": "system", "content": MOTIVATE_NOTE}] if motivate else []) + \
            ([{"role": "system", "content": RECOMMEND_NOTE}] if recommend else []) + \
            [{"role": "user", "content": f"[{now}] {text}" + (f"\n\n({past_note})" if past_note else "")}]
        if logged:
            profile = profile + "\n" + "\n".join(s for _, s in logged)   # summaries count as sources below
        # facts about him: low temperature. Everything else (jokes, opinions, fun facts): some life.
        self.options = QUESTION_OPTIONS if personal and QUESTION.search(text.strip()) else OPTIONS
        if recommend:   # real titles: less invention, room for three picks and her favourite
            self.options = dict(OPTIONS, temperature=0.35, num_predict=150)
        spoken = []   # sentences already handed to on_sentence (i.e. being said out loud)

        def say(s):
            s = _tidy(s)
            if not s:
                return
            spoken.append(s)
            if on_sentence:
                on_sentence(s)

        # Code-enforced honesty: up to 2 retries, each told exactly what was wrong.
        # While streaming, every sentence is checked BEFORE it's spoken; a bad first sentence means
        # nothing was said yet, so a retry is still invisible to him.
        past = chat_history(history)
        earlier = [s for m in past if m["role"] == "assistant" for s in re.split(r"(?<=[.!?])\s+", m["content"])]
        # what a date/number about him may come from: his profile + memory, his words now and earlier, today's date
        sources = " ".join([profile, text, datetime.now().strftime("%A %d %B %Y %H:%M")] +
                           [m["content"] for m in past if m["role"] == "user"])
        # "when did / how old / how many ..." about him: the answer must BE a date or number he gave
        asks_when = personal and bool(WHEN_Q.search(text))
        # each line of what he wrote/said, so a date can be traced to WHOSE it is
        lines = [ln for chunk in [profile] + [m["content"] for m in past if m["role"] == "user"]
                 for ln in re.split(r"\n|(?<=[.!?])\s+", chunk) if ln.strip()]
        age = _age_note(sources) if personal else ""
        if age:   # computed, not guessed; also counts as a source for the "23"
            sources += " " + age
            messages.insert(-1, {"role": "system", "content": age})
        for attempt in range(3):
            stop = []

            def gate(s):
                if stop or BANNED.search(s):
                    return
                if s.strip(" .").upper() == "ACTION" or CANT.search(s) or \
                        _problems(s, personal, earlier, sources, asks_when, text, lines, bool(logged), no_record):
                    stop.append(s)   # don't say it, or anything after it in this attempt
                    return
                if on_sentence:
                    say(s)
            said = self._stream(messages, stats, gate)
            if spoken:
                break    # he's already hearing this answer
            if said.strip(" .").upper() == "ACTION" or CANT.search(said):
                return None   # a real request (or one it says it can't do): the tool brain takes it
            problems = _problems(said, personal, earlier, sources, asks_when, text, lines, bool(logged), no_record)
            if not problems or attempt == 2:
                break
            if _repeats(said, earlier):
                # it keeps copying its last reply ("You're here, sir. That means..."): give it room for new words
                self.options = dict(self.options, temperature=min(1.0, self.options["temperature"] + 0.25))
            messages += [{"role": "assistant", "content": said}, {"role": "user", "content":
                         f"(System: not allowed: {' '.join(problems)} Answer his last message again, in character.)"}]
        if not spoken:   # not streaming, or every attempt failed: keep only the honest sentences
            def fake(s):
                return BANNED.search(s) or CLAIM.search(s) or DONE_CLAIM.search(s) or SEES.search(s) or \
                    _invented_past(s, bool(logged)) or \
                    _repeats(s, earlier) or \
                    (no_record and PAST_CLAIM.search(s)) or \
                    (personal and (_ungrounded(s, sources) or _misattributed(text, s, lines)))
            ungrounded = personal and (_ungrounded(said, sources) or _misattributed(text, said, lines) or asks_when)
            if no_record:
                fallback = f"I don't have any record of that, {USER_NAME}."
            elif ungrounded:
                fallback = f"You haven't told me that yet, {USER_NAME}."
            else:   # a line for the mood that hasn't been said yet
                pool = LOW_FALLBACKS if low else CALM_FALLBACKS
                fallback = next((f for f in pool if not _repeats(f, earlier)), pool[-1])
            for s in [s for s in re.split(r"(?<=[.!?])\s+", said) if s and not fake(s)] or [fallback]:
                say(s)
        return " ".join(spoken)

    def _stream(self, messages, stats, on_sentence=None):
        t0 = time.perf_counter()
        text, first_token, first_sentence, last = "", None, None, None
        stream = self.client.chat(model=MODEL, messages=messages, options=self.options, keep_alive="30m",
                                  think=False, stream=True)
        cut, sent = False, 0   # sent = how much of text has been passed on as sentences
        for chunk in stream:
            # Read to the end even after enough sentences: closing an Ollama stream early stalls
            # the NEXT request by ~2 s (measured). num_predict keeps a runaway short instead.
            last = chunk
            if cut:
                continue
            piece = chunk.message.content or ""
            if piece.strip() and first_token is None:
                first_token = time.perf_counter() - t0
            text += piece
            ends = list(SENTENCE_END.finditer(text))
            if ends and first_sentence is None:
                first_sentence = time.perf_counter() - t0
            if len(ends) >= MAX_SENTENCES:
                text, cut = text[:ends[MAX_SENTENCES - 1].end()], True
                ends = ends[:MAX_SENTENCES]
                start = ends[-2].end() if len(ends) > 1 else 0
                if LEAD_IN.match(text[start:].strip()):   # cut right after "But you know what?": drop it
                    text, ends = text[:start], ends[:-1]
            for m in ends:   # hand on each sentence the moment it's complete
                if m.end() > sent and on_sentence and text[sent:m.end()].strip():
                    on_sentence(text[sent:m.end()].strip())
                sent = max(sent, m.end())
        if on_sentence and text[sent:].strip():   # last words without a full stop
            on_sentence(text[sent:].strip())
        total = time.perf_counter() - t0
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        stats.append({"brain": "companion", "cut": cut, "prompt_tok": last.prompt_eval_count or 0,
                      "prompt_s": (last.prompt_eval_duration or 0) / 1e9, "gen_tok": last.eval_count or 0,
                      "gen_s": (last.eval_duration or 0) / 1e9, "load_s": (last.load_duration or 0) / 1e9,
                      "total_s": total, "first_token_s": first_token, "first_sentence_s": first_sentence or total})
        return text
