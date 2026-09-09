/* Gamma Quant — deterministic research calculations. No order execution. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.GQ = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VERSION = '1.1.0';
  const DAY = 86400000, YEAR = 365 * DAY;
  const SPECS = Object.freeze({ ES: { point: 50, tick: .25, micro: 'MES' }, MES: { point: 5, tick: .25 }, NQ: { point: 20, tick: .25, micro: 'MNQ' }, MNQ: { point: 2, tick: .25 } });
  const NAMES = { SPY: 'S&P 500 ETF', QQQ: 'Nasdaq-100 ETF', SPX: 'S&P 500 index', ES: 'E-mini S&P 500', NQ: 'E-mini Nasdaq-100', NVDA: 'NVIDIA', TSLA: 'Tesla', MU: 'Micron', GOOGL: 'Alphabet', PLTR: 'Palantir', V: 'Visa', MCD: 'McDonald’s', AMZN: 'Amazon', EOG: 'EOG Resources', KMI: 'Kinder Morgan' };
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const round = (x, n = 2) => Math.round((x + Number.EPSILON) * 10 ** n) / 10 ** n;
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const sum = xs => xs.reduce((a, b) => a + b, 0);
  const mean = xs => xs.length ? sum(xs) / xs.length : 0;
  const last = xs => xs[xs.length - 1];
  function assert(ok, message) { if (!ok) throw new Error(message); }
  function normalPDF(x) { return Math.exp(-.5 * x * x) / Math.sqrt(2 * Math.PI); }
  function normalCDF(x) {
    const a = Math.abs(x), t = 1 / (1 + .2316419 * a);
    const p = 1 - normalPDF(a) * t * (.319381530 + t * (-.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
    return x >= 0 ? p : 1 - p;
  }
  function bs(spot, strike, years, iv, rate = .04, dividend = 0, type = 'call') {
    assert(type === 'call' || type === 'put', 'Option type must be call or put.');
    assert([spot, strike, years, iv, rate, dividend].every(finite), 'Non-finite Black–Scholes input.');
    assert(spot > 0 && strike > 0 && years > 0 && iv > 0, 'Black–Scholes requires positive spot, strike, remaining time and IV.');
    const d1 = (Math.log(spot / strike) + (rate - dividend + .5 * iv * iv) * years) / (iv * Math.sqrt(years));
    const d2 = d1 - iv * Math.sqrt(years), dq = Math.exp(-dividend * years), dr = Math.exp(-rate * years);
    const gamma = dq * normalPDF(d1) / (spot * iv * Math.sqrt(years));
    const delta = type === 'call' ? dq * normalCDF(d1) : dq * (normalCDF(d1) - 1);
    const value = type === 'call' ? spot * dq * normalCDF(d1) - strike * dr * normalCDF(d2) : strike * dr * normalCDF(-d2) - spot * dq * normalCDF(-d1);
    return { value: Math.max(0, value), delta, gamma, vega: spot * dq * normalPDF(d1) * Math.sqrt(years) / 100 };
  }
  function impliedVol(price, spot, strike, years, rate = .04, dividend = 0, type = 'call') {
    assert([price, spot, strike, years, rate, dividend].every(finite), 'Non-finite implied-volatility input.');
    assert(price >= 0 && spot > 0 && strike > 0 && years > 0, 'Implied volatility requires nonnegative price and positive spot, strike and remaining time.');
    const dq = Math.exp(-dividend * years), dr = Math.exp(-rate * years);
    const lower = type === 'call' ? Math.max(0, spot * dq - strike * dr) : Math.max(0, strike * dr - spot * dq);
    const upper = type === 'call' ? spot * dq : strike * dr;
    assert(price >= lower - 1e-8 && price <= upper + 1e-8, 'Option price violates discounted no-arbitrage bounds.');
    let lo = 1e-4, hi = 5, mid = null, value = null;
    if (price <= lower + 1e-8) return { iv: null, converged: false, reason: 'Price is at intrinsic bound; IV is not numerically identifiable.' };
    for (let i = 1; i <= 100; i++) {
      mid = (lo + hi) / 2; value = bs(spot, strike, years, mid, rate, dividend, type).value;
      if (Math.abs(value - price) <= Math.max(1e-8, price * 1e-7)) return { iv: mid, converged: true, iterations: i, modelValue: value };
      if (value > price) hi = mid; else lo = mid;
    }
    return { iv: null, converged: false, reason: 'IV solver did not converge within 100 bounded iterations.' };
  }
  const etFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short' });
  function eastern(timestamp) {
    const p = Object.fromEntries(etFormat.formatToParts(new Date(timestamp)).map(x => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, minutes: Number(p.hour) * 60 + Number(p.minute), weekday: p.weekday };
  }
  function validateSnapshot(input) {
    assert(input && typeof input === 'object' && !Array.isArray(input), 'Snapshot must be a JSON object.');
    assert(typeof input.symbol === 'string' && /^[A-Z0-9:.\-]{1,20}$/.test(input.symbol), 'Invalid instrument symbol.');
    assert(finite(input.spot) && input.spot > 0, 'Spot must be a positive number.');
    assert(typeof input.asOf === 'string' && /T/.test(input.asOf) && /(?:Z|[+-]\d{2}:\d{2})$/.test(input.asOf) && finite(Date.parse(input.asOf)), 'A valid ISO asOf timestamp is required.');
    assert(Array.isArray(input.bars) && input.bars.length >= 60 && input.bars.length <= 100000, 'Provide 60–100,000 OHLCV bars.');
    let prev = -Infinity;
    const bars = input.bars.map((b, i) => {
      assert(b && [b.t, b.o, b.h, b.l, b.c, b.v].every(finite), `Bar ${i}: t/o/h/l/c/v must be numeric.`);
      assert(b.t > prev && b.t <= Date.parse(input.asOf), `Bar ${i}: timestamps must increase and not exceed asOf.`);
      assert(b.o > 0 && b.c > 0 && b.l > 0 && b.h >= Math.max(b.o, b.c) && b.l <= Math.min(b.o, b.c) && b.v >= 0, `Bar ${i}: invalid OHLC or volume.`);
      prev = b.t;
      if (b.session != null) assert(typeof b.session === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(b.session) && finite(Date.parse(b.session)), `Bar ${i}: session must be YYYY-MM-DD.`);
      return { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v, session: b.session || eastern(b.t).date };
    });
    assert(!input.options || (Array.isArray(input.options) && input.options.length <= 15000), 'options must be an array with at most 15,000 rows.');
    const ids = new Set();
    const options = (input.options || []).map((o, i) => {
      assert(o && (o.type === 'call' || o.type === 'put'), `Option ${i}: invalid type.`);
      assert(finite(o.strike) && o.strike > 0, `Option ${i}: invalid strike.`);
      assert(typeof o.expiry === 'string' && finite(Date.parse(o.expiry)) && /T/.test(o.expiry) && /(?:Z|[+-]\d{2}:\d{2})$/.test(o.expiry), `Option ${i}: expiry needs an exact ISO time, not just a date.`);
      assert(o.oi == null || (finite(o.oi) && Number.isInteger(o.oi) && o.oi >= 0), `Option ${i}: open interest must be a nonnegative integer or null when unknown.`);
      assert(finite(o.multiplier) && o.multiplier > 0, `Option ${i}: positive contract multiplier required.`);
      for (const k of ['gamma', 'iv', 'bid', 'ask', 'volume']) if (o[k] != null) assert(finite(o[k]) && o[k] >= 0, `Option ${i}: invalid ${k}.`);
      if (o.delta != null) assert(finite(o.delta) && o.delta >= -1 && o.delta <= 1, `Option ${i}: delta must be between -1 and 1.`);
      if (o.position != null) assert(finite(o.position) && Number.isInteger(o.position), `Option ${i}: signed position must be an integer.`);
      if (o.bid != null && o.ask != null) assert(o.ask >= o.bid, `Option ${i}: crossed bid/ask.`);
      if (o.quoteAsOf != null) assert(typeof o.quoteAsOf === 'string' && /T/.test(o.quoteAsOf) && /(?:Z|[+-]\d{2}:\d{2})$/.test(o.quoteAsOf) && finite(Date.parse(o.quoteAsOf)) && Date.parse(o.quoteAsOf) <= Date.parse(input.asOf) + 5000, `Option ${i}: invalid quote timestamp.`);
      const key = `${o.type}|${o.strike}|${o.expiry}|${o.multiplier}`;
      assert(!ids.has(key), `Duplicate option at row ${i}.`); ids.add(key);
      return { type: o.type, strike: o.strike, expiry: o.expiry, oi: o.oi ?? null, multiplier: o.multiplier, gamma: o.gamma ?? null, iv: o.iv ?? null, delta: o.delta ?? null, bid: o.bid ?? null, ask: o.ask ?? null, volume: o.volume ?? 0, quoteAsOf: o.quoteAsOf ?? null, greekAsOf:o.greekAsOf??null,ivAsOf:o.ivAsOf??null,greekSource:o.greekSource||'MISSING',ivSource:o.ivSource||'MISSING',oiAsOf:o.oiAsOf||input.oiAsOf||'UNKNOWN',expiryVerified: o.expiryVerified === true, lifecycleVerified:o.lifecycleVerified===true,lifecycleIndependentlyVerified:o.lifecycleIndependentlyVerified===true,settlementType:o.settlementType||'UNKNOWN',lastTradingAt:o.lastTradingAt||null,settlementAt:o.settlementAt||null,payoffFixingAt:o.payoffFixingAt||o.settlementAt||null,modelExpiryAt:o.modelExpiryAt||o.payoffFixingAt||o.settlementAt||null,settlementValuePublishedAt:o.settlementValuePublishedAt||null,lifecycleSource:o.lifecycleSource||'USER_DECLARED',lifecycleRuleVersion:o.lifecycleRuleVersion||'UNVERSIONED',root:o.root||'',underlying:o.underlying||input.symbol,position: o.position ?? null, id: String(o.id || key).slice(0, 120) };
    });
    const minutes = Number(input.barMinutes || 5);
    if (input.spotAsOf != null) assert(typeof input.spotAsOf === 'string' && finite(Date.parse(input.spotAsOf)) && Date.parse(input.spotAsOf) <= Date.parse(input.asOf) + 5000, 'Invalid spotAsOf timestamp.');
    assert([1, 5, 15, 60, 240, 1440, 10080].includes(minutes), 'barMinutes must be 1, 5, 15, 60, 240, 1440 or 10080.');
    return { symbol: input.symbol, name: NAMES[input.symbol] || input.symbol, spot: input.spot, asOf: input.asOf, spotAsOf: input.spotAsOf || new Date(Math.min(Date.parse(input.asOf), last(bars).t + minutes * 60000)).toISOString(), bars, options, barMinutes: minutes, mode: 'imported', source: String(input.source || 'User-supplied snapshot').slice(0, 180), oiAsOf: String(input.oiAsOf || 'Unspecified'), chainComplete: input.chainComplete === true, rate: finite(input.rate) ? input.rate : .04, dividend: finite(input.dividend) ? input.dividend : 0, warnings: Array.isArray(input.warnings) ? input.warnings.slice(0, 20).map(x => String(x).slice(0, 300)) : [], contract: typeof input.contract === 'string' ? input.contract.slice(0, 24) : null, expiryConvention: String(input.expiryConvention || 'Supplied exact timestamps; not externally verified').slice(0, 180) };
  }
  function ema(values, period) {
    if (!values.length) return [];
    const k = 2 / (period + 1); let v = values[0];
    return values.map(x => (v = x * k + v * (1 - k)));
  }
  function rsi(values, period = 14) {
    if (values.length <= period) return null;
    const ds = values.slice(1).map((x, i) => x - values[i]);
    let gain = mean(ds.slice(0, period).map(x => Math.max(x, 0))), loss = mean(ds.slice(0, period).map(x => Math.max(-x, 0)));
    for (const d of ds.slice(period)) { gain = (gain * (period - 1) + Math.max(d, 0)) / period; loss = (loss * (period - 1) + Math.max(-d, 0)) / period; }
    return gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  function atr(bars, period = 14) {
    const trs = bars.map((b, i) => Math.max(b.h - b.l, i ? Math.abs(b.h - bars[i - 1].c) : 0, i ? Math.abs(b.l - bars[i - 1].c) : 0));
    if (trs.length < period) return null;
    let v = mean(trs.slice(0, period)); for (const tr of trs.slice(period)) v = (v * (period - 1) + tr) / period;
    return v;
  }
  function aggregate(bars, targetMinutes, baseMinutes = 5) {
    if (targetMinutes === baseMinutes) return bars;
    assert(targetMinutes > baseMinutes && targetMinutes % baseMinutes === 0, 'Cannot reconstruct finer bars from coarser data.');
    const out = []; let current = null, sessionStart = null, lastSession = null;
    for (const b of bars) {
      const session = b.session || eastern(b.t).date;
      if (session !== lastSession) { sessionStart = b.t; lastSession = session; }
      const bucket = targetMinutes === 1440 ? 0 : Math.floor((b.t - sessionStart) / (targetMinutes * 60000));
      const key = session + ':' + bucket;
      if (!current || current.key !== key) { current = { key, t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v, session }; out.push(current); }
      else { current.h = Math.max(current.h, b.h); current.l = Math.min(current.l, b.l); current.c = b.c; current.v += b.v; }
    }
    return out.map(({ key, ...b }) => b);
  }
  function easternSessionEnd(date, futures = false) {
    const hour = futures ? 17 : 16;
    const guess = Date.parse(`${date}T${hour + 5}:00:00Z`);
    return guess - (eastern(guess).minutes - hour * 60) * 60000;
  }
  function closedBars(snapshot, targetMinutes) {
    const base = snapshot.barMinutes || 5, at = Date.parse(snapshot.asOf);
    if (targetMinutes < base || targetMinutes % base) return [];
    const bars = snapshot.bars.filter(b => base > 1440 ? b.t + base * 60000 <= at : base === 1440 ? easternSessionEnd(b.session || eastern(b.t).date, !!SPECS[snapshot.symbol]) <= at : b.t + base * 60000 <= at);
    let out = aggregate(bars, targetMinutes, base);
    if (targetMinutes > base && out.length) {
      const current = last(out), end = easternSessionEnd(current.session || eastern(current.t).date, !!SPECS[snapshot.symbol]);
      const bucketEnd = targetMinutes === 1440 ? end : Math.min(end, current.t + targetMinutes * 60000);
      if (bucketEnd > at) out = out.slice(0, -1);
    }
    return out;
  }
  function indicators(bars, barMinutes = 5) {
    if (bars.length < 50) return { insufficient: true };
    const c = bars.map(b => b.c), e20 = ema(c, 20), e50 = ema(c, 50), a = atr(bars), current = last(bars), s = current.session || eastern(current.t).date;
    let pv = 0, vol = 0, previousSession = null;
    const vwap = bars.map(b => { const session = b.session || eastern(b.t).date; if (session !== previousSession) { pv = 0; vol = 0; previousSession = session; } pv += (b.h + b.l + b.c) / 3 * b.v; vol += b.v; return vol ? pv / vol : null; });
    const prevSessions = [...new Set(bars.map(b => b.session || eastern(b.t).date))].filter(x => x !== s);
    const prev = bars.filter(b => (b.session || eastern(b.t).date) === last(prevSessions));
    const today = bars.filter(b => (b.session || eastern(b.t).date) === s);
    const opening = today.filter(b => eastern(b.t).minutes >= 570 && eastern(b.t).minutes < 585);
    const rv = mean(bars.slice(-21, -1).map(b => b.v));
    const changes = c.slice(-21).slice(1).map((x, i) => Math.log(x / c.slice(-21)[i]));
    const variance = changes.length > 1 ? sum(changes.map(x => (x - mean(changes)) ** 2)) / (changes.length - 1) : 0;
    const vwapLast = barMinutes >= 1440 ? null : last(vwap), price = last(c), r = rsi(c), trendLong = price > last(e20) && last(e20) > last(e50), trendShort = price < last(e20) && last(e20) < last(e50);
    const slope = (last(e20) - e20[e20.length - 6]) / (a || 1);
    const direction = trendLong && slope > .05 ? 'bullish' : trendShort && slope < -.05 ? 'bearish' : 'neutral';
    const recent = bars.slice(-21, -1);
    const periodsPerYear=barMinutes>=10080?52:barMinutes>=1440?252:252*390/barMinutes;
    return { insufficient: false, price, ema20: last(e20), ema50: last(e50), ema20Series: e20, ema50Series: e50, vwapSeries: barMinutes >= 1440 ? vwap.map(() => null) : vwap, vwap: vwapLast, rsi: r, atr: a, rvol: rv ? current.v / rv : null, direction, slope, pdh: prev.length ? Math.max(...prev.map(b => b.h)) : null, pdl: prev.length ? Math.min(...prev.map(b => b.l)) : null, orh: barMinutes <= 15 && opening.length ? Math.max(...opening.map(b => b.h)) : null, orl: barMinutes <= 15 && opening.length ? Math.min(...opening.map(b => b.l)) : null, support: Math.min(...recent.map(b => b.l)), resistance: Math.max(...recent.map(b => b.h)), volAnnual: Math.sqrt(variance * periodsPerYear) * 100, change: prev.length ? (price / last(prev).c - 1) * 100 : 0 };
  }
  function factorScore(i) {
    if (i.insufficient) return { score: 0, factors: [], direction: 'neutral', insufficient: true };
    const bullish = i.direction === 'bullish', bearish = i.direction === 'bearish';
    const f = [
      { label: 'Trend structure', max: 35, value: bullish || bearish ? 35 : 10, detail: 'Close / EMA20 / EMA50 alignment' },
      { label: 'Trend slope', max: 20, value: Math.round(clamp(Math.abs(i.slope) * 16, 0, 20)), detail: 'EMA20 change over 5 bars, scaled by ATR' },
      { label: 'Momentum', max: 20, value: bullish ? (i.rsi >= 50 && i.rsi <= 72 ? 20 : 8) : bearish ? (i.rsi >= 28 && i.rsi <= 50 ? 20 : 8) : 5, detail: 'RSI14 alignment; extreme values penalized' },
      { label: 'Volume participation', max: 15, value: i.rvol == null ? 0 : Math.round(clamp(i.rvol * 10, 0, 15)), detail: 'Last bar volume / prior 20-bar mean' },
      { label: 'VWAP alignment', max: 10, value: i.vwap == null ? 0 : ((bullish && i.price >= i.vwap) || (bearish && i.price <= i.vwap) ? 10 : 0), detail: 'Bar-typical-price session VWAP; not tick-exact' }
    ];
    return { score: sum(f.map(x => x.value)), factors: f, direction: i.direction, insufficient: false };
  }
  function gammaExposure(snapshot, { maxDays = 30, convention = 'call-put' } = {}) {
    assert(['call-put', 'all-long', 'all-short', 'signed'].includes(convention), 'Unknown positioning convention.');
    const now = Date.parse(snapshot.asOf), byStrike = new Map(), rows = [], unqualifiedRows=[];
    let excluded = 0, missingGamma = 0, missingIV = 0, missingOI=0, unverifiedExpiry = 0, lifecycleExcluded=0;
    const signFor = o => convention === 'call-put' ? (o.type === 'call' ? 1 : -1) : convention === 'all-long' ? 1 : convention === 'all-short' ? -1 : null;
    for (const o of snapshot.options) {
      const modelAt = o.modelExpiryAt || o.payoffFixingAt || o.settlementAt || o.expiry;
      const modelTime = Date.parse(modelAt), days = (modelTime - now) / DAY;
      if (!finite(modelTime) || days <= 0 || (maxDays === 0 ? eastern(modelTime).date !== eastern(now).date : days > maxDays)) { excluded++; continue; }
      const lifecycleOK = snapshot.symbol==='SPX' ? o.lifecycleVerified===true : o.expiryVerified===true;
      if (!lifecycleOK) {
        unverifiedExpiry++; lifecycleExcluded++;
        if (finite(o.oi) && finite(o.gamma) && o.gamma >= 0 && !(convention === 'signed' && !finite(o.position))) {
          const qty = convention === 'signed' ? o.position : o.oi * signFor(o);
          unqualifiedRows.push({...o,modelAt,days,qty,gross:o.gamma*o.oi*o.multiplier*snapshot.spot**2*.01,net:o.gamma*qty*o.multiplier*snapshot.spot**2*.01});
        }
        continue;
      }
      if (!finite(o.oi)) { missingOI++; continue; }
      if (!finite(o.gamma) || o.gamma < 0 || (convention === 'signed' && !finite(o.position))) { missingGamma++; continue; }
      const qty = convention === 'signed' ? o.position : o.oi * signFor(o);
      const gross = o.gamma * o.oi * o.multiplier * snapshot.spot ** 2 * .01;
      const net = o.gamma * qty * o.multiplier * snapshot.spot ** 2 * .01;
      const row = { ...o, modelAt, days, net, gross, qty };
      rows.push(row);
      if (!finite(o.iv) || o.iv <= 0) missingIV++;
      const b = byStrike.get(o.strike) || { strike: o.strike, call: 0, put: 0, net: 0, callOI: 0, putOI: 0 };
      b.net += net; b[o.type] += gross; b[o.type + 'OI'] += o.oi; byStrike.set(o.strike, b);
    }
    const strikes = [...byStrike.values()].sort((a, b) => a.strike - b.strike), usable = rows.filter(o => finite(o.iv) && o.iv > 0);
    const curve = usable.length ? Array.from({ length: 81 }, (_, i) => {
      const spot = snapshot.spot * (.92 + i * .002);
      const net = sum(usable.map(o => bs(spot, o.strike, o.days / 365, o.iv, snapshot.rate || 0, snapshot.dividend || 0, o.type).gamma * o.qty * o.multiplier * spot ** 2 * .01));
      return { spot, net };
    }) : [];
    const flips = [];
    for (let i = 1; i < curve.length; i++) {
      const a = curve[i - 1], b = curve[i];
      if (a.net * b.net < 0) flips.push(a.spot + (b.spot - a.spot) * (-a.net / (b.net - a.net)));
      else if (a.net === 0 && b.net !== 0) flips.push(a.spot);
    }
    const calls = strikes.filter(x => x.call > 0), puts = strikes.filter(x => x.put > 0);
    const callWall = calls.length ? calls.reduce((a, b) => b.call > a.call ? b : a).strike : null;
    const putWall = puts.length ? puts.reduce((a, b) => b.put > a.put ? b : a).strike : null;
    const zeroDay = rows.filter(o => eastern(Date.parse(o.modelAt)).date === eastern(now).date);
    const gross = sum(rows.map(o => o.gross)), net = sum(rows.map(o => o.net));
    const expiries = [...new Set(rows.map(x => x.modelAt))].sort();
    const importedInHorizon=snapshot.options.length-excluded, unqualifiedGross=sum(unqualifiedRows.map(o=>o.gross)),unqualifiedNet=sum(unqualifiedRows.map(o=>o.net));
    const curveStatus=!curve.length?'UNAVAILABLE_PARTIAL_INPUTS':flips.length>1?'MULTIPLE_CROSSINGS_IN_TESTED_RANGE':flips.length===1?'ONE_CROSSING_IN_TESTED_RANGE':'NO_CROSSING_IN_TESTED_RANGE';
    const independentlyVerified=rows.length>0&&rows.every(o=>o.lifecycleIndependentlyVerified===true);
    return { net, gross, grossCalls:sum(rows.filter(o=>o.type==='call').map(o=>o.gross)),grossPuts:sum(rows.filter(o=>o.type==='put').map(o=>o.gross)),strikes, rows, unqualifiedRows, unqualifiedGross, unqualifiedNet, curve, flips, flip: flips.length ? [...flips].sort((a, b) => Math.abs(a - snapshot.spot) - Math.abs(b - snapshot.spot))[0] : null, callWall, putWall, zeroShare: gross ? sum(zeroDay.map(o => o.gross)) / gross * 100 : null, expiries, excluded, lifecycleExcluded,missingGamma, missingIV, missingOI, unverifiedExpiry, importedInHorizon, coverage: importedInHorizon ? rows.length / importedInHorizon * 100 : 0, coverageMeaning:'Qualified rows / imported rows within selected horizon; not total market coverage', complete: snapshot.chainComplete && missingGamma === 0 && missingOI===0 && unverifiedExpiry===0, independentlyVerified, convention, modelNetAtSpot: curve.length ? curve[40].net : null, curveStatus,testedSpotRange:curve.length?{low:curve[0].spot,high:curve.at(-1).spot,points:curve.length}:null,qualificationStatus:rows.length?(snapshot.chainComplete&&unverifiedExpiry===0?'QUALIFIED_IMPORTED_SCOPE':'PARTIAL_IMPORTED_SCOPE'):(unqualifiedRows.length?'UNQUALIFIED_ARITHMETIC_ONLY':'UNAVAILABLE') };
  }
  function riskSize({ equity, riskPct, entry, stop, symbol, fee = 2.5, slippageTicks = 2 }) {
    const spec = SPECS[symbol]; assert(spec, 'Unsupported futures risk symbol.');
    assert([equity, riskPct, entry, stop, fee, slippageTicks].every(finite), 'Risk inputs must be finite.');
    assert(equity > 0 && riskPct > 0 && riskPct <= 5 && entry > 0 && stop > 0 && entry !== stop && fee >= 0 && slippageTicks >= 0, 'Invalid risk inputs; risk must be >0 and ≤5%.');
    const budget = equity * riskPct / 100, stopRisk = Math.abs(entry - stop) * spec.point;
    const costs = fee * 2 + slippageTicks * 2 * spec.tick * spec.point, perContract = stopRisk + costs;
    const contracts = Math.max(0, Math.floor(budget / perContract));
    return { budget, stopRisk, perContract, costs, contracts, total: contracts * perContract, tickValue: spec.tick * spec.point, pointValue: spec.point };
  }
  function setup(snapshot, bars, prefs = {}) {
    const { barMinutes = 5, eventFreeze = false } = prefs;
    const i = indicators(bars, barMinutes), f = factorScore(i);
    if (i.insufficient || !finite(i.atr) || i.atr <= 0) return { state: 'INSUFFICIENT DATA', direction: 'neutral', indicators: { ...i, insufficient: true }, factors: f, gates: ['Need at least 50 closed bars and nonzero ATR in this timeframe.'], paperOnly: true };
    const direction = i.direction, long = direction !== 'bearish', tick = SPECS[snapshot.symbol]?.tick || .01;
    const snap = x => round(Math.round(x / tick) * tick, 4);
    const entry = snap(i.ema20), width = i.atr * .2;
    const stop = snap(long ? Math.min(entry - 1.2 * i.atr, i.support - .15 * i.atr) : Math.max(entry + 1.2 * i.atr, i.resistance + .15 * i.atr));
    const distance = Math.abs(entry - stop), t1 = snap(entry + (long ? 1 : -1) * distance * 2), t2 = snap(entry + (long ? 1 : -1) * distance * 3);
    const extension = Math.abs(snapshot.spot - entry) / (i.atr || 1);
    const invalid = long ? snapshot.spot <= stop : snapshot.spot >= stop;
    let state = direction === 'neutral' ? 'WATCH' : invalid ? 'INVALIDATED' : extension > 1.5 ? 'NO CHASE' : extension > .35 ? 'WAIT FOR RETEST' : 'ARMED';
    if (eventFreeze) state = 'EVENT FREEZE';
    const gates = [snapshot.mode === 'demo' ? 'Synthetic data: paper research only.' : 'External snapshot: provenance / latency not independently certified.', 'Strategy has no out-of-sample qualification.', 'Exchange holiday / halt and economic-event feeds are not connected.'];
    const now = Date.now(), age = (now - Date.parse(snapshot.asOf)) / 1000;
    if (snapshot.mode !== 'demo' && (age > 90 || age < -5)) gates.push('Snapshot is stale or future-dated relative to system time.');
    const priceAge = (now - Date.parse(snapshot.spotAsOf || new Date(last(snapshot.bars).t + (snapshot.barMinutes || 5) * 60000).toISOString())) / 1000;
    if (snapshot.mode !== 'demo' && (priceAge > 90 || priceAge < -5)) gates.push('Reference price is stale or future-dated. A fresh fetch timestamp does not make the last bar current.');
    if (!snapshot.options.length) gates.push('No native option chain loaded for this instrument.');
    if (eventFreeze) gates.push('Manual event freeze is ON.');
    const et = eastern(now);
    if (!SPECS[snapshot.symbol] && (['Sat', 'Sun'].includes(et.weekday) || et.minutes < 585 || et.minutes >= 960)) gates.push('Conservative cash-entry window is closed (09:45–16:00 ET, weekdays).');
    return { state, direction, indicators: i, factors: f, entry, entryLow: snap(entry - width), entryHigh: snap(entry + width), stop, t1, t2, rr: 2, extension, gates, paperOnly: true, timeStop: barMinutes >= 1440 ? '10 daily bars' : '6 signal bars', trigger: direction === 'neutral' ? 'Wait for directional EMA structure before considering a setup.' : `${long ? 'Bullish' : 'Bearish'} close after an EMA20 retest; require ${long ? 'higher low' : 'lower high'} and renewed volume.`, invalidation: `Close ${long ? 'below' : 'above'} ${stop}; cancel after ${barMinutes >= 1440 ? '10 daily' : '6 signal'} bars without follow-through.` };
  }
  function optionPlan(snapshot, { direction = 'bullish', style = 'spread', minDays = 7, maxDays = 45, equity = 25000, riskPct = .5, fee = .65, slip = .02 } = {}) {
    assert(['bullish','bearish','neutral'].includes(direction) && ['spread','single'].includes(style), 'Invalid option direction or style.');
    assert(finite(minDays) && finite(maxDays) && minDays >= 0 && maxDays >= minDays, 'Invalid expiry window.');
    assert([equity, riskPct, fee, slip].every(finite) && equity > 0 && riskPct > 0 && riskPct <= 5 && fee >= 0 && slip >= 0, 'Invalid option risk settings.');
    const now = Date.parse(snapshot.asOf), type = direction === 'bearish' ? 'put' : 'call';
    if (direction === 'neutral') return { ok: false, reason: 'No directional structure: no options candidate.' };
    const pool = snapshot.options.filter(o => {
      const days = (Date.parse(o.expiry) - now) / DAY, mid = (o.bid + o.ask) / 2;
      const age = o.quoteAsOf ? (now - Date.parse(o.quoteAsOf)) / 1000 : Infinity;
      return o.type === type && days >= minDays && days <= maxDays && o.multiplier === 100 && o.expiryVerified === true && finite(o.bid) && finite(o.ask) && o.bid > 0 && o.ask >= o.bid && o.oi >= 100 && o.volume >= 20 && mid > 0 && (o.ask - o.bid) / mid <= .15 && age >= -5 && age <= 90 && finite(o.delta) && (type === 'call' ? o.delta >= 0 : o.delta <= 0);
    });
    const possible = pool.filter(o => Math.abs(o.delta) >= .35 && Math.abs(o.delta) <= .65).sort((a, b) => Math.abs(Math.abs(a.delta) - .5) - Math.abs(Math.abs(b.delta) - .5) || Date.parse(a.expiry) - Date.parse(b.expiry));
    for (const buy of possible) {
      const candidates = style === 'single' ? [null] : pool.filter(o => o.expiry === buy.expiry && (type === 'call' ? o.strike > buy.strike : o.strike < buy.strike)).sort((a, b) => Math.abs(a.strike - buy.strike) - Math.abs(b.strike - buy.strike));
      for (const sell of candidates) {
        const entry = buy.ask + slip - (sell ? Math.max(0, sell.bid - slip) : 0), width = sell ? Math.abs(sell.strike - buy.strike) : null;
        if (entry <= 0 || (sell && entry >= width)) continue;
        const legs = sell ? 2 : 1, fees = legs * fee, maxLoss = entry * 100 + fees;
        const maxGain = sell ? (width - entry) * 100 - fees : type === 'call' ? Infinity : buy.strike * 100 - maxLoss;
        if (maxGain <= 0) continue;
        const breakeven = buy.strike + (type === 'call' ? 1 : -1) * (entry + fees / 100);
        const budget = equity * riskPct / 100, contracts = Math.max(0, Math.floor(budget / maxLoss));
        return { ok: true, type, style, buy, sell, entry, width, maxLoss, maxGain, breakeven, contracts, budget, fees, legs, days: (Date.parse(buy.expiry) - now) / DAY, paperOnly: true, label: style === 'single' ? `Long ${type}` : `${type === 'call' ? 'Bull call' : 'Bear put'} debit spread` };
      }
    }
    return { ok: false, reason: 'No standard contract passes expiry, quote-age, delta, open-interest, volume and spread filters. No substitute is invented.' };
  }
  function optionPayoff(plan, spot) {
    assert(plan.ok && finite(spot) && spot >= 0, 'A valid plan and nonnegative terminal spot are required.');
    const intrinsic = o => o.type === 'call' ? Math.max(spot - o.strike, 0) : Math.max(o.strike - spot, 0);
    return (intrinsic(plan.buy) - (plan.sell ? intrinsic(plan.sell) : 0) - plan.entry) * 100 - plan.fees;
  }
  function rng(seed) { let a = seed >>> 0; return () => { a += 0x6D2B79F5; let t = a; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const cache = {};
  function demo(symbol = 'SPY') {
    if (cache[symbol]) return cache[symbol];
    assert(NAMES[symbol], 'Unsupported demo instrument.');
    const bases = { SPY: 580, QQQ: 505, SPX: 5800, ES: 5815, NQ: 20500, NVDA: 145, TSLA: 310, MU: 145, GOOGL: 192, PLTR: 88, V: 325, MCD: 285, AMZN: 220, EOG: 120, KMI: 28 };
    const seed = [...symbol].reduce((n, c) => n * 31 + c.charCodeAt(0), 13), random = rng(seed);
    const base = bases[symbol], asOf = '2026-09-04T18:30:00.000Z', endDate = new Date('2026-09-04T00:00:00Z');
    const dates = [];
    for (let d = new Date(endDate); dates.length < 75; d.setUTCDate(d.getUTCDate() - 1)) if (![0, 6].includes(d.getUTCDay())) dates.unshift(d.toISOString().slice(0, 10));
    let price = base * .96, bars = [];
    const volBase = ['SPY', 'QQQ'].includes(symbol) ? 270000 : ['ES', 'NQ'].includes(symbol) ? 1600 : 120000;
    const direction = ['TSLA', 'PLTR', 'EOG', 'KMI'].includes(symbol) ? -.7 : 1;
    for (let day = 0; day < dates.length; day++) {
      price *= 1 + (random() - .47) * .003;
      const count = day === dates.length - 1 ? 60 : 78;
      for (let j = 0; j < count; j++) {
        const t = Date.parse(dates[day] + 'T13:30:00Z') + j * 300000;
        const o = price;
        const move = (random() - .49) * .0008 + Math.sin(j / 8) * .0001 + (day === dates.length - 1 ? direction * .000038 : 0);
        price = Math.max(base * .1, price * (1 + move));
        const wick = base * (.00008 + random() * .00022);
        bars.push({ t, o, h: Math.max(o, price) + wick, l: Math.min(o, price) - wick, c: price, v: Math.round(volBase * (.5 + random() * .7 + .5 * Math.abs(j - 39) / 39)), session: dates[day] });
      }
    }
    // Deterministic synthetic scale. These are not historical market prices.
    const scale = base / last(bars).c;
    bars = bars.map(b => ({ ...b, o: b.o * scale, h: b.h * scale, l: b.l * scale, c: b.c * scale, v: symbol === 'SPX' ? 0 : b.v }));
    const options = [], chainSymbol = symbol;
    if (!['ES', 'NQ'].includes(symbol)) {
      const step = symbol === 'SPX' ? 25 : base < 100 ? 1 : 5, anchor = Math.round(base / step) * step;
      const offsets = [0, 7, 14, 28, 42], ivBase = ['NVDA', 'TSLA', 'MU', 'PLTR'].includes(symbol) ? .39 : .19;
      for (const d of offsets) {
        const expiry = new Date(Date.parse('2026-09-04T20:00:00Z') + d * DAY).toISOString();
        for (let k = -15; k <= 15; k++) for (const type of ['call', 'put']) {
          const strike = anchor + k * step; if (strike <= 0) continue;
          const iv = ivBase + (type === 'put' ? .025 : 0) + Math.abs(k) * .003;
          const p = bs(base, strike, (Date.parse(expiry) - Date.parse(asOf)) / YEAR, iv, .04, .012, type);
          const wallAt = type === 'call' ? 4 : -4, weight = .3 + Math.exp(-(((k - wallAt) / 5) ** 2)) * (type === 'call' ? 1.25 : 1.05);
          const oi = Math.round((1800 + random() * 14000) * weight * (d === 0 ? 1.4 : 1));
          const spread = Math.max(.02, p.value * (.02 + random() * .03));
          const bid = round(Math.max(.01, p.value - spread / 2)), ask = round(Math.max(bid + .01, p.value + spread / 2));
          options.push({ id: `DEMO:${chainSymbol}:${d}:${type}:${strike}`, type, strike, expiry, expiryVerified: true, oi, multiplier: 100, iv, gamma: p.gamma, delta: p.delta, bid, ask, volume: Math.round(40 + random() * oi * .15), quoteAsOf: asOf });
        }
      }
    }
    cache[symbol] = { symbol, name: NAMES[symbol], spot: base, asOf, bars, options, barMinutes: 5, mode: 'demo', source: 'Seeded synthetic fixture · NOT historical prices', oiAsOf: '2026-09-03 (synthetic)', chainComplete: options.length > 0, rate: .04, dividend: .012, contract: ['ES', 'NQ'].includes(symbol) ? symbol + 'U26-DEMO' : null, warnings: ['Synthetic OHLCV, open interest and option quotes. Not market observations.', 'Weekday fixture omits holiday and session exceptions.'], expiryConvention: 'Explicit synthetic PM expiry timestamps' };
    return cache[symbol];
  }
  return { VERSION, DAY, SPECS, NAMES, finite, round, clamp, mean, last, bs, impliedVol, eastern, validateSnapshot, ema, rsi, atr, aggregate, closedBars, easternSessionEnd, indicators, factorScore, gammaExposure, riskSize, setup, optionPlan, optionPayoff, demo };
});
