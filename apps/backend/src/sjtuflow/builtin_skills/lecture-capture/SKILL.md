# Lecture Capture

## Purpose

转写课堂视频/媒体流前的预检和入库决策：检查文件、估算来源、查重已有 transcript，最终给出"建议转写 / 已有，跳过 / 不建议直接转写"的判断。本 skill 不触发任何转写或写盘，转写动作由用户在 UI 上单独确认。

## Required Tools

- `media.probe`
- `media.canvas_access_hint`
- `media.resolve_stream`
- `transcripts.list`

## Workflow

1. 确认来源：
   - **本地文件**：用户给出绝对路径或在已授权工作区内的相对路径。
   - **Canvas 媒体流**：用户给出 Canvas 课程页面 URL，调 `media.canvas_access_hint(url=<url>)`，把"为什么需要浏览器登录态"原样告知用户；本 skill 自身不绕过认证。
   - **已登录页面片段 / 本地 HTML 文件 / 直接媒体 URL**：调 `media.resolve_stream(source=<source>)` 解析候选 stream URL，但不要展示带签名参数的完整 URL。
2. 本地文件预检：调 `media.probe(path=<path>)`，从返回中提取 duration / streams / codec / 估算大小。`probe` 报错（路径不存在 / 不在工作区 / 编码异常）时立即停止，不重试。
3. 查重：调 `transcripts.list`，按文件名 stem 或用户给的标题在 transcript metadata 中模糊匹配；若已有同名 transcript，列出 `id` + `updated_at`，建议跳过转写、改用 `transcript-review`。
4. 输出判断：
   - **建议转写**：未查到同名 transcript，probe 正常，时长在合理范围内。
   - **已有 transcript**：列出已有条目，建议跳到 `transcript-review`。
   - **不建议直接转写**：时长 > 90 分钟（建议先分段）/ probe 报错 / 只有 Canvas external_tools URL 且缺少登录态 HTML 或授权媒体 URL。
5. 末尾给出"下一步建议"：本地文件让用户确认 `media.transcribe_and_save`；HTML/stream 来源让用户确认 `media.transcribe_source`。本 skill 不直接触发。

## Output

聊天中以 Markdown 块返回：

```
# <文件名 / 来源标题>

## 预检
- 来源：<本地路径 / Canvas URL>
- 时长：<HH:MM:SS> 或 "未知（Canvas 媒体流，未本地探测）"
- 编码 / 流：<codec>
- 估算转写耗时：<分钟>（按时长 0.3–0.5× 经验估）

## 已有 transcript（<count>）
- <title>（id=<id>，updated_at=<date>）

## 判断
- <建议转写 / 已有，跳过 / 不建议直接转写>
- 理由：<一句话>

## 下一步建议
- 在 UI 上确认 `media.transcribe_and_save(path=<path>, language=<hint>)`，或先跳到 `transcript-review`
```

不写盘。任何转写、音频提取、保存操作都属于写工具，由用户在 UI 上单独确认。

## Safety

- 本 skill 只调用 read 类工具；`media.extract_audio`、`media.transcribe`、`media.transcribe_and_save` 等 write/重操作禁止在本 skill 中调用。
- Canvas external_tools 页面统一走 `media.canvas_access_hint`；只有用户提供已登录页面片段、本地 HTML 文件或授权媒体 URL 时，才调用 `media.resolve_stream`。
- 本地文件路径必须在已授权工作区内；`media.probe` 报权限或路径错时立即停止并提示用户，不静默尝试其他路径。
- 时长 > 90 分钟的文件默认建议分段；具体怎么切由用户决定，本 skill 不替用户切。
- 转写耗时估算仅为粗略参考（按时长 0.3–0.5× 经验），实际取决于本地算力。
