"""Suchbegriff-Wächter: fragt die Suche hinterlegter Shops ab und filtert passende Produkte.

Je Shopsystem der schonendste Weg:
  Shopify      → Predictive-Search-JSON (/search/suggest.json)
  WooCommerce  → Store-API-JSON (/wp-json/wc/store/v1/products)
  alles andere → normale Suchseite, Trefferkarten auslesen (schema.org oder typische Klassen)
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .detect import (AVAILABLE, BLOCKED, QUEUE, SOLDOUT, UNKNOWN, _platform, _to_float, normalize_url,
                     schema_status, text_status)
from .fetcher import Fetcher, Page
from .release import find_release

SEARCH_PARAMS = ("q", "s", "qs", "search", "query", "keyword", "keywords", "searchparam", "ssearch",
                 "suchbegriff", "term", "sword")

PRODUCT_TYPES = {
    "egal": {"label": "Alle Produkte", "query": "", "any": [], "not": []},
    "display": {"label": "Display / Booster-Box", "query": "display",
                "any": ["display", "booster box", "boosterbox"],
                "not": ["leer", "empty", "acryl", "acrylic", "huelle", "case", "staender", "aufbewahrung", "protector"]},
    "etb": {"label": "Top-Trainer-Box / ETB", "query": "trainer box",
            "any": ["top trainer box", "toptrainerbox", "elite trainer box", "etb", "ttb"],
            "not": ["leer", "empty", "huelle", "case", "protector"]},
    "bundle": {"label": "Booster-Bundle", "query": "bundle", "any": ["booster bundle", "boosterbundle"], "not": []},
    "booster": {"label": "Einzelne Booster", "query": "booster", "any": ["booster", "boosterpack"],
                "not": ["display", "box", "bundle", "blister", "tin", "kollektion", "collection"]},
    "blister": {"label": "Blister", "query": "blister", "any": ["blister"], "not": []},
    "tin": {"label": "Tin", "query": "tin", "any": ["tin", "tins"], "not": []},
    "kollektion": {"label": "Kollektion / Box", "query": "kollektion",
                   "any": ["kollektion", "collection", "premium"], "not": []},
}
# Zubehör, das gern mit dem Produktnamen gelistet wird – bei einem gewählten Produkttyp nie ein Treffer
# („Sleeves“ fehlt bewusst: viele Top-Trainer-Boxen werben im Titel mit den enthaltenen Hüllen.)
ACCESSORIES = ["acryl", "acrylic", "magnetisch", "magnetic", "protector", "huelle", "huellen",
               "schutzbox", "staender", "aufbewahrung", "storage", "binder", "ordner", "playmat", "spielmatte",
               "toploader", "deckbox", "deck box", "leer", "empty"]
# Ohne Suchbegriff („alle Pokémon-Produkte dieses Typs“) fliegen andere Sammelkartenspiele raus.
OTHER_TCGS = ["yugioh", "yu gi oh", "magic the gathering", "mtg", "one piece", "lorcana", "digimon", "dragon ball",
              "flesh and blood", "star wars unlimited", "weiss schwarz", "union arena", "riftbound", "gundam",
              "metazoo", "sorcery"]
BROAD_QUERY = "Pokémon"

AUTO_OFF, AUTO_NEW, AUTO_UNAVAILABLE = 0, 1, 2
AUTO_MODES = {AUTO_OFF: "Nein", AUTO_NEW: "Nur neue Treffer",
              AUTO_UNAVAILABLE: "Alles, was gerade nicht kaufbar ist"}


def search_categories(search: dict) -> list[str]:
    return [line.strip() for line in (search.get("categories") or "").splitlines() if line.strip()]


def search_label(search: dict) -> str:
    """Name eines Suchauftrags für Meldungen: der Suchbegriff oder z. B. „Alle Pokémon · Display“."""
    query = (search.get("query") or "").strip()
    if query:
        return query
    ptype = PRODUCT_TYPES.get(search.get("ptype") or "egal")
    return "Alle Pokémon" + (f" · {ptype['label']}" if ptype and ptype["any"] else "")
LANGUAGES = {"egal": "Jede Sprache", "de": "Deutsch", "en": "Englisch", "jp": "Japanisch"}
TRACKING_PARAMS = {"listtype", "searchparam", "delivery_alarm", "_pos", "_psq", "_ss", "_psid", "_sid", "srsltid",
                   "sid", "force_sid", "spartner", "ref", "source"}
LANG_MARKERS = {
    "de": ["deutsch", "german", "de", "dt", "ger"],
    "en": ["englisch", "english", "en", "eng"],
    "jp": ["japanisch", "japanese", "japan", "jp", "jap", "jpn"],
    "zh": ["chinesisch", "chinese", "china", "chn", "chi", "cn", "zh", "gem pack"],   # Gem Packs gibt es nur auf Chinesisch
    "ko": ["koreanisch", "korean", "korea", "kor", "kr"],
    "th": ["thailaendisch", "thai"],
    "id": ["indonesisch", "indonesian"],
    "fr": ["franzoesisch", "french", "fr"],
    "es": ["spanisch", "spanish"],
    "it": ["italienisch", "italian"],
}
# Set-Kürzel asiatischer Ausgaben in Klammern: „(M3)“, „(SV8a)“, „(CBB5C)“ – deutsche Titel haben die nicht
ASIAN_SET_CODE = re.compile(r"\((?:m\d+[a-z]{0,2}|sv\d+[a-z]{0,2}|s\d+[a-z]{0,2}|cbb\d+[a-z]?|cs\d+[a-z]?|csv\d+[a-z]?)\)", re.I)


class SearchError(Exception):
    def __init__(self, status: str, reason: str, retry_after: int | None = None):
        super().__init__(reason)
        self.status, self.reason, self.retry_after = status, reason, retry_after


@dataclass
class Item:
    url: str
    title: str
    price: float | None = None
    currency: str | None = None
    image: str | None = None
    status: str = UNKNOWN
    release: str | None = None
    release_date: str | None = None


@dataclass
class ShopInfo:
    base: str
    name: str
    platform: str | None
    mode: str
    search_url: str
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- Shop erkennen

async def detect_shop(url: str, fetcher: Fetcher) -> ShopInfo:
    parts = urlsplit(normalize_url(url))
    if not parts.hostname:
        raise SearchError("error", "Das sieht nicht nach einem Link aus.")
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        page = await fetcher.get(origin + "/")
    except httpx.HTTPError as exc:
        raise SearchError("error", f"Shop nicht erreichbar ({type(exc).__name__})")
    _raise_for(page)
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(page.url))     # Weiterleitung auf www. o. ä.

    tree = LexborHTMLParser(page.text)
    platform = _platform(page)
    name = _shop_name(tree, urlsplit(origin).hostname or origin)
    if platform == "Shopify":
        return ShopInfo(origin, name, platform, "shopify",
                        origin + "/search/suggest.json?q={q}&resources[type]=product&resources[limit]=10")
    if platform == "WooCommerce":
        return ShopInfo(origin, name, platform, "woo",
                        origin + "/wp-json/wc/store/v1/products?search={q}&per_page=40")
    template, found = _search_form(tree, page.url, origin)
    notes = [] if found else ["Kein Suchfeld erkannt – ich versuche es mit /search?q=…"]
    return ShopInfo(origin, name, platform, "html", template, notes)


def _shop_name(tree: LexborHTMLParser, host: str) -> str:
    """Kurzer Shopname: og:site_name, sonst das kürzeste Stück des Seitentitels, sonst die Domain."""
    node = tree.css_first('meta[property="og:site_name"]')
    name = (node.attributes.get("content") or "").strip() if node else ""
    if not name:
        title = tree.css_first("title")
        parts = [p.strip() for p in re.split(r"\s[|–—-]\s", title.text(strip=True))] if title else []
        name = min((p for p in parts if len(p) >= 3), key=len, default="")
    if not name or "://" in name or name.lower().startswith("www.") or len(name) > 30 or len(name.split()) > 3:
        name = host.removeprefix("www.")
    return name


def _search_form(tree: LexborHTMLParser, page_url: str, origin: str) -> tuple[str, bool]:
    for form in tree.css("form"):
        if (form.attributes.get("method") or "get").lower() != "get":
            continue
        field_name = None
        extras = {}
        for inp in form.css("input"):
            name = inp.attributes.get("name") or ""
            kind = (inp.attributes.get("type") or "text").lower()
            if kind == "hidden" and name and "token" not in name.lower():
                extras[name] = inp.attributes.get("value") or ""
            elif kind in ("text", "search") and name and (kind == "search" or name.lower() in SEARCH_PARAMS):
                field_name = field_name or name
        if field_name:
            action = urljoin(page_url, form.attributes.get("action") or page_url).split("#")[0]
            sep = "&" if "?" in action else "?"
            prefix = urlencode(extras) + "&" if extras else ""
            return f"{action}{sep}{prefix}{quote_plus(field_name)}={{q}}", True
    return origin + "/search?q={q}", False


# --------------------------------------------------------------------------- Suchen

async def run_search(shop: dict, query: str, fetcher: Fetcher) -> list[Item]:
    """Fragt die Suche eines Shops ab. `shop` ist die Datenbankzeile (mode, search_url, base)."""
    url = shop["search_url"].replace("{q}", quote_plus(query))
    try:
        page = await fetcher.get(url, json=shop["mode"] in ("shopify", "woo"))
    except httpx.HTTPError as exc:
        raise SearchError("error", f"Shop nicht erreichbar ({type(exc).__name__})")
    if shop["mode"] in ("shopify", "woo") and (page.status in (401, 403, 404) and not page.block):
        # JSON-Weg abgeschaltet → normale Suchseite
        fallback = shop["base"] + ("/search?q={q}&type=product" if shop["mode"] == "shopify"
                                   else "/?s={q}&post_type=product")
        return await run_search({**shop, "mode": "html", "search_url": fallback}, query, fetcher)
    _raise_for(page)
    if page.status >= 400:
        raise SearchError("error", f"Die Shop-Suche antwortet mit Fehler {page.status}")
    try:
        if shop["mode"] == "shopify":
            return _parse_shopify(page.text, shop["base"])
        if shop["mode"] == "woo":
            return _parse_woo(page.text)
    except (ValueError, KeyError, TypeError):
        raise SearchError("error", "Die Antwort der Shop-Suche war unverständlich")
    return parse_cards(page.text, page.url)


async def search_shop(shop: dict, search: dict, fetcher: Fetcher) -> list[Item]:
    """Alle Schreibweisen eines Suchauftrags in einem Shop abfragen.

    Mit Produkttyp-Wort („… display“) finden lockere Shopsuchen das Richtige schneller. Strenge
    Suchen (alle Wörter Pflicht) liefern aber nichts, wenn der Shop anders benennt – dann einmal ohne.
    """
    items: list[Item] = []
    for alt in alternatives(search["query"]) or [BROAD_QUERY]:
        query = shop_query(alt, search.get("ptype") or "egal")
        found = await run_search(shop, query, fetcher)
        if not found and query != alt:
            found = await run_search(shop, alt, fetcher)
        items += found
    return items


async def fetch_category(url: str, fetcher: Fetcher, max_pages: int = 3) -> list[Item]:
    """Liest eine Kategorie-Seite eines Shops (bis zu drei Seiten weit)."""
    url = normalize_url(url)
    parts = urlsplit(url)
    if "/collections/" in parts.path:
        # Shopify-Kategorie: die komplette Liste gibt es als JSON
        base = f"{parts.scheme}://{parts.netloc}"
        json_url = f"{base}{parts.path.rstrip('/')}/products.json?limit=100"
        try:
            page = await fetcher.get(json_url, json=True)
            if page.status == 200 and not page.block:
                found = _parse_shopify_products(page.text, base)
                if found:                      # leer heißt bei manchen Shops nur „JSON abgeschaltet“
                    return found
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    items: list[Item] = []
    visited: set[str] = set()
    for _ in range(max_pages):
        try:
            page = await fetcher.get(url)
        except httpx.HTTPError as exc:
            raise SearchError("error", f"Kategorie-Seite nicht erreichbar ({type(exc).__name__})")
        _raise_for(page)
        if page.status >= 400:
            raise SearchError("error", f"Kategorie-Seite antwortet mit Fehler {page.status}")
        visited.add(url)
        items += parse_cards(page.text, page.url)
        url = _next_page(page.text, page.url)
        if not url or url in visited:
            break
    return items


def _next_page(text: str, base: str) -> str | None:
    tree = LexborHTMLParser(text)
    node = (tree.css_first('link[rel="next"]') or tree.css_first('a[rel="next"]')
            or tree.css_first('a[aria-label*="ächste"], a[aria-label*="Next"], a[title*="ächste"]'))
    href = node.attributes.get("href") if node else None
    return urljoin(base, href) if href else None


def _parse_shopify_products(text: str, base: str) -> list[Item]:
    items = []
    for p in json.loads(text)["products"]:
        variants = p.get("variants") or []
        images = p.get("images") or []
        price = min((v for v in (_to_float(x.get("price")) for x in variants) if v is not None), default=None)
        items.append(Item(
            url=f"{base}/products/{p['handle']}",
            title=html.unescape(p.get("title") or ""),
            price=price, currency="EUR",
            image=images[0].get("src") if images else None,
            status=AVAILABLE if any(v.get("available") for v in variants) else SOLDOUT,
        ))
    return [i for i in items if i.title]


def _raise_for(page: Page) -> None:
    if page.queue:
        raise SearchError(QUEUE, "Der Shop leitet in eine Warteschlange um – oft läuft gerade ein Drop.")
    if page.block:
        raise SearchError(BLOCKED, page.block, page.retry_after)


def _parse_shopify(text: str, base: str) -> list[Item]:
    products = json.loads(text)["resources"]["results"]["products"]
    items = []
    for p in products:
        image = p.get("image") or (p.get("featured_image") or {}).get("url")
        if image and image.startswith("//"):
            image = "https:" + image
        items.append(Item(
            url=urljoin(base, (p.get("url") or "").split("?")[0]),
            title=html.unescape(p.get("title") or ""),
            price=_to_float(p.get("price")),
            currency="EUR",
            image=image,
            status=AVAILABLE if p.get("available") else SOLDOUT,
        ))
    return [i for i in items if i.title]


def _parse_woo(text: str) -> list[Item]:
    items = []
    for p in json.loads(text):
        prices = p.get("prices") or {}
        minor = int(prices.get("currency_minor_unit") or 2)
        price = _to_float(prices.get("price"))
        images = p.get("images") or []
        buyable = p.get("is_in_stock") and p.get("is_purchasable", True)
        items.append(Item(
            url=p.get("permalink") or "",
            title=html.unescape(re.sub(r"<[^>]+>", "", p.get("name") or "")),
            price=round(price / 10 ** minor, 2) if price is not None else None,
            currency=prices.get("currency_code") or "EUR",
            image=images[0].get("src") if images else None,
            status=AVAILABLE if buyable else SOLDOUT,
        ))
    return [i for i in items if i.url and i.title]


CARD_CLASSES = {"productbox", "product-box", "product-item", "product-card", "card-product", "product-tile",
                "product-miniature", "grid-product", "product-grid-item", "productlist-item",
                "product-list-item", "product-wrapper", "product-card-wrapper", "product"}
SKIP_LINKS = ("cart", "warenkorb", "wishlist", "merkzettel", "compare", "vergleich", "login", "account",
              "javascript:", "mailto:")


def parse_cards(text: str, base: str) -> list[Item]:
    """Liest Produktkarten von einer Such- oder Kategorieseite."""
    tree = LexborHTMLParser(text)
    cards = tree.css('[itemtype*="schema.org/Product"]')
    if not cards:
        cards = [n for n in tree.css("[class]")
                 if CARD_CLASSES & set((n.attributes.get("class") or "").lower().split())]
    host = urlsplit(base).hostname
    items: dict[str, Item] = {}
    for card in cards:
        item = _card_item(card, base, host)
        if item and item.url not in items:
            items[item.url] = item
    return list(items.values())


def _card_item(card: LexborNode, base: str, host: str | None) -> Item | None:
    raw = card.text(separator=" ", strip=True)
    if len(raw) > 6000:                        # ganzer Seitenbereich statt einer Karte
        return None

    link = _prop(card, "url")
    if not link:
        anchor = card.css_first('[class*="title"] a[href]') or next(
            (a for a in card.css("a[href]") if _product_link(a.attributes.get("href") or "")), None)
        link = anchor.attributes.get("href") if anchor else None
    if not link or not _product_link(link):
        return None
    url = clean_url(urljoin(base, link))
    if urlsplit(url).hostname != host:
        return None

    title = _prop(card, "name", own=True)
    if not title:
        # „card-title“, „product-title“ … – aber nicht „title-header“, der oft Zusatzangaben enthält
        node = (card.css_first('[class$="title"], [class*="title "], [class$="-name"], [class*="-name "]')
                or card.css_first('[class*="title"]') or card.css_first('[class*="name"]'))
        title = node.text(separator=" ", strip=True) if node else ""
    if not title:
        texts = [a.text(separator=" ", strip=True) for a in card.css("a[href]")]
        title = max(texts, key=len, default="")
    if not title:
        img = card.css_first("img[alt]")
        title = img.attributes.get("alt", "") if img else ""
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) < 3:
        return None

    price = _to_float(_prop(card, "price"))
    if price is None:
        # „149,99 €“, „€ 149,99“ und auch „149 ,99 €“ (Euro und Cent getrennt gesetzt)
        m = (re.search(r"(\d{1,4}(?:\.\d{3})*)\s*,\s*(\d{2})\s*(?:€|EUR)", raw)
             or re.search(r"€\s*(\d{1,4}(?:\.\d{3})*)\s*,\s*(\d{2})", raw))
        price = _to_float(f"{m.group(1)},{m.group(2)}") if m else None
    if price is not None and price <= 0:
        price = None                           # „0,00 €“ = Preis noch nicht bekannt (oft bei Vorbestellungen)

    status = UNKNOWN
    availability = _prop(card, "availability")
    if availability and schema_status(availability):
        status = schema_status(availability)
    else:
        # Titel mit prüfen: „… VORVERKAUF“ oder „(Vorbestellung)“ steht oft nur dort
        status = text_status(f"{title} {raw[:1500]}")[0] or UNKNOWN

    image = _prop(card, "image")
    if not image:
        img = card.css_first("img")
        if img:
            image = img.attributes.get("data-src") or img.attributes.get("src")
    image = urljoin(base, image) if image and not image.startswith("data:") else None

    release, release_date = find_release(f"{title} {raw[:2000]}")
    return Item(url=url, title=title, price=price, currency=_prop(card, "priceCurrency") or "EUR",
                image=image, status=status, release=release, release_date=release_date)


def _prop(card: LexborNode, name: str, own: bool = False) -> str | None:
    """schema.org-Eigenschaft einer Karte. `own`: nur die der Karte selbst, nicht die eines eingebetteten
    Herstellers – sonst heißt bei manchen Shops jedes Produkt einfach „Pokemon“."""
    nodes = card.css(f'[itemprop="{name}"]')
    node = next((n for n in nodes if not own or _own_scope(n, card)), None)
    if node is None:
        return None
    value = node.attributes.get("content") or node.attributes.get("href") or node.text(separator=" ", strip=True)
    return value.strip() if value else None


def _own_scope(node: LexborNode, card: LexborNode) -> bool:
    current = node.parent
    while current is not None and current.mem_id != card.mem_id:
        if "itemscope" in current.attributes:
            return False
        current = current.parent
    return True


def clean_url(url: str) -> str:
    """Entfernt Such- und Tracking-Anhängsel, damit derselbe Artikel immer denselben Link hat."""
    parts = urlsplit(url.split("#")[0])
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    return parts._replace(query=urlencode(query)).geturl()


def _product_link(href: str) -> bool:
    href = href.strip().lower()
    return bool(href) and not href.startswith("#") and not any(word in href for word in SKIP_LINKS)


# --------------------------------------------------------------------------- Filtern

def normalize(text: str) -> str:
    text = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def alternatives(query: str) -> list[str]:
    """„Prismatische Entwicklungen | Prismatic Evolutions“ → zwei Schreibweisen."""
    return [alt.strip() for alt in query.split("|") if alt.strip()]


def shop_query(alternative: str, ptype: str) -> str:
    extra = PRODUCT_TYPES.get(ptype, PRODUCT_TYPES["egal"])["query"]
    if extra and normalize(extra).strip() not in normalize(alternative):
        return f"{alternative} {extra}"
    return alternative


def _has(hay: str, phrase: str) -> bool:
    """Wort oder Wortgruppe am Wortanfang – „display“ passt auch auf „displays“."""
    words = normalize(phrase).split()
    return bool(words) and re.search(r"(?<= )" + r"\s+".join(map(re.escape, words)), hay) is not None


def matches(item: Item, search: dict) -> bool:
    slug = urlsplit(item.url).path.replace("/", " ")
    hay = normalize(f"{item.title} {slug}")

    alts = alternatives(search.get("query") or "")
    if alts and not any(all(_has(hay, word) for word in normalize(alt).split()) for alt in alts):
        return False
    if not alts and any(_has(hay, other) for other in OTHER_TCGS):
        return False
    ptype = PRODUCT_TYPES.get(search.get("ptype") or "egal", PRODUCT_TYPES["egal"])
    if ptype["any"] and not any(_has(hay, p) for p in ptype["any"]):
        return False
    if any(_has(hay, p) for p in ptype["not"]):
        return False
    if ptype["any"] and any(_has(hay, p) for p in ACCESSORIES):
        return False
    if not _language_ok(hay, search.get("lang") or "egal", asian_code=bool(ASIAN_SET_CODE.search(item.title))):
        return False
    excludes = [w.strip() for w in (search.get("exclude") or "").split(",") if w.strip()]
    if any(_has(hay, w) for w in excludes):
        return False
    max_price = search.get("max_price")
    if max_price and item.price is not None and item.price > float(max_price):
        return False
    return True


def _language_ok(hay: str, want: str, asian_code: bool = False) -> bool:
    if want == "egal":
        return True
    found = {lang for lang, words in LANG_MARKERS.items() if any(f" {w} " in hay for w in words)}
    if want == "de":
        # deutsche Ware ist oft gar nicht markiert – also: deutsch markiert, oder keine andere Sprache erkennbar
        return "de" in found or not (found - {"de"} or asian_code)
    return want in found or (want == "jp" and asian_code and not found)
