"""Schonmodus: höchstens N Anfragen pro Shop und Minute – über Produkte, Suchen und Übernahmen hinweg."""
import asyncio
import time

import httpx

from restockradar.db import Database
from restockradar.fetcher import WINDOW, Fetcher, host_key
from restockradar.monitor import Monitor
from restockradar.notify import Notifier


def fill(fetcher: Fetcher, host: str, *ages: float) -> None:
    """Täuscht Anfragen vor, die `ages` Sekunden her sind."""
    now = time.monotonic()
    from collections import deque
    fetcher._history[host] = deque(sorted(now - a for a in ages))


def test_host_key_ignores_www():
    assert host_key("https://www.Shop.test/a") == host_key("http://shop.test/b") == "shop.test"


def test_slot_wait_math():
    f = Fetcher(min_gap=0)
    assert f.slot_wait("shop.test", 2) == 0                     # noch nichts angefragt
    fill(f, "shop.test", 50, 10)                                # zwei Anfragen in der letzten Minute
    assert f.recent("shop.test") == 2
    assert 9 <= f.slot_wait("shop.test", 2) <= 11               # älteste fällt in ~10 s raus
    assert f.slot_wait("shop.test", 3) == 0
    assert 9 <= f.slot_wait("shop.test", 3, extra=1) <= 11      # eine eingeplant → voll bis die älteste geht
    assert 49 <= f.slot_wait("shop.test", 3, extra=2) <= 51     # zwei eingeplant → erst die zweitälteste zählt
    assert f.slot_wait("shop.test", 0) == 0                     # 0 = unbegrenzt
    assert 59 <= f.slot_wait("neu.test", 1, extra=1) <= 60      # nur eingeplant, noch nichts gesendet
    fill(f, "shop.test", WINDOW + 5)                            # alte Anfragen zählen nicht
    assert f.recent("shop.test") == 0


def make(tmp_path, budget):
    db = Database(tmp_path / "t.db")
    db.set_setting("shop_budget", budget)
    fetcher = Fetcher(min_gap=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")))
    monitor = Monitor(db, fetcher, Notifier(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    return db, fetcher, monitor


def test_due_checks_beyond_budget_are_postponed(tmp_path):
    db, fetcher, monitor = make(tmp_path, budget=2)
    ids = [db.add_watch(url=f"https://www.shop.test/p{i}", interval=60, next_check=0) for i in range(3)]
    other = db.add_watch(url="https://anders.test/p", interval=60, next_check=0)

    async def scenario():
        now = time.time()
        started = monitor._schedule_watches(now)
        assert set(started) == {ids[0], ids[1], other}          # anderer Shop hat eigenes Budget
        postponed = db.watch(ids[2])
        assert postponed["next_check"] > now + 30               # erst, wenn das Budget wieder Luft hat
        await asyncio.gather(*list(monitor._tasks))

    asyncio.run(scenario())


def test_unlimited_budget_starts_everything(tmp_path):
    db, fetcher, monitor = make(tmp_path, budget=0)
    for i in range(5):
        db.add_watch(url=f"https://shop.test/p{i}", interval=60, next_check=0)

    async def scenario():
        assert len(monitor._schedule_watches(time.time())) == 5
        await asyncio.gather(*list(monitor._tasks))

    asyncio.run(scenario())


def test_api_load_and_budget_setting(tmp_path):
    from fastapi.testclient import TestClient
    from restockradar.web import create_app

    app = create_app(tmp_path, fetcher=Fetcher(min_gap=0), start_monitor=False)
    db = app.state.db
    for i in range(6):                                         # 6 Produkte im Minutentakt
        db.add_watch(url=f"https://www.laden.test/p{i}", interval=60, next_check=1e12)
    db.add_watch(url="https://ruhig.test/p", interval=120, next_check=1e12)
    with TestClient(app) as client:
        load = {row["host"]: row for row in client.get("/api/state").json()["load"]}
        assert load["laden.test"]["planned"] == 6.0 and load["laden.test"]["stretch"] == 1.5   # Standard 4/min
        assert load["ruhig.test"]["planned"] == 0.5 and load["ruhig.test"]["stretch"] is None
        assert client.put("/api/settings", json={"shop_budget": 99}).json()["shop_budget"] == 30
        client.put("/api/settings", json={"shop_budget": 0})
        assert client.get("/api/state").json()["load"][0]["stretch"] is None       # unbegrenzt


def test_search_skips_shop_when_budget_full(tmp_path):
    db, fetcher, monitor = make(tmp_path, budget=2)
    db.add_shop(name="Laden", base="https://shop.test", mode="html", search_url="https://shop.test/?q={q}")
    search_id = db.add_search(query="Display")
    fill(fetcher, "shop.test", 5, 3)                            # Budget gerade ausgeschöpft

    async def scenario():
        await monitor.check_search(db.search(search_id))
        assert fetcher.recent("shop.test") == 2                 # keine weitere Anfrage
        assert db.search(search_id)["baseline"] == "[]"         # Shop kommt beim nächsten Mal dran

    asyncio.run(scenario())
