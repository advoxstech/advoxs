import { baseConfig } from "@advoxs/config/eslint-preset.js";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

export default [...baseConfig, ...compat.extends("next/core-web-vitals")];
