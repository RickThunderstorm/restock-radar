"""Mini-Testshop + Mini-ntfy auf dem eigenen Rechner, um einen Restock gefahrlos durchzuspielen.

  python tools/fake_shop.py            startet auf http://127.0.0.1:8900
  Produkt:        http://127.0.0.1:8900/products/test-display
  Lager umschalten: http://127.0.0.1:8900/toggle
  ntfy-Server in Restock-Radar auf http://127.0.0.1:8900 stellen – Nachrichten landen in der Konsole.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"available": False, "received": []}


def product_page() -> str:
    availability = "InStock" if STATE["available"] else "OutOfStock"
    button = ('<button type="submit" name="add">In den Warenkorb</button>' if STATE["available"]
              else '<button type="submit" name="add" disabled>Ausverkauft</button>')
    data = {"@context": "https://schema.org", "@type": "Product", "name": "Test-Display (Restock-Probe)",
            "offers": {"@type": "Offer", "price": "149.99", "priceCurrency": "EUR",
                       "availability": f"https://schema.org/{availability}"}}
    return (f"<html><head><title>Test-Display</title><script type='application/ld+json'>{json.dumps(data)}</script>"
            f"</head><body><main><h1>Test-Display</h1><form action='/cart/add'>{button}</form></main></body></html>")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/products/test-display"):
            self._send(200, product_page())
        elif self.path == "/toggle":
            STATE["available"] = not STATE["available"]
            self._send(200, json.dumps({"available": STATE["available"]}), "application/json")
        elif self.path == "/received":
            self._send(200, json.dumps(STATE["received"], ensure_ascii=False), "application/json")
        else:
            self._send(404, "nicht da")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        STATE["received"].append(payload)
        print("ntfy-Nachricht:", json.dumps(payload, ensure_ascii=False), flush=True)
        self._send(200, "{}", "application/json")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("Testshop läuft auf http://127.0.0.1:8900/products/test-display", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8900), Handler).serve_forever()
