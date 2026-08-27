# Rocky Web

React/Vite frontend for Rocky. The web app calls the backend through relative
`/api` paths so development, device HTTPS, PWA preview, and future native
packaging can keep provider secrets server-side.

## Run Locally

Start the API first:

```bash
cd /Users/adityamachiraju/rocky-platform/apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then start Vite:

```bash
cd /Users/adityamachiraju/rocky-platform/apps/web
npm run dev
```

Vite proxies `/api` to `http://localhost:8000`.

## Verification

```bash
cd /Users/adityamachiraju/rocky-platform/apps/web
npm run test
npm run lint
npm run build
```

## Device HTTPS

Use the M4 device QA runbook for certificate setup:

```bash
open /Users/adityamachiraju/rocky-platform/docs/M4_REAL_DEVICE_QA.md
```

With certificate paths configured:

```bash
export ROCKY_DEV_HTTPS_CERT="$HOME/.config/rocky/certs/device.pem"
export ROCKY_DEV_HTTPS_KEY="$HOME/.config/rocky/certs/device-key.pem"
npm run dev:device
```

For installability and service-worker testing, use:

```bash
npm run preview:device
```

## PWA Notes

The manifest, icons, standalone mode, `viewport-fit=cover`, and service worker
are part of Rocky's web fallback. The service worker precaches static assets
only; `/api` is denied from navigation fallback and has no runtime cache.
