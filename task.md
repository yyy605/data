# 智能日报生成器 — 任务清单
## 元信息
- 关联规范：specs/proposal.md、specs/design.md
- 任务总数：10
- 预计总执行时间：3 到 4h（包含测试）
- 执行策略：按依赖关系顺序执行，独立任务可并行执行

---

## Task 1：创建项目结构和基础配置
描述：初始化项目目录结构，创建pyproject.toml，安装核心依赖，创建配置文件模板

输入：specs/design.md（模块职责定义）
输出：项目根目录结构 + pyproject.toml + config.yaml.example
依赖：无

验收标准：
- [x] 目录结构符合 design.md §2 的模块划分（collector/、generator/、notifier/、shared/、tests/）
- [x] pyproject.toml 包含 httpx、pyyaml、jinja2 依赖
- [x] uv sync 成功安装所有依赖
- [x] config.yaml.example 包含 members 映射表模板

---

## Task 2：实现共享基础层
描述：实现配置读取、日志记录、错误处理和SQLite存储4个共享模块

输入：config.yaml.example（配置格式）、design.md §6（非功能约束）
输出：shared/config.py、shared/logger.py、shared/errors.py、shared/storage.py
依赖：Task 1

验收标准：
- [x] config.py 能读取 config.yaml 并返回配置对象
- [x] config.py 在配置缺少必填字段时抛出明确的错误信息
- [x] logger.py 输出 JSON lines 格式日志
- [x] errors.py 定义 CollectorError、GeneratorError、NotifierError 三个自定义异常
- [x] storage.py 能创建SQLite，写入和查询日报记录
- [x] 所有模块包含对应的单元测试，测试通过

---

## Task 3：实现GitHub采集模块
描述：调用GitHub API获取指定仓库在指定时间范围内的Commit记录，返回CommitRecord列表

输入：design.md §3（CommitRecord 数据模型）、design.md §4.1（采集层接口契约）
输出：collector/github.py + tests/test_github.py
依赖：Task 2

验收标准：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个CommitRecord包含全部7个字段且类型正确
- [x] 支持分页查询（GitHub API默认每页30条）
- [x] API超时重试3次（间隔5s），若仍失败则返回空列表 + 错误日志
- [x] GitHub API限流（HTTP 403）时，等待reset时间后重试
- [x] 使用Mock数据的单元测试全部通过

---

## Task 4：实现飞书任务采集模块
描述：调用飞书开放平台API获取指定项目中当日状态变更的任务列表，返回TaskRecord列表

输入：design.md §3（TaskRecord 数据模型）、design.md §4.1（采集层接口契约）
输出：collector/lark_task.py + tests/test_lark_task.py
依赖：Task 2

验收标准：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个TaskRecord包含全部5个字段且类型正确
- [x] 飞书Token过期时自动刷新后重试1次
- [x] API超时重试3次，若仍失败则返回空列表 + 错误日志
- [x] 使用Mock数据的单元测试全部通过

---

## Task 5：实现飞书消息采集模块
描述：调用飞书开放平台API获取指定群的消息记录，按关键词过滤，返回MessageRecord列表

输入：design.md §3（MessageRecord 数据模型）、design.md §4.1（采集层接口契约）
输出：collector/lark_msg.py + tests/test_lark_msg.py
依赖：Task 2

验收标准：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个MessageRecord包含全部4个字段且类型正确
- [x] 关键词过滤正确，只返回包含指定关键词的消息
- [x] 敏感关键词（薪资、绩效等）被正确过滤，不出现在结果中
- [x] 飞书Token过期时自动刷新后重试1次
- [x] 使用Mock数据的单元测试全部通过

---

## Task 6：实现日报生成模块
描述：接收各成员的采集数据，按“代码提交→任务进展→协作沟通”组织日报内容，输出Markdown和HTML格式文件

输入：design.md §3（MemberReport、DailyReport 数据模型）、design.md §4.2（生成层接口契约）
输出：generator/formatter.py、generator/template.py + tests/test_generator.py
依赖：Task 2

验收标准：
- [x] generate() 函数签名符合 design.md §4.2 的接口定义
- [x] 输出的DailyReport.markdown包含三个部分标题
- [x] 输出的DailyReport.html可被浏览器正确渲染
- [x] 若某成员当日无任何记录，日报中需要显示“今日无记录”
- [x] 若某数据源采集失败，日报中需要标注“数据获取失败”
- [x] Markdown到HTML的转换格式正确
- [x] 使用Mock数据的单元测试全部通过

---

## Task 7：实现邮件推送模块
描述：将HTML格式日报通过SMTP发送给指定收件人

输入：design.md §4.3（推送层接口契约）
输出：notifier/email.py + tests/test_email.py
依赖：Task 6

验收标准：
- [x] send() 函数签名符合 design.md §4.3 的接口定义
- [x] 邮件主题包含日期和团队名称
- [x] 邮件正文为HTML格式的日报内容
- [x] 发送失败时重试2次，仍失败则返回False + 错误日志
- [x] 使用Mock SMTP的单元测试通过

---

## Task 8：实现飞书推送模块
描述：将Markdown格式日报通过飞书机器人webhook发送到指定群

输入：design.md §4.3（推送层接口契约）
输出：notifier/lark_bot.py + tests/test_lark_bot.py
依赖：Task 6

验收标准：
- [x] send() 函数签名符合 design.md §4.3 的接口定义
- [x] 发送的消息为Markdown格式
- [x] 发送失败时重试2次，仍失败则返回False + 错误日志
- [x] 使用Mock webhook的单元测试通过

---

## Task 9：实现主编排入口
描述：创建main.py作为系统入口，按顺序编排“采集→聚合→生成→推送”的完整流程

输入：所有模块的接口
输出：main.py
依赖：Task 3、Task 4、Task 5、Task 6、Task 7、Task 8

验收标准：
- [x] python main.py 执行完整的日报生成流程
- [x] python main.py --check 验证所有API连接和配置
- [x] python main.py --dry-run 执行采集和生成但不推送
- [x] 若单个数据源失败，不应影响其他数据源的采集
- [x] 若所有数据源失败，不生成空日报，记录错误日志
- [x] 执行日志包含：开始时间、各数据源数据采集条数、推送结果、结束时间

---

## Task 10：集成测试
描述：编写端到端集成测试，验证完整流程在各种场景下的行为

输入：main.py + 所有模块
输出：tests/test_integration.py
依赖：Task 9

验收标准：
- [x] 正常场景：所有数据源可用，日报生成并推送成功
- [x] 降级场景：若某个数据源超时，日报中需要标注“数据获取失败”，其他部分正常
- [x] 空数据场景：若某成员当日无任何记录，日报中需要显示“今日无记录”
- [x] 全部失败场景：若所有数据源不可用，不生成日报，记录错误日志
- [x] 执行耗时少于60s（Mock环境下）

---

## 执行顺序
推荐执行顺序（考虑依赖关系和并行机会）：
阶段 1：Task 1（项目初始化）
阶段 2：Task 2（共享基础层）
阶段 3：Task 3+Task 4+Task 5 并行（三个采集模块）
阶段 4：Task 6（日报生成模块）
阶段 5：Task 7+Task 8 并行（两个推送模块）
阶段 6：Task 9（主编排入口）
阶段 7：Task 10（集成测试）

---

## 验证状态说明

### 已自动化验证
- 全部单元测试与集成测试通过（`pytest`，74 个用例）
- GitHub Commit 采集、飞书消息采集、飞书 Token 获取端点经真实 API 探测确认存在
- 飞书任务采集使用 Task v2 接口（`/open-apis/task/v2/tasks`），真实 API 返回鉴权错误而非 404，路径有效
- HTML 由 Jinja2 模板（`generator/templates/report.html.jinja2`）渲染，标题自动转义
- Docker 镜像构建成功，容器内 CLI 与 cron 调度均验证通过
- 真实邮箱（QQ SMTP）与飞书机器人 webhook 均已实测推送成功

### 需真实凭据才能端到端验证
以下链路代码已完成且单测覆盖，但需要真实账号方能确认最终效果：
- 私有 GitHub 仓库的 Commit 与变更统计采集
- 飞书任务与群消息的实际数据拉取
- SMTP 邮件投递
- 飞书机器人 webhook 推送
- 5 人团队真实耗时（当前以 Mock 数据验证 < 60s）

### 口径说明
- 采集窗口采用**滚动 24 小时**：结束时间为执行时刻，或指定历史日期的当日 23:59:59，起点为终点前 24 小时
- 法定节假日与调休由 `chinesecalendar` 库自动识别，`schedule.holidays` / `schedule.workdays` 可手工覆盖特例
