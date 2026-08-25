#!/usr/bin/env bash
# 把最新 reports/ 发布到 GitHub Pages（gh-pages 分支）
# 用法：bash scripts/publish_report.sh
set -e
cd "$(dirname "$0")/.."

export PATH="/c/Program Files/GitHub CLI:$PATH"

git switch main -q
git branch gh-pages 2>/dev/null || true

rm -rf .gh-pages-tmp
git worktree prune -q || true
git worktree add .gh-pages-tmp gh-pages

cd .gh-pages-tmp
git pull origin gh-pages -q || true
find . -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cp ../reports/report.html ../reports/report.json . 2>/dev/null || true
cp ../reports/screenshot_UI-*.png . 2>/dev/null || true

if [ ! -f index.html ]; then
  printf '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=report.html"><a href="report.html">打开测试报告</a>' > index.html
fi

git add -A
git -c user.name="AgentTest Hub" -c user.email="dev@example.com" commit -q -m "docs: 更新在线测试报告" || echo "（无变更，跳过提交）"
git push -q origin gh-pages
cd ..
git worktree remove .gh-pages-tmp
echo "✅ 已发布: https://lefan17.github.io/agent-test-hub/report.html"
