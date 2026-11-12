from datetime import date

from collector.github import CommitRecord
from collector.lark_msg import MessageRecord
from collector.lark_task import TaskRecord
from generator.formatter import DailyReport, MemberReport, generate
from generator.template import markdown_to_html


def test_generate_contains_three_sections_and_renders_html():
    report = generate(
        [{
            "name": "张三",
            "commits": [CommitRecord(
                sha="abcdef1234567890",
                repository="example/repo",
                message="实现日报生成",
                author="张三",
                committed_at=date.today(),
                additions=20,
                deletions=2,
            )],
            "tasks": [TaskRecord(
                task_id="task-1",
                title="完成接口",
                status="进行中",
                assignee="张三",
                updated_at=date.today(),
            )],
            "messages": [MessageRecord(
                message_id="msg-1",
                sender_id="ou_zhangsan",
                content="项目风险已同步",
                sent_at=date.today(),
            )],
        }],
        "2026-11-06",
        "研发团队",
    )

    assert isinstance(report, DailyReport)
    assert "### 代码提交" in report.markdown
    assert "### 任务进展" in report.markdown
    assert "### 协作沟通" in report.markdown
    assert "<h3>代码提交</h3>" in report.html
    assert "实现日报生成" in report.html
    assert "<!doctype html>" in report.html.lower()


def test_generate_shows_no_records_for_empty_member():
    report = generate([{"name": "李四"}], date(2026, 11, 6), "研发团队")

    assert report.markdown.count("今日无记录") >= 3


def test_generate_marks_failed_sources():
    report = generate([{
        "name": "王五",
        "source_failures": {"github": True, "lark_task": True},
    }], "2026-11-06", "研发团队")

    assert "数据获取失败" in report.markdown
    assert report.markdown.count("数据获取失败") == 2


def test_markdown_to_html_formats_headings_lists_and_bold_text():
    html = markdown_to_html("# 标题\n\n## 成员\n\n- **数据获取失败**（GitHub）\n- 普通内容\n")

    assert html == (
        '<h1>标题</h1>\n<h2>成员</h2>\n<ul>\n'
        '<li><strong>数据获取失败</strong>（GitHub）</li>\n<li>普通内容</li>\n</ul>'
    )


def test_commit_message_is_collapsed_to_single_line():
    report = generate([{
        "name": "张三",
        "commits": [{
            "sha": "abcdef1234567890",
            "repository": "o/r",
            "message": "feat: 新增功能\n\n- 详细说明一\n- 详细说明二\n\nCo-authored-by: X <x@y.z>",
            "author": "zhangsan",
            "additions": 3,
            "deletions": 1,
        }],
    }], "2026-11-06", "研发团队")

    commit_lines = [
        line for line in report.markdown.splitlines()
        if line.startswith("- `abcdef1`")
    ]
    assert len(commit_lines) == 1
    assert "feat: 新增功能" in commit_lines[0]
    assert "详细说明一" not in report.markdown
    assert "Co-authored-by" not in report.markdown


def test_long_commit_subject_is_truncated():
    long_subject = "x" * 200
    report = generate([{
        "name": "张三",
        "commits": [{
            "sha": "abcdef1234567890",
            "repository": "o/r",
            "message": long_subject,
            "author": "zhangsan",
            "additions": 0,
            "deletions": 0,
        }],
    }], "2026-11-06", "研发团队")

    bullet = next(line for line in report.markdown.splitlines() if line.startswith("- `abcdef1`"))
    assert "x" * 200 not in bullet
    assert "…" in bullet


def test_html_is_rendered_from_jinja2_template():
    from generator.template import HTML_TEMPLATE, TEMPLATE_DIR

    assert (TEMPLATE_DIR / HTML_TEMPLATE).is_file()

    report = generate([{"name": "张三"}], "2026-11-06", "研发团队")

    assert report.html.lower().startswith("<!doctype html>")
    assert "<title>研发团队 2026-11-06 日报</title>" in report.html
    assert "{{" not in report.html


def test_html_title_is_escaped_but_body_keeps_markup():
    report = generate([{"name": "张三"}], "2026-11-06", "<script>alert(1)</script>")

    head, _, body = report.html.partition("<body>")
    assert "<script>" not in head
    assert "&lt;script&gt;" in head
    assert "<h2>张三</h2>" in body


def test_generate_accepts_member_report_objects():
    report = generate([
        MemberReport(name="赵六", commits=[], tasks=[], messages=[])
    ], "2026-11-06", "研发团队")

    assert report.members[0].name == "赵六"
    assert report.date == "2026-11-06"
