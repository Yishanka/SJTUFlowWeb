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
6. 转写本地视频，或用自然语言/Canvas URL 定位 Canvas 课程录播并保存 transcript 到资料库。

Canvas 课程视频的推荐流程：

1. 进入“媒体转写”页面，先点“准备 Canvas 登录态”，在 SJTUFlow 托管浏览器里登录一次 Canvas。
2. 在 Canvas 录播输入框里写自然语言，例如“今天算法设计课程老师是否提到签到？”；也可以直接粘贴 `https://oc.sjtu.edu.cn/courses/.../external_tools/...` 作为调试/兼容路径。
3. 自然语言路径会先用 Canvas token 匹配课程，然后通过 SJTU 课程视频 LTI 工具 `external_tools/9487` 获取该课程 VOD 回放列表；LLM 只负责从脱敏的视频元数据中选择。
4. 转写任务开始时会先短暂检查该 profile 是否已登录；如果未登录，会直接提示你重新准备登录态，不会反复弹登录窗口。
5. 选中视频后，后端通过 SJTU VOD API 取得授权流地址并交给 ffmpeg；显式 URL 调试路径才会无头打开页面抓流。
6. 后端流式转写并保存 transcript。视频本体不会保存到本地。

注意：你平时使用的 Chrome/Safari 已登录 Canvas，不代表本地后端能直接读取那个浏览器的 cookie。SJTUFlow 不读取你的日常浏览器 profile，也不绕过登录、验证码、DRM 或课程权限。
模型只会看到脱敏后的课程/视频/媒体候选信息；签名媒体 URL、Cookie 和请求头不会发给模型或显示在前端。

## 本地转写模型

本地媒体转写使用 `faster-whisper`，默认 ASR 模型是 `base`。第一次转写时，如果本机 Hugging Face cache 里没有 `Systran/faster-whisper-base`，后端会尝试从 Hugging Face 下载；如果当前机器不能解析或访问 `huggingface.co`，转写会失败，需要先修网络/DNS，或把模型放到本地后在配置里指定。

可选配置在 `~/.sjtuflow/config.toml`：

```toml
[asr]
model = "base"
model_path = ""          # 本地 faster-whisper/CTranslate2 模型目录；填写后不走下载
download_root = ""       # 可选 Hugging Face cache 目录
local_files_only = false # true 表示只用本地缓存/本地路径
device = "cpu"
compute_type = "int8"
```

推荐把模型下载成一个普通本地目录，然后在前端“本地设置 → 本地转写模型 → 本地模型目录”里填写该目录：

```bash
mkdir -p /home/projects/SJTUFlowWeb/models/faster-whisper-base

uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Systran/faster-whisper-base",
    local_dir="/home/projects/SJTUFlowWeb/models/faster-whisper-base",
    allow_patterns=[
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
    ],
)
PY
```

如果当前网络访问 Hugging Face 不稳定，可以使用镜像：

```bash
HF_ENDPOINT=https://hf-mirror.com uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Systran/faster-whisper-base",
    local_dir="/home/projects/SJTUFlowWeb/models/faster-whisper-base",
    allow_patterns=[
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
    ],
)
PY
```

下载完成后，在前端填入：

```text
/home/projects/SJTUFlowWeb/models/faster-whisper-base
```

如果这台机器完全不能联网，也可以在另一台机器下载同一个目录后拷贝过来。目录里至少应包含 `model.bin`、`config.json`、`tokenizer.json`、`preprocessor_config.json` 和 `vocabulary.*`。

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
- 后端媒体工具：本地媒体转写、Canvas 自然语言定位录播、托管浏览器会话解析媒体流、transcript 默认入库。
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
