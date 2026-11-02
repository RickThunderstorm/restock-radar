"""Die Überwachungsschleife: prüft fällige Seiten, erkennt Wechsel und schlägt Alarm."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

from .db import Database
from .detect import AVAILABLE, BLOCKED, BUYABLE, ERROR, PREORDER, QUEUE, SOLDOUT, UNKNOWN, Result, analyze
from .fetcher import Fetcher
from .notify import Message, Notifier, is_configured

log = logging.getLogger("restockradar")

MIN_INTERVAL = 30          # schneller wird kein Shop abgefragt
MAX_BACKOFF = 30 * 60      # bei Sperren höchstens 30 Minuten Pause
WARN_AFTER = 5             # so viele Fehlschläge am Stück, dann kommt eine Warnung
COOLDOWN = 5 * 60          # gegen Flackern: dieselbe Seite höchstens alle 5 Minuten melden
QUEUE_REPEAT = 30 * 60
PARALLEL = 4

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
        self._tasks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(PARALLEL)

    async def run(self) -> None:
        while True:
            try:
                for watch in self.db.due_watches(time.time()):
                    if watch["id"] not in self._running:
                        self._spawn(self._guarded(watch))
                await self._heartbeat_if_due()
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

    # ------------------------------------------------------------------ Lebenszeichen
    async def _heartbeat_if_due(self) -> None:
        settings = self.db.settings()
        if not settings["heartbeat_enabled"]:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if settings["last_heartbeat"] == today or now.strftime("%H:%M") < settings["heartbeat_time"]:
            return
        watches = [w for w in self.db.watches() if w["enabled"]]
        channels = settings["channels"]
        if not watches or not any(c.get("enabled") and is_configured(n, c) for n, c in channels.items()):
            return                                  # noch nichts eingerichtet – dann auch kein Lebenszeichen
        self.db.set_setting("last_heartbeat", today)
        problems = sum(1 for w in watches if w["status"] in (BLOCKED, ERROR, UNKNOWN))
        buyable = [w for w in watches if w["status"] in BUYABLE]
        text = f"{len(watches)} Seiten aktiv"
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
