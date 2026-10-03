import type { NextConfig } from "next";

// Server-side address of the Python API on the compose network (never exposed to the browser).
const API_URL = (process.env.API_URL ?? "http://api:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Dev-only badge: the default (bottom-left) covers the phone tab bar and top-right covers
  // "Sign out"; top-left only overlaps the logo.
  devIndicators: { position: "top-left" },
  async rewrites() {
    return {
      // MCP clients (Claude Desktop / Claude Code) talk to /mcp on this app's public origin with
      // their own `Authorization: Bearer pak_...` header. The rewrite proxies the request as-is
      // (headers and streamed body), so the API authenticates the key itself. Session cookies
      // are NOT translated here: owner sessions are rejected on /mcp by design.
      beforeFiles: [
        { source: "/mcp", destination: `${API_URL}/mcp` },
        { source: "/mcp/:path*", destination: `${API_URL}/mcp/:path*` },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
