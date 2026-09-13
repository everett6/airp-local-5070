FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# `output: "standalone"` (see next.config.mjs) means the build already
# traced and copied only the node_modules files actually needed at runtime
# into .next/standalone — so the final image doesn't need `npm install`,
# the full node_modules tree, or even the source files, just the traced
# output. Meaningfully smaller image and faster container start than
# copying the full node_modules/.next from the builder stage.
FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
