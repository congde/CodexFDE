# Codex AI 工程交付行动营：工作台驱动的 FlowERP 持续交付

项目建设主线：**用 Codex，搭建个人 AI 研发工作台；通过工作台组织人与 AI 协同，持续开发 FlowERP。** Codex 是开发伙伴，个人工作台是协同阵地，FlowERP 是持续增长的客户产品。

**FlowERP 已建立独立客户项目仓库：[https://github.com/congde/flowERP.git](https://github.com/congde/flowERP.git)。** CodexFDE 维护个人研发工作台与课程建设；FlowERP 的后续业务开发在独立仓库进行，由工作台添加该项目后组织调研、受控执行与验收。

**工作台唯一入口是 http://127.0.0.1:8001/。** 真实需求从工作台内的“事项与决策”开始，课程跟跑也使用同一工作台。普通事项已接入 Codex 源码调研、需求澄清、确认执行、候选验收与显式集成。

**启动工作台与独立 FlowERP：先准备两个仓库各自的 `.venv`，在工作台登记 FlowERP，或设置 `$env:FLOWERP_PROJECT_ROOT="D:\work\flowERP"`。** 随后在本仓库运行以下命令；启动器会从独立仓库启动客户服务，沿用本机保存的数据目录。只启动工作台可执行 `.\.venv\Scripts\python.exe -X utf8 -m workbench.cli serve-workbench`，不要求安装 ERP。

```powershell
python main.py
```

启动后访问 [研发工作台](http://127.0.0.1:8001/) 和 [FlowERP](http://127.0.0.1:8000/)。重复执行会复用同一数据目录的服务；服务在后台运行。Windows 也可双击 `打开工作台.cmd`。数据位置由本机 `.runtime/services.json` 保存，日常启动无需手动指定目录或重新初始化。

- L01～L04：做出能接收 Spec、受控修改代码并运行最小 Eval 的工作台 V0。
- L05～L15：用工作台持续交付 FlowERP；每次真实交付都反过来升级 Eval、Loop、Graph、API、Web 和反馈闭环。
- L16：从未实现的 ERP 小需求出发，现场完成一次有边界、有证据、可答辩的冷启动交付。

这里有一个不能省略的自举换挡：L01～L03 工作台尚未完成，学生直接监督 Codex 开发规则、Spec 和解析能力；L04 先用 Codex 补齐 Workbench V0，再让 V0 首次以 Spec、写集、前红、Diff、后绿和人审约束 Codex 交付库存导出；L05 起由 FlowERP 现场问题推动工作台升级，再由升级后的工作台控制 Codex 修复或交付 ERP。完整故事合同见 [Codex × FDE 双阶段故事链](docs/courses/Codex-FDE双阶段故事链.md)。

FDE 指 **Forward-Deployed Engineering**：贴近用户、数据和运行后果，通过现场循环决定做什么、交付循环约束怎样做、能力循环把重复失败沉淀为下次可复用的工作台资产。本项目不训练模型，不能把资产升级写成“模型自动进化”。

最终成果不是一份课程文档，而是两个不可拆分的可运行产品：

1. **个人研发自动化工作台**：负责把需求变成 Spec，约束执行范围，运行 Eval，保留失败、修订、审核和反馈证据。
2. **FlowERP 客户项目**：负责提供真实业务约束，并检验工作台是否真的能持续交付。

> 课程采用“案例先行、工具后置”。例如 L03 先用“库存导出”案例识别歧义、补齐验收口径，再介绍 Spec 模板、OpenSpec、Superpowers 等常见方法。通用工具用于迁移和比较，不替代对真实业务的判断。

## 两个仓库如何协作

| 仓库 | 维护内容 | 使用方式 |
|---|---|---|
| CodexFDE（本仓库） | 个人研发工作台、交付治理、课程参考实现；课程材料按现有约定仅保留本地 | 从工作台首页添加项目，在“事项与决策”中组织交付 |
| [FlowERP 独立仓库](https://github.com/congde/flowERP.git) | ERP 业务代码、客户 HTTP API、客户页面与业务测试 | 使用自己的源码目录、虚拟环境和业务数据库，接受工作台组织的受控交付 |

本仓库已移除 `flowerp/`、`web/` 和旧的合并业务 API；业务代码、客户界面和业务测试统一维护在 FlowERP 独立仓库。工作台通过独立进程启动客户服务，通过项目配置运行客户 Eval，不在进程内导入 ERP。旧数据库、已有事项与历史证据保留原归属，不自动迁移。

L04 起的本地课程快照从已配置的独立 FlowERP 仓库取入业务源码与检查，记录来源目录和逐文件 SHA-256；仅在任务隔离目录中构造缺陷与复验。历史课程 Git 标签仍表示原来的单仓库版本，不能把旧标签称为已重新验证的双仓库基线。

### 在工作台添加项目

打开 [工作台首页](http://127.0.0.1:8001/)，点击 **「＋ 添加项目」**，或在“事项与决策”中展开“管理项目”：

1. **已有本地目录**：填写项目名称和目录绝对路径，例如 `D:\work\flowERP`。已有 Git 仓库直接登记；尚未使用 Git 的目录可勾选初始化版本管理，不会自动提交或上传文件。属于其他 Git 仓库的子目录应改为登记仓库根目录。
2. **Git 仓库链接**：填写 `https://github.com/congde/flowERP.git` 和克隆目标目录。目标目录必须尚不存在，工作台不会覆盖已有文件。也支持 SSH 链接，认证使用本机 Git 的现有配置，不要在链接中填写密码或令牌。
3. **质量检查配置**：可以先留空，添加后进行调研；确认技术方案及执行代码交付前，须在“配置项目”中补齐检查命令和本机执行环境。
4. **默认项目**：可勾选“设为新事项的默认项目”。创建需求时核对所属项目；已有事项仍绑定原项目。

添加项目不会自动安装依赖、运行代码或完成业务验收。调研和执行以所选项目的目录为边界，工作台源码变更不会混入另一个独立仓库的文件快照。更新工作台代码后，须重启原服务加载新后端；已有工作台仍须沿用原运行目录。

### 启动独立 FlowERP

如果通过 Git 链接添加，克隆已经完成；如果目录尚未取得，也可以先在终端执行 `git clone https://github.com/congde/flowERP.git D:\work\flowERP`，再按本地目录添加。以下为 Windows 示例，在独立仓库中准备环境并启动客户服务：

```powershell
Set-Location D:\work\flowERP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -X utf8 -m flowerp serve --port 8002 --runtime-dir .runtime
```

示例使用 [8002 端口](http://127.0.0.1:8002/)，便于与迁移前仍运行的 8000 旧服务区分；独立 FlowERP 自身默认端口仍为 8000。已有虚拟环境或业务数据库时沿用现有配置，不要重复初始化业务数据。

在工作台“质量检查配置”中填入以下参数数组；本地路径不同则相应修改。命令从隔离候选目录运行，`{report_path}` 由工作台替换为本轮报告路径：

```json
["D:/work/flowERP/.venv/Scripts/python.exe", "-X", "utf8", "-m", "eval.harness", "--suite", "blocking", "--report-path", "{report_path}"]
```

工作台仍从 CodexFDE 仓库启动。若客户服务使用上述 8002 端口，在保留工作台原端口和运行目录的前提下，为 `serve-workbench` 增加 `--erp-url http://127.0.0.1:8002`。`python main.py` 的客户服务入口已改为独立仓库；若登记了多个包含 FlowERP 入口的项目，须用 `FLOWERP_PROJECT_ROOT` 明确选择。已有端口上的旧进程不会因为源码迁移自动更新，须在维护时重启。独立项目的完整运行说明见 [FlowERP README](https://github.com/congde/flowERP/blob/main/README.md)。

## 先认清三个入口

| 入口 | 是否跟跑必做 | 用途 | 默认地址 / 数据 |
|---|---:|---|---|
| 个人研发工作台 | 是 | 日常研发、课程任务、交付状态与证据链 | <http://127.0.0.1:8001> · `.runtime/workbench.db` |
| FlowERP 客户项目 | 是 | 操作库存、订单、采购等 ERP 业务 | <http://127.0.0.1:8000> · `.runtime/flowerp.db` |
| 完整 Harness 平台 | 否，可选挑战 | 体验 Profile、Provider、插件、Session 与多项目平台能力 | 终端 REPL / <http://127.0.0.1:8010> · `.harness-runtime/` |

**8001 是工作台，8000 是客户项目。** 两个界面、两个数据库、两个职责，不能混用。8010 只属于可选的完整 Harness 平台，不是 L01～L16 的通过条件。

所有工作台操作统一从 [工作台首页](http://127.0.0.1:8001/) 进入，不另设日常研发页面。表中均为默认端口；自定义端口时仍使用该服务的根路径 `/`。

表中的数据库路径是历史默认位置，**不代表本机当前服务的数据位置**。日常启动使用 `python main.py` 自动读取本机配置；实际目录和排错命令见下文，不要直接套用首次安装命令。

### 当前能力与边界

- 首页提供事项与决策、课程任务、交付状态与审核证据。
- 支持添加本地项目目录或通过 Git 链接克隆项目，分别配置质量检查命令，并选择新事项的默认项目。
- 同一事项保存业务讨论、Codex 调研方案、具名决定、每轮执行事件、真实 Diff、独立 Eval 和补丁。
- 返工以此前候选为起点创建新隔离副本；接受候选后，另行确认才能将累计改动集成到项目源码。
- 验收候选不自动合并；执行速度、失败续修、多轮协作与集成发布仍需完善。

2026-09-06 曾验证一例真实 Codex 文档任务：退出码为 0，无越界改动，独立阻断检查 29 项通过，并保存补丁。这是后端执行链路的验证记录，不代表完整工作台体验或教学成效。

## 60 秒理解这个项目

一次完整交付不是“让 Codex 写完代码”，而是下面这条可追溯链路：

```text
真实 ERP 需求
  → 明确范围与不可破坏规则
  → 形成可验收 Spec
  → 记录执行前检查；缺陷修复保留可复现失败
  → Codex 在允许写集内修改
  → 运行同一套阻断 Eval
  → 人工审核
  → 交付摘要与反馈
  → 将重复问题沉淀回工作台
```

日常研发允许既有检查在执行前为绿，不会人为制造红灯。新需求仍需补充对应验证；现有阻断检查通过不能单独证明需求完成。课程隔离交付的前红、Diff、后绿要求按本讲合同执行。

课程始终同时观察三条线：

- **方法主线**：工作台如何从最小闭环成长为可复用的交付系统。
- **产品主线**：FlowERP 如何从主数据逐步增长到库存、订单、采购和可操作 Web。
- **学习证据**：学生能否留下首次判断、失败、修订、互评和迁移证据。

**课程资料当前仅保留本地。** `docs/` 按仓库现有约定不纳入 Git，新克隆不会包含以下讲义、课件和合同链接的目标文件。课程命令及部分检查需要这些资料，请从课程提供方取得匹配版本；源码提交不等于完整课程材料发布。

学生从 [课程资料总入口](docs/README.md) 开始，课堂投影与复习使用 [L01～L16 独立课件](docs/courses/slides/README.md)。对外课程名与 16 讲标题以 [课表｜Codex AI 工程交付行动营](docs/课表｜Codex AI 工程交付行动营.md) 的「主题」列为准，每讲四项内容合同以 [16 讲课程大纲](docs/课程大纲-Codex-FDE行动营-个人研发自动化工作台.md) 为准。基础较弱或尚未配置环境的学员先完成 [L00 课前准备](docs/courses/L00-课前准备-安装工具与通过环境自检.md)及其[行动卡](docs/courses/tasks/L00-课前准备.md)。L00 不计入正式 16 讲，也不产生工作台或 FlowERP 产品增量。

## 5 分钟跑起来（本仓库课程参考环境）

### 1. 准备环境

仓库要求 Python 3.10 或更高版本；课堂统一使用 Python 3.12.x。课程跟跑线默认只使用 Python 标准库和 SQLite，不依赖外部服务。学员跟课请先完成 L00，不要把本节当作 L01 已完成。

实际调用 Codex 修改代码，还需要可用的 Git、Codex CLI 及其模型访问环境。工作台的标准库实现与本地检查不等于模型可以离线运行；请在启动工作台的同一终端确认 CLI 能实际执行任务。

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

本仓库默认阻断 Eval 检查工作台；FlowERP 的业务阻断 Eval 在独立仓库运行。下方 `demo` 是兼容命令，会转交独立 FlowERP 进程，因此需要先配置客户项目。课程中显式选择的 ERP 检查仍保留原用例名，执行目标及退出码进入证据。

```bash
python -X utf8 -m workbench.cli demo
python -X utf8 -m eval.harness --suite blocking
```

课程跟跑另运行 `python -X utf8 -m workbench.cli course-status`，检查课程合同、Eval 映射和线性标签；`course_ready: true` 只说明课程合同可跟跑，不代表学生已经亲手构造了每讲能力。通用研发执行后端不要求课程标签，但沿用的阻断检查仍可能依赖本地课程合同。

### 3. 启动两个必做界面

#### 一条命令自动加载两个系统

在仓库根目录执行 `python main.py`，或双击 `打开工作台.cmd`，即可同时启动或复用工作台（8001）与 FlowERP（8000）。从系统 Python 调用 `main.py` 时会转入本仓库 `.venv`；请勿使用其他项目的已激活虚拟环境。服务在后台运行，启动命令完成后仍可访问页面。需要打开浏览器时使用 `python main.py --open-browser`。

启动结果中 `started` 表示新启动，`reused` 表示复用已有服务；若端口属于其他服务或不同数据目录，会报错，不会自动切换端口或数据库。通过该入口新启动的工作台启用网页代码执行能力，每次具体执行仍需在网页核对方案并授权；复用服务时保留其原有启动设置。

两个入口及 `workbench.cli serve-workbench`、`workbench.cli serve` 都读取本机 `.runtime/services.json`，当前内容为：

```json
{
  "workbench": ".runtime",
  "flowerp": ".runtime/flowerp-restored-20260906-123646"
}
```

配置路径相对于仓库根目录解析；配置错误或数据库缺失会报错，不会悄悄换成空库。本机配置不提交 Git。未配置的旧安装优先沿用 `.runtime` 中各自已有的数据库；全新安装分别使用 `.runtime/workbench` 与 `.runtime/flowerp`。`desktop-launch-*` 是启动锁目录，不是业务数据目录。

`main.py --runtime-dir` 只覆盖工作台目录，`--erp-runtime-dir` 只覆盖 ERP 目录；`--port` 指工作台端口，`--erp-port` 指 ERP 端口。这个入口现在启动两个系统，旧的单 ERP 启动方式请使用 `python -m workbench.cli serve`。显式指定目录仍用于隔离实验；日常启动无需手写目录。以下完整路径命令用于排错。

#### 本机已有数据位置与手动排错（2026-09-06 恢复后）

在 `D:\work\CodexFDE` 下执行。先检查 8000、8001 是否已有服务；已有服务可访问时直接使用。需要重启时先确认没有执行中的任务，再停止对应服务，保持下列运行目录不变。

| 服务 | 当前运行目录 | 主数据库 |
|---|---|---|
| 研发工作台 | `.runtime` | `.runtime/workbench.db`（原库） |
| FlowERP | `.runtime/flowerp-restored-20260906-123646` | 该目录下的 `flowerp.db`（从原 ERP 库完整备份恢复） |

日常使用 `python main.py` 即可。只有需要分别在前台运行服务排错时，才在两个 PowerShell 终端分别执行以下命令：

```powershell
# 终端 A：研发工作台，沿用原有任务、事项与证据目录
.\.venv\Scripts\python.exe -X utf8 -m workbench.cli serve-workbench --runtime-dir .runtime
```

```powershell
# 终端 B：FlowERP，沿用恢复后的业务数据目录
.\.venv\Scripts\python.exe -X utf8 -m workbench.cli serve --runtime-dir .runtime/flowerp-restored-20260906-123646
```

需要网页调用 Codex 执行代码时，在工作台命令末尾加 `--enable-code-execution`，其余参数保持不变；每次执行仍需在网页核对方案并授权。

恢复时已通过工作台 API 读取到 10 个任务、5 个事项；恢复后的 ERP 库包含 6 个用户、8 个商品、14 张销售单据。这些是当时的本地记录数量，包含课程模拟记录，不代表真实教学成效，也不是以后启动必须满足的固定数量。

**已有数据时不要再次初始化、删除数据库或为了启动方便改用空目录。** 此前使用 `.runtime/desktop-launch-workbench` 和 `.runtime/desktop-launch-flowerp`，导致页面看起来没有数据；原库并未丢失。重启不得再切回这两个目录。原 `.runtime/flowerp.db` 已保留，但恢复后的 ERP 新写入位于上表目录，不能在两份库之间轮换启动。

恢复前备份保存在 `.runtime/backups/runtime-restore-20260906-123646/`。运行数据库、备份与日志仅保留本地，不提交 Git；新克隆的仓库不会包含这些数据。未来迁移须先备份、记录新位置并更新本节，不能仅复制主库而遗漏工作台证据、隔离副本或运行配置。

启动后可检查实际工作台路径、历史记录和 ERP 初始化状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/health
Invoke-RestMethod http://127.0.0.1:8001/api/tasks
Invoke-RestMethod http://127.0.0.1:8001/api/v1/initiatives
Invoke-RestMethod http://127.0.0.1:8000/api/v1/setup/status
```

工作台健康接口的 `database` 应指向原 `.runtime/workbench.db`；ERP 的 `initialized` 应为 `true`。若突然出现“创建您的工作空间”或历史列表为空，先核对服务的 `--runtime-dir` 和数据库，不要立即新建账号。恢复后的本地 ERP 当时返回 `authentication_required: false`；启用认证的环境使用已有组织代码、账号和密码，不存在通用默认密码。

#### 首次安装且没有历史数据

以下目录仅用于新安装。已有本机数据请使用上节命令，不要切换到这些新目录。

终端 A——个人研发工作台：

```bash
python -X utf8 -m workbench.cli serve-workbench --runtime-dir .runtime/workbench --enable-code-execution
```

打开工作台唯一入口 <http://127.0.0.1:8001/>。启用执行入口不会立即改代码，每次仍需核对具体方案并授权；不带 `--enable-code-execution` 则只开放复验。网页代码执行仅允许绑定本机回环地址。

已有服务时，先核对其执行任务，再从原终端停止并按相同端口、运行目录与客户项目地址重启；不要为了开启入口另建空白账本。`--runtime-dir` 决定保存任务与证据的位置。

可选的完整 Harness（8010）不是这个必做工作台（8001）的替代入口；需要联动 FlowERP 时使用后文的 `harness-workbench serve-web --boot`。

终端 B——FlowERP 客户项目：

```bash
python -X utf8 -m workbench.cli init --runtime-dir .runtime/flowerp --username admin
python -X utf8 -m workbench.cli serve --runtime-dir .runtime/flowerp
```

`init` 会在终端中安全提示输入并确认管理员密码。打开 <http://127.0.0.1:8000>，组织代码使用 `DEFAULT`，使用刚创建的管理员账号和密码登录。首次安装的工作台与 ERP 使用独立目录；以后可用 `python main.py` 自动加载。初始化、备份和管理命令仍应显式指定对应数据目录的 `--runtime-dir`，不要删除数据库来“重新开始”。

| 你看到的内容 | 正确端口 |
|---|---:|
| 日常研发、课程任务、Spec、Eval、事件与审核证据 | 8001 |
| 商品、库存、销售订单、采购单和运营状态 | 8000 |
| Profile、Provider、插件和 Session 平台视图 | 8010（可选） |

## 真实需求从事项开始

在工作台首页填写署名，进入“事项与决策”，记录问题、目标、验收条件和本期不做的内容。例如“优化财务报销”应先明确具体痛点，并核对 FlowERP 已有实现。

1. 点击“提出事项”，只填写“优化 FlowERP 财务报销”也可保存。
2. 点击“让 Codex 调研并讨论”，工作台实际以只读模式调用本机 Codex 检查代码，并在事项里展示发现和业务问题。
3. 在同一事项回答问题；信息足够后，核对本期目标、验收条件、非目标与实施步骤，指定验收人并确认。源码路径由 Codex 调研提出，放在展开详情中供核对。
4. 点击“授权工作台执行本轮方案”。工作台冻结源码与 Spec，在隔离副本调用 Codex，运行前后阻断级 Eval，保存过程、实际改动和补丁。
5. 在“本轮成果”查看候选和检查；不满意就在讨论框反馈，下一轮延续此前候选，旧记录不覆盖。
6. 由已指定的验收人填写依据，接受候选后再确认集成。工作台核对原始源码和候选指纹，保留备份，将累计改动写回源码；不会自动提交 Git 或部署。

调研会保存从当前磁盘采集的源码片段、文件位置和校验信息，以及实际 Codex 调用记录。调研期间源码更新时保留讨论结果，并阻止过期方案进入执行。后台 CLI 使用独立会话环境，避免复用桌面任务的工具连接。本机试运行曾出现进程启动和读取延迟，超时或中断均保留记录，不能据此声称稳定的响应时延。

本机须已安装并登录 Codex CLI，工作台须启用 `--enable-code-execution`（原有端口、运行目录等参数保持不变）。讨论、执行与集成均异步运行，刷新页面可以继续查看；服务重启会停止未完成轮次并保留失败记录，不会重复执行。当前支持选择已登记的独立本地项目，每个事项绑定一个项目；尚不支持一次交付同时修改多个仓库，也不自动解决集成冲突。阻断级 Eval 证明已有规则未被破坏，不能替代本期需求的测试和人审。

已有执行任务、隔离副本、失败报告与补丁继续保留在原工作台运行目录。移除独立页面不删除历史交付证据。后端能力及当前边界见本地 [工作台研发能力与入口约定](docs/reference/daily-development.md)。

## 16 讲怎样推进同一个系统

| 阶段 | FlowERP 产品状态 | 工作台新增或验证的能力 | 关键学习证据 |
|---|---|---|---|
| L01～L04 · V0 | 建立商品/仓库基线，交付第一个库存导出切片 | 仓库约束、可验收 Spec、受控修改、最小 Eval | 首次判断、红灯、范围内 Diff、绿灯 |
| L05～L08 · 质量链 | 幂等入库、可用库存、订单与原子预占逐步可用 | 失败优先 Eval、证据汇总、本地护栏、CI 复验 | 同一失败能被本地与 CI 稳定复现 |
| L09～L12 · 自修复编排 | 取消释放库存、订单状态机、采购审批与入库 | 失败转任务、有界 Loop、独立子任务、显式 Graph 与人审 | 停止条件、回退路径、职责分离 |
| L13～L15 · 产品化 | ERP 能力通过 API 和 Web 被真实操作 | 任务 API、工作台面板、摘要与真实反馈 | API/持久化一致、审核记录、修订前后对比 |
| L16 · 冷启动答辩 | 现场交付一个此前未实现的受控 ERP 小需求 | 复用整条工作台交付链 | 新红灯、真实 Diff、新绿灯与具名答辩 |

每讲先说明 Codex 如何与学生、业务人员、复验者或审核者协作，再回答四问：交付了什么 ERP 状态；暴露了什么重复工程问题；工作台新增或验证了什么能力；什么证据证明学生能迁移该能力。

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
python -X utf8 -m workbench.cli course-submit --lesson 4 --execute-code --actor student --bootstrap-task-id TASK-已接受的工作台任务编号
python -X utf8 -m workbench.cli course-eval --lesson 4
```

- `--execute-code` 明确授权 Codex 在本讲允许写集内修改代码。
- L04 先完成工作台前置验收，将示例中的任务编号替换为已接受的 `WB-L04-BOOTSTRAP` 任务编号。
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
| [FlowERP `flowerp/`](https://github.com/congde/flowERP/tree/main/flowerp) | ERP 领域模型、SQLite 持久化与业务服务 |
| [`workbench/`](workbench/) | Spec、任务 API、CLI、交付摘要与反馈 |
| [`eval/`](eval/) | 唯一质量入口；Hook、CI、Loop、Graph 都复用它 |
| [`agent/`](agent/) | 失败任务映射、有界 Loop 与显式状态图 |
| [`workbench_web/`](workbench_web/) | 个人研发工作台统一界面，默认 8001，首页为唯一入口 |
| [FlowERP `web/`](https://github.com/congde/flowERP/tree/main/web) | FlowERP 客户项目界面，默认 8000 |
| [`harness_web/`](harness_web/) | 可选的完整 Harness 平台界面，默认 8010 |
| [`docs/courses/slides/`](docs/courses/slides/) | 与极客时间主题逐讲对应的 16 份独立 PPT |
| [`docs/courses/tasks/`](docs/courses/tasks/) | 16 讲目标卡、命令卡和验收卡 |
| [`docs/courses/`](docs/courses/) | L00～L16 学生讲义、课程蓝图、任务卡与实验 |
| [`docs/reference/`](docs/reference/) | 工作台、FlowERP 领域与运行边界参考资料 |
| [`deploy/`](deploy/) | 容器化、运行与回滚资料 |

## 一次工作台任务怎样交付

工作台用户统一从首页进入：添加独立项目并配置 Eval，在“事项与决策”中选择该项目、提出需求、核对方案和写集，再授权执行、复验与具名验收。每个项目使用自己的隔离候选和检查命令，交付包保留来源、Diff、检查结果与人工决定。

旧版 `task-submit/task-create/task-run` 是直接操作当前目录的开发排错入口，不会自动选择登记的客户仓库。不要再在 CodexFDE 根目录使用 `--write-scope flowerp` 交付 ERP；本仓库已经没有该业务目录。客户需求使用首页的项目交付流程。

默认调用 `codex`；可通过环境变量 `FLOWERP_CODEX_COMMAND` 指定 Codex CLI 可执行文件。工作台独立记录退出码、实际文件变更与检查结果，失败不能伪装成成功。

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

先确认端口：8001 是个人研发工作台，唯一入口为 `/`；8000 是 FlowERP，8010 是可选 Harness。然后强制刷新浏览器，避免旧静态资源缓存。

### 普通事项如何驱动 Codex

在首页“事项与决策”内使用“与 Codex 推进这件事”。工作台通过本机 Codex CLI 调研与执行，实际启动参数和任务文字保存在事项的“工作台怎样调用 Codex”中。每轮业务决定、执行授权与验收分别记录；开启执行模式不会自行启动需求，也不会替用户作出验收决定。

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
| 工作台研发能力与入口约定（本地资料） | [当前边界](docs/reference/daily-development.md) |
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

项目日常交付可参阅 [从项目需求到效果回收](docs/reference/项目驱动交付.md)：在本机工作台中选择项目、确认 PRD 与技术方案、调用 Codex CLI 开发和测试，再登记人工发布与真实效果。
