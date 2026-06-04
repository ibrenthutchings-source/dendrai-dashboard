# Dendrai App

This monorepo app is a React + Vite frontend paired with an Express backend proxy for Gemini API requests.

## Overview

- Frontend: `dendrai-app/src/App.jsx`
- Backend proxy: `dendrai-app/server/index.js`
- Vite dev server proxies `/api` to the backend
- Production build can be served from Express using the generated `dist` folder

## Development

1. Install dependencies

```bash
cd dendrai-app
npm install
```

2. Create a `.env` file from `.env.example`

```bash
cp .env.example .env
```

3. Set your Gemini API key in `.env`

```env
GEMINI_API_KEY=your_google_generative_language_api_key_here
GEMINI_MODEL=gemini-2.5-flash
VITE_API_BASE_URL=http://localhost:4000
```

4. Run the backend proxy

```bash
npm run backend
```

5. In a second terminal, run the Vite dev server

```bash
npm run dev
```

Your frontend will send requests to `/api/gemini`, and Vite will proxy them to the backend.

## Production

1. Build the frontend

```bash
npm run build
```

2. Start the backend

```bash
npm run start
```

3. Open `http://localhost:4000`

The backend serves the built frontend in production mode and also handles `/api/gemini`.

## Docker

This repo includes a production-ready Docker setup for the app.

### Build the Docker image

```bash
docker build -t dendrai-app .
```

### Run the Docker container

```bash
docker run -p 4000:4000 \
  -e GEMINI_API_KEY="your_google_generative_language_api_key_here" \
  -e GEMINI_MODEL="gemini-2.5-flash" \
  dendrai-app
```

The app will be available on `http://localhost:4000`.

### Docker Compose

```bash
docker compose up --build
```

This starts the app on port `4000` and can read environment variables from a `.env` file.

## Environment variables

- `GEMINI_API_KEY` — required for server-side Gemini proxy requests
- `GEMINI_MODEL` — optional, defaults to `gemini-2.5-flash`
- `VITE_API_BASE_URL` — optional Vite proxy target for local development

## Notes

- Keep `GEMINI_API_KEY` out of source control.
- In production, prefer environment variables managed by your deployment platform.
- The server uses Express to proxy Gemini requests and, in production, serves static frontend files.
