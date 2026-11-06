# 智能日报生成器 — 架构设计
## 1．系统架构
- 管道式架构：采集层 → 生成层 → 推送层 + 共享基础层
- [架构图]

## 2．模块职责
- `collector/`：数据采集（GitHub、飞书任务、飞书消息）
- `generator/`：日报生成（数据整理 + Markdown/HTML）
- `notifier/`：推送通知（邮件 + 飞书机器人）
- `shared/`：配置、日志、错误处理、存储
- `main.py`：编排入口

## 3．数据模型
- CommitRecord / TaskRecord / MessageRecord
- MemberReport / DailyReport

## 4．接口契约
- 采集层：`collect(params) → list[Record]`
- 生成层：`generate(members, date, team) → DailyReport`
- 推送层：`send(report, target) → bool`

## 5．技术选型（ADR）
- ADR-001: HTTP 客户端 → httpx
- ADR-002: 数据存储 → SQLite

## 6．非功能性约束
- 错误处理：优雅降级策略
- 安全性：密钥管理 + 数据脱敏
- 可运维性：JSON 日志 + 健康检查 + 配置热更新

