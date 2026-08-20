# Codex + FDE 行动营｜个人研发自动化工作台

在真实业务交付中建设一套可复用的研发自动化工作台：把模糊需求变成可验收 Spec，用统一 Eval 收口质量，用有界 Loop / Graph 处理失败，再用 API、Web 与反馈把结果变成可追溯交付。

**FlowERP** 是本仓库的客户项目、实验场和验收场——提供真实需求、业务不变量、失败代价和采用反馈。它服务于工作台建设，不是一门 ERP 功能开发课。

```text
主线作品：个人研发自动化工作台
真实现场：FlowERP 连续开发与交付
学习证据：判断、实现、失败、修订、互评、迁移与答辩
```

交付主链路：

```text
Spec → Eval → Harness → 失败任务 → Loop / Graph → API / Web → Feedback → Evolution
```

## 仓库里有什么

| 目录 | 职责 |
| --- | --- |
| `workbench/` | Spec、任务 API、CLI、执行沙箱、摘要与反馈 |
| `eval/` | 唯一质量入口；Hook、CI、Loop、Graph 都复用它 |
| `agent/` | 失败任务映射、有界 Loop、显式状态图与人工审核 |
| `flowerp/` | ERP 领域模型、SQLite 持久化与业务不变量 |
| `web/` | 无密钥演示面板（ERP + 交付状态） |
| `tests/` | 单元、集成、HTTP、并发与恢复测试 |
| `deploy/` | 容器化与冷启动 |
| `course/tasks/` | 16 讲目标卡、命令卡、验收卡（本地课件） |
| `docs/` | 大纲合同、讲义与产品文档（本地资料） |

可验收合同见 [`FDE_SPEC.md`](FDE_SPEC.md)。Agent 约束见 [`AGENTS.md`](AGENTS.md)。

## 环境要求

- Python 3.10+
- Windows / macOS / Linux
- 课程跟跑线只依赖 Python 标准库与 SQLite，不需要第三方包

## 5 分钟跑通

```bash
# 演示一条含审批、库存与履约约束的业务链路（临时目录，不污染正式库）
python -X utf8 -m workbench.cli demo

# 阻断级 Eval（唯一质量入口）
python -X utf8 -m eval.harness --suite blocking

# 可选：启动 Web
python -X utf8 -m workbench.cli init --organization "FlowERP" --username admin
python -X utf8 -m workbench.cli serve --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。运行数据默认写入 `.runtime/`，不会提交到 Git。

`init` 会交互式设置管理员密码（至少 10 位）。也可跳过 `init`，在首次打开的初始化页创建组织和管理员。

## 工作台怎么用

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
电商：店铺接入 → 幂等接单 → SKU 映射/拦截 → 审单 → 预占 → 回传
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

课程建设按国家级一流本科课程（金课）口径持续重构；正式申报资格与当批次要求须由学校依据教育部最新文件确认。仓库实现与文案不能替代学生目标达成，也不能伪造申报资格或教学成效。

## 安全

不要在公开 Issue 中粘贴密码、访问令牌、真实客户数据或数据库文件。保留复现步骤与请求编号，通过维护者认可的私密渠道报告。
