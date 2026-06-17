# Lecture Capture

## Purpose

转写课堂视频/媒体流前的预检和入库决策：检查文件、估算来源、查重已有 transcript，最终给出"建议转写 / 已有，跳过 / 不建议直接转写"的判断。本 skill 不触发任何转写或写盘，转写动作由用户在 UI 上单独确认。

## Required Tools

- `media.probe`
- `media.canvas_access_hint`
- `media.ensure_canvas_login`
- `media.check_canvas_login`
- `media.plan_canvas_request`
- `media.find_canvas_pages`
- `media.resolve_canvas_page`
- `transcripts.list`

## Workflow

1. 确认来源：
   - **本地文件**：用户给出绝对路径或在已授权工作区内的相对路径。
   - **Canvas 媒体流**：用户可给课程名、日期、主题或 Canvas 录播页面 URL。优先调 `media.plan_canvas_request(request=<自然语言或URL>)`；自然语言路径由后端用 Canvas 课程列表选择课程，再通过 SJTU 课程视频 LTI 工具 `external_tools/9487` 获取 VOD 回放列表和流候选。只展示脱敏 URL 和选择依据。
   - **调试场景**：已有明确 Canvas 页面 URL 时可调 `media.resolve_canvas_page(url=<url>)` 观察候选 stream，但不要展示带签名参数的完整 URL。
   - 如果返回 `requires_browser_login`，告诉用户先在媒体页点击“准备 Canvas 登录态”，在 SJTUFlow 托管浏览器中登录一次 Canvas 后重试；本 skill 自身不绕过认证。
2. 本地文件预检：调 `media.probe(path=<path>)`，从返回中提取 duration / streams / codec / 估算大小。`probe` 报错（路径不存在 / 不在工作区 / 编码异常）时立即停止，不重试。
3. 查重：调 `transcripts.list`，按文件名 stem 或用户给的标题在 transcript metadata 中模糊匹配；若已有同名 transcript，列出 `id` + `updated_at`，建议跳过转写、改用 `transcript-review`。
4. 输出判断：
   - **建议转写**：未查到同名 transcript，probe 正常，时长在合理范围内。
   - **已有 transcript**：列出已有条目，建议跳到 `transcript-review`。
   - **不建议直接转写**：时长 > 90 分钟（建议先分段）/ probe 报错 / 托管浏览器 profile 尚未登录或页面未加载出视频流。
5. 末尾给出"下一步建议"：本地文件让用户确认 `media.transcribe_and_save`；Canvas 请求让用户确认 `media.transcribe_canvas_request`。本 skill 不直接触发。

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
- Canvas 录播优先走 `media.plan_canvas_request`，使用 Canvas token 定位课程，使用 SJTUFlow 保存的 Canvas 登录 state 启动 SJTU 课程视频 LTI/VOD 接口获取回放与流地址；显式 external_tools URL 仅作为兼容/调试路径。`media.canvas_access_hint` 只用于解释限制；不要要求普通用户复制 HTML。
- 本地文件路径必须在已授权工作区内；`media.probe` 报权限或路径错时立即停止并提示用户，不静默尝试其他路径。
- 时长 > 90 分钟的文件默认建议分段；具体怎么切由用户决定，本 skill 不替用户切。
- 转写耗时估算仅为粗略参考（按时长 0.3–0.5× 经验），实际取决于本地算力。
