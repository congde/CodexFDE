# CI_GATE_SPEC｜远程复验不变量

> 学员在 L08 先写反例，再实现 `workbench.ci_evidence`。本文件是终态参考合同，不是学生可以跳过动手的答案。

## 来源

课程主线 L08，由「Job 绿但 Harness block」的假绿事故整理而来。

## 目标

本地与 CI 使用同一 Harness 入口；失败必须留下可下载、可重算、绑定 Run 身份的 Evidence Envelope。

## 非目标

- 不在 Workflow 里复制业务规则。
- 不把自动修复直接合并主分支。
- 不把信封做成 FlowERP 专用脚本。

## 约束

1. 阻断失败时进程非零，不得吞掉退出码。
2. 未取消的失败 Run 仍须生成 Harness 报告。
3. 报告文件缺失时信封命令非零，且不得留下假成功文件。
4. `GITHUB_SHA` 或 `GITHUB_RUN_ID` 缺失时拒绝生成信封。
5. 信封绑定 workflow、runner、Python 与报告 SHA-256。
6. 自动修复不得直接合并主分支。

## 验收用例

1. 正常报告 + 完整身份 → 写出信封，决策与报告一致。
2. 报告不存在 → 非零退出，输出路径不出现。
3. 缺 `GITHUB_SHA` 或 `GITHUB_RUN_ID` → 非零退出。

## 完成定义

`python -X utf8 -m workbench.ci_evidence` 与 `python -X utf8 -m unittest tests.test_ci_evidence -v` 通过；Run A/B/C 身份可被同伴独立复算。
