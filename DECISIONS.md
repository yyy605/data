# 架构与技术决策记录（ADR）

> **这份文档解决什么问题**
>
> AI 写代码时会顺带解释"为什么这么选"，但那些解释散落在对话里，几周后就找不到了。于是再看到 `httpx` 或 `ON CONFLICT` 时，只剩"它就是这么写的"，说不出理由，也就不敢改。
>
> 这份文档把**当时的取舍**固化下来：每个决策都写清「当时面对什么问题 → 考虑过哪些方案 → 为什么选这个 → 付出了什么代价 → 什么情况下该重新考虑」。
>
> **怎么用**：想改某段代码前，先按编号查到对应 ADR。如果 ADR 里写的「重新评估条件」已经成立，那就是该改的时候；否则先理解它为什么在那儿。

| 项目 | 内容 |
| --- | --- |
| 适用范围 | 智能日报生成器（`main.py` + `collector/` + `generator/` + `notifier/` + `shared/`） |
| 决策数量 | 21 条 |
| 首次记录 | 2026-11-06 |
| 最近修订 | 2026-11-12 |
| 关联文档 | [design.md](./design.md)、[proposal.md](./proposal.md)、[TESTING.md](./TESTING.md) |

---

## 0. 决策索引

| 编号 | 主题 | 一句话结论 | 代码锚点 |
| --- | --- | --- | --- |
| ADR-001 | HTTP 客户端 | 用 httpx，不用 requests / aiohttp | `collector/*.py` |
| ADR-002 | 数据存储 | 用 SQLite 标准库，不引入 ORM | `shared/storage.py` |
| ADR-003 | 运行时形态 | Python 3.11+，全同步、单进程、无守护 | 全局 |
| ADR-004 | HTML 渲染 | 用 Jinja2 模板，不手拼字符串 | `generator/template.py` |
| ADR-005 | 配置格式 | 用 YAML，不用 JSON / TOML / .env | `shared/config.py` |
| ADR-006 | 时区处理 | `ZoneInfo` + `tzdata`，不用 `pytz` | `main.py:_timezone` |
| ADR-007 | 节假日识别 | 用 `chinesecalendar`，不只判周末 | `main.py:_is_working_day` |
| ADR-008 | 测试框架 | pytest + 依赖注入替身，不 mock 网络 | `tests/` |
| ADR-009 | 架构风格 | 管道式四层，不用单体函数 / 微服务 | 目录结构 |
| ADR-010 | 数据契约 | 入参 `Mapping`，出参 `frozen dataclass` | `collector/*.py` |
| ADR-011 | 采集失败语义 | 返回空列表 + 回调上报，不向上抛异常 | `shared/errors.py` |
| ADR-012 | 推送返回语义 | 返回 `bool`，不抛异常 | `notifier/*.py` |
| ADR-013 | 输出单一事实源 | Markdown 为源，HTML 由它派生 | `generator/template.py` |
| ADR-014 | 可测试性 | 外部客户端一律可注入 | `client` / `smtp_factory` |
| ADR-015 | 密钥管理 | 配置只存变量名，值走环境变量 | `main.py:_env` |
| ADR-016 | 日志格式 | 自写 JSON Lines Formatter，不用 logstash 插件 | `shared/logger.py` |
| ADR-017 | 重试策略 | 统一"3 次尝试"，并区分限流与无权限 | `collector/github.py` |
| ADR-018 | 写入幂等 | `ON CONFLICT(report_date) DO UPDATE` | `shared/storage.py:48` |
| ADR-019 | 采集窗口 | 滚动 24 小时，不用自然日 00:00 起 | `main.py:_collection_window` |
| ADR-020 | 调度形态 | 容器内 cron，不用 APScheduler 常驻进程 | `docker/entrypoint.sh` |
| ADR-021 | 容器入口 | 一个脚本双模式（调度 / 一次性命令） | `docker/entrypoint.sh` |
| ADR-022 | 报告落盘 | 除 SQLite 外，另存 `<日期>.md` / `<日期>.html` 到 `reports/` | `shared/storage.py:export_report_files` |
| ADR-023 | 分页上限 | 飞书消息 `page_size` 夹在 1–50，超限错误码会掩盖真实原因 | `collector/lark_msg.py:_page_size` |

---

## ADR-001 HTTP 客户端选择 httpx

- **状态**：已采纳（对应 design.md ADR-001）
- **日期**：2026-11-06
- **代码位置**：`collector/github.py`、`collector/lark_task.py`、`collector/lark_msg.py`

**当时的问题**
要同时对接三个 HTTP 服务（GitHub、飞书开放平台），需要统一处理超时、重试、请求头、测试替身。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| `requests` | 最普及、示例最多 | 超时参数必须显式传 `timeout=`，团队历史上踩过"忘记设置导致永久挂起"的坑；无内置 async 退路 |
| `aiohttp` | 原生异步，吞吐高 | 需要把整个调用链改成 async，而本项目的瓶颈是**串行依赖链**不是并发能力（见 ADR-003） |
| `urllib` | 零依赖 | 手写重试/超时/连接池成本高，代码可读性差 |
| **`httpx`** ✅ | 同步/异步双 API，超时与 headers 是客户端级默认值，`httpx.Response` 易构造便于测试 | — |

**决定**
用 `httpx.Client`，并把 `timeout`、公共 `headers` 放在客户端构造时统一设置。

**代价 / 已知不足**
多一个第三方依赖；`httpx` 大版本升级（0.x → 1.0）可能有破坏性变更，`pyproject.toml` 已用 `>=0.27,<1` 锁住上界。

**什么情况下应该重新评估**
单次运行需要并发请求 500+ 次且耗时成为主要瓶颈时，改 `httpx.AsyncClient` + `asyncio.gather`（见 ADR-003 的重新评估条件）。

---

## ADR-002 数据存储用 SQLite 标准库

- **状态**：已采纳（对应 design.md ADR-002）
- **日期**：2026-11-06
- **代码位置**：`shared/storage.py`

**当时的问题**
需要保存日报历史，但部署环境是公司内网单机，且 proposal §2.3 明确写了"不需要部署远程数据库"。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| PostgreSQL / MySQL | 并发强、可远程 | 为一台机器上一个每天写 1 条的定时任务引入数据库服务，运维成本完全不成比例 |
| 直接写 Markdown 文件 | 零依赖 | 无法按日期唯一索引，重跑会产生重复文件；查询要靠扫目录 |
| ORM（SQLAlchemy） | 换库方便、模型清晰 | 只有一张表两个操作，ORM 的抽象成本高于收益 |
| **标准库 `sqlite3`** ✅ | 零额外依赖、单文件、支持 `ON CONFLICT` | — |

**决定**
单表 `daily_reports(report_date UNIQUE, team, markdown, html, created_at)`，用 `sqlite3` 直接写 SQL，不引 ORM。

**代价 / 已知不足**
SQL 语句直接嵌在 Python 里，表结构变更没有迁移工具；并发写入靠 SQLite 自身文件锁（本项目单进程，不构成风险）。

**什么情况下应该重新评估**
需要多团队隔离（proposal §2.2 明确不做）、或需要按周/月聚合统计时。

---

## ADR-003 Python 3.11+，全同步单进程

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：全局（已确认无 `async def` / `asyncio`）

**当时的问题**
要不要用异步提升采集速度？要不要常驻进程做调度？

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| asyncio 并发采集 | 吞吐高 | 采集链路是「先列表 → 再逐个取详情」的**串行依赖**，且每天只跑一次，省下的秒数远小于引入 async 后全链路改造与调试的复杂度 |
| 常驻进程 + APScheduler | 调度灵活 | proposal 要求"基于 Cron 的定时任务，不需要常驻后台服务"；常驻进程还要处理崩溃重启、内存泄漏 |
| **同步 + cron 触发** ✅ | 逻辑线性可读、崩溃即退出、日志简单 | — |

**决定**
全部同步代码，进程由外部 cron 拉起，跑完即退出。

**代价 / 已知不足**
GitHub 采集是明显的 N+1 串行请求，200 条 commit 约 40s，250 条触及 60s 上限（实测数据见 ACCEPTANCE.md）。

**什么情况下应该重新评估**
团队超过 15 人，或单窗口 commit 持续超过 200 条时：优先给 GitHub 详情请求加 `ThreadPoolExecutor`（改动约 20 行，无需 async），而不是整体改 async。

---

## ADR-004 HTML 用 Jinja2 模板渲染

- **状态**：已采纳（2026-11-12 修订）
- **日期**：2026-11-06 引入 / 2026-11-12 改为真正使用
- **代码位置**：`generator/template.py`、`generator/templates/report.html.jinja2`

**当时的问题**
需要把 Markdown 正文包进一个完整 HTML 文档（含 `charset`、viewport、内联样式、标题）。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 字符串拼接 | 零依赖、最直接 | HTML 结构一变就要改 Python 字符串；标题直接插值会有 XSS 风险（见下） |
| `markdown` 库自行转 HTML | 功能全 | 只用到标题/列表/粗体三种语法，引入完整解析器过重 |
| 第三方 Markdown→HTML 命令行 | 无 | 多一个运行时依赖 |
| **Jinja2 模板** ✅ | 结构与数据分离；自带自动转义 | — |

**决定**
Markdown→HTML 的**正文转换**仍由 `markdown_to_html()` 手写（只支持标题/列表/粗体/行内代码）；**HTML 外壳**放到 `report.html.jinja2`，用 Jinja2 渲染。

**一个容易忘的细节**
模板里是 `{{ body|safe }}` 而不是 `{{ body }}`。因为 `autoescape=select_autoescape(("html","jinja2"))` 对 `.jinja2` 文件默认开启转义，而 `body` 已经是可信 HTML，必须显式标 `safe`；`title` 则依赖自动转义防注入。

**历史教训（为什么要记这条）**
最初按文档要求声明了 `jinja2` 依赖，但实际是手拼字符串，依赖从未被 import——属于"文档写了、代码没用"的僵尸依赖。后来在验收复核时被发现，才改成真实使用。**这类"声明与实现不一致"最容易在交接时误导人。**

**代价 / 已知不足**
模板文件必须随代码一起打包（Docker 里已验证 `/app/generator/templates/report.html.jinja2` 存在）；`|safe` 用错位置会引入 XSS，改动模板时需谨慎。

**什么情况下应该重新评估**
需要多套外观（邮件版/网页版）、或需要国际化时，扩展模板而非改 Python。

---

## ADR-005 配置用 YAML

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`shared/config.py`、`config.yaml.example`

**当时的问题**
配置里有嵌套结构（`members[]`、`github.repositories[]`、`lark.keywords[]`），需要人能读懂、能注释。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| JSON | 标准库自带 | 不能写注释；多行列表难读 |
| TOML | 语义清晰 | 嵌套数组（成员列表里套仓库列表）写起来冗长 |
| 纯 `.env` | 简单 | 无法表达列表与层级，成员映射表写不进去 |
| **YAML** ✅ | 支持注释、层级直观、对非程序员友好 | 需要 PyYAML |

**决定**
`config.yaml` 放**非密钥**配置；密钥只放 `.env`（见 ADR-015）。

**代价 / 已知不足**
YAML 的隐式类型转换有坑（如 `on`/`yes` 被解析为布尔）；`safe_load` 已用于避免任意对象构造。

**什么情况下应该重新评估**
配置项超过 50 个、需要 schema 校验时，可引入 pydantic。

---

## ADR-006 时区用 ZoneInfo + tzdata

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`main.py:_timezone`

**当时的问题**
日报要按团队本地时间判断"今天是哪天""18:00 是否到了"，而容器默认是 UTC。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| `pytz` | 老牌、资料多 | 官方已推荐迁移到 `zoneinfo`；`localize()`/`normalize()` 易用错 |
| 固定 UTC+8 偏移 | 零依赖 | 无法表达其他时区，且写死偏移在跨时区团队会错 |
| **`zoneinfo.ZoneInfo` + `tzdata`** ✅ | 标准库 API；`tzdata` 解决 Windows/精简镜像缺 IANA 数据库的问题 | — |

**决定**
`team.timezone` 配置时区名（默认 `Asia/Shanghai`），统一用 `ZoneInfo`；`tzdata` 作为依赖固定安装。

**代价 / 已知不足**
`ZoneInfo` 在缺系统时区库的平台上会抛 `ZoneInfoNotFoundError`——这正是当初 Windows 上直接报错的原因，所以才显式加了 `tzdata` 依赖。

**什么情况下应该重新评估**
需要按成员各自时区出日报时（当前是团队级时区）。

---

## ADR-007 节假日用 chinesecalendar

- **状态**：已采纳
- **日期**：2026-11-12
- **代码位置**：`main.py:_is_working_day`

**当时的问题**
proposal §3.3 要求"识别非工作日（周末及法定节假日）"。最初实现只判断 `weekday() >= 5`，法定节假日需人工往配置里写日期。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 只判周末 + 手工配置节假日 | 零依赖 | 每年要人工维护；且**调休上班日**（周六上班）无法表达 |
| 自己维护节假日表 | 可控 | 每年国务院公告调整，维护成本高且易漏 |
| **`chinesecalendar`** ✅ | 自动含法定节假日与调休；已验证覆盖 2026 年 | — |

**决定**
默认用库判断；`schedule.holidays` / `schedule.workdays` 作为**手工覆盖**，优先级高于库（便于应对临时调整）。

**代价 / 已知不足**
库的数据随国务院公告更新，跨年时可能有一段时间缺当年数据；因此实现了"库不可用则降级为按星期判断"的兜底（有测试覆盖）。

**什么情况下应该重新评估**
需要支持非中国大陆节假日时，改为可插拔的日历提供者。

---

## ADR-008 用 pytest + 依赖注入替身

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`tests/`、`pyproject.toml:[tool.pytest.ini_options]`

**当时的问题**
测试不能依赖真实网络与真实邮箱，否则 CI 不稳定、还可能要凭据。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| `unittest.mock.patch` 全局打补丁 | 无需改生产代码 | 补丁路径与实现耦合，重构即失效；易出现"测的是 mock 不是代码" |
| unittest 原生 | 标准库 | fixture/monkeypatch 等能力弱，样板代码多 |
| 真实沙箱账号 | 最真实 | 有凭据泄露风险，且无法模拟限流/超时 |
| **pytest + 显式注入** ✅ | 生产代码只多一个可选参数；测试直接构造替身 | — |

**决定**
所有外部客户端都可从 `params`/`target` 注入（`client`、`smtp_factory`）；默认值为 `None` 时才创建真实客户端。

**代价 / 已知不足**
生产代码多了"可注入"的判断分支（`owns_client`），需要负责关闭自己创建的连接。测试全部走替身，**真实响应结构变化测不出来**（这是 TESTING.md 里已列的覆盖空白）。

**什么情况下应该重新评估**
接入更多第三方后，可增加"契约测试"（录制真实响应回放）。

---

## ADR-009 管道式四层架构

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`main.py`、`collector/`、`generator/`、`notifier/`、`shared/`

**当时的问题**
数据要经过"取 → 整理 → 发"三个阶段，每阶段可能失败，且要能单独测试。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 单文件脚本顺序写 | 最快出活 | 无法单独测试；任一环节失败就整体崩；数据源增加时函数膨胀 |
| 微服务拆分 | 独立部署 | 为一个每天跑一次的任务拆三个服务，部署与联调成本过高 |
| 事件驱动（消息队列） | 解耦彻底 | 需要中间件，违背"单机 cron"约束 |
| **管道式四层** ✅ | 每层职责单一、可独立测试、失败可局部降级 | — |

**决定**
`collector`（取）→ `generator`（整理，纯函数无 IO）→ `notifier`（发）；`shared` 提供配置/日志/异常/存储；`main.py` 只做编排，不含业务逻辑。

**关键约束（容易写错的点）**
`generator` 层**不做任何 IO**，只接收数据、返回 `DailyReport`。这让日报生成可以纯内存测试（50 人 3.6ms）。

**代价 / 已知不足**
层间要传数据结构，`main.py` 里存在一定量的"参数组装"代码（如 `_github_params`、`_lark_params`）。

**什么情况下应该重新评估**
需要多个消费端（如同时出周报、月报）时，可在 `generator` 之上加组合层，而非改动现有分层。

---

## ADR-010 入参 Mapping，出参 frozen dataclass

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`collector/github.py:36`、`collector/lark_task.py:26`、`collector/lark_msg.py:29`

**当时的问题**
各采集器要接收不同参数（token / app_id / since / keywords…），返回结构也不同，但 `main.py` 需要统一调度。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 每个采集器定义专属参数 dataclass | 类型安全 | `main.py` 要按采集器分支构造，违背"统一编排"；且各源参数差异大，dataclass 字段会很杂 |
| 位置参数列表 | 简单 | 参数一多就难读易错，加参数即破坏调用方 |
| **`Mapping[str, Any]` 入参 + `frozen dataclass` 出参** ✅ | 编排统一（都能 `collect(params)`）；出参有类型、不可变、可安全缓存 | — |

**决定**
采集层统一 `collect(params: Mapping[str, Any]) -> list[Record]`；记录类型为 `@dataclass(frozen=True)`，字段数固定（Commit 7 / Task 5 / Message 4），并有测试断言字段数。

**代价 / 已知不足**
入参是弱类型字典，字段名写错只能运行时报错；因此每个采集器都有 `_required_text` 之类的显式校验与错误信息。

**什么情况下应该重新评估**
采集器参数稳定后，可为每个采集器加 `TypedDict` 提升可读性而不改变运行时行为。

---

## ADR-011 采集失败：返回空列表 + 回调上报

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`shared/errors.py:25`（`notify_collector_error`）、三个采集器 `except` 分支

**当时的问题**
proposal §3.2 要求"单一数据源失败不应阻断其他数据源"，且"失败要显式标注，严禁静默跳过"。这要求失败既能**继续流程**，又能被**上层感知**。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 失败直接抛 `CollectorError` | 语义清晰 | `main.py` 要为每个源写 try/except，且容易一处漏写导致整体中断；"继续执行"变成默认之外的行为 |
| 失败返回 `None` | 能区分"空结果"和"失败" | 每个调用点都要判 `None`；类型标注变复杂 |
| 只返回 `[]` 不区分 | 最简单 | 无法区分"今天确实没提交"和"接口挂了"——这正是要避免的静默失败 |
| **返回 `[]` + `on_error` 回调** ✅ | 流程天然继续；上层收集失败源用于标注 | — |

**决定**
采集器内部捕获异常 → 记错误日志 → 调用 `params["on_error"](exc)`（可选回调）→ 返回 `[]`。`main.py` 收集回调里的异常，得到 `failed_sources`，再传给生成层标注。

**代价 / 已知不足**
失败信息通过"回调 + 返回空列表"两条通道传达，读代码时容易只看返回值而忽略回调；日志里会同时出现 `Data source failed`（ERROR）和 `Data source will be marked as failed`（WARNING）两条。

**什么情况下应该重新评估**
需要"部分成功"（如某仓库成功、某仓库失败但保留成功部分）时，应改为返回 `Result` 对象而非纯列表。

---

## ADR-012 推送层返回 bool

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`notifier/email.py:18`、`notifier/lark_bot.py:17`

**当时的问题**
邮件和飞书是两个独立通道，一个挂了不该影响另一个，且编排层需要汇总"哪些发出去了"。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 抛 `NotifierError` | 与其他层一致 | 两个通道要各自 try/except，且"失败"是推送的**正常业务分支**，不是异常情况 |
| 返回 `(ok, detail)` 元组 | 信息更全 | 调用方多数只需要布尔；细节已经写进日志 |
| **返回 `bool`** ✅ | 编排层可直接 `{"email": email.send(...)}`；天然支持"部分成功" | — |

**决定**
`send(report, target) -> bool`。内部重试耗尽后返回 `False` 并记 ERROR 日志；配置缺失也返回 `False`（不抛）。

**代价 / 已知不足**
失败原因只能从日志找，调用方拿不到结构化原因；退出码只能表达"有通道失败"，无法区分是哪个通道。

**什么情况下应该重新评估**
需要按通道做差异化重试策略或告警路由时，改为返回结果对象。

---

## ADR-013 Markdown 为唯一事实源，HTML 派生

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`generator/template.py:render_markdown` / `render_html`

**当时的问题**
邮件要 HTML，飞书要 Markdown。如果两套分别渲染，内容一定会逐渐不一致。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 分别渲染两份 | 各自可最优 | 两份真相，改一处忘一处；测试要断言两遍 |
| 先 HTML 再转 Markdown | HTML 表达力强 | 反向转换有损，且飞书需要的是 Markdown 源码 |
| **先 Markdown，再转 HTML** ✅ | 单一事实源；Markdown 本身可读、可归档、可直接贴群 | — |

**决定**
`generate()` 先生成 `markdown`，再由它派生 `html`；`DailyReport` 同时携带两者，邮件用 `html`、飞书用 `markdown`。

**代价 / 已知不足**
自写的 `markdown_to_html()` 只支持四种子集（标题、无序列表、粗体、行内代码），超出范围的 Markdown 语法不会被渲染——所以生成侧必须只产出这个子集。

**什么情况下应该重新评估**
日报需要表格、图片、折叠块等富文本时，换成熟 Markdown 库或直接产出结构化数据 + 两个渲染器。

---

## ADR-014 外部客户端一律可注入

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`collector/*.py`（`params["client"]`）、`notifier/email.py`（`target["smtp_factory"]`）

**当时的问题**
见 ADR-008 的测试动机；同时要保证生产路径不因可测试性而变复杂。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 全局单例客户端 | 简单 | 无法替换；测试必须打补丁 |
| 构造函数注入（改成类） | 面向对象标准做法 | 采集器是函数式契约 `collect(params)`，改成类会破坏 ADR-010 的统一编排 |
| **字典键注入 + 缺省创建** ✅ | 契约不变；生产调用无需传；测试可传替身 | — |

**决定**
`params.get("client")` 为空时自行创建并负责关闭（`owns_client` 标记）；非空时直接用、不关闭。

**代价 / 已知不足**
`owns_client` 这种"谁创建谁关闭"的模式容易漏写 `finally`，本项目所有采集器都用 `try/finally` 保证。

**什么情况下应该重新评估**
采集器数量增长到需要共享连接池时，改为由 `main.py` 统一创建并传入。

---

## ADR-015 配置只存环境变量名

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`main.py:_env`、`config.yaml.example` 中的 `*_env` 字段

**当时的问题**
`config.yaml` 需要随仓库分发（含团队/仓库/群组等业务信息），而 token、secret 绝不能进版本库。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 密钥直接写 `config.yaml` 并 gitignore 整个文件 | 直观 | 模板文件也就无法展示完整结构；且团队成员容易误提交 |
| 只用环境变量（无配置文件） | 安全 | 成员映射、仓库列表这类结构化配置放环境变量里极难维护 |
| **配置存变量名 + 值走环境变量** ✅ | 配置文件可公开、可提交模板；密钥与业务配置分离 | — |

**决定**
`token_env: "GITHUB_TOKEN"` 这种间接引用；`_env()` 读取真实值，缺失时抛 `ConfigurationError`（可选项返回 `None`）。

**代价 / 已知不足**
多一层间接：**把 token 直接填进 `token_env` 会静默失效**——`os.getenv("ghp_xxx...")` 返回空，报错信息只说"missing GitHub token"。这个坑实际踩过一次（见 `ACCEPTANCE.md` 待办记录）。

**什么情况下应该重新评估**
引入密钥管理服务（Vault/KMS）时，把 `_env()` 换成 provider 抽象即可。

---

## ADR-016 自写 JSON Lines 日志 Formatter

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`shared/logger.py`

**当时的问题**
proposal §6（design.md）要求"JSON 日志"，且需要携带结构化字段（`source`、`count_*`、`duration_seconds`）供人工或工具检索。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| `logging.Formatter` 纯文本 | 标准库默认 | 不满足 JSON 要求，字段无法结构化 |
| `python-json-logger` 库 | 现成 | 为 30 行代码引入第三方依赖不划算 |
| **自写 `JsonLineFormatter`** ✅ | 零依赖；用 `extra_` 前缀约定字段，避免与 LogRecord 内置属性冲突 | — |

**决定**
所有结构化字段通过 `log_extra(**values)` 以 `extra_` 前缀注入，Formatter 剥离前缀后展开到 JSON 顶层；异常用 `exc_info=True` 序列化为 `exception` 字段。

**代价 / 已知不足**
`log_extra()` 的返回值必须作为 `extra=` 传入，直接 `logger.info(..., **log_extra(...))` 不会生效；这是本项目最容易写错的地方之一。另外 `extra_` 前缀是自定义约定，非标准。

**什么情况下应该重新评估**
需要接入集中式日志平台（Loki/ELK）时，可换成该平台的标准 Formatter。

---

## ADR-017 统一"3 次尝试"，并区分限流与无权限

- **状态**：已采纳（2026-11-12 修正）
- **日期**：2026-11-06 初版 / 2026-11-12 修正 403 判定
- **代码位置**：`collector/github.py:140`、`collector/lark_task.py:151`、`collector/lark_msg.py:150`、`notifier/*.py:MAX_ATTEMPTS`

**当时的问题**
外部 API 会超时、会限流；重试次数与间隔需要一个统一约定，否则每个模块各写一套。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 指数退避 + 抖动 | 更抗雪崩 | 每天只跑一次、请求量小，复杂退避带来的收益接近于零，反而拖长最坏耗时 |
| 无限重试 | 最大化成功率 | 会卡死 cron 任务，违背"60s 内完成"的验收要求 |
| **固定 3 次尝试、间隔可配（默认 5s）** ✅ | 简单、可预期、可通过 `retry_interval` 调整 | — |

**决定**
采集与推送统一"最多 3 次尝试"（即首次 + 2 次重试），间隔默认 5s，可由配置覆盖；测试里设为 0 以免拖慢。

**一处重要的修正（2026-11-12）**
初版把**所有 403 都当限流**处理（`_is_rate_limited` 恒为真），导致"token 无权限"这类不可恢复错误白白等待 reset 时间。现改为：只有 `X-RateLimit-Remaining: 0` 或存在 `Retry-After` 才判定限流，否则立即失败。**教训：限流是可恢复错误，权限不是，两者必须分开。**

**同一类错误的第二处（2026-11-12）**
飞书 `99991672`（缺 scope）曾被误列入"token 过期"码表，触发无意义的刷新并掩盖真实原因。现已移出，并加了两个回归测试锁住行为。

**代价 / 已知不足**
固定间隔在最坏情况下（3 次 × 5s）会多耗 10s；Docker 部署时若同时遇到限流可能挤占 60s 预算。

**什么情况下应该重新评估**
请求量上升、或频繁触发限流时，改为指数退避；或按错误类型分级重试。

---

## ADR-018 日报写入幂等（按日期 upsert）

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`shared/storage.py:48`

**当时的问题**
配置修正后需要当天重跑；如果每次插入新行，同一天会出现多份日报，读者无法判断哪份是最终版。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 每次 INSERT + 版本号 | 保留历史 | proposal §2.2 明确不做历史版本分析，多版本只会造成歧义 |
| 先 DELETE 再 INSERT | 简单 | 非原子操作，中途失败会丢数据 |
| **`ON CONFLICT(report_date) DO UPDATE`** ✅ | 单语句原子 upsert | — |

**决定**
`report_date` 建唯一约束，写入用 upsert；重复运行会覆盖当天记录。

**代价 / 已知不足**
重跑会**无条件覆盖**，人工修正过的内容也会被冲掉（本期不允许人工编辑，所以可接受）；SQLite 的 `lastrowid` 在冲突分支不可靠，代码里对返回 `0` 的情况做了二次查询兜底。

**什么情况下应该重新评估**
需要审计"谁改了什么"时，改为追加式表 + 视图取最新。

---

## ADR-019 采集窗口滚动 24 小时

- **状态**：已采纳
- **日期**：2026-11-12
- **代码位置**：`main.py:_collection_window`

**当时的问题**
proposal §3.1 写"过去 24h"，而术语表原写"当日 00:00 至执行时刻"，两者矛盾。同时真实场景里提交常跨零点。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 自然日 00:00 → 现在 | 与"日报=今天"直觉一致 | 昨晚 23:00 的提交会彻底丢失；周一早上会漏掉周末跨零点的记录 |
| 自然日 00:00 → 23:59，按 `committed_at` 归日 | 归属最严格 | 需要二次归集，且同一份数据会在两天的日报里处理两次 |
| **滚动 24 小时** ✅ | 跨零点不丢数据；与 proposal §3.1 措辞一致 | — |

**决定**
终点为执行时刻（指定历史日期时为当日 23:59:59），起点为终点前 24 小时。术语表已同步修正。

**代价 / 已知不足**
同一条记录只会出现在"执行时刻所在的那一份"日报里——昨晚的记录进今天，就不会出现在昨天那份里。若要严格自然日归属，需改成按 `committed_at` 分组。

**什么情况下应该重新评估**
业务要求"日报严格对应自然日"时（例如做考勤统计），必须改回自然日归属。

---

## ADR-020 调度用容器内 cron，不用常驻调度器

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`docker/entrypoint.sh`、`Dockerfile`

**当时的问题**
proposal §2.3 要求"基于 Cron 的定时任务，不需要常驻后台服务"，且部署在公司内网单机。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 宿主机 crontab 调 `docker exec` | 无需容器内 cron | 依赖宿主机配置，迁移环境要重新配；容器重启后易失效 |
| APScheduler 常驻进程 | 纯 Python、可动态改调度 | 违背"非常驻"约束；需自己处理进程守护 |
| Kubernetes CronJob | 生产级 | 内网单机场景过度设计 |
| **容器内 cron** ✅ | 自包含；`docker compose up` 即完成部署；日志直接进容器 stdout | — |

**决定**
镜像内安装 cron，`entrypoint.sh` 生成 `/etc/cron.d/daily-report`（`0 18 * * 1-5`，`CRON_TZ` 可配），实际执行 `.venv/bin/python main.py`。

**代价 / 已知不足**
cron 不继承容器环境变量，因此入口脚本会把 `os.environ` 落盘成 `.cron-env.sh` 供任务 source——这一步如果漏掉，任务会因缺 token 静默失败。

**什么情况下应该重新评估**
迁移到 Kubernetes 时改用 CronJob，容器只保留一次性执行模式（ADR-021 已支持）。

---

## ADR-021 入口脚本双模式

- **状态**：已采纳
- **日期**：2026-11-06
- **代码位置**：`docker/entrypoint.sh`

**当时的问题**
同一个镜像既要能"起调度器"，又要能"临时跑一次 `--check` / `--dry-run`"，否则调试要重建镜像。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 两个镜像 | 职责单一 | 构建与维护成本翻倍 |
| 用环境变量开关 | 无参数 | 调试时容易忘记设/忘记清，行为不直观 |
| **首参数判断（有参数即透传）** ✅ | `docker run image python main.py --check` 直接可用；无参数才起调度 | — |

**决定**
`entrypoint.sh` 里：首参数存在且不等于 `scheduler` → `exec "$@"`；否则起 cron。同时把 `/app/.venv/bin` 前置到 `PATH`，保证 `python` 指向装了依赖的解释器。

**代价 / 已知不足**
脚本必须带 shebang（`#!/bin/sh`）并保持 LF 行尾——两者缺失会导致 `exec format error`，本项目实际踩过（在 Windows 上创建的文件是 CRLF）。

**什么情况下应该重新评估**
需要更复杂的启动编排时，考虑用 `tini`/`s6-overlay` 之类。

---

## ADR-022 报告同时落盘为文件

- **状态**：已采纳
- **日期**：2026-11-12
- **代码位置**：`shared/storage.py:export_report_files`、`main.py:run`

**当时的问题**
早期只把日报写进 SQLite，`docker-compose.yml` 里挂载的 `./reports` 和 `Dockerfile` 里的 `mkdir -p /app/reports` 成了死配置——目录存在但永远是空的。实际使用中想看某天的日报，只能写 Python 或 SQL 去库里捞。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 只存数据库（原状） | 单一事实源，无一致性问题 | 无法直接用编辑器/浏览器打开；`reports/` 成为误导性的死配置 |
| 只存文件 | 直观、可直接归档 | 无法按日期唯一索引与结构化查询；重跑易产生重复文件 |
| **数据库为主 + 文件为副本** ✅ | 库用于查询与幂等，文件用于人读与归档；两者内容同源同次写入 | — |

**决定**
`ReportStorage` 仍负责 SQLite；新增 `export_report_files()` 把同一份 `markdown`/`html` 写成 `reports/<日期>.md` 与 `reports/<日期>.html`。写入用「临时文件 + `os.replace`」保证原子性，同日重跑直接覆盖，不留部分文件。

**代价 / 已知不足**
存在两份副本，理论上可能不一致（例如手工删了文件、或手工改了文件）。当前以数据库为准，文件只是导出物；若文件写入失败，**不阻断推送**（库已存好），只记 ERROR 日志。目录可通过 `storage.reports_dir` 配置。

**什么情况下应该重新评估**
若出现"需要以文件为事实源"的诉求（如用 Git 管理日报历史），应反转方向：改为文件为主、库做索引，并加入一致性校验。

---

## ADR-023 分页参数必须夹在上限内

- **状态**：已采纳
- **日期**：2026-11-13
- **代码位置**：`collector/lark_msg.py:_page_size`、`MAX_PAGE_SIZE = 50`

**当时的问题**
飞书 `GET /open-apis/im/v1/messages` 的 `page_size` 上限是 **50**，而采集器默认传 **100**。实测发现参数超限时飞书返回的是 `99992402 field validation failed`，而不是权限错误——这导致排查方向被彻底带偏。

**实测证据**

```
page_size=50   → 99991672  Access denied（真实原因：缺 im:message:readonly）
page_size=51   → 99992402  field validation failed
page_size=100  → 99992402  field validation failed   ← 采集器当时用的值
```

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 只改默认值为 50 | 一行改动 | 调用方显式传 100 时仍会踩坑，且错误信息依旧误导 |
| 超限时抛异常提示 | 显式 | 运行时才炸，不解决问题 |
| **取值时夹在 1–50** ✅ | 永不产生超限请求；调用方传什么都不至于误报 | — |

**决定**
`_page_size()` 对入参做 `max(1, min(requested, 50))`，并把常量与原因写进代码注释。

**代价 / 已知不足**
若调用方期望"一次取 200 条"，实际只会拿到 50 条——但分页循环会继续翻页，最终结果仍完整，只是请求数变多。

**什么情况下应该重新评估**
飞书放宽该接口上限时（需重新实测确认，不能凭文档假设）。

---

## ADR-024 程序自己加载 `.env`，而不是要求调用方注入

- **状态**：已采纳
- **日期**：2026-11-13
- **代码位置**：`shared/config.py:load_dotenv`、`main.py:main`

**当时的问题**
密钥全部放在 `.env`，但 `.env` 只是文件，不会被进程读取。实测直接 `python main.py --check` 的结果：

```
environment      FAIL  missing: GITHUB_TOKEN, LARK_APP_ID, LARK_APP_SECRET,
                              LARK_WEBHOOK_URL, SMTP_HOST, SMTP_FROM
```

也就是说"手动跑一次日报"需要用户先记住一条加载环境变量的命令（`uv run --env-file .env ...` 或 PowerShell 的循环导出），否则每次都要重新踩一遍坑。定时任务和容器不受影响（cron 有外部注入、compose 有 `env_file`），**只有人工触发这个场景会失败**——而人工触发恰恰是最需要"零门槛"的场景。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 文档里写清 `--env-file` | 零改动 | 依赖人记住，且 cron / `make` / IDE 各写一遍 |
| 引入 `python-dotenv` | 现成实现 | 为一个 20 行解析器加依赖，与"依赖最小化"冲突 |
| 写 `run.ps1` / `run.sh` 包装 | 不改主程序 | 两套脚本要各自维护，容器内用不上 |
| **`load_dotenv()` 内置到启动流程** ✅ | 任何入口都一致；仍兼容外部注入 | — |

**决定**
`shared/config.py` 提供 `load_dotenv(*paths, override=False)`：手工解析（支持注释、空行、`export` 前缀、成对引号），**已存在的环境变量优先**；`main()` 在解析参数后依次尝试 `config.yaml` 同目录与当前目录的 `.env`，缺失文件直接跳过。

**代价 / 已知不足**
- 不覆盖，所以想临时改一个值仍需在 shell 里覆盖（这是刻意的：`FOO=bar python main.py` 必须生效）
- 不支持多行值与变量插值（`${OTHER}`），当前密钥都是单行常量，够用
- 与 `override=False` 搭配的副作用：若 shell 里残留了过期的同名变量，程序会用它而不是 `.env` 里的新值

**什么情况下应该重新评估**
出现多环境（`.env.dev` / `.env.prod`）或需要变量插值时，改用 `python-dotenv` 或 `pydantic-settings`，不要在自研解析器上继续堆特性。

---

## ADR-025 把 CA 信任做成配置项，而不是让用户改代码或关软件

- **状态**：已采纳
- **日期**：2026-11-13
- **代码位置**：`shared/config.py:ca_bundle`、`collector/*.py` 的 `verify=params.get("verify", True)`

**当时的问题**
本机安装了 Watt Toolkit（Steam++）并开启了 GitHub 加速。它把 35 个 GitHub 域名写入 Windows hosts 指向 `127.0.0.1`，由 `Steam++.Accelerator` 在 443 端口做 TLS 卸载。实测后果：

```
Windows 本机：github  FAIL  SSL: CERTIFICATE_VERIFY_FAILED
容器内：      github  FAIL  Connection refused
```

两者原因不同：本机是被 MITM 证书拦下（certifi 不信任 `CN=SteamTools Certificate`），容器里 `127.0.0.1` 指向容器自身所以根本没有监听者。

关键事实：**`api.github.com` 直连本来就是通的**（`--add-host` 绕过 hosts 实测 `HTTP 200`），只有 `github.com` 网页直连超时。也就是说加速器对采集毫无必要，却把采集链路弄断了。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 让用户关掉加速器 | 零代码改动 | `github.com` 网页直连不通，用户装它就是为了浏览 |
| 把真实 IP 写进 hosts | 直接 | 加速器启动会覆盖 hosts；IP 也会变 |
| 代码里 `verify=False` | 一行 | 关闭全部校验，等于放弃中间人防护 |
| 硬编码信任某个证书 | 简单 | 证书与机器绑定，不能进仓库 |
| **配置项 `network.ca_bundle`** ✅ | 与机器解耦；加速器开或关都兼容；容器也能复用同一份文件 | — |

**决定**
`ca_bundle(config)` 返回 httpx 的 `verify` 取值：未配置时返回 `True`（保持 certifi 默认），配置了路径就校验文件存在并返回该路径。main.py 把它注入每个 params/target 字典，四个 httpx 客户端统一读取 `params.get("verify", True)`。容器侧另加 `dns: ${DOCKER_DNS:-223.5.5.5}`，绕开 Windows hosts 继承。（本次同时把 `github.com`/`api.github.com` 的真实可达性写进了验证记录，便于日后判断该关哪个开关。）

**代价 / 已知不足**
- `certs/` 必须 gitignore，新机器要重新导出一遍（README 给了 6 行脚本）
- 只在配置了 `ca_bundle` 时才生效，`--check` 增加一项 `ca_bundle` 便于确认当前走的是默认还是自定义信任库
- SMTP 未接入该配置（`smtp.qq.com` 不在劫持列表内）；若将来邮件服务器被代理，需要另加 `ssl.SSLContext`
- 容器改用外部 DNS 后不再遵循企业内网解析，局域网服务名需通过 `DOCKER_DNS` 指定

**什么情况下应该重新评估**
代理改为系统级（PAC / 全局透明代理）而不依赖 hosts 时，应改用 `SSL_CERT_FILE` 环境变量或把 CA 装入系统信任库，而不是逐客户端传参。

---

## ADR-026 归属身份改成「多键匹配」，并给提交记录补上邮箱

- **状态**：已采纳
- **日期**：2026-11-13
- **代码位置**：`main.py:_member_keys` / `_matches`、`collector/github.py:CommitRecord.author_email`、`shared/config.py` 的成员校验

**当时的问题**
排查"怎么区分不同人做的任务"时实测发现，归属其实只靠一个键在工作：

```
commit author : ['原良']            ← 汉字姓名，不是 GitHub 登录名 yyy605
GitHub API    : author(账号) = null, git.author = {name: 原良, email: 20233076@qq.com}
配置里的 email : member13587511845@qq.com   ← 与 git 邮箱不一致
```

GitHub 只在提交邮箱已绑定到账号时才返回 `author.login`，否则给 `null`。本机 `git config user.email` 是 `20233076@qq.com`，未绑定到 `yyy605`，于是采集器退回读 `commit.author.name`。结果：**改一下 git 用户名，整条归属链就断了**；而配置里的 `email` 与 git 邮箱又对不上，连兜底都没有。

**考虑过的方案**

| 方案 | 优点 | 为什么没选 |
| --- | --- | --- |
| 只让用户去 GitHub 绑邮箱 | 零代码 | 绑定是不可控的外部动作；没绑定的人照样归不到 |
| 把邮箱拼进 `author` 字段当匹配值 | 不改 schema | `author` 会渲染进日报（` — 原良`），拼上邮箱就无法阅读 |
| 用登录名强制覆盖 | 简单 | `author: null` 时根本没有登录名可覆盖 |
| **多键匹配 + 给记录补 `author_email`** ✅ | 姓名、登录名、git 邮箱任一条都能归属；互不依赖 | — |

**决定**
1. `CommitRecord` 增加 `author_email`（默认空串），采集时取 `commit.author.email`；
2. 成员支持可选列表 `git_emails`、`aliases`，加载时校验必须是字符串或非空字符串列表，避免拼错成 mapping 后静默失效；
3. `_matches(keys, *values)` 接受多个候选值，提交记录同时用 `author` 与 `author_email` 比对，任务/消息仍各用一个字段。

**代价 / 已知不足**
- `CommitRecord` 从 7 字段变为 8 字段，`test_collect_returns_seven_field_commit_record` 相应改名并断言 8 字段；这是内部契约，task/proposal/design 三份文档未固定字段个数
- 多键放宽了匹配面：两个成员如果填了同一个邮箱/别名，同一条记录会同时归给两人（当前行为是"任一命中即归属"，不做唯一性告警）
- `git_emails` 需要人工维护，换机器改了 `git config user.email` 后要同步

**什么情况下应该重新评估**
若团队统一要求提交邮箱绑定到 GitHub 账号，`author.login` 恒定可用，`git_emails` 就退化为冗余保险，可考虑改为"只允许 login 匹配 + 未绑定即告警"。

---

## 附：被否决方案速查

| 方案 | 否决原因 | 相关 ADR |
| --- | --- | --- |
| requests / aiohttp / urllib | 超时默认值隐患 / 改造成本 / 手写太多 | ADR-001 |
| PostgreSQL、MySQL、ORM | 单机单表场景过度设计 | ADR-002 |
| asyncio 全异步 | 瓶颈是串行依赖，不是并发能力 | ADR-003 |
| 手拼 HTML 字符串 | 结构难维护，标题有 XSS 风险 | ADR-004 |
| 把密钥写进 config.yaml | 模板无法提交，易泄露 | ADR-015 |
| 所有 403 都重试 | 权限错误不可恢复，白等 | ADR-017 |
| 每次 INSERT 新行 | 同日多份日报造成歧义 | ADR-018 |
| 自然日 00:00 窗口 | 跨零点数据丢失 | ADR-019 |
| APScheduler 常驻 | 违背"无常驻服务"约束 | ADR-020 |
| 依赖 `python-dotenv` | 一个 20 行解析器不值得加依赖 | ADR-024 |
| 关闭 TLS 校验以穿过代理 | 等于放弃中间人防护 | ADR-025 |
| 只靠单个身份字段归属 | git 用户名一变就断链 | ADR-026 |

---

## 附：忘了为什么时，三个快速入口

1. **跑测试看行为契约** —— 测试名就是断言清单
   ```bash
   uv run pytest -v
   ```
2. **跑自检查环境假设** —— 六个外部依赖各自是否可达
   ```bash
   uv run python main.py --check
   ```
3. **查本文档的代码锚点** —— 每条 ADR 都给了文件与行号，直接跳过去看实现。

若发现某条 ADR 的「重新评估条件」已经满足，请在改动前更新本文件，并把决策时间与理由写进对应条目——**否则下一个人还会问同一个问题。**
