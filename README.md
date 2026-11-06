# 智能日报生成器

自动从 GitHub、飞书任务与飞书群消息中采集团队当日工作数据，生成结构化日报（Markdown + HTML），并通过邮件与飞书机器人推送。

需求与设计见 [proposal.md](./proposal.md)、[design.md](./design.md)、[task.md](./task.md)。

## 功能概览

- **数据采集**：GitHub Commit（含变更统计）、飞书项目任务状态变更、飞书群消息（关键词筛选）
- **日报生成**：按“代码提交 → 任务进展 → 协作沟通”三段式编排，支持 Markdown 与 HTML
- **推送通知**：SMTP 邮件 + 飞书自定义机器人 webhook
- **容错**：单数据源失败不阻断其他源，日报中标注“数据获取失败”；全部失败则不生成日报
- **定时**：每个工作日 18:00 执行，自动跳过周末与配置的节假日

## 目录结构

```
collector/   # 数据采集：github.py / lark_task.py / lark_msg.py
generator/   # 日报生成：formatter.py / template.py
notifier/    # 推送通知：email.py / lark_bot.py
shared/      # 配置、日志、异常、SQLite 存储
tests/       # 单元测试 + 端到端集成测试
main.py      # 编排入口
docker/      # 容器入口脚本（cron 调度）
```

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（依赖与虚拟环境管理）

## 快速开始

```bash
uv sync
cp config.yaml.example config.yaml   # 按需修改团队成员映射、仓库、群组等
cp .env.example .env                 # 填入真实密钥
```

`config.yaml` 中的密钥均以“环境变量名”引用，真实值通过环境变量注入，不写进配置文件。

### 常用命令

```bash
uv run python main.py --check     # 校验配置、环境变量与各服务连通性
uv run python main.py --dry-run   # 采集并生成日报，但不推送
uv run python main.py             # 完整流程：采集 → 生成 → 存库 → 推送
uv run python main.py --date 2026-11-06 --force   # 指定日期并跳过非工作日限制
uv run pytest -q                  # 运行全部测试
```

退出码：`0` 表示成功（含跳过非工作日、dry-run），`1` 表示配置错误、全部数据源失败或推送失败。

### 环境变量

| 变量 | 说明 |
| --- | --- |
| `GITHUB_TOKEN` | GitHub 访问令牌 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭据 |
| `LARK_WEBHOOK_URL` | 飞书群机器人 webhook |
| `SMTP_HOST` / `SMTP_PORT` | 邮件服务地址与端口 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 邮件账号 |
| `SMTP_FROM` | 发件人地址 |

变量名可在 `config.yaml` 中通过 `*_env` 字段自定义。

### 数据说明

- 日报历史记录写入 SQLite（`storage.sqlite_path`，默认 `data/reports.db`）
- 采集窗口默认为当日 00:00 至执行时刻（时区取 `team.timezone`）
- 成员归属通过 `members` 中的 `github_login`、`lark_user_id`、`name`、`email` 映射
- 非工作日跳过：周末由 `schedule.skip_weekends`（默认开启）控制，法定节假日写入 `schedule.holidays`（`YYYY-MM-DD` 列表）；无需跳过的场景可使用 `--force`

## Docker 部署

```bash
cp config.yaml.example config.yaml
cp .env.example .env        # 填入真实密钥
docker compose up -d --build
docker compose logs -f
```

容器内由 cron 按 `CRON_SCHEDULE`（默认 `0 18 * * 1-5`）与 `CRON_TZ`（默认 `Asia/Shanghai`）触发，日志输出到 Docker 标准输出，并配置了每小时一次的健康检查（等价于 `main.py --check`）。

如需单次执行而非调度：

```bash
docker compose run --rm daily-report .venv/bin/python main.py --dry-run
```

### 非容器部署（宿主机 cron）

```cron
0 18 * * 1-5 cd /opt/smart-daily-report && .venv/bin/python main.py >> logs/daily-report.log 2>&1
```

## 非功能约束

- **日志**：JSON Lines 格式，包含开始/结束时间、各数据源条数、推送结果
- **安全**：密钥仅通过环境变量注入；飞书消息过滤敏感关键词（薪资、绩效等）
- **降级**：单个数据源超时或失败不影响其他数据源，日报显式标注失败来源
