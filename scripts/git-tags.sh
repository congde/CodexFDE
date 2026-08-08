#!/usr/bin/env bash
# 为《Codex+FDE 行动营》每讲创建一个 git tag（tag 名 = 课程名）。
# 学员通过 `git checkout <tag>` 获取对应讲次内容。
#
# 用法（在本仓库根目录运行）：
#   bash scripts/git-tags.sh            # 依次创建全部 tag
#   bash scripts/git-tags.sh --list     # 仅列出课程名与 tag 的对应关系，不创建
#
# 说明：
#   - tag 递增依赖：每讲完成一次 commit 后，再创建对应 tag（ensures 每个 tag 指向该讲内容就绪的提交）。
#   - 若想全部 tag 指向当前 HEAD（占位），加参数 --all-head。
set -euo pipefail

# 课程名（tag 名） → 讲次
TAGS=(
  "L00-开篇-不能替你承担工程责任"
  "L00-导读-先看一套完整成品"
  "L01-认清边界-人驱动AI-vs-FDE工程化"
  "L02-用AGENTS.md沉淀记忆与约束"
  "L03-把模糊需求变成可验收Spec"
  "L04-用Spec驱动Codex完成CRUD"
  "L05-用Hooks搭起AI产出门禁"
  "L06-用MCP打通外部数据源"
  "L07-给AI建立Eval考题库"
  "L08-CICD无人值守批量开发"
  "L09-搭建Sub-Agents虚拟团队"
  "L10-用LangGraph编排工作流"
  "L11-Eval驱动评审打回修正闭环"
  "L12-生产级成本安全监控治理"
  "L13-Codex运行时封装与调度"
  "L14-多渠道自动产出一键生成"
  "L15-给仓库配个问答Bot"
  "L16-云端部署与反馈采集飞轮"
  "L17-结束语-学会委托下一次需求"
)

if [[ "${1:-}" == "--list" ]]; then
  echo "# 课程名 (git tag) → 讲次"
  for t in "${TAGS[@]}"; do
    echo "  ${t}"
  done
  exit 0
fi

if command -v git >/dev/null 2>&1; then
  if [[ ! -d .git ]]; then
    echo "[提示] 当前目录不是 git 仓库，请先: git init（或 clone）。"
    exit 1
  fi
else
  echo "[提示] 未检测到 git，请先安装并确保 git 在 PATH。"
  exit 1
fi

for t in "${TAGS[@]}"; do
  if git rev-parse "$t" >/dev/null 2>&1; then
    echo "[跳过] tag 已存在：${t}"
    continue
  fi
  git tag -a "$t" -m "课程：${t}"
  echo "[创建] ${t}"
done

echo ""
echo "完成。学员获取某讲内容："
echo '  git checkout "L05-用Hooks搭起AI产出门禁"'