# SJTUFlow 项目蓝图

## 定位

SJTUFlow 是一个纯本地的 SJTU 学习助手。它由本机后端和浏览器前端组成，不需要登录 SJTUFlow 账号，不提供云端多用户服务。用户启动应用后，浏览器打开本地界面；首次使用时填写模型、Canvas token、资料目录等配置，然后在本机完成课程 briefing、自然语言对话、文件读取、转写和 skill 工作流。

CLI 仍保留为开发和备用入口，但产品主入口是本地 Web：

```text
uv run sjtuflow web
  -> http://127.0.0.1:8765
```

## 当前状态

已完成的基础能力：

- monorepo 结构：`apps/backend` 放 Python 后端和内置 skills，`apps/frontend` 为空前端框架，`docs` 位于仓库根目录。
- 后端 API：FastAPI 本地服务，提供配置、doctor、briefing、session chat、tools、skills、transcripts 等接口。
- Agent runtime：OpenAI-compatible tool calling、mock provider、工具循环、审计日志。
- Canvas MVP：课程、作业、公告、文件列表、文件下载。
- Skill MVP：只预加载标题和说明，按需通过 `skills.read` 读取全文。
- Transcript MVP：只预加载标题和说明，按需通过 `transcripts.read` 读取全文，可保存文本 transcript。

尚未完成的产品能力：

- 完整前端 UI。
- 视频下载/音频提取/ASR 转写工具。
- 邮箱连接器。
- 文档抽取、检索索引和课程问答。
- 可视化权限确认队列。

## 用户体验目标

首次启动：

1. 浏览器打开本地界面。
2. 如果配置缺失，进入配置页。
3. 用户填写模型 provider、endpoint、API key 环境变量或明文 key、Canvas token、资料目录。
4. 后端保存 `~/.sjtuflow/config.toml`。
5. 进入主界面并生成 startup briefing。

日常使用：

- 顶部显示本地连接状态、模型状态、Canvas 状态。
- 首页有 briefing 区块：紧急、即将到来、更新、警告分块展示。
- 主区域是对话/任务工作区，用户用自然语言提需求。
- 侧栏展示 Skills、Transcripts、下载文件、近期任务。
- 前端提供创建/编辑 skill 的入口；用户创建的 skills 写入 `~/.sjtuflow/skills/`。
- 写文件、下载、外部写入等动作进入确认流程。
- transcript 和 skill 默认只显示标题与说明，正文按需打开或由模型按需读取。

## 核心场景

- “这周有哪些作业和截止时间？”
- “展开这门课最近的公告，并告诉我需要做什么。”
- “把这个视频转成 transcript，保存到资料库。”
- “读取上周算法课 transcript，整理老师提到的考点。”
- “根据 Canvas 公告和本地 transcript 生成周报。”
- “把这套周报流程保存成一个 skill。”

## 能力分层

信息获取：

- Canvas 课程、公告、作业、文件、页面、提交状态。
- 邮箱邮件列表、正文、附件，后续可选。
- 本地文件、transcript、SKILL.md。
- 视频/音频来源元数据。

本地处理：

- 下载课件和附件到资料目录。
- 音视频音频提取与语音转写。
- transcript 保存、列表、读取、摘要。
- PDF、HTML、docx、pptx 文本抽取。
- 本地检索索引。

生成与编排：

- briefing 摘要和高优先级事项提取。
- 基于来源回答问题。
- 生成周报、TODO、复习提纲、任务计划。
- 将重复流程沉淀为 skill。

## 非目标

- 不做多用户 SaaS 或远端账号系统。
- 不建立中央数据库；默认使用本地配置、资料目录、缓存和审计日志。
- 不绕过学校平台的登录、验证码、DRM 或权限限制。
- 不自动提交作业、发送邮件、删除文件或修改远端内容。
- 不在启动时全量同步所有课程资料。

## 目录

```text
SJTUFlowWeb/
  apps/
    backend/
      src/sjtuflow/
        agent/
        cli/
        connectors/
        llm/
        services/
        skills/
        storage/
        tools/
        web/
        builtin_skills/
          weekly-review/SKILL.md
    frontend/
      public/
      src/
  docs/
  ~/.sjtuflow/skills/        # 用户通过前端创建，本地运行时目录
  packages/
  pyproject.toml
  uv.lock
```
