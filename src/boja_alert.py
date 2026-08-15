import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
import urllib.request
import urllib.parse
from xml.etree import ElementTree as ET

FEED_URL = "https://www.juntadeandalucia.es/boja/distribucion/s53.xml"

STATE_FILE = "state.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ADMIN_KEYWORDS = [
    "auxiliar administrativo",
    "auxiliares administrativos",
    "auxiliar administrativa",
    "auxiliares administrativas",
    "cuerpo auxiliar administrativo",
    "cuerpo de auxiliares administrativos",
    "escala auxiliar administrativa",
    "escala de auxiliares administrativos",
    "grupo c2",
    "subgrupo c2",
]

CALL_KEYWORDS = [
    "convocatoria",
    "convocatorias",
    "bases",
    "proceso selectivo",
    "turno libre",
    "oposicion",
    "oposiciones",
    "plazas",
    "oferta de empleo publico",
]

IMPORTANT_KEYWORDS = [
    "correccion de errores",
    "modificacion",
    "admitidos",
    "admitidas",
    "excluidos",
    "excluidas",
    "tribunal",
    "lista definitiva",
    "lista provisional",
    "plazo de presentacion",
]

EXCLUDE = [
    "libre designacion",
    "concurso de meritos",
    "comision de servicios",
    "promocion interna",
]

NS = {
    "a": "http://www.w3.org/2005/Atom"
}

def norm(text):
    """
    Normaliza texto:
    - minúsculas
    - elimina acentos
    - elimina espacios repetidos
    """

    text = (text or "").lower()

    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        c for c in text
        if unicodedata.category(c) != "Mn"
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()

def load_state():
    try:
        return set(
            json.loads(
                Path(STATE_FILE)
                .read_text(
                    encoding="utf-8"
                )
            )
        )

    except Exception:
        return set()

def save_state(ids):
    Path(STATE_FILE).write_text(
        json.dumps(
            sorted(ids),
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

def fetch():
    req = urllib.request.Request(
        FEED_URL,
        headers={
            "User-Agent": "boja-alertas/1.0"
        }
    )
    with urllib.request.urlopen(
        req,
        timeout=30
    ) as response:
        return response.read()

def parse(xml):
    root = ET.fromstring(xml)
    items = []
    for entry in root.findall(
        "a:entry",
        NS
    ):
        title = entry.findtext(
            "a:title",
            "",
            NS
        )
        summary = entry.findtext(
            "a:summary",
            "",
            NS
        )
        eid = entry.findtext(
            "a:id",
            "",
            NS
        )
        updated = entry.findtext(
            "a:updated",
            "",
            NS
        )
        link = ""
        for l in entry.findall(
            "a:link",
            NS
        ):
            if l.attrib.get(
                "rel",
                "alternate"
            ) == "alternate":

                link = l.attrib.get(
                    "href",
                    ""
                )
                break

        if not eid:
            eid = hashlib.sha256(
                (
                    title
                    +
                    link
                    +
                    updated
                ).encode()
            ).hexdigest()

        items.append(
            {
                "id": eid,
                "title": title,
                "summary": summary,
                "updated": updated,
                "link": link
            }
        )

    return items

def classify(item):
    text = norm(
        item["title"]
        +
        " "
        +
        item["summary"]
    )

    if any(
        norm(x) in text
        for x in EXCLUDE
    ):
        return None

    admin_hits = [
        x for x in ADMIN_KEYWORDS
        if norm(x) in text
    ]

    if not admin_hits:
        return None

    call_hits = [
        x for x in CALL_KEYWORDS
        if norm(x) in text
    ]

    important_hits = [
        x for x in IMPORTANT_KEYWORDS
        if norm(x) in text
    ]

    score = (
        len(admin_hits) * 3
        +
        len(call_hits) * 2
        +
        len(important_hits) * 2
    )

    if score < 5:
        return None

    if "convocatoria" in text or "proceso selectivo" in text:
        category = "CONVOCATORIA"

    elif "bases" in text:
        category = "BASES"

    elif (
        "correccion de errores" in text
        or "modificacion" in text
    ):

        category = "CORRECCION / MODIFICACION"

    elif (
        "admitidos" in text
        or "admitidas" in text
        or "lista definitiva" in text
        or "lista provisional" in text
    ):
        category = "LISTAS"

    elif "tribunal" in text:
        category = "TRIBUNAL"

    elif "plazo de presentacion" in text:
        category = "PLAZO"

    else:
        category = "OTRA"

    return {
        "category": category,
        "score": score
    }

def telegram(message):
    data = urllib.parse.urlencode(
        {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
    ).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data,
        method="POST",
        headers={
            "Content-Type":
            "application/x-www-form-urlencoded"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:
        return response.read()

def clean_html(text):
    return re.sub(
        "<[^>]+>",
        " ",
        text or ""
    )

def main():
    state = load_state()
    items = parse(
        fetch()
    )

    processed = set()

    for item in reversed(items):
        if item["id"] in state:
            continue

        result = classify(item)

        if result:
            title = clean_html(
                item["title"]
            )

            summary = clean_html(
                item["summary"]
            )

            if len(summary) > 500:
                summary = (
                    summary[:497]
                    +
                    "..."
                )

            message = (
                f"🔔 <b>ALERTA BOJA "
                f"- {result['category']}</b>\n\n"
                f"<b>{title}</b>\n\n"
                f"🎯 Relevancia: "
                f"{result['score']}\n"
            )

            if item["updated"]:
                message += (
                    f"📅 {item['updated']}\n"
                )

            if summary:
                message += (
                    f"\n{summary}"
                )

            if item["link"]:
                message += (
                    "\n\n🔗 "
                    f"<a href=\"{item['link']}\">"
                    "Ver publicación BOJA</a>"
                )

            try:
                telegram(message)
                processed.add(
                    item["id"]
                )

            except Exception as e:
                print(
                    "Error Telegram:",
                    e
                )

        else:
            # publicación no interesante
            # la marcamos como revisada
            processed.add(
                item["id"]
            )

    state.update(
        processed
    )

    save_state(
        set(
            list(state)[-1000:]
        )
    )

if __name__ == "__main__":

    main()
