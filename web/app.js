"use strict";

const $ = (id) => document.getElementById(id);
const CONTROL_KEYS = ["buyer_drift", "buyer_volatility", "seller_drift", "seller_volatility", "seller_mean_reversion", "buyer_probability", "num_events", "seed", "cancellation_probability", "fee_bps", "max_trade_spread", "maker_half_spread_ticks", "candle_seconds"];
const PERCENT_KEYS = new Set(["buyer_drift", "buyer_volatility", "seller_drift", "seller_volatility", "buyer_probability", "cancellation_probability"]);
const colors = { ink: "#243d4e", grid: "#edf1eb", muted: "#a3ada5", teal: "#168a7a", coral: "#d67c68", blue: "#7498ba" };
const state = { defaults: {}, run: null, index: -1, busy: false, dirty: true, playing: false, request: 0, controller: null, timer: null, animation: null, hover: null, chart: null, lastPaint: 0, replayStart: 0, replayOrigin: 0 };
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value) => Number.isFinite(value) ? dollars.format(value) : "—";
const integer = (value) => Number.isFinite(value) ? number.format(value) : "—";
const compactMoney = (value) => `${value < 0 ? "−" : ""}$${Math.abs(value) >= 1000 ? (Math.abs(value) / 1000).toFixed(1) + "k" : Math.abs(value).toFixed(0)}`;
const elapsedLabel = (seconds) => { const safe = Math.max(0, Math.floor(seconds)); return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`; };
const isTrade = (event) => event.type === 4 || event.type === 5;

function updateControl(key) {
  const input = $(key);
  const value = Number(input.value);
  if (input.type === "range") input.style.setProperty("--range", `${(value - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100}%`);
  const output = $(`${key}_value`);
  if (!output) return;
  if (["buyer_drift", "seller_drift"].includes(key)) output.value = `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
  else if (["buyer_volatility", "seller_volatility"].includes(key)) output.value = `${value.toFixed(2)}%`;
  else if (PERCENT_KEYS.has(key)) output.value = `${Math.round(value)}%`;
  else if (key === "seller_mean_reversion") output.value = `${value.toFixed(1)} /min`;
  else if (key === "fee_bps") output.value = value === 0 ? "Zero commission" : `${value.toFixed(1)} bps (${(value / 100).toFixed(3)}%)`;
  else if (key === "max_trade_spread") {
    output.value = money(value);
    $("spread-rule-help").textContent = value === 0 ? "No executions: orders remain passive. The book can still change." : `Spread must be at or below ${money(value)}. An incoming order must also accept the opposite price.`;
  }
  else if (key === "maker_half_spread_ticks") output.value = `${value} ticks`;
}

function writeControls(config) {
  for (const key of CONTROL_KEYS) {
    if (config[key] !== undefined) $(key).value = PERCENT_KEYS.has(key) ? Number((config[key] * 100).toFixed(8)) : config[key];
    updateControl(key);
  }
}

function readControls() {
  const config = { ...state.defaults };
  for (const key of CONTROL_KEYS) config[key] = Number($(key).value) / (PERCENT_KEYS.has(key) ? 100 : 1);
  return config;
}

function setBusy(busy) {
  state.busy = busy;
  $("busy-overlay").hidden = !busy;
  $("generate").disabled = busy;
  $("generate").innerHTML = busy ? '<span class="spinner" aria-hidden="true"></span> Generating…' : '<span aria-hidden="true">↻</span> Generate market';
  $("play").disabled = busy || !state.run;
  $("reset").disabled = busy || !state.run;
  $("timeline").disabled = busy || !state.run;
  $("export").classList.toggle("disabled", busy || state.dirty || !state.run);
  $("export").setAttribute("aria-disabled", String(busy || state.dirty || !state.run));
  if (busy) $("run-status").innerHTML = '<span class="status-dot"></span>Matching orders…';
}

function scheduleGenerate() {
  clearTimeout(state.timer);
  stopReplay();
  state.dirty = true;
  state.request++;
  state.controller?.abort();
  setBusy(false);
  $("run-status").textContent = "Settings changed · regenerating…";
  state.timer = setTimeout(generate, 500);
}

async function generate(event) {
  event?.preventDefault();
  clearTimeout(state.timer);
  if (!$("controls").reportValidity()) { $("run-status").textContent = "Check the highlighted setting"; return; }
  stopReplay();
  const request = ++state.request;
  state.controller?.abort();
  state.controller = new AbortController();
  state.dirty = true;
  $("error").hidden = true;
  setBusy(true);
  try {
    const response = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readControls()), signal: state.controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The simulation could not be generated.");
    if (request !== state.request) return;
    if (!Array.isArray(result.events) || result.events.length === 0) throw new Error("The simulation returned no events. Please try another seed.");
    state.run = result;
    state.dirty = false;
    state.index = result.events.length - 1;
    state.hover = null;
    $("timeline").max = state.index;
    $("timeline").value = state.index;
    $("export").href = `/api/export?run_id=${encodeURIComponent(result.run_id)}&format=zip`;
    $("export").setAttribute("download", "");
    $("run-status").innerHTML = '<span class="status-dot"></span>Market ready';
    $("run-meta").textContent = `${integer(result.events.length)} events · Seed ${result.config.seed}`;
    $("interval-label").textContent = `${result.config.candle_seconds}s`;
    $("fee-rate").textContent = `${result.config.fee_bps.toFixed(1)} bps total`;
    $("validation-status").textContent = result.validation?.passed ? "✓ Matching engine checks passed" : "Engine validation requires review";
    $("validation-status").classList.toggle("negative", result.validation?.passed === false);
    render();
  } catch (error) {
    if (error.name === "AbortError" || request !== state.request) return;
    $("error").textContent = `${error.message} ${state.run ? "The previous run is still shown." : "Make sure the local Python server is running, then try Generate market."}`;
    $("error").hidden = false;
    $("run-status").textContent = "Generation failed";
    if (!state.run) $("empty-chart").textContent = "Waiting for a successful simulation";
  } finally {
    if (request === state.request) setBusy(false);
  }
}

function initialFrame() {
  const config = state.run.config;
  return { time: config.start_time, last_price: config.initial_price, best_bid: null, best_ask: null, pnl: 0, pnl_before_fees: 0, maker_fees_paid: 0, maker_trade_edge: 0, maker_inventory_revaluation: 0, spread_blocked_orders: 0, cash: config.initial_cash, inventory: config.initial_inventory, equity: config.initial_cash + config.initial_inventory * config.initial_price, exchange_fees: 0, volume: 0, trade_count: 0, maker_fills: 0 };
}

function currentFrame() { return state.index < 0 ? initialFrame() : state.run.events[state.index]; }
function duration() { return state.run?.config.duration_seconds || 300; }
function elapsed() { return state.run ? Math.min(duration(), Math.max(0, currentFrame().time - state.run.config.start_time)) : 0; }

function candlePrefix() {
  if (!state.run || state.index < 0) return [];
  const candles = [];
  const { start_time, candle_seconds } = state.run.config;
  for (let i = 0; i <= state.index; i++) {
    const event = state.run.events[i];
    if (!isTrade(event) || !Number.isFinite(event.price)) continue;
    const time = start_time + Math.floor(Math.max(0, event.time - start_time) / candle_seconds) * candle_seconds;
    const last = candles[candles.length - 1];
    if (last && last.time === time) {
      last.high = Math.max(last.high, event.price); last.low = Math.min(last.low, event.price); last.close = event.price; last.volume += event.size;
    } else candles.push({ time, open: event.price, high: event.price, low: event.price, close: event.price, volume: event.size });
  }
  return candles;
}

function initialBook() {
  const bids = new Map(), asks = new Map();
  for (const order of state.run.initial_orders || []) {
    const side = order.side === 1 ? bids : asks;
    side.set(order.price, (side.get(order.price) || 0) + order.size);
  }
  const bidList = [...bids].sort((a, b) => b[0] - a[0]), askList = [...asks].sort((a, b) => a[0] - b[0]);
  return Array.from({ length: 10 }, (_, i) => [askList[i]?.[0] || 0, askList[i]?.[1] || 0, bidList[i]?.[0] || 0, bidList[i]?.[1] || 0]).flat();
}

function visibleBook() {
  if (state.index < 0) return initialBook();
  return state.run.book_frames?.[state.index] || state.run.orderbook?.[state.index] || (state.index === state.run.events.length - 1 ? state.run.final_book : null);
}

function updateDepth() {
  const book = visibleBook();
  let maxSize = 1, bidTotal = 0, askTotal = 0;
  for (let i = 0; i < 10; i++) maxSize = Math.max(maxSize, book?.[4 * i + 1] || 0, book?.[4 * i + 3] || 0);
  for (let i = 0; i < 10; i++) {
    const askPrice = book?.[4 * i] / 10000, askSize = book?.[4 * i + 1] || 0, bidPrice = book?.[4 * i + 2] / 10000, bidSize = book?.[4 * i + 3] || 0;
    const bidValid = Number.isFinite(bidPrice) && bidPrice > 0 && bidPrice < 1e7 && bidSize > 0;
    const askValid = Number.isFinite(askPrice) && askPrice > 0 && askPrice < 1e7 && askSize > 0;
    $(`bid-price-${i}`).textContent = bidValid ? bidPrice.toFixed(2) : "—";
    $(`ask-price-${i}`).textContent = askValid ? askPrice.toFixed(2) : "—";
    $(`bid-size-${i}`).textContent = bidValid ? integer(bidSize) : "—";
    $(`ask-size-${i}`).textContent = askValid ? integer(askSize) : "—";
    $(`bid-cell-${i}`).style.setProperty("--depth", `${bidValid ? bidSize / maxSize * 100 : 0}%`);
    $(`ask-cell-${i}`).style.setProperty("--depth", `${askValid ? askSize / maxSize * 100 : 0}%`);
    bidTotal += bidValid ? bidSize : 0; askTotal += askValid ? askSize : 0;
  }
  $("bid-depth").textContent = integer(bidTotal);
  $("ask-depth").textContent = integer(askTotal);
}

function render() {
  if (!state.run) return;
  const current = currentFrame(), config = state.run.config, book = visibleBook();
  const last = Number.isFinite(current.last_price) ? current.last_price : config.initial_price;
  const difference = last - config.initial_price;
  $("last-price").textContent = money(last);
  $("price-change").textContent = `${difference >= 0 ? "+" : "−"}${money(Math.abs(difference))} (${difference >= 0 ? "+" : ""}${(difference / config.initial_price * 100).toFixed(2)}%)`;
  $("price-change").className = `price-change ${difference >= 0 ? "positive" : "negative"}`;
  const bid = current.best_bid ?? (book?.[2] ? book[2] / 10000 : null), ask = current.best_ask ?? (book?.[0] ? book[0] / 10000 : null);
  $("best-bid").textContent = money(bid); $("best-ask").textContent = money(ask);
  $("spread").textContent = `Spread ${bid != null && ask != null ? money(ask - bid) : "—"}`;
  $("maker-pnl").textContent = `${current.pnl > 0 ? "+" : ""}${money(current.pnl)}`;
  $("maker-pnl").className = `accounting-value ${current.pnl >= 0 ? "positive" : "negative"}`;
  $("maker-gross-pnl").textContent = money(current.pnl_before_fees);
  $("maker-fees-paid").textContent = money(current.maker_fees_paid);
  $("maker-trade-edge").textContent = money(current.maker_trade_edge);
  $("maker-inventory-revaluation").textContent = money(current.maker_inventory_revaluation);
  $("maker-cash").textContent = money(current.cash); $("maker-inventory").textContent = `${integer(current.inventory)} shares`;
  $("maker-equity").textContent = money(current.equity); $("exchange-fees").textContent = money(current.exchange_fees);
  $("trade-count").textContent = integer(current.trade_count); $("trade-volume").textContent = integer(current.volume);
  $("trade-summary").textContent = `${integer(current.trade_count)} trades · ${integer(current.volume)} shares`;
  const spreadUnits = book ? book[0] - book[2] : Math.round((ask - bid) * 10000);
  const allowed = spreadUnits > 0 && spreadUnits <= Math.round(config.max_trade_spread * 10000);
  $("spread-gate-status").textContent = `Current spread ${money(spreadUnits / 10000)} / limit ${money(config.max_trade_spread)} · ${allowed ? "Within threshold; price must also cross" : "Too wide to execute"}`;
  $("blocked-orders").textContent = `Orders made passive by threshold: ${integer(current.spread_blocked_orders)}`;
  $("replay-time").innerHTML = `${elapsedLabel(elapsed())} <span>/ ${elapsedLabel(duration())}</span>`;
  $("timeline").value = state.index;
  $("timeline").style.setProperty("--range", `${(state.index + 1) / state.run.events.length * 100}%`);
  $("replay-state").textContent = state.playing ? "Replaying generated history" : state.index === state.run.events.length - 1 ? "Full run preview · Play to replay" : state.index < 0 ? "Initial book · Play to begin" : `Replay paused · event ${integer(state.index + 1)}`;
  updateDepth();
  drawPrice();
  drawSmallChart("pnl-chart", "pnl", colors.teal, [{key:"pnl_before_fees", color:colors.blue, dash:[5,3]}, {key:"maker_trade_edge", color:"#b08436", dash:[2,3]}]);
  drawSmallChart("fees-chart", "exchange_fees", colors.blue);
}

function canvasContext(id) {
  const canvas = $(id), rect = canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
  if (!rect.width || !rect.height) return null;
  const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.font = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  return { ctx, width: rect.width, height: rect.height };
}

function drawPrice() {
  const canvas = canvasContext("price-chart");
  if (!canvas || !state.run) return;
  const { ctx, width, height } = canvas;
  const chart = { left: 13, top: 20, right: width - 61, bottom: height - 28 };
  const config = state.run.config, candles = candlePrefix();
  let low = Infinity, high = -Infinity;
  for (const candle of candles) { low = Math.min(low, candle.low); high = Math.max(high, candle.high); }
  const firstBook = initialBook();
  if (state.index < 0) { low = (firstBook[2] / 10000) || config.initial_price - .05; high = (firstBook[0] / 10000) || config.initial_price + .05; }
  for (let i = 0; i <= state.index; i++) {
    const event = state.run.events[i];
    if (Number.isFinite(event.best_bid)) low = Math.min(low, event.best_bid);
    if (Number.isFinite(event.best_ask)) high = Math.max(high, event.best_ask);
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) { low = config.initial_price - .1; high = config.initial_price + .1; }
  const priceSpan = Math.max(.08, high - low), pad = priceSpan * .16;
  low -= pad; high += pad;
  const x = (time) => chart.left + Math.max(0, Math.min(duration(), time - config.start_time)) / duration() * (chart.right - chart.left);
  const y = (price) => chart.bottom - (price - low) / (high - low) * (chart.bottom - chart.top);
  ctx.strokeStyle = colors.grid; ctx.lineWidth = 1; ctx.fillStyle = colors.muted;
  for (let i = 0; i <= 4; i++) {
    const price = low + (high - low) * i / 4, py = y(price);
    ctx.beginPath(); ctx.moveTo(chart.left, py); ctx.lineTo(chart.right, py); ctx.stroke();
    ctx.textAlign = "left"; ctx.fillText(price.toFixed(2), chart.right + 9, py + 3);
  }
  const timeSteps = width < 430 ? 3 : 5;
  for (let i = 0; i <= timeSteps; i++) {
    const second = duration() * i / timeSteps, px = x(config.start_time + second);
    ctx.textAlign = i === 0 ? "left" : i === timeSteps ? "right" : "center";
    ctx.fillText(elapsedLabel(second), px, height - 8);
  }
  ctx.save(); ctx.beginPath(); ctx.rect(chart.left, chart.top - 1, chart.right - chart.left, chart.bottom - chart.top + 2); ctx.clip();
  for (const [key, color] of [["best_bid", colors.teal], ["best_ask", colors.coral]]) {
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.globalAlpha = .35; ctx.beginPath();
    let started = false;
    const step = Math.max(1, Math.floor((state.index + 1) / 1200));
    for (let i = 0; i <= state.index; i += step) {
      const event = state.run.events[i]; if (!Number.isFinite(event[key])) continue;
      if (!started) { ctx.moveTo(x(event.time), y(event[key])); started = true; } else ctx.lineTo(x(event.time), y(event[key]));
    }
    if (state.index >= 0) { const last = currentFrame(); if (Number.isFinite(last[key])) ctx.lineTo(x(last.time), y(last[key])); }
    ctx.stroke(); ctx.globalAlpha = 1;
  }
  const candleWidth = Math.max(1.5, Math.min(11, (chart.right - chart.left) * config.candle_seconds / duration() * .65));
  for (const candle of candles) {
    const px = x(candle.time + config.candle_seconds * .5), rising = candle.close >= candle.open;
    ctx.strokeStyle = rising ? colors.teal : colors.coral; ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(px, y(candle.high)); ctx.lineTo(px, y(candle.low)); ctx.stroke();
    ctx.fillRect(px - candleWidth / 2, Math.min(y(candle.open), y(candle.close)), candleWidth, Math.max(1.5, Math.abs(y(candle.open) - y(candle.close))));
  }
  ctx.restore();
  const lastPrice = currentFrame().last_price;
  if (Number.isFinite(lastPrice) && candles.length) {
    const lastY = y(lastPrice); ctx.strokeStyle = "#b8c7bd"; ctx.setLineDash([3, 4]); ctx.lineWidth = .7;
    ctx.beginPath(); ctx.moveTo(chart.left, lastY); ctx.lineTo(chart.right, lastY); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = colors.ink; ctx.fillRect(chart.right + 3, lastY - 8, 51, 16); ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.fillText(lastPrice.toFixed(2), chart.right + 28.5, lastY + 3);
  }
  state.chart = { ...chart, candles, x, y, candleWidth };
  $("empty-chart").hidden = candles.length > 0;
  $("empty-chart").textContent = state.index < 0 ? "Initial book · replay to see executed trades" : "No trades at this point in the replay";
  if (state.hover !== null && candles.length) drawHover(ctx);
}

function drawHover(ctx) {
  const { candles, x, y, top, bottom, left, right } = state.chart;
  let nearest = null, distance = Infinity;
  for (const candle of candles) {
    const gap = Math.abs(x(candle.time + state.run.config.candle_seconds * .5) - state.hover);
    if (gap < distance) { distance = gap; nearest = candle; }
  }
  if (!nearest || state.hover < left || state.hover > right) { $("chart-tooltip").hidden = true; return; }
  const px = x(nearest.time + state.run.config.candle_seconds * .5);
  ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = "#8b9c91"; ctx.lineWidth = .7;
  ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bottom); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = colors.ink; ctx.beginPath(); ctx.arc(px, y(nearest.close), 2.5, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  const tip = $("chart-tooltip"); tip.hidden = false;
  tip.innerHTML = `<b>${elapsedLabel(nearest.time - state.run.config.start_time)} · ${state.run.config.candle_seconds}s candle</b><span>O</span>${money(nearest.open)} <span>H</span>${money(nearest.high)}<br><span>L</span>${money(nearest.low)} <span>C</span>${money(nearest.close)}<br><span>Vol</span>${integer(nearest.volume)} shares`;
  tip.style.left = `${Math.max(8, Math.min(right - 160, px + 12))}px`; tip.style.top = "12px";
}

function drawSmallChart(id, key, color, comparisons = []) {
  const canvas = canvasContext(id);
  if (!canvas || !state.run) return;
  const { ctx, width, height } = canvas, left = 1, right = width - 47, top = 8, bottom = height - 18;
  const series = [...comparisons.slice().reverse(), {key, color, dash:[]}];
  let min = 0, max = 0;
  for (let i = 0; i <= state.index; i++) for (const line of series) {
    const value = state.run.events[i][line.key];
    if (Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value); }
  }
  const margin = Math.max(.5, (max - min) * .12); min -= margin; max += margin;
  const x = (time) => left + Math.min(duration(), Math.max(0, time - state.run.config.start_time)) / duration() * (right - left);
  const y = (value) => bottom - (value - min) / (max - min) * (bottom - top);
  ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'; ctx.lineWidth = 1; ctx.strokeStyle = colors.grid;
  for (const py of [top, bottom]) { ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke(); }
  ctx.strokeStyle = "#d9e3d9"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(left, y(0)); ctx.lineTo(right, y(0)); ctx.stroke(); ctx.setLineDash([]);
  const step = Math.max(1, Math.floor((state.index + 1) / 600));
  for (const line of series) {
    const points = [[left, y(0)]];
    for (let i = 0; i <= state.index; i += step) {
      const event = state.run.events[i];
      if (Number.isFinite(event[line.key])) points.push([x(event.time), y(event[line.key])]);
    }
    if (state.index >= 0) { const last = currentFrame(); points.push([x(last.time), y(last[line.key])]); }
    ctx.beginPath(); ctx.moveTo(...points[0]);
    for (let i = 1; i < points.length; i++) ctx.lineTo(...points[i]);
    ctx.strokeStyle = line.color; ctx.lineWidth = 1.7; ctx.setLineDash(line.dash); ctx.stroke(); ctx.setLineDash([]);
    const end = points[points.length - 1];
    if (line.key === key) {
      ctx.lineTo(end[0], y(0)); ctx.lineTo(left, y(0)); ctx.closePath();
      const gradient = ctx.createLinearGradient(0, top, 0, bottom);
      gradient.addColorStop(0, color + "12"); gradient.addColorStop(1, color + "02"); ctx.fillStyle = gradient; ctx.fill();
    }
    if (points.length > 1) { ctx.fillStyle = line.color; ctx.beginPath(); ctx.arc(end[0], end[1], 2.2, 0, Math.PI * 2); ctx.fill(); }
  }
  ctx.fillStyle = colors.muted; ctx.textAlign = "left"; ctx.fillText(compactMoney(max - margin), right + 8, top + 3); ctx.fillText(compactMoney(min + margin), right + 8, bottom + 2);
  ctx.fillText("00:00", left, height - 1); ctx.textAlign = "center"; ctx.fillText(elapsedLabel(duration() / 2), (left + right) / 2, height - 1); ctx.textAlign = "right"; ctx.fillText(elapsedLabel(duration()), right, height - 1);
}

function stopReplay() {
  state.playing = false;
  if (state.animation) cancelAnimationFrame(state.animation);
  state.animation = null;
  $("play").innerHTML = '<span aria-hidden="true">▶</span>';
  $("play").setAttribute("aria-label", "Play simulation replay");
}

function toggleReplay() {
  if (!state.run || state.busy) return;
  if (state.playing) { stopReplay(); render(); return; }
  if (state.index >= state.run.events.length - 1) state.index = -1;
  state.playing = true;
  state.replayOrigin = elapsed(); state.replayStart = performance.now(); state.lastPaint = 0;
  $("play").innerHTML = '<span aria-hidden="true">Ⅱ</span>';
  $("play").setAttribute("aria-label", "Pause simulation replay");
  state.hover = null; $("chart-tooltip").hidden = true;
  function tick(now) {
    if (!state.playing) return;
    const seconds = state.replayOrigin + (now - state.replayStart) / 1000 * Number($("replay-speed").value);
    const target = state.run.config.start_time + seconds;
    while (state.index + 1 < state.run.events.length && state.run.events[state.index + 1].time <= target) state.index++;
    if (state.index >= state.run.events.length - 1 || seconds >= duration()) { state.index = state.run.events.length - 1; stopReplay(); render(); return; }
    if (now - state.lastPaint > 65) { render(); state.lastPaint = now; }
    state.animation = requestAnimationFrame(tick);
  }
  render(); state.animation = requestAnimationFrame(tick);
}

function setup() {
  $("depth-rows").innerHTML = Array.from({ length: 10 }, (_, i) => `<div class="depth-row"><div id="bid-cell-${i}" class="depth-cell bid"><span id="bid-price-${i}" class="buy-text">—</span><span id="bid-size-${i}" class="quantity">—</span></div><div id="ask-cell-${i}" class="depth-cell ask"><span id="ask-price-${i}" class="sell-text">—</span><span id="ask-size-${i}" class="quantity">—</span></div></div>`).join("");
  $("controls").addEventListener("submit", generate);
  for (const key of CONTROL_KEYS) {
    $(key).addEventListener($(key).tagName === "SELECT" ? "change" : "input", () => {
      updateControl(key);
      document.querySelectorAll(".preset").forEach((button) => button.classList.remove("active"));
      scheduleGenerate();
    });
    updateControl(key);
  }
  document.querySelectorAll(".preset").forEach((button) => button.addEventListener("click", () => {
    const base = { buyer_drift: 0, seller_drift: 0, buyer_volatility: .006, seller_volatility: .006, seller_mean_reversion: 1.5, buyer_probability: .5 };
    const settings = button.dataset.preset === "bullish" ? { ...base, buyer_drift: .005, seller_drift: .003, seller_mean_reversion: .7, buyer_probability: .6 } : button.dataset.preset === "volatile" ? { ...base, buyer_volatility: .018, seller_volatility: .018, seller_mean_reversion: .5 } : base;
    writeControls(settings);
    document.querySelectorAll(".preset").forEach((preset) => preset.classList.toggle("active", preset === button));
    generate();
  }));
  $("play").addEventListener("click", toggleReplay);
  $("reset").addEventListener("click", () => { stopReplay(); state.index = -1; state.hover = null; $("chart-tooltip").hidden = true; render(); });
  $("timeline").addEventListener("input", () => { stopReplay(); state.index = Number($("timeline").value); state.hover = null; $("chart-tooltip").hidden = true; render(); });
  $("replay-speed").addEventListener("change", () => { if (state.playing) { state.replayOrigin = elapsed(); state.replayStart = performance.now(); } });
  $("price-chart").addEventListener("pointermove", (event) => { if (state.playing || state.busy || !state.run) return; state.hover = event.clientX - $("price-chart").getBoundingClientRect().left; drawPrice(); });
  $("price-chart").addEventListener("pointerleave", () => { state.hover = null; $("chart-tooltip").hidden = true; drawPrice(); });
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 100); });
  $("export").addEventListener("click", (event) => { if (state.busy || state.dirty || !state.run) event.preventDefault(); });
}

async function initialize() {
  setup();
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("Could not load the simulation settings.");
    const settings = await response.json(); state.defaults = settings.defaults; writeControls(state.defaults); await generate();
  } catch (error) {
    $("error").textContent = `${error.message} Start the Python server and reload this page.`;
    $("error").hidden = false; $("run-status").textContent = "Local server unavailable";
    $("empty-chart").textContent = "Connect to the local Python server to begin";
  }
}

initialize();
