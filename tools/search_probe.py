"""Zeigt, was ein Suchauftrag in einem Shop findet und warum Treffer durchfallen.

  python tools/search_probe.py <shop-link> "<Suchbegriff>" [produkttyp] [sprache]
  z. B. python tools/search_probe.py cardcosmos.de "Prismatische Entwicklungen" etb de
"""
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restockradar import search                   # noqa: E402
from restockradar.fetcher import Fetcher          # noqa: E402


async def main(shop_url: str, query: str, ptype: str = "egal", lang: str = "egal") -> None:
    fetcher = Fetcher()
    info = await search.detect_shop(shop_url, fetcher)
    print(f"Shop: {info.name} · {info.platform or 'unbekannt'} · Modus {info.mode}\nSuche: {info.search_url}")
    for note in info.notes:
        print("Hinweis:", note)
    criteria = {"query": query, "ptype": ptype, "lang": lang, "max_price": None, "exclude": ""}
    items = await search.search_shop(asdict(info), criteria, fetcher)
    print(f"\n{len(items)} Ergebnisse vom Shop, passend: {sum(search.matches(i, criteria) for i in items)}")
    for item in items:
        mark = "✓" if search.matches(item, criteria) else "✗"
        price = f"{item.price:.2f} €" if item.price is not None else "–"
        print(f"  {mark} {item.status:<9} {price:>10}  {item.title[:80]}")
    await fetcher.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(*sys.argv[1:5]))
