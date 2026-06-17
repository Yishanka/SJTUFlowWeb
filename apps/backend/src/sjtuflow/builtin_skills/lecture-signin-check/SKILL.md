# Lecture Sign-in Check

## Purpose

回答"某次课老师是否提到签到 / 点名 / attendance"这类问题。优先基于本地 transcript；没有 transcript 时，可在用户已提供本地 HTML 片段、HTML 文件路径或已授权媒体 URL 的情况下解析媒体流、转写入库，再回答。

## Required Tools

- `transcripts.list`
- `transcripts.search`
- `transcripts.read`
- `media.canvas_access_hint`
- `media.resolve_stream`
- `media.transcribe_source`

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
   - 若用户提供了本地 HTML 片段 / HTML 文件路径 / 直接媒体 URL，先调用 `media.resolve_stream(source=<source>)`。
   - 若解析到 stream URL，调用 `media.transcribe_source(source=<source>, title=<课程+日期>, language="zh")`。这是写入 transcript 的工具，必须经过确认策略。
   - 转写完成后再用 `transcripts.search` / `transcripts.read` 读取新 transcript，并回到步骤 3 回答。
5. 如果用户只提供 Canvas `external_tools` 页面 URL：
   - 调用 `media.canvas_access_hint(url=<url>)`，明确说明 Canvas token 通常不能直接抓媒体流。
   - 告诉用户需要浏览器保持登录态，并提供以下任一内容：已登录页面中 `<video src="...">` 元素、本地 HTML 片段文件路径、或已授权媒体 URL。
   - 不要声称后端可以绕过登录态、验证码、DRM 或课程权限。

## Output

聊天中以 Markdown 返回：

```
## 结论

<提到 / 未提到 / 需要登录态媒体来源>

## 依据

- <transcript title> [mm:ss]："<简短原文引用>"

## 说明

- 回答仅基于本地 transcript 或本次已授权转写结果。
- 如果缺少已授权媒体来源，说明需要用户从已登录浏览器页面提供 video src / HTML 片段。
```

## Safety

- 不绕过 Canvas 登录、验证码、DRM 或课程权限。
- 不保存视频本体；转写工具只允许临时音频缓存，transcript 默认入库。
- 不把带 `key=`、`token=`、`signature=` 等签名 URL 原样展示给用户；必要时只展示脱敏 URL。
- `media.transcribe_source` 属于写入资料库的动作，必须走确认策略。
- 引用 transcript 原文保持简短，避免大段复制。
