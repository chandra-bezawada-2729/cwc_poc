# CWC web interface: build the React app, serve it with nginx, proxy /api.

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
# npm ci is exact. If the lockfile was made on Windows and lacks a Linux native
# binding (a known npm optional-dependency bug), fall back to a clean install.
RUN npm ci --no-audit --no-fund \
 || (echo "npm ci failed - retrying with npm install" && rm -rf node_modules && npm install --no-audit --no-fund)
COPY . .
RUN npm run build

# ── Run stage ─────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
