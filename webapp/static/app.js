"use strict";
// masGold reports for the iPad: read only, except the Dead Cash write-off of a customer balance (needs the user's current password). Every figure is a display string made by the server (the same numbers as the desktop program); the page never calculates money.
// Data is inserted with textContent only (no innerHTML), so no report text can inject markup.

const app = document.getElementById("app");
const state = { tiles: null, legacy: null, today: null, special: null, ps: null, stmt: null, cs: null, invoice: null, custInvoices: null, module: "", redraws: [], overview: null, report: "", filters: {}, result: null, page: 1, busy: false, error: "" };

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "style") {                                            // CSSOM, not the style attribute: the page's CSP refuses inline style attributes
      for (const rule of String(v).split(";")) {
        const at = rule.indexOf(":");
        if (at > 0) node.style.setProperty(rule.slice(0, at).trim(), rule.slice(at + 1).trim());
      }
    }
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c.nodeType ? c : document.createTextNode(String(c)));
  return node;
}

async function call(path, options) {
  const init = { credentials: "same-origin", headers: { "X-Requested-With": "masgold" }, ...options };
  if (init.body) init.headers["Content-Type"] = "application/json";
  const response = await fetch(path, init);
  let data = {};
  try { data = await response.json(); } catch (e) { /* an empty answer */ }
  if (!response.ok) {
    const error = new Error(data.error || "تعذر تنفيذ الطلب.");
    error.status = response.status;
    throw error;
  }
  return data;
}

// ---------------------------------------------------------------- login
function showLogin(message) {
  state.overview = null;
  const user = el("input", { type: "text", autocomplete: "username", placeholder: "اسم المستخدم", autocapitalize: "none", autocorrect: "off" });
  const pass = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة المرور" });
  const err = el("div", { class: "msg err", text: message || "", hidden: !message });
  const go = el("button", { class: "btn primary", text: "دخول" });
  async function submit() {
    go.disabled = true; err.hidden = true;
    try {
      await call("/api/login", { method: "POST", body: JSON.stringify({ username: user.value, password: pass.value }) });
      pass.value = "";
      await boot();
    } catch (e) {
      err.textContent = e.message; err.hidden = false; go.disabled = false;
    }
  }
  go.addEventListener("click", submit);
  pass.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  app.replaceChildren(el("div", { class: "login" }, el("h1", { text: "تقارير مجوهرات ماس" }), user, pass, err, go,
    el("div", { class: "note-box", text: "عرض فقط — نفس حساب البرنامج وصلاحياته." })));
}

// ---------------------------------------------------------------- shell
async function boot() {
  try {
    state.overview = await call("/api/overview");
  } catch (e) {
    showLogin(e.status === 401 ? "" : e.message);
    return;
  }
  state.module = state.overview.modules.length === 1 ? state.overview.modules[0].key : "";      // the home page of cards (a single card opens straight away)
  startAttentionPolling();
  if (state.module) openModule(state.module); else { render(); loadToday(); loadTiles(); loadAttention(false); }
}

function currentModule() { return state.overview.modules.find((m) => m.key === state.module) || null; }

function openModule(key) {
  state.cs = null; state.ps = null; state.special = null; state.module = key; state.report = ""; state.result = null; state.filters = {}; state.page = 1; state.error = ""; state.chart = null; state.writeoff = null;
  const module = currentModule();
  if (key === "inventory") loadToday();
  if (module.kind && module.kind !== "reports") { loadSpecial(module.kind); return; }
  const first = module.groups[0].reports[0];
  const today = state.overview.today;
  if (isGrid(module)) { render(); return; }                                                                            // many reports: a grid of report cards, one is opened from it
  if (first) selectReport(first.key, module.period === "today" ? { from: today, to: today } : {}); else render();     // a daily card opens on TODAY
}

// A card with more than three reports shows them as cards too (less visual noise than a row of buttons); a small card keeps its buttons.
const GRID_MIN = 4;
function isGrid(module) { return !!module && module.count >= GRID_MIN; }

function backToList() {
  state.report = ""; state.result = null; state.error = ""; state.chart = null; state.writeoff = null; state.invoice = null; state.custInvoices = null; state.busy = false;
  render();
}

const REPORT_ICONS = { goldflow: "📈", stock: "📦", alerts: "⚠️", stockweights: "⚖️", indicators: "💹", gold: "🏷️", scrapcost: "♻️", demand: "🗓️", profit: "💰", market: "🧭",
  makingprofit: "🛠️", employees: "👷", suppliers: "🏭", treasury: "🏦", cashflow: "💸", sales: "🧾", customers: "👥", deadcash: "🚫" };

function renderReportCards(module) {
  const box = el("div", {});
  for (const g of module.groups) {
    if (g.name) box.append(el("div", { class: "section", text: g.name }));
    box.append(el("div", { class: "tiles small" }, g.reports.map((r) =>
      el("button", { class: "tile", onclick: () => selectReport(r.key, module.period === "today" ? { from: state.overview.today, to: state.overview.today } : {}) },
        el("span", { class: "icon", text: REPORT_ICONS[r.key] || "📄" }), el("span", { class: "name", text: r.title })))));
  }
  return box;
}

function goHome() {
  loadToday();
  loadTiles();
  loadAttention(false);
  state.special = null; state.stmt = null; state.legacy = null; state.module = ""; state.report = ""; state.result = null; state.error = ""; state.chart = null; state.writeoff = null; state.invoice = null; state.custInvoices = null; state.busy = false;
  render();
}

function renderHome() {
  return el("div", {}, el("h2", { text: "الرئيسية", style: "margin-bottom:12px" }),
    el("div", { class: "tiles" }, state.overview.modules.map((m) =>
      renderTile(m))));
}

// A module card with its live figures (lines), an alert badge and a 7-day line, all from /api/tiles; without them it is the plain card.
function renderTile(m) {
  const info = state.tiles && state.tiles[m.key];
  const head = el("span", { class: "head" }, el("span", { class: "icon", text: m.icon }), el("span", { class: "name", text: m.title }),
    info && info.badge ? el("span", { class: "badge", text: info.badge }) : null);
  const parts = [head, el("span", { class: "sub", text: m.subtitle })];
  if (info && info.lines && info.lines.length) {
    parts.push(el("span", { class: "lines" }, info.lines.map((l) => el("span", { class: "tl " + (l.tone || "") }, el("span", { class: "k", text: l.label }), el("span", { class: "v", text: l.value })))));
  }
  if (info && info.spark && info.spark.length) parts.push(renderSpark(info.spark));
  return el("button", { class: "tile" + (info && info.lines && info.lines.length ? " live" : ""), onclick: () => openModule(m.key) }, parts);
}

function renderSpark(values) {
  const NS = "http://www.w3.org/2000/svg", w = 120, h = 28, step = w / Math.max(values.length - 1, 1);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("class", "spark"); svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(NS, "polyline");
  line.setAttribute("points", values.map((v, i) => `${(i * step).toFixed(1)},${(h - 3 - v * (h - 8)).toFixed(1)}`).join(" "));
  line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "2"); line.setAttribute("stroke-linejoin", "round");
  svg.append(line);
  return svg;
}

async function loadTiles() {
  try {
    state.tiles = (await call("/api/tiles")).tiles;
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    state.tiles = null;
  }
  if (!state.module && !state.special) render();
}

async function logout() {
  try { await call("/api/logout", { method: "POST", body: "{}" }); } catch (e) { /* the cookie is cleared anyway */ }
  state.report = ""; state.result = null; state.filters = {};
  showLogin("");
}

function selectReport(key, initial) {
  state.report = key; state.filters = { ...(initial || {}) }; state.result = null; state.page = 1; state.error = ""; state.chart = null; state.writeoff = null;
  load();
}

async function load() {
  if (!state.report) return;
  state.busy = true; state.error = "";
  render();
  const params = new URLSearchParams({ key: state.report, page: String(state.page) });
  for (const [k, v] of Object.entries(state.filters)) if (v !== "" && v !== undefined) params.set(k, v);
  const asked = state.report;
  try {
    const result = await call("/api/report?" + params.toString());
    if (asked !== state.report) return;                                  // a newer choice was made meanwhile
    state.result = result;
    if (result.periodUsed) { state.filters.from = result.filters.from; state.filters.to = result.filters.to; }
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    state.error = e.message; state.result = null;
  }
  state.busy = false;
  render();
}

function render() {
  if (!state.overview) return;
  state.redraws = [];                                                    // the charts of the previous render are gone
  const module = currentModule();
  if (!module || module.kind !== "stocktake") stopCamera();              // leaving the card releases the camera
  const grid = isGrid(module), inReport = !!state.report;
  const back = !module || state.overview.modules.length < 2 ? null
    : grid && inReport ? el("button", { class: "btn", text: "← " + module.title, onclick: backToList }) : el("button", { class: "btn", text: "← الرئيسية", onclick: goHome });
  const top = el("div", { class: "top" }, el("h1", { text: module ? module.title : "مجوهرات ماس" }), back,
    el("span", { class: "who", text: state.overview.user }), el("button", { class: "btn", text: "خروج", onclick: logout }));
  if (!module) { app.replaceChildren(...[top, renderAttention(), renderToday(), renderHome()].filter(Boolean)); return; }
  if (module.kind && module.kind !== "reports") {
    const draw = { reviews: renderReviews, transfers: renderTransfers, calc: renderCalc, stocktake: renderStocktake, activity: renderActivity, bankflow: renderBankflow, cashflow: renderCashflow, currency: renderCurrency }[module.kind];
    const view = state.special ? draw(state.special) : el("div", { class: "loading", text: "جارٍ التحميل…" });
    app.replaceChildren(...[top, view, state.custInvoices ? renderCustomerInvoices(state.custInvoices) : null, state.invoice ? renderInvoice(state.invoice) : null].filter(Boolean));
    return;
  }
  if (grid && !inReport) { app.replaceChildren(top, renderReportCards(module)); return; }
  const groups = grid ? null : el("div", { class: "groups" }, module.groups.map((g) =>
    el("div", { class: "group" }, g.name ? el("span", { class: "name", text: g.name }) : null,
      g.reports.map((r) => el("button", { class: "chip" + (r.key === state.report ? " on" : ""), text: r.title, onclick: () => selectReport(r.key) })))));
  // the search boxes belong to the card, not to a report: they stay even when a report is loading or fails
  const search = state.module === "customers" ? renderCustomerSearch() : state.module === "inventory" ? renderPieceSearch() : null;
  const body = el("div", {});
  if (state.module === "inventory" && state.today && state.today.stockCards && state.today.stockCards.length) {
    body.append(el("div", { class: "cards" }, state.today.stockCards.map((c) => el("div", { class: "card " + (c.tone || "") }, el("div", { class: "label", text: c.label }), el("div", { class: "value", text: c.value }),
      c.note ? el("div", { class: "note", text: c.note }) : null))));
  }
  if (state.error) body.append(el("div", { class: "msg err", text: state.error }));
  if (state.busy) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (state.result) body.append(renderResult(state.result));
  // replaceChildren turns a null into the text "null", so only real nodes are passed
  app.replaceChildren(...[top, groups, search, body, state.chart ? renderChartOverlay(state.chart) : null, state.writeoff ? renderWriteOff(state.writeoff) : null,
    state.custInvoices ? renderCustomerInvoices(state.custInvoices) : null, state.stmt ? renderStatement(state.stmt) : null, state.invoice ? renderInvoice(state.invoice) : null, state.legacy ? renderLegacy(state.legacy) : null].filter(Boolean));
}

// ---------------------------------------------------------------- one report
async function shareText(title, text) {
  if (navigator.share) { try { await navigator.share({ title, text }); return; } catch (e) { if (e && e.name === "AbortError") return; } }
  try { await navigator.clipboard.writeText(text); alert("تم نسخ الملخص."); } catch (e) { alert(text); }
}

async function shareReport() {
  const params = new URLSearchParams({ key: state.report });
  for (const [k, v] of Object.entries(state.filters)) if (v !== "" && v !== undefined && k !== "page") params.set(k, v);
  try {
    const response = await fetch("/api/export?" + params.toString(), { credentials: "same-origin", headers: { "X-Requested-With": "masgold" } });
    if (!response.ok) throw new Error("export");
    const file = new File([await response.blob()], state.report + ".csv", { type: "text/csv" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: "تقرير " + state.report }); return; }
  } catch (e) { if (e && e.name === "AbortError") return; }
  downloadCsv();
}

function downloadCsv() {
  const params = new URLSearchParams({ key: state.report });
  for (const [k, v] of Object.entries(state.filters)) if (v !== "" && v !== undefined && k !== "page") params.set(k, v);
  const link = el("a", { href: "/api/export?" + params.toString(), download: "" });
  document.body.append(link);
  link.click();
  link.remove();
}

function isoDay(base, offset) {
  const d = new Date(base + "T12:00:00");
  d.setDate(d.getDate() + offset);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

function quickPeriods() {
  const today = state.overview.today;
  const apply = (from, to) => () => { state.filters.from = from; state.filters.to = to; state.page = 1; load(); };
  return el("div", { class: "group", style: "margin-bottom:8px" }, el("span", { class: "name", text: "الفترة" }),
    [["اليوم", today, today], ["أمس", isoDay(today, -1), isoDay(today, -1)], ["آخر 7 أيام", isoDay(today, -6), today], ["هذا الشهر", today.slice(0, 8) + "01", today]].map(([text, from, to]) =>
      el("button", { class: "chip" + (state.filters.from === from && state.filters.to === to ? " on" : ""), text, onclick: apply(from, to) })));
}

function renderFilters(result) {
  const fields = [];
  if (result.periodUsed) {
    fields.push(field("من", el("input", { type: "date", value: state.filters.from || "", onchange: (e) => { state.filters.from = e.target.value; } })));
    fields.push(field("إلى", el("input", { type: "date", value: state.filters.to || "", onchange: (e) => { state.filters.to = e.target.value; } })));
  }
  for (const spec of result.filterSpecs || []) {
    if (spec.type === "text") {
      fields.push(field(spec.label, el("input", { type: "text", value: state.filters[spec.key] || "", oninput: (e) => { state.filters[spec.key] = e.target.value; } })));
    } else {
      const select = el("select", { onchange: (e) => { state.filters[spec.key] = e.target.value; } },
        spec.options.map((o) => el("option", { value: o.value, text: o.text, selected: String(state.filters[spec.key] ?? spec.options[0].value) === String(o.value) })));
      fields.push(field(spec.label, select));
    }
  }
  if (result.searchable) {
    const search = el("input", { type: "text", value: state.filters.text || "", placeholder: "اسم أو رقم", oninput: (e) => { state.filters.text = e.target.value; } });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") { state.page = 1; load(); } });
    fields.push(field("بحث", search));
  }
  fields.push(el("button", { class: "btn primary", text: "عرض", onclick: () => { state.page = 1; load(); } }));
  return el("div", { class: "filters" }, fields);
}

function field(label, input) { return el("div", { class: "field" }, el("label", { text: label }), input); }

const KARAT_CLASS = { 18: "k18", 21: "k21", 22: "k22", 24: "k24" };
const CHART_REFS = new Set(["CHART", "PRICE_CHART", "DEMAND_CHART", "PIECES", "INVOICE"]);
// line colours by meaning: the shop BUYS (green/teal), the shop SELLS (amber), the average is grey; yearly lines use a fixed palette (newest first)
const TONE_COLORS = { total: "#5EC3CE", gold: "#DDB961", buy: "#5EC3CE", sell: "#E7AA5B", avg: "#A5B2C3" };
const YEAR_COLORS = ["#DDB961", "#70B0F1", "#5EC3CE", "#A5B2C3", "#77C599", "#E7AA5B"];
function toneColor(tone) {
  if (/^y\d+$/.test(tone || "")) return YEAR_COLORS[Number(tone.slice(1)) % YEAR_COLORS.length];
  return TONE_COLORS[tone] || TONE_COLORS.gold;
}

async function searchCustomers(page) {
  const c = state.cs;
  c.loading = true; c.error = ""; c.page = page || 1;
  render();
  try {
    c.data = await call("/api/customer_search?text=" + encodeURIComponent(c.text) + "&page=" + c.page);
    c.searched = c.text;
    if (c.data.invoice && c.page === 1) { c.loading = false; render(); openInvoice(c.data.invoice.id); return; }      // an exact invoice number opens the invoice itself
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    c.error = e.message;
  }
  c.loading = false;
  render();
}

function renderCustomerSearch() {
  if (!state.cs) state.cs = { text: "", data: null, loading: false, error: "", page: 1, searched: "" };
  const c = state.cs;
  const input = el("input", { type: "text", value: c.text, placeholder: "اسم العميل أو الموبايل أو الكود أو رقم الفاتورة", oninput: (e) => { c.text = e.target.value; } });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && c.text.trim()) searchCustomers(1); });
  const box = el("div", { class: "searchbox" }, el("div", { class: "filters" }, field("بحث عن عميل أو فاتورة", input),
    el("button", { class: "btn primary", text: "بحث", disabled: c.loading, onclick: () => { if (c.text.trim()) searchCustomers(1); } }),
    c.data ? el("button", { class: "btn", text: "مسح", onclick: () => { state.cs = null; render(); } }) : null));
  if (c.loading) box.append(el("div", { class: "loading", text: "جارٍ البحث…" }));
  if (c.error) box.append(el("div", { class: "msg err", text: c.error }));
  const d = c.data;
  if (d && !c.loading) {
    if (d.invoice) box.append(el("div", { class: "msg ok" }, "الفاتورة " + d.invoice.number + "  ", el("button", { class: "btn small", text: "فتح الفاتورة", onclick: () => openInvoice(d.invoice.id) })));
    else if (/^[A-Za-z0-9]{6,20}$/.test(c.searched) && c.page === 1 && !d.customers.length) box.append(el("div", { class: "msg warn", text: "لا توجد فاتورة بهذا الرقم ولا عميل بهذا الاسم." }));
    if (d.customers.length) {
      const canWriteOff = !!(state.overview && state.overview.canWriteOff);
      box.append(el("div", { class: "section", text: "العملاء" }));
      box.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["العميل", "الكود", "الموبايل", "عليه", "له", ""].map((t) => el("th", { text: t })))),
        el("tbody", {}, d.customers.map((r) => el("tr", { class: r.active ? "" : "dim" }, el("td", { text: r.name }), el("td", { text: r.code }), el("td", { text: r.mobile }),
          el("td", { text: r.owes, class: r.owes ? "owes" : "" }), el("td", { text: r.credit, class: r.credit ? "credit" : "" }),
          el("td", {}, el("div", { class: "actions" }, el("button", { class: "btn small", text: "فواتيره", onclick: () => openCustomerInvoices(r) }),
            el("button", { class: "btn small", text: "كشف الحساب", onclick: () => openStatement(r) }),
            state.overview.canLegacy ? el("button", { class: "btn small", text: "رصيد قديم", onclick: () => openLegacy(r) }) : null,
            canWriteOff && r.owes ? el("button", { class: "btn small", text: "شطب رصيد", onclick: () => openWriteOff(r) }) : null))))))));
      if (d.page > 1 || d.hasMore) {
        box.append(el("div", { class: "pager" }, el("button", { class: "btn", text: "التالي", disabled: !d.hasMore, onclick: () => searchCustomers(d.page + 1) }), el("span", { text: "صفحة " + d.page }),
          el("button", { class: "btn", text: "السابق", disabled: d.page <= 1, onclick: () => searchCustomers(d.page - 1) })));
      }
    } else if (!d.invoice && !(/^[A-Za-z0-9]{6,20}$/.test(c.searched) && c.page === 1)) box.append(el("div", { class: "note-box", text: "لا يوجد عميل مطابق." }));
  }
  return box;
}

function renderResult(result) {
  const out = el("div", {});
  out.append(el("h2", { text: result.title, style: "margin-bottom:10px" }));
  out.append(el("div", { class: "group", style: "margin-bottom:8px" }, el("button", { class: "btn small", text: "تنزيل CSV", onclick: () => downloadCsv() }),
    navigator.share ? el("button", { class: "btn small", text: "مشاركة", onclick: () => shareReport() }) : null));
  if (result.periodUsed) out.append(quickPeriods());
  out.append(renderFilters(result));
  if (result.applied && result.applied.length) out.append(el("div", { class: "note-box", text: result.applied.join(" • ") }));
  if (result.totals.length) {
    out.append(el("div", { class: "cards" }, result.totals.map((t) =>
      el("div", { class: "card " + (t.tone || "") + " " + (KARAT_CLASS[t.karat] || "") }, el("div", { class: "label", text: t.label }), el("div", { class: "value", text: t.value }),
        t.note ? el("div", { class: "note", text: t.note }) : null))));
  }
  for (const group of result.lines || []) out.append(renderLine(group));
  for (const group of result.bars || []) out.append(renderBars(group));
  if (result.report === "indicators") out.append(liveGoldChart());
  if (result.balances) out.append(renderBalances(result.balances));
  result.tables.forEach((t) => out.append(renderTable(t)));
  if (result.meaning && result.meaning.length) out.append(el("details", { class: "meaning" }, el("summary", { text: "معنى الأرقام" }), result.meaning.map((m) => el("div", { text: m }))));
  return out;
}

function renderTable(t) {
  const box = el("div", {});
  box.append(el("div", { class: "section" }, t.karat > 0 ? el("span", { class: "bar", style: "background:var(--" + KARAT_CLASS[t.karat] + ")" }) : null,
    t.title + (t.paged ? "  (" + t.rowCount + ")" : "")));
  if (!t.available) { box.append(el("div", { class: "msg warn", text: "غير متاح: " + t.reason })); return box; }
  if (!t.rows.length) { box.append(el("div", { class: "note-box", text: "لا توجد بيانات مطابقة." })); return box; }
  const actionable = t.rows.some((r) => r.refText && CHART_REFS.has(r.ref && r.ref.type));
  const head = el("tr", {}, t.columns.map((c) => el("th", { text: c.title })), actionable ? el("th", { text: "" }) : null);
  const rows = t.rows.map((r) => el("tr", { class: r.dim ? "dim" : "" }, r.cells.map((c) => el("td", { text: c })),
    actionable ? el("td", {}, r.refText && CHART_REFS.has(r.ref.type) ? el("button", { class: "btn small", text: r.refText, onclick: () => (r.ref.type === "INVOICE" ? openInvoice(r.ref.id) : openChart(r.ref)) }) : null) : null));
  box.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, rows))));
  if (t.note) box.append(el("div", { class: "note-box", text: t.note }));
  if (t.paged && (t.page > 1 || t.hasMore)) {
    box.append(el("div", { class: "pager" },
      el("button", { class: "btn", text: "التالي", disabled: !t.hasMore, onclick: () => { state.page = t.page + 1; load(); } }),
      el("span", { text: "صفحة " + t.page }),
      el("button", { class: "btn", text: "السابق", disabled: t.page <= 1, onclick: () => { state.page = t.page - 1; load(); } })));
  }
  return box;
}

function renderBars(group) {
  const color = group.karat > 0 ? "var(--" + KARAT_CLASS[group.karat] + ")" : group.tone === "payable" ? "var(--sell)" : "var(--buy)";
  const box = el("div", { class: "bars" }, el("div", { class: "section", text: group.title, style: "color:" + color }));
  if (!group.rows.length) box.append(el("div", { class: "note-box", text: "لا توجد حركات في هذه الفترة." }));
  for (const r of group.rows) {
    box.append(el("div", { class: "row" }, el("span", { class: "lbl", text: r.label }),
      el("div", { class: "track" }, el("div", { class: "fill", style: "width:" + Math.max(r.ratio > 0 ? 0.5 : 0, r.ratio * 100) + "%;background:" + color })),
      el("span", { class: "val", text: r.text })));
  }
  return box;
}

// ---------------------------------------------------------------- line chart (a point per day: does the gold grow or shrink)
function renderLine(group) {
  const canvas = el("canvas", {});
  const box = el("div", {}, group.title ? el("div", { class: "section", text: group.title }) : null,
    el("div", { class: "chartbox" },
      el("div", { class: "legend" }, group.series.map((s) => el("span", {}, el("i", { style: "background:" + toneColor(s.tone) }), s.name))), canvas));
  const draw = () => {
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w) return;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const labels = group.labels;
    if (labels.length < 2) return;
    let low = group.low, high = group.high, pad = (high - low) * 0.1;
    if (pad <= 0) pad = Math.max(1, Math.abs(high) * 0.05);
    low -= pad; high += pad;
    const left = 64, right = 10, top = 10, bottom = 24, cw = w - left - right, ch = h - top - bottom;
    const x = (i) => left + cw * i / (labels.length - 1), y = (v) => top + ch * (1 - (v - low) / (high - low));
    ctx.font = "11px sans-serif"; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const gv = low + (high - low) * g / 4;
      ctx.strokeStyle = "#303842"; ctx.beginPath(); ctx.moveTo(left, y(gv)); ctx.lineTo(left + cw, y(gv)); ctx.stroke();
      ctx.fillStyle = "#858D97"; ctx.fillText(gv.toFixed(1), 4, y(gv) + 4);
    }
    const step = Math.max(1, Math.ceil(labels.length / 10));
    ctx.fillStyle = "#858D97";
    for (let i = 0; i < labels.length; i += step) ctx.fillText(labels[i], x(i) - 12, h - 6);
    for (const s of group.series) {
      const color = toneColor(s.tone);
      ctx.setLineDash(s.tone === "avg" ? [6, 4] : []);
      ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.beginPath();
      let started = false;
      s.values.forEach((v, k) => {
        if (v === null) { started = false; return; }
        if (!started) { ctx.moveTo(x(k), y(v)); started = true; } else ctx.lineTo(x(k), y(v));
      });
      ctx.stroke(); ctx.fillStyle = color;
      ctx.setLineDash([]);
      if (labels.length <= 60) s.values.forEach((v, k) => { if (v !== null) { ctx.beginPath(); ctx.arc(x(k), y(v), 3, 0, 6.2832); ctx.fill(); } });
    }
  };
  state.redraws.push(draw);
  requestAnimationFrame(draw);
  return box;
}

// ---------------------------------------------------------------- the pop-up charts of the desktop reports (price of a karat, yearly sales lines, monthly sales of a type, the pieces behind a row)
function openChart(ref) {
  state.chart = { ref, loading: true, error: "", data: null, days: 90, granularity: ref.id === "W" ? "W" : "M", years: "" };
  loadChart();
}

async function loadChart() {
  const c = state.chart;
  if (!c) return;
  c.loading = true; c.error = "";
  render();
  const params = new URLSearchParams({ kind: c.ref.type, id: c.ref.id || "", extra: c.ref.extra || "", report: state.report });
  if (c.ref.type === "PRICE_CHART") params.set("days", String(c.days));
  if (c.ref.type === "DEMAND_CHART") { params.set("id", c.granularity); if (c.years) params.set("years", c.years); }
  if (c.ref.type === "CHART" || c.ref.type === "PIECES") for (const [k, v] of Object.entries(state.filters)) if (v !== "" && v !== undefined && k !== "page") params.set(k, v);
  try {
    const data = await call("/api/chart?" + params.toString());
    if (state.chart !== c) return;
    c.data = data;
    if (data.selected) c.years = data.selected.join(",");
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    c.error = e.message;
  }
  c.loading = false;
  render();
}

function chartControls(c) {
  const row = el("div", { class: "group", style: "margin-bottom:10px" });
  if (c.ref.type === "PRICE_CHART") {
    [[30, "30 يوم"], [90, "90 يوم"], [365, "سنة"], [0, "الكل"]].forEach(([days, text]) =>
      row.append(el("button", { class: "chip" + (c.days === days ? " on" : ""), text, onclick: () => { c.days = days; loadChart(); } })));
  } else if (c.ref.type === "DEMAND_CHART") {
    [["M", "شهري"], ["W", "أسبوعي"]].forEach(([g, text]) =>
      row.append(el("button", { class: "chip" + (c.granularity === g ? " on" : ""), text, onclick: () => { c.granularity = g; loadChart(); } })));
    const selected = (c.data && c.data.selected) || [];
    ((c.data && c.data.available) || []).forEach((year) => {
      const on = selected.includes(year);
      row.append(el("button", { class: "chip" + (on ? " on" : ""), text: year, onclick: () => {
        const next = on ? selected.filter((y) => y !== year) : [year, ...selected];
        if (!next.length) return;
        c.years = next.sort().reverse().join(","); loadChart();
      } }));
    });
  }
  return row;
}

function renderChartOverlay(c) {
  const body = el("div", {}, chartControls(c));
  if (c.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (c.error) body.append(el("div", { class: "msg err", text: c.error }));
  const d = c.data;
  if (d && !c.loading) {
    if (d.applied && d.applied.length) body.append(el("div", { class: "note-box", text: d.applied.join(" • ") }));
    if (d.count !== undefined) body.append(el("div", { class: "cards" }, el("div", { class: "card" }, el("div", { class: "label", text: "الإجمالي" }), el("div", { class: "value", text: d.count + " قطعة • " + d.weight }))));
    if (d.kind === "DEMAND_CHART" && !d.hasData) body.append(el("div", { class: "note-box", text: "لا توجد بيانات مبيعات بأوزان معروفة لهذه السنة." }));
    (d.lines || []).forEach((g) => body.append(renderLine(g)));
    (d.bars || []).forEach((g) => body.append(renderBars(g)));
    if (d.pieces) {
      body.append(el("div", { class: "note-box", text: d.pieces.count + " قطعة • " + d.pieces.weight }));
      body.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, d.pieces.columns.map((t) => el("th", { text: t })))),
        el("tbody", {}, d.pieces.rows.map((r) => el("tr", {}, r.map((cell) => el("td", { text: cell }))))))));
    }
  }
  return el("div", { class: "overlay" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: (d && d.title) || "رسم بياني" }), el("button", { class: "btn", text: "إغلاق", onclick: () => { state.chart = null; render(); } })), body));
}

// ---------------------------------------------------------------- customers: who owes the shop / whom the shop owes, and the Dead Cash write-off (the only write of this app)
function renderBalances(b) {
  const box = el("div", {});
  box.append(el("div", { class: "section", text: "أرصدة العملاء (" + b.total + ")" }));
  if (!b.rows.length) { box.append(el("div", { class: "note-box", text: "لا توجد أرصدة مطابقة." })); return box; }
  const canWriteOff = !!(state.overview && state.overview.canWriteOff);
  const head = el("tr", {}, ["العميل", "الكود", "عليه (للمحل)", "له (على المحل)"].map((t) => el("th", { text: t })), el("th", { text: "" }));
  const rows = b.rows.map((r) => el("tr", {}, el("td", { text: r.name }), el("td", { text: r.code }), el("td", { text: r.owes, class: r.owes ? "owes" : "" }), el("td", { text: r.credit, class: r.credit ? "credit" : "" }),
    el("td", {}, el("div", { class: "actions" }, el("button", { class: "btn small", text: "فواتيره", onclick: () => openCustomerInvoices(r) }),
      el("button", { class: "btn small", text: "كشف الحساب", onclick: () => openStatement(r) }),
      state.overview.canLegacy ? el("button", { class: "btn small", text: "رصيد قديم", onclick: () => openLegacy(r) }) : null,
      canWriteOff && r.owes ? el("button", { class: "btn small", text: "شطب رصيد", onclick: () => openWriteOff(r) }) : null))));
  box.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, rows))));
  if (b.limited) box.append(el("div", { class: "note-box", text: "المعروض أكبر 300 رصيد؛ استخدم البحث للوصول لعميل معين." }));
  return box;
}

function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex(4) + "-" + hex(2) + "-4" + hex(2).slice(1) + "-a" + hex(2).slice(1) + "-" + hex(6);
}

async function openWriteOff(row) {
  const w = { row, ctx: null, error: "", busy: true, done: null, amount: "", reason: "", key: newKey() };
  state.writeoff = w;
  render();
  try {
    w.ctx = await call("/api/deadcash/context?customer=" + encodeURIComponent(row.id));
    w.amount = w.ctx.balance;
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    w.error = e.message;
  }
  w.busy = false;
  render();
}

async function submitWriteOff(w, password) {
  w.busy = true; w.error = "";
  render();
  try {
    w.done = await call("/api/deadcash/writeoff", { method: "POST", body: JSON.stringify({
      customer: w.ctx.customer_id, amount: w.amount, reason: w.reason, password, expected_balance: w.ctx.balance, request_key: w.key }) });
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    w.error = e.message;
  }
  password = "";
  w.busy = false;
  render();
}

function renderWriteOff(w) {
  const body = el("div", {});
  if (w.busy) body.append(el("div", { class: "loading", text: "جارٍ التنفيذ…" }));
  if (w.error) body.append(el("div", { class: "msg err", text: w.error }));
  if (w.done) {
    body.append(el("div", { class: "msg ok", text: "تم الشطب " + w.done.code + " — الرصيد قبل " + w.done.balance_before + " وبعد " + w.done.balance_after + (w.done.duplicate ? " (طلب مكرر لم يُنفَّذ مرة ثانية)" : "") }));
    body.append(el("button", { class: "btn primary", text: "تم", onclick: () => { state.writeoff = null; load(); } }));
  } else if (w.ctx && !w.busy) {
    if (!w.ctx.eligible) {
      body.append(el("div", { class: "msg warn", text: "لا يوجد رصيد على هذا العميل لشطبه (الرصيد الحالي " + w.ctx.balance + ")." }));
    } else {
      const amount = el("input", { type: "text", inputmode: "decimal", value: w.amount, oninput: (e) => { w.amount = e.target.value; } });
      const reason = el("input", { type: "text", value: w.reason, placeholder: "مثلاً: عميل مسافر وانقطع التواصل", oninput: (e) => { w.reason = e.target.value; } });
      const password = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة مرورك الحالية" });
      const confirm = el("input", { type: "checkbox" });
      const go = el("button", { class: "btn primary", text: "تنفيذ الشطب", disabled: true });
      const sync = () => { go.disabled = !(confirm.checked && password.value && w.reason.trim() && w.amount.trim()); };
      [confirm, password, reason, amount].forEach((n) => { n.addEventListener("input", sync); n.addEventListener("change", sync); });
      go.addEventListener("click", () => { const p = password.value; password.value = ""; submitWriteOff(w, p); });
      body.append(el("div", { class: "note-box", text: "الرصيد المستحق على العميل الآن: " + w.ctx.balance + " ج. الشطب لا يحرّك أي نقدية أو بنك، ويُسجَّل في دفتر العميل وفي سجل التدقيق باسمك." }),
        el("div", { class: "filters" }, field("مبلغ الشطب (ج)", amount), field("السبب (إجباري)", reason), field("كلمة المرور", password)),
        el("label", { class: "confirm" }, confirm, " أؤكد أن هذا الرصيد غير قابل للتحصيل وأريد شطبه"), go);
    }
  }
  return el("div", { class: "overlay" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: "شطب رصيد — " + w.row.name }), el("button", { class: "btn", text: "إغلاق", onclick: () => { const done = w.done; state.writeoff = null; if (done) load(); else render(); } })), body));
}

// ---------------------------------------------------------------- an invoice in full, and the invoices of one customer (read only)
async function openInvoice(id) {
  const v = { id, loading: true, error: "", data: null };
  state.invoice = v;
  render();
  try {
    v.data = await call("/api/invoice?id=" + encodeURIComponent(id));
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    v.error = e.message;
  }
  v.loading = false;
  render();
}

function kv(label, value, strong) { return el("div", { class: "kv" }, el("span", { class: "k", text: label }), el("span", { class: "v" + (strong ? " strong" : ""), text: value || "—" })); }

function simpleTable(columns, rows) {
  return el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, columns.map((c) => el("th", { text: c })))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((cell) => el("td", { text: cell })))))));
}

function renderInvoice(v) {
  const body = el("div", {});
  if (v.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (v.error) body.append(el("div", { class: "msg err", text: v.error }));
  const d = v.data && v.data.detail;
  if (d && d.number) {
    if (d.reversedText) body.append(el("div", { class: "msg warn", text: d.reversedText }));
    if (d.legacyLabel) body.append(el("div", { class: "note-box", text: d.legacyLabel }));
    body.append(el("div", { class: "cards" },
      el("div", { class: "card" }, el("div", { class: "label", text: "إجمالي الفاتورة" }), el("div", { class: "value", text: d.invoiceTotal }), el("div", { class: "note", text: d.directionText })),
      el("div", { class: "card" }, el("div", { class: "label", text: d.movedLabel || "المدفوع فعلياً" }), el("div", { class: "value", text: d.movedAmount }), el("div", { class: "note", text: d.paymentMethods })),
      el("div", { class: "card" }, el("div", { class: "label", text: "حساب العميل" }), el("div", { class: "value", text: d.accountText }), el("div", { class: "note", text: d.settlementText }))));
    body.append(el("div", { class: "kvgrid" }, kv("رقم الفاتورة", d.number, true), kv("النوع", d.typeText), kv("التاريخ", d.finalizedAt), kv("الحالة", d.statusText),
      kv("العميل", d.customerName + (d.customerCode ? " (" + d.customerCode + ")" : "")), kv("الموبايل", d.customerMobile), kv("المنطقة", d.customerPlace),
      kv("البائع", d.salesperson), kv("حفظها", d.finalizedBy), kv("قيمة الذهب", d.goldValue), kv("متوسط المصنعية", d.averageMaking), d.showManager ? kv("إجمالي المصنعية", d.makingTotal) : null,
      d.oldGoldTotal ? kv("إجمالي الكسر", d.oldGoldTotal) : null));
    if (d.notes) body.append(el("div", { class: "note-box", text: "ملاحظات: " + d.notes }));
    const sale = v.data.saleLines || [];
    if (sale.length) {
      body.append(el("div", { class: "section", text: "الأصناف المباعة" }));
      body.append(simpleTable(["#", "الصنف", "العيار", "الوزن", "المصنع", "المقاس", "الباركود", "ملاحظات"], sale.map((l) => [l.serial, l.item, l.karat, l.weight, l.factory, l.size, l.barcode, l.notes || l.kindText])));
    }
    const old = v.data.oldGoldLines || [];
    if (old.length) {
      body.append(el("div", { class: "section", text: "كسر العميل" }));
      body.append(simpleTable(Object.keys(old[0]).filter((k) => typeof old[0][k] !== "boolean"), old.map((l) => Object.keys(old[0]).filter((k) => typeof old[0][k] !== "boolean").map((k) => String(l[k] ?? "")))));
    }
    const pays = v.data.payments || [];
    if (pays.length) {
      body.append(el("div", { class: "section", text: "المدفوعات" }));
      body.append(simpleTable(["الطريقة", "الاتجاه", "المبلغ"], pays.map((p) => [p.method, p.direction === "IN" ? "استلام من العميل" : "دفع للعميل", p.amount])));
    }
    const events = v.data.timeline || [];
    if (events.length) {
      body.append(el("div", { class: "section", text: "تاريخ الفاتورة" }));
      body.append(simpleTable(["الحدث", "الوقت", "بواسطة", "المبلغ", "المرجع"], events.map((e) => [e.text, e.time, e.actor, e.amount, e.code])));
    }
    for (const [title, list] of [["المرتجعات", v.data.returns], ["الاستبدالات", v.data.exchanges], ["التصحيحات", v.data.corrections]]) {
      if (list && list.length) {
        body.append(el("div", { class: "section", text: title }));
        list.forEach((x) => body.append(el("div", { class: "note-box", text: Object.values(x).filter((val) => typeof val === "string" && val).join(" • ") })));
      }
    }
  }
  return el("div", { class: "overlay top2" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: d && d.number ? "فاتورة " + d.number : "فاتورة" }),
      d && d.number ? el("button", { class: "btn", text: "مشاركة", onclick: () => shareText("فاتورة " + d.number, ["فاتورة " + d.number, d.typeText, d.finalizedAt, "الإجمالي: " + d.invoiceTotal, d.directionText, "حساب العميل: " + d.accountText].filter(Boolean).join("\n")) }) : null,
      el("button", { class: "btn", text: "إغلاق", onclick: () => { state.invoice = null; render(); } })), body));
}

async function openCustomerInvoices(row, page) {
  const c = { row, page: page || 1, loading: true, error: "", data: null, text: (state.custInvoices && state.custInvoices.row === row) ? state.custInvoices.text : "" };
  state.custInvoices = c;
  render();
  try {
    c.data = await call("/api/customer_invoices?customer=" + encodeURIComponent(row.id) + "&page=" + c.page + (c.text ? "&text=" + encodeURIComponent(c.text) : ""));
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    c.error = e.message;
  }
  c.loading = false;
  render();
}

function renderCustomerInvoices(c) {
  const body = el("div", {});
  const search = el("input", { type: "text", placeholder: "رقم الفاتورة", value: c.text, oninput: (e) => { c.text = e.target.value; } });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") openCustomerInvoices(c.row, 1); });
  body.append(el("div", { class: "filters" }, field("بحث برقم الفاتورة", search), el("button", { class: "btn primary", text: "بحث", onclick: () => openCustomerInvoices(c.row, 1) })));
  if (c.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (c.error) body.append(el("div", { class: "msg err", text: c.error }));
  if (c.data && !c.loading) {
    if (!c.data.rows.length) body.append(el("div", { class: "note-box", text: "لا توجد فواتير مطابقة لهذا العميل." }));
    else {
      body.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["الفاتورة", "التاريخ", "النوع", "الإجمالي", "الحالة", ""].map((t) => el("th", { text: t })))),
        el("tbody", {}, c.data.rows.map((r) => el("tr", {}, [r.number, r.date, r.kind, r.total, r.status].map((cell) => el("td", { text: cell })),
          el("td", {}, el("button", { class: "btn small", text: "فتح الفاتورة", onclick: () => openInvoice(r.id) }))))))));
      if (c.data.page > 1 || c.data.hasMore) {
        body.append(el("div", { class: "pager" },
          el("button", { class: "btn", text: "التالي", disabled: !c.data.hasMore, onclick: () => openCustomerInvoices(c.row, c.data.page + 1) }),
          el("span", { text: "صفحة " + c.data.page }),
          el("button", { class: "btn", text: "السابق", disabled: c.data.page <= 1, onclick: () => openCustomerInvoices(c.row, c.data.page - 1) })));
      }
    }
  }
  return el("div", { class: "overlay" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: "فواتير " + c.row.name }), el("button", { class: "btn", text: "إغلاق", onclick: () => { state.custInvoices = null; render(); } })), body));
}

// ---------------------------------------------------------------- today's figures on the home page (each part is left out by the server when the user may not see it)
// ---------------------------------------------------------------- what needs attention now (polled while the page is open; a notification only while the page is open)
async function loadAttention(quiet) {
  try {
    const next = await call("/api/attention");
    const before = state.attention ? state.attention.reviews : null;
    state.attention = next;
    document.title = (next.reviews ? "(" + next.reviews + ") " : "") + "تقارير مجوهرات ماس";
    if (quiet && before !== null && next.reviews > before && "Notification" in window && Notification.permission === "granted") {
      try { new Notification("مراجعة جديدة بانتظارك", { body: "عدد المراجعات المعلقة: " + next.reviews }); } catch (e) { /* not supported here */ }
    }
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    return;
  }
  if (!state.module && !state.special) render();
}

function startAttentionPolling() {
  if (state.attentionTimer) return;
  state.attentionTimer = setInterval(() => { if (state.overview) loadAttention(true); }, 60000);
}

function renderAttention() {
  const a = state.attention;
  if (!a) return null;
  const box = el("div", { class: "todaybox" });
  box.append(el("div", { class: "section", text: "الأهم دلوقتي" }));
  if (!a.items.length) box.append(el("div", { class: "note-box", text: "لا يوجد شيء يحتاج انتباهك الآن ✅" }));
  else box.append(el("div", { class: "cards" }, a.items.map((i) => el("button", { class: "card " + (i.tone || ""), onclick: () => openModule(i.module) }, el("div", { class: "label", text: i.label }), el("div", { class: "value", text: i.value })))));
  if ("Notification" in window && Notification.permission === "default") {
    box.append(el("button", { class: "btn small", text: "🔔 فعّل تنبيه المراجعات الجديدة (والصفحة مفتوحة)", onclick: async () => { try { await Notification.requestPermission(); } catch (e) { /* ignored */ } render(); } }));
  }
  return box;
}

async function loadToday() {
  try {
    state.today = await call("/api/today");
    state.todayAt = new Date().toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    state.today = null;
  }
  if (!state.module || (state.module === "inventory" && !state.special)) render();
}

function renderToday() {
  const t = state.today;
  if (!t || (!t.cards.length && !t.prices.length)) return null;
  const box = el("div", { class: "todaybox" });
  box.append(el("div", { class: "refreshrow" }, el("button", { class: "btn small", text: "↻ تحديث", onclick: () => { loadToday(); loadTiles(); } }), el("span", { class: "stamp", text: "آخر تحديث " + (state.todayAt || "") })));
  if (t.prices.length) {
    box.append(el("div", { class: "prices" }, el("span", { class: "name", text: "سعر الذهب اليوم" }),
      t.prices.map((p) => el("span", { class: "price" }, el("b", { text: "عيار " + p.karat }), el("span", { class: "buy", text: " شراء " + p.buy }), el("span", { class: "sell", text: "  بيع " + p.sell })))));
  }
  if (t.cards.length) {
    box.append(el("div", { class: "cards" }, t.cards.map((c) => el("div", { class: "card " + (c.tone || "") }, el("div", { class: "label", text: c.label }), el("div", { class: "value", text: c.value }),
      c.note ? el("div", { class: "note", text: c.note }) : null))));
  }
  return box;
}

// ---------------------------------------------------------------- a customer's statement (read only)
async function openStatement(row, page, message) {
  const v = { row, page: page || 1, loading: true, error: "", data: null, message: message || "" };
  state.stmt = v;
  render();
  try {
    v.data = await call("/api/statement?customer=" + encodeURIComponent(row.id) + "&page=" + v.page);
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    v.error = e.message;
  }
  v.loading = false;
  render();
}

function renderStatement(v) {
  const body = el("div", {});
  if (v.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (v.error) body.append(el("div", { class: "msg err", text: v.error }));
  const d = v.data;
  if (d && !v.loading) {
    body.append(el("div", { class: "cards" }, el("div", { class: "card" }, el("div", { class: "label", text: "رصيد العميل" }), el("div", { class: "value", text: d.customer.balance }),
      el("div", { class: "note", text: [d.customer.code, d.customer.mobile].filter(Boolean).join(" • ") }))));
    if (v.message) body.append(el("div", { class: "msg ok", text: v.message }));
    if (d.settle && d.settle.can) body.append(renderSettle(v, d));
    if (!d.rows.length) body.append(el("div", { class: "note-box", text: "لا توجد حركات." }));
    else {
      body.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["التاريخ", "المصدر", "المرجع", "الأثر", "الرصيد بعدها", "البيان", ""].map((t) => el("th", { text: t })))),
        el("tbody", {}, d.rows.map((r) => el("tr", {}, [r.date, r.source, r.reference, r.amount, r.balance, r.description || r.notes].map((cell) => el("td", { text: cell })),
          el("td", {}, r.invoiceId ? el("button", { class: "btn small", text: "فتح الفاتورة", onclick: () => openInvoice(r.invoiceId) }) : null)))))));
      if (d.page > 1 || d.hasMore) {
        body.append(el("div", { class: "pager" }, el("button", { class: "btn", text: "التالي", disabled: !d.hasMore, onclick: () => openStatement(v.row, d.page + 1) }), el("span", { text: "صفحة " + d.page }),
          el("button", { class: "btn", text: "السابق", disabled: d.page <= 1, onclick: () => openStatement(v.row, d.page - 1) })));
      }
    }
  }
  return el("div", { class: "overlay" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: "كشف حساب " + v.row.name }), el("button", { class: "btn", text: "إغلاق", onclick: () => { state.stmt = null; render(); } })), body));
}

function renderSettle(v, d) {
  const st = d.settle;
  const f = v.settleForm || (v.settleForm = { account: "", amount: st.amount, note: "", key: newKey(), open: false });
  if (!f.open) return el("button", { class: "btn", text: "تسوية الحساب (" + st.directionText + ")", onclick: () => { f.open = true; render(); } });
  const account = el("select", { onchange: (e) => { f.account = e.target.value; } }, el("option", { value: "", text: "نقدي (الخزنة)" }),
    st.accounts.map((a) => el("option", { value: a.id, text: a.name, selected: f.account === a.id })));
  const amount = el("input", { type: "text", inputmode: "decimal", value: f.amount, oninput: (e) => { f.amount = e.target.value; sync(); } });
  const note = el("input", { type: "text", value: f.note, oninput: (e) => { f.note = e.target.value; } });
  const password = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة مرورك الحالية" });
  const confirm = el("input", { type: "checkbox" });
  const go = el("button", { class: "btn primary", text: "تنفيذ التسوية", disabled: true });
  function sync() { go.disabled = !(confirm.checked && password.value && f.amount.trim()); }
  [confirm, password].forEach((n) => { n.addEventListener("input", sync); n.addEventListener("change", sync); });
  go.addEventListener("click", async () => {
    const pw = password.value; password.value = "";
    go.disabled = true; v.error = "";
    try {
      const r = await call("/api/settle", { method: "POST", body: JSON.stringify({ customer: v.row.id, account: f.account, amount: f.amount, note: f.note, password: pw, request_key: f.key }) });
      v.settleForm = null;
      openStatement(v.row, 1, "تمت التسوية " + r.code + " — الرصيد بعدها: " + r.balanceAfter);
      return;
    } catch (e) {
      if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
      v.error = e.message;
    }
    render();
  });
  return el("div", { class: "searchbox" }, el("div", { class: "section", text: "تسوية الحساب — " + st.directionText + " (الرصيد " + st.amountText + ")" }),
    el("div", { class: "filters" }, field("طريقة الدفع", account), field("المبلغ (ج)", amount), field("ملاحظة", note), field("كلمة المرور", password)),
    el("label", { class: "confirm" }, confirm, " أؤكد المبلغ وطريقة الدفع"), go,
    el("div", { class: "note-box", text: "التسوية حركة فلوس حقيقية تتسجل باسمك ولا تُعدَّل لاحقًا (التصحيح من البرنامج). لا يمكن أن تزيد عن الرصيد." }));
}

// ---------------------------------------------------------------- the inventory card: find a piece by barcode or item number
async function searchPiece() {
  const p = state.ps;
  p.loading = true; p.error = "";
  render();
  try {
    p.data = await call("/api/piece?text=" + encodeURIComponent(p.text));
    p.searched = p.text;
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    p.error = e.message;
  }
  p.loading = false;
  render();
}

function renderPieceSearch() {
  if (!state.ps) state.ps = { text: "", data: null, loading: false, error: "", searched: "" };
  const p = state.ps;
  const input = el("input", { type: "text", value: p.text, placeholder: "الباركود أو رقم القطعة", oninput: (e) => { p.text = e.target.value; } });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && p.text.trim()) searchPiece(); });
  const box = el("div", { class: "searchbox" }, el("div", { class: "filters" }, field("بحث عن قطعة", input),
    el("button", { class: "btn primary", text: "بحث", disabled: p.loading, onclick: () => { if (p.text.trim()) searchPiece(); } }),
    p.data ? el("button", { class: "btn", text: "مسح", onclick: () => { state.ps = null; render(); } }) : null));
  if (p.loading) box.append(el("div", { class: "loading", text: "جارٍ البحث…" }));
  if (p.error) box.append(el("div", { class: "msg err", text: p.error }));
  if (p.data && !p.loading) {
    if (!p.data.found) box.append(el("div", { class: "msg warn", text: "لا توجد قطعة بهذا الباركود أو الرقم." }));
    else {
      const x = p.data.piece;
      box.append(el("div", { class: "kvgrid" }, kv("رقم القطعة", x.number, true), kv("الباركود", x.barcode), kv("الصنف", x.itemType), kv("العيار", x.karat), kv("الوزن", x.weight), kv("المصنع", x.factory),
        kv("الفئة", x.category), kv("المقاس", x.size), kv("الحالة", x.status, true), x.invoiceNumber ? kv("بيعت في فاتورة", x.invoiceNumber) : null, x.customer ? kv("العميل", x.customer) : null,
        x.invoiceDate ? kv("تاريخ البيع", x.invoiceDate) : null));
      if (x.invoiceId) box.append(el("button", { class: "btn small", text: "فتح الفاتورة", onclick: () => openInvoice(x.invoiceId) }));
    }
  }
  return box;
}

// ---------------------------------------------------------------- cards that draw themselves: the manager reviews (read only) and the transfers
async function loadSpecial(kind) {
  const s = { kind, loading: true, error: "", data: null, message: "", form: null, text: "" };
  state.special = s;
  render();
  try {
    if (kind === "calc") { if (!state.today) await loadToday(); s.data = {}; }
    else if (kind === "stocktake") s.data = await call("/api/stocktake_status");
    else s.data = await call(kind === "reviews" ? "/api/reviews" : kind === "activity" ? "/api/activity" : kind === "bankflow" ? "/api/bankflow" : kind === "cashflow" ? "/api/cashflow" : kind === "currency" ? "/api/currency" : "/api/transfers");
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    s.error = e.message;
  }
  s.loading = false;
  render();
}

function renderCurrency(s) {
  const body = el("div", {});
  if (s.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (s.error) body.append(el("div", { class: "msg err", text: s.error }));
  const d = s.data;
  if (d) {
    if (!d.enabled) body.append(el("div", { class: "msg warn", text: "أسعار العملات متوقفة من الإعدادات." }));
    else if (!d.available) body.append(el("div", { class: "msg warn", text: d.error || "أسعار العملات غير متاحة الآن." }));
    else {
      body.append(el("div", { class: "cards" }, d.rows.map((r) => el("div", { class: "card gold" }, el("div", { class: "label", text: r.name + " (" + r.code + ")" }), el("div", { class: "value", text: r.price + " ج" }),
        el("div", { class: "note", text: "الجنيه المصري مقابل وحدة واحدة" })))));
      body.append(el("div", { class: "note-box", text: "آخر تحديث " + d.updated + (d.error ? " • " + d.error : "") + ". سعر السوق للعرض فقط، وليس سعر بنك." }));
    }
  }
  return body;
}

function renderBankflow(s) { return renderFlow(s, "البنوك والمحافظ"); }
function renderCashflow(s) { return renderFlow(s, "الخزنة"); }

function renderFlow(s, what) {
  const body = el("div", {});
  if (s.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (s.error) body.append(el("div", { class: "msg err", text: s.error }));
  const d = s.data;
  if (d) {
    body.append(el("div", { class: "cards" },
      el("div", { class: "card receivable" }, el("div", { class: "label", text: "الداخل إلى " + what + " اليوم" }), el("div", { class: "value", text: d.totalIn }), el("div", { class: "note", text: "بلا التحويلات بين الحسابات" })),
      el("div", { class: "card payable" }, el("div", { class: "label", text: "الخارج من " + what + " اليوم" }), el("div", { class: "value", text: d.totalOut }), el("div", { class: "note", text: "بلا التحويلات بين الحسابات" }))));
    body.append(simpleTable(["الحساب", "الداخل", "الخارج", "تحويل داخل", "تحويل خارج", "الرصيد الآن"], d.accounts.map((r) => [r.name, r.in, r.out, r.transferIn, r.transferOut, r.closing])));
    body.append(el("div", { class: "note-box", text: "اليوم " + d.date + ". التحويل بين حسابين لا يُحسب داخلًا ولا خارجًا." }));
  }
  return body;
}

function renderReviews(s) {
  const body = el("div", {});
  if (s.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (s.error) body.append(el("div", { class: "msg err", text: s.error }));
  if (s.data) {
    body.append(el("div", { class: "cards" }, el("div", { class: "card" + (s.data.total ? " payable" : " receivable") }, el("div", { class: "label", text: "بانتظار المراجعة" }), el("div", { class: "value", text: String(s.data.total) }),
      el("div", { class: "note", text: "«تمت المراجعة» للتسعير والتسوية والخصم؛ باقي الأنواع من البرنامج على الكمبيوتر" }))));
    const pw = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة مرورك الحالية لتأكيد المراجعة" });
    if (s.message) body.append(el("div", { class: "msg ok", text: s.message }));
    if (s.data.sections.some((x) => x.rows.some((r) => r.reviewId))) body.append(el("div", { class: "filters" }, field("كلمة المرور", pw)));
    if (!s.data.sections.length) body.append(el("div", { class: "note-box", text: "لا توجد مراجعات معلقة." }));
    for (const sec of s.data.sections) {
      body.append(el("div", { class: "section", text: sec.title + " (" + sec.count + ")" }));
      body.append(el("div", { class: "tablewrap" }, el("table", {}, el("tbody", {}, sec.rows.map((r) => el("tr", {}, el("td", { text: r.text }), el("td", { text: r.invoiceNumber }), el("td", { text: r.date }), el("td", { text: r.by }),
        el("td", {}, r.invoiceId ? el("button", { class: "btn small", text: "فتح الفاتورة", onclick: () => openInvoice(r.invoiceId) }) : null),
        el("td", {}, r.reviewId ? el("button", { class: "btn small primary", text: "تمت المراجعة", onclick: async (ev) => {
          const word = pw.value; if (!word) { s.error = "اكتب كلمة المرور أولاً."; render(); return; }
          pw.value = ""; ev.target.disabled = true; s.error = ""; s.message = "";
          try {
            await call("/api/review_done", { method: "POST", body: JSON.stringify({ review_id: r.reviewId, password: word }) });
            s.message = "تم تسجيل المراجعة.";
            s.data = await call("/api/reviews");
          } catch (e) {
            if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
            s.error = e.message;
          }
          render();
        } }) : null)))))));
    }
  }
  return body;
}

// ---------------------------------------------------------------- the camera as a stocktake scanner (owner request 2026-11-04): the codes go to the desktop's open الجرد بالباركود session
const camera = { stream: null, reader: null, timer: null, seen: new Map(), audio: null, busy: false };

function stopCamera() {
  if (camera.timer) { clearInterval(camera.timer); camera.timer = null; }
  camera.reader = null; camera.busy = false;
  if (camera.stream) { camera.stream.getTracks().forEach((t) => t.stop()); camera.stream = null; }
}

function loadZxing() {
  if (window.ZXing) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = "/zxing.min.js";
    tag.onload = () => resolve();
    tag.onerror = () => reject(new Error("تعذر تحميل قارئ الباركود."));
    document.head.append(tag);
  });
}

// Sound (owner 2026-11-05): a good scan = two rising pleasant beeps, a wrong / already counted / unknown one = three low harsh warning buzzes.
// Safari keeps the audio locked until a tap, so the context is created and resumed inside the «تشغيل الكاميرا» click (unlockAudio).
function unlockAudio() {
  try {
    camera.audio = camera.audio || new (window.AudioContext || window.webkitAudioContext)();
    if (camera.audio.state !== "running") camera.audio.resume();
    const osc = camera.audio.createOscillator(), gain = camera.audio.createGain();
    gain.gain.value = 0.001; osc.connect(gain); gain.connect(camera.audio.destination);
    osc.start(); osc.stop(camera.audio.currentTime + 0.05);
  } catch (e) { /* no sound */ }
}

function tone(freq, start, length, type, volume) {
  const ctx = camera.audio, osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.type = type; osc.frequency.value = freq;
  gain.gain.setValueAtTime(volume, ctx.currentTime + start);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + length);
  osc.connect(gain); gain.connect(ctx.destination);
  osc.start(ctx.currentTime + start); osc.stop(ctx.currentTime + start + length + 0.02);
}

function beep(ok) {
  try {
    unlockAudio();
    if (ok) { tone(988, 0, 0.1, "sine", 0.5); tone(1319, 0.11, 0.14, "sine", 0.5); }
    else { tone(220, 0, 0.18, "square", 0.45); tone(196, 0.22, 0.18, "square", 0.45); tone(165, 0.44, 0.3, "square", 0.45); }
  } catch (e) { /* no sound */ }
  if (navigator.vibrate) navigator.vibrate(ok ? 60 : [150, 80, 150, 80, 150]);
}

function renderStocktake(s) {
  if (s.view) return s.view;
  const status = el("div", { class: "msg" });
  const last = el("div", { class: "card" }, el("div", { class: "label", text: "آخر مسح" }), el("div", { class: "value", text: "—" }), el("div", { class: "note", text: "" }));
  const counts = el("div", { class: "note-box", text: "" });
  const perf = el("div", { class: "note", text: "" });                  // where the time goes: reading the picture / the round trip to the desktop
  const timing = { read: 0, trip: 0, frames: 0 };
  const showPerf = () => { perf.textContent = "زمن قراءة الصورة " + timing.read + " م.ث • زمن الإرسال للكمبيوتر " + timing.trip + " م.ث • فريمات " + timing.frames; };
  const video = el("video", { playsinline: true, muted: true, autoplay: true, class: "scanvideo" });
  video.muted = true;
  const note = (text, kind) => { status.className = "msg " + (kind || ""); status.textContent = text; };
  const show = (code, answer, quiet) => {
    const ok = answer.result === "OK";
    last.className = "card " + (ok ? "receivable" : "deadcash");
    last.children[1].textContent = code;
    last.children[2].textContent = answer.message || answer.result;
    counts.textContent = answer.scanned !== undefined ? "تم جرد " + answer.scanned + " قطعة (" + answer.weight + " جرام) • متبقي " + answer.remaining : "";
    if (!quiet) beep(ok);
  };
  async function send(code, quiet) {
    const now = Date.now();
    if (now - (camera.seen.get(code) || 0) < 2500) return null;        // the same label held in front of the lens counts once
    camera.seen.set(code, now);
    try {
      const t0 = performance.now();
      const answer = await call("/api/stocktake_scan", { method: "POST", body: JSON.stringify({ code }) });
      timing.trip = Math.round(performance.now() - t0); showPerf();
      show(code, answer, quiet);
      return answer.result === "OK";
    } catch (e) {
      if (e.status === 401) { stopCamera(); showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return null; }
      note(e.message, "err");
      return false;
    }
  }
  // ONE label per picture (owner 2026-11-05): a single read of the whole frame, no parts, no turning of the picture, no TRY_HARDER - the fastest setting.
  async function scanFrame(Z, reader, video, canvas, part) {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return [];
    const scale = Math.min(1, 800 / Math.max(w, h));
    canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
    part.drawImage(video, 0, 0, canvas.width, canvas.height);
    try {
      return [reader.decode(new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.HTMLCanvasElementLuminanceSource(canvas)))).getText()];
    } catch (e) { return []; }                                        // nothing readable in this frame
  }
  const start = el("button", { class: "btn primary", text: "تشغيل الكاميرا" });
  start.addEventListener("click", async () => {
    if (!window.isSecureContext) { note("الكاميرا تحتاج رابط HTTPS: فعّل «HTTPS» من إعدادات البرنامج على الكمبيوتر.", "err"); return; }
    start.disabled = true;
    unlockAudio();                                                       // inside the tap: Safari allows sound from now on
    try {
      await loadZxing();
      stopCamera();
      const Z = window.ZXing;
      const hints = new Map([[Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.ITF, Z.BarcodeFormat.QR_CODE]]]);
      const reader = new Z.MultiFormatReader();
      reader.setHints(hints);
      camera.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      video.srcObject = camera.stream;
      await video.play();
      const canvas = document.createElement("canvas"), part = canvas.getContext("2d", { willReadFrequently: true });
      camera.reader = reader;
      camera.timer = setInterval(async () => {
        if (camera.busy || !camera.reader) return;                        // never queue frames: a slow frame just skips the next tick
        camera.busy = true;
        try {
          const t0 = performance.now();
          const codes = await scanFrame(Z, reader, video, canvas, part);
          timing.read = Math.round(performance.now() - t0); timing.frames++; if (timing.frames % 5 === 0 || codes.length) showPerf();
          let good = 0, bad = 0;
          for (const r of await Promise.all(codes.map((code) => send(code, true)))) { if (r === true) good++; else if (r === false) bad++; }
          if (good + bad) { beep(bad === 0); }
        } finally { camera.busy = false; }
      }, 150);
      note("الكاميرا شغالة: باركود واحد في كل مرة.", "");
    } catch (e) {
      note(e && e.name === "NotAllowedError" ? "لم يُسمح باستخدام الكاميرا: اسمح بها من إعدادات سفاري." : (e && e.message) || "تعذر تشغيل الكاميرا.", "err");
    }
    start.disabled = false;
  });
  const stop = el("button", { class: "btn", text: "إيقاف الكاميرا", onclick: () => { stopCamera(); note("الكاميرا متوقفة.", ""); } });
  const manual = el("input", { type: "text", inputmode: "numeric", placeholder: "أو اكتب الباركود", autocomplete: "off" });
  const sendManual = el("button", { class: "btn", text: "إرسال", onclick: () => { const v = manual.value.trim(); if (v) { manual.value = ""; camera.seen.delete(v); send(v); } } });
  const open = s.data && s.data.open;
  note(open ? "الجرد مفتوح على الكمبيوتر: كل قطعة تمسحها تظهر هناك." : "افتح «الجرد بالباركود» على الكمبيوتر أولاً، ثم شغّل الكاميرا.", open ? "" : "warn");
  s.view = el("div", {}, status, el("div", { class: "filters" }, start, stop), video, last, counts, perf,
    el("div", { class: "filters" }, field("باركود يدوي", manual), sendManual),
    el("div", { class: "note-box", text: "الكاميرا تحتاج HTTPS. المسح لا يغيّر المخزون: هو يعدّ القطعة في جلسة الجرد المفتوحة على الكمبيوتر فقط، والاعتماد يتم من هناك." }));
  return s.view;
}

function renderCalc(s) {
  const prices = state.today ? state.today.prices : [];
  const body = el("div", {});
  if (!prices.length) { body.append(el("div", { class: "msg warn", text: "لا توجد أسعار حية الآن." })); return body; }
  const f = s.form || (s.form = { karat: prices.find((p) => p.karat === "21") ? "21" : prices[0].karat, weight: "" });
  const out = el("div", { class: "cards" });
  const draw = () => {
    const p = prices.find((x) => x.karat === f.karat);
    const w = Number(String(f.weight).replace(",", "."));
    out.replaceChildren(...(p && w > 0 ? [
      el("div", { class: "card receivable" }, el("div", { class: "label", text: "قيمة الشراء (شراء المحل)" }), el("div", { class: "value", text: (w * Number(p.buyRaw)).toLocaleString("en-US", { maximumFractionDigits: 0 }) + " ج" }),
        el("div", { class: "note", text: "سعر الجرام " + p.buy })),
      el("div", { class: "card gold" }, el("div", { class: "label", text: "قيمة البيع (بيع المحل)" }), el("div", { class: "value", text: (w * Number(p.sellRaw)).toLocaleString("en-US", { maximumFractionDigits: 0 }) + " ج" }),
        el("div", { class: "note", text: "سعر الجرام " + p.sell }))] : []));
  };
  const karat = el("select", { onchange: (e) => { f.karat = e.target.value; draw(); } }, prices.map((p) => el("option", { value: p.karat, text: "عيار " + p.karat, selected: p.karat === f.karat })));
  const weight = el("input", { type: "text", inputmode: "decimal", value: f.weight, placeholder: "الوزن بالجرام", oninput: (e) => { f.weight = e.target.value; draw(); } });
  body.append(el("div", { class: "filters" }, field("العيار", karat), field("الوزن (جرام)", weight)), out,
    el("div", { class: "note-box", text: "تقدير سريع بالأسعار الحية للجرام فقط: لا يشمل مصنعية ولا خصم كسر ولا تقريب الفاتورة، وليس سعر فاتورة." }));
  draw();
  return body;
}

async function searchActivity(s) {
  s.loading = true; s.error = "";
  render();
  try { s.data = await call("/api/activity?text=" + encodeURIComponent(s.text)); } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    s.error = e.message;
  }
  s.loading = false;
  render();
}

function renderActivity(s) {
  const body = el("div", {});
  const input = el("input", { type: "text", value: s.text, placeholder: "ابحث بالمستخدم أو الحدث أو المرجع", oninput: (e) => { s.text = e.target.value; } });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") searchActivity(s); });
  body.append(el("div", { class: "filters" }, field("بحث", input), el("button", { class: "btn primary", text: "بحث", onclick: () => searchActivity(s) })));
  if (s.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (s.error) body.append(el("div", { class: "msg err", text: s.error }));
  if (s.data) {
    if (!s.data.rows.length) body.append(el("div", { class: "note-box", text: "لا توجد حركات." }));
    else body.append(simpleTable(["الوقت", "المستخدم", "الحدث", "المرجع", "البيان"], s.data.rows.map((r) => [r.date, r.user, r.event, r.reference, r.text])));
    body.append(el("div", { class: "note-box", text: "آخر 100 حركة من سجل التدقيق. للقراءة فقط." }));
  }
  return body;
}

function renderTransfers(s) {
  const body = el("div", {});
  if (s.loading) body.append(el("div", { class: "loading", text: "جارٍ التحميل…" }));
  if (s.error) body.append(el("div", { class: "msg err", text: s.error }));
  const d = s.data;
  if (!d) return body;
  body.append(el("div", { class: "cards" }, d.accounts.map((a) => el("div", { class: "card" }, el("div", { class: "label", text: a.name }), el("div", { class: "value", text: a.balanceText }),
    el("div", { class: "note", text: a.type === "MOBILE_WALLET" ? "محفظة" : "بنك" }))),
    d.private ? el("div", { class: "card gold" }, el("div", { class: "label", text: "الحساب الخاص" }), el("div", { class: "value", text: d.private.balance }),
      el("div", { class: "note", text: "داخل " + d.private.totalIn + " • خارج " + d.private.totalOut })) : null));
  if (s.message) body.append(el("div", { class: "msg ok", text: s.message }));
  const f = s.form || (s.form = { kind: "BETWEEN", from: "", to: "", account: "", amount: "", note: "", key: newKey() });
  const kinds = [["BETWEEN", "بين حسابين"]].concat(d.canPrivate ? [["TO_PRIVATE", "إلى الحساب الخاص"], ["FROM_PRIVATE", "من الحساب الخاص"]] : []);
  body.append(el("div", { class: "section", text: "تحويل جديد" }));
  body.append(el("div", { class: "group", style: "margin-bottom:10px" }, kinds.map(([k, text]) => el("button", { class: "chip" + (f.kind === k ? " on" : ""), text, onclick: () => { f.kind = k; render(); } }))));
  const select = (key, label) => field(label, el("select", { onchange: (e) => { f[key] = e.target.value; } },
    el("option", { value: "", text: "اختر الحساب" }), d.accounts.map((a) => el("option", { value: a.key, text: a.name + " — " + a.balanceText, selected: f[key] === a.key }))));
  const amount = el("input", { type: "text", inputmode: "decimal", value: f.amount, oninput: (e) => { f.amount = e.target.value; sync(); } });
  const note = el("input", { type: "text", value: f.note, oninput: (e) => { f.note = e.target.value; } });
  const password = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة مرورك الحالية" });
  const confirm = el("input", { type: "checkbox" });
  const go = el("button", { class: "btn primary", text: "تنفيذ التحويل", disabled: true });
  function sync() { go.disabled = !(confirm.checked && password.value && f.amount.trim()); }
  [confirm, password].forEach((n) => { n.addEventListener("input", sync); n.addEventListener("change", sync); });
  const accounts = f.kind === "BETWEEN" ? [select("from", "من حساب"), select("to", "إلى حساب")] : [select("account", f.kind === "TO_PRIVATE" ? "من حساب (يخرج منه)" : "إلى حساب (يدخل فيه)")];
  body.append(el("div", { class: "filters" }, accounts, field("المبلغ (ج)", amount), field("ملاحظة", note), field("كلمة المرور", password)),
    el("label", { class: "confirm" }, confirm, " أؤكد التحويل ومراجعة الحسابين والمبلغ"), go,
    el("div", { class: "note-box", text: "التحويل حركة فلوس حقيقية: يتسجل باسمك في دفتر الحسابات. البنوك والمحافظ فقط، والخزنة غير مشمولة." }));
  go.addEventListener("click", async () => {
    const pw = password.value; password.value = "";
    go.disabled = true; s.error = ""; s.message = "";
    try {
      const r = await call("/api/transfer", { method: "POST", body: JSON.stringify({ kind: f.kind, from: f.from, to: f.to, account: f.account, amount: f.amount, note: f.note, password: pw, request_key: f.key }) });
      s.message = "تم التحويل " + (r.code || "") + (r.duplicate ? " (طلب مكرر لم يُنفَّذ مرة ثانية)" : "");
      s.form = null;
      state.special = s;
      loadSpecialKeep(s);
      return;
    } catch (e) {
      if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
      s.error = e.message;
    }
    render();
  });
  if (d.private && d.private.entries.length) {
    body.append(el("div", { class: "section", text: "آخر حركات الحساب الخاص" }));
    body.append(el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["التاريخ", "الاتجاه", "الحساب", "المبلغ", "الرصيد"].map((t) => el("th", { text: t })))),
      el("tbody", {}, d.private.entries.map((e) => el("tr", { class: e.voided ? "dim" : "" }, [e.date, e.text, e.account, e.amount, e.balance].map((cell) => el("td", { text: cell }))))))));
  }
  return body;
}

async function loadSpecialKeep(old) {
  try {
    old.data = await call("/api/transfers");
  } catch (e) { /* the old figures stay; the message above is what happened */ }
  render();
}

// ---------------------------------------------------------------- an OLD (paper ledger) balance on a customer's account: no cash, bank or wallet movement (owner only, current password)
function openLegacy(row) {
  state.legacy = { row, direction: "OWES", amount: "", date: "", note: "", key: newKey(), error: "", busy: false, done: null };
  render();
}

async function submitLegacy(w, password) {
  w.busy = true; w.error = "";
  render();
  try {
    w.done = await call("/api/legacy_balance", { method: "POST", body: JSON.stringify({ customer: w.row.id, direction: w.direction, amount: w.amount, old_date: w.date, note: w.note, password, request_key: w.key }) });
  } catch (e) {
    if (e.status === 401) { showLogin("انتهت الجلسة، سجّل الدخول من جديد."); return; }
    w.error = e.message;
  }
  password = "";
  w.busy = false;
  render();
}

function renderLegacy(w) {
  const body = el("div", {});
  if (w.busy) body.append(el("div", { class: "loading", text: "جارٍ التنفيذ…" }));
  if (w.error) body.append(el("div", { class: "msg err", text: w.error }));
  if (w.done) {
    body.append(el("div", { class: "msg ok", text: "تمت إضافة الرصيد القديم — قبل: " + w.done.balanceBefore + " • بعد: " + w.done.balanceAfter + (w.done.duplicate ? " (طلب مكرر لم يُنفَّذ مرة ثانية)" : "") }));
    body.append(el("button", { class: "btn primary", text: "تم", onclick: () => { state.legacy = null; if (state.module === "customers") { if (state.cs && state.cs.data) searchCustomers(state.cs.page); load(); } else render(); } }));
  } else if (!w.busy) {
    const amount = el("input", { type: "text", inputmode: "decimal", value: w.amount, oninput: (e) => { w.amount = e.target.value; sync(); } });
    const date = el("input", { type: "date", value: w.date, onchange: (e) => { w.date = e.target.value; } });
    const note = el("input", { type: "text", value: w.note, placeholder: "مثلاً: دفتر ورقي 2023", oninput: (e) => { w.note = e.target.value; sync(); } });
    const password = el("input", { type: "password", autocomplete: "current-password", placeholder: "كلمة مرورك الحالية" });
    const confirm = el("input", { type: "checkbox" });
    const go = el("button", { class: "btn primary", text: "تسجيل الرصيد القديم", disabled: true });
    function sync() { go.disabled = !(confirm.checked && password.value && w.amount.trim() && w.note.trim()); }
    [confirm, password].forEach((n) => { n.addEventListener("input", sync); n.addEventListener("change", sync); });
    go.addEventListener("click", () => { const p = password.value; password.value = ""; submitLegacy(w, p); });
    body.append(el("div", { class: "note-box", text: "رصيد قديم من الدفاتر الورقية يُسجَّل على حساب العميل فقط؛ لا يؤثر على الخزنة أو البنوك أو المحافظ، ويظهر في كشف الحساب باسمك." }),
      el("div", { class: "group", style: "margin-bottom:10px" }, [["OWES", "على العميل (مديونية)"], ["CREDIT", "للعميل (له عندنا)"]].map(([k, text]) =>
        el("button", { class: "chip" + (w.direction === k ? " on" : ""), text, onclick: () => { w.direction = k; render(); } }))),
      el("div", { class: "filters" }, field("المبلغ (ج)", amount), field("تاريخ الدين القديم (اختياري)", date), field("البيان (إجباري)", note), field("كلمة المرور", password)),
      el("label", { class: "confirm" }, confirm, " أؤكد أن هذا رصيد قديم صحيح وأريد تسجيله"), go);
  }
  return el("div", { class: "overlay top2" }, el("div", { class: "sheet" },
    el("div", { class: "top" }, el("h1", { text: "رصيد قديم — " + w.row.name }), el("button", { class: "btn", text: "إغلاق", onclick: () => { state.legacy = null; render(); } })), body));
}

function liveGoldChart() {
  if (!state.liveFrame) {
    state.liveFrame = el("iframe", { class: "livechart", loading: "lazy", sandbox: "allow-scripts allow-same-origin allow-popups", title: "الشارت المباشر للذهب",
      src: "https://s.tradingview.com/widgetembed/?symbol=OANDA%3AXAUUSD&interval=60&theme=dark&style=1&locale=ar&toolbarbg=0E1116&hidesidetoolbar=1&withdateranges=1" });
  }
  return el("div", {}, el("div", { class: "section", text: "الشارت المباشر للذهب (XAUUSD)" }), state.liveFrame);
}

window.addEventListener("resize", () => state.redraws.forEach((draw) => draw()));
boot();
