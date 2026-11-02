"""Die Überwachungsschleife: prüft fällige Seiten, erkennt Wechsel und schlägt Alarm."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from .db import Database
from .detect import AVAILABLE, BLOCKED, BUYABLE, ERROR, PREORDER, QUEUE, SOLDOUT, UNKNOWN, Result, analyze
from .fetcher import Fetcher, host_key
from .notify import Message, Notifier, is_configured
from .reminders import link_shops, releases_on, reminder_message
from .search import (AUTO_NEW, AUTO_UNAVAILABLE, SearchError, fetch_category, matches, search_categories,
                     search_label, search_shop)
from .watches import add_watch, fill_from_hit

log = logging.getLogger("restockradar")

MIN_INTERVAL = 30          # schneller wird kein Shop abgefragt
MAX_BACKOFF = 30 * 60      # bei Sperren höchstens 30 Minuten Pause
WARN_AFTER = 5             # so viele Fehlschläge am Stück, dann kommt eine Warnung
COOLDOWN = 5 * 60          # gegen Flackern: dieselbe Seite höchstens alle 5 Minuten melden
QUEUE_REPEAT = 30 * 60
PARALLEL = 4
SEARCH_MIN_INTERVAL = 60    # Suchaufträge höchstens jede Minute
AUTO_INTERVAL = 180         # automatisch übernommene Produkte alle 3 Minuten prüfen – schont die Shops
SLOT_PATIENCE = 20          # so lange wartet ein Suchauftrag höchstens auf einen freien Platz im Shop-Budget
SHOP_PAUSE = {"throttled": 10 * 60, BLOCKED: 30 * 60, QUEUE: 5 * 60, "error": 5 * 60}

STATUS_WORDS = {
    AVAILABLE: "verfügbar", PREORDER: "vorbestellbar", SOLDOUT: "ausverkauft",
    UNKNOWN: "unklar", BLOCKED: "gesperrt", QUEUE: "Warteschlange", ERROR: "Fehler",
}


class Monitor:
    def __init__(self, db: Database, fetcher: Fetcher, notifier: Notifier):
        self.db = db
        self.fetcher = fetcher
        self.notifier = notifier
        self._running: set[int] = set()
        self._running_searches: set[int] = set()
        self._auto_running: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(PARALLEL)

    async def run(self) -> None:
        while True:
            try:
                now = time.time()
                self._schedule_watches(now)
                for search in self.db.due_searches(now):
                    if search["id"] not in self._running_searches:
                        self._spawn(self._guarded_search(search))
                await self._heartbeat_if_due()
                self._release_reminders_if_due()
            except Exception:                       # die Schleife darf nie sterben
                log.exception("Fehler in der Überwachungsschleife")
            await asyncio.sleep(1)

    async def check_now(self, watch_id: int) -> dict | None:
        watch = self.db.watch(watch_id)
        if watch and watch["id"] not in self._running:
            await self._guarded(watch)
        return self.db.watch(watch_id)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guarded(self, watch: dict) -> None:
        self._running.add(watch["id"])
        try:
            async with self._sem:
                await self.check(watch)
        except Exception:
            log.exception("Prüfung fehlgeschlagen: %s", watch["url"])
            self.db.update_watch(watch["id"], next_check=time.time() + max(MIN_INTERVAL, watch["interval"] or 60))
        finally:
            self._running.discard(watch["id"])

    # ------------------------------------------------------------------ eine Prüfung
    async def check(self, watch: dict) -> None:
        res = await analyze(watch["url"], self.fetcher, method=watch["method"], variant_id=watch["variant_id"])
        now = time.time()
        interval = max(MIN_INTERVAL, watch["interval"] or 60)
        upd: dict = {"last_check": now, "status": res.status, "reason": res.reason, "note": res.note}
        if watch["method"] == "shopify" and res.method and res.method != "shopify":
            # Der Shop drosselt sein Lager-JSON – ab jetzt dauerhaft über die Seite prüfen.
            upd.update(method=res.method, note="")
            self._event(watch, "info", f"Prüfe jetzt über {res.method_label}, weil der Shop sein Lager-JSON drosselt")

        if res.status in (BLOCKED, ERROR, UNKNOWN):
            errors = (watch["error_count"] or 0) + 1
            upd["error_count"] = errors
            if res.status == UNKNOWN:
                upd["next_check"] = now + interval
            else:
                # Höflich zurückweichen: jede Sperre verdoppelt die Pause.
                backoff = min(max(interval * 2, (watch["backoff"] or interval) * 2), MAX_BACKOFF)
                if res.retry_after:
                    backoff = max(backoff, min(res.retry_after, MAX_BACKOFF))
                upd.update(backoff=backoff, next_check=now + backoff)
            if errors >= WARN_AFTER and not watch["warned"]:
                upd["warned"] = 1
                self._event(watch, "warning", f"Seite nicht lesbar: {res.reason}")
                self._spawn(self._send(watch["id"], self._warning_message(watch, res)))
            self.db.update_watch(watch["id"], **upd)
            return

        upd.update(error_count=0, backoff=0, next_check=now + interval * random.uniform(0.85, 1.15))
        if watch["warned"]:
            upd["warned"] = 0
            self._event(watch, "info", "Seite wieder lesbar")
            self._spawn(self._send(watch["id"], Message(
                f"✅ Wieder lesbar: {self._name(watch)}", watch["shop"] or "", watch["url"], loud=False, kind="info")))
        if res.price is not None:
            upd["price"] = res.price
        if res.cart_url:
            upd["cart_url"] = res.cart_url
        if res.release:
            upd.update(release=res.release, release_date=res.release_date)
        if res.image and not watch["image"]:
            upd["image"] = res.image

        if res.status == QUEUE:
            if not watch["last_queue"] or now - watch["last_queue"] > QUEUE_REPEAT:
                upd["last_queue"] = now
                self._event(watch, "queue", "Warteschlange aktiv – Drop läuft vermutlich")
                self._spawn(self._send(watch["id"], Message(
                    f"⏳ Warteschlange bei {watch['shop']}",
                    f"{self._name(watch)}: Der Shop hat eine Warteschlange aktiviert – oft startet gerade ein Drop.",
                    watch["url"], loud=watch["priority"] == "laut", kind="queue")))
            self.db.update_watch(watch["id"], **upd)
            return

        old, new = watch["stock"], res.status
        if new != old:
            upd.update(stock=new, last_change=now)
            if old is None:
                self._event(watch, "info", f"Lagerstand erkannt: {STATUS_WORDS[new]}", old, new, res.price)
            elif self._worth_alarm(old, new):
                if watch["last_notified"] and now - watch["last_notified"] < COOLDOWN:
                    self._event(watch, "restock", f"Wieder {STATUS_WORDS[new]} (gerade erst gemeldet, keine neue Nachricht)", old, new, res.price)
                else:
                    upd["last_notified"] = now
                    self._event(watch, "restock", f"{STATUS_WORDS[new].capitalize()}!", old, new, res.price)
                    self._spawn(self._send(watch["id"], self._restock_message(watch, res)))
            elif old in BUYABLE and new not in BUYABLE:
                minutes = (now - (watch["last_change"] or now)) / 60
                self._event(watch, "soldout", f"Wieder ausverkauft – war {_duration(minutes)} kaufbar", old, new, res.price)
            else:
                self._event(watch, "info", f"Jetzt {STATUS_WORDS[new]}", old, new, res.price)
        self.db.update_watch(watch["id"], **upd)

    @staticmethod
    def _worth_alarm(old: str | None, new: str) -> bool:
        if new == AVAILABLE:
            return old != AVAILABLE
        if new == PREORDER:
            return old not in BUYABLE
        return False

    # ------------------------------------------------------------------ Suchaufträge
    async def check_search_now(self, search_id: int) -> dict | None:
        search = self.db.search(search_id)
        if search and search["id"] not in self._running_searches:
            await self._guarded_search(search)
        return self.db.search(search_id)

    async def _guarded_search(self, search: dict) -> None:
        self._running_searches.add(search["id"])
        try:
            async with self._sem:
                await self.check_search(search)
        except Exception:
            log.exception("Suchauftrag fehlgeschlagen: %s", search["query"])
            self.db.update_search(search["id"], next_check=time.time() + max(SEARCH_MIN_INTERVAL, search["interval"] or 180))
        finally:
            self._running_searches.discard(search["id"])

    async def check_search(self, search: dict) -> None:
        """Fragt Shopsuchen und Kategorie-Seiten ab. Je Quelle wird beim ersten Mal still erfasst."""
        now = time.time()
        baseline = set(json.loads(search["baseline"] or "[]"))
        categories = search_categories(search)
        errors: list[str] = []

        if (search["query"] or "").strip() or not categories:
            for shop in self.db.shops():
                if not shop["enabled"] or shop["mode"] == "link" or (shop["paused_until"] or 0) > now:
                    continue                        # Link-Shops werden nie abgefragt, nur verlinkt
                if not await self._take_slot(host_key(shop["base"])):
                    continue                        # Budget voll – dieser Shop ist beim nächsten Durchlauf dran
                try:
                    items = await search_shop(shop, search, self.fetcher)
                except SearchError as err:
                    kind = "throttled" if "bremst" in err.reason else err.status
                    pause = max(SHOP_PAUSE.get(kind, 300), min(err.retry_after or 0, MAX_BACKOFF))
                    self.db.update_shop(shop["id"], status=kind, reason=err.reason, paused_until=now + pause, last_check=now)
                    continue
                self.db.update_shop(shop["id"], status="ok", reason="", paused_until=0, last_check=now)
                self._handle_items(search, shop, items, silent=shop["id"] not in baseline)
                baseline.add(shop["id"])

        for url in categories:
            host = urlsplit(url).hostname or url
            shop = self.db.shop_by_host(host) or {"id": None, "name": host.removeprefix("www.")}
            if not await self._take_slot(host_key(url)):
                continue
            try:
                items = await fetch_category(url, self.fetcher)
            except SearchError as err:
                errors.append(f"{shop['name']}: {err.reason}")
                continue
            key = f"cat:{url}"
            self._handle_items(search, shop, items, silent=key not in baseline)
            baseline.add(key)

        interval = max(SEARCH_MIN_INTERVAL, search["interval"] or 180)
        self.db.update_search(search["id"], baseline=json.dumps(sorted(baseline, key=str)), last_check=now,
                              last_error="; ".join(errors), next_check=now + interval * random.uniform(0.9, 1.1))
        if search["auto_watch"] == AUTO_UNAVAILABLE:
            self._start_auto_watch(self.db.search(search["id"]))

    def _handle_items(self, search: dict, shop: dict, items: list, silent: bool) -> None:
        seen: set[str] = set()
        for item in items:
            if item.url in seen:
                continue
            seen.add(item.url)
            if matches(item, search):
                self._handle_hit(search, shop, item, silent)
            elif (stale := self.db.hit_by_url(search["id"], item.url)) and not stale["watch_id"]:
                # passt nach besserer Erkennung nicht mehr (z. B. falsche Sprache) – aus der Liste nehmen
                self.db.delete_hit(stale["id"])

    def _handle_hit(self, search: dict, shop: dict, item, silent: bool) -> None:
        now = time.time()
        label = f"Suche „{search_label(search)}“"
        existing = self.db.hit_by_url(search["id"], item.url)
        if not existing:
            hit_id = self.db.add_hit(search_id=search["id"], shop_id=shop["id"], url=item.url, title=item.title,
                                     price=item.price, currency=item.currency, image=item.image, status=item.status,
                                     release=item.release, release_date=item.release_date,
                                     is_new=0 if silent else 1, first_seen=now, last_seen=now, last_change=now)
            if silent:
                return
            buyable = item.status in BUYABLE
            self.db.add_event(None, "restock" if buyable else "info", f"{label}: neu bei {shop['name']} – {item.title}")
            parts = [None if buyable else STATUS_WORDS.get(item.status), format_price(item.price, item.currency), shop["name"]]
            text = " · ".join(p for p in parts if p)
            if item.release:
                text += f"\nErscheint laut Shop: {item.release}"
            headline = {AVAILABLE: "🆕 Neu & verfügbar: ", PREORDER: "🆕 Neu & vorbestellbar: "}.get(item.status, "🆕 Neu gelistet: ")
            self._spawn(self._send(None, Message(
                headline + item.title,
                text + f"\nGefunden über {label}",
                item.url, image=item.image, loud=buyable and search["priority"] == "laut",
                kind="restock" if buyable else "info")))
            if search["auto_watch"] == AUTO_NEW and self._auto_room(search) > 0:
                self._spawn(self._auto_watch(hit_id, item.url, search))
            return

        upd = {"last_seen": now, "title": item.title, "price": item.price, "image": item.image or existing["image"]}
        if item.release:
            upd.update(release=item.release, release_date=item.release_date)
        old, new = existing["status"], item.status
        if new != UNKNOWN and new != old:
            upd.update(status=new, last_change=now)
            # Wird der Treffer schon als Produkt überwacht, meldet sich die Produkt-Überwachung selbst.
            if old not in (None, UNKNOWN) and self._worth_alarm(old, new) and not existing["watch_id"]:
                self.db.add_event(None, "restock", f"{label}: wieder {STATUS_WORDS[new]} – {item.title}")
                self._spawn(self._send(None, Message(
                    ("🟢 Wieder verfügbar: " if new == AVAILABLE else "🟡 Jetzt vorbestellbar: ") + item.title,
                    " · ".join(p for p in (format_price(item.price, item.currency), shop["name"]) if p)
                    + f"\nGefunden über {label}",
                    item.url, image=item.image, loud=search["priority"] == "laut", kind="restock")))
        self.db.update_hit(existing["id"], **upd)

    # ------------------------------------------------------------------ Schonmodus
    def _schedule_watches(self, now: float) -> list[int]:
        """Startet fällige Prüfungen. Ist das Budget eines Shops voll, wird verschoben statt gesendet."""
        limit = self._budget()
        planned: dict[str, int] = {}
        started = []
        for watch in self.db.due_watches(now):
            if watch["id"] in self._running:
                continue
            host = host_key(watch["url"])
            wait = self.fetcher.slot_wait(host, limit, planned.get(host, 0))
            if wait:
                self.db.update_watch(watch["id"], next_check=now + wait + random.uniform(0, 5))
                continue
            planned[host] = planned.get(host, 0) + 1
            started.append(watch["id"])
            self._spawn(self._guarded(watch))
        return started

    def _budget(self) -> int:
        try:
            return max(0, int(self.db.settings().get("shop_budget") or 0))
        except (TypeError, ValueError):
            return 0

    async def _take_slot(self, host: str, patience: float = SLOT_PATIENCE) -> bool:
        """Für Suchaufträge: kurz auf einen freien Platz warten, sonst diese Runde auslassen."""
        wait = self.fetcher.slot_wait(host, self._budget())
        if wait > patience:
            return False
        if wait:
            await asyncio.sleep(wait)
        return True

    # ------------------------------------------------------------------ automatisch übernehmen
    def _auto_room(self, search: dict) -> int:
        taken = sum(1 for h in self.db.hits(search["id"]) if h["watch_id"])
        return max(0, (search["max_auto"] or 30) - taken)

    def _start_auto_watch(self, search: dict) -> None:
        """„Alles, was gerade nicht kaufbar ist“: offene Treffer nach und nach als Produkte übernehmen."""
        if search["id"] in self._auto_running:
            return
        room = self._auto_room(search)
        today = datetime.now().date().isoformat()

        def priority(hit: dict) -> tuple:
            # Kommendes zuerst (Termin in der Zukunft, dann Vorverkauf), danach Ausverkauftes günstig vor teuer –
            # sonst landen ausverkaufte Sammler-Raritäten vor dem nächsten Release.
            upcoming = bool(hit["release_date"] and hit["release_date"] >= today)
            group = 0 if upcoming else 1 if hit["status"] == PREORDER else 2
            return group, hit["release_date"] or "9999", hit["price"] if hit["price"] is not None else 1e9

        todo = sorted((h for h in self.db.hits(search["id"])
                       if not h["watch_id"] and h["status"] in (SOLDOUT, PREORDER, UNKNOWN)), key=priority)[:room]
        if todo:
            self._auto_running.add(search["id"])
            self._spawn(self._auto_watch_many(search, todo))

    async def _auto_watch_many(self, search: dict, hits: list[dict]) -> None:
        try:
            for hit in hits:
                try:
                    while (wait := self.fetcher.slot_wait(host_key(hit["url"]), self._budget())):
                        await asyncio.sleep(wait)       # Übernahmen dürfen warten – sie sind nicht eilig
                    await self._auto_watch(hit["id"], hit["url"], search)
                except Exception:                   # eine kaputte Seite darf die anderen nicht aufhalten
                    log.exception("Automatische Übernahme fehlgeschlagen: %s", hit["url"])
        finally:
            self._auto_running.discard(search["id"])

    async def _auto_watch(self, hit_id: int, url: str, search: dict) -> None:
        res = await analyze(url, self.fetcher)
        if res.status == ERROR:
            self.db.add_event(None, "warning", f"Treffer konnte nicht übernommen werden: {res.reason}")
            return
        fill_from_hit(res, self.db.hit(hit_id))
        watch = add_watch(self.db, res, priority=search["priority"], interval=AUTO_INTERVAL, auto=True,
                          note=f"Automatisch aus Suche „{search_label(search)}“ übernommen")
        self.db.update_hit(hit_id, watch_id=watch["id"])

    # ------------------------------------------------------------------ Nachrichten
    @staticmethod
    def _name(watch: dict) -> str:
        return watch["name"] or watch["shop"] or watch["url"]

    def _restock_message(self, watch: dict, res: Result) -> Message:
        word = "Verfügbar" if res.status == AVAILABLE else "Vorbestellbar"
        icon = "🟢" if res.status == AVAILABLE else "🟡"
        parts = [p for p in (watch["variant_title"], format_price(res.price or watch["price"], watch["currency"]), watch["shop"]) if p]
        text = " · ".join(parts)
        cart = res.cart_url or watch["cart_url"]
        if cart:
            text += "\n„Direkt zur Kasse“ legt ihn sofort in den Warenkorb."
        return Message(f"{icon} {word}: {self._name(watch)}", text, watch["url"], cart, watch["image"] or res.image,
                       loud=watch["priority"] == "laut", kind="restock")

    def _warning_message(self, watch: dict, res: Result) -> Message:
        return Message(f"⚠️ Seite nicht lesbar: {self._name(watch)}",
                       f"{res.reason or 'Unbekannter Fehler'} – ich versuche es weiter, aber seltener.",
                       watch["url"], loud=False, kind="warning")

    async def _send(self, watch_id: int | None, msg: Message) -> dict:
        channels = self.db.settings()["channels"]
        results = await self.notifier.send_all(channels, msg)
        if not results:
            self.db.add_event(watch_id, "warning", "Keine Benachrichtigung eingerichtet – Nachricht nicht gesendet")
            return results
        ok = [name for name, err in results.items() if err is None]
        failed = {name: err for name, err in results.items() if err}
        text = "Gesendet an " + ", ".join(ok) if ok else "Senden fehlgeschlagen"
        if failed:
            text += " · Fehler: " + "; ".join(failed.values())
        self.db.add_event(watch_id, "sent" if ok else "warning", text)
        return results

    def _event(self, watch: dict, kind: str, text: str, old=None, new=None, price=None) -> None:
        self.db.add_event(watch["id"], kind, text, old, new, price)

    # ------------------------------------------------------------------ Erinnerung am Erscheinungstag
    def _release_reminders_if_due(self, now: datetime | None = None) -> None:
        settings = self.db.settings()
        now = now or datetime.now()
        today = now.date()
        clock = now.strftime("%H:%M")
        plans = [("release_reminder", "release_time", "last_release_reminder", today, False),
                 ("release_eve", "release_eve_time", "last_release_eve", today + timedelta(days=1), True)]
        for enabled, at, last, day, eve in plans:
            if not settings[enabled] or settings[last] == today.isoformat() or clock < settings[at]:
                continue
            self.db.set_setting(last, today.isoformat())
            items = releases_on(self.db, day)
            if not items:
                continue
            msg = reminder_message(items, link_shops(self.db), eve=eve)
            self.db.add_event(None, "info", msg.title.removeprefix("📅 "))
            self._spawn(self._send(None, msg))

    # ------------------------------------------------------------------ Lebenszeichen
    async def _heartbeat_if_due(self) -> None:
        settings = self.db.settings()
        if not settings["heartbeat_enabled"]:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if not settings["last_heartbeat"]:
            self.db.set_setting("last_heartbeat", today)    # am Einrichtungstag noch kein Lebenszeichen
            return
        if settings["last_heartbeat"] == today or now.strftime("%H:%M") < settings["heartbeat_time"]:
            return
        watches = [w for w in self.db.watches() if w["enabled"]]
        searches = [s for s in self.db.searches() if s["enabled"]]
        channels = settings["channels"]
        if not (watches or searches) or not any(c.get("enabled") and is_configured(n, c) for n, c in channels.items()):
            return                                  # noch nichts eingerichtet – dann auch kein Lebenszeichen
        self.db.set_setting("last_heartbeat", today)
        problems = sum(1 for w in watches if w["status"] in (BLOCKED, ERROR, UNKNOWN))
        buyable = [w for w in watches if w["status"] in BUYABLE]
        text = f"{len(watches)} Seiten aktiv"
        if searches:
            text += f" · {len(searches)} Suchaufträge"
        text += f" · {problems} mit Problemen" if problems else " · alle lesbar"
        if buyable:
            text += f" · gerade kaufbar: {', '.join(self._name(w) for w in buyable[:3])}"
        self._spawn(self._send(None, Message("✅ Restock-Radar läuft", text, loud=False, kind="info")))


def format_price(price: float | None, currency: str | None) -> str | None:
    if price is None:
        return None
    amount = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{amount} €" if (currency or "EUR").upper() == "EUR" else f"{amount} {currency}"


def _duration(minutes: float) -> str:
    if minutes < 1:
        return "unter einer Minute"
    if minutes < 90:
        return f"{round(minutes)} Min."
    return f"{minutes / 60:.1f} Std.".replace(".", ",")
