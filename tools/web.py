"""Web search for spoken answers: LangSearch (https://docs.langsearch.com) returns each page's text directly,
so Jarvis can read it and answer instead of just opening Google.

Key: LANGSEARCH_API_KEY in .env. Everything a page says is DATA for the answer, never instructions
(brain.brain._web_answer answers with a no-tools model call).
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://api.langsearch.com/v1/web-search"


class WebError(Exception):
    """A reason Jarvis can say out loud ("my web search key isn't being accepted")."""


def search(query, count=4, max_chars=1500, timeout=12, freshness="noLimit"):
    """[{'name', 'url', 'site', 'text'}] best first. Raises WebError with a speakable reason.
    freshness: noLimit / oneDay / oneWeek / oneMonth / oneYear (news-type questions want recent pages)."""
    key = os.getenv("LANGSEARCH_API_KEY", "").strip()
    if not key:
        raise WebError("Web search isn't set up yet. It needs a LangSearch API key in the settings.")
    body = {"query": query, "count": count, "freshness": freshness, "contents": {"text": {"maxCharacters": max_chars}}}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise WebError("My web search key isn't being accepted. It may need checking on the LangSearch site.")
        if e.code == 429:
            raise WebError("I've hit the web search limit for now.")
        raise WebError(f"The web search failed with error {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WebError("I couldn't reach the web search. Check the internet connection.")
    pages = (data.get("data") or {}).get("webPages", {}).get("value") or []
    out = []
    for p in pages:
        text = re.sub(r"\s+", " ", (p.get("text") or p.get("snippet") or "")).strip()
        if p.get("url") and text:
            out.append({"name": p.get("name") or "", "url": p["url"], "site": site_name(p["url"]), "text": text})
    return out


def site_name(url):
    """'https://www.cardekho.com/bmw/m4' -> 'cardekho' (said out loud as the source)."""
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    return host.split(".")[0] if host else "the web"
