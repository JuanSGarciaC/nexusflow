import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts", "coverage", "e2e/.playwright"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // innerHTML y similares están prohibidos: React escapa por defecto y no hay HTML del usuario.
      "no-restricted-syntax": [
        "error",
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: "Prohibido: riesgo de XSS." },
      ],
    },
  },
);
