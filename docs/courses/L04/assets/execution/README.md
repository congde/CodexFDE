# L04 执行截图与原始记录

采集日期：2026-09-07。六张图为真实命令输出的浏览器排版截图，非工作台原生界面。创建、复验页展示部分字段；JSON 保留完整原始输出。

本次使用当前参考实现与独立运行数据库。未实施新的 V0 控制代码变更，未调用 Codex 开发库存导出，未作人类独立复验或接受。测试替身和测试中的审核身份仅用于控制逻辑检查。

实际任务：`TASK-D41D18C759`；最终状态：`review`。

| 步骤 | 截图 | 原始命令与输出 | 退出码 |
|---|---|---|---|
| 1 | [查看截图](01-valid-spec.png) | [完整记录](records/01-valid-spec.json) | 0 |
| 2 | [查看截图](02-invalid-spec.png) | [完整记录](records/02-invalid-spec.json) | 1 |
| 3 | [查看截图](03-control-checks.png) | [完整记录](records/03-control-checks.json) | 0 |
| 4 | [查看截图](04-create-task.png) | [完整记录](records/04-create-task.json) | 0 |
| 5 | [查看截图](05-run-task.png) | [完整记录](records/05-run-task.json) | 0 |
| 6 | [查看截图](06-block-unaccepted.png) | [完整记录](records/06-block-unaccepted.json) | 2 |

[有效能力 Spec](records/WB-L04-BOOTSTRAP.md) · [缺字段输入](records/missing-done.md) · [整次记录](records/manifest.json)

交付机制与故障判断见[选读](../../交付机制与故障选读.md)，本人操作按[实践操作手册](../../实践操作手册.md)执行。学生不能把参考任务编号与截图当成本人交付证据。

## 文件校验

| 截图 | SHA-256 |
|---|---|
| 01-valid-spec.png | `65ff55bcc09ab5d848f4ad1e2da1e83f1f75a1ad189a10300ab4b74d07cd7e49` |
| 02-invalid-spec.png | `fbc0fd074607e88003c7432839e10f54ebfd1cc447d5f705d58e9548eba55b4a` |
| 03-control-checks.png | `a4466b05d76f8aaec1d5112ddb92a481e8aa230d6a08d7b633984790f742a0bd` |
| 04-create-task.png | `2f5d009e145632210cc1af11dc117b8fdd13b9f630e44c332f6b974f8eae7feb` |
| 05-run-task.png | `7e78ea72adcdec3dc221426a76c58c6ed15ae915752fb4cf0f6e333355ab6786` |
| 06-block-unaccepted.png | `6f91a51974ab613084ab86e7bb58837ea472481d907eb83036d7668a9c24a106` |
