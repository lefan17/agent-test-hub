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

// Dependabot 分组升级的固定句式，例如：
//   chore(deps): bump the langchain-stack group across 1 directory with 2 updates
//   chore(deps-dev): bump the commitlint group across 1 directory with 2 updates
//   ci(deps): bump the actions group across 1 directory with 6 updates
//
// 为什么要豁免它：
//   这条标题由 GitHub 生成，仓库无法控制它的长度。实测三个机器 PR 的 header
//   分别是 76（#18）、77（#19）、66（#20）字符，只有超过 72 的那两条撞上本仓
//   自定的 header-max-length。
//
// 为什么豁免边界是安全的（不是「给机器人开后门，顺带放松人工约束」）：
//   本仓库的 header-max-length = 72 是对**人**的约束（CONTRIBUTING.md 与
//   scripts/check_commit_msg.py 的 SUBJECT_MAX 同口径）。为了让机器人过关而
//   把它放宽到 80，等于对所有人工提交一起放松——那才是本末倒置。
//   而下述正则要求「分组升级」的完整句式与 "with N updates" 收尾，人工提交
//   写不出这个形状，所以它不会变成绕过长度检查的后门。
//
// 用法边界：commitlint 的 `ignores` 命中后该提交**整条跳过校验**（不只是跳过
// 长度规则）。这里接受这一点，因为句式已精确到只有 Dependabot 会产出；
// 用 @commitlint/lint 实测过 10 个样本：三条真实机器标题全部放行，而人工超长
// ASCII 标题、非法 type、大写 scope 仍然照常判失败。
const DEPENDABOT_GROUP_BUMP =
  /^(?:chore|ci)\(deps(?:-dev)?\): bump the [a-z0-9_-]+ group(?: across \d+ director(?:y|ies))? with \d+ updates?$/;

const isDependabotGroupBump = (message) =>
  DEPENDABOT_GROUP_BUMP.test((message || '').split('\n')[0].trim());

export default {
  extends: ['@commitlint/config-conventional'],
  // 命中该谓词的提交整条跳过校验（仅 Dependabot 分组升级的固定句式）。
  ignores: [isDependabotGroupBump],
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
