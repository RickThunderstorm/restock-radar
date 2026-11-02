"""Neuheiten-Radar: Kategorie-Seiten, breite Interessen, Auto-Übernahme, Erscheinungstermine."""
import asyncio
import json
import sqlite3

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


def card(name, url, status_text, extra=""):
    return (f'<div class="product-wrapper" itemscope itemtype="https://schema.org/Product">'
            f'<meta itemprop="name" content="{name}"><meta itemprop="url" content="{url}">'
            f'<div class="productbox">{status_text} {extra}</div></div>')


PAGE1 = ('<html><head><link rel="next" href="/pokemon-displays?page=2"></head><body>'
         + card("Pokemon Ewige Rivalen Booster Display (deutsch)", "https://kat.test/ewige-rivalen-display", "Ausverkauft")
         + card("Yu-Gi-Oh! Quarter Century Bonanza Display (deutsch)", "https://kat.test/yugioh-display", "Ausverkauft")
         + "</body></html>")
PAGE2 = ('<html><body>'
         + card("Pokemon Delta Herrschaft Booster Display (deutsch)", "https://kat.test/delta-display",
                "Vorverkauf", "Erscheint am 30.01.2027")
         + card("Pokemon Weiße Flammen Booster Display (deutsch)", "https://kat.test/weisse-flammen-display", "Auf Lager")
         + "</body></html>")
PRODUCT = ("<html><body><main><h1>{name}</h1><p>{text}</p>"
           "<button class='single_add_to_cart_button' {disabled}>{button}</button></main></body></html>")


class CategoryShop:
    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request):
        self.calls.append(str(request.url))
        path, page = request.url.path, request.url.params.get("page")
        if path == "/pokemon-displays":
            return httpx.Response(200, text=PAGE2 if page == "2" else PAGE1)
        if path == "/delta-display":
            return httpx.Response(200, text=PRODUCT.format(name="Delta Herrschaft Display", text="Erscheint am 30.01.2027",
                                                           disabled="", button="Jetzt vorbestellen"))
        if path in ("/ewige-rivalen-display", "/weisse-flammen-display"):
            return httpx.Response(200, text=PRODUCT.format(name="Display", text="", disabled="disabled",
                                                           button="In den Warenkorb"))
        return httpx.Response(404)


def test_category_pages_follow_next_link_and_read_release():
    shop = CategoryShop()
    items = run(search.fetch_category("https://kat.test/pokemon-displays",
                                      Fetcher(min_gap=0, transport=httpx.MockTransport(shop.handler))))
    assert [i.url.rsplit("/", 1)[1] for i in items] == ["ewige-rivalen-display", "yugioh-display",
                                                        "delta-display", "weisse-flammen-display"]
    delta = items[2]
    assert delta.status == "preorder" and delta.release == "30.01.2027" and delta.release_date == "2027-01-30"


def test_shopify_collection_json():
    data = {"products": [
        {"title": "Delta Herrschaft Top-Trainer-Box", "handle": "delta-ttb", "images": [{"src": "https://cdn/x.jpg"}],
         "variants": [{"price": "79.99", "available": False}, {"price": "74.99", "available": True}]}]}

    def handler(request):
        assert request.url.path == "/collections/pokemon/products.json"
        return httpx.Response(200, json=data)

    items = run(search.fetch_category("https://shopify.test/collections/pokemon/",
                                      Fetcher(min_gap=0, transport=httpx.MockTransport(handler))))
    assert items[0].url == "https://shopify.test/products/delta-ttb"
    assert items[0].status == "available" and items[0].price == 74.99


def test_empty_shopify_collection_json_falls_back_to_page():
    def handler(request):
        if request.url.path.endswith("products.json"):
            return httpx.Response(200, json={"products": []})
        return httpx.Response(200, text=PAGE2.replace("kat.test", "shopify.test"))

    items = run(search.fetch_category("https://shopify.test/collections/pokemon",
                                      Fetcher(min_gap=0, transport=httpx.MockTransport(handler))))
    assert len(items) == 2


def test_broad_interest_without_query_skips_other_tcgs():
    broad = {"query": "", "ptype": "display", "lang": "de", "max_price": None, "exclude": ""}
    assert search.matches(search.Item("https://s/x", "Pokemon Ewige Rivalen Booster Display (deutsch)"), broad)
    assert search.matches(search.Item("https://s/x", "Karmesin & Purpur Weiße Flammen Display"), broad)
    assert not search.matches(search.Item("https://s/x", "Yu-Gi-Oh! Quarter Century Display"), broad)
    assert not search.matches(search.Item("https://s/x", "One Piece OP-12 Booster Display"), broad)
    assert search.search_label(broad) == "Alle Pokémon · Display / Booster-Box"


def setup(tmp_path, **search_values):
    shop, sent = CategoryShop(), []

    def notify(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    db = Database(tmp_path / "t.db")
    db.update_channel("ntfy", enabled=True, topic="restock-test")
    db.set_setting("shop_budget", 0)        # hier geht es um die Übernahme, nicht um den Schonmodus
    search_id = db.add_search(query="", ptype="display", lang="de", categories="https://kat.test/pokemon-displays",
                              **search_values)
    monitor = Monitor(db, Fetcher(min_gap=0, transport=httpx.MockTransport(shop.handler)),
                      Notifier(transport=httpx.MockTransport(notify)))
    return db, monitor, shop, sent, search_id


async def tick(monitor, db, search_id):
    await monitor.check_search(db.search(search_id))
    while monitor._tasks:
        await asyncio.gather(*list(monitor._tasks))


def test_auto_watch_everything_not_buyable_with_cap(tmp_path):
    db, monitor, shop, sent, search_id = setup(tmp_path, auto_watch=search.AUTO_UNAVAILABLE, max_auto=1)

    async def scenario():
        await tick(monitor, db, search_id)
        hits = {h["url"].rsplit("/", 1)[1]: h for h in db.hits(search_id)}
        assert set(hits) == {"ewige-rivalen-display", "delta-display", "weisse-flammen-display"}   # ohne Yu-Gi-Oh
        assert sent == []                                       # erster Durchlauf: still
        watches = db.watches()
        assert len(watches) == 1 and watches[0]["auto"] == 1    # Obergrenze 1
        assert watches[0]["url"].endswith("/delta-display")     # Kommendes (mit Termin) zuerst
        assert watches[0]["interval"] == 180
        assert not hits["weisse-flammen-display"]["watch_id"]   # kaufbar → nicht übernommen

        db.update_search(search_id, max_auto=5)
        await tick(monitor, db, search_id)
        watched = {w["url"].rsplit("/", 1)[1]: w for w in db.watches()}
        assert set(watched) == {"ewige-rivalen-display", "delta-display"}
        assert watched["delta-display"]["release"] == "30.01.2027"
        assert watched["delta-display"]["stock"] == "preorder"
        assert watched["delta-display"]["name"] == "Delta Herrschaft Display"

    run(scenario())


def test_new_listing_message_mentions_release(tmp_path):
    db, monitor, shop, sent, search_id = setup(tmp_path)

    async def scenario():
        global PAGE2
        await tick(monitor, db, search_id)
        original = PAGE2
        try:
            PAGE2 = original.replace("</body>", card("Pokemon Neues Set Booster Display (deutsch)",
                                                      "https://kat.test/neu-display", "Vorverkauf",
                                                      "Release: KW 9/2027") + "</body>")
            await tick(monitor, db, search_id)
        finally:
            PAGE2 = original
        assert len(sent) == 1
        assert sent[0]["title"] == "🆕 Neu & vorbestellbar: Pokemon Neues Set Booster Display (deutsch)"
        assert "Erscheint laut Shop: KW 9/2027" in sent[0]["message"]
        assert "Alle Pokémon · Display / Booster-Box" in sent[0]["message"]

    run(scenario())


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "alt.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE watches (id INTEGER PRIMARY KEY, url TEXT NOT NULL, created REAL);"
                       "INSERT INTO watches (url, created) VALUES ('https://alt.test/p', 1);"
                       "CREATE TABLE searches (id INTEGER PRIMARY KEY, query TEXT NOT NULL, created REAL);")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE shops (id INTEGER PRIMARY KEY, name TEXT, base TEXT NOT NULL UNIQUE)")
    conn.execute("INSERT INTO shops (name, base) VALUES ('Sammelkarten kaufen bei Card-Corner', 'https://www.card-corner.de')")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.shops()[0]["name"] == "card-corner.de"
    watch = db.watches()[0]
    assert watch["url"] == "https://alt.test/p" and "release" in watch and watch["auto"] == 0
    search_id = db.add_search(query="x", categories="https://k.test/a")
    assert db.search(search_id)["max_auto"] == 30


def test_api_broad_search_validation(tmp_path):
    app = create_app(tmp_path, fetcher=Fetcher(min_gap=0, transport=httpx.MockTransport(CategoryShop().handler)),
                     notifier=Notifier(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                     start_monitor=False)
    with TestClient(app) as client:
        too_broad = client.post("/api/searches", json={"query": "", "ptype": "egal"})
        assert too_broad.status_code == 400 and "zu breit" in too_broad.json()["detail"]
        bad_link = client.post("/api/searches", json={"categories": "keine url hier"})
        assert bad_link.status_code == 400
        ok = client.post("/api/searches", json={"query": "", "ptype": "display", "lang": "de", "auto_watch": 2,
                                                "categories": "kat.test/pokemon-displays\n\nkat.test/pokemon-displays"}).json()
        assert ok["category_list"] == ["https://kat.test/pokemon-displays"]
        assert ok["label"] == "Alle Pokémon · Display / Booster-Box"
        checked = client.post(f"/api/searches/{ok['id']}/check").json()
        assert checked["hit_count"] == 3 and checked["hits"][0]["shop_name"] == "kat.test"
        assert client.patch(f"/api/searches/{ok['id']}", json={"auto_watch": 7}).status_code == 400
