# Lecture Sign-in Check

## Purpose

回答签到 / 点名 / attendance 相关问题，并先判断用户要查的是"签到记录/考勤状态"还是"老师在课堂里是否提到签到"。

- 查"我有没有签到 / 签到记录 / 考勤状态 / attendance record"：优先用 Canvas token 找课程与入口，再用 Canvas 外部工具页读取可见记录。
- 查"老师有没有提到签到 / 点名 / 有没有说要签到"：优先用本地 transcript 作答；没有 transcript 时，引导用户到 Media 页启动录播转写。

## Required Tools

- `canvas.connection_status`
- `transcripts.list`
- `transcripts.search`
- `transcripts.read`
- `canvas.list_courses`
- `canvas.list_modules`
- `canvas.list_module_items`
- `canvas.list_external_tool_module_items`
- `canvas.read_external_tool_page`

## Workflow

1. 明确目标课程和日期：
   - 从用户问题中提取课程名、日期、讲次、"今天 / 昨天"等线索。
   - 如果课程或日期缺失，先用已有上下文推断；仍不明确时向用户确认。
2. 判断意图：
   - 用户问"我是否签到 / 签到成功了吗 / 考勤记录 / 签到记录在哪里"时，按"签到记录"处理。
   - 用户问"老师有没有提到签到 / 有没有点名 / 课上说没说 attendance"时，按"课堂内容"处理。
   - 两者都可能相关时，先查签到记录，再说明 transcript 只能证明课堂是否提及，不能证明实际签到状态。
3. 签到记录路径：
   - 先调用 `canvas.connection_status(ping=true)` 确认 Canvas token 可用。
   - 调用 `canvas.list_courses` 找到目标课程；课程不唯一时让用户确认。
   - 调用 `canvas.list_modules`、`canvas.list_module_items` 或 `canvas.list_external_tool_module_items` 查是否有签到、考勤、Attendance、Roll Call、external_tools 入口。
   - 如果用户提供了 Canvas `external_tools` URL，或模块里找到明确签到/考勤 external tool，调用 `canvas.read_external_tool_page(url=<url>)`。
   - 若返回 `requires_browser_login`，告诉用户先在 Media 页点击"准备 Canvas 登录态"，完成登录后重试。
   - 若 token 工具找不到签到入口，但用户确认 Canvas 左侧菜单有签到/考勤入口，请用户粘贴该 `external_tools` 页面 URL；学生账号/API 可能看不到左侧 LTI 导航配置。
   - 基于外部工具页返回的可见文本回答，不要把脱敏前的 query/token/signature URL 展示出来。
4. 课堂内容路径：
   - 先调用 `transcripts.list`，按课程名、日期、讲次、source 过滤候选。
   - 再用 `transcripts.search(query="签到")`、`transcripts.search(query="点名")`、`transcripts.search(query="attendance")` 辅助定位。
   - 找到 1-3 条候选后调用 `transcripts.read`，只读取必要 transcript。
5. 如果 transcript 已存在：
   - 严格基于 transcript 内容回答是否提到签到。
   - 找到相关片段时给出时间戳和简短原文引用。
   - 找不到时回答"本地 transcript 未提及签到 / 点名"，不要凭常识补全。
6. 如果 transcript 不存在：
   - 明确说明当前本地转写库里没有足够依据，不能判断老师是否提到签到。
   - 提醒用户去“媒体转写”页面，在 Canvas 录播输入框填写同一句自然语言问题或粘贴 Canvas `external_tools` URL。
   - 说明媒体页会用 Canvas token 匹配课程、用 SJTUFlow 托管浏览器抓取录播页和媒体候选，并保存 transcript；如果需要登录，用户要在 SJTUFlow 打开的浏览器窗口完成 Canvas 登录后重试。
   - 不要在聊天中直接调用视频抓取或转写工具。

## Output

聊天中以 Markdown 返回：

```
## 结论

<已签到 / 未看到签到记录 / 提到 / 未提到 / 缺少 transcript，需先转写>

## 依据

- Canvas: <课程/外部工具页标题/可见记录摘要>
- Transcript: <transcript title> [mm:ss]："<简短原文引用>"

## 说明

- 签到状态仅基于当前账号可见的 Canvas/LTI 页面。
- 课堂是否提到签到仅基于本地 transcript。
- 如果托管浏览器 profile 尚未登录，说明需要用户在 Media 页准备 Canvas 登录态。
```

## Safety

- 不绕过 Canvas 登录、验证码、DRM 或课程权限。
- 本 skill 不触发视频抓取、转写或保存；媒体页的转写工具只允许临时音频缓存，transcript 默认入库。
- 不把带 `key=`、`token=`、`signature=` 等签名 URL 原样展示给用户；必要时只展示脱敏 URL。
- 不代表用户点击签到、补签或提交任何远程表单；只读取当前账号已经可见的页面。
- 引用 transcript 原文保持简短，避免大段复制。
