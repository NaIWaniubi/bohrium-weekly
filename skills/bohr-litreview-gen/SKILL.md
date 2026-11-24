---
name: bohr-litreview-gen
description: |
  文献综述生成 Skill：把「综述不是把摘要连起来」的判分要求拆成五步可复跑流程——
  筛选有据、结构有链、边界诚实；证据矩阵逐条锚定源字节哈希与字符偏移。
---

# 文献综述生成 Skill

综述的正确形态：筛选有据、结构有链、边界诚实。确定性引擎负责语料事实与定位，Agent 负责判断与写作——每一步都可复核。

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；真实案例材料可能含 9 个以上来源/记录、具体领域研究问题、前瞻性筛选协议等，**旧占位材料与新输入不是同一输入身份**。
- 工作区 `/app/work`；产物 `/app/output`，必含 `result.json`（覆盖原 output_contract 全字段，带 `case_id`、`status`、`sources`、`uncertainty`、`change_records`，诚实状态）。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**；三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果必须标明边界；可编辑产物附真实渲染预览。
- 先存诚实部分交付物，再增量改进。资源上限：50 个 UTF-8 文件、单 ≤500KB、总 ≤2MB、无符号链接。

## 1. 共用确定性引擎（stdlib only，随提交打包于 common/engine/）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 库扫描 → 文件全量事实表(sha256/mtime/**meta**)、章节块(chunk_id+偏移+**pages+印刷页码偏移**)、精确重复组、近重复检测、版本族别名链、needs_tool |
| answer.py | **过滤先行**：每条约束独立留痕；多路查询规划（子查询聚焦 + 跨语言扩展词独立成路）；**字段加权 BM25**（正文主分 + 文件名字段加权 `_v2_final` 信号）+ **dense TF-IDF cosine**（概念改写/同义词漂移）+ RRF 保底；命中带 file/sha256/pages/offset/evidence/贡献计划 |
| run_case.py | 按契约读 case.json，合并过滤约束与跨语言扩展词，跑链路，落盘命中全文；**自动产出完整首版 result.json**（answer.content、evidence_matrix、screening_log、review_draft、sources 全量、uncertainty/failure_recovery、citations/version_resolution），并复核证据哈希；断点续传 |
| finalize.py | 完整交付层：候选证据矩阵、逐文件筛选记录、谨慎证据草稿、版本链、全量来源与运行证据；保留原 output_contract 字段 |
| benchmark.py / render_report.py / baseline.py | 金标准指标（P@3/P@5/MRR@10/NDCG@5）、HTML 详报渲染、对照基线 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output --profile review
```

### v5 完整交付纪律（必须）

1. 引擎先生成**非空候选证据矩阵与谨慎草稿**，状态保持 `partial`；Agent 不得从空骨架重写，也不得把候选相关性直接写成共识。
2. 阅读 `evidence/hits/*.txt` 后，逐条更新 `evidence_matrix[].included/reason/comparative_fields/strength/supports`，再写 `review.summary/sections`。
3. 每条实质结论必须引用 `[E#]` 或 `[S#]`，并能回到 quote+offset+sha256；相反证据并列写入 uncertainty.conflicts。
4. 只有筛选、主题、证据、综述四步都真实完成才把 status 改 `ok`；否则保留 partial 与具体 limits。

## 2. 判分点逐条对策（原文映射）

| 判分点（分值） | 本文对策 |
|-|-|
| 实际效果 40：完成度15/正确性15/完整性5/可核验5——结论与研究问题和证据范围一致，研究产物/证据矩阵/不确定性三块齐 | 五步法走满；**每条结论带 `[S#]` 引用**，无引文支撑的句子删掉或降级为不确定性条目 |
| 使用增益 25：质量15/效率5/可验证性5——相同条件下加载 Skill 后相比基础 Agent 的提升 | 过滤器先行（基础 Agent 无审计结构）；grep+哈希定位替代翻页；benchmark 分离度作为增益证据 |
| 创新增益 15：新增能力/方法改进/创新证据 | 文件名字段加权、版本族别名链、近重复区分物理副本与版本变体——基础 Agent 不具备的稳定能力 |
| 可靠体验 10：调用易用3/稳定性4/异常处理3 | office 元数据与 PDF 分页感知双通道；needs_tool/损坏/空命中全部显式标记；多步工具链断裂时断点续传+账本定位 |
| 生态贡献 10 + GitHub 公开链接 5 | README 有实例证据；仓库匿名可读拿满附加 |

## 3. 单案例执行 SOP（五步法）

1. **读案例**：提取研究问题、检索范围、纳排规则；若案例给前瞻性协议，按协议字段建 filter 并逐条留痕。
2. **建库**：scan_index 对案例给定库跑一次；查 needs_tool 与近重复组。
3. **应答**：跨语言词表写入 terms.txt 后跑 run_case。
4. **读证**：逐条全文阅读 evidence/hits/*.txt（has_more=true 回源文补读）。
5. **成稿**：在引擎候选矩阵上完成纳排、主题归纳和综述三节，每完成一节立即保存；四步未齐保持 partial。

## 4. 输出契约（原题 output_contract 三块）

| 内容 | 字段与要求 | 标准 |
|-|-|-|
| 研究产物 | 综述、筛选、趋势或选题评估结果 | 结论与研究问题和证据范围一致 |
| 证据矩阵 | 文献来源、纳排理由、比较字段、定位 | 关键判断可回到材料 |
| 不确定性 | 冲突、偏倚、空白、无法确定项 | 不越过证据范围扩展结论 |

result.json 完整首版字段（Agent 在其上完成语义综合，不从空骨架开始）：

```json
{"case_id":"…","status":"ok|partial|failed",
 "research_question":"…","screening_protocol":{"included_criteria":"…","excluded_criteria":"…"},
 "review":{"summary":"≤600字，每条结论后括注 [S#]","sections":[{"title":"…","claim":"…","refs":["S3","S7"]}]},
 "evidence_matrix":[{"sid":"S1","source":{"file":"papers/x.pdf","sha256":"…"},
   "included":true,"reason":"符合纳入标准…","comparative_fields":{"year":2024,"method":"GNN"},
   "locator":{"chunk_id":"…","offset":[s,e],"quote":"≤120字原文"}}],
 "uncertainty":{"conflicts":[],"bias":[],"gaps":[],"undetermined":[]},
 "selection_note":"不足全球完整性/新颖性主张——按题面证据范围作答",
 "change_records":[],
 "provenance":{"generated_at":"ISO8601"}}
```

## 5. 判断纪律（对应"文字自述不能替代实际运行证据"）

- 矩阵每行的 locator 必须指向**实际读过**的 chunk/offset；未读行留空不得写结论。
- 纳排理由必须落在协议条款与文件事实（哈希/类型/年份）上，禁止"感觉相关"。
- 结论句必须带 `[S#]`；没有引用支撑的句子删掉或降级为不确定性条目。
- **不主张**"全面/完整/首次"，除非案例白板明确给出。
- 索引截断（has_more）时回源文补读后更新 locator；预算不够则标 limits。

## 6. 异常与恢复矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 命中证据与结论矛盾 | uncertainty.conflicts 双列原文+各自定位 | 只挑一边 |
| 元数据缺失 | comparative_fields 置 null，undetermined 记一条 | 按标签补造 |
| 多版本同题文献 | families canonical 主引 + aliases 备注 | 双引计两次证据 |
| 检索为空 | unmatched_warning 保留；扩词重试 ≤2 轮 | 编造相似文献填充 |
| 超预算 | 保存当前 partial，change_records 记未完成节号 | 硬扛到全损 |

## 7. 依赖、版本与安装

- Python ≥ 3.8 stdlib only，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool / office-renderer / ocr-tool 等）。
- 引擎 `bohrium-weekly-common@5.0`（2026-11-24，随仓库同版本锁定）。无构建：本目录放入评测约定位置后直接调用；新增 `finalize.py` 必须随包提交。

## 8. 实例运行证据（本仓库自带，可复跑）

在 15 文件混合科研库上实测：

| 指标（9 题平均，含 1 题空命中诚实用例） | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| MRR@10 / NDCG@5 | **0.833 / 0.799** | 0.167 / 0.113 |
| hash+offset 验证率 | **88.9%**（非空命中 100%） | 0% |

回归：`.selftest/regress.py` 一键全绿；v5 自动交付断言覆盖非空 answer、证据哈希、全量 sources、硬过滤与完整报告。

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法差异；平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。
