# Transcript Review

## Purpose

阅读一段课堂 transcript，抽取主题、考点、作业提醒和关键时间戳，便于课后复盘。

## Required Tools

- `transcripts.list`
- `transcripts.read`

## Workflow

1. 定位目标 transcript。先调用 `transcripts.list`（只返 metadata），按 `title`、`description`、`updated_at` 匹配用户指定的课程/日期/讲次。候选 ≥ 2 条时向用户确认而非自行猜测；0 条时明确告知本地资料库未找到，并提示通过媒体工具先生成 transcript（不要在本 skill 内调用媒体工具）。
2. 拿到目标 transcript 的 `id` 后，调用 `transcripts.read(transcript_id=<id>)`，返回里包含 `content`（JSON 资料还会含 segments）。
3. 严格基于 transcript 文本抽取，禁止脑补：
   - 主题（topics）：3–7 条短语，尽量带一个示例时间戳 `[mm:ss]`。
   - 考点（exam_points）：只在原文出现 "会考 / 重点 / 期末 / exam / final" 等措辞时列出；否则留空。
   - 作业 / 截止 / 通知（action_items）：日期 + 提交方式 + 来源时间戳。
   - 关键节点（highlights）：最多 5 条；一句话归纳。
   - 未解之处（open_questions）：transcript 中提到但未展开。
4. 回复末尾给出 `source`，写明 transcript title 与 path。

## Output

聊天中以 Markdown 块返回，结构：

```
# <transcript title> · <updated_at>

## 主题
- ...

## 考点
- ...

## 待办与截止
- ...

## 关键节点
- ...

## 未解之处
- ...

来源：<title>（<path>）
```

不写盘。若用户要求"保存为摘要 / 生成 report"，由用户在 UI 上单独触发 `filesystem.write_text` 或 `transcripts.save_text`，走 pending approval；本 skill 不直接写。

## Safety

- 本 skill 只读，不调用任何写工具。
- 不预加载 transcript 全文到上下文：必须先 `list` 再 `read`，且 `read` 一次只读一条。
- 不对未在 transcript 中出现的内容做主观推断；不确定时显式标注"transcript 未提及"。
- 引用原文片段保持简短（≤ 30 字），避免大段抄录。
