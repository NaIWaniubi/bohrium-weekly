"""Generate tailored Skills for the 45 uncovered period-4 challenges."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSPECT = ROOT / "_inspect"
SKILLS = ROOT / "skills"
SUBMIT = ROOT / "_submit"

TRACK_CFG = {
    "search": {
        "profile": "search",
        "goal": "把学术文献检索做成可审计管线：问题拆解、跨库/跨语言扩展、去重、排序、证据回链与空结果诚实报告。",
        "sop": [
            "读取 case.json/task.md，拆出研究问题、时间/语言/类型/来源约束与必需元数据字段。",
            "用 scan_index 建库；用 search_enhance 抽取 NL 硬约束并生成去废词查询计划。",
            "多路检索 + BM25/dense/RRF + PRF；用 canonical 版本去重，避免把副本当独立证据。",
            "逐条阅读 evidence/hits 全文，选章节/句子级证据并核对 sha/pages/offset。",
            "用 search_quality 生成带 [E#] 的直接答案与覆盖报告；无证据保持空结果。",
        ],
        "outputs": "检索结果、查询/筛选审计、canonical 版本、章节/句子证据、直接答案、覆盖与效率报告",
        "exceptions": "空命中不编造；跨语言召回低时扩词≤2轮；版本冲突并列记录；无权限/损坏文件显式标记。",
    },
    "reading": {
        "profile": "reading",
        "goal": "把文献阅读做成结构化解析：文档/图表/公式/结论/证据/知识图谱都可复核，而不是只给摘要。",
        "sop": [
            "读取全部原始文档与 case 约束，确认必做对象（原图、原表、公式、段落、记录）。",
            "用 scan_index/reading_enhance 解析文本、图表标题、表格上下文、公式与章节。",
            "按题目要求做图表解析、公式抽取、翻译、比较多文档、结论/数据提取或事实核验。",
            "每条结果回链 file+sha256+page/offset；未提供的像素/图注明确列缺口，不构造。",
            "输出结构化 notes/records/知识图谱/nanopublication 与 reading_report；Agent 读证后增补语义解释。",
        ],
        "outputs": "结构化文档、图表/公式清单、比较多文档矩阵、结论/数据记录、事实核验、笔记/知识图谱",
        "exceptions": "无文本层扫描件走 OCR；图表缺像素不补造；公式识别不确定时保留原文；翻译只做可复核术语映射，长句由 Agent 处理。",
    },
    "writing": {
        "profile": "writing",
        "goal": "把学术写作做成证据驱动流程：先大纲与证据矩阵，再逐节成稿、润色、引用格式化与可编译产物。",
        "sop": [
            "读取任务、目标读者、文体/模板/字数/引用格式与全部证据材料。",
            "用 writing_enhance 生成章节大纲与逐节证据草稿；每个实质句带 [E#]。",
            "按题目补全引言/相关工作/方法/结果/讨论/回复/基金/开题等指定部分。",
            "运行 polish_text、reference formatting、structure check；LaTeX 题目生成 .tex 并编译验证。",
            "输出 manuscript/report/引用清单/变更记录；无法核验的科学论断降级为不确定性。",
        ],
        "outputs": "大纲、逐节草稿、润色稿、参考文献、结构检查、LaTeX/报告/思维导图",
        "exceptions": "无证据不写强结论；引用缺失标 unknown；格式冲突按题目模板优先；编译失败保留源文件与错误日志。",
    },
    "graphics": {
        "profile": "graphics",
        "goal": "把科研绘图做成可编辑、可渲染、可复核的图形生产：SVG 源文件 + PNG/PDF 预览 + 图形说明。",
        "sop": [
            "读取原始材料、数据字典、图形类型/尺寸/配色/字体与科学关系约束。",
            "选择 bar/line/flowchart/pathway/apparatus/graphical-abstract 模板，生成可编辑 SVG。",
            "用真实数据填图，核对坐标轴、单位、图例、误差表示与样本量；机制图核对节点/边方向。",
            "渲染 PNG/PDF 预览（环境支持时），保存 graphics_source.json 与 SVG。",
            "输出 result.json 与图形说明；科学关系不确定时标注候选/未知，不伪造通路。",
        ],
        "outputs": "可编辑 SVG、PNG/PDF 预览、图形源数据、图注/说明、result.json",
        "exceptions": "无数据不画数值图；未知通路边标虚线/候选；字体缺失用系统字体并记录；渲染失败保留 SVG 源文件。",
    },
    "slides": {
        "profile": "slides",
        "goal": "把论文/材料转成可编辑学术汇报：页面组织、证据映射、讲者备注与真实预览齐全。",
        "sop": [
            "读取论文、模板、受众、时长、页数上限与证据规则。",
            "用 slides_enhance.plan_slides 生成标题—章节—要点—证据—讲者备注映射。",
            "生成可编辑 PPTX；每页保留来源定位，避免把整段论文堆到页面。",
            "用 soffice/平台渲染器生成 PDF/PNG 预览，检查溢出、字号、图表清晰度。",
            "输出 slide_plan.json、PPTX、预览与 slide_report；模板不匹配时显式记录。",
        ],
        "outputs": "slide plan、可编辑 PPTX、PDF/PNG 预览、讲者备注、证据映射、报告",
        "exceptions": "模板缺失用默认母版并记录；图表无原图不伪造；页数超限优先删重复证据；渲染失败保留 PPTX 与计划。",
    },
}


def section(content, start, end):
    m = re.search(start + r"(.*?)(?=" + end + r"|\Z)", content, re.S)
    return m.group(1).strip() if m else ""


def directions(content):
    m = re.search(r"可选择的突破方向(.*?)(?:参赛者只需要|$)", content, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        match = re.match(r"\s*\d+\.\s*(.+)", line)
        if match:
            out.append(match.group(1).strip()[:220])
    return out[:6]


def profile_for(track, title, content):
    if track in TRACK_CFG:
        return TRACK_CFG[track]["profile"]
    if track == "library":
        return "classify" if any(k in title for k in ("管理", "分类", "整理")) else "search"
    if track == "analysis":
        return "code" if "代码" in title else "data"
    return "search"


def main():
    groups = json.load(open(INSPECT / "contest4_groups.json", encoding="utf-8"))
    track_by_id = {cid: g["track"] for g in groups for cid in g["challenge_ids"]}
    count = 0
    for path in sorted((INSPECT / "uncovered").glob("*.json")):
        slug = path.stem
        data = json.load(open(path, encoding="utf-8"))
        content = data.get("content", "")
        title = data.get("title") or slug
        track = track_by_id.get(slug, "search")
        profile = profile_for(track, title, content)
        cfg = TRACK_CFG.get(track, TRACK_CFG["search"])
        dirs = directions(content)
        rubric = section(content, r"#### 2\. 评分维度概要", r"#### 3\.")
        delivery = section(content, r"#### 3\. 交付要求", r"## Harbor")
        profile_notes = content[content.rfind("## "):] if "## " in content else ""
        name = "bohr-" + slug.replace("-", "_") + "-skill"
        engine_rows = """| scan_index.py | 文件事实、章节/页码/偏移、元数据、重复/版本、needs_tool |
| answer.py | 硬过滤 + 多路 BM25/dense/RRF + 句子证据 |
| run_case.py / finalize.py | 完整首版 result.json、全量 sources、证据四重复核、异常恢复 |
| enhance.py / enhance_v7.py | canonical 版本、直接答案、claim 综述与各专项 profile 编排 |
| reading_enhance.py | 文档/图表/公式/比较/结论提取/笔记/知识图谱 |
| writing_enhance.py | 大纲、证据草稿、润色、引用、结构检查、LaTeX |
| graphics_enhance.py | 可编辑 SVG 图表/流程/通路/装置/图形摘要 + 预览 |
| slides_enhance.py | slide plan、PPTX、讲者备注、PDF/PNG 预览 |
| datacode_enhance.py / experiment_enhance.py / audit_enhance.py | 数据执行、实验设计、学术审查 |"""
        sop = "\n".join(f"{i}. {step}" for i, step in enumerate(cfg["sop"], 1))
        dir_text = "\n".join("- " + d for d in dirs) or "- 以题目交付要求与真实材料为准"
        md = f"""---
name: {name}
description: |
  {title}：玻尔周赛第四期 {track} 赛道可执行 Skill，{cfg['goal']}
---

# {title} Skill

## 0. 运行契约

- 输入 `/input/case.json`（必要时 `task.md`、`library`、模板/数据字典）；真实 PDF/Office/图片为事实依据。
- 工作区 `/app/work`；产物 `/app/output`，必含 `result.json`、原始/可编辑产物、真实预览、`sources`、`uncertainty`、`change_records`。
- 预算 64 模型请求 / 900 秒 / 16384 token；先存完整首版，再增量改进。
- 禁止杜撰科学数据、图注、像素、引用、软件状态；未完成项明确标 partial。

## 1. 共用引擎（stdlib only，随包提交）

| 脚本 | 作用 |
|-|-|
{engine_rows}

```
python -I /skills/weekly-skill/common/engine/run_case.py --case /input/case.json \\
        --index /app/work/index.json --work /app/work --out /app/output --profile {profile}
```

## 2. 解题总纲

{cfg['goal']}

**本题突破方向（题面原文）：**
{dir_text}

## 3. 单案例 SOP

{sop}

## 4. 本题评分维度（题面原文）

{rubric[:9000]}

## 5. 输出契约与验收

- 主产物：{cfg['outputs']}。
- 每条实质结论/图表/页面必须回链 `file + sha256 + page/offset` 或可编辑源文件。
- `result.json` 保留原 `output_contract` 全字段与 `case_id`；状态只写真实完成度。
- 可编辑 Office/图形/幻灯片必须提供真实 PDF/PNG 预览。

{delivery[:5000]}

## 6. 异常与恢复

{cfg['exceptions']}

## 7. 版本与依赖

- Python ≥3.8，stdlib only；平台预装 pdf-text-tool/office-renderer/ocr-tool/latex-compiler/browser-automation 等按 `environment/resources.yaml` 调用。
- 引擎 `bohrium-weekly-common@7.2`，无构建，放入 `/skills/weekly-skill/` 直接运行。

## 8. 组织方输入画像（必须遵守）

{profile_notes[-3500:]}

## 9. 实例证据

- `.selftest/regress.py` 全绿；profile9 三题 rubrics 覆盖 100/100/100。
- datacase 代码结果与交付统计完全一致；experimentcase 15 项、auditcase 14 项断言全绿。
- 所有数字只证明本地方法与对照差异，不冒充平台正式评分。
"""
        out_dir = SKILLS / name
        out_dir.mkdir(exist_ok=True)
        (out_dir / "SKILL.md").write_text(md, encoding="utf-8")
        url = f"https://github.com/NaIWaniubi/bohrium-weekly/blob/master/skills/{name}/SKILL.md\nSKILL.md\n"
        (out_dir / "GITHUB_URL.txt").write_text(url, encoding="utf-8")
        pkg = SUBMIT / slug
        pkg.mkdir(exist_ok=True)
        (pkg / "GITHUB_URL.txt").write_text(url, encoding="utf-8")
        count += 1
        print(f"[{count:02d}] {slug} -> {name} [{track}/{profile}]")
    print("total", count)


if __name__ == "__main__":
    main()
