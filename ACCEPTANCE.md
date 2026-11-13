# 智能日报生成器 — 验收报告

| 项目 | 内容 |
| --- | --- |
| 仓库 | https://github.com/yyy605/data |
| 分支 | `main` |
| 被验收的代码提交 | `28bd845`（验收基线，本报告单独提交） |
| 验收日期 | 2026-11-13 |
| 代码规模 | 源码 16 文件 2540 行；测试 11 文件 1565 行 |
| 自动化测试 | **81 个用例，全部通过** |

本报告只记录**实测结果**。无法在当前环境验证的项目均明确标注为「待验证」，不做推测性结论。

---

## 一、验收结论

| 类别 | 通过 | 待验证 |
| --- | --- | --- |
| 功能验收（proposal 3.1） | 7 / 7 | 0 |
| 性能验收（proposal 3.2） | 3 / 3 | 0 |
| 边界场景（proposal 3.3） | 4 / 4 | 0 |
| 技术约束（proposal 2.3） | 5 / 5 | 0 |

**总体：全部验收项通过。** 飞书三项权限已开通、机器人已入群，采集链路与推送链路均已端到端实测。飞书两个数据源当前**返回 0 条记录**，属于业务侧尚无数据，不构成验收阻塞（见第七节）。

---

## 二、功能验收

| # | 验收项 | 结果 | 实测证据 |
| --- | --- | --- | --- |
| 1 | 获取指定 GitHub 仓库过去 24h 的 Commit | ✅ 通过 | 完整推送实测采集 **17 条** commit，含作者、`+/−` 变更统计与提交标题 |
| 2 | 获取飞书当日状态变更任务 | ✅ 通过 | 权限 `task:task:read` 已授予（`--check` 中 `lark_scope_task` PASS）；`/open-apis/task/v2/tasks` 返回 `code=0`。可见范围内任务 **0 条**，故内容为空但链路正常 |
| 3 | 按关键词提取飞书群讨论 | ✅ 通过 | 权限 `im:message:readonly` 已授予（`lark_scope_msg` PASS）；近 90 天 8 条消息全部为 `system` 通知与机器人卡片，无真人文本，故内容为空但链路正常 |
| 4 | 日报包含三个核心板块 | ✅ 通过 | 生成结果含 `### 代码提交` `### 任务进展` `### 协作沟通` |
| 5 | Markdown 可直接渲染为 HTML | ✅ 通过 | 输出 `<!doctype html>` 完整文档，含内联样式；转换器单测覆盖 |
| 6 | 邮件送达团队 Leader | ✅ 通过 | 真实 SMTP 发送成功：`push_email=True, attempts=1`（QQ 邮箱 465/SSL） |
| 7 | 飞书机器人推送到群组 | ✅ 通过 | 真实 webhook 推送成功：`push_lark_bot=True, attempts=1`，使用 `interactive` 卡片 + `lark_md` |

---

## 三、性能验收

| # | 验收项 | 结果 | 实测数据 |
| --- | --- | --- | --- |
| 1 | 5 人团队单次生成 < 60s | ✅ 通过 | 完整流程（采集+生成+存库+双通道推送）**11.89s**，其中 GitHub 采集 17 条 commit |
| 2 | 单数据源失败不阻断其他源 | ✅ 通过 | 飞书两源失败时，GitHub 采集与推送全部正常，退出码 0 |
| 3 | 失败数据源显式标注 | ✅ 通过 | 日报输出 `- **数据获取失败**（飞书任务）`，无静默跳过 |

### 实测容量上限（模拟 200ms/请求）

| 场景 | 请求数 | 耗时 | 60s 内 |
| --- | --- | --- | --- |
| 25 commits | 26 | 5.2s | ✅ |
| 100 commits | 101 | 20.3s | ✅ |
| 200 commits | 202 | 40.5s | ✅ |
| 300 commits | 303 | 60.8s | ❌ |

**推荐运行范围：5–15 人团队，单窗口 commit ≤ 200。** GitHub 采集为串行 N+1 请求，是唯一瓶颈；飞书消息（服务端时间过滤）与日报渲染（50 人 3.6ms）均不构成瓶颈。

---

## 四、边界场景

| # | 场景 | 结果 | 说明 |
| --- | --- | --- | --- |
| 1 | 成员当日无记录 | ✅ 通过 | 显示 `- 今日无记录` |
| 2 | GitHub 限流等待重试（≤3 次） | ✅ 通过 | 仅当 `X-RateLimit-Remaining: 0` 或存在 `Retry-After` 才判定限流并按 reset 时间等待 |
| 3 | 飞书 Token 过期自动刷新 | ✅ 通过 | `99991663/99991664/99991668` 触发刷新并重试 1 次 |
| 4 | 非工作日自动跳过 | ✅ 通过 | 周末 + 法定节假日 + 调休由 `chinesecalendar` 自动识别（已验证 2026-01-01 跳过、2026-10-10 调休上班） |

---

## 五、技术约束

| 约束 | 结果 |
| --- | --- |
| Python 3.11+ | ✅ `requires-python = ">=3.11"` |
| Docker 容器化部署 | ✅ 镜像 288.7 MB（275 MiB）构建成功，容器内 `--check`、`--dry-run` 与 cron 调度均验证通过 |
| Cron 定时、无常驻服务 | ✅ 容器内 `/etc/cron.d/daily-report`，`0 18 * * 1-5`，`CRON_TZ=Asia/Shanghai` |
| 本地 SQLite 存储 | ✅ `data/reports.db`，写入/查询/覆盖更新均有单测；另导出 `reports/<日期>.md` 与 `.html` 副本 |
| 外部依赖 | ✅ GitHub API、飞书开放平台、SMTP 均实测可达 |

---

## 六、环境自检结果（`python main.py --check`）

```
config           PASS  configuration loaded
environment      PASS  all referenced variables are set
github           PASS  HTTP 200
lark             PASS  tenant_access_token acquired
lark_scope_task  PASS  granted
lark_scope_msg   PASS  granted
lark_scope_chat  PASS  not required (lark.chat_id is configured)
smtp             PASS  smtp.qq.com:465 reachable
lark_webhook     PASS  open.feishu.cn reachable
```

退出码 `0`。`lark_scope_chat` 在 `lark.chat_id` 已配置时视为可选——该项权限只用于自动发现群 ID。

---

## 七、飞书接入现状

### 已解决

| 问题 | 现象 | 处理 |
| --- | --- | --- |
| 三项应用身份权限未授予 | `99991672 Access denied` | 在应用后台开通并发布版本 |
| 机器人不在群内 | `230002 Bot/User can NOT be out of the chat` | 把应用添加为群机器人 |
| 消息接口 `page_size` 超限 | `99992402 field validation failed`，掩盖了真实权限错误 | 夹在 1–50 |
| 系统消息无发送者导致整源失败 | `Incomplete Lark message payload` | 只保留 `text`/`post`；单条不可用则跳过 |

### 当前数据现状（实测，非故障）

| 数据源 | API 返回 | 说明 |
| --- | --- | --- |
| 飞书任务 | `code=0`，可见任务 **0 条** | 应用可见范围内暂无任务；需在飞书任务中创建并共享给该应用 |
| 飞书群消息 | `code=0`，近 90 天 8 条，全部为 `system` 通知与机器人卡片 | 群内尚无真人文本消息，暂无可采集的协作内容 |

因此 `--dry-run` 输出 `count_lark_task=0 count_lark_msg=0 failed_sources=`（空）——**采集成功但无数据**，与采集失败有本质区别。

### 让数据出现的条件

- **任务板块**：在飞书任务里创建任务，并确保该应用在其可见范围内（任务清单共享给应用，或配置 `lark.tasklist_id`）
- **协作沟通板块**：群内有真人发送的 `text` 或 `post` 消息，且命中 `lark.keywords` 任一关键词、不含 `excluded_keywords`

### 仍待补齐的配置

| 字段 | 当前值 | 需替换为 | 状态 |
| --- | --- | --- | --- |
| `members[1].github_login` | `github-user-2` | 于广飞的真实 GitHub 账号 | 等业务侧确认 |
| `members[2].github_login` | `github-user-3` | 王博宁的真实 GitHub 账号 | 等业务侧确认 |

> 在替换前，这两个人**不会**出现在「代码提交」板块——commit 的作者名无法与占位符匹配。他们的 `lark_user_id` 是真实值，任务与消息归属不受影响。

---

## 八、使用说明

```bash
uv sync                              # 安装依赖
cp config.yaml.example config.yaml   # 配置团队/仓库/群组
cp .env.example .env                 # 填入密钥（程序启动时自动加载）

uv run python main.py --check        # 连通性自检
uv run python main.py --dry-run      # 采集+生成，不推送
uv run python main.py                # 完整流程
uv run python main.py --force        # 非工作日也执行（手动立即推送）
uv run python main.py --date 2026-11-12 --force   # 指定日期补发
uv run pytest -q                     # 运行测试（81 项）
```

退出码：`0` 成功（含跳过非工作日、dry-run）；`1` 配置错误、全部数据源失败或推送失败。

`.env` 由 `shared.config.load_dotenv()` 在启动时自动加载（先找 `config.yaml` 同目录，再找当前目录），已存在的环境变量优先。因此本地无需手工导出变量，容器内由 `env_file` 注入，行为一致。

### 生成条件

| 条件 | 不满足时 |
| --- | --- |
| 至少一个数据源正常返回 | 三源全失败 → **不生成**，退出码 `1` |
| 是工作日（或加 `--force`） | 跳过，退出码 `0` |
| — | 采集成功但 0 条记录 → **仍然生成**，板块显示「今日无记录」 |
| — | SQLite 写入或文件导出失败 → 仅记日志，**不阻断推送** |

### 产物位置

| 位置 | 说明 |
| --- | --- |
| `data/reports.db` | `daily_reports` 表，按 `report_date` 唯一，同日重跑覆盖 |
| `reports/<日期>.md` / `.html` | 人可读副本，原子写入 |

查询时以 `report_date` 为键；`id` 会因 upsert 消耗自增序列而变化，`created_at` 保留首次写入时间（UTC）且重跑不更新。

### 数据口径

- 采集窗口：**滚动 24 小时**（终点为执行时刻，或指定日期的当日 23:59:59）
- 成员归属：按 `members` 中 `github_login` / `lark_user_id` / `name` / `email` 四者任一匹配
- 敏感词：`excluded_keywords` 命中的消息不会进入日报

---

## 九、已知限制

1. **GitHub 采集串行**：每个 commit 需单独请求详情以获取变更统计，200 个 commit 约 40s。超 250 个会触及 60s 上限，建议加并发或改用 GraphQL 批量获取。
2. **飞书任务无服务端时间过滤**：Task v2 列表接口不支持按更新时间筛选，需拉全量后本地裁剪。任务总量过万时建议改用 `tasks/search`。
3. **飞书卡片体积上限**：约 30KB。15 人团队约 14.5KB（安全），50 人约 47KB（超限），超限需分片或改推送摘要。
4. **只能单向推送，无法从飞书反向触发**：项目内无任何入站 HTTP 代码（无 WebSocket/事件订阅回调），飞书自定义机器人 webhook 本身只能发不能收。要在飞书里"要求它生成日报"，需新增入站通道（事件订阅需公网 HTTPS 地址且回调须 3 秒内响应，与当前"无常驻服务"约束冲突；轮询群消息指令则不需要新权限）。
5. **`schedule.hour` / `schedule.minute` 不生效**：这两个键无任何生产代码读取（已全文检索确认），真正控制执行时间的是容器环境变量 `CRON_SCHEDULE` 或宿主机 crontab。保留原样，仅作后续"由配置生成调度表达式"的预留。
6. **`data/` `reports/` 内文件在容器部署下属主为 root**：容器以 root 运行并挂载这两个目录，宿主机用户删除其中文件需 `sudo`。

---

## 十、本次复验的变更记录

相对上一版（被验收提交 `5abc23c`）：

| 变更 | 影响 |
| --- | --- |
| 飞书三项应用权限开通、机器人入群 | 功能验收 #2 #3 由「待验证」转为「通过」 |
| `shared/config.py` 新增 `load_dotenv()`，`main()` 启动时自动加载 `.env` | 手动运行不再需要额外参数；补 3 个单测，用例数 78 → 81 |
| README 补「手动触发一次日报」「生成条件」说明 | 文档与代码行为对齐 |
| `DECISIONS.md` 新增 ADR-024（记录自研 `.env` 解析的取舍） | ADR 总数 23 → 24 |

---

*本报告所有「通过」项均有对应的实测输出或单元测试支撑；标注的提交哈希即被验收的代码版本。*
