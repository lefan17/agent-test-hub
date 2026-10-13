// 提交规范：Conventional Commits
// 文档：https://www.conventionalcommits.org/zh-hans/v1.0.0/
//
// 本仓库的 CI 里有一个等价的 Python 校验步骤（scripts/check_commit_msg.py），
// 这里再配一份 commitlint，是为了展示「Node 生态工具链」的标准接法。
// 两处规则保持一致。
//
// 为什么是 .mjs 而不是 .js：
//   1. package.json 里没有 "type": "module"，.js 会按 CommonJS 解析，
//      而 wagoid/commitlint-github-action@v6 明确要求 ESM 配置（.mjs）。
//   2. 本文件的上一版是 .js，且开头用了 6 行 `#` 注释 —— 那是 Python/shell
//      风格注释，不是合法 JavaScript，任何 JS 解析器都会在第一个字符就报错。
//      那次事故的根因不是扩展名，而是「写完没有用任何 JS 解析器验证过」。
//      现在本文件可被 `node --check` 与动态 import 真实校验（见 CONTRIBUTING.md）。

export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // 与本仓库 git 历史里实际出现的 type 对齐，也与 scripts/check_commit_msg.py 的 TYPES 一致
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'docs',
        'style',
        'refactor',
        'perf',
        'test',
        'build',
        'ci',
        'chore',
        'revert',
      ],
    ],
    'scope-case': [2, 'always', 'lower-case'],
    // scripts/check_commit_msg.py 同口径：header 上限 72 字符，允许中文
    'header-max-length': [2, 'always', 72],
    'subject-case': [0], // 关闭：中文描述没有「大小写」概念
    'subject-full-stop': [0], // 关闭：同上，避免误伤中文句末
    'body-max-line-length': [0], // 关闭：中文技术说明常超过 100 字符
  },
};
