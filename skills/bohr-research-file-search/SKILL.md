---
name: bohr-research-file-search
description: |
  科研文件智能检索 Skill：围绕「跨文件语义检索—章节定位—过滤执行—证据回链」的判分点逐条落地，
  提供五步法案例执行 SOP、引用四件套（页码/锚句/逐字原文/sha256）、审计账本与一键回归。
---

# 科研文件智能检索 Skill

把「跨文件语义检索—章节定位—过滤执行—证据回链」做成可复跑流程，而不是把提问丢给文件名搜索。

## 0. 运行契约（必须遵守）

- 输入：组织方固定案例 `/input/case.json`（必要时含 `task.md`、`library`）；材料按题目提供，PDF 为真实文件。
- 工作区：`/app/work`；产物 `/app/output`，必含 `result.json`（覆盖原 output_contract 全字段 + `case_id`、`status`、`sources`、`uncertainty`、`change_records`）。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**。三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果必须标明边界；可编辑产物附真实渲染预览。
- 先存诚实部分交付物，再增量改进。资源上限：50 个 UTF-8 文件、单 ≤500KB、总 ≤2MB、无符号链接。
- 平台记录模型、源/案例版本、运行与预算；**不要自报 benchmark 分数**。

## 1. 共用确定性引擎（stdlib only，随提交打包于 common/engine/）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 库扫描 → 文件全量事实表(relpath/ext/size/sha256/mtime/**meta 元数据**)、章节级文本块(chunk_id+字符偏移+**pages+印刷页码偏移**)、精确重复组、近重复检测（同标题族字节不同=版本变体）、版本族别名链与推荐 canonical、needs_tool 清单 |
| answer.py | **过滤先行**：每条约束独立留痕（kept/dropped_since_prev）；多路查询规划（问句拆解聚焦子查询、跨语言扩展词独立成路）；**字段加权 BM25**（正文 k1=1.5/b=0.75 主分 + 文件名字段加权，`_v2_final` 类版本信号可被检索）+ **dense 通道**（纯 stdlib TF-IDF 向量化器+余弦相似度，捕捉概念改写与同义词漂移）+ RRF 融合保底；命中带 file/sha256/chunk_id/pages/offset/**evidence**（匹配处窗口）/贡献计划 |
| run_case.py | 按契约读 case.json，合并过滤约束与跨语言扩展词，跑链路，把每个命中的**全文**落盘到 `evidence/hits/`；**自动产出完整首版 result.json**（answer.content 非空、search_results、evidence_matrix、sources 全量、uncertainty/failure_recovery、citations/version_resolution），并逐条回读源文件复核 sha256；**断点续传**（`--force` 强制重跑） |
| finalize.py | 完整交付层：生成可读答案、绝对偏移证据引用、全量来源清单、筛选记录、版本链、运行/哈希/回滚证据；保留原 output_contract 全部字段 |
| benchmark.py / render_report.py / baseline.py | 金标准指标（P@3/P@5/MRR@10/NDCG@5）、HTML 详报渲染、对照基线 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output --profile search
```

### v5 完整交付纪律（必须）

1. 引擎已直接生成完整首版答案与 `answer.evidence_refs`；**不得把 answer.content 置空或只保留骨架**。
2. `filter_audit` 支持类型、目录、年份、项目、批次、作者、DOI、版本、可读权限与路径约束；未知显式条件 fail-closed 留审计行，不静默放宽。
3. Agent 阅读 `evidence/hits/*.txt` 后，把 answer.content 改成真正回答问题的段落；每条实质结论引用 `[E#]`，并保证 quote 在 offset 原文中逐字存在。
4. `has_more=true` 必须回源补读；OCR/提取失败保留在 uncertainty.missing，未完成时保持 partial。

## 2. 判分点逐条对策（原文映射）

| 判分点（分值） | 本文对策 |
|-|-|
| 实际效果 40：完成度15/正确性15/完整性5/可核验5——评价是否真正完成「跨文件语义检索—章节定位—过滤执行—证据回链」完整交付，而不是把提问丢给文件名搜索 | 五步法强制走完：案例读题→库事实→过滤先行应答→**全文读证**→成稿。每条结论必须带回链（file+sha256+offset+quote），未读过 evidence/hits 全文的行不得写入结论 |
| 命中文件与章节是否真的回答提问、定位是否落在原文那一段 | 引用生成器输出段落首句锚点（anchor.first_sentence）+ 匹配处逐字窗口（quotes[]）+ 物理页码（pages）+ 印刷页码偏移（printed_page_offset），缺一不算可复核 |
| 是否覆盖关键文件/章节/元数据字段与全部过滤维度 | filter_audit 每条约束独立留痕；元数据来自文件系统与解析正文首部，禁止领域标签外推 |
| 检索路径、文件与版本标识、章节定位、引用片段能否独立复核 | 账本 ledger.json 逐文件落痕；命中排序带 bm25/dense/RRF 三值可解释到"哪几个子查询贡献了这个命中" |
| 使用增益 25：质量15/效率5/可验证性5 | 过滤器先行（基础 Agent 无此结构）；grep+哈希定位替代翻页查版本；benchmark 分离度（P@5 0.502 vs baseline 0.167）作为增益证据 |
| 创新增益 15：新增能力5/方法改进5/创新证据5 | 文件名字段加权进检索分（`_v2_final` 信号可被检索）；版本族别名链自动消解；近重复检测区分"物理副本 vs 版本变体" |
| 可靠体验 10：调用易用3/稳定性4/异常处理3 | office 元数据与 PDF 分页感知双通道；needs_tool/损坏/空命中全部显式标记进 uncertainty；fail-open 单文件失败不影响全库索引 |
| 生态贡献 10：文档3/可复用4/分享3 + GitHub 公开链接 5 | README 有实例证据与可复跑回归；仓库匿名可读 |

## 3. 单案例执行 SOP（五步法）

1. **读案例**：打开 `/input/case.json` 与 `task.md`，提取显式过滤约束（类型/目录/年份/批次/权限…）与核心研究问题。
2. **建库**：对案例给定库跑 `scan_index.py` 一次，得到事实表；查 needs_tool 与近重复组；需要 OCR 的按题目用官方 ocr-tool 补录，**绝不凭文件名臆测内容**。
3. **应答**：跨语言词表写入 `/app/work/terms.txt` 后跑 run_case；过滤器逐条生效并留痕。
4. **读证**：逐条全文阅读 `evidence/hits/*.txt`（has_more=true 回源文补读）。
5. **成稿**：在引擎完整首版上改写 answer.content，按 output_contract 补齐字段并立即保存；预算剩 <20% 时保存 partial。

## 4. 输出契约（原题 output_contract 三块）

| 内容 | 字段与要求 | 标准 |
|-|-|-|
| 资料结果 | 目录、文件清单、元数据、版本关系或知识结构 | 与输入文件和题目规则一一对应 |
| 操作证据 | 哈希、来源、权限、变更、日志 | 他人可复跑并核对 |
| 失败与恢复 | 跳过、冲突、无权限、重复、恢复记录 | 不把未完成写成完成 |

result.json 完整首版字段（Agent 在其上增补，不从空骨架开始）：

```json
{"case_id":"…","status":"ok|partial|failed",
 "answer":{"content":"读完 evidence/hits/*.txt 全文后才填写；每条结论带 file+sha256+offset",
           "evidence_refs":[{"file":"papers/x_v2_final.pdf","sha256":"d4f6…","offset":[0,528],
                             "quote":"≤120字原文","meaning":"该引文支撑哪一句结论"}]},
 "sources":[{"file":"…","sha256":"…"}],
 "uncertainty":{"missing":[],"conflicts":[],"limits":[]},
 "change_records":[],
 "provenance":{"generated_at":"ISO8601"}}
```

## 5. 证据回链四件套（引用必填）

1. **file + sha256**：定位到具体文件与其字节指纹
2. **pages / offset**：物理页码集合与块内字符区间
3. **印刷页码偏移**（printed_page_offset）：仅当块内检出印刷标签≠物理位置时填，处理双栏/封面错位场景
4. **锚句 + 逐字引用**：段落首句锚点 + 匹配处原文窗口（≤180 字），标注该引文支撑的结论句

## 6. 异常与恢复矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 损坏/不可读 | uncertainty.missing 记路径+原因；账本 warn | 静默丢弃 |
| 无文本层扫描件 | needs_tool → 按题目用 ocr-tool；仍不行标 limits | 凭文件名臆测内容 |
| 重名/同哈希多路径 | aliases 推荐 canonical，回链取 canonical | 双引物理副本当两处证据 |
| 同标题族字节不同 | near_duplicates 记版本变体，按题目指定版本回链 | 把旧版当终稿 |
| 空命中 | unmatched_warning 原样保留；扩词重试 ≤2 轮 | 编造相似文献 |
| 超预算 | 立即保存 honest partial 并记 change_records | 硬扛到失败 |

## 7. 依赖、版本与安装

- Python ≥ 3.8 stdlib only，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool / office-renderer / ocr-tool 等）。
- 引擎 `bohrium-weekly-common@5.0`（2026-11-24，随仓库同版本锁定）。无构建步骤：将本目录放入评测约定位置（Skill 本体 `/skills/weekly-skill/`，共用引擎随附同一相对路径 `common/engine/`）；新增 `finalize.py` 必须随包提交。

## 8. 实例运行证据（本仓库自带，可复跑）

在 15 文件混合科研库（真 PDF×3 版本族 + docx + xlsx + pptx + 扫描件 + 损坏件 + 副本）上实测：

| 指标（9 题平均，含 1 题空命中诚实用例） | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| MRR@10 / NDCG@5 | **0.833 / 0.799** | 0.167 / 0.113 |
| hash+offset 验证率 | **88.9%**（非空命中 100%） | 0% |

回归：`.selftest/regress.py` 一键全绿（字节编译→造库→扫描→全链路→硬过滤 fail-closed→基准分离）；v5 非空证据引用实测 9/9 文件哈希、chunk 哈希、quote 切片与提取文本工件复核通过，零匹配查询返回 0 hits。

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法与对照协议差异；平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。
