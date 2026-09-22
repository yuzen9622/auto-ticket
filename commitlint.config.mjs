import { findSensitiveTerms } from "./scripts/release/sensitive-terms.mjs"

/**
 * Conventional Commits 驗證，外加一條專案自己的敏感詞閘門。
 *
 * 版本號、CHANGELOG 與 Release PR 都由 release-please 從 commit type 推導，所以
 * type 寫錯的代價不是「格式不好看」，是發錯版號。敏感詞那條則是因為 repo 轉 public
 * 之後 commit 歷史追不回來——擋在寫進去之前是唯一有效的時機。
 */
export default {
  extends: ["@commitlint/config-conventional"],
  plugins: [
    {
      rules: {
        "subject-no-sensitive-terms": ({ subject, scope }) => {
          const hits = findSensitiveTerms(`${scope ?? ""} ${subject ?? ""}`)
          return [
            hits.length === 0,
            `commit subject 不得出現會外洩選擇器／風控細節的字詞：${hits.join("、")}。` +
              "改寫成使用者看得懂的效果描述，工程細節留在 docs/internal/（不進版控）。",
          ]
        },
      },
    },
  ],
  rules: {
    "subject-no-sensitive-terms": [2, "always"],
    "body-max-line-length": [0],
  },
}
