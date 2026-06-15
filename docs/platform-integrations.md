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

下一阶段重点实现：

```text
media.probe
media.extract_audio
media.transcribe
media.save_transcript
```

建议流水线：

```text
local video/audio
  -> ffmpeg probe
  -> extract audio
  -> ASR transcription
  -> transcript JSON + Markdown
  -> optional save
  -> optional indexing
```

要求：

- 只处理用户提供或已授权访问的媒体文件。
- 大文件走 job 状态。
- transcript 可只在会话临时使用，也可保存。

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
