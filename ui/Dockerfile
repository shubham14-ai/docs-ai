# ── Stage 1: install deps ────────────────────────────────────────────────────
FROM node:22-alpine AS deps
WORKDIR /app

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
      npm ci --prefer-offline; \
    else \
      npm install; \
    fi

# ── Stage 2: build ───────────────────────────────────────────────────────────
# `FROM deps`, not a fresh node:22-alpine with `COPY --from=deps node_modules`:
# the copy would re-materialise ~160MB as a brand new layer, which then has to
# be exported and unpacked on every build. Inheriting reuses the layer as-is.
FROM deps AS builder

ARG NEXT_PUBLIC_API_URL=http://api:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY . .

# Ensure public/ exists so the COPY in the runner stage never fails
RUN mkdir -p public

RUN npm run build

# ── Stage 3: dev server ──────────────────────────────────────────────────────
# Not built by `docker compose -f docker-compose.yml`; the override selects it.
# node_modules stay baked in (inherited from `deps`, never copied), the override
# mounts only the source over the top, and `next dev` recompiles on change. Bind
# mounts from a Windows host do not deliver inotify events, so watching polls.
FROM deps AS dev

ENV NODE_ENV=development
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
ENV WATCHPACK_POLLING=true
ENV CHOKIDAR_USEPOLLING=true

# Only the source -- node_modules is already here, inherited from `deps`.
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]

# ── Stage 4: production runner ───────────────────────────────────────────────
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
# Next's standalone server binds to HOSTNAME, defaulting to the container's own
# hostname -- which `localhost` inside the container does not resolve to, so the
# healthcheck could never connect. Bind on all interfaces instead.
ENV HOSTNAME=0.0.0.0

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
