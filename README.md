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

## 共用引擎 `skills/common/engine/`

- `lib.py` — 原子 JSON、哈希、CJK 分词、标题归一化、版本族排序、操作账本
- `scan_index.py` — 库扫描 → 文件全量事实 + 章节块 + 重复组 + 版本族 + needs_tool
- `answer.py` — 多路查询规划 + 标准 BM25 主排序 + RRF 融合保底 + 过滤先行审计 + 证据窗口（file/sha256/chunk_id/offset）
- `run_case.py` — 固定案例编排：按契约读 `/input/case.json`，落盘命中全文，
  写出覆盖 output_contract 的 `result.json` 骨架（含 evidence_windows 供渲染）
- `baseline.py` / `benchmark.py` / `render_report.py` — 对照基线、金标准指标（P@5/R@5/hash 验证率）、HTML 报告渲染；金标准问答集见 `skills/common/bench/queries.json`

详细文档：[`skills/common/README.md`](skills/common/README.md)。

## 设计要点（对照评审维度）

1. **可核验性**：所有结论锚定到源字节哈希与字符偏移；账本 ledger.json 留痕。
2. **过滤约束执行**：每条约束独立 audit 行，先执行后匹配，排序只用通过过滤的语料。
3. **诚实边界**：无提取器/损坏/空命中显式标记；未读全文不写结论；不越界主张。
4. **预算纪律**：64 请求 / 900s / 16k token 内多节增量保存。

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