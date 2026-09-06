# Codex AI 工程交付行动营：工作台驱动的 FlowERP 持续交付

这不是一套把 16 讲拆成 16 个孤立工具的课程，也不是一门单纯开发 ERP 功能的课程。

项目只有一条主线：**先构建一套个人研发自动化工作台（Harness），再让它在真实的 FlowERP 需求、失败和验收中持续升级。**

- L01～L04：做出能接收 Spec、受控修改代码并运行最小 Eval 的工作台 V0。
- L05～L15：用工作台持续交付 FlowERP；每次真实交付都反过来升级 Eval、Loop、Graph、API、Web 和反馈闭环。
- L16：从未实现的 ERP 小需求出发，现场完成一次有边界、有证据、可答辩的冷启动交付。

这里有一个不能省略的自举换挡：L01～L03 工作台尚未完成，学生直接监督 Codex 开发规则、Spec 和解析能力；L04 先用 Codex 补齐 Workbench V0，再让 V0 首次以 Spec、写集、前红、Diff、后绿和人审约束 Codex 交付库存导出；L05 起由 FlowERP 现场问题推动工作台升级，再由升级后的工作台控制 Codex 修复或交付 ERP。完整故事合同见 [Codex × FDE 双阶段故事链](docs/courses/Codex-FDE双阶段故事链.md)。

FDE 指 **Forward-Deployed Engineering**：贴近用户、数据和运行后果，通过现场循环决定做什么、交付循环约束怎样做、能力循环把重复失败沉淀为下次可复用的工作台资产。本项目不训练模型，不能把资产升级写成“模型自动进化”。

最终成果不是一份课程文档，而是两个不可拆分的可运行产品：

1. **个人研发自动化工作台**：负责把需求变成 Spec，约束执行范围，运行 Eval，保留失败、修订、审核和反馈证据。
2. **FlowERP 客户项目**：负责提供真实业务约束，并检验工作台是否真的能持续交付。

> 课程采用“案例先行、工具后置”。例如 L03 先用“库存导出”案例识别歧义、补齐验收口径，再介绍 Spec 模板、OpenSpec、Superpowers 等常见方法。通用工具用于迁移和比较，不替代对真实业务的判断。

## 先认清三个入口

| 入口 | 是否跟跑必做 | 用途 | 默认地址 / 数据 |
|---|---:|---|---|
| 个人研发工作台 | 是 | 查看课程任务、交付状态与证据链 | <http://127.0.0.1:8001> · `.runtime/workbench.db` |
| FlowERP 客户项目 | 是 | 操作库存、订单、采购等 ERP 业务 | <http://127.0.0.1:8000> · `.runtime/flowerp.db` |
| 完整 Harness 平台 | 否，可选挑战 | 体验 Profile、Provider、插件、Session 与多项目平台能力 | 终端 REPL / <http://127.0.0.1:8010> · `.harness-runtime/` |

**8001 是工作台，8000 是客户项目。** 两个界面、两个数据库、两个职责，不能混用。8010 只属于可选的完整 Harness 平台，不是 L01～L16 的通过条件。

## 60 秒理解这个项目

一次完整交付不是“让 Codex 写完代码”，而是下面这条可追溯链路：

```text
真实 ERP 需求
  → 明确范围与不可破坏规则
  → 形成可验收 Spec
  → 先得到失败证据
  → Codex 在允许写集内修改
  → 运行同一套阻断 Eval
  → 人工审核
  → 交付摘要与反馈
  → 将重复问题沉淀回工作台
```

课程始终同时观察三条线：

- **方法主线**：工作台如何从最小闭环成长为可复用的交付系统。
- **产品主线**：FlowERP 如何从主数据逐步增长到库存、订单、采购和可操作 Web。
- **学习证据**：学生能否留下首次判断、失败、修订、互评和迁移证据。

学生从 [课程资料总入口](docs/README.md) 开始，课堂投影与复习使用 [L01～L16 独立课件](docs/courses/slides/README.md)。对外课程名与 16 讲标题以 [课表｜Codex AI 工程交付行动营](docs/课表｜Codex AI 工程交付行动营.md) 的「主题」列为准，每讲四项内容合同以 [16 讲课程大纲](docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md) 为准。基础较弱或尚未配置环境的学员先完成 [L00 课前准备](docs/courses/L00-课前准备-安装工具与通过环境自检.md)及其[行动卡](docs/courses/tasks/L00-课前准备.md)。L00 不计入正式 16 讲，也不产生工作台或 FlowERP 产品增量。

## 5 分钟跑起来

### 1. 准备环境

仓库要求 Python 3.10 或更高版本；课堂统一使用 Python 3.12.x。课程跟跑线默认只使用 Python 标准库和 SQLite，不依赖外部服务。学员跟课请先完成 L00，不要把本节当作 L01 已完成。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

macOS：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Linux 可以使用仓库允许的 Python 3.10+，但不作为课堂统一排错口径。后续命令默认已激活 `.venv`；Windows 也可继续直接调用 `.\.venv\Scripts\python.exe`。

### 2. 先验证仓库

```bash
python -X utf8 -m workbench.cli demo
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m workbench.cli course-status
```

`course-status` 检查课程合同、Eval 映射和线性标签；`course_ready: true` 只说明课程合同可跟跑，不代表学生已经亲手构造了每讲能力。

### 3. 启动两个必做界面

终端 A——个人研发工作台：

```bash
python -X utf8 -m workbench.cli serve-workbench
```

打开 <http://127.0.0.1:8001>。

可选的完整 Harness（8010）不是这个必做工作台（8001）的替代入口；需要联动 FlowERP 时使用后文的 `harness-workbench serve-web --boot`。

终端 B——FlowERP 客户项目：

```bash
python -X utf8 -m workbench.cli init --username admin
python -X utf8 -m workbench.cli serve
```

`init` 会在终端中安全提示输入并确认管理员密码。打开 <http://127.0.0.1:8000>，使用刚创建的管理员登录。已有运行数据时，`init` 会按命令自身的幂等与冲突规则处理，不要删除数据库来“重新开始”。

| 你看到的内容 | 正确端口 |
|---|---:|
| 课程任务、Spec、Eval、事件与审核证据 | 8001 |
| 商品、库存、销售订单、采购单和运营状态 | 8000 |
| Profile、Provider、插件和 Session 平台视图 | 8010（可选） |

## 16 讲怎样推进同一个系统

| 阶段 | FlowERP 产品状态 | 工作台新增或验证的能力 | 关键学习证据 |
|---|---|---|---|
| L01～L04 · V0 | 建立商品/仓库基线，交付第一个库存导出切片 | 仓库约束、可验收 Spec、受控修改、最小 Eval | 首次判断、红灯、范围内 Diff、绿灯 |
| L05～L08 · 质量链 | 幂等入库、可用库存、订单与原子预占逐步可用 | 失败优先 Eval、证据汇总、本地护栏、CI 复验 | 同一失败能被本地与 CI 稳定复现 |
| L09～L12 · 自修复编排 | 取消释放库存、订单状态机、采购审批与入库 | 失败转任务、有界 Loop、独立子任务、显式 Graph 与人审 | 停止条件、回退路径、职责分离 |
| L13～L15 · 产品化 | ERP 能力通过 API 和 Web 被真实操作 | 任务 API、工作台面板、摘要与真实反馈 | API/持久化一致、审核记录、修订前后对比 |
| L16 · 冷启动答辩 | 现场交付一个此前未实现的受控 ERP 小需求 | 复用整条工作台交付链 | 新红灯、真实 Diff、新绿灯与具名答辩 |

每讲必须回答四个问题：交付了什么 ERP 状态；暴露了什么重复工程问题；工作台新增或验证了什么能力；什么证据证明学生能迁移该能力。

## 跟课的正确入口

不要靠 README 猜每讲任务。课程大纲是合同，任务卡是行动入口，CLI 是机器可执行投影。

### 查看合同与生成本讲 Spec

```bash
python -X utf8 -m workbench.cli course-contract --lesson 3
python -X utf8 -m workbench.cli course-spec --lesson 3
```

以 L03 为例，详细教学设计见 [把模糊需求变成可验收 Spec](docs/courses/L03-把模糊需求变成可验收Spec.md)，学生行动卡见 [L03 Spec 驱动](docs/courses/tasks/L03-Spec驱动.md)。

### 从 L04 起执行真实交付

```bash
python -X utf8 -m workbench.cli course-submit --lesson 4 --execute-code --actor student
python -X utf8 -m workbench.cli course-eval --lesson 4
```

- `--execute-code` 明确授权 Codex 在本讲允许写集内修改代码。
- `--verify-only` 只复验已有候选，不能作为学生亲手实现本讲增量的证据。
- L04 以后由隔离工作区构造“执行前红、范围内 Diff、执行后绿”；不要把终态仓库已经通过测试误当成学习达成。

### 检查逐讲基线

```bash
python -X utf8 -m workbench.cli course-status --require-baselines
```

当输出中的 `baseline_semantics` 为 `progression_gate` 时，线性标签只是讲师侧的进度门闩；可构造性仍要看隔离工作区中的实际证据。

16 张目标卡、命令卡和验收卡统一收录在 [docs/courses/tasks](docs/courses/tasks/README.md)。

## 仓库地图

| 目录 | 职责 |
|---|---|
| [`flowerp/`](flowerp/) | ERP 领域模型、SQLite 持久化与业务服务 |
| [`workbench/`](workbench/) | Spec、任务 API、CLI、交付摘要与反馈 |
| [`eval/`](eval/) | 唯一质量入口；Hook、CI、Loop、Graph 都复用它 |
| [`agent/`](agent/) | 失败任务映射、有界 Loop 与显式状态图 |
| [`workbench_web/`](workbench_web/) | 跟跑必做的个人研发工作台界面，默认 8001 |
| [`web/`](web/) | FlowERP 客户项目界面，默认 8000 |
| [`harness_web/`](harness_web/) | 可选的完整 Harness 平台界面，默认 8010 |
| [`docs/courses/slides/`](docs/courses/slides/) | 与极客时间主题逐讲对应的 16 份独立 PPT |
| [`docs/courses/tasks/`](docs/courses/tasks/) | 16 讲目标卡、命令卡和验收卡 |
| [`docs/courses/`](docs/courses/) | L00～L16 学生讲义、课程蓝图、任务卡与实验 |
| [`docs/reference/`](docs/reference/) | 工作台、FlowERP 领域与运行边界参考资料 |
| [`deploy/`](deploy/) | 容器化、运行与回滚资料 |

## 一次工作台任务怎样交付

课程命令用于逐讲跟跑；下面的通用任务命令用于把新的 ERP 需求提交给工作台：

```bash
python -X utf8 -m workbench.cli task-submit \
  --request "导出指定仓库的可用库存，不得泄露成本字段" \
  --requirement-id REQ-INVENTORY-EXPORT-001 \
  --business-ref FLOWERP-INVENTORY \
  --actor student \
  --execute-code \
  --write-scope flowerp \
  --write-scope tests
```

工作台会形成可追溯的任务、Spec、执行、Eval、事件和人工审核记录。写权限必须通过 `--write-scope` 明确收窄；没有 `--execute-code` 时，不应把任务描述误解为代码修改授权。

默认代码执行器由环境变量 `FLOWERP_CODEX_COMMAND` 指定。执行器必须接收工作台生成的提示词并返回真实退出码；失败不能伪装成成功。

也可以分阶段操作：

```bash
python -X utf8 -m workbench.cli task-create \
  --request "修复取消订单未释放预占" \
  --actor student \
  --execute-code \
  --write-scope flowerp \
  --write-scope tests
python -X utf8 -m workbench.cli task-run <TASK_ID> --actor student
python -X utf8 -m workbench.cli task-show <TASK_ID>
python -X utf8 -m workbench.cli task-review <TASK_ID> \
  --reviewer reviewer \
  --decision approve \
  --note "阻断 Eval 与业务证据均已复核"
```

自动修复与显式编排仍复用同一质量入口：

```bash
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
```

Loop 必须有最大轮次和停止条件；Graph 必须让失败回退与人工审核可见。它们都不是“自动成功”按钮。

## 质量入口与业务红线

### 标准验证命令

```bash
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m eval.harness --suite blocking
python -X utf8 -m workbench.cli demo
python -X utf8 -m agent.loop --max-rounds 3
python -X utf8 -m agent.graph --max-rounds 3
python -X utf8 -m workbench.feedback summary
```

修改业务规则时，至少补一个正常路径和一个失败路径；修改课程内容时，必须同时核对课程大纲、详细讲义和任务卡。

### 不可破坏的业务规则

1. 可用库存不得为负；预占必须原子化。
2. 同一个入库幂等键只能生效一次。
3. 订单状态只能按定义的状态机迁移；取消要释放预占。
4. 采购补货必须经过人工审批才能入库。
5. 任务、Eval 报告和反馈必须可追溯，失败不可伪装成成功。

## FlowERP 当前能做什么

FlowERP 是课程的客户项目、实验场和验收场，不是冻结夹具。当前主线覆盖：

- 主数据：组织、用户、角色、商品、仓库和基础权限。
- 库存：入库幂等、批次、预占、释放、可用库存和库存查询。
- 销售：订单创建、状态迁移、取消释放预占及相关审计。
- 采购：采购单、人工审批、审批后入库。
- 渠道与运营：渠道订单、回调租约、运行状态、备份和健康检查。
- Web：无密钥的 ERP 操作界面；状态最终落到业务服务和 SQLite。

它是可教学、可验证的单体基线，不应被表述为已经满足所有生产级 ERP 场景。上线差距、容量、高可用和安全边界以 [上线差距与验收矩阵](docs/FlowERP-上线差距与验收矩阵.md) 和 [上线运行手册](docs/FlowERP-上线运行手册.md) 为准。

<details>
<summary>运营、备份与容器命令</summary>

```bash
python -X utf8 -m workbench.cli doctor
python -X utf8 -m workbench.cli runtime-status
python -X utf8 -m workbench.cli backup
python -X utf8 -m workbench.cli verify-backup <BACKUP_PATH>
```

只启动 FlowERP 客户项目的容器：

```bash
docker compose -f deploy/docker-compose.yml up --build
```

就绪检查：<http://127.0.0.1:8000/api/v1/health/ready>。

</details>

## 可选：完整 Harness 平台

仓库还提供一个独立、可复用的完整 Harness 平台，用于研究多项目注册、Profile、Provider seam、Tool Registry、插件生命周期、Session 事件流和 Agent Loop。它是扩展挑战，**不能替代 8001 工作台、具名人审或 L01～L16 通过标准**。

安装后可以直接使用脚本入口：

```bash
harness-workbench bootstrap
harness-workbench repl
harness-workbench serve-web
```

如果希望一个命令同时注册当前项目、启动 Harness Web，并联动启动 FlowERP：

```bash
harness-workbench serve-web --boot
```

`--boot` 是组合启动开关，等价于 `--bootstrap --with-flowerp`。Harness 退出时只会关闭由它启动的 FlowERP；如果目标端口已经运行着真实 FlowERP，则直接复用，不会终止该进程。端口冲突时可以显式指定：

```bash
harness-workbench serve-web --boot --port 8090 --flowerp-port 8080
```

也可以使用模块入口：

```bash
python -X utf8 -m workbench.harness_cli bootstrap
python -X utf8 -m workbench.harness_cli repl
python -X utf8 -m workbench.harness_cli serve-web
```

Web 默认地址为 <http://127.0.0.1:8010>，运行数据位于 `.harness-runtime/`。

<details>
<summary>常用 Harness 终端命令</summary>

```text
status
projects
profiles
tools
plugins
composition
dump-config
tasks
sessions
help
exit
```

查看全部非交互命令：

```bash
harness-workbench --help
harness-workbench plugin-runtime
harness-workbench plugin-events
```

</details>

可选完整 Harness 用来对照 Session/Profile/Plugin、thread、event stream、approval 与 interrupt；边界见 [个人 AI 研发工作台](docs/reference/个人AI研发工作台.md)。不得声称已等价于其他产品或已接入官方 app-server。

## 常见问题

### `flowerp-workbench` 或 `harness-workbench` 找不到

确认已经激活 `.venv` 并执行：

```bash
python -m pip install -e .
```

所有关键能力也都有不依赖脚本入口的模块命令，例如 `python -X utf8 -m workbench.cli --help`。

### 打开的页面和文档描述不一致

先确认端口：8001 是课程工作台，8000 是 FlowERP，8010 是可选 Harness。然后强制刷新浏览器，避免旧静态资源缓存。

### 启动时报 `WinError 10013` 或“端口已被占用”

先检查目标端口是否已有监听程序：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

停止确认不再需要的进程，或者显式换一个空闲端口：

```powershell
python -X utf8 -m workbench.cli serve --port 8080
python -X utf8 -m workbench.cli serve-workbench --port 8081
python -X utf8 -m workbench.harness_cli serve-web --port 8090
```

服务不会自动抢占或终止已有进程。Windows 下监听采用独占绑定，避免两个服务悄悄共享同一个端口。

### 两个界面看到的数据不一致

这是职责分离，不一定是错误：

- `.runtime/workbench.db` 保存工作台任务与交付证据。
- `.runtime/flowerp.db` 保存 FlowERP 业务状态。
- `.harness-runtime/` 保存可选平台状态。

不要复制、改名或混用这些数据库来绕过初始化和验收。

### `course-status` 通过，但本讲没有出现红灯

`course_ready: true` 只证明合同与标签存在。请通过课程命令在隔离工作区验证“执行前红、范围内 Diff、执行后绿”，并检查当前讲的 `baseline_semantics`。

## 进一步阅读

| 想了解什么 | 文档 |
|---|---|
| 对外课表与 16 讲主题 | [课表｜Codex AI 工程交付行动营](docs/课表｜Codex AI 工程交付行动营.md) |
| 16 讲唯一课程合同 | [课程大纲](docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md) |
| 学生学习路线与逐页课件安排 | [课程蓝图](docs/courses/课程蓝图.md) |
| 个人工作台的产品边界 | [个人 AI 研发工作台](docs/reference/个人AI研发工作台.md) |
| FlowERP 领域口径 | [领域模型与业务不变量](docs/reference/FlowERP领域模型与业务不变量.md) |
| API、Web 与冷启动 | [接口与运行边界](docs/reference/FlowERP接口与运行边界.md) |
| 部署回滚操作 | [回滚手册](deploy/ROLLBACK.md) |

## 课程建设与证据诚信

本仓库按国家级一流本科课程的建设逻辑持续重构，强调学生中心、产出导向、形成性评价和持续改进；这是一项**建设目标**，不等于已经具备申报资格或已经通过认定。

人才培养方案、课程编码、学分学时、真实教学周期、学生学习记录、同行评价、团队资格和学校审核等外部证据缺失时，只能标记为“待建设”或“待校方确认”。参考仓库测试通过、模拟数据、截图和 Agent 自述都不能替代真实教学达成证据。

## 安全提示

- 不要提交 `.env`、密钥、运行数据库、备份、报告或生成产物。
- Web 页面不得包含服务端凭据；生产部署必须替换示例密码并按运行手册配置认证、来源限制和备份。
- 不要删除失败证据；修复后保留可复现命令、修订前后版本和审核记录。
- 对外演示或申报前，必须移除学生个人敏感信息和未经授权的作品。
