# Two-stage build, same idea as api/Dockerfile: Node is a build tool here, not a
# runtime. Node compiles the app, nginx serves the static files it produced, and
# node_modules never reaches the final image.

# --- stage 1: build the static bundle ----------------------------------------
FROM node:22-alpine AS build

# Corepack installs the exact pnpm named in package.json's `packageManager`
# field, so the image builds with the same version as a laptop does. The prompt
# has to be silenced explicitly — there is no TTY here to answer it.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

# Manifests first, so this layer (the slow one) is only rebuilt when the
# dependencies change rather than on every source edit. pnpm-workspace.yaml
# comes along because it holds pnpm's settings, not just workspace layout.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# --frozen-lockfile: install exactly the lockfile or fail. A build that quietly
# resolves a newer dependency than the one that was tested is not a build.
# Dev dependencies included — `pnpm build` type-checks with tsc before bundling.
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# --- stage 2: runtime --------------------------------------------------------
FROM nginx:alpine

# Replaces the default server block: SPA fallback plus the /api proxy.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# busybox wget is already in the image, so no extra package for the check.
# It asks "is nginx serving the shell?" — which is what `compose up --wait` needs.
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=5 \
    CMD ["wget", "--quiet", "--spider", "http://127.0.0.1/"]
