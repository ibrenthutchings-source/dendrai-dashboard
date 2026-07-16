import React from 'react'

// Screen components in this codebase register themselves onto `window`
// as a side effect of module evaluation (no ES exports), and are consumed
// as bare global JSX identifiers. This wraps that pattern in React.lazy:
// `loader` performs the dynamic import(s), and once every module in the
// group has evaluated, the named global is guaranteed to be registered.
export function lazyGlobal(loader, globalName) {
  return React.lazy(() => loader().then(() => ({ default: window[globalName] })))
}
