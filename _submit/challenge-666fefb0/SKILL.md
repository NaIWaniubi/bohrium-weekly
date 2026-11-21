---
name: bohr-literature-classify
description: |
  面向"文献与资料自动分类"的 Agent Skill：把科研资料库里杂乱的论文 PDF、预印本、
  会议论文、学位论文、补充材料、数据集与扫描件，按题目给定的组织规则与元数据/
  版本体系，自动分类归档并交付可复核的清单与操作记录。依赖确定性引擎做扫描、
  哈希、版本族识别与分类候选排序，Agent 只做"规则—材料"的判定回链。
---

# 文献与资料自动分类 Skill

让杂目录变成"有元数据、有版本关系、可检索交接"的资料库：分类结论全部落在索引期的结构化证据上（sha256 / 版本族 / 目录结构），而非肉眼印象。

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；材料按题目提供，PDF 为真实文件。
- 工作区 `/app/work`；产物 `/app/output` + `result.json`（覆盖原 output_contract 字段，带 `case_id`，含诚实 status/sources/uncertainty/change_records）。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**；三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果标明边界；可编辑产物附真实渲染预览。
- 先保存诚实部分交付物，再增量改进。Skill 资源 ≤50 个 UTF-8 文件、单文件 ≤500KB、总 ≤2MB、无符号链接。

## 1. 执行阶段

### Phase A — 库事实（一次）

```bash
python -I /skills/weekly-skill/common/engine/scan_index.py \
        --root "<library>" --out /app/work/index.json \
        --ledger /app/work/ledger.json
```

拿到：全量文件事实表、章节文本块、精确重复组、**版本族**（draft/rev/final 归一链）、needs_tool 清单。分类的全部锚点都在这里。

### Phase B — 分类（逐案例）

1. 从 case.json / task.md 提取组织规则（学科方向 / 文件类型 / 年份区间 / 目录约定 / 排除项）。**每条规则生成独立 filter 行**，执行时逐条留痕。
2. 运行：

```bash
python -I /skills/weekly-skill/common/engine/run_case.py \
        --case /input/case.json --index /app/work/index.json \
        --work /app/work --out /app/output
```

3. 对 `families` 的成员逐一判定归属：把家族挂到规则桶、给出成员清单与 canonical；同哈希多路径走 aliases 推荐 canonical，避免把物理副本当两个条目。
4. needs_tool / 无文本层文件**单独列桶**并说明限制，不混入正文分类结果。
5. 写出 result.json 并立即保存。

## 2. 输出契约（原题 output_contract 三块 + 结构骨架）

| 区块 | 内容 | 标准 |
|-|-|-|
| 资料结果 | 目录、文件清单、元数据、版本关系或知识结构 | 与输入文件和题目规则一一对应 |
| 操作证据 | 哈希、来源、权限、变更、日志 | 他人可复跑并核对 |
| 失败与恢复 | 跳过、冲突、无权限、重复、恢复记录 | 不把未完成写成完成 |

```json
{
  "case_id": "…",
  "status": "ok | partial | failed",
  "rules_applied": [
    {"rule":"类型=PDF","filter":"type=[.pdf]","kept":12,"dropped_since_prev":40},
    {"rule":"排除草稿目录","filter":"exclude=[notes/]","kept":…}
  ],
  "catalog": {
    "buckets": [
      {"bucket_id":"B1","title":"最终版核心论文","criteria":"…",
       "members":[{"relpath":"papers/x_v2_final.pdf","sha256":"…","ext":".pdf"}],
       "aliases_resolved":[{"family":"…","canonical":"…","aliases":["…"]}]}
    ]
  },
  "version_lineage": [
    {"family_key":"survey on gcn for mol prop","canonical":"papers/…_v2_final.md",
     "aliases":["notes/…_draft.md"],"evidence":"同 norm_title + variant_rank"}
  ],
  "exact_duplicates": [{"sha256":"…","paths":["a","b"],"resolution":"canonical=a"}],
  "unclassified": [{"relpath":"…","why":"无文本层扫描件，需 OCR"}],
  "operation_evidence": {"ledger":"/app/work/ledger.json",
    "run_recipe":"scan_index + run_case 命令原文"},
  "uncertainty": {"missing": [], "conflicts": [], "limits": []},
  "change_records": [],
  "provenance": {"generated_at":"ISO8601"}
}
```

## 3. 异常处理矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 文件损坏/不可读 | 未分类桶 + uncertainty.missing | 静默丢弃或按名归类 |
| 同名不同版本 | version_lineage 记别名链，主引用取 canonical | 选错版本当终稿 |
| 同哈希多路径 | exact_duplicates 一条 + resolution | 双计两个条目 |
| 分类歧义（跨桶） | 两桶各放候选 + conflicts 说明，保守处理 | 单方强断 |
| 空规则命中 | rules_applied 原样保留 kept=0 数字；扩词/放宽仅限人工确认 | 造相似条目填满 |

## 4. 元数据与版本识别规程

- **标题归一化**（norm_title）：剥标点/大小写，去 draft|rev|final|终稿 等后缀 → 家族键。
- **版本序**（variant_rank）：final/终稿=+100；vN 按十位计分。用于家族内"哪份是最新"。
- **重复判定**：sha256 完全一致即物理副本；名称不同但家族键相同即别名。
- 元数据字段只能取自文件系统事实（size/mtime/ext）与可解析正文首部行；解析不到就进 uncertainty，**禁止从领域标签外推**。

## 5. 最小调用示例（示意，非正式案例结果）

```bash
printf '{"type":[".md",".pdf"],"dir":["papers"],"need_text":true}' > /app/work/f.json
python -I /skills/weekly-skill/common/engine/answer.py \
        --index /app/work/index.json --question "graph neural network survey final version" \
        --filter /app/work/f.json --top 10 --out /app/work/ans.json
```

示意返回（节选，**非真实案例结果**）：

```json
{ "filter_audit":[{"constraint":"type=.md,.pdf","kept":2,"dropped_since_prev":3}],
  "hits":[{"rank":1,"file":"papers/gcn_mol_pred_v2_final.md",
           "sha256_file":"d4f6d8108fd2…","chunk_id":"d4f6d8108fd2#1"}] }
```

## 6. 提交前自查

- [ ] catalog 每桶成员与 filter 审计数字对得上，一一对应（kept=0 的规则同样如实保留）
- [ ] 版本族/别名/重复组均有 sha256 证据，推荐 canonical 有依据
- [ ] 未分类与 needs_tool 已单列并说明，未伪装完成
- [ ] operation_evidence 给出可复跑命令原文
- [ ] 资源总量在上限内、无杜撰元数据
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

| 指标 | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | 0.667 | 0.200 |
| R@5 | 0.933 | 0.050 |
| gold 召回数/top5 | 3.2 | 0.2 |
| hash+offset 验证率 | 100% | 0% |

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法差异，
平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。
