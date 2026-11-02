import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from restockradar import search
from restockradar.db import Database
from restockradar.fetcher import Fetcher
from restockradar.monitor import Monitor
from restockradar.notify import Notifier
from restockradar.web import create_app


def run(coro):
    return asyncio.run(coro)


def item(title, url="https://s.test/p/x", price=None, status="unknown"):
    return search.Item(url=url, title=title, price=price, status=status)


def crit(**kw):
    return {"query": "Prismatische Entwicklungen | Prismatic Evolutions", "ptype": "egal", "lang": "egal",
            "max_price": None, "exclude": "", **kw}


# --------------------------------------------------------------------------- Filter

def test_matches_alternatives_umlauts_and_slug():
    assert search.matches(item("Pokémon – Prismatische Entwicklungen Display (DE)"), crit())
    assert search.matches(item("Prismatic Evolutions Booster Bundle EN"), crit())
    # Set-Name steht nur im Link (typisch für JTL-Shops)
    assert search.matches(item("Aquana-ex 023/131", "https://s.test/Aquana-ex-Prismatische-Entwicklungen-Karte"), crit())
    assert not search.matches(item("Quarter Century Bonanza Display"), crit())
    assert search.matches(item("Glurak Sammelkoffer"), {**crit(), "query": "glurak"})
    assert search.matches(item("Pokémon Karmesin & Purpur Glänzendes Schicksal"), {**crit(), "query": "glaenzendes schicksal"})


def test_type_filters():
    display = crit(ptype="display")
    assert search.matches(item("Prismatische Entwicklungen Booster Display"), display)
    assert not search.matches(item("Prismatische Entwicklungen Booster Bundle"), display)
    assert not search.matches(item("Prismatische Entwicklungen Display leer (Acryl-Case)"), display)
    etb = crit(ptype="etb")
    assert search.matches(item("Prismatische Entwicklungen Top-Trainer-Box"), etb)
    assert search.matches(item("Prismatic Evolutions Elite Trainer Box"), etb)
    booster = crit(ptype="booster")
    assert search.matches(item("Prismatische Entwicklungen Booster"), booster)
    assert not search.matches(item("Prismatische Entwicklungen Booster Display"), booster)


def test_language_price_and_exclude():
    de = crit(lang="de")
    assert search.matches(item("Prismatische Entwicklungen Display - DE"), de)
    assert search.matches(item("Prismatische Entwicklungen Display"), de)        # unmarkiert = deutsch
    assert not search.matches(item("Prismatic Evolutions Display - EN"), de)
    assert search.matches(item("Prismatic Evolutions Display (englisch)"), crit(lang="en"))
    assert not search.matches(item("Prismatische Entwicklungen Display"), crit(lang="en"))
    assert not search.matches(item("Prismatische Entwicklungen Display", price=399.0), crit(max_price=250))
    assert search.matches(item("Prismatische Entwicklungen Display", price=None), crit(max_price=250))
    assert not search.matches(item("Prismatische Entwicklungen Nachtara PSA 10"), crit(exclude="psa, gradiert"))


def test_shop_query_adds_type_word():
    assert search.shop_query("Prismatische Entwicklungen", "display") == "Prismatische Entwicklungen display"
    assert search.shop_query("Prismatische Display", "display") == "Prismatische Display"
    assert search.shop_query("Glurak", "egal") == "Glurak"


# --------------------------------------------------------------------------- Shop-Antworten

SHOPIFY_JSON = {"resources": {"results": {"products": [
    {"title": "Pokémon - Prismatische Entwicklungen Display - DE", "url": "/products/pe-display?_pos=1&_psq=x",
     "price": "249.90", "available": False, "image": "//cdn.shopify.com/pe.jpg"},
    {"title": "Pokémon - Prismatic Evolutions Booster Bundle - EN", "url": "/products/pe-bundle?_pos=2",
     "price": "109.90", "available": True, "image": "https://cdn.shopify.com/b.jpg"},
]}}}

WOO_JSON = [{"name": "Prismatische Entwicklungen &#8211; Top-Trainer-Box", "permalink": "https://woo.test/produkt/ttb/",
             "prices": {"price": "6999", "currency_code": "EUR", "currency_minor_unit": 2},
             "is_in_stock": True, "is_purchasable": True, "images": [{"src": "https://woo.test/ttb.jpg"}]}]

JTL_HTML = """<html><body><div class="product-list">
<div class="product-wrapper" itemscope itemtype="https://schema.org/Product">
  <div class="productbox"><div class="ribbon">Auf Lager</div>
  <div class="productbox-title"><a href="/Prismatische-Entwicklungen-Mini-Tin-Display">Mini Tin Display (deutsch)</a></div>
  <meta itemprop="name" content="Prismatische Entwicklungen Mini Tin Display (deutsch)">
  <meta itemprop="url" content="https://jtl.test/Prismatische-Entwicklungen-Mini-Tin-Display">
  <span itemprop="price" content="449.90"></span><meta itemprop="priceCurrency" content="EUR">
  <img src="/media/tin.jpg"></div></div>
<div class="product-wrapper" itemscope itemtype="https://schema.org/Product">
  <div class="productbox"><div class="ribbon">Ausverkauft</div>
  <meta itemprop="name" content="Quarter Century Bonanza Display (deutsch)">
  <meta itemprop="url" content="https://jtl.test/Yugioh-Quarter-Century">
  <span itemprop="price" content="179.90"></span></div></div>
</div></body></html>"""

CLASS_HTML = """<ul class="products">
<li class="product"><a href="/p/etb"><img data-src="/i/etb.jpg"><h2 class="product-title">Prismatische Entwicklungen Top-Trainer-Box</h2></a>
  <span class="price">69,99 €</span><button disabled>Ausverkauft</button></li>
<li class="product"><a href="/p/bundle"><h2 class="product-title">Prismatische Entwicklungen Booster-Bundle</h2></a>
  <span class="price">39,99&nbsp;€</span><a class="button" href="/cart/?add=5">In den Warenkorb</a></li>
<li class="product"><a href="#">Platzhalter</a></li>
</ul>"""


def test_parse_shopify_and_woo():
    items = search._parse_shopify(json.dumps(SHOPIFY_JSON), "https://shop.test")
    assert items[0].url == "https://shop.test/products/pe-display"
    assert items[0].price == 249.9 and items[0].status == "soldout"
    assert items[0].image == "https://cdn.shopify.com/pe.jpg"
    assert items[1].status == "available"

    woo = search._parse_woo(json.dumps(WOO_JSON))
    assert woo[0].title == "Prismatische Entwicklungen – Top-Trainer-Box"
    assert woo[0].price == 69.99 and woo[0].status == "available"


def test_parse_cards_microdata_and_classes():
    jtl = search.parse_cards(JTL_HTML, "https://jtl.test/?qs=x")
    assert [i.title for i in jtl] == ["Prismatische Entwicklungen Mini Tin Display (deutsch)",
                                      "Quarter Century Bonanza Display (deutsch)"]
    assert jtl[0].status == "available" and jtl[0].price == 449.9
    assert jtl[0].image == "https://jtl.test/media/tin.jpg"
    assert jtl[1].status == "soldout"

    cards = search.parse_cards(CLASS_HTML, "https://woo.test/?s=x")
    assert [(i.url, i.status, i.price) for i in cards] == [
        ("https://woo.test/p/etb", "soldout", 69.99),
        ("https://woo.test/p/bundle", "available", 39.99)]


def shop_transport(routes: dict):
    def handler(request: httpx.Request):
        value = routes.get(str(request.url))
        if value is None:
            return httpx.Response(404, text="nicht da")
        if isinstance(value, httpx.Response):
            return value
        if isinstance(value, (dict, list)):
            return httpx.Response(200, json=value)
        return httpx.Response(200, text=value)
    return httpx.MockTransport(handler)


def test_detect_shop_variants():
    shopify_home = '<html><head><meta property="og:site_name" content="Kartenladen"></head><script src="https://cdn.shopify.com/x.js"></script></html>'
    jtl_home = ('<html><title>Spiele &amp; Karten | GTTG</title><form action="/" method="get">'
                '<input type="hidden" name="jtl_token" value="abc"><input type="text" name="qs"></form></html>')
    woo_home = '<html><body class="woocommerce"></body></html>'
    fetcher = Fetcher(min_gap=0, transport=shop_transport({
        "https://shopify.test/": shopify_home, "https://jtl.test/": jtl_home, "https://woo.test/": woo_home}))
    shop = run(search.detect_shop("shopify.test/products/x", fetcher))
    assert shop.mode == "shopify" and shop.name == "Kartenladen" and "suggest.json" in shop.search_url
    jtl = run(search.detect_shop("https://jtl.test", fetcher))
    assert jtl.mode == "html" and jtl.search_url == "https://jtl.test/?qs={q}" and jtl.name == "GTTG"
    woo = run(search.detect_shop("https://woo.test", fetcher))
    assert woo.mode == "woo" and "wc/store/v1/products" in woo.search_url


# --------------------------------------------------------------------------- Überwachung

class FakeShop:
    def __init__(self):
        self.products = [{"title": "Prismatische Entwicklungen Display - DE", "url": "/products/pe-display",
                          "price": "249.90", "available": False}]
        self.throttle = False

    def handler(self, request: httpx.Request):
        if self.throttle:
            return httpx.Response(429, text="slow", headers={"retry-after": "60"})
        if request.url.path == "/search/suggest.json":
            return httpx.Response(200, json={"resources": {"results": {"products": self.products}}})
        if request.url.path.startswith("/products/"):
            return httpx.Response(200, text="<html><body><main><h1>Display</h1>"
                                  "<button class='single_add_to_cart_button' disabled>In den Warenkorb</button></main></body></html>")
        return httpx.Response(404)


def setup_monitor(tmp_path):
    shop, sent = FakeShop(), []

    def notify(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    db = Database(tmp_path / "t.db")
    db.update_channel("ntfy", enabled=True, topic="restock-test")
    shop_id = db.add_shop(name="Kartenladen", base="https://shop.test", platform="Shopify", mode="shopify",
                          search_url="https://shop.test/search/suggest.json?q={q}&resources[type]=product")
    search_id = db.add_search(query="Prismatische Entwicklungen", ptype="display", lang="de", priority="laut")
    monitor = Monitor(db, Fetcher(min_gap=0, transport=httpx.MockTransport(shop.handler)),
                      Notifier(transport=httpx.MockTransport(notify)))
    return db, monitor, shop, sent, shop_id, search_id


async def tick(monitor, db, search_id):
    await monitor.check_search(db.search(search_id))
    while monitor._tasks:
        await asyncio.gather(*list(monitor._tasks))


def test_search_baseline_then_new_and_restock(tmp_path):
    db, monitor, shop, sent, shop_id, search_id = setup_monitor(tmp_path)

    async def scenario():
        await tick(monitor, db, search_id)                       # erster Durchlauf: still
        assert sent == [] and len(db.hits(search_id)) == 1
        assert json.loads(db.search(search_id)["baseline"]) == [shop_id]

        shop.products.append({"title": "Prismatische Entwicklungen Booster Display (deutsch)",
                              "url": "/products/pe-display-2", "price": "239.90", "available": True})
        shop.products.append({"title": "Prismatic Evolutions Display - EN", "url": "/products/pe-en",
                              "price": "199.90", "available": True})       # falsche Sprache → ignorieren
        await tick(monitor, db, search_id)
        assert len(sent) == 1
        assert sent[0]["title"].startswith("🆕 Neu & verfügbar: Prismatische Entwicklungen Booster Display")
        assert sent[0]["priority"] == 5 and "Kartenladen" in sent[0]["message"]

        shop.products[0]["available"] = True                      # alter Treffer wieder da
        await tick(monitor, db, search_id)
        assert len(sent) == 2 and sent[1]["title"].startswith("🟢 Wieder verfügbar")
        assert len(db.hits(search_id)) == 2

    run(scenario())


def test_search_auto_watch_and_shop_pause(tmp_path):
    db, monitor, shop, sent, shop_id, search_id = setup_monitor(tmp_path)
    db.update_search(search_id, auto_watch=1)

    async def scenario():
        await tick(monitor, db, search_id)
        shop.products.append({"title": "Prismatische Entwicklungen Display (DE) Vorbestellung",
                              "url": "/products/neu", "price": "229.90", "available": False})
        await tick(monitor, db, search_id)
        hit = next(h for h in db.hits(search_id) if h["url"].endswith("/neu"))
        assert hit["watch_id"] and db.watch(hit["watch_id"])["url"] == "https://shop.test/products/neu"
        assert sent[0]["priority"] == 3                           # neu, aber noch nicht kaufbar → leise

        shop.throttle = True
        await tick(monitor, db, search_id)
        paused = db.shop(shop_id)
        assert paused["status"] == "throttled" and paused["paused_until"] > 0

    run(scenario())


# --------------------------------------------------------------------------- API

def test_api_shop_search_and_hit_watch(tmp_path):
    shop = FakeShop()
    home = '<script src="https://cdn.shopify.com/x.js"></script>'

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text=home)
        return shop.handler(request)

    app = create_app(tmp_path, fetcher=Fetcher(min_gap=0, transport=httpx.MockTransport(handler)),
                     notifier=Notifier(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                     start_monitor=False)
    with TestClient(app) as client:
        assert client.post("/api/searches", json={"query": "x"}).status_code == 400   # noch kein Shop
        added = client.post("/api/shops", json={"url": "https://shop.test/collections/pokemon"}).json()
        assert added["shop"]["mode"] == "shopify" and added["test"]["count"] == 1
        assert client.post("/api/shops", json={"url": "shop.test"}).status_code == 400  # doppelt

        assert client.post("/api/searches", json={"query": "  "}).status_code == 400
        s = client.post("/api/searches", json={"query": "Prismatische Entwicklungen", "ptype": "display"}).json()
        s = client.post(f"/api/searches/{s['id']}/check").json()
        assert s["hit_count"] == 1 and s["hits"][0]["shop_name"] == "shop.test"

        watch = client.post(f"/api/hits/{s['hits'][0]['id']}/watch").json()
        assert watch["url"] == "https://shop.test/products/pe-display"
        state = client.get("/api/state").json()
        assert state["searches"][0]["hits"][0]["watch_id"] == watch["id"]
        assert "display" in state["search_meta"]["types"]

        changed = client.patch(f"/api/searches/{s['id']}", json={"lang": "en"}).json()
        assert changed["hit_count"] == 0 and changed["baseline"] == "[]"
