/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Smaller, self-contained output for the production Docker image — copies
  // only the files actually needed at runtime instead of the full
  // node_modules tree, which meaningfully shrinks the image and speeds up
  // container start (no full node_modules to load from disk).
  output: "standalone",
  // Explicit even though Next enables this by default in production builds
  // — documents the intent rather than relying on an implicit default.
  compress: true,
  // Drop the `X-Powered-By: Next.js` response header — no functional
  // benefit to advertising the framework on every response.
  poweredByHeader: false,
};

export default nextConfig;
