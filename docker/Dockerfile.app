# Next.js: official bun image (Debian-based). Don't use the alpine variant; Bun's musl support is flaky.
FROM oven/bun:1

WORKDIR /app

# Dependency layer, cached unless the lockfile changes.
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile

# App source
COPY . .

# Dev runs as the image's `bun` user (uid 1000) so files written to the bind mount stay host-owned.
# .next must exist in the image: compose mounts an anonymous volume there, and Docker seeds it
# (including ownership) from the image. Without it the volume is root-owned and `next dev` fails.
RUN mkdir -p /app/.next && chown -R bun:bun /app
USER bun

EXPOSE 3000
# `bun install` first so the node_modules volume picks up deps added since the image was built.
CMD ["sh", "-c", "bun install && bun run dev --hostname 0.0.0.0 --port 3000"]
