# Next.js — official bun image (Debian-based). Do NOT use the alpine variant;
# Bun's musl story is flaky.
FROM oven/bun:1

WORKDIR /app

# Dependency layer — cached unless the lockfile changes
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile

# App source
COPY . .

EXPOSE 3000
CMD ["bun", "run", "dev"]
