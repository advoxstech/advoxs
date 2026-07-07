import { baseConfig } from "@advoxs/config/eslint-preset.js";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const config = [
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  ...baseConfig,
  ...compat.extends("next/core-web-vitals"),
];

export default config;
