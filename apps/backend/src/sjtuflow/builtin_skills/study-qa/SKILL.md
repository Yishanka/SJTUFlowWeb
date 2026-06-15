# Study QA

## Purpose

基于本地 transcripts 回答用户的具体学习问题，强制引用原文片段和时间戳；transcript 中没有的内容明确说"未提及"，不凭模型训练知识答。

## Required Tools

- `transcripts.list`
- `transcripts.read`

## Workflow

1. 接收问题：用户给一个具体问题（"上节课讲的 attention 是什么？"），同时给出课程 / 日期 / 讲次线索（可选）。
2. 候选筛选：
   - 调 `transcripts.list`，按 `title`、`description`、`updated_at` 在 metadata 层过滤候选。
   - 候选 ≥ 4 条时向用户确认目标讲次，不要自行展开读全部。
   - 候选 0 条时明确告知本地资料库未找到，并提示先用 `lecture-capture` 或媒体工具入库；本 skill 不调用媒体写工具。
3. 读取：对最多 3 条候选调 `transcripts.read(transcript_id=<id>)`。
4. 抽取与回答：
   - 在 transcript 文本里搜索与问题相关的片段（关键词、同义词、上下文窗口）。
   - 找到 → 用 1–3 段回答，**每段必须附"原文引用（≤ 30 字）+ 时间戳 `[mm:ss]` + transcript title"**。
   - 找不到 → 直接回答"本地 transcript 未提及"，不要用训练数据补全，不要"根据常识推断"。
5. 末尾追加"相关位置"：列出 transcript title + 涉及的时间戳列表，方便用户跳读。

## Output

聊天中以 Markdown 块返回：

```
## 答

<1–3 段回答>

> "<原文引用 ≤ 30 字>" — <transcript title> [mm:ss]

## 相关位置
- <transcript title>：[mm:ss], [mm:ss], ...

## 说明
- 仅基于本地 transcript 回答；未涉及的部分会显式标 "未提及"。
```

不写盘。

## Safety

- 本 skill 只读，不调用任何写工具。
- **严格禁止凭模型训练知识回答**：所有答案必须能在 transcript 原文找到支撑；找不到就说 "未提及"，不要补全、不要"根据常识 / 通识推断"。
- 一次最多 `transcripts.read` 3 条，多余由用户分次追问，避免一次性把多条 transcript 全文吃进上下文。
- 单条引用 ≤ 30 字；需要更长的引用时拆成多条短引用，避免大段抄录。
- 用户问题涉及成绩 / 考核标准 / 学术诚信等强声明时，必须显式提示"以课程通知为准"，不替老师定性。
- 不替用户做作业或考试答案；属于学术诚信场景时拒绝回答并提示原因。
