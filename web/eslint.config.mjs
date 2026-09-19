import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // 由 animate-ui registry 產生的第三方元件：照抄上游，不在本專案維護，
    // 用 React Compiler 規則去挑它的毛病只會製造與我們無關的紅燈。
    "components/animate-ui/**",
    "hooks/use-controlled-state.tsx",
    "hooks/use-data-state.tsx",
    "lib/get-strict-context.tsx",
  ]),
]);

export default eslintConfig;
