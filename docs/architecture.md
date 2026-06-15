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
  tools/        Canvas/filesystem/skills/transcripts 工具
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
| POST | `/api/sessions` | 创建 agent 会话 |
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
7. 审计日志写入 `~/.sjtuflow/audit/YYYY-MM-DD.jsonl`。

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
- 转写结果可保存为 Markdown 或 JSON，默认在 `~/SJTUFlowData/transcripts/`。

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

不需要中心数据库。后续如果需要本地检索，可以在资料目录中增加轻量索引文件；这不作为当前架构的启动前提。
