import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  transpilePackages: ["@advoxs/ui", "@advoxs/types"],
  devIndicators: false,
};

export default nextConfig;
