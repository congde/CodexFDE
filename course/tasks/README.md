# FlowERP 行动营｜16 讲课程主线

本目录中的 16 张任务卡以 `docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md` 为唯一课程合同。每讲先对齐大纲中的核心内容、演示结果、课内增量和通过标准，再用 FlowERP 案例承载实作；案例不得取代大纲规定的本讲目标。

## 一条有先后关系的主线、两个可运行成果、一条学习证据线

- **方法主线**：L01～L04 先构造能消费 Spec、受控执行和运行最小 Eval 的 Workbench V0；L05 起继续升级 Harness、Loop、Graph、API 与反馈能力。
- **产品主线**：通过同一工作台逐讲交付 FlowERP——库存导出 → 幂等入库 → 可用库存 → 销售订单 → 原子预占 → 取消释放 → 订单状态 → 采购审批 → API/Web → 反馈改进 → 现场新需求。
- **学习证据线**：学生逐讲提交判断、代码、报告、失败修订、同伴复核和答辩证据，用来证明工作台能力由学生本人形成并能够迁移。

最终作品是“工作台驱动的电商 ERP 持续交付系统”。其中工作台必须以独立平台运行，拥有自己的进程、API、Web、项目注册表和数据目录；FlowERP 作为目标项目接入，不能反向承载工作台页面或数据库。不能为了讲工具另造脱离 FlowERP 的案例，不能让 ERP 退化为冻结夹具，也不能用 ERP 功能数量代替工作台能力与学生证据。

每张任务卡都必须在“项目主线与评价证据”中明确四项：**ERP 产品增量、工作台增量、二者因果、学生学习证据**。L05～L15 不得连续两讲让 ERP 产品状态不变；L16 必须现场交付此前未实现的小需求。

## 前沿做法怎样进入任务卡

逐讲依据见 [`docs/courses/16讲主线与前沿校准矩阵.md`](../../docs/courses/16讲主线与前沿校准矩阵.md)。矩阵是大纲的派生治理索引，不是第二份大纲：稳定基础可以进入跟跑线；当前增强只用于对照、挑战或迁移；快速演进的观察项不进入基础通过线。任何新工具都必须先说明它解决了本讲哪个现场问题、产生了什么工作台增量、留下了什么学生证据，否则移到补充阅读。

行动密度另按 [`docs/courses/16讲行动密度审计与优化记录.md`](../../docs/courses/16讲行动密度审计与优化记录.md) 执行。每张任务卡必须让学生亲手构造独有产物、主动触发失败、保留修订、接受陌生人复验并说明迁移边界；参考仓库成品或最终截图不能替代这些过程。

## 逐讲知识地图

| 讲 | 工作台增量 | FlowERP 产品增量/受保护状态 | 独有实作产物 | 本讲明确不教 |
|---:|---|---|---|---|
| 01 | 终局地图与接手协议 | ERP 基线快照，业务账不变 | 环境记录与接手报告 | 不把参考实现当学生成果 |
| 02 | 项目规则与 DoD | 固化四类领域边界，业务账不变 | 规则补丁与越界判决 | 不用文字规则代替事务和审批 |
| 03 | Spec Schema 与解析器 | 签字确认库存导出合同 | `FDE_SPEC.md` 与用例矩阵 | 不提前编码 |
| 04 | 受控执行、结果摘要、最小 Eval | 实现库存导出 | 能力信封、Diff、正反复验 | 不顺手扩范围 |
| 05 | 失败优先 Eval | 幂等入库 | 两次红灯、失败后状态、同命令绿灯 | 不删除裁判求绿 |
| 06 | Harness 判决和报告 | 可用库存口径 | Harness JSON 与退出码合同 | 不让 Harness 猜根因 |
| 07 | 本地 Hook | 销售订单创建 | Hook 证据包与订单正反路径 | 不让 Hook 代替业务守卫 |
| 08 | CI 与证据信封 | 原子预占 | A/B/C Run 与同伴法证 | 不把门禁修复和业务修复混为一次 |
| 09 | Repair Task | 取消释放预占 | `repair-task.json` 与受控修复 | 不启动多轮 |
| 10 | 有界 Loop | 合法订单状态迁移 | 收敛/未收敛 Loop History | 不把耗尽写成成功 |
| 11 | Subagents 调度 | 采购申请 | 冲突图、子报告、串行集成 | 不并行共享写集 |
| 12 | Graph 与具名人审 | 审批后入库 | Graph Trace 与 ERP 状态对账 | 不让模型自批 |
| 13 | Task API | API 提交补货需求并完成交付 | API 验收矩阵与任务证据链 | 不把 `202` 写成完成 |
| 14 | Web 交付驾驶舱 | ERP 操作页与证据下钻 | DOM/API/SQLite/ERP 对账 | 不用静态数据或假进度 |
| 15 | Feedback/Evolution | 反馈驱动的 ERP 改进 | 反馈、升级资产、独立验证任务 | 不让原始反馈直达裁判 |
| 16 | 冷启动与发布证据 | 现场抽取并交付新需求 | Release Evidence Index 与现场证据 | 不预演固定答案 |

## 依赖关系

每讲必须消费上一讲的具体产物，不允许只靠标题串联：

```text
环境检查与接手报告 → 项目规则 → 库存导出 Spec → Workbench V0/库存导出
→ 幂等入库 Eval → 可用库存 Harness → 订单 Hook → 原子预占 CI
→ 取消释放 Repair → 订单状态 Loop → 采购 Subagents → 审批 Graph
→ 补货 Task API → ERP/交付 Web → 反馈驱动增量 → 现场新需求
```

讲师若跳过依赖，必须明确补齐前置产物。例如 L09 没有真实 Harness 报告就不能开始，L14 没有可查询的 Task API 就不能用静态 JSON 代替。

## 唯一可执行课程入口

课件中的自然语言合同必须与 `workbench/course_mainline.py` 的机器合同一致。每讲开工先查看本讲合同；L03 起生成本讲局部 Spec，禁止把根目录中描述终态的 `FDE_SPEC.md` 当作每讲合同；L04 起只运行本讲登记的阻断 Eval，并通过课程任务入口提交真实交付。

**跟跑必做**入口是 `workbench.cli`、`eval.harness`、`agent.loop` / `agent.graph` 与 `web/`（FlowERP）。`harness_web/` 与 OPC「超级个体 + Agent 员工」是**可选挑战**，不是大纲 L01～L16 通过标准，不能替代具名人审与 Eval 证据。

```powershell
python -X utf8 -m workbench.cli course-contract --lesson 8
python -X utf8 -m workbench.cli course-spec --lesson 8
python -X utf8 -m workbench.cli course-eval --lesson 8
python -X utf8 -m workbench.cli course-submit --lesson 8 --execute-code
```

`course-submit` 强制在 `--execute-code` 与 `--verify-only` 之间显式选择。真实写入会从本讲 `course/lNN-start` 标签创建 detached Git Worktree，在隔离目录依次运行执行前 Eval、Codex 和执行后 Eval；只有“基线稳定红灯、发生范围内真实改动、候选转绿”才产生实现证据。标签缺失、基线已绿、零改动或越界写入都会拒绝或退回；`--verify-only` 只证明当前候选通过，不能证明学生完成了本讲实现。课程发布前还必须执行：

L15/L16 不允许用静态通用 Eval 冒充现场证据。先在会话分支提交本次反馈/抽题专属的失败 Eval，再把该提交作为会话基线：

```powershell
python -X utf8 -m workbench.cli course-spec --lesson 15 --eval-case feedback_rule_is_enforced
python -X utf8 -m workbench.cli course-submit --lesson 15 --execute-code --eval-case feedback_rule_is_enforced --session-baseline-ref refs/heads/l15-session-red
python -X utf8 -m workbench.cli course-eval --lesson 15 --case feedback_rule_is_enforced
```

逐讲基线和审核通过候选的发布生命周期使用以下受控入口；标签发布必须显式确认，候选提升只应用经过哈希校验的 Patch，且不会自动提交：

```powershell
python -X utf8 -m workbench.cli course-baseline-audit --lesson 8 --candidate-ref COURSE-COMMIT --evidence review.md
python -X utf8 -m workbench.cli course-baseline-publish --lesson 8 --candidate-ref COURSE-COMMIT --evidence review.md --confirm
python -X utf8 -m workbench.cli course-candidate-export TASK-ID
python -X utf8 -m workbench.cli course-candidate-promote TASK-ID --target-workspace D:\review\lesson-candidate
python -X utf8 -m workbench.cli course-worktree-clean TASK-ID
```

自动交付遇到阻断失败或受控执行失败时最多尝试 3 次；未收敛任务进入 `dead_letter`，保留每次尝试和最后失败证据，等待人工处理，不能伪装成完成。课程发布前还必须执行：

```powershell
python -X utf8 -m workbench.cli course-status --require-baselines
```

该命令要求存在 `course/l01-start` 至 `course/l16-start` 的逐讲起始标签。当前终态仓库不能替代这些基线；标签缺失时，材料只能标记“课程基线待建设”，不得声称已经可以完整渐进跟跑。

基线不得从同一个终态提交批量打标签；标签唯一性、线性历史、逐讲红绿合同和发布证据要求见 [`逐讲代码基线建设方案.md`](../../docs/courses/逐讲代码基线建设方案.md)。

## 每讲硬性验收

每张任务卡都必须回答六个问题：

1. 上一讲留下了什么可引用状态？
2. 本讲 FlowERP 的权威产品状态怎样变化，或保护了什么不变状态？
3. 该交付暴露并推动了哪个工作台增量？
4. 学员必须掌握哪三至五个可迁移知识点？
5. 正常路径、失败路径和失败后不变状态分别是什么？
6. 学员离开课堂后，能独立提交什么证据？

“理解概念”“掌握方法”“提升能力”不算验收。合格证据必须包含可复现输入、权威状态、预期结果、实际结果和稳定身份；课程通用口号不能冒充本讲知识点。

## 课程总验收命令

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m workbench.cli demo
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m workbench.feedback summary
```
