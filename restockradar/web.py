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

from . import APP_NAME, VERSION, system
from .db import Database
from .detect import AVAILABLE, BUYABLE, SOLDOUT, Result, analyze, normalize_url
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
        }

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
        variant_id, variant_title, price, stock = result.variant_id, result.variant_title, result.price, result.status
        cart_url = result.cart_url
        if body.variant_id and result.variants:
            variant = next((v for v in result.variants if v["id"] == body.variant_id), None)
            if variant:
                parts = urlsplit(url)
                variant_id, variant_title = variant["id"], variant["title"]
                price = variant["price"] if variant["price"] is not None else price
                stock = AVAILABLE if variant["available"] else SOLDOUT
                cart_url = f"{parts.scheme}://{parts.netloc}/cart/{variant['id']}:1"
        interval = max(MIN_INTERVAL, body.interval or db.settings()["default_interval"])
        watch_id = db.add_watch(
            url=url, name=result.name, shop=result.shop, image=result.image, price=price,
            currency=result.currency, platform=result.platform, method=result.method,
            variant_id=variant_id, variant_title=variant_title, cart_url=cart_url,
            status=stock, stock=stock if stock in (*BUYABLE, SOLDOUT) else None,
            confidence=result.confidence, rating=result.rating, reason=result.reason, note=result.note,
            priority=body.priority if body.priority in ("laut", "leise") else "laut",
            interval=interval, last_check=time.time(), next_check=time.time() + interval)
        db.add_event(watch_id, "info", "Wird jetzt überwacht")
        return db.watch(watch_id)

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
