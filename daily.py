#!/usr/bin/env python3
"""Daily Law Updates bot: collects headlines, posts to Telegram, builds an Instagram card.

Env vars (GitHub secrets / variables):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   - channel posting
  PREVIEW_CHAT_ID                        - your private chat id, for Instagram previews
  IG_ACCESS_TOKEN, IG_ACCOUNT_ID         - Instagram publishing
  IG_MODE = off | preview | live         - default preview
  PAGES_BASE                             - e.g. https://mjlawsocialmedia.github.io/law-updates-cards
  DRY_RUN = 1                            - print only, send nothing
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
CARDS = ROOT / "cards"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UA = {"User-Agent": "Mozilla/5.0 (law-updates-bot)"}

def gnews(q):
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1d")
            + "&hl=en-IN&gl=IN&ceid=IN:en")

CATEGORIES = [
    ("Court notices", "⚖️", [gnews('"Supreme Court" notice OR circular advocates India'),
                              gnews('"High Court" circular OR notice advocates Delhi OR Allahabad')]),
    ("Law vacancies", "💼", [gnews('law vacancy OR "legal officer" OR "judicial service" OR "district court" recruitment India'), gnews('advocate OR "law graduates" vacancy recruitment')]),
    ("Law internships", "🎓", [gnews('law internship India'), gnews('legal internship 2026 law students apply')]),
    ("Blog updates", "📝", ["https://advmriduljindal.com/blog/feed/"]),
]
MAX_ITEMS = 5

LAW = r"\b(law|legal|advocate|judicial|judge|court|tribunal|llb|llm|lawyer|counsel|paralegal|clerk|district courts?|high court|supreme court)\b"
FILTERS = {
    "Court notices": (r"\b(notice|circular|order|directs?|issues?|bench|roster|e-?filing|listing|vacation|rules?|bail|petition|plea|sc|hc)\b", r"\b(ipo|stake|acts? on|advises|ranking|exam pattern|syllabus)\b"),
    "Law vacancies": (r"(" + LAW + r").*(recruit|vacanc|hiring|post|apply|opening|job|exam|notification)|(recruit|vacanc|hiring|apply|opening|job|exam|notification).*(" + LAW + r")", r"\b(railway|rrb|bhel|nlc|upessc|ssc|bank|police constable|teacher|syllabus|admit card|result|answer key)\b"),
    "Law internships": (r"\binternships?\b", r"\b(smuggling|ipo|detention|dna)\b"),
    "Blog updates": (r".", r"^$"),
}

def relevant(name, title):
    inc, exc = FILTERS[name]
    t = title.lower()
    return bool(re.search(inc, t, re.I)) and not re.search(exc, t, re.I) and (name != "Law internships" or bool(re.search(LAW, t, re.I)))

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def parse(xml_bytes):
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        source = (src.text if src is not None else "") or ""
        pub = it.findtext("pubDate") or ""
        try:
            when = dt.datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except Exception:
            when = None
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        out.append({"title": title, "link": link, "source": source, "when": when})
    return out

def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"seen": []}

def collect(state):
    seen = set(state["seen"])
    now = dt.datetime.now(dt.timezone.utc)
    result = {}
    for name, _emoji, urls in CATEGORIES:
        items, titles = [], set()
        for u in urls:
            try:
                for e in parse(fetch(u)):
                    key = re.sub(r"\W+", " ", e["title"].lower()).strip()
                    if not e["title"] or key in titles or e["link"] in seen:
                        continue
                    if not relevant(name, e["title"]):
                        continue
                    if e["when"] and (now - e["when"]).total_seconds() > 36 * 3600:
                        continue
                    titles.add(key)
                    items.append(e)
            except Exception as exc:
                print(f"[warn] {name}: {u}: {exc}", file=sys.stderr)
        items.sort(key=lambda e: e["when"] or now, reverse=True)
        result[name] = items[:MAX_ITEMS]
    return result

def tg(method, token, **data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def telegram_posts(token, chat_id, result, dry):
    footer = "\n\n— Law Chambers of Mridul Jindal | advmriduljindal.com"
    for name, emoji, _ in CATEGORIES:
        items = result[name]
        if not items:
            continue
        lines = [f"{emoji} <b>{html.escape(name)}</b>"]
        for i, e in enumerate(items, 1):
            src = f" ({html.escape(e['source'])})" if e["source"] else ""
            lines.append(f"{i}. <a href=\"{html.escape(e['link'], quote=True)}\">{html.escape(e['title'])}</a>{src}")
        text = "\n".join(lines) + footer
        if dry:
            print(text, "\n")
        else:
            tg("sendMessage", token, chat_id=chat_id, text=text, parse_mode="HTML",
               disable_web_page_preview="true")
            time.sleep(2)

def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def make_card(result, date_str):
    from PIL import Image, ImageDraw, ImageFont
    W = H = 1080
    img = Image.new("RGB", (W, H), "#0f2747")
    d = ImageDraw.Draw(img)
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    f_title, f_sub = ImageFont.truetype(bold, 58), ImageFont.truetype(reg, 30)
    f_cat, f_item = ImageFont.truetype(bold, 34), ImageFont.truetype(reg, 29)
    f_foot = ImageFont.truetype(bold, 28)
    d.rectangle([0, 0, W, 8], fill="#c9a24a")
    d.text((60, 50), "LAW UPDATES", font=f_title, fill="#ffffff")
    d.text((60, 125), date_str + "  |  Daily legal digest", font=f_sub, fill="#c9a24a")
    y = 200
    for name, emoji, _ in CATEGORIES:
        items = result[name][:2]
        if not items or name == "Blog updates" and not items:
            continue
        if y > 880:
            break
        d.text((60, y), name.upper(), font=f_cat, fill="#c9a24a")
        y += 50
        for e in items:
            for line in wrap(d, "• " + e["title"], f_item, W - 120)[:2]:
                d.text((60, y), line, font=f_item, fill="#ffffff")
                y += 38
            y += 8
        y += 18
    d.rectangle([0, H - 110, W, H], fill="#0a1b33")
    d.text((60, H - 80), "Law Chambers of Mridul Jindal", font=f_foot, fill="#ffffff")
    d.text((60, H - 45), "advmriduljindal.com  |  Follow for daily updates", font=f_sub, fill="#c9a24a")
    CARDS.mkdir(exist_ok=True)
    name = f"card-{date_str.replace(' ', '-')}.jpg"
    img.save(CARDS / name, quality=92)
    return name

def ig_caption(result):
    lines = ["⚖️ Today's law updates", ""]
    for name, emoji, _ in CATEGORIES:
        for e in result[name][:2]:
            lines.append(f"{emoji} {e['title']}")
    lines += ["", "Full links on our Telegram channel: t.me/LawChambersofMridulJindal",
              "", "Law Chambers of Mridul Jindal | advmriduljindal.com",
              "#law #legalupdates #india #supremecourt #advocate #lawstudents"]
    return "\n".join(lines)[:2000]

def wait_for_url(url, tries=24):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(15)
    return False

def graph(path, token, **data):
    body = urllib.parse.urlencode({**data, "access_token": token}).encode()
    req = urllib.request.Request(f"https://graph.facebook.com/v25.0/{path}", data=body)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def send_preview(token, prev, card, caption):
    import uuid
    tg("sendMessage", token, chat_id=prev,
       text="Instagram preview (not posted). Set the repo variable IG_MODE=live to post automatically.\n\n" + caption)
    b = uuid.uuid4().hex
    data = (CARDS / card).read_bytes()
    payload = (f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{prev}\r\n"
               f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{card}\"\r\n"
               f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=payload,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=60).read()

def phase_collect():
    dry = os.environ.get("DRY_RUN") == "1"
    date_str = dt.datetime.now(IST).strftime("%d %b %Y")
    state = load_state()
    result = collect(state)
    total = sum(len(v) for v in result.values())
    print(f"collected {total} items")
    if total == 0:
        return
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    telegram_posts(token, chat, result, dry or not token)
    card = make_card(result, date_str)
    caption = ig_caption(result)
    (CARDS / "latest.json").write_text(json.dumps({"card": card, "caption": caption}))
    if not dry:
        state["seen"] = (state["seen"] + [e["link"] for v in result.values() for e in v])[-2000:]
        STATE.write_text(json.dumps(state))
    mode = os.environ.get("IG_MODE", "preview")
    prev = os.environ.get("PREVIEW_CHAT_ID")
    if not dry and mode == "preview" and token and prev:
        send_preview(token, prev, card, caption)

def phase_publish():
    if os.environ.get("IG_MODE", "preview") != "live":
        return
    latest = json.loads((CARDS / "latest.json").read_text())
    base = os.environ["PAGES_BASE"].rstrip("/")
    img_url = f"{base}/cards/{latest['card']}"
    if not wait_for_url(img_url):
        print("image not reachable on Pages; skipping Instagram", file=sys.stderr)
        return
    acct, tok = os.environ["IG_ACCOUNT_ID"], os.environ["IG_ACCESS_TOKEN"]
    c = graph(f"{acct}/media", tok, image_url=img_url, caption=latest["caption"])
    time.sleep(5)
    print("published", graph(f"{acct}/media_publish", tok, creation_id=c["id"]))

if __name__ == "__main__":
    {"collect": phase_collect, "publish": phase_publish}[sys.argv[1] if len(sys.argv) > 1 else "collect"]()
