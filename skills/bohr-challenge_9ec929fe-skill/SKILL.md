---
name: bohr-challenge_9ec929fe-skill
description: |
  科研代码生成与执行：基于 bohrium-weekly v3 确定性引擎的 Agent Skill（多路查询规划+字段加权 BM25+过滤先行审计+证据回链引用）。
---

# 科研代码生成与执行 Skill

## 0. 运行契约（必须遵守）

- 输入：组织方固定案例的 `/input/case.json`（必要时含 `task.md`、`library`）；材料按题目提供，PDF 收到真实文件。
- 工作区：`/app/work`；最终产物：`/app/output`，含 `result.json`（覆盖原 output_contract 字段并带 `case_id`，含诚实 status/sources/uncertainty/change_records）。
- 预算：**64 次模型请求 / 900 秒 / 单响应 16384 token（含推理）**；三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态或科学数据；部分结果标明边界；可编辑产物附真实渲染预览。
- 先保存诚实部分交付物，再增量改进。Skill 资源上限：50 个 UTF-8 文件，单文件 ≤500KB、总计 ≤2MB，无符号链接。


## 1. 执行阶段

### Phase A — 库事实（一次）

```bash
python -I /skills/weekly-skill/common/engine/scan_index.py \
        --root "<library 或 /input/library>" --out /app/work/index.json \
        --ledger /app/work/ledger.json
```

拿到：全量文件事实表（sha256/mtime/**meta 元数据**）、章节级文本块（含 **pages 与印刷页码偏移**）、
精确重复组、近重复检测（同标题族字节不同=版本变体）、版本族别名链、needs_tool 清单。

### Phase B — 应答每题（逐案例）

1. 从 case.json / task.md 提取显式过滤约束，合并为 filter JSON，**每条约束独立留痕**。
2. 跨语言扩展词写入 `/app/work/terms.txt` 后：

```bash
python -I /skills/weekly-skill/common/engine/run_case.py \
        --case /input/case.json --index /app/work/index.json \
        --work /app/work --out /app/output
```

3. **全文阅读** `/app/output/evidence/hits/*.txt` 中每个命中的完整原文（`has_more=true` 回源文补读）。未读全文的行不得写入结论。
4. 按 output_contract 填 result.json 并**立即保存**。

## 2. 本题重点方法

- 分析代码生成 — 根据数据字典与任务描述生成清洗、统计、可视化代码，重点是字段对齐、口径显式化、缺失值与异常值
- 环境配置 — 处理依赖声明、版本锁定、包冲突与运行环境隔离，使同一份代码在不同机器上可安装、可启动，并能说
- 运行调试 — 让代码在真实数据上跑通，处理路径、编码、类型、内存与超时问题，保留每次执行的命令行、日志与退
- 结果验证 — 建立与金标准、数据字典、原始行数对照的校验环节，覆盖数值、结构、图表与查询结果，识别"跑通了
- 错误处理 — 对缺列、类型不符、依赖缺失、查询为空、数值溢出等异常给出识别、重试、降级、终止或转人工的明确

## 3. 输出契约（原题 output_contract）

| 内容 | 字段 | 标准 |
|-|-|-|
| 分析结果 | 清洗表、统计结果、图表、代码或查询结果 | 结果与数据字典、原始行数一致 |
| 操作证据 | 数据来源、哈希、步骤与参数、日志 | 他人可复跑并核对 |
| 失败与恢复 | 缺列、类型不符、依赖缺失、查询为空、数值溢出处置 | 不把未完成写成完成 |

## 4. 判断纪律

- 每条结论必须带 `[S#]` 引用；没有引用支撑的句子删掉或降级为不确定性条目。
- 引用定位四件套：`file + sha256 + pages/offset + 逐字原文窗口`，缺一不算可复核。
- 纳排/纳入理由必须落在协议条款与文件事实上，禁止"感觉相关"。
- 空命中如实报 unmatched_warning；扩词重试 ≤2 轮；仍空则声明"库内无匹配"。

## 5. 异常处理矩阵

| 情形 | 处置 | 禁止 |
|-|-|-|
| 文件损坏/不可读 | uncertainty.missing 记路径+原因；账本 warn | 静默丢弃 |
| 无文本层扫描件 | needs_tool → 按题目用 ocr-tool；不行则标 limits | 凭文件名补造内容 |
| 重名/同哈希多路径 | aliases 推荐 canonical，回链取 canonical | 双引物理副本当两处证据 |
| 同标题族字节不同 | near_duplicates 记版本变体，按题目指定版本回链 | 把旧版当终稿 |
| 空命中 | unmatched_warning 原样保留；扩词重试≤2轮 | 编造相似文献 |
| 超预算（接近64请求/900s） | 立即保存 honest partial，uncertainty 注明未完成案例号 | 硬扛到失败 |

| 依赖缺失 | 记 limits，给出声明式依赖清单与环境要求 | 静默假定环境可用 |
| 数值异常 | 标记异常行+原因，不改原始值；统计注明样本数变化 | 吞掉异常继续算 |
| 图表失败 | 退回到文本表并标 limits | 编造图表外观 |

## 6. 最小调用示例（示意输出，非正式案例结果）

```bash
printf '{"type":[".md",".pdf"],"need_text":true}' > /app/work/f.json
printf 'graph neural networks\nmolecular property prediction\n' > /app/work/terms.txt
python -I /skills/weekly-skill/common/engine/answer.py \
        --index /app/work/index.json \
        --question "<研究问题原文>" \
        --filter /app/work/f.json --terms-file /app/work/terms.txt \
        --top 25 --out /app/work/ans.json --ledger /app/work/ledger.json
```

示意返回（节选，**非真实案例结果**）：

```json
{"filter_audit":[{"constraint":"type=.md,.pdf","kept":2,"dropped_since_prev":3}],
  "hits":[{"rank":1,"file":"papers/x_v2_final.pdf",
           "sha256_file":"d4f6d8108fd2…","chunk_id":"d4f6d8108fd2#1",
           "pages":[2]}]}
```

## 7. 依赖、版本与安装

- **运行环境**：Python ≥ 3.8，仅标准库，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool / office-renderer / ocr-tool 等）。
- **引擎版本**：`bohrium-weekly-common@3.0`（2026-11-21，随仓库同版本锁定）。
- **安装**：无构建步骤。本目录内容放入评测约定位置（Skill 本体 `/skills/weekly-skill/`，共用引擎随附 `common/engine/`），按第 1/6 节命令直接调用。

## 8. 实例运行证据（本仓库自带，可复跑）

| 指标（9 题平均，含 1 题空命中诚实用例） | 本 Skill 链 | 朴素文件名搜索对照 |
|-|-|-|
| P@5 | **0.502** | 0.167 |
| R@5 | **0.824** | 0.139 |
| hash+offset 验证率 | **88.9%**（非空命中题 100%） | 0% |

v3 能力：PDF 分页定位与印刷页码偏移、office/PDF 元数据、近重复判定、字段加权 BM25（含 `_v2_final` 类文件名信号）、引用生成器（页码+锚句+逐字引用四件套）、空命中诚实报告。

边界如实声明：语料为自建合成科研库、本机 Windows 环境，上表证明方法差异，
平台侧最终分由组织方固定案例与官方 Harness 生成，不以本表冒充。

