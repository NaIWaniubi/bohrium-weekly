---
name: bohr-product_arena_03_429cc14d-skill
description: |
  写作04｜参考文献与引用管理：玻尔周赛第四期 writing 赛道可执行 Skill，把学术写作做成“读取原稿 → 识别任务 → 生成指定产物 → 来源/修改核验 → 预览”的闭环；直接交付可编辑成稿，不用大纲或报告替代。
---

# 写作04｜参考文献与引用管理 Skill

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
2. 运行 writing profile，确认 `task_type` 与题目一致；`answer.content` 必须是实际成稿/修订稿全文。
3. 核对 `artifacts` 中的 MD/DOCX/PDF/HTML、`change_records`、`source_mapping` 与 `uncertainty`。
4. 仅在有真实新材料时增量补写；禁止用报告、大纲或证据 bullet 覆盖主产物。
5. 输出前验证 `output_contract_preserved=true`，所有文件存在且可打开。

## 4. 本题评分维度（题面原文）

总分 **100 分**。综合三个固定案例的实际表现和提交材料评分，各评分项只计一次；本节只展示评分维度、分值和考察内容，不展示内部详细分档。

| 评分维度 | 分值 | 评分点及分值 | 考察内容 |
|-|-|-|-|
| 实际效果 | 40 分 | 任务完成度 15 分；结果正确性 15 分；结果完整性 5 分；结果可核验性 5 分 | 是否从一份真实稿件和一份真实文献库出发，完成引用插入、编号更新、文献去重、格式转换与来源核验，并交付可继续编辑的稿件和同步的文献表。；每条引用是否落到真实、可定位的文献记录上，元数据是否准确，正文论断与所引文献是否真的匹配。；是否覆盖任务要求的六类动作以及全部章节、全部引用点与全部文献条目。；能否从成品回溯到原始稿件、文献库快照、每条引用的匹配依据和每一步改动。 |
| 使用增益 | 25 分 | 质量增益 15 分；效率增益 5 分；增益可验证性 5 分 | 在相同稿件、相同文献库、相同目标规范下，加载 Skill、插件或工作流后，相比不加载的基础 Agent 是否提升引用管理质量。；完成同等引用整理所需的人工修改量、逐条核对量、返工轮次和综合资源成本是否下降。；是否通过公平对照证明质量或效率改善。 |
| 创新增益 | 15 分 | 新增能力 5 分；方法改进 5 分；创新证据 5 分 | 是否形成基础 Agent 或现有文献管理工具不具备、或难以稳定实现的能力，例如编号连锁更新、正文与列表双向同步、论断级引用匹配、去重决策可解释。；条目匹配策略、去重判据、编号重排算法、格式映射规则是否带来实质改进。；是否证明创新点相对已有方案（基础 Agent、通用文献管理工具）的边界和价值。 |
| 可靠体验 | 10 分 | 调用与易用性 3 分；运行稳定性 4 分；异常处理 3 分 | Agent 是否能正确触发和调用，用户是否能按说明完成一次从稿件到成品的引用整理。；在不同篇幅、不同文献库来源和重复运行下能否持续产出编号一致、条目完整的成品。；对文献库字段缺失、DOI 解析失败、条目歧义、编号冲突、稿件格式不支持、文件损坏等情况的识别与处置能力。 |
| 生态贡献 | 10 分 | 文档完整性 3 分；资源可复用性 4 分；分享推广价值 3 分 | 使用说明、依赖、配置、示例、支持的目标规范、适用范围、限制和维护信息是否齐全。；Skill 本体、脚本、去重规则、格式映射表、测试题集等资源能否被他人复用。；是否沉淀可分享的案例、失败模式和实践经验。 |

质量与增益以平台固定案例、基础 Agent/Skill Agent 对照结果和可核验记录为依据；文字自述不能替代实际运行证据。

## 5. 输出契约与验收

- 主产物：与 `task_type` 对应的可编辑成稿/修订稿/引用结果/结构化写作产物；`answer.content` 为全文，`artifacts` 含 MD/DOCX/PDF/HTML 预览。
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
