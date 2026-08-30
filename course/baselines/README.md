# 逐讲起始基线（course/lNN-start）

本目录说明如何审计并发布 `course/l01-start` … `course/l16-start`。  
这些标签服务**教学复现**（红→绿），不是国家级一流本科课程申报材料，也不得伪造申报资格。

## 原则

1. **禁止**用当前终态 `HEAD` 给 16 讲批量打同一个（或“已经全绿”的）标签。
2. 标签必须落在**侧分支**上的线性祖先链（推荐分支名 `course/baselines`），不改写 `main` 历史、不做 force-push。
3. L04 起：候选提交对本讲登记 Eval 必须**红**；对上一讲登记 Eval 必须**绿**（L15/L16：静态合同绿，动态红延期到 session baseline）。
4. 每讲发布须附具名人工证据文件（见 `evidence/`），CLI 会计算 SHA256 写入 annotated tag。
5. `course/baselines/PROGRESSION.json` 是课程起始态的红绿门闩：缺省文件表示终态全能力已交付；基线提交中该文件按讲递增 `enabled`。不得在终态主线冒充“尚未交付”。

## 推荐顺序

```text
audit →（人工确认报告）→ publish --confirm
```

单讲包装：

```powershell
python -X utf8 scripts/publish_lesson_baseline.py --lesson 4 --candidate-ref <sha> --evidence course/baselines/evidence/L04-baseline-review.md
```

等价于：

```powershell
python -X utf8 -m workbench.cli course-baseline-audit --lesson 4 --candidate-ref <sha> --evidence course/baselines/evidence/L04-baseline-review.md
python -X utf8 -m workbench.cli course-baseline-publish --lesson 4 --candidate-ref <sha> --evidence course/baselines/evidence/L04-baseline-review.md --confirm
```

批量构建线性提交并发布（侧分支，需本地确认）：

```powershell
python -X utf8 scripts/build_course_baselines.py --publish --confirm
```

## 证据要求

`evidence/LXX-baseline-review.md` 至少包含：

- 审核人姓名或工号
- 日期
- 候选 commit SHA（发布前可先写占位，审核时补齐）
- 对本讲「应为红 / 上一讲应为绿」的简要确认
- 明确声明：本证据仅用于课程基线，不构成申报成效证明

模板：[`evidence/LXX-baseline-review.md`](evidence/LXX-baseline-review.md)

## 诚实边界（深度优化）

| 项 | 承诺 | 不承诺 |
| --- | --- | --- |
| `course_ready` | 本地 16 个起始标签线性、互异，且通过 audit 门禁 | 远端已推送；陌生人零配置开课 |
| `PROGRESSION.json` | L04+ 起始红 / 上一讲绿的**可复现门闩** | 已按真实 `flowerp` 缺能力切片重写 16 讲产品史 |
| 终态 `HEAD` | 不提交 `PROGRESSION.json`（或全能力已交付） | 可用终态冒充某一讲起始标签 |
| OPC / `harness_web` | 可选挑战与驾驶舱 | 大纲 L01～L16 通过标准 |
| 国家级一流本科 | 建设母版与诚信红线已写入 | 已具备申报资格或已认定 |

开课前讲师清单：

1. `python -X utf8 -m workbench.cli course-status --require-baselines` 退出 0  
2. `python -X utf8 -m unittest tests.test_course_outline_alignment tests.test_progression tests.test_course_mainline tests.test_course_release -q`  
3. 向学员说明：跟跑必做入口是 `workbench.cli` / `eval` / `agent.*` / `web/`；基线红绿由标签 + progression 门闩保证  
4. 若克隆新环境：先取得含标签的仓库，再检查 `course_ready`

## 一期 / 二期边界

| 阶段 | 范围 | `course-status --require-baselines` |
| --- | --- | --- |
| 一期 | 工具链 + 叙事 + **L01–L03** 标签 | 仍缺 L04–L16 时 `course_ready=false`（诚实） |
| 二期 | L04–L16 真渐进红绿态（`PROGRESSION.json` 门闩） | 全绿且 `course_ready: true` |

当前仓库已在 `course/baselines` 祖先链发布 L01–L16 起始标签并合并进 `main`；用 `course-status --require-baselines` 复核，勿用终态 HEAD 冒充起始态。

## 跟跑入口提醒

- **必做**：`workbench.cli`、`eval.harness`、`agent.loop` / `agent.graph`、`web/`
- **可选**：`harness_web/`、OPC Agent 员工（非大纲通过标准）
