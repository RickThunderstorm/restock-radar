"""Zeigt, was Restock-Radar auf einer Produktseite findet: python tools/probe.py <link> [<link> …]"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restockradar.detect import analyze          # noqa: E402
from restockradar.fetcher import Fetcher         # noqa: E402


async def main(urls: list[str]) -> None:
    fetcher = Fetcher()
    for url in urls:
        res = await analyze(url, fetcher)
        data = res.to_dict()
        print(f"\n=== {url}")
        for key in ("status", "method", "confidence", "rating", "reason", "note", "name", "price",
                    "currency", "platform", "variant_id", "cart_url", "image"):
            print(f"{key:>11}: {data[key]}")
        for sig in data["signals"]:
            print(f"     signal: {sig['label']:<28} {sig['status']:<10} {sig['detail']}")
        if data["variants"]:
            print("   variants:", json.dumps(data["variants"], ensure_ascii=False))
    await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
