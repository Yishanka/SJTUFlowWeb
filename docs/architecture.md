# 系统架构

## 总览

SJTUFlow 采用 monorepo 和本地前后端模式：

```text
Browser UI
  |
  | HTTP on 127.0.0.1
  v
FastAPI backend
  |
  +-- LocalAppService
  |     +-- config / doctor / briefing / sessions
  |
  +-- AgentLoop ---- LLM provider
  |     |
  |     +-- ToolRegistry
  |           +-- Canvas tools
  |           +-- Filesystem tools
  |           +-- Skills tools
  |           +-- Transcript tools
  |           +-- Media tools
  |
  +-- Workspace / audit / local data
```

CLI 现在是开发入口和兼容入口。主产品入口是 `sjtuflow web`，默认绑定 `127.0.0.1:8765`。

## Monorepo

```text
apps/backend/src/sjtuflow/
  agent/        Agent loop、briefing、确认策略
  cli/          开发 CLI 与 web 启动命令
  connectors/   Canvas 等平台底层客户端
  llm/          mock 与 OpenAI-compatible provider
  services/     Web/CLI 可复用的本地服务层
  skills/       SKILL.md 加载与 metadata
  storage/      config、workspace、audit
  tools/        Canvas/filesystem/skills/transcripts/media 工具
  web/          FastAPI app 与 uvicorn 入口
  builtin_skills/
    weekly-review/SKILL.md

apps/frontend/
  public/
  src/

~/.sjtuflow/skills/
  user-created-skill/SKILL.md
```

根目录 `pyproject.toml` 仍是 uv 项目入口，Hatch wheel 配置指向 `apps/backend/src/sjtuflow`。

## 后端 API

当前 API 前缀为 `/api`：

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 本地服务健康检查 |
| GET | `/api/config` | 读取脱敏配置 |
| PUT | `/api/config` | 按 dotted key 更新配置 |
| GET | `/api/doctor` | 检查配置、目录、模型、Canvas、工具 |
| GET | `/api/briefing` | 生成一次 startup briefing |
| GET | `/api/tools` | 列出工具 schema |
| GET | `/api/skills` | 列出 skill 标题和说明 |
| GET | `/api/skills/{name}` | 按需读取完整 SKILL.md |
| POST | `/api/skills` | 创建用户 skill |
| PUT | `/api/skills/{name}` | 更新用户 skill |
| DELETE | `/api/skills/{name}` | 删除用户 skill |
| GET | `/api/transcripts` | 列出 transcript 标题和说明 |
| GET | `/api/transcripts/{id}` | 按需读取完整 transcript |
| POST | `/api/media/canvas-access-hint` | 说明 Canvas external_tools 媒体页的登录态要求 |
| POST | `/api/media/ensure-canvas-login` | 打开可见托管浏览器准备/刷新 Canvas 登录态 |
| POST | `/api/media/find-canvas-pages` | 调试兜底：用托管浏览器在课程页/模块页中收集 external_tools 候选 |
| POST | `/api/media/resolve-canvas-page` | 兼容/调试：复用 SJTUFlow 托管浏览器 profile 进入 Canvas 页面并解析脱敏流候选 |
| POST | `/api/media/plan-canvas-request` | 从自然语言或 URL 规划 Canvas 录播转写，返回脱敏课程/视频/流选择依据 |
| POST | `/api/media/resolve-stream` | 调试兜底：从 HTML 片段/本地 HTML/媒体 URL 解析脱敏流候选 |
| POST | `/api/media/probe` | 读取本地媒体元数据 |
| POST | `/api/media/extract-audio` | 从本地媒体提取音频 |
| POST | `/api/media/transcribe` | 本地媒体转写，返回内存结果 |
| POST | `/api/media/transcribe-and-save` | 本地媒体转写并默认保存 transcript |
| POST | `/api/media/transcribe-stream` | 已授权媒体流转写并默认保存 transcript |
| POST | `/api/media/transcribe-canvas-page` | 用托管浏览器解析 Canvas 页面并转写保存 transcript |
| POST | `/api/media/transcribe-canvas-request` | 从自然语言或 URL 自动定位课程录播并转写保存 |
| POST | `/api/media/transcribe-source` | 调试兜底：解析来源并转写保存 transcript |
| POST | `/api/media/save-transcript` | 保存 transcript JSON/Markdown |
| GET | `/api/jobs` | 列出后台任务 |
| GET | `/api/jobs/{id}` | 查询后台任务状态 |
| POST | `/api/sessions` | 创建 agent 会话 |
| GET | `/api/sessions` | 列出历史会话摘要 |
| GET | `/api/sessions/{id}` | 读取历史会话完整消息 |
| DELETE | `/api/sessions/{id}` | 删除历史会话 |
| POST | `/api/sessions/{id}/messages` | 向会话发送消息 |
| POST | `/api/sessions/{id}/clear` | 清空会话上下文 |

写配置示例：

```json
{
  "updates": {
    "model.provider": "openai-compatible",
    "model.endpoint": "https://api.openai.com/v1",
    "canvas.access_token_env": "SJTU_CANVAS_TOKEN"
  }
}
```

## Agent Loop

后端每个 session 持有一个 `AgentLoop`：

1. 创建会话时加载配置、workspace、skills metadata 和工具 schema。
2. 可选运行 startup briefing。
3. 用户消息进入模型。
4. 模型选择直接回答或调用工具。
5. 工具按风险等级执行；Web 版本后续需要接入确认队列。
6. 工具结果回写模型消息。
7. 会话消息写入 `~/.sjtuflow/sessions/<session-id>.json`。
8. 审计日志写入 `~/.sjtuflow/audit/YYYY-MM-DD.jsonl`。

当前 Web session 使用 `interactive=False`，因此需要确认的写工具默认会被拒绝，除非配置显式允许 `permissions.allow_non_interactive_writes`。前端确认队列是后续重点。

## Skill 与 Transcript 加载原则

Skill 和 transcript 都不应在启动时把全文塞进系统提示。

Skill：

- 后端内置 skills 放在 `apps/backend/src/sjtuflow/builtin_skills/`，随应用发布，默认只读。
- 用户通过前端创建或编辑的 skills 放在 `~/.sjtuflow/skills/`。
- `skills.list` 和 Web `/api/skills` 只返回 `name/title/description/path`。
- 模型需要完整操作手册时调用 `skills.read`。
- 创建或修改用户 skill 属于本地写入，需要确认和审计；内置 skill 不由前端直接覆盖。

Transcript：

- `transcripts.list` 和 Web `/api/transcripts` 只返回 `id/title/description/path/source/duration`。
- 模型需要全文时调用 `transcripts.read`。
- 转写结果保存为 JSON 和 Markdown，默认在 `~/SJTUFlowData/transcripts/`。
- 媒体流转写不保存视频本体，只保留 transcript；Canvas 录播自然语言主流程通过 `media.transcribe_canvas_request` 接收课程/日期/主题描述，使用 Canvas token 定位课程，使用 SJTUFlow 保存的 Canvas 登录 state 启动 SJTU 课程视频 LTI `external_tools/9487`，调用 `courses.sjtu.edu.cn` VOD API 获取回放列表和流地址。后端不读取用户日常浏览器 cookie；对外展示和 LLM 选择时必须脱敏签名 URL，且不能暴露 Cookie/request headers。
- 单个课程存在多个回放/流候选时，后端先让 LLM 基于脱敏候选元数据选择；未配置真实模型时使用确定性启发式选择。显式 Canvas URL 调试路径仍可用 browser network 捕获 `.m3u8` / `.mp4`。

## 数据目录

默认状态目录：`~/.sjtuflow/`

默认资料目录：`~/SJTUFlowData/`

建议结构：

```text
SJTUFlowData/
  canvas/
  transcripts/
  extracted/
  reports/
```

历史会话保存在 `~/.sjtuflow/sessions/`。它用于恢复聊天上下文；审计日志仍保存在 `~/.sjtuflow/audit/`，用于排错和追踪工具调用。

不需要中心数据库。后续如果需要本地检索，可以在资料目录中增加轻量索引文件；这不作为当前架构的启动前提。
