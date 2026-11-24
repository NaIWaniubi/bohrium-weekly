"""Reading-track enhancement: parsing, figures, formulas, comparison and notes."""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone


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


def _docs(index):
    chunks = defaultdict(list)
    for c in _list(index.get("chunks")):
        chunks[c.get("file")].append(c)
    docs = []
    for row in _list(index.get("files")):
        rel = row.get("relpath")
        parts = sorted(chunks.get(rel, []), key=lambda c: c.get("start") or 0)
        docs.append({"file": rel, "meta": row.get("meta") or {}, "ext": row.get("ext"),
                     "sha256": row.get("sha256"), "text": "\n".join(c.get("text", "") for c in parts),
                     "chunks": parts})
    return docs


def extract_figures_tables(text):
    items = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"(?i)^\s*(figure|fig\.?|table)\s*\d+", line):
            items.append({"kind": "table" if line.lower().startswith("table") else "figure",
                          "caption": line.strip(), "line": i + 1,
                          "context": "\n".join(lines[max(0, i - 2):i + 6])[:1200]})
    return items


def extract_formulas(text):
    formulas = []
    for match in re.finditer(r"\$\$?(.+?)\$\$?|\\\[(.+?)\\\]|\\\((.+?)\\\)", text, re.S):
        formula = next((g for g in match.groups() if g), "").strip()
        if formula:
            formulas.append({"formula": formula, "offset": match.start(), "end": match.end()})
    return formulas


GLOSSARY = {
    "graph neural network": "图神经网络", "molecular property prediction": "分子性质预测",
    "message passing": "消息传递", "mean absolute error": "平均绝对误差",
    "confidence interval": "置信区间", "effect size": "效应量", "limitations": "局限性",
    "abstract": "摘要", "methods": "方法", "results": "结果", "discussion": "讨论",
    "conclusion": "结论", "dataset": "数据集", "baseline": "基线", "outlier": "异常值",
}


def translate_terms(text, target="zh"):
    output = str(text or "")
    hits = []
    for source, target_text in GLOSSARY.items():
        for match in re.finditer(re.escape(source), output, re.I):
            hits.append({"source": match.group(0), "target": target_text, "offset": match.start()})
        if target == "zh":
            output = re.sub(re.escape(source), target_text, output, flags=re.I)
    return output, hits


def structured_notes(doc):
    text = doc.get("text", "")
    sections = {}
    current = "general"
    for line in text.splitlines():
        match = re.match(r"^\s*#{1,4}\s*(.+)$", line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return {
        "file": doc.get("file"), "sha256": doc.get("sha256"), "title": doc.get("meta", {}).get("title"),
        "sections": {k: "\n".join(v).strip()[:4000] for k, v in sections.items()},
        "figures_tables": extract_figures_tables(text), "formulas": extract_formulas(text),
        "summary": (sections.get("Abstract") or sections.get("abstract") or [text[:800]])[0][:1200],
    }


def compare_documents(docs, question=""):
    rows = []
    for doc in docs:
        notes = structured_notes(doc)
        rows.append({"file": doc.get("file"), "sha256": doc.get("sha256"),
                     "title": notes.get("title"), "summary": notes.get("summary"),
                     "sections": list(notes.get("sections", {})),
                     "figures_tables": len(notes.get("figures_tables", [])),
                     "formulas": len(notes.get("formulas", []))})
    return {"question": question, "documents": rows,
            "comparison_fields": ["title", "summary", "sections", "figures_tables", "formulas"],
            "limitations": ["section alignment is heuristic; review source before substantive comparison"]}


def extract_conclusions_data(docs):
    records = []
    for doc in docs:
        for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", doc.get("text", "")):
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            if re.search(r"(?i)\b(?:result|show|demonstrate|significant|reduce|increase|mean|p-value)\b|结果|显著|降低|提高", sentence):
                numbers = re.findall(r"\d+(?:\.\d+)?\s*(?:%|eV|mg/L|nm|p\s*[<=>]\s*[\d.]+)?", sentence)
                records.append({"file": doc.get("file"), "sha256": doc.get("sha256"),
                                "sentence": sentence[:800], "numbers": numbers,
                                "type": "result_candidate"})
    return records


def verify_facts(claims, docs):
    output = []
    for claim in claims:
        best = None
        for doc in docs:
            score = len(set(re.findall(r"[a-z]{3,}", _norm(claim))) &
                        set(re.findall(r"[a-z]{3,}", _norm(doc.get("text")))))
            if best is None or score > best[0]:
                best = (score, doc)
        if not best or best[0] < 2:
            output.append({"claim": claim, "status": "unsupported", "reason": "no sufficient term overlap"})
        else:
            output.append({"claim": claim, "status": "candidate_supported",
                           "file": best[1].get("file"), "sha256": best[1].get("sha256"),
                           "overlap_terms": best[0], "reason": "term overlap only; manual verification required"})
    return output


def knowledge_graph(docs):
    nodes, edges = {}, []
    for doc in docs:
        rel = doc.get("file")
        nodes[rel] = {"id": rel, "type": "document", "title": doc.get("meta", {}).get("title")}
        for term in re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b", doc.get("text", "")[:5000])[:20]:
            nodes.setdefault(term, {"id": term, "type": "concept"})
            edges.append({"source": rel, "target": term, "relation": "mentions"})
        doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", doc.get("text", ""))
        if doi:
            nodes[doi.group(0)] = {"id": doi.group(0), "type": "doi"}
            edges.append({"source": rel, "target": doi.group(0), "relation": "has_doi"})
    return {"nodes": list(nodes.values()), "edges": edges, "limitations": ["co-occurrence graph, not causal relations"]}


def nanopublication(doc):
    text = doc.get("text", "")
    doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text)
    triples = [
        {"subject": doc.get("file"), "predicate": "sha256", "object": doc.get("sha256")},
        {"subject": doc.get("file"), "predicate": "has_text", "object": bool(text)},
    ]
    if doi:
        triples.append({"subject": doc.get("file"), "predicate": "has_doi", "object": doi.group(0)})
    return {"triples": triples, "format": "simplified RDF-like nanopublication", "limitations": ["not a full W3C PROV export"]}


def build_report(payload):
    return "# Reading report\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def apply_reading(result, index, case, task_md, out_dir, work_dir=None):
    docs = _docs(index)
    if not docs:
        return {"reading_status": {"state": "failed", "error": "no readable documents"}}
    text = "\n".join(case.get("question", "").split()) + " " + task_md.lower()
    payload = {"documents": len(docs)}
    if any(k in text for k in ("figure", "table", "图表", "图注", "表格")):
        payload["figures_tables"] = {d["file"]: extract_figures_tables(d["text"]) for d in docs}
    if any(k in text for k in ("formula", "公式", "数学")):
        payload["formulas"] = {d["file"]: extract_formulas(d["text"]) for d in docs}
    if any(k in text for k in ("translate", "翻译", "双语")):
        payload["translations"] = {}
        for d in docs:
            translated, hits = translate_terms(d["text"])
            payload["translations"][d["file"]] = {"text": translated[:12000], "term_hits": hits}
    if any(k in text for k in ("compare", "比较", "整合")):
        payload["comparison"] = compare_documents(docs, case.get("question"))
    if any(k in text for k in ("extract", "提取", "结论", "数据")):
        payload["records"] = extract_conclusions_data(docs)
    if any(k in text for k in ("fact", "核验", "溯源")):
        records = payload.get("records") or extract_conclusions_data(docs)
        payload["facts"] = verify_facts([r.get("sentence", "") for r in records], docs)
    if any(k in text for k in ("graph", "知识图谱", "nanopublication")):
        payload["knowledge_graph"] = knowledge_graph(docs)
        payload["nanopublications"] = [nanopublication(d) for d in docs]
    payload["notes"] = [structured_notes(d) for d in docs]
    payload["comparison"] = payload.get("comparison") or compare_documents(docs, case.get("question"))
    report = build_report(payload)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "reading_report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)
    return {"reading_status": {"state": "ok", "generated_at": _now()}, **payload, "report": report}
