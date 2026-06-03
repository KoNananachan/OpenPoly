# openPoly frontend

React + React Flow canvas UI for the openPoly strategy pipeline. Built with Vite
+ TypeScript.

For the full picture — what openPoly is, how to run the backend, and the
default (same-machine) vs separated deployment models — see the
[repository README](../README.md).

## Local dev

The frontend is a Vite dev server that proxies API calls to the backend.

```bash
# from frontend/
yarn install
VITE_API_PROXY_TARGET=http://127.0.0.1:18000 yarn dev
```

`VITE_API_PROXY_TARGET` points the dev server at your backend. In the default
same-machine setup that is the local backend on `127.0.0.1:18000`; for the
geoblock / separated-deployment setup it points at your SSH tunnel. See
[`docs/deploy/`](../docs/deploy/) for both.

## Scripts

| Command | What it does |
|---|---|
| `yarn dev` | Start the Vite dev server (HMR) |
| `yarn build` | Type-check (`tsc -b`) + production build |
| `yarn typecheck` | Type-check only, no emit |
| `yarn lint` | ESLint |
| `yarn format` | Prettier write |

## Layout

`src/sections/` mirrors the backend `openpoly/sections/` by name — each strategy
section's canvas node lives alongside its backend impl's namesake folder.
