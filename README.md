# FlowERP

一个以真实业务闭环、数据一致性和可验证交付为核心的轻量级 ERP。

FlowERP 面向小型贸易、电商和仓储团队，覆盖商品、客户、供应商、销售、采购、库存、应收应付、复式总账与运营治理。项目只依赖 Python 标准库和 SQLite，可在没有外部服务的环境中完整运行，适合作为可部署的单节点 ERP、业务原型以及 Codex + FDE 工程训练项目。

> FlowERP 不是只有静态页面的演示系统。页面操作会进入真实 API、事务、SQLite 持久化、权限校验和审计链路。当前版本定位为单组织、单写实例、中低并发的可用 ERP；它不宣称具备 PostgreSQL 集群、跨可用区容灾等多节点高可用能力。

## 产品能力

| 模块 | 已实现能力 |
| --- | --- |
| 电商渠道中台 | 店铺档案、凭据环境变量引用、平台 SKU/组合品映射、订单幂等接入、统一审单、地址/金额/支付/映射拦截、库存预占、取消与发货回传任务 |
| 商品与往来单位 | 商品、条码、税率、库存上下限、客户信用额度、供应商交期、状态维护、乐观锁 |
| 销售管理 | 草稿编辑、信用检查、确认、原子预占、部分发货、取消、退货审批与退货入库 |
| 采购管理 | 草稿编辑、职责分离审批、驳回、在途库存、逐行质检、部分收货、拒收与收货过账 |
| 库存管理 | 多仓库与库位、批次、序列号、调拨、盘点、不可变库存流水、FIFO 库存估值 |
| 财务管理 | 应收应付发票、三单匹配、采购价差、收付款核销、作废冲销、账龄分析、银行账户与对账单 |
| 现金与银行 | 对账单整批平衡校验、流水去重、自动/人工匹配、取消匹配、银行/总账余额核对、关账阻断 |
| 业务总账 | 自动复式凭证、试算平衡、资产负债表、经营结果、库存/应收/应付/采购暂估对账 |
| 定价管理 | 客户和渠道价目表、阶梯数量价格、有效期与优先级解析 |
| 数据治理 | 审计日志、风险告警、全量业务对账、CSV 两阶段导入、主数据与库存导出 |
| 研发交付工作台 | 需求/业务对象关联、任务级 Spec、受控代码执行、Blocking Eval、具名交付审核、反馈治理和可验证 Evolution Record |
| 运行保障 | 就绪检查、维护模式、限流、过载保护、写实例租约、Outbox、备份与恢复验证 |
| 权限安全 | 组织隔离、角色权限、会话管理、CSRF、防暴力登录、用户停用与角色变更 |

## 核心业务闭环

```text
电商：平台店铺 → 订单幂等接入 → SKU 映射与异常拦截 → 统一审单 → 销售单/库存预占 → 发货回传任务
销售：客户订单 → 信用检查 → 库存预占 → 发货出库 → 应收发票 → 收款核销
采购：采购草稿 → 四眼审批 → 到货质检 → 库存入账 → 三单匹配 → 应付发票 → 付款核销
退货：退货申请 → 独立审批 → 退货收货 → 库存恢复 → 退款或红字处理
财务：业务事件 → 自动复式凭证 → 子账/总账对账 → 试算平衡 → 期间关账
资金：银行对账单 → 整批校验 → 收付款候选匹配 → 未达项清理 → 银行/总账对账
```

系统持续保护以下不变量：

- 可用库存不得为负，预占和扣减必须原子化。
- 同一个入库或过账幂等键只能生效一次。
- 单据只能按状态机迁移，取消必须释放有效预占。
- 采购制单人与审批人必须分离，未审批采购不能收货。
- 已过账库存流水和会计凭证不可直接修改，只能通过反向业务冲销。
- 关账前必须通过未过账单据、库存估值、试算平衡和子账总账检查。
- 银行对账单必须满足期初余额加流水净额等于期末余额；未完成的对账单会阻断对应期间关账。
- 失败必须保留证据，API 状态、SQLite 状态和审计结果必须一致。

## 快速开始

### 环境要求

- Python 3.10 或更高版本
- Windows、macOS 或 Linux
- 不需要安装第三方 Python 包

### 1. 初始化管理员

```bash
python -X utf8 -m workbench.cli init --organization "FlowERP" --username admin
```

命令会交互式要求输入两次管理员密码，密码至少 10 位。密码不会写入配置文件或命令历史。

也可以跳过此步骤直接启动服务，然后在首次打开的初始化页面创建组织和管理员。

### 2. 启动服务

```bash
python -X utf8 -m workbench.cli serve --host 127.0.0.1 --port 8000
```

浏览器打开：<http://127.0.0.1:8000/>

默认运行数据写入 `.runtime/flowerp.db`。数据库、备份、报告等运行产物不会提交到 Git。

### 3. 运行演示业务链路

```bash
python -X utf8 -m workbench.cli demo
```

该命令在临时运行目录中执行一条包含审批、库存和履约约束的可验证业务链路，不会污染正式运行数据库。

### 生成完整 Mock 验收账套

在已初始化的本地账套中生成多商品、客户、供应商、库存、销售、采购、应收应付、总账和自动对账数据：

```bash
python -X utf8 -m workbench.cli mock-data --runtime-dir .runtime
python -X utf8 -m workbench.cli verify-mock-data --runtime-dir .runtime
```

`mock-data` 使用固定业务标识并支持幂等重放，重复执行不会重复创建业务单据。输出中的 `verification.complete` 只有在库存、销售、财务、会计对账以及银行流水匹配全部通过时才为 `true`。

## 页面导航

- **经营驾驶舱**：订单、库存、应收应付和待办风险概览。
- **渠道订单中台**：店铺接入状态、统一订单池、SKU 映射、异常拦截和平台回传任务。
- **销售订单**：订单生命周期、发货记录和退货处理。
- **采购管理**：审批、到货质检、收货单与应付登记。
- **库存管理**：余额、流水、盘点、序列号和补货建议。
- **财务中心**：发票、收付款、银行账户与对账单、账龄、总账、财务报表、关账和自动对账。
- **商品档案**：商品参数、启停状态、价目表和价格规则。
- **客户与供应商**：信用、账期、联系人、地址和状态维护。
- **审计与数据治理**：审计日志、业务对账、告警、导入和导出。
- **研发交付**：提交需求后自动归一业务对象、生成任务级 Spec、受控执行并运行阻断级 Eval；查看报告与责任事件链，把交付审核和反馈审核分别具名记录；将 accepted Feedback 提升为 `EVO-*`，登记版本化资产并由独立下一 Task 完成复验。
- **系统与用户**：角色、账户状态、运行检查和维护模式。

## 验证与 Loop

一条命令启动自动交付流水线：

```bash
python -X utf8 -m workbench.cli task-submit \
  --requirement-id REQ-ECOM-001 \
  --business-ref CHANNEL_ORDER:EC-20260817-1001 \
  --business-ref SKU:NOTEBOOK-AI \
  --execute-code \
  --write-scope flowerp \
  --write-scope tests \
  --execution-timeout 900 \
  --timeout 1200 \
  --request "验证渠道订单缺货时不部分预占，补货后可恢复履约"
```

平台会自动生成 `.runtime/specs/TASK-*.md`，依次进入 `spec_ready`、`executing`、`evaluating`，最后停在 `review` 或 `rework`。需要逐阶段调试时，仍可使用手动入口：

`--execute-code` 会调用本机独立 Codex CLI 的非交互模式；`--write-scope` 是必须显式给出的写入白名单。工作台使用 `workspace-write` 沙箱和一次性会话，保存 JSONL 命令轨迹、Token 用量、最终结构化输出，并在执行前后独立扫描工作区生成真实 Diff。任何白名单外写入都会把任务标记为 `failed`，不会继续拿 Eval 绿灯掩盖越界。未传 `--execute-code` 时仍是 `verification_only`。如果 Codex CLI 不在 PATH，可用 `FLOWERP_CODEX_COMMAND` 指向独立安装的可执行文件。

```bash
python -X utf8 -m workbench.cli task-create \
  --requirement-id REQ-ECOM-001 \
  --business-ref CHANNEL_ORDER:EC-20260817-1001 \
  --business-ref SKU:NOTEBOOK-AI \
  --request "验证渠道订单缺货时不部分预占，补货后可恢复履约"
python -X utf8 -m workbench.cli task-run TASK-ID
python -X utf8 -m workbench.cli task-review TASK-ID \
  --reviewer reviewer-a --decision approve --note "Spec、业务对象与 blocking 证据一致"
```

`task-run` 只会停在 `review` 或 `rework`。即使 blocking 全绿，也必须由 `task-review` 或 Web 中的具名审核动作才能进入 `completed`；技术完成不会自动改变采购、销售或库存业务状态。

提交前建议依次执行：

```bash
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m workbench.feedback summary
python -X utf8 -m workbench.evolution summary
```

需要真实人工审核暂停/恢复时：

```bash
python -X utf8 -m agent.graph --require-human-review --state-file .runtime/delivery-review.json
python -X utf8 -m agent.graph --state-file .runtime/delivery-review.json --review-decision approve --reviewer reviewer-a
```

质量入口分别验证：

- 正常路径、失败路径、权限和状态机。
- 库存非负、并发预占、幂等入库和盘点快照。
- 采购四眼审批、三单匹配和采购价差。
- FIFO 销售成本、复式记账、冲销和子账总账一致性。
- 数据库完整性、备份可恢复性和敏感信息扫描。
- Loop 的轮数、时间、无进展和可核验 Token 预算停止条件。
- 失败任务映射、Graph 持久化人工审核节点、任务 API 和反馈审核状态机。

## 数据导入与导出

页面支持商品、客户和供应商 CSV 导入。导入采用两阶段流程：

1. 解析全部行并验证表头、编码、字段类型、邮箱和文件内重复项。
2. 展示通过与失败数量；只有全部通过的任务才能明确提交。
3. 提交时使用单一事务，任意编码冲突都会整批回滚。

页面同时提供商品、客户、供应商和即时库存 CSV 导出。

## 备份、恢复验证与维护模式

创建一致性备份：

```bash
python -X utf8 -m workbench.cli backup --label before-release
```

验证备份可恢复性：

```bash
python -X utf8 -m workbench.cli verify-backup .runtime/backups/<backup-file>
```

执行运行诊断：

```bash
python -X utf8 -m workbench.cli doctor
python -X utf8 -m workbench.cli runtime-status
```

变更前暂停业务写入：

```bash
python -X utf8 -m workbench.cli maintenance on --reason "schema upgrade"
python -X utf8 -m workbench.cli maintenance off --reason "verification passed"
```

维护模式只阻止业务写入，健康检查和只读查询仍然可用。

## Docker 部署

先复制并检查生产配置：

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
```

容器具备以下默认保护：

- 只读根文件系统，运行数据库使用独立 volume。
- 移除 Linux capabilities，并启用 `no-new-privileges`。
- 进程数限制、健康检查、优雅停止和日志轮转。
- 镜像构建阶段必须通过完整测试与阻断级 Eval。

生产部署必须修改 `FLOWERP_ALLOWED_ORIGINS`、Cookie 安全选项、磁盘阈值和备份策略。不要把真实 `.env`、密码、Cookie 或数据库提交到仓库。

## 配置

主要环境变量见 [.env.example](.env.example)：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `FLOWERP_ENV` | `development` 或 `production` | `development` |
| `FLOWERP_RUNTIME_DIR` | 数据库和运行文件目录 | `.runtime` |
| `FLOWERP_HOST` / `FLOWERP_PORT` | 服务监听地址与端口 | `127.0.0.1:8000` |
| `FLOWERP_AUTH_REQUIRED` | 是否强制身份认证 | 生产环境为 `true` |
| `FLOWERP_ALLOWED_ORIGINS` | 允许的 Web 来源，逗号分隔 | 空 |
| `FLOWERP_COOKIE_SECURE` | Cookie 是否仅通过 HTTPS 发送 | 生产环境为 `true` |
| `FLOWERP_SESSION_HOURS` | 登录会话有效期 | `12` |
| `FLOWERP_MAX_CONCURRENT_REQUESTS` | 最大并发请求数 | `64` |
| `FLOWERP_REQUEST_RATE_PER_MINUTE` | 单来源每分钟请求上限 | `600` |
| `FLOWERP_MIN_FREE_DISK_MB` | 就绪检查最低可用磁盘 | `512` |
| `FLOWERP_REQUIRE_RECENT_BACKUP` | 是否要求存在近期已验证备份 | `false` |

本地直接运行时需要由 shell、服务管理器或部署系统注入环境变量；`.env.example` 是模板，应用不会自行读取 `.env`。Docker Compose 会按其标准行为读取项目 `.env`。

## API 与健康检查

所有正式业务接口位于 `/api/v1/`，旧版演示接口在生产配置中默认关闭。

```text
GET /api/v1/health/live   进程存活检查
GET /api/v1/health/ready  数据库、Schema、磁盘、Outbox、备份和维护状态检查
GET /api/v1/metrics       运行指标
```

API 模块与主要端点说明见 [FlowERP API 概览](docs/FlowERP-API概览.md)。

## 项目结构

```text
flowerp/       ERP 领域服务、权限、SQLite Schema、会计与业务规则
workbench/     HTTP API、CLI、任务、反馈和交付摘要
web/           无前端构建依赖的 ERP 管理界面
eval/          阻断级与观察级 Eval、统一 Harness
agent/         失败映射、有界 Loop 和显式状态图
tests/         单元、集成、HTTP、并发和恢复测试
deploy/        Dockerfile、Compose 和冷启动配置
course/tasks/  16 讲目标卡、命令卡和验收卡
docs/          API、运行、架构、总账、验收矩阵和课程资料
```

## 工程交付工作台

除 ERP 产品外，仓库还包含一条完整的 FDE 交付链路：

```text
Spec → Eval → Harness → 失败任务 → Loop / Graph → API / Web → Feedback → Evolution Record
```

- `FDE_SPEC.md` 定义可验收目标。
- `eval/` 是 Hook、CI、Loop 和 Graph 共用的唯一质量入口。
- `agent.loop` 只在有失败时生成修复任务，并受轮数、时间、无进展和实测 Token 用量限制。
- `agent.graph` 显式表达评估、修复、复验和人工审核状态，并支持具名暂停/恢复。
- `/api/v1/tasks` 保存需求 ID、ERP 业务对象引用、Spec、Eval、事件责任人、Task 专属报告快照和具名交付审核；`/api/v1/feedback` 保存独立的反馈审核状态；`/api/v1/evolutions` 约束 `proposed → approved → asset_changed → verified`，并拒绝源 Task 自证、无关 Task 背书和报告校验和不一致。
- `.github/workflows/eval.yml` 在 CI 中复用同一套验证命令。

## 部署边界

当前版本适合：

- 单组织或小型团队。
- 单实例部署、中低写入并发。
- 以人民币为主的贸易、电商和仓储履约。
- 需要业务总账、成本与内控，但不直接承担法定财务申报的场景。

当前版本尚不等同于大型通用 ERP：

- SQLite 单写架构不支持多节点水平扩展。
- 尚未覆盖生产制造、MRP、工资、人力资源、固定资产全生命周期。
- 未集成本地电子发票、税务申报、银行直连和法定会计报表。
- 多币种已有数据结构基础，但汇兑损益和完整本地会计准则仍需专项实现。
- 淘宝、京东、拼多多、抖音、视频号和快手等真实连接仍需企业申请开放平台应用、取得对应权限并配置店铺授权；仓库内提供的是可审计的统一接入域模型和适配器边界，不会伪造第三方授权成功。

更完整的上线标准和演进方向见：

- [真实 ERP 产品基线与演进路线](docs/FlowERP-真实ERP产品基线与演进路线.md)
- [上线差距与验收矩阵](docs/FlowERP-上线差距与验收矩阵.md)
- [上线运行手册](docs/FlowERP-上线运行手册.md)
- [业务总账与库存估值](docs/FlowERP-业务总账与库存估值.md)
- [高可用架构与故障演练](docs/FlowERP-高可用架构与故障演练.md)

## 安全说明

如果发现安全问题，请不要在公开 Issue 中粘贴密码、访问令牌、真实客户数据或数据库文件。先保留复现步骤和请求编号，再通过项目维护者认可的私密渠道报告。
