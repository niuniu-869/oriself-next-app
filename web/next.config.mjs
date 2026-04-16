/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // API proxy: avoid exposing backend URL to client; iframe still hits backend directly.
  async rewrites() {
    const internal = process.env.API_INTERNAL_URL || 'http://localhost:8000';
    return [
      {
        source: '/api/:path*',
        destination: `${internal}/:path*`,
      },
    ];
  },

  // Security headers
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
      {
        // Issue pages themselves may be framed for sharing
        source: '/issues/:slug',
        headers: [
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
        ],
      },
    ];
  },

  // Standalone output only when building for Docker.
  // Local `pnpm build && pnpm start` works with default output — no warning.
  // Docker sets BUILD_STANDALONE=1 (see web/Dockerfile) to emit .next/standalone.
  output: process.env.BUILD_STANDALONE === '1' ? 'standalone' : undefined,
};

export default nextConfig;
