---
name: bohr-challenge_15c85674-skill
description: |
  文献智能问答：玻尔周赛第四期 reading 赛道可执行 Skill，把文献阅读做成结构化解析：文档/图表/公式/结论/证据/知识图谱都可复核，而不是只给摘要。
---

# 文献智能问答 Skill

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

原题满分 **100 分**。GitHub 公开作品链接为额外附加分，最高加 **5 分**。综合三个案例的实际表现和提交材料评分，各评分项只计一次，案例难度不额外加权。

| 评分维度 | 分值 | 评分点及分值 | 考察内容 |
|-|-|-|-|
| 实际效果 | 40 分 | 任务完成度 15 分；结果正确性 15 分；结果完整性 5 分；结果可核验性 5 分 | 能完成逐题问答；事实、数值、单位及条件符合原文；子问题覆盖完整；引文可定位且支持对应结论。 |
| 使用增益 | 25 分 | 质量增益 15 分；效率增益 5 分；增益可验证性 5 分 | 相对白板改善回答质量；减少证据定位步骤、追问轮次、人工核对和重试成本，并结合引用准确性评估收益；增益有同输入对照和实际记录支持。 |
| 创新增益 | 15 分 | 新增能力 5 分；方法改进 5 分；创新证据 5 分 | 针对基础 Agent 的具体不足形成能力或方法改进，例如跨章节整合、证据选择、条件提取和拒答判断；说明改进机制、适用边界，并由实际结果验证。 |
| 可靠体验 | 10 分 | 调用与易用性 3 分；运行稳定性 4 分；异常处理 3 分 | 能按说明独立调用；跨论文及重复运行表现稳定；对输入错误、材料不足、证据冲突等情况给出明确反馈和合理处置。 |
| 生态贡献 | 10 分 | 文档完整性 3 分；资源可复用性 4 分；分享推广价值 3 分 | 说明、依赖、示例和限制清楚；必要资源可获得、可迁移；明确目标用户、应用场景及可复用的实践经验。 |

质量和效率增益以平台生成的白板、正式运行结果及可核验记录为依据。方法说明中的效果主张需要实际证据支持，文字自述不替代运行结果。

**GitHub 公开作品链接（5 分）**　提交本题作品的 GitHub 公开链接。评审无需登录即可打开链接并找到本次提交的作品，得 5 分。缺交或经核实链接无效，本项得 0 分，其他评分项按原规则计分。

## 5. 输出契约与验收

- 主产物：结构化文档、图表/公式清单、比较多文档矩阵、结论/数据记录、事实核验、笔记/知识图谱。
- 每条实质结论/图表/页面必须回链 `file + sha256 + page/offset` 或可编辑源文件。
- `result.json` 保留原 `output_contract` 全字段与 `case_id`；状态只写真实完成度。
- 可编辑 Office/图形/幻灯片必须提供真实 PDF/PNG 预览。

**提交材料**

提交一套完整、可加载的 Skill 材料，包含下表内容。说明和示例可以合并在同一份文档中，保持入口清晰即可。

| 材料 | 必需内容 | 交付标准 |
|-|-|-|
| Skill 本体 | `SKILL.md`，以及实际使用的提示、规则或处理流程；写清名称、用途、适用条件、调用方式、执行步骤、输入输出和异常处理。 | 平台能据此加载并执行给定案例；流程可用于同类文献问答，不能用预制答案代替实际运行。 |
| 运行必需的脚本与资源 | 实际依赖的脚本、配置、模板、词表等；所需依赖及版本、安装方式、资源路径、来源与使用条件。没有额外依赖时明确说明。 | 文件齐备、引用路径有效；公开依赖可按说明获得；运行不依赖作者未说明的个人文件或手工操作。不使用脚本的 Skill 无须为此另写脚本。 |
| 使用与方法说明 | 作品版本、完整调用步骤、输入输出解释；拟解决的基础能力不足、关键改进方法与作用机制；适用场景、目标用户、自查与验收方式、常见问题、已知限制、失败处理和复用建议。 | 其他人或 Agent 可按说明独立使用；说明与提交版本一致，能核对改进主张及其适用边界。 |
| 最小调用示例 | 至少一组输入、调用方式和对应输出，演示段落编号、问题编号、答案及证据的关联。可使用自拟短材料；示意输出须标明为示例。 | 示例足以说明接口和完整使用流程；不把示意输出当作正式效果证明。 |

**Skill 运行后的输出要求**

每个案例返回一份结构化结果，保留输入标识及以下对应关系。下表给出业务字段；字段名可由平台适配，含义与层级须保持一致。

| 内容 | 字段与要求 |
|-|-|
| 输入标识 | `case_id`、`paper_id`、`material_version` 与本次输入一致。 |
| 逐题回答 | `answers[]` 中每个问题一项，包含 `question_id`、回答文本 `answer`、成立条件列表 `conditions`；不得省略无法回答的问题。 |
| 回答状态 | 每项 `status` 明确区分 `answered`（完整回答）、`partial`（部分回答）、`insufficient_evidence`（材料不足）、`input_error`（输入读取失败）。 |
| 论断与证据 | 每项 `claims[]` 含 `claim_id`、论断文本 `text`、类型 `kind` 和 `evidence[]`；`kind` 为 `reported`（原文报告）或 `inferred`（推断）；每条证据含 `paragraph_id` 和原文短引文 `quote`，并通过输出中的论文标识定位来源。 |
| 未完成部分 | 每项 `unanswered_parts[]` 列明未回答部分及原因；没有则为空列表。 |
| 阅读范围 | `coverage` 说明已读章节、未读章节及原因；实际读取情况结合平台运行记录核验。 |

有依据地说明“材料未报告”也是有效回答。是否完成问题，结合回答依据和材料边界判断，不仅凭状态标签或未回答列表判定漏答。

正式测试案例、白板和测评结果由平台提供或生成，参赛者无须另交三个测试案例、白板或预先生成的正式答案。提交前应核对材料齐备、调用步骤可执行、输出与上述要求一致，并如实说明已知限制。

缺少作品必要证据时，相关评分项最高为该项满分的 40%；缺少原始输出或核验依据时，相关项按最低档处理。已证实虚构结果、伪造证据或隐瞒关键失败的相关项记 0 分。组织方材料、白板缺失或平台故障，暂停相关项评分，补齐后再评。

**本题作品的 GitHub 公开链接**　请在提交材料中填写作品所在的 GitHub 仓库、目录或文件链接。如一个仓库包含多个作品，请直接链接到本题作品所在位置，或在链接旁注明目录或文件名。提交前请用未登录 GitHub 的浏览器窗口打开一次，确认能看到作品。

请在提交目录根路径放置 `GITHUB_URL.txt`，第一行写 GitHub 链接。如需指定作品文件，可在第二行写本次提交目录中的相对文件路径。

## 6. 异常与恢复

无文本层扫描件走 OCR；图表缺像素不补造；公式识别不确定时保留原文；翻译只做可复核术语映射，长句由 Agent 处理。

## 7. 版本与依赖

- Python ≥3.8，stdlib only；平台预装 pdf-text-tool/office-renderer/ocr-tool/latex-compiler/browser-automation 等按 `environment/resources.yaml` 调用。
- 引擎 `bohrium-weekly-common@7.2`，无构建，放入 `/skills/weekly-skill/` 直接运行。

## 8. 组织方输入画像（必须遵守）

## 当前独立运行适配
原三个案例、九个中文问题及16项公开评分不变。官方DSH预算统一为64次模型请求、单次16384输出token、900秒。主产物路径适配为 /app/output/result.json，原答案字段和完整原文保留。旧32次/8192/600秒结果不是新预算下的固定对照；新匹配白板及实际验收尚待，不宣称已经测通。

Goal: answer every supplied question with source evidence. Constraints: use only the supplied scientific paragraphs and preserve uncertainty. Completion criteria: the original answer schema and actual /app/output/result.json. Provided resources are listed in environment/resources.yaml.


## 9. 实例证据

- `.selftest/regress.py` 全绿；profile9 三题 rubrics 覆盖 100/100/100。
- datacase 代码结果与交付统计完全一致；experimentcase 15 项、auditcase 14 项断言全绿。
- 所有数字只证明本地方法与对照差异，不冒充平台正式评分。
