# Weekly Review

## Overview

汇总最近课程更新、未来作业截止时间和需要用户关注的 Canvas 信息，生成一份简洁周报。

## Required Tools

- `canvas.list_upcoming_assignments`
- `canvas.list_recent_announcements`
- `canvas.list_courses`
- `filesystem.write_text`

## Workflow

1. 读取未来 14 天 Canvas 作业与最近 3 天公告。
2. 按课程合并重复事项，优先列出最近截止的任务。
3. 标记缺少截止时间、无法访问课程或认证失败的警告。
4. 如果用户要求保存，写入 `reports/weekly-review-YYYY-MM-DD.md`，写入前必须确认。

## Output

用中文输出：

- 本周最紧急事项
- 未来两周 DDL
- 最近公告更新
- 建议行动

