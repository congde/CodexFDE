# Codex + FDE 行动营｜工作台驱动的电商 ERP 持续交付系统

先建设一套类似 DeepSeek Harness 的个人研发自动化工作台，再通过它持续构建电商 FlowERP：把模糊需求变成可验收 Spec，用统一 Eval 收口质量，用有界 Loop / Graph 处理失败，再用 API、Web 与反馈完成下一轮产品交付。

**Harness Workbench 是独立平台，不是 FlowERP 的子模块。** 它拥有独立进程、Web、API、项目注册表和 `.harness-runtime/` 数据库；FlowERP 是由它管理和持续交付的第一个目标项目。FlowERP 不是背景案例或冻结测试夹具，两者以“平台管理目标项目”的方式共同组成结业作品。

平台架构参考 DeepSeek 官方 Harness 的插件化、Profile、capability seam 和追加式 Session 日志思想，但课程 V0 是 Python 标准库的教学实现，不复制 Cordis 内核，也不宣称功能等价。版本化对照见 [`DeepSeek Harness 参考架构与差距`](docs/courses/DeepSeek-Harness参考架构与差距.md)。

工作台首先是一套围绕开发者核心工作，连接输入、生产、验证、交付、反馈和能力升级的长期运行系统。服务对象、核心产出、通用内核、开发者实例及设计红线见 [`个人 AI 工作台｜产品定义与能力模型`](docs/个人AI工作台-产品定义与能力模型.md)。

项目的统一目标、权威边界、16 讲建造顺序与当前收敛清单见 [`项目全景｜工作台驱动 FlowERP 持续交付`](docs/项目全景-工作台驱动FlowERP持续交付.md)。

开课、跟课和验收的统一入口见 [`课程指南｜个人 AI 研发工作台驱动 FlowERP 持续交付`](docs/FlowERP-Codex-FDE行动营-16讲课程汇总.md)；逐讲字段仍以课程大纲合同为准。

```text
方法主线：L01–L04 构建 Workbench V0，后续持续升级
产品主线：通过工作台逐讲构建 FlowERP
学习证据：判断、实现、失败、修订、互评、迁移与答辩
```

交付主链路：

```text
ERP 需求 → Spec → 工作台受控实现 → Eval / Harness → Repair / Loop / Graph
→ ERP 增量验收 → API / Web → Feedback → 下一次 ERP 交付
```

## 仓库里有什么

| 目录 | 职责 |
| --- | --- |
| `workbench/` | Spec、任务 API、CLI、执行沙箱、摘要与反馈 |
| `workbench/delivery_view.py` | Task、Spec、执行、Eval、审核、反馈与进化的统一只读投影 |
| `eval/` | 唯一质量入口；Hook、CI、Loop、Graph 都复用它 |
| `agent/` | 失败任务映射、有界 Loop、显式状态图与人工审核 |
| `flowerp/` | ERP 领域模型、SQLite 持久化与业务不变量 |
| `harness_web/` | **可选** Harness 平台驾驶舱（非大纲必做；含 OPC 视图） |
| `web/` | FlowERP 业务系统前端 + L14 最小交付状态与证据下钻（课程跟跑必做），不替代完整 Harness 驾驶舱 |
| `tests/` | 单元、集成、HTTP、并发与恢复测试 |
| `deploy/` | 容器化与冷启动 |
| `course/tasks/` | 16 讲目标卡、命令卡、验收卡 |
| `docs/` | 大纲合同、讲义与产品文档（本地资料） |

## 课程跟跑主路径 vs 可选驾驶舱

课表合同唯一事实源：[`docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md`](docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md)。  
机器投影：`workbench/course_mainline.py`（不是第二份大纲）。

| 用途 | 命令 / 目录 |
| --- | --- |
| **跟跑必做** | `workbench.cli`、`eval.harness`、`agent.loop` / `agent.graph`、`web/`（FlowERP） |
| **可选平台驾驶舱** | `harness-workbench serve-web` → `harness_web/`（8010） |
| **可选 OPC 挑战** | Agent 员工班组视图；**不是** L01～L16 通过标准，不能替代具名人审 |

```powershell
# 开课就绪检查（缺 course/lNN-start 时 course_ready=false，属诚实状态）
python -X utf8 -m workbench.cli course-status
python -X utf8 -m workbench.cli course-status --require-baselines

# 单讲合同 / Spec
python -X utf8 -m workbench.cli course-contract --lesson 8
python -X utf8 -m workbench.cli course-spec --lesson 8
```

开课就绪：本地存在线性 `course/l01-start`…`course/l16-start` 且  
`python -X utf8 -m workbench.cli course-status --require-baselines` 退出码 0、`course_ready: true` 时，才可声称支持逐讲红→绿复现。标签在侧分支 `course/baselines` 祖先链上发布（讲师侧，不进入学生跟跑树），不改写远端历史（推送需另行确认）。

开课前建议再跑：

```powershell
python -X utf8 -m unittest tests.test_course_outline_alignment tests.test_progression tests.test_course_mainline tests.test_course_release tests.test_course_api -q
python -X utf8 scripts/sync_outline_contracts.py   # 仅当改过大纲合同字段后
```

L04+ 起始红由讲师侧 `PROGRESSION.json` 门闩保证（路径 `course/baselines/`，gitignore，不给学生仓库），不是完整产品缺能力 git 史。

可验收合同见 [`FDE_SPEC.md`](FDE_SPEC.md)。Agent 约束见 [`AGENTS.md`](AGENTS.md)。

## 环境要求

- Python 3.10+
- Windows / macOS / Linux
- 课程跟跑线只依赖 Python 标准库与 SQLite，**运行时不需要第三方包**
- 仍建议使用虚拟环境：隔离解释器、固定工作区安装方式，避免污染系统 Python

```bash
# 创建并激活虚拟环境（需 Python 3.10+；Windows 可用 py -3.11 / py -3.12）
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# python3 -m venv .venv && source .venv/bin/activate

# 以可编辑模式安装本仓库（dependencies=[]，不会拉第三方业务包）
python -m pip install -U pip
python -m pip install -e .
```

激活后有两个独立产品入口：`flowerp` 启动 ERP；`harness-workbench` 启动研发自动化平台（**默认进入终端 REPL**）。`python -m workbench.cli …` 是工作台的批处理与课程命令入口。

**若提示找不到 `harness-workbench` 或 `flowerp` 命令**，说明尚未执行 `pip install -e .`。可先直接用模块方式启动（见下文「工作台怎么启动」），或补装：

```powershell
python -m pip install -U pip
python -m pip install -e .
```

## 工作台怎么启动

Harness Workbench **终端优先**：主界面是 CLI 交互式 REPL（`harness>`），不是网页。  
Web 面板是**可选**可视化辅助，需要另开命令启动。

### 启动后只有 CLI、没有网页？——正常

运行 `harness-workbench` 后出现类似输出，说明已经启动成功：

```text
(.venv) PS D:\work\CodexFDE> harness-workbench
bootstrap: exists · PROJECT-FLOWERP · D:\work\CodexFDE
Harness Workbench · 终端控制面。输入 help 查看命令，quit 退出。
harness>
```

| 入口 | 命令 | 是什么 |
| --- | --- | --- |
| **主界面（默认）** | `harness-workbench` | 终端控制面 REPL（`harness>`） |
| **可选网页** | `harness-workbench serve-web --bootstrap` | 浏览器 Agent 控制台 <http://127.0.0.1:8010/>（中文：会话对话 + 轨迹） |


在 `harness>` 里可直接敲 `help`、`status`、`submit` 等，**不必开网页也能用完整工作台**。  
若要网页：另开一个终端执行 `serve-web`（当前 `harness>` 会话可继续保留）。

### 1. 安装（首次）

```powershell
cd d:\work\CodexFDE
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

### 2. 主入口：交互终端

```powershell
# 已 pip install -e . 后
harness-workbench

# 未安装控制台脚本时（等价）
python -X utf8 -m workbench.harness_cli
```

进入 `harness>` 后常用命令：

```text
harness> help
harness> status
harness> bootstrap
harness> submit --req REQ-ERP-001 --scope flowerp,tests 验证库存预占规则
harness> tasks
harness> task TASK-XXXXXXXXXX
harness> session SESSION-XXXXXXXXXXXX
harness> tools
harness> agent SESSION-XXXXXXXXXXXX
harness> review TASK-XXX --decision approve --note "blocking 全绿，接受交付"
harness> quit
```

### 3. 非交互常用命令

```powershell
harness-workbench bootstrap
harness-workbench status
harness-workbench composition --profile PROFILE-HEADLESS
harness-workbench dump-config
harness-workbench plugin-runtime --profile PROFILE-DEFAULT
harness-workbench plugin-events --profile PROFILE-DEFAULT --limit 20
harness-workbench run --request "验证库存预占" --requirement-id REQ-001
harness-workbench --json mcp status
```

`run` 对标 dsh headless：最终助手答案打 **stdout**，状态元数据打 **stderr**，退出码 `0/1`。

Profile 插件不再只是数据库里的启用列表：工作台会根据 `requires/provides` 建立活动服务，Provider 切换时逆序清理副作用并重载依赖方，失败则恢复旧组合。设计与边界见 [`插件生命周期与可逆副作用`](docs/architecture/插件生命周期与可逆副作用.md)。

### 4. 可选：Web 面板（8010）

需要浏览器界面时，**另开一个终端**执行（不要关掉现有的 `harness>`）：

```powershell
harness-workbench serve-web --bootstrap
# 或
python -X utf8 -m workbench.platform_server --bootstrap
```

浏览器打开 <http://127.0.0.1:8010/>。Web 为中文界面，使用官方 DeepSeek Harness 的 `--dsw-*` 色板与三栏布局（侧栏 · 对话/轨迹 · 详情）。这是课程 V0 的**可选**视觉驾驶舱，**不是**官方 Cordis/React 产品本体，也**不是**大纲 L01～L16 跟跑必做。OPC「超级个体 + Agent 员工」为可选挑战皮肤。若看到旧页面请 **Ctrl+F5** 强刷。

## 两个产品，别混端口

| 产品 | 作用 | 默认入口 | 运行数据 |
| --- | --- | --- | --- |
| **Harness Workbench** | 研发自动化平台：终端 REPL / Spec / Eval / 审核 | **终端** `harness-workbench`；可选 Web **8010** | `.harness-runtime/` |
| **FlowERP** | 电商 ERP 业务系统：库存、订单、采购、Web | **8000** | `.runtime/` |

两者可同时运行，数据库与进程物理隔离。

## 5 分钟跑通

```bash
# 演示一条含审批、库存与履约约束的业务链路（临时目录，不污染正式库）
python -X utf8 -m workbench.cli demo

# 阻断级 Eval（唯一质量入口）
python -X utf8 -m eval.harness --suite blocking

# 初始化 Harness 平台并注册当前仓库为默认目标项目 PROJECT-FLOWERP
python -X utf8 -m workbench.cli harness-bootstrap
# 或：harness-workbench bootstrap

# 启动独立 Harness 平台（终端主入口）
# 方式 A：已 pip install -e . 时
harness-workbench

# 方式 B：未安装控制台脚本时（等价）
python -X utf8 -m workbench.harness_cli

# 可选：再开一个终端启动 Web 面板（8010）
harness-workbench serve-web --bootstrap
# 或：python -X utf8 -m workbench.platform_server --bootstrap

# 另一个终端启动 FlowERP 业务系统
python -X utf8 -m workbench.cli init --organization "FlowERP" --username admin
python -X utf8 main.py --host 127.0.0.1 --port 8000
```

Harness 主控制面是终端 REPL（`harness>`）。使用 `bootstrap` / `--bootstrap` 或先跑 `harness-bootstrap` 时，FlowERP 仓库会自动注册为 `PROJECT-FLOWERP`。可选 Web 打开 <http://127.0.0.1:8010/>。FlowERP 打开 <http://127.0.0.1:8000/>。

在 Harness Web（可选）中：右上角填写具名操作者 → 提交任务 → 等状态到 `review` → 点「查看」→ 填写审核理由 → 提交，任务才会进入 `completed`。终端侧用 `submit` / `review` 完成同一流程。

```bash
# 若 8000 已被占用，可换端口
python -X utf8 main.py --host 127.0.0.1 --port 8001
```

`init` 会交互式设置管理员密码（至少 10 位）。也可跳过 `init`，在首次打开的初始化页创建组织和管理员。

## 工作台怎么用

独立平台通过项目注册表连接任意 Git 工作区；任务以 `PROJECT:PROJECT-ID` 稳定引用目标项目，Eval 和 Codex 均在注册的项目根目录执行。写 API 只监听本机地址，并要求 `X-Workbench-Actor` 与 `Idempotency-Key`。浏览器界面会自动提供这两项本地操作证据。

### 一条命令提交交付任务

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

任务会生成 `.runtime/specs/TASK-*.md`，依次进入 `spec_ready` → `executing` → `evaluating`，最后停在 `review` 或 `rework`。

- `--execute-code`：调用本机 Codex CLI 非交互模式；未传则只做 `verification_only`
- `--write-scope`：必须显式给出的写入白名单；白名单外写入会把任务标为 `failed`
- 即使 blocking 全绿，也必须经具名审核才能进入 `completed`

若 Codex CLI 不在 PATH，设置 `FLOWERP_CODEX_COMMAND` 指向可执行文件。

### 分阶段调试

```bash
python -X utf8 -m workbench.cli task-create \
  --requirement-id REQ-ECOM-001 \
  --business-ref SKU:NOTEBOOK-AI \
  --request "验证渠道订单缺货时不部分预占，补货后可恢复履约"
python -X utf8 -m workbench.cli task-run TASK-ID
python -X utf8 -m workbench.cli task-review TASK-ID \
  --reviewer reviewer-a --decision approve --note "Spec、业务对象与 blocking 证据一致"
```

### 有界修复与人工审核

```bash
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m agent.graph --require-human-review --state-file .runtime/delivery-review.json
python -X utf8 -m agent.graph --state-file .runtime/delivery-review.json \
  --review-decision approve --reviewer reviewer-a
```

Loop 只在有失败时生成修复任务，并受轮数、时间、无进展与实测 Token 预算约束。Graph 显式表达评估、修复、复验与人工审核状态。

## 启动与排错

### 启动后只有 `harness>`、没有网页界面？

这是预期行为。`harness-workbench` 的主界面就是终端 CLI；网页需另开：

```powershell
harness-workbench serve-web --bootstrap
```

打开 <http://127.0.0.1:8010/> 后应看到面向 FlowERP 交付的工作台：顶部常驻进度条（需求→规格→改代码→验收→你确认）、待验收横幅、右侧进度详情。次要操作收在「更多」。若仍是旧页面，强制刷新（Ctrl+F5）。

详见上文「工作台怎么启动」。

### `harness-workbench` 无法识别

PowerShell 报「无法将 harness-workbench 项识别为 cmdlet」时，通常是虚拟环境里还没 `pip install -e .`。

**立即可用（无需安装脚本）——终端主入口：**

```powershell
python -X utf8 -m workbench.harness_cli
```

**可选 Web 面板：**

```powershell
python -X utf8 -m workbench.platform_server --bootstrap
# 或
python -X utf8 -m workbench.cli harness-serve --bootstrap
```

**想用短命令时：**

```powershell
python -m pip install -e .
harness-workbench
harness-workbench serve-web --bootstrap
```

### FlowERP 启动报 `WinError 10013` / 端口绑定失败

多半是 **8000 已被占用**（常见是之前已有一个 `python main.py` 在跑），不是管理员权限问题。

```powershell
# 查看占用 8000 的进程
netstat -ano | findstr ":8000"

# 若确认是旧实例，结束进程（把 PID 换成 netstat 最后一列）
Stop-Process -Id <PID> -Force

# 或直接换端口
python -X utf8 main.py --port 8001
```

若 <http://127.0.0.1:8000/> 已能打开 FlowERP 页面，说明服务已在运行，无需再启一次。

### Harness 与 FlowERP 命令对照

| 目标 | 推荐命令 | 入口 |
| --- | --- | --- |
| 研发工作台（终端） | `harness-workbench` 或 `python -X utf8 -m workbench.harness_cli` | `harness>` REPL |
| 研发工作台（可选 Web） | `harness-workbench serve-web --bootstrap` | <http://127.0.0.1:8010/> Agent Console |
| 电商 ERP | `python -X utf8 main.py` | <http://127.0.0.1:8000/> |

## 质量入口

提交前建议：

```bash
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m workbench.cli demo
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m workbench.feedback summary
```

- `blocking`：业务不变量、安全边界、状态机与幂等；失败必须阻断
- `observing`：课程资产与可维护性提示；失败记告警，不伪装成业务失败
- Hook、CI、Loop、Graph **不复制**测试逻辑，只消费 Harness 退出码与报告
- CI 入口：[`.github/workflows/eval.yml`](.github/workflows/eval.yml)

不可破坏的业务规则：

1. 可用库存不得为负；预占必须原子化
2. 同一入库幂等键只能生效一次
3. 订单只能按状态机迁移；取消必须释放预占
4. 采购补货须人工审批后才能入库
5. 任务、Eval 报告与反馈可追溯；失败不可伪装成成功

## FlowERP 现场能力（摘要）

FlowERP 是可运行的单组织、单写实例 ERP：页面操作进入真实 API、事务、SQLite、权限与审计，不是静态演示页。

覆盖商品/客户/供应商、销售与采购、库存、应收应付、复式总账、渠道订单中台与运营治理。典型闭环：

```text
销售：订单 → 信用检查 → 库存预占 → 发货 → 应收 → 收款核销
采购：草稿 → 四眼审批 → 质检收货 → 三单匹配 → 应付 → 付款核销
电商：店铺接入 → 幂等接单 → SKU 映射/拦截 → 审单 → 预占 → 回传任务原子领取 → 失败退避/死信
```

适合中低并发单节点场景；不宣称多节点高可用或法定财税申报完备。更多产品边界见本地 `docs/`。

### 常用运维命令

```bash
python -X utf8 -m workbench.cli mock-data --runtime-dir .runtime
python -X utf8 -m workbench.cli verify-mock-data --runtime-dir .runtime
python -X utf8 -m workbench.cli backup --label before-release
python -X utf8 -m workbench.cli verify-backup .runtime/backups/<backup-file>
python -X utf8 -m workbench.cli doctor
python -X utf8 -m workbench.cli maintenance on --reason "schema upgrade"
```

### Docker

```bash
cp .env.example .env   # Windows: Copy-Item .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
```

生产配置见 [`.env.example`](.env.example)。不要提交真实 `.env`、密码、Cookie 或数据库。

健康检查：

```text
GET /api/v1/health/live
GET /api/v1/health/ready
GET /api/v1/metrics
```

## 课程与建设口径

16 讲围绕工作台能力逐讲推进，每讲对应 FlowERP 暴露的工程问题、工作台增量，以及学生可迁移的证据。大纲合同与详细讲义在本地 `docs/`、`course/`。

课程建设按国家级一流本科课程（金课）口径持续重构；课程目标、评价计算、两周期改进和证据边界见 [`国家级一流本科课程建设方案`](docs/courses/国家级一流本科课程建设方案.md)，当前申报缺口与责任边界见 [`申报级质量门`](docs/courses/国家级一流本科课程申报级质量门.md)。正式申报资格与当批次要求须由学校依据教育部最新文件确认。仓库实现与文案不能替代学生目标达成，也不能伪造申报资格或教学成效。

## 安全

不要在公开 Issue 中粘贴密码、访问令牌、真实客户数据或数据库文件。保留复现步骤与请求编号，通过维护者认可的私密渠道报告。
