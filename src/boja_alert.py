import hashlib
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


# ============================================================
# CONFIGURACIÓN
# ============================================================

BOJA_FEED_URL = (
    "https://www.juntadeandalucia.es/boja/distribucion/s53.xml"
)

EMPLEO_PUBLICO_URL = (
    "https://portalempleopublico.juntadeandalucia.es/"
)

STATE_FILE = "state.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# ============================================================
# PALABRAS CLAVE
# ============================================================

ADMIN_KEYWORDS = [
    "auxiliar administrativo",
    "auxiliares administrativos",
    "auxiliar administrativa",
    "auxiliares administrativas",

    "cuerpo auxiliar administrativo",
    "cuerpo de auxiliares administrativos",

    "escala auxiliar administrativa",
    "escala de auxiliares administrativos",

    "c2.1000",
    "c2 1000",

    "grupo c2",
    "subgrupo c2",
]


CALL_KEYWORDS = [
    "convocatoria",
    "convocatorias",
    "bases",
    "proceso selectivo",
    "procesos selectivos",
    "turno libre",
    "acceso libre",
    "oposicion",
    "oposiciones",
    "plazas",
    "oferta de empleo publico",
    "oferta de empleo público",
]


IMPORTANT_KEYWORDS = [
    "correccion de errores",
    "corrección de errores",
    "modificacion",
    "modificación",

    "admitidos",
    "admitidas",
    "excluidos",
    "excluidas",

    "tribunal",

    "lista definitiva",
    "lista provisional",

    "plazo de presentacion",
    "plazo de presentación",

    "nombramiento",

    "adjudicacion",
    "adjudicación",
]


EXCLUDE = [
    "libre designacion",
    "libre designación",

    "concurso de meritos",
    "concurso de méritos",

    "comision de servicios",
    "comisión de servicios",

    "promocion interna",
    "promoción interna",
]


# ============================================================
# BOJA
# ============================================================

BOJA_NS = {
    "a": "http://www.w3.org/2005/Atom"
}


# ============================================================
# UTILIDADES
# ============================================================

def norm(text):
    """
    Normaliza texto:
    - minúsculas
    - elimina acentos
    - espacios consecutivos
    """

    text = (text or "").lower()

    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        c
        for c in text
        if unicodedata.category(c) != "Mn"
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def clean_html(text):
    """
    Elimina etiquetas HTML.
    """

    if not text:
        return ""

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# ============================================================
# ESTADO
# ============================================================

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


# ============================================================
# DESCARGA HTTP
# ============================================================

def download(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; boja-alertas/1.0)"
            )
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return response.read()


# ============================================================
# BOJA
# ============================================================

def fetch_boja():

    return download(
        BOJA_FEED_URL
    )


def parse_boja(xml):

    root = ET.fromstring(xml)

    items = []

    for entry in root.findall(
        "a:entry",
        BOJA_NS
    ):

        title = entry.findtext(
            "a:title",
            "",
            BOJA_NS
        )

        summary = entry.findtext(
            "a:summary",
            "",
            BOJA_NS
        )

        eid = entry.findtext(
            "a:id",
            "",
            BOJA_NS
        )

        updated = entry.findtext(
            "a:updated",
            "",
            BOJA_NS
        )

        link = ""

        for element in entry.findall(
            "a:link",
            BOJA_NS
        ):

            if element.attrib.get(
                "rel",
                "alternate"
            ) == "alternate":

                link = element.attrib.get(
                    "href",
                    ""
                )

                break

        if not eid:

            eid = hashlib.sha256(
                (
                    "BOJA|"
                    + title
                    + link
                    + updated
                ).encode()
            ).hexdigest()

        items.append(
            {
                "id": eid,
                "source": "BOJA",
                "title": clean_html(title),
                "summary": clean_html(summary),
                "updated": updated,
                "link": link,
            }
        )

    return items


# ============================================================
# PORTAL DE EMPLEO PÚBLICO
# ============================================================

class PortalParser(HTMLParser):
    """
    Parser HTML sencillo.

    Extrae enlaces cuyo texto o URL parezcan corresponder
    a anuncios del Portal de Empleo Público.
    """

    def __init__(self, base_url):

        super().__init__(
            convert_charrefs=True
        )

        self.base_url = base_url

        self.current_href = None
        self.current_text = []

        self.links = []

    def handle_starttag(
        self,
        tag,
        attrs
    ):

        if tag.lower() != "a":
            return

        attributes = dict(attrs)

        href = attributes.get(
            "href"
        )

        if href:

            self.current_href = (
                urllib.parse.urljoin(
                    self.base_url,
                    href
                )
            )

            self.current_text = []

    def handle_data(self, data):

        if self.current_href is not None:

            self.current_text.append(
                data
            )

    def handle_endtag(self, tag):

        if (
            tag.lower() == "a"
            and self.current_href is not None
        ):

            text = clean_html(
                " ".join(
                    self.current_text
                )
            )

            self.links.append(
                {
                    "url": self.current_href,
                    "text": text,
                }
            )

            self.current_href = None
            self.current_text = []


def fetch_empleo_publico():

    base_url = (
        "https://portalempleopublico.juntadeandalucia.es/"
        "sede/acceso-tramites/"
        "seguimiento-procesos-selectivos"
    )

    items = []

    seen_urls = set()

    # El portal muestra actualmente hasta 10 páginas.
    # Vamos a recorrerlas todas.
    for page in range(0, 11):

        if page == 0:

            url = (
                base_url
                + "?field_en_curso_value=1"
                + "&field_20_de_plazas_adicionales_value=All"
            )

        else:

            url = (
                base_url
                + "?field_en_curso_value=1"
                + "&field_20_de_plazas_adicionales_value=All"
                + f"&page={page}"
            )

        try:

            html = download(url)

        except Exception as error:

            print(
                f"ERROR leyendo página {page}:",
                error
            )

            continue


        parser = PortalParser(url)

        parser.feed(
            html.decode(
                "utf-8",
                errors="replace"
            )
        )


        print(
            f"Portal página {page}: "
            f"{len(parser.links)} enlaces"
        )


        for link in parser.links:

            link_url = link["url"]
            text = link["text"]

            if not text:
                continue

            if link_url in seen_urls:
                continue

            seen_urls.add(link_url)


            combined = norm(
                text
                + " "
                + link_url
            )


            # ------------------------------------------------
            # BUSQUEDA DE AUXILIAR ADMINISTRATIVO
            # ------------------------------------------------

            is_auxiliar = any(
                norm(keyword) in combined
                for keyword in [
                    "c2.1000",
                    "c2 1000",
                    "cuerpo auxiliar administrativo",
                    "cuerpo de auxiliares administrativos",
                    "auxiliar administrativo",
                    "auxiliar administrativa",
                ]
            )


            if not is_auxiliar:
                continue


            item_id = hashlib.sha256(
                (
                    "EMPLEO_PUBLICO|"
                    + link_url
                    + "|"
                    + text
                ).encode()
            ).hexdigest()


            items.append(
                {
                    "id": item_id,
                    "source": "EMPLEO_PUBLICO",
                    "title": text,
                    "summary": "",
                    "updated": "",
                    "link": link_url,
                }
            )


            print(
                "PORTAL MATCH:",
                text,
                "=>",
                link_url
            )


    print(
        "Portal Empleo Público: "
        f"{len(items)} procesos C2/Auxiliar encontrados"
    )


    return items

# ============================================================
# CLASIFICACIÓN
# ============================================================

def classify(item):

    text = norm(
        item["title"]
        + " "
        + item["summary"]
    )

    # Exclusiones
    if any(
        norm(keyword) in text
        for keyword in EXCLUDE
    ):

        return None

    admin_hits = [
        keyword
        for keyword in ADMIN_KEYWORDS
        if norm(keyword) in text
    ]

    if not admin_hits:

        return None

    call_hits = [
        keyword
        for keyword in CALL_KEYWORDS
        if norm(keyword) in text
    ]

    important_hits = [
        keyword
        for keyword in IMPORTANT_KEYWORDS
        if norm(keyword) in text
    ]

    score = (
        len(admin_hits) * 3
        + len(call_hits) * 2
        + len(important_hits) * 2
    )

    # Para el Portal de Empleo Público, una coincidencia
    # clara con C2.1000/Auxiliar Administrativo ya es
    # suficiente para considerar el anuncio.
    if item["source"] == "EMPLEO_PUBLICO":

        if not admin_hits:
            return None

        if score < 3:
            return None

    else:

        if score < 5:
            return None

    # Clasificación
    if (
        "convocatoria" in text
        or "proceso selectivo" in text
    ):

        category = "CONVOCATORIA"

    elif "bases" in text:

        category = "BASES"

    elif (
        "correccion de errores" in text
        or "modificacion" in text
    ):

        category = "CORRECCIÓN / MODIFICACIÓN"

    elif (
        "admitidos" in text
        or "admitidas" in text
        or "lista definitiva" in text
        or "lista provisional" in text
    ):

        category = "LISTAS"

    elif "tribunal" in text:

        category = "TRIBUNAL"

    elif (
        "plazo de presentacion" in text
    ):

        category = "PLAZO"

    elif "adjudicacion" in text:

        category = "ADJUDICACIÓN"

    else:

        category = "OTRA"

    return {
        "category": category,
        "score": score,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):

    data = urllib.parse.urlencode(
        {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
    ).encode()

    request = urllib.request.Request(
        (
            "https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        ),
        data=data,
        method="POST",
        headers={
            "Content-Type":
            "application/x-www-form-urlencoded"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return response.read()


# ============================================================
# MENSAJE
# ============================================================

def build_message(
    item,
    classification
):

    source = item["source"]

    if source == "BOJA":

        source_icon = "🔵"

    else:

        source_icon = "🟢"

    title = clean_html(
        item["title"]
    )

    summary = clean_html(
        item["summary"]
    )

    if len(summary) > 500:

        summary = (
            summary[:497]
            + "..."
        )

    message = (
        f"{source_icon} "
        f"<b>ALERTA EMPLEO PÚBLICO</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"📂 Tipo: "
        f"{classification['category']}\n"
        f"🎯 Relevancia: "
        f"{classification['score']}\n"
        f"📌 Fuente: {source}\n"
    )

    if item["updated"]:

        message += (
            f"📅 {item['updated']}\n"
        )

    if summary:

        message += (
            f"\n{summary}\n"
        )

    if item["link"]:

        message += (
            "\n🔗 "
            f'<a href="{item["link"]}">'
            "Ver publicación</a>"
        )

    return message


# ============================================================
# PROCESAMIENTO
# ============================================================

def process_items(
    items,
    state
):

    processed = set()

    for item in reversed(items):

        if item["id"] in state:

            continue

        classification = classify(
            item
        )

        if classification:

            message = build_message(
                item,
                classification
            )

            try:

                telegram(
                    message
                )

                # Solo marcamos como procesado
                # después de que Telegram confirme.
                processed.add(
                    item["id"]
                )

                print(
                    "Alerta enviada:",
                    item["source"],
                    item["title"]
                )

            except Exception as error:

                print(
                    "ERROR Telegram:",
                    item["id"],
                    error
                )

                # No se marca como procesado.
                # Se volverá a intentar en la
                # siguiente ejecución.

        else:

            # No es relevante.
            processed.add(
                item["id"]
            )

    return processed


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    all_items = []

    # --------------------------------------------------------
    # BOJA
    # --------------------------------------------------------

    try:

        boja_items = parse_boja(
            fetch_boja()
        )

        print(
            f"BOJA: {len(boja_items)} publicaciones"
        )

        all_items.extend(
            boja_items
        )

    except Exception as error:

        print(
            "ERROR consultando BOJA:",
            error
        )


    # --------------------------------------------------------
    # PORTAL DE EMPLEO PÚBLICO
    # --------------------------------------------------------

    try:

        empleo_items = (
            fetch_empleo_publico()
        )

        print(
            "Portal Empleo Público: "
            f"{len(empleo_items)} anuncios relevantes"
        )

        all_items.extend(
            empleo_items
        )

    except Exception as error:

        print(
            "ERROR consultando Portal "
            "de Empleo Público:",
            error
        )


    if not all_items:

        print(
            "No se han obtenido publicaciones."
        )

        return


    processed = process_items(
        all_items,
        state
    )


    state.update(
        processed
    )


    # Mantener solamente los últimos 1000 IDs.
    state = set(
        list(state)[-1000:]
    )


    save_state(
        state
    )


    print(
        "Proceso terminado correctamente."
    )


if __name__ == "__main__":

    main()
