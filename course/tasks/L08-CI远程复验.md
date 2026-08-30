# L08｜把同一套 Eval 接入 CI

- **核心内容**：本地快速反馈与远程可信复验；候选分支、报告归档和人工合并。
- **演示结果**：不合格变更在 CI 中失败，修复后生成通过报告。
- **课内增量**：配置 CI 工作流并上传 Harness 报告。
- **通过标准**：本地与 CI 使用同一入口；非零退出码阻断候选变更；自动修复不直接合并主分支。
- **挑战任务**：失败后只创建候选分支或草稿 PR，并附上失败证据。

## 项目主线与评价证据

- **FlowERP 现场问题**：本讲由 FlowERP 真实交付暴露可重复工程问题，用于触发工作台能力验证。

- **ERP 产品增量**：交付整单原子预占；库存不足时不得部分预占，同时保留坏实现的假绿证据。
- **工作台增量**：增加门禁 Spec、CI 同入口复验、Evidence Envelope、SHA/Run 身份、失败报告归档和最小权限。
- **双线因果**：原子预占缺陷在坏 CI 中被显示为绿色，迫使工作台先修可信门禁，再由同一门禁验证业务修复。
- **ERP 验收证据**：A 中部分预占缺陷可重现；B 同业务代码诚实变红；C 整单原子预占转绿，库存不足时所有行均保持未预占。
- **学生学习证据**：门禁 Spec、Workflow/Python Diff、假绿/诚实红/可信绿三个 Run、Evidence Envelope 和同伴盲判。
- **形成性评价**：学生必须先让坏门禁暴露矛盾，再分两次提交修门禁与修业务；只交最终绿灯没有形成性证据。

## 付费行动课交付合同

本讲不是阅读现成 Workflow。学员领取 L05 缺陷提交和一条会吞掉非零退出码的故障流水线，必须亲手交付：

1. `CI_GATE_SPEC.md`：至少六条带反例的门禁不变量；
2. `.github/workflows/eval.yml` 修复 Diff；
3. `workbench/ci_evidence.py` 与正常/缺报告/缺身份测试；
4. Run A 假绿、Run B 诚实红、Run C 可信绿及对应 Artifact；
5. A→B 未改业务、B→C 未改裁判的文件边界证明；
6. `ci_evidence.py` 对非 FlowERP 命名报告夹具的迁移测试与通用/项目字段表；
7. 另一组不看作者说明完成的法证结论。

只改 YAML 缩进、复制成品、提交讲师截图或只展示 Run C，均按未完成处理。

开工前必须具备：可推送的训练分支、真实独立 Runner、可下载 Artifact、L05 缺陷提交和故障 Workflow。缺少远端平台时记录为“待补实验”，不能用讲师代跑、截图或另一个本地目录冒充达成。

## 主次边界

- **FlowERP 只提供输入**：本讲事故来自预占缺陷与坏 CI；评分以工作台门禁工程为主。
- **FlowERP 产品状态**：A 保留原子预占假绿，B 只修门禁得到诚实红，C 再由工作台交付原子预占得到可信绿；
- **工作台才是本讲作品**：门禁 Spec、Workflow、Evidence Envelope、失败归档、身份核验和迁移测试占主要时间与至少 80% 评分证据；
- **不在 L08 新增**：库存/采购/API/页面扩建一律退回。
- **Run C 只作校准**：应用 L05 补丁不超过 10 分钟，不按业务代码量得分；若扩展库存、采购、API 或页面，直接判偏离主线。

## 课前独立诊断

在触发 CI 前提交且不得覆盖：

1. 一张红灯截图缺少哪些身份信息，为什么不能证明当前提交有缺陷？
2. 失败时仍应归档哪些证据？
3. 为什么运行不受信任代码的 Job 不应同时持有部署密钥？

## 混合式学习流程

| 场域 | 学习任务 | 必须留下的证据 |
|---|---|---|
| 线上 30 分钟 | 建立 SHA—Workflow—Run—Artifact—环境身份链 | 首次证据判断、身份索引、失败预测 |
| 线下 110 分钟 | 复现假绿，写门禁 Spec，实现可迁移证据信封，只修门禁得到红，再应用已知补丁校准转绿 | Spec、工作台代码、Run A/B/C、迁移测试与同伴法证 |
| 课后迁移 | 在个人项目再制造并修复一个 CI 假成功反例 | 迁移前后 Run、差异说明与权限复核 |

## AI 使用与证据边界

- AI 可分析 Workflow 和日志，但必须绑定真实 SHA、Run ID、环境和 Artifact，不能把相似历史 Run 当当前事实；
- 主线 CI 不调用模型，不配置模型密钥；如扩展调用，需单独风险评审和最小权限；
- 自动化不得直接合并主分支，修复只进入候选分支或草稿 PR，并接受具名人工复核；
- 外部 Action、依赖和缓存均属供应链输入，需固定版本并防止来自不受信任分支的秘密暴露。

## 本讲主线

同一份 L05 缺陷代码先被坏门禁判成绿色。学员必须把“CI 颜色”降级为待核验信号：先用 Artifact 证明 Run A 假绿，再只修 Workflow 得到 Run B 诚实红，最后冻结裁判、只修业务得到 Run C 可信绿。本讲建立远程独立复验，不自动合并，也不自动修复。

## 大纲与前沿硬校准

- **必须完成**：CI 复用本地入口，非零码阻断候选变更，红灯也归档报告并绑定提交与环境。
- **当前做法**：最小权限和提交身份是基础；Artifact Attestation/SBOM 只增强来源与供应链取证。
- **退回条件**：自动合并/修复、给不受信任代码部署密钥、把 Attestation 写成安全证明。见[校准矩阵](../../docs/courses/16讲主线与前沿校准矩阵.md)。

## 必须教会的知识

1. **提交身份**：CI 结论必须绑定 commit SHA、workflow run 和环境版本，不能只写“主分支通过”。
2. **入口同源**：本地与远端都运行单测和同一个 blocking Harness，CI 不重写业务判定。
3. **失败仍归档**：使用无条件归档保存红灯 Harness JSON；日志缺失不是通过。
4. **最小权限**：默认只读代码；会执行不受信任仓库代码的 job 不获得部署密钥。
5. **供应链边界**：固定运行时与 Action 版本，区分业务失败、测试缺陷和基础设施故障。

## 课堂任务

```powershell
python -X utf8 -m workbench.cli course-contract --lesson 8
python -X utf8 -m workbench.cli course-spec --lesson 8
python -X utf8 -m workbench.cli course-submit --lesson 8 --execute-code
python -X utf8 -m workbench.cli course-eval --lesson 8
```

### Ticket 1｜制造并证明假绿

- 从 L05 缺陷提交创建 `lab/l08-<student-id>`；
- 使用讲师提供的故障 Workflow 触发 Run A；
- 下载报告，证明 `job=success` 与 `decision=block` 同时出现；
- 在 `CI_GATE_SPEC.md` 写下被破坏的不变量和预期反例。

### Ticket 2｜实现 Evidence Envelope

实现并测试：

```powershell
python -X utf8 -m workbench.ci_evidence `
  --report .runtime/reports/harness-blocking.json `
  --output .runtime/reports/ci-evidence.json
```

输出至少包含 `commit_sha`、`run_id`、`workflow`、`runner_os`、Python 版本、`suite`、`report_sha256` 和 `report_decision`。报告不存在或缺少 SHA/Run ID 时必须非零退出且不留下假证据。

```powershell
python -X utf8 -m unittest tests.test_ci_evidence -v
```

测试中必须再提供一份非 FlowERP 命名、但遵循同一报告 Schema 的夹具；模块不得导入 `flowerp`。提交一张“通用字段/项目特定字段”边界表，证明沉淀的是工作台能力，不是 ERP 专用脚本。

### Ticket 3｜只修门禁，得到 Run B

- 删除 `|| true`/`continue-on-error` 等吞错路径；
- 加入完整 unittest 和同一个 blocking Harness；
- 前序失败但 Run 未取消时仍生成报告、证据信封和 Artifact；
- 权限降到 `contents: read`，缺报告显式失败；
- 触发 Run B，并证明 A→B 没有修改 FlowERP 业务实现。

### Ticket 4｜冻结裁判，应用已知补丁校准 Run C

- 不修改 Workflow、Harness、Eval、等级和 expected；
- 直接应用 L05 已评审的最小修复补丁，不在本讲重新探索或扩展 ERP 实现；
- 触发 Run C，下载新 Artifact；
- 证明 B→C 的 Workflow/Harness/Eval 内容哈希没有变化。

### Ticket 5｜陌生人法证

与另一组交换 B/C 证据包。接收方重算报告哈希、核对 SHA/Run/环境，任选一个命令复现，并在十分钟内具名给出拒绝或进入人工合并的结论。

本地基础命令：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
```

## 独有产物与验收

提交一份 CI Evidence Index，包含门禁 Spec、代码 Diff、A/B/C 的 SHA 与 Run ID、环境、命令、退出码、Artifact/Envelope 名称和哈希、两段文件边界证明、权限清单与同伴结论。本地/远端入口不同、失败时报告丢失、向整个 Job 暴露密钥或红灯自动合并，均退回。

## 提交证据包与四级量规

证据包包含课前判断、`CI_GATE_SPEC.md`、Workflow/Python Diff 与测试、非 FlowERP 夹具迁移测试、Run A/B/C、Artifact 与哈希、本地复现、A→B/B→C 边界证明、权限清单、同伴盲判和课后迁移。

| 维度 | 优秀 | 达成 | 发展中 | 未达成 |
|---|---|---|---|---|
| 假绿与因果隔离 | A/B/C 完整，C 只应用 L05 冻结补丁 | 三段结论和文件边界成立 | 有三次 Run 但边界证据不足 | 只有最终绿灯、扩建 ERP 或改裁判求绿 |
| 门禁工程 | Spec 有反例，失败续跑、缺文件和非零退出均实测 | 关键门禁行为通过 | 只做静态 YAML 审查 | 仍吞错或失败失明 |
| Evidence 编程与迁移 | 模块、失败测试、错配检测和非 FlowERP 夹具完整 | 基础模块与迁移测试通过 | 只在 Workflow 内拼脚本或项目耦合未解释 | 无身份绑定或写成 ERP 专用脚本 |
| 身份与同源复验 | SHA、Run、Workflow、环境、Artifact 双向可查 | 关键身份完整且同入口 | 缺环境/哈希或差异未解释 | 只有截图或 CI 另造裁判 |
| 权限与同伴复核 | 最小权限、依赖策略、盲判和人工合并完整 | 无秘密暴露且同伴可复验 | 权限偏宽或复核不完整 | `write-all`、自动合并或秘密泄露 |

硬门槛：伪造 Run/SHA、只交 Run C、A→B 偷改业务、B→C 偷改 Workflow/Harness/Eval、在 L08 扩建 FlowERP、失败不归档、blocking 非零不阻断、自动合并主分支或暴露密钥，直接退回。

## 持续改进与迁移

记录假绿识别率、门禁 Spec 反例质量、Evidence 测试通过率、A→B/B→C 越界率、红灯 Artifact 保留率、同伴盲判正确率和过宽权限数。平台表达式、Action 版本和安全要求在正式授课前按当前官方资料复核。

## 留给下一讲

红色报告描述了事实，却没有授权修复者能改什么。L09 把证据压成严格 Repair Task。
