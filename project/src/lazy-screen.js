import React from 'react'

// Screen components in this codebase register themselves onto `window`
// as a side effect of module evaluation (no ES exports), and are consumed
// as bare global JSX identifiers. This wraps that pattern in React.lazy:
// `loader` performs the dynamic import(s), and once every module in the
// group has evaluated, the named global is guaranteed to be registered.
//
// A chunk import can fail with "Failed to fetch dynamically imported
// module" when the tab was loaded before a newer deploy replaced the
// content-hashed asset files — the browser is still holding a reference to
// a chunk filename (e.g. scenario-analysis-<oldhash>.js) that no longer
// exists on the server. No amount of retrying within the same page load can
// fix that (the stale hash is baked into the already-loaded main bundle);
// only a full reload that re-fetches a fresh index.html resolves it. This
// is the standard Vite-recommended recovery: reload once, and remember that
// we already tried so a genuinely broken/missing chunk doesn't reload-loop
// forever.
function isStaleChunkError(err) {
  const msg = err?.message || String(err || '');
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed/i.test(msg);
}

const CHUNK_RELOAD_KEY = 'dendrai.chunkReload';

export function lazyGlobal(loader, globalName) {
  return React.lazy(() =>
    loader().then((mod) => {
      // A successful load clears the guard, so a *different* stale-chunk
      // incident later in the same long-lived tab can still trigger one
      // more reload instead of being permanently blocked by a past retry.
      sessionStorage.removeItem(CHUNK_RELOAD_KEY);
      return mod;
    }).then(() => ({ default: window[globalName] })).catch((err) => {
      if (isStaleChunkError(err) && !sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
        sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
        window.location.reload();
        // Never resolves — the reload is already in flight.
        return new Promise(() => {});
      }
      throw err;
    })
  )
}
