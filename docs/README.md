# Codex AI 工程交付行动营｜文档总览与学习入口

**用 Codex 搭建个人 AI 研发工作台，再通过工作台组织人与 AI 协同，持续开发 FlowERP。**

学完要交付两项成果：自己的研发工作台，以及通过它开发并留下证据的 FlowERP。你负责范围、授权和验收，Codex 协助调查、实现与修复，工作台组织任务并保存过程。

## 现在从哪里开始

- **第一次学习**：从 [L00 课前准备](./courses/L00/README.md)核验环境，再进入 [L01](./courses/L01/README.md)使用课程已提供的项目壳。想练习从 0 搭建，可另建新工作台项目，按[参考选做文档](./courses/L00/从0开始搭建个人AI研发工作台.md)与 Codex 一起设计、实现并复验。
- **正在跟课**：在 [16 讲学习路线](#16-讲学习路线)找到当前讲，沿原任务和记录继续。
- **查设计、排错或备课**：进入 [docs 完整介绍](#docs-完整介绍)，按问题选择资料。

## 工作台为什么逐讲增长

![先建工作台，再通过工作台交付 FlowERP](./courses/assets/readme-guide-20261005/01-build-workbench-deliver-flowerp.png)

**图左是建设工具，图右是用工具交付产品。** L00 先完成环境准备。课程已提供页面壳，也可参考选做文档从 0 搭建一个新工作台项目；L01～L03 沿选定的同一项目和入口建立账本、规则、Spec 与解析能力；L04 补齐并验收工作台 V0，再由它组织首次 FlowERP 库存导出。

之后，每次产品交付都会暴露新的问题：收货重试可能重复记账，取消订单可能不释放库存，采购入库必须等待批准。工作台因此补上检查、返工、分工和审核能力，再用下一次 FlowERP 交付检验是否有效。

<details>
<summary>查看精确关系图与可编辑源文件</summary>

![Codex、个人研发工作台与 FlowERP 的关系](./courses/assets/course-three-layer.svg)

沿“Codex 帮你建工作台 → 工作台组织 FlowERP 交付 → 产品结果推动工作台改进”阅读。人负责范围、授权和接受决定。[可编辑源图](./courses/assets/course-three-layer.drawio)

</details>

<a id="l13-l16-access"></a>

## 16 讲学习路线

每次只进入当前讲的 README。**辅导资料解释为什么，实践手册说明怎么做，提交模板索引你自己的证据。** 下方四组图先说明能力怎样接起来，再用表格找到本讲入口；读完本讲，应能说出它比上一讲多解决了哪个问题。

### L01～L04：建起工作台，完成首次交付

从课程提供的壳或选做搭建并复验的壳继续，先完成账本与自举，再把协作规则、需求合同和执行能力补齐；L04 通过自己的工作台交付库存导出。课程提供的 `starter/` 是起步支架，L01 的本人 Spec、红灯测试、实现、复验与自举仍须实际完成。

![L01 到 L04 从首次工作台建设走到工作台组织库存导出](./courses/assets/lesson-mainline-20261006/01-first-delivery.png)

**按图从左读到右：**L01 保存首次建设与自举记录，L02 让新会话遵守仓库规则，L03 将需求合同做成模板和解析器，L04 补齐受控执行并组织首次 ERP 交付。后面的讲次继续使用这些能力。

| 讲次 | 主题与本讲入口 |
|---|---|
| L01 | [以终为始：一次可验证的 AI 交付怎样完成？](./courses/L01/README.md) |
| L02 | [把仓库规则写进 `AGENTS.md`](./courses/L02/README.md) |
| L03 | [把模糊需求变成可验收 Spec](./courses/L03/README.md) |
| L04 | [委托 Codex 执行一次最小变更](./courses/L04/README.md) |

### L05～L08：让检查可信

重复收货、库存口径和订单预占先后暴露四个问题：检查漏错、汇总误报、忘记运行、别人无法复验。

![L05 到 L08 分别解决检查辨识力、可信汇总、本地触发和远程复验](./courses/assets/lesson-mainline-20261006/02-trust-the-checks.png)

**图中检查卡一直被复用。** L05 设计能抓错的 Eval，L06 将分项汇总成可信报告，L07 在 Codex 的实际生命周期节点调用它，L08 再让 CI 检查明确版本的同一候选。继续跟课时，先核对原检查确实已接入本讲入口。

| 讲次 | 主题与本讲入口 |
|---|---|
| L05 | [先设计失败，再编写 Eval](./courses/L05/README.md) |
| L06 | [用 Harness 汇总证据和等级](./courses/L06/README.md) |
| L07 | [用 Codex Hooks 建立本地护栏](./courses/L07/README.md) |
| L08 | [把同一套 Eval 接入 CI](./courses/L08/README.md) |

### L09～L12：控制返工、分工与审核

有了可信失败，下一步需要决定改哪里、是否继续、怎样分工，以及何时停下来等人决定。

![L09 到 L12 从限定修复任务走到有界返工、独立分工和可恢复的人审状态](./courses/assets/lesson-mainline-20261006/03-control-repair-and-review.png)

**每格对应一种新决定。** L09 形成可授权的修复任务，L10 控制重复尝试，L11 组织互不冲突的子任务，L12 保存等待、打回和恢复状态。软件交付审核与采购批准分别留下记录。

| 讲次 | 主题与本讲入口 |
|---|---|
| L09 | [把失败报告翻译成修复任务](./courses/L09/README.md) |
| L10 | [建立有停止条件的修复 Loop](./courses/L10/README.md) |
| L11 | [用 Codex 原生 Subagents 并行处理独立任务](./courses/L11/README.md) |
| L12 | [用 Graph 显式表达状态、回退和人工审核](./courses/L12/README.md) |

### L13～L16：让别人能使用、接手和继续交付

已有执行和审核链，还要让别人能提交、看懂、继续使用，并在条件改变后完成新的交付。

![L13 到 L16 从持久化任务接口走到页面使用、反馈改进和陌生环境迁移](./courses/assets/lesson-mainline-20261006/04-use-feedback-and-transfer.png)

**跟随图中的同一任务身份。** L13 让提交和查询有稳定入口，L14 将真实状态呈现在页面，L15 让反馈驱动新任务并检验经验复用，L16 换人换环境，用这套链路交付一个此前未实现的小需求。

| 讲次 | 主题与本讲入口 |
|---|---|
| L13 | [把执行链路封装成任务 API](./courses/L13/README.md) |
| L14 | [让交付状态在 Web 面板可见](./courses/L14/README.md) |
| L15 | [生成交付摘要并采集真实反馈](./courses/L15/README.md) |
| L16 | [在新环境接手，并完成现场新需求](./courses/L16/README.md) |

## 怎样判断可以进入下一讲

![一讲从首次判断走到操作、复验、人审和独立迁移](./courses/assets/readme-guide-20261005/02-complete-a-lesson.png)

**沿主箭头完成本讲；复验失败或人审退回，就带着原错误回到操作。** 手册给出目标、操作位置、预期结果、失败处理和完成判断。原先就正确的行为如实记录，不伪造红灯。

完成时核对三件事：

1. **能复验**：保存本人实际输出、退出码、失败后状态、修订和范围内 Diff；工作台检查与 FlowERP 业务检查分别核对。
2. **有人接受**：提交模板能定位当前候选和证据，由具名人员决定接受或退回。
3. **能独立迁移**：换一个条件或小需求，自己解释并完成方法调整。

缺哪一项，就回到本讲续做。参考实现、绿色截图或 Codex 的完成声明，不能代替这些记录。

<details>
<summary>查看完整实践闭环图与源文件</summary>

![每讲从首次判断走到复验、审核与独立迁移](./courses/assets/student-lesson-loop.svg)

检查通过、具名接受、独立迁移分别核对。[可编辑源图](./courses/assets/student-lesson-loop.drawio) · [PNG](./courses/assets/student-lesson-loop.png)

</details>

工作台建成后，从 [http://127.0.0.1:8001/](http://127.0.0.1:8001/) 首页“事项与决策”继续组织交付，启动步骤跟当前讲手册。

## docs 完整介绍

![按问题选择课表与大纲、courses、reference 或 architecture](./courses/assets/readme-guide-20261005/03-choose-docs.png)

**四类资料各回答一个问题。** 课表与大纲确定要求，`courses/` 带你实践，`reference/` 帮你查设计与操作，`architecture/` 用来核对建设规格和缺口。具体入口都在下面。

本页配图为阅读与机制示意，来源见[公共教学资源](./courses/assets/README.md)；实际操作、运行结果和学生完成情况，以相应材料和本人证据为准。

### 核对课程要求：课表、大纲与蓝图

| 要核对什么 | 打开哪份资料 |
|---|---|
| 16 讲正式主题与授课安排 | [课表](./课表｜Codex%20AI%20工程交付行动营.md) |
| 核心内容、演示结果、课内增量和通过标准 | [课程大纲](./课程大纲-Codex-FDE行动营-个人研发自动化工作台.md) |
| 教师怎样安排活动、评价与持续改进 | [课程蓝图](./courses/课程蓝图.md) |

课表和大纲确定课程要求，蓝图将其转成教学设计。真实课堂实施、学生记录和改进效果仍需实际采集。

### 跟课与实践：courses

**当前讲材料。** `courses/L00/` 完成环境准备，并提供从 0 搭建的参考选做文档；`L01/`～`L16/` 沿选定项目建设正式能力。按“本讲 README → 辅导资料 → 实践手册 → SUBMISSION”继续。L00 的环境记录模板在 `labs/L00/`，已提供的壳在 `L00/starter/`，由 L01 手册中的准备工具生成到空的个人目录；L01～L16 的现行提交要求从各讲入口取得。

**实践中需要的配套。** 只在手册对应步骤打开，不另走一条学习路线：

- [labs 实验配套](./courses/labs/)：提示词、提交模板和 [修复输出结构](./courses/labs/repair-output.schema.json)。
- 各讲 `examples/`、`tools/`、`scripts/` 与技能目录：样例、实验程序和辅助工具。例如 [L01 证据工具](./courses/L01/tools/)、[L04 检查脚本](./courses/L04/scripts/)、[L16 实验说明](./courses/L16/examples/README.md)。
- 各讲 `reference/`：深入机制与扩展实验，例如 [L06 参考说明](./courses/L06/reference/参考说明.md)；`reference/archive/` 保留旧材料。
- 各讲 `assets/` 与 [公共教学资源](./courses/assets/README.md)：正文配图、源文件和历史截图。
- [cases 案例目录](./courses/cases/)：材料定位与复验入口。现有 [L16 库存余额筛选案例](./courses/cases/L16-库存余额筛选真实执行案例.md)原正文缺失，保留截图索引和重新验证要求。

**快速定位与版本核对。**

- [行动卡索引](./courses/行动卡索引.md)：回到当前讲的手册目标和提交位置。
- [课程工作区](./courses/FlowERP-AI研发工作台.code-workspace)：按 L00 手册打开 VS Code，统一文件位置、Python 环境和课程任务。
- [授课版本记录](./courses/session-versions.json)：核对已登记讲次的引用、提交和维护验证说明。

### 课件怎样获取

课堂 PPT、`.pptx` 与 `slides/` 归档不随 Git 发布。向课程提供方取得与讲义、手册匹配的授课版本，按本讲 README 的文件名存放；[slides 课件说明](./courses/slides/README.md)提供索引和核验说明。

[讲义阅读导航](./courses/讲义阅读导航.md)与[课件获取与本地检查](./courses/课件获取与本地检查.md)是旧入口的兼容页，现行导航在本页，教师检查方法在课程蓝图。

### 查设计与操作：reference

从 [参考资料总入口](./reference/README.md)按问题查阅，也可以直接打开下面的专题。理解设计时按“FDE 与工作台的关系 → 整体构思 → 具体设计”阅读。

**先理解设计为什么这样安排。**

- [FDE 与个人 AI 研发工作台](./reference/FDE与个人AI研发工作台.md)：沿库存案例理解现场、交付、能力三循环。
- [个人 AI 研发工作台](./reference/个人AI研发工作台.md)：整体构思、模块关系、事项延续与证据绑定。
- [工作台具体设计](./reference/工作台具体设计.md)：模块、数据、执行恢复和经验采用条件。
- [从业务需求到上线：理解 Spec 与个人研发工作台](./reference/从业务需求到上线：理解Spec与个人研发工作台.md)：用库存导出串起讨论、Spec、实现、检查和接受。

**从首页组织一次交付。**

- [日常研发入口](./reference/daily-development.md)：在“事项与决策”里继续调研、执行、返工和反馈。
- [项目驱动交付](./reference/项目驱动交付.md)：登记客户项目，确认质量命令，将需求带入事项。
- [工作台网页代码执行](./reference/工作台网页代码执行.md)：核对候选、修改范围、授权和中断处理。

**检查结果、定位交付证据。**

- [工作台 Eval Harness](./reference/工作台Eval-Harness.md)：运行候选检查，核对报告来源、退出码和人审。
- [工作台 Hook 使用说明](./reference/工作台-Hook-使用说明.md)：准备、安装和信任 Stop Hook，核对宿主触发与结果。
- [发布证据索引](./reference/发布证据索引.md)：将要求、修改、检查与人的决定交给复验者。

**核对 FlowERP 业务与运行边界。**

- [FlowERP 领域模型与业务不变量](./reference/FlowERP领域模型与业务不变量.md)：库存、订单、采购及失败后状态。
- [FlowERP 接口与运行边界](./reference/FlowERP接口与运行边界.md)：两个服务、接口、持久化和冷启动。

工作台默认使用端口 8001 和 `workbench.db`；FlowERP 客户界面默认使用端口 8000 和 `flowerp.db`。客户源码、业务检查和数据在[独立 FlowERP 仓库](https://github.com/congde/flowERP)维护，两套数据分别核对。

**遇到故障或准备维护。**

- [实操手册执行与排错](./reference/实操手册执行与排错.md)：环境、路径、解释器和从原记录恢复；Windows 用 PowerShell，macOS 用 zsh。
- [本机个人研发平台](./reference/personal-platform.md)：启动、项目交付、运行分工、预览发布、经验复用与迁移。
- [工作台稳定性与可用性](./reference/workbench-reliability.md)：带日期的故障、修复、验证记录和未完成的验收条件。

专题配图在 `reference/assets/`：[工作台设计](./reference/assets/workbench-design/)、[FDE 配图说明](./reference/assets/fde/README.md)、[交付入门](./reference/assets/delivery-explained/)。随对应正文查看。

### 核对建设规格与缺口：architecture

[工作台范式与闭环建设](./architecture/工作台范式与闭环建设.md)说明 **Harness + 记忆系统 + 工作流蒸馏** 的职责、实现对照、验收要求和剩余缺口。

维护时沿一条经验链核对：**前一事项留下经验 → 后一事项召回并明确采用 → 实际执行与人审 → 失败后修订或停用。** 这条跨事项链路才用于判断闭环；单次交付或反馈登记还不够。

建设计划、已有代码、历史验证和当前运行分别阅读，后端能力与首页交互是否贯通也分别确认。
