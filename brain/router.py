"""Instant command router: common commands are matched by rules, no AI needed.

Why: a small local model is slow (1-3 s) and sometimes forgets to call tools.
"Open Chrome" doesn't need intelligence, it needs to be instant and 100% reliable.
Anything the rules don't recognise (chat, multi-step, unusual requests) goes to the LLM.
"""
import re

from config import ROUTINES
from tools.pc_tools import SITE_ALIASES, SITE_SEARCH

POLITE = r"(?:(?:hey |ok |okay )?(?:keena|kina|kena|jarvis)[, ]*)?(?:can you |could you |would you |will you |please |kindly |just |now |go ahead and |okay |ok |so |alright |and |also |hey )*"
TAIL = r"(?: for me)?(?: please)?(?: now)?(?: (?:keena|kina|kena|jarvis))?[.!?]*$"

WEBSITES = {
    "youtube": "youtube.com", "gmail": "mail.google.com", "google": "google.com",
    "instagram": "instagram.com", "linkedin": "linkedin.com", "github": "github.com",
    "chatgpt": "chatgpt.com", "netflix": "netflix.com", "twitter": "x.com", "x": "x.com",
    "amazon": "amazon.in", "flipkart": "flipkart.com", "maps": "maps.google.com",
    "google maps": "maps.google.com", "whatsapp web": "web.whatsapp.com", "claude": "claude.ai",
}

KEY_NAMES = {"control": "ctrl", "escape": "esc", "windows": "win", "window": "win", "return": "enter",
             "delete": "delete", "backspace": "backspace", "spacebar": "space", "page down": "pagedown"}
KNOWN_KEYS = {"ctrl", "alt", "shift", "win", "enter", "esc", "tab", "space", "delete", "backspace", "up", "down", "left",
              "right", "home", "end", "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f11", "f12"} | \
             set("abcdefghijklmnopqrstuvwxyz0123456789")
_FILLER_WORDS ={"yeah", "yes", "yep", "ok", "okay", "so", "and", "please", "jarvis", "keena", "kina", "kena", "bro", "hey", "i", "do", "just"}
# "remember when we talked about..." / "remember what I said?" are questions about the past, not things to save
NOT_A_QUESTION = r"(?!(?:when|what|how|why|who|where|if|the time|me)\b)(?!.*\?\s*$)"
_SITES = [s for s in (*SITE_SEARCH, *SITE_ALIASES) if s not in ("youtube", "yt", "google", "mail", "x")]
SITE_WORDS = r"(?P<site>" + "|".join(sorted(map(re.escape, _SITES), key=len, reverse=True)) + r")"
MULTI_STEP = re.compile(r"\b(and|then|after|also|plus)\b")


def _m(pattern, text):
    return re.match(POLITE + pattern + TAIL, text, re.I)


def _to_third_person(fact):
    """'arjun is my cto' -> 'Arjun is his CTO' (about_me.txt is written about the user)."""
    swaps = {"my": "his", "i am": "he is", "i'm": "he is", "i've": "he has", "i'll": "he will", "i'd": "he would",
             "i": "he", "me": "him", "mine": "his", "myself": "himself"}
    out = re.sub(r"\b(i am|i'm|i've|i'll|i'd|my|mine|myself|me|i)\b", lambda m: swaps[m[1].lower()], fact, flags=re.I)
    return conjugate_he(out)   # "he love BMW M4" -> "he loves BMW M4"


NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
             "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "sixty": 60, "half an": 0.5, "half a": 0.5}
UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)"


def _minutes(num, unit):
    n = NUM_WORDS.get(num.lower(), None) if not re.match(r"^[\d.]+$", num) else float(num)
    if n is None:
        return None
    unit = unit.lower()
    return n / 60 if unit.startswith("s") else n * 60 if unit.startswith("h") else n


NUM = r"(\d+(?:\.\d+)?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|forty five|forty|sixty|half an|half a)"
CLOCK = r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)"


CALC = re.compile(r"\b(?:in|on|with|using|into|open|use)?\s*(?:the |my )?calculator\b", re.I)
FILE_KINDS = r"(?: video| file| song| movie| photo| picture| pdf| document| clip| audio)?"
FOLDER = r"(downloads?|desktop|documents?|videos?|music|pictures|photos)"
PLAYER = r"(vlc(?: media player)?|windows media player|media player|movies and tv|photos app)"


def _route_calculator(t):
    if not CALC.search(t):
        return None
    if re.search(r"\b(?:do|show|put|type) (?:it|that|this)\b", t, re.I) and _last_math["expr"]:
        return [("show_in_calculator", {"expression": _last_math["expr"], "answer": _last_math["answer"]})]
    rest = CALC.sub(" ", t)
    rest = re.sub(r"\b(?:can you|could you|please|kindly|able to|and|then|open|type|find|calculate|what is|what's|"
                  r"do|show|tell me|for me|(?:keena|kina|kena|jarvis))\b", " ", rest, flags=re.I)
    rest = re.sub(r"^\s*(?:plus|and)\s+", "", re.sub(r"\s+", " ", rest)).strip(" ,.?")
    e = math_expression(rest) if rest else None
    if not e:
        return None if rest else [("open_app", {"name": "calculator"})]
    ans = evaluate(e)
    if ans is None:
        return None
    _last_math.update(expr=e, answer=ans)
    return [("show_in_calculator", {"expression": e, "answer": ans})]


def _route_file(t):
    m = re.match(POLITE + r"(?:play|open|show|start|run)(?: me)? (?:the |my |a |that )?(.+?)" + FILE_KINDS +
                 r" (?:in|from|on|inside|which is in) (?:the |my )?" + FOLDER + r"(?: folder)?"
                 r"(?:,? (?:using|with|in|on) (?:the )?" + PLAYER + r")?$", t, re.I)
    if not m:
        return None
    args = {"name": m[1], "folder": m[2].lower()}
    if m[3]:
        args["app"] = m[3].lower()
    return [("open_file", args)]


def _route_extras(text):
    """Reminders, timers, notes, weather, folders, clipboard, power. Works on the original text."""
    t = re.sub(r"\s+", " ", text.strip()).rstrip(" .!?")
    P = POLITE
    for special in (_route_calculator, _route_file):
        found = special(t)
        if found:
            return found
    # Reminders
    if m := re.match(P + rf"remind me (?:in|after) {NUM} {UNIT} (?:to |that |about )?(.+)$", t, re.I):
        mins = _minutes(m[1], m[2])
        return [("set_reminder", {"text": _to_second_person(m[3]), "minutes": mins})] if mins else None
    if m := re.match(P + rf"remind me (?:to |that |about )?(.+?) (?:in|after) {NUM} {UNIT}$", t, re.I):
        mins = _minutes(m[2], m[3])
        return [("set_reminder", {"text": _to_second_person(m[1]), "minutes": mins})] if mins else None
    if m := re.match(P + rf"remind me (?:at|by) {CLOCK} (?:to |that |about )?(.+)$", t, re.I):
        return [("set_reminder", {"text": _to_second_person(m[2]), "at": m[1]})]
    if m := re.match(P + rf"remind me (?:to |that |about )?(.+?) (?:at|by) {CLOCK}$", t, re.I):
        return [("set_reminder", {"text": _to_second_person(m[1]), "at": m[2]})]
    if m := re.match(P + rf"(?:set |start |put )?(?:a |an )?{NUM} {UNIT} timer$", t, re.I) or \
            re.match(P + rf"(?:set |start |put )(?:a |the )?timer (?:for |of )?{NUM} {UNIT}$", t, re.I) or \
            re.match(P + rf"timer (?:for )?{NUM} {UNIT}$", t, re.I):
        mins = _minutes(m[1], m[2])
        return [("set_timer", {"minutes": mins})] if mins else None
    if re.match(P + r"(?:what are|show|list|read|tell me) (?:my |all )?(?:the )?(?:reminders|timers)(?: i have)?$", t, re.I) or \
            re.match(P + r"(?:do i have )?any reminders$", t, re.I):
        return [("list_reminders", {})]
    if re.match(P + r"(?:clear|delete|remove|cancel) (?:all )?(?:my |the )?(?:reminders|timers)$", t, re.I):
        return [("clear_reminders", {})]
    # A reminder with no time: ask for it, never guess one ("remind me to call mom" once got "in 10 minutes")
    if m := re.match(P + r"(remind me|set (?:a |me a )?reminder|don'?t (?:let me )?forget)(?: (to|that|about|for))? (.+)$",
                     t, re.I):
        verb, link, task = m[1].lower(), (m[2] or "").lower(), m[3]
        a_fact = verb.startswith("don") and link not in ("to", "about")   # "don't forget (that) my mom likes jasmine"
        has_time = re.search(rf"\b(?:in|after) {NUM} {UNIT}\b|\b(?:at|by) \d", task, re.I)
        if not a_fact and not has_time:
            return [("ask_reminder_time", {"text": _to_second_person(task)})]
    # Notes
    if m := re.match(P + r"(?:take|make|add|write) (?:a |down a |down )?note(?: that| of| saying)?[:,]? (.+)$", t, re.I) or \
            re.match(P + r"note (?:down |this )?(?:that )?[:,]?(.+)$", t, re.I):
        return [("take_note", {"text": m[1].strip(" :,")})]
    if re.match(P + r"(?:read|show|tell me|what are) (?:me )?(?:my |the )?(?:last |latest |recent )?notes$", t, re.I):
        return [("read_notes", {})]
    # Weather
    w = t.replace("?", ",")  # "...in Chennai? Tamil Nadu" -> "in Chennai, Tamil Nadu"
    if m := re.match(P + r"(?:what(?:'s| is)|how(?:'s| is)|what will|how will|tell me|check)? ?(?:the )?(?:weather|temperature|forecast)"
                         r"(?: be)?(?: like)?(?: (today|tomorrow|day after tomorrow))?(?: (?:in|at|for) ([a-z .,]+?))?"
                         r"(?: (today|tomorrow|day after tomorrow|now|right now|outside))?$", w, re.I):
        day = (m[1] or m[3] or "today").lower()
        day = "today" if day in ("now", "right now", "outside") else day
        args = {"day": day}
        if m[2]:
            args["city"] = m[2].strip(" ,")
        return [("get_weather", args)]
    if m := re.match(P + r"(?:is it|will it|is it going to|does it look like it(?:'s| is) going to) (?:be )?"
                         r"(?:raining|rain|hot|cold|sunny|cloudy)(?: (today|tomorrow|day after tomorrow))?"
                         r"(?: in ([a-z .,]+?))?(?: (today|tomorrow|day after tomorrow))?$", w, re.I):
        args = {"day": (m[1] or m[3] or "today").lower()}
        if m[2]:
            args["city"] = m[2].strip(" ,")
        return [("get_weather", args)]
    if m := re.match(P + r"(?:what(?:'s| is)|how(?:'s| is)|what will|how will) (?:the )?(?:weather|forecast|temperature)"
                         r"(?: be)?(?: like)? (tomorrow|day after tomorrow)(?: in ([a-z .,]+?))?$", t, re.I) or \
            re.match(P + r"(?:what(?:'s| is)|how(?:'s| is)) (?:the )?(?:weather|forecast)(?: like)?(?: in ([a-z .,]+?))? (tomorrow|day after tomorrow)$", t, re.I):
        g = [x for x in m.groups()]
        day = next(x for x in g if x and x.lower() in ("tomorrow", "day after tomorrow"))
        city = next((x for x in g if x and x.lower() not in ("tomorrow", "day after tomorrow")), None)
        return [("get_weather", {"day": day.lower(), **({"city": city.strip(" ,")} if city else {})})]
    # Folders
    if m := re.match(P + r"(?:open|show)(?: me)? (?:my |the )?(downloads?|documents?|desktop|pictures|photos|music|videos|screenshots|jarvis)(?: folder)?$", t, re.I):
        return [("open_folder", {"name": m[1].lower()})]
    if m := re.match(P + r"(?:open|show)(?: me)? (?:the |my )?(?:folder|directory) (?:named |called )?(.+?)"
                         r"(?: (?:in|on|from) (?:the |my )?([a-z]) ?(?:drive|:))?$", t, re.I) or \
            re.match(P + r"(?:open|show)(?: me)? (?:the |my )?(.+?) (?:folder|directory)"
                         r"(?: (?:in|on|from) (?:the |my )?([a-z]) ?(?:drive|:))?$", t, re.I):
        return [("open_folder", {"name": m[1], **({"location": f"{m[2]} drive"} if m[2] else {})})]
    if m := re.match(P + r"open (?:the |my )?([a-z]) ?drive$", t, re.I):
        return [("open_folder", {"name": f"{m[1]} drive"})]
    # Clipboard (plain read; "summarize what I copied" goes to the brain)
    if re.match(P + r"(?:read|what(?:'s| is)(?: in)?|tell me)(?: me)? (?:what i copied|my clipboard|the clipboard|what i(?:'ve| have) copied)$", t, re.I):
        return [("read_clipboard", {})]
    # Power
    if re.match(P + r"(?:shut ?down|turn off|power off|switch off) (?:my |the )?(?:pc|computer|laptop|system)$", t, re.I):
        return [("power", {"action": "shutdown"})]
    if re.match(P + r"(?:restart|reboot) (?:my |the )?(?:pc|computer|laptop|system)$", t, re.I):
        return [("power", {"action": "restart"})]
    return None


def _to_second_person(text):
    """'call my mom' stays 'call your mom' when Jarvis says it back."""
    swaps = {"my": "your", "i": "you", "me": "you", "mine": "yours", "myself": "yourself", "i'm": "you're", "i am": "you are"}
    return re.sub(r"\b(i am|i'm|my|mine|myself|me|i)\b", lambda m: swaps[m[1].lower()], text.strip(), flags=re.I)


def route(text):
    """Return a list of (tool, args) if every part of the command is recognised, else None.
    "open notepad and type hello" -> two steps, both instant."""
    extra = _route_extras(text)
    if extra:
        return extra
    memory = _route_memory(text)
    if memory:
        return memory
    # "remember ..." keeps your original capitals (names!) and isn't split on "and"
    m = re.match(POLITE + r"(?:remember|don'?t forget)(?: that| this)?[:,]? " + NOT_A_QUESTION + r"(.+?)[.!?]*$",
                 re.sub(r"\s+", " ", text.strip()), re.I)
    if m:
        return [("remember", {"fact": _to_third_person(m[1])})]
    # "My birthday is August 11, 2003. Remember that." -> the sentence before it, instantly
    m = re.match(r"(.+?)[.,!]?\s+(?:(?:please|(?:keena|kina|kena|jarvis))[, ]+)?(?:remember|don'?t forget|note) (?:that|this|it)(?: please)?[.!]*$",
                 re.sub(r"\s+", " ", text.strip()), re.I)
    # the part before must be a real statement: "Yeah, I remember that." once saved the fact "Yeah, he"
    content = [w for w in re.findall(r"[a-z0-9']+", m[1].lower()) if w not in _FILLER_WORDS] if m else []
    if m and len(content) >= 3 and not re.search(r"\b(?:remind|reminder)\b", m[1], re.I):
        return [("remember", {"fact": _to_third_person(m[1].strip(" ,."))})]
    steps = _route_text(text)
    if steps is None:
        # "Okay that's cool. Now reduce the volume to 30" -> ignore short chit-chat sentences in front
        sentences = [x for x in re.split(r"(?<=[.!?,])\s+", text.strip()) if x]
        if len(sentences) > 1 and all(len(x.split()) <= 5 for x in sentences[:-1]):
            steps = _route_text(sentences[-1])
    if steps:
        # "open youtube and play X" -> just search YouTube (opening it first only adds a second tab)
        if any(n == "play_on_youtube" for n, _ in steps):
            steps = [(n, a) for n, a in steps if not (n == "open_website" and "youtube" in a.get("url", ""))]
    return steps


# "Yeah, remember that." / "remember it" / "I remember that" right after he said something worth keeping
_REMEMBER_ALONE = re.compile(r"^(?:(?:yeah|yes|yep|ok|okay|so|and|please|(?:keena|kina|kena|jarvis)|bro|hey)[,.!]? )*(?:i )?(?:do )?"
                             r"(?:remember|note|save) (?:that|this|it)(?: (?:please|(?:keena|kina|kena|jarvis)|bro))?[.!]*$")
_FORGET_LAST = re.compile(POLITE + r"(?:forget|delete|erase|remove)(?: about)? (?:that|it|this|what i (?:just )?(?:said|told you))"
                          r"(?: from (?:your )?memory)?" + TAIL)
_FORGET_ABOUT = re.compile(POLITE + r"(?:forget|delete|erase)(?: everything| all(?: you know)?| what you know)? about (.+?)" + TAIL)
_RECALL = re.compile(POLITE + r"(?:what (?:do|did) you (?:know|remember)|what have i told you|what all do you know|"
                     r"tell me what you (?:know|remember))(?: about (.+?))?" + TAIL)


def _route_memory(text):
    """'what do you know about me' / 'forget that' / 'forget everything about Arjun': exact and instant,
    straight from memory. Forgetting always asks him to confirm first."""
    t = re.sub(r"\s+", " ", text.strip().lower().replace("’", "'"))
    if _REMEMBER_ALONE.match(t):
        return [("remember_last", {})]   # "remember that" with nothing else: his previous sentence
    # "Please do forget that." got "Understood, sir." from the chat side and nothing was forgotten:
    # any short sentence asking to forget "that/it" IS the command, whatever the polite words around it
    if _FORGET_LAST.match(t) or (len(t.split()) <= 8 and re.search(r"\b(?:forget|delete|erase) (?:that|it|this)\b", t)
                                 and not re.search(r"\b(?:don'?t|never|not|i|you) forget\b", t)):
        return [("forget_last", {})]
    if m := _FORGET_ABOUT.match(t):
        return [("forget_about", {"topic": _original_case(text, m[1])})]
    if (m := _RECALL.match(t)) and (not m[1] or m[1] in ("me", "myself")):
        # the full "what have I told you" list. "What do you know about Arjun / my car" goes to the companion:
        # it sees his profile AND his memory (Ravi is in the profile, not in memory)
        return [("recall", {})]
    return None


def _original_case(text, part):
    """The matched words with his capitals ('arjun' -> 'Arjun')."""
    i = text.lower().find(part)
    return text[i:i + len(part)] if i >= 0 else part


def _site_search(t):
    """'open amazon and search for a wireless mouse' / 'find X on flipkart' / 'search linkedin for Y' -> results
    inside that site, one step. Checked before splitting on "and" (queries contain it: "bread and butter").
    YouTube and Google keep their own paths (play the first video / web answer)."""
    # "open amazon, search for a mouse AND OPEN THE CHEAPEST ONE": the search, then the page commands after it
    for m in _THEN_ACTION.finditer(t):
        head = _site_search_one(t[:m.start()])
        rest = head and _route_text(t[m.end():])
        if head and rest:
            return head + rest
    return _site_search_one(t)


_THEN_ACTION = re.compile(r",? (?:and then|and|then|after that) (?=(?:open|click|add|scroll|read|show|tell|list|go|play|select|"
                          r"pick|choose|buy|what|how)\b)")


# "browse for", "try searching for" ("Open Amazon and browse for a gaming mouse" went to the slow AI: 8.9 s)
_VERB = (r"(?:try (?:to )?)?(?:search(?:ing)?|look(?:ing)?|find(?:ing)?|look(?:ing)? up|show me|check|get me|browse|"
         r"browsing|shop(?:ping)?|buy|order)(?: for| up| out| around for)?")


def _site_search_one(t):
    s = SITE_WORDS
    for p in (rf"(?:open|go to|launch) (?:the )?{s}(?: website| app| site)?,? (?:and |then |and then )?{_VERB} (?P<q>.+)",
              rf"{_VERB} (?P<q>.+?) (?:on|in|at|from) (?:the )?{s}(?: website| app| site)?",
              rf"(?:see,? |look,? |now,? )?(?:in|on) (?:the )?{s},? (?:can you |please |now |just )*{_VERB} (?P<q>.+)",   # "in flipkart, try searching for X"
              rf"(?:search|look up|browse) (?:the )?{s} (?:for|about) (?P<q>.+)",
              rf"{s} search(?: for)? (?P<q>.+)",
              # "Ok, in Flipkart" / "what about flipkart?": the same thing he was just looking at, on that site
              r"(?:(?:what|how) about (?:on |in )?|(?:and |now |try |check |see |look )*(?:in|on|at) )(?:the )?"
              r"(?P<site>amazon|flipkart|myntra)(?P<q>)(?: too| also| instead)?\??"):
        if m := _m(p, t):
            if re.search(r"\b(?:buy|order)\b", m[0]) and not re.search(r"\b(?:search|look|find|browse|shop)", m[0]):
                return [("shop_for", {"site": m["site"], "query": m["q"]})]   # "buy X on amazon": its product page
            return [("search_site", {"site": m["site"], "query": m["q"] or "the same"})]
    return None


def _merge_open_then_search(steps):
    """"close the amazon tab AND open flipkart AND search for the same mouse": 'open flipkart' + 'search for X' is
    ONE search inside Flipkart (it opened flipkart.com, then gave a web answer about the history of the mouse)."""
    out = []
    for name, args in steps:
        prev = out[-1] if out else None
        site = prev and prev[0] == "open_website" and next(
            (k for k in SITE_SEARCH if k.split()[0] in prev[1].get("url", "") and k not in ("google", "youtube")), None)
        if site and name in ("web_answer", "search_web"):
            out[-1] = ("search_site", {"site": site, "query": args.get("question") or args.get("query", "")})
            continue
        out.append((name, args))
    return out


ORDINALS = {"first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "last": -1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_ITEM = r"(?: one| result| link| video| product| item| option| listing| match)?"
_PICK = r"(?:open|click(?: on)?|show(?: me)?|go to|select|choose|pick|play|take me to|get me)"


def _route_browser(t):
    """Instant commands for the page in Jarvis's browser. The tools check which window is in front."""
    if m := _m(_PICK + r" (?:the )?(first|1st|top|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|last)" + _ITEM, t):
        return [("browser_open_result", {"n": ORDINALS[m[1]]})]
    if m := _m(_PICK + r" (?:the )?(?:result|option|number|item|video|product) (?:number )?(\d|one|two|three|four|five)", t):
        return [("browser_open_result", {"n": int(m[1]) if m[1].isdigit() else ORDINALS[m[1]]})]
    if m := _m(_PICK + r" (?:the )?(cheapest|lowest priced|least expensive|best rated|top rated|highest rated|best reviewed)" + _ITEM, t):
        return [("browser_open_result", {"which": "cheapest" if m[1] in ("cheapest", "lowest priced", "least expensive")
                                         else "best rated"})]
    if _m(r"(?:read|tell me|what are|list|show me|go through)(?: me)? (?:the |all the |all )?(?:top )?(?:results|options|"
          r"products|listings|search results)(?: on (?:the|this) page)?", t):
        return [("browser_results", {})]
    if _m(r"(?:add (?:it|this|that|this one|that one|this item|this product) )?(?:to (?:the |my )?cart)|add (?:it |this |that )?to (?:the |my )?(?:cart|basket)", t):
        return [("browser_click", {"target": "add to cart"})]
    if _m(r"buy (?:it|this|that)(?: one)?(?: now)?|buy now", t):
        return [("browser_click", {"target": "buy now"})]   # the tool asks before clicking
    # "now buy HyperX Pulsefire Core": the AI invented an Amazon link (page not found). Search, open the matching product.
    if (m := _m(r"(?:now )?(?:buy|order|get me|purchase) (?:me )?(?:a |an |the )?(.+)", t)) and len(m[1].split()) <= 10:
        return [("shop_for", {"query": m[1]})]
    # "open it" / "go with that one" after he asked which is best: the one Jarvis recommended
    if _m(r"(?:open|show(?: me)?|go with|get|choose|pick|select|take me to) (?:it|that|this|that one|this one|the one you "
          r"(?:said|suggested|recommended|picked)|your (?:pick|choice|recommendation))", t):
        return [("open_pick", {})]
    # "which is the best one?" while results are open: picked from the page (soft: chat answers it when no page is open)
    if _m(r"(?:what do you think[,.]? )?(?:which (?:one )?(?:is|would be|looks) (?:the )?(?:best|better|good|right)(?: one)?(?: for me)?|"
          r"which (?:one )?should i (?:buy|get|choose|pick|go (?:for|with))|what (?:do|would) you (?:suggest|recommend|pick|choose)|"
          r"(?:suggest|recommend) (?:me )?(?:one|the best(?: one)?|a good one|something)|"
          r"which (?:is|are) (?:the )?(?:best|cheapest|top rated|most popular)(?: one)?(?: here)?)", t):
        return [("page_answer", {"question": t, "soft": True})]
    if m := _m(r"(?:click|tap|hit)(?: on)? (?:the )?(.+?)(?: button| link| tab)?", t):
        return [("browser_click", {"target": m[1]})]
    if m := _m(r"press (?:the )?(.+?) (?:button|link)", t):
        return [("browser_click", {"target": m[1]})]
    if _m(r"(?:go to |open )?(?:the )?next page(?: of results)?", t):
        return [("browser_click", {"target": "next"})]
    if m := _m(r"scroll(?: the page)?(?: a bit| a little)? (down|up)(?: a bit| a little| more)?|scroll (?:to (?:the )?)?(top|bottom)"
               r"|(?:go )?(?:to )?(?:the )?(top|bottom) of (?:the |this )?page|page (down|up)", t):
        return [("scroll", {"direction": next(g for g in m.groups() if g)})]
    if _m(r"scroll", t):
        return [("scroll", {"direction": "down"})]
    if _m(r"(?:go )?back(?: to the previous page)?|(?:go to )?(?:the )?previous page", t):
        return [("browser_nav", {"action": "back"})]
    if _m(r"go forward", t):
        return [("browser_nav", {"action": "forward"})]
    if _m(r"(?:refresh|reload)(?: the| this)?(?: page| tab)?", t):
        return [("browser_nav", {"action": "reload"})]
    if m := _m(r"search (?:here|this (?:site|page)|on this (?:site|page)|in this (?:site|page))(?: for)? (.+)", t):
        return [("browser_search", {"query": m[1]})]
    # questions about the page he's looking at: answered by the brain from the page text
    if _m(r"(?:what(?:'s| is) (?:the |its )?(?:price|cost|rating|delivery date)(?: of (?:it|this|that))?|how much (?:is|does) (?:it|this|that)"
          r"(?: one| product)?(?: cost)?|what(?:'s| are) the (?:reviews?|ratings?|specs?|specifications|features|details|"
          r"key features)(?: like)?|(?:how|what) (?:are|is) the (?:reviews?|ratings?|reviewers?)(?: like| saying)?|"
          r"what do (?:people|customers|buyers|users|reviewers|the reviews) (?:say|think)(?: about (?:it|this))?|"
          r"(?:read|summari[sz]e) (?:this|the) (?:page|article|product|reviews?)|what(?:'s| is) (?:on )?this page(?: about)?|"
          r"what does (?:it|this|this page) say|is (?:it|this) (?:in stock|available|good|worth it)|when will (?:it|this) "
          r"(?:arrive|be delivered))", t):
        return [("page_answer", {"question": t})]
    return None


def _route_text(text):
    t = re.sub(r"\s+", " ", text.strip().lower())
    if not t:
        return None
    if steps := _site_search(t):
        return steps
    parts = [p.strip(" ,.") for p in re.split(r",? (?:and then|and also|and|then|after that) ", t)]
    if len(parts) > 3:
        return None
    steps = []
    for part in parts:
        one = _route_one(part)
        if one is None:
            return None  # any part we don't understand -> let the brain handle the whole thing
        steps += one
    steps = _merge_open_then_search(steps)
    # Typed text keeps your original capitals ("type Hello World", not "hello world")
    for name, args in steps:
        if name == "type_text":
            i = text.lower().find(args["text"])
            if i >= 0:
                args["text"] = text[i:i + len(args["text"])]
    return steps


def _route_one(t):
    if not t:
        return None
    # "remember that arjun is my cto" -> saved permanently (checked before the multi-step guard,
    # since facts often contain "and")
    if m := re.match(POLITE + r"(?:remember|don'?t forget)(?: that| this)?[:,]? " + NOT_A_QUESTION + r"(.+?)[.!?]*$", t, re.I):
        return [("remember", {"fact": _to_third_person(m[1])})]
    if MULTI_STEP.search(t):
        return None

    # Typing (usually the second half of "open notepad and type hello")
    # ("write ..." is left to the brain: "write a poem" means compose, not type literally)
    # Dictation: everything he says next is typed as-is ("type one by one what I say" once typed those words)
    if re.match(POLITE + r"(?:start |begin |turn on )?(?:dictation|dictate)(?: mode)?(?: on)?$|"
                r"(?:" + POLITE + r")?type (?:one by one |exactly |down )?(?:what(?:ever)? i (?:say|speak|tell you)|as i (?:say|speak)|"
                r"after me|everything i say)", t, re.I):
        return [("start_dictation", {})]
    if m := re.match(POLITE + r"type(?: out)? (.+?)[.!?]*$", t, re.I):
        # "type the draft again" refers to something; it once typed the words "the draft again in the notepad"
        if re.match(r"(?:the |that |this |my |a |an )?(?:draft|email|mail|content|message|letter|it|that|this|"
                    r"same|whole|full|again|above|previous|new|better|shorter|brief)\b", m[1], re.I):
            return None
        return [("type_text", {"text": m[1]})]

    # Volume
    if m := _m(r"(?:set |change |put |turn |make |bring |increase |decrease |reduce |raise |lower |turn up |turn down |keep )?"
               r"(?:the |my )?(?:volume|sound)(?: level)? (?:to |at |on |till |upto |up to |down to |by )?(\d{1,3})(?: ?%| percent)?", t):
        return [("set_volume", {"level": int(m[1])})]
    if _m(r"(?:mute|silence)(?: the)?(?: volume| sound| pc| audio)?", t) or \
            _m(r"unmute(?: the)?(?: volume| sound| pc| audio)?", t):
        return [("media_control", {"action": "mute"})]

    # Relative volume ("turn the volume up" went to the AI: there was no instant path for it)
    if _m(r"(?:turn (?:the )?volume up|turn up the volume|volume up|increase (?:the )?volume|louder|make it louder|"
          r"raise (?:the )?volume)", t):
        return [("change_volume", {"step": 10})]
    if _m(r"(?:turn (?:the )?volume down|turn down the volume|volume down|decrease (?:the )?volume|lower (?:the )?volume|"
          r"quieter|make it quieter|reduce (?:the )?volume)", t):
        return [("change_volume", {"step": -10})]
    # System, battery, clipboard, sleep, key presses: instant paths the audit found missing
    if _m(r"(?:how(?:'s| is) (?:the |my )?(?:system|pc|computer|laptop)(?: doing| running)?|system status|"
          r"(?:what(?:'s| is) )?(?:my |the )?battery(?: level| percentage)?|(?:what(?:'s| is) )?(?:the )?(?:cpu|ram|memory) usage|"
          r"how much (?:ram|memory|battery)(?: is left| do i have)?)", t):
        return [("system_status", {})]
    if _m(r"(?:what(?:'s| is) (?:on|in) (?:my |the )?clipboard|read (?:my |the )?clipboard|what did i copy)", t):
        return [("read_clipboard", {})]
    if _m(r"(?:put|send) (?:the |my )?(?:pc|computer|laptop|system) to sleep|sleep (?:the |my )?(?:pc|computer|laptop)", t):
        return [("sleep_pc", {})]
    if m := _m(r"press (.+)", t):
        keys = [KEY_NAMES.get(k, k) for k in re.split(r"\s*(?:\+|plus|and|then)\s*|\s+", m[1].strip()) if k]
        if keys and all(k in KNOWN_KEYS for k in keys):
            return [("press_keys", {"keys": keys})]
    # Driving the browser page he's on (Jarvis's own Chrome window): before media/YouTube, so "play the second video"
    # picks a result instead of searching YouTube for "the second video"
    if steps := _route_browser(t):
        return steps

    # Media
    if _m(r"(?:pause|stop|resume|play)(?: the)?(?: music| song| video| it)?", t):
        return [("media_control", {"action": "play_pause"})]
    if _m(r"(?:next|skip)(?: the)?(?: song| track| video)?", t) or _m(r"play (?:the )?next(?: song| track| video)?", t):
        return [("media_control", {"action": "next"})]
    if _m(r"(?:previous|go back|last)(?: song| track| video)?", t) or _m(r"play (?:the )?previous(?: song| track| video)?", t):
        return [("media_control", {"action": "previous"})]

    # YouTube
    if m := _m(r"play (.+?) (?:on|in|from) youtube", t):
        return [("play_on_youtube", {"query": m[1]})]
    if m := _m(r"(?:search|look up|find) (.+?) (?:on|in) youtube", t):
        return [("play_on_youtube", {"query": m[1]})]
    if m := _m(r"play (.+?(?:song|songs|music|video|videos|playlist|trailer))", t):
        return [("play_on_youtube", {"query": m[1]})]
    # "play believer" / "play anirudh hukum": a title, so YouTube (bare "play" / "play the music" = media key, above;
    # games and apps go through "open X")
    if (m := _m(r"play (?!(?:the |a |some )?(?:game|it|that|this|again|next|previous)\b)(.+)", t)) and len(m[1].split()) <= 8:
        return [("play_on_youtube", {"query": m[1]})]

    # Web search. Answering is the default ("it can't search the web and answer, it just opens Chrome");
    # the browser only when he names it: "search google for ...", "... on google", "... in chrome", "show me results"
    if m := _m(r"(?:search|google|look up|find out|check)(?: the web| online| the internet)?(?: for| about)? (.+?)"
               r"(?: on google| in google| online| on the web| in chrome| in the browser)?", t):
        if re.search(r"^(?:\w+ )*?(?:google\b|search google)|\b(?:on|in) (?:google|chrome|the browser)\b|\bshow me\b", t) \
                or not re.match(r"(?:\w+ )*?(?:search|look up|find out|check (?:the web|online|the internet))", t):
            return [("search_web", {"query": re.sub(r"^google (?:for |about )?", "", m[1])})]
        return [("web_answer", {"question": m[1]})]

    # System
    if _m(r"(?:take|grab|capture) (?:a |the )?screen ?shot(?: of (?:my |the )?screen)?", t) or _m(r"screen ?shot", t):
        return [("take_screenshot", {})]
    if _m(r"lock (?:my |the )?(?:pc|computer|laptop|screen|system)", t):
        return [("lock_pc", {})]
    if _m(r"(?:how(?:'s| is) my (?:pc|computer|system)(?: doing)?|system status|(?:check )?(?:cpu|ram|battery)(?: usage| status| level)?)", t):
        return [("system_status", {})]

    # Routines ("start work mode", "work mode", "run focus mode")
    for name in ROUTINES:
        if _m(rf"(?:start |run |activate |enable |turn on |switch to )?(?:the )?{re.escape(name)}", t):
            return [("run_routine", {"name": name})]

    # the mail Keena typed into Gmail: send it (she asks "Send it to ...?" first)
    if _m(r"(?:yes,? |ok(?:ay)?,? )?send (?:it|that|the (?:e?mail|mail|draft|message))(?: now)?|send it off|hit send", t):
        return [("send_draft", {})]
    # The Notepad email draft -> one Gmail compose window
    if _m(r"(?:put|send|move|copy|open|transfer|paste) (?:it|this|that|the (?:draft|email|mail)|my (?:draft|email)) "
          r"(?:in|into|to|on) (?:the )?gmail", t) or _m(r"(?:open|move) (?:it |the draft )?in gmail", t):
        return [("draft_to_gmail", {})]
    # Close a browser tab: YouTube etc. live in tabs, not apps ("close the youtube video" looked for an app)
    if m := _m(r"(?:close|exit|shut|stop and close) (?:the |this |that |my )?(youtube|yt|gmail|linkedin|instagram|whatsapp web|"
               r"facebook|twitter|netflix|google)(?: video| tab| page| window)?", t):
        return [("close_tab", {"site": "youtube" if m[1] == "yt" else m[1].replace(" web", "")})]
    if _m(r"close (?:the |this |that )?(?:video|youtube video)", t):
        return [("close_tab", {"site": "youtube"})]
    if _m(r"close (?:the |this |that |current |the current )?(?:browser )?tab", t):
        return [("close_tab", {})]
    if m := _m(r"close (?:the |this |that )?(\w+) tab", t):
        return [("close_tab", {"site": m[1]})]
    # Close app
    if m := _m(r"(?:close|quit|exit|kill|shut) (?:the |my )?(.+?)(?: app| application| window)?", t):
        return [("close_app", {"name": m[1]})]

    # Open website or app
    if m := _m(r"(?:open|launch|start|run|go to|bring up) (?:the |my |up )?(.+?)(?: app| application| website| site| browser)?", t):
        target = m[1].strip()
        if target in WEBSITES:
            return [("open_website", {"url": WEBSITES[target]})]
        if re.search(r"\.(com|in|org|net|io|ai|dev|app|co)\b", target):
            return [("open_website", {"url": target.replace(" ", "")})]
        if len(target.split()) <= 4:  # app names are short; long phrases are probably not an app
            return [("open_app", {"name": target})]
    return None


# ---------------------------------------------------------------------------------------------
# Small talk: instant replies for chit-chat that doesn't need the AI (saves ~3 s each time)
# ---------------------------------------------------------------------------------------------
from datetime import datetime  # noqa: E402

from brain.persona import line  # noqa: E402
from brain.memory import conjugate_he  # noqa: E402

_SMALLTALK = [
    (r"(?:(?:that'?s|that is|it'?s|you'?re|you are) )?(?:very |so |really )?(?:good|great|nice|awesome|amazing|perfect|"
     r"brilliant|excellent|cool|fantastic|the best)(?: work| job| one)?(?:,? (?:keena|kina|kena|jarvis))?(?:[.!,]* (?:good|great|nice) (?:work|job))?",
     "praise"),
    (r"(?:well done|good job|good work|nice work|great job|great work)(?:,? (?:keena|kina|kena|jarvis))?(?:[.!,]* (?:well done|good job|good work))?",
     "praise"),
    (r"(?:hi|hello|hey|good (?:morning|afternoon|evening))(?:,? (?:keena|kina|kena|jarvis))?", "greeting"),
    (r"how are you(?: doing)?(?: today)?(?:,? (?:keena|kina|kena|jarvis))?|how(?:'s| is) it going|what'?s up", "how_are_you"),
    (r"(?:damn,? |wow,? )?(?:you(?:'re| are)?|you'?re just|that was|that'?s|that is|it was) (?:really |so |very |truly |just |simply |pretty |super )*"
     r"(?:amazing|awesome|brilliant|great|the best|smart|incredible|fantastic|genius|a genius|good|helpful|quick|fast|"
     r"impressive|cool|nice|perfect|insane|crazy good|fire)(?: (?:keena|kina|kena|jarvis))?(?:,? you know)?(?:,? (?:keena|kina|kena|jarvis))?",
     "praise"),
    (r"(?:so )?what (?:all |else |other things |are (?:all )?(?:the )?(?:other )?things )?(?:can|could) you (?:able to )?(?:do|help (?:me )?with)(?: for me)?(?:,? (?:keena|kina|kena|jarvis))?"
     r"|what are (?:all )?(?:the )?(?:other )?things (?:that )?you can (?:able to )?do(?:,? (?:keena|kina|kena|jarvis))?|what are your (?:features|capabilities|skills)"
     r"|what do you do",
     "capabilities"),
    (r"who are you|what(?:'s| is) your name|introduce yourself", "who"),
    (r"are you (?:there|awake|listening|online)(?:,? (?:keena|kina|kena|jarvis))?", "are_you_there"),
]   # (pattern, kind of line): the words themselves live in brain/persona.py


WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
                "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
                "thousand": 1000,
                "phi": 5, "fife": 5, "fi": 5}  # Whisper often hears "five" as "phi"
_last_math = {"expr": None, "answer": None}


def math_expression(t):
    """'what's five plus 5 equal to' -> '5 + 5' (only digits and + - * / ( ) . allowed), else None."""
    m = re.fullmatch(r"(?:what(?:'s| is)|calculate|how much is|tell me|whats|solve|find(?: what is| out)?|type)?\s*(.+?)"
                     r"(?:\s*(?:equals?(?: to)?|is equal to|=))?", t.strip(" .?!").lower())
    if not m:
        return None
    e = m[1].replace(",", "")
    # spoken numbers -> digits, only when the sentence is clearly maths
    if re.search(r"\b(plus|minus|times|into|divided|multiplied|percent|squared|cubed|square root|power)\b", e):
        e = re.sub(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", lambda x: str(WORD_NUMBERS[x[1]]), e)
    if not re.search(r"\d", e):
        return None
    pct = re.fullmatch(r"([\d.]+)\s*(?:%|percent)\s*of\s*([\d.]+)", e)
    if pct:
        e = f"{pct[1]} / 100 * {pct[2]}"
    e = re.sub(r"square root of\s*([\d.]+)", r"(\1) ** 0.5", e)
    e = re.sub(r"([\d.]+)\s*squared", r"(\1) ** 2", e)
    e = re.sub(r"([\d.]+)\s*cubed", r"(\1) ** 3", e)
    for words, op in ((r"plus|\band\b", "+"), (r"minus|less", "-"), (r"times|multiplied by|into|\bx\b", "*"),
                      (r"divided by|by|over", "/"), (r"to the power of|power", "**")):
        e = re.sub(words, f" {op} ", e)
    e = re.sub(r"\s+", " ", e).strip()
    if not re.fullmatch(r"[\d.\s+\-*/()]+", e) or not re.search(r"[+\-*/]", e):
        return None
    return e


def _quick_math(t):
    """'what's 5 plus 5' -> 'That's 10.'  Safe: only numbers and + - * / ( ) are ever evaluated."""
    e = math_expression(t)
    if not e:
        return None
    out = evaluate(e)
    if out is None:
        return None
    _last_math.update(expr=e, answer=out)
    return f"That's {out}."


def evaluate(e):
    """Safely evaluate a digits-and-operators expression; returns a nicely formatted string or None."""
    import ast
    import operator
    pct = re.fullmatch(r"([\d.]+)\s*(?:%|percent)\s*of\s*([\d.]+)", e)
    if pct:
        e = f"{pct[1]} / 100 * {pct[2]}"
    e = re.sub(r"square root of\s*([\d.]+)", r"(\1) ** 0.5", e)
    e = re.sub(r"([\d.]+)\s*squared", r"(\1) ** 2", e)
    e = re.sub(r"([\d.]+)\s*cubed", r"(\1) ** 3", e)
    for words, op in ((r"plus|\band\b", "+"), (r"minus|less", "-"), (r"times|multiplied by|into|\bx\b", "*"),
                      (r"divided by|by|over", "/"), (r"to the power of|power", "**")):
        e = re.sub(words, f" {op} ", e)
    e = e.strip()
    if not re.fullmatch(r"[\d.\s+\-*/()]+", e) or not re.search(r"[+\-*/]", e):
        return None
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.Pow: operator.pow, ast.USub: operator.neg}

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            if isinstance(node.op, ast.Pow) and abs(ev(node.right)) > 10:
                raise ValueError
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError
    try:
        val = ev(ast.parse(e, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
        return None
    if abs(val - round(val)) < 1e-9:
        return f"{int(round(val)):,}"
    return f"{val:,.4f}".rstrip("0").rstrip(".")


_BIG = re.compile(POLITE + r"(?:go |switch to |enter |activate |turn on |open |start |show )?(?:the |your )?"
                  r"(?:(?:big|full|large) ?screen(?: mode)?|full ?screen|big mode|go big|take over the screen)(?: on)?" + TAIL)
_SMALL = re.compile(POLITE + r"(?:(?:exit|leave|close|stop|turn off|end|quit) (?:the )?(?:big|full|large) ?screen(?: mode)?"
                    r"|(?:go |switch to |back to )?(?:the )?(?:small|normal|mini|compact|corner) ?(?:screen|mode|screen mode)?"
                    r"|go small|minimi[sz]e(?: yourself)?|back to (?:the )?corner)" + TAIL)


def screen_mode(text):
    """'big screen' -> 'big', 'mini mode' -> 'small', anything else -> None. Instant, no AI."""
    t = re.sub(r"[\s,-]+", " ", text.strip().lower()).strip(" ")   # Whisper writes "mini-mode", "big screen, Jarvis."
    if _BIG.match(t):
        return "big"
    if _SMALL.match(t):
        return "small"
    return None


def smalltalk(text):
    """Instant reply for simple chit-chat, or None."""
    t = re.sub(r"\s+", " ", text.strip().lower()).strip(" .!?")
    if not t or len(t.split()) > 12:
        return None
    t = re.sub(r"^(?:(?:keena|kina|kena|jarvis)[, ]+|ok(?:ay)?[, ]+|wow[, ]+|oh[, ]+)+", "", t)
    t = re.sub(r"[.!?,]*\s+(?:yes|yeah|man|bro|dude|buddy|sir|(?:keena|kina|kena|jarvis))$", "", t)  # trailing filler
    math = _quick_math(t)
    if math:
        return math
    if re.fullmatch(r"(?:what(?:'s| is) the )?time(?: is it)?(?: now)?|what time is it(?: now)?", t):
        return f"It's {datetime.now().strftime('%I:%M %p').lstrip('0')}."
    if re.fullmatch(r"what(?:'s| is) (?:the |today'?s )?(?:date|day)(?: today)?|what day is (?:it|today)", t):
        return f"It's {datetime.now().strftime('%A, %d %B')}."
    for pattern, kind in _SMALLTALK:
        if re.fullmatch(pattern, t):
            return line(kind)
    return None
