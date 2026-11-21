# bohrium-weekly 引擎（common/engine）

三个 Skill（科研文件智能检索 / 文献与资料自动分类 / 文献综述生成）共用的确定性内核。
stdlib only，无第三方依赖；全部输出为机器可读 JSON，每一步可复跑核对。

## 文件

| 文件 | 作用 |
|-|-|
| `lib.py` | 基础工具：原子 JSON 写、哈希（CJK/BOM 容错读取）、标题归一化与版本族排序、操作账本 `ledger_append` |
| `scan_index.py` | 语料库扫描与索引：`--root <库> --out <index.json>`。产出文件全量清单（relpath/ext/size/sha256/mtime）、章节级文本块（chunk_id+字符偏移）、精确重复组（同 sha256 多路径）、版本族（draft→final 的别名链）与推荐 canonical、无提取器文件的诚实标记（needs_tool） |
| `answer.py` | 单题应答：`--index --question [--filter F] [--terms-file T] --top N --out`。**多路查询规划**（问题拆解成聚焦子查询，跨语言扩展词独立成路）+ **标准 BM25**（k1=1.5/b=0.75）主排序 + RRF 融合保底，**过滤先行**：每条约束独立留痕（kept/dropped_since_prev）；命中块携带 file/sha256/chunk_id/offset/evidence/贡献计划 |
| `run_case.py` | 固定案例编排：按契约读取 `/input/case.json` 与 `task.md`，合并显式过滤约束与跨语言扩展词，运行链路，把每个命中的**全文**落盘到 `evidence/hits/`，并写出覆盖 output_contract 全字段的 `result.json` 骨架（判定字段留空给 Agent 填） |
| `baseline.py` | 对照基线：模拟"未加载本 Skill 的朴素 Agent"——仅文件名匹配、无过滤审计、无定位、无哈希（题目评分点明批评的失败模式） |
| `benchmark.py` | 金标准问答集指标：读 `bench/queries.json`，同题分别跑 Skill 链与 baseline，输出 P@5 / R@5 / top5 召回数 / hash 验证率对比表 |
| `render_report.py` | 把 result.json 渲染成自包含单页 HTML（过滤审计表 + 命中定位表 + 证据原文窗口 + 不确定性分区），评审无需环境直接打开 |

## 实例运行证据（本仓库自带可复跑）

在 15 文件混合科研库（真 PDF×3 版本族 + docx + xlsx + pptx + 扫描位图×4 + 损坏件 + 精确重复副本）上：

```
python -I scan_index.py --root <库> --out index.json --ledger ledger.json
python -I benchmark.py --index index.json --out bench_result.json
```

| 指标（5 题平均） | Skill 链 | baseline(文件名搜索) |
|-|-|-|
| P@5 | **0.667** | 0.200 |
| R@5 | **0.933** | 0.050 |
| gold 召回数/top5 | **3.2** | 0.2 |
| hash+offset 验证率 | **100%** | 0% |

边界如实说明：本机为 Windows 实验环境，语料为自建合成科研库；平台侧由组织方固定案例与 DeepSeek Harness 产生正式对照，上表证明的是方法与对照协议的差异，不是最终 benchmark 分。

## 设计原则（对照评分维度）

1. **实际效果 40 分**：只输出真实读过源字节的结论——脚本永不杜撰未读过的段落；命中给出文件哈希+字符偏移，定位可直接复核。
2. **使用增益 25 分**：过滤先执行且逐条留痕；问句拆解为多路子查询（跨语言扩展独立成路）标准 BM25 主排序 + RRF 融合保底，任一路词漏掉不会杀死召回；命中可解释到"哪几个计划贡献了它"。
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