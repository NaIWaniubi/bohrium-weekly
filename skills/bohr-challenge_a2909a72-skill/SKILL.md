---
name: bohr-challenge_a2909a72-skill
description: |
  学术文献检索：玻尔周赛第四期 search 赛道可执行 Skill，把学术文献检索做成可审计管线：问题拆解、跨库/跨语言扩展、去重、排序、证据回链与空结果诚实报告。
---

# 学术文献检索 Skill

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
        --index /app/work/index.json --work /app/work --out /app/output --profile search
```

## 2. 解题总纲

把学术文献检索做成可审计管线：问题拆解、跨库/跨语言扩展、去重、排序、证据回链与空结果诚实报告。

**本题突破方向（题面原文）：**
- 多轮查询规划： 将复杂科研问题拆解为合理的检索步骤，通过多轮搜索提高关键文献覆盖度，减少遗漏和无效查询。
- 关键词扩展： 识别专业术语、同义词、缩写、跨语言表达及相关概念，提高文献召回率，同时控制无关结果。
- 数据库检索： 合理选择和组合不同学术数据库与检索来源，提高检索相关性、覆盖度、元数据质量或执行效率。
- 结果去重与来源记录： 识别重复文献，核验文献信息，保留可靠来源，提高结果质量与可追溯性。

## 3. 单案例 SOP

1. 读取 case.json/task.md，拆出研究问题、时间/语言/类型/来源约束与必需元数据字段。
2. 用 scan_index 建库；用 search_enhance 抽取 NL 硬约束并生成去废词查询计划。
3. 多路检索 + BM25/dense/RRF + PRF；用 canonical 版本去重，避免把副本当独立证据。
4. 逐条阅读 evidence/hits 全文，选章节/句子级证据并核对 sha/pages/offset。
5. 用 search_quality 生成带 [E#] 的直接答案与覆盖报告；无证据保持空结果。

## 4. 本题评分维度（题面原文）

原题满分 **100 分**。GitHub 公开作品链接为额外附加分，最高加 **5 分**。综合三个固定案例的实际表现和提交材料评分，各评分项只计一次；本节只展示评分维度、分值和考察内容，不展示内部详细分档。

| 评分维度 | 分值 | 评分点及分值 | 考察内容 |
|-|-|-|-|
| 实际效果 | 40 分 | 任务完成度 15 分；结果正确性 15 分；结果完整性 5 分；结果可核验性 5 分 | \*\* 评价是否真正理解科研检索需求，按约束执行检索并交付可用的文献结果集，而不是给出一段"看起来像检索结果"的文字。**；** 评价返回文献的真实性、主题相关性、元数据准确性（标题、作者、年份、期刊、DOI）以及排序质量，主要参考 Precision@k、nDCG@k、元数据准确率与人工核验。**；** 评价是否覆盖科研问题所需的关键文献、代表性研究和主要研究方向，主要参考 Recall@k、关键文献命中率与人工覆盖度核验。**；** 评价结果能否追溯到可靠来源、原始查询与实际检索过程，评审人员能否独立复核。\*\* |
| 使用增益 | 25 分 | 质量增益 15 分；效率增益 5 分；增益可验证性 5 分 | \*\* 评价相同条件下加载 Skill、插件或工作流后，相比不加载的基础 Agent 是否提高检索准确性、覆盖度、排序质量与信息可信度。**；** 评价是否减少检索时间、工具调用、人工筛选、结果核验与重复检索的成本。**；** 评价是否通过公平对照证明检索质量或效率改善。\*\* |
| 创新增益 | 15 分 | 新增能力 5 分；方法改进 5 分；创新证据 5 分 | \*\* 评价是否形成基础 Agent 或现有检索方案不具备、或难以稳定实现的能力（如查询规划、多库联合、术语扩展、去重聚类、结果校验）。**；** 评价查询构造、关键词扩展、多库组合、去重、相关性判断、结果校验等方法是否带来实质改进。**；** 评价是否证明创新点相对已有方案的边界与价值。\*\* |
| 可靠体验 | 10 分 | 调用与易用性 3 分；运行稳定性 4 分；异常处理 3 分 | \*\* 评价 Agent 能否正确触发和调用，用户能否按说明完成一次完整检索。**；** 评价在不同主题、不同数据库和重复运行下能否持续产出可用结果。**；** 评价对检索为空、接口限流、元数据缺失、来源冲突、权限不足等情况的识别与处置。\*\* |
| 生态贡献 | 10 分 | 文档完整性 3 分；资源可复用性 4 分；分享推广价值 3 分 | \*\* 评价使用说明、依赖、配置、示例、适用范围、限制与维护信息是否齐全。**；** 评价 Skill 本体、脚本、配置、题集与检索模板能否被他人复用。**；** 评价是否沉淀可分享的案例、Benchmark、检索经验与真实应用价值。\*\* |

质量与增益以平台固定案例、基础 Agent/Skill Agent 对照结果和可核验记录为依据；文字自述不能替代实际运行证据。

**GitHub 公开作品链接（5 分）**　提交本题作品的 GitHub 公开链接。评审无需登录即可打开链接并找到本次提交的作品，得 5 分。缺交或经核实链接无效，本项得 0 分，其他评分项按原规则计分。

## 5. 输出契约与验收

- 主产物：检索结果、查询/筛选审计、canonical 版本、章节/句子证据、直接答案、覆盖与效率报告。
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
| 检索结果 | 候选文献、排序/筛选结果、元数据和来源定位；完成标准：结果与检索范围、相关性依据和缺失项一致 |
| 证据记录 | 查询、筛选、排序或追踪依据；完成标准：他人可按记录复核每条结果 |
| 状态说明 | 空结果、冲突、权限或接口异常；完成标准：不补造文献，明确已完成和未完成部分 |

正式测试案例、白板和测评结果由平台提供或生成，参赛者无须另交三个测试案例、白板或预先生成的正式答案。材料缺失、无法判断和运行失败要分别说明。

**本题作品的 GitHub 公开链接**　请在提交材料中填写作品所在的 GitHub 仓库、目录或文件链接。如一个仓库包含多个作品，请直接链接到本题作品所在位置，或在链接旁注明目录或文件名。提交前请用未登录 GitHub 的浏览器窗口打开一次，确认能看到作品。

请在提交目录根路径放置 `GITHUB_URL.txt`，第一行写 GitHub 链接。如需指定作品文件，可在第二行写本次提交目录中的相对文件路径。

## 6. 异常与恢复

空命中不编造；跨语言召回低时扩词≤2轮；版本冲突并列记录；无权限/损坏文件显式标记。

## 7. 版本与依赖

- Python ≥3.8，stdlib only；平台预装 pdf-text-tool/office-renderer/ocr-tool/latex-compiler/browser-automation 等按 `environment/resources.yaml` 调用。
- 引擎 `bohrium-weekly-common@7.2`，无构建，放入 `/skills/weekly-skill/` 直接运行。

## 8. 组织方输入画像（必须遵守）

## Actual source-bound search cases
Organizer-authorized source repair:9actual scientific documents with actual Crossref/EuropePMC/publisher responses, real retained reference graph, concrete query/reference/iteration/quality workflows and explicitly organizer-local incremental import states. Public own16rubric unchanged. No live remote coverage, global novelty/latest-publication or fabricated full-text/citation/status claim.


## 9. 实例证据

- `.selftest/regress.py` 全绿；profile9 三题 rubrics 覆盖 100/100/100。
- datacase 代码结果与交付统计完全一致；experimentcase 15 项、auditcase 14 项断言全绿。
- 所有数字只证明本地方法与对照差异，不冒充平台正式评分。
