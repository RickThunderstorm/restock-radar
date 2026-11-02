"""Zeigt, was Restock-Radar auf einer Kategorie-Seite findet (inkl. Erscheinungsterminen).

  python tools/category_probe.py <kategorie-link> [produkttyp] [sprache] [suchbegriff]
  z. B. python tools/category_probe.py https://www.gate-to-the-games.de/Pokemon-Karten/Pokemon-Displays/ display de
"""
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restockradar import search                   # noqa: E402
from restockradar.fetcher import Fetcher          # noqa: E402


async def main(url: str, ptype: str = "egal", lang: str = "egal", query: str = "") -> None:
    fetcher = Fetcher()
    items = await search.fetch_category(url, fetcher)
    criteria = {"query": query, "ptype": ptype, "lang": lang, "max_price": None, "exclude": ""}
    hits = [i for i in items if search.matches(i, criteria)]
    print(f"{len(items)} Produkte gelesen, {len(hits)} passen · Status {dict(Counter(i.status for i in hits))}")
    for item in items:
        mark = "✓" if item in hits else "✗"
        price = f"{item.price:.2f} €" if item.price is not None else "–"
        release = f"  [erscheint {item.release}]" if item.release else ""
        print(f"  {mark} {item.status:<9} {price:>10}  {item.title[:70]}{release}")
    await fetcher.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(*sys.argv[1:5]))
