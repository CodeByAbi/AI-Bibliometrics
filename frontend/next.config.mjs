/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // bundle-02: lucide-react is imported via barrel in 8 files — let Next
  // split it into per-icon chunks instead of one shared module.
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
