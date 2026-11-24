"""Writing-track enhancement: outlines, cited drafts, polishing and formatting."""
import json
import os
import re
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+|\n+", text or "") if s.strip()]


def outline_document(task, evidence=None):
    task = str(task or "")
    evidence = _list(evidence)
    lower = task.lower()
    if any(k in lower for k in ("grant", "基金", "funding")):
        sections = ["specific aims", "significance", "innovation", "research strategy", "timeline", "budget justification"]
    elif any(k in lower for k in ("proposal", "开题", "研究计划")):
        sections = ["research question", "hypothesis", "literature gap", "objectives", "methods", "expected outcomes", "timeline", "risks"]
    elif any(k in lower for k in ("review", "rebuttal", "审稿", "回复")):
        sections = ["summary of comments", "point-by-point response", "changes made", "remaining limitations"]
    else:
        sections = ["abstract", "introduction", "related work", "methods", "results", "discussion", "conclusion"]
    return {"task": task, "sections": [{"name": s, "evidence_count": len(evidence), "status": "planned"} for s in sections]}


def draft_section(section, evidence, style="academic"):
    evidence = _list(evidence)
    if not evidence:
        return f"## {section}\n\nNo source evidence was provided; no substantive claim is drafted."
    lines = [f"## {section}", ""]
    for i, row in enumerate(evidence[:8], 1):
        if isinstance(row, dict):
            quote = row.get("quote") or row.get("text") or row.get("sentence") or ""
            source = row.get("file") or row.get("source") or "source"
        else:
            quote, source = str(row), "source"
        quote = re.sub(r"\s+", " ", quote).strip()
        if quote:
            lines.append(f"- 材料报告：{source} 指出“{quote[:280]}”[E{i}]。")
    lines += ["", "上述陈述仅复述所给材料；跨文献推断与结论强度需在人工复核后补充。"]
    return "\n".join(lines)


def polish_text(text):
    original = str(text or "")
    output = original
    replacements = {
        r"\bvery\s+": "", r"\breally\s+": "", r"\ba lot of\b": "many",
        r"\bdue to the fact that\b": "because", r"\bin order to\b": "to",
        r"\bit is important to note that\b": "", r"\bobviously\b": "",
        "非常": "", "显然": "", "众所周知": "",
    }
    for pattern, replacement in replacements.items():
        output = re.sub(pattern, replacement, output, flags=re.I)
    output = re.sub(r"[ \t]{2,}", " ", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output, {"original_chars": len(original), "polished_chars": len(output),
                    "note": "conservative surface polishing; scientific meaning unchanged"}


def format_references(refs, style="apa"):
    rows = []
    for i, ref in enumerate(_list(refs), 1):
        if isinstance(ref, str):
            rows.append(f"[{i}] {ref}")
            continue
        author = ref.get("author") or ref.get("authors") or "Unknown"
        year = ref.get("year") or "n.d."
        title = ref.get("title") or "Untitled"
        doi = ref.get("doi") or ""
        journal = ref.get("journal") or ""
        if style.lower() == "ieee":
            rows.append(f"[{i}] {author}, “{title},” {journal}, {year}. {doi}".strip())
        else:
            rows.append(f"{author} ({year}). {title}. {journal}. {doi}".strip())
    return rows


def check_structure(text):
    headings = re.findall(r"(?m)^#{1,4}\s*(.+)$", text or "")
    expected = ["abstract", "introduction", "methods", "results", "discussion", "conclusion"]
    lower = [h.lower() for h in headings]
    return {"headings": headings, "missing_expected": [h for h in expected if not any(h in x for x in lower)],
            "status": "ok" if headings else "no_headings"}


def generate_latex(text, title="Manuscript", authors="Author"):
    body = str(text or "").replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")
    return ("\\documentclass{article}\n\\usepackage[utf8]{inputenc}\n"
            "\\usepackage{amsmath,graphicx,booktabs}\n"
            f"\\title{{{title}}}\n\\author{{{authors}}}\n\\begin{{document}}\n\\maketitle\n"
            f"{body}\n\\end{{document}}\n")


def mindmap(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return "# Mind map\n\n" + "\n".join("- " + line.lstrip("# ") for line in lines[:80])


def rebuttal_matrix(comments, changes):
    return [{"comment": c, "response": "Thank you. We have revised the manuscript accordingly.",
             "change": changes.get(c, "not specified") if isinstance(changes, dict) else "not specified"}
            for c in _list(comments)]


def build_report(payload):
    return "# Writing report\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def apply_writing(result, index, case, task_md, out_dir, work_dir=None):
    question = case.get("question") or case.get("task") or task_md[:500]
    evidence = (result.get("answer") or {}).get("evidence_refs") or result.get("evidence_matrix") or []
    outline = outline_document(question, evidence)
    sections = {row["name"]: draft_section(row["name"], evidence[:5]) for row in outline["sections"]}
    full_text = "\n\n".join(sections.values())
    polished, polish_audit = polish_text(full_text)
    refs = format_references([r.get("metadata", r) for r in evidence], case.get("citation_style", "apa"))
    latex = generate_latex(polished, case.get("title", "Manuscript"), case.get("authors", "Author"))
    payload = {"outline": outline, "sections": sections, "polished_text": polished,
               "polish_audit": polish_audit, "references": refs, "structure": check_structure(polished),
               "latex": latex, "mindmap": mindmap(polished),
               "rebuttal": rebuttal_matrix(case.get("review_comments", []), case.get("changes", {})),
               "limitations": ["deterministic draft; agent must verify every scientific claim and citation"]}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "writing_report.md"), "w", encoding="utf-8") as handle:
        handle.write(build_report(payload))
    with open(os.path.join(out_dir, "manuscript.tex"), "w", encoding="utf-8") as handle:
        handle.write(latex)
    return {"writing_status": {"state": "ok", "generated_at": _now()}, **payload, "report": build_report(payload)}
