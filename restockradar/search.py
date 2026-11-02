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
from urllib.parse import quote_plus, urlencode, urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .detect import (AVAILABLE, BLOCKED, QUEUE, SOLDOUT, UNKNOWN, _platform, _to_float, normalize_url,
                     schema_status, text_status)
from .fetcher import Fetcher, Page

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
LANGUAGES = {"egal": "Jede Sprache", "de": "Deutsch", "en": "Englisch", "jp": "Japanisch"}
LANG_MARKERS = {
    "de": ["deutsch", "german", "de", "dt"],
    "en": ["englisch", "english", "en", "eng"],
    "jp": ["japanisch", "japanese", "jp", "jap", "jpn"],
}


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
    node = tree.css_first('meta[property="og:site_name"]')
    name = (node.attributes.get("content") or "").strip() if node else ""
    if not name:
        title = tree.css_first("title")
        name = re.split(r"\s[|–—-]\s", title.text(strip=True))[-1].strip() if title else ""
    if not name or len(name) > 40:
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
                "product-list-item", "product-wrapper", "product"}
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
    url = urljoin(base, link).split("#")[0]
    if urlsplit(url).hostname != host:
        return None

    title = _prop(card, "name")
    if not title:
        node = card.css_first('[class*="title"]') or card.css_first('[class*="name"]')
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
        m = re.search(r"(\d{1,4}(?:\.\d{3})*,\d{2})\s*(?:€|EUR)", raw) or re.search(r"€\s*(\d{1,4}(?:\.\d{3})*,\d{2})", raw)
        price = _to_float(m.group(1)) if m else None

    status = UNKNOWN
    availability = _prop(card, "availability")
    if availability and schema_status(availability):
        status = schema_status(availability)
    else:
        status = text_status(raw[:1500])[0] or UNKNOWN

    image = _prop(card, "image")
    if not image:
        img = card.css_first("img")
        if img:
            image = img.attributes.get("data-src") or img.attributes.get("src")
    image = urljoin(base, image) if image and not image.startswith("data:") else None

    return Item(url=url, title=title, price=price, currency=_prop(card, "priceCurrency") or "EUR",
                image=image, status=status)


def _prop(card: LexborNode, name: str) -> str | None:
    node = card.css_first(f'[itemprop="{name}"]')
    if node is None:
        return None
    value = node.attributes.get("content") or node.attributes.get("href") or node.text(separator=" ", strip=True)
    return value.strip() if value else None


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

    if not any(all(_has(hay, word) for word in normalize(alt).split()) for alt in alternatives(search["query"])):
        return False
    ptype = PRODUCT_TYPES.get(search.get("ptype") or "egal", PRODUCT_TYPES["egal"])
    if ptype["any"] and not any(_has(hay, p) for p in ptype["any"]):
        return False
    if any(_has(hay, p) for p in ptype["not"]):
        return False
    if not _language_ok(hay, search.get("lang") or "egal"):
        return False
    excludes = [w.strip() for w in (search.get("exclude") or "").split(",") if w.strip()]
    if any(_has(hay, w) for w in excludes):
        return False
    max_price = search.get("max_price")
    if max_price and item.price is not None and item.price > float(max_price):
        return False
    return True


def _language_ok(hay: str, want: str) -> bool:
    if want == "egal":
        return True
    found = {lang for lang, words in LANG_MARKERS.items() if any(f" {w} " in hay for w in words)}
    if want == "de":
        return "de" in found or not (found & {"en", "jp"})   # deutsche Ware ist oft gar nicht markiert
    return want in found
