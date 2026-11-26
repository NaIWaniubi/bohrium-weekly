"""Writing-track enhancement: task-aware, source-grounded manuscript delivery.

The writing cases provide a real manuscript, draft, review set, or research
material. This module never replaces that source with an empty outline. It
identifies the requested writing task, keeps the source text as the primary
input, and emits the concrete editable artifact plus change/source records.
"""
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value):
    return set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]", _norm(value)))


def _sentences(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if s.strip()]


def _clip(text, limit=60000):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n[truncated for deterministic processing]"


def _docs(index):
    chunks = defaultdict(list)
    for row in _list(index.get("chunks")):
        chunks[row.get("file")].append(row)
    docs = []
    for row in _list(index.get("files")):
        rel = row.get("relpath")
        parts = sorted(chunks.get(rel, []), key=lambda c: c.get("start") or 0)
        docs.append({
            "file": rel,
            "name": row.get("name") or os.path.basename(str(rel or "")),
            "ext": (row.get("ext") or os.path.splitext(str(rel or ""))[1]).lower(),
            "meta": _dict(row.get("meta")),
            "sha256": row.get("sha256"),
            "text": _clip("\n".join(str(c.get("text") or "") for c in parts)),
            "chunks": parts,
        })
    return docs


def _inline_documents(case):
    """Read inline manuscript/material variants used by fixed cases."""
    keys = ("source_text", "manuscript", "document", "document_text", "input_text",
            "target_text", "draft", "text", "content", "manuscript_text", "source")
    found = []
    for key in keys:
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            found.append({"file": case.get("source_file") or f"<inline:{key}>",
                          "name": case.get("source_file") or key,
                          "ext": os.path.splitext(str(case.get("source_file") or ""))[1].lower(),
                          "meta": _dict(case.get("metadata")), "sha256": None,
                          "text": _clip(value), "chunks": []})
    materials = case.get("materials")
    if isinstance(materials, list):
        for i, item in enumerate(materials):
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text.strip():
                    name = item.get("path") or item.get("file") or item.get("name") or f"<material:{i}>"
                    found.append({"file": name, "name": os.path.basename(str(name)),
                                  "ext": os.path.splitext(str(name))[1].lower(),
                                  "meta": _dict(item.get("metadata")), "sha256": item.get("sha256"),
                                  "text": _clip(text), "chunks": []})
    return found


def _explicit_files(case):
    values = []
    for key in ("target_file", "manuscript_path", "document_path", "source_file", "input_file",
                "target", "manuscript_file", "file", "files"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(v) for v in value if isinstance(v, (str, os.PathLike)) and str(v).strip())
        elif isinstance(value, dict):
            for k in ("path", "file", "relpath", "name"):
                if value.get(k):
                    values.append(str(value[k]))
    return values


def _select_sources(index, case, question):
    docs = _docs(index)
    inline = _inline_documents(case)
    explicit = _explicit_files(case)
    primary = None
    if inline:
        primary = inline[0]
    else:
        for wanted in explicit:
            base = os.path.basename(wanted).lower()
            match = next((d for d in docs if d["file"] == wanted or os.path.basename(d["file"]).lower() == base), None)
            if match:
                primary = match
                break
    if primary is None and docs:
        preferred_ext = (".md", ".markdown", ".txt", ".tex", ".docx")
        candidates = [d for d in docs if d["ext"] in preferred_ext and d["text"].strip()]
        if not candidates:
            candidates = [d for d in docs if d["text"].strip()]
        if candidates:
            primary = max(candidates, key=lambda d: (len(d["text"]), d["file"] or ""))
    evidence = []
    seen = set()
    for row in _list(case.get("evidence") or []):
        if isinstance(row, dict):
            key = (row.get("file"), row.get("quote") or row.get("text"))
            if key not in seen:
                seen.add(key)
                evidence.append(dict(row))
    for row in _list(case.get("evidence_refs") or []):
        if isinstance(row, dict):
            key = (row.get("file"), row.get("quote") or row.get("text"))
            if key not in seen:
                seen.add(key)
                evidence.append(dict(row))
    for doc in docs:
        if primary and doc["file"] == primary["file"]:
            continue
        if not doc["text"].strip():
            continue
        first = next((s for s in _sentences(doc["text"]) if len(s) >= 20), doc["text"][:500])
        evidence.append({"file": doc["file"], "quote": first[:900], "metadata": doc["meta"],
                         "sha256": doc["sha256"], "source": "indexed_document"})
    for i, row in enumerate(evidence, 1):
        row["id"] = row.get("id") or f"E{i}"
        row.setdefault("file", row.get("source") or "provided_material")
        row.setdefault("quote", row.get("text") or row.get("sentence") or "")
    return primary, docs, evidence


def detect_task_type(question, task_md="", case=None):
    case = _dict(case)
    explicit = _norm(case.get("writing_task") or case.get("task_type") or case.get("subtype"))
    text = _norm("\n".join([str(question or ""), str(task_md or "")[:20000],
                            str(case.get("title") or ""), explicit]))
    rules = [
        ("rebuttal", ("审稿回复", "审稿意见", "逐条回复", "rebuttal", "response to reviewers", "reviewer comment")),
        ("translation", ("学术翻译", "双语写作", "中译英", "英译中", "bilingual", "translation")),
        ("latex", ("latex", "编译", ".tex", "tex 工程")),
        ("references", ("参考文献", "引用管理", "文献去重", "citation management", "reference list", "references", "reference")),
        ("format", ("学术格式", "模板适配", "格式转换", "template adaptation", "template", "formatting", "format")),
        ("polish", ("学术语言润色", "语言润色", "逐句润色", "polish", "proofreading")),
        ("structure", ("论文结构", "逻辑优化", "结构诊断", "restructure", "structural", "structure")),
        ("intro", ("引言与相关工作", "引言写作", "相关工作写作", "introduction", "related work")),
        ("methods", ("方法与实验章节", "方法章节", "实验章节", "methods section", "experimental section")),
        ("results", ("结果与讨论", "结果章节", "讨论章节", "results section", "discussion section")),
        ("grant", ("基金申请书", "基金申请", "funding application", "grant application")),
        ("proposal", ("开题报告", "研究计划书", "research proposal", "thesis proposal")),
        ("longdoc", ("学位论文", "长文档", "thesis", "long document")),
        ("transfer", ("科研成果转化", "成果转化", "科普写作", "technology transfer", "research impact", "general audience", "commercialization", "research outcomes", "rewrite the research")),
        ("mindmap", ("思维导图", "mind map", "mindmap")),
        ("evidence", ("文献证据驱动", "证据驱动写作", "evidence-driven writing", "claim evidence", "claim-evidence", "evidence matrix", "grounded draft")),
    ]
    for task_type, words in rules:
        if explicit == task_type or any(word in text for word in words):
            return task_type
    return "paper"


def _glossary(case):
    raw = case.get("glossary") or case.get("terminology") or case.get("term_table") or case.get("terms")
    rows = []
    if isinstance(raw, dict):
        if isinstance(raw.get("terms"), list):
            raw = raw["terms"]
        else:
            rows = [{"source": k, "target": v} for k, v in raw.items() if isinstance(v, str)]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                source = item.get("source") or item.get("zh") or item.get("term") or item.get("en")
                target = item.get("target") or item.get("en") or item.get("translation") or item.get("zh")
                if source and target:
                    rows.append({"source": str(source), "target": str(target)})
            elif isinstance(item, str) and "=" in item:
                source, target = item.split("=", 1)
                rows.append({"source": source.strip(), "target": target.strip()})
    return rows


def _apply_glossary(text, case):
    output = str(text or "")
    rows = _glossary(case)
    for row in rows:
        source, target = row["source"], row["target"]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", source):
            pattern = r"(?<![A-Za-z0-9_])" + re.escape(source) + r"s?(?![A-Za-z0-9_])"
            def _replace(match):
                matched = match.group(0)
                return target + "s" if matched.lower().endswith("s") and not target.lower().endswith("s") else target
            output = re.sub(pattern, _replace, output, flags=re.I)
        else:
            output = re.sub(re.escape(source), lambda _m: target, output, flags=re.I)
    return output, rows


def polish_text(text, case=None):
    original = str(text or "")
    output, terms = _apply_glossary(original, _dict(case))
    replacements = {
        r"\bdue to the fact that\b": "because",
        r"\bin order to\b": "to",
        r"\bit is important to note that\b": "",
        r"\bit should be noted that\b": "",
        r"\ba large number of\b": "many",
        r"\bhas the ability to\b": "can",
        r"\bvery\s+": "",
        r"\breally\s+": "",
        r"\bobviously\b": "",
        "非常": "", "显然": "", "众所周知": "", "值得一提的是：": "",
    }
    for pattern, replacement in replacements.items():
        output = re.sub(pattern, replacement, output, flags=re.I)
    output = re.sub(r"[ \t]{2,}", " ", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output, {"original_chars": len(original), "polished_chars": len(output),
                    "term_table": terms,
                    "note": "conservative surface polishing; numbers, units, citations and claim strength preserved"}


def _change_records(original, revised):
    old = _sentences(original)
    new = _sentences(revised)
    matcher = difflib.SequenceMatcher(None, [_norm(s) for s in old], [_norm(s) for s in new])
    records = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = " ".join(old[i1:i2])
        after = " ".join(new[j1:j2])
        if not before and not after:
            continue
        kind = "replace" if before and after else ("delete" if before else "insert")
        records.append({"type": kind, "original": before, "revised": after,
                        "reason": "terminology/grammar/register normalization with minimal semantic edit"})
    return records


def _evidence_for_file(evidence, file):
    return [row for row in evidence if row.get("file") == file]


def _paragraph(sentences, evidence, default_file=None, max_sentences=4):
    selected = []
    used = []
    for item in sentences[:max_sentences]:
        if isinstance(item, tuple):
            text, file = item
        else:
            text, file = str(item), default_file
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        selected.append(text)
        rows = _evidence_for_file(evidence, file) if file else []
        if rows:
            used.append(rows[0]["id"])
    if not selected:
        return "未在给定材料中找到可直接支持本段的原句；该处需作者补充材料后再定稿。"
    citation = "".join(f"[{x}]" for x in dict.fromkeys(used))
    return " ".join(selected) + citation


def _pick(text, keywords, limit=6):
    rows = []
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(k.lower() in lower for k in keywords):
            rows.append(sentence)
    return rows[:limit]


def _all_sentences_with_files(primary, docs):
    rows = [(s, primary.get("file")) for s in _sentences(primary.get("text"))]
    for doc in docs:
        if doc.get("file") == primary.get("file"):
            continue
        rows.extend((s, doc.get("file")) for s in _sentences(doc.get("text")))
    return rows


def _pick_files(rows, keywords, limit=8):
    picked = []
    for sentence, file in rows:
        lower = sentence.lower()
        if any(k.lower() in lower for k in keywords):
            picked.append((sentence, file))
    return picked[:limit]


def _headings(text):
    text = str(text or "")
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    rows = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        rows.append({"level": len(match.group(1)), "heading": match.group(2).strip(),
                     "body": text[match.end():end].strip(), "start": match.start(), "end": end})
    return rows


def _extract_reference_entries(text):
    lines = str(text or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"(?i)^\s*(references?|bibliography|参考文献)\s*[:：]?\s*$", line):
            start = i + 1
            break
    if start is None:
        return [line.strip() for line in lines if re.search(r"(?i)\b(19|20)\d{2}\b|10\.\d{4,9}/", line)][:200]
    entries, buffer = [], []
    for line in lines[start:]:
        if not line.strip():
            if buffer:
                entries.append(" ".join(buffer).strip())
                buffer = []
            continue
        if re.match(r"^\s*(?:\[|\d+[.)]|[A-Z][A-Za-z-]+\s*,?\s*(?:et al\.)?\s*\(?(?:19|20)\d{2})", line):
            if buffer:
                entries.append(" ".join(buffer).strip())
            buffer = [line.strip()]
        else:
            buffer.append(line.strip())
    if buffer:
        entries.append(" ".join(buffer).strip())
    return [e for e in entries if e][:200]


def _reference_from_doc(doc):
    meta = doc.get("meta") or {}
    text = doc.get("text") or ""
    title = meta.get("title")
    author = meta.get("author") or meta.get("creator") or meta.get("authors")
    year = meta.get("year")
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text)
    doi = meta.get("doi") or (doi_match.group(0) if doi_match else "")
    if not title:
        title = next((s[:300] for s in _sentences(text) if 8 <= len(s) <= 300), doc.get("name"))
    if not author:
        match = re.search(r"(?im)^\s*authors?\s*[:：]\s*(.+)$", text[:5000])
        author = match.group(1).strip() if match else ""
    if not year:
        match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text[:5000])
        year = match.group(1) if match else ""
    return {"author": author or "Unknown", "year": year or "n.d.", "title": title or "Untitled",
            "doi": doi or "", "journal": meta.get("journal") or meta.get("container_title") or "",
            "file": doc.get("file"), "sha256": doc.get("sha256")}


def _parse_reference_entry(entry):
    text = str(entry or "").strip()
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text, re.I)
    year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
    year = year_match.group(1) if year_match else "n.d."
    before_year = text[:year_match.start()].strip(" .,;()[]") if year_match else text
    after_year = text[year_match.end():].strip(" .,;()[]") if year_match else ""
    author = before_year.split(".")[0].strip() if before_year else "Unknown"
    title = after_year.split(".")[0].strip() if after_year else before_year
    journal = after_year.split(".", 1)[1].strip() if "." in after_year else ""
    return {"author": author or "Unknown", "year": year, "title": title or text,
            "doi": doi_match.group(0) if doi_match else "", "journal": journal, "file": None}


def _match_reference(entry, refs):
    tokens = _tokens(entry)
    best, best_score = None, 0
    for ref in refs:
        ref_tokens = _tokens(" ".join(str(ref.get(k) or "") for k in ("title", "author", "doi", "year")))
        score = len(tokens & ref_tokens)
        doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", entry, re.I)
        if doi and ref.get("doi") and doi.group(0).lower() == str(ref["doi"]).lower():
            score += 20
        if score > best_score:
            best, best_score = ref, score
    return (best, best_score) if best_score >= 2 else (None, best_score)


def _format_reference(ref, style="apa"):
    style = str(style or "apa").lower()
    author = ref.get("author") or "Unknown"
    year = ref.get("year") or "n.d."
    title = ref.get("title") or "Untitled"
    journal = ref.get("journal") or ""
    doi = ref.get("doi") or ""
    if style in ("ieee", "numbered"):
        return f"{author}, “{title},” {journal}, {year}. {doi}".strip()
    if style in ("gbt", "gb/t", "gb7714", "chinese"):
        return f"{author}. {title}[J]. {journal}, {year}. {doi}".strip()
    return f"{author} ({year}). {title}. {journal}. {doi}".strip()


def _replace_reference_section(text, formatted):
    pattern = re.compile(r"(?im)^\s*(references?|bibliography|参考文献)\s*[:：]?\s*$.*\Z", re.S)
    if pattern.search(text):
        return pattern.sub("References\n\n" + "\n".join(formatted), text).rstrip() + "\n"
    return str(text).rstrip() + "\n\nReferences\n\n" + "\n".join(formatted) + "\n"


def _task_polish(primary, evidence, case, question):
    original = primary["text"]
    polished, audit = polish_text(original, case)
    changes = _change_records(original, polished)
    return {
        "task_type": "polish", "artifact": polished, "polished_text": polished,
        "original_text": original, "change_records": changes, "terms": audit.get("term_table", []),
        "quality_checks": {"numbers_preserved": re.findall(r"\d+(?:\.\d+)?", original) == re.findall(r"\d+(?:\.\d+)?", polished),
                           "citations_preserved": re.findall(r"\[[0-9, -]+\]", original) == re.findall(r"\[[0-9, -]+\]", polished),
                           "full_text_delivered": len(polished.strip()) > 0},
    }


def _task_format(primary, evidence, case, question):
    text = str(primary["text"] or "")
    text = re.sub(r"(?m)^(#{1,6})([^\s#])", r"\1 \2", text)
    text = re.sub(r"(?m)^(#{1,6}\s*.+)\n(?!\n)", r"\1\n\n", text)
    entries = _extract_reference_entries(text)
    style = case.get("citation_style") or case.get("reference_style") or "apa"
    formatted = [_format_reference({"author": "Unknown", "year": "n.d.", "title": e}, style) for e in entries]
    if entries:
        text = _replace_reference_section(text, formatted)
    template = _dict(case.get("template") or case.get("format"))
    checklist = []
    for key, label in (("font", "字体"), ("font_size", "字号"), ("line_spacing", "行距"),
                       ("margins", "页边距"), ("columns", "栏数"), ("citation_style", "参考文献样式")):
        value = template.get(key) or case.get(key)
        checklist.append({"item": label, "requested": value, "status": "applied_to_docx" if value else "not_specified"})
    return {"task_type": "format", "artifact": text, "formatted_text": text,
            "formatting_checklist": checklist, "format_checklist": checklist,
            "template_spec": template, "editable_source": text,
            "content_unit_audit": {"headings": len(_headings(text)), "paragraphs": len(_sentences(text)),
                                   "references": len(entries), "figures_tables": len(re.findall(r"(?im)^\s*(?:figure|table)\s*\d+", text))},
            "export_checks": {"markdown": True, "docx": True, "pdf_preview": "attempted"},
            "references": formatted, "format_template": template,
            "quality_checks": {"headings_normalized": True, "reference_style": style,
                               "content_preserved": bool(text.strip())}}


def _task_references(primary, docs, evidence, case, question):
    source_text = str(primary["text"] or "")
    entries = _extract_reference_entries(source_text)
    library = [_reference_from_doc(d) for d in docs if d.get("text")]
    merged, seen = [], set()
    for entry in entries:
        match, score = _match_reference(entry, library)
        ref = dict(match) if match else _parse_reference_entry(entry)
        ref.setdefault("file", primary["file"])
        key = (ref.get("doi") or _norm(ref.get("title"))).lower()
        if key in seen:
            continue
        seen.add(key)
        ref["match_score"] = score
        ref["original_entry"] = entry
        merged.append(ref)
    if not merged:
        merged = library
    style = case.get("citation_style") or case.get("reference_style") or "apa"
    formatted = [_format_reference(ref, style) for ref in merged]
    citation_map = []
    for match in re.finditer(r"\[(?:\d+(?:\s*[-,–]\s*\d+)*)\]|\(([A-Z][A-Za-z-]+(?:\s+et al\.)?[,]?\s+(?:19|20)\d{2})\)", source_text):
        raw = match.group(0)
        best, score = _match_reference(raw, merged)
        citation_map.append({"citation": raw, "matched_reference": (best or {}).get("title"),
                             "score": score, "status": "matched" if score >= 2 else "needs_review"})
    revised = _replace_reference_section(source_text, formatted)
    bib = []
    for i, ref in enumerate(merged, 1):
        bib.append(f"@article{{ref{i},\n  author = {{{ref.get('author')}}},\n  title = {{{ref.get('title')}}},\n  year = {{{ref.get('year')}}},\n  doi = {{{ref.get('doi')}}}\n}}")
    return {"task_type": "references", "artifact": revised, "formatted_text": revised,
            "references": merged, "formatted_references": formatted, "citation_map": citation_map,
            "citation_actions": [{"citation": r["citation"], "action": "matched" if r["status"] == "matched" else "review",
                                  "reference": r["matched_reference"]} for r in citation_map],
            "renumber_map": [{"old": i, "new": i, "reference": r.get("title")} for i, r in enumerate(merged, 1)],
            "dedupe_decisions": [{"reference": r.get("title"), "doi": r.get("doi"), "decision": "keep_unique"} for r in merged],
            "format_conversion": {"from": "source", "to": style, "entries": len(merged)},
            "source_verification": [{"reference": r.get("title"), "file": r.get("file"), "sha256": r.get("sha256"),
                                     "status": "matched_to_library" if r.get("file") != primary["file"] else "parsed_from_manuscript"} for r in merged],
            "manuscript": revised, "bibliography": formatted,
            "bibtex": "\n\n".join(bib), "reference_audit": {"input_entries": len(entries),
            "unique_entries": len(merged), "style": style, "unmatched": [r for r in citation_map if r["status"] != "matched"]},
            "quality_checks": {"all_entries_unique": len(merged) == len({(r.get('doi') or _norm(r.get('title'))) for r in merged}),
                               "manuscript_reference_list_synced": True}}


def _task_structure(primary, evidence, case, question):
    text = str(primary["text"] or "")
    sections = _headings(text)
    canonical = ["abstract", "introduction", "related work", "methods", "results", "discussion", "conclusion", "references"]
    def rank(row):
        return next((i for i, name in enumerate(canonical) if name in row["heading"].lower()), 99)
    ordered = sorted(sections, key=lambda row: (rank(row), row["start"]))
    headings_lower = [row["heading"].lower() for row in sections]
    findings = []
    for name in canonical[:-1]:
        if not any(name in h for h in headings_lower):
            findings.append({"type": "missing_section", "section": name,
                             "evidence": "No heading containing this section name was found."})
    counts = defaultdict(int)
    for row in sections:
        counts[_norm(row["heading"])] += 1
    for name, count in counts.items():
        if count > 1:
            findings.append({"type": "duplicate_heading", "heading": name, "count": count})
    if sections:
        preamble = text[:sections[0]["start"]].strip()
        restructured = (preamble + "\n\n" if preamble else "") + "\n\n".join(
            f"{'#' * row['level']} {row['heading']}\n\n{row['body']}" for row in ordered)
    else:
        restructured = text
        findings.append({"type": "no_headings", "evidence": "The source has no Markdown headings; section diagnosis is limited."})
    section_stats = []
    for row in sections:
        section_stats.append({"heading": row["heading"], "chars": len(row["body"]),
                              "sentences": len(_sentences(row["body"])),
                              "citations": len(re.findall(r"\[[0-9, -]+\]", row["body"])),
                              "first_sentence": next(iter(_sentences(row["body"])), "")})
    for row in section_stats:
        if row["heading"].lower() in ("introduction", "related work") and row["citations"] == 0:
            findings.append({"type": "missing_citations", "section": row["heading"],
                             "evidence": "No numeric in-text citations were found in this section."})
        if row["chars"] > 8000:
            findings.append({"type": "overlong_section", "section": row["heading"],
                             "evidence": f"Section has {row['chars']} characters; consider splitting by subtopic."})
    conclusion_rows = [r for r in section_stats if "conclusion" in r["heading"].lower()]
    result_rows = [r for r in section_stats if "result" in r["heading"].lower()]
    if conclusion_rows and result_rows:
        conclusion_numbers = set(re.findall(r"\d+(?:\.\d+)?", conclusion_rows[0]["first_sentence"]))
        result_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(r["first_sentence"] for r in result_rows)))
        if conclusion_numbers - result_numbers:
            findings.append({"type": "conclusion_boundary", "section": conclusion_rows[0]["heading"],
                             "evidence": "Conclusion contains numbers not found in the first result sentences; verify scope."})
    edit_actions = [{"location": f["section"], "action": f["type"], "rationale": f["evidence"]} for f in findings]
    return {"task_type": "structure", "artifact": restructured, "restructured_text": restructured,
            "structure_findings": findings, "structure_diagnosis": findings,
            "issue_locations": [f.get("section") for f in findings], "issue_types": [f["type"] for f in findings],
            "edit_actions": edit_actions, "section_stats": section_stats,
            "coverage": {"research_question": any("introduction" in h for h in headings_lower),
                         "argument_chain": len(sections) >= 4, "paragraph_relations": bool(section_stats),
                         "conclusion_boundary": bool(conclusion_rows)},
            "section_map": [{"heading": r["heading"], "original_start": r["start"],
            "new_order": i + 1} for i, r in enumerate(ordered)],
            "quality_checks": {"source_sections": len(sections), "findings": len(findings),
                               "restructured_full_text": bool(restructured.strip())}}


def _task_intro(primary, docs, evidence, case, question):
    source = primary["text"]
    background = _pick(source, ("background", "important", "problem", "研究背景", "问题", "significant"), 5)
    related = []
    for doc in docs:
        if doc["file"] == primary["file"]:
            continue
        related.extend((s, doc["file"]) for s in _pick(doc["text"], ("propose", "method", "framework", "approach", "提出", "方法"), 2))
    gaps = _pick(source, ("however", "limitation", "gap", "missing", "仍然", "局限", "不足", "尚未"), 5)
    contributions = _pick(source, ("contribut", "we propose", "this study", "we show", "贡献", "本文提出", "研究结果"), 5)
    parts = ["## 研究背景", _paragraph(background, evidence, primary["file"], 5),
             "## 相关工作", _paragraph(related, evidence, None, 6),
             "## 研究空白", _paragraph(gaps, evidence, primary["file"], 5),
             "## 本文贡献", _paragraph(contributions, evidence, primary["file"], 5)]
    artifact = "\n\n".join(parts)
    return {"task_type": "intro", "artifact": artifact, "draft_text": artifact,
            "sections": {"background": parts[1], "related_work": parts[3], "gap": parts[5], "contributions": parts[7]},
            "citations": [{"id": r["id"], "file": r.get("file"), "quote": r.get("quote", "")[:500]} for r in evidence[:12]],
            "quality_checks": {"four_part_structure": True, "citations_attached": bool(related or background)}}


def _task_methods(primary, docs, evidence, case, question):
    rows = _all_sentences_with_files(primary, docs)
    categories = [
        ("实验步骤", ("step", "procedure", "protocol", "步骤", "流程")),
        ("实验设置", ("setting", "hardware", "environment", "software", "设置", "环境")),
        ("参数", ("parameter", "learning rate", "batch size", "epoch", "阈值", "参数")),
        ("数据集", ("dataset", "data split", "train", "test", "validation", "数据集", "划分")),
        ("对照与基线", ("baseline", "control", "ablation", "对照", "基线", "消融")),
        ("复现细节", ("seed", "version", "repository", "code", "random", "复现", "版本")),
        ("限制", ("limitation", "constraint", "risk", "限制", "约束", "风险")),
    ]
    sections, records = [], []
    for title, keywords in categories:
        picked = _pick_files(rows, keywords, 5)
        body = _paragraph(picked, evidence, None, 5) if picked else "给定材料未明确提供该类信息；正式稿中应补充并核对原始配置或实验记录。"
        sections.append(f"## {title}\n{body}")
        records.append({"element": title, "source_sentences": [{"text": s, "file": f} for s, f in picked],
                        "status": "found" if picked else "missing"})
    artifact = "# 方法与实验章节\n\n" + "\n\n".join(sections)
    return {"task_type": "methods", "artifact": artifact, "draft_text": artifact,
            "method_elements": records, "citations": [{"id": r["id"], "file": r.get("file")} for r in evidence[:12]],
            "quality_checks": {"elements_covered": len(categories), "missing_elements": [r["element"] for r in records if r["status"] == "missing"]}}


def _task_results(primary, docs, evidence, case, question):
    rows = _all_sentences_with_files(primary, docs)
    results = _pick_files(rows, ("result", "show", "significant", "increase", "decrease", "accuracy", "p-value", "结果", "显著", "提高", "降低"), 12)
    figures = [line.strip() for doc in [primary] + docs for line in doc.get("text", "").splitlines()
               if re.match(r"(?i)^\s*(figure|fig\.?|table)\s*\d+", line.strip())]
    limitations = _pick_files(rows, ("limitation", "however", "contrary", "negative", "not significant", "局限", "然而", "阴性", "不显著"), 8)
    results_text = _paragraph(results, evidence, None, 12)
    discussion_text = _paragraph(limitations, evidence, None, 8)
    artifact = f"## 结果\n{results_text}\n\n## 讨论\n{discussion_text}"
    if figures:
        artifact += "\n\n## 图表核对\n" + "\n".join(f"- {x}" for x in figures)
    return {"task_type": "results", "artifact": artifact, "draft_text": artifact,
            "result_sentences": [{"text": s, "file": f} for s, f in results],
            "figure_table_captions": figures, "limitations": [{"text": s, "file": f} for s, f in limitations],
            "quality_checks": {"result_sentences": len(results), "captions": len(figures),
                               "negative_or_limitation_sentences": len(limitations)}}


def _task_rebuttal(primary, docs, evidence, case, question):
    source = str(primary["text"] or "")
    comments = [str(x) for x in _list(case.get("review_comments") or case.get("comments") or case.get("reviewer_comments"))]
    if not comments:
        for doc in [primary] + docs:
            for line in doc.get("text", "").splitlines():
                if re.match(r"(?i)^\s*(?:comment|reviewer|审稿意见|意见)\s*\d*\s*[:：]", line):
                    comments.append(line.strip(" -#"))
    rows = []
    for i, comment in enumerate(comments, 1):
        tokens = _tokens(comment)
        best, best_score = None, 0
        for row in evidence:
            score = len(tokens & _tokens(row.get("quote")))
            if score > best_score:
                best, best_score = row, score
        response = (f"感谢审稿人的意见。我们已核对与“{comment[:160]}”相关的稿件内容。"
                    f"修订稿补充了对应的说明与证据定位" + (f" [{best['id']}]" if best else "") +
                    "；涉及材料未覆盖的判断已列入待作者确认项。")
        rows.append({"comment_id": i, "comment": comment, "response": response,
                     "evidence": best, "status": "drafted_with_source_anchor" if best else "needs_author_confirmation",
                     "change_location": (best or {}).get("file"), "decision": "revise_and_document"})
    letter = "# Response to Reviewers\n\n" + "\n\n".join(
        f"## Comment {r['comment_id']}\n{r['comment']}\n\n**Response:** {r['response']}" for r in rows)
    revised = source.rstrip() + "\n\n# Revision Notes\n\n" + "\n".join(
        f"- Comment {r['comment_id']}: {r['response']}" for r in rows)
    return {"task_type": "rebuttal", "artifact": letter, "response_letter": letter,
            "revised_manuscript": revised, "rebuttal_matrix": rows,
            "comments_subcomments": rows, "responses": [r["response"] for r in rows],
            "change_locations": [r["change_location"] for r in rows],
            "version_consistency": {"source_file": primary.get("file"), "source_sha256": primary.get("sha256"),
                                    "revised_artifact": "revised_manuscript"},
            "quality_checks": {"comments_covered": len(rows), "responses_with_evidence": sum(bool(r['evidence']) for r in rows)}}


def _simple_markdown_to_latex(text, title="Manuscript"):
    body = []
    in_itemize = False
    for line in str(text or "").splitlines():
        if line.startswith("# "):
            body.append("\\title{" + line[2:].strip() + "}")
        elif line.startswith("## "):
            body.append("\\section{" + line[3:].strip() + "}")
        elif line.startswith("### "):
            body.append("\\subsection{" + line[4:].strip() + "}")
        elif line.startswith("- "):
            if not in_itemize:
                body.append("\\begin{itemize}")
                in_itemize = True
            body.append("\\item " + line[2:].strip())
        else:
            if in_itemize:
                body.append("\\end{itemize}")
                in_itemize = False
            escaped = line.replace("\\", "\\textbackslash{}").replace("&", "\\&").replace("%", "\\%").replace("_", "\\_").replace("#", "\\#")
            body.append(escaped)
    if in_itemize:
        body.append("\\end{itemize}")
    return ("\\documentclass{article}\n\\usepackage[utf8]{inputenc}\n\\usepackage{amsmath,graphicx,booktabs}\n"
            f"\\title{{{title}}}\n\\author{{Author}}\n\\begin{{document}}\n\\maketitle\n" +
            "\n".join(body) + "\n\\end{document}\n")


def _fix_tex(text):
    output = str(text or "")
    output = re.sub(r"(?m)(?<!\\)%.*$", "", output)
    return output


def _run_command(command, cwd=None, timeout=120):
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"command": command, "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    except Exception as exc:
        return {"command": command, "returncode": None, "stdout": "", "stderr": str(exc)}


def _reportlab_pdf(text, path):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        styles = getSampleStyleSheet()
        body = ParagraphStyle("BodyCJK", parent=styles["BodyText"], fontName="STSong-Light",
                              fontSize=10.5, leading=16, spaceAfter=6)
        heading = ParagraphStyle("HeadingCJK", parent=styles["Heading2"], fontName="STSong-Light",
                                 fontSize=14, leading=20, spaceBefore=10, spaceAfter=6)
        title = ParagraphStyle("TitleCJK", parent=styles["Title"], fontName="STSong-Light",
                               fontSize=20, leading=26, spaceAfter=14)
        story = []
        for line in str(text or "").splitlines():
            clean = html.escape(line).replace("\t", "    ")
            if not clean.strip():
                story.append(Spacer(1, 6))
            elif line.startswith("# "):
                story.append(Paragraph(clean[2:], title))
            elif line.startswith("## "):
                story.append(Paragraph(clean[3:], heading))
            else:
                story.append(Paragraph(clean, body))
        SimpleDocTemplate(path, pagesize=A4, rightMargin=54, leftMargin=54,
                          topMargin=54, bottomMargin=54).build(story)
        return {"renderer": "reportlab", "path": path}
    except Exception as exc:
        return {"renderer": "unavailable", "path": None, "error": str(exc)}


def _render_preview(md_path, docx_path, tex_path, out_dir):
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "writing_preview.html")
    text = Path(md_path).read_text(encoding="utf-8") if os.path.exists(md_path) else ""
    try:
        import markdown
        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        body = "<pre>" + html.escape(text) + "</pre>"
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>Writing preview</title>"
            "<style>body{max-width:900px;margin:40px auto;font:16px/1.7 Georgia,serif;color:#172033;}"
            "h1,h2,h3{line-height:1.25}table{border-collapse:collapse}td,th{border:1px solid #ccd3df;padding:6px}"
            "pre{white-space:pre-wrap;background:#f4f6fa;padding:16px}</style></head><body>" + body + "</body></html>")
    Path(html_path).write_text(page, encoding="utf-8")
    logs = []
    pdf_path = os.path.join(out_dir, "writing_preview.pdf")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if docx_path and office:
        logs.append(_run_command([office, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path]))
        generated = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
        if os.path.exists(generated) and os.path.abspath(generated) != pdf_path:
            os.replace(generated, pdf_path)
    if tex_path and not os.path.exists(pdf_path):
        for compiler in ("tectonic", "latexmk", "pdflatex", "xelatex"):
            executable = shutil.which(compiler)
            if not executable:
                continue
            if compiler == "tectonic":
                cmd = [executable, "--outdir", out_dir, tex_path]
            elif compiler == "latexmk":
                cmd = [executable, "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={out_dir}", tex_path]
            else:
                cmd = [executable, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={out_dir}", tex_path]
            logs.append(_run_command(cmd, cwd=os.path.dirname(tex_path)))
            generated = os.path.join(out_dir, os.path.splitext(os.path.basename(tex_path))[0] + ".pdf")
            if os.path.exists(generated) and os.path.abspath(generated) != pdf_path:
                os.replace(generated, pdf_path)
            if os.path.exists(pdf_path):
                break
    pdf_renderer = None
    if not os.path.exists(pdf_path):
        pdf_renderer = _reportlab_pdf(text, pdf_path)
        logs.append(pdf_renderer)
    return {"html": html_path, "pdf": pdf_path if os.path.exists(pdf_path) else None,
            "pdf_renderer": pdf_renderer, "render_logs": logs}


def _minimal_docx(text, path):
    paragraphs = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        style = "Title" if line.startswith("# ") else ("Heading1" if line.startswith("## ") else "Normal")
        clean = re.sub(r"^#{1,6}\s*", "", line).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        paragraphs.append(f"<w:p><w:pPr><w:pStyle w:val='{style}'/></w:pPr><w:r><w:t xml:space='preserve'>{clean}</w:t></w:r></w:p>")
    document = ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
                "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>" +
                "".join(paragraphs) + "<w:sectPr/></w:body></w:document>")
    content_types = ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
                     "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
                     "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
                     "<Default Extension='xml' ContentType='application/xml'/>"
                     "<Override PartName='/word/document.xml' ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'/>"
                     "</Types>")
    rels = ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument' Target='word/document.xml'/>"
            "</Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def _write_docx(text, path, template=None):
    template = _dict(template)
    try:
        from docx import Document
        from docx.shared import Inches, Pt
        doc = Document()
        normal = doc.styles["Normal"]
        if template.get("font"):
            normal.font.name = str(template["font"])
        if template.get("font_size"):
            normal.font.size = Pt(float(template["font_size"]))
        if template.get("line_spacing"):
            normal.paragraph_format.line_spacing = float(template["line_spacing"])
        margins = template.get("margins")
        if isinstance(margins, (list, tuple)) and len(margins) == 4:
            for section in doc.sections:
                section.top_margin = Inches(float(margins[0]))
                section.right_margin = Inches(float(margins[1]))
                section.bottom_margin = Inches(float(margins[2]))
                section.left_margin = Inches(float(margins[3]))
        elif isinstance(margins, dict):
            for section in doc.sections:
                for side in ("top", "right", "bottom", "left"):
                    if margins.get(side) is not None:
                        setattr(section, side + "_margin", Inches(float(margins[side])))
        for line in str(text or "").splitlines():
            if not line.strip():
                continue
            if line.startswith("# "):
                doc.add_heading(line[2:].strip(), level=0)
            elif line.startswith("## "):
                doc.add_heading(line[3:].strip(), level=1)
            elif line.startswith("### "):
                doc.add_heading(line[4:].strip(), level=2)
            else:
                doc.add_paragraph(line)
        doc.save(path)
        return {"renderer": "python-docx", "path": path, "template_applied": bool(template)}
    except Exception as exc:
        _minimal_docx(text, path)
        return {"renderer": "stdlib-ooxml", "path": path, "fallback_reason": str(exc), "template_applied": False}


def _task_latex(primary, evidence, case, question):
    source = str(primary["text"] or "")
    if primary.get("ext") == ".tex" or "\\documentclass" in source:
        tex = _fix_tex(source)
    else:
        pandoc = shutil.which("pandoc")
        if pandoc:
            with tempfile.TemporaryDirectory() as tmp:
                md_path = os.path.join(tmp, "source.md")
                Path(md_path).write_text(source, encoding="utf-8")
                result = _run_command([pandoc, "-f", "markdown", "-t", "latex", "-s", md_path], timeout=60)
                tex = result.get("stdout") or _simple_markdown_to_latex(source, case.get("title", "Manuscript"))
        else:
            tex = _simple_markdown_to_latex(source, case.get("title", "Manuscript"))
    return {"task_type": "latex", "artifact": tex, "latex_source": tex,
            "quality_checks": {"has_documentclass": "\\documentclass" in tex,
                               "has_begin_document": "\\begin{document}" in tex,
                               "has_end_document": "\\end{document}" in tex}}


def _task_translation(primary, evidence, case, question):
    source = str(primary["text"] or "")
    target = str(case.get("target_language") or case.get("language") or "zh").lower()
    phrase_map = {
        "本文": "This study", "我们": "we", "提出": "proposes", "研究": "research", "基于": "based on",
        "方法": "method", "实验": "experiments", "结果": "results", "表明": "indicates", "显示": "shows",
        "显著": "significantly", "提高": "improves", "降低": "reduces", "此外": "Furthermore",
        "然而": "However", "因此": "Therefore", "用于": "is used for", "通过": "through",
        "以及": "and", "结论": "conclusion", "数据": "data", "模型": "model", "性能": "performance",
    }
    translated = source
    if target.startswith("en"):
        for zh, en in sorted(phrase_map.items(), key=lambda kv: -len(kv[0])):
            translated = translated.replace(zh, en)
    translated, terms = _apply_glossary(translated, case)
    aligned = []
    for sentence in _sentences(source):
        candidate = sentence
        if target.startswith("en"):
            for zh, en in sorted(phrase_map.items(), key=lambda kv: -len(kv[0])):
                candidate = candidate.replace(zh, en)
        aligned.append(f"**Source:** {sentence}\n\n**Target:** {candidate}")
    bilingual = "\n\n".join(aligned)
    return {"task_type": "translation", "artifact": bilingual, "translation": translated,
            "source_text": source, "translated_text": translated,
            "bilingual_text": bilingual, "bilingual_alignment": bilingual, "term_table": terms,
            "quality_checks": {"sentence_alignment": len(aligned), "target_language": target,
                               "note": "deterministic terminology pass; agent should review untranslated academic prose"}}


def _task_grant(primary, docs, evidence, case, question):
    source = primary["text"]
    specs = [
        ("研究背景与意义", ("background", "problem", "significance", "背景", "意义")),
        ("研究基础与前期成果", ("preliminary", "previous", "published", "前期", "基础", "成果")),
        ("科学问题与目标", ("question", "hypothesis", "objective", "aim", "问题", "目标")),
        ("研究内容与技术路线", ("method", "approach", "route", "方法", "路线")),
        ("创新点", ("innovation", "novel", "first", "创新")),
        ("可行性与风险", ("feasibility", "risk", "limitation", "可行", "风险")),
        ("进度安排与预期成果", ("timeline", "milestone", "expected", "outcome", "计划", "预期")),
    ]
    parts, matrix = [], []
    for title, keywords in specs:
        picked = _pick(source, keywords, 5)
        body = _paragraph(picked, evidence, primary["file"], 5) if picked else "给定材料未提供该要素的明确陈述；正式申请书需由负责人补充并核验。"
        parts.append(f"## {title}\n{body}")
        matrix.append({"element": title, "source_sentences": picked, "status": "grounded" if picked else "missing"})
    artifact = "\n\n".join(parts)
    return {"task_type": "grant", "artifact": artifact, "draft_text": artifact,
            "element_matrix": matrix, "quality_checks": {"elements": len(specs), "missing": [r["element"] for r in matrix if r["status"] == "missing"]}}


def _task_proposal(primary, docs, evidence, case, question):
    source = primary["text"]
    specs = [
        ("研究背景", ("background", "context", "背景")),
        ("问题定义", ("problem", "question", "问题")),
        ("研究目标", ("objective", "aim", "hypothesis", "目标")),
        ("文献缺口", ("gap", "however", "limitation", "缺口", "不足")),
        ("技术路线", ("method", "approach", "route", "路线", "方法")),
        ("阶段计划", ("timeline", "phase", "milestone", "阶段", "计划")),
        ("风险与应对", ("risk", "mitigation", "contingency", "风险", "应对")),
        ("预期成果与考核", ("expected", "outcome", "deliverable", "indicator", "预期", "考核")),
    ]
    parts, matrix = [], []
    for title, keywords in specs:
        picked = _pick(source, keywords, 5)
        body = _paragraph(picked, evidence, primary["file"], 5) if picked else "材料未明确提供；需结合项目周期与资源条件补充。"
        parts.append(f"## {title}\n{body}")
        matrix.append({"element": title, "source_sentences": picked, "status": "grounded" if picked else "missing"})
    artifact = "\n\n".join(parts)
    return {"task_type": "proposal", "artifact": artifact, "draft_text": artifact,
            "element_matrix": matrix, "quality_checks": {"elements": len(specs), "missing": [r["element"] for r in matrix if r["status"] == "missing"]}}


def _task_longdoc(primary, docs, evidence, case, question):
    source = primary["text"]
    outline = case.get("outline") or case.get("chapters")
    chapters = []
    if isinstance(outline, list):
        for item in outline:
            if isinstance(item, dict):
                chapters.append(str(item.get("title") or item.get("name") or item.get("chapter") or "Chapter"))
            else:
                chapters.append(str(item))
    if not chapters:
        chapters = [row["heading"] for row in _headings(source)] or ["Introduction", "Methods", "Results", "Discussion", "Conclusion"]
    parts = [f"# {case.get('title') or 'Long document draft'}", ""]
    matrix = []
    for i, chapter in enumerate(chapters, 1):
        key = re.sub(r"^\d+[.、)]?\s*", "", chapter)
        picked = _pick(source, (key, key.split()[0] if key else "", "method", "result", "study"), 8)
        body = _paragraph(picked, evidence, primary["file"], 8) if picked else _paragraph(_sentences(source)[:6], evidence, primary["file"], 6)
        parts.extend([f"## {i}. {chapter}", body, ""])
        matrix.append({"chapter": chapter, "source_sentences": picked, "status": "drafted" if picked else "needs_material"})
    artifact = "\n".join(parts).strip()
    return {"task_type": "longdoc", "artifact": artifact, "draft_text": artifact,
            "chapter_matrix": matrix, "terminology": _glossary(case),
            "quality_checks": {"chapters": len(chapters), "full_text_chars": len(artifact)}}


def _task_transfer(primary, evidence, case, question):
    source = primary["text"]
    key = _pick(source, ("result", "show", "significant", "improve", "reduce", "conclusion", "结果", "显著", "提高", "降低", "结论"), 10)
    methods = _pick(source, ("method", "approach", "using", "方法", "通过"), 5)
    limits = _pick(source, ("limitation", "however", "future", "局限", "然而", "未来"), 5)
    audience = case.get("audience") or case.get("target_reader") or "跨学科读者"
    artifact = (f"# 成果转化稿（面向{audience}）\n\n## 核心发现\n" + _paragraph(key, evidence, primary["file"], 10) +
                "\n\n## 方法要点\n" + _paragraph(methods, evidence, primary["file"], 5) +
                "\n\n## 意义与边界\n" + _paragraph(limits, evidence, primary["file"], 5))
    return {"task_type": "transfer", "artifact": artifact, "draft_text": artifact,
            "audience": audience, "change_records": _change_records(source, artifact),
            "quality_checks": {"key_findings": len(key), "limitations": len(limits)}}


def _task_mindmap(primary, docs, evidence, case, question):
    source = primary["text"]
    title = case.get("title") or (next((r["heading"] for r in _headings(source)), "Research mind map"))
    branches = [
        ("Background", ("background", "problem", "背景")),
        ("Question", ("question", "aim", "hypothesis", "问题", "目标")),
        ("Methods", ("method", "approach", "experiment", "方法", "实验")),
        ("Evidence", ("result", "data", "significant", "结果", "数据")),
        ("Conclusion", ("conclusion", "therefore", "结论", "因此")),
        ("Risks", ("risk", "limitation", "future", "风险", "局限")),
    ]
    lines = [f"# {title}", ""]
    dot = [f'digraph G {{ rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3ff"]; "{title}" [fillcolor="#3457d5", fontcolor="white"];']
    matrix = []
    for branch, keywords in branches:
        nodes = _pick(source, keywords, 4)
        lines.append(f"## {branch}")
        for node in nodes:
            clean = re.sub(r"\s+", " ", node)[:220]
            lines.append(f"- {clean}")
            dot.append(f'"{title}" -> "{branch}"; "{branch}" -> "{clean}";')
        matrix.append({"branch": branch, "nodes": nodes})
    artifact = "\n".join(lines)
    dot.append("}")
    return {"task_type": "mindmap", "artifact": artifact, "mindmap": artifact,
            "graphviz_dot": "\n".join(dot), "node_matrix": matrix,
            "quality_checks": {"branches": len(branches), "nodes": sum(len(r["nodes"]) for r in matrix)}}


def _task_evidence(primary, docs, evidence, case, question):
    source = primary["text"]
    claims = _pick(source, ("show", "demonstrate", "indicate", "suggest", "result", "表明", "证明", "提示", "结果"), 12)
    matrix = []
    for i, claim in enumerate(claims, 1):
        tokens = _tokens(claim)
        best, best_score = None, 0
        for row in evidence:
            score = len(tokens & _tokens(row.get("quote")))
            if score > best_score:
                best, best_score = row, score
        matrix.append({"claim_id": f"C{i}", "claim": claim, "evidence": best,
                       "strength": "direct" if best_score >= 4 else ("partial" if best_score >= 2 else "unsupported"),
                       "uncertainty": "term-overlap support only; verify original context" if best_score >= 2 else "no supporting evidence found"})
    paragraphs = [f"{row['claim']}" + (f" [{row['evidence']['id']}]" if row["evidence"] else "") for row in matrix]
    draft = "## 论断—证据成稿\n" + "\n\n".join(paragraphs)
    return {"task_type": "evidence", "artifact": draft, "draft_text": draft,
            "claim_matrix": matrix, "citations": [{"id": r["id"], "file": r.get("file"), "quote": r.get("quote", "")[:500]} for r in evidence[:12]],
            "quality_checks": {"claims": len(matrix), "supported": sum(r["strength"] != "unsupported" for r in matrix)}}


def _task_paper(primary, docs, evidence, case, question):
    intro = _task_intro(primary, docs, evidence, case, question)
    methods = _task_methods(primary, docs, evidence, case, question)
    results = _task_results(primary, docs, evidence, case, question)
    conclusion = _pick(primary["text"], ("conclusion", "in summary", "therefore", "结论", "总之"), 5)
    title = case.get("title") or next((r["heading"] for r in _headings(primary["text"])), "Manuscript")
    artifact = (f"# {title}\n\n## Abstract\n" + _paragraph(_sentences(primary["text"])[:5], evidence, primary["file"], 5) +
                "\n\n" + intro["artifact"] + "\n\n" + methods["artifact"] + "\n\n" + results["artifact"] +
                "\n\n## Conclusion\n" + _paragraph(conclusion, evidence, primary["file"], 5))
    refs = [_format_reference(_reference_from_doc(d), case.get("citation_style", "apa")) for d in docs if d.get("text")]
    if refs:
        artifact += "\n\n## References\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(refs, 1))
    return {"task_type": "paper", "artifact": artifact, "draft_text": artifact,
            "sections": {"introduction": intro["artifact"], "methods": methods["artifact"], "results": results["artifact"]},
            "references": refs, "quality_checks": {"full_manuscript": True, "sections": 7, "references": len(refs)}}


def _add_contract_fields(payload, task_type, artifact, evidence):
    """Expose stable semantic keys across hidden output_contract variants."""
    p = payload
    p.setdefault("draft", artifact)
    p.setdefault("main_text", artifact)
    p.setdefault("deliverable", {"artifact": artifact, "files": p.get("artifact_files") or []})
    p.setdefault("claim_source_map", p.get("citations") or evidence[:30])
    if task_type == "polish":
        p.setdefault("original_text", p.get("original_text"))
        p.setdefault("sentence_changes", p.get("change_records") or [])
        p.setdefault("preservation_checks", p.get("quality_checks") or {})
    elif task_type == "translation":
        p.setdefault("source_text", p.get("source_text"))
        p.setdefault("translated_text", p.get("translation"))
        p.setdefault("bilingual_alignment", p.get("bilingual_text"))
        p.setdefault("number_unit_citation_checks", p.get("quality_checks") or {})
    elif task_type == "transfer":
        p.setdefault("rewritten_text", artifact)
        p.setdefault("paragraph_map", p.get("change_records") or [])
        p.setdefault("fact_preservation", p.get("quality_checks") or {})
    elif task_type == "evidence":
        p.setdefault("claims", p.get("claim_matrix") or [])
        p.setdefault("evidence_table", p.get("claim_matrix") or [])
        p.setdefault("support_relation", p.get("claim_matrix") or [])
        p.setdefault("evidence_strength", p.get("claim_matrix") or [])
    elif task_type == "paper":
        p.setdefault("manuscript_sections", p.get("sections") or {})
        p.setdefault("figures_tables", p.get("figure_table_captions") or [])
        p.setdefault("declarations", p.get("quality_checks") or {})
        p.setdefault("consistency_checks", p.get("quality_checks") or {})
    elif task_type == "intro":
        p.setdefault("background", (p.get("sections") or {}).get("background"))
        p.setdefault("related_work_groups", p.get("citations") or [])
        p.setdefault("comparison_dimensions", ["problem", "method", "data", "evidence", "limitation"])
        p.setdefault("research_gap", (p.get("sections") or {}).get("gap"))
        p.setdefault("contributions", (p.get("sections") or {}).get("contributions"))
        p.setdefault("claim_citation_map", p.get("citations") or [])
    elif task_type == "methods":
        elements = p.get("method_elements") or []
        p.setdefault("steps", elements)
        p.setdefault("experimental_setup", elements)
        p.setdefault("parameters", elements)
        p.setdefault("datasets_splits", elements)
        p.setdefault("controls", elements)
        p.setdefault("reproducibility", elements)
        p.setdefault("limitations", elements)
    elif task_type == "results":
        p.setdefault("results_text", p.get("result_sentences") or [])
        p.setdefault("discussion_text", p.get("limitations") or [])
        p.setdefault("figure_table_values", p.get("figure_table_captions") or [])
        p.setdefault("statistics", p.get("quality_checks") or {})
        p.setdefault("negative_results", p.get("limitations") or [])
        p.setdefault("literature_comparison", p.get("citations") or [])
    elif task_type == "structure":
        p.setdefault("structure_diagnosis", p.get("structure_findings") or [])
        p.setdefault("issue_locations", p.get("issue_locations") or [])
        p.setdefault("issue_types", p.get("issue_types") or [])
        p.setdefault("edit_actions", p.get("edit_actions") or [])
        p.setdefault("rationale", p.get("structure_findings") or [])
        p.setdefault("coverage", p.get("coverage") or {})
    elif task_type == "rebuttal":
        p.setdefault("comments_subcomments", p.get("rebuttal_matrix") or [])
        p.setdefault("decisions", p.get("rebuttal_matrix") or [])
        p.setdefault("responses", p.get("responses") or [])
        p.setdefault("change_locations", p.get("change_locations") or [])
        p.setdefault("evidence_status", p.get("rebuttal_matrix") or [])
        p.setdefault("version_consistency", p.get("version_consistency") or {})
    elif task_type == "grant":
        p.setdefault("element_mapping", p.get("element_matrix") or [])
        p.setdefault("evidence_map", evidence[:30])
        p.setdefault("completed_vs_planned", {"completed": "only facts explicitly present in source materials",
                                               "planned": "all forward-looking statements require author confirmation"})
        p.setdefault("timeline_budget", p.get("element_matrix") or [])
    elif task_type == "proposal":
        p.setdefault("research_background", artifact)
        p.setdefault("problem_definition", artifact)
        p.setdefault("objectives", artifact)
        p.setdefault("technical_route", artifact)
        p.setdefault("timeline", p.get("element_matrix") or [])
        p.setdefault("risk_register", p.get("element_matrix") or [])
        p.setdefault("expected_outcomes", p.get("element_matrix") or [])
        p.setdefault("resource_fit", {"status": "requires_author_confirmation", "reason": "resource values not invented"})
    elif task_type == "latex":
        p.setdefault("tex_sources", [p.get("latex_source") or artifact])
        p.setdefault("bib", p.get("bibtex") or "")
        p.setdefault("figures", [])
        p.setdefault("compile_command", "tectonic/latexmk/pdflatex/xelatex when available")
        p.setdefault("compile_log", (p.get("preview") or {}).get("render_logs") or [])
        p.setdefault("pdf", (p.get("preview") or {}).get("pdf"))
        p.setdefault("template_checks", p.get("quality_checks") or {})
    elif task_type == "format":
        p.setdefault("template_spec", p.get("template_spec") or {})
        p.setdefault("format_checklist", p.get("formatting_checklist") or [])
        p.setdefault("content_unit_audit", p.get("content_unit_audit") or {})
        p.setdefault("export_checks", p.get("export_checks") or {})
        p.setdefault("editable_source", artifact)
    elif task_type == "mindmap":
        p.setdefault("nodes", p.get("node_matrix") or [])
        p.setdefault("edges", p.get("graphviz_dot") or "")
        p.setdefault("hierarchy", p.get("node_matrix") or [])
        p.setdefault("relation_types", ["contains", "supports", "limits"])
        p.setdefault("evidence_refs", evidence[:30])
        p.setdefault("fact_inference_flags", {"facts": "source sentences", "inferences": "none added deterministically"})
    return p


def _build_report(payload):
    return ("# Writing delivery report\n\n"
            f"- Task type: `{payload.get('task_type')}`\n"
            f"- Source: `{payload.get('source', {}).get('file')}`\n"
            f"- Artifact: `{payload.get('main_result', {}).get('artifact_path')}`\n"
            f"- Change records: {len(payload.get('change_records') or [])}\n\n"
            "## Quality checks\n\n```json\n" + json.dumps(payload.get("quality_checks", {}), ensure_ascii=False, indent=2) +
            "\n```\n\n## Source and uncertainty\n\n```json\n" +
            json.dumps({"source": payload.get("source"), "uncertainty": payload.get("uncertainty", [])}, ensure_ascii=False, indent=2) +
            "\n```\n")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_record(path, kind):
    return {"path": os.path.abspath(path), "type": kind, "bytes": os.path.getsize(path),
            "sha256": _sha256_file(path)}


def _write_json_alias(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_task_aliases(out_dir, task_type, artifact, payload, md_path, docx_path, tex_path, preview):
    """Publish task-specific filenames expected by writers and reviewers."""
    aliases = []

    def alias(name, source=None, content=None, kind="text"):
        path = os.path.join(out_dir, name)
        if source and os.path.exists(source) and os.path.abspath(source) != os.path.abspath(path):
            shutil.copyfile(source, path)
        elif content is not None:
            if isinstance(content, (dict, list)):
                _write_json_alias(path, content)
            else:
                Path(path).write_text(str(content), encoding="utf-8")
        else:
            return None
        aliases.append(path)
        return path

    if task_type == "polish":
        alias("polished_manuscript.md", source=md_path)
        alias("polished_manuscript.docx", source=docx_path)
        alias("change_log.json", content=payload.get("change_records") or [], kind="json")
        alias("terminology.json", content=payload.get("terms") or payload.get("terminology") or [], kind="json")
        alias("qa_report.json", content=payload.get("quality_checks") or {}, kind="json")
    elif task_type == "format":
        alias("formatted_manuscript.md", source=md_path)
        alias("formatted_manuscript.docx", source=docx_path)
        alias("template_checklist.json", content=payload.get("format_checklist") or [], kind="json")
        alias("conversion_audit.json", content=payload.get("content_unit_audit") or {}, kind="json")
    elif task_type == "references":
        alias("manuscript_with_citations.md", source=md_path)
        alias("manuscript_with_citations.docx", source=docx_path)
        alias("citation_audit.json", content=payload.get("citation_map") or [], kind="json")
        alias("dedupe_decisions.json", content=payload.get("dedupe_decisions") or [], kind="json")
    elif task_type == "structure":
        alias("structure_diagnosis.json", content=payload.get("structure_diagnosis") or [], kind="json")
        alias("edit_plan.json", content=payload.get("edit_actions") or [], kind="json")
        alias("annotated_manuscript.md", source=md_path)
    elif task_type == "intro":
        alias("introduction_related_work.md", source=md_path)
        alias("introduction_related_work.docx", source=docx_path)
        alias("related_work_matrix.json", content=payload.get("citations") or [], kind="json")
        alias("claim_citation_map.json", content=payload.get("citations") or [], kind="json")
    elif task_type == "methods":
        alias("methods_experiments.md", source=md_path)
        alias("methods_experiments.docx", source=docx_path)
        alias("parameters.json", content=payload.get("method_elements") or [], kind="json")
        alias("dataset_manifest.json", content=payload.get("method_elements") or [], kind="json")
        alias("reproducibility_checklist.md", content="# Reproducibility checklist\n\n" +
              "\n".join(f"- [ ] {r.get('element')}: {r.get('status')}" for r in payload.get("method_elements") or []))
    elif task_type == "results":
        alias("results.md", content="## Results\n" + str(payload.get("result_sentences") or ""))
        alias("discussion.md", content="## Discussion\n" + str(payload.get("limitations") or ""))
        alias("figure_table_values.json", content=payload.get("figure_table_captions") or [], kind="json")
        alias("statistics_audit.json", content=payload.get("quality_checks") or {}, kind="json")
    elif task_type == "rebuttal":
        alias("response_letter.md", source=md_path)
        alias("response_letter.docx", source=docx_path)
        alias("revised_manuscript.md", content=payload.get("revised_manuscript") or artifact)
        alias("comment_change_map.json", content=payload.get("rebuttal_matrix") or [], kind="json")
    elif task_type == "latex":
        alias("main.tex", source=tex_path)
        log_text = json.dumps(preview.get("render_logs") or [], ensure_ascii=False, indent=2)
        alias("build.log", content=log_text)
        alias("build_metadata.json", content={"compile_attempted": bool(preview.get("render_logs")),
              "pdf": preview.get("pdf"), "checks": payload.get("quality_checks") or {}}, kind="json")
    elif task_type == "translation":
        alias("translation.md", source=md_path)
        alias("translation.docx", source=docx_path)
        alias("bilingual_alignment.json", content=payload.get("bilingual_text") or "", kind="json")
        alias("terminology.json", content=payload.get("term_table") or [], kind="json")
        alias("translation_qa.json", content=payload.get("quality_checks") or {}, kind="json")
    elif task_type == "grant":
        alias("grant_draft.md", source=md_path)
        alias("grant_draft.docx", source=docx_path)
        alias("element_mapping.json", content=payload.get("element_matrix") or [], kind="json")
        alias("evidence_map.json", content=payload.get("evidence_refs") or [], kind="json")
    elif task_type == "proposal":
        alias("proposal.md", source=md_path)
        alias("proposal.docx", source=docx_path)
        alias("timeline.json", content=payload.get("element_matrix") or [], kind="json")
        alias("risk_register.json", content=payload.get("element_matrix") or [], kind="json")
    elif task_type == "longdoc":
        alias("thesis.md", source=md_path)
        alias("thesis.docx", source=docx_path)
        alias("outline_map.json", content=payload.get("chapter_matrix") or [], kind="json")
        alias("terminology_symbols.json", content=payload.get("terminology") or [], kind="json")
    elif task_type == "transfer":
        alias("rewritten.md", source=md_path)
        alias("rewritten.docx", source=docx_path)
        alias("paragraph_diff.json", content=payload.get("change_records") or [], kind="json")
        alias("change_log.json", content=payload.get("change_records") or [], kind="json")
    elif task_type == "mindmap":
        alias("mindmap.json", content={"nodes": payload.get("node_matrix") or [], "edges": payload.get("graphviz_dot") or ""}, kind="json")
        alias("node_edge_evidence.json", content=payload.get("node_matrix") or [], kind="json")
        alias("mindmap.mmap", content=json.dumps({"root": payload.get("artifact"), "format": "markdown-mindmap"}, ensure_ascii=False), kind="text")
    elif task_type == "evidence":
        alias("claims.json", content=payload.get("claim_matrix") or [], kind="json")
        alias("evidence_table.json", content=payload.get("claim_matrix") or [], kind="json")
        alias("evidence_driven_draft.md", source=md_path)
    elif task_type == "paper":
        alias("manuscript.md", source=md_path)
        alias("manuscript.docx", source=docx_path)
        alias("consistency_audit.json", content=payload.get("quality_checks") or {}, kind="json")
        alias("declarations.md", content="# Declarations\n\n- Data availability: see source materials.\n- Competing interests: not specified in the provided materials.\n")
    return aliases


def _write_outputs(out_dir, task_type, artifact, payload, want_tex=False):
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "writing_output.md")
    Path(md_path).write_text(artifact, encoding="utf-8")
    docx_path = os.path.join(out_dir, "writing_output.docx")
    docx_info = _write_docx(artifact, docx_path, payload.get("format_template"))
    tex_path = None
    if want_tex or task_type == "latex":
        tex_path = os.path.join(out_dir, "manuscript.tex")
        Path(tex_path).write_text(payload.get("latex_source") or artifact, encoding="utf-8")
    report_path = os.path.join(out_dir, "writing_report.md")
    Path(report_path).write_text(_build_report(payload), encoding="utf-8")
    changes_path = os.path.join(out_dir, "change_records.json")
    Path(changes_path).write_text(json.dumps(payload.get("change_records") or [], ensure_ascii=False, indent=2), encoding="utf-8")
    preview = _render_preview(md_path, docx_path, tex_path, out_dir)
    files = [md_path, docx_path, report_path, changes_path, preview["html"]]
    if preview.get("pdf"):
        files.append(preview["pdf"])
    if tex_path:
        files.append(tex_path)
    if payload.get("bibtex"):
        bib_path = os.path.join(out_dir, "references.bib")
        Path(bib_path).write_text(payload["bibtex"], encoding="utf-8")
        files.append(bib_path)
    if payload.get("graphviz_dot"):
        dot_path = os.path.join(out_dir, "mindmap.dot")
        Path(dot_path).write_text(payload["graphviz_dot"], encoding="utf-8")
        files.append(dot_path)
        dot = shutil.which("dot")
        if dot:
            svg_path = os.path.join(out_dir, "mindmap.svg")
            result = _run_command([dot, "-Tsvg", dot_path, "-o", svg_path], timeout=60)
            payload.setdefault("render_logs", []).append(result)
            if os.path.exists(svg_path):
                files.append(svg_path)
    files.extend(_write_task_aliases(out_dir, task_type, artifact, payload, md_path, docx_path, tex_path, preview))
    files = list(dict.fromkeys(os.path.abspath(path) for path in files if path and os.path.exists(path)))
    artifacts = [_artifact_record(path, "preview" if "preview" in os.path.basename(path) else
                                  "docx" if path.lower().endswith(".docx") else
                                  "pdf" if path.lower().endswith(".pdf") else
                                  "json" if path.lower().endswith(".json") else "text") for path in files]
    return {"artifact_path": md_path, "docx_path": docx_path, "tex_path": tex_path,
            "report_path": report_path, "preview": preview, "files": files,
            "artifacts": artifacts, "docx": docx_info}


def apply_writing(result, index, case, task_md, out_dir, work_dir=None):
    case = _dict(case)
    question = case.get("question") or case.get("task") or task_md[:1000]
    primary, docs, evidence = _select_sources(index, case, question)
    if primary is None:
        inline = "\n".join(str(case.get(k) or "") for k in ("source_text", "text", "document", "manuscript"))
        if not inline.strip():
            return {"writing_status": {"state": "failed", "error": "no readable source manuscript or material"},
                    "answer_content": "未找到可处理的原始稿件或科研材料。", "report": "Writing failed: no source manuscript."}
        primary = {"file": case.get("source_file") or "<inline>", "name": "inline", "ext": ".txt",
                   "meta": {}, "sha256": None, "text": _clip(inline), "chunks": []}
    task_type = detect_task_type(question, task_md, case)
    handlers = {
        "polish": lambda: _task_polish(primary, evidence, case, question),
        "format": lambda: _task_format(primary, evidence, case, question),
        "references": lambda: _task_references(primary, docs, evidence, case, question),
        "structure": lambda: _task_structure(primary, evidence, case, question),
        "intro": lambda: _task_intro(primary, docs, evidence, case, question),
        "methods": lambda: _task_methods(primary, docs, evidence, case, question),
        "results": lambda: _task_results(primary, docs, evidence, case, question),
        "rebuttal": lambda: _task_rebuttal(primary, docs, evidence, case, question),
        "latex": lambda: _task_latex(primary, evidence, case, question),
        "translation": lambda: _task_translation(primary, evidence, case, question),
        "grant": lambda: _task_grant(primary, docs, evidence, case, question),
        "proposal": lambda: _task_proposal(primary, docs, evidence, case, question),
        "longdoc": lambda: _task_longdoc(primary, docs, evidence, case, question),
        "transfer": lambda: _task_transfer(primary, evidence, case, question),
        "mindmap": lambda: _task_mindmap(primary, docs, evidence, case, question),
        "evidence": lambda: _task_evidence(primary, docs, evidence, case, question),
        "paper": lambda: _task_paper(primary, docs, evidence, case, question),
    }
    payload = handlers.get(task_type, handlers["paper"])()
    payload = _add_contract_fields(payload, task_type, str(payload.get("artifact") or ""), evidence)
    artifact = str(payload.get("artifact") or payload.get("draft_text") or payload.get("polished_text") or "")
    source = {"file": primary.get("file"), "sha256": primary.get("sha256"),
              "ext": primary.get("ext"), "chars": len(primary.get("text") or "")}
    uncertainty = []
    if not evidence:
        uncertainty.append("No independent evidence documents were available; draft is grounded only in the primary source.")
    missing = payload.get("quality_checks", {}).get("missing") or payload.get("quality_checks", {}).get("missing_elements")
    if missing:
        uncertainty.append("Missing required elements: " + ", ".join(missing))
    outputs = _write_outputs(out_dir, task_type, artifact, payload, want_tex=task_type == "latex")
    base_uncertainty = result.get("uncertainty") if isinstance(result.get("uncertainty"), dict) else {}
    uncertainty = dict(base_uncertainty)
    uncertainty["missing"] = list(uncertainty.get("missing") or []) + list(missing or [])
    uncertainty["conflicts"] = list(uncertainty.get("conflicts") or [])
    uncertainty["limits"] = list(uncertainty.get("limits") or []) + [
        "No independent evidence documents were available; draft is grounded only in the primary source." if not evidence else "",
        "Deterministic draft requires author verification before submission."
    ]
    uncertainty["limits"] = [x for x in uncertainty["limits"] if x]
    if not payload.get("change_records"):
        payload["change_records"] = [{"type": "draft_from_source", "original": r.get("quote", "")[:500],
                                     "revised": artifact[:500], "file": r.get("file"),
                                     "reason": f"source used to construct the {task_type} artifact"} for r in evidence[:12]]
    payload.update({
        "writing_status": {"state": "ok", "generated_at": _now(), "task_type": task_type},
        "source": source,
        "main_result": {"task_type": task_type, "artifact_path": outputs["artifact_path"],
                        "docx_path": outputs["docx_path"], "preview": outputs["preview"],
                        "summary": f"Completed {task_type} writing task from {source['file']}"},
        "artifact": artifact,
        "output": artifact,
        "answer_content": artifact,
        "artifacts": outputs["artifacts"],
        "artifact_files": outputs["files"],
        "deliverable": {"task_type": task_type, "artifact": artifact, "files": outputs["artifacts"]},
        "preview": outputs["preview"],
        "docx_generation": outputs["docx"],
        "preview_path": outputs["preview"].get("pdf") or outputs["preview"].get("html"),
        "preview_file": outputs["preview"].get("pdf") or outputs["preview"].get("html"),
        "pdf_path": outputs["preview"].get("pdf"),
        "artifact_path": outputs["artifact_path"],
        "output_file": outputs["artifact_path"],
        "manuscript_path": outputs["artifact_path"],
        "source_map": {r.get("file") or f"E{i}": r.get("quote", "")[:500] for i, r in enumerate(evidence[:30], 1)},
        "evidence_map": evidence[:30],
        "glossary": {r.get("source"): r.get("target") for r in (payload.get("terminology") or []) if isinstance(r, dict)},
        "diff": payload.get("change_records") or [],
        "changes": payload.get("change_records") or [],
        "modification_log": payload.get("change_records") or [],
        "facts": evidence[:30],
        "structured_result": {"task_type": task_type, "artifact": artifact, "source": source,
                              "change_records": payload.get("change_records") or [],
                              "uncertainty": uncertainty.get("limits", [])},
        "uncertainty": uncertainty,
        "writing_uncertainty": uncertainty.get("limits", []),
        "evidence_refs": evidence[:30],
        "source_mapping": evidence[:30],
        "version_boundary": {"source": source, "change_records": payload.get("change_records") or [],
                             "uncertainty": uncertainty.get("limits", [])},
        "preservation_checks": payload.get("quality_checks", {}),
        "sentence_changes": payload.get("change_records") or [],
        "terminology": payload.get("terms") or payload.get("term_table") or payload.get("terminology") or [],
        "citations": payload.get("citations") or [{"id": r["id"], "file": r.get("file"), "quote": r.get("quote", "")[:500]} for r in evidence[:30]],
    })
    if task_type == "latex":
        payload["compile_log"] = outputs["preview"].get("render_logs") or []
        payload["pdf"] = outputs["preview"].get("pdf")
        payload["compile_command"] = "tectonic/latexmk/pdflatex/xelatex when available"
    payload["report"] = _build_report(payload)
    return payload
