# 智能日报生成器 — 验收报告

| 项目 | 内容 |
| --- | --- |
| 仓库 | https://github.com/yyy605/data |
| 分支 | `main` |
| 被验收的代码提交 | `5abc23c` |
| 验收日期 | 2026-11-12 |
| 代码规模 | 源码 16 文件 2345 行；测试 10 文件 1382 行 |
| 自动化测试 | **67 个用例，全部通过** |

本报告只记录**实测结果**。无法在当前环境验证的项目均明确标注为「待验证」，不做推测性结论。

---

## 一、验收结论

| 类别 | 通过 | 待验证 |
| --- | --- | --- |
| 功能验收（proposal 3.1） | 5 / 7 | 2 |
| 性能验收（proposal 3.2） | 3 / 3 | 0 |
| 边界场景（proposal 3.3） | 4 / 4 | 0 |
| 技术约束（proposal 2.3） | 5 / 5 | 0 |

**总体：核心链路已端到端跑通，2 项阻塞于飞书应用权限审批，非代码问题。**

---

## 二、功能验收

| # | 验收项 | 结果 | 实测证据 |
| --- | --- | --- | --- |
| 1 | 获取指定 GitHub 仓库过去 24h 的 Commit | ✅ 通过 | 真实拉取 3 条 commit（`5abc23c`/`2214912`/`c7a7b47`），含作者与 `+/−` 变更统计 |
| 2 | 获取飞书当日状态变更任务 | ⏳ 待验证 | 端点 `/open-apis/task/v2/tasks` 已确认真实存在（返回业务错误码而非 404）；应用缺 `task:task:read` 权限，返回 `99991672` |
| 3 | 按关键词提取飞书群讨论 | ⏳ 待验证 | 端点 `/open-apis/im/v1/messages` 已确认存在；缺 `im:message:readonly`，且 `chat_id` 待补 |
| 4 | 日报包含三个核心板块 | ✅ 通过 | 生成结果含 `### 代码提交` `### 任务进展` `### 协作沟通` |
| 5 | Markdown 可直接渲染为 HTML | ✅ 通过 | 输出 `<!doctype html>` 完整文档，含内联样式；转换器单测覆盖 |
| 6 | 邮件送达团队 Leader | ✅ 通过 | 真实 SMTP 发送成功：`push_email=True, attempts=1`（QQ 邮箱 465/SSL） |
| 7 | 飞书机器人推送到群组 | ✅ 通过 | 真实 webhook 推送成功：`push_lark_bot=True, attempts=1`，使用 `interactive` 卡片 + `lark_md` |

---

## 三、性能验收

| # | 验收项 | 结果 | 实测数据 |
| --- | --- | --- | --- |
| 1 | 5 人团队单次生成 < 60s | ✅ 通过 | 完整流程（采集+生成+存库+双通道推送）**5.86s** |
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
| Docker 容器化部署 | ✅ 镜像 286MB 构建成功，容器内 CLI 与 cron 调度均验证通过 |
| Cron 定时、无常驻服务 | ✅ 容器内 `/etc/cron.d/daily-report`，`0 18 * * 1-5`，`CRON_TZ=Asia/Shanghai` |
| 本地 SQLite 存储 | ✅ `data/reports.db`，写入/查询/覆盖更新均有单测；另导出 `reports/<日期>.md|.html` 副本 |
| 外部依赖 | ✅ GitHub API、飞书开放平台、SMTP 均实测可达 |

---

## 六、环境自检结果（`python main.py --check`）

```
config         PASS  configuration loaded
environment    PASS  all referenced variables are set
github         PASS  HTTP 200
lark           PASS  ok（tenant_access_token 获取成功）
smtp           PASS  smtp.qq.com:465 reachable
lark_webhook   PASS  open.feishu.cn reachable
```

---

## 七、待办事项（阻塞验收项 2、3）

### 1. 开通飞书应用权限

应用 ID：`cli_aa7fa5d60c799c1e`

需开通并**发布新版本**（权限勾选后必须发布才生效）：

| 权限码 | 用途 |
| --- | --- |
| `task:task:read` | 读取飞书任务 |
| `im:message:readonly` | 读取群消息 |
| `im:chat:readonly` | 查询群列表（用于获取 chat_id） |

申请入口：
```
https://open.feishu.cn/app/cli_aa7fa5d60c799c1e/auth?q=task:task:read,im:message:readonly,im:chat:readonly
```

发布路径：左侧「版本管理与发布」→ 创建版本 → 申请发布 →（管理员）审核通过。

### 2. 补齐配置占位符

`config.yaml` 中仍为示例值、需替换的字段：

| 字段 | 当前值 | 需替换为 |
| --- | --- | --- |
| `lark.chat_id` | `oc_example_chat_id` | 真实群 ID（`oc_` 开头） |
| `members[1].github_login` | `github-user-2` | 于广飞的真实 GitHub 账号 |
| `members[2].github_login` | `github-user-3` | 王博宁的真实 GitHub 账号 |

### 3. 完成后复验

```bash
python main.py --check      # 六项应全 PASS
python main.py --force      # 应显示 count_lark_task>0、count_lark_msg>0，且无 failed_sources
```

---

## 八、使用说明

```bash
uv sync                              # 安装依赖
cp config.yaml.example config.yaml   # 配置团队/仓库/群组
cp .env.example .env                 # 填入密钥

uv run python main.py --check        # 连通性自检
uv run python main.py --dry-run      # 采集+生成，不推送
uv run python main.py                # 完整流程
uv run python main.py --date 2026-11-12 --force   # 指定日期并跳过非工作日
uv run pytest -q                     # 运行测试（67 项）
```

退出码：`0` 成功（含跳过非工作日、dry-run）；`1` 配置错误、全部数据源失败或推送失败。

### 数据口径

- 采集窗口：**滚动 24 小时**（终点为执行时刻，或指定日期的当日 23:59:59）
- 成员归属：按 `members` 中 `github_login` / `lark_user_id` / `name` / `email` 四者任一匹配
- 敏感词：`excluded_keywords` 命中的消息不会进入日报

---

## 九、已知限制

1. **GitHub 采集串行**：每个 commit 需单独请求详情以获取变更统计，200 个 commit 约 40s。超 250 个会触及 60s 上限，建议加并发或改用 GraphQL 批量获取。
2. **飞书任务无服务端时间过滤**：Task v2 列表接口不支持按更新时间筛选，需拉全量后本地裁剪。任务总量过万时建议改用 `tasks/search`。
3. **飞书卡片体积上限**：约 30KB。15 人团队约 14.5KB（安全），50 人约 47KB（超限），超限需分片或改推送摘要。

---

*本报告由自动化验收脚本生成，所有"通过"项均有对应实测输出或单元测试支撑。*
