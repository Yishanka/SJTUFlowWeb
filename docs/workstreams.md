# 分工与接口

本文档用于后续拆任务。优先级从前端可用性、视频转写、transcript 保存、skill 扩展开始。

## A. 前端完整体验

负责人目标：把 `apps/frontend` 从空框架实现为本地 Web 应用。

开发位置：`apps/frontend/`。

页面：

- First-run config：模型配置、Canvas token、资料目录、权限策略。
- Dashboard：briefing 分块展示。
- Chat workspace：历史会话列表、消息流、工具状态。
- Skills：metadata 列表、全文查看、创建/编辑入口，区分内置 skill 与用户 skill。
- Transcripts：metadata 列表、全文查看、上传/转写入口。
- Settings：配置、doctor、数据目录、审计日志入口。

接口：

- `GET /api/config`
- `PUT /api/config`
- `GET /api/doctor`
- `GET /api/briefing`
- `POST /api/sessions`
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/messages`
- `GET /api/skills`
- `GET /api/skills/{name}`
- `POST /api/skills`
- `PUT /api/skills/{name}`
- `DELETE /api/skills/{name}`
- `GET /api/transcripts`
- `GET /api/transcripts/{transcript_id}`

要求：

- 不做登录页。
- 首屏就是本地工作台或首次配置。
- 用户能从历史会话列表恢复上一次聊天。
- briefing 每类信息独立区块展示。
- skill/transcript 列表只展示标题和说明，正文点击后读取。
- 内置 skill 只能复制/查看；用户 skill 可以创建、编辑、删除。

## B. 视频提取与转 Transcript Tools

负责人目标：实现本地媒体处理工具，让 agent 能把视频/音频转换成 transcript。

开发位置：`apps/backend/src/sjtuflow/tools/media.py`、`apps/backend/src/sjtuflow/web/app.py`、`apps/backend/src/sjtuflow/services/`。

后端工具建议：

```text
media.probe(path: str)
media.extract_audio(path: str, out_dir: str | None = None)
media.transcribe(path: str, provider: str = "local-whisper", language: str | None = None)
media.save_transcript(title: str, content: str, source: str = "", description: str = "")
```

接口建议：

```text
POST /api/media/probe
POST /api/media/extract-audio
POST /api/media/transcribe
POST /api/media/save-transcript
GET  /api/jobs/{job_id}
```

实现要求：

- 优先处理用户主动提供的本地文件。
- 不绕过视频平台 DRM、验证码或权限限制。
- 大文件/转写走 job 状态，不阻塞 HTTP 请求。
- transcript JSON 保存 segments、start、end、text。
- transcript Markdown 供阅读和后续检索。
- 用户可选择“只用于本轮会话”或“保存到资料库”。

## C. Transcript Library

负责人目标：完善 transcript 的 metadata-first 加载与保存。

开发位置：`apps/backend/src/sjtuflow/tools/transcripts.py`、`apps/backend/src/sjtuflow/services/local_app.py`、`apps/backend/src/sjtuflow/web/app.py`。

已有能力：

- `transcripts.list`
- `transcripts.read`
- `transcripts.save_text`
- `GET /api/transcripts`
- `GET /api/transcripts/{id}`

待补：

- 删除/重命名 transcript 的确认流程。
- transcript 搜索。
- transcript 摘要缓存。
- 与媒体转写 job 对接。

数据格式：

```json
{
  "id": "stable-id",
  "title": "Lecture 03",
  "description": "短说明或摘要",
  "source": "/path/to/video.mp4",
  "duration_seconds": 3560,
  "path": "~/SJTUFlowData/transcripts/lecture-03.json",
  "updated_at": "2026-06-15T12:00:00+00:00"
}
```

要求：

- 列表接口不得返回全文。
- Agent 系统提示不得预加载全文。
- 全文读取必须走 `transcripts.read`。

## D. Skill 扩展

负责人目标：新增更多 `SKILL.md`，让 agent 有可复用学习工作流。

开发位置：`apps/backend/src/sjtuflow/builtin_skills/`、`apps/backend/src/sjtuflow/skills/loader.py`、`apps/backend/src/sjtuflow/tools/skills.py`。

目录策略：

- 内置 skills：`apps/backend/src/sjtuflow/builtin_skills/<name>/SKILL.md`。
- 用户 skills：`~/.sjtuflow/skills/<name>/SKILL.md`。
- 前端创建按钮只写用户 skills，不直接覆盖内置 skills。

建议新增：

- `transcript-review`：阅读课堂 transcript，抽取主题、考点、作业提醒。
- `course-briefing`：按课程聚合 Canvas 公告、作业和文件更新。
- `assignment-planning`：读取作业要求，生成计划、资料清单和提交检查表。
- `final-review`：期末复习整理，按课程生成复习计划。

格式要求：

```markdown
# Skill Name

## Purpose
一句话说明用途。

## Required Tools
- tool.name

## Workflow
1. ...

## Output
说明输出格式、路径或 UI 展示方式。

## Safety
说明哪些动作需要确认，哪些动作禁止。
```

加载要求：

- `skills.list` 只返回标题和说明。
- `skills.read` 按需读取全文。
- 新增/修改/删除用户 skill 属于本地写入，必须确认。

## E. 邮箱可选工具

负责人目标：如时间允许，加入 IMAP 读取能力。

开发位置：`apps/backend/src/sjtuflow/connectors/mail/`、`apps/backend/src/sjtuflow/tools/mail.py`、`apps/backend/src/sjtuflow/storage/config.py`。

工具建议：

```text
mail.search(query: str, since_days: int = 14, mailbox: str = "INBOX")
mail.read(message_id: str)
mail.download_attachments(message_id: str, out_dir: str | None = None)
```

要求：

- 默认只读。
- 正文截断，附件下载需确认。
- 邮箱密码使用环境变量或系统凭据，不写日志。
- 暂不实现发送邮件。

## F. 后端基础与质量

负责人目标：把后端变成稳定的本地 API。

开发位置：`apps/backend/src/sjtuflow/services/`、`apps/backend/src/sjtuflow/web/`、`apps/backend/src/sjtuflow/agent/`、`tests/`。

待补：

- API tests。
- pending approval 队列。
- job manager。
- 历史会话前端筛选、重命名和批量删除。
- 前端开发 CORS 配置。
- 更清晰的错误码。
- 审计日志查看接口。

验收：

- `uv run sjtuflow doctor` 正常。
- `uv run sjtuflow web --no-open` 可启动。
- `GET /api/health` 返回 ok。
- mock provider 下能完成 session 对话。
