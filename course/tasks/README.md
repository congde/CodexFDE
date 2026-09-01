# FlowERP 行动营｜16 讲课程主线

本目录中的 16 张任务卡以 `docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md` 为唯一课程合同。每讲先对齐大纲中的核心内容、演示结果、课内增量和通过标准，再用 FlowERP 案例承载实作；案例不得取代大纲规定的本讲目标。

## 从 PPT 决策进入 VS Code 行动

课程统一遵循 [`三载体课程实施方案`](../../docs/courses/三载体课程实施方案.md)：PPT 引导决策，Markdown 承载教材，VS Code 完成行动。学生先在对应 PPT 决策页提交第一次判断，再阅读对应讲义；只有形成签字版合同、允许写集和失败语义后，才进入本目录的行动卡。

用 VS Code 打开 [`FlowERP-AI研发工作台.code-workspace`](../FlowERP-AI研发工作台.code-workspace)，通过“终端 → 运行任务”执行统一入口。每讲至少依次使用“查看本讲合同”“生成本讲 Spec”（L03 起）和“运行本讲 Eval”（L04 起有登记用例时）；“复验已有候选”只验证终态，不计作学生实现证据。真实红→绿仍须按任务卡使用 `course-submit --execute-code`、逐讲基线和受控 Worktree，不能用按钮隐藏授权边界。

行动结束后回到 Markdown 完成第二次签字，把首次判断、失败、Diff、复验、同伴反馈和迁移说明放入统一提交包。只交 PPT 投票、最终代码或绿色截图都不构成一讲的完整学习证据。

## 一条有先后关系的主线、两个可运行成果、一条学习证据线

- **方法主线**：L01～L04 先构造能消费 Spec、受控执行和运行最小 Eval 的 Workbench V0；L05 起继续升级 Harness、Loop、Graph、API 与反馈能力。
- **产品主线**：通过同一工作台逐讲交付 FlowERP——库存导出 → 幂等入库 → 可用库存 → 销售订单 → 原子预占 → 取消释放 → 订单状态 → 采购审批 → API/Web → 反馈改进 → 现场新需求。
- **学习证据线**：学生逐讲提交判断、代码、报告、失败修订、同伴复核和答辩证据，用来证明工作台能力由学生本人形成并能够迁移。

最终作品是“工作台驱动的电商 ERP 持续交付系统”。其中工作台必须以独立平台运行，拥有自己的进程、API、Web、项目注册表和数据目录；FlowERP 作为目标项目接入，不能反向承载工作台页面或数据库。不能为了讲工具另造脱离 FlowERP 的案例，不能让 ERP 退化为冻结夹具，也不能用 ERP 功能数量代替工作台能力与学生证据。

每张任务卡都必须在“项目主线与评价证据”中明确四项：**ERP 产品增量、工作台增量、二者因果、学生学习证据**。L05～L15 不得连续两讲让 ERP 产品状态不变；L16 必须现场交付此前未实现的小需求。

备课与发布还必须通过 [`docs/courses/16讲系统化精修蓝图.md`](../../docs/courses/16讲系统化精修蓝图.md) 审计。该蓝图不改变大纲合同，只把 16 讲统一检查为“入口冲突 → 学生动作 → ERP/工作台双出口 → 主动失败 → 个人证据 → 下一讲交接”。基础线之外的迁移或挑战成果不能补偿基础任务缺失。

学生进入任务卡前，先阅读对应详细讲义的“图文学习导航”。讲义发布使用 [`教材化与图文升级规范`](../../docs/courses/教材化与图文升级规范.md)；备课引用使用 [`外部优秀资料与逐讲阅读地图`](../../docs/courses/外部优秀资料与逐讲阅读地图.md)。外部资料只负责校准方法和技术语义，最终得分仍来自本仓库可复现的首次判断、失败、修订和迁移证据。

每张任务卡都发生在 [`FlowERP连续案例叙事圣经`](../../docs/courses/FlowERP连续案例叙事圣经.md) 的同一项目时间线上。学生必须先提交案例中的“决策时刻”，再领取命令卡；不得根据后文参考实现倒填首次判断。故事人物和公司为教学叙事，不得写成真实企业事故或真实教学成效。

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

## 每讲统一提交包

任务卡中的专属产物不同，但提交包结构统一。学生不得只交最终截图或把参考仓库运行结果当作个人作品。

```text
lesson-NN-submission/
├── 01-first-judgement.md      # 未看答案前的需求、风险或失败判断
├── 02-contract/               # 本讲 Spec、写集、用例或子任务合同
├── 03-failure/                # 主动触发的红灯、退出码和失败后权威状态
├── 04-change/                 # commit/diff 与范围说明
├── 05-verification/           # 同一入口绿灯、报告 ID、状态引用
├── 06-peer-feedback.md        # 具名同伴反证或教师反馈
├── 07-revision.md             # 修订前后差异、采纳/拒绝理由
├── 08-transfer.md             # 换参数、换同构需求或陌生项目的迁移结果
└── 09-authorship.md           # AI、同伴和本人贡献及剩余风险
```

`09-authorship.md` 至少回答：AI 用于哪些环节；哪些建议被拒绝及原因；本人修改了什么；哪条命令可推翻“已经完成”的结论；当前仍未证明什么。组队任务必须把个人贡献映射到稳定证据 ID，不能用小组总分推断每名学生达成。

每项核心任务允许一次基于证据的修订。首次版本与二次版本必须同时保留：首次版本用于识别真实误区，二次版本用于评价学习结果；覆盖原文件、清理失败历史或只提交最终绿色状态均退回。

## 通用四维量规

| 维度 | 权重 | 达成表现 | 硬性退回 |
|---|---:|---|---|
| 判断与合同 | 25% | 能说明价值、范围、非目标、风险和可执行验收 | 未判断需求即直接编码，或关键边界不可验证 |
| 实现与业务正确性 | 30% | 最小 Diff；正常、失败和失败后不变状态正确 | 违反库存/幂等/状态机/采购审批规则或越界写入 |
| 验证与证据 | 30% | 输入、预期、实际、退出码/权威状态和证据 ID 齐全 | 模型自述代替运行，删失败，红灯宣称成功 |
| 协作与责任 | 15% | 来源、分工、人审、AI 使用和剩余风险可追溯 | 匿名审批、无法说明作品来源、提交凭据或敏感数据 |

具体任务卡可为本讲独有能力增加评分锚点，但不能降低上述共同底线。

## 课程总验收命令

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m workbench.cli demo
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m workbench.feedback summary
```
