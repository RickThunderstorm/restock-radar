"""Zeigt die rohe Antwort eines Shops (Status, Ziel-URL, Anfang des Inhalts): python tools/raw.py <link>"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restockradar.fetcher import Fetcher         # noqa: E402


async def main(url: str) -> None:
    fetcher = Fetcher()
    page = await fetcher.get(url)
    print(page.status, page.url, "block:", page.block, "queue:", page.queue)
    print({k: v for k, v in page.headers.items() if k.lower() in ("server", "content-type", "x-shopid", "powered-by", "retry-after")})
    print(page.text[:600])
    await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
