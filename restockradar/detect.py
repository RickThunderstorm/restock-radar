"""Erkennt, ob ein Produkt verfügbar ist – über mehrere unabhängige Signale.

Reihenfolge der Verlässlichkeit:
  Shopify-Lagerfeld > schema.org (JSON-LD / Microdata) > Meta-Tags / Shop-Button > Seitentext
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .fetcher import Fetcher, Page

AVAILABLE = "available"
PREORDER = "preorder"
SOLDOUT = "soldout"
UNKNOWN = "unknown"
BLOCKED = "blocked"
QUEUE = "queue"
ERROR = "error"

BUYABLE = (AVAILABLE, PREORDER)

SOURCE_LABELS = {
    "shopify": "Shopify-Lagerfeld",
    "jsonld": "schema.org (Google-Daten)",
    "microdata": "schema.org (Microdata)",
    "meta": "Meta-Tag",
    "button": "Warenkorb-Button",
    "text": "Text auf der Seite",
}

OUT_PHRASES = [
    "derzeit nicht verfügbar", "momentan nicht verfügbar", "aktuell nicht verfügbar",
    "nicht mehr verfügbar", "nicht verfügbar", "nicht lieferbar", "nicht auf lager",
    "nicht vorrätig", "ausverkauft", "vergriffen", "bald wieder verfügbar", "bald verfügbar",
    "demnächst verfügbar", "benachrichtigen, wenn", "benachrichtige mich",
    "e-mail, wenn verfügbar", "sold out", "out of stock", "currently unavailable",
    "notify me", "coming soon",
]
PRE_PHRASES = ["jetzt vorbestellen", "vorbestellen", "vorbestellung", "vorbestellbar",
               "pre-order", "preorder", "pre order"]
BUY_PHRASES = ["in den warenkorb", "warenkorb hinzufügen", "in den einkaufswagen",
               "jetzt kaufen", "sofort kaufen", "add to cart", "add to basket", "buy now"]
IN_TEXT_PHRASES = BUY_PHRASES + ["auf lager", "sofort lieferbar", "sofort versandfertig",
                                 "versandbereit", "lieferbar", "in stock"]

# Kauf-Buttons der gängigen Shopsysteme – zuverlässiger als irgendein Button auf der Seite
KNOWN_BUY_SELECTORS = [
    'form[action*="/cart/add"] button[type="submit"]',      # Shopify
    'form[action*="/cart/add"] [name="add"]',
    'form[action*="/cart/add"] input[type="submit"]',
    'button.single_add_to_cart_button',                     # WooCommerce
    '#productDetailPageBuyProductForm button',              # Shopware 6
    '.product-detail-buy .btn-buy',
    '#product-addtocart-button',                            # Magento
    'button[data-button-action="add-to-cart"]',             # PrestaShop
    'button[name="inWarenkorb"]',                           # JTL-Shop
    '#add-to-cart', 'button#AddToCart', 'button[data-add-to-cart]',
]
SKIP_CONTAINERS = ("related", "recommend", "upsell", "cross-sell", "crosssell", "similar",
                   "slider", "carousel", "recently", "zuletzt", "header", "footer", "nav")


@dataclass
class Signal:
    source: str
    status: str
    weight: int
    detail: str = ""

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class Result:
    url: str
    status: str = UNKNOWN
    method: str | None = None
    confidence: str = "niedrig"
    rating: str = "nicht möglich"
    reason: str = ""
    name: str | None = None
    image: str | None = None
    price: float | None = None
    currency: str | None = None
    shop: str = ""
    platform: str | None = None
    variant_id: str | None = None
    variant_title: str | None = None
    variants: list[dict] = field(default_factory=list)
    cart_url: str | None = None
    signals: list[Signal] = field(default_factory=list)
    note: str = ""
    retry_after: int | None = None

    @property
    def method_label(self) -> str:
        return SOURCE_LABELS.get(self.method or "", "")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["signals"] = [dict(asdict(s), label=s.label) for s in self.signals]
        data["method_label"] = SOURCE_LABELS.get(self.method or "", None)
        return data


# --------------------------------------------------------------------------- Einstieg

async def analyze(url: str, fetcher: Fetcher, *, method: str | None = None,
                  variant_id: str | None = None) -> Result:
    """Prüft eine Produktseite. Mit `method` wird gezielt das gemerkte Signal ausgewertet."""
    url = normalize_url(url)
    res = Result(url=url, shop=urlsplit(url).hostname or "")
    variant_id = variant_id or _variant_from_url(url)

    # Schnellweg: Bei Shopify reicht das kleine Lagerfeld-JSON – schont den Shop.
    # Viele Shops drosseln dieses JSON allerdings schnell; dann geht es mit der Seite weiter.
    json_allowed = method in (None, "shopify")
    if method == "shopify" and _shopify_handle(url):
        try:
            if await _apply_shopify(res, url, fetcher, variant_id):
                res.method, res.confidence, res.rating = "shopify", "hoch", "sehr gut"
                res.status = res.signals[0].status
                return res
        except _ShopSaysNo as no:
            if no.page.queue:
                res.status, res.reason = QUEUE, "Der Shop leitet in eine Warteschlange um – oft läuft gerade ein Drop."
                return res
        except (httpx.HTTPError, ValueError):
            pass
        json_allowed = False

    try:
        page = await fetcher.get(url)
    except httpx.HTTPError as exc:
        res.status, res.reason = ERROR, _network_reason(exc)
        return res

    res.retry_after = page.retry_after
    if page.queue:
        res.status, res.reason = QUEUE, "Der Shop leitet in eine Warteschlange um – oft läuft gerade ein Drop."
        return res
    if page.block:
        res.status, res.reason = BLOCKED, page.block
        return res
    if page.status >= 400:
        res.status = ERROR
        res.reason = "Seite nicht gefunden (404)" if page.status == 404 else f"Shop antwortet mit Fehler {page.status}"
        return res
    if _redirected_home(url, page.url):
        res.status = ERROR
        res.reason = ("Der Shop leitet auf seine Startseite um – das Produkt gibt es dort wohl nicht (mehr) "
                      "oder nicht für Deutschland.")
        return res

    tree = LexborHTMLParser(page.text)
    res.platform = _platform(page)
    _apply_page_info(res, tree, page.url)

    if res.platform == "Shopify" and _shopify_handle(url) and json_allowed:
        try:
            await _apply_shopify(res, url, fetcher, variant_id)
        except (_ShopSaysNo, httpx.HTTPError, ValueError):
            pass                                    # die Seite selbst liefert ja noch Signale
    if res.platform == "Shopify":
        _shopify_form(res, tree, page.url, variant_id)
    res.signals.extend(s for s in (
        _jsonld_signal(res, tree, page.url, variant_id),
        _microdata_signal(tree),
        _meta_signal(tree),
        _button_signal(tree),
        _text_signal(tree),
    ) if s)
    res.name = _pick_name(res.name, tree)

    _decide(res, method)
    return res


def _redirected_home(requested: str, final: str) -> bool:
    """Produktlink → Startseite (z. B. „/“ oder „/de“) heißt: Wir bewerten die falsche Seite."""
    want = [p for p in urlsplit(requested).path.split("/") if p]
    got = [p for p in urlsplit(final).path.split("/") if p]
    bare_locale = len(got) == 1 and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", got[0], re.I)
    return len(want) >= 2 and (not got or bool(bare_locale))


def normalize_url(url: str) -> str:
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


# --------------------------------------------------------------------------- Entscheidung

def _decide(res: Result, method: str | None) -> None:
    signals = sorted(res.signals, key=lambda s: -s.weight)
    if not signals:
        res.status, res.rating = UNKNOWN, "nicht möglich"
        res.reason = ("Auf der Seite habe ich kein Verfügbarkeits-Signal gefunden. "
                      "Vielleicht lädt der Shop den Lagerstand erst per JavaScript nach.")
        return

    chosen = next((s for s in signals if s.source == method), None) if method else None
    if method and not chosen:
        res.note = f"Das Signal „{SOURCE_LABELS.get(method, method)}“ fehlt gerade – ich nutze ersatzweise ein anderes."

    if not chosen:
        chosen = signals[0]
        strong = [s for s in signals if s.weight >= 2]
        if chosen.source != "shopify" and len({s.status for s in strong}) > 1:
            # Widerspruch: Viele Shops liefern in den Google-Daten dauerhaft „InStock“.
            # Das Signal, das „nicht kaufbar“ meldet, ist meist das aktuelle – und das,
            # was beim Restock umspringt.
            chosen = next(s for s in strong if s.status not in BUYABLE)
            res.note = res.note or "Die Signale widersprechen sich – ich beobachte das vorsichtigere."

    agree = all(s.status == chosen.status for s in signals if s.weight >= 2)
    res.status = chosen.status
    res.method = chosen.source
    res.confidence = "hoch" if chosen.weight >= 3 and agree else "mittel" if chosen.weight >= 2 else "niedrig"
    res.rating = {4: "sehr gut", 3: "sehr gut", 2: "gut"}.get(chosen.weight, "eingeschränkt")


# --------------------------------------------------------------------------- Shopify

def _platform(page: Page) -> str | None:
    h, text = page.headers, page.text
    if any(k in h for k in ("x-shopid", "x-shopify-stage", "x-sorting-hat-shopid")) \
            or "shopify" in h.get("powered-by", "").lower() \
            or "cdn.shopify.com" in text or "Shopify.theme" in text:
        return "Shopify"
    low = text[:400000].lower()
    for marker, name in (("woocommerce", "WooCommerce"), ("shopware", "Shopware"),
                         ("jtl-shop", "JTL-Shop"), ("prestashop", "PrestaShop"),
                         ("mage/cookies", "Magento"), ("magento", "Magento"),
                         ("gambio", "Gambio"), ("oxid", "OXID")):
        if marker in low:
            return name
    return None


def _shopify_handle(url: str) -> str | None:
    m = re.search(r"/products/([^/?#]+)", urlsplit(url).path)
    if not m:
        return None
    return re.sub(r"\.(js|json|oembed)$", "", m.group(1))


def _variant_from_url(url: str) -> str | None:
    values = parse_qs(urlsplit(url).query).get("variant")
    return values[0] if values else None


class _ShopSaysNo(Exception):
    def __init__(self, page: Page):
        super().__init__(page.block or "queue")
        self.page = page


def _shopify_json_url(url: str) -> str:
    parts = urlsplit(url)
    # Sprachpräfix wie /en oder /de-at behalten – ohne ihn antworten manche Shops mit 404
    locale = re.match(r"^/[a-z]{2}(?:-[a-z]{2})?(?=/)", parts.path, re.I)
    prefix = locale.group(0) if locale and not parts.path.startswith("/products/") else ""
    return f"{parts.scheme}://{parts.netloc}{prefix}/products/{_shopify_handle(url)}.js"


async def _apply_shopify(res: Result, url: str, fetcher: Fetcher, variant_id: str | None) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    page = await fetcher.get(_shopify_json_url(url), json=True)
    if page.block or page.queue:
        raise _ShopSaysNo(page)
    if page.status != 200:
        return False
    data = json.loads(page.text)
    variants = [{
        "id": str(v.get("id")),
        "title": _variant_name(v.get("public_title") or v.get("title")),
        "available": bool(v.get("available")),
        "price": _cents(v.get("price")),
    } for v in data.get("variants", [])]
    chosen = next((v for v in variants if v["id"] == str(variant_id)), None) if variant_id else None
    available = chosen["available"] if chosen else bool(data.get("available"))
    if not chosen and len(variants) == 1:
        chosen = variants[0]

    res.platform = "Shopify"
    res.variants = variants if len(variants) > 1 else []
    res.name = res.name or data.get("title")
    image = data.get("featured_image")
    if image and not res.image:
        res.image = "https:" + image if image.startswith("//") else image
    if chosen:
        res.variant_id, res.variant_title, res.price = chosen["id"], chosen["title"], chosen["price"]
    elif data.get("price") is not None:
        res.price = _cents(data.get("price"))
    res.currency = res.currency or "EUR"
    buy_variant = chosen or next((v for v in variants if v["available"]), variants[0] if variants else None)
    if buy_variant:
        # Offizieller Shopify-Warenkorb-Link: legt den Artikel direkt in den Korb und öffnet die Kasse.
        res.cart_url = f"{origin}/cart/{buy_variant['id']}:1"
    detail = f"Variante „{chosen['title'] or 'Standard'}“" if chosen and len(variants) > 1 else "Produkt"
    status = AVAILABLE if available else SOLDOUT
    res.signals.insert(0, Signal("shopify", status, 4, f"{detail}: {'verfügbar' if available else 'ausverkauft'}"))
    return True


def _variant_name(title) -> str | None:
    """Shopify nennt die einzige Variante eines Produkts „Default Title“ – die zeigen wir nicht an."""
    title = (title or "").strip()
    return None if not title or title.lower() == "default title" else title


def _cents(value) -> float | None:
    try:
        return round(int(value) / 100, 2)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- Seiten-Infos

def _apply_page_info(res: Result, tree: LexborHTMLParser, base: str) -> None:
    res.name = _meta(tree, "og:title")
    image = _meta(tree, "og:image") or _meta(tree, "twitter:image")
    if image and image.startswith("http://") and base.startswith("https://"):
        image = "https://" + image[7:]
    res.image = urljoin(base, image) if image else None
    price = _meta(tree, "product:price:amount") or _meta(tree, "og:price:amount")
    res.price = _to_float(price)
    res.currency = _meta(tree, "product:price:currency") or _meta(tree, "og:price:currency")


def _meta(tree: LexborHTMLParser, key: str) -> str | None:
    node = tree.css_first(f'meta[property="{key}"]') or tree.css_first(f'meta[name="{key}"]')
    value = node.attributes.get("content") if node else None
    return value.strip() if value else None


def _pick_name(name: str | None, tree: LexborHTMLParser) -> str | None:
    """Manche Shops tragen als Titel nur ihren eigenen Namen ein – dann zählt die Überschrift."""
    site = (_meta(tree, "og:site_name") or "").lower()
    if name and site and site in name.lower() and len(name) - len(site) < 12:
        name = None
    return name or _first_text(tree, "h1") or _first_text(tree, "title")


def _shopify_form(res: Result, tree: LexborHTMLParser, base: str, variant_id: str | None) -> None:
    """Liest Varianten und Varianten-ID aus dem Bestellformular – klappt auch, wenn das JSON gedrosselt ist."""
    form = tree.css_first('form[action*="/cart/add"]')
    if form is None:
        return
    parts = urlsplit(base)
    chosen = variant_id
    select = form.css_first('select[name="id"]')
    if select is not None and not res.variants:
        variants = []
        for option in select.css("option"):
            vid = (option.attributes.get("value") or "").strip()
            if not vid.isdigit():
                continue
            text = re.sub(r"\s+", " ", option.text(strip=True))
            soldout = "disabled" in option.attributes or any(p in text.lower() for p in OUT_PHRASES)
            variants.append({"id": vid, "title": _variant_name(re.split(r"\s[-–]\s", text)[0]) or "Variante",
                             "available": not soldout, "price": None})
            if chosen is None and "selected" in option.attributes:
                chosen = vid
        if len(variants) > 1:
            res.variants = variants
        elif variants and chosen is None:
            chosen = variants[0]["id"]
    if chosen is None:
        node = form.css_first('input[name="id"]')
        chosen = (node.attributes.get("value") or "").strip() if node else None
    if variant_id and res.variants:
        res.variant_id = variant_id
        res.variant_title = next((v["title"] for v in res.variants if v["id"] == variant_id), res.variant_title)
    elif not res.variants and chosen:
        res.variant_id = res.variant_id or chosen
    if chosen and chosen.isdigit() and not res.cart_url:
        # Offizieller Shopify-Warenkorb-Link: legt den Artikel direkt in den Korb und öffnet die Kasse.
        res.cart_url = f"{parts.scheme}://{parts.netloc}/cart/{chosen}:1"


def _first_text(tree: LexborHTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    text = node.text(strip=True) if node else ""
    return text or None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.]", "", str(value))
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- Signale

def schema_status(value: str) -> str | None:
    v = re.sub(r"[\s_\-]", "", str(value).strip().lower().rsplit("/", 1)[-1])
    if v in ("instock", "onlineonly", "limitedavailability", "available", "availablefororder",
             "yes", "true", "1"):
        return AVAILABLE
    if v in ("preorder", "presale", "backorder"):
        return PREORDER
    if v in ("outofstock", "soldout", "discontinued", "instoreonly", "oos", "unavailable",
             "no", "false", "0"):
        return SOLDOUT
    return None


def _jsonld_signal(res: Result, tree: LexborHTMLParser, base: str, variant_id: str | None = None) -> Signal | None:
    products = []
    for node in tree.css('script[type="application/ld+json"]'):
        data = _loads_lenient(node.text())
        if data is not None:
            products.extend(_iter_products(data))
    if not products:
        return None
    path = urlsplit(base).path.rstrip("/")
    product = next((p for p in products if path and isinstance(p.get("url") or p.get("@id"), str)
                    and urlsplit(p.get("url") or p.get("@id")).path.rstrip("/") == path), products[0])

    statuses, price, currency, per_variant = _offer_info(product, variant_id)
    if isinstance(product.get("name"), str) and product["name"].strip():
        res.name = product["name"].strip()          # der Produktname aus den Google-Daten ist am saubersten
    if not res.image:
        image = _image_url(product.get("image"))
        res.image = urljoin(base, image) if image else None
    if price is not None and (res.price is None or per_variant):
        res.price, res.currency = price, currency or res.currency
    if not statuses:
        return None
    status = AVAILABLE if AVAILABLE in statuses else PREORDER if PREORDER in statuses else SOLDOUT
    return Signal("jsonld", status, 3, _status_word(status) + (" (gewählte Variante)" if per_variant else ""))


def _loads_lenient(text: str):
    text = text.strip()
    text = re.sub(r"^\s*(<!--|//\s*<!\[CDATA\[)", "", text)
    text = re.sub(r"(-->|//\s*\]\]>)\s*$", "", text)
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            return json.loads(candidate, strict=False)
        except ValueError:
            continue
    return None


def _iter_products(node, in_list: bool = False):
    if isinstance(node, list):
        for item in node:
            yield from _iter_products(item, in_list)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = [str(t).lower() for t in (types if isinstance(types, list) else [types]) if t]
        if not in_list and any(t in ("product", "productgroup", "individualproduct") for t in types):
            yield node
            return
        for key, value in node.items():
            yield from _iter_products(value, in_list or key == "itemListElement" or "itemlist" in types)


def _offer_info(product: dict, variant_id: str | None = None) -> tuple[list[str], float | None, str | None, bool]:
    stack = list(_as_list(product.get("offers")))
    for variant in _as_list(product.get("hasVariant")):
        if isinstance(variant, dict):
            link = variant.get("url") or variant.get("@id") or ""
            stack.extend({"url": link, **o} for o in _as_list(variant.get("offers")) if isinstance(o, dict))
    offers = []
    while stack:
        offer = stack.pop(0)
        if isinstance(offer, dict):
            offers.append(offer)
            stack.extend(_as_list(offer.get("offers")))

    # Shopify & Co. führen pro Variante ein eigenes Angebot mit „?variant=…“ im Link
    per_variant = False
    if variant_id:
        mine = [o for o in offers if f"variant={variant_id}" in str(o.get("url") or o.get("@id") or "")]
        if mine:
            offers, per_variant = mine, True

    statuses = [s for o in offers if o.get("availability") and (s := schema_status(o["availability"]))]
    price, currency = None, None
    for offer in offers:
        spec = offer.get("priceSpecification")
        spec_price = spec.get("price") if isinstance(spec, dict) else None
        price = _to_float(offer.get("price") or offer.get("lowPrice") or spec_price)
        if price is not None:
            currency = offer.get("priceCurrency")
            break
    return statuses, price, currency, per_variant


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _image_url(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _image_url(value[0])
    if isinstance(value, dict):
        return value.get("url") or value.get("contentUrl")
    return None


def _microdata_signal(tree: LexborHTMLParser) -> Signal | None:
    for node in tree.css('[itemprop="availability"]'):
        if _in_skipped_container(node):
            continue
        attrs = node.attributes
        status = schema_status(attrs.get("href") or attrs.get("content") or node.text(strip=True))
        if status:
            return Signal("microdata", status, 3, _status_word(status))
    return None


def _meta_signal(tree: LexborHTMLParser) -> Signal | None:
    for key in ("product:availability", "og:availability", "availability"):
        value = _meta(tree, key)
        status = schema_status(value) if value else None
        if status:
            return Signal("meta", status, 2, f"{key} = {value}")
    return None


def _button_signal(tree: LexborHTMLParser) -> Signal | None:
    for selector in KNOWN_BUY_SELECTORS:
        node = tree.css_first(selector)
        if node is not None:
            status = _button_status(node, known=True)
            if status:
                return Signal("button", status, 2, f"„{_button_text(node)[:40]}“" + (" (deaktiviert)" if _disabled(node) else ""))

    for node in tree.css('button, input[type="submit"], a[role="button"], a[class*="btn"], a[class*="button"]'):
        if _in_skipped_container(node):
            continue
        status = _button_status(node, known=False)
        if status:
            return Signal("button", status, 1, f"„{_button_text(node)[:40]}“" + (" (deaktiviert)" if _disabled(node) else ""))
    return None


def _button_text(node: LexborNode) -> str:
    text = node.text(separator=" ", strip=True) or node.attributes.get("value") or node.attributes.get("aria-label") or ""
    return re.sub(r"\s+", " ", text).strip()


def _disabled(node: LexborNode) -> bool:
    attrs = node.attributes
    classes = (attrs.get("class") or "").lower().split()
    return "disabled" in attrs or attrs.get("aria-disabled") == "true" or "disabled" in classes or "is-disabled" in classes


def _button_status(node: LexborNode, *, known: bool) -> str | None:
    text = _button_text(node).lower()
    if any(p in text for p in OUT_PHRASES):
        return SOLDOUT
    if any(p in text for p in PRE_PHRASES):
        return SOLDOUT if _disabled(node) else PREORDER
    if any(p in text for p in BUY_PHRASES) or (known and text):
        return SOLDOUT if _disabled(node) else AVAILABLE
    return None


def _in_skipped_container(node: LexborNode) -> bool:
    current = node.parent
    for _ in range(12):
        if current is None or current.tag in ("body", "html"):
            return False
        if current.tag in ("header", "footer", "nav", "aside"):
            return True
        marker = ((current.attributes.get("class") or "") + " " + (current.attributes.get("id") or "")).lower()
        if any(word in marker for word in SKIP_CONTAINERS):
            return True
        current = current.parent
    return False


def _text_signal(tree: LexborHTMLParser) -> Signal | None:
    root = tree.css_first("main") or tree.css_first('[itemtype*="schema.org/Product"]') or tree.body
    if root is None:
        return None
    root = LexborHTMLParser(root.html or "")
    for junk in root.css("script, style, noscript, header, footer, nav, aside, template"):
        junk.decompose()
    text = re.sub(r"\s+", " ", (root.body.text(separator=" ") if root.body else "")).lower()

    out_hit = next((p for p in OUT_PHRASES if p in text), None)
    for phrase in OUT_PHRASES:          # „nicht lieferbar“ darf nicht als „lieferbar“ zählen
        text = text.replace(phrase, " ")
    pre_hit = next((p for p in PRE_PHRASES if p in text), None)
    in_hit = next((p for p in IN_TEXT_PHRASES if p in text), None)

    if pre_hit and not out_hit:
        return Signal("text", PREORDER, 1, f"„{pre_hit}“ gefunden")
    if out_hit and not in_hit:
        return Signal("text", SOLDOUT, 1, f"„{out_hit}“ gefunden")
    if in_hit and not out_hit:
        return Signal("text", AVAILABLE, 1, f"„{in_hit}“ gefunden")
    return None


# --------------------------------------------------------------------------- Hilfen

def _status_word(status: str) -> str:
    return {AVAILABLE: "verfügbar", PREORDER: "vorbestellbar", SOLDOUT: "ausverkauft"}.get(status, status)


def _network_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "Der Shop hat nicht rechtzeitig geantwortet"
    if isinstance(exc, httpx.ConnectError):
        return "Keine Verbindung zum Shop (Internet oder Adresse prüfen)"
    return f"Netzwerkfehler: {type(exc).__name__}"
