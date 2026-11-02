"""Weboberfläche und API. Läuft lokal; aufs Handy nur mit Zugangsschlüssel."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import APP_NAME, VERSION, system, watches
from . import search as finder
from .db import Database
from .detect import Result, analyze, normalize_url
from .fetcher import Fetcher
from .monitor import MIN_INTERVAL, Monitor, format_price
from .notify import Message, Notifier, is_configured, new_ntfy_topic, new_pairing_code, ntfy_links, qr_svg
from .paths import static_dir

log = logging.getLogger("restockradar")

PREVIEW_TTL = 600
LOOPBACK = {"127.0.0.1", "::1", "testclient"}


class PreviewIn(BaseModel):
    url: str


class WatchIn(BaseModel):
    url: str
    priority: str = "laut"
    interval: int | None = None
    variant_id: str | None = None


class WatchPatch(BaseModel):
    name: str | None = None
    priority: str | None = None
    enabled: bool | None = None
    interval: int | None = None
    method: str | None = None


class SettingsIn(BaseModel):
    heartbeat_enabled: bool | None = None
    heartbeat_time: str | None = None
    default_interval: int | None = None
    setup_done: bool | None = None


class NtfyIn(BaseModel):
    enabled: bool | None = None
    server: str | None = None
    new_topic: bool = False


class TelegramIn(BaseModel):
    token: str


class DiscordIn(BaseModel):
    webhook: str | None = None
    enabled: bool | None = None


class ToggleIn(BaseModel):
    enabled: bool


class ShopIn(BaseModel):
    url: str


class SearchIn(BaseModel):
    query: str
    ptype: str = "egal"
    lang: str = "egal"
    max_price: float | None = None
    exclude: str = ""
    priority: str = "laut"
    auto_watch: bool = False
    interval: int | None = None


class SearchPatch(BaseModel):
    query: str | None = None
    ptype: str | None = None
    lang: str | None = None
    max_price: float | None = None
    exclude: str | None = None
    priority: str | None = None
    auto_watch: bool | None = None
    enabled: bool | None = None
    interval: int | None = None


class Controller:
    """Wird von außen (run.py) gesetzt, um den Server mit anderer Adresse neu zu starten."""
    port: int = 8787
    restart: Callable[[], None] | None = None


def create_app(data_dir: Path, controller: Controller | None = None, *, fetcher: Fetcher | None = None,
               notifier: Notifier | None = None, start_monitor: bool = True) -> FastAPI:
    controller = controller or Controller()
    db = Database(data_dir / "restockradar.db")
    fetcher = fetcher or Fetcher()
    notifier = notifier or Notifier()
    monitor = Monitor(db, fetcher, notifier)
    previews: dict[str, tuple[float, Result]] = {}
    pairing: dict = {"state": "idle", "task": None, "bot_name": ""}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(monitor.run()) if start_monitor else None
        yield
        for t in (task, pairing.get("task")):
            if t:
                t.cancel()
        await fetcher.close()
        await notifier.close()

    app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.monitor = db, monitor

    # ------------------------------------------------------------------ Zugangsschutz
    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else ""
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        settings = db.settings()
        lan_ip = system.lan_ip() if settings["lan_enabled"] else None
        if host not in {"127.0.0.1", "localhost", "::1", "testserver", lan_ip}:
            # schützt vor Webseiten, die sich per DNS-Trick als „localhost“ ausgeben
            return HTMLResponse("Unbekannte Adresse.", status_code=403)
        if client not in LOOPBACK:
            key = settings["access_key"]
            if not settings["lan_enabled"] or not key:
                return HTMLResponse("Handy-Zugriff ist ausgeschaltet.", status_code=403)
            if secrets.compare_digest(request.query_params.get("key", ""), key):
                response = RedirectResponse(request.url.path)
                response.set_cookie("rr_key", key, max_age=365 * 86400, httponly=True, samesite="lax")
                return response
            if not secrets.compare_digest(request.cookies.get("rr_key", ""), key):
                return HTMLResponse(_locked_page(), status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"     # nach Updates sofort die neue Oberfläche
        return response

    # ------------------------------------------------------------------ Seiten
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_dir() / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=static_dir()), name="static")

    @app.get("/api/ping")
    async def ping():
        return {"app": APP_NAME, "version": VERSION}

    @app.get("/api/state")
    async def state():
        settings = db.settings()
        return {
            "now": time.time(),
            "version": VERSION,
            "watches": db.watches(),
            "events": db.events(80),
            "settings": _public_settings(settings),
            "channels": _public_channels(settings["channels"]),
            "autostart": {"supported": system.autostart_supported(), "enabled": system.autostart_enabled()},
            "min_interval": MIN_INTERVAL,
            "shops": db.shops(),
            "searches": [_search_view(db, s) for s in db.searches()],
            "search_meta": {"types": {k: v["label"] for k, v in finder.PRODUCT_TYPES.items()},
                            "langs": finder.LANGUAGES},
        }

    # ------------------------------------------------------------------ Shops & Suchaufträge
    @app.post("/api/shops")
    async def add_shop(body: ShopIn):
        try:
            info = await finder.detect_shop(body.url, fetcher)
        except finder.SearchError as err:
            raise HTTPException(400, err.reason)
        if db.shop_by_base(info.base):
            raise HTTPException(400, "Diesen Shop hast du schon hinterlegt.")
        test = {"count": 0, "sample": [], "error": None}
        try:
            items = await finder.run_search({"mode": info.mode, "search_url": info.search_url, "base": info.base},
                                            "pokemon", fetcher)
            test.update(count=len(items), sample=[i.title for i in items[:3]])
        except finder.SearchError as err:
            if err.status == "blocked" and "bremst" not in err.reason:
                raise HTTPException(400, f"{err.reason} – diesen Shop kann ich nicht durchsuchen.")
            test["error"] = err.reason
        status = "ok" if test["count"] else "warn"
        reason = test["error"] or ("" if test["count"] else "Probesuche nach „Pokémon“ ohne Treffer")
        shop_id = db.add_shop(name=info.name, base=info.base, platform=info.platform, mode=info.mode,
                              search_url=info.search_url, status=status, reason=reason)
        return {"shop": db.shop(shop_id), "test": test, "notes": info.notes}

    @app.patch("/api/shops/{shop_id}")
    async def patch_shop(shop_id: int, body: ToggleIn):
        if not db.shop(shop_id):
            raise HTTPException(404, "Nicht gefunden")
        db.update_shop(shop_id, enabled=int(body.enabled), paused_until=0)
        return db.shop(shop_id)

    @app.delete("/api/shops/{shop_id}")
    async def delete_shop(shop_id: int):
        db.delete_shop(shop_id)
        return {"ok": True}

    @app.post("/api/searches")
    async def add_search(body: SearchIn):
        values = _search_values(body.model_dump())
        if not db.shops():
            raise HTTPException(400, "Lege zuerst mindestens einen Shop an.")
        search_id = db.add_search(**values)
        monitor._spawn(monitor.check_search_now(search_id))      # erster Durchlauf sofort
        return _search_view(db, db.search(search_id))

    @app.patch("/api/searches/{search_id}")
    async def patch_search(search_id: int, body: SearchPatch):
        if not db.search(search_id):
            raise HTTPException(404, "Nicht gefunden")
        values = _search_values(body.model_dump(exclude_unset=True), partial=True)
        if values.keys() & {"query", "ptype", "lang", "max_price", "exclude"}:
            # Andere Filter = andere Treffer: neu und still erfassen, statt alles als „neu“ zu melden
            db.clear_hits(search_id)
            values.update(baseline="[]", next_check=0)
        if "enabled" in values:
            values["next_check"] = 0
        db.update_search(search_id, **values)
        return _search_view(db, db.search(search_id))

    @app.delete("/api/searches/{search_id}")
    async def delete_search(search_id: int):
        db.delete_search(search_id)
        return {"ok": True}

    @app.post("/api/searches/{search_id}/check")
    async def check_search(search_id: int):
        if not await monitor.check_search_now(search_id):
            raise HTTPException(404, "Nicht gefunden")
        return _search_view(db, db.search(search_id))

    @app.post("/api/hits/{hit_id}/watch")
    async def watch_hit(hit_id: int):
        hit = db.hit(hit_id)
        if not hit:
            raise HTTPException(404, "Nicht gefunden")
        if hit["watch_id"] and db.watch(hit["watch_id"]):
            return db.watch(hit["watch_id"])
        result = await analyze(hit["url"], fetcher)
        if result.status == "error":
            raise HTTPException(400, result.reason or "Seite nicht lesbar")
        search_row = db.search(hit["search_id"])
        watch = watches.add_watch(db, result, priority=search_row["priority"] if search_row else "laut",
                                  note="Aus Suchtreffer übernommen")
        db.update_hit(hit_id, watch_id=watch["id"])
        return watch

    # ------------------------------------------------------------------ Produkte
    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        url = normalize_url(body.url)
        if not urlsplit(url).hostname:
            raise HTTPException(400, "Das sieht nicht nach einem Link aus.")
        result = await analyze(url, fetcher)
        previews[url] = (time.time(), result)
        duplicate = next((w["id"] for w in db.watches() if w["url"] == url), None)
        return {**result.to_dict(), "duplicate_of": duplicate,
                "price_text": format_price(result.price, result.currency)}

    @app.post("/api/watches")
    async def add_watch(body: WatchIn):
        url = normalize_url(body.url)
        cached = previews.get(url)
        result = cached[1] if cached and time.time() - cached[0] < PREVIEW_TTL else await analyze(url, fetcher)
        return watches.add_watch(db, result, priority=body.priority, interval=body.interval,
                                 variant_id=body.variant_id)

    @app.patch("/api/watches/{watch_id}")
    async def patch_watch(watch_id: int, body: WatchPatch):
        if not db.watch(watch_id):
            raise HTTPException(404, "Nicht gefunden")
        values = body.model_dump(exclude_none=True)
        if "interval" in values:
            values["interval"] = max(MIN_INTERVAL, values["interval"])
        if "enabled" in values:
            values["enabled"] = int(values["enabled"])
            values["next_check"] = 0
        if values.get("priority") not in (None, "laut", "leise"):
            raise HTTPException(400, "Priorität muss „laut“ oder „leise“ sein")
        db.update_watch(watch_id, **values)
        return db.watch(watch_id)

    @app.delete("/api/watches/{watch_id}")
    async def delete_watch(watch_id: int):
        db.delete_watch(watch_id)
        return {"ok": True}

    @app.post("/api/watches/{watch_id}/check")
    async def check_watch(watch_id: int):
        watch = await monitor.check_now(watch_id)
        if not watch:
            raise HTTPException(404, "Nicht gefunden")
        return watch

    # ------------------------------------------------------------------ Einstellungen
    @app.put("/api/settings")
    async def put_settings(body: SettingsIn):
        for key, value in body.model_dump(exclude_none=True).items():
            if key == "default_interval":
                value = max(MIN_INTERVAL, value)
            if key == "heartbeat_time" and not _valid_time(value):
                raise HTTPException(400, "Uhrzeit bitte als HH:MM")
            db.set_setting(key, value)
        return _public_settings(db.settings())

    @app.post("/api/channels/ntfy")
    async def setup_ntfy(body: NtfyIn):
        conf = db.settings()["channels"]["ntfy"]
        server = (body.server or conf.get("server") or "https://ntfy.sh").rstrip("/")
        topic = conf.get("topic")
        if body.new_topic or not topic:
            topic = new_ntfy_topic()
        enabled = True if body.enabled is None else body.enabled
        db.update_channel("ntfy", server=server, topic=topic, enabled=enabled)
        links = ntfy_links(server, topic)
        return {"topic": topic, "server": server, **links, "qr": qr_svg(links["subscribe"]), "enabled": enabled}

    @app.post("/api/channels/telegram/connect")
    async def telegram_connect(body: TelegramIn):
        token = body.token.strip()
        try:
            bot_name = await notifier.telegram_bot_name(token)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if pairing.get("task"):
            pairing["task"].cancel()
        code = new_pairing_code()
        link = f"https://t.me/{bot_name}?start={code}"
        pairing.update(state="waiting", bot_name=bot_name)

        async def wait():
            chat_id = await notifier.telegram_wait_for_start(token, code)
            if not chat_id:
                pairing["state"] = "timeout"
                return
            conf = db.update_channel("telegram", token=token, chat_id=chat_id, bot_name=bot_name, enabled=True)
            pairing["state"] = "done"
            await notifier.send("telegram", conf, Message(
                "✅ Verbunden", "Ab jetzt bekommst du hier deine Restock-Meldungen.", loud=False, kind="test"))

        pairing["task"] = asyncio.create_task(wait())
        return {"bot_name": bot_name, "link": link, "qr": qr_svg(link)}

    @app.get("/api/channels/telegram/status")
    async def telegram_status():
        return {"state": pairing["state"], "bot_name": pairing["bot_name"]}

    @app.post("/api/channels/discord")
    async def setup_discord(body: DiscordIn):
        values = body.model_dump(exclude_none=True)
        hook = values.get("webhook")
        if hook is not None:
            hook = hook.strip()
            host = urlsplit(hook).hostname or ""
            if not (hook.startswith("https://") and host.endswith(("discord.com", "discordapp.com")) and "/api/webhooks/" in hook):
                raise HTTPException(400, "Das ist kein Discord-Webhook-Link (er beginnt mit https://discord.com/api/webhooks/…).")
            values["webhook"] = hook
            values.setdefault("enabled", True)
        db.update_channel("discord", **values)
        return _public_channels(db.settings()["channels"])["discord"]

    @app.post("/api/channels/{name}/enabled")
    async def toggle_channel(name: str, body: ToggleIn):
        if name not in ("ntfy", "telegram", "discord"):
            raise HTTPException(404, "Unbekannter Kanal")
        db.update_channel(name, enabled=body.enabled)
        return _public_channels(db.settings()["channels"])[name]

    @app.post("/api/channels/{name}/test")
    async def test_channel(name: str):
        conf = db.settings()["channels"].get(name)
        if not conf or not is_configured(name, conf):
            raise HTTPException(400, "Dieser Kanal ist noch nicht eingerichtet.")
        error = await notifier.send(name, conf, Message(
            "🟢 Verfügbar: Beispiel-Display (Test)",
            "149,99 € · beispiel-shop.de\nSo sieht eine echte Meldung aus. Wenn du das liest, klappt alles.",
            loud=True, kind="test"))
        if error:
            raise HTTPException(502, error)
        return {"ok": True}

    # ------------------------------------------------------------------ Handy & Autostart
    @app.get("/api/lan")
    async def lan_info():
        return _lan_info(db, controller)

    @app.post("/api/lan")
    async def lan_toggle(body: ToggleIn):
        settings = db.settings()
        if body.enabled and not settings["access_key"]:
            db.set_setting("access_key", secrets.token_urlsafe(16))
        db.set_setting("lan_enabled", body.enabled)
        if controller.restart:
            asyncio.get_running_loop().call_later(0.5, controller.restart)
        return {**_lan_info(db, controller), "restarting": bool(controller.restart)}

    @app.post("/api/autostart")
    async def autostart(body: ToggleIn):
        try:
            system.set_autostart(body.enabled)
        except (RuntimeError, OSError) as exc:
            raise HTTPException(400, str(exc))
        return {"supported": True, "enabled": system.autostart_enabled()}

    return app


# ---------------------------------------------------------------------- Hilfen

def _search_values(values: dict, partial: bool = False) -> dict:
    if "query" in values or not partial:
        query = (values.get("query") or "").strip()
        if not finder.alternatives(query):
            raise HTTPException(400, "Bitte einen Suchbegriff eingeben.")
        values["query"] = query
    if "ptype" in values and values["ptype"] not in finder.PRODUCT_TYPES:
        raise HTTPException(400, "Unbekannter Produkttyp")
    if "lang" in values and values["lang"] not in finder.LANGUAGES:
        raise HTTPException(400, "Unbekannte Sprache")
    if "priority" in values and values["priority"] not in ("laut", "leise"):
        raise HTTPException(400, "Priorität muss „laut“ oder „leise“ sein")
    if values.get("max_price") is not None and values["max_price"] <= 0:
        values["max_price"] = None
    for key in ("auto_watch", "enabled"):
        if key in values and values[key] is not None:
            values[key] = int(values[key])
    if values.get("interval") is not None:
        values["interval"] = max(60, values["interval"])
    elif "interval" in values:
        values.pop("interval")
    return values


def _search_view(db: Database, search: dict) -> dict:
    hits = db.hits(search["id"])
    return {**search, "hit_count": len(hits), "hits": hits[:30]}


def _public_settings(settings: dict) -> dict:
    return {k: v for k, v in settings.items() if k not in ("channels", "access_key", "last_heartbeat")}


def _public_channels(channels: dict) -> dict:
    ntfy, tg, dc = channels["ntfy"], channels["telegram"], channels["discord"]
    return {
        "ntfy": {"enabled": ntfy.get("enabled"), "configured": is_configured("ntfy", ntfy),
                 "server": ntfy.get("server"), "topic": ntfy.get("topic"),
                 **(ntfy_links(ntfy["server"], ntfy["topic"]) if ntfy.get("topic") else {}),
                 "qr": qr_svg(ntfy_links(ntfy["server"], ntfy["topic"])["subscribe"]) if ntfy.get("topic") else None},
        "telegram": {"enabled": tg.get("enabled"), "configured": is_configured("telegram", tg),
                     "bot_name": tg.get("bot_name")},
        "discord": {"enabled": dc.get("enabled"), "configured": is_configured("discord", dc),
                    "webhook_hint": ("…" + dc["webhook"][-6:]) if dc.get("webhook") else ""},
    }


def _lan_info(db: Database, controller: Controller) -> dict:
    settings = db.settings()
    ip = system.lan_ip()
    if not settings["lan_enabled"] or not ip:
        return {"enabled": settings["lan_enabled"], "available": bool(ip), "url": None, "qr": None}
    url = f"http://{ip}:{controller.port}/?key={settings['access_key']}"
    return {"enabled": True, "available": True, "url": f"http://{ip}:{controller.port}", "qr": qr_svg(url)}


def _valid_time(value: str) -> bool:
    try:
        hours, minutes = value.split(":")
        return len(hours) == 2 and len(minutes) == 2 and 0 <= int(hours) < 24 and 0 <= int(minutes) < 60
    except ValueError:
        return False


def _locked_page() -> str:
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Restock-Radar</title><body style='font-family:system-ui;padding:2rem;text-align:center'>"
            "<h2>Restock-Radar</h2><p>Scanne den QR-Code unter <b>Einstellungen → Handy</b> am PC, "
            "um dieses Handy freizuschalten.</p></body>")
