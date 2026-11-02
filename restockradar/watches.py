"""Legt aus einem Prüfergebnis ein überwachtes Produkt an – für Links und für Suchtreffer."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from .db import Database
from .detect import AVAILABLE, BUYABLE, SOLDOUT, Result

MIN_INTERVAL = 30


def add_watch(db: Database, result: Result, *, priority: str = "laut", interval: int | None = None,
              variant_id: str | None = None, note: str = "Wird jetzt überwacht") -> dict:
    variant_title, price, stock, cart_url = result.variant_title, result.price, result.status, result.cart_url
    variant = next((v for v in result.variants if v["id"] == variant_id), None) if variant_id else None
    if variant:
        parts = urlsplit(result.url)
        variant_title = variant["title"]
        price = variant["price"] if variant["price"] is not None else price
        stock = AVAILABLE if variant["available"] else SOLDOUT
        cart_url = f"{parts.scheme}://{parts.netloc}/cart/{variant['id']}:1"
    else:
        variant_id = result.variant_id
    interval = max(MIN_INTERVAL, interval or db.settings()["default_interval"])
    now = time.time()
    watch_id = db.add_watch(
        url=result.url, name=result.name, shop=result.shop, image=result.image, price=price,
        currency=result.currency, platform=result.platform, method=result.method,
        variant_id=variant_id, variant_title=variant_title, cart_url=cart_url,
        status=stock, stock=stock if stock in (*BUYABLE, SOLDOUT) else None,
        confidence=result.confidence, rating=result.rating, reason=result.reason, note=result.note,
        priority=priority if priority in ("laut", "leise") else "laut",
        interval=interval, last_check=now, next_check=now + interval)
    db.add_event(watch_id, "info", note)
    return db.watch(watch_id)
