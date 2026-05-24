/* ============================================================
   Dendrai Risk Loop — Statistical Forecasting Engine
   Models: ARIMA, Prophet-like, Random Forest, Ensemble
   All pure JS, no dependencies, browser-safe.
   ============================================================ */

window.FORECASTING = (function () {

  // ── Utilities ─────────────────────────────────────────────

  function mean(a) { return a.reduce((s, v) => s + v, 0) / (a.length || 1); }
  function variance(a) { const m = mean(a); return a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length || 1); }
  function std(a) { return Math.sqrt(variance(a)); }

  // Gaussian elimination  Ax = b  (destructive on A copy)
  function solveLinear(A, b) {
    const n = A.length;
    const M = A.map((row, i) => [...row, b[i]]);
    for (let col = 0; col < n; col++) {
      let maxRow = col;
      for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[maxRow][col])) maxRow = r;
      [M[col], M[maxRow]] = [M[maxRow], M[col]];
      const piv = M[col][col];
      if (Math.abs(piv) < 1e-12) continue;
      for (let r = col + 1; r < n; r++) {
        const f = M[r][col] / piv;
        for (let k = col; k <= n; k++) M[r][k] -= f * M[col][k];
      }
    }
    const x = new Array(n).fill(0);
    for (let i = n - 1; i >= 0; i--) {
      x[i] = M[i][n];
      for (let j = i + 1; j < n; j++) x[i] -= M[i][j] * x[j];
      x[i] /= M[i][i] || 1e-12;
    }
    return x;
  }

  // Ordinary Least Squares  y = X β
  function ols(X, y) {
    const n = X.length, p = X[0].length;
    const XtX = Array.from({ length: p }, () => new Array(p).fill(0));
    const Xty = new Array(p).fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < p; j++) {
        Xty[j] += X[i][j] * y[i];
        for (let k = 0; k < p; k++) XtX[j][k] += X[i][j] * X[i][k];
      }
    }
    return solveLinear(XtX, Xty);
  }

  // Difference d times; returns differenced series + stack of initial values for inversion
  function difference(arr, d) {
    let cur = [...arr];
    const initials = [];
    for (let i = 0; i < d; i++) {
      initials.push(cur[0]);
      cur = cur.slice(1).map((v, j) => v - cur[j]);
    }
    return { diff: cur, initials };
  }

  // Inverse differencing (cumsum d times seeded from initials)
  function undifference(forecasted, lastOrig, d) {
    if (d === 0) return forecasted;
    let prev = lastOrig;
    return forecasted.map(fd => { prev = prev + fd; return prev; });
  }

  // Pearson correlation
  function corr(xs, ys) {
    const n = Math.min(xs.length, ys.length);
    if (n < 4) return null;
    const sx = xs.slice(-n), sy = ys.slice(-n);
    const mx = mean(sx), my = mean(sy);
    let num = 0, dx = 0, dy = 0;
    for (let i = 0; i < n; i++) {
      const a = sx[i] - mx, b = sy[i] - my;
      num += a * b; dx += a * a; dy += b * b;
    }
    const d = Math.sqrt(dx * dy);
    return d < 1e-12 ? null : num / d;
  }

  // Seeded PRNG (Mulberry32) for reproducible RF
  function mulberry32(seed) {
    return () => {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }


  // ── ARIMA(p, d, q) ─────────────────────────────────────────
  // AR coefficients via OLS; MA via residual OLS (two-step approximation).

  function fitARIMA(values, { p = 2, d = 1, q = 1 } = {}) {
    const N = values.length;
    // Degrade gracefully for very short series
    if (N < p + d + 6) { p = 1; d = 1; q = 0; }

    const { diff: diffed, initials } = difference(values, d);
    const m = mean(diffed);
    const centered = diffed.map(v => v - m);
    const nd = centered.length;

    // AR via OLS: build [lag1, lag2, …, lagP] design matrix
    const Xar = [], yar = [];
    for (let t = Math.max(p, q); t < nd; t++) {
      const row = [];
      for (let k = 1; k <= p; k++) row.push(centered[t - k]);
      Xar.push(row); yar.push(centered[t]);
    }
    let phi = p > 0 && Xar.length > p ? ols(Xar, yar) : [];

    // Residuals for MA fit
    const residuals = new Array(nd).fill(0);
    for (let t = p; t < nd; t++) {
      let pred = 0;
      for (let k = 0; k < phi.length; k++) pred += phi[k] * centered[t - 1 - k];
      residuals[t] = centered[t] - pred;
    }

    // MA via OLS on residuals
    let theta = [];
    if (q > 0 && nd > q + p + 2) {
      const Xma = [], yma = [];
      for (let t = Math.max(p, q); t < nd; t++) {
        const row = [];
        for (let k = 1; k <= q; k++) row.push(residuals[t - k]);
        Xma.push(row); yma.push(residuals[t]);
      }
      if (Xma.length > q) theta = ols(Xma, yma);
    }

    // Residual std for CI
    const fittedResid = residuals.slice(Math.max(p, q));
    const sigmaResid = std(fittedResid) || 1;

    return {
      type: 'arima', p, d, q, phi, theta, m, initials,
      lastOrig: values[values.length - 1],
      histDiff: diffed.slice(-Math.max(p + 1, 4)),
      histResid: residuals.slice(-Math.max(q + 1, 4)),
      sigmaResid,
    };
  }

  function predictARIMA(model, steps) {
    const { phi, theta, m, d, lastOrig, histDiff, histResid, sigmaResid } = model;
    const p = phi.length, q = theta.length;

    const workDiff = [...histDiff];
    const centHist = workDiff.map(v => v - m);
    const workResid = [...histResid];

    const diffFc = [];
    for (let s = 0; s < steps; s++) {
      const t = centHist.length;
      let val = m;
      for (let k = 0; k < p && k < t; k++) val += phi[k] * centHist[t - 1 - k];
      for (let k = 0; k < q && k < workResid.length; k++) val += theta[k] * workResid[workResid.length - 1 - k];
      diffFc.push(val);
      centHist.push(val - m);
      workResid.push(0);
    }

    const base = undifference(diffFc, lastOrig, d);
    const lo = base.map((v, i) => v - 1.96 * sigmaResid * Math.sqrt(i + 1));
    const hi = base.map((v, i) => v + 1.96 * sigmaResid * Math.sqrt(i + 1));
    return { base, lo, hi };
  }


  // ── Prophet-like (trend + Fourier seasonality) ─────────────
  // y(t) = β₀ + β₁·t + Σ [aₖ·sin(2πkt/P) + bₖ·cos(2πkt/P)]
  // P = 4 (quarterly), k = 1..2 Fourier terms

  function fitProphet(values) {
    const n = values.length;
    const X = values.map((_, i) => {
      const t = i / Math.max(n - 1, 1);
      const phase = i % 4;          // 0..3 within each year
      return [
        1, t,
        Math.sin(2 * Math.PI * phase / 4), Math.cos(2 * Math.PI * phase / 4),
        Math.sin(4 * Math.PI * phase / 4), Math.cos(4 * Math.PI * phase / 4),
      ];
    });
    const coeffs = ols(X, values);
    const fitted = X.map(row => row.reduce((s, v, j) => s + v * coeffs[j], 0));
    const resid = values.map((v, i) => v - fitted[i]);
    const sigmaResid = std(resid) || 1;
    return { type: 'prophet', coeffs, n, sigmaResid };
  }

  function predictProphet(model, steps) {
    const { coeffs, n, sigmaResid } = model;
    const base = [];
    for (let s = 0; s < steps; s++) {
      const i = n + s;
      const t = i / (n + steps - 1);
      const phase = i % 4;
      const x = [
        1, t,
        Math.sin(2 * Math.PI * phase / 4), Math.cos(2 * Math.PI * phase / 4),
        Math.sin(4 * Math.PI * phase / 4), Math.cos(4 * Math.PI * phase / 4),
      ];
      base.push(x.reduce((s, v, j) => s + v * coeffs[j], 0));
    }
    const lo = base.map((v, i) => v - 1.96 * sigmaResid * Math.sqrt(i + 1));
    const hi = base.map((v, i) => v + 1.96 * sigmaResid * Math.sqrt(i + 1));
    return { base, lo, hi };
  }


  // ── Random Forest ──────────────────────────────────────────
  // Features: lags 1-4, rolling mean/std, time index, quarter, FRED values at t

  function buildFeatures(values, fredArrays, i) {
    const feats = [];
    for (const lag of [1, 2, 3, 4]) feats.push(i >= lag ? values[i - lag] : (values[0] || 0));
    const win = values.slice(Math.max(0, i - 4), i);
    feats.push(win.length ? mean(win) : values[0] || 0);
    feats.push(win.length > 1 ? std(win) : 0);
    feats.push(i);
    feats.push(i % 4);
    if (fredArrays) for (const arr of Object.values(fredArrays)) feats.push(i < arr.length ? arr[i] : (arr[arr.length - 1] || 0));
    return feats;
  }

  function fitTree(X, y, depth, minSamples) {
    if (!X.length || depth === 0 || X.length <= minSamples) return { leaf: true, value: mean(y) };
    const nF = X[0].length;
    let bestLoss = Infinity, bestF = 0, bestT = 0;
    for (let f = 0; f < nF; f++) {
      const sorted = [...new Set(X.map(r => r[f]))].sort((a, b) => a - b);
      for (let k = 0; k < sorted.length - 1; k++) {
        const thresh = (sorted[k] + sorted[k + 1]) / 2;
        const ly = [], ry = [];
        for (let i = 0; i < X.length; i++) (X[i][f] <= thresh ? ly : ry).push(y[i]);
        if (!ly.length || !ry.length) continue;
        const loss = ly.reduce((s, v) => s + (v - mean(ly)) ** 2, 0) + ry.reduce((s, v) => s + (v - mean(ry)) ** 2, 0);
        if (loss < bestLoss) { bestLoss = loss; bestF = f; bestT = thresh; }
      }
    }
    const li = [], ri = [];
    X.forEach((r, i) => (r[bestF] <= bestT ? li : ri).push(i));
    if (!li.length || !ri.length) return { leaf: true, value: mean(y) };
    return {
      leaf: false, f: bestF, t: bestT,
      left: fitTree(li.map(i => X[i]), li.map(i => y[i]), depth - 1, minSamples),
      right: fitTree(ri.map(i => X[i]), ri.map(i => y[i]), depth - 1, minSamples),
    };
  }

  function predTree(node, x) {
    if (node.leaf) return node.value;
    return x[node.f] <= node.t ? predTree(node.left, x) : predTree(node.right, x);
  }

  function fitRandomForest(values, fredArrays = null, nTrees = 25) {
    const n = values.length;
    const X = values.map((_, i) => buildFeatures(values, fredArrays, i));
    const rng = mulberry32(7);
    const trees = [];
    for (let t = 0; t < nTrees; t++) {
      const idx = Array.from({ length: n }, () => Math.floor(rng() * n));
      trees.push(fitTree(idx.map(i => X[i]), idx.map(i => values[i]), 4, 2));
    }
    return { type: 'rf', trees, values: [...values], fredArrays };
  }

  function predictRandomForest(model, steps) {
    const { trees, fredArrays } = model;
    const extended = [...model.values];
    const base = [], treePreds = [];
    for (let s = 0; s < steps; s++) {
      const x = buildFeatures(extended, fredArrays, extended.length);
      const preds = trees.map(tr => predTree(tr, x));
      const avg = mean(preds);
      const sigma = std(preds);
      base.push(avg); treePreds.push({ avg, sigma });
      extended.push(avg);
    }
    const lo = treePreds.map(p => p.avg - 1.96 * p.sigma);
    const hi = treePreds.map(p => p.avg + 1.96 * p.sigma);
    return { base, lo, hi };
  }


  // ── Ensemble ───────────────────────────────────────────────

  function fitEnsemble(values, fredArrays = null) {
    const arima   = fitARIMA(values);
    const prophet = fitProphet(values);
    const rf      = fitRandomForest(values, fredArrays);
    return { type: 'ensemble', arima, prophet, rf, weights: [1/3, 1/3, 1/3] };
  }

  function predictEnsemble(model, steps) {
    const { arima, prophet, rf, weights: w } = model;
    const p1 = predictARIMA(arima, steps);
    const p2 = predictProphet(prophet, steps);
    const p3 = predictRandomForest(rf, steps);
    const base = p1.base.map((v, i) => w[0]*v + w[1]*p2.base[i] + w[2]*p3.base[i]);
    const lo   = p1.lo.map((v, i)   => w[0]*v + w[1]*p2.lo[i]   + w[2]*p3.lo[i]);
    const hi   = p1.hi.map((v, i)   => w[0]*v + w[1]*p2.hi[i]   + w[2]*p3.hi[i]);
    return { base, lo, hi, components: { arima: p1.base, prophet: p2.base, rf: p3.base } };
  }

  function updateEnsembleWeights(model, mapes) {
    const inv = mapes.map(m => 1 / (Math.max(m, 0.01)));
    const tot = inv.reduce((a, b) => a + b, 0);
    model.weights = inv.map(v => v / tot);
  }


  // ── FRED → EDGAR leading indicator analysis ────────────────
  // For each FRED series at each lag 1..maxLag, compute Pearson r
  // Returns sorted list: [{ id, lag, r, absR }]

  function findLeadingIndicators(kpiValues, fredSeriesMap, maxLag = 4) {
    const results = [];
    for (const [id, seriesArr] of Object.entries(fredSeriesMap)) {
      for (let lag = 1; lag <= maxLag; lag++) {
        // FRED(t-lag) → KPI(t): shift FRED back by lag
        const fredShifted = seriesArr.slice(0, seriesArr.length - lag);
        const kpiAligned  = kpiValues.slice(lag);
        const n = Math.min(fredShifted.length, kpiAligned.length);
        if (n < 4) continue;
        const r = corr(fredShifted.slice(-n), kpiAligned.slice(-n));
        if (r != null && !isNaN(r)) results.push({ id, lag, r, absR: Math.abs(r) });
      }
    }
    return results.sort((a, b) => b.absR - a.absR);
  }

  // Build a 2-D matrix of {lag, id} → r  for heatmap display
  function leadingIndicatorMatrix(kpiValues, fredSeriesMap, maxLag = 4) {
    const raw = findLeadingIndicators(kpiValues, fredSeriesMap, maxLag);
    const ids = [...new Set(raw.map(r => r.id))];
    const lags = Array.from({ length: maxLag }, (_, i) => i + 1);
    const matrix = {};
    for (const id of ids) {
      matrix[id] = {};
      for (const lag of lags) {
        const entry = raw.find(r => r.id === id && r.lag === lag);
        matrix[id][lag] = entry ? entry.r : null;
      }
    }
    // Return top-N IDs by best absolute r across lags
    const topIds = ids
      .map(id => ({ id, best: Math.max(...lags.map(lag => Math.abs(matrix[id][lag] || 0))) }))
      .sort((a, b) => b.best - a.best)
      .slice(0, 8)
      .map(x => x.id);
    return { matrix, ids: topIds, lags };
  }

  return {
    fitARIMA, predictARIMA,
    fitProphet, predictProphet,
    fitRandomForest, predictRandomForest,
    fitEnsemble, predictEnsemble, updateEnsembleWeights,
    findLeadingIndicators, leadingIndicatorMatrix,
    corr, mean, std,
  };
})();
