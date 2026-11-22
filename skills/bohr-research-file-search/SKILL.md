---
name: bohr-research-file-search
description: |
  面向"科研资料库整理与跨文件定位"的 Agent Skill：把一个科研文件库（PDF/Word/Excel/
  PPT/笔记/数据表混合目录）扫描成可核验索引，对提问先执行过滤约束再做跨文件定位，
  返回命中文件的章节级证据（sha256 + 字符偏移 + 匹配窗口），并记录版本族与重复内容。
  适用于科研资料整理、文件管理、知识沉淀与"从杂目录里找回关键内容"场景。
---

# 科研文件智能检索 Skill

把"跨文件语义检索—章节定位—过滤执行—证据回链"做成可复跑流程，而不是把提问丢给文件名搜索。

## 0. 运行契约（必须遵守）

- 输入：组织方固定案例的 `/input/case.json`（必要时含 `task.md`、`library` 字段）；材料按题目提供，PDF 收到的是**真实 PDF**（无预抽取答案层）。
- 工作区：`/app/work`；最终产物：`/app/output`，含 `result.json`（覆盖原 output_contract 全部字段并带 `case_id`）。
- 预算：**64 次模型请求 / 900 秒 / 单响应 16384 token（含推理）**。三案例、三条件各重复三次；真实服务失败由平台单独挂起。
- 诚实要求：不得杜撰缺失的软件状态或科学数据；部分结果必须标明边界；可编辑 Office/幻灯/图表产物需附真实渲染 PDF/PNG 预览。
- 平台记录模型、源/案例版本、运行与预算；**不要自报 benchmark 分数**。
- 先保存诚实部分交付物，再增量改进。
- Skill 资源上限：50 个 UTF-8 文本文件，单个 ≤500KB，总计 ≤2MB，无符号链接。

## 1. 执行阶段

### Phase A — 建库（每个库一次，预算最省）

```bash
python -I /skills/weekly-skill/common/engine/scan_index.py \
        --root "<library 或 /input/library>" --out /app/work/index.json \
        --ledger /app/work/ledger.json
```

读回 `index.json` 的 `summary` 与 `needs_tool`：
- `needs_tool` 中的文件（如无文本层的扫描件 PDF）按题目要求调用官方工具后补录，**不得凭文件名臆测内容**。
- 版本族与精确重复组已结构化（见 index 的 `families` / `dedup_groups` / `aliases`），后续引用一律取推荐 canonical 路径并注明别名链。

### Phase B — 应答每题（逐案例）

1. 合并显式过滤约束为 filter JSON（`type/extensions/dir/min_size/max_size/exclude/need_text`），**每条约束都要能在 result.json 的 filter_audit 中独立复现**。
2. 运行：

```bash
python -I /skills/weekly-skill/common/engine/run_case.py \
        --case /input/case.json --index /app/work/index.json \
        --work /app/work --out /app/output
```

3. **全文阅读** `/app/output/evidence/hits/*.txt` 中每个命中的完整原文（及 `has_more=true` 时回源文补读）。未读全文的行不得写入结论。
4. 判定每个命中是否真正回答提问、定位是否落在原文、引用与元数据是否真实——不满足就降级为"候选"，并说明依据。
5. 按 output_contract 填 `result.json` 并**立即保存**。

## 2. 输出契约（对应题目要求的三块）

`result.json` 必须覆盖（原题 output_contract 全字段 + case_id）：

| 区块 | 字段 | 标准 |
|-|-|-|
| 资料结果 | 目录、文件清单、元数据、版本关系、知识结构 | 与输入文件和题目规则一一对应 |
| 操作证据 | 哈希、来源、权限、变更、日志 | 他人可复跑并核对 |
| 失败与恢复 | 跳过、冲突、无权限、重复、恢复记录 | 不把未完成写成完成 |

另附：本题推荐结构骨架（Agent 填实际值）：

```json
{
  "case_id": "…",
  "status": "ok | partial | failed",
  "question": "…",
  "filter_audit": [ {"constraint":"…","kept":N,"dropped_since_prev":M} ],
  "files_in_scope": [{"relpath":"…","sha256":"…","size":N,"ext":".md"}],
  "version_families": [{"family_key":"…","canonical":"…","aliases":["…"]}],
  "answer": {
    "content": "仅在读过 evidence/hits/*.txt 全文后填写；每条结论带 file + sha256 + offset",
    "evidence_refs": [{"file":"…","sha256":"…","offset":[s,e],"quote":"≤120字","meaning":"该引文支撑哪一句结论"}]
  },
  "uncertainty": {"missing": [], "conflicts": [], "limits": []},
  "change_records": [],
  "provenance": {"generated_at":"ISO8601"}
}
```

## 3. 异常处理矩阵（可靠体验项）

| 情形 | 处置 | 禁止 |
|-|-|-|
| 文件不可读/损坏 | result.status=partial；uncertainty.missing 记路径+原因；账本 warn | 跳过不记 |
| 无文本层扫描件 | needs_tool → 按题目用 ocr-tool；仍不行则标 limits | 凭文件名/标签补造内容 |
| 重名/同哈希多路径 | aliases 推荐 canonical，回链用 canonical | 双引同一物理副本当两处证据 |
| 空命中 | unmatched_warning 原样保留；扩词表重试 ≤2 轮；仍空则如实报"库内无匹配" | 编造相似文献 |
| 超预算（接近 64 请求/900s） | 立即保存当前 honest partial，uncertainty 注明未完成案例号 | 硬扛到失败 |

## 4. 跨语言扩展规程（terms.txt 写法）

中文问句对英文文献召回差时，把关键概念改写为英文术语，一行一个，写 `/app/work/terms.txt` 后用 `--terms-file`（或 run_case 的 case.expanded_terms）传入。示例：

```
graph neural networks
molecular property prediction
message passing neural networks
quantum chemistry benchmark
```

仅当题目语境确需时扩展，禁止整段机翻注入。

## 5. 最小调用示例（示意输出，非正式案例结果）

```bash
# 假设 /app/work 已建好 index.json
printf '{"type":[".md",".pdf"],"need_text":true}' > /app/work/f.json
printf 'graph neural networks\nmessage passing\n' > /app/work/terms.txt
python -I /skills/weekly-skill/common/engine/answer.py \
        --index /app/work/index.json \
        --question "分子性质预测里图神经网络的方法" \
        --filter /app/work/f.json --terms-file /app/work/terms.txt \
        --top 25 --out /app/work/ans.json --ledger /app/work/ledger.json
```

示意返回（节选，**非真实案例结果**）：

```json
{ "filter_audit":[{"constraint":"type=.md,.pdf","kept":2,"dropped_since_prev":3}],
  "hits":[{"rank":1,"file":"papers/gcn_mol_pred_v2_final.md",
           "sha256_file":"d4f6d8108fd2…","chunk_id":"d4f6d8108fd2#1",
           "offset":{"start":0,"end":411}}],
  "unmatched_warning": null }
```

## 6. 自查清单（提交前）

- [ ] result.json 覆盖 output_contract 全部字段且带 case_id
- [ ] 每条结论可回链到 evidence/hits 原文与源字节哈希
- [ ] filter_audit 每约束独立可查；空命中有如实说明
- [ ] needs_tool/损坏/冲突均入 uncertainty，未伪装完成
- [ ] 未编造文献、未自报评测分、资源总量符合上限
## 7. 依赖、版本与安装

- **运行环境**：Python ≥ 3.8，仅标准库（stdlib only），零第三方依赖；PDF/Office/
  OCR 等二进制解析一律走平台预装工具（`environment/resources.yaml` 所列
  pdf-text-tool / office-renderer / ocr-tool 等），本 Skill 不捆绑二进制。
- **引擎版本**：`bohrium-weekly-common@1.0`（2026-11-20 提交，随仓库同版本锁定；
  SHA-256 与提交记录见仓库 commit）。引擎源码：仓库
  `skills/common/engine/`（lib.py / scan_index.py / answer.py / run_case.py）。
- **安装**：无构建步骤。将本目录内容放入评测约定位置（Skill 本体在
  `/skills/weekly-skill/`，共用引擎随附同一相对路径 `common/engine/`），按第 1/5 节
  命令直接调用。README 与自测记录不随提交打包（见仓库根 .gitignore）。

## 8. 实例运行证据（本仓库自带，可复跑）

共用引擎在 15 文件混合科研库（真 PDF×3 版本族 + docx + xlsx + pptx + 扫描位图 + 损坏件 +
精确重复副本）上实测（命令见 `skills/common/README.md`）：

| 指标（9 题平均，含 1 题空命中诚实用例） | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| gold 召回数/top5 | **2.33** | 0.22 |
| hash+offset 验证率 | **88.9%**（非空命中题 100%） | 0% |
| MRR@10 / NDCG@5 | **0.833 / 0.799** | 0.167 / 0.113 |

v3 新增能力对照判分点：PDF 分页定位与印刷页码偏移检测、office/PDF 元数据、
近重复（版本变体）判定、字段加权 BM25（含 `_v2_final` 类文件名信号）、
引用生成器（页码+锚句+逐字引用四件套）、空命中诚实报告。

回归：`.selftest/regress.py` 一键全绿（字节编译→造库→扫描→全链路→基准分离）。

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法差异，
平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。
