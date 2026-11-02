"""Benachrichtigungen über ntfy, Telegram und Discord – plus Kopplung per QR-Code."""
from __future__ import annotations

import asyncio
import html
import io
import secrets
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
import qrcode
import qrcode.image.svg

from . import APP_NAME

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class Message:
    title: str
    text: str
    url: str | None = None
    cart_url: str | None = None
    image: str | None = None
    loud: bool = True
    kind: str = "restock"       # restock | queue | info | warning | test


class Notifier:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(timeout=15, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def send_all(self, channels: dict, msg: Message) -> dict[str, str | None]:
        """Schickt an alle aktiven Kanäle. Ergebnis: Kanal -> None (ok) oder Fehlertext."""
        active = {name: conf for name, conf in channels.items() if conf.get("enabled") and is_configured(name, conf)}
        results = await asyncio.gather(*(self.send(name, conf, msg) for name, conf in active.items()))
        return dict(zip(active, results))

    async def send(self, name: str, conf: dict, msg: Message) -> str | None:
        try:
            if name == "ntfy":
                await self._ntfy(conf, msg)
            elif name == "telegram":
                await self._telegram(conf, msg)
            elif name == "discord":
                await self._discord(conf, msg)
            else:
                return f"Unbekannter Kanal {name}"
            return None
        except httpx.HTTPStatusError as exc:
            return f"{name} antwortet mit Fehler {exc.response.status_code}: {_short(exc.response.text)}"
        except httpx.HTTPError as exc:
            return f"{name} nicht erreichbar ({type(exc).__name__})"

    # ------------------------------------------------------------------ ntfy
    async def _ntfy(self, conf: dict, msg: Message) -> None:
        payload = {
            "topic": conf["topic"],
            "title": msg.title,
            "message": msg.text,
            "priority": 5 if msg.loud else 3,
            "tags": {"restock": ["green_circle"], "queue": ["hourglass"], "warning": ["warning"],
                     "test": ["white_check_mark"]}.get(msg.kind, []),
        }
        if msg.url:
            payload["click"] = msg.url
        actions = [{"action": "view", "label": "Zum Produkt", "url": msg.url}] if msg.url else []
        if msg.cart_url:
            actions.append({"action": "view", "label": "Direkt zur Kasse", "url": msg.cart_url})
        if actions:
            payload["actions"] = actions
        if msg.image:
            payload["attach"] = msg.image
        resp = await self._client.post(conf.get("server", "https://ntfy.sh").rstrip("/") + "/", json=payload)
        resp.raise_for_status()

    # ------------------------------------------------------------------ Telegram
    async def _telegram(self, conf: dict, msg: Message) -> None:
        base = f"{TELEGRAM_API}/bot{conf['token']}"
        text = f"<b>{html.escape(msg.title)}</b>\n{html.escape(msg.text)}"
        buttons = [[{"text": "🛒 Direkt zur Kasse", "url": msg.cart_url}]] if msg.cart_url else []
        if msg.url:
            buttons.append([{"text": "Zum Produkt", "url": msg.url}])
        common = {"chat_id": conf["chat_id"], "parse_mode": "HTML", "disable_notification": not msg.loud}
        if buttons:
            common["reply_markup"] = {"inline_keyboard": buttons}
        if msg.image:
            resp = await self._client.post(f"{base}/sendPhoto", json={**common, "photo": msg.image, "caption": text[:1024]})
            if resp.status_code == 200:
                return
            # Bild konnte Telegram nicht laden – dann eben ohne Bild
        resp = await self._client.post(f"{base}/sendMessage", json={**common, "text": text, "link_preview_options": {"is_disabled": True}})
        resp.raise_for_status()

    # ------------------------------------------------------------------ Discord
    async def _discord(self, conf: dict, msg: Message) -> None:
        color = {"restock": 0x2EA043, "queue": 0xD29922, "warning": 0xD29922, "test": 0x3B82F6}.get(msg.kind, 0x6B7280)
        lines = [msg.text]
        if msg.cart_url:
            lines.append(f"[🛒 Direkt zur Kasse]({msg.cart_url})")
        embed = {"title": msg.title[:256], "description": "\n".join(lines)[:4000], "color": color}
        if msg.url:
            embed["url"] = msg.url
        if msg.image:
            embed["thumbnail"] = {"url": msg.image}
        payload = {"username": APP_NAME, "embeds": [embed],
                   "content": "@everyone" if msg.loud and msg.kind in ("restock", "queue") else "",
                   "allowed_mentions": {"parse": ["everyone"] if msg.loud else []}}
        resp = await self._client.post(conf["webhook"], json=payload)
        resp.raise_for_status()

    # ------------------------------------------------------------------ Telegram-Kopplung
    async def telegram_bot_name(self, token: str) -> str:
        resp = await self._client.get(f"{TELEGRAM_API}/bot{token}/getMe")
        if resp.status_code != 200:
            raise ValueError("Dieser Token funktioniert nicht. Bitte nochmal aus dem BotFather kopieren.")
        return resp.json()["result"]["username"]

    async def telegram_wait_for_start(self, token: str, code: str, timeout: float = 600) -> str | None:
        """Wartet, bis jemand im Bot auf „Starten“ tippt, und liefert dessen Chat-ID."""
        base = f"{TELEGRAM_API}/bot{token}"
        offset = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                resp = await self._client.get(f"{base}/getUpdates", params={"offset": offset, "timeout": 20},
                                              timeout=30)
                updates = resp.json().get("result", []) if resp.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                if (message.get("text") or "").strip() == f"/start {code}":
                    # Update bestätigen, damit es nicht erneut ausgeliefert wird
                    await self._client.get(f"{base}/getUpdates", params={"offset": offset, "timeout": 0})
                    return str(message["chat"]["id"])
            if not updates:
                await asyncio.sleep(1)
        return None


def is_configured(name: str, conf: dict) -> bool:
    if name == "ntfy":
        return bool(conf.get("topic"))
    if name == "telegram":
        return bool(conf.get("token") and conf.get("chat_id"))
    if name == "discord":
        return str(conf.get("webhook", "")).startswith("https://")
    return False


def new_ntfy_topic() -> str:
    return "restock-" + secrets.token_hex(6)


def ntfy_links(server: str, topic: str) -> dict:
    host = urlsplit(server).netloc or "ntfy.sh"
    return {
        "subscribe": f"ntfy://{host}/{topic}?display={APP_NAME}",
        "web": f"{server.rstrip('/')}/{topic}",
    }


def new_pairing_code() -> str:
    return secrets.token_hex(4)


@lru_cache(maxsize=32)
def qr_svg(data: str) -> str:
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def _short(text: str) -> str:
    return text.strip().replace("\n", " ")[:160]
