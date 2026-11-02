"""Wo Restock-Radar seine Daten ablegt."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("RESTOCKRADAR_DATA")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "RestockRadar"
    else:
        base = Path.home() / ".restockradar"
    base.mkdir(parents=True, exist_ok=True)
    return base


def static_dir() -> Path:
    # In der .exe liegen die Dateien im Entpack-Ordner von PyInstaller.
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "restockradar" / "static"
