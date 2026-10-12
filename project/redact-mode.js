/* ============================================================
   Redact Mode — TEMPORARY, for screen-recording the risk loop.

   Replaces the active company's name and ticker with a placeholder in
   everything rendered EXCEPT the Setup screen (Mission Control), where the
   real company has to stay visible so the demo can select it.

   Deliberately a DOM-level scrubber, not per-component changes: the name
   appears in headers, tables, charts (SVG text), AI narratives, chat,
   toasts and log lines, and threading a flag through every one of those
   would touch dozens of files for something meant to be removed.

   Turn on/off:
     - Ctrl+Alt+R (persisted in localStorage, so it survives a reload)
     - or open the app with ?redact=1 / ?redact=0
   No on-screen indicator, so nothing extra shows up in the recording.

   To remove this feature entirely: delete this file, its import in
   src/main.jsx, and the DENDRAI_REDACT effect in app.jsx.

   Limits — it rewrites text nodes and title/placeholder/aria-label/alt
   attributes. It does NOT rewrite <input> values, so a field the user
   typed the name into stays visible. Names the app has no way to know
   (a brand that differs from the legal name) go in localStorage key
   "dendrai.redact.extra" as a comma-separated list.
   ============================================================ */

window.DENDRAI_REDACT = (function () {
  const KEY = 'dendrai.redact';
  const EXTRA_KEY = 'dendrai.redact.extra';
  const SETUP_SCREEN = 'config';
  const FAKE_NAME = 'Acme Corp';
  const FAKE_TICKER = 'ACME';
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'data-screen-label'];

  // Corporate suffixes stripped to get the short trading name, and first
  // words too generic to redact on their own ("General Motors" -> not "General").
  const SUFFIX = /[\s,.]+(corporation|corp|incorporated|inc|company|co|limited|ltd|llc|plc|holdings|group|n\.v|s\.a|ag)\.?$/i;
  const GENERIC_FIRST = new Set([
    'general', 'first', 'united', 'american', 'national', 'global', 'international',
    'new', 'western', 'eastern', 'northern', 'southern', 'the', 'bank', 'north', 'south',
  ]);

  let enabled = false;
  let screen = null;
  let names = [];        // company-name terms, matched case-insensitively
  let tickers = [];      // ticker terms, matched case-sensitively on word boundaries
  let nameRe = null;
  let tickerRe = null;
  let observer = null;
  // Text node -> { orig, written }: the app's real text and what we wrote over
  // it. `written` lets us tell "still our scrubbed text" (safe to restore)
  // from "the app changed it since" (new real text; never overwrite it).
  const originals = new Map();
  const attrOriginals = new Map(); // Element -> { attr: real value }

  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function readFlag() {
    try {
      const q = new URLSearchParams(window.location.search).get('redact');
      if (q === '1') { window.localStorage.setItem(KEY, '1'); return true; }
      if (q === '0') { window.localStorage.removeItem(KEY); return false; }
      return window.localStorage.getItem(KEY) === '1';
    } catch { return false; }
  }

  function extraTerms() {
    try {
      return (window.localStorage.getItem(EXTRA_KEY) || '').split(',').map(s => s.trim()).filter(Boolean);
    } catch { return []; }
  }

  function buildTerms(rawTickers, rawNames) {
    const nameSet = new Set();
    for (const n of [...rawNames, ...extraTerms()]) {
      const full = (n || '').trim();
      if (full.length < 3) continue;
      nameSet.add(full);
      const short = full.replace(SUFFIX, '').trim();
      if (short.length >= 3) nameSet.add(short);
      const words = short.split(/\s+/);
      if (words.length >= 2 && words[0].length >= 4 && !GENERIC_FIRST.has(words[0].toLowerCase())) {
        nameSet.add(words[0]);
      }
    }
    // Longest first so "ON Semiconductor Corporation" wins over "ON Semiconductor".
    names = [...nameSet].sort((a, b) => b.length - a.length);
    tickers = [...new Set(rawTickers.map(t => (t || '').trim()).filter(t => t.length >= 1))];
    nameRe = names.length ? new RegExp(names.map(esc).join('|'), 'gi') : null;
    // Case-sensitive: a ticker like "ON" must not redact the word "on".
    tickerRe = tickers.length ? new RegExp(`\\b(?:${tickers.map(esc).join('|')})\\b`, 'g') : null;
  }

  function scrub(text) {
    if (!text) return text;
    let out = text;
    if (nameRe) out = out.replace(nameRe, FAKE_NAME);
    if (tickerRe) out = out.replace(tickerRe, FAKE_TICKER);
    return out;
  }

  function scrubTextNode(node) {
    const cur = node.nodeValue;
    if (!cur || !cur.trim()) return;
    const rec = originals.get(node);
    if (rec && cur === rec.written) return;   // our own output, already scrubbed
    const next = scrub(cur);
    if (next === cur) { if (rec) originals.delete(node); return; }
    originals.set(node, { orig: cur, written: next });
    node.nodeValue = next;
  }

  function scrubAttrs(el) {
    for (const a of ATTRS) {
      const cur = el.getAttribute && el.getAttribute(a);
      if (!cur) continue;
      const next = scrub(cur);
      if (next === cur) continue;
      let rec = attrOriginals.get(el);
      if (!rec) { rec = {}; attrOriginals.set(el, rec); }
      if (!(a in rec)) rec[a] = cur;
      el.setAttribute(a, next);
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { scrubTextNode(root); return; }
    if (root.nodeType !== 1) return;
    const tag = root.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE') return;
    scrubAttrs(root);
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n = tw.nextNode();
    while (n) {
      if (n.nodeType === 3) {
        const p = n.parentNode && n.parentNode.tagName;
        if (p !== 'SCRIPT' && p !== 'STYLE') scrubTextNode(n);
      } else scrubAttrs(n);
      n = tw.nextNode();
    }
  }

  function restoreAll() {
    for (const [node, rec] of originals) {
      if (node.isConnected && node.nodeValue === rec.written) node.nodeValue = rec.orig;
    }
    originals.clear();
    for (const [el, rec] of attrOriginals) {
      if (!el.isConnected) continue;
      for (const [a, v] of Object.entries(rec)) el.setAttribute(a, v);
    }
    attrOriginals.clear();
    if (baseTitle != null) document.title = baseTitle;
  }

  let baseTitle = null;

  // Active = on AND not on the Setup screen AND there is something to hide.
  function isActive() { return enabled && screen !== SETUP_SCREEN && (nameRe || tickerRe); }

  function onMutations(muts) {
    if (!isActive()) return;
    // Our own writes re-enter here; scrub() is idempotent (the placeholders
    // don't contain the terms), so this settles after one pass.
    for (const m of muts) {
      if (m.type === 'characterData') scrubTextNode(m.target);
      else if (m.type === 'attributes') scrubAttrs(m.target);
      else m.addedNodes.forEach(walk);
    }
    if (baseTitle == null) baseTitle = document.title;
    document.title = scrub(document.title);
  }

  function start() {
    if (observer || !document.body) return;
    observer = new MutationObserver(onMutations);
    observer.observe(document.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS,
    });
  }

  // Re-evaluate everything: called on toggle, screen change and term change.
  function apply() {
    start();
    if (isActive()) {
      if (baseTitle == null) baseTitle = document.title;
      walk(document.body);
      document.title = scrub(baseTitle);
    } else {
      restoreAll();
    }
  }

  return {
    // Called from app.jsx with the live company + current screen.
    update({ tickers: t = [], names: n = [], screen: s } = {}) {
      buildTerms(t, n);
      screen = s;
      apply();
    },
    toggle() {
      enabled = !enabled;
      try { enabled ? window.localStorage.setItem(KEY, '1') : window.localStorage.removeItem(KEY); } catch {}
      apply();
    },
    get enabled() { return enabled; },
    init() {
      enabled = readFlag();
      window.addEventListener('keydown', e => {
        if (e.ctrlKey && e.altKey && !e.shiftKey && (e.key === 'r' || e.key === 'R')) {
          e.preventDefault();
          window.DENDRAI_REDACT.toggle();
        }
      });
    },
  };
})();

window.DENDRAI_REDACT.init();
