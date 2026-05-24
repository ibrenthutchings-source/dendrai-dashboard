/* ============================================================
   Dendrai Risk Loop — Backtesting Engine
   Walk-forward validation: MAPE · RMSE · R² · Precision · Recall · F1
   ============================================================ */

window.BACKTESTING = (function () {

  function mape(actuals, predicted) {
    let sum = 0, count = 0;
    const n = Math.min(actuals.length, predicted.length);
    for (let i = 0; i < n; i++) {
      if (actuals[i] != null && predicted[i] != null && actuals[i] !== 0) {
        sum += Math.abs((actuals[i] - predicted[i]) / actuals[i]);
        count++;
      }
    }
    return count ? (sum / count) * 100 : null;
  }

  function rmse(actuals, predicted) {
    const n = Math.min(actuals.length, predicted.length);
    if (!n) return null;
    const ss = actuals.slice(0, n).reduce((s, v, i) => s + (v - predicted[i]) ** 2, 0);
    return Math.sqrt(ss / n);
  }

  function r2(actuals, predicted) {
    const n = Math.min(actuals.length, predicted.length);
    if (!n) return null;
    const act = actuals.slice(0, n);
    const m = act.reduce((a, b) => a + b, 0) / n;
    let ssTot = 0, ssRes = 0;
    for (let i = 0; i < n; i++) {
      ssTot += (act[i] - m) ** 2;
      ssRes += (act[i] - predicted[i]) ** 2;
    }
    return ssTot === 0 ? 0 : 1 - ssRes / ssTot;
  }

  // Directional accuracy — "positive" = value went up vs prior period
  function directionalMetrics(actuals, predicted) {
    const n = Math.min(actuals.length, predicted.length);
    let tp = 0, fp = 0, fn = 0, tn = 0;
    for (let i = 1; i < n; i++) {
      const aUp = actuals[i] > actuals[i - 1];
      const pUp = predicted[i] > predicted[i - 1];
      if (aUp && pUp)   tp++;
      else if (!aUp && pUp)  fp++;
      else if (aUp && !pUp)  fn++;
      else tn++;
    }
    const precision = (tp + fp) > 0 ? tp / (tp + fp) : null;
    const recall    = (tp + fn) > 0 ? tp / (tp + fn) : null;
    const f1 = precision != null && recall != null && (precision + recall) > 0
      ? 2 * precision * recall / (precision + recall) : null;
    return { precision, recall, f1, tp, fp, fn, tn };
  }

  // Walk-forward backtest: train on expanding window, 1-step ahead at each test point.
  // fitFn(trainValues, fredArrays?) → model
  // predictFn(model, steps) → { base: number[] }
  function walkForwardBacktest(values, fitFn, predictFn, testPeriods = 4, fredArrays = null) {
    const n = values.length;
    if (n < testPeriods + 6) return null;
    const trainEnd = n - testPeriods;
    const actuals = [], predicted = [];
    for (let t = 0; t < testPeriods; t++) {
      const train = values.slice(0, trainEnd + t);
      const fred  = fredArrays
        ? Object.fromEntries(Object.entries(fredArrays).map(([k, v]) => [k, v.slice(0, trainEnd + t)]))
        : null;
      try {
        const model = fitFn(train, fred);
        const result = predictFn(model, 1);
        predicted.push(result.base ? result.base[0] : result[0]);
      } catch {
        predicted.push(train[train.length - 1]); // fallback: last value
      }
      actuals.push(values[trainEnd + t]);
    }
    return {
      actuals, predicted,
      periods: testPeriods,
      mape:      mape(actuals, predicted),
      rmse:      rmse(actuals, predicted),
      r2:        r2(actuals, predicted),
      ...directionalMetrics(actuals, predicted),
    };
  }

  const MODEL_DEFS = [
    {
      key: 'arima',
      name: 'ARIMA',
      color: 'var(--acc)',
      fitFn:  (v, f) => { const m = FORECASTING.fitARIMA(v); return m; },
      predFn: (m, s) => FORECASTING.predictARIMA(m, s),
    },
    {
      key: 'prophet',
      name: 'Prophet',
      color: 'var(--violet)',
      fitFn:  (v)    => FORECASTING.fitProphet(v),
      predFn: (m, s) => FORECASTING.predictProphet(m, s),
    },
    {
      key: 'rf',
      name: 'Random Forest',
      color: 'var(--amber)',
      fitFn:  (v, f) => FORECASTING.fitRandomForest(v, f),
      predFn: (m, s) => FORECASTING.predictRandomForest(m, s),
    },
    {
      key: 'ensemble',
      name: 'Ensemble',
      color: 'var(--ink)',
      fitFn:  (v, f) => FORECASTING.fitEnsemble(v, f),
      predFn: (m, s) => FORECASTING.predictEnsemble(m, s),
    },
  ];

  // Run all 4 models, update ensemble weights from backtest MAPEs, return full results
  function backtestAll(values, fredArrays = null, testPeriods = 4) {
    const results = {};
    const mapes = [];
    for (const def of MODEL_DEFS.filter(d => d.key !== 'ensemble')) {
      const bt = walkForwardBacktest(values, def.fitFn, def.predFn, testPeriods, fredArrays);
      results[def.key] = bt;
      mapes.push(bt?.mape ?? 99);
    }
    // Refit ensemble with calibrated weights then backtest it
    const ensModel = FORECASTING.fitEnsemble(values, fredArrays);
    FORECASTING.updateEnsembleWeights(ensModel, mapes);
    const ensBt = walkForwardBacktest(values, () => ensModel, FORECASTING.predictEnsemble, testPeriods, fredArrays);
    results.ensemble = ensBt;
    return { results, mapes, ensembleWeights: ensModel.weights };
  }

  // Fit all 4 models on full dataset and return forecasts (for display)
  function forecastAll(values, fredArrays = null, steps = 4, backtestMapes = null) {
    const forecasts = {};
    const mapes = backtestMapes || [1, 1, 1];
    for (const def of MODEL_DEFS.filter(d => d.key !== 'ensemble')) {
      try {
        const model = def.fitFn(values, fredArrays);
        forecasts[def.key] = def.predFn(model, steps);
      } catch (e) {
        forecasts[def.key] = null;
      }
    }
    const ensModel = FORECASTING.fitEnsemble(values, fredArrays);
    FORECASTING.updateEnsembleWeights(ensModel, mapes);
    forecasts.ensemble = FORECASTING.predictEnsemble(ensModel, steps);
    return forecasts;
  }

  function fmtPct(v, digits = 1)  { return v == null ? '—' : v.toFixed(digits) + '%'; }
  function fmtNum(v, digits = 2)  { return v == null ? '—' : v.toFixed(digits); }
  function fmtProp(v)              { return v == null ? '—' : (v * 100).toFixed(0) + '%'; }

  function formatMetrics(bt) {
    if (!bt) return { mape:'—', rmse:'—', r2:'—', precision:'—', recall:'—', f1:'—' };
    return {
      mape:      bt.mape  != null ? fmtPct(bt.mape)        : '—',
      rmse:      bt.rmse  != null ? fmtNum(bt.rmse)         : '—',
      r2:        bt.r2    != null ? fmtNum(bt.r2)           : '—',
      precision: fmtProp(bt.precision),
      recall:    fmtProp(bt.recall),
      f1:        fmtProp(bt.f1),
    };
  }

  return {
    mape, rmse, r2, directionalMetrics,
    walkForwardBacktest, backtestAll, forecastAll,
    formatMetrics, MODEL_DEFS,
  };
})();
