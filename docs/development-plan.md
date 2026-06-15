# 开发计划

## 当前基线

本仓库已经从 CLI 项目调整为本地 Web monorepo：

- 后端在 `apps/backend/src/sjtuflow`。
- 前端在 `apps/frontend`，目前仅保留空框架。
- 内置 skills 在 `apps/backend/src/sjtuflow/builtin_skills`，用户 skills 在 `~/.sjtuflow/skills`。
- `uv run sjtuflow web` 可启动本地 API。
- CLI、MCP、mock provider、Canvas、filesystem、skills、transcripts 工具仍可作为开发入口使用。

下一阶段目标不是再扩展 CLI，而是把浏览器端体验补完整，并补齐视频/transcript/skill 相关工具。

## 阶段 1：前端最小可用

目标：用户能在浏览器里完成首次配置、查看 briefing、发起对话。

开发内容：

- 选定前端栈，建议 Vite + React + TypeScript。
- 实现首次配置页，对接 `GET/PUT /api/config` 和 `GET /api/doctor`。
- 实现首页 briefing 区块，对接 `GET /api/briefing`。
- 实现会话工作区，对接 `POST /api/sessions` 和 `POST /api/sessions/{id}/messages`。
- 实现历史会话列表，对接 `GET /api/sessions`、`GET /api/sessions/{id}`、`DELETE /api/sessions/{id}`。
- 实现 Skills 和 Transcripts 侧栏，只显示标题与说明，点击后再读取全文。
- 实现创建 skill 按钮和编辑表单，写入用户本地 skill 目录。

交付标准：

- 新用户可通过浏览器完成配置并进入主界面。
- 配置缺失、token 缺失、模型 key 缺失有明确提示。
- briefing 按 urgent/upcoming/updates/warnings 分块展示。
- mock provider 下可以完成一轮对话演示。
- 关闭并重新打开前端后可以恢复历史会话。

## 阶段 2：确认与任务状态

目标：Web 模式下支持需要确认的写工具，不再依赖 CLI input。

开发内容：

- 后端新增 pending approval 机制。
- Agent loop 遇到写工具时返回 `approval_required` 状态。
- 前端展示工具名、风险等级、参数摘要、目标路径。
- 用户点击允许/拒绝后恢复工具执行。
- 长任务统一返回 job id，前端轮询或 SSE 展示状态。

交付标准：

- Canvas 文件下载、transcript 保存、skill 写入都必须经过前端确认。
- 审计日志记录确认结果。
- 非确认状态下写工具不会静默执行。

## 阶段 3：视频与 Transcript

目标：支持从本地视频/音频或已授权浏览器媒体流生成 transcript，默认保存到资料库，并让 agent 按需读取。

开发内容：

- 后端 media 工具：
  - `media.canvas_access_hint`
  - `media.probe`
  - `media.extract_audio`
  - `media.transcribe`
  - `media.transcribe_and_save`
  - `media.transcribe_stream`
  - `media.save_transcript`
- 支持上传本地媒体文件或填写本地路径。
- 支持前端从 SJTU Canvas `external_tools` 登录态页面转交流媒体 `stream_url`。
- Canvas token 通常不能直接获取 external_tools 媒体流；必须记录并在模型回复中说明浏览器登录态要求。
- 不绕过平台权限、验证码或 DRM。
- 视频本体不保存到本地；只允许转写期间的临时音频缓存，任务结束后清理。
- transcript 输出 Markdown 和 JSON 两种格式。
- demo 默认保存 transcript 到资料库，暂不提供“不保存”或资料管理入口。
- transcript metadata 只包含标题、说明、来源、时长、路径、更新时间。
- 全文通过 `transcripts.read` 按需加载。

交付标准：

- 小视频/音频可以生成带时间戳 transcript。
- 已授权媒体流可以转成 transcript，并且不保存视频本体。
- transcript 列表不会预加载全文。

## 阶段 4：更多 Skills

目标：沉淀课程学习常用流程。

开发内容：

- 新增内置 `builtin_skills/transcript-review/SKILL.md`。
- 新增内置 `builtin_skills/course-briefing/SKILL.md`。
- 新增内置 `builtin_skills/assignment-planning/SKILL.md`。
- 新增内置 `builtin_skills/final-review/SKILL.md`。
- 前端支持查看内置和用户 skill metadata、全文、来源类型。
- skill 创建/更新走确认流程。

交付标准：

- 每个 skill 有清晰 Purpose、Required Tools、Workflow、Output。
- Agent 系统提示只加载 skill 标题和说明。
- 用户可自然语言要求“用某个 skill 做任务”。
- 用户创建的 skill 写入 `~/.sjtuflow/skills/<name>/SKILL.md`。

## 阶段 5：可选邮箱

目标：按时间允许添加邮箱读取，不作为前端 MVP 阻塞项。

开发内容：

- IMAP 配置项。
- `mail.search`
- `mail.read`
- `mail.download_attachments`
- briefing 可选读取最近重要邮件。

交付标准：

- 邮件正文默认截断。
- 密码/token 不进入日志。
- 发送邮件暂不实现。

## 测试策略

- 后端：配置、workspace、skill metadata、transcript metadata、API routes 使用单元测试。
- Agent：mock provider 模拟工具调用和错误路径。
- Canvas：mock HTTP，不依赖真实账号。
- 前端：配置页、briefing、chat、metadata 列表做组件测试。
- E2E：mock provider + mock API 完成首次配置到对话。

## 质量要求

- 所有本地写入必须经过 workspace resolver。
- 日志脱敏 token、password、cookie、authorization。
- 长文本默认 metadata-first，按需读取全文。
- 启动 briefing 只读、轻量、失败不阻塞。
- 文档和 README 必须反映当前可运行能力，不承诺未实现功能。
