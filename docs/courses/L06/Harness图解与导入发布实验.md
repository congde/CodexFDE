> 当前个人学习与执行入口已统一为[辅导资料](辅导资料.md)和[实践操作手册](实践操作手册.md)。下文保留 PPT 五项预设演示，供讲师展示阶段差异；不替代个人六项练习。

> 历史／选读资料：商品导入、六项检查及七行实验属于旧路线。当前必做库存主线及命令见[辅导资料](辅导资料.md)和[实践操作手册](实践操作手册.md)；下文旧记录不作为当前接入或验收结论。

# Harness 图解与商品导入发布实验

本材料对应当前 24 页 PPT。先弄清 Harness 如何组织一次运行，再通过商品批量导入实验观察它怎样选择检查、保存结果、按等级判决。实验使用当前 FlowERP 的实际导入服务，在临时 SQLite 数据库中执行，不操作日常业务数据。

## 1. Harness 是什么

Harness 是围绕执行对象组织一次运行的程序或机制。执行对象可以是模型与工具，也可以是一组检查函数。它接收任务和规则，准备运行条件，调用执行对象，读取真实结果，再按规则结束或交接，同时保存过程记录。

可以用五个问题辨认它：谁接收输入？谁准备环境？谁调用执行对象？谁收集结果？谁决定何时停止？只有一个模型回答，或者只有一个断言，还没有回答完整的运行组织问题。一个很小的脚本也可以承担 Harness 的职责，不要求先建设大型平台。

本讲把范围缩小到 Eval Harness。Eval 判断一条要求，例如“无效批次被拒绝，商品主表不变”。Harness 负责确认这项和其他必需检查都被运行，保存各项结果与原因，按等级计算统一结论，并输出 JSON 与进程退出码。模型可以协助开发这段程序，但不用参与每次检查。

个人研发工作台把这一质量入口放入交付过程：你确定业务规则与允许修改范围，Codex 协助修改候选，Harness 运行原检查，复验者核对报告与候选，审核者记录接受或返工理由。完整的 Agent 执行控制与本讲的质量检查组织属于不同范围。本讲的小运行器没有进程隔离、超时终止或自动返工功能。

## 2. 新案例与独立要求

运营准备导入三件商品，商品编码为 NEW-1、NEW-2、NEW-3，售价均为 1290 分。前两行名称正确，第三行缺名称。当前教学要求是整批校验通过才提交，有一行无效就拒绝整批，商品主表保持不变。预检查可以保存导入暂存记录，因此不要误写为“数据库完全不变”。

错误教学适配器逐行提交，遇到空名称直接跳过。它调用的是真实服务，却把正确服务组织成了错误的业务过程：前两件商品已经写入。检查必须同时观察是否拒绝，以及商品主表前后状态。只看最终没有抛出异常，不能证明整批导入正确。

实验登记五项：正常批次、无效批次不写入、预检查不写商品、L05 收货参考回归、帮助补图观察项。前三项独立建临时数据库，L05 项直接调用既有 `eval.cases.receiving_is_idempotent`。帮助补图是明确标记的受控失败，用来展示 observing，不代表实际检查了一个帮助网站。

## 3. 连续可执行命令

前提：已按课程完成本仓库 `.venv` 安装。在控制仓库根目录打开 PowerShell。以下操作依次执行，所有报告使用本次唯一目录。

```powershell
$py = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw '请进入控制仓库根目录，并先完成课程 .venv 安装' }
$lab = 'docs/courses/L06/examples/import_release_lab.py'
$run = Join-Path '.runtime' ('l06-import-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null

& $py -B -X utf8 $lab false-green --report-path (Join-Path $run '01-false-green.json')
if ($LASTEXITCODE -ne 0) { throw '第一阶段退出码应为 0，请保留错误输出并排查' }

& $py -B -X utf8 $lab gate-fixed --report-path (Join-Path $run '02-block.json')
if ($LASTEXITCODE -ne 1) { throw '第二阶段应阻断并退出 1' }

& $py -B -X utf8 $lab fixed --report-path (Join-Path $run '03-pass.json')
if ($LASTEXITCODE -ne 0) { throw '第三阶段应通过，仍需读取分项' }

& $py -B -X utf8 $lab fixed --rows 7 --report-path (Join-Path $run '04-transfer.json')
if ($LASTEXITCODE -ne 0) { throw '换七行数据的复验未通过' }
```

四次运行预期如下。PASS/BLOCK/WARN 是根据每项 passed 与 level 阅读出的含义。

| 阶段 | 实际分项 | 摘要 | 退出码 |
|---|---|---|---|
| false-green | 3 PASS、1 BLOCK、1 WARN | 错误地 pass | 0 |
| gate-fixed | 3 PASS、1 BLOCK、1 WARN | block | 1 |
| fixed | 4 PASS、0 BLOCK、1 WARN | pass | 0 |
| fixed，七行 | 4 PASS、0 BLOCK、1 WARN | pass | 0 |

阶段是预先提供的教学版本。程序在内存中切换汇总算法与适配方式，不会替你修改生产代码。运行演示说明你观察过这些分支，不能据此声称自己已经实现 Harness。

## 4. 读取失败并独立重算

```powershell
$falseReport = Get-Content -LiteralPath (Join-Path $run '01-false-green.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$falseReport.results | Format-Table name, level, passed, duration_ms
$falseReport.results | Where-Object name -eq 'invalid_batch_no_write' | Format-List *
$actualBlocks = @($falseReport.results | Where-Object { $_.level -eq 'blocking' -and -not $_.passed }).Count
if ($actualBlocks -ne 1 -or $falseReport.summary.blocking_failed -ne 0) { throw '没有复现预期的假绿灯，先调查分项原因' }
Write-Output '拒绝这份报告：分项存在 1 个阻断失败，摘要却写 0'

$green = Get-Content -LiteralPath (Join-Path $run '03-pass.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$green.results | Format-Table name, level, passed, duration_ms
$green.summary | Format-List
```

错误适配器应产生 `rejected=False; added=2`。修正后，无效批次应为 `rejected=True; added=0`。帮助项仍然失败，但它属于 observing，因此不阻断本次质量门。若出现导入失败、数据库初始化错误或文件不可读，应先恢复验证条件，不能把任何异常都当成预期的业务反例。

运行器用独占创建保存报告，已有路径会报错。重新开始时生成新的 `$run`，不要覆盖旧失败。每份 JSON 保留生成时间、各项毫秒耗时、错误类型和原因。耗时是观察值，不证明实现了超时中断。

## 5. 从演示到自己的实现

你的建设任务是独立补齐 `runner_starter.py` 的汇总逻辑，并为自己的模块建立合同测试。至少包括：一个阻断失败加多个通过仍必须阻断；只有观察失败时告警保留且正常退出；用例异常保留原因并继续后续项；无效选择不能形成空绿灯。最终还要把自己 L05 的原检查实际接入，并保留原输出。课堂脚本里的参考回归不能替代这项个人证据。

本讲原课程合同的库存产品增量及原隔离实操材料仍保留在原实践手册中；当前 PPT 改用商品导入解释 Harness，不再重复讲库存算术，也不把这份演示声称为原隔离任务已经完成。正式采用新案例替换整套课程实践时，需同步课程产品进度与隔离构造入口。

你应能解释：业务检查为什么失败、汇总是否正确、报告是不是来自本次候选、还有哪些未覆盖风险。真实交付的最后一步仍由复验者和审核者完成，不由参考运行或模型自述代替。

## 6. PPT 阅读对应

| PPT | 内容 |
|---|---|
| 3～7 | 新业务情境、Harness 定义、运行环节和职责范围 |
| 9～13 | Eval 拆分、统一运行、失败等级与三个输出 |
| 15～19 | 登记、循环、两层修复、独立复核和执行命令 |
| 21～24 | 换数据、人员交接、个人通过要求与总结 |

第 2、8、14、20 页是完整章节目录。新实验源文件：[import_release_lab.py](examples/import_release_lab.py)。
