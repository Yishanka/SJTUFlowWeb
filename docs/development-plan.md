# 开发计划

## 当前基线

本仓库已经从 CLI 项目调整为本地 Web monorepo：

- 后端在 `apps/backend/src/sjtuflow`。
- 前端在 `apps/frontend`，当前是零依赖静态单页应用。
- 内置 skills 在 `apps/backend/src/sjtuflow/builtin_skills`，用户 skills 在 `~/.sjtuflow/skills`。
- `uv run sjtuflow web` 可启动本地 API。
- CLI、MCP、mock provider、Canvas、filesystem、skills、transcripts 工具仍可作为开发入口使用。

下一阶段目标不是再扩展 CLI，而是完善浏览器端确认队列、端到端媒体体验和更多课程工具。

## 阶段 1：前端最小可用（已完成 MVP）

目标：用户能在浏览器里完成首次配置、查看 briefing、发起对话。

已实现内容：

- 零依赖静态 SPA，后端可直接托管 `apps/frontend` 或 `apps/frontend/dist`。
- 本地设置页，对接 `GET/PUT /api/config` 和 `GET /api/doctor`。
- 控制面板展示 briefing。
- 学习对话工作区和历史会话列表。
- Skills 和 Transcripts metadata-first 列表，点击后读取全文。
- 用户 skill 创建、复制、编辑、删除入口。
- 媒体页支持本地媒体转写、Canvas/SJTU 课程视频 LTI/VOD 转写，以及显式 Canvas external_tools URL 调试路径。

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
  - `media.ensure_canvas_login`
  - `media.check_canvas_login`
  - `media.find_canvas_pages`
  - `media.resolve_canvas_page`
  - `media.plan_canvas_request`
  - `media.resolve_stream`
  - `media.probe`
  - `media.extract_audio`
  - `media.transcribe`
  - `media.transcribe_and_save`
  - `media.transcribe_stream`
  - `media.transcribe_canvas_page`
  - `media.transcribe_canvas_request`
  - `media.transcribe_source`
  - `media.save_transcript`
- 支持上传本地媒体文件或填写本地路径。
- 支持使用 SJTUFlow 托管的本地浏览器 profile 登录 Canvas，并显式保存 `canvas-storage-state.json`；后续自然语言转写用该 state 启动 SJTU 课程视频 LTI `external_tools/9487` 和 `courses.sjtu.edu.cn` VOD API。
- 支持在用户只给课程名/日期/自然语言问题时，由 `media.plan_canvas_request` 统一使用 Canvas 课程列表、SJTU VOD 回放列表和流候选进行 LLM/启发式选择；前端不再拆成“找 URL”和“转写 URL”两个入口。
- 托管浏览器登录态通过 `media.ensure_canvas_login` 单独准备，任务完成后关闭窗口；后续转写先用 `media.check_canvas_login`/内部探测确认 state，有登录态才调用 VOD API 和 ffmpeg。
- 单页多个视频流时不盲取第一个；模型只接收脱敏候选元数据和索引，签名 URL、Cookie、request headers 保留在后端内部。
- Canvas token 通常不能直接获取课程视频媒体流；必须记录并在模型回复中说明 SJTUFlow Canvas 登录 state 要求。
- 不读取用户日常浏览器 profile 的 cookie；直接媒体 URL / 本地 HTML 解析仅作为 debug fallback。
- 不绕过平台权限、验证码或 DRM。
- 视频本体不保存到本地；只允许转写期间的临时音频缓存，任务结束后清理。
- transcript 输出 Markdown 和 JSON 两种格式。
- demo 默认保存 transcript 到资料库，暂不提供“不保存”或资料管理入口。
- transcript metadata 只包含标题、说明、来源、时长、路径、更新时间。
- 全文通过 `transcripts.read` 按需加载。

交付标准：

- 小视频/音频可以生成带时间戳 transcript。
- Canvas 托管浏览器会话解析到的已授权媒体流可以转成 transcript，并且不保存视频本体。
- 自然语言输入如“今天算法课老师是否提到签到？”可以在 Media 页触发课程匹配、SJTU VOD 回放查找、视频流选择和转写任务。
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
