import asyncio
import json

import httpx

from restockradar.db import Database
from restockradar.fetcher import Fetcher
from restockradar.monitor import Monitor, format_price
from restockradar.notify import Message, Notifier

URL = "https://tcg.test/products/display"
JS = "https://tcg.test/products/display.js"


class Shop:
    """Simulierter Shopify-Shop, dessen Lagerstand sich im Test umschalten lässt."""

    def __init__(self):
        self.available = False
        self.blocked = False

    def handler(self, request: httpx.Request):
        if self.blocked:
            return httpx.Response(429, text="slow down", headers={"retry-after": "120"})
        if str(request.url) == JS:
            return httpx.Response(200, json={"title": "Display", "available": self.available,
                                             "variants": [{"id": 7, "available": self.available, "price": 14999}]})
        return httpx.Response(404)


def setup(tmp_path):
    shop, sent = Shop(), []

    def notify_handler(request: httpx.Request):
        sent.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    db = Database(tmp_path / "t.db")
    db.update_channel("ntfy", enabled=True, topic="restock-test")
    monitor = Monitor(db, Fetcher(min_gap=0, transport=httpx.MockTransport(shop.handler)),
                      Notifier(transport=httpx.MockTransport(notify_handler)))
    watch_id = db.add_watch(url=URL, name="Display", shop="tcg.test", method="shopify",
                            stock="soldout", status="soldout", interval=60, priority="laut")
    return db, monitor, shop, sent, watch_id


async def check(monitor, db, watch_id):
    await monitor.check(db.watch(watch_id))
    await asyncio.gather(*list(monitor._tasks))
    return db.watch(watch_id)


def test_restock_triggers_loud_notification_once(tmp_path):
    db, monitor, shop, sent, watch_id = setup(tmp_path)

    async def scenario():
        w = await check(monitor, db, watch_id)
        assert w["stock"] == "soldout" and not sent

        shop.available = True
        w = await check(monitor, db, watch_id)
        assert w["stock"] == "available"
        assert w["cart_url"] == "https://tcg.test/cart/7:1"
        assert len(sent) == 1
        url, payload = sent[0]
        assert url == "https://ntfy.sh/"
        assert payload["priority"] == 5 and payload["topic"] == "restock-test"
        assert payload["title"].endswith("Verfügbar: Display")
        assert any(a["label"] == "Direkt zur Kasse" for a in payload["actions"])

        await check(monitor, db, watch_id)          # bleibt verfügbar → keine zweite Nachricht
        assert len(sent) == 1

        shop.available = False
        await check(monitor, db, watch_id)
        kinds = [e["kind"] for e in db.events()]
        assert "soldout" in kinds and "sent" in kinds

        shop.available = True                       # Flackern innerhalb der Sperrzeit
        await check(monitor, db, watch_id)
        assert len(sent) == 1

    asyncio.run(scenario())


def test_block_backs_off_and_warns(tmp_path):
    db, monitor, shop, sent, watch_id = setup(tmp_path)
    shop.blocked = True

    async def scenario():
        last = None
        for _ in range(5):
            last = await check(monitor, db, watch_id)
        assert last["status"] == "blocked"
        assert last["backoff"] >= 120
        assert last["warned"] == 1
        assert len(sent) == 1 and sent[0][1]["priority"] == 3   # Warnung kommt leise

        shop.blocked = False
        last = await check(monitor, db, watch_id)
        assert last["warned"] == 0 and last["backoff"] == 0

    asyncio.run(scenario())


def test_switches_away_from_throttled_shopify_json(tmp_path):
    page = ('<html><body><main><form action="/cart/add"><input name="id" value="7">'
            '<button type="submit" disabled>Ausverkauft</button></form></main>'
            '<script src="https://cdn.shopify.com/x.js"></script></body></html>')

    def handler(request):
        if request.url.path.endswith(".js"):
            return httpx.Response(429, text="Too many requests")
        return httpx.Response(200, text=page)

    db = Database(tmp_path / "t.db")
    monitor = Monitor(db, Fetcher(min_gap=0, transport=httpx.MockTransport(handler)), Notifier())
    watch_id = db.add_watch(url=URL, name="Display", shop="tcg.test", method="shopify", stock="soldout", interval=60)

    async def scenario():
        w = await check(monitor, db, watch_id)
        assert w["method"] == "button" and w["status"] == "soldout" and w["error_count"] == 0
        assert any("drosselt" in e["text"] for e in db.events())

    asyncio.run(scenario())


def test_telegram_and_discord_payloads():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(400, json={"ok": False})   # Bild nicht ladbar → Textnachricht
        return httpx.Response(200, json={"ok": True})

    notifier = Notifier(transport=httpx.MockTransport(handler))
    channels = {
        "telegram": {"enabled": True, "token": "123:abc", "chat_id": "42"},
        "discord": {"enabled": True, "webhook": "https://discord.com/api/webhooks/1/x"},
        "ntfy": {"enabled": False, "topic": "x"},
    }
    msg = Message("🟢 Verfügbar: Display <DE>", "149,99 €", "https://s.test/p", "https://s.test/cart/1:1",
                  "https://s.test/i.jpg", loud=False)
    results = asyncio.run(notifier.send_all(channels, msg))
    assert results == {"telegram": None, "discord": None}
    paths = [p for p, _ in seen]
    assert paths[:2] == ["/bot123:abc/sendPhoto", "/bot123:abc/sendMessage"] or "/bot123:abc/sendMessage" in paths
    tg = next(body for path, body in seen if path.endswith("sendMessage"))
    assert tg["disable_notification"] is True and "&lt;DE&gt;" in tg["text"]
    assert tg["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://s.test/cart/1:1"
    dc = next(body for path, body in seen if "webhooks" in path)
    assert dc["content"] == "" and dc["embeds"][0]["url"] == "https://s.test/p"


def test_no_heartbeat_on_setup_day_then_daily(tmp_path):
    db, monitor, shop, sent, watch_id = setup(tmp_path)
    db.set_setting("heartbeat_time", "00:00")

    async def scenario():
        await monitor._heartbeat_if_due()
        await asyncio.gather(*list(monitor._tasks))
        assert sent == []                                   # Einrichtungstag: still
        db.set_setting("last_heartbeat", "2000-01-01")      # ein Tag später
        await monitor._heartbeat_if_due()
        await asyncio.gather(*list(monitor._tasks))
        assert len(sent) == 1 and sent[0][1]["priority"] == 3

    asyncio.run(scenario())


def test_format_price():
    assert format_price(1499.9, "EUR") == "1.499,90 €"
    assert format_price(None, "EUR") is None
