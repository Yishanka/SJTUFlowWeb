# Course Briefing

## Purpose

按课程聚合 Canvas 公告、作业和文件更新，输出一份"这门课最近发生了什么"的简报。

## Required Tools

- `canvas.connection_status`
- `canvas.list_courses`
- `canvas.list_recent_announcements`
- `canvas.list_assignments`
- `canvas.list_upcoming_assignments`
- `canvas.list_files`
- `transcripts.list`

## Workflow

1. 确认目标课程：
   - 用户给出明确课程名 / `course_id` 时跳到步骤 2。
   - 否则先 `canvas.list_courses`，从候选中让用户确认；不要自行选一门"看起来像"的课。
2. 限定时间窗口：默认"最近 14 天"（公告用 `since_days=14`；上游作业列表配合 `canvas.list_upcoming_assignments(window_days=14)`）。用户给出范围时遵从用户。
3. 顺序收集（只读，不下载文件本体）：
   - `canvas.list_recent_announcements(since_days=<window>)`：取标题、发布时间、摘要前 200 字；按 `course_id` 过滤为目标课程。
   - 当前作业：`canvas.list_assignments(course_id=<id>)`，列出本课程作业。
   - 临近作业：`canvas.list_upcoming_assignments(window_days=<window>)`，从结果中过滤本课程，标出最近的 DDL。
   - `canvas.list_files(course_id=<id>)`：取最近上传文件元数据；不下载。
   - `transcripts.list()`：同一课程是否有本地 transcript（按 transcript metadata 中的 `title/source` 匹配课程名）。
4. 按类别去重 + 按时间倒序。某类为空也显式写"无更新"，不要省略类别。
5. 末尾给出"建议下一步"：例如某作业临近截止、某文件可能要在课前预读、某 transcript 可用 `transcript-review` 进一步处理。

## Output

聊天中以 Markdown 块返回，结构：

```
# <课程名> · 最近 <N> 天

## 公告 (<count>)
- [<date>] <title> — <摘要前 200 字>

## 作业 (<count>)
- [<due_at>] <title>（<提交类型>, <points> 分）

## 文件 (<count>)
- [<updated_at>] <name>（<size>）

## 本地 transcripts (<count>)
- <title>（<updated_at>）

## 建议下一步
- ...
```

不写盘。每条来源标注 Canvas 对象 id（announcement_id / assignment_id / file_id）便于人工核验。

## Safety

- 本 skill 只读 Canvas，不下载附件、不上传作业、不修改任何远端状态。
- 工具调用前可先 `canvas.connection_status(ping=true)` 确认 token 有效；若失败立即停止并提示用户检查 token，不做任何降级抓取。
- 结果含成绩 / 点名等敏感信息时，在输出中提示"数据仅本地展示"。
- 每类最多展示 20 条，多余用 `+N more` 收起，由用户追问。
