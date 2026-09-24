// UI spec §12.5 — forbidden-syntax enforcement + TS-aware linting.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactPlugin from "eslint-plugin-react";
import globals from "globals";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true }, projectService: false },
    },
    rules: {
      // §4.1: no unescaped HTML interpolation, ever.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML is forbidden (spec §4.1). Render text nodes.",
        },
      ],
      // §8.6: sonner only — no competing toaster libs.
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "react-hot-toast",
              message: "Use sonner (spec §8.6).",
            },
          ],
        },
      ],
      // Type-only constructs are fine unused at runtime (tsc checks locals).
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["src/**/*.tsx"],
    plugins: { react: reactPlugin },
    settings: { react: { version: "detect" } },
    rules: {
      "react/no-danger": "error",
      "react/jsx-no-target-blank": "error",
    },
  },
  {
    ignores: ["dist/**", "node_modules/**", "eslint.config.js"],
  },
);
