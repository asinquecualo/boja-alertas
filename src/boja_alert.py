"""Alertas BOJA y Portal de Empleo Público para Auxiliar Administrativo C2.1000.

Pensado para ejecutarse periódicamente desde GitHub Actions.  Requiere las
variables TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID cuando haya que enviar avisos.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from datetime import date, timedelta
from urllib.error import HTTPError


BOJA_FEED_URL = "https://www.juntadeandalucia.es/boja/distribucion/s53.xml"
PORTAL_BASE_URL = "https://portalempleopublico.juntadeandalucia.es"
PORTAL_VIEW_PATH = "/sede/acceso-tramites/seguimiento-procesos-selectivos"
PORTAL_AJAX_URL = f"{PORTAL_BASE_URL}/views/ajax"
BOP_SEVILLA_INDEX_URL = (
    "https://bopsevilla.dipusevilla.es/publica/consulta-de-bops/index.html"
)
BOE_SUMMARY_API = "https://boe.es/datosabiertos/api/boe/sumario"
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))

# Valores del formulario Drupal confirmados para C2.1000.
PORTAL_FILTERS = {
    "field_tipo_de_acceso_target_id": "56",       # Acceso libre
    "field_tipo_personal_target_id": "464",       # Personal funcionario
    "field_cuerpo_grupo_target_id": "2398",       # C2.1 Cuerpo Auxiliar Administrativo
    "field_especialidad_opcion_catego_target_id": "2405",  # C2.1000
    "field_en_curso_value": "1",                  # En curso
}

BOJA_NS = {"a": "http://www.w3.org/2005/Atom"}
ADMIN_KEYWORDS = (
    "auxiliar administrativo", "auxiliares administrativos",
    "auxiliar administrativa", "auxiliares administrativas",
    "cuerpo auxiliar administrativo", "cuerpo de auxiliares administrativos",
    "escala auxiliar administrativa", "c2.1000", "c2 1000",
)
CALL_KEYWORDS = (
    "convocatoria", "convoca", "bases", "proceso selectivo", "oposicion", "plazas",
    "oferta de empleo publico", "turno libre", "acceso libre",
)
IMPORTANT_KEYWORDS = (
    "correccion de errores", "modificacion", "admitidos", "admitidas",
    "excluidos", "excluidas", "tribunal", "lista definitiva",
    "lista provisional", "plazo de presentacion", "nombramiento",
)
EXCLUDE_KEYWORDS = (
    "libre designacion", "concurso de meritos", "comision de servicios",
    "promocion interna",
)


def norm(value: str) -> str:
    """Normaliza texto para comparaciones que ignoran mayúsculas y acentos."""
    value = unicodedata.normalize("NFD", (value or "").lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def identifier(*parts: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def request(
    url: str,
    data: dict[str, str] | None = None,
    accept: str = "application/json, text/javascript, */*; q=0.01",
) -> bytes:
    """Hace una petición HTTP con el encabezado de un navegador corriente."""
    encoded = urllib.parse.urlencode(data).encode("utf-8") if data else None
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; boja-alertas/2.0)",
            "Accept": accept,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{PORTAL_BASE_URL}{PORTAL_VIEW_PATH}",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        return response.read()


def load_state() -> set[str]:
    """Lee tanto el formato histórico (lista) como el formato con 'seen'."""
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, list):
            return {str(item) for item in saved}
        if isinstance(saved, dict) and isinstance(saved.get("seen"), list):
            return {str(item) for item in saved["seen"]}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def save_state(seen: set[str]) -> None:
    # Una lista mantiene compatibilidad con el state.json creado por versiones previas.
    STATE_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ResultsTableParser(HTMLParser):
    """Extrae las filas y enlaces de las tablas del fragmento HTML de Drupal."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._table: dict[str, Any] | None = None
        self._in_caption = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_tag: str | None = None
        self._link: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = {"caption": "", "headers": [], "rows": []}
        elif self._table is None:
            return
        elif tag == "caption":
            self._in_caption = True
            self._cell = []
        elif tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
            self._cell_tag = tag
        elif tag == "a" and self._cell is not None:
            self._link = dict(attrs).get("href")
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._link is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link is not None:
            link_text = clean_text(" ".join(self._link_text))
            if link_text and self._cell is not None:
                self._cell.append(f" [URL:{self._link}]")
            self._link = None
            self._link_text = []
        elif tag == "caption" and self._table is not None:
            self._table["caption"] = clean_text(" ".join(self._cell or []))
            self._cell = None
            self._in_caption = False
        elif tag in {"th", "td"} and self._cell is not None and self._row is not None:
            value = clean_text(" ".join(self._cell))
            if self._cell_tag == "th":
                self._table["headers"].append(value)
            else:
                self._row.append(value)
            self._cell = None
            self._cell_tag = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            if self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def drupal_results_html(payload: bytes) -> str:
    """Obtiene el HTML del comando Drupal que sustituye la vista de resultados."""
    try:
        commands = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("El portal no devolvió JSON Drupal válido") from error

    if not isinstance(commands, list):
        raise RuntimeError("Respuesta Drupal inesperada: no contiene una lista de comandos")
    for command in commands:
        if (
            isinstance(command, dict)
            and command.get("command") == "insert"
            and command.get("method") == "replaceWith"
            and isinstance(command.get("data"), str)
        ):
            return command["data"]
    raise RuntimeError("La respuesta Drupal no contiene el bloque de resultados")


def extract_processes(results_html: str) -> list[dict[str, str]]:
    if "No se han encontrado resultados" in results_html:
        return []

    parser = ResultsTableParser()
    parser.feed(results_html)
    processes: list[dict[str, str]] = []
    for table in parser.tables:
        headers = table["headers"]
        for row in table["rows"]:
            values = dict(zip(headers, row))
            title = values.get("Proceso selectivo", "")
            url_match = re.search(r"\s*\[URL:([^\]]+)\]", title)
            link = urllib.parse.urljoin(PORTAL_BASE_URL, url_match.group(1)) if url_match else ""
            title = re.sub(r"\s*\[URL:[^\]]+\]", "", title).strip()
            if not title:
                continue
            summary = " · ".join(
                f"{header}: {value}" for header, value in values.items()
                if header != "Proceso selectivo" and value
            )
            # El hash contiene los datos que pueden cambiar (fase/plazas/OEP). Así
            # una modificación visible del proceso genera una alerta una sola vez.
            change_key = norm("|".join((table["caption"], title, summary, link)))
            processes.append({
                "id": identifier("EMPLEO_PUBLICO", change_key),
                "source": "EMPLEO_PUBLICO",
                "title": f"{table['caption']} — {title}" if table["caption"] else title,
                "summary": summary,
                "updated": "",
                "link": link or f"{PORTAL_BASE_URL}{PORTAL_VIEW_PATH}",
                "classification": {"category": "ACTUALIZACIÓN DEL PROCESO", "score": 0},
            })
    return processes


class BopSevillaParser(HTMLParser):
    """Extrae los anuncios de la página de un boletín del BOP de Sevilla."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.items: list[dict[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._next_link = ""
        self._in_title = False
        self._title_text: list[str] = []
        self._current: dict[str, Any] | None = None

    def _finish(self) -> None:
        if not self._current:
            return
        title = clean_text(self._current["title"])
        link = self._current["link"]
        summary = clean_text(" ".join(self._current["summary"]))
        if title and link:
            cve_match = re.search(r"BOP-SE-\d{4}-\d+", summary, re.IGNORECASE)
            item_id = cve_match.group(0).upper() if cve_match else identifier(
                "BOP_SEVILLA", title, link
            )
            self.items.append({
                "id": item_id,
                "source": "BOP_SEVILLA",
                "title": title,
                "summary": summary,
                "updated": "",
                "link": link,
            })
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []
        elif tag == "h3":
            self._in_title = True
            self._title_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._anchor_text.append(data)
        if self._in_title:
            self._title_text.append(data)
        elif self._current is not None:
            self._current["summary"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            if clean_text(" ".join(self._anchor_text)).lower() == "ir al detalle":
                # El siguiente enlace de detalle marca el fin del anuncio anterior.
                self._finish()
                self._next_link = urllib.parse.urljoin(self.base_url, self._href)
            self._href = None
            self._anchor_text = []
        elif tag == "h3" and self._in_title:
            title = clean_text(" ".join(self._title_text))
            if title and self._next_link:
                self._finish()
                self._current = {"title": title, "link": self._next_link, "summary": []}
                self._next_link = ""
            self._in_title = False
            self._title_text = []

    def close(self) -> None:
        super().close()
        self._finish()


def fetch_empleo_publico() -> list[dict[str, Any]]:
    # En esta vista los filtros expuestos deben viajar en la URL. Si se envían
    # únicamente en el cuerpo POST, Drupal responde correctamente pero ignora
    # sus valores y muestra la primera categoría (A1.1).
    endpoint = (
        f"{PORTAL_AJAX_URL}?_wrapper_format=drupal_ajax&"
        f"{urllib.parse.urlencode(PORTAL_FILTERS)}"
    )
    data = {
        "view_name": "procesos_selectivos_buscador_simple",
        "view_display_id": "procesos_selectivos",
        "view_args": "",
        "view_path": PORTAL_VIEW_PATH.lstrip("/"),
        "view_base_path": PORTAL_VIEW_PATH.lstrip("/"),
        # Drupal acepta este id estable de la vista; no es un token de sesión.
        "view_dom_id": "c60e072c4dbd12df0635e4b55b1fff378ce7a362fd06e3b3ecd7a11c05b88e02",
        "pager_element": "0",
        "_drupal_ajax": "1",
    }
    result_html = drupal_results_html(request(endpoint, data))
    items = extract_processes(result_html)
    print(f"Portal C2.1000: {len(items)} procesos en curso")
    return items


def parse_boja(xml_data: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_data)
    items: list[dict[str, str]] = []
    for entry in root.findall("a:entry", BOJA_NS):
        title = clean_text(entry.findtext("a:title", "", BOJA_NS))
        summary = clean_text(entry.findtext("a:summary", "", BOJA_NS))
        updated = entry.findtext("a:updated", "", BOJA_NS)
        link = ""
        for candidate in entry.findall("a:link", BOJA_NS):
            if candidate.attrib.get("rel", "alternate") == "alternate":
                link = candidate.attrib.get("href", "")
                break
        entry_id = entry.findtext("a:id", "", BOJA_NS) or identifier("BOJA", title, link, updated)
        items.append({"id": entry_id, "source": "BOJA", "title": title,
                      "summary": summary, "updated": updated, "link": link})
    return items


def fetch_boja() -> list[dict[str, str]]:
    # No usamos los encabezados AJAX para el RSS, pero el servidor los tolera.
    items = parse_boja(request(BOJA_FEED_URL))
    print(f"BOJA: {len(items)} publicaciones")
    return items


def fetch_bop_sevilla() -> list[dict[str, str]]:
    """Descarga el BOP más reciente publicado y devuelve sus anuncios."""
    landing = request(BOP_SEVILLA_INDEX_URL).decode("utf-8", errors="replace")
    match = re.search(
        r'href=["\']([^"\']*/buscador/BOP-\d{2}-\d{2}-\d{4}/?)["\']',
        landing,
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError("No se ha podido localizar el último boletín del BOP de Sevilla")
    bulletin_url = urllib.parse.urljoin(BOP_SEVILLA_INDEX_URL, html.unescape(match.group(1)))
    parser = BopSevillaParser(bulletin_url)
    parser.feed(request(bulletin_url).decode("utf-8", errors="replace"))
    parser.close()
    print(f"BOP Sevilla: {len(parser.items)} anuncios en el último boletín")
    return parser.items


def parse_boe_summary(xml_data: bytes, published: str) -> list[dict[str, str]]:
    """Convierte la respuesta XML de la API oficial de sumarios del BOE."""
    root = ET.fromstring(xml_data)
    items: list[dict[str, str]] = []
    for element in root.iter("item"):
        item_id = element.findtext("identificador", "")
        title = clean_text(element.findtext("titulo", ""))
        link = element.findtext("url_html", "") or element.findtext("url_pdf", "")
        if item_id and title and link:
            items.append({
                "id": item_id,
                "source": "BOE",
                "title": title,
                "summary": "",
                "updated": published,
                "link": link,
            })
    return items


def fetch_boe() -> list[dict[str, str]]:
    """Consulta hasta siete días para cubrir festivos o una ejecución fallida."""
    items: list[dict[str, str]] = []
    for offset in range(7):
        day = date.today() - timedelta(days=offset)
        try:
            summary = request(
                f"{BOE_SUMMARY_API}/{day:%Y%m%d}",
                accept="application/xml",
            )
        except HTTPError as error:
            if error.code == 404:  # Fin de semana o día sin publicación.
                continue
            raise
        items.extend(parse_boe_summary(summary, day.isoformat()))
    print(f"BOE: {len(items)} publicaciones revisadas (últimos 7 días)")
    return items


def classify_boja(item: dict[str, str]) -> dict[str, Any] | None:
    text = norm(f"{item['title']} {item['summary']}")
    if any(word in text for word in EXCLUDE_KEYWORDS):
        return None
    admin_hits = [word for word in ADMIN_KEYWORDS if word in text]
    if not admin_hits:
        return None
    call_hits = [word for word in CALL_KEYWORDS if word in text]
    important_hits = [word for word in IMPORTANT_KEYWORDS if word in text]
    score = 3 * len(admin_hits) + 2 * len(call_hits) + 2 * len(important_hits)
    if score < 5:
        return None
    if any(word in text for word in ("convocatoria", "proceso selectivo")):
        category = "CONVOCATORIA"
    elif "bases" in text:
        category = "BASES"
    elif any(word in text for word in ("admitidos", "admitidas", "excluidos", "excluidas")):
        category = "LISTAS DE ADMITIDOS"
    elif "tribunal" in text:
        category = "TRIBUNAL"
    else:
        category = "PUBLICACIÓN RELEVANTE"
    return {"category": category, "score": score}


def telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    response = request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"},
    )
    try:
        if not json.loads(response).get("ok"):
            raise RuntimeError("Telegram rechazó el mensaje")
    except json.JSONDecodeError as error:
        raise RuntimeError("Respuesta no válida de Telegram") from error


def notification(item: dict[str, Any]) -> str:
    labels = {
        "BOJA": "BOJA",
        "EMPLEO_PUBLICO": "PORTAL EMPLEO PÚBLICO",
        "BOP_SEVILLA": "BOP SEVILLA",
        "BOE": "BOE",
    }
    label = labels[item["source"]]
    category = item["classification"]["category"]
    message = f"🔔 <b>{label} — {html.escape(category)}</b>\n\n<b>{html.escape(item['title'])}</b>"
    if item.get("updated"):
        message += f"\n📅 {html.escape(item['updated'])}"
    if item.get("summary"):
        message += f"\n\n{html.escape(item['summary'][:1200])}"
    if item.get("link"):
        message += f'\n\n🔗 <a href="{html.escape(item["link"], quote=True)}">Ver publicación</a>'
    return message


def main() -> None:
    seen = load_state()
    boja_items = fetch_boja()
    portal_items = fetch_empleo_publico()
    bop_sevilla_items = fetch_bop_sevilla()
    boe_items = fetch_boe()
    sent_or_irrelevant: set[str] = set()

    for item in boja_items + portal_items + bop_sevilla_items + boe_items:
        if item["id"] in seen:
            continue
        if item["source"] in {"BOJA", "BOP_SEVILLA", "BOE"}:
            classification = classify_boja(item)
            if not classification:
                # Los elementos BOJA irrelevantes se recuerdan para no reanalizarlos.
                sent_or_irrelevant.add(item["id"])
                continue
            item["classification"] = classification
        telegram(notification(item))
        sent_or_irrelevant.add(item["id"])
        print(f"Alerta enviada: {item['source']} — {item['title']}")

    seen.update(sent_or_irrelevant)
    save_state(seen)
    print("Proceso terminado correctamente.")


if __name__ == "__main__":
    main()
