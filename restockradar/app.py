"""Startet Restock-Radar: Webserver im Hintergrund, Symbol neben der Uhr, Browser auf."""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler

import httpx
import uvicorn

from . import APP_NAME, system
from .db import Database
from .paths import data_dir
from .web import Controller, create_app

log = logging.getLogger("restockradar")
PORTS = range(8787, 8797)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="restockradar", description=f"{APP_NAME} starten")
    parser.add_argument("--port", type=int, help="fester Port (sonst ab 8787)")
    parser.add_argument("--headless", action="store_true", help="ohne Symbol neben der Uhr (z. B. Raspberry Pi)")
    parser.add_argument("--minimized", action="store_true", help="Browser beim Start nicht öffnen")
    parser.add_argument("--data", help="anderer Datenordner (z. B. zum Testen)")
    parser.add_argument("--lan", action="store_true", help="im Heimnetz erreichbar machen (für Geräte ohne Bildschirm)")
    args = parser.parse_args(argv)

    if args.data:
        os.environ["RESTOCKRADAR_DATA"] = args.data
    folder = data_dir()
    _setup_logging(folder)
    if args.lan:
        _enable_lan(folder)

    existing = _find_running(args.port, folder)
    if existing:
        log.info("Läuft schon auf Port %s – öffne nur den Browser", existing)
        if not args.minimized:
            webbrowser.open(f"http://127.0.0.1:{existing}/")
        return

    port = args.port or next((p for p in PORTS if _port_free(p)), None)
    if port is None:
        log.error("Kein freier Port zwischen %s und %s", PORTS[0], PORTS[-1])
        sys.exit(1)

    (folder / "instance.port").write_text(str(port), encoding="utf-8")
    runner = ServerRunner(folder, port)
    if not args.minimized:
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()

    if args.headless:
        settings = Database(folder / "restockradar.db").settings()
        ip = system.lan_ip()
        if settings["lan_enabled"] and ip:
            print(f"{APP_NAME} läuft. Im Heimnetz öffnen: http://{ip}:{port}/?key={settings['access_key']}")
        else:
            print(f"{APP_NAME} läuft auf http://127.0.0.1:{port}/")
        print("Strg+C beendet.")
        runner.run()
        return

    threading.Thread(target=runner.run, daemon=True).start()
    from .tray import run_tray
    run_tray(port, on_quit=runner.stop)


class ServerRunner:
    """Hält den Webserver am Laufen und startet ihn neu, wenn sich die Adresse ändert."""

    def __init__(self, folder, port: int):
        self.folder = folder
        self.controller = Controller()
        self.controller.port = port
        self.controller.restart = self.restart
        self._server: uvicorn.Server | None = None
        self._restart = False
        self._stopped = False

    def run(self) -> None:
        while not self._stopped:
            lan = Database(self.folder / "restockradar.db").settings()["lan_enabled"]
            host = "0.0.0.0" if lan else "127.0.0.1"
            config = uvicorn.Config(create_app(self.folder, self.controller), host=host,
                                    port=self.controller.port, log_config=None, access_log=False)
            self._server = uvicorn.Server(config)
            self._restart = False
            log.info("Server startet auf %s:%s", host, self.controller.port)
            self._server.run()
            if not self._restart:
                break

    def restart(self) -> None:
        self._restart = True
        if self._server:
            self._server.should_exit = True

    def stop(self) -> None:
        self._stopped = True
        if self._server:
            self._server.should_exit = True


def _enable_lan(folder) -> None:
    db = Database(folder / "restockradar.db")
    if not db.settings()["access_key"]:
        db.set_setting("access_key", secrets.token_urlsafe(16))
    db.set_setting("lan_enabled", True)


def _setup_logging(folder) -> None:
    if sys.stdout is None:          # .exe ohne Konsolenfenster
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    handlers: list[logging.Handler] = [RotatingFileHandler(folder / "restockradar.log", maxBytes=1_000_000,
                                                           backupCount=2, encoding="utf-8")]
    if sys.stderr and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _ping(port: int) -> bool:
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/api/ping", timeout=1)
        return resp.status_code == 200 and resp.json().get("app") == APP_NAME
    except (httpx.HTTPError, ValueError):
        return False


def _find_running(port: int | None, folder) -> int | None:
    """Läuft schon eine Instanz mit diesem Datenordner? Dann keine zweite starten (doppelte Meldungen)."""
    candidates = [port] if port else []
    try:
        candidates.append(int((folder / "instance.port").read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    for p in [*candidates, *PORTS]:
        if not _port_free(p) and _ping(p):
            return p
    return None


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _open_when_ready(port: int) -> None:
    for _ in range(60):
        if _ping(port):
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        time.sleep(0.5)
