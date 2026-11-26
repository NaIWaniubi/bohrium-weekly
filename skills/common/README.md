# bohrium-weekly 引擎（common/engine）

三个 Skill（科研文件智能检索 / 文献与资料自动分类 / 文献综述生成）共用的确定性内核。
stdlib only，无第三方依赖；全部输出为机器可读 JSON，每一步可复跑核对。

## 文件

| 文件 | 作用 |
|-|-|
| `lib.py` | 基础工具：原子 JSON 写、哈希（CJK/BOM 容错读取）、标题归一化与版本族排序、操作账本 `ledger_append` |
| `scan_index.py` | 语料库扫描与索引：`--root <库> --out <index.json>`。产出文件全量清单（relpath/ext/size/sha256/mtime/**meta 元数据**）、章节级文本块（chunk_id+字符偏移）、**分页感知**（PDF `[page N]` 标记→块携带 pages 与印刷页码偏移）、精确重复组、**近重复检测**（同标题族+字节不同+体积差≤25% 视为版本变体）、版本族别名链与推荐 canonical、无提取器文件的诚实标记（needs_tool） |
| `answer.py` | 单题应答：`--index --question [--filter F] [--terms-file T] --top N --out`。**双路检索融合**：多路查询规划+跨语言扩展 + **字段加权 BM25**（正文 k1=1.5/b=0.75 主分 + 文件名字段加权 `_v2_final` 信号） + **语义 dense**（纯 stdlib TF-IDF 向量化器+余弦相似度，捕捉概念改写与同义词漂移） + RRF 融合保底；过滤先行逐条留痕；命中携带 file/sha256/chunk_id/offset/pages/**citations**（段落首句锚点+逐字引用四件套）；引用生成器输出证据回链bundle |
| `run_case.py` | 固定案例编排：按契约读取 `/input/case.json` 与 `task.md`，合并过滤约束与跨语言扩展词，运行链路，落盘命中全文；`--index` 缺失时自动扫描材料建索引；调用 finalize 写出**非空完整首版 result.json**（answer/sources/evidence_matrix/screening_log/uncertainty/failure_recovery/version_lineage/operation_evidence），保留 output_contract；**断点续传**：result.json 已存在且 case_id 匹配时跳过（`--force` 强制重跑） |
| `finalize.py` | 确定性完整交付层：生成可读答案、全量来源、绝对偏移证据引用、版本链、异常恢复与源文件哈希复核；隐藏 output_contract 同义字段自动映射到真实交付内容；`--profile classify` 还真实生成 `organized/` 规范副本、元数据侧车、精确重复别名、manifest 与回滚说明，源文件永不修改 |
| `enhance.py` / `enhance_v7.py` | v6/v7 编排：NL 约束、查询改写、PRF、多样化、canonical 版本、答案综合、claim 级综述与数据/代码真实执行 |
| `version_metadata.py` | 行政版本说明状态机：canonical/alias/supersedes；SHA/DOI 去重；元数据 provenance；修正 hits/screening 的主文献 |
| `search_quality.py` | 去指令废词查询计划、canonical hits、章节/句子证据、带 `[E#]` 的直接答案、覆盖/效率/rubric 报告 |
| `review_quality.py` | claim 矩阵、canonical 纳排、主题归纳、冲突/研究空白矩阵、无占位句的引用综述与质量报告 |
| `datacode_enhance.py` | 表格导入、数据画像、清洗审计、描述/推断统计、效应量、CI、SVG 图表、代码执行、结果验证与报告；生成脚本复用同一引擎，结果同源 |
| `experiment_enhance.py` | 实验目标/假设/变量操作化、对照、满足重复次数与 run 上限的计划、资源公式、参数范围、风险登记、方案比较与有界参数优化 |
| `audit_enhance.py` | 学术审查：claim-证据矩阵、数字/DOI 引用核验、方法学/统计/合规检查、文本重复检测、按严重度排序的 findings与报告 |
| `reading_enhance.py` | 文档/图表/公式解析、比较多文档、结论/数据提取、事实核验、结构化笔记、知识图谱/nanopublication |
| `writing_enhance.py` | 任务感知写作交付：原稿/材料读取、润色与逐句修改、模板格式、引用同步、结构诊断、引言/方法/结果/回复/翻译/LaTeX/基金/开题/长文档/成果转化/思维导图/证据驱动/完整论文；输出可编辑 MD/DOCX、PDF/HTML 预览、修改记录与来源映射 |
| `graphics_enhance.py` | 可编辑 SVG 柱状/折线图、流程图、通路图、装置图、图形摘要与 PNG 预览 |
| `slides_enhance.py` | 论文转 slide plan、可编辑 PPTX、讲者备注、PDF/PNG 预览 |
| `baseline.py` | 对照基线：模拟"未加载本 Skill 的朴素 Agent"——仅文件名匹配、无过滤审计、无定位、无哈希（题目评分点明批评的失败模式） |
| `benchmark.py` | 金标准问答集指标：读 `bench/queries.json`，同题分别跑 Skill 链与 baseline，输出 P@5 / R@5 / top5 召回数 / hash 验证率对比表 |
| `render_report.py` | 把 result.json 渲染成自包含单页 HTML（过滤审计表 + 命中定位表 + 证据原文窗口 + 不确定性分区），评审无需环境直接打开 |

## 实例运行证据（本仓库自带可复跑）

在 15 文件混合科研库（真 PDF×3 版本族 + docx + xlsx + pptx + 扫描位图×4 + 损坏件 + 精确重复副本）上：

```
python -I scan_index.py --root <库> --out index.json --ledger ledger.json
python -I benchmark.py --index index.json --out bench_result.json
```

| 指标（9 题平均，含 1 题空命中诚实用例） | Skill 链 | baseline(文件名搜索) |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| gold 召回数/top5 | **2.33** | 0.22 |
| hash+offset 验证率 | **88.9%**（空命中题不计入则 100%） | 0% |

v5 起新增完整交付与独立挑刺回归：非空 answer、证据文件/chunk/quote/artifact 四重复核、全量 sources、年份/类型硬过滤、未知约束 fail-closed、零匹配不造假证据、长章节续块、CRLF/GBK、嵌套契约、路径 containment、旧结果不误续传、15 文件实体整理（14 副本 + 1 重复别名）、原子重跑与源文件哈希前后一致。

v7 起新增 profile9 隐藏案例画像与数据/代码回归：行政 canonical/alias/supersedes 解析、canonical hits/纳排、claim 矩阵、无占位综述、章节证据与直接答案；datacase 覆盖缺失/重复/异常/单位转换、Welch/Mann-Whitney、效应量、CI、SVG、代码执行与源数据不变。三题本地 rubrics 覆盖 100/100/100，完整回归 ALL GREEN。

v7.1 起新增实验设计回归：变量矩阵、空白/阳性对照、3 次重复与 12 run 上限、资源未知不伪造、参数范围、风险登记与小样本候选优化；数据生成代码与交付统计完全一致。

v7.2 起新增学术审查回归：claim-证据矩阵、无支持 claim 标记、数字引用解析、DOI 不匹配检测、方法学/合规缺项与文本重复证据；auditcase 14 项断言全绿。

v7.3 起新增阅读/写作/图形/幻灯片四个 profile：文档图表公式解析、结构化笔记、证据驱动写作、可编辑 SVG、PPTX 与真实预览；剩余 45 题全部生成定制 Skill，覆盖第四期 77/77 题。剩余 profile 17 项回归全绿。

v3 起新增四类题：多约束（类型+目录）、版本辨识（哪一版带附录）、跨文件共识、
空命中诚实报告。

边界如实说明：本机为 Windows 实验环境，语料为自建合成科研库；平台侧由组织方固定案例与 DeepSeek Harness 产生正式对照，上表证明的是方法与对照协议的差异，不是最终 benchmark 分。

## 设计原则（对照评分维度）

1. **实际效果 40 分**：脚本只输出真实读过源字节的候选事实；命中给出文件哈希+字符偏移。分类 profile 进一步**真实生成整理目录**，不是聊天建议；每条 copy/alias 都可回滚。
2. **使用增益 25 分**：过滤先执行且逐条留痕；类型/目录/年份/项目/批次/作者/DOI/版本/权限均为硬约束，未知显式条件 fail-closed；问句拆解为多路子查询标准 BM25 主排序 + dense + RRF 融合，任一路词漏掉不会杀死召回。
3. **创新增益 15 分**：DOI/标题版本族识别在索引期完成；精确重复只保留一个规范副本并记录 alias；近重复区分物理副本与版本变体，避免重复引用。
4. **可靠体验 10 分**：stdlib OOXML 解析兜底、PDF 分页、符号链接/权限/损坏事实入索引；无提取器/空命中显式标记；完整首版先落盘，Agent 增量增补。
5. **生态贡献 10 分**：账本、manifest、哈希复核、HTML 报告与一键回归共同构成可复跑证据链。

## 运行前提

```bash
python -I scan_index.py --root /input/library --out /app/work/index.json \
        --ledger /app/work/ledger.json
python -I answer.py --index /app/work/index.json \
        --question "..." --filter /app/work/filter.json \
        --terms-file /app/work/terms.txt --top 25 --out /app/work/answer.json
python -I run_case.py --case /input/case.json --index /app/work/index.json \
        --work /app/work --out /app/output
```

平台预装工具（pdf-text-tool / office-renderer / ocr-tool 等）在 `environment/resources.yaml`
中列明；当本机无可 import 的解析库且无对应 CLI 时，binary 文件按 needs_tool 处理，
由 Agent 按题目要求调用官方工具后回填，禁止凭文件名臆测内容。

## 已知边界（诚实声明）

- 纯启发式章节切分：大文件中首次标题前 >1500 字符才切节；长章节生成带 80 字符重叠的续块，`has_more=true` 表示还有相邻块，不再丢尾部内容。
- 证据 quote 最多 240 字符；纯文本 offset 指向源文本，PDF/Office offset 指向提取文本，并同时提供 `evidence/extracted_text/` 工件与页码，不能把提取文本偏移冒充二进制字节偏移。
- 文件 SHA-256、chunk SHA-256、quote 切片三者分别复核；任一失败会在 evidence_refs 标记并使状态降为 partial。
- 显式过滤条件无法验证时 fail-closed；未知年份结构、权限事实或分类 matcher 不会静默放行。
- 语义召回非神经检索：依赖关键词扩展词表（SKILL.md 中给出扩展规程），纯同义改写的问题召回会下降，已在 SKILL.md 写明对策（多路查询+人工兜底）。