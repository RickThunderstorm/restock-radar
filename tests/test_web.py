import json

import httpx
from fastapi.testclient import TestClient

from restockradar.fetcher import Fetcher
from restockradar.notify import Notifier
from restockradar.web import create_app

URL = "https://shop.test/products/etb"
PAGE = ("<html><head><meta property='og:title' content='Top-Trainer-Box'></head><body><main>"
        "<script type='application/ld+json'>" + json.dumps({
            "@type": "Product", "name": "Top-Trainer-Box",
            "offers": {"price": "59.99", "priceCurrency": "EUR", "availability": "https://schema.org/OutOfStock"}})
        + "</script></main></body></html>")


def make_client(tmp_path, notify_handler=None):
    shop = httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE) if str(r.url) == URL else httpx.Response(404))
    notifier = Notifier(transport=httpx.MockTransport(notify_handler or (lambda r: httpx.Response(200, json={}))))
    app = create_app(tmp_path, fetcher=Fetcher(min_gap=0, transport=shop), notifier=notifier, start_monitor=False)
    return TestClient(app)


def test_preview_add_patch_delete(tmp_path):
    with make_client(tmp_path) as client:
        prev = client.post("/api/preview", json={"url": "shop.test/products/etb"}).json()
        assert prev["status"] == "soldout" and prev["method"] == "jsonld"
        assert prev["price_text"] == "59,99 €" and prev["duplicate_of"] is None

        watch = client.post("/api/watches", json={"url": URL, "priority": "leise", "interval": 5}).json()
        assert watch["stock"] == "soldout" and watch["priority"] == "leise"
        assert watch["interval"] == 30                     # schneller als 30 s geht nicht

        assert client.post("/api/preview", json={"url": URL}).json()["duplicate_of"] == watch["id"]

        patched = client.patch(f"/api/watches/{watch['id']}", json={"enabled": False, "priority": "laut"}).json()
        assert patched["enabled"] == 0 and patched["priority"] == "laut"
        assert client.patch(f"/api/watches/{watch['id']}", json={"priority": "egal"}).status_code == 400

        state = client.get("/api/state").json()
        assert len(state["watches"]) == 1 and "access_key" not in state["settings"]

        client.delete(f"/api/watches/{watch['id']}")
        assert client.get("/api/state").json()["watches"] == []


def test_channels_setup_and_test_message(tmp_path):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    with make_client(tmp_path, handler) as client:
        assert client.post("/api/channels/ntfy/test").status_code == 400     # noch nicht eingerichtet
        ntfy = client.post("/api/channels/ntfy", json={}).json()
        assert ntfy["topic"].startswith("restock-") and ntfy["subscribe"].startswith("ntfy://ntfy.sh/")
        assert ntfy["qr"].startswith("<?xml") or "<svg" in ntfy["qr"]
        assert client.post("/api/channels/ntfy/test").json() == {"ok": True}
        assert sent[0]["topic"] == ntfy["topic"] and sent[0]["priority"] == 5

        bad = client.post("/api/channels/discord", json={"webhook": "https://example.com/x"})
        assert bad.status_code == 400
        ok = client.post("/api/channels/discord", json={"webhook": "https://discord.com/api/webhooks/1/abc123"}).json()
        assert ok["configured"] and ok["webhook_hint"] == "…abc123"
        channels = client.get("/api/state").json()["channels"]
        assert "webhook" not in channels["discord"]                      # Webhook wird nie komplett ausgegeben


def test_guard_blocks_foreign_hosts_and_lan_without_key(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/ping").status_code == 200
        # Webseite, die sich per DNS-Trick als lokaler Server ausgibt
        assert client.get("/api/ping", headers={"host": "evil.example"}).status_code == 403

    lan_client = TestClient(create_app(tmp_path, start_monitor=False), client=("192.168.1.50", 5000))
    with lan_client:
        assert lan_client.get("/api/ping").status_code == 403                # Handy-Zugriff ist aus


def test_settings_validation(tmp_path):
    with make_client(tmp_path) as client:
        assert client.put("/api/settings", json={"heartbeat_time": "25:99"}).status_code == 400
        saved = client.put("/api/settings", json={"heartbeat_time": "08:30", "default_interval": 10}).json()
        assert saved["heartbeat_time"] == "08:30" and saved["default_interval"] == 30
