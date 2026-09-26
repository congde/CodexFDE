# L12｜用 Graph 显式表达状态、回退和人工审核

## 第一次打开，按这条路线走

让 Graph 等待、继续和回退，并把流程状态与采购业务事实对上。

**开始前准备：** 自己的工作台候选与采购环境。

| 顺序 | 打开什么 | 现在做什么，做到哪里再继续 |
|---|---|---|
| 1. 看懂本讲任务 | [辅导资料](辅导资料.md) | 先读开头的案例或总览，写下本讲要解决的问题；原理随实践回查 |
| 2. 开始操作 | [实践操作手册](实践操作手册.md) | 手册第 1～4 步：观察等待、批准入库和重复请求，写首次判断；分清软件审核与采购单审批 |
| 3. 完成本人实现 | [实践操作手册](实践操作手册.md) | 第 5～7 步：建设个人候选，验证状态转移与采购规则 |
| 4. 复验与交接 | [行动卡](行动卡.md)及手册末尾 | 第 8 步：对账同一候选，保留具名决定 |

**个人记录放哪里：** SUBMISSION.md；教学审核输入与真实人的决定分开记录。报告、截图和教师示例用于对照；个人结论填写实际观察，尚未复验就注明待复验。

## 附件什么时候用

| 文件或目录 | 用途与打开时机 |
|---|---|
| [辅导资料.md](辅导资料.md) | 主讲材料：解释案例、知识与判断方法 |
| [实践操作手册.md](实践操作手册.md) | 操作主入口：位置、命令、预期结果、失败处理和提交要求 |
| [assets/](assets/README.md) | 正文插图、截图与来源记录；随正文打开，不必逐张浏览 |
| [examples/](examples/) | 手册调用的实验、支架和参考源码；只运行当前步骤指定的入口，不逐个执行脚本 |
| [prompts/](prompts/README.md) | 进行到相应阶段时复制给 Codex；先填自己的路径、范围和事实 |
| [skills/](skills/) | 需要方法审查或复用时使用；保留整个 Skill 子目录，不只复制 SKILL.md |
| [slides/](slides/) | 跟课堂或复习时使用；多版本按授课指定版本选择，不必全部阅读 |
| [SUBMISSION.md](SUBMISSION.md) | 从实践开始就填写个人副本；保存首次判断、失败、修订和复验，不能把空模板当作成果 |
| [参考详解.md](参考详解.md) | 遇到原理、源码或历史案例疑问时查询；首次学习不用从头通读 |
| [行动卡.md](行动卡.md) | 开始实践或提交前快速核对目标与通过标准；详细操作看手册 |

## 本讲具体附件入口

下面只列主线中会用到的材料。其他脚本和图片保留在目录中，需要时按手册引用打开。

| 附件 | 何时使用 |
|---|---|
| [graph_control_lab.py](examples/graph_control_lab.py) | 观察等待、继续与回退；审核输入是教学替身 |
| [purchase_approval_lab.py](examples/purchase_approval_lab.py) | 核对审批、入库、重复请求和失败状态 |

## 遇到问题，回到哪个入口

- 看不懂业务规则或判断理由：回到辅导资料的对应案例。
- 不知道在哪输入、缺少文件、命令报错或中途重开窗口：回到实践手册的准备、排错或恢复步骤，先确认原会话与候选。
- 不知道某个脚本能否直接运行：先查手册是否调用它；参考实验与个人交付按手册区分。
- 同一主题有多份 PPT、图或历史记录：按课堂指定版本使用；首次自学以当前辅导资料和实践手册为主。

下载或移动附件时保留本讲目录结构；只拷贝一个 Markdown 文件可能导致配图、Prompt 或脚本路径失效。

[课程总入口](../../README.md) · [上一讲](../L11/README.md) · [下一讲](../L13/README.md)

---

## 按需参考：已有实验说明与资料记录

下面保留本讲已有说明，供查询实验机制、证据范围和课件记录。涉及历史实测、页数或同步状态的描述只对应原记录；当前学习顺序以上方导航为准。

本讲把研发过程中的移交、打回、等待和异常写成明确状态，并用采购审批后入库检验这些控制是否对应真实业务。研发交付审核接受的是软件候选，采购审批批准的是业务单据，两者不能合并。

配套资料：[辅导资料](辅导资料.md)、[实践操作手册](实践操作手册.md)，可按需查阅。

新版[辅导资料](辅导资料.md)已展开 Graph 的组成、执行器、运行轨迹与实际边界，配套[实践操作手册](实践操作手册.md)、[行动卡](行动卡.md)、[提交模板](SUBMISSION.md)、[三阶段提示词](prompts/README.md)和 [state-handoff-review Skill](skills/state-handoff-review/SKILL.md)。[29 页主线自学 PPT](slides/L12-用Graph显式表达状态回退和人工审核-主线自学版-29页-定稿.pptx)保留六章目录，以八幅 imagegen 方法图讲解 Graph，包含可编辑正文、实验表格、总结思考和 L13 衔接。讲义末尾提供新版页码对应；旧版课件和 Word 保持原文件。教学实验不替代个人真实交付或跨事项闭环。

### Graph 控制实验

在项目根目录激活 `.venv` 后运行：

```bash
python -X utf8 docs/courses/L12/examples/graph_control_lab.py wait
```

[graph_control_lab.py](examples/graph_control_lab.py)调用真实 `agent.graph`，报告与审核人输入明确为教学替身。它不调用 Codex 开发，也不产生真实人审。十六种模式均已运行：

| 模式 | 当前实际观察 |
|---|---|
| demo | 无持久化、无人审要求的演示策略自动 completed |
| wait / resume | 等待具名审核；再次运行保持等待，不重新检查 |
| approve | 从等待批准后 completed，不重新运行 Eval |
| reject | 返回 develop，再检查后重新等待 |
| reject-at-limit | 上限 1 时打回，轮数字段变为 2 后 stopped，未再检查 |
| blocking-failure | 三轮阻断失败后 stopped；未发生真实开发 |
| eval-exception | 检查抛异常，Graph 保存 failed 与错误 |
| empty-report | 合成空结果、零阻断数也进入等待；消费者缺完整校验 |
| unknown-state | 未知状态进入 failed |
| malformed-state | 读取阶段 JSONDecodeError 直接抛出 |
| save-error | 保存阶段 FileExistsError 直接抛出 |
| stale-approval | 修改教学候选标记后仍可批准，未绑定候选或重新检查 |
| approval-without-wait | 新运行传入批准不会直接批准，仍进入等待 |
| terminal-rerun | 已完成状态再次读取保持完成，不重新执行 |
| unchecked-move | 直接调用 move 可从 develop 跳到 completed，方法未校验边 |

实验包装退出 0 表示实际行为符合观察，不表示这些行为均满足最终交付设计。真实 Harness 自身拒绝空套件，empty-report 是消费者接口反例。教学审核人字符串不提供身份认证。

### 采购审批与入库实验

```bash
python -X utf8 docs/courses/L12/examples/purchase_approval_lab.py approved --report-path .runtime/l12-first/approved.json
```

[purchase_approval_lab.py](examples/purchase_approval_lab.py)使用真实服务、临时数据库和统一 Harness。A 原来在库／预占／可用为 10／2／8，PR-TARGET 申请七件，OTHER 和 PR-OTHER 应保留。

| 模式 | 实际观察 | Harness 退出 |
|---|---|---|
| approved | 具名字段审批后 received，库存 17／2／15，一条 +7 流水 | 0 |
| unapproved / rejected | ApprovalRequired，拒绝后五表不变 | 0 |
| blank-reviewer | ValueError，五表不变 | 0 |
| replay | 相同键再次请求，五表不变 | 0 |
| different-key | 已入库后换键再请求被拒绝，五表不变 | 0 |
| status-write-failure | 教学触发器阻止单据改 received；库存已为 17，单据仍 approved，原子性检查失败 | 1 |
| recovery-same-key | 同样故障后移除触发器，使用原键重试，补齐单据状态且不重复增加库存 | 0 |
| key-collision | 使用既有 opening 键，单据 received 但库存仍 10，状态对账失败 | 1 |

两个失败来自当前实际服务行为，保留报告供学习；本次未修复产品代码。恢复同键成功不能证明第一次操作具有原子性。完整讲义将这些结果用于推导状态对账、恢复与验收，而不以流程标签代替业务事实。

### 本轮更新与下载边界

已复跑 16 种 Graph 控制实验和 9 种采购实验，新增各阶段五表快照及[三张运行记录截图](assets/README.md)。讲义和实践手册已加入逐图解释。30 页新版 PPT 与两份 Word 已同步，并完成全部 48 页的逐页检查。截图是实际运行记录的排版截图，非工作台原生界面；教学控制输入与真实业务记录已分别标明。
