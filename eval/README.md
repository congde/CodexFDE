# Eval 合同

`python -X utf8 -m eval.harness --suite blocking` 是本项目唯一阻断入口。Hook、CI、Loop 与 Graph 不复制测试逻辑，只读取 Harness 的退出码和 JSON 报告。

- `blocking`：业务不变量、安全边界、状态机与幂等；失败必须阻断交付。
- `observing`：课程资产、体验和可维护性提示；失败记录告警，不伪装成业务失败。
- Eval 验证可观察结果，不验证“某函数是否被调用”。
