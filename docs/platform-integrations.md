# 平台与工具集成

## 原则

- 优先使用官方 API 和用户主动授权。
- 不绕过认证、验证码、DRM 或权限限制。
- 默认只读，写本地文件、下载附件、保存 transcript、修改 skill 都需要确认。
- 平台原始响应先映射成内部模型，再暴露给 agent。
- 长文本采用 metadata-first：先列标题和说明，需要时再读取全文。

## Startup Briefing

briefing 是本地 Web 首页的重要区块，不是全量同步。

当前来源：

- `canvas.list_upcoming_assignments`
- `canvas.list_recent_announcements`

返回分组：

- `urgent`
- `upcoming`
- `updates`
- `warnings`

后续可加入：

- 重要邮件摘要。
- 最近转写失败或同步失败的任务。
- 近期本地 transcript 更新。

## Canvas

当前工具：

```text
canvas.connection_status
canvas.list_courses
canvas.list_assignments
canvas.list_upcoming_assignments
canvas.list_recent_announcements
canvas.list_files
canvas.get_file
canvas.download_file
```

约束：

- 读取课程、作业、公告、文件元数据为 `read`。
- 下载文件为 `write`，Web 前端必须展示确认。
- token 推荐使用环境变量 `SJTU_CANVAS_TOKEN`。

## Transcript

当前工具：

```text
transcripts.list
transcripts.read
transcripts.save_text
```

存储目录：

```text
~/SJTUFlowData/transcripts/
```

加载规则：

- 列表只返回标题、说明、来源、时长、路径、更新时间。
- 全文按需读取。
- 保存 transcript 属于本地写入。

## Skills

内置 skills 随后端发布，位于 `apps/backend/src/sjtuflow/builtin_skills/`。用户在前端创建、复制或编辑的 skills 保存到 `~/.sjtuflow/skills/`。

加载规则：

- 列表只返回标题、说明、路径和来源类型。
- 全文按需读取。
- 前端不直接覆盖内置 skill；如需修改，应复制为用户 skill。
- 创建、编辑、删除用户 skill 都属于本地写入。

## 视频与音频

当前工具：

```text
media.canvas_access_hint
media.ensure_canvas_login
media.check_canvas_login
media.find_canvas_pages
media.resolve_canvas_page
media.plan_canvas_request
media.resolve_stream
media.probe
media.extract_audio
media.transcribe
media.transcribe_and_save
media.transcribe_stream
media.transcribe_canvas_page
media.transcribe_canvas_request
media.transcribe_source
media.save_transcript
```

本地文件流水线：

```text
local video/audio
  -> ffmpeg probe
  -> extract audio
  -> ASR transcription
  -> transcript JSON + Markdown
  -> save to transcript library by default
```

SJTU Canvas external_tools 流媒体流水线：

```text
natural language request
  -> Canvas token lists courses
  -> LLM/heuristic selection chooses course
  -> backend checks SJTUFlow saved Canvas login state
  -> backend launches SJTU lecture-video LTI external_tools/9487
  -> backend calls courses.sjtu.edu.cn VOD API
  -> LLM/heuristic selection chooses recording(s) and stream(s)
  -> backend ffmpeg streams temporary audio
  -> ASR transcription
  -> transcript JSON + Markdown
  -> save to transcript library by default
```

要求：

- 只处理用户提供或已授权访问的媒体文件。
- Canvas API token 通常不能直接获取 SJTU 课程视频媒体流；自然语言主流程使用 Canvas token 选课，使用已保存的 SJTUFlow Canvas 登录 state 启动 `external_tools/9487` LTI，再调用 `courses.sjtu.edu.cn` VOD API 获取回放和流。
- SJTUFlow 托管的本地浏览器 profile 只用于准备登录态并导出 `canvas-storage-state.json`；登录准备是可见窗口，后续 VOD API 和 ffmpeg 流读取使用该 state。
- 后端不读取用户日常浏览器 profile 的 cookie，也不要求用户复制系统浏览器登录态。
- 用户主流程是 `media.plan_canvas_request` / `media.transcribe_canvas_request`：输入自然语言课程描述、日期/主题问题时，后端用 Canvas token 定位课程，再通过 SJTU LTI/VOD 获取回放列表并选择视频；显式 Canvas external_tools URL 仅作为兼容/调试路径。
- `media.find_canvas_pages` 是开发/调试兜底工具；`media.resolve_canvas_page` 解析单页媒体候选。前端不再拆成“找 URL”和“转写 URL”两个入口。
- LLM 只接收脱敏候选元数据和索引；签名 stream URL、Cookie、request headers 不暴露给模型或前端。
- Agent 回复相关问题时必须说明登录态要求，不能暗示可以绕过认证、验证码、DRM 或课程权限。
- `media.resolve_canvas_page` 可以解析托管浏览器页面中的 `<video src>`、`.mp4`、`.m3u8`、network resource；返回结果必须脱敏 `key`、`token` 等签名查询参数，且不能暴露 Cookie/request headers。
- `media.resolve_stream` 仅作为调试兜底，用于直接媒体 URL 或本地 HTML 片段/文件；不作为用户主流程。
- 视频本体不保存到本地；流媒体处理只允许临时音频缓存，任务结束后清理。
- 大文件走 job 状态。
- transcript 默认保存到 `~/SJTUFlowData/transcripts/`，demo 暂不提供“不保存”或资料管理入口。

## 邮箱

邮箱作为可选项。建议第一版只读 IMAP：

```text
mail.search
mail.read
mail.download_attachments
```

约束：

- 默认读取最近邮件。
- 正文截断。
- 附件下载需要确认。
- 不实现发送邮件。

## 文件与资料库

默认资料库：

```text
~/SJTUFlowData/
  canvas/
  transcripts/
  extracted/
  reports/
```

所有读写通过 `Workspace` 校验路径。允许写入的根：

- 当前仓库目录。
- `data_dir`。
- `state_dir`。

## 作业相关能力

允许：

- 解释题目。
- 整理资料。
- 生成解题计划。
- 生成草稿。
- 检查提交清单。

禁止默认自动执行：

- 未确认提交作业。
- 未确认发送邮件。
- 未确认删除或覆盖远端内容。
- 绕过平台限制。
