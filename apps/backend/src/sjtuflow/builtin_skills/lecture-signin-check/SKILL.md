# Lecture Sign-in Check

## Purpose

回答"某次课老师是否提到签到 / 点名 / attendance"这类问题。优先基于本地 transcript；没有 transcript 时，使用 SJTUFlow 托管的本地浏览器会话进入 Canvas 课程视频页，解析媒体流、转写入库，再回答。

## Required Tools

- `transcripts.list`
- `transcripts.search`
- `transcripts.read`
- `canvas.list_courses`
- `media.canvas_access_hint`
- `media.find_canvas_pages`
- `media.resolve_canvas_page`
- `media.transcribe_canvas_page`

## Workflow

1. 明确目标课程和日期：
   - 从用户问题中提取课程名、日期、讲次、"今天 / 昨天"等线索。
   - 如果课程或日期缺失，先用已有上下文推断；仍不明确时向用户确认。
2. 查本地资料库：
   - 先调用 `transcripts.list`，按课程名、日期、讲次、source 过滤候选。
   - 再用 `transcripts.search(query="签到")`、`transcripts.search(query="点名")`、`transcripts.search(query="attendance")` 辅助定位。
   - 找到 1-3 条候选后调用 `transcripts.read`，只读取必要 transcript。
3. 如果 transcript 已存在：
   - 严格基于 transcript 内容回答是否提到签到。
   - 找到相关片段时给出时间戳和简短原文引用。
   - 找不到时回答"本地 transcript 未提及签到 / 点名"，不要凭常识补全。
4. 如果 transcript 不存在：
   - 如果用户没有提供 Canvas `external_tools` 页面 URL，先用 `canvas.list_courses` 根据课程名找 `course_id`。若候选课程不唯一，让用户确认。
   - 拿到 `course_id` 后调用 `media.find_canvas_pages(course_id=<id>, query=<课程+日期+讲次关键词>)`，从候选 external_tools 页面里寻找最匹配的课程视频页。候选不唯一或标题不清楚时，让用户确认具体页面。
   - 如果用户已经提供 Canvas `external_tools` 页面 URL，直接调用 `media.resolve_canvas_page(url=<url>)`。
   - 若返回 `requires_browser_login`，说明 SJTUFlow 已打开/会打开自己的本地浏览器 profile，需要用户在该窗口登录 Canvas，然后重试同一请求。
   - 若解析到 stream，调用 `media.transcribe_canvas_page(url=<url>, title=<课程+日期>, language="zh")`。这是写入 transcript 的工具，必须经过确认策略。
   - 转写完成后再用 `transcripts.search` / `transcripts.read` 读取新 transcript，并回到步骤 3 回答。
5. 如果缺少 Canvas 页面 URL：
   - 如果 `media.find_canvas_pages` 找不到候选，说明无法自动定位课程视频页，请用户提供课程视频页面 URL。
   - 可调用 `media.canvas_access_hint(url=<url>)` 解释 Canvas token 与浏览器登录态的区别。
   - 不要声称后端可以绕过登录态、验证码、DRM 或课程权限。

## Output

聊天中以 Markdown 返回：

```
## 结论

<提到 / 未提到 / 需要在 SJTUFlow 浏览器窗口登录 Canvas>

## 依据

- <transcript title> [mm:ss]："<简短原文引用>"

## 说明

- 回答仅基于本地 transcript 或本次已授权转写结果。
- 如果托管浏览器 profile 尚未登录，说明需要用户在 SJTUFlow 打开的浏览器窗口完成 Canvas 登录。
```

## Safety

- 不绕过 Canvas 登录、验证码、DRM 或课程权限。
- 不保存视频本体；转写工具只允许临时音频缓存，transcript 默认入库。
- 不把带 `key=`、`token=`、`signature=` 等签名 URL 原样展示给用户；必要时只展示脱敏 URL。
- `media.transcribe_canvas_page` 属于写入资料库的动作，必须走确认策略。
- 引用 transcript 原文保持简短，避免大段复制。
