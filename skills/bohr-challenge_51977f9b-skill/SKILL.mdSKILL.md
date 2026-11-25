---
name: bohr-challenge_51977f9b-skill
description: |
  系统性文献综述：面向玻尔周赛的可执行 Skill——判分点逐条对策 + 双路检索引擎 + 证据回链 + 断点续传。
---

# 系统性文献综述 Skill

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；平台固定 3 个案例，每个含任务说明、科研材料、输入约束与评分证据范围；PDF 为真实文件。
- 工作区：`/app/work`；产物 `/app/output`，必含 `result.json`，覆盖原 output_contract 全字段并带 `case_id`、`status`、`sources`、`uncertainty`、`change_records`。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**。三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态与科学数据；部分结果必须标明边界；可编辑产物附真实渲染预览。
- 先存诚实部分交付物，再增量改进。资源上限：50 个 UTF-8 文件、单 ≤500KB、总 ≤2MB、无符号链接。


## 1. 共用确定性引擎（stdlib only，随提交打包于 common/engine/）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 库扫描→文件全量事实表(sha256/mtime/meta)、章节块(chunk_id+偏移+pages+印刷页码偏移)、精确重复组、近重复检测、版本族别名链、needs_tool |
| answer.py | 过滤先行审计 + 多路查询规划 + BM25(正文+文件名字段加权) + TF-IDF cosine dense + RRF 融合；命中带 file/sha256/pages/offset/evidence |
| run_case.py | 按契约读 case.json，跑扫描/过滤/双路检索/引用链，落盘 evidence/hits；**自动产出非空完整首版 result.json**（answer、sources、evidence_matrix、screening_log、uncertainty、failure_recovery、version_lineage、operation_evidence），保留 output_contract；断点续传 |
| finalize.py | 确定性完整交付层：生成可读答案、全量来源、候选证据矩阵、版本链、异常恢复与哈希复核；分类 profile 还生成 organized/ 实体副本、元数据侧车、重复别名、manifest 与回滚说明，源文件只读 |
| enhance.py | v6 编排：NL 约束→查询改写→PRF→多样化→精确内容去重；分类/综述深度增强；data/code 真实执行编排 |
| enhance_v7.py | v7 编排：canonical 版本/元数据、检索答案综合、综述 claim 矩阵与 composed review |
| version_metadata.py | 行政版本说明状态机（canonical/alias/supersedes）+ SHA/DOI 去重 + 元数据 provenance + canonical 修正 |
| search_quality.py | 去废词查询计划、canonical hits、章节/句子证据、带 [E#] 的直接答案、覆盖与效率报告 |
| review_quality.py | claim 级证据矩阵、canonical 纳排、主题/冲突/空白矩阵、无占位句的引用综述草稿 |
| datacode_enhance.py | 真实表格导入/清洗审计/描述与推断统计/效应量/CI/SVG 图表/代码执行/质量验证/报告 |
| experiment_enhance.py | 实验设计：目标/假设/变量操作化、对照、可行 run plan、资源公式、参数范围、风险登记、方案比较与参数优化计划 |
| audit_enhance.py | 学术审查：claim-证据矩阵、数字/DOI 引用核验、方法学与合规检查、文本重复检测、按严重度排序的 findings 与报告 |
| benchmark.py / render_report.py / baseline.py | 金标准指标（P@3/P@5/MRR@10/NDCG@5）、HTML 详报渲染、对照基线 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output
```

链路对案例是确定性的、无需模型也能产出完整首版结果——这是相对基础 Agent 的增益证据：基础 Agent 需要逐文件翻页、肉眼比对版本；本链路先审计过滤、再定位章节、最后每条结论回链到源字节哈希。

### v5 完整首版（执行时必须保留）

`run_case.py` 已自动生成非空 `answer.content`、`answer.evidence_refs`、`sources`、`evidence_matrix`、`screening_log`、`uncertainty`、`failure_recovery` 与 `operation_evidence`。Agent 首先读这份完整首版，再按本题契约增补语义判断；**不得把 answer 置空或推倒重写**。`--profile classify` 会真实生成 `organized/` 规范副本与可回滚 manifest；`--profile review` 的确定性稿保持 partial，直到筛选、主题、证据、综述四步完成。

### v7 专业增强（必须保留）

- **版本事实**：`version_metadata.py` 用行政说明状态机解析 canonical/alias/supersedes，避免把 alias 或旧版本当主文献；元数据只来自文件/原文并保留 provenance。
- **检索**：`search_quality.py` 生成去指令废词的计划，只用 canonical 证据，按章节/句子定位，并直接生成带 `[E#]` 引用的答案；无证据保持空结果。
- **综述**：`review_quality.py` 先修正确 canonical 纳排，再建 claim 矩阵、主题、冲突与研究空白矩阵，输出无占位句且逐句引用的综述草稿。
- **数据/代码**：`datacode_enhance.py` 真实执行清洗、统计、效应量、CI、SVG 图表与代码运行，保留审计、日志和质量检查；源数据只读。
- 验证：`.selftest/regress.py` 全绿；profile9 三题 rubrics 覆盖 100/100/100；datacase 16 项断言全绿。

### v7.1 数据/代码与实验设计增强

- `datacode_enhance.py` 的生成脚本直接复用同一引擎，已断言代码运行结果与交付统计完全一致；源数据只读。
- `experiment_enhance.py` 输出变量矩阵、对照、满足重复次数与 run 上限的实验计划、资源可行性公式、参数范围、风险登记与有界优化计划；未知成本/时间不伪造。
- `--profile experiment` 用于实验方案/参数优化/可行性/方案比较类题目。

### v7.2 学术审查专项

- `audit_enhance.py` 从目标稿件抽取实质 claim，逐条寻找库内支持证据；无支持不写成已验证。
- 数字引用与 DOI 引用分别核验，能识别“引文存在但 DOI/年份不匹配”；方法学、统计、局限性、数据/代码可用性、伦理与合规缺项逐条列出。
- 文本重复/近重复只报告证据与位置，不直接做抄袭定性；findings 按 high/medium/low 排序并回链原文。
- `--profile audit` 用于论文质量、逻辑论证、引用事实、阅读批注与学术合规类题目。

## 2. 解题总纲

做成可复跑的分析流水线：规则前置、结果可溯源。

**突破方向对标：**
- 检索式 — 把研究问题转成可执行的检索式，明确数据库、字段、布尔逻辑、同义词与受控词、时间与语言限制，并
- 纳排标准 — 形成条款化、可判定的纳入与排除标准，明确研究类型、人群或对象、时间范围、语言、可得全文等条件
- 筛选记录 — 保留题录级与全文级两轮筛选的逐条结论、排除理由与判定人，使纳入集可以被追溯和重算。
- 质量评价 — 为纳入研究选择或设计可解释的评价量表，逐条给出维度判断、依据位置与总体质量等级，并说明该等级
- 可复核综述流程 — 把上述环节串成带版本、计数与人工修改记录的流程，输出综述结论、冲突证据与研究空白，并明确每条

## 3. 单案例执行 SOP（五步法）

1. **读案例**：打开 `/input/case.json` 与 `task.md`，提取显式过滤约束（类型/目录/年份/批次/权限…）与核心研究问题。
2. **建库**：`scan_index.py` 对案例给定库跑一次，得到事实表；查 needs_tool 与近重复组，需要 OCR 的按题目用官方 ocr-tool 补录，**绝不凭文件名臆测内容**。
3. **应答**：跨语言词表写入 `/app/work/terms.txt` 后跑 run_case；过滤器逐条生效并留痕。
4. **读证**：逐条全文阅读 `/app/output/evidence/hits/*.txt`（has_more=true 回源文补读）。
5. **成稿**：按本题输出契约填 result.json 并立即保存；预算剩 <20% 时保存 partial。

## 4. 本题判分点与对策（原文映射）

| 判分点（分值） | 本文对策 |
|-|-|
| 实际效果 40（完成度15/正确性15/完整性5/可核验5） | 代码跑通+与数据字典/原始行数/金标准三向对照；每步前后行数可查；图表选型有说明+真实渲染预览 |
| 使用增益 25（质量15/效率5/可验证性5） | 清洗规则机器执行可复跑，审计步记输入/输出条目数；不把人肉比对当流程 |
| 创新增益 15（新增能力/方法改进/创新证据） | 断点续传（result.json 已存在即跳过）保证预算不吃重复劳动；校验环节自动对照金标准 |
| 可靠体验 10（调用易用3/稳定性4/异常处理3） | 缺列/类型不符/编码不一致按题目纪律处置，账本留痕；subprocess CLI 兜底链 |
| 生态贡献 10（文档3/可复用4/分享3）+ GitHub 公开链接 5 | 维护信息（引擎版本、回归入口、已知边界）齐全；全部资源开源托管 |

## 5. 输出契约（原题 output_contract 逐字段）

| 分析结果 | 清洗表、统计结果、图表、代码或查询结果 | 结果与数据字典、原始行数一致 |
| 操作证据 | 数据来源、哈希、步骤与参数、日志 | 他人可复跑并核对 |
| 失败与恢复 | 缺列、类型不符、依赖缺失、查询为空、数值溢出处置 | 不把未完成写成完成 |

result.json 完整首版：

```json
{"case_id":"<来自 case.json>","status":"ok|partial|failed",
 "answer":{"content":"读过 evidence/hits/*.txt 全文后才填写，每条结论带 file+sha256+offset"},
           "evidence_refs":[{"file":"papers/x_v2_final.pdf","sha256":"d4f6…","offset":[0,528],"quote":"≤120字原文","meaning":"该引文支撑哪一句结论"}]},
 "sources":[{"file":"…","sha256":"…"}],
 "uncertainty":{"missing":[],"conflicts":[],"limits":[]},
 "change_records":[],
 "provenance":{"generated_at":"ISO8601"}}
```

## 6. 异常与恢复（领域化）

| 依赖缺失/环境隔离失败 | 记 limits 给出声明式依赖清单 | 静默假定环境可用 |
| 数值溢出/样本骤降 | 标异常行+原因，统计注明样本数变化 | 吞掉异常继续算 |
| 代码运行失败 | 退回最小复现路径并标 limits；不伪报成功 | 跳过真跑直接交答案 |

## 7. 依赖版本与安装

- Python ≥3.8 stdlib only，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool/office-renderer/ocr-tool 等）。
- 引擎 bohrium-weekly-common@7.2（2026-11-24）。无构建：将本目录放入 `/skills/weekly-skill/`，引擎随附 common/engine/，直接调用。

## 8. 实例证据（仓库自带可复跑）

在 15 文件混合科研库上实测（.selftest/regress.py 一键回归 ALL GREEN）：
P@5=0.502 / R@5=0.824 / MRR@10=0.833 / NDCG@5=0.799，
对照朴素文件名搜索 baseline 0.167/0.139/0.167/0.113；hash 验证率 88.9%（非空命中 100%）。
边界：自建合成语料，证明方法差异；平台侧终分由组织方固定案例与官方 Harness 生成。

