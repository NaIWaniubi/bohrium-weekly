---
name: bohr-challenge_caca73b2-skill
description: |
  写作10｜结果与讨论章节写作：玻尔周赛第四期 writing 赛道可执行 Skill，把学术写作做成“读取原稿 → 识别任务 → 生成指定产物 → 来源/修改核验 → 预览”的闭环；直接交付可编辑成稿，不用大纲或报告替代。
---

# 写作10｜结果与讨论章节写作 Skill

## 0. 运行契约

- 输入 `/input/case.json`（必要时 `task.md`、`library`、模板/数据字典）；真实 PDF/Office/图片为事实依据。
- 工作区 `/app/work`；产物 `/app/output`，必含 `result.json`、原始/可编辑产物、真实预览、`sources`、`uncertainty`、`change_records`。
- 预算 64 模型请求 / 900 秒 / 16384 token；先存完整首版，再增量改进。
- 禁止杜撰科学数据、图注、像素、引用、软件状态；未完成项明确标 partial。

## 1. 共用引擎（stdlib only，随包提交）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 文件事实、章节/页码/偏移、元数据、重复/版本、needs_tool |
| answer.py | 硬过滤 + 多路 BM25/dense/RRF + 句子证据 |
| run_case.py / finalize.py | 完整首版 result.json、全量 sources、证据四重复核、异常恢复 |
| enhance.py / enhance_v7.py | canonical 版本、直接答案、claim 综述与各专项 profile 编排 |
| reading_enhance.py | 文档/图表/公式/比较/结论提取/笔记/知识图谱 |
| writing_enhance.py | 任务感知写作：原稿读取、逐句润色、模板格式、引用同步、结构诊断、引言/方法/结果/回复/翻译/LaTeX/基金/开题/长文档/成果转化/思维导图/证据驱动/完整论文；输出 MD/DOCX/PDF 预览与修改记录 |
| graphics_enhance.py | 可编辑 SVG 图表/流程/通路/装置/图形摘要 + 预览 |
| slides_enhance.py | slide plan、PPTX、讲者备注、PDF/PNG 预览 |
| datacode_enhance.py / experiment_enhance.py / audit_enhance.py | 数据执行、实验设计、学术审查 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output --profile writing
```

## 2. 解题总纲

把学术写作做成“读取原稿 → 识别任务 → 生成指定产物 → 来源/修改核验 → 预览”的闭环；直接交付可编辑成稿，不用大纲或报告替代。

**本题突破方向（题面原文）：**
- 以题目交付要求与真实材料为准

## 3. 单案例 SOP

1. 读取任务、目标读者、文体/模板/字数/引用格式与全部证据材料。
2. 先运行 writing profile 生成 source-grounded scaffold，确认 `task_type` 与题目一致；scaffold 必须包含原稿、证据定位与可编辑文件。
3. **Agent 二次成稿（必须）**：用自身语言能力把 scaffold 改写成题目要求的完整成稿/修订稿——补足连贯段落、术语一致、引用落点与方法/结果细节；翻译题完成整篇翻译，LaTeX 题用平台 latex-compiler 编译，引用题完成插入/重编号/去重。不得把 scaffold、报告、大纲或证据 bullet 当最终答案。
4. **回读核验（必须）**：逐项核对数字、单位、引用编号、图表指向、限定强度与源材料一致；把实际改动写入 `change_records`，将最终全文写回 `answer.content` 和对应 artifacts。
5. 输出前验证 `output_contract_preserved=true`，所有文件存在且可打开；无法核验的内容进入 `uncertainty`，不得伪报完成。


### Agent 二次成稿检查表（按题选用）

- **润色**：交付完整润色稿与逐句 `original/revised/type/reason`；数字、单位、引用编号、限定强度守恒。
- **格式**：真实 DOCX + PDF；字号、行距、页边距、栏数、题注、目录、参考文献样式逐项核对。
- **引用**：正文引用插入/重编号、文献去重、格式转换、来源核验同步完成，正文与文献表一致。
- **证据写作**：交付“论断清单—证据表—成稿”，每条论断有支持范围、强度与不确定性。
- **结构**：逐章逐段诊断，给出问题位置、类型、编辑动作与理由，并交付修改后稿件。
- **引言**：背景—相关工作分组—研究空白—贡献四部分承接，引用落在具体论断上。
- **方法**：步骤、设置、参数、数据集划分、对照、复现、限制七类齐全，参数追到配置/日志。
- **结果**：结果段与讨论段分开，图表读数、单位、误差、统计量、阴性结果与局限一致。
- **审稿回复**：意见/子意见逐条回应，回复、修订稿、修改位置与证据状态一致。
- **翻译**：整篇翻译 + 双语对齐；术语、数字、单位、引用、否定与限定条件守恒。
- **LaTeX**：完整 `.tex` 工程、参考文献/图片依赖、编译命令、日志与 PDF；编译失败必须如实记录。
- **基金/开题**：七要素成稿 + 要素/证据映射 + 时间线/风险/预期成果，不把计划写成已完成。
- **长文档**：按提纲写章节，术语/符号/图表编号/交叉引用/版本记录跨章节一致。
- **成果转化**：按受众与体裁完整改写，保留关键结论、数据、限定条件，并提供逐段对照。
- **思维导图**：节点—层级—关系—证据挂接齐全，输出可编辑 JSON/Markdown/DOT 与预览。
- **完整论文**：结构完整、图表/参考文献/声明齐全，事实与引用逐句可回溯。

## 4. 本题评分维度（题面原文）

总分 **100 分**。综合三个固定案例的实际表现和提交材料评分，各评分项只计一次；本节只展示评分维度、分值和考察内容，不展示内部详细分档。

| 评分维度 | 分值 | 评分点及分值 | 考察内容 |
|-|-|-|-|
| 实际效果 | 40 分 | 任务完成度 15 分；结果正确性 15 分；结果完整性 5 分；结果可核验性 5 分 | 是否真的把给定数据与图表写成了可编辑、可提交的结果段与讨论段。；图表读数、单位、误差棒含义、统计量与推断强度是否与原始数据一致。；图表、阴性结果、不一致文献与局限是否被如实交代。；正文数值与讨论论断能否被独立复核。 |
| 使用增益 | 25 分 | 质量增益 15 分；效率增益 5 分；增益可验证性 5 分 | 相同条件下加载 Skill、插件或工作流后，章节写作质量相比不加载的基础 Agent 是否提升。；是否减少起草、核对与返工的时间或步骤。；是否通过公平对照证明质量或效率改善。 |
| 创新增益 | 15 分 | 新增能力 5 分；方法改进 5 分；创新证据 5 分 | 是否形成基础 Agent 或现有写作方案不具备、或难以稳定实现的能力。；读图表、列发现、写结果、核证据、写讨论这一流程是否有实质改进。；是否证明创新点相对已有方案的边界和价值。 |
| 可靠体验 | 10 分 | 调用与易用性 3 分；运行稳定性 4 分；异常处理 3 分 | Agent 是否能正确触发，作者是否能按说明完成一次完整章节写作。；换图、换学科、换数据量后能否持续产出可用章节。；缺图、缺统计量、单位不明、引用找不到、图表读数不清时的识别与处置。 |
| 生态贡献 | 10 分 | 文档完整性 3 分；资源可复用性 4 分；分享推广价值 3 分 | 使用说明、依赖、配置、示例、适用范围、限制和维护信息是否齐全。；题集、评分表、章节模板、检查清单和脚本能否被他人直接复用。；是否沉淀可分享的案例、Benchmark、实践经验和真实写作价值。 |

质量与增益以平台固定案例、基础 Agent/Skill Agent 对照结果和可核验记录为依据；文字自述不能替代实际运行证据。

## 5. 输出契约与验收

- 主产物：Agent 二次成稿后的可编辑成稿/修订稿/引用结果/结构化写作产物；`answer.content` 为最终全文，`artifacts` 含 MD/DOCX/PDF/HTML 预览。deterministic scaffold 仅作证据底稿，不能替代最终成稿。
- 每条实质结论/图表/页面必须回链 `file + sha256 + page/offset` 或可编辑源文件。
- `result.json` 保留原 `output_contract` 全字段与 `case_id`；状态只写真实完成度。
- 可编辑 Office/图形/幻灯片必须提供真实 PDF/PNG 预览。

**提交材料**

参赛者只提交 Skill 及其必要资源；平台统一使用本题固定的 3 个案例运行 Skill。

| 材料 | 必需内容 | 交付标准 |
|-|-|-|
| Skill 本体 | `SKILL.md`，以及实际使用的提示、规则或处理流程；写清用途、适用条件、调用方式、输入输出和异常处理。 | 平台能据此加载并执行给定案例，不能用预制答案代替实际运行。 |
| 运行必需的脚本与资源 | 实际依赖的脚本、配置、模板、词表或其他资源；写清版本、安装方式、路径、来源和使用条件。 | 文件齐备、路径有效，不依赖作者未说明的个人文件或手工操作。 |
| 使用与方法说明 | 完整调用步骤、输入输出解释、关键方法、适用场景、限制、失败处理和自查方法。 | 其他人或 Agent 可按说明独立使用，说明与提交版本一致。 |
| 最小调用示例 | 至少一组输入、调用方式和示意输出；示意输出须标明为示例。 | 足以说明接口和完整使用流程，不冒充正式案例结果。 |

**Skill 运行后的输出要求**

每个案例返回一份结构化结果，保留输入标识、主结果、证据/来源定位、运行状态和不确定性。

| 内容 | 字段与要求 |
|-|-|
| 目标产物 | 可编辑的稿件、改稿、引用/格式结果或结构化写作产物；完成标准：满足题目指定体裁、受众和格式 |
| 事实与来源映射 | 数字、术语、引用、图表和关键论断的来源/修改记录；完成标准：可逐条回溯，不新增材料外事实 |
| 版本与边界 | 修改差异、人工确认项、材料缺口和异常；完成标准：结果可继续编辑，未确认部分明确标注 |

正式测试案例、白板和测评结果由平台提供或生成，参赛者无须另交三个测试案例、白板或预先生成的正式答案。材料缺失、无法判断和运行失败要分别说明。

## 6. 异常与恢复

无证据不写强结论；引用缺失标 unknown；格式冲突按题目模板优先；编译失败保留源文件与错误日志。

## 7. 版本与依赖

- Python ≥3.8，stdlib only；平台预装 pdf-text-tool/office-renderer/ocr-tool/latex-compiler/browser-automation 等按 `environment/resources.yaml` 调用。
- 引擎 `bohrium-weekly-common@7.4`，无构建，放入 `/skills/weekly-skill/` 直接运行。

## 8. 组织方输入画像（必须遵守）

## Harbor技术提交约定

# Runtime contract
This is a Skill authoring task. Create /app/submission/SKILL.md and any required text resources. Use valid name/description YAML frontmatter. Do not submit prepared answers or formal test outputs. The evaluator installs the actual Skill under /skills/weekly-skill, then runs official DeepSeek Harness on each fixed original case.

At execution time original input is /input/case.json and task.md. Material and artifacts are provided according to the topic; PDF parsing receives the actual PDF without a pre-extracted answer layer. Read actual files instead of assuming source content from labels. Work under /app/work; final actual results and editable artifacts belong under /app/output, including result.json with all original output_contract fields and case_id. Include honest status, sources, uncertainty and change records. Editable Office/slide/diagram outputs should also have real rendered PDF/PNG previews for inspection. A partial result must identify its limits. Do not fabricate missing software state or scientific data.

Model, source/case versions, runtime, budgets and actual native traces are recorded by the platform; do not invent benchmark scores. Execution budget: 64 model requests, 900 seconds, 16384 tokens per response including reasoning. Save honest partial deliverables early and improve incrementally. Three cases, three repetitions per condition; actual service failures are paused separately. The platform provides/generates any required comparison; participants do not provide hidden tests or whiteboards. Skill resources: at most 50 UTF-8 text files, 500KB each and 2MB total, no symlinks. Runtime artifacts may be binary and are retained independently of final JSON validity.

实际输入文件和原论文是事实依据；不依据错误的领域标签补造数据。


Resource entry point: environment/resources.yaml. Bundled files are relative to that directory; declared preinstalled runtime paths describe the pinned image. The actual fixed scientific cases are supplied by the evaluation platform.


---

题面已公开，正式提交开放时间以赛事通知为准。


## 9. 实例证据

- `.selftest/regress.py` 全绿；17 类写作任务专项回归与 10 包端到端 preflight 全绿；profile9 三题 rubrics 覆盖 100/100/100。
- datacase 代码结果与交付统计完全一致；experimentcase 15 项、auditcase 14 项断言全绿。
- 所有数字只证明本地方法与对照差异，不冒充平台正式评分。
