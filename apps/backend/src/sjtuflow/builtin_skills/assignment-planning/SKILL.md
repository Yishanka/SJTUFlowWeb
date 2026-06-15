# Assignment Planning

## Purpose

读取一份作业要求，生成执行计划、资料清单和提交检查表，让用户在动手前看清全貌。

## Required Tools

- `canvas.list_assignments`
- `canvas.list_files`
- `canvas.get_file`
- `transcripts.list`
- `skills.list`

## Workflow

1. 定位作业：
   - 用户若给出明确课程 + 作业标题，先 `canvas.list_assignments(course_id=<id>)` 列出该课作业，按标题精确匹配；候选 ≥ 2 时让用户确认。
   - 用户若只说"最近的作业"，由用户确认是看本课所有还是全局；不要自动挑"最近的一条"。
2. 解析作业要求：从 `description`、`submission_types`、`points_possible`、`due_at`、`allowed_extensions` 抽取关键约束。`description` 中含 rubric 时按条列出。
3. 关联本地资料：
   - `canvas.list_files(course_id=<id>)`：列出与作业可能相关的课件（按文件名 / 关键词匹配，给出 top 5 候选，不下载）。可对个别候选用 `canvas.get_file(file_id=<id>)` 拿元数据，但**不要**调用 `canvas.download_file`（写工具，需用户在 UI 上单独确认）。
   - `transcripts.list()`：列出该课程已转写的 transcript，让用户判断是否相关。
4. 生成计划：
   - 倒推时间：截止 → 提交前自查 → 主体撰写 → 资料收集 → 题目理解。每步给出建议时长和可量化产出。
   - 标注阻塞条件（例如需要小组协作 / 需要数据 / 需要先看某讲）。
5. 生成提交检查表：从 rubric / 要求中提炼"格式 / 内容 / 完整性 / 学术诚信"四组可勾选项。
6. 末尾给出"下一步建议"：例如调用 `transcript-review` skill 总结相关讲次；或让用户在 UI 上确认 `canvas.download_file` 下载某 PDF。

## Output

聊天中以 Markdown 返回，结构：

```
# <作业标题>

## 关键信息
- 截止：<due_at>
- 提交类型：<submission_types>
- 满分：<points>
- 文件类型限制：<allowed_extensions>

## 要求拆解
- ...

## 相关本地资料
- 课件：<file_name>（Canvas file_id=<id>）
- transcripts：<title>

## 执行计划
| 步骤 | 截止 | 产出 | 备注 |
|---|---|---|---|
| ... | ... | ... | ... |

## 提交检查表
- [ ] 格式：...
- [ ] 内容：...
- [ ] 完整性：...
- [ ] 学术诚信：...

## 下一步建议
- ...
```

默认不写盘。如需"把计划保存为本地文件"，由用户在 UI 上触发 `filesystem.write_text` 并经 pending approval，本 skill 不直接写。

## Safety

- 本 skill 只调用 read 类工具；不下载附件（`canvas.download_file` 是 write，禁止在本 skill 中调用）、不提交作业、不调用任何写工具。
- 不替用户做学术判断或"完成作业"，仅做信息整理与计划。
- 作业描述包含保密 / 学术诚信 / 查重等敏感声明时，在输出中显式提醒。
- Canvas 调用失败立即停止并提示用户，不做猜测。
