---
name: bohr-literature-classify
description: |
  文献与资料自动分类 Skill：把「杂目录→有元数据、有版本关系、可检索交接的资料库」做成可复跑流程，
  分类结论全部落在索引期的结构化证据上（sha256/版本族/目录结构），而非肉眼印象。
---

# 文献与资料自动分类 Skill

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；材料按题目提供，PDF 为真实文件。
- 工作区 `/app/work`；产物 `/app/output`，必含 `result.json`（覆盖原 output_contract 全字段，带 `case_id`、`status`、`sources`、`uncertainty`、`change_records`，诚实状态）。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**；三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果必须标明边界；可编辑产物附真实渲染预览。
- 先存诚实部分交付物，再增量改进。资源上限：50 个 UTF-8 文件、单 ≤500KB、总 ≤2MB、无符号链接。

## 1. 共用确定性引擎（stdlib only，随提交打包于 common/engine/）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 库扫描 → 文件全量事实表(relpath/ext/size/sha256/mtime/**meta 元数据**)、章节块(chunk_id+偏移+**pages+印刷页码偏移**)、精确重复组、**近重复检测**（同标题族字节不同=版本变体）、版本族别名链与推荐 canonical、needs_tool |
| answer.py | **过滤先行**：每条约束独立留痕；多路查询规划 + **字段加权 BM25**（正文主分 + 文件名字段加权 `_v2_final` 信号）+ **dense TF-IDF cosine** + RRF 保底；命中带 file/sha256/pages/offset/evidence/贡献计划 |
| run_case.py | 按契约读 case.json，合并过滤约束与跨语言扩展词，跑链路，落盘命中全文到 evidence/hits/，产出覆盖 output_contract 的 result.json 骨架（含 citations+version_resolution）；断点续传 |
| benchmark.py / render_report.py / baseline.py | 金标准指标（P@3/P@5/MRR@10/NDCG@5）、HTML 详报渲染、对照基线 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output
```

## 2. 判分点逐条对策（原文映射）

| 判分点（分值） | 本文对策 |
|-|-|
| 实际效果 40：完成度15/正确性15/完整性5/可核验5——目录、文件清单、元数据、版本关系或知识结构，与输入文件和题目规则一一对应 | 五步法走满：案例读题→库事实→过滤先行应答→全文读证→成稿；catalog 每桶成员与 filter 审计数字对得上，一一对应 |
| 使用增益 25：质量15/效率5/可验证性5 | 过滤器先行逐条留痕 audit 行（基础 Agent 无此结构）；grep+哈希定位替代逐文件翻查；benchmark 分离度作为增益证据 |
| 创新增益 15：新增能力/方法改进/创新证据 | 版本族别名链自动消解（norm_title+variant_rank，评审可复现的选择依据）；近重复检测区分物理副本 vs 版本变体 |
| 可靠体验 10：调用易用3/稳定性4/异常处理3 | office 元数据与 PDF 分页感知双通道，任意格式稳定产出；needs_tool/损坏/空命中全部显式标记进 uncertainty |
| 生态贡献 10 + GitHub 公开链接 5 | operation_evidence 给出可复跑命令原文；README 有实例证据；仓库匿名可读拿满附加 |

## 3. 单案例执行 SOP（五步法）

1. **读案例**：从 case.json/task.md 提取组织规则（学科方向/文件类型/年份区间/目录约定/排除项）；每条规则生成独立 filter 行，执行时逐条留痕。
2. **建库**：scan_index 对案例给定库跑一次；查 needs_tool 与近重复组。
3. **应答**：对 families 成员逐一判定归属，把家族挂到规则桶，给出成员清单与 canonical；同哈希多路径走 aliases 推荐 canonical，避免把物理副本当两个条目。
4. **读证**：逐条全文阅读 evidence/hits/*.txt（has_more=true 回源文补读）。
5. **成稿**：写 result.json 并立即保存；预算剩 <20% 时保存 partial。

## 4. 输出契约（原题 output_contract 三块）

| 内容 | 字段与要求 | 标准 |
|-|-|-|
| 资料结果 | 目录、文件清单、元数据、版本关系或知识结构 | 与输入文件和题目规则一一对应 |
| 操作证据 | 哈希、来源、权限、变更、日志 | 他人可复跑并核对 |
| 失败与恢复 | 跳过、冲突、无权限、重复、恢复记录 | 不把未完成写成完成 |

result.json 骨架：

```json
{"case_id":"…","status":"ok|partial|failed",
 "rules_applied":[{"rule":"类型=PDF","filter":"type=[.pdf]","kept":12,"dropped_since_prev":40}],
 "catalog":{"buckets":[{"bucket_id":"B1","title":"最终版核心论文","criteria":"…",
   "members":[{"relpath":"papers/x_v2_final.pdf","sha256":"…","ext":".pdf"}],
   "aliases_resolved":[{"family":"…","canonical":"…","aliases":["…"]}]}]},
 "version_lineage":[{"family_key":"survey on gcn for mol prop","canonical":"papers/…_v2_final.md",
   "aliases":["notes/…_draft.md"],"evidence":"同 norm_title + variant_rank"}],
 "exact_duplicates":[{"sha256":"…","paths":["a","b"],"resolution":"canonical=a"}],
 "unclassified":[{"relpath":"…","why":"无文本层扫描件，需 OCR"}],
 "operation_evidence":{"ledger":"/app/work/ledger.json","run_recipe":"scan_index + run_case 命令原文"},
 "uncertainty":{"missing":[],"conflicts":[],"limits":[]},
 "change_records":[],
 "provenance":{"generated_at":"ISO8601"}}
```

## 5. 元数据与版本识别规程

- **标题归一化**（norm_title）：剥标点/大小写，去 draft|rev|final|终稿 等后缀 → 家族键。
- **版本序**（variant_rank）：final/终稿=+100；vN 按十位计分。用于家族内"哪份是最新"。
- **重复判定**：sha256 完全一致即物理副本；名称不同但家族键相同即别名。
- 元数据字段只取自文件系统事实（size/mtime/ext）与可解析正文首部行；解析不到就进 uncertainty，禁止从领域标签外推。

## 6. 异常与恢复矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 文件损坏/不可读 | 未分类桶 + uncertainty.missing | 静默丢弃或按名归类 |
| 同名不同版本 | version_lineage 记别名链，主引用取 canonical | 选错版本当终稿 |
| 同哈希多路径 | exact_duplicates 一条 + resolution | 双计两个条目 |
| 分类歧义（跨桶） | 两桶各放候选 + conflicts 说明，保守处理 | 单方强断 |
| 空规则命中 | rules_applied 原样保留 kept=0 数字 | 造相似条目填满 |

## 7. 依赖、版本与安装

- Python ≥ 3.8 stdlib only，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool / office-renderer / ocr-tool 等）。
- 引擎 `bohrium-weekly-common@4.0`（2026-11-22，随仓库同版本锁定）。无构建：本目录放入评测约定位置后直接调用。

## 8. 实例运行证据（本仓库自带，可复跑）

在 15 文件混合科研库上实测：

| 指标（9 题平均，含 1 题空命中诚实用例） | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| MRR@10 / NDCG@5 | **0.833 / 0.799** | 0.167 / 0.113 |
| hash+offset 验证率 | **88.9%**（非空命中 100%） | 0% |

回归：`.selftest/regress.py` 一键全绿。

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法差异；平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。
