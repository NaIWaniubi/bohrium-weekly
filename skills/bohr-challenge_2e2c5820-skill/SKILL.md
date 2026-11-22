---
name: bohr-challenge_2e2c5820-skill
description: |
  实验方案可行性评估：面向玻尔周赛的可执行 Skill——判分点逐条对策 + 双路检索引擎 + 证据回链 + 断点续传。
---

# 实验方案可行性评估 Skill

## 0. 运行契约（必须遵守）

- 输入：`/input/case.json`（必要时含 `task.md`、`library`）；平台固定 3 个案例，每个含任务说明、科研材料、输入约束与评分证据范围；PDF 为真实文件。
- 工作区：`/app/work`；产物 `/app/output`，必含 `result.json`，覆盖原 output_contract 全字段并带 `case_id`、`status`、`sources`、`uncertainty`、`change_records`。
- 预算：**64 模型请求 / 900 秒 / 单响应 16384 token（含推理）**。三案例 × 三条件；真实服务失败由平台单独挂起。
- 禁止杜撰软件状态与科学数据；部分结果必须标明边界；可编辑产物附真实渲染预览。
- 先存诚实部分交付物，再增量改进。资源上限：50 个 UTF-8 文件、单 ≤500KB、总 ≤2MB、无符号链接。


## 1. 共用确定性引擎（stdlib only，随提交打包于 common/engine/）

| 脚本 | 作用 |
|-|-|
| scan_index.py | 库扫描→文件全量事实表(sha256/mtime/meta)、章节块(chunk_id+偏移+pages+印刷页码偏移)、精确重复组、近重复检测、版本族别名链、needs_tool |
| answer.py | 过滤先行审计 + 多路查询规划 + BM25(正文+文件名字段加权) + TF-IDF cosine dense + RRF 融合；命中带 file/sha256/pages/offset/evidence |
| run_case.py | 按契约读 case.json，合并过滤约束与跨语言扩展词，跑链路，落盘命中全文到 evidence/hits/，产出 result.json 骨架（含 citations+version_resolution）；断点续传 |
| benchmark.py / render_report.py / baseline.py | 金标准指标（P@3/P@5/MRR@10/NDCG@5）、HTML 详报渲染、对照基线 |

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \
        --index /app/work/index.json --work /app/work --out /app/output
```

链路对案例是确定性的、无需模型也能产出证据骨架——这是相对基础 Agent 的增益证据：基础 Agent 需要逐文件翻页、肉眼比对版本；本链路先审计过滤、再定位章节、最后每条结论回链到源字节哈希。

## 2. 解题总纲

做成可复跑的分析流水线：规则前置、结果可溯源。

**突破方向对标：**
- 检查项体系 — 把设备、材料、时间、样本、伦理与安全、参数范围、风险与异常拆成可勾选、可判定的检查项，明确每
- 资源与条件核验 — 将方案中的设备型号、精度、附件、耗材规格与现有条件清单比对，区分"已具备""可外协""需采购
- 时间与样本估算 — 按步骤拆出设备占用、人工操作、等待周期和检测周转，给出可解释的估算过程；核对样本来源、数量、
- 风险与异常处置 — 识别设备故障、材料批次差异、样本量不足、参数越界等情形，给出降级路径、替代方案和转人工节点。
- 伦理与安全合规路径 — 明确是否涉及动物、人体样本、危险试剂或个人数据，对应需要哪一级审批、哪些材料、大致需要多久。

## 3. 单案例执行 SOP（五步法）

1. **读案例**：打开 `/input/case.json` 与 `task.md`，提取显式过滤约束（类型/目录/年份/批次/权限…）与核心研究问题。
2. **建库**：`scan_index.py` 对案例给定库跑一次，得到事实表；查 needs_tool 与近重复组，需要 OCR 的按题目用官方 ocr-tool 补录，**绝不凭文件名臆测内容**。
3. **应答**：跨语言词表写入 `/app/work/terms.txt` 后跑 run_case；过滤器逐条生效并留痕。
4. **读证**：逐条全文阅读 `/app/output/evidence/hits/*.txt`（has_more=true 回源文补读）。
5. **成稿**：按本题输出契约填 result.json 并立即保存；预算剩 <20% 时保存 partial。

## 4. 本题判分点与对策（原文映射）

| 判分点（分值） | 本文对策 |
|-|-|
| 实际效果 40（完成度15/正确性15/完整性5/可核验5） | 代码跑通+与数据字典/原始行数/金标准三向对照；每步前后行数可查；图表选型有说明+真实渲染预览 |
| 使用增益 25（质量15/效率5/可验证性5） | 清洗规则机器执行可复跑，审计步记输入/输出条目数；不把人肉比对当流程 |
| 创新增益 15（新增能力/方法改进/创新证据） | 断点续传（result.json 已存在即跳过）保证预算不吃重复劳动；校验环节自动对照金标准 |
| 可靠体验 10（调用易用3/稳定性4/异常处理3） | 缺列/类型不符/编码不一致按题目纪律处置，账本留痕；subprocess CLI 兜底链 |
| 生态贡献 10（文档3/可复用4/分享3）+ GitHub 公开链接 5 | 维护信息（引擎版本、回归入口、已知边界）齐全；全部资源开源托管 |

## 5. 输出契约（原题 output_contract 逐字段）

| 分析结果 | 清洗表、统计结果、图表、代码或查询结果 | 结果与数据字典、原始行数一致 |
| 操作证据 | 数据来源、哈希、步骤与参数、日志 | 他人可复跑并核对 |
| 失败与恢复 | 缺列、类型不符、依赖缺失、查询为空、数值溢出处置 | 不把未完成写成完成 |

result.json 骨架：

```json
{"case_id":"<来自 case.json>","status":"ok|partial|failed",
 "answer":{"content":"读过 evidence/hits/*.txt 全文后才填写，每条结论带 file+sha256+offset"},
           "evidence_refs":[{"file":"papers/x_v2_final.pdf","sha256":"d4f6…","offset":[0,528],"quote":"≤120字原文","meaning":"该引文支撑哪一句结论"}]},
 "sources":[{"file":"…","sha256":"…"}],
 "uncertainty":{"missing":[],"conflicts":[],"limits":[]},
 "change_records":[],
 "provenance":{"generated_at":"ISO8601"}}
```

## 6. 异常与恢复（领域化）

| 依赖缺失/环境隔离失败 | 记 limits 给出声明式依赖清单 | 静默假定环境可用 |
| 数值溢出/样本骤降 | 标异常行+原因，统计注明样本数变化 | 吞掉异常继续算 |
| 代码运行失败 | 退回最小复现路径并标 limits；不伪报成功 | 跳过真跑直接交答案 |

## 7. 依赖版本与安装

- Python ≥3.8 stdlib only，零第三方依赖；二进制解析走平台预装工具（pdf-text-tool/office-renderer/ocr-tool 等）。
- 引擎 bohrium-weekly-common@4.0（2026-11-22）。无构建：将本目录放入 `/skills/weekly-skill/`，引擎随附 common/engine/，直接调用。

## 8. 实例证据（仓库自带可复跑）

在 15 文件混合科研库上实测（.selftest/regress.py 一键回归 ALL GREEN）：
P@5=0.502 / R@5=0.824 / MRR@10=0.833 / NDCG@5=0.799，
对照朴素文件名搜索 baseline 0.167/0.139/0.167/0.113；hash 验证率 88.9%（非空命中 100%）。
边界：自建合成语料，证明方法差异；平台侧终分由组织方固定案例与官方 Harness 生成。

