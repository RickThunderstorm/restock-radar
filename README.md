# Restock-Radar

Behält ausverkaufte Produkte im Auge und schlägt auf dem Handy Alarm, sobald sie wieder da sind – mit Bild, Preis und „Direkt zur Kasse“-Knopf.

## Für den Alltag (Windows)

1. `RestockRadar.exe` irgendwohin legen (z. B. `Dokumente\RestockRadar`) und doppelklicken.
2. Der Browser öffnet sich mit dem **Einrichtungs-Assistenten**: Benachrichtigungsweg wählen (empfohlen: **ntfy**-App), QR-Code mit dem Handy scannen, „Test senden“.
3. Produktlink oben einfügen → Restock-Radar prüft die Seite, zeigt Bild, Preis, Status und wie gut sie sich überwachen lässt → **Überwachen**.

Das Programm sitzt danach unten rechts neben der Uhr (Radar-Symbol). Rechtsklick → *Öffnen* oder *Beenden*.
In den Einstellungen: **Mit Windows starten** einschalten, dann läuft es nach jedem Neustart automatisch.

### Benachrichtigungswege

| Weg | Einrichtung | Besonderheit |
|---|---|---|
| **ntfy** (empfohlen) | App installieren, QR-Code scannen (iPhone: Thema abtippen) | „Laut“ = höchste Priorität, klingelt auch bei „Nicht stören“, wenn man das in der App erlaubt |
| **Telegram** | Im BotFather `/newbot`, Token einfügen, QR scannen, „Starten“ tippen | Bild + Knöpfe „Direkt zur Kasse“ / „Zum Produkt“ |
| **Discord** | Webhook-Link aus den Kanal-Einstellungen einfügen | „Laut“ erwähnt @everyone |

Pro Produkt lässt sich **Laut** oder **Leise** wählen (Glocken-Symbol).

### Vom Handy aus

Einstellungen → **Handy-Zugriff im WLAN** einschalten → QR-Code scannen. Das Handy wird dauerhaft freigeschaltet, fremde Geräte im WLAN kommen ohne den Code nicht rein. Beim ersten Mal fragt Windows nach der Firewall: Zugriff in **privaten Netzwerken** erlauben.

## Wie die Erkennung funktioniert

Restock-Radar braucht keine Stichwörter. Es wertet mehrere Signale aus und nimmt das verlässlichste:

1. **Shopify-Lagerfeld** (`/products/<name>.js`) – nur wenn der Shop es nicht drosselt
2. **schema.org-Daten** (für Google), bei Shopify sogar pro Variante
3. **Meta-Tags** wie `product:availability`
4. **Warenkorb-Button** der gängigen Shopsysteme (aktiv, ausgegraut, „Vorbestellen“, „Ausverkauft“)
5. **Seitentext** als letzte Möglichkeit

Widersprechen sich die Signale (manche Shops melden Google dauerhaft „auf Lager“), beobachtet es das vorsichtigere – das ist meist das, was beim Restock umspringt. Unter „Was ich auf der Seite gefunden habe“ steht jedes Signal einzeln.

Außerdem erkannt: **Vorbestellung**, **Warteschlangen** (Queue-it – oft das Zeichen, dass ein Drop läuft), **Bot-Schutz** (Cloudflare, Akamai, DataDome …) und **Weiterleitungen auf die Startseite**.

### Fair zu den Shops

- Höchstens alle 30 Sekunden pro Produkt, mit zufälliger Streuung, mindestens 3 Sekunden Abstand pro Shop.
- Bremst ein Shop (Fehler 429) oder sperrt er, wartet Restock-Radar doppelt so lange (bis 30 Minuten) und hält sich an `Retry-After`.
- Bot-Schutz und Warteschlangen werden **nicht** umgangen, und es wird nichts automatisch gekauft – das verstößt gegen die AGB der Shops.

Große Händler (Amazon, MediaMarkt, Kaufland, eBay …) sperren automatische Abrufe meist. Kleine Fachhändler funktionieren in der Regel sehr gut – und sind bei Restocks oft die ersten.

## Raspberry Pi / Linux

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --headless --lan
```

`--lan` macht Restock-Radar im Heimnetz erreichbar und gibt beim Start den Link samt Zugangsschlüssel aus. Als Dienst, der beim Hochfahren startet (`/etc/systemd/system/restockradar.service`):

```ini
[Unit]
Description=Restock-Radar
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/restock-radar
ExecStart=/home/pi/restock-radar/.venv/bin/python run.py --headless --lan
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now restockradar
```

## Daten

Alles liegt lokal: unter Windows in `%APPDATA%\RestockRadar`, unter Linux in `~/.restockradar` (Datenbank, Logdatei). Nichts wird an fremde Server geschickt außer den Benachrichtigungen an den gewählten Dienst.

## Entwicklung

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest            # Tests
.\.venv\Scripts\python run.py               # starten (mit Symbol neben der Uhr)
.\build.ps1                                  # baut dist\RestockRadar.exe
```

Der Projektpfad muss kurz sein (z. B. `F:\restock-radar`), sonst kann Windows die Bibliotheken nicht laden.

Werkzeuge:

- `tools/probe.py <link>` – zeigt, welche Signale Restock-Radar auf einer Seite findet.
- `tools/fake_shop.py` – Mini-Testshop mit umschaltbarem Lager plus Mini-ntfy, um einen Restock gefahrlos durchzuspielen.
- `tools/raw.py <link>` – rohe Antwort eines Shops (Status, Weiterleitung, Bot-Schutz).

Startoptionen: `--headless` (ohne Symbol), `--minimized` (Browser nicht öffnen), `--lan`, `--port`, `--data`.
