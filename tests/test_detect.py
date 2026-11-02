import asyncio
import json

import httpx
import pytest

from restockradar import detect
from restockradar.fetcher import Fetcher


def run(coro):
    return asyncio.run(coro)


def fetcher_for(routes: dict):
    def handler(request: httpx.Request):
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404, text="nicht da")
        value = routes[key]
        if isinstance(value, httpx.Response):
            return value
        if isinstance(value, (dict, list)):
            return httpx.Response(200, json=value)
        return httpx.Response(200, text=value, headers={"content-type": "text/html"})
    return Fetcher(min_gap=0, transport=httpx.MockTransport(handler))


def page(body: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body><main>{body}</main></body></html>"


def jsonld(availability: str) -> str:
    data = {"@context": "https://schema.org", "@type": "Product", "name": "Elite-Trainer-Box",
            "image": "/img/etb.jpg",
            "offers": {"@type": "Offer", "price": "59.99", "priceCurrency": "EUR",
                       "availability": f"https://schema.org/{availability}"}}
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def test_jsonld_soldout_and_product_info():
    url = "https://shop.test/etb"
    res = run(detect.analyze(url, fetcher_for({url: page("<h1>ETB</h1>", jsonld("OutOfStock"))})))
    assert res.status == detect.SOLDOUT
    assert res.method == "jsonld"
    assert res.rating == "sehr gut"
    assert res.name == "Elite-Trainer-Box"
    assert res.price == 59.99
    assert res.image == "https://shop.test/img/etb.jpg"


def test_jsonld_preorder():
    url = "https://shop.test/etb"
    res = run(detect.analyze(url, fetcher_for({url: page("", jsonld("PreOrder"))})))
    assert res.status == detect.PREORDER


def test_jsonld_graph_and_related_products_ignored():
    data = {"@graph": [
        {"@type": "ItemList", "itemListElement": [
            {"@type": "Product", "name": "Andere", "offers": {"availability": "InStock"}}]},
        {"@type": "Product", "name": "Display", "offers": [
            {"@type": "AggregateOffer", "offers": [{"availability": "http://schema.org/OutOfStock"}]}]},
    ]}
    url = "https://shop.test/display"
    html = page("", f'<script type="application/ld+json">{json.dumps(data)}</script>')
    res = run(detect.analyze(url, fetcher_for({url: html})))
    assert res.status == detect.SOLDOUT
    assert res.name == "Display"


def test_conflict_prefers_cautious_signal():
    # Google-Daten sagen dauerhaft „InStock“, der echte Button ist aber deaktiviert.
    url = "https://shop.test/etb"
    body = '<form action="/cart/add"><button type="submit" disabled>Ausverkauft</button></form>'
    res = run(detect.analyze(url, fetcher_for({url: page(body, jsonld("InStock"))})))
    assert res.status == detect.SOLDOUT
    assert res.method == "button"
    assert res.note


def test_known_button_available_and_disabled():
    url = "https://shop.test/a"
    on = page('<button class="single_add_to_cart_button">In den Warenkorb</button>')
    off = page('<button class="single_add_to_cart_button" disabled>In den Warenkorb</button>')
    assert run(detect.analyze(url, fetcher_for({url: on}))).status == detect.AVAILABLE
    assert run(detect.analyze(url, fetcher_for({url: off}))).status == detect.SOLDOUT


def test_generic_button_skips_recommendations():
    url = "https://shop.test/a"
    body = ('<div class="product"><button>Benachrichtigen, wenn verfügbar</button></div>'
            '<div class="related-products"><button>In den Warenkorb</button></div>')
    res = run(detect.analyze(url, fetcher_for({url: page(body)})))
    assert res.status == detect.SOLDOUT
    assert res.rating == "eingeschränkt"


def test_text_signal_handles_negation():
    url = "https://shop.test/a"
    res = run(detect.analyze(url, fetcher_for({url: page("<p>Dieser Artikel ist leider nicht lieferbar.</p>")})))
    assert res.status == detect.SOLDOUT
    assert res.method == "text"


def test_meta_tag():
    url = "https://shop.test/a"
    head = '<meta property="product:availability" content="in stock"><meta property="og:title" content="Booster">'
    res = run(detect.analyze(url, fetcher_for({url: page("", head)})))
    assert res.status == detect.AVAILABLE
    assert res.name == "Booster"


def test_nothing_found():
    url = "https://shop.test/a"
    res = run(detect.analyze(url, fetcher_for({url: page("<div id=app></div>")})))
    assert res.status == detect.UNKNOWN
    assert res.rating == "nicht möglich"


def test_shopify_variant_and_cart_link():
    url = "https://tcg.test/collections/pokemon/products/display?variant=222"
    html = page('<script src="https://cdn.shopify.com/s/x.js"></script>'
                '<form action="/cart/add"><button type="submit" name="add">In den Warenkorb</button></form>')
    product = {"title": "Display", "available": True, "price": 14999, "featured_image": "//cdn.shopify.com/d.jpg",
               "variants": [{"id": 111, "public_title": "Deutsch", "available": True, "price": 14999},
                            {"id": 222, "public_title": "Englisch", "available": False, "price": 13999}]}
    routes = {url: html, "https://tcg.test/products/display.js": product}
    res = run(detect.analyze(url, fetcher_for(routes)))
    assert res.platform == "Shopify"
    assert res.method == "shopify"
    assert res.status == detect.SOLDOUT           # gewählte Variante „Englisch“ ist weg
    assert res.variant_id == "222" and res.price == 139.99
    assert res.cart_url == "https://tcg.test/cart/222:1"
    assert res.image == "https://cdn.shopify.com/d.jpg"
    assert len(res.variants) == 2


def test_shopify_fast_path_only_fetches_json():
    url = "https://tcg.test/products/display"
    product = {"title": "Display", "available": True, "variants": [{"id": 1, "available": True, "price": 100}]}
    res = run(detect.analyze(url, fetcher_for({"https://tcg.test/products/display.js": product}), method="shopify"))
    assert res.status == detect.AVAILABLE
    assert res.cart_url == "https://tcg.test/cart/1:1"


def test_shopify_keeps_locale_prefix():
    assert detect._shopify_json_url("https://s.test/en/products/box?variant=1") == "https://s.test/en/products/box.js"
    assert detect._shopify_json_url("https://s.test/de-at/collections/x/products/box") == "https://s.test/de-at/products/box.js"
    assert detect._shopify_json_url("https://s.test/collections/x/products/box.js") == "https://s.test/products/box.js"


SHOPIFY_PAGE = page(
    '<script src="https://cdn.shopify.com/s/x.js"></script>'
    '<form action="/cart/add"><select name="id">'
    '<option value="111" selected>Deutsch - 149,99 €</option>'
    '<option value="222" disabled>Englisch - Ausverkauft</option></select>'
    '<button type="submit" name="add">In den Warenkorb</button></form>',
    '<meta property="og:site_name" content="TCG Laden">'
    '<script type="application/ld+json">' + json.dumps({
        "@type": "Product", "name": "Display",
        "offers": [
            {"@type": "Offer", "url": "https://tcg.test/products/display?variant=111", "price": "149.99",
             "priceCurrency": "EUR", "availability": "http://schema.org/InStock"},
            {"@type": "Offer", "url": "https://tcg.test/products/display?variant=222", "price": "139.99",
             "priceCurrency": "EUR", "availability": "http://schema.org/OutOfStock"}]}) + "</script>")


def throttled_json_fetcher(calls):
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith(".js"):
            return httpx.Response(429, text="Too many requests", headers={"retry-after": "60"})
        return httpx.Response(200, text=SHOPIFY_PAGE)
    return Fetcher(min_gap=0, transport=httpx.MockTransport(handler))


def test_shopify_throttled_json_falls_back_to_page_form():
    calls = []
    res = run(detect.analyze("https://tcg.test/products/display", throttled_json_fetcher(calls)))
    assert res.status == detect.AVAILABLE and res.method == "jsonld"
    assert [v["id"] for v in res.variants] == ["111", "222"]
    assert res.variants[1]["available"] is False
    assert res.cart_url == "https://tcg.test/cart/111:1"


def test_jsonld_picks_offer_of_chosen_variant():
    calls = []
    url = "https://tcg.test/products/display?variant=222"
    res = run(detect.analyze(url, throttled_json_fetcher(calls)))
    assert res.status == detect.SOLDOUT and res.method == "jsonld"
    assert res.price == 139.99 and res.variant_id == "222" and res.variant_title == "Englisch"
    assert res.cart_url == "https://tcg.test/cart/222:1"


def test_shopify_fast_path_throttled_uses_page_once():
    calls = []
    res = run(detect.analyze("https://tcg.test/products/display", throttled_json_fetcher(calls), method="shopify"))
    assert res.status == detect.AVAILABLE and res.method == "jsonld"
    assert calls == ["https://tcg.test/products/display.js", "https://tcg.test/products/display"]


def test_redirect_to_homepage_is_not_judged():
    def handler(request):
        if request.url.path == "/products/etb":
            return httpx.Response(302, headers={"location": "https://shop.test/de"})
        return httpx.Response(200, text=page('<button>Jetzt vorbestellen</button>'))
    res = run(detect.analyze("https://shop.test/products/etb", Fetcher(min_gap=0, transport=httpx.MockTransport(handler))))
    assert res.status == detect.ERROR and "Startseite" in res.reason
    assert not detect._redirected_home("https://s.test/products/etb", "https://s.test/de/products/etb")


def test_name_prefers_heading_when_title_is_shop_name():
    url = "https://shop.test/a"
    head = '<meta property="og:title" content="TCG Laden - Shop"><meta property="og:site_name" content="TCG Laden">'
    res = run(detect.analyze(url, fetcher_for({url: page("<h1>Top-Trainer-Box</h1><p>Ausverkauft</p>", head)})))
    assert res.name == "Top-Trainer-Box"


def test_blocked_and_queue_and_404():
    url = "https://shop.test/a"
    blocked = httpx.Response(403, text="<html>Just a moment...</html>", headers={"server": "cloudflare"})
    res = run(detect.analyze(url, fetcher_for({url: blocked})))
    assert res.status == detect.BLOCKED and "Cloudflare" in res.reason

    res = run(detect.analyze("https://shop.test/missing", fetcher_for({})))
    assert res.status == detect.ERROR and "404" in res.reason

    def queue_handler(request):
        if request.url.host == "shop.test":
            return httpx.Response(302, headers={"location": "https://shop.queue-it.net/?c=shop"})
        return httpx.Response(200, text="Sie sind in der Warteschlange")
    queue_fetcher = Fetcher(min_gap=0, transport=httpx.MockTransport(queue_handler))
    assert run(detect.analyze(url, queue_fetcher)).status == detect.QUEUE


@pytest.mark.parametrize("value,expected", [
    ("https://schema.org/InStock", detect.AVAILABLE),
    ("http://schema.org/LimitedAvailability", detect.AVAILABLE),
    ("PreOrder", detect.PREORDER),
    ("BackOrder", detect.PREORDER),
    ("out of stock", detect.SOLDOUT),
    ("Discontinued", detect.SOLDOUT),
    ("irgendwas", None),
])
def test_schema_status(value, expected):
    assert detect.schema_status(value) == expected
