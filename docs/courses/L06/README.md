# L06｜用 Harness 汇总证据和等级

本讲主线：统一库存口径，查询与 CSV 都遵守可用＝在库－预占。先修假绿汇总，再修导出字段，最后将原检查接入 L07。

1. 读[辅导资料](辅导资料.md)，理解 8−3＝5 与可信红报告。
2. 按[实践操作手册](实践操作手册.md)建立自己的候选并完成两次修复。
3. 按[行动卡](行动卡.md)核对迁移、范围与独立复验，填写[SUBMISSION](SUBMISSION.md)。
4. 按[L06 检查接入 L07](../L07/L06检查接入手册.md)注册并运行原检查，之后才安装 Hook。

主练习脚本是 [stock_practice.py](examples/stock_practice.py)，检查源码见 [checks_starter.py](examples/checks_starter.py)，运行器独立合同见 [test_runner_contract.py](examples/test_runner_contract.py)。

个人报告保存在 `.runtime/l06-stock/` 的独立会话中；缺少 L05 来源时先回上一讲补齐。教学支架与个人修复分别记录，脚本通过不等于具名接受。

## 按需选读

- [商品导入案例](商品导入选读.md)与[导入实验手册](商品导入实验手册.md)：保留的迁移案例，六项检查和七行实验不属于当前库存主线。
- [实现细节与扩展阅读](实现细节与扩展阅读.md)、[参考详解](参考详解.md)：历史原理与记录，命令以当前实践手册为准。
- [assets](assets/)、[slides](slides/)、[旧提示词](prompts/README.md)：原有材料保留；旧导入图和课件不作为本讲必做合同。PPT 本轮未重制。

[课程总入口](../../README.md) · [上一讲](../L05/README.md) · [下一讲](../L07/README.md)
