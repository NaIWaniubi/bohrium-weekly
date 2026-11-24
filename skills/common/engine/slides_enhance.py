"""Slides-track enhancement: paper-to-slide planning, PPTX and previews."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _dict(value):
    return value if isinstance(value, dict) else {}


def plan_slides(paper_text, max_slides=12):
    lines = [line.strip() for line in str(paper_text or "").splitlines() if line.strip()]
    title = next((line.lstrip("# ") for line in lines if line.startswith("#")), lines[0] if lines else "Presentation")
    headings = [line.lstrip("# ") for line in lines if re.match(r"^#{1,3}\s+", line)]
    bullets = [line.lstrip("#-* ") for line in lines if len(line) > 30][:60]
    slides = [{"title": title, "bullets": ["Research presentation"], "notes": "Title slide"}]
    for i, heading in enumerate(headings[:max_slides - 2], 1):
        related = [b for b in bullets if any(term in b.lower() for term in heading.lower().split()[:3])][:4]
        slides.append({"title": heading, "bullets": related or bullets[i * 3:i * 3 + 4],
                       "notes": f"Section {i}: {heading}"})
    slides.append({"title": "Takeaways", "bullets": bullets[-4:] or ["Summary", "Limitations", "Future work"],
                   "notes": "Conclusion and questions"})
    return {"title": title, "slides": slides[:max_slides], "source_lines": len(lines)}


def generate_pptx(plan, out_path):
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        prs = Presentation()
        for slide_data in plan.get("slides", []):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = slide_data.get("title", "")
            body = slide.placeholders[1].text_frame
            body.clear()
            for i, bullet in enumerate(slide_data.get("bullets", [])):
                p = body.paragraphs[0] if i == 0 else body.add_paragraph()
                p.text = str(bullet)
                p.level = 0
            notes = slide.notes_slide.notes_text_frame
            notes.text = slide_data.get("notes", "")
        prs.save(out_path)
        return {"engine": "python-pptx", "path": out_path}
    except Exception as exc:
        return {"engine": "unavailable", "path": None, "error": str(exc)}


def render_preview(pptx_path, out_dir):
    if not pptx_path or not os.path.isfile(pptx_path):
        return {"pdf": None, "pngs": [], "error": "pptx not available"}
    try:
        with tempfile.TemporaryDirectory(prefix="bohrium-slides-") as tmp:
            proc = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, pptx_path],
                                  capture_output=True, text=True, timeout=120)
            pdf = os.path.join(tmp, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf")
            if proc.returncode == 0 and os.path.isfile(pdf):
                target = os.path.join(out_dir, "slides_preview.pdf")
                shutil.copy2(pdf, target)
                return {"pdf": target, "pngs": [], "engine": "soffice"}
    except Exception as exc:
        return {"pdf": None, "pngs": [], "error": str(exc)}
    return {"pdf": None, "pngs": [], "error": "preview renderer unavailable"}


def apply_slides(result, index, case, task_md, out_dir, work_dir=None):
    docs = []
    for row in _list(index.get("files")):
        rel = row.get("relpath")
        text = "\n".join(c.get("text", "") for c in _list(index.get("chunks")) if c.get("file") == rel)
        if text:
            docs.append((rel, text))
    paper_text = max(docs, key=lambda x: len(x[1]))[1] if docs else task_md
    plan = plan_slides(paper_text, int(case.get("max_slides", 12)))
    os.makedirs(out_dir, exist_ok=True)
    pptx_path = os.path.join(out_dir, "academic_slides.pptx")
    generated = generate_pptx(plan, pptx_path)
    preview = render_preview(pptx_path, out_dir)
    with open(os.path.join(out_dir, "slide_plan.json"), "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
    report = "# Slide report\n\n```json\n" + json.dumps({"plan": plan, "generation": generated, "preview": preview},
                                                                     ensure_ascii=False, indent=2) + "\n```"
    with open(os.path.join(out_dir, "slide_report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)
    return {"slides_status": {"state": "ok" if generated.get("path") else "partial", "generated_at": _now()},
            "slide_plan": plan, "generation": generated, "preview": preview, "report": report}
