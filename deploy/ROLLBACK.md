# 冷启动与回滚

1. `docker compose -f deploy/docker-compose.yml build --pull`
2. `docker compose -f deploy/docker-compose.yml up -d`
3. `curl http://127.0.0.1:8000/api/health`
4. 演示失败时保留 `.runtime/reports/`；应用镜像回滚到上一个通过阻断级 Eval 的 tag。
5. SQLite 数据卷与应用镜像分离。涉及 schema 迁移时先备份数据卷，再做向前兼容迁移；本课程不演示破坏性回滚。
