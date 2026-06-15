# SJTUFlow

SJTUFlow 是一个纯本地的 SJTU 学习助手。用户启动应用后，在浏览器里完成首次配置、查看 briefing、对话、管理 skills 和 transcripts；不需要注册 SJTUFlow 账号，也不会把资料上传到 SJTUFlow 云端。

## 启动应用

```bash
uv run sjtuflow web
```

默认会启动本机后端并打开：

```text
http://127.0.0.1:8765
```

当前前端仍是空框架，所以页面会先显示 API running 信息。后续前端完成后，用户应直接在浏览器内填写模型、Canvas token、资料目录和权限设置。

## 使用方式

前端完成后的默认流程：

1. 启动应用。
2. 在浏览器首次配置模型和 Canvas。
3. 进入首页查看 startup briefing。
4. 在工作区对话，例如“这周有哪些作业？”。
5. 在侧栏管理 Skills 和 Transcripts。

Skills 分两类：

- 后端内置 skills：随应用发布，放在后端包内。
- 用户创建 skills：通过前端创建，保存在 `~/.sjtuflow/skills/`。

Transcripts 默认保存在 `~/SJTUFlowData/transcripts/`。

## 数据位置

```text
~/.sjtuflow/
  config.toml
  skills/
  audit/

~/SJTUFlowData/
  canvas/
  transcripts/
  extracted/
  reports/
```

## 当前状态

已完成：

- monorepo：`apps/backend` + `apps/frontend`。
- 本地 FastAPI 后端 API。
- Canvas 读取与文件下载工具。
- Skill metadata-first 加载。
- Transcript metadata-first 加载与文本保存。
- CLI 作为开发和备用入口。

待完成：

- 完整前端界面。
- Web 端写操作确认队列。
- 视频/音频转 transcript 工具。
- 更多内置 `SKILL.md`。
- 可选邮箱工具。

## 开发命令

```bash
uv run sjtuflow web --no-open
uv run sjtuflow doctor
uv run sjtuflow skills
uv run python apps/backend/main.py doctor
```

更多设计见 [docs/README.md](./docs/README.md)。
