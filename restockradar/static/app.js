"use strict";

/* ================================================================ Grundlagen */

const ICONS = {
  settings: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  bellOff: '<path d="M8.7 3A6 6 0 0 1 18 8c0 2 .2 3.6.6 5"/><path d="M17 17H3s3-2 3-9c0-.6.1-1.2.3-1.7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="m2 2 20 20"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.6L21 7H6"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  hourglass: '<path d="M6 2h12M6 22h12M8 2v4l4 6 4-6V2M8 22v-4l4-6 4 6v4"/>',
  dot: '<circle cx="12" cy="12" r="3.5"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="m12 12 6-6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  store: '<path d="M3 9l2-5h14l2 5"/><path d="M4 9v11h16V9"/><path d="M3 9h18"/><path d="M9 20v-6h6v6"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
};
const releaseChip = (text) => (text ? `<span class="chip date" title="Erscheinungstermin laut Shop – kann sich verschieben">${icon("calendar")} erscheint ${esc(text)}</span>` : "");
const icon = (name, cls = "i") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const STATUS = {
  available: { label: "Verfügbar", cls: "ok" },
  preorder: { label: "Vorbestellbar", cls: "pre" },
  soldout: { label: "Ausverkauft", cls: "muted" },
  queue: { label: "Warteschlange", cls: "pre" },
  unknown: { label: "Unklar", cls: "warn" },
  blocked: { label: "Gesperrt", cls: "warn" },
  error: { label: "Fehler", cls: "bad" },
};
const THROTTLED = { label: "Gebremst", cls: "warn" };
const isThrottled = (status, reason) => status === "blocked" && /bremst/.test(reason || "");
const statusOf = (status, reason) => (isThrottled(status, reason) ? THROTTLED : STATUS[status] || STATUS.unknown);
const BUYABLE = ["available", "preorder"];
const PROBLEM = ["blocked", "error", "unknown"];
const INTERVALS = [30, 60, 120, 300, 600];
const BUDGETS = [[2, "2 pro Minute – sehr schonend"], [4, "4 pro Minute – empfohlen"], [6, "6 pro Minute"],
  [10, "10 pro Minute – schnell"], [0, "Unbegrenzt"]];

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* leer */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Fehler ${res.status}`);
  }
  return data;
}

function toast(text, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : "");
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3200);
}

let S = null;          // letzter Stand vom Programm
let offset = 0;        // Uhr-Abweichung zwischen Programm und Browser
const ui = {
  preview: null, previewLoading: false, prio: "laut", interval: 60, variant: null,
  panelOpen: false, wizardStep: null, wizardChannel: null,
  ntfyOpen: false, tg: null, tgTimer: null, dcEdit: false,
  mode: "link", searchPrio: "laut", searchFormBuilt: false, openSearches: new Set(),
  keys: { watches: "", events: "", searches: "", shops: "" }, offline: false,
};
const SHOP_STATUS = {
  ok: ["Bereit", "ok"], warn: ["Ohne Probetreffer", "warn"], throttled: ["Gebremst", "warn"],
  blocked: ["Gesperrt", "bad"], error: ["Fehler", "bad"], queue: ["Warteschlange", "warn"],
};

const nowSec = () => (Date.now() + offset) / 1000;

function rel(ts) {
  if (!ts) return "noch nie";
  const s = Math.round(nowSec() - ts);
  if (s < 5) return "gerade eben";
  if (s < 60) return `vor ${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `vor ${m} Min.`;
  const h = Math.round(m / 60);
  if (h < 24) return `vor ${h} Std.`;
  return clock(ts);
}

function clock(ts) {
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const today = new Date().toDateString() === d.toDateString();
  return today ? time : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}, ${time}`;
}

const intervalLabel = (s) => (s < 60 ? `${s} s` : `${Math.round(s / 60)} Min.`);
const priceText = (p, cur) => (p == null ? "" : new Intl.NumberFormat("de-DE", { style: "currency", currency: cur || "EUR" }).format(p));
const channelReady = () => S && Object.values(S.channels).some((c) => c.enabled && c.configured);

/* ================================================================ Laden & Takt */

async function refresh() {
  try {
    S = await api("/api/state");
    offset = S.now * 1000 - Date.now();
    ui.offline = false;
    renderAll();
  } catch {
    ui.offline = true;
    renderRunstate();
  }
}

function renderAll() {
  renderRunstate();
  renderStats();
  renderHint();
  renderSearches();
  renderWatches();
  renderEvents();
  if (ui.mode === "search") renderShopsLine();
  if (ui.panelOpen) renderLoad();
}

function tick() {
  document.querySelectorAll("[data-rel]").forEach((el) => { el.textContent = rel(Number(el.dataset.rel)); });
  renderRunstate();
}

/* ================================================================ Kopfzeile & Übersicht */

function renderRunstate() {
  const el = $("#runstate");
  if (ui.offline) {
    el.innerHTML = `<span class="dot off"></span>Keine Verbindung zum Programm – läuft Restock-Radar noch?`;
    return;
  }
  if (!S) return;
  const active = S.watches.filter((w) => w.enabled);
  const searches = (S.searches || []).filter((s) => s.enabled);
  if (!active.length && !searches.length) {
    el.innerHTML = `<span class="dot off"></span>Bereit – noch nichts zu beobachten`;
    return;
  }
  const next = Math.min(...[...active, ...searches].map((x) => x.next_check || 0));
  const secs = Math.max(0, Math.round(next - nowSec()));
  const parts = [];
  if (active.length) parts.push(`${active.length} ${active.length === 1 ? "Produkt" : "Produkte"}`);
  if (searches.length) parts.push(`${searches.length} ${searches.length === 1 ? "Suche" : "Suchen"}`);
  el.innerHTML = `<span class="dot"></span>Läuft · ${parts.join(" · ")} · nächste Prüfung ${secs <= 1 ? "jetzt" : `in ${secs} s`}`;
}

function renderStats() {
  const active = S.watches.filter((w) => w.enabled);
  const buyable = active.filter((w) => BUYABLE.includes(w.status)).length;
  const problems = active.filter((w) => PROBLEM.includes(w.status)).length;
  const week = nowSec() - 7 * 86400;
  const finds = S.events.filter((e) => e.kind === "restock" && e.ts > week).length;
  $("#stats").innerHTML = `
    <div class="stat"><div class="num">${active.length}</div><div class="lbl">Beobachtet</div></div>
    <div class="stat ${buyable ? "ok" : ""}"><div class="num">${buyable}</div><div class="lbl">Gerade kaufbar</div></div>
    <div class="stat ${problems ? "warn" : ""}"><div class="num">${problems}</div><div class="lbl">Mit Problemen</div></div>
    <div class="stat"><div class="num">${finds}</div><div class="lbl">Funde (7 Tage)</div></div>`;
}

function renderHint() {
  const el = $("#channel-hint");
  if (channelReady()) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="callout warn">${icon("alert")}
      <div class="txt"><b>Noch keine Benachrichtigung eingerichtet.</b> Ohne sie kann ich dir nichts aufs Handy schicken.
        <button class="link-btn" data-open-settings>Jetzt einrichten</button></div>
    </div>`;
}

/* ================================================================ Vorschau beim Hinzufügen */

function previewStatus(p) {
  if (ui.variant && p.variants && p.variants.length) {
    const v = p.variants.find((x) => x.id === ui.variant);
    if (v) return { status: v.available ? "available" : "soldout", price: v.price };
  }
  return { status: p.status, price: p.price };
}

function renderPreview() {
  const el = $("#preview");
  if (ui.previewLoading) {
    el.innerHTML = `<div class="preview"><div class="loading"><div class="spinner"></div>Ich schaue mir die Seite an …</div></div>`;
    return;
  }
  const p = ui.preview;
  if (!p) { el.innerHTML = ""; return; }

  const { status, price } = previewStatus(p);
  const st = statusOf(status, p.reason);
  const img = p.image ? `<img src="${esc(safeUrl(p.image))}" alt="" loading="lazy" onerror="this.remove()">` : icon("image");
  const sub = [p.shop, p.platform].filter(Boolean).map(esc).join(" · ");
  const callouts = [];

  if (p.duplicate_of) {
    callouts.push(["info", "info", "Diese Seite beobachtest du schon. Du kannst sie trotzdem ein zweites Mal hinzufügen, z. B. für eine andere Variante."]);
  }
  if (isThrottled(status, p.reason)) {
    callouts.push(["warn", "hourglass", "<b>Der Shop bremst gerade</b>, weil kurz hintereinander zu viele Anfragen kamen. Prüfe in einer Minute nochmal – oder füge ihn einfach hinzu, ich warte dann automatisch ab."]);
  } else if (status === "blocked") {
    callouts.push(["bad", "shield", `<b>${esc(p.reason)}.</b> Ich kann diesen Shop gerade nicht lesen und umgehe solche Sperren nicht. Du kannst ihn trotzdem hinzufügen – ich versuche es dann in größeren Abständen weiter.`]);
  } else if (status === "queue") {
    callouts.push(["warn", "hourglass", `<b>Warteschlange aktiv.</b> ${esc(p.reason)} Ich melde mich, wenn sie wieder auftaucht.`]);
  } else if (status === "error" || status === "unknown") {
    callouts.push(["warn", "alert", esc(p.reason)]);
  } else if (p.rating === "eingeschränkt") {
    callouts.push(["warn", "alert", "Ich habe nur ein schwaches Signal gefunden. Das klappt meistens, kann aber danebenliegen."]);
  }
  if (p.note) callouts.push(["info", "info", esc(p.note)]);
  if (BUYABLE.includes(status)) {
    callouts.push(["ok", "zap", "Gerade kaufbar! Ab jetzt melde ich dir jeden neuen Restock."]);
  }

  const ratingLine = p.method_label
    ? `<span class="rating">${icon("shield")} Überwachung <b>${esc(p.rating)}</b> über ${esc(p.method_label)}</span>`
    : "";
  const variants = p.variants && p.variants.length
    ? `<label class="field"><span>Variante</span><select data-variant>${p.variants.map((v) =>
        `<option value="${esc(v.id)}" ${v.id === ui.variant ? "selected" : ""}>${esc(v.title)} – ${v.available ? "verfügbar" : "ausverkauft"}</option>`).join("")}
        ${ui.variant ? "" : `<option value="" selected>Egal welche</option>`}</select></label>`
    : "";
  const signals = p.signals && p.signals.length
    ? `<details class="signals"><summary>Was ich auf der Seite gefunden habe (${p.signals.length})</summary>
        <ul class="signal-list">${p.signals.map((s) => `
          <li><span class="src">${esc(s.label)}</span><span class="det">${esc(s.detail)}</span>
          <span class="badge ${STATUS[s.status]?.cls || "muted"}">${STATUS[s.status]?.label || esc(s.status)}</span></li>`).join("")}
        </ul></details>`
    : "";

  el.innerHTML = `
    <div class="preview">
      <div class="preview-main">
        <div class="preview-img">${img}</div>
        <div>
          <h3 class="preview-title">${esc(p.name || p.shop || p.url)}</h3>
          <div class="preview-sub">${sub}${price != null ? ` · <b>${esc(priceText(price, p.currency))}</b>` : ""}</div>
          <div class="preview-status"><span class="badge ${st.cls}">${st.label}</span>${ratingLine}</div>
          ${callouts.map(([cls, ico, html]) => `<div class="callout ${cls}">${icon(ico)}<div class="txt">${html}</div></div>`).join("")}
        </div>
      </div>
      <div class="options">
        ${variants}
        <div class="field"><span>Benachrichtigung</span>
          <div class="segmented" role="group">
            <button type="button" data-prio="laut" aria-pressed="${ui.prio === "laut"}">${icon("bell")} Laut</button>
            <button type="button" data-prio="leise" aria-pressed="${ui.prio === "leise"}">${icon("bellOff")} Leise</button>
          </div></div>
        <label class="field"><span>Prüfen alle</span>
          <select data-interval>${INTERVALS.map((s) => `<option value="${s}" ${s === ui.interval ? "selected" : ""}>${intervalLabel(s)}</option>`).join("")}</select></label>
      </div>
      ${signals}
      <div class="preview-actions">
        <button class="btn primary" data-act="watch">${icon("radar")} ${status === "blocked" && !isThrottled(status, p.reason) ? "Trotzdem überwachen" : "Überwachen"}</button>
        <button class="btn ghost" data-act="cancel">Abbrechen</button>
      </div>
    </div>`;
}

async function submitPreview() {
  const input = $("#add-url");
  const url = input.value.trim();
  if (!url) { input.focus(); return; }
  ui.preview = null;
  ui.previewLoading = true;
  $("#add-submit").disabled = true;
  renderPreview();
  try {
    ui.preview = await api("/api/preview", { method: "POST", body: { url } });
    ui.prio = "laut";
    ui.interval = S ? S.settings.default_interval : 60;
    ui.variant = ui.preview.variant_id || null;
  } catch (err) {
    toast(err.message, true);
  } finally {
    ui.previewLoading = false;
    $("#add-submit").disabled = false;
    renderPreview();
  }
}

async function addWatch(button) {
  const p = ui.preview;
  button.disabled = true;
  try {
    await api("/api/watches", { method: "POST", body: { url: p.url, priority: ui.prio, interval: ui.interval, variant_id: ui.variant } });
    toast(`Wird überwacht: ${p.name || p.shop}`);
    ui.preview = null;
    $("#add-url").value = "";
    renderPreview();
    await refresh();
  } catch (err) {
    toast(err.message, true);
    button.disabled = false;
  }
}

/* ================================================================ Suchbegriff statt Link */

function setMode(mode) {
  ui.mode = mode;
  document.querySelectorAll("#add-mode [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("#add-form").hidden = mode !== "link";
  $("#preview").hidden = mode !== "link";
  $("#search-form").hidden = mode !== "search";
  if (mode === "search") {
    if (!ui.searchFormBuilt) renderSearchForm();
    renderShopsLine();
    $("#s-query").focus();
  }
}

function renderSearchForm() {
  if (!S) return;
  ui.searchFormBuilt = true;
  const meta = S.search_meta;
  const options = (map, sel) => Object.entries(map).map(([k, v]) => `<option value="${esc(k)}" ${k === sel ? "selected" : ""}>${esc(v)}</option>`).join("");
  const autoModes = Object.entries(meta.auto_modes).map(([k, v]) => `<option value="${k}" ${k === "2" ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#search-form").innerHTML = `
    <label class="add-label" for="s-query">Wonach soll ich Ausschau halten?</label>
    <div class="search-grid">
      <label class="field first"><span>Suchbegriff <span class="hint">– optional · leer = alle Pokémon-Produkte des Typs · Schreibweisen mit | trennen</span></span>
        <input type="text" id="s-query" placeholder="z. B. Delta-Herrschaft | Delta Reign – oder leer lassen"></label>
      <label class="field"><span>Produkttyp</span><select id="s-ptype">${options(meta.types, "display")}</select></label>
      <label class="field"><span>Sprache</span><select id="s-lang">${options(meta.langs, "de")}</select></label>
      <label class="field"><span>Höchstpreis in € <span class="hint">– optional</span></span>
        <input type="text" id="s-max" inputmode="decimal" placeholder="z. B. 250"></label>
      <label class="field span2"><span>Ohne diese Wörter <span class="hint">– optional, mit Komma getrennt</span></span>
        <input type="text" id="s-exclude" placeholder="z. B. PSA, gradiert"></label>
      <label class="field full"><span>Kategorie-Seiten <span class="hint">– optional, ein Link pro Zeile, z. B. die Seite „Pokémon Displays (Deutsch)“ eines Shops. Dort sehe ich alle Produkte, nicht nur Suchtreffer.</span></span>
        <textarea id="s-cats" rows="2" placeholder="https://shop.de/pokemon/displays-deutsch"></textarea></label>
    </div>
    <div class="search-foot">
      <div class="segmented" role="group" aria-label="Benachrichtigung">
        <button type="button" data-sprio="laut" aria-pressed="${ui.searchPrio === "laut"}">${icon("bell")} Laut</button>
        <button type="button" data-sprio="leise" aria-pressed="${ui.searchPrio === "leise"}">${icon("bellOff")} Leise</button>
      </div>
      <label class="field inline"><span>Automatisch überwachen</span>
        <span class="row"><select id="s-auto">${autoModes}</select>
        <span class="hint">höchstens</span><input type="text" id="s-max-auto" inputmode="numeric" value="30" class="tiny" aria-label="Höchstens so viele Produkte"></span></label>
      <span class="shops-line" id="shops-line"></span>
      <button class="btn primary" type="submit">${icon("search")} Suche anlegen</button>
    </div>`;
}

function renderShopsLine() {
  const el = $("#shops-line");
  if (!el || !S) return;
  const active = S.shops.filter((s) => s.enabled);
  const key = active.map((s) => s.id + s.name).join("|") || "-";
  if (el.dataset.key === key) return;          // nicht bei jeder Aktualisierung neu zeichnen
  el.dataset.key = key;
  el.innerHTML = active.length
    ? `Durchsucht ${active.length} ${active.length === 1 ? "Shop" : "Shops"}: ${active.slice(0, 4).map((s) => esc(s.name)).join(", ")}${active.length > 4 ? " …" : ""} · <button type="button" class="link-btn" data-open-shops>Shops verwalten</button>`
    : `<span style="color:var(--warn)">${icon("alert")} Noch keine Shops – nur Kategorie-Seiten möglich.</span> <button type="button" class="link-btn" data-open-shops>Shop hinzufügen</button>`;
}

async function submitSearch(form) {
  const max = $("#s-max").value.trim().replace(",", ".");
  const body = {
    query: $("#s-query").value, ptype: $("#s-ptype").value, lang: $("#s-lang").value,
    max_price: max ? Number(max) : null, exclude: $("#s-exclude").value, categories: $("#s-cats").value,
    priority: ui.searchPrio, auto_watch: Number($("#s-auto").value), max_auto: Number($("#s-max-auto").value) || 30,
  };
  if (max && !(Number(max) > 0)) { toast("Höchstpreis bitte als Zahl, z. B. 250", true); return; }
  if (!body.query.trim() && !body.categories.trim() && body.ptype === "egal") {
    toast("Wähle einen Produkttyp, gib einen Suchbegriff ein oder eine Kategorie-Seite an – sonst wird es zu breit.", true);
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await api("/api/searches", { method: "POST", body });
    toast(`Suche angelegt – ich schaue jetzt in ${S.shops.filter((s) => s.enabled).length} Shops nach`);
    $("#s-query").value = "";
    $("#s-max").value = "";
    $("#s-exclude").value = "";
    $("#s-cats").value = "";
    ui.keys.searches = "";
    await refresh();
    setTimeout(refresh, 4000);
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderSearches() {
  const key = JSON.stringify([S.searches.map((s) => [s.id, s.enabled, s.priority, s.auto_watch, s.last_check, s.hit_count,
    s.auto_count, s.last_error, s.hits.map((h) => [h.id, h.status, h.price, h.watched, h.release])]), [...ui.openSearches]]);
  if (key === ui.keys.searches) return;
  ui.keys.searches = key;
  $("#searches-section").hidden = !S.searches.length;
  $("#searches").innerHTML = S.searches.map(searchCard).join("");
}

function searchCard(s) {
  const meta = S.search_meta;
  const cats = s.category_list || [];
  const chips = [
    s.query && s.ptype !== "egal" ? meta.types[s.ptype] : null,
    s.lang !== "egal" ? meta.langs[s.lang] : null,
    s.max_price ? `bis ${priceText(s.max_price, "EUR")}` : null,
    s.exclude ? `ohne ${s.exclude}` : null,
  ].filter(Boolean).map((c) => `<span class="chip">${esc(c)}</span>`).join("")
    + (cats.length ? `<span class="chip" title="${esc(cats.join("\n"))}">${icon("list")} ${cats.length} ${cats.length === 1 ? "Kategorie-Seite" : "Kategorie-Seiten"}</span>` : "")
    + (s.auto_watch ? `<span class="chip ok" title="${esc(meta.auto_modes[s.auto_watch])}">Auto: ${s.auto_watch === 2 ? "nicht Kaufbares" : "neue"} · ${s.auto_count}/${s.max_auto}</span>` : "");
  const loud = s.priority === "laut";
  const shopsCount = s.query || !cats.length ? S.shops.filter((x) => x.enabled).length : 0;
  const sources = [shopsCount ? `${shopsCount} ${shopsCount === 1 ? "Shop" : "Shops"}` : null,
    cats.length ? `${cats.length} ${cats.length === 1 ? "Kategorie" : "Kategorien"}` : null].filter(Boolean).join(" + ");
  const open = ui.openSearches.has(s.id);
  const shown = open ? s.hits : s.hits.slice(0, 6);
  const fresh = nowSec() - 2 * 86400;
  const hits = shown.length ? `<ul class="hits">${shown.map((h) => {
    const st = STATUS[h.status] || { label: "Status unklar", cls: "muted" };
    const img = h.image ? `<img src="${esc(safeUrl(h.image))}" alt="" loading="lazy" onerror="this.remove()">` : icon("image");
    const sub = [h.shop_name, priceText(h.price, h.currency)].filter(Boolean).map(esc).join(" · ");
    return `<li class="hit" data-hit="${h.id}">
      <div class="thumb">${img}</div>
      <div><a class="t" href="${esc(safeUrl(h.url))}" target="_blank" rel="noopener">${esc(h.title)}</a><div class="s">${sub}</div></div>
      <div class="side">
        ${h.is_new && h.first_seen > fresh ? `<span class="chip new">Neu</span>` : ""}
        ${releaseChip(h.release)}
        <span class="badge ${st.cls}">${st.label}</span>
        ${h.watched ? `<span class="chip ok">${icon("check")} Überwacht</span>` : `<button class="btn small" data-act="hit-watch">${icon("radar")} Überwachen</button>`}
      </div></li>`;
  }).join("")}</ul>` : `<div class="hits-empty">${s.last_check ? `Noch keine passenden Treffer – ich suche alle ${intervalLabel(s.interval)} weiter.` : `<span class="loading"><span class="spinner"></span>Erster Durchlauf läuft …</span>`}</div>`;
  const more = s.hits.length > 6 ? `<button class="link-btn small" data-act="more" style="margin-top:8px">${open ? "Weniger anzeigen" : `Alle ${s.hit_count} Treffer anzeigen`}</button>` : "";
  return `
    <article class="search-card ${s.enabled ? "" : "is-paused"}" data-sid="${s.id}">
      <div class="search-head">
        <div class="grow">
          <h3>${icon("search")} ${esc(s.label)}</h3>
          ${chips ? `<div class="chips">${chips}</div>` : ""}
          <div class="meta">${s.enabled ? `geprüft <span data-rel="${s.last_check || 0}">${rel(s.last_check)}</span> · alle ${intervalLabel(s.interval)}` : "pausiert"} · ${s.hit_count} Treffer · ${sources}</div>
          ${s.last_error ? `<div class="problem">${icon("alert")} ${esc(s.last_error)}</div>` : ""}
        </div>
        <div class="actions">
          <button class="icon-btn ${loud ? "on" : ""}" data-act="prio" title="${loud ? "Laut – Alarm bei neuen, kaufbaren Treffern" : "Leise – nur stille Meldung"}" aria-label="Lautstärke umschalten">${icon(loud ? "bell" : "bellOff")}</button>
          <button class="icon-btn" data-act="check" title="Jetzt suchen" aria-label="Jetzt suchen">${icon("refresh")}</button>
          <button class="icon-btn" data-act="pause" title="${s.enabled ? "Pausieren" : "Fortsetzen"}" aria-label="${s.enabled ? "Pausieren" : "Fortsetzen"}">${icon(s.enabled ? "pause" : "play")}</button>
          <button class="icon-btn" data-act="delete" title="Suche löschen" aria-label="Suche löschen">${icon("trash")}</button>
        </div>
      </div>
      ${hits}${more}
    </article>`;
}

async function onSearchAction(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest("[data-sid]");
  const s = S.searches.find((x) => x.id === Number(card.dataset.sid));
  if (!s) return;
  const act = btn.dataset.act;
  try {
    if (act === "more") {
      ui.openSearches.has(s.id) ? ui.openSearches.delete(s.id) : ui.openSearches.add(s.id);
    } else if (act === "prio") {
      await api(`/api/searches/${s.id}`, { method: "PATCH", body: { priority: s.priority === "laut" ? "leise" : "laut" } });
    } else if (act === "pause") {
      await api(`/api/searches/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } });
    } else if (act === "check") {
      btn.classList.add("spin");
      const res = await api(`/api/searches/${s.id}/check`, { method: "POST" });
      toast(`„${res.label}“: ${res.hit_count} Treffer`);
    } else if (act === "delete") {
      if (!confirm(`Suche „${s.label}“ löschen? Automatisch übernommene Produkte bleiben in deiner Liste.`)) return;
      await api(`/api/searches/${s.id}`, { method: "DELETE" });
    } else if (act === "hit-watch") {
      btn.disabled = true;
      const hitId = Number(btn.closest("[data-hit]").dataset.hit);
      const w = await api(`/api/hits/${hitId}/watch`, { method: "POST" });
      toast(`Wird überwacht: ${w.name || w.shop}`);
    }
    ui.keys.searches = "";
    ui.keys.watches = "";
    await refresh();
  } catch (err) {
    toast(err.message, true);
    btn.classList.remove("spin");
    btn.disabled = false;
  }
}

/* ================================================================ Produktliste */

function renderWatches() {
  const key = JSON.stringify(S.watches.map((w) => [w.id, w.status, w.enabled, w.priority, w.last_check, w.price, w.reason, w.note, w.name, w.cart_url, w.release]));
  if (key === ui.keys.watches) return;
  ui.keys.watches = key;
  const el = $("#watches");
  if (!S.watches.length) {
    el.innerHTML = `<div class="empty"><h3>Noch nichts auf dem Radar</h3>
      Füge oben einen Produktlink ein. Ich prüfe dann, wie gut sich die Seite überwachen lässt,<br>und melde mich sofort, wenn der Artikel wieder da ist.</div>`;
    return;
  }
  el.innerHTML = S.watches.map(watchCard).join("");
}

function watchCard(w) {
  const st = statusOf(w.status, w.reason);
  const buyable = BUYABLE.includes(w.status) && w.enabled;
  const img = w.image ? `<img src="${esc(safeUrl(w.image))}" alt="" loading="lazy" onerror="this.remove()">` : icon("image");
  const sub = [w.shop, w.variant_title, priceText(w.price, w.currency)].filter(Boolean).map(esc).join(" · ");
  const method = { shopify: "Shopify-Lagerfeld", jsonld: "Google-Daten", microdata: "Google-Daten", meta: "Meta-Tag", button: "Warenkorb-Button", text: "Seitentext" }[w.method] || "–";
  const problem = PROBLEM.includes(w.status) && w.reason ? `<div class="problem">${icon("alert")} ${esc(w.reason)}${w.backoff ? ` · nächster Versuch in ${intervalLabel(w.backoff)}` : ""}</div>` : "";
  const note = w.note && !problem ? `<div class="problem">${esc(w.note)}</div>` : "";
  const loud = w.priority === "laut";
  return `
    <article class="watch ${buyable ? "is-buyable" : ""} ${w.enabled ? "" : "is-paused"}" data-id="${w.id}">
      <div class="thumb">${img}</div>
      <div class="info">
        <a class="name" href="${esc(safeUrl(w.url))}" target="_blank" rel="noopener">${esc(w.name || w.url)}</a>
        <div class="sub">${sub}</div>
        <div class="meta">${w.enabled ? `geprüft <span data-rel="${w.last_check || 0}">${rel(w.last_check)}</span> · alle ${intervalLabel(w.interval)}` : "pausiert"} · ${method}</div>
        ${w.release || w.auto ? `<div class="chips">${releaseChip(w.release)}${w.auto ? `<span class="chip">${icon("search")} aus Suchauftrag</span>` : ""}</div>` : ""}
        ${problem}${note}
      </div>
      <div class="side">
        <span class="badge ${w.enabled ? st.cls : "muted"}">${w.enabled ? st.label : "Pausiert"}</span>
        ${buyable ? `<a class="btn go small" href="${esc(safeUrl(w.cart_url || w.url))}" target="_blank" rel="noopener">${icon("cart")} ${w.cart_url ? "Direkt zur Kasse" : "Zum Shop"}</a>` : ""}
        <div class="actions">
          <button class="icon-btn ${loud ? "on" : ""}" data-act="prio" title="${loud ? "Laut – klingelt auch bei „Nicht stören“ (ntfy/Discord)" : "Leise – nur stille Meldung"}" aria-label="Lautstärke umschalten">${icon(loud ? "bell" : "bellOff")}</button>
          <button class="icon-btn" data-act="check" title="Jetzt prüfen" aria-label="Jetzt prüfen">${icon("refresh")}</button>
          <button class="icon-btn" data-act="pause" title="${w.enabled ? "Pausieren" : "Fortsetzen"}" aria-label="${w.enabled ? "Pausieren" : "Fortsetzen"}">${icon(w.enabled ? "pause" : "play")}</button>
          <button class="icon-btn" data-act="delete" title="Entfernen" aria-label="Entfernen">${icon("trash")}</button>
        </div>
      </div>
    </article>`;
}

async function onWatchAction(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest("[data-id]");
  const w = S.watches.find((x) => x.id === Number(card.dataset.id));
  if (!w) return;
  try {
    if (btn.dataset.act === "prio") {
      await api(`/api/watches/${w.id}`, { method: "PATCH", body: { priority: w.priority === "laut" ? "leise" : "laut" } });
    } else if (btn.dataset.act === "pause") {
      await api(`/api/watches/${w.id}`, { method: "PATCH", body: { enabled: !w.enabled } });
    } else if (btn.dataset.act === "check") {
      btn.classList.add("spin");
      const updated = await api(`/api/watches/${w.id}/check`, { method: "POST" });
      toast(`${updated.name || updated.shop}: ${(STATUS[updated.status] || STATUS.unknown).label}`);
    } else if (btn.dataset.act === "delete") {
      if (!confirm(`„${w.name || w.url}“ nicht mehr beobachten?`)) return;
      await api(`/api/watches/${w.id}`, { method: "DELETE" });
    }
    ui.keys.watches = "";
    await refresh();
  } catch (err) {
    toast(err.message, true);
    btn.classList.remove("spin");
  }
}

/* ================================================================ Verlauf */

const EVENT_ICONS = { restock: "zap", sent: "send", soldout: "dot", warning: "alert", queue: "hourglass", info: "dot" };

function renderEvents() {
  const key = S.events.map((e) => e.id).join(",") + "|" + S.watches.length;
  if (key === ui.keys.events) return;
  ui.keys.events = key;
  const names = Object.fromEntries(S.watches.map((w) => [w.id, w.name || w.shop]));
  const el = $("#events");
  if (!S.events.length) {
    el.innerHTML = `<li class="none">Hier erscheint alles, was passiert: Funde, gesendete Nachrichten und Probleme.</li>`;
    return;
  }
  el.innerHTML = S.events.slice(0, 40).map((e) => `
    <li class="ev-${esc(e.kind)}">
      <span class="ev-ico">${icon(EVENT_ICONS[e.kind] || "dot")}</span>
      <div>${e.watch_id && names[e.watch_id] ? `<span class="ev-name">${esc(names[e.watch_id])}</span> · ` : ""}${esc(e.text)}</div>
      <time>${clock(e.ts)}</time>
    </li>`).join("");
}

/* ================================================================ Kanäle (Einstellungen & Assistent) */

function channelCard(name, { wizard = false } = {}) {
  const c = S.channels[name];
  const meta = {
    ntfy: ["N", "ntfy", "Kostenlose App, am einfachsten. Kann richtig laut Alarm schlagen."],
    telegram: ["T", "Telegram", "Nachricht mit Bild und „Direkt zur Kasse“-Knopf."],
    discord: ["D", "Discord", "In einen Kanal deines Discord-Servers."],
  }[name];
  const status = c.configured
    ? `<span class="chip ${c.enabled ? "ok" : ""}">${c.enabled ? "Aktiv" : "Aus"}</span>
       <label class="switch" title="An/Aus"><input type="checkbox" data-toggle-channel="${name}" ${c.enabled ? "checked" : ""} aria-label="${meta[1]} an oder aus"><span></span></label>`
    : `<span class="chip">Nicht eingerichtet</span>`;
  return `
    <div class="card" id="card-${name}">
      <div class="card-head">
        <span class="ch-ico ${name}">${meta[0]}</span>
        <div class="grow"><h3>${meta[1]}</h3><p>${meta[2]}</p></div>
        ${status}
      </div>
      <div class="card-body">${channelBody(name, c, wizard)}</div>
    </div>`;
}

function channelBody(name, c, wizard) {
  const test = `<button class="btn small" data-act="test" data-channel="${name}">${icon("send")} Test senden</button>`;

  if (name === "ntfy") {
    if (!c.configured) {
      return `<div class="row"><button class="btn primary" data-act="ntfy-create">Kanal erstellen</button></div>`;
    }
    if (!(ui.ntfyOpen || wizard)) {
      return `<div class="row">${test}<button class="btn small ghost" data-act="ntfy-open">${icon("phone")} Handy verbinden</button></div>`;
    }
    return `
      <div class="qr-row">
        <div class="qr" title="ntfy-Kanal abonnieren">${c.qr || ""}</div>
        <div class="grow small">
          <ol>
            <li>Installiere die App <b>ntfy</b> (Play Store oder App Store).</li>
            <li><b>Android:</b> Diesen QR-Code mit der Kamera scannen.<br>
                <b>iPhone:</b> In der App auf <b>+</b> tippen und dieses Thema eintragen:<br>
                <code>${esc(c.topic)}</code> <button class="link-btn" data-copy="${esc(c.topic)}">kopieren</button></li>
            <li>Auf <b>Test senden</b> tippen – es sollte sofort klingeln.</li>
          </ol>
          <span class="muted">Tipp: Erlaube in der App für diesen Kanal „dringende“ Benachrichtigungen, dann klingelt es auch bei „Nicht stören“.</span>
        </div>
      </div>
      <div class="row">${test}<button class="btn small ghost" data-act="ntfy-new" title="Falls jemand Fremdes den Kanal kennt">Neuen Kanal erzeugen</button></div>`;
  }

  if (name === "telegram") {
    const tg = ui.tg;
    if (tg && tg.state === "waiting") {
      return `
        <div class="qr-row">
          <div class="qr">${tg.qr}</div>
          <div class="grow small">
            <b>Fast geschafft!</b>
            <span>Scanne den Code mit dem Handy oder öffne den Link – und tippe in Telegram auf <b>Starten</b>.</span>
            <a class="btn small" href="${esc(safeUrl(tg.link))}" target="_blank" rel="noopener">${icon("external")} In Telegram öffnen</a>
            <div class="loading"><div class="spinner"></div>Warte auf „Starten“ bei @${esc(tg.bot_name)} …</div>
          </div>
        </div>`;
    }
    if (c.configured && !(tg && tg.state === "form")) {
      return `<div>Verbunden mit <b>@${esc(c.bot_name)}</b>.</div>
        <div class="row">${test}<button class="btn small ghost" data-act="tg-reset">Neu verbinden</button></div>`;
    }
    const timeout = tg && tg.state === "timeout" ? `<div class="callout warn">${icon("alert")}<div class="txt">Es kam kein „Starten“ an. Versuch es einfach nochmal.</div></div>` : "";
    return `${timeout}
      <ol class="small">
        <li>Öffne in Telegram den <a href="https://t.me/BotFather" target="_blank" rel="noopener">BotFather</a> und schicke ihm <code>/newbot</code>.</li>
        <li>Gib einen Namen ein (z. B. „Mein Restock Radar“) und danach einen Benutzernamen, der auf <code>bot</code> endet.</li>
        <li>BotFather schickt dir einen <b>Token</b>. Kopiere ihn hier hinein:</li>
      </ol>
      <div class="row"><input type="password" data-tg-token placeholder="123456789:AAH…" autocomplete="off" aria-label="Telegram-Token">
        <button class="btn primary" data-act="tg-connect">Verbinden</button></div>`;
  }

  // Discord
  if (c.configured && !ui.dcEdit) {
    return `<div>Webhook gespeichert <span class="muted">(${esc(c.webhook_hint)})</span>.</div>
      <div class="row">${test}<button class="btn small ghost" data-act="dc-edit">Ändern</button></div>`;
  }
  return `
    <ol class="small">
      <li>In Discord: Rechtsklick auf deinen Kanal → <b>Kanal bearbeiten</b> → <b>Integrationen</b> → <b>Webhooks</b>.</li>
      <li><b>Neuer Webhook</b> → <b>Webhook-URL kopieren</b> und hier einfügen:</li>
    </ol>
    <div class="row"><input type="text" data-dc-hook placeholder="https://discord.com/api/webhooks/…" autocomplete="off" aria-label="Discord-Webhook">
      <button class="btn primary" data-act="dc-save">Speichern</button></div>`;
}

async function onChannelAction(e) {
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.copy); toast("Kopiert"); } catch { toast("Kopieren ging nicht – bitte markieren", true); }
    return;
  }
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  const scope = btn.closest(".card") || document;
  btn.disabled = true;
  try {
    if (act === "test") {
      await api(`/api/channels/${btn.dataset.channel}/test`, { method: "POST" });
      toast("Test gesendet – schau aufs Handy");
    } else if (act === "ntfy-create" || act === "ntfy-new") {
      if (act === "ntfy-new" && !confirm("Neuen Kanal erzeugen? Danach musst du das Handy neu verbinden.")) return;
      await api("/api/channels/ntfy", { method: "POST", body: { new_topic: act === "ntfy-new" } });
      ui.ntfyOpen = true;
    } else if (act === "ntfy-open") {
      ui.ntfyOpen = true;
    } else if (act === "tg-reset") {
      ui.tg = { state: "form" };
    } else if (act === "tg-connect") {
      const token = $("[data-tg-token]", scope).value.trim();
      if (!token) { toast("Bitte erst den Token einfügen", true); return; }
      const data = await api("/api/channels/telegram/connect", { method: "POST", body: { token } });
      ui.tg = { state: "waiting", ...data };
      watchTelegram();
    } else if (act === "dc-edit") {
      ui.dcEdit = true;
    } else if (act === "dc-save") {
      const webhook = $("[data-dc-hook]", scope).value.trim();
      if (!webhook) { toast("Bitte erst den Webhook-Link einfügen", true); return; }
      await api("/api/channels/discord", { method: "POST", body: { webhook } });
      ui.dcEdit = false;
      toast("Discord gespeichert");
    } else if (act === "shop-add") {
      const url = $("[data-shop-url]", scope).value.trim();
      if (!url) { toast("Bitte erst den Link zum Shop einfügen", true); return; }
      btn.innerHTML = `<span class="spinner"></span> Prüfe Shop …`;
      const res = await api("/api/shops", { method: "POST", body: { url } });
      const t = res.test;
      toast(t.count ? `${res.shop.name} hinzugefügt – Probesuche: ${t.count} Treffer` :
        `${res.shop.name} hinzugefügt, aber ${t.error || "die Probesuche fand nichts"}`, !t.count);
    } else if (act === "shop-del") {
      const shop = S.shops.find((s) => s.id === Number(btn.closest("[data-shop]").dataset.shop));
      if (!shop || !confirm(`${shop.name} entfernen? Seine Treffer werden mit gelöscht.`)) return;
      await api(`/api/shops/${shop.id}`, { method: "DELETE" });
    }
    await refresh();
    rerenderOverlays();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    if (act === "shop-add") btn.innerHTML = `${icon("store")} Hinzufügen`;
  }
}

async function onChannelToggle(e) {
  const box = e.target.closest("[data-toggle-channel]");
  if (!box) return false;
  try {
    await api(`/api/channels/${box.dataset.toggleChannel}/enabled`, { method: "POST", body: { enabled: box.checked } });
    await refresh();
    rerenderOverlays();
  } catch (err) {
    toast(err.message, true);
  }
  return true;
}

function watchTelegram() {
  clearInterval(ui.tgTimer);
  ui.tgTimer = setInterval(async () => {
    try {
      const st = await api("/api/channels/telegram/status");
      if (st.state === "done") {
        clearInterval(ui.tgTimer);
        ui.tg = null;
        toast(`Telegram verbunden mit @${st.bot_name}`);
        await refresh();
        rerenderOverlays();
      } else if (st.state === "timeout") {
        clearInterval(ui.tgTimer);
        ui.tg = { state: "timeout" };
        rerenderOverlays();
      }
    } catch { /* nächster Versuch */ }
  }, 2000);
}

/* ================================================================ Einstellungen */

function openPanel(focusId) {
  ui.panelOpen = true;
  ui.ntfyOpen = false;
  renderPanel();
  $("#panel").hidden = false;
  $("#panel-backdrop").hidden = false;
  if (focusId) setTimeout(() => document.getElementById(focusId)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

function closePanel() {
  ui.panelOpen = false;
  $("#panel").hidden = true;
  $("#panel-backdrop").hidden = true;
}

function renderPanel() {
  if (!S) return;
  const set = S.settings;
  const lan = ui.lan;
  $("#panel-body").innerHTML = `
    <div class="group-title">Benachrichtigungen</div>
    ${channelCard("ntfy")}${channelCard("telegram")}${channelCard("discord")}

    <div class="group-title">Suchaufträge</div>
    ${shopsCard()}

    <div class="group-title">Verhalten</div>
    <div class="card"><div class="card-body" style="margin:0">
      <div class="setting-row">
        <div class="grow"><b>Tägliches Lebenszeichen</b><p>Eine stille Nachricht, dass alles läuft – damit du merkst, falls nicht.</p></div>
        <input type="time" data-setting="heartbeat_time" value="${esc(set.heartbeat_time)}" aria-label="Uhrzeit">
        <label class="switch"><input type="checkbox" data-setting="heartbeat_enabled" ${set.heartbeat_enabled ? "checked" : ""} aria-label="Lebenszeichen an oder aus"><span></span></label>
      </div>
      <div class="setting-row">
        <div class="grow"><b>Standard-Prüfintervall</b><p>Für neue Produkte. Schneller als 30 Sekunden geht nicht – aus Fairness gegenüber den Shops.</p></div>
        <select data-setting="default_interval" aria-label="Prüfintervall">${INTERVALS.map((s) => `<option value="${s}" ${s === set.default_interval ? "selected" : ""}>${intervalLabel(s)}</option>`).join("")}</select>
      </div>
      <div class="setting-row">
        <div class="grow"><b>Schonmodus</b><p>Höchstens so viele Anfragen pro Shop und Minute – über alle Produkte und Suchen zusammen. Schützt davor, dass ein Shop deine Internet-Adresse bremst.</p></div>
        <select data-setting="shop_budget" aria-label="Anfragen pro Shop und Minute">${BUDGETS.map(([v, l]) => `<option value="${v}" ${v === Number(set.shop_budget) ? "selected" : ""}>${l}</option>`).join("")}</select>
      </div>
    </div></div>
    <div class="card" id="load-card">
      <div class="card-head"><div class="grow"><h3>Last pro Shop</h3><p>Geplante Anfragen pro Minute. Liegt ein Shop über dem Schonmodus, prüft Restock-Radar dort entsprechend seltener.</p></div></div>
      <div class="card-body" id="load-list">${loadList()}</div>
    </div>

    <div class="group-title">Gerät</div>
    <div class="card" id="lan-card">
      <div class="setting-row">
        <div class="grow"><b>Handy-Zugriff im WLAN</b><p>Produkte vom Handy aus hinzufügen und Status ansehen.</p></div>
        <label class="switch"><input type="checkbox" data-lan ${set.lan_enabled ? "checked" : ""} aria-label="Handy-Zugriff an oder aus"><span></span></label>
      </div>
      ${set.lan_enabled ? `<div class="card-body">${lan && lan.qr ? `
        <div class="qr-row"><div class="qr">${lan.qr}</div>
          <div class="grow small"><span>Scanne den Code mit der Handy-Kamera. Das Handy muss im selben WLAN sein.</span>
          <code>${esc(lan.url)}</code>
          <span class="muted">Falls Windows nach der Firewall fragt: Zugriff in <b>privaten Netzwerken</b> erlauben.</span></div></div>`
        : lan && !lan.available ? `<div class="muted small">Kein Heimnetz gefunden.</div>` : `<div class="loading"><div class="spinner"></div>Lade …</div>`}</div>` : ""}
    </div>
    ${S.autostart.supported ? `
    <div class="card"><div class="setting-row">
      <div class="grow"><b>Mit Windows starten</b><p>Restock-Radar startet unsichtbar beim Hochfahren und sitzt unten rechts neben der Uhr.</p></div>
      <label class="switch"><input type="checkbox" data-autostart ${S.autostart.enabled ? "checked" : ""} aria-label="Autostart an oder aus"><span></span></label>
    </div></div>` : ""}
    <p class="muted small" style="text-align:center">Restock-Radar ${esc(S.version)} · <button class="link-btn" data-act-wizard>Einrichtung erneut starten</button></p>`;
  if (set.lan_enabled && !ui.lan) loadLan();
}

function loadList() {
  const rows = S.load || [];
  if (!rows.length) return `<div class="muted small">Noch keine Shops in Benutzung.</div>`;
  const num = (x) => String(x).replace(".", ",");
  return `<ul class="shop-list">${rows.map((r) => `
    <li>${icon("store")}
      <div class="grow"><div class="n">${esc(r.host)}</div>
        <div class="d">geplant ${num(r.planned)} pro Min.${r.limit ? ` · erlaubt ${r.limit}` : ""} · letzte Minute ${r.recent}</div></div>
      ${r.stretch ? `<span class="chip warn" title="Prüfungen in diesem Shop dauern etwa ${num(r.stretch)}-mal so lange">gestreckt ×${num(r.stretch)}</span>`
        : `<span class="chip ok">im Rahmen</span>`}
    </li>`).join("")}</ul>`;
}

function renderLoad() {
  const el = $("#load-list");
  if (el && S) el.innerHTML = loadList();
}

function shopsCard() {
  const list = S.shops.length ? `<ul class="shop-list">${S.shops.map((s) => {
    const [label, cls] = SHOP_STATUS[s.status] || ["Unbekannt", ""];
    const how = { shopify: "Shopify-Suche", woo: "WooCommerce-Suche", html: "Suchseite" }[s.mode] || s.mode;
    return `<li data-shop="${s.id}">
      ${icon("store")}
      <div class="grow"><div class="n"><a href="${esc(safeUrl(s.base))}" target="_blank" rel="noopener">${esc(s.name)}</a></div>
        <div class="d">${esc(how)}${s.platform && s.mode === "html" ? ` · ${esc(s.platform)}` : ""}${s.reason ? ` · ${esc(s.reason)}` : ""}</div></div>
      <span class="chip ${cls}" title="${esc(s.reason || "")}">${label}</span>
      <label class="switch" title="An/Aus"><input type="checkbox" data-shop-toggle="${s.id}" ${s.enabled ? "checked" : ""} aria-label="${esc(s.name)} an oder aus"><span></span></label>
      <button class="icon-btn" data-act="shop-del" title="Shop entfernen" aria-label="Shop entfernen">${icon("trash")}</button>
    </li>`;
  }).join("")}</ul>` : `<div class="muted small">Noch keine Shops. Füge die Kartenshops hinzu, bei denen du kaufen würdest.</div>`;
  return `
    <div class="card" id="shops-card">
      <div class="card-head">
        <span class="ch-ico" style="background:#0f766e">${icon("store")}</span>
        <div class="grow"><h3>Shops für Suchaufträge</h3><p>Hier sucht Restock-Radar nach deinen Suchbegriffen. Nimm nur Shops, denen du vertraust – bei Pokémon gibt es viele Fakeshops.</p></div>
      </div>
      <div class="card-body">
        <div class="row"><input type="text" data-shop-url placeholder="https://kartenshop.de" aria-label="Link zum Shop">
          <button class="btn primary" data-act="shop-add">${icon("store")} Hinzufügen</button></div>
        ${list}
      </div>
    </div>`;
}

async function loadLan() {
  try { ui.lan = await api("/api/lan"); } catch { ui.lan = { available: false }; }
  if (ui.panelOpen) renderPanel();
}

async function onPanelChange(e) {
  if (await onChannelToggle(e)) return;
  const t = e.target;
  try {
    if (t.dataset.setting) {
      const key = t.dataset.setting;
      const value = t.type === "checkbox" ? t.checked : ["default_interval", "shop_budget"].includes(key) ? Number(t.value) : t.value;
      await api("/api/settings", { method: "PUT", body: { [key]: value } });
      toast("Gespeichert");
    } else if ("lan" in t.dataset) {
      const res = await api("/api/lan", { method: "POST", body: { enabled: t.checked } });
      ui.lan = res;
      if (res.restarting) {
        toast("Starte neu …");
        await waitForRestart();
      }
    } else if ("autostart" in t.dataset) {
      await api("/api/autostart", { method: "POST", body: { enabled: t.checked } });
      toast(t.checked ? "Startet ab jetzt mit Windows" : "Autostart aus");
    } else if (t.dataset.shopToggle) {
      await api(`/api/shops/${t.dataset.shopToggle}`, { method: "PATCH", body: { enabled: t.checked } });
    } else {
      return;
    }
    await refresh();
    renderPanel();
  } catch (err) {
    toast(err.message, true);
    await refresh();
    renderPanel();
  }
}

async function waitForRestart() {
  await new Promise((r) => setTimeout(r, 1500));
  for (let i = 0; i < 30; i++) {
    try { await api("/api/ping"); ui.lan = await api("/api/lan"); return; } catch { /* startet noch */ }
    await new Promise((r) => setTimeout(r, 700));
  }
}

function rerenderOverlays() {
  if (ui.panelOpen) renderPanel();
  if (ui.wizardStep !== null) renderWizard();
  renderHint();
}

/* ================================================================ Einrichtungs-Assistent */

function openWizard() {
  ui.wizardStep = 0;
  ui.wizardChannel = null;
  $("#wizard").hidden = false;
  renderWizard();
}

async function closeWizard(done) {
  ui.wizardStep = null;
  $("#wizard").hidden = true;
  if (done) {
    try { await api("/api/settings", { method: "PUT", body: { setup_done: true } }); } catch { /* egal */ }
    await refresh();
    $("#add-url").focus();
  }
}

function renderWizard() {
  const step = ui.wizardStep;
  const bars = `<div class="wizard-steps">${[0, 1, 2, 3].map((i) => `<span class="${i <= step ? "done" : ""}"></span>`).join("")}</div>`;
  let html = "";
  if (step === 0) {
    html = `${bars}
      <h2 id="wizard-title">Willkommen bei Restock-Radar</h2>
      <p class="lead">Ich behalte ausverkaufte Produkte für dich im Auge und melde mich in der Sekunde, in der sie wieder da sind.</p>
      <ul class="feature-list">
        <li>${icon("check")}<span><b>Link einfügen – fertig.</b> Ich finde selbst heraus, woran man erkennt, ob etwas verfügbar ist.</span></li>
        <li>${icon("check")}<span><b>Alarm aufs Handy</b> mit Bild, Preis und „Direkt zur Kasse“-Knopf.</span></li>
        <li>${icon("check")}<span><b>Fair zu den Shops:</b> Ich frage in Ruhe nach und weiche zurück, wenn ein Shop bremst.</span></li>
      </ul>
      <div class="wizard-foot"><button class="btn ghost" data-wz="skip">Später einrichten</button>
        <button class="btn primary" data-wz="next">Los geht's</button></div>`;
  } else if (step === 1) {
    html = `${bars}
      <h2 id="wizard-title">Wohin sollen die Meldungen?</h2>
      <p class="lead">Du kannst später weitere Wege hinzufügen.</p>
      <div class="choice-grid">
        <button class="choice" data-wz-channel="ntfy"><span class="ch-ico ntfy">N</span><span class="grow"><b>ntfy-App</b> <span class="chip ok">Empfohlen</span><p>Am einfachsten: App installieren, QR-Code scannen. Lautester Alarm.</p></span></button>
        <button class="choice" data-wz-channel="telegram"><span class="ch-ico telegram">T</span><span class="grow"><b>Telegram</b><p>Wenn du Telegram schon nutzt. Einrichtung dauert 2 Minuten.</p></span></button>
        <button class="choice" data-wz-channel="discord"><span class="ch-ico discord">D</span><span class="grow"><b>Discord</b><p>In einen Kanal deines Servers, z. B. für eine Gruppe.</p></span></button>
      </div>
      <div class="wizard-foot"><button class="btn ghost" data-wz="back">Zurück</button><button class="btn ghost" data-wz="skip">Später einrichten</button></div>`;
  } else if (step === 2) {
    const ready = S.channels[ui.wizardChannel].configured;
    html = `${bars}
      <h2 id="wizard-title">Einrichten</h2>
      ${channelCard(ui.wizardChannel, { wizard: true })}
      ${ready ? `<div class="callout ok">${icon("check")}<div class="txt">Eingerichtet! Schick dir zur Sicherheit einen Test.</div></div>` : ""}
      <div class="wizard-foot"><button class="btn ghost" data-wz="back">Zurück</button>
        <button class="btn primary" data-wz="next">${ready ? "Weiter" : "Überspringen"}</button></div>`;
  } else {
    html = `${bars}
      <h2 id="wizard-title">Alles bereit!</h2>
      <p class="lead">Füge jetzt oben deinen ersten Produktlink ein. Tipp: Lass Restock-Radar einfach laufen – es sitzt unten rechts neben der Uhr und arbeitet im Hintergrund.</p>
      ${S.autostart.supported && !S.autostart.enabled ? `
        <div class="card"><div class="setting-row"><div class="grow"><b>Mit Windows starten?</b><p>Empfohlen – dann verpasst du auch nach einem Neustart nichts.</p></div>
        <label class="switch"><input type="checkbox" data-autostart aria-label="Autostart"><span></span></label></div></div>` : ""}
      <div class="wizard-foot"><span></span><button class="btn primary" data-wz="done">Loslegen</button></div>`;
  }
  $("#wizard-card").innerHTML = html;
}

async function onWizardClick(e) {
  const ch = e.target.closest("[data-wz-channel]");
  if (ch) {
    ui.wizardChannel = ch.dataset.wzChannel;
    ui.wizardStep = 2;
    if (ui.wizardChannel === "ntfy" && !S.channels.ntfy.configured) {
      try { await api("/api/channels/ntfy", { method: "POST", body: {} }); await refresh(); } catch (err) { toast(err.message, true); }
    }
    renderWizard();
    return;
  }
  const nav = e.target.closest("[data-wz]");
  if (!nav) return onChannelAction(e);
  const act = nav.dataset.wz;
  if (act === "skip") return closeWizard(true);
  if (act === "done") return closeWizard(true);
  if (act === "back") ui.wizardStep = Math.max(0, ui.wizardStep - 1);
  if (act === "next") ui.wizardStep = Math.min(3, ui.wizardStep + 1);
  renderWizard();
}

async function onWizardChange(e) {
  if (await onChannelToggle(e)) return;
  if ("autostart" in e.target.dataset) {
    try {
      await api("/api/autostart", { method: "POST", body: { enabled: e.target.checked } });
      toast(e.target.checked ? "Startet ab jetzt mit Windows" : "Autostart aus");
      await refresh();
    } catch (err) { toast(err.message, true); }
  }
}

/* ================================================================ Verdrahtung */

function wire() {
  $("#btn-settings").innerHTML = icon("settings");
  $("#btn-phone").innerHTML = icon("phone");
  $("#panel [data-close]").innerHTML = icon("x");

  $("#add-form").addEventListener("submit", (e) => { e.preventDefault(); submitPreview(); });
  $("#add-url").addEventListener("paste", () => setTimeout(() => {
    if (/^\s*(https?:\/\/)?[\w-]+(\.[\w-]+)+\S*\s*$/i.test($("#add-url").value)) submitPreview();
  }, 0));

  $("#preview").addEventListener("click", (e) => {
    const prio = e.target.closest("[data-prio]");
    if (prio) { ui.prio = prio.dataset.prio; renderPreview(); return; }
    const act = e.target.closest("[data-act]");
    if (!act) return;
    if (act.dataset.act === "watch") addWatch(act);
    if (act.dataset.act === "cancel") { ui.preview = null; renderPreview(); $("#add-url").focus(); }
  });
  $("#preview").addEventListener("change", (e) => {
    if (e.target.matches("[data-interval]")) ui.interval = Number(e.target.value);
    if (e.target.matches("[data-variant]")) { ui.variant = e.target.value || null; renderPreview(); }
  });

  $("#watches").addEventListener("click", onWatchAction);
  $("#searches").addEventListener("click", onSearchAction);
  $("#add-mode").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (b) setMode(b.dataset.mode);
  });
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); submitSearch(e.target); });
  $("#search-form").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sprio]");
    if (!b) return;
    ui.searchPrio = b.dataset.sprio;
    document.querySelectorAll("[data-sprio]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.sprio === ui.searchPrio)));
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-open-settings]")) openPanel();
    if (e.target.closest("[data-open-shops]")) openPanel("shops-card");
    if (e.target.closest("[data-act-wizard]")) { closePanel(); openWizard(); }
  });

  $("#btn-settings").addEventListener("click", () => openPanel());
  $("#btn-phone").addEventListener("click", () => openPanel("lan-card"));
  $("#panel [data-close]").addEventListener("click", closePanel);
  $("#panel-backdrop").addEventListener("click", closePanel);
  $("#panel-body").addEventListener("click", onChannelAction);
  $("#panel-body").addEventListener("change", onPanelChange);
  $("#wizard-card").addEventListener("click", onWizardClick);
  $("#wizard-card").addEventListener("change", onWizardChange);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && ui.panelOpen) closePanel(); });
}

(async function start() {
  wire();
  await refresh();
  if (S && !S.settings.setup_done && !channelReady()) openWizard();
  setInterval(refresh, 3000);
  setInterval(tick, 1000);
})();
