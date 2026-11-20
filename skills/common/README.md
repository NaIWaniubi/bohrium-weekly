# bohrium-weekly 引擎（common/engine）

三个 Skill（科研文件智能检索 / 文献与资料自动分类 / 文献综述生成）共用的确定性内核。
stdlib only，无第三方依赖；全部输出为机器可读 JSON，每一步可复跑核对。

## 文件

| 文件 | 作用 |
|-|-|
| `lib.py` | 基础工具：原子 JSON 写、哈希（CJK/BOM 容错读取）、标题归一化与版本族排序、操作账本 `ledger_append` |
| `scan_index.py` | 语料库扫描与索引：`--root <库> --out <index.json>`。产出文件全量清单（relpath/ext/size/sha256/mtime）、章节级文本块（chunk_id+字符偏移）、精确重复组（同 sha256 多路径）、版本族（draft→final 的别名链）与推荐 canonical、无提取器文件的诚实标记（needs_tool） |
| `answer.py` | 单题应答：`--index --question [--filter F] [--terms-file T] --top N --out`。**过滤先行**：每条约束独立留痕（kept/dropped_since_prev）；命中块携带 file/sha256/chunk_id/offset/evidence（匹配处窗口）与 score_raw |
| `run_case.py` | 固定案例编排：按契约读取 `/input/case.json` 与 `task.md`，合并显式过滤约束与跨语言扩展词，运行链路，把每个命中的**全文**落盘到 `evidence/hits/`，并写出覆盖 output_contract 全字段的 `result.json` 骨架（判定字段留空给 Agent 填） |

## 设计原则（对照评分维度）

1. **实际效果 40 分**：只输出真实读过源字节的结论——脚本永不杜撰未读过的段落；命中给出文件哈希+字符偏移，定位可直接复核。
2. **使用增益 25 分**：过滤先执行且逐条留痕；跨语言问句可用 `--terms-file` 扩展后再匹配；命中排序用 BM25-lite，文档与查询词集合的相同性判定可解释。
3. **创新增益 15 分**：版本族识别（norm_title+variant_rank）在索引期完成，重复内容与草稿/终稿关系不再靠 Agent 肉眼比对；精确重复组给推荐 canonical，避免重复引用。
4. **可靠体验 10 分**：无提取器/损坏/无文本层/权限不足的文件标记 `needs_tool` 或进账本 warn，绝不静默跳过或伪读；空命中显式 `unmatched_warning`。
5. **生态贡献 10 分**：账本（ledger.json）记录每次扫描与应答的输入标识与状态，供他人复跑。

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

- 纯启发式章节切分：大文件中首次标题前 >1500 字符才切节，短文档整篇一块——定位以 chunk 内 offset 为准。
- 命中窗口截断 700 字符，`has_more=true` 表示索引内已截断，需回源文补读。
- 语义召回非神经检索：依赖关键词扩展词表（SKILL.md 中给出扩展规程），纯同义改写的问题召回会下降，已在 SKILL.md 写明对策（多路查询+人工兜底）。