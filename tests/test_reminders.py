"""Erinnerung am Erscheinungstag – mit Links zu Shops, die Restock-Radar nicht lesen darf."""
import asyncio
import json
from datetime import date, datetime

import httpx
from fastapi.testclient import TestClient

from restockradar import reminders
from restockradar.db import Database
from restockradar.fetcher import Fetcher
from restockradar.monitor import Monitor
from restockradar.notify import Message, Notifier
from restockradar.web import create_app

DAY = date(2026, 11, 6)


def test_short_query():
    assert reminders.short_query("Mega-Entwicklung Delta Herrschaft Display (36 Booster) (deutsch) VORVERKAUF") == "delta herrschaft"
    assert reminders.short_query("Pokémon Top-Trainer-Box 30 Jahre") == "jahre"
    assert reminders.short_query("Display") == "display"


def test_template_from_example():
    assert reminders.template_from_example("https://games-island.eu/search/?qs=Pikachu&sort=1", "pikachu") == \
        "https://games-island.eu/search/?qs={q}&sort=1"
    assert reminders.template_from_example("https://laden.test/suche/pikachu", "pikachu") == "https://laden.test/suche/{q}"
    assert reminders.template_from_example("https://laden.test/search?q=glurak", "pikachu") is None


def test_shop_link_template_or_google():
    assert reminders.shop_link({"search_url": "https://gi.test/search/?qs={q}", "base": "https://gi.test"},
                               "delta herrschaft") == "https://gi.test/search/?qs=delta+herrschaft"
    google = reminders.shop_link({"search_url": "", "base": "https://www.gi.test"}, "delta herrschaft")
    assert google == "https://www.google.com/search?q=site%3Agi.test+delta+herrschaft"


def seed(db: Database):
    db.add_shop(name="Gate", base="https://gate.test", mode="html", search_url="https://gate.test/?qs={q}")
    db.add_shop(name="Games Island", base="https://games-island.test", mode="html",
                search_url="https://games-island.test/search/?qs={q}", status="blocked")
    db.add_shop(name="Link-Laden", base="https://link.test", mode="link", search_url="", status="link")
    search_id = db.add_search(query="", ptype="display")
    for title, release_date, release in [
        ("Mega-Entwicklung Delta Herrschaft Display (36 Booster) (deutsch)", "2026-11-06", "06.11.2026"),
        ("Mega-Entwicklung Delta Herrschaft Display (36 Booster) (deutsch)", "2026-11-06", "06.11.2026"),  # doppelt
        ("Mega-Entwicklung Neues Set Display (deutsch)", "2026-12-04", "04.12.2026"),
    ]:
        db.add_hit(search_id=search_id, shop_id=1, url=f"https://gate.test/{len(title)}-{release_date}-{db.hits(search_id).__len__()}",
                   title=title, status="preorder", release=release, release_date=release_date, first_seen=1)
    db.add_watch(url="https://cards.test/delta-ttb", name="Delta Herrschaft Top-Trainer-Box", interval=60,
                 next_check=1e12, release="06.11.2026", release_date="2026-11-06")


def test_releases_on_dedupes_and_links_blocked_shops(tmp_path):
    db = Database(tmp_path / "t.db")
    seed(db)
    items = reminders.releases_on(db, DAY)
    assert sorted(i["title"] for i in items) == ["Delta Herrschaft Top-Trainer-Box",
                                                 "Mega-Entwicklung Delta Herrschaft Display (36 Booster) (deutsch)"]
    shops = reminders.link_shops(db)
    assert [s["name"] for s in shops] == ["Games Island", "Link-Laden"]
    msg = reminders.reminder_message(items, shops)
    assert msg.title == "📅 Heute erscheinen 2 Produkte" and msg.loud
    assert "Gesperrte Shops selbst prüfen: Games Island, Link-Laden" in msg.text
    urls = [url for _, url in msg.links]
    assert "https://games-island.test/search/?qs=delta+herrschaft" in urls
    assert any("google.com/search" in u and "link.test" in u for u in urls)

    cal = reminders.calendar(db, date(2026, 11, 2))
    assert [d["date"] for d in cal] == ["2026-11-06", "2026-12-04"]
    assert cal[0]["links"][0]["url"].startswith("https://games-island.test/search/?qs=")


def test_monitor_sends_reminder_once_per_day_and_eve(tmp_path):
    sent = []

    def notify(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    db = Database(tmp_path / "t.db")
    seed(db)
    db.update_channel("ntfy", enabled=True, topic="restock-test")
    db.set_setting("release_eve", True)
    monitor = Monitor(db, Fetcher(min_gap=0), Notifier(transport=httpx.MockTransport(notify)))

    async def scenario():
        monitor._release_reminders_if_due(datetime(2026, 11, 5, 7, 0))      # zu früh für alles
        monitor._release_reminders_if_due(datetime(2026, 11, 5, 19, 30))    # Vorabend
        monitor._release_reminders_if_due(datetime(2026, 11, 6, 8, 5))      # Erscheinungstag
        monitor._release_reminders_if_due(datetime(2026, 11, 6, 9, 0))      # nicht doppelt
        await asyncio.gather(*list(monitor._tasks))

    asyncio.run(scenario())
    titles = [m["title"] for m in sent]
    assert titles == ["📅 Morgen erscheinen 2 Produkte", "📅 Heute erscheinen 2 Produkte"]
    assert sent[0]["priority"] == 3 and sent[1]["priority"] == 5            # Vorabend leise, am Tag laut
    labels = [a["label"] for a in sent[1]["actions"]]
    assert labels[:2] == ["Games Island", "Link-Laden"] and len(labels) <= 3


def test_telegram_and_discord_show_all_links():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    notifier = Notifier(transport=httpx.MockTransport(handler))
    msg = Message("📅 Heute erscheint X", "• X – gate.test", None, loud=True, kind="info",
                  links=[("Games Island", "https://gi.test/?qs=x"), ("Link-Laden", "https://g.test/x")])
    asyncio.run(notifier.send_all({"telegram": {"enabled": True, "token": "1:a", "chat_id": "2"},
                                   "discord": {"enabled": True, "webhook": "https://discord.com/api/webhooks/1/x"}}, msg))
    tg = next(b for b in seen if "chat_id" in b)
    assert [row[0]["url"] for row in tg["reply_markup"]["inline_keyboard"]] == ["https://gi.test/?qs=x", "https://g.test/x"]
    dc = next(b for b in seen if "embeds" in b)
    assert "[🔎 Games Island](https://gi.test/?qs=x)" in dc["embeds"][0]["description"]


def test_api_link_shops(tmp_path):
    def handler(request):
        if request.url.host == "geschuetzt.test":
            return httpx.Response(200, text="<title>Sicherheitspruefung</title>Proof-of-Work wird berechnet")
        return httpx.Response(404)

    app = create_app(tmp_path, fetcher=Fetcher(min_gap=0, transport=httpx.MockTransport(handler)), start_monitor=False)
    with TestClient(app) as client:
        blocked = client.post("/api/shops", json={"url": "https://geschuetzt.test"})
        assert blocked.status_code == 409 and blocked.json()["detail"]["blocked"] is True

        bad = client.post("/api/shops", json={"url": "geschuetzt.test", "link_only": True,
                                              "example": "https://geschuetzt.test/search/?qs=glurak"})
        assert bad.status_code == 400 and "pikachu" in bad.json()["detail"]

        ok = client.post("/api/shops", json={"url": "geschuetzt.test", "link_only": True,
                                             "example": "https://geschuetzt.test/search/?qs=PIKACHU"}).json()["shop"]
        assert ok["mode"] == "link" and ok["search_url"] == "https://geschuetzt.test/search/?qs={q}"

        other = client.post("/api/shops", json={"url": "anderer.test", "link_only": True}).json()["shop"]
        assert other["search_url"] == ""                                   # Google-Suche als Ersatz
        app.state.db.update_shop(other["id"], mode="html", status="blocked")
        switched = client.patch(f"/api/shops/{other['id']}", json={"link_only": True}).json()
        assert switched["mode"] == "link" and switched["status"] == "link"

        assert client.put("/api/settings", json={"release_time": "7:5"}).status_code == 400
        saved = client.put("/api/settings", json={"release_eve": True, "release_eve_time": "20:00"}).json()
        assert saved["release_eve"] is True and "last_release_eve" not in saved
        assert client.get("/api/state").json()["calendar"] == []
