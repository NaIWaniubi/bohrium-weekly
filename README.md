# bohrium-weekly — Bohrium AI 产品周赛 Skill 作品集

玻尔（Bohrium）AI 产品周赛的参赛 Skill 仓库。每个目录是一份**独立、自包含、可直接复跑**
的 Skill 提交；共用的确定性内核在 `common/engine/`（stdlib only，零第三方依赖）。

## 提交清单（对应题目 slug）

| 目录 | 题目（周赛第四期） | slug | 提交记录 |
|-|-|-|-|
| [`bohr-research-file-search`](skills/bohr-research-file-search/) | 科研文件智能检索：科研资料管理——整理文件，也保留版本关系 | challenge-139745c7 | [查看](skills/bohr-research-file-search/GITHUB_URL.txt) |
| [`bohr-literature-classify`](skills/bohr-literature-classify/) | 文献与资料自动分类：科研资料管理——整理文件，也保留版本关系 | challenge-666fefb0 | [查看](skills/bohr-literature-classify/GITHUB_URL.txt) |
| [`bohr-litreview-gen`](skills/bohr-litreview-gen/) | 文献综述生成：文献综述——综合证据，也说明边界 | challenge-e97137c9 | [查看](skills/bohr-litreview-gen/GITHUB_URL.txt) |

每份提交目录内包含：`SKILL.md`（name/description frontmatter + 完整使用与方法说明 +
异常处理矩阵 + 最小调用示例）、依赖的引擎脚本（软链接到 common/engine 或拷贝副本，
按评测约定以 `skills/weekly-skill/...` 路径引用）、`GITHUB_URL.txt`。

| [`bohr-challenge-0467f5db-skill`](skills/bohr-challenge-0467f5db-skill/) | 检索覆盖率与遗漏分析（选题 4） | challenge-0467f5db | [查看](skills/bohr-challenge-0467f5db-skill/GITHUB_URL.txt) |
| [`bohr-challenge-5b90f5e9-skill`](skills/bohr-challenge-5b90f5e9-skill/) | 系统性文献检索与纳排筛选（选题 5） | challenge-5b90f5e9 | [查看](skills/bohr-challenge-5b90f5e9-skill/GITHUB_URL.txt) |
| [`bohr-challenge-5c918ff0-skill`](skills/bohr-challenge-5c918ff0-skill/) | 多数据库联合检索（选题 6） | challenge-5c918ff0 | [查看](skills/bohr-challenge-5c918ff0-skill/GITHUB_URL.txt) |
| [`bohr-challenge-60a8202d-skill`](skills/bohr-challenge-60a8202d-skill/) | 引文追踪与关联文献发现（选题 7） | challenge-60a8202d | [查看](skills/bohr-challenge-60a8202d-skill/GITHUB_URL.txt) |
| [`bohr-challenge-e7598a44-skill`](skills/bohr-challenge-e7598a44-skill/) | 科研问题驱动检索（选题 8） | challenge-e7598a44 | [查看](skills/bohr-challenge-e7598a44-skill/GITHUB_URL.txt) |
| [`bohr-agent-f8fb8bda-skill`](skills/bohr-agent-f8fb8bda-skill/) | 深度研究 Agent（选题 9） | agent-f8fb8bda | [查看](skills/bohr-agent-f8fb8bda-skill/GITHUB_URL.txt) |
| [`bohr-agent-6aefb2fd-skill`](skills/bohr-agent-6aefb2fd-skill/) | 多步骤数据分析 Agent（选题 10） | agent-6aefb2fd | [查看](skills/bohr-agent-6aefb2fd-skill/GITHUB_URL.txt) |
| [`bohr-challenge-9ec929fe-skill`](skills/bohr-challenge-9ec929fe-skill/) | 科研代码生成与执行（选题 11） | challenge-9ec929fe | [查看](skills/bohr-challenge-9ec929fe-skill/GITHUB_URL.txt) |

## 共用引擎 `skills/common/engine/`

- `lib.py` — 原子 JSON、哈希、CJK 分词、标题归一化、版本族排序、操作账本
- `scan_index.py` — 库扫描 → 文件全量事实 + 章节块 + 重复组 + 版本族 + needs_tool
- `answer.py` — 多路查询规划 + 标准 BM25 主排序 + RRF 融合保底 + 过滤先行审计 + 证据窗口（file/sha256/chunk_id/offset）
- `run_case.py` — 固定案例编排：按契约读 `/input/case.json`，落盘命中全文，
  调用 finalize 写出**非空完整首版 result.json**（answer/sources/evidence_matrix/version_lineage/operation_evidence）
- `finalize.py` — 完整交付层：可读答案、全量来源、证据哈希复核、版本链、异常恢复；分类 profile 真实生成 `organized/` 规范副本、元数据侧车、重复别名、manifest 与回滚说明，源文件只读
- `baseline.py` / `benchmark.py` / `render_report.py` — 对照基线、金标准指标（P@5/R@5/hash 验证率）、HTML 报告渲染；金标准问答集见 `skills/common/bench/queries.json`

详细文档：[`skills/common/README.md`](skills/common/README.md)。

## 设计要点（对照评审维度）

1. **可核验性**：所有结论锚定到源字节哈希与字符偏移；账本 ledger.json 留痕。
2. **过滤约束执行**：每条约束独立 audit 行，先执行后匹配；年份/项目/版本/权限等硬约束不支持时 fail-closed。
3. **真实整理**：分类题生成 organized/ 实体副本、规范命名、元数据侧车、重复别名与 manifest；源文件永不修改。
4. **诚实边界**：无提取器/损坏/空命中显式标记；未读全文不写实质结论；不越界主张。
5. **预算纪律**：64 请求 / 900s / 16k token 内先落完整首版，再多节增量保存。

## Changelog

- **v7（2026-11-24）**：新增 `version_metadata.py`（行政版本说明状态机、canonical/alias/supersedes、元数据 provenance）、`search_quality.py`（去废词计划、canonical hits、章节/句子证据、带引用答案）、`review_quality.py`（claim 矩阵、canonical 纳排、主题/冲突/空白矩阵、无占位综述）、`datacode_enhance.py`（真实清洗、统计、效应量、CI、SVG、代码执行与验证）。`enhance_v7.py` 统一编排；profile9 三题本地 rubrics 覆盖 100/100/100，datacase 16 项断言全绿，完整回归 ALL GREEN。修复提交包漏增强模块问题，并增加隔离 import 与端到端 preflight。
- **v6（2026-11-24）**：三赛道增强层——分类行政说明/标签/权限/前后树/幂等，检索 NL 约束/PRF/共识，综述筛选/主题/证据强度/PRISMA/草稿。
- **v5（2026-11-24）**：新增 `finalize.py` 完整交付层——`answer.content` 永不为空，自动生成全量 sources、证据矩阵、筛选记录、版本链、异常恢复与哈希复核；分类 profile **真实生成 organized/ 规范副本、元数据侧车、精确重复别名、manifest 与回滚说明**，源文件零修改。扫描器新增 stdlib OOXML 兜底、DOI 身份、符号链接/权限/状态；过滤器覆盖年份/项目/批次/作者/DOI/版本/权限，未知约束 fail-closed。独立挑刺发现的 17 项问题已修复并纳入 `regress_hardening.py`：零匹配假证据、长章节续块、CRLF 偏移、GBK、深合并契约、文件/chunk/quote/artifact 四重复核、路径 containment、resume 完整性、原子替换、UTF-8 文件名长度等。回归新增实体整理与源哈希不变断言，15 文件生成 14 副本 + 1 重复别名。
- **v4（2026-11-22）**：双路检索融合——增加**语义 dense 通道**（纯 stdlib TF-IDF 向量化器+余弦相似度，捕捉概念改写与同义词漂移），
  与 BM25 双路加权融合（0.45·bm25_main + 0.15·bm25_title + 0.20·dense_cosine + 0.20·rrf）；
  benchmark 新增 **MRR@10、NDCG@5、P@3** 三项标准指标；HTML 详报 hits 表新增 pages/scores/dense_cosine 列、
  citations 表与版本解析段；run_case 增加**断点续传**（result.json 已存在时跳过，`--force` 强制重跑）。
  新实测：MRR@10=0.833 vs baseline 0.167，NDCG@5=0.799 vs baseline 0.113。
- **v3（2026-11-21）**：scan_index 支持 PDF 分页感知（`[page N]` 标记、
  印刷页码偏移检测）、office/PDF 元数据提取、近重复检测（版本变体判定）；
  answer 升级为字段加权 BM25（文件名字段信号）+ 引用生成器（段落首句锚点、
  逐字引用、页码、哈希四件套）；run_case 的 result.json 增加 citations 与
  version_resolution；benchmark 题集扩到 9 题（多约束/版本辨识/跨文件共识/
  空命中诚实）；新增 .selftest/regress.py 一键回归（全绿）。
- **v2（2026-11-20）**：真实混合语料回归；标准 BM25+多路查询规划+RRF；
  baseline 对照；benchmark 金标准指标；HTML 报告渲染器。
- **v1（2026-11-19）**：初版 stdlib 引擎与三份 SKILL.md。

> 自测记录见仓库 `.selftest/`（不随提交打包）。平台侧最终分由组织方固定案例
> 与官方 Harness 生成，本仓库数字为方法与对照差异的证据。

## 本地验证

```bash
cd skills/common/engine
python -I scan_index.py --root <语料库> --out ../.selftest/index.json --ledger ../.selftest/ledger.json
python -I answer.py --index ../.selftest/index.json --question "..." \
        --filter ../.selftest/filter.json --terms-file ../.selftest/terms.txt \
        --top 25 --out ../.selftest/answer.json
python -I run_case.py --case <case.json> --index ../.selftest/index.json \
        --work ../.selftest/work --out ../.selftest/out
```

仓库仅含文本资源（≤50 文件、≤2MB 上限合规）；运行产物与账本在本地生成，不入库。

## 重建本地提交包

```bash
python -I tools/build_submissions.py
```

脚本按每个 `_submit/<slug>/GITHUB_URL.txt` 指向的 Skill 目录重建 32 个自包含包，
并校验 UTF-8、50 文件、单文件 500KB、总 2MB 与必需资源；不会调用平台或修改源文件。

## Rubrics 覆盖评估

```bash
python -I .selftest/gen_profile9.py
python -I tools/rubric_eval.py
```

`profile9` 生成与组织方隐藏案例同构的 9 文档资料库：真实 PDF 版本族、精确副本别名、
行政版本说明、DOI/作者元数据、Excel 数据、会议记录、PPT、扫描件与损坏件。
`rubric_eval.py` 分别跑分类/检索/综述三条链路，并按公开 rubrics 检查实体整理、标签、
权限、版本、前后树、幂等、NL 约束、查询改写、PRF、句子证据、跨文件共识、筛选协议、
主题聚类、证据强度、冲突/空白、PRISMA 与引用草稿；输出 `.selftest/rubric_eval_out/scorecard.json`。
该分数是本地覆盖诊断，不冒充平台正式评分。