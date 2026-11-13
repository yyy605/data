# 智能日报生成器 — 测试文档

| 项目 | 内容 |
| --- | --- |
| 测试框架 | pytest 8.x |
| 测试文件 | 10 个（`tests/`） |
| 测试函数 | 66 个 |
| 实际用例 | **68 个**（2 个参数化） |
| 最近执行结果 | **68 passed** |
| 执行耗时 | 约 0.4s（全部使用 Mock，不访问网络） |

配套文档：[ACCEPTANCE.md](./ACCEPTANCE.md)（验收结果报告）、[task.md](./task.md)（任务清单）。

---

## 一、测试分层策略

```
        ┌─────────────────────────────┐
   E2E  │ test_integration.py  (9)    │  完整管道：采集→生成→存库→导出→推送
        ├─────────────────────────────┤
  单元  │ test_github.py        (6)   │  ┐
        │ test_lark_task.py     (8)   │  │ 采集层
        │ test_lark_msg.py      (7)   │  ┘
        │ test_generator.py     (9)   │  生成层
        │ test_email.py         (6)   │  ┐ 推送层
        │ test_lark_bot.py      (5)   │  ┘
        │ test_shared.py        (8)   │  基础层
        │ test_schedule.py     (10)   │  调度规则
        └─────────────────────────────┘
```

**Mock 策略**：所有外部依赖均注入替身，测试不发起真实网络请求。

| 依赖 | 替身方式 |
| --- | --- |
| GitHub API | 自定义 `SequenceClient`，按顺序返回预置 `httpx.Response` |
| 飞书 API | `TaskClient` / `MessageClient`，记录请求并可返回异常 |
| SMTP | `smtp_factory` 注入 `FakeSMTP` |
| 飞书 webhook | `client` 参数注入 `FakeWebhookClient` |
| 时间 | `monkeypatch` 替换 `time.sleep`，避免测试等待 |

---

## 二、执行方法

```bash
# 全量
uv run pytest -q

# 单个文件
uv run pytest tests/test_github.py -q

# 单个用例
uv run pytest tests/test_integration.py::test_normal_flow_generates_and_pushes_report -v

# 显示每个用例名
uv run pytest -v
```

`pyproject.toml` 已配置 `testpaths = ["tests"]` 与 `pythonpath = ["."]`，因此可直接 `uv run pytest`，无需设置 `PYTHONPATH`。

---

## 三、测试用例清单

### 3.1 基础层 — `tests/test_shared.py`（8）

| 用例 | 验证内容 |
| --- | --- |
| `test_load_config_reads_example` | 仓库内的 `config.yaml.example` 能被正确解析，`team.name` 与成员映射可读取 |
| `test_load_config_rejects_missing_required_section` | 缺少必填配置段时抛出 `ConfigurationError`，错误信息指出缺失的段名 |
| `test_load_config_rejects_missing_member_field` | 成员缺少 `lark_user_id` 等必填字段时报错，且错误定位到具体成员下标 |
| `test_json_log_formatter_outputs_valid_json` | 日志输出为合法 JSON，`extra_*` 字段被展开为顶层键，含 logger 名与 message |
| `test_storage_creates_saves_reads_and_lists_report` | SQLite 自动建库建表，写入后可按日期读取，列表按日期倒序 |
| `test_storage_upserts_same_report_date` | 同一日期重复写入时执行覆盖更新，不产生重复记录 |
| `test_export_writes_markdown_and_html_files` | 导出 `<日期>.md` / `<日期>.html`，内容与传入一致，且不残留 `.tmp` 临时文件 |
| `test_export_overwrites_same_date_without_leftovers` | 同日重复导出覆盖旧文件，目录内不产生多余文件 |

### 3.2 GitHub 采集 — `tests/test_github.py`（6）

| 用例 | 验证内容 |
| --- | --- |
| `test_collect_returns_seven_field_commit_record` | 返回 `CommitRecord` 且**恰好 7 个字段**、类型正确；`since`/`until` 正确传给 API；作者优先取 GitHub login |
| `test_collect_supports_pagination` | 依据 `Link: rel="next"` 翻页，`per_page=100`，跨页结果正确合并 |
| `test_collect_retries_timeout_three_times_and_returns_empty_list` | 连续超时 3 次后返回空列表（不抛异常），且只 sleep 2 次 |
| `test_collect_requires_repository` | 未配置仓库时抛 `CollectorError` |
| `test_collect_retries_rate_limit_using_reset` | 403 且 `X-RateLimit-Remaining: 0` 时按 reset 时间等待并重试 |
| `test_collect_fails_fast_on_permission_403` | 403 但配额未耗尽（权限错误）时**不重试**，仅请求 1 次，避免无谓等待 |

### 3.3 飞书任务 — `tests/test_lark_task.py`（8）

| 用例 | 验证内容 |
| --- | --- |
| `test_collect_uses_task_v2_endpoint_and_maps_fields` | 请求 `/open-apis/task/v2/tasks`（非已废弃的 `project/v2`）；返回 5 字段；`members` 中 `role=assignee` 被提取，`follower` 被忽略 |
| `test_collect_marks_completed_task` | 依据 `completed_at` 推断状态：空→「进行中」，有值→「已完成」 |
| `test_collect_filters_out_tasks_outside_window` | 该接口无服务端时间过滤，需按 `updated_at` 本地裁剪滚动窗口 |
| `test_collect_filters_by_tasklist_id` | 配置 `tasklist_id` 时只保留属于该清单的任务 |
| `test_collect_requires_app_credentials` | 缺 `app_id` 时抛 `CollectorError` |
| `test_collect_refreshes_expired_token_once` | `99991663` 触发刷新，重试后使用新 token |
| `test_collect_does_not_refresh_token_on_permission_error` | `99991672`（缺 scope）**不触发**刷新，仅请求 1 次，错误不被掩盖 |
| `test_collect_retries_timeout_three_times_and_returns_empty` | 超时 3 次后返回空列表 |

### 3.4 飞书消息 — `tests/test_lark_msg.py`（7）

| 用例 | 验证内容 |
| --- | --- |
| `test_collect_returns_four_field_records_and_filters_keywords` | 返回 4 字段；仅保留含白名单关键词的消息；正文能从飞书 JSON 字符串中解出 `text` |
| `test_collect_without_keywords_returns_non_sensitive_messages` | 未配置关键词时全部保留，但**黑名单敏感词**（薪资等）仍被过滤 |
| `test_collect_requires_chat_id` | 缺 `chat_id` 时抛 `CollectorError` |
| `test_collect_refreshes_expired_token_once` | token 过期码触发刷新并使用新 token |
| `test_collect_retries_timeout_three_times_and_returns_empty` | 超时 3 次后返回空列表 |
| `test_collect_does_not_refresh_token_on_permission_error` | `99991672` 不触发刷新，避免掩盖真实权限错误 |
| `test_collect_clamps_page_size_to_feishu_limit` | `page_size` 被夹在 1–50：飞书对 `>50` 返回 `99992402`，会掩盖真实的权限错误 |

### 3.5 日报生成 — `tests/test_generator.py`（9）

| 用例 | 验证内容 |
| --- | --- |
| `test_generate_contains_three_sections_and_renders_html` | 三个板块标题齐全；HTML 含 `<h3>`、`<!doctype html>`；commit 内容正确渲染 |
| `test_generate_shows_no_records_for_empty_member` | 成员无数据时三个板块各显示一次「今日无记录」 |
| `test_generate_marks_failed_sources` | 失败数据源显示「数据获取失败」，数量与失败源数量一致 |
| `test_markdown_to_html_formats_headings_lists_and_bold_text` | Markdown 转换严格匹配预期 HTML（标题层级、列表、`**粗体**`） |
| `test_commit_message_is_collapsed_to_single_line` | **多行 commit message 只保留标题行**，正文与 `Co-authored-by` 不进入日报（防止打散列表结构） |
| `test_long_commit_subject_is_truncated` | 超长标题被截断并加省略号 |
| `test_generate_accepts_member_report_objects` | 支持直接传入 `MemberReport` 对象，不只是 dict |
| `test_html_is_rendered_from_jinja2_template` | HTML 由 `report.html.jinja2` 模板渲染，模板文件存在且无未渲染占位符 |
| `test_html_title_is_escaped_but_body_keeps_markup` | 标题经 Jinja2 自动转义（防 XSS），正文保留 Markdown 转换后的 HTML |

### 3.6 邮件推送 — `tests/test_email.py`（5 + 1 参数化 = 6）

| 用例 | 验证内容 |
| --- | --- |
| `test_send_uses_subject_with_date_and_team_and_html_body` | 主题含日期与团队名；正文同时含 `text/plain` 与 `text/html`；收发件人正确 |
| `test_send_retries_twice_after_failure` | 失败 2 次后第 3 次成功，最终只发 1 封 |
| `test_send_returns_false_after_all_attempts_fail` | 3 次全失败返回 `False`，不抛异常 |
| `test_send_returns_false_when_recipients_missing` | 缺收件人时返回 `False` 并记录错误 |
| `test_send_returns_false_when_required_setting_missing` | 参数化 `host` / `from_address` 缺失，均返回 `False` |

### 3.7 飞书机器人 — `tests/test_lark_bot.py`（5）

| 用例 | 验证内容 |
| --- | --- |
| `test_send_pushes_markdown_card` | 发送 `interactive` 卡片，标题为「团队 日期 日报」，正文 `lark_md` 含 Markdown |
| `test_send_retries_twice_then_succeeds` | 网络异常 + 业务错误码后第 3 次成功，共请求 3 次 |
| `test_send_returns_false_after_three_failures` | 连续 3 次失败返回 `False` |
| `test_send_returns_false_when_webhook_url_missing` | 缺 webhook 地址时返回 `False` |
| `test_send_returns_false_on_http_error_status` | HTTP 4xx 视为失败并重试，最终返回 `False` |

### 3.8 调度规则 — `tests/test_schedule.py`（9 个函数 + 1 参数化 = 10）

| 用例 | 验证内容 |
| --- | --- |
| `test_collection_window_is_rolling_24_hours` | 采集窗口跨度恒为 24 小时（参数化两个日期） |
| `test_collection_window_for_today_ends_now` | 当日日报的窗口终点为「现在」（误差 < 60s） |
| `test_collection_window_for_past_date_ends_at_day_end` | 历史日期的窗口终点为当日 23:59:59 |
| `test_weekend_is_skipped` | 周六、周日判定为非工作日，周一为工作日 |
| `test_legal_holiday_is_skipped_via_calendar_library` | `chinesecalendar` 正确识别法定节假日（2026-01-01） |
| `test_make_up_workday_is_treated_as_working_day` | 正确识别**调休上班**（2026-10-10 周六上班） |
| `test_manual_holidays_override_calendar` | `schedule.holidays` 手工覆盖优先于日历库 |
| `test_manual_workdays_override_calendar` | `schedule.workdays` 手工覆盖优先于日历库 |
| `test_falls_back_to_weekday_detection_when_calendar_missing` | 日历库不可用时降级为按星期判断，不崩溃 |

### 3.9 端到端集成 — `tests/test_integration.py`（9）

| 用例 | 验证内容 |
| --- | --- |
| `test_normal_flow_generates_and_pushes_report` | 三源全可用：三板块齐全、内容入库、HTML 合法、双通道各推送 1 次 |
| `test_degraded_source_marks_failure_but_keeps_other_sections` | GitHub 超时：日报标注「数据获取失败」，其余板块正常，日志含 `Data source failed / source=github` |
| `test_member_without_records_shows_no_records` | 成员二无数据时显示「今日无记录」，且不串入他人 commit |
| `test_all_sources_failing_skips_report_and_logs_error` | 三源全失败：退出码 1、不写库、不推送、日志记录 `All data sources failed` |
| `test_dry_run_skips_delivery` | `--dry-run` 生成并入库，但完全不推送 |
| `test_full_flow_completes_well_under_60_seconds` | 全流程耗时断言 < 60s（对应 proposal 3.2） |
| `test_non_working_day_is_skipped` | 周末直接跳过：不生成、不推送 |
| `test_report_files_are_exported` | 流程结束后 `reports/` 下生成 md/html，内容与数据库记录逐字一致 |
| `test_file_export_failure_does_not_block_delivery` | 文件写入抛 `OSError` 时日报仍入库并正常推送，退出码 0 |

---

## 四、手工验证流程

自动化测试使用 Mock，真实链路需按以下步骤验证。

### 4.1 环境自检

```bash
uv run python main.py --check
```

预期输出（6 项全 PASS）：

```
config         PASS  configuration loaded
environment    PASS  all referenced variables are set
github         PASS  HTTP 200
lark           PASS  ok（tenant_access_token 获取成功）
smtp           PASS  smtp.qq.com:465 reachable
lark_webhook   PASS  open.feishu.cn reachable
```

退出码：全通过为 `0`，任一项失败为 `1`。

### 4.2 采集与生成（不推送）

```bash
uv run python main.py --dry-run --force
```

检查日志中的 `Collection finished` 行：

```
count_github=N  count_lark_task=N  count_lark_msg=N  failed_sources=[]
```

- `failed_sources` 应为空列表
- 三个 `count_*` 均 > 0 表示三源全部成功

### 4.3 完整推送

```bash
uv run python main.py --force
```

检查 `Push finished` 行：

```
push_email=True  push_lark_bot=True
```

并确认实际收到邮件与飞书群消息。

### 4.4 降级验证（可选）

临时填写错误的 `lark.chat_id`，重跑 `--dry-run`，预期：

- 该数据源失败但**不中断**其他源
- 日报中对应板块显示 `- **数据获取失败**（飞书消息）`
- 退出码仍为 `0`

### 4.5 数据库校验

```bash
uv run python -c "from shared.storage import ReportStorage; r=ReportStorage('data/reports.db').get_report('2026-11-12'); print(bool(r), r['markdown'][:200])"
```

### 4.6 Docker 部署验证

```bash
docker compose config -q            # 配置合法
docker compose up -d --build        # 构建并启动
docker compose logs -f              # 应显示 scheduler started
docker compose run --rm daily-report python main.py --check
```

---

## 五、真实环境验证清单

以下项目自动化测试已覆盖**代码逻辑**，但需真实凭据才能确认**最终效果**。交接后请逐项打勾：

- [ ] `main.py --check` 六项全 PASS
- [ ] GitHub 私有仓库能读到 commit 与 `+/-` 变更统计
- [ ] 飞书任务采集返回非空（需 `task:task:read` 权限已发布）
- [ ] 飞书群消息按关键词正确筛选（需 `im:message:readonly` + 正确 `chat_id`）
- [ ] 敏感词消息未出现在日报中
- [ ] 邮件实际送达收件人，HTML 在客户端正常渲染
- [ ] 飞书群收到 Markdown 卡片，格式正常
- [ ] 5 人团队真实耗时 < 60s
- [ ] 周末与法定节假日确实跳过
- [ ] 故意填错一个数据源，确认日报标注「数据获取失败」且其他板块正常

---

## 六、测试覆盖边界（已知未覆盖项）

以下场景**没有**自动化测试，属于已知空白：

| 未覆盖项 | 原因 | 风险 |
| --- | --- | --- |
| GitHub / 飞书真实 API 响应结构 | 需真实凭据，且响应结构可能随版本变化 | 中：接口字段变更时需人工核对 |
| 真实 SMTP 投递与反垃圾 | 依赖外部邮件服务 | 低：已验证连接与发送成功 |
| 飞书卡片超 30KB 的行为 | 需构造 20+ 人真实数据 | 中：超限会被拒，需分片改造 |
| 并发场景 | 当前实现无并发 | 低 |
| 时区夏令时切换 | 目标时区 `Asia/Shanghai` 无夏令时 | 低 |
| 数据库并发写入 | 单进程 cron 场景不会触发 | 低 |
| 网络中断恢复 | 仅覆盖超时/重试，未覆盖 DNS 故障等 | 低 |

---

## 七、回归测试基线与当前差距

| 指标 | 基线 |
| --- | --- |
| 用例总数 | 68 |
| 必须通过率 | 100% |
| 执行时间上限 | < 5s（当前 0.5s） |
| 网络依赖 | 0（全部 Mock） |

新增功能时应同步新增测试，且不得降低既有通过率。修改以下文件时必须运行对应测试文件：

| 修改文件 | 必须运行 |
| --- | --- |
| `shared/*.py` | `tests/test_shared.py` |
| `collector/github.py` | `tests/test_github.py` |
| `collector/lark_task.py` | `tests/test_lark_task.py` |
| `collector/lark_msg.py` | `tests/test_lark_msg.py` |
| `generator/*.py`、`generator/templates/*` | `tests/test_generator.py` |
| `notifier/email.py` | `tests/test_email.py` |
| `notifier/lark_bot.py` | `tests/test_lark_bot.py` |
| `main.py` | `tests/test_schedule.py` + `tests/test_integration.py` |

---

*本文档中的用例名与数量均从源码实际提取（`Select-String -Pattern '^def test_'`），与 `pytest` 执行结果一致。*
