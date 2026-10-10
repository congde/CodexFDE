# 我的个人 AI 研发工作台

这是课程提供的 L00 页面壳，沿用正式项目的目录与入口。它只提供首页、静态文件和 `/api/health`，没有账本、执行器、Eval 或 FlowERP 业务功能。

先激活课程参考仓库的 `.venv`，再进入此个人项目根目录运行；核对解释器来自参考环境、`workbench.__file__` 来自当前项目：

```text
python -X utf8 -m workbench.cli serve-workbench
```

默认访问 `http://127.0.0.1:8001/`；端口占用时增加 `--port 8002`。计划数据库为本项目 `.runtime/workbench/workbench.db`，也可用 `--runtime-dir` 明确指定。壳不创建目录或数据库，不读写既有数据库。

`workbench/cli.py` 接收启动命令；`runtime_paths.py` 决定运行数据位置；`workbench_server.py` 提供 HTTP；`workbench_web/` 保存页面。L01 再建设五个账本命令，并留下本人 Spec、实现前失败、Diff、修复后结果和自举记录；L04 首次通过受控工作台交付 FlowERP。

根入口 `python -X utf8 main.py` 默认启动同一工作台；`python -X utf8 main.py --help` 转发 CLI 帮助。架构放在 [docs/reference/工作台具体设计.md](docs/reference/工作台具体设计.md)。`agent/`、`eval/`、`scripts/`、`deploy/` 预留后续建设位置，`harness_web/` 为可选预留；不包含未来模块的假实现。

`pyproject.toml` 使用当前课程项目的历史发行名 `flowerp-fde-camp`，该名字不表示本仓库包含 FlowERP 业务包。当前登记 `codexfde` 和 `flowerp-workbench` 入口；可选 Harness 命令在实现后再登记。源码使用标准库，课程统一用 Python 3.11 运行，项目基础声明沿当前参考项目保持 `Python>=3.10`。

本目录由课程支架生成。目录存在、页面响应和 `/api/health` 输出均不证明学生已独立实现后续能力。
