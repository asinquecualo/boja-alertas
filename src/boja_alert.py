import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

FEED_URL = "https://www.juntadeandalucia.es/boja/distribucion/s53.xml"
STATE_FILE = "state.json"
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Broad on purpose: avoid missing differently-worded C2/admin calls.
INCLUDE = [
    "auxiliar administrativo", "auxiliares administrativos",
    "auxiliar administrativa", "auxiliares administrativas",
    "escala auxiliar administrativa", "escala de auxiliares administrativos",
    "cuerpo de auxiliares administrativos", "cuerpo auxiliar administrativo",
    "administrativo c2", "administrativos c2", "subgrupo c2",
    "administración general c2", "administracion general c2",
]
EXCLUDE = [
    "libre designación", "libre designacion", "concurso de méritos",
    "concurso de meritos", "comisión de servicios", "comision de servicios",
    "promoción interna", "promocion interna",
]
NS = {"a": "http://www.w3.org/2005/Atom"}

def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def load_state():
    try:
        return set(json.loads(Path(STATE_FILE).read_text(encoding="utf-8")))
    except Exception:
        return set()

def save_state(ids):
    Path(STATE_FILE).write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")

def fetch():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "boja-alertas/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def parse(xml):
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", NS):
        title = e.findtext("a:title", "", NS)
        summary = e.findtext("a:summary", "", NS)
        eid = e.findtext("a:id", "", NS)
        updated = e.findtext("a:updated", "", NS)
        link = ""
        for l in e.findall("a:link", NS):
            if l.attrib.get("rel", "alternate") == "alternate":
                link = l.attrib.get("href", "")
                break
        if not eid:
            eid = hashlib.sha256((title + link + updated).encode()).hexdigest()
        out.append({"id": eid, "title": title, "summary": summary, "updated": updated, "link": link})
    return out

def relevant(item):
    text = norm(item["title"] + " " + item["summary"])
    return any(x in text for x in INCLUDE) and not any(x in text for x in EXCLUDE)

def telegram(text):
    import urllib.parse
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def clean_html(s):
    return re.sub(r"<[^>]+>", " ", s or "")

def main():
    state = load_state()
    items = parse(fetch())
    # Keep a bounded state file.
    all_ids = list(state)
    new_relevant = []
    for item in items:
        if item["id"] in state:
            continue
        if relevant(item):
            new_relevant.append(item)

    # Mark every currently seen feed entry as seen, so old entries don't
    # generate a flood of alerts on first run.
    state.update(i["id"] for i in items)
    save_state(set(list(state)[-1000:]))

    for item in reversed(new_relevant):
        title = clean_html(item["title"])
        summary = clean_html(item["summary"])
        if len(summary) > 500:
            summary = summary[:497] + "..."
        msg = f"🔔 <b>Nueva publicación BOJA potencialmente relevante</b>\n\n<b>{title}</b>"
        if summary:
            msg += f"\n\n{summary}"
        if item["link"]:
            msg += f'\n\n<a href="{item["link"]}">Ver publicación en BOJA</a>'
        telegram(msg)

if __name__ == "__main__":
    main()
