import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  transpilePackages: ["@advoxs/ui", "@advoxs/types"],
};

export default nextConfig;
