"""Turn manager: is this message a request to DO something, or just conversation?

  "action"   -> the tool brain (can open apps, set reminders, look things up...)
  "converse" -> the companion brain (persona, no tools, faster, can't fake actions)

Mixed messages ("rough day, play some music") count as action. Rules only, no AI call, < 1 ms.
If a real request slips through as "converse", the companion answers ACTION and it's handed back.
"""
import re

# Verbs that, at the start of a clause, are an order to the PC
VERBS = (r"open|close|quit|exit|launch|start|stop|play|pause|resume|skip|mute|unmute|set|turn|increase|decrease|"
         r"raise|lower|search|google|look up|find|show|type|press|take|remind|note|write down|draft|email|mail|send|"
         r"lock|shut down|shutdown|restart|reboot|run|go to|check|read|copy|save|delete|create|add|clear|cancel|"
         r"minimize|maximize|switch|install|download|bring up|pull up|put on|list|look for|compare|"
         r"write|rewrite|retype|update|edit|revise|redo|paste|replace")
# Start of a clause: start of text, after punctuation, or after polite/linking words
CLAUSE = (r"(?:^|[.,!?;:]\s*|\b(?:and|then|also|so|please|now|jarvis|keena|kina|kena|can you|could you|would you|will you|"
          r"i want you to|i need you to|go ahead and|just|help me|help me to|can you help me|can you help me to)\s+)")
ORDER = re.compile(CLAUSE + rf"(?:{VERBS})\b(?!\s+(?:me know|me think|it go|it be|on\b))")

# Things only the tools can deal with, wherever they appear
TOOL_WORDS = re.compile(r"\b(?:remind(?:er|ers)?|timer|alarm|weather|forecast|screenshot|volume|youtube|spotify|"
                        r"clipboard|wikipedia|notepad|chrome|browser|my notes|e-?mail|gmail|folder|downloads|"
                        r"calculator|wi-?fi|bluetooth|brightness|routine|work mode|focus mode)\b")

# Saving facts about him needs the remember tool, and only when he asks ("remember that...")
REMEMBER = re.compile(r"\b(?:remember (?:that|this)|don'?t forget|note (?:that|down)|save (?:that|this)|"
                      r"update (?:my|that|it))\b")
YES = re.compile(r"^\s*(?:yes|yeah|yep|sure|ok(?:ay)?|do it|go ahead|please do|yes please)\b[\s\w,.!]{0,20}$")
# Right after Jarvis DID something (typed a draft...): "make it more professional", "a more brief version",
# "update it better" are requests to redo it. They went to the chat side, which said "Content updated." (false)
REVISE = re.compile(r"\b(?:update|rewrite|redo|revise|edit|change|fix|improve|retype|again|better|version|shorter|longer|"
                    r"brief(?:er)?|concise|professional|formal|casual|polite|friendlier|detailed|simpler|"
                    r"make it|make that|more|less|instead|replace|add|remove)\b")


def classify(text, last_path=None, wrote=False):
    """wrote: the previous turn typed / drafted something (so "more brief" means redo it)."""
    t = re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip()
    # "Arjun is my CTO" used to count as a request to save it; since Milestone 2 Jarvis only saves when asked
    if ORDER.search(t) or TOOL_WORDS.search(t) or REMEMBER.search(t):
        return "action"
    # "yes, do it" right after an action turn (e.g. it asked to confirm something)
    if last_path == "tools" and YES.match(t):
        return "action"
    # a follow-up that changes what was just done ("more brief version", "it was not updated actually")
    if wrote and (REVISE.search(t) or re.search(r"\b(?:not|never) (?:updated|typed|done|changed|did)\b", t)):
        return "action"
    return "converse"
