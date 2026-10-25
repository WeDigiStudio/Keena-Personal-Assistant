"""Jarvis's own Chrome window, driven over Chrome's DevTools protocol (Playwright).

"If I ask Jarvis to open Amazon it can only open amazon.com, what's the use?" Pages Jarvis opens land in this
window, so he can go on: "open the first result", "add it to cart", "scroll down", "what's the price".

- A separate Chrome profile (data/chrome-profile): Chrome refuses remote control of the normal profile. He signs
  in to sites there once.
- Every Playwright call runs on ONE thread (its objects belong to the thread that made them); _call() hands work
  to it, so the voice loop, the bench and idle threads can all use this module.
- Safety: buttons that buy, pay, send, delete or submit ask first; nothing is ever typed into a password or card
  field; page text is returned as DATA for a no-tools model call (a page can't instruct Jarvis).
"""
import atexit
import ctypes
import difflib
import os
import queue
import re
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse

from config import BROWSER_PORT, BROWSER_PROFILE, CHROME_PATH


class BrowserError(Exception):
    """Message is spoken as-is."""


NOT_OPEN = "My browser isn't open. Ask me to open a site first."
RISKY = re.compile(r"\b(?:buy|pay|payment|place (?:your |the )?order|order now|checkout|check out|proceed to (?:buy|pay|checkout)|"
                   r"confirm|submit|send|post|publish|delete|remove|unsubscribe|subscribe|sign out|log ?out|transfer|"
                   r"book now|reserve)\b", re.I)
BLANK = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")

# search results per site: (part of the host, one result block, the link inside it)
RESULTS = [
    ("amazon.", 'div[data-component-type="s-search-result"]', 'a[href*="/dp/"]'),
    ("flipkart.", "div[data-id]", 'a[href*="/p/"]'),
    ("youtube.", "ytd-video-renderer, ytd-rich-item-renderer", "a#video-title, a#video-title-link"),
    ("google.", "#search div.g, #rso > div", "a:has(h3)"),
    ("github.", '[data-testid="results-list"] > div', 'a[href^="/"]:not([href*="/stargazers"])'),
    ("myntra.", "li.product-base", "a"),
    ("imdb.", "li.find-result-item, li.ipc-metadata-list-summary-item", "a"),
]
_RESULTS_JS = r"""([block, link]) => {
  const out = [], seen = new Set();
  const blocks = block ? document.querySelectorAll(block)
                       : document.querySelectorAll('main a[href], [role=main] a[href], #content a[href]');
  for (const b of blocks) {
    const a = block ? b.querySelector(link) : b;
    if (!a || !a.href || a.href.startsWith('javascript') || seen.has(a.href)) continue;
    const r = b.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    const txt = (b.innerText || '').trim();
    const h = b.querySelector('h2, h3, #video-title') || a;
    // the longest name wins: Flipkart's first text can be a badge ("Flipkart's Choice")
    const names = [(h.innerText || '').trim(), a.getAttribute('aria-label') || '', b.querySelector('a[title]') ?
                   b.querySelector('a[title]').title : '', (b.querySelector('img') || {}).alt || '']
                  .map(s => s.replace(/\s+/g, ' ').trim()).filter(s => s && s.length < 300);
    const title = (names.sort((x, y) => y.length - x.length)[0] || '').slice(0, 160);
    if (!block && title.length < 12) continue;      // generic pages: skip menu links
    seen.add(a.href);
    const price = b.querySelector('.a-price .a-offscreen');
    const stars = b.querySelector('[aria-label*="out of 5 stars"]');
    out.push({href: a.href, title,
              sponsored: /^Sponsored|\nSponsored\b/.test(txt.slice(0, 400)) || !!b.querySelector('.puis-sponsored-label-text'),
              price: price ? price.textContent : ((txt.match(/₹\s?[\d,]+(?:\.\d+)?/) || [])[0] || ''),
              rating: stars ? stars.getAttribute('aria-label') : ((txt.match(/\b([0-5]\.\d)\s*★/) || [])[1] || ''),
              count: ((txt.match(/\(([\d,.]+K?)\)/) || txt.match(/([\d,]+)\s*(?:Ratings|ratings|reviews|Reviews)/) || [])[1] || '')});
    if (out.length >= 30) break;
  }
  return out;
}"""
_CLICKABLE_JS = r"""() => {
  document.querySelectorAll('[data-jv]').forEach(e => e.removeAttribute('data-jv'));
  const els = document.querySelectorAll('a, button, [role=button], [role=link], [role=tab], [role=menuitem], ' +
                                        'input[type=submit], input[type=button], summary, [onclick]');
  const out = [];
  for (const e of els) {
    const r = e.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    const st = getComputedStyle(e);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const i = out.length;
    e.setAttribute('data-jv', i);
    out.push({i, text: (e.innerText || e.value || '').replace(/\s+/g, ' ').trim().slice(0, 100),
              label: (e.getAttribute('aria-label') || e.title || '').trim().slice(0, 100),
              inview: r.top >= 0 && r.top < innerHeight});
    if (out.length >= 1500) break;
  }
  return out;
}"""
_PAGE_TEXT_JS = r"""() => {
  const txt = s => { const e = document.querySelector(s); return e ? (e.innerText || e.getAttribute('title') || '').trim() : ''; };
  if (document.querySelector('#productTitle')) {        // Amazon product page: the parts that matter, in order
    const price = document.querySelector('#corePriceDisplay_desktop_feature_div .a-offscreen, #corePrice_feature_div .a-offscreen, .a-price .a-offscreen');
    return [['Product', txt('#productTitle')], ['Price', price ? price.textContent.trim() : ''],
            ['Rating', txt('#acrPopover')], ['Ratings count', txt('#acrCustomerReviewText')],
            ['Availability', txt('#availability')], ['Delivery', txt('#mir-layout-DELIVERY_BLOCK')],
            ['Overview', txt('#productOverview_feature_div')], ['About this item', txt('#feature-bullets')],
            ['Reviews summary', txt('#product-summary') || txt('[data-hook="cr-insights-widget-aspects"]')]]
      .filter(([k, v]) => v).map(([k, v]) => k + ': ' + v.replace(/\s+/g, ' ')).join('\n').slice(0, 6000);
  }
  const pick = ['#ppd', '#dp-container', 'main', 'article', '[role=main]', '#content', 'body']
    .map(s => document.querySelector(s)).find(e => e && (e.innerText || '').length > 200) || document.body;
  return (pick.innerText || '').replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim().slice(0, 6000);
}"""
SEARCH_BOX = ('input[type=search], input[name=q], input[name=k], input[name=field-keywords], input[name=search_query], '
              'input[role=combobox], input[aria-label*="earch" i], input[placeholder*="earch" i], textarea[name=q]')

_jobs = queue.Queue()
_thread = None
_state = {"pw": None, "browser": None, "page": None}


# ---------------------------------------------------------------- the browser thread
def _worker():
    while True:
        fn, args, box, done = _jobs.get()
        try:
            box["value"] = fn(*args)
        except Exception as e:
            box["error"] = e
        done.set()


def _call(fn, *args, timeout=45):
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_worker, daemon=True, name="jarvis-browser")
        _thread.start()
    box, done = {}, threading.Event()
    _state["used"] = time.time()
    _jobs.put((fn, args, box, done))
    if not done.wait(timeout):
        raise BrowserError("The browser took too long to respond.")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _port_open():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", BROWSER_PORT)) == 0


def _connect(launch):
    b = _state["browser"]
    if b and b.is_connected():
        return b
    if not _port_open():
        if not launch:
            raise BrowserError(NOT_OPEN)
        if not CHROME_PATH:
            raise BrowserError("I can't find Chrome on this PC.")
        os.makedirs(BROWSER_PROFILE, exist_ok=True)
        subprocess.Popen([CHROME_PATH, f"--remote-debugging-port={BROWSER_PORT}", f"--user-data-dir={BROWSER_PROFILE}",
                          "--no-first-run", "--no-default-browser-check", "--start-maximized",
                          "--autoplay-policy=no-user-gesture-required"])
        for _ in range(60):
            if _port_open():
                break
            time.sleep(0.15)
        else:
            raise BrowserError("Chrome didn't start.")
    if _state["pw"] is None:
        from playwright.sync_api import sync_playwright
        _state["pw"] = sync_playwright().start()
    # first start after a reboot: the port answers before Chrome's DevTools endpoint does (ECONNREFUSED; the
    # search fell back to his normal browser). Retry for a few seconds.
    for attempt in range(6):
        try:
            _state["browser"] = _state["pw"].chromium.connect_over_cdp(f"http://127.0.0.1:{BROWSER_PORT}", timeout=15000)
            return _state["browser"]
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.0)


def _pages(b):
    return [p for c in b.contexts for p in c.pages if not p.url.startswith(("devtools://", "chrome-extension://"))]


def _page(launch=False):
    """The tab he's looking at: the visible one (he may have switched tabs by hand), else the last one Jarvis used."""
    b = _connect(launch)
    pages = _pages(b)
    if not pages:
        if not launch:
            raise BrowserError(NOT_OPEN)
        return b.contexts[0].new_page()
    # the tab Jarvis used last, if he's still looking at it (with two windows, two tabs are "visible": it once read
    # the results of the OLD search)
    for p in ([_state["page"]] if _state["page"] in pages else []) + pages:
        try:
            if p.evaluate("document.visibilityState") == "visible":
                _state["page"] = p
                return p
        except Exception:
            pass
    last = _state["page"]
    return last if last in pages else pages[-1]


def _window_front(page):
    """Bring the Chrome window itself in front of other apps (bring_to_front only picks the tab)."""
    import pygetwindow as gw
    from tools import pc_tools as pc
    try:
        title = page.title()[:40]
    except Exception:
        return
    wins = [w for w in gw.getAllWindows() if w.title.endswith("Google Chrome") and title and title in w.title]
    if wins:
        pc._bring_forward(wins[0])


def _settle(page, ms=4000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=ms)
    except Exception:
        pass


def _follow_new_tab(page, before):
    """A click that opened a new tab: continue in that tab."""
    time.sleep(0.6)
    new = [p for p in _pages(page.context.browser) if p not in before] if page.context.browser else []
    if new:
        page = new[-1]
        _settle(page)
        page.bring_to_front()
    _state["page"] = page
    return page


def _short(title, words=9):
    w = re.sub(r"\s+", " ", title or "").strip().split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _label(page):
    """amazon.in -> Amazon (for speaking)"""
    return re.sub(r"\.(?:com|in|org|net|co|io)(?:\.\w+)?$", "", _site(page)).split(".")[-1].title() or "this page"


def _site(page):
    host = urlparse(page.url).hostname or ""
    return host.removeprefix("www.")


# ---------------------------------------------------------------- actions (run on the browser thread)
def _open_url(url):
    page = None
    b = _connect(True)
    pages = _pages(b)
    page = next((p for p in pages if p.url in BLANK), None) or _new_tab(b, pages)
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.bring_to_front()
    _state["page"] = page
    _window_front(page)
    return page.title()


def _new_tab(b, pages):
    """A new TAB in the existing window (Playwright's new_page() opens a whole new window over CDP)."""
    if pages:
        try:
            b.new_browser_cdp_session().send("Target.createTarget", {"url": "about:blank", "newWindow": False})
            for _ in range(20):
                new = [p for p in _pages(b) if p not in pages]
                if new:
                    return new[-1]
                time.sleep(0.1)
        except Exception:
            pass
    return (b.contexts[0] if b.contexts else b.new_context()).new_page()


def _results(page):
    host = urlparse(page.url).hostname or ""
    block, link = next(((b, l) for h, b, l in RESULTS if h in host), ("", ""))
    if block:
        try:
            page.wait_for_selector(block, timeout=6000)
        except Exception:
            pass
    found = page.evaluate(_RESULTS_JS, [block, link])
    organic = [r for r in found if not r["sponsored"]]
    return organic or found


def _number(s):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m[0].replace(",", "")) if m else None


def _open_result(n=1, which=None):
    page = _page()
    results = _results(page)
    if not results:
        raise BrowserError("I don't see any results on this page.")
    if which == "cheapest":
        priced = [r for r in results if _number(r["price"])]
        if not priced:
            raise BrowserError("I can't see prices on this page.")
        pick = min(priced, key=lambda r: _number(r["price"]))
    elif which == "best rated":
        rated = [r for r in results if _number(r["rating"])]
        if not rated:
            raise BrowserError("I can't see ratings on this page.")
        pick = max(rated, key=lambda r: _number(r["rating"]))   # max() keeps the first (higher-ranked) on ties
    else:
        n = int(n or 1)
        if n > len(results):
            raise BrowserError(f"There are only {len(results)} results on this page.")
        pick = results[n - 1] if n > 0 else results[n]
    _open_href(pick["href"], pick["title"])
    extra = f", {_spoken_price(pick['price'])}" if which == "cheapest" and pick["price"] else ""
    return f"Opening {_short(pick['title']) or 'it'}{extra}."


def _open_href(href, title=""):
    page = _page()
    page.goto(href, wait_until="domcontentloaded", timeout=25000)
    page.bring_to_front()
    _state["page"] = page
    _state["topic"] = product_name(title) or _state.get("topic")   # "search flipkart for the same mouse"
    _window_front(page)
    return f"Opening {_short(title or page.title())}."


SHOPS = ("amazon", "flipkart", "myntra")


def _match(query, title):
    """How much of what he named is in a result's title (0..1): 'hyperx pulsefire core' vs the listing names."""
    q = [w for w in _norm(query) if w not in ("a", "an", "the", "for", "with", "and", "me", "new")]
    t = set(_norm(title))
    return sum(w in t for w in q) / len(q) if q else 0


def _shop(query, site=None):
    """'Buy HyperX Pulsefire Core': search the shop he's on (else Amazon), open the listing that matches his words.
    Never clicks Buy: that's his "buy it now", which asks first."""
    from tools.pc_tools import SITE_SEARCH
    import urllib.parse
    if not site:
        try:
            here = _site(_page())
        except BrowserError:
            here = ""
        site = next((s for s in SHOPS if s in here), "amazon")
    label, template = SITE_SEARCH[site]
    _open_url(template.format(q=urllib.parse.quote_plus(query)))
    page = _page()
    results = _results(page)
    best = max(results[:12], key=lambda r: _match(query, r["title"]), default=None)
    _state["topic"] = query
    if not best or _match(query, best["title"]) < 0.6:
        return f"Here are the results for {query} on {label}. I don't see an exact match; tell me which one to open."
    _open_href(best["href"], best["title"])
    price = f", {_spoken_price(best['price'])}" if best["price"] else ""
    return f"Here's {product_name(best['title'])} on {label}{price}. Say buy it now when you're ready; I'll ask before paying."


# ---------------------------------------------------------------- Gmail: the mail types itself, sends on his yes
GMAIL_BODY = 'div[aria-label="Message Body"][contenteditable="true"], div[role="textbox"][aria-label*="Body"]'
GMAIL_SUBJECT = 'input[name="subjectbox"]'


def _not_signed_in(page):
    host = urlparse(page.url).hostname or ""
    return host.startswith("accounts.google.") or host.startswith("workspace.google.") or "/gmail/about" in page.url


def _compose_page():
    page = _state.get("compose")
    if page and not page.is_closed() and "mail.google.com" in page.url:
        return page
    return None


def _gmail_compose(to, subject, body, typing_delay=12):
    """Open (or reuse) Gmail's compose window in Keena's Chrome and TYPE the mail in, visibly. To and Subject come
    in with the address; the body is typed so he sees it being written. Never sends."""
    import urllib.parse
    page = _compose_page()
    if page is None:
        params = {"view": "cm", "fs": "1", "to": to or "", "su": subject or ""}
        url = "https://mail.google.com/mail/?" + urllib.parse.urlencode({k: v for k, v in params.items() if v},
                                                                        quote_via=urllib.parse.quote)
        _open_url(url)
        page = _page()
        # not signed in: Gmail redirects to the sign-in page or to the workspace.google.com marketing page (it sat
        # there 25 s). Open the sign-in page in HER window: her Chrome profile has its own logins, not his normal one's
        for _ in range(40):
            if _not_signed_in(page) or page.locator(GMAIL_BODY).count():
                break
            time.sleep(0.25)
        if _not_signed_in(page):
            page.goto("https://accounts.google.com/ServiceLogin?service=mail&continue=https://mail.google.com/mail/",
                      wait_until="domcontentloaded", timeout=25000)
            _window_front(page)
            raise BrowserError("Gmail isn't signed in, in my Chrome window. I've opened the sign-in page there: "
                               "sign in once, then ask me again.")
        try:
            page.wait_for_selector(GMAIL_BODY, timeout=20000)
        except Exception:
            raise BrowserError("Gmail's compose window didn't open. Is it signed in, in my Chrome window?")
        _state["compose"] = page
    else:   # a revision: same window, new subject and body
        page.bring_to_front()
        if subject:
            page.fill(GMAIL_SUBJECT, subject, timeout=5000)
    _window_front(page)
    box = page.locator(GMAIL_BODY).first
    box.click(timeout=5000)
    page.keyboard.press("Control+A")   # inside the message body: selects only the body
    page.keyboard.press("Backspace")
    page.keyboard.type(body.strip(), delay=typing_delay)   # "the mail types itself" (shot 3)
    return f"It's in Gmail{'' if to else ', but there is no address yet'}."


def _gmail_send():
    """Click Send in the compose window Keena filled, and say so only once Gmail confirms it."""
    page = _compose_page()
    if page is None:
        raise BrowserError("There's no draft open in Gmail to send.")
    page.bring_to_front()
    _window_front(page)
    page.locator(GMAIL_BODY).first.click(timeout=5000)
    page.keyboard.press("Control+Enter")   # Gmail's own Send shortcut
    try:
        page.get_by_text(re.compile(r"Message sent", re.I)).first.wait_for(timeout=12000)
    except Exception:
        if _compose_page() is not None and page.locator(GMAIL_BODY).count():
            raise BrowserError("Gmail didn't confirm it was sent. It's still open: check the address.")
    _state["compose"] = None
    return "Sent!"


def _close_tab(site=None):
    """Close a tab in Jarvis's window: the one showing the site, or the one in front."""
    pages = _pages(_connect(False))
    if site:
        key = site.lower().replace(" ", "")
        pages = [p for p in pages if key in (urlparse(p.url).hostname or "")]
        if not pages:
            return None   # not in Jarvis's window: the caller looks in his other browsers
        page = _state["page"] if _state["page"] in pages else pages[-1]   # the one he's looking at
    else:
        page = _page()
    title = _short(page.title(), 8)
    page.close()
    if _state["page"] is page:
        _state["page"] = None
    return f"Closed {title}." if title else "Closed the tab."


def _spoken_price(p):
    n = _number(p)
    if n is None:
        return ""
    return f"{n:,.0f} rupees" if "₹" in p or not re.search(r"[$£€]", p) else p.strip()


def _list_results(k=5):
    page = _page()
    results = _results(page)[:k]
    if not results:
        raise BrowserError("I don't see any results on this page.")
    parts = []
    for i, r in enumerate(results, 1):
        bits = [_short(r["title"], 7)]
        if r["price"]:
            bits.append(_spoken_price(r["price"]))
        if _number(r["rating"]):
            bits.append(f"{_number(r['rating']):.1f} stars")
        parts.append(f"{i}: " + ", ".join(b for b in bits if b))
    return "Top results. " + ". ".join(parts) + "."


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


def _score(target, cand):
    t = " ".join(_norm(target))
    best = 0.0
    for s in (cand["text"], cand["label"]):
        s = " ".join(_norm(s))
        if not s:
            continue
        if s == t:
            sc = 1.0
        elif re.search(rf"\b{re.escape(t)}\b", s):
            sc = 0.9 - min(0.25, (len(s) - len(t)) / 200)
        elif len(s) >= 4 and re.search(rf"\b{re.escape(s)}\b", t):
            sc = 0.7
        else:
            sc = difflib.SequenceMatcher(None, t, s).ratio() * 0.8
        best = max(best, sc)
    return best + (0.05 if cand["inview"] else 0)


def _click(target, confirm=None):
    page = _page()
    _window_front(page)
    target = re.sub(r"^(?:on |the |a )+|\s+(?:button|link|tab|option|icon)$", "", target.strip(" .!?").lower())
    cands = page.evaluate(_CLICKABLE_JS)
    scored = sorted(((_score(target, c), c) for c in cands), key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.62:
        raise BrowserError(f"I can't find {target} on this page.")
    best = scored[0][1]
    name = best["text"] or best["label"] or target
    if RISKY.search(name) or RISKY.search(target):
        if not confirm or not confirm(f"That will {name.lower()[:60]}. Shall I go ahead?"):
            return f"Okay, I didn't click {name[:60]}."
    before = _pages(page.context.browser) if page.context.browser else []
    page.locator(f'[data-jv="{best["i"]}"]').first.click(timeout=6000)
    _settle(page, 3000)
    _follow_new_tab(page, before)
    return f"Clicked {_short(name, 8)}."


def _search_here(query):
    page = _page()
    for box in page.locator(SEARCH_BOX).all()[:8]:
        try:
            if not box.is_visible() or (box.get_attribute("type") or "").lower() == "password":
                continue
            box.click(timeout=3000)
            box.fill(query, timeout=3000)
            box.press("Enter")
            _settle(page)
            _state["topic"] = query
            return f"Searching {_label(page)} for {query}."
        except Exception:
            continue
    # no usable box (Flipkart's login pop-up covered it): the site's own search address
    from tools.pc_tools import SITE_SEARCH
    import urllib.parse
    key = next((k for k in SITE_SEARCH if k.split()[0] in _site(page)), None)
    if key:
        page.goto(SITE_SEARCH[key][1].format(q=urllib.parse.quote_plus(query)), wait_until="domcontentloaded", timeout=25000)
        _state["topic"] = query
        return f"Searching {SITE_SEARCH[key][0]} for {query}."
    raise BrowserError(f"I can't find a search box on {_site(page) or 'this page'}.")


def _scroll(direction):
    page = _page()
    js = {"down": "window.scrollBy(0, innerHeight * 0.8)", "up": "window.scrollBy(0, -innerHeight * 0.8)",
          "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)"}[direction]
    page.evaluate(js)
    return {"down": "Scrolled down.", "up": "Scrolled up.", "top": "Back at the top.", "bottom": "At the bottom."}[direction]


def _nav(action):
    page = _page()
    page.bring_to_front()
    _window_front(page)
    # wait for "commit" only: a page restored from Chrome's back/forward cache never fires domcontentloaded
    # (Amazon "go back" hung 15 s, then failed)
    # go_back() returns None for a cache restore too, so "did it move" = did the URL change
    if action in ("back", "forward"):
        before = page.evaluate("location.href")
        page.evaluate("history.back()" if action == "back" else "history.forward()")
        moved = False
        for _ in range(30):   # a cache restore is instant, a real load a little slower
            time.sleep(0.1)
            try:
                moved = page.evaluate("location.href") != before
            except Exception:   # mid-navigation
                moved = True
            if moved:
                break
        _settle(page, 3000)
        if not moved:
            return "There's no previous page." if action == "back" else "There's no next page to go forward to."
    else:
        page.reload(wait_until="domcontentloaded", timeout=20000)
        return "Reloaded."
    title = _short(page.title(), 8)
    if not title:
        return "Gone back." if action == "back" else "Gone forward."
    return f"Back to {title}." if action == "back" else f"Forward to {title}."


def _page_text():
    """The page as text for a question about it. A results page is the result list (name, price, stars, how many
    ratings), so "which is the best one?" compares real listings ("the one that feels right in your hand, sir")."""
    page = _page()
    host = urlparse(page.url).hostname or ""
    results = _results(page) if any(h in host for h, _, _ in RESULTS) else []
    if len(results) >= 3:
        lines = [f"{i}. {r['title']}" + (f" | price {r['price'].strip()}" if r["price"] else "") +
                 (f" | {_number(r['rating']):.1f} stars" if _number(r["rating"]) else "") +
                 (f" from {r['count']} ratings" if r["count"] else "") for i, r in enumerate(results[:10], 1)]
        text = "SEARCH RESULTS:\n" + "\n".join(lines)
    else:
        text = page.evaluate(_PAGE_TEXT_JS)
    return {"title": page.title(), "site": _site(page), "url": page.url, "text": text, "results": results[:10]}


def _front_site():
    page = _page()
    return _site(page)


# ---------------------------------------------------------------- public API (any thread)
def open_url(url):
    return _call(_open_url, url)


def open_href(href, title=""):
    return _call(_open_href, href, title)


def shop(query, site=None):
    return _call(_shop, query, site, timeout=60)


def close_tab(site=None):
    return _call(_close_tab, site)


def gmail_compose(to, subject, body):
    return _call(_gmail_compose, to, subject, body, timeout=120)   # typing a long mail takes a while


def gmail_send():
    return _call(_gmail_send, timeout=40)


def gmail_open():
    """Is a draft Keena typed still open in Gmail?"""
    try:
        return _call(lambda: _compose_page() is not None, timeout=10) if _port_open() else False
    except Exception:
        return False


def _count(s):
    """'(15.7K)' -> 15700, '4,00,724' -> 400724"""
    m = re.search(r"([\d,.]+)\s*([KkLl]?)", s or "")
    if not m:
        return 0
    n = float(m[1].replace(",", "") or 0)
    return n * {"k": 1e3, "l": 1e5}.get(m[2].lower(), 1)


def best_by_numbers(results):
    import math
    rated = [r for r in results if _number(r["rating"]) or _count(r["count"])]
    if not rated:
        return None
    return max(rated, key=lambda r: (_number(r["rating"]) or 4.0) * math.log10(_count(r["count"]) + 10))


def describe(r):
    bits = [product_name(r["title"])]
    if r["price"]:
        bits.append(f"at {_spoken_price(r['price'])}")
    if _number(r["rating"]):
        bits.append(f"{_number(r['rating']):.1f} stars")
    if r["count"]:
        bits.append(f"from {r['count']} ratings")
    return ", ".join(bits)


def product_name(title):
    """'OnePlus Nord Buds 3r TWS Earbuds up to 54h, ...' -> 'OnePlus Nord Buds 3r TWS Earbuds'"""
    head = re.split(r"\s+(?:\||-|–|with|up ?to|for|featuring)\s+|[,(\[|]", title or "", maxsplit=1, flags=re.I)[0]
    return " ".join(head.split()[:8]).strip(" -,.")


def current_url():
    return _call(lambda: _page().url, timeout=10)


def topic():
    """What he was last looking at ("the same mouse"): the last search or the product opened."""
    return _state.get("topic")


def set_topic(text):
    _state["topic"] = text


def _stop():
    if _state["pw"]:
        _state["pw"].stop()   # Jarvis's Chrome stays open; only the driver goes (else node.js EPIPE dump on exit)
        _state["pw"] = _state["browser"] = None


@atexit.register
def _shutdown():
    if _thread and _thread.is_alive() and _state["pw"]:
        try:
            _call(_stop, timeout=3)
        except Exception:
            pass


def open_result(n=1, which=None):
    return _call(_open_result, n, which)


def list_results(k=5):
    return _call(_list_results, k)


def click(target, confirm=None):
    return _call(_click, target, confirm, timeout=120)   # may wait for his yes


def search_here(query):
    return _call(_search_here, query)


def scroll(direction):
    return _call(_scroll, direction)


def nav(action):
    return _call(_nav, action)


def page_text():
    return _call(_page_text)


def is_running():
    return _port_open()


def recently_used(seconds=180):
    """Jarvis drove its browser in the last few minutes: "go back" means that page even if another window is in front."""
    return time.time() - _state.get("used", 0) < seconds and _port_open()


def is_front():
    """Is Jarvis's Chrome the window in front? (its main process was started with our profile)"""
    if not _port_open():
        return False
    import psutil
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        cmd = " ".join(psutil.Process(pid.value).cmdline())
    except Exception:
        return False
    return "chrome-profile" in cmd and os.path.normcase(BROWSER_PROFILE) in os.path.normcase(cmd)


def front_site():
    """The site in Jarvis's browser if that window is in front, else None."""
    if not is_front():
        return None
    try:
        return _call(_front_site, timeout=10)
    except Exception:
        return None
