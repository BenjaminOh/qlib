/** @type {import('next').NextConfig} */
const nextConfig = {
  // Required for the production Dockerfile multi-stage build.
  // `next build` emits .next/standalone/server.js with bundled deps, so the
  // runtime image doesn't need node_modules.
  output: "standalone",
  reactStrictMode: true,
};

module.exports = nextConfig;
