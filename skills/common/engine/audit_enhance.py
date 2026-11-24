"""Evidence-bounded academic audit: claims, citations, methods and compliance."""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+|\n+", text or "") if s.strip()]


def _target_text(index, case):
    target = case.get("target") or case.get("manuscript") or case.get("document")
    files = {row.get("relpath"): row for row in _list(index.get("files"))}
    chunks = defaultdict(list)
    for chunk in _list(index.get("chunks")):
        chunks[chunk.get("file")].append(chunk.get("text") or "")
    if target:
        for candidate in (target, os.path.basename(target)):
            if candidate in files:
                return candidate, "\n".join(chunks.get(candidate, []))
    for rel, row in files.items():
        if str(row.get("ext") or "").lower() in (".md", ".txt", ".docx", ".pdf") and "reference" not in rel.lower():
            return rel, "\n".join(chunks.get(rel, []))
    return None, ""


def extract_claims(text):
    claims = []
    markers = re.compile(r"(?i)\b(?:show|shows|demonstrate|demonstrates|prove|proves|significant|"
                         r"reduce|reduces|increase|increases|improve|improves|universal|all|always|never|"
                         r"outperform|effective|generalize)\b|证明|显著|降低|提高|普遍|所有|优于")
    for sentence in _sentences(text):
        if len(sentence) < 25:
            continue
        if markers.search(sentence) or re.search(r"\d+(?:\.\d+)?\s*(?:%|percent|p\s*[<=>])", sentence, re.I):
            claims.append(sentence)
    return claims


def extract_citations(text):
    citations = []
    for match in re.finditer(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]", text):
        for number in re.findall(r"\d+", match.group(1)):
            citations.append({"kind": "numeric", "value": number, "raw": match.group(0),
                              "offset": match.start()})
    for match in re.finditer(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text):
        citations.append({"kind": "doi", "value": match.group(0).rstrip(".,;"), "raw": match.group(0),
                          "offset": match.start()})
    return citations


def _reference_texts(index):
    output = []
    for row in _list(index.get("files")):
        rel = row.get("relpath") or ""
        if "reference" in rel.lower() or "references" in rel.lower():
            text = "\n".join(c.get("text", "") for c in _list(index.get("chunks")) if c.get("file") == rel)
            output.append((rel, row, text))
    return output


def resolve_reference(index, citation):
    refs = _reference_texts(index)
    value = _norm(citation.get("value"))
    if citation.get("kind") == "doi":
        for rel, row, text in refs:
            if value in _norm(text) or value == _norm((row.get("meta") or {}).get("doi")):
                return rel, row, text
    if citation.get("kind") == "numeric":
        match = re.search(r"(?m)^\s*\[" + re.escape(citation["value"]) + r"\]\s*(.+)$", "\n".join(t for _, _, t in refs))
        if match:
            line = match.group(1)
            for rel, row, text in refs:
                if _norm(line[:60]) in _norm(text):
                    return rel, row, text
    return None, None, ""


def _overlap(a, b):
    ta = set(re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2}", _norm(a)))
    tb = set(re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2}", _norm(b)))
    return len(ta & tb) / max(len(ta | tb), 1)


def verify_claims(index, claims, citations):
    refs = _reference_texts(index)
    output = []
    for i, claim in enumerate(claims, 1):
        linked = []
        for rel, row, text in refs:
            score = _overlap(claim, text)
            if score >= 0.08:
                linked.append((score, rel, row, text))
        linked.sort(reverse=True, key=lambda x: x[0])
        if not linked:
            status, reason = "unsupported", "no reference in the provided library supports this claim"
        else:
            score, rel, row, text = linked[0]
            status = "supported" if score >= 0.18 else "partial"
            reason = f"best reference overlap={score:.3f}; manual verification still required"
        output.append({"claim_id": f"C{i}", "claim": claim, "status": status, "reason": reason,
                       "candidate_references": [{"file": rel, "overlap": round(score, 4)}
                                                for score, rel, _, _ in linked[:3]],
                       "citations_in_manuscript": len(citations)})
    return output


def verify_citations(index, citations, target_text=""):
    output = []
    ref_lines = {}
    for match in re.finditer(r"(?m)^\s*\[(\d+)\]\s*(.+)$", target_text or ""):
        ref_lines[match.group(1)] = match.group(2).strip()
    for citation in citations:
        rel, row, text = resolve_reference(index, citation)
        status = "resolved" if rel else "missing"
        mismatch = None
        if not rel and citation.get("kind") == "numeric":
            line = ref_lines.get(citation.get("value"), "")
            doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", line)
            for ref_rel, ref_row, ref_text in _reference_texts(index):
                title_overlap = _overlap(line, ref_text)
                if title_overlap >= 0.12:
                    rel, row, text = ref_rel, ref_row, ref_text
                    cited_doi = doi_match.group(0).rstrip(".,;").lower() if doi_match else None
                    actual_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", ref_text)
                    actual_doi = _norm((ref_row.get("meta") or {}).get("doi")) or (
                        _norm(actual_match.group(0)) if actual_match else "")
                    if cited_doi and actual_doi and cited_doi != actual_doi:
                        status = "mismatch"
                        mismatch = {"cited_doi": cited_doi, "reference_doi": actual_doi}
                    else:
                        status = "resolved"
                    break
        output.append({**citation, "resolved_file": rel, "status": status,
                       "mismatch": mismatch, "metadata": (row or {}).get("meta") or {}})
    return output


def methodology_checklist(text):
    checks = [
        ("sample_size_reported", r"\b\d+\s+(?:molecules|samples|participants|records|cells)\b|样本", "sample size"),
        ("randomization_reported", r"\brandomi[sz]", "randomization"),
        ("blinding_reported", r"\bblind", "blinding"),
        ("statistical_test_reported", r"\b(?:t-test|anova|mann-whitney|regression|chi-square|p-value)\b|检验", "statistical test"),
        ("effect_size_reported", r"\b(?:effect size|cohen|hedges|odds ratio|risk ratio)\b|效应量", "effect size"),
        ("confidence_interval_reported", r"\b(?:confidence interval|95%\s*CI)\b|置信区间", "confidence interval"),
        ("limitations_reported", r"\blimitations?\b|局限性|限制", "limitations"),
        ("data_availability_reported", r"\bdata availability\b|数据可用", "data availability"),
        ("code_availability_reported", r"\bcode availability\b|代码可用", "code availability"),
        ("ethics_reported", r"\bethics?\b|伦理", "ethics statement"),
    ]
    output = []
    for key, pattern, label in checks:
        present = bool(re.search(pattern, text, re.I))
        negated = bool(re.search(r"(?i)\b(?:no|not|without)\s+" + pattern.split(r"\b")[0], text))
        output.append({"item": key, "label": label, "present": present,
                       "status": "present" if present and not negated else "missing_or_negated",
                       "evidence": "text match" if present else "not found"})
    return output


def compliance_checklist(text, case):
    checks = [
        ("title", r"(?m)^#\s+\S", "title"),
        ("abstract", r"(?im)^\s*##?\s*abstract\b|摘要", "abstract"),
        ("keywords", r"(?im)^\s*keywords?\s*[:：]|关键词", "keywords"),
        ("authors", r"(?im)^\s*authors?\s*[:：]|作者", "author list"),
        ("affiliations", r"(?im)^\s*affiliations?\s*[:：]|单位", "affiliations"),
        ("funding", r"(?im)^\s*funding\s*[:：]|基金", "funding"),
        ("conflicts_of_interest", r"(?im)^\s*conflicts? of interest\s*[:：]|利益冲突", "conflict of interest"),
        ("references", r"(?im)^\s*##?\s*references\b|参考文献", "references"),
        ("data_availability", r"(?im)data availability|数据可用性", "data availability statement"),
    ]
    output = []
    for key, pattern, label in checks:
        present = bool(re.search(pattern, text, re.I))
        required = (key in (case.get("criteria") or []) or "compliance" in (case.get("criteria") or [])
                    or key in ("title", "abstract", "keywords", "authors", "references", "conflicts_of_interest", "data_availability"))
        output.append({"item": key, "label": label, "present": present, "required": required,
                       "status": "pass" if present else ("fail" if required else "not_required")})
    return output


def text_overlap(index, target_text, target_file):
    target_sentences = _sentences(target_text)
    output = []
    for row in _list(index.get("files")):
        rel = row.get("relpath")
        if not rel or rel == target_file or "reference" in rel.lower():
            continue
        other = "\n".join(c.get("text", "") for c in _list(index.get("chunks")) if c.get("file") == rel)
        for sentence in target_sentences:
            if len(sentence) < 40:
                continue
            if _norm(sentence) in _norm(other):
                output.append({"source": rel, "quote": sentence, "type": "exact"})
            else:
                score = _overlap(sentence, other)
                if score >= 0.55:
                    output.append({"source": rel, "quote": sentence, "type": "near", "score": round(score, 4)})
    return output[:100]


def build_findings(claims, citations, methods, compliance, overlap):
    findings = []
    for row in claims:
        if row["status"] != "supported":
            findings.append({"severity": "high" if row["status"] == "unsupported" else "medium",
                             "category": "claim_evidence", "subject": row["claim_id"],
                             "detail": row["reason"], "evidence": row["claim"]})
    for row in citations:
        if row["status"] != "resolved":
            findings.append({"severity": "high", "category": "citation", "subject": row.get("raw"),
                             "detail": "citation does not resolve to a provided reference", "evidence": row.get("raw")})
    for row in methods:
        if row["status"] != "present" and row["item"] in ("statistical_test_reported", "limitations_reported", "sample_size_reported"):
            findings.append({"severity": "medium", "category": "methodology", "subject": row["item"],
                             "detail": row["label"] + " not reported", "evidence": row["status"]})
    for row in compliance:
        if row["status"] == "fail":
            findings.append({"severity": "medium", "category": "compliance", "subject": row["item"],
                             "detail": row["label"] + " missing", "evidence": row["status"]})
    for row in overlap:
        findings.append({"severity": "high" if row["type"] == "exact" else "medium",
                         "category": "text_overlap", "subject": row["source"],
                         "detail": row["type"] + " overlap detected", "evidence": row["quote"]})
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["category"]))
    return findings


def build_report(target, claims, citations, methods, compliance, overlap, findings):
    return "\n".join([
        "# Academic audit report", "", f"- Target: `{target}`", f"- Claims: {len(claims)}",
        f"- Citations: {len(citations)}", f"- Findings: {len(findings)}", "",
        "## Findings", "```json", json.dumps(findings, ensure_ascii=False, indent=2), "```", "",
        "## Claim-evidence matrix", "```json", json.dumps(claims, ensure_ascii=False, indent=2), "```", "",
        "## Citation verification", "```json", json.dumps(citations, ensure_ascii=False, indent=2), "```", "",
        "## Methodology checklist", "```json", json.dumps(methods, ensure_ascii=False, indent=2), "```", "",
        "## Compliance checklist", "```json", json.dumps(compliance, ensure_ascii=False, indent=2), "```", "",
        "## Text overlap", "```json", json.dumps(overlap, ensure_ascii=False, indent=2), "```",
    ])


def apply_audit(result, index, case, task_md, out_dir, work_dir=None):
    target, text = _target_text(index, case)
    if not target or not text:
        return {"audit_status": {"state": "failed", "error": "target manuscript not found"}}
    claims = extract_claims(text)
    citations = extract_citations(text)
    claim_matrix = verify_claims(index, claims, citations)
    citation_verification = verify_citations(index, citations, text)
    methods = methodology_checklist(text)
    compliance = compliance_checklist(text, case)
    overlap = text_overlap(index, text, target)
    findings = build_findings(claim_matrix, citation_verification, methods, compliance, overlap)
    report = build_report(target, claim_matrix, citation_verification, methods, compliance, overlap, findings)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "audit_report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)
    return {"audit_status": {"state": "ok", "generated_at": _now()}, "target": target,
            "claims": claim_matrix, "citation_verification": citation_verification,
            "methodology_checklist": methods, "compliance_checklist": compliance,
            "text_overlap": overlap, "findings": findings, "report": report}
