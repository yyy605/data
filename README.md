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
- 采集窗口为**滚动 24 小时**：结束时间是执行时刻（或 `--date` 指定日期的当日 23:59:59），起点为终点前 24 小时；时区取 `team.timezone`
- 成员归属通过 `members` 中的 `github_login`、`lark_user_id`、`name`、`email` 映射
- 非工作日跳过：法定节假日与调休由 `chinesecalendar` 库自动识别；`schedule.holidays` / `schedule.workdays` 可手工覆盖个别日期；无需跳过时使用 `--force`
- 飞书任务采集使用 **Task v2** 接口 `/open-apis/task/v2/tasks`。该接口不支持服务端时间过滤，因此按 `updated_at` 在本地裁剪采集窗口；配置 `lark.tasklist_id` 可只采集指定任务清单（留空表示全部）

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

### Windows + WSL2 部署

Windows 上通过 WSL2 运行 Docker Engine（无需 Docker Desktop）：

```powershell
# 1. 启用 WSL2 功能（管理员），然后重启
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# 2. 安装 Ubuntu（离线镜像亦可：wsl --install --from-file xxx.wsl --name Ubuntu-24.04）
wsl --install -d Ubuntu
```

在 Ubuntu 内安装 Docker Engine 并启用 systemd：

```bash
# 换用清华源（国内网络）
sudo sed -i 's#http://archive.ubuntu.com/ubuntu#https://mirrors.tuna.tsinghua.edu.cn/ubuntu#g' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update

sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg \
  | sudo tee /etc/apt/keyrings/docker.asc > /dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu noble stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER

# 启用 systemd（否则 systemctl 不可用）
printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
```

回到 Windows 执行 `wsl --shutdown`，重新进入 Ubuntu 后：

```bash
sudo systemctl enable --now docker
docker run --rm hello-world
```

WSL2 内建议把项目放在 `~/` 而非 `/mnt/d/`（跨文件系统构建明显更慢）：

```bash
cp -r "/mnt/d/Smart Daily Report Generator" ~/smart-daily-report
cd ~/smart-daily-report && docker compose up -d --build
```

若 `docker pull` 访问 Docker Hub 失败，配置国内镜像加速器 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://hub.rat.dev"
  ]
}
```

然后 `sudo systemctl restart docker`。

### 容器内常用命令

```bash
# 单次执行（不推送）
docker compose run --rm daily-report python main.py --dry-run

# 校验配置与连通性
docker compose run --rm daily-report python main.py --check

# 立即生成一次（跳过非工作日限制）
docker compose run --rm daily-report python main.py --force
```

## 非功能约束

- **日志**：JSON Lines 格式，包含开始/结束时间、各数据源条数、推送结果
- **安全**：密钥仅通过环境变量注入；飞书消息过滤敏感关键词（薪资、绩效等）
- **降级**：单个数据源超时或失败不影响其他数据源，日报显式标注失败来源
