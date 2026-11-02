"""Erinnerung am Erscheinungstag – mit Direktlinks zu Shops, die Restock-Radar nicht lesen darf.

Die Termine stammen aus Shops, die lesbar sind. Für gesperrte Shops (Bot-Schutz) gibt es keinen
automatischen Abruf, nur einen Link zur passenden Suche: Den öffnest du selbst im Browser.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

from .db import Database
from .fetcher import host_key
from .notify import Message
from .search import alternatives, normalize

# Wörter, die in fast jedem Produktnamen stehen und für die Suche nichts taugen
STOPWORDS = {
    "pokemon", "tcg", "sammelkartenspiel", "display", "displays", "booster", "boosters", "boosterpack", "bundle",
    "top", "trainer", "elite", "box", "etb", "ttb", "deutsch", "englisch", "german", "english", "de", "en", "dt",
    "vorverkauf", "vorbestellung", "preorder", "sealed", "ovp", "neu", "mega", "entwicklung", "karmesin", "purpur",
    "scarlet", "violet", "schwert", "schild", "sonne", "mond", "set", "mini", "tin", "tins", "kollektion",
    "collection", "premium", "enhanced", "edition", "the", "and", "und", "der", "die", "das", "36", "18", "24",
}
MAX_ITEMS = 6
MAX_LINKS = 3        # ntfy erlaubt drei Knöpfe


def short_query(title: str) -> str:
    """„Mega-Entwicklung Delta Herrschaft Display (36 Booster) (deutsch)“ → „delta herrschaft“."""
    words = [w for w in normalize(title).split() if w not in STOPWORDS and not w.isdigit()]
    return " ".join(words[:3]) or normalize(title).split()[0]


def link_shops(db: Database) -> list[dict]:
    """Shops, die nur per Link erreichbar sind: als Link-Shop angelegt oder vom Bot-Schutz gesperrt."""
    return [s for s in db.shops() if s["enabled"] and (s["mode"] == "link" or s["status"] == "blocked")]


def shop_link(shop: dict, query: str) -> str:
    template = shop.get("search_url") or ""
    if "{q}" in template:
        return template.replace("{q}", quote_plus(query))
    host = host_key(shop["base"])
    return "https://www.google.com/search?q=" + quote_plus(f"site:{host} {query}")


def template_from_example(example: str, word: str) -> str | None:
    """Aus einem Such-Link wie „…/search/?qs=pikachu“ eine Vorlage „…/search/?qs={q}“ machen."""
    parts = urlsplit(example.strip())
    word = word.lower()
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(word in v.lower() for _, v in query):
        rebuilt = "&".join(f"{quote(k)}={'{q}' if word in v.lower() else quote_plus(v)}" for k, v in query)
        return parts._replace(query=rebuilt, fragment="").geturl()
    if word in parts.path.lower():
        start = parts.path.lower().index(word)
        path = parts.path[:start] + "{q}" + parts.path[start + len(word):]
        return parts._replace(path=path, fragment="").geturl()
    return None


def releases_on(db: Database, day: date) -> list[dict]:
    """Alles, was laut Shop an diesem Tag erscheint – aus Suchtreffern und überwachten Produkten."""
    iso = day.isoformat()
    items: dict[str, dict] = {}
    searches = {s["id"]: s for s in db.searches() if s["enabled"]}
    for search in searches.values():
        for hit in db.hits(search["id"]):
            if hit["release_date"] == iso:
                alts = alternatives(search["query"] or "")
                _add(items, hit["title"], hit["url"], hit["shop_name"] or host_key(hit["url"]), hit["release"],
                     alts[0] if alts else short_query(hit["title"]))
    for watch in db.watches():
        if watch["enabled"] and watch["release_date"] == iso:
            _add(items, watch["name"] or watch["url"], watch["url"], host_key(watch["url"]), watch["release"],
                 short_query(watch["name"] or ""))
    return list(items.values())


def _add(items: dict, title: str, url: str, shop: str, release: str, query: str) -> None:
    key = normalize(title)
    if key not in items:
        items[key] = {"title": title, "url": url, "shop": shop, "release": release, "query": query}


def links_for(items: list[dict], shops: list[dict]) -> list[tuple[str, str]]:
    """Ein Link pro gesperrtem Shop und Suchbegriff – zuerst die verschiedenen Shops, dann weitere Begriffe."""
    queries = list(dict.fromkeys(item["query"] for item in items if item["query"]))
    links = []
    for query in queries:
        for shop in shops:
            label = shop["name"] if len(queries) == 1 else f"{shop['name']}: {query}"
            links.append((label, shop_link(shop, query)))
    return links


def reminder_message(items: list[dict], shops: list[dict], *, eve: bool = False) -> Message:
    when = "Morgen" if eve else "Heute"
    if len(items) == 1:
        title = f"📅 {when} erscheint: {items[0]['title']}"
    else:
        title = f"📅 {when} erscheinen {len(items)} Produkte"
    lines = [f"• {i['title']} – {i['shop']}" for i in items[:MAX_ITEMS]]
    if len(items) > MAX_ITEMS:
        lines.append(f"… und {len(items) - MAX_ITEMS} weitere")
    links = links_for(items, shops)
    if links:
        lines.append("Gesperrte Shops selbst prüfen: " + ", ".join(dict.fromkeys(s["name"] for s in shops)))
    return Message(title, "\n".join(lines), items[0]["url"] if items else None, loud=not eve, kind="info",
                   links=links)


def calendar(db: Database, today: date, limit: int = 12) -> list[dict]:
    """Kommende Erscheinungstermine für die Oberfläche, nach Datum gruppiert."""
    dates = sorted({row["release_date"] for row in _dated_rows(db) if row["release_date"] >= today.isoformat()})
    shops = link_shops(db)
    days, count = [], 0
    for iso in dates:
        items = releases_on(db, date.fromisoformat(iso))
        if not items:
            continue
        days.append({"date": iso, "label": items[0]["release"], "items": items,
                     "links": [{"label": label, "url": url} for label, url in links_for(items, shops)]})
        count += len(items)
        if count >= limit:
            break
    return days


def _dated_rows(db: Database) -> list[dict]:
    rows = [w for w in db.watches() if w["enabled"] and w["release_date"]]
    for search in db.searches():
        if search["enabled"]:
            rows += [h for h in db.hits(search["id"]) if h["release_date"]]
    return rows
