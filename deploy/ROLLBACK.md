# 冷启动与回滚

## 启动前

在仓库根目录运行以下命令。需要本机 Docker 引擎和 Compose 可用。首次实验使用新的 Compose 项目名与空数据卷；保留旧项目和失败证据，不用删除旧数据库制造“冷启动”。默认端口 8000、8001 必须空闲；已有服务仍在处理任务时不能直接结束它。

```powershell
docker compose version
docker info
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml config --quiet
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml build --pull
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml up -d
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml ps
```

每次新的冷启动实验换一个此前未使用的项目名，并在证据中记录该名称。重复使用上例项目名会复用数据卷，不能声称再次完成了空库启动。所有后续命令的 `-p` 必须保持一致。

| 服务 | 浏览器入口 | 独立数据卷 | 数据归属 |
| --- | --- | --- | --- |
| FlowERP | `http://127.0.0.1:8000` | `flowerp-runtime` | 客户产品、组织、业务账本 |
| 个人工作台 | `http://127.0.0.1:8001` | `workbench-runtime` | 事项、任务、复验报告与具名决定 |

卷的实际名称还带 Compose 项目前缀。容器内都挂载到 `/app/.runtime`，但不是同一个卷；不能互换。镜像构建上下文排除本地数据库、运行目录与凭据，构建检查产生的数据也不进入最终运行层。

首次空库需要初始化 FlowERP 组织和管理员；在交互终端执行，按提示输入密码，不把密码写入命令或实验记录：

```powershell
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml exec flowerp python -X utf8 -m workbench.cli init --organization FlowERP --username admin
```

## 两个页面都必须验收

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
Invoke-RestMethod http://127.0.0.1:8001/api/health
docker compose -p flowerp-l16-trial -f deploy/docker-compose.yml logs --no-color
```

健康接口只证明服务响应与其检查项。还要在浏览器登录 FlowERP，完成本次需求的实际操作并核对业务结果；打开工作台，确认事项与任务入口可用、客户项目链接正确、原始证据可查。保留镜像 ID、项目名、初始化与启动记录、失败日志、实际业务对账和具名结论，再写入发布证据索引。

如果调整 `FLOWERP_PORT`，还要同步设置 `FLOWERP_ALLOWED_ORIGINS` 为实际访问来源；`WORKBENCH_PORT` 调整工作台端口。浏览器中的 FlowERP 链接使用宿主机地址，不能填写容器服务名。端口配置不是身份认证。

## 容器启动与现场代码交付的边界

这份 Compose 以只读镜像运行产品与工作台，工作台默认只开放组织、复验和人审。镜像不携带本机 Codex 登录信息与课程 Git 历史，因此不能用“两页打开了”证明完成了 L16 的新需求现场交付。

真实代码交付在带课程基线的本机仓库中进行，按[网页代码执行说明](../docs/reference/工作台网页代码执行.md)核对范围、授权并留下起始失败、范围内改动、复验与人审证据。通过审核的候选才能进入发布构建；不能直接把隔离副本中的未审改动当作已发布成果。

## 保留证据与回滚

失败时先保存日志和两套卷中的证据。不要执行删除数据卷的操作，不要覆盖旧报告。停止这次实验可用同一项目名执行 `docker compose ... stop`；这不会删除卷。

发布前记录上一版确实通过检查的镜像 ID，并为其保留明确标签。回滚时让两个服务使用该已验证镜像，同时保留原数据卷；当前 `flowerp-course:local` 标签可能被重新构建覆盖，不能单独作为回滚依据。数据库变更需要先做一致性备份并在独立目录验证恢复；不演示破坏性 schema 回滚，也不能把正在写入的 SQLite 文件简单复制当作可靠备份。

## 当前验证状态

2026-09-05：当前维护环境未发现 Docker 或 Podman 命令。双服务配置与上述操作说明已补齐，镜像实际构建、容器健康检查和空卷冷启动仍待有引擎的环境验证；本机测试通过不能替代这些证据。
