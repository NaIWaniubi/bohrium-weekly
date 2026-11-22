---
name: bohr-litreview-gen
description: |
  面向"文献综述生成"的 Agent Skill：围绕固定研究问题组织文献证据，生成带纳排
  标准、比较字段与逐条定位的综述/筛选/趋势分析产物，并把冲突、偏倚、空白与
  无法确定项显式分区。产出全部锚定在可复核的源字节哈希上，文字自述不能替代
  实际运行证据。
---

# 文献综述生成 Skill

综述不是把摘要连起来：筛选有据、结构有链、边界诚实。确定性引擎负责语料事实与定位，Agent 负责判断与写作——每一步都可复核。

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；真实案例材料可能含 9 个以上来源/记录、具体领域研究问题、前瞻性筛选协议等，**旧占位材料与新输入不是同一输入身份**。
- 工作区 `/app/work`；产物 `/app/output` + `result.json`（覆盖原 output_contract 字段，带 `case_id`，含诚实 status/sources/uncertainty/change_records）。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**；三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果标明边界；可编辑产物附真实渲染预览。
- 先保存诚实部分交付物，再增量改进。资源 ≤50 文件、单 ≤500KB、总 ≤2MB、无符号链接。

## 1. 执行阶段

### Phase A — 语料事实（一次）

```bash
python -I /skills/weekly-skill/common/engine/scan_index.py \
        --root "<library>" --out /app/work/index.json \
        --ledger /app/work/ledger.json
```

拿到：文件哈希表、章节块、重复组、版本族、needs_tool。综述的所有引用从这里取数。

### Phase B — 逐案例应答

1. 明确**研究问题、检索范围与纳排规则**（来自 case/task；若案例给前瞻性协议，按协议字段建 filter 并逐条留痕）。
2. 跨语言扩展词表写入 `/app/work/terms.txt`（关键概念中英对照，一行一个），运行：

```bash
python -I /skills/weekly-skill/common/engine/run_case.py \
        --case /input/case.json --index /app/work/index.json \
        --work /app/work --out /app/output
```

3. **全文阅读** `evidence/hits/*.txt`，对每条命中做纳/排判定并记理由。
4. 按 output_contract 写研究产物与证据矩阵，**每完成一节立即保存**（预算纪律）。

## 2. 输出契约（原题 output_contract 三块 + 结构骨架）

| 区块 | 内容 | 标准 |
|-|-|-|
| 研究产物 | 综述、筛选、趋势或选题评估结果 | 结论与研究问题和证据范围一致 |
| 证据矩阵 | 文献来源、纳排理由、比较字段、定位 | 关键判断可回到材料 |
| 不确定性 | 冲突、偏倚、空白、无法确定项 | 不越过证据范围扩展结论 |

```json
{
  "case_id": "…",
  "status": "ok | partial | failed",
  "research_question": "…",
  "screening_protocol": {"included_criteria":"…","excluded_criteria":"…"},
  "review": {
    "summary": "≤600 字综述正文（每条结论后括注 [S#]）",
    "sections": [{"title":"…","claim":"…","refs":["S3","S7"]}]
  },
  "evidence_matrix": [
    {"sid":"S1","source":{"file":"papers/x.pdf","sha256":"…"},
     "included":true,"reason":"符合纳入标准…","comparative_fields":{"year":2024,"method":"GNN"},
     "locator":{"chunk_id":"…","offset":[s,e],"quote":"≤120字原文"}}
  ],
  "uncertainty": {
    "conflicts":[{"topic":"…","positions":["S2…","S5…"]}],
    "bias":[], "gaps":[], "undetermined":[]
  },
  "selection_note": "不足全球完整性/新颖性主张——按题面证据范围作答",
  "change_records": [],
  "provenance": {"generated_at":"ISO8601"}
}
```

## 3. 判断纪律（对应"文字自述不能替代实际运行证据"）

- 矩阵每行的 locator 必须指向**实际读过**的 chunk/offset；未读行留空不得写结论。
- 纳排理由必须落在协议条款与文件事实（哈希/类型/年份）上，禁止"感觉相关"。
- 结论句必须带 `[S#]` 引用；没有引用支撑的句子删掉或降级为不确定性条目。
- **不主张**"全面/完整/首次"——除非案例白板明确给出（参赛者不自带隐藏测试或白板）。

## 4. 异常处理矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 命中证据与结论矛盾 | uncertainty.conflicts 双列原文+各自定位 | 只挑一边 |
| 元数据缺失（作者/年份） | matrix comparative_fields 置 null，undetermined 记一条 | 按标签补造 |
| 多版本同题文献 | families canonical 主引 + aliases 备注 | 双引计两次证据 |
| 需要全文但索引截断（has_more） | 回源文补读后更新 locator；预算不够则标 limits | 用截断片段下强结论 |
| 检索为空 | unmatched_warning 保留；扩词重试 ≤2 轮 | 编造相似文献填充 |
| 超预算 | 保存当前 partial，change_records 记未完成节号 | 硬扛到全损 |

## 5. 最小调用示例（示意，非正式案例结果）

```bash
printf '{"type":[".md",".pdf"],"need_text":true}' > /app/work/f.json
printf 'graph neural networks\nmolecular property prediction\nmessage passing\n' > /app/work/terms.txt
python -I /skills/weekly-skill/common/engine/answer.py \
        --index /app/work/index.json \
        --question "图神经网络用于分子性质预测的代表性方法与局限" \
        --filter /app/work/f.json --terms-file /app/work/terms.txt \
        --top 25 --out /app/work/ans.json --ledger /app/work/ledger.json
```

示意返回（节选，**非真实案例结果**）：

```json
{ "filter_audit":[{"constraint":"type=.md,.pdf","kept":2,"dropped_since_prev":3}],
  "hits":[{"rank":1,"file":"papers/gcn_mol_pred_v2_final.md",
           "sha256_file":"d4f6d8108fd2…","chunk_id":"d4f6d8108fd2#1"}],
  "unmatched_warning": null }
```

## 6. 提交前自查

- [ ] result.json 三块契约齐全、case_id 在场
- [ ] evidence_matrix 每行有 sha256 + 读过的定位
- [ ] 冲突/偏倚/空白/不可定项显式分区，未越界扩展
- [ ] 无"全面/首次"类超范围主张；无杜撰元数据、无自报评测分
- [ ] 资源总量符合上限
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
