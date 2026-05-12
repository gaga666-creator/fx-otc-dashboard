# Deployment Guide

This project deploys as two separate services:

- Backend: FastAPI service
- Frontend: Vite static app

The frontend must call only the backend API. It must not call third-party quote sources directly.

## Deployment Checklist

Backend can be deployed to Render, Railway, or Fly.io as a Python web service.

Required backend start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Required Playwright install step:

```bash
python -m playwright install chromium
```

Required backend environment:

```env
ENV=production
DATABASE_URL=sqlite:///./data/rates.db
OKX_PLAYWRIGHT_ENABLED=true
CORS_ORIGINS=https://your-frontend-domain.vercel.app
```

Optional backend environment:

```env
CMC_API_KEY=
REFRESH_STALE_SECONDS=900
```

Required frontend environment:

```env
VITE_API_BASE=https://your-backend-domain.example.com
```

Frontend build command:

```bash
npm run build
```

Frontend publish directory:

```text
dist
```

## Render Backend

1. Create a new Render Web Service.
2. Connect the repository.
3. Set the root directory to:

```text
backend
```

4. Set build command:

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

5. Set start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

6. Add environment variables:

```env
ENV=production
DATABASE_URL=sqlite:///./data/rates.db
OKX_PLAYWRIGHT_ENABLED=true
CORS_ORIGINS=https://your-frontend-domain.vercel.app
CMC_API_KEY=
```

7. Deploy.
8. Copy the Render backend URL for `VITE_API_BASE`.

SQLite note: Render filesystem persistence depends on service configuration. For production persistence, use a Render disk or move storage to a managed database later.

## Vercel Frontend

1. Create a new Vercel project.
2. Connect the repository.
3. Set root directory:

```text
frontend
```

4. Set build command:

```bash
npm run build
```

5. Set output directory:

```text
dist
```

6. Add environment variable:

```env
VITE_API_BASE=https://your-render-backend.onrender.com
```

7. Deploy.
8. After Vercel deploys, update backend `CORS_ORIGINS` to the Vercel frontend URL.
9. Redeploy or restart the backend if needed.

## Railway Backend Check

Railway can run the backend as a Python service.

Use backend as the service root. Set:

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set the same backend environment variables listed above.

SQLite note: Railway ephemeral storage may not persist across deploys unless configured. Use a volume or managed database if persistence matters.

## Fly.io Backend Check

Fly.io can run the backend in a container.

The image or Dockerfile must:

1. Install Python dependencies from `backend/requirements.txt`.
2. Run `python -m playwright install chromium`.
3. Start FastAPI with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Set Fly service port mapping to the app port. If using Fly volumes for SQLite, mount a volume and set `DATABASE_URL` to that mounted path.

## Production OKX Behavior

OKX uses Playwright Chromium. If Chromium cannot launch, the OKX page is blocked, or visible prices cannot be parsed, the API must return:

```text
status = unavailable
```

Do not show mocked OKX prices in production. The dashboard should remain usable with OKX unavailable.

## Admin Refresh Protection

`POST /api/admin/refresh` should not be exposed publicly without protection.

Recommended minimum protection before production exposure:

- Add a simple `ADMIN_REFRESH_TOKEN`.
- Require `Authorization: Bearer <token>` for refresh requests.
- Or protect the route at the platform/gateway layer.

The current API is intentionally unchanged. Add token enforcement before opening this endpoint to the public internet.

## Post-Deploy API Tests

Replace `BACKEND_URL` with the production backend URL.

```bash
curl https://BACKEND_URL/api/health
curl https://BACKEND_URL/api/rates/latest
curl -X POST https://BACKEND_URL/api/admin/refresh
curl https://BACKEND_URL/api/telegram/summary
```

Expected:

- `GET /api/health` returns backend status and source statuses.
- `GET /api/rates/latest` returns all quote fields and `derived`.
- `POST /api/admin/refresh` refreshes sources without failing the whole API when one source fails.
- `GET /api/telegram/summary` returns plain text.

## Frontend Verification

After deploying frontend:

1. Open the Vercel URL.
2. Confirm cards load from `/api/rates/latest`.
3. Confirm refresh calls `POST /api/admin/refresh`.
4. Confirm unavailable sources render as `--` with `unavailable`, not mock values.
5. Confirm mobile layout keeps cards in two columns where required.

