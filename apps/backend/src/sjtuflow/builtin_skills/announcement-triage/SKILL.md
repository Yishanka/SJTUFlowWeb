# Announcement Triage

## Purpose

把 Canvas 最近公告分流成"需要 action"、"新增 DDL / 资料"、"仅通知"三类，避免重要 action 被淹没。本 skill 只读且不回复任何公告。

## Required Tools

- `canvas.connection_status`
- `canvas.list_courses`
- `canvas.list_recent_announcements`

## Workflow

1. 确认范围：
   - 默认全部 active 课程：`canvas.list_courses(enrollment_state="active")`。
   - 用户指定单课时只看那门，按 `course_id` 过滤。
2. 时间窗口：默认最近 14 天（`since_days=14`）；用户给出范围时遵从。
3. 拉取：`canvas.list_recent_announcements(since_days=<window>)`，按 `course_id` 过滤到目标课程集合。
4. 逐条按启发式分类，**不要脑补**：
   - **需要 action**：标题或正文出现 "请回复 / 请确认 / 请填 / 报名 / 选组 / 投票 / RSVP / confirm by / fill / sign up" 等表述。
   - **新增 DDL / 资料**：提到具体作业、考试、课件链接、ddl 时间；建议用户跳到 `assignment-planning` 或 `course-briefing`。
   - **仅通知**：以上都不是。
5. 每条标注：所属课程、发布时间、announcement_id、建议动作、置信度（高 / 中 / 低）。低置信度条目必须显式写"启发式判断，请人工确认"。
6. 末尾汇总"今天必须处理"清单：仅含"需要 action"类、且截止时间在 48 小时内的条目。

## Output

聊天中以 Markdown 块返回：

```
# 公告分流 · 最近 <N> 天

## 需要 action（<count>）
- [<date>][<课程>] <title>
  - 建议动作：<一句话具体动作>
  - 置信度：高 / 中 / 低
  - announcement_id=<id>

## 新增 DDL / 资料（<count>）
- [<date>][<课程>] <title> — 跳到 `assignment-planning` / `course-briefing`
  - announcement_id=<id>

## 仅通知（<count>）
- [<date>][<课程>] <title>（announcement_id=<id>）

## 今天必须处理
- ...
```

不写盘。结果只在聊天里展示。

## Safety

- 本 skill 只读；不做任何回复 / 提交 / 状态变更。Canvas 即使有 reply 类写工具也禁止在本 skill 中调用。
- 分类基于关键词启发式，**不是确定性判断**；输出中必须显式标注置信度，让用户对低置信度条目人工核验。
- Canvas 调用前可先 `canvas.connection_status(ping=true)` 确认 token 有效；失败立即停止并提示用户检查 token，不做降级抓取。
- 每类最多展示 20 条，多余用 `+N more` 收起，由用户追问。
- 公告涉及成绩 / 隐私 / 点名等敏感内容时，输出中提示"数据仅本地展示"。
