# DeepSeek Harness 对照研读与课程差距

> **目的**：先把官方产品抄明白，再决定课程 V0 抄什么。本文是研读笔记与差距表，不是“已等价”声明。
> **官方源**：`deepseek-ai/deepseek-harness`（MIT，Developer Preview）
> **本地研读副本**：`.tmp/deepseek-harness`（sparse checkout，已 gitignore）
> **核对日期**：2026-08-29
> **主文档**：`docs/architecture.md` / `architecture.zh.md`、`docs/glossary.zh.md`、`docs/tool-execution-pipeline.md`、`docs/capability-seams.md`、`docs/subsystems/session.md`、`packages/bundle/base`、`packages/bundle/headless`

---

## 0. 一句话定位

DeepSeek Harness（`dsh`）是**插件化的 Agent 运行时**，不是模型本身。

- 底层是 **Cordis**：插件向共享 `ctx` 贡献服务、类型化事件、可逆副作用。
- 口号是 **Everything is a Plugin**：模型适配器、工具注册表、Session 日志、Agent Loop、UI、沙箱……都可以从配置替换。
- 没有“打补丁的特权内核”；扩展方式是**挂载另一个插件**，卸载时注册撤销。

课程仓库 **不得** 复制 Cordis/TypeScript 实现，也不得声称与官方产品功能等价。课程要抄的是：**可验证的架构判断**（Session 真源、turn/step 语义、tool 流水线、seam 三元组、Profile 组合）。

---

## 1. 官方核心心智模型（必须先背熟）

### 1.1 Profile / Bundle / Patch

| 概念 | 官方定义 | 含义 |
|------|----------|------|
| **Profile** | Harness home 里的具名组装 | 列出叠放的 Bundle + 树外插件 + `cordis.patch.yml` |
| **Bundle** | Cordis 配置行 + 挂载代码的分发格式 | 例如 `dsh-base`、`dsh-headless`、`dsh-web-app` |
| **Patch** | 按 row id **整行替换** config | 后写覆盖先写；不是浅合并 |

官方模板 Profile：

- `web` — 浏览器面（`dsh web` ≡ `--profile web`）
- `headless` — 一次性 CLI，无 server，stdout 最终答案，exit 0/1
- `sdk` / `sdk-minimal` — JSON-RPC / 最小 SDK 树
- `acp` — 自动化 ACP server

启动查看组合树：

```sh
dsh --profile web --dump-config
dsh --profile headless "run the tests"
```

`dsh-base` 是多数 Profile 的共享第一层：模型、完整工具集、持久 Session、沙箱与审批、设置、凭据、遥测。`headless` 在其之上加**一次性 runner**，不挂 Web。

### 1.2 循环层级（最容易抄错）

官方术语（`glossary.zh.md`）：

| 术语 | 官方定义 |
|------|----------|
| **步骤 step** | **一次模型请求** + 该响应触发的工具执行 |
| **轮次 turn** | 对已接纳输入的一次排空；含 **零个或多个 step**；在模型与工具都停或策略终止后结束 |
| **Round** | 外层策略迭代（Goal Round / Ralph Round），**不是**会话里的每个 turn |

官方 turn 流（`architecture.zh.md`）：

```text
turn/start
  claim inbox 输入
  assemble prompt sections + tool schemas
  -> agent/pre-step          # waterfall: reject | enter(messages)
     step/start
     user/message（进入本步的消息）
     deriveMessages(log)     # 模型历史只从日志投影
     agent/request -> llm/stream
       -> assistant/chunk* -> assistant/message
     tool/call*
       -> tools/pre-execute -> tools/execute -> tools/post-execute
       -> tool/result*
     step/end
     （工具还欠下一次模型请求？或有新输入？→ 再开一个 step）
  -> agent/turn-stopping
turn/end
```

**硬规则：模型可见即已记录。** 抵达模型的一切必须能从 Session 日志重建；`deriveMessages()` 是投影，不是第二份真相。

### 1.3 Session 事件词汇（核心子集）

持久 Session 事件（节选自 `session.md`）：

| 事件 | 作用 |
|------|------|
| `turn/start` / `turn/end` | 打开/关闭轮次（可无 step） |
| `step/start` / `step/end` | 一次模型调用边界 |
| `user/message` | 人类提示 / inject / goal 续行 |
| `assistant/chunk` | 流式原文，保真回放 |
| `assistant/message` | 组装后的助手消息（含 usage） |
| `tool/call` / `tool/result` | 工具请求与模型可见结果 |
| `request/header` / `request/context` | 请求头/路由元数据 |

另有 live 扩展点（不一定落盘为模型历史）：`agent/pre-step`、`agent/request`、`llm/stream`、`tools/*`、`agent/turn-stopping` 等。

### 1.4 Tool 执行流水线

```text
assistant 含 tool-call
  → Session: tool/call（执行前先记日志）
  → tools/pre-execute（waterfall：hooks / permission / sandbox）
  → monotonic guards
  → （ask → ctx.approval 一次性审批）
  → tools/execute（around：timeout / retry / metrics）
  → tool body（可触发 fs/*）
  → tools/post-execute（accept / block / replace / add context）
  → finalizeContent
  → tools/result（同步通知，冻结权威结果）
  → Session: tool/result
```

要点：

1. **先记 `tool/call`，再执行**——失败也有证据。
2. `pre/execute/post` 是 **waterfall**，监听器必须 `next()`。
3. 审批在 guards 之前可拦截；拒绝则跳过 tool body，仍走 post → result。

### 1.5 Capability Seam 三元组

一个 **seam** 必须同时有：

1. **Service Definition** — 拥有 `ctx.<key>` 的服务声明
2. **Service Provider** — 一个或多个实现
3. **Consumer** — 通常是面向模型的 tool

规范例子：`dsh-shell`（定义）+ `dsh-bash-sandbox`（提供方）+ `dsh-tool-bash`（消费方）。

只挂一个 Provider 元数据、没有 Definition/Consumer，**不算** seam。

`dsh-base` 实际挂载的能力（节选 `cordis.patch.yml`）：

`llm`、`session`、`agent`、`agent-loop`、`jobs`、`settings`、`credentials`、`subprocess`、`sandbox`、`approval`、`permission`、`tool-bash`/`tool-pwsh`、`tool-fs`、`tool-web`、`tool-subagent`、`system-prompt`、持久化与 query……

### 1.6 核心 `ctx` 键（研读锚点）

| `ctx` | 包职责 |
|-------|--------|
| `ctx.sessions` | 只追加 SessionEvent 日志 + 内存 store |
| `ctx.tools` | 作用域化工具注册与把关流水线 |
| `ctx.agents` / `ctx.agentLoop` | Agent 接口与默认驱动 |
| `ctx.llm` | 适配器注册与流式词汇 |
| `ctx.systemPrompt` | prompt section + tool schema 组装 |
| `ctx.fs` / `ctx.shell` / `ctx.sandbox` | 执行世界（换远程沙箱则 Bash/PTY/LSP 一起走） |
| `ctx.approval` / permission | 人机审批与权限预设 |
| `ctx.commands` | 斜杠人类命令（不经模型） |
| `ctx.jobs` / `ctx.goals` | 后台任务与同会话目标 |

---

## 2. 课程 V0（CodexFDE workbench）真实映射

| 官方概念 | 我们现在有什么 | 抄得对不对 | 差距 |
|----------|----------------|------------|------|
| Cordis 插件树 | SQLite Profile + Python `PluginRuntime` / `EffectScope` / `PluginSupervisor` | **教学语义已落地，非同构** | 已有声明依赖、逆序清理、状态、epoch、Profile 隔离与失败回滚；仍无 Context Proxy、Bundle/Patch、异步 HMR |
| Profile `web`/`headless` | `PROFILE-DEFAULT` / `PROFILE-HEADLESS` | **部分** | 无 Bundle 叠层；headless 仍走交付 Task，不是“打印最终答案退出”的通用 agent |
| Session 真源 | `harness_session_events` 只追加 | **方向对** | 事件词汇混入了大量 `task/*`；缺 `request/header`；chunk 保真不足 |
| `deriveMessages` | `session_context.derive_messages` | **方向对** | 未强制“模型可见 ⇒ 必须可重建”的运行时不变量 |
| turn / step | `AgentLoop` 里一步 ≈ 一次 tool | **抄错了** | 官方 step = 一次 LLM 调用；我们把 tool 当成了 step |
| tool 流水线 | `tool/call → tools/pre → execute → post → tool/result` | **骨架对** | 缺 approval waterfall、monotonic guards、`tools/result` 冻结通知、fs 意图事件 |
| LLM seam | `llm.template` / `llm.codex` + chunk | **弱近似** | 无真正 `llm/stream` waterfall；无 systemPrompt 组装；Codex 不可用时回退 template |
| Agent Loop | 固定计划：spec→list→grep→read→eval | **交付专用驱动** | 不是“inbox + pre-step + 模型选工具”的通用 loop |
| Tools | spec/workspace/shell/eval/codex/mcp | **课程子集** | 缺官方默认的 fs edit、bash/pwsh 全栈、web、subagent、jobs、ask-user |
| Approval | Task `review` 具名审核 | **业务审批** | 不是工具级 `ctx.approval` 一次性 ask |
| Sandbox / fs | write_scope 校验 + Codex sandbox 参数 | **弱** | 无统一 `ctx.fs`/`ctx.sandbox` 执行世界 |
| headless | `harness-workbench run` | **形似** | 出口是 Task status，不是 agent 最终自然语言答案 |
| Web | `harness_web` 可选面板 | **演示面** | 不是 Cordis Conversation 节点驱动的 Chat |
| MCP | `mcp.call` + `HARNESS_MCP_URL` | **可选骨架** | 官方 MCP 是完整 client seam，不是单个 tool |

### 2.1 当前最危险的误解

1. **把交付工作流当成 Agent Loop**
   我们在跑 Spec→Eval→Review；官方在跑 inbox→模型→工具→再模型。两者都需要 Harness，但语义不同。要“抄明白”，必须先承认：**课程主线是交付 Harness，要借用 dsh 的 Session/Tool/Seam，而不是假装已经实现了 dsh agent-loop。**

2. **step 语义反了**
   现在 `step/start` 标在每个 `workspace.grep` 上。对照官方，应改为：一次 `llm` 调用开启一个 step，该次响应里的多个 tool 仍属同一 step。

3. **插件列表 ≠ Plugin 系统**
   早期 `activate eval.local` 只替换 Profile 配置。本轮已经增加 `PluginContract`、可逆 `EffectScope`、六态生命周期、依赖 epoch、追加式事件和失败回滚；但它仍是课程用 Python 教学内核，没有 Cordis Context Proxy、完整事件模式、Bundle/Patch 和第三方代码热加载，不能宣称 Cordis 兼容。

---

## 3. “抄什么 / 不抄什么”

### 3.1 必须抄（课程 V0 可落地、可验收）

1. **Session 只追加日志 + deriveMessages**（已有雏形，补齐词汇与不变量）
2. **tool/call 先于执行** + pre/execute/post 权限流水线（已有骨架，补 approval 钩子）
3. **Profile 组合可 dump**（已有 `dump-config`，应对齐官方“按层打印可替换行”的产品语义）
4. **headless 一次性、无常驻 server、exit code 有意义**（已有入口，语义要对齐）
5. **seam = Definition + Provider + Consumer**（文档与代码注释强制三元组，禁止只登记插件名）
6. **turn/step 按官方定义改名或迁移**（否则事件日志无法对照 dsh 研读）

### 3.2 明确不抄（或仅文档级引用）

1. Cordis 运行时与 TypeScript 包树
2. Conversation UI / WebWorker / HMR
3. DeepSeek 官方 LLM API wire extensions
4. Agent Teams / Ralph / Goal 完整产品面（课程 L11–L12 可讲思想，V0 不实现）
5. 远程沙箱全家桶（fs/shell/pty/lsp 同世界迁移）
6. 声称“已兼容 dsh 插件”或“可安装 npm `@deepseek-ai/*`”

### 3.3 下一轮按官方顺序改造（抄明白后的执行清单）

> 以下是**改造顺序**，不是已完成声明。

| 优先级 | 改造项 | 官方锚点 | 验收 |
|--------|--------|----------|------|
| P0 | 纠正 turn/step：step = 一次 LLM 调用 | glossary + architecture turn flow | **已完成**：AgentLoop 单 step 包住 LLM 计划 + 多 tool；单测断言 |
| P0 | SessionEvent 词汇对齐核心集 | session.md `SessionEventMap` | **部分完成**：核心 turn/step/tool/assistant 已对齐；`task/*` 仍为课程扩展 |
| P1 | `agent/pre-step` 与 `systemPrompt` 最小缝 | architecture 新行为表 | **已完成**：`workbench/system_prompt.py`；空请求 reject；enter 写 `user/message` |
| P1 | 工具审批钩子（ask → named approval） | tool-execution-pipeline | **已完成**：`ToolSpec.approval=ask`；`approval/ask` + `tools/result`；deny 仍写 `tool/result` |
| P1 | headless 打印“最终助手答案”到 stdout | dsh-headless README | **已完成**：`final_answer` 出 stdout，状态元数据出 stderr；exit 0/1 |
| P2 | fs/shell 作为真正 seam（定义+本地提供方+tool） | capability-seams | **已完成**：`fs.local`/`shell.local`(+`shell.deny`)；tools 经 provider 注入 |
| P2 | 持久化 seam（jsonl 可选） | session persistence | **已完成**：`persist.jsonl` 镜像事件；可从 jsonl `derive_messages` |
| P3 | MCP client 作为独立 seam（非单 tool） | 官方 MCP 材料 | **已完成**：mcp.off/http/manifest；mcp.list/mcp.call + CLI |
| P3 | 可逆插件生命周期与依赖重载 | Cordis Fiber / effect 思想 | **已完成教学子集**：requires/provides、LIFO 幂等清理、状态迁移、generation/epoch、Profile 隔离、失败回滚与持久事件；不含完整 Cordis/HMR |

---

## 4. 研读命令（本机）

```sh
# 官方源（已在 .tmp，可再拉）
git -C .tmp/deepseek-harness pull --ff-only

# 必读
# .tmp/deepseek-harness/docs/architecture.zh.md
# .tmp/deepseek-harness/docs/glossary.zh.md
# .tmp/deepseek-harness/docs/tool-execution-pipeline.md
# .tmp/deepseek-harness/docs/subsystems/session.md
# .tmp/deepseek-harness/packages/bundle/base/README.zh.md
# .tmp/deepseek-harness/packages/bundle/headless/README.zh.md

# 若本机装了正式 dsh（可选）
dsh --profile web --dump-config
dsh --profile headless "say hello"
```

课程侧对照入口：

```sh
harness-workbench dump-config
harness-workbench composition --profile PROFILE-HEADLESS
harness-workbench run --request "..."
```

---

## 5. 诚实结论

| 维度 | 评分（相对“抄明白官方架构”） |
|------|------------------------------|
| 文档理解（本文之后） | 目标：**先读懂** |
| 代码同构度 | **不再使用主观百分比**：Session、Tool、Seam 和可逆生命周期已有可运行教学语义；仍缺完整 Cordis 组合树、事件系统与真实通用 Agent 模型循环 |
| 产品等价度 | **0%**：课程交付 Harness ≠ dsh 通用 Agent Harness |
| 可继续抄的价值 | **高**：先修 turn/step 与 Session 词汇，再谈“像 dsh” |

**一句话**：DeepSeek Harness 的真核是 **Cordis 上的 Session 事件真源 + turn/step Agent Loop + 可替换 seam**；课程仓库已经从“插件化表皮”推进到具备依赖、状态、可逆副作用和回滚证据的教学运行时。剩余差距主要在完整 Cordis 组合/事件/HMR 与通用 Agent 产品面，非课程交付主线必需。
