# Final Review

## Purpose

按课程聚合 transcripts、作业和公告，生成一份可执行的期末复习计划。

## Required Tools

- `canvas.list_courses`
- `canvas.list_recent_announcements`
- `canvas.list_assignments`
- `canvas.list_upcoming_assignments`
- `canvas.list_files`
- `transcripts.list`
- `transcripts.read`
- `skills.list`

## Workflow

1. 确认范围：用户给出课程列表或"本学期所有课程"。后者用 `canvas.list_courses(enrollment_state="active")` 取活跃课程，按课程逐门处理；不要把所有数据并表混合。
2. 对每门课，按下面顺序收集（保持 metadata-first）：
   - 考试线索：`canvas.list_recent_announcements(since_days=60)`，过滤含 "final / 期末 / 考试 / exam / 考纲" 关键词的条目。
   - 作业历史：`canvas.list_assignments(course_id=<id>, include_past=true)`，标出权重高、题型与考试相关的几次。
   - 临近 DDL：`canvas.list_upcoming_assignments(window_days=21)`，过滤本课程。
   - 资料盘点：`canvas.list_files(course_id=<id>)` 概览（仅元数据，不下载）。
   - 本地 transcript 列表：`transcripts.list()`，按 metadata 中的 `title/source` 匹配该课程，挑出 3–5 条与考纲主题最相关的 `id`。
3. 对挑出的 transcript，逐条调用 `transcripts.read(transcript_id=<id>)`，按 `transcript-review` 的抽取规则归纳主题与考点（避免重复；建议直接建议用户对单条 transcript 调用 `transcript-review` skill）。
4. 综合输出复习计划：
   - "考纲主题 → 关联资料（文件 / transcript / 作业）→ 学习动作 → 预计用时" 四列表。
   - 按天 / 周的时间表，向考试日期倒推。
   - 标记不确定信息（例如未确认的考试日期、考试范围模糊点），让用户先去核对。
5. 末尾追加"下一步建议"：例如调用 `assignment-planning` 复盘某次大作业；或让用户在 UI 上确认 `canvas.download_file` 下载某课件。

## Output

聊天中以 Markdown 返回，结构：

```
# <课程名> · 期末复习计划

## 考试基本信息（来自 Canvas）
- 日期：<date> 或 "Canvas 未明确，建议核对"
- 形式：<闭卷/开卷/线上> 或 "未知"
- 占比：<percent> 或 "未知"

## 重点主题
| 主题 | 关联资料 | 动作 | 预计用时 |
|---|---|---|---|
| ... | transcripts/<title>、files/<name> | 精读 + 自测 | 2h |

## 时间表
- D-14 ~ D-8：...
- D-7 ~ D-3：...
- D-2 ~ D-0：...

## 风险与不确定
- ...

## 下一步建议
- 对 transcripts/<title> 调用 transcript-review
- 让用户在 UI 上确认下载 files/<name>
```

默认不写盘。如需保存到 `~/SJTUFlowData/reports/`，由用户在 UI 上确认。

## Safety

- 本 skill 只读，不下载文件、不修改远端、不调用任何写工具。
- 涉及考试日期 / 考纲范围等强声明信息时，必须显式标注"以课程通知为准"，避免误导。
- 每门课最多读 5 条 transcript（`transcripts.read` 调用 ≤ 5 次），更多由用户分次追问。
- Canvas 调用失败立即停止并报告，不做绕过。
