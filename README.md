# 外匯 / 場外交易監控面板

React + Vite frontend and FastAPI backend for FX / OTC monitoring.

## Structure

```text
fx-otc-dashboard/
  backend/
    app/
      fetchers/
      main.py
      db.py
      models.py
    data/
    requirements.txt
    .env.production.example
  frontend/
    src/
    package.json
    .env.example
    .env.production.example
```

## Local Setup

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Build frontend:

```powershell
cd frontend
npm run build
```

## API

- `GET /api/rates/latest`
- `POST /api/admin/refresh`
- `GET /api/health`
- `GET /api/telegram/summary`

The frontend only calls backend APIs. It never fetches third-party quote sources directly.

## Frontend API Base URL

Vite reads the backend API host from `VITE_API_BASE`.

Local:

```env
VITE_API_BASE=http://127.0.0.1:8000
```

Production:

```env
VITE_API_BASE=https://your-backend-domain.example.com
```

If frontend and backend are served from the same origin, `VITE_API_BASE` can be left empty.

## Backend Production Env

See `backend/.env.production.example`.

```env
ENV=production
CMC_API_KEY=
DATABASE_URL=sqlite:///./data/rates.db
REFRESH_STALE_SECONDS=900
OKX_PLAYWRIGHT_ENABLED=true
CORS_ORIGINS=https://your-frontend-domain.vercel.app
```

`CMC_API_KEY` is optional. Without it, CMC USDT/USD returns `1.0000` with `fallback` status.

## CORS

Backend CORS is controlled by `CORS_ORIGINS`, a comma-separated list of allowed frontend origins.

Example:

```env
CORS_ORIGINS=https://fx-otc-dashboard.vercel.app,https://www.example.com
```

Do not use `*` for a public production dashboard if you later add admin protection.

## Backend Deployment

Render:

```bash
pip install -r backend/requirements.txt
python -m playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set root directory to `backend` if the platform supports it. Add production env vars from `backend/.env.production.example`.

Railway:

```bash
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Deploy the `backend` folder as the service root. Use a volume or external database if you need SQLite persistence across deploys.

Fly.io:

Use a Python image, install requirements, run `python -m playwright install chromium`, and start:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

For SQLite persistence, mount a Fly volume at the backend data path or switch `DATABASE_URL` to a persistent file path.

## Frontend Deployment

Vercel:

- Project root: `frontend`
- Build command: `npm run build`
- Output directory: `dist`
- Env: `VITE_API_BASE=https://your-backend-domain.example.com`

Netlify:

- Base directory: `frontend`
- Build command: `npm run build`
- Publish directory: `frontend/dist`
- Env: `VITE_API_BASE=https://your-backend-domain.example.com`

## Playwright Production Notes

OKX P2P uses Playwright. Install Chromium during backend build:

```bash
python -m playwright install chromium
```

Some serverless environments do not support long-running browser launches. If Chromium cannot launch or OKX blocks the page, the OKX quote returns `unavailable`; the dashboard should remain usable. Do not replace OKX with mock prices in production.

## Gold Source Notes

London gold tries WFBullion first. If the site returns a Cloudflare/security-verification page or no usable London gold row, the backend falls back to the free `https://api.gold-api.com/price/XAU` endpoint for XAU/USD mid price. That fallback does not provide bid/ask, so London gold buy/sell remain unavailable while mid price can still be converted to CNY/g. Treat this free endpoint as best-effort and monitor rate limits before production use.

## Admin Refresh Protection

`POST /api/admin/refresh` should be protected before public production exposure. The simplest option is an `ADMIN_REFRESH_TOKEN` checked through an `Authorization: Bearer ...` header or a gateway rule. This project currently keeps the API schema unchanged; add token enforcement before exposing the endpoint publicly.
