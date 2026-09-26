# L12 图解与运行记录

`diagrams/` 是机制与设计示意。`latest/` 的三张 PNG 是本地实际运行数据的排版截图，不是工作台原生界面，也不是人的批准凭证。

- [采购状态、库存与流水对照](latest/receipt-compare.png)：正常、部分提交和旧键冲突来自各自独立数据库。
- [同键恢复三个时点](latest/same-key-recovery.png)：来自同一次 recovery-same-key 运行，教师移除故障后使用原键重试。
- [等待、恢复与打回](latest/wait-and-return.png)：真实 Graph 使用合成 Eval 报告与教学 reviewer；没有 Codex 开发动作。

原始运行索引：[reference/index.json](latest/reference/index.json)。每个模式保留命令、退出码、标准输出和错误；采购模式还保留报告及 `report.states.json`。快照包含采购申请、库存、库存流水、订单和订单明细；无实际客户数据或运行数据库。

status-write-failure 与 key-collision 的 Harness 退出码为 1，其余采购实验为 0。Graph 包装实验的 0 只证明观察符合当前参考行为，不证明设计缺口已经修好。产品代码尚未修复；恢复成功不能倒推第一次写入原子化。
