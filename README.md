# SJTUFlow

SJTUFlow 是一个纯本地的 SJTU 学习助手。用户启动应用后，在浏览器里完成首次配置、查看 briefing、对话、管理 skills 和 transcripts；不需要注册 SJTUFlow 账号，也不会把资料上传到 SJTUFlow 云端。

## 启动应用

如果要使用 Canvas 课程视频自动抓取/转写，首次需要安装本地浏览器运行时：

```bash
uv run playwright install chromium
```

```bash
uv run sjtuflow web
```

默认会启动本机后端并打开：

```text
http://127.0.0.1:8765
```

页面会打开本地 Web 工作台，用户可以在浏览器内填写模型、Canvas token、资料目录和权限设置，然后完成 briefing、对话、skill、transcript 和媒体转写工作。

## 使用方式

默认流程：

1. 启动应用。
2. 在浏览器首次配置模型和 Canvas。
3. 进入首页查看 startup briefing。
4. 在工作区对话，例如“这周有哪些作业？”。
5. 在侧栏管理 Skills 和 Transcripts。
6. 转写本地视频或 Canvas 课程视频页面，并默认保存 transcript 到资料库。

Canvas 课程视频的推荐流程：

1. 在对话中提供 Canvas 课程视频页面 URL，例如 `https://oc.sjtu.edu.cn/courses/.../external_tools/...`。
2. SJTUFlow 会打开自己管理的本地浏览器 profile。
3. 第一次使用时，你需要在这个 SJTUFlow 浏览器窗口里登录 Canvas；之后后端会复用该本地 profile。
4. 后端从页面/网络请求中解析视频流，流式转写并保存 transcript。
5. 视频本体不会保存到本地，只保存 transcript。

注意：你平时使用的 Chrome/Safari 已登录 Canvas，不代表本地后端能直接读取那个浏览器的 cookie。SJTUFlow 不读取你的日常浏览器 profile，也不绕过登录、验证码、DRM 或课程权限。

Skills 分两类：

- 后端内置 skills：随应用发布，放在后端包内。
- 用户创建 skills：通过前端创建，保存在 `~/.sjtuflow/skills/`。

Transcripts 默认保存在 `~/SJTUFlowData/transcripts/`。
视频本体不会被复制到资料库；媒体流只会生成临时音频并转成 transcript。

## 数据位置

```text
~/.sjtuflow/
  config.toml
  skills/
  sessions/
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
- 历史会话本地保存与恢复 API。
- Canvas 读取与文件下载工具。
- Skill metadata-first 加载。
- Transcript metadata-first 加载与文本保存。
- 后端媒体工具：本地媒体转写、Canvas 托管浏览器会话解析媒体流、transcript 默认入库。
- 静态前端 MVP：控制面板、学习对话、Skills、Transcripts、媒体转写和本地设置。
- CLI 作为开发和备用入口。

待完成：

- Web 端写操作确认队列。
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
