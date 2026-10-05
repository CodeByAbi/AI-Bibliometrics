/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // bundle-02: lucide-react is imported via barrel in 8 files — let Next
  // split it into per-icon chunks instead of one shared module.
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
};

export default nextConfig;
