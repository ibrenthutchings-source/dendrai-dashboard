# Dendrai Risk Loop

This folder contains the `Dendrai Risk Loop` React app migrated from the original prototype.

## Development

Install dependencies and start Vite:

```bash
cd project
npm install
npm run dev
```

Open `http://localhost:5173` (or the port shown by Vite).

## Build

```bash
cd project
npm run build
```

The production build is emitted to `project/dist`.

## Preview

```bash
cd project
npm run preview
```

## Notes

- The legacy prototype file `project/Dendrai Risk Loop.html` is preserved as a reference.
- The app bootstrap is now `project/src/main.jsx` and the Vite entrypoint is `project/index.html`.
- Global helper modules still attach shared helpers to `window` for compatibility with the migrated code.
