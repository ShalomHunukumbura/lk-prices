// Sri Lanka food prices: the overview page. Reads the same derived JSON as the
// full-data page (site/data), and draws everything with plain SVG.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const NS = "http://www.w3.org/2000/svg";
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const UNITS = { "Coconut (Avg.)": "nut", "Egg (White)": "egg", "Apple (Imp)": "fruit", "Orange (Imp)": "fruit", "Coconut oil": "litre" };
const SECTION_ICON = { vegetables: "carrot", rice: "grains", fish: "fish", fruits: "orange-slice", other: "basket" };
const ITEM_ICON = { "Egg (White)": "egg", "Coconut (Avg.)": "drop", "Coconut oil": "drop" };
const SECTION_NAME = { vegetables: "Vegetables", rice: "Rice", fish: "Fish", fruits: "Fruit", other: "Staples" };
// Plain names for CBSL's labels.
const NICE = { "Coconut (Avg.)": "Coconut", "Egg (White)": "Egg", "Sugar (White)": "Sugar", "Snake gourd": "Snake gourd" };
const DEFAULT_BASKET = { "Samba": 5, "Red Dhal": 1, "Coconut (Avg.)": 8, "Sugar (White)": 1, "Egg (White)": 10, "Big Onion (Imp)": 1 };
// What the hero chart marks. Both dates are well documented.
const NOTES = [
  { month: "2020-03", text: "COVID curfew" },
  { month: "2022-03", text: "Rupee floated" },
];

const D = {};
const state = { basket: new Map(Object.entries(DEFAULT_BASKET)), year: 2016, tmYear: 2016, tmFilter: "all", tmAll: false,
                spotRange: "all", spotCur: "LKR" };

// ---------- helpers ----------
const el = (tag, attrs = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
};
const h = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "style") node.setAttribute("style", v); else if (k in node) node[k] = v; else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
};
const icon = (name, extra = "") => {
  const url = `url(assets/icons/${name}.svg)`;
  return h("span", { className: `ico ${extra}`, style: `-webkit-mask-image:${url};mask-image:${url}` });
};
const nice = (item) => NICE[item] || item;
const unit = (item) => UNITS[item] || "kg";
const rs = (v) => v == null ? "n/a" : `Rs ${Math.round(v).toLocaleString("en-US")}`;
const usd = (v) => v == null ? "n/a" : `$${v.toFixed(2)}`;
const monthName = (ym) => `${MONTHS[+ym.slice(5) - 1]} ${ym.slice(0, 4)}`;
const longDate = (iso) => `${+iso.slice(8)} ${LONG_MONTHS[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}`;
const pct = (a, b) => (a / b - 1) * 100;
const signed = (p) => `${p >= 0 ? "+" : ""}${Math.round(p)}%`;
const itemIcon = (item, section) => ITEM_ICON[item] || SECTION_ICON[section] || "basket";

function changePill(p) {
  const up = p >= 0;
  return h("span", { className: `pill ${up ? "up" : "down"}`, title: up ? "Price went up" : "Price went down" },
    icon(up ? "arrow-up" : "arrow-down"), `${Math.abs(Math.round(p))}%`);
}

// Count a number up to its value when it scrolls into view.
function tween(node, to, format, ms = 1100) {
  if (REDUCE) { node.textContent = format(to); return; }
  const start = performance.now(), from = node._v ?? 0;
  node._v = to;
  const step = (t) => {
    const k = Math.min(1, (t - start) / ms), e = 1 - Math.pow(1 - k, 4);
    node.textContent = format(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function whenSeen(node, fn) {
  if (!("IntersectionObserver" in window)) return fn();
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { io.disconnect(); fn(); }
  }, { threshold: 0.35 });
  io.observe(node);
}

// ---------- series ----------
// One price series per item: Pettah where it reports the item (Negombo for
// fish), so the level doesn't jump when markets join or leave.
function preferredMarket(item, type = "retail") {
  const entry = D.items.items[item];
  const order = entry.section === "fish" ? ["Negombo", "Peliyagoda", "Narahenpita"] : ["Pettah", "Narahenpita", "Dambulla"];
  const count = (m) => (entry.markets[m]?.[type] || []).filter((v) => v != null).length;
  const best = order.find((m) => count(m) > 24);
  return best || Object.keys(entry.markets).sort((a, b) => count(b) - count(a))[0];
}
function series(item, type = "retail", market = preferredMarket(item, type)) {
  return D.items.items[item].markets[market]?.[type] || [];
}
function yearAverage(values, year) {
  const y = String(year), vals = values.filter((v, i) => v != null && D.items.months[i].startsWith(y));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}
// The average over the last twelve months: a fairer "now" than one month for
// prices that swing with the seasons (tomatoes).
function recentAverage(values) {
  const vals = values.slice(-12).filter((v) => v != null);
  return vals.length >= 6 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}
// The latest price, from the last three months.
function latest(values) {
  for (let i = values.length - 1; i >= Math.max(0, values.length - 3); i--) if (values[i] != null) return values[i];
  return null;
}
const priced = (item) => latest(series(item)) != null;

// ---------- line chart ----------
function lineChart(root, { labels, lines, format, notes = [], height = 300, area = false, animate = false, onRead, yZero = true, compact = false }) {
  root.replaceChildren();
  const W = Math.max(280, Math.round(root.clientWidth || 600)), H = compact ? 120 : (W < 520 ? Math.min(height, 230) : height);
  const M = compact ? { top: 6, right: 6, bottom: 6, left: 6 } : { top: 18, right: 14, bottom: 26, left: 52 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": lines.map((l) => l.name).join(" and ") });
  // A sparkline fills its box; the stroke keeps its width when stretched.
  if (compact) svg.setAttribute("preserveAspectRatio", "none");
  const tip = h("div", { className: "tip" });
  root.append(svg, tip);

  let min = Infinity, max = -Infinity;
  for (const l of lines) for (const v of l.values) if (v != null) { min = Math.min(min, v); max = Math.max(max, v); }
  if (!isFinite(min)) { svg.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "tick" }, "No prices for this choice")); return; }
  const lo = yZero ? 0 : min - (max - min) * 0.1, hi = max + (max - lo) * 0.08;
  const x = (i) => M.left + (labels.length < 2 ? 0.5 : i / (labels.length - 1)) * (W - M.left - M.right);
  const y = (v) => H - M.bottom - ((v - lo) / (hi - lo || 1)) * (H - M.top - M.bottom);

  if (!compact) {
    const raw = (hi - lo) / 4, pow = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw);
    for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
      svg.append(el("line", { x1: M.left, x2: W - M.right, y1: y(t), y2: y(t), stroke: "var(--grid)" }));
      svg.append(el("text", { x: M.left - 10, y: y(t) + 4, "text-anchor": "end", class: "tick" }, format(t)));
    }
    const years = labels.map((l, i) => [l, i]).filter(([l]) => l.length === 4 || l.endsWith("-01"));
    const every = Math.ceil(years.length / (W < 520 ? 4 : 8));
    years.filter((_, k) => k % every === 0).forEach(([l, i]) =>
      svg.append(el("text", { x: x(i), y: H - 6, "text-anchor": "middle", class: "tick" }, l.slice(0, 4))));
    // Notes too close to sit side by side are stacked.
    let lastX = -Infinity, row = 0;
    for (const n of notes) {
      const i = labels.indexOf(n.month);
      if (i < 0) continue;
      row = x(i) - lastX < 96 ? row + 1 : 0;
      lastX = x(i);
      svg.append(el("line", { x1: x(i), x2: x(i), y1: M.top, y2: H - M.bottom, class: "note-line" }));
      svg.append(el("text", { x: x(i) + 6, y: M.top + 10 + row * 14, class: "note-text" }, n.text));
    }
  }

  const defs = el("defs");
  svg.append(defs);
  lines.forEach((l, k) => {
    let d = "", pen = false, first = null, last = null;
    l.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true; first ??= i; last = i;
    });
    if (area && k === 0 && first != null) {
      const id = `g${Math.random().toString(36).slice(2, 8)}`;
      const grad = el("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 });
      grad.append(el("stop", { offset: "0%", "stop-color": l.color, "stop-opacity": 0.22 }),
                  el("stop", { offset: "100%", "stop-color": l.color, "stop-opacity": 0 }));
      defs.append(grad);
      svg.append(el("path", { d: `${d}L${x(last).toFixed(1)},${y(lo)}L${x(first).toFixed(1)},${y(lo)}Z`, fill: `url(#${id})`, class: animate ? "fade-area" : "" }));
    }
    const path = el("path", { d, fill: "none", stroke: l.color, "stroke-width": compact ? 2 : 2.25, "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" });
    svg.append(path);
    if (animate && !REDUCE) { const len = path.getTotalLength(); path.style.setProperty("--len", len); path.classList.add("draw"); }
    if (k === 0 && last != null && !compact) {
      svg.append(el("circle", { cx: x(last), cy: y(l.values[last]), r: 5, fill: l.color, class: "pulse" }));
      svg.append(el("circle", { cx: x(last), cy: y(l.values[last]), r: 4.5, fill: l.color, stroke: "var(--panel)", "stroke-width": 2 }));
    }
  });

  if (compact) return;
  const cursor = el("g");
  svg.append(cursor);
  const show = (i) => {
    cursor.replaceChildren();
    if (i == null) { tip.classList.remove("on"); onRead?.(null); return; }
    cursor.append(el("line", { x1: x(i), x2: x(i), y1: M.top, y2: H - M.bottom, stroke: "var(--ink-3)" }));
    const parts = [];
    for (const l of lines) {
      const v = l.values[i];
      if (v == null) continue;
      cursor.append(el("circle", { cx: x(i), cy: y(v), r: 5, fill: l.color, stroke: "var(--panel)", "stroke-width": 2 }));
      parts.push(`${lines.length > 1 ? l.name + " " : ""}${format(v)}`);
    }
    if (onRead) { onRead(i); return; }
    const top = Math.min(...lines.map((l) => l.values[i]).filter((v) => v != null).map(y));
    tip.textContent = `${labels[i].length === 4 ? labels[i] : monthName(labels[i])}: ${parts.join(", ")}`;
    const r = root.getBoundingClientRect();
    tip.style.left = `${Math.min(r.width - 90, Math.max(90, (x(i) / W) * r.width))}px`;
    tip.style.top = `${(top / H) * r.height}px`;
    tip.classList.add("on");
  };
  const at = (clientX) => {
    const r = svg.getBoundingClientRect(), px = ((clientX - r.left) / r.width) * W;
    return Math.max(0, Math.min(labels.length - 1, Math.round(((px - M.left) / (W - M.left - M.right)) * (labels.length - 1))));
  };
  svg.addEventListener("pointermove", (e) => show(at(e.clientX)));
  svg.addEventListener("pointerleave", () => show(null));
  svg.tabIndex = 0;
  let k = labels.length - 1;
  svg.addEventListener("keydown", (e) => {
    const d = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    if (!d) return;
    e.preventDefault(); k = Math.max(0, Math.min(labels.length - 1, k + d)); show(k);
  });
  svg.addEventListener("blur", () => show(null));
}

// ---------- hero ----------
function drawHero() {
  const { months, series: s } = D.index, retail = s.all.retail;
  const last = retail.length - 1, x = retail[last] / 100;
  tween($("#hero-x"), x, (v) => `${v.toFixed(1)}×`, 1600);
  const defaultRead = () => {
    $("#hero-read").textContent = retail[last].toFixed(0);
    $("#hero-read-sub").textContent = `${monthName(months[last])}, latest`;
  };
  defaultRead();
  lineChart($("#hero-chart"), {
    labels: months, lines: [{ name: "Retail", color: "var(--accent)", values: retail }],
    format: (v) => v.toFixed(0), notes: NOTES, area: true, animate: true,
    onRead: (i) => {
      if (i == null) return defaultRead();
      $("#hero-read").textContent = retail[i].toFixed(0);
      $("#hero-read-sub").textContent = `${monthName(months[i])}: ${(retail[i] / 100).toFixed(2)}× August 2016`;
    },
  });
}

// ---------- highlights ----------
function drawStory() {
  const { months, series: s } = D.index, retail = s.all.retail;
  // 2022 against 2021, year average against year average: steadier than the
  // single steepest month, which would pick an extreme.
  const avg = (y) => { const v = retail.filter((_, i) => months[i].startsWith(y)); return v.reduce((a, b) => a + b, 0) / v.length; };
  const rise = pct(avg("2022"), avg("2021"));
  whenSeen($("#crisis-stat"), () => tween($("#crisis-stat"), rise, (v) => `+${Math.round(v)}%`));
  $("#crisis-text").textContent = `On average, food cost ${Math.round(rise)}% more in 2022 than in 2021, the sharpest rise in ten years.`;
  const from = months.indexOf("2021-01"), to = Math.min(months.length, months.indexOf("2023-12") + 1);
  lineChart($("#crisis-chart"), {
    labels: months.slice(from, to), compact: true, yZero: false, area: true,
    lines: [{ name: "Retail", color: "var(--accent)", values: retail.slice(from, to) }], format: (v) => v.toFixed(0),
  });

  const coco = series("Coconut (Avg.)"), cocoThen = yearAverage(coco, 2016), cocoNow = latest(coco);
  whenSeen($("#coco-now"), () => { tween($("#coco-then"), cocoThen, rs); tween($("#coco-now"), cocoNow, rs); });
  $("#coco-text").textContent = `A coconut at Pettah costs ${(cocoNow / cocoThen).toFixed(1)} times what it did in 2016.`;

  const rice = series("Samba"), riceThen = yearAverage(rice, 2016), riceNow = latest(rice);
  const kgThen = 1000 / riceThen, kgNow = 1000 / riceNow, cells = Math.ceil(kgThen);
  const grains = (kg) => Array.from({ length: cells }, (_, i) => icon("grains", i < Math.round(kg) ? "" : "off"));
  $("#rice-then-l").textContent = `In 2016 it bought ${kgThen.toFixed(1)} kg`;
  $("#rice-now-l").textContent = `Today it buys ${kgNow.toFixed(1)} kg`;
  $("#rice-then").replaceChildren(...grains(kgThen));
  $("#rice-now").replaceChildren(...grains(kgNow));

  const pettah = D.margins.markets.Pettah, years = D.margins.years, lastY = pettah.length - 1;
  whenSeen($("#markup-stat"), () => tween($("#markup-stat"), pettah[lastY], (v) => `${Math.round(v)}%`));
  $("#markup-text").textContent = `Pettah's shops charge about ${Math.round(pettah[lastY])}% more than the wholesale price for the same item, on the same day.`;
  // Wholesale against retail for one item, as two plain bars.
  const item = "Samba", w = latest(series(item, "wholesale", "Pettah")), r = latest(series(item, "retail", "Pettah"));
  const bars = $("#markup-bars");
  bars.replaceChildren(
    h("div", { className: "bar" }, h("span", {}, "Wholesale"), h("b", { style: "width:0" }), h("span", { className: "num" }, rs(w))),
    h("div", { className: "bar" }, h("span", {}, "In the shop"), h("b", { className: "retail", style: "width:0" }), h("span", { className: "num" }, rs(r))),
    h("span", { className: "muted", style: "font-size:13px" }, `Samba rice at Pettah, per kg, ${monthName(D.items.months.at(-1))}`));
  whenSeen(bars, () => {
    const [bw, br] = bars.querySelectorAll("b");
    bw.style.width = `${(w / r) * 100}%`; br.style.width = "100%";
  });
  $("#margin-big").textContent = `${Math.round(pettah[lastY])}%`;
  const y22 = years.indexOf(2022), y21 = years.indexOf(2021);
  if (y22 > 0 && y21 >= 0)
    $("#margin-text").textContent = `In the 2022 crisis it fell from ${Math.round(pettah[y21])}% to ${Math.round(pettah[y22])}%: retail prices rose less than wholesale ones.`;
}

// ---------- basket ----------
function drawPicker() {
  const q = $("#search").value.trim().toLowerCase();
  const groups = {};
  for (const [item, entry] of Object.entries(D.items.items)) {
    if (!priced(item) || (q && !nice(item).toLowerCase().includes(q))) continue;
    (groups[entry.section] ||= []).push(item);
  }
  const order = ["rice", "other", "vegetables", "fish", "fruits"].filter((s) => groups[s]);
  if (!order.length) { $("#groups").replaceChildren(h("p", { className: "empty" }, `No item matches "${q}".`)); return; }
  $("#groups").replaceChildren(...order.map((sec) => h("div", { className: "group" },
    h("h3", {}, icon(SECTION_ICON[sec]), SECTION_NAME[sec]),
    h("div", { className: "chips" }, ...groups[sec].sort((a, b) => nice(a).localeCompare(nice(b))).map((item) => {
      const qty = state.basket.get(item);
      const chip = h("button", { className: `chip${qty ? " in" : ""}`, type: "button", title: `Add ${nice(item)}` },
        nice(item), qty ? h("span", { className: "count" }, `×${qty}`) : icon("plus"));
      chip.addEventListener("click", () => { state.basket.set(item, (state.basket.get(item) || 0) + 1); drawPicker(); drawReceipt(); });
      return chip;
    })))));
}

function drawReceipt() {
  const year = state.year, lines = [];
  let then = 0, now = 0, missing = 0;
  for (const [item, qty] of state.basket) {
    const s = series(item), p0 = yearAverage(s, year), p1 = latest(s);
    if (p1 == null) continue;
    if (p0 == null) missing++; else then += p0 * qty;
    now += p1 * qty;
    lines.push({ item, qty, p0, p1 });
  }
  $("#total-then-l").textContent = `In ${year}`;
  if (!lines.length) {
    $("#lines").replaceChildren(h("p", { className: "empty" }, "Your basket is empty. Tap an item to add it."));
  } else {
    const head = h("div", { className: "line line-head" }, h("span", {}, "Item"), h("span", {}, "How much"), h("span", { style: "text-align:right" }, String(year)), h("span", { style: "text-align:right" }, "Now"));
    $("#lines").replaceChildren(h("div", { className: "lines" }, head, ...lines.map(({ item, qty, p0, p1 }) => {
      const minus = h("button", { type: "button", "aria-label": `One less ${nice(item)}` }, icon(qty > 1 ? "minus" : "x"));
      const plus = h("button", { type: "button", "aria-label": `One more ${nice(item)}` }, icon("plus"));
      minus.addEventListener("click", () => { qty > 1 ? state.basket.set(item, qty - 1) : state.basket.delete(item); drawPicker(); drawReceipt(); });
      plus.addEventListener("click", () => { state.basket.set(item, qty + 1); drawPicker(); drawReceipt(); });
      return h("div", { className: "line" },
        h("div", { className: "name" }, icon(itemIcon(item, D.items.items[item].section)), h("span", {}, nice(item))),
        h("div", { className: "stepper" }, minus, h("span", { className: "q" }, `${qty} ${unit(item)}`), plus),
        h("span", { className: "then num", "data-year": String(year) }, p0 == null ? "n/a" : rs(p0 * qty)),
        h("span", { className: "now num" }, rs(p1 * qty)));
    })));
  }
  tween($("#total-then"), then, rs, 700);
  tween($("#total-now"), now, rs, 700);
  const verdict = $("#verdict");
  if (!lines.length || then === 0) { verdict.textContent = ""; return; }
  const comparable = lines.filter((l) => l.p0 != null).reduce((a, l) => a + l.p1 * l.qty, 0);
  verdict.replaceChildren("The same shopping costs ", h("strong", {}, `${(comparable / then).toFixed(1)} times`),
    ` what it did in ${year}.`, missing ? ` ${missing} item${missing > 1 ? "s weren't" : " wasn't"} priced in ${year}, so ${missing > 1 ? "they're" : "it's"} left out of the comparison.` : "");
}

// ---------- time machine ----------
function drawTimeMachine() {
  const year = state.tmYear;
  $("#tm-year").textContent = year;
  const cards = [];
  for (const [item, entry] of Object.entries(D.items.items)) {
    if (state.tmFilter !== "all" && entry.section !== state.tmFilter) continue;
    const s = series(item), then = yearAverage(s, year), now = recentAverage(s);
    if (then == null || now == null) continue;
    cards.push({ item, entry, s, then, now, change: pct(now, then) });
  }
  cards.sort((a, b) => b.change - a.change);
  const limit = innerWidth < 640 ? 6 : 12;
  const shown = state.tmAll ? cards : cards.slice(0, limit);
  $("#tm-more").hidden = state.tmAll || cards.length <= limit;
  const months = D.items.months, yi = months.findIndex((m) => m.startsWith(String(year)));
  $("#tm-grid").replaceChildren(...shown.map(({ item, entry, s, then, now, change }) => {
    const card = h("article", { className: "card item-card" },
      h("div", { className: "top" }, h("div", { className: "nm" }, icon(itemIcon(item, entry.section)), h("span", { title: nice(item) }, nice(item))), changePill(change)),
      h("div", { className: "prices" }, h("span", { className: "now num" }, rs(now)), h("span", { className: "then num" }, `from ${rs(then)}`)),
      h("span", { className: "muted", style: "font-size:13px" }, `per ${unit(item)}, ${year} vs last 12 months`));
    const spark = h("div", { className: "chart" });
    card.insertBefore(spark, card.children[2]);
    requestAnimationFrame(() => {
      lineChart(spark, { labels: months, compact: true, yZero: false, lines: [{ name: nice(item), color: change >= 0 ? "var(--up)" : "var(--down)", values: s }], format: rs });
      const svg = spark.querySelector("svg");
      if (svg && yi >= 0) {
        const W = +svg.getAttribute("viewBox").split(" ")[2], x = 6 + (yi / (months.length - 1)) * (W - 12);
        svg.prepend(el("rect", { x, y: 0, width: Math.max(2, (12 / (months.length - 1)) * (W - 12)), height: 120, fill: "var(--accent-soft)" }));
      }
    });
    return card;
  }));
}

// ---------- today ----------
function drawToday() {
  const m = D.movers;
  $("#today-title").textContent = `The market on ${longDate(m.date)}.`;
  $("#today-sub").textContent = `Retail prices against the report before, on ${longDate(m.previous)}. The biggest moves first.`;
  const item = (r) => h("span", { className: "tick-item" }, h("strong", {}, nice(r.item)), h("span", { className: "mk" }, r.market), h("span", { className: "num" }, rs(r.price)), changePill(r.change));
  const all = [...m.up, ...m.down].sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
  // Twice over, so the band loops without a gap.
  $("#ticker").replaceChildren(...all.map(item), ...(REDUCE ? [] : all.map(item)));
  const list = (rows) => rows.slice(0, 5).map((r) => h("div", { className: "move" },
    h("div", {}, h("strong", {}, nice(r.item)), h("span", { className: "mk" }, `${r.market}, per ${unit(r.item)}`)),
    h("span", { className: "num" }, rs(r.price)), changePill(r.change)));
  $("#moves-up").replaceChildren(...list(m.up));
  $("#moves-down").replaceChildren(...list(m.down));
}

// ---------- margins ----------
function drawMargins() {
  const { years, markets } = D.margins;
  const colors = { Pettah: "var(--accent)", Dambulla: "var(--wholesale)", Negombo: "var(--down)" };
  lineChart($("#margin-chart"), {
    labels: years.map(String), height: 280,
    lines: Object.keys(colors).filter((k) => markets[k]).map((k) => ({ name: k, color: colors[k], values: markets[k] })),
    format: (v) => `${Math.round(v)}%`,
  });
  const legend = h("div", { className: "legend", style: "margin-bottom:8px" },
    ...Object.entries(colors).map(([k, c]) => h("span", {}, h("i", { style: `background:${c}` }), k)));
  $("#margin-chart").prepend(legend);
}

// ---------- spotlight ----------
function fillSpotMarkets() {
  const item = $("#spot-item").value, markets = Object.keys(D.items.items[item].markets).sort();
  const sel = $("#spot-market"), keep = sel.value;
  sel.replaceChildren(...markets.map((m) => h("option", { value: m }, m)));
  sel.value = markets.includes(keep) ? keep : preferredMarket(item);
}
function drawSpot() {
  const item = $("#spot-item").value, market = $("#spot-market").value;
  const m = D.items.items[item].markets[market] || {};
  let labels = D.items.months, retail = m.retail || [], wholesale = m.wholesale || [];
  const rate = state.spotCur === "USD" && D.usd ? D.usd.lkr_per_usd : null;
  const conv = (vals) => vals.map((v, i) => v == null ? null : rate ? (rate[i] ? v / rate[i] : null) : v);
  retail = conv(retail); wholesale = conv(wholesale);
  if (state.spotRange !== "all") {
    const n = +state.spotRange;
    labels = labels.slice(-n); retail = retail.slice(-n); wholesale = wholesale.slice(-n);
  }
  lineChart($("#spot-chart"), {
    labels, height: 320, yZero: false,
    lines: [{ name: "Retail", color: "var(--accent)", values: retail }, { name: "Wholesale", color: "var(--wholesale)", values: wholesale }],
    format: rate ? usd : rs,
  });
}

// ---------- wiring ----------
function segmented(root, key, attr, after) {
  for (const b of root.querySelectorAll("button")) b.addEventListener("click", () => {
    state[key] = b.dataset[attr];
    for (const o of root.querySelectorAll("button")) o.setAttribute("aria-pressed", String(o === b));
    after();
  });
}

function revealOnScroll() {
  const nodes = document.querySelectorAll(".reveal");
  if (REDUCE || !("IntersectionObserver" in window)) { nodes.forEach((n) => n.classList.add("seen")); return; }
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("seen"); io.unobserve(e.target); }
  }), { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
  nodes.forEach((n) => io.observe(n));
}

async function start() {
  revealOnScroll();
  try {
    const get = (name) => fetch(`data/${name}.json`).then((r) => { if (!r.ok) throw new Error(name); return r.json(); });
    [D.index, D.items, D.margins, D.movers, D.meta] = await Promise.all(["index", "items", "margins", "movers", "meta"].map(get));
    D.usd = await get("usd").catch(() => null);
  } catch {
    document.body.classList.add("failed");
    return;
  }
  const lastYear = +D.items.months.at(-1).slice(0, 4);
  const firstYear = +D.items.months[0].slice(0, 4);
  $("#basket-year").replaceChildren(...Array.from({ length: lastYear - firstYear }, (_, i) => h("option", { value: firstYear + i }, String(firstYear + i))));
  $("#basket-year").value = String(state.year);
  $("#basket-year").addEventListener("change", (e) => { state.year = +e.target.value; drawReceipt(); });
  $("#search").addEventListener("input", drawPicker);
  const range = $("#tm-range");
  range.min = firstYear; range.max = lastYear - 1;
  range.addEventListener("input", () => { state.tmYear = +range.value; drawTimeMachine(); });
  segmented($("#tm-filter"), "tmFilter", "f", () => { state.tmAll = false; drawTimeMachine(); });
  $("#tm-more").addEventListener("click", () => { state.tmAll = true; drawTimeMachine(); });

  const spotItems = Object.keys(D.items.items).filter(priced).sort((a, b) => nice(a).localeCompare(nice(b)));
  $("#spot-item").replaceChildren(...spotItems.map((i) => h("option", { value: i }, nice(i))));
  $("#spot-item").value = "Samba";
  fillSpotMarkets();
  $("#spot-item").addEventListener("change", () => { fillSpotMarkets(); drawSpot(); });
  $("#spot-market").addEventListener("change", drawSpot);
  segmented($("#spot-range"), "spotRange", "r", drawSpot);
  if (D.usd) { $("#spot-cur").hidden = false; segmented($("#spot-cur"), "spotCur", "c", drawSpot); }

  $("#built").textContent = `${D.meta.reports.toLocaleString("en-US")} reports from ${longDate(D.meta.first_report)} to ${longDate(D.meta.latest_report)}. Updated every day.`;

  drawHero(); drawStory(); drawPicker(); drawReceipt(); drawTimeMachine(); drawToday(); drawMargins(); drawSpot();

  let width = innerWidth, timer;
  addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (innerWidth === width) return;
      width = innerWidth;
      drawHero(); drawStory(); drawTimeMachine(); drawMargins(); drawSpot();
    }, 200);
  });
}
start();
