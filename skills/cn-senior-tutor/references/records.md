# 本地教材索引与学习档案

首次学习、切换教材、续学、完成批改及安排复习时读取。将结构化证据保存在当前学习工作区 `.student-tutor`；这不是自动同步、家长报告或提醒服务。

## 定位和隔离

- 学生昵称 ID 默认 `student-1`，只用 ASCII 字母、数字、下划线、短横线，长度 1—64。多人共用环境先确认当前昵称。教材课程 ID 也使用此规则，如 `math-g5-v1`。
- `--root` 始终指向学习工作区的 `.student-tutor`，不是安装技能的目录。脚本路径相对 本 `SKILL.md` 所在目录。不要在技能安装目录放学生数据。
- 初高中 `grade` 表示学段内年级 1—3；小学 1—6。学生档案记录目前学段和年级，课程另记录教材学段和年级，补基础时两者可以不同。
- 按实际输入建档，不要求真实姓名、生日、学校、联系方式。不要遍历或展示其他昵称的档案。
- 项目提供的 `.gitignore` 排除学习数据；复制技能到已有项目时，确保已有忽略规则包含 `.student-tutor/` 和 `.student-tutor/inputs/`，不能覆盖现有忽略文件。

## 工具接口

所有通用参数放在子命令之后。用当前文件编辑工具写入合法 UTF-8 JSON 输入文件；不要把学生答案拼进 shell 的 `echo`、here-string 或命令参数。

```text
python "<技能目录>/scripts/study_state.py" init --root "<学习工作区>/.student-tutor" --learner student-1 --stage primary --grade 5
python "<技能目录>/scripts/study_state.py" update-profile --root "<学习工作区>/.student-tutor" --learner student-1 --stage junior --grade 1
python "<技能目录>/scripts/study_state.py" course --root "<学习工作区>/.student-tutor" --learner student-1 --course math-g5-v1 --input "<课程输入.json>"
python "<技能目录>/scripts/study_state.py" record --root "<学习工作区>/.student-tutor" --learner student-1 --course math-g5-v1 --input "<作答输入.json>"
python "<技能目录>/scripts/study_state.py" show --root "<学习工作区>/.student-tutor" --learner student-1 --course math-g5-v1 --limit 10
```

初中/高中将 `--stage` 改为 `junior`/`senior`，采用实际年级。`show` 不带 `--course` 时查看当前学生课程概况。初次没有档案时 `init`，再次使用先 `show`；已有资料冲突时核对昵称，不用清空档案解决。

学生明确告知升年级、升学段或原档案填写有误时，用 `update-profile` 更新当前学段和年级，保留所有旧课程与作答。该命令必须指定当前 `--stage`，省略 `--grade` 表示年级未知，会写入 `null`，因此已知年级时一并传入。不能凭日期或所读教材自动修改学生年级。

## 教材课程输入

输入只含支持字段，未知字段会被拒绝。必需 `source_file`、`subject`、`topics`；可选 `source_sha256`、`stage`、`grade`、`edition`、`volume`，不要编版本。课程 `stage`/`grade` 表示教材范围，可以与学生档案不同；跨学段补基础时明确填写，例如初中学生补小学五年级教材填 `stage: primary`、`grade: 5`。

新课程省略或 `null` 的 `stage` 继承学生学段；同学段且省略 `grade` 才继承学生年级，跨学段且省略时年级为未知。已有课程省略或 `null` 的 `stage` 保留原学段，省略 `grade` 保留原年级。要明确表示年级、版次或册别未知时填 `null`。旧课程的教材学段不能替换，换教材需新课程 ID。

```json
{
  "source_file": "<实际教材路径>",
  "subject": "数学",
  "grade": 5,
  "topics": [
    {
      "id": "fractions-add",
      "title": "异分母分数加法",
      "pdf_pages": [20, 21],
      "prerequisites": ["equivalent-fractions"],
      "read_status": "verified"
    }
  ]
}
```

知识点 `id` 使用相同的安全 ID 规则。`pdf_pages` 是文件中从 1 开始的页序号，不能填未核实的印刷页码。`verified` 表示正文已读且关键内容完整；`needs_confirmation` 表示识别/条件仍待确认；这两种必须有已处理页码。`unread` 只作目录定位，页码可以空。

首次导入可访问 PDF 时，使用当前环境的文件哈希工具计算 SHA-256，转为 64 位小写十六进制并写入 `source_sha256`。续学和重新提取前核对当前文件哈希；不同则新建课程，不沿用旧页码。无法取得文件或哈希时省略此字段并说明来源尚待核对，不编造哈希。既有非空哈希遇到新值不同会报错，省略或 `null` 不会清除旧值。

同课程更新合并精确 ID 的知识节点，保留其他节点和作答历史。换文件、版本或学科时建新课程 ID；导入时检查文件内容是否变化，不能只看路径是否相同。前置知识 ID 可先列出，未读取正文的前置知识不能据此声称已掌握。

只有文字题、还没有 PDF 时，`source_file` 写“学生文字输入（无PDF）”，`pdf_pages` 为空，`read_status` 为 `unread`，表示尚未读取对应教材页；正常保存作答，不因此把学生评价为未学。以后导入 PDF 建新的教材课程，保留文字题历史。

## 作答输入

```json
{
  "attempt_id": "fractions-add-20261004-01",
  "topic_id": "fractions-add",
  "question": "计算 1/2+1/3，并说明通分的理由。",
  "student_answer": "5/6",
  "reasoning": "把分母都化为6，分子分别为3和2。",
  "outcome": "correct",
  "assistance": "hinted",
  "feedback": "在通分提示后正确完成，独立应用还待验证。",
  "error_cause": "",
  "evidence": "第一次直接相加分母；给出等值分数提示后修正。",
  "review_on": "2026-10-05"
}
```

必需 `attempt_id`、`topic_id`、`question`、`student_answer`、`outcome`、`assistance`；其余为可选。`review_on` 按实际当前日期和学习情况确定，不照抄示例日期。

- `outcome`：`correct` / `partially_correct` / `incorrect` / `not_evaluated`。结果与推理矛盾不能记完全正确；只有展示解析、还没作答时用 `not_evaluated`。
- `assistance`：`independent` / `hinted` / `explained` / `unresolved`。多种帮助并存时记录影响本题的最高帮助程度，不能让最后一句正确回答掩盖前面的完整讲解。
- `reasoning` 忠实记录实际思路，没有过程就留空；`error_cause` 有证据才具体归因，否则“待核实”；`evidence` 记录支撑评价的事实。
- 相同 `attempt_id` 和相同内容可重试，不重复计数；相同 ID 的不同内容报错。下一次作答用新 ID，保留原历史。

## 续学和掌握程度

先读取本学生的课程概况，再读取当前课程最近记录；需要更早证据时提高 `--limit`（最多 100）或按宿主工具读取对应档案的必要范围。把结论告诉学生：“上次通分需要提示，今天先用一题看看能否独立完成。”

依据记录分别判断接触过、提示后完成、本题独立完成、不同形式独立验证通过、延后复习通过；工具不从正确率自动认证掌握。未读的教材节点与学习状态是两种概念，`verified` 不表示学生会了。

先确认 `record` 成功后再说“这次练习已保存”。失败时仍可在会话内辅导，并明确尚未保存；损坏文件不覆盖成空档案。只清理自己刚创建的临时输入文件，不能删除教材或学生原有档案。
