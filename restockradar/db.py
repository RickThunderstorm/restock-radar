"""Kleine SQLite-Ablage für beobachtete Seiten, Verlauf und Einstellungen."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id            INTEGER PRIMARY KEY,
    url           TEXT NOT NULL,
    name          TEXT,
    shop          TEXT,
    image         TEXT,
    price         REAL,
    currency      TEXT,
    platform      TEXT,
    method        TEXT,
    variant_id    TEXT,
    variant_title TEXT,
    cart_url      TEXT,
    status        TEXT DEFAULT 'unknown',
    stock         TEXT,             -- letzter gültiger Lagerstand (ohne Fehler/Sperren)
    confidence    TEXT,
    rating        TEXT,
    reason        TEXT,
    note          TEXT,
    priority      TEXT DEFAULT 'laut',
    enabled       INTEGER DEFAULT 1,
    interval      INTEGER DEFAULT 60,
    error_count   INTEGER DEFAULT 0,
    warned        INTEGER DEFAULT 0,
    backoff       INTEGER DEFAULT 0,
    last_check    REAL,
    last_change   REAL,
    last_notified REAL,
    last_queue    REAL,
    next_check    REAL DEFAULT 0,
    created       REAL
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    watch_id INTEGER,
    ts       REAL,
    kind     TEXT,
    text     TEXT,
    old      TEXT,
    new      TEXT,
    price    REAL
);
CREATE INDEX IF NOT EXISTS events_watch ON events (watch_id, ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS shops (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    base          TEXT NOT NULL UNIQUE,
    platform      TEXT,
    mode          TEXT,             -- shopify | woo | html
    search_url    TEXT,             -- Vorlage mit {q}
    enabled       INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'ok',
    reason        TEXT,
    paused_until  REAL DEFAULT 0,   -- bei Drosselung/Sperre eine Weile in Ruhe lassen
    last_check    REAL,
    created       REAL
);
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY,
    query         TEXT NOT NULL,
    ptype         TEXT DEFAULT 'egal',
    lang          TEXT DEFAULT 'egal',
    max_price     REAL,
    exclude       TEXT,
    priority      TEXT DEFAULT 'laut',
    auto_watch    INTEGER DEFAULT 0,
    enabled       INTEGER DEFAULT 1,
    interval      INTEGER DEFAULT 180,
    baseline      TEXT DEFAULT '[]', -- Shops, deren vorhandene Treffer schon still erfasst sind
    last_check    REAL,
    next_check    REAL DEFAULT 0,
    created       REAL
);
CREATE TABLE IF NOT EXISTS hits (
    id            INTEGER PRIMARY KEY,
    search_id     INTEGER NOT NULL,
    shop_id       INTEGER,
    url           TEXT NOT NULL,
    title         TEXT,
    price         REAL,
    currency      TEXT,
    image         TEXT,
    status        TEXT,
    is_new        INTEGER DEFAULT 0,  -- nach dem ersten Durchlauf neu aufgetaucht
    watch_id      INTEGER,
    first_seen    REAL,
    last_seen     REAL,
    last_change   REAL,
    UNIQUE (search_id, url)
);
"""

SHOP_FIELDS = {"name", "base", "platform", "mode", "search_url", "enabled", "status", "reason",
               "paused_until", "last_check"}
SEARCH_FIELDS = {"query", "ptype", "lang", "max_price", "exclude", "priority", "auto_watch", "enabled",
                 "interval", "baseline", "last_check", "next_check"}
HIT_FIELDS = {"search_id", "shop_id", "url", "title", "price", "currency", "image", "status", "is_new",
              "watch_id", "first_seen", "last_seen", "last_change"}

DEFAULT_SETTINGS = {
    "setup_done": False,
    "default_interval": 60,
    "heartbeat_enabled": True,
    "heartbeat_time": "09:00",
    "last_heartbeat": "",
    "lan_enabled": False,
    "access_key": "",
    "channels": {
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": ""},
        "telegram": {"enabled": False, "token": "", "chat_id": "", "bot_name": ""},
        "discord": {"enabled": False, "webhook": ""},
    },
}

WATCH_FIELDS = {
    "url", "name", "shop", "image", "price", "currency", "platform", "method", "variant_id",
    "variant_title", "cart_url", "status", "stock", "confidence", "rating", "reason", "note",
    "priority", "enabled", "interval", "error_count", "warned", "backoff", "last_check",
    "last_change", "last_notified", "last_queue", "next_check",
}


class Database:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _rows(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ Seiten
    def add_watch(self, **values) -> int:
        values = {k: v for k, v in values.items() if k in WATCH_FIELDS}
        values["created"] = time.time()
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self._exec(f"INSERT INTO watches ({cols}) VALUES ({marks})", tuple(values.values())).lastrowid

    def update_watch(self, watch_id: int, **values) -> None:
        values = {k: v for k, v in values.items() if k in WATCH_FIELDS}
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self._exec(f"UPDATE watches SET {sets} WHERE id = ?", (*values.values(), watch_id))

    def delete_watch(self, watch_id: int) -> None:
        self._exec("DELETE FROM watches WHERE id = ?", (watch_id,))
        self._exec("DELETE FROM events WHERE watch_id = ?", (watch_id,))

    def watch(self, watch_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM watches WHERE id = ?", (watch_id,))
        return rows[0] if rows else None

    def watches(self) -> list[dict]:
        return self._rows("SELECT * FROM watches ORDER BY created DESC")

    def due_watches(self, now: float) -> list[dict]:
        return self._rows("SELECT * FROM watches WHERE enabled = 1 AND next_check <= ? ORDER BY next_check", (now,))

    # ------------------------------------------------------------------ Shops, Suchaufträge, Treffer
    def _insert(self, table: str, allowed: set[str], values: dict) -> int:
        values = {k: v for k, v in values.items() if k in allowed}
        values["created"] = time.time()
        if table == "hits":
            values.pop("created")
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self._exec(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())).lastrowid

    def _update(self, table: str, allowed: set[str], row_id: int, values: dict) -> None:
        values = {k: v for k, v in values.items() if k in allowed}
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self._exec(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))

    def add_shop(self, **values) -> int:
        return self._insert("shops", SHOP_FIELDS, values)

    def update_shop(self, shop_id: int, **values) -> None:
        self._update("shops", SHOP_FIELDS, shop_id, values)

    def delete_shop(self, shop_id: int) -> None:
        self._exec("DELETE FROM shops WHERE id = ?", (shop_id,))
        self._exec("DELETE FROM hits WHERE shop_id = ?", (shop_id,))

    def shop(self, shop_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM shops WHERE id = ?", (shop_id,))
        return rows[0] if rows else None

    def shops(self) -> list[dict]:
        return self._rows("SELECT * FROM shops ORDER BY name COLLATE NOCASE")

    def shop_by_base(self, base: str) -> dict | None:
        rows = self._rows("SELECT * FROM shops WHERE base = ?", (base,))
        return rows[0] if rows else None

    def add_search(self, **values) -> int:
        return self._insert("searches", SEARCH_FIELDS, values)

    def update_search(self, search_id: int, **values) -> None:
        self._update("searches", SEARCH_FIELDS, search_id, values)

    def delete_search(self, search_id: int) -> None:
        self._exec("DELETE FROM searches WHERE id = ?", (search_id,))
        self._exec("DELETE FROM hits WHERE search_id = ?", (search_id,))

    def search(self, search_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM searches WHERE id = ?", (search_id,))
        return rows[0] if rows else None

    def searches(self) -> list[dict]:
        return self._rows("SELECT * FROM searches ORDER BY created DESC")

    def due_searches(self, now: float) -> list[dict]:
        return self._rows("SELECT * FROM searches WHERE enabled = 1 AND next_check <= ? ORDER BY next_check", (now,))

    def clear_hits(self, search_id: int) -> None:
        self._exec("DELETE FROM hits WHERE search_id = ?", (search_id,))

    def add_hit(self, **values) -> int:
        return self._insert("hits", HIT_FIELDS, values)

    def update_hit(self, hit_id: int, **values) -> None:
        self._update("hits", HIT_FIELDS, hit_id, values)

    def hit(self, hit_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM hits WHERE id = ?", (hit_id,))
        return rows[0] if rows else None

    def hit_by_url(self, search_id: int, url: str) -> dict | None:
        rows = self._rows("SELECT * FROM hits WHERE search_id = ? AND url = ?", (search_id, url))
        return rows[0] if rows else None

    def hits(self, search_id: int) -> list[dict]:
        return self._rows(
            "SELECT h.*, s.name AS shop_name FROM hits h LEFT JOIN shops s ON s.id = h.shop_id "
            "WHERE h.search_id = ? ORDER BY "
            "CASE h.status WHEN 'available' THEN 0 WHEN 'preorder' THEN 1 ELSE 2 END, h.first_seen DESC",
            (search_id,))

    # ------------------------------------------------------------------ Verlauf
    def add_event(self, watch_id: int | None, kind: str, text: str, old: str | None = None,
                  new: str | None = None, price: float | None = None) -> None:
        self._exec("INSERT INTO events (watch_id, ts, kind, text, old, new, price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (watch_id, time.time(), kind, text, old, new, price))
        self._exec("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY ts DESC LIMIT 2000)")

    def events(self, limit: int = 60) -> list[dict]:
        return self._rows("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ Einstellungen
    def settings(self) -> dict:
        stored = {r["key"]: json.loads(r["value"]) for r in self._rows("SELECT key, value FROM settings")}
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        for key, value in stored.items():
            if key == "channels" and isinstance(value, dict):
                for name, conf in value.items():
                    merged["channels"].setdefault(name, {}).update(conf)
            else:
                merged[key] = value
        return merged

    def set_setting(self, key: str, value) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    def update_channel(self, name: str, **values) -> dict:
        channels = self.settings()["channels"]
        channels.setdefault(name, {}).update(values)
        self.set_setting("channels", channels)
        return channels[name]
