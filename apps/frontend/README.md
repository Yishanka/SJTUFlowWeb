# SJTUFlow Frontend

本目录是 SJTUFlow 的本地浏览器前端，对应分工中的 A 部分。

当前实现采用零前端依赖的静态单页应用，直接调用现有 FastAPI 后端的
`/api/*` 接口。这样便于课程项目演示，也避免因为安装前端依赖影响运行。

## 本地预览

先在仓库根目录启动后端：

```bash
uv run sjtuflow web --no-open
```

再启动前端预览：

```bash
cd apps/frontend
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:5173
```

当前端运行在 5173 端口时，会默认请求 `http://127.0.0.1:8765` 上的后端 API。

## 构建

```bash
cd apps/frontend
npm run build
```

该命令会把 `index.html`、`app.js`、`styles.css` 复制到 `apps/frontend/dist`。
后端检测到该目录后，可以直接在 `/` 托管前端页面。

## 已实现页面

- 控制面板：展示 briefing、系统状态和项目入口
- 学习对话：支持历史会话恢复、自动新建会话和推荐问题
- Skills 工作流：metadata 列表、全文查看、内置 skill 复制、用户 skill 创建/编辑/删除
- 课堂转写库：metadata-first 列表，点击后按需读取全文；支持搜索、重命名、删除和刷新摘要
- 媒体转写：本地媒体和已授权流媒体的转写任务入口
- 本地设置：模型、Canvas、资料目录、安全确认策略和运行检查
