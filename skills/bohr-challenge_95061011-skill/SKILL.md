---
name: bohr-challenge_95061011-skill
description: |
  多文档比较与整合：玻尔周赛第四期 reading 赛道可执行 Skill，把文献阅读做成结构化解析：文档/图表/公式/结论/证据/知识图谱都可复核，而不是只给摘要。
---

# 多文档比较与整合 Skill

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
| writing_enhance.py | 大纲、证据草稿、润色、引用、结构检查、LaTeX |
| graphics_enhance.py | 可编辑 SVG 图表/流程/通路/装置/图形摘要 + 预览 |
| slides_enhance.py | slide plan、PPTX、讲者备注、PDF/PNG 预览 |
| datacode_enhance.py / experiment_enhance.py / audit_enhance.py | 数据执行、实验设计、学术审查 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output --profile reading
```

## 2. 解题总纲

把文献阅读做成结构化解析：文档/图表/公式/结论/证据/知识图谱都可复核，而不是只给摘要。

**本题突破方向（题面原文）：**
- 以题目交付要求与真实材料为准

## 3. 单案例 SOP

1. 读取全部原始文档与 case 约束，确认必做对象（原图、原表、公式、段落、记录）。
2. 用 scan_index/reading_enhance 解析文本、图表标题、表格上下文、公式与章节。
3. 按题目要求做图表解析、公式抽取、翻译、比较多文档、结论/数据提取或事实核验。
4. 每条结果回链 file+sha256+page/offset；未提供的像素/图注明确列缺口，不构造。
5. 输出结构化 notes/records/知识图谱/nanopublication 与 reading_report；Agent 读证后增补语义解释。

## 4. 本题评分维度（题面原文）

原题满分 **100 分**。GitHub 公开作品链接为额外附加分，最高加 **5 分**。综合三个固定案例的实际表现和提交材料评分，各评分项只计一次；本节只展示评分维度、分值和考察内容，不展示内部详细分档。

| 评分维度 | 分值 | 评分点及分值 | 考察内容 |
|-|-|-|-|
| 实际效果 | 40 分 | 任务完成度 15 分；结果正确性 15 分；结果完整性 5 分；结果可核验性 5 分 | 是否把同一问题下的多篇论文落到同一套字段定义上，交付可逐格对照的比较表、方法矩阵、证据卡片或整合报告。；每个单元格的取值是否来自它标注的那篇论文，单位、数据集、评价指标和对照设置是否随值记录，结论方向与关系强度是否被误读。；是否覆盖比较所需的字段组（对象、数据、方法、条件、指标、结论、局限）、任务指定的论文集合与比较维度。；每个单元格能否回溯到具体论文的具体位置，差异结论能否按记录复现与抽样核对。 |
| 使用增益 | 25 分 | 质量增益 15 分；效率增益 5 分；增益可验证性 5 分 | 在相同论文集合与比较任务下，加载成果相比不加载的基础 Agent 是否让比较结果更可信。；完成一次同等规模论文比较所需的阅读、填表、返工与逐格核对条目数是否下降。；是否通过同论文、同字段定义、同预算的公平对照证明质量或效率改善。 |
| 创新增益 | 15 分 | 新增能力 5 分；方法改进 5 分；创新证据 5 分 | 是否形成基础 Agent 或现有方案不具备、或难以稳定实现的跨文档对齐能力。；字段定义、抽取顺序、两级对齐、冲突归因等做法是否带来实质改进。；是否证明创新点相对已有工具与基础 Agent 的边界和价值。 |
| 可靠体验 | 10 分 | 调用与易用性 3 分；运行稳定性 4 分；异常处理 3 分 | Agent 是否能正确触发和调用，用户是否能按说明完成一次 5～10 篇论文的比较任务。；在不同主题、篇幅、版式的论文与重复运行下，字段口径与取值是否稳定一致。；对 PDF 解析失败、图表无法读取、论文主题不相关、同一论文多版本、结论互相矛盾且无法归因等情况的识别与处置。 |
| 生态贡献 | 10 分 | 文档完整性 3 分；资源可复用性 4 分；分享推广价值 3 分 | 使用说明、依赖、字段字典、配置、示例、适用范围、限制和维护信息是否齐全。；Skill 本体、字段字典、抽取脚本、评分表与输出模板能否被他人直接用于自己的论文集合。；是否沉淀可分享的比较案例、字段体系、冲突解释经验与真实应用价值。 |

质量与增益以平台固定案例、基础 Agent/Skill Agent 对照结果和可核验记录为依据；文字自述不能替代实际运行证据。

**GitHub 公开作品链接（5 分）**　提交本题作品的 GitHub 公开链接。评审无需登录即可打开链接并找到本次提交的作品，得 5 分。缺交或经核实链接无效，本项得 0 分，其他评分项按原规则计分。

## 5. 输出契约与验收

- 主产物：结构化文档、图表/公式清单、比较多文档矩阵、结论/数据记录、事实核验、笔记/知识图谱。
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
| 阅读/解析结果 | 按题目要求的答案、结构化字段、翻译或知识结构；完成标准：覆盖任务要求且能回到材料定位 |
| 证据与边界 | 页码、段落、图表、字段或公式位置；未报告和不确定项；完成标准：关键结论可复核，无法判断不被写成事实 |
| 运行状态 | 成功、部分完成、无法判断或拒答及原因；完成标准：异常原因和可人工接管位置清楚 |

正式测试案例、白板和测评结果由平台提供或生成，参赛者无须另交三个测试案例、白板或预先生成的正式答案。材料缺失、无法判断和运行失败要分别说明。

**本题作品的 GitHub 公开链接**　请在提交材料中填写作品所在的 GitHub 仓库、目录或文件链接。如一个仓库包含多个作品，请直接链接到本题作品所在位置，或在链接旁注明目录或文件名。提交前请用未登录 GitHub 的浏览器窗口打开一次，确认能看到作品。

请在提交目录根路径放置 `GITHUB_URL.txt`，第一行写 GitHub 链接。如需指定作品文件，可在第二行写本次提交目录中的相对文件路径。

## 6. 异常与恢复

无文本层扫描件走 OCR；图表缺像素不补造；公式识别不确定时保留原文；翻译只做可复核术语映射，长句由 Agent 处理。

## 7. 版本与依赖

- Python ≥3.8，stdlib only；平台预装 pdf-text-tool/office-renderer/ocr-tool/latex-compiler/browser-automation 等按 `environment/resources.yaml` 调用。
- 引擎 `bohrium-weekly-common@7.2`，无构建，放入 `/skills/weekly-skill/` 直接运行。

## 8. 组织方输入画像（必须遵守）

## Concrete input interface (organizer revision v2)
Read /input/comparison-workorder.json and all supplied corpus documents. Build a common-field comparison matrix with exact source_document_id and paragraph_id, then test comparability of task, metric, units, material, pollutant and budget before quantitative synthesis. A single-paper summary does not satisfy multi-document comparison. Retain all noncomparable and unreported conditions; do not pool unlike datasets or dyes.
The public example remains synthetic. Real papers, concrete target values, hidden expected readings and formal claims are supplied only during evaluation, never during Skill authoring. Original public scoring remains unchanged.


Provided resources and preinstalled tools are listed in `environment/resources.yaml`.


## 9. 实例证据

- `.selftest/regress.py` 全绿；profile9 三题 rubrics 覆盖 100/100/100。
- datacase 代码结果与交付统计完全一致；experimentcase 15 项、auditcase 14 项断言全绿。
- 所有数字只证明本地方法与对照差异，不冒充平台正式评分。
