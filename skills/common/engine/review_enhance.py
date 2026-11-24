# -*- coding: utf-8 -*-
"""Deterministic, evidence-bounded helpers for literature-review workflows.

The module is intentionally stdlib-only and conservative: it organizes only the
material passed by the caller, keeps exact source locators wherever text chunks
are available, and marks missing facts instead of inventing them.

Public API:
    parse_screening_protocol(case, task_md="")
    screen_documents(index, protocol, chunks=None)
    cluster_themes(index, included_files, question="")
    classify_evidence_strength(file_meta, metadata, quotes)
    detect_contradictions(index, included_files, themes=None)
    detect_research_gaps(themes, included_files, question="")
    build_prisma_flow(index, screening_log)
    build_review_draft(question, themes, evidence_matrix, conflicts, gaps, protocol)
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter, defaultdict

__all__ = [
    "parse_screening_protocol",
    "screen_documents",
    "cluster_themes",
    "classify_evidence_strength",
    "detect_contradictions",
    "detect_research_gaps",
    "build_prisma_flow",
    "build_review_draft",
]

# ---------------------------------------------------------------------------
# Generic safe accessors and text utilities
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "in", "into", "is", "it", "its", "may", "might", "of", "on", "or",
    "our", "should", "that", "the", "their", "them", "there", "these", "they",
    "this", "those", "to", "was", "were", "will", "with", "would", "we", "using",
    "used", "use", "via", "than", "then", "also", "both", "each", "such", "while",
    "study", "studies", "report", "reports", "reported", "reporting", "result",
    "results", "method", "methods", "analysis", "evidence", "paper", "papers",
    "research", "review", "reviewed", "finding", "findings", "show", "shows",
    "shown", "found", "demonstrate", "demonstrated", "suggest", "suggests",
}

_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"']+", re.I)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _clean(value):
    return re.sub(r"\s+", " ", _text(value)).strip()


def _unique_strings(values):
    out, seen = [], set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("text") or value.get("criterion") or value.get("value")
        value = _clean(value)
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _file_id(row):
    row = _dict(row)
    return _text(row.get("relpath") or row.get("file") or row.get("path") or row.get("name"))


def _normalize_doi(value):
    value = _clean(value)
    value = re.sub(r"^doi\s*[:：]\s*", "", value, flags=re.I)
    return value.rstrip(".,;:!?)]}，。；：！？、").lower()


def _extract_doi(value):
    match = _DOI_RE.search(_text(value))
    return _normalize_doi(match.group(0)) if match else ""


def _metadata(row, extra=None):
    row = _dict(row)
    merged = {}
    for source in (row.get("meta"), row.get("metadata"), row, extra):
        if isinstance(source, dict):
            for key, value in source.items():
                if key not in {"meta", "metadata"} and value is not None:
                    merged[key] = value
    return merged


def _year_of(metadata, text=""):
    metadata = _dict(metadata)
    for key in ("year", "publication_year", "published_year", "date"):
        value = metadata.get(key)
        match = _YEAR_RE.search(_text(value))
        if match:
            return int(match.group(1))
    for container in ("years", "publication_years"):
        value = metadata.get(container)
        if isinstance(value, dict):
            for key in ("min", "max", "from", "to"):
                match = _YEAR_RE.search(_text(value.get(key)))
                if match:
                    return int(match.group(1))
        elif isinstance(value, list) and value:
            match = _YEAR_RE.search(_text(value[0]))
            if match:
                return int(match.group(1))
    match = _YEAR_RE.search(_text(text))
    return int(match.group(1)) if match else None


def _language_of(text):
    if _CJK_RUN_RE.search(_text(text)):
        return "zh"
    if re.search(r"[A-Za-z]{3,}", _text(text)):
        return "en"
    return "unknown"


def _normalize_language(value):
    value = _clean(value).lower()
    if not value:
        return ""
    if value in {"zh", "chi", "zho", "chinese", "中文", "汉语", "英文中文"}:
        return "zh"
    if value in {"en", "eng", "english", "英文", "英语"}:
        return "en"
    if value.startswith("zh"):
        return "zh"
    if value.startswith("en"):
        return "en"
    return value.split("-")[0]


def _terms(text):
    """Return deterministic content terms (English words plus CJK n-grams)."""
    value = _text(text).lower()
    terms = []
    for match in _EN_TOKEN_RE.finditer(value):
        token = match.group(0).strip("-_")
        if len(token) >= 2 and token not in _STOPWORDS:
            terms.append(token)
    for match in _CJK_RUN_RE.finditer(value):
        run = match.group(0)
        # Bigrams are robust for short Chinese labels; trigrams retain a little
        # more phrase structure without requiring a dictionary or model.
        for size in (2, 3):
            if len(run) >= size:
                terms.extend(run[i:i + size] for i in range(len(run) - size + 1))
    return terms


def _term_set(text):
    return set(_terms(text))


def _term_overlap(a, b):
    return len(_term_set(a) & _term_set(b))


def _cosine(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(weight * b.get(term, 0.0) for term, weight in a.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


def _files_from_index(index):
    if isinstance(index, dict):
        rows = index.get("files")
    elif isinstance(index, list):
        rows = index
    else:
        rows = None
    return [row for row in _list(rows) if isinstance(row, dict) and _file_id(row)]


def _chunks_from(value):
    if isinstance(value, dict):
        value = value.get("chunks")
    return [row for row in _list(value) if isinstance(row, dict)]


def _chunks_for_file(chunks, file_name):
    return [row for row in chunks if _file_id(row) == file_name or _text(row.get("file")) == file_name]


def _file_text(row, chunks):
    meta = _metadata(row)
    title = _clean(meta.get("title"))
    abstract = _clean(meta.get("abstract") or meta.get("summary"))
    keywords = meta.get("keywords") or meta.get("keyword") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keyword_text = " ".join(_clean(x) for x in _list(keywords))
    body = "\n".join(_clean(c.get("text")) for c in chunks if _clean(c.get("text")))
    return "\n".join(x for x in (title, title, abstract, abstract, keyword_text, body) if x)


def _has_full_text(row, chunks):
    row = _dict(row)
    if row.get("readable") is False or row.get("permission") == "denied":
        return False
    status = _clean(row.get("extracted_status") or row.get("extraction_status")).lower()
    if status in {"permission_denied", "access_denied", "error", "failed", "corrupt",
                  "corrupted", "abstract_only", "metadata_only", "needs_tool"}:
        return False
    meta = _metadata(row)
    for key in ("full_text_available", "has_full_text", "full_text"):
        if key in meta:
            return meta[key] is True
    for chunk in chunks:
        kind = _clean(chunk.get("kind") or chunk.get("section_kind")).lower()
        if kind in {"abstract", "title", "metadata", "references", "bibliography"}:
            continue
        if _clean(chunk.get("text")):
            return True
    return bool(_clean(row.get("text")))


def _clip_source_quote(chunk, preferred="", max_chars=360, file_sha=""):
    """Return an exact substring and absolute character offsets for a chunk."""
    chunk = _dict(chunk)
    body = _text(chunk.get("text"))
    if not body:
        return None
    start = 0
    if preferred:
        pos = body.casefold().find(_clean(preferred).casefold())
        if pos >= 0:
            start = max(0, pos - 40)
    while start < len(body) and body[start].isspace():
        start += 1
    end = min(len(body), start + max_chars)
    if end < len(body):
        # Prefer a sentence boundary, but never alter the source substring.
        window = body[start:end]
        boundary = max(window.rfind(". "), window.rfind("。"), window.rfind("\n"))
        if boundary > int(max_chars * 0.55):
            end = start + boundary + 1
    quote = body[start:end]
    base = chunk.get("start")
    try:
        base = int(base)
    except (TypeError, ValueError):
        base = 0
    file_name = _file_id(chunk)
    sha = _text(chunk.get("sha256_file") or chunk.get("sha256") or file_sha)
    locator = {
        "evidence_id": "E-%s-%s-%d" % (
            (sha[:10] or hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:10]),
            _text(chunk.get("chunk_id") or "chunk"), base + start),
        "file": file_name,
        "sha256": sha,
        "chunk_id": chunk.get("chunk_id"),
        "kind": chunk.get("kind") or chunk.get("section_kind"),
        "offset": [base + start, base + end],
        "quote": quote,
    }
    if chunk.get("pages") is not None:
        locator["pages"] = chunk.get("pages")
    return locator


def _evidence_refs(chunks, terms="", limit=3, file_sha=""):
    chunks = _list(chunks)
    query = _term_set(terms)
    scored = []
    for index, chunk in enumerate(chunks):
        body = _text(chunk.get("text"))
        overlap = len(query & _term_set(body)) if query else 0
        score = overlap + (0.25 if len(body) >= 80 else 0.0)
        scored.append((score, -index, chunk))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    refs = []
    for _, _, chunk in scored[:limit]:
        ref = _clip_source_quote(chunk, file_sha=file_sha)
        if ref:
            refs.append(ref)
    return refs


def _source_id(value, prefix, number):
    value = _clean(value).strip("[]")
    if value:
        return value
    return "%s%d" % (prefix, number)


def _citation(value):
    value = _clean(value).strip("[]")
    return "[%s]" % value if value else ""


# ---------------------------------------------------------------------------
# 1. Protocol parsing
# ---------------------------------------------------------------------------

def _section_items(text, names):
    """Extract bullet/inline items under a Markdown-like section heading."""
    lines = _text(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    wanted = [name.casefold() for name in names]
    items, active = [], False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s*(.+?)\s*$", line)
        label = heading.group(1) if heading else line
        label_clean = re.sub(r"[*_`]", "", label).strip()
        matched = any(name in label_clean.casefold() for name in wanted)
        inline = None
        if not heading:
            inline_match = re.match(
                r"^(?:纳入标准|排除标准|研究问题|检索协议|时间范围|语言|文献类型|全文要求|inclusion criteria|"
                r"exclusion criteria|research question|screening protocol|time range|language|"
                r"document type|full[- ]text requirement)\s*[:：]\s*(.+)$",
                label_clean, re.I)
            if inline_match:
                inline = inline_match.group(1).strip()
        if heading:
            if matched:
                active = True
                continue
            if active:
                active = False
            continue
        if inline is not None:
            if matched:
                items.append(inline)
                active = True
            elif active:
                active = False
            continue
        if active:
            bullet = re.match(r"^(?:[-*+•]|\d+[.)、])\s*(.+?)\s*$", line)
            if bullet:
                items.append(bullet.group(1))
            elif not line.startswith("|"):
                items.append(line)
    return _unique_strings(items)


def _question_from_text(text):
    for line in _text(text).replace("\r", "\n").split("\n"):
        match = re.match(
            r"^\s*(?:#{1,6}\s*)?(?:研究问题|research question|question)\s*[:：]\s*(.+?)\s*$",
            line, re.I)
        if match:
            return _clean(match.group(1))
    return ""


def _year_constraint(value):
    result = {}
    if isinstance(value, dict):
        for key in ("min", "max", "from", "to", "start", "end", "year_min", "year_max"):
            match = _YEAR_RE.search(_text(value.get(key)))
            if match:
                target = "min" if key in {"min", "from", "start", "year_min"} else "max"
                result[target] = int(match.group(1))
        if not result:
            years = re.findall(r"(?:19|20)\d{2}", _text(value))
            if years:
                nums = [int(x) for x in years]
                result = {"min": min(nums), "max": max(nums)}
        return result
    if isinstance(value, (list, tuple)):
        years = [int(x) for x in re.findall(r"(?:19|20)\d{2}", " ".join(_text(v) for v in value))]
        if years:
            return {"min": min(years), "max": max(years)}
        return {}
    years = [int(x) for x in re.findall(r"(?:19|20)\d{2}", _text(value))]
    if not years:
        return {}
    return {"min": min(years), "max": max(years)}


def _languages_constraint(value):
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，/;；]|\bor\b|\band\b|、", value, flags=re.I)
    else:
        parts = _list(value)
    out = []
    for part in parts:
        lang = _normalize_language(part)
        if lang and lang not in out:
            out.append(lang)
    return out


def _doc_types_constraint(value):
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，/;；]|\bor\b|\band\b|、", value, flags=re.I)
    else:
        parts = _list(value)
    out = []
    for part in parts:
        item = _clean(part).lower()
        if item and item not in out:
            out.append(item)
    return out


def _full_text_constraint(case, structured, task):
    for source in (structured, case):
        for key in ("full_text_required", "require_full_text", "need_text", "fulltext_required",
                    "full_text", "has_full_text"):
            if key in source:
                value = source[key]
                if isinstance(value, str):
                    return value.strip().lower() in {"true", "yes", "1", "required", "必须", "是"}
                return value is True
    if re.search(r"全文要求\s*[:：].*(?:required|必须|仅.*全文|有全文)|full[- ]text\s+required|"
                 r"only\s+full[- ]text", _text(task), re.I):
        return True
    if re.search(r"摘要即可|abstract\s+only|no\s+full[- ]text\s+required", _text(task), re.I):
        return False
    return None


def parse_screening_protocol(case, task_md=""):
    """Parse explicit screening rules without supplying unstated defaults."""
    case_dict = _dict(case)
    if isinstance(case, str) and not task_md:
        task_md = case
    if not task_md:
        task_md = case_dict.get("task_md") or case_dict.get("task")
    task = _text(task_md)
    raw_protocol = case_dict.get("screening_protocol") or case_dict.get("protocol")
    protocol_text = ""
    if isinstance(raw_protocol, str):
        protocol_text = raw_protocol
        task = (task + "\n" + protocol_text).strip()
        structured = {}
    else:
        structured = _dict(raw_protocol)

    question = _clean(case_dict.get("question") or case_dict.get("research_question") or
                      structured.get("question") or structured.get("research_question"))
    if not question:
        question = _question_from_text(task)

    inclusion = _unique_strings(
        _list(structured.get("inclusion") or structured.get("inclusion_criteria") or
              structured.get("include") or structured.get("included_criteria") or
              case_dict.get("inclusion") or case_dict.get("inclusion_criteria") or
              case_dict.get("included_criteria")))
    exclusion = _unique_strings(
        _list(structured.get("exclusion") or structured.get("exclusion_criteria") or
              structured.get("exclude") or structured.get("excluded_criteria") or
              case_dict.get("exclusion") or case_dict.get("exclusion_criteria") or
              case_dict.get("excluded_criteria")))
    if not inclusion:
        inclusion = _section_items(task, ["纳入标准", "inclusion criteria", "eligibility criteria"])
    if not exclusion:
        exclusion = _section_items(task, ["排除标准", "exclusion criteria"])

    year_value = structured.get("year") or structured.get("years") or structured.get("time_range") or \
        structured.get("date_range") or case_dict.get("year") or case_dict.get("years") or \
        case_dict.get("time_range") or case_dict.get("date_range")
    if year_value is None:
        time_line = _section_items(task, ["时间范围", "time range", "publication years"])
        year_value = " ".join(time_line)
    year = _year_constraint(year_value)

    language_value = structured.get("language") or structured.get("languages") or \
        case_dict.get("language") or case_dict.get("languages")
    if language_value is None:
        language_line = _section_items(task, ["语言", "language"])
        language_value = " ".join(language_line)
    languages = _languages_constraint(language_value)

    type_value = structured.get("document_types") or structured.get("literature_type") or \
        structured.get("type") or case_dict.get("document_types") or case_dict.get("type")
    if type_value is None:
        type_line = _section_items(task, ["文献类型", "document type", "literature type"])
        type_value = " ".join(type_line)
    doc_types = _doc_types_constraint(type_value)
    full_text = _full_text_constraint(case_dict, structured, task)

    prospective = structured.get("prospective") is True or case_dict.get("prospective") is True
    if not prospective:
        prospective = bool(re.search(
            r"前瞻性|prospective|预注册|preregister|protocol[- ]first|screening protocol|筛选协议|检索协议",
            task + "\n" + _text(case_dict.get("title")), re.I))

    reason_codes = {
        "duplicate": "重复记录按 sha256 或 DOI 归并，仅保留一个主记录",
        "non_literature": "行政说明、版本说明或清单文件，不是文献来源",
        "off_topic": "未命中纳入标准，或命中明确的排除标准",
        "no_full_text": "协议要求全文，但当前材料只有摘要或未提取正文",
        "metadata_missing": "关键元数据缺失，无法核验协议约束",
        "permission_denied": "文件权限或访问状态不允许读取",
        "year_out_of_range": "文献年份不在协议时间范围内",
        "year_unknown": "无法从元数据或正文确定文献年份",
        "language_mismatch": "文献语言不在协议允许范围内",
        "language_unknown": "无法可靠确定文献语言",
        "document_type_mismatch": "文件或文献类型不在协议允许范围内",
        "included": "满足已声明的纳入标准且未触发排除或待复核条件",
        "insufficient_information": "材料不足，不能可靠判断纳入或排除",
    }
    for i, criterion in enumerate(inclusion, 1):
        reason_codes["inclusion_%d" % i] = criterion
    for i, criterion in enumerate(exclusion, 1):
        reason_codes["exclusion_%d" % i] = criterion

    return {
        "question": question,
        "inclusion": inclusion,
        "exclusion": exclusion,
        "reason_codes": reason_codes,
        "prospective": prospective,
        "constraints": {
            "year": year,
            "languages": languages,
            "document_types": doc_types,
            "literature_types": doc_types,
            "full_text_required": full_text,
            "permission": structured.get("permission") or case_dict.get("permission"),
        },
    }


# ---------------------------------------------------------------------------
# 2. Document screening
# ---------------------------------------------------------------------------

class _DisjointSet:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _criterion_matches(criterion, text):
    terms = _term_set(criterion)
    if not terms:
        return False
    return bool(terms & _term_set(text))


def _doc_type_allowed(ext, metadata, allowed):
    if not allowed:
        return True
    ext = _clean(ext).lower()
    source_type = _clean(metadata.get("source_type") or metadata.get("document_type") or
                         metadata.get("literature_type")).lower()
    for item in allowed:
        item = item.lower().strip()
        if item.startswith(".") and ext == item:
            return True
        if item in {ext.lstrip("."), source_type}:
            return True
        if item in {"primary studies", "primary study"} and "primary" in source_type:
            return True
        if item in {"systematic reviews", "systematic review"} and "systematic" in source_type:
            return True
    return False


def _is_administrative(row, chunks):
    """Recognize explicit administrative/version artifacts, not research notes."""
    row = _dict(row)
    path = _file_id(row).replace("\\", "/").lower()
    name = os.path.basename(path)
    meta = _metadata(row)
    source_type = _clean(meta.get("source_type") or meta.get("document_type") or
                         meta.get("literature_type")).lower()
    title = _clean(meta.get("title")).lower()
    if source_type in {"administrative", "admin", "version note", "version notes", "manifest", "ledger"}:
        return True
    if re.search(r"(^|/)(?:admin|administrative)(/|$)", path):
        return True
    if re.search(r"(?:^|[_\-. ])(?:version[_\- ]?notes?|admin(?:istrative)?|manifest|ledger)(?:$|[_\-. ])", name):
        return True
    if re.match(r"^(?:administrative|admin(?:istrative)?|version notes?|alias mapping)\b", title):
        return True
    opening = " ".join(_text(chunk.get("text")) for chunk in _list(chunks)[:2])[:500].lower()
    return bool(re.search(r"^(?:administrative|admin(?:istrative)?|version notes?|alias mapping)\b", opening))


def _metadata_summary(row, chunks, doi=""):
    meta = _metadata(row)
    title = _clean(meta.get("title"))
    year = _year_of(meta, "\n".join(_text(c.get("text")) for c in chunks))
    source_type = _clean(meta.get("source_type") or meta.get("document_type") or
                         meta.get("literature_type"))
    language = _normalize_language(meta.get("language"))
    if not language:
        language = _language_of(_file_text(row, chunks))
    keywords = meta.get("keywords") or meta.get("keyword") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    flags = []
    if not title:
        flags.append("title_missing")
    if year is None:
        flags.append("year_missing")
    if not doi:
        flags.append("doi_missing")
    if not source_type:
        flags.append("source_type_missing")
    if _dict(row).get("readable") is False or _clean(_dict(row).get("extracted_status")).lower() in {
        "permission_denied", "access_denied"}:
        permission = "denied"
        flags.append("permission_denied")
    elif _dict(row).get("readable") is True:
        permission = "granted"
    else:
        permission = "unknown"
        flags.append("permission_unknown")
    full_text = _has_full_text(row, chunks)
    if not full_text:
        flags.append("no_full_text")
    raw_meta = row.get("metadata") or row.get("meta") or {}
    if not raw_meta and not any(row.get(key) for key in
                                ("title", "year", "doi", "source_type", "language", "abstract", "keywords")):
        flags.append("metadata_missing")
    return {
        "title": title or None,
        "year": year,
        "doi": doi or None,
        "source_type": source_type or None,
        "language": language,
        "keywords": [_clean(x) for x in _list(keywords) if _clean(x)],
        "full_text_available": full_text,
        "permission": permission,
        "flags": flags,
    }


def screen_documents(index, protocol, chunks=None):
    """Screen every indexed record, preserving an auditable row per record."""
    index_dict = _dict(index)
    files = _files_from_index(index)
    protocol = _dict(protocol)
    constraints = _dict(protocol.get("constraints"))
    chunk_rows = _chunks_from(chunks) if chunks is not None else _chunks_from(index_dict.get("chunks"))
    if not files:
        return []

    metas = [_metadata(row) for row in files]
    admin_flags = [_is_administrative(row, _chunks_for_file(chunk_rows, _file_id(row))) for row in files]
    dois = [_extract_doi(metas[i].get("doi") or metas[i].get("DOI") or
                         "\n".join(_text(c.get("text")) for c in _chunks_for_file(chunk_rows, _file_id(files[i]))))
            for i in range(len(files))]

    dsu = _DisjointSet(len(files))
    by_sha, by_doi = {}, {}
    for i, row in enumerate(files):
        if admin_flags[i]:
            continue
        sha = _clean(row.get("sha256") or row.get("sha256_file")).lower()
        if sha:
            if sha in by_sha:
                dsu.union(i, by_sha[sha])
            else:
                by_sha[sha] = i
        if dois[i]:
            if dois[i] in by_doi:
                dsu.union(i, by_doi[dois[i]])
            else:
                by_doi[dois[i]] = i

    groups = defaultdict(list)
    for i in range(len(files)):
        groups[dsu.find(i)].append(i)

    def canonical_rank(i):
        row = files[i]
        fchunks = _chunks_for_file(chunk_rows, _file_id(row))
        meta = metas[i]
        return (
            1 if _has_full_text(row, fchunks) else 0,
            1 if row.get("readable") is True else 0,
            sum(1 for key in ("title", "year", "doi", "source_type") if meta.get(key)),
            1 if _clean(row.get("extracted_status")).lower() == "ok" else 0,
            -i,
        )

    canonical = {}
    duplicate_basis = {}
    for members in groups.values():
        primary = max(members, key=canonical_rank)
        for member in members:
            canonical[member] = primary
            if member != primary:
                same_sha = bool(_clean(files[member].get("sha256")).lower() and
                                _clean(files[member].get("sha256")).lower() ==
                                _clean(files[primary].get("sha256")).lower())
                duplicate_basis[member] = "sha256" if same_sha else "DOI"

    inclusion = _unique_strings(_list(protocol.get("inclusion")))
    exclusion = _unique_strings(_list(protocol.get("exclusion")))
    question = _text(protocol.get("question"))
    inclusion_context = _term_set(" ".join(inclusion + ([question] if question else [])))
    year_rule = _dict(constraints.get("year"))
    allowed_languages = set(_languages_constraint(constraints.get("languages")))
    allowed_types = _doc_types_constraint(constraints.get("document_types") or constraints.get("literature_types"))
    full_text_required = constraints.get("full_text_required") is True

    log = []
    for i, row in enumerate(files):
        file_name = _file_id(row)
        fchunks = _chunks_for_file(chunk_rows, file_name)
        meta = metas[i]
        summary = _metadata_summary(row, fchunks, dois[i])
        text = _file_text(row, fchunks)
        query_terms = " ".join([question] + inclusion)
        refs = _evidence_refs(fchunks, query_terms, limit=3,
                              file_sha=_text(row.get("sha256") or row.get("sha256_file")))
        result = {
            "file": file_name,
            "sha256": _text(row.get("sha256") or row.get("sha256_file")),
            "decision": "needs_review",
            "reason_code": "insufficient_information",
            "reason": "材料不足，不能可靠判断",
            "evidence_refs": refs,
            "metadata": summary,
        }

        if admin_flags[i]:
            result.update({
                "decision": "exclude",
                "reason_code": "non_literature",
                "reason": "行政说明、版本说明或清单文件，不是文献来源",
                "metadata": dict(summary, source_class="administrative_non_literature"),
            })
            log.append(result)
            continue

        if canonical[i] != i:
            primary = files[canonical[i]]
            primary_name = _file_id(primary)
            result.update({
                "decision": "exclude",
                "reason_code": "duplicate",
                "reason": "重复记录按 %s 归并到 %s" % (duplicate_basis.get(i, "sha256/DOI"), primary_name),
                "metadata": dict(summary, duplicate_of=primary_name,
                                 duplicate_basis=duplicate_basis.get(i, "sha256/DOI")),
                "evidence_refs": _evidence_refs(_chunks_for_file(chunk_rows, primary_name), query_terms, limit=3,
                                                file_sha=_text(primary.get("sha256") or primary.get("sha256_file"))),
            })
            log.append(result)
            continue

        # Explicit access failures precede content judgments.
        if summary["permission"] == "denied":
            result.update({"reason_code": "permission_denied",
                           "reason": "文件权限或访问状态不允许读取，需恢复权限后复核"})
            log.append(result)
            continue

        exclusion_hit = any((_term_set(criterion) - inclusion_context) & _term_set(text)
                            for criterion in exclusion)
        if exclusion_hit:
            result.update({"decision": "exclude", "reason_code": "off_topic",
                           "reason": "命中协议排除标准"})
            log.append(result)
            continue

        ext = _clean(row.get("ext") or os.path.splitext(file_name)[1]).lower()
        if not _doc_type_allowed(ext, meta, allowed_types):
            result.update({"decision": "exclude", "reason_code": "document_type_mismatch",
                           "reason": "文件或文献类型不在协议允许范围内"})
            log.append(result)
            continue

        if year_rule:
            year = summary["year"]
            if year is None:
                code = "metadata_missing" if "metadata_missing" in summary["flags"] else "year_unknown"
                result.update({"reason_code": code, "reason": "协议限定年份，但无法确定文献年份"})
                log.append(result)
                continue
            if ("min" in year_rule and year < year_rule["min"]) or \
               ("max" in year_rule and year > year_rule["max"]):
                result.update({"decision": "exclude", "reason_code": "year_out_of_range",
                               "reason": "文献年份 %d 不在协议时间范围内" % year})
                log.append(result)
                continue

        if allowed_languages:
            language = summary["language"]
            if language == "unknown":
                result.update({"reason_code": "metadata_missing" if "metadata_missing" in summary["flags"]
                               else "language_unknown", "reason": "无法可靠确定文献语言"})
                log.append(result)
                continue
            if language not in allowed_languages:
                result.update({"decision": "exclude", "reason_code": "language_mismatch",
                               "reason": "文献语言 %s 不在协议允许范围内" % language})
                log.append(result)
                continue

        if full_text_required and not summary["full_text_available"]:
            result.update({"reason_code": "no_full_text",
                           "reason": "协议要求全文，但当前只有摘要、元数据或不可读正文"})
            log.append(result)
            continue

        # Missing title/year/source type is explicit even when text can still be
        # topically screened; required missing facts keep the record pending.
        critical_missing = [flag for flag in summary["flags"]
                            if flag in {"metadata_missing", "title_missing", "year_missing",
                                        "source_type_missing"}]
        if critical_missing and ("metadata_missing" in summary["flags"] or
                                 (year_rule and summary["year"] is None)):
            result.update({"reason_code": "metadata_missing",
                           "reason": "关键元数据缺失（%s），需补全后复核" % ", ".join(critical_missing)})
            log.append(result)
            continue

        criteria = inclusion or ([question] if question else [])
        if not criteria:
            result.update({"reason_code": "insufficient_information",
                           "reason": "协议未声明纳入标准或研究问题，不能可靠判断相关性"})
            log.append(result)
            continue
        if not text.strip():
            result.update({"reason_code": "insufficient_information",
                           "reason": "没有可读取的标题、摘要或正文，无法判断相关性"})
            log.append(result)
            continue
        matched = [c for c in criteria if _criterion_matches(c, text)]
        if not matched:
            result.update({"decision": "exclude", "reason_code": "off_topic",
                           "reason": "未命中任何纳入标准或研究问题内容"})
            log.append(result)
            continue

        result.update({
            "decision": "include",
            "reason_code": "included",
            "reason": "满足已声明的纳入标准，且未触发排除或待复核条件",
            "metadata": dict(summary, matched_criteria=matched),
        })
        log.append(result)
    return log


# ---------------------------------------------------------------------------
# 3. Theme induction with TF-IDF and agglomerative clustering
# ---------------------------------------------------------------------------

def _resolve_included(index, included_files):
    index = _dict(index)
    by_name = {_file_id(row): row for row in _files_from_index(index)}
    chunks = _chunks_from(index.get("chunks"))
    if isinstance(included_files, dict):
        included_files = included_files.get("files") or included_files.get("included_files") or []
    if isinstance(included_files, str):
        included_files = [included_files]
    names, rows = [], []
    for item in _list(included_files):
        if isinstance(item, dict):
            name = _file_id(item)
            row = item if name in by_name and _file_id(by_name[name]) == name else by_name.get(name, item)
        else:
            name = _text(item)
            row = by_name.get(name)
        if name and name not in names:
            names.append(name)
            rows.append(row or {"relpath": name})
    docs = []
    for name, row in zip(names, rows):
        fchunks = _chunks_for_file(chunks, name)
        text = _file_text(row, fchunks)
        docs.append({"file": name, "row": row, "chunks": fchunks, "text": text,
                     "terms": Counter(_terms(text))})
    return docs


def _dedupe_resolved_docs(docs):
    """Collapse sha256/DOI aliases before any thematic counting."""
    if len(docs) < 2:
        return docs
    dsu = _DisjointSet(len(docs))
    by_sha, by_doi = {}, {}
    dois = []
    for i, doc in enumerate(docs):
        row = _dict(doc.get("row"))
        sha = _clean(row.get("sha256") or row.get("sha256_file")).lower()
        if sha:
            if sha in by_sha:
                dsu.union(i, by_sha[sha])
            else:
                by_sha[sha] = i
        meta = _metadata(row)
        doi = _extract_doi(meta.get("doi") or meta.get("DOI") or doc.get("text"))
        dois.append(doi)
        if doi:
            if doi in by_doi:
                dsu.union(i, by_doi[doi])
            else:
                by_doi[doi] = i
    groups = defaultdict(list)
    for i in range(len(docs)):
        groups[dsu.find(i)].append(i)

    def rank(i):
        doc = docs[i]
        row = _dict(doc.get("row"))
        path = _file_id(row).replace("\\", "/").lower()
        meta = _metadata(row)
        return (
            0 if re.search(r"(^|/)(?:aliases?|copies|duplicates?)(/|$)", path) else 1,
            1 if _has_full_text(row, doc.get("chunks")) else 0,
            1 if row.get("readable") is True else 0,
            sum(1 for key in ("title", "year", "doi", "source_type") if meta.get(key)),
            -i,
        )

    selected = [max(members, key=rank) for members in groups.values()]
    return [docs[i] for i in sorted(selected)]


def _tfidf_vectors(docs):
    n = len(docs)
    df = Counter()
    for doc in docs:
        df.update(doc["terms"].keys())
    vectors = []
    for doc in docs:
        row = {}
        for term, count in doc["terms"].items():
            idf = math.log((n + 1.0) / (df[term] + 1.0)) + 1.0
            row[term] = (1.0 + math.log(count)) * idf
        norm = math.sqrt(sum(v * v for v in row.values())) or 1.0
        vectors.append({term: value / norm for term, value in row.items()})
    return vectors, df


def _cluster_similarity(vectors, left, right):
    scores = [_cosine(vectors[a], vectors[b]) for a in left for b in right]
    return sum(scores) / len(scores) if scores else 0.0


def _agglomerative(vectors, threshold=0.10):
    clusters = [[i] for i in range(len(vectors))]
    while len(clusters) > 1:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                score = _cluster_similarity(vectors, clusters[i], clusters[j])
                if best is None or score > best[0]:
                    best = (score, i, j)
        if not best or best[0] < threshold:
            break
        _, i, j = best
        merged = clusters[i] + clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in {i, j}] + [merged]
    return clusters


def _theme_top_terms(docs, vectors, members, limit=6):
    scores = Counter()
    member_set = set(members)
    for idx in members:
        for term, weight in vectors[idx].items():
            scores[term] += weight
    # Prefer terms occurring in more than one member, then TF-IDF weight.
    scores = Counter({term: weight * (1.35 if sum(term in docs[i]["terms"] for i in member_set) > 1 else 1.0)
                      for term, weight in scores.items()})
    return [term for term, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def cluster_themes(index, included_files, question=""):
    """Induce labels from the supplied batch using TF-IDF + average linkage."""
    docs = _resolve_included(index, included_files)
    docs = [doc for doc in docs if not _is_administrative(doc["row"], doc["chunks"])]
    docs = _dedupe_resolved_docs(docs)
    docs = [doc for doc in docs if doc["terms"]]
    if not docs:
        return []
    vectors, _ = _tfidf_vectors(docs)
    clusters = _agglomerative(vectors)
    clusters.sort(key=lambda members: (-len(members), min(members)))

    themes = []
    for number, members in enumerate(clusters, 1):
        members = sorted(members)
        top_terms = _theme_top_terms(docs, vectors, members)
        label_terms = top_terms[:3] or [docs[members[0]]["file"]]
        label = " / ".join(label_terms)
        member_files = [docs[i]["file"] for i in members]
        quotes = []
        for i in members:
            quotes.extend(_evidence_refs(
                docs[i]["chunks"], " ".join(top_terms), limit=1,
                file_sha=_text(_dict(docs[i]["row"]).get("sha256") or
                               _dict(docs[i]["row"]).get("sha256_file"))))
        # Stable de-duplication by source locator.
        seen, unique_quotes = set(), []
        for quote in quotes:
            key = (quote.get("file"), quote.get("chunk_id"), tuple(quote.get("offset") or []))
            if key not in seen:
                seen.add(key)
                unique_quotes.append(quote)

        subthemes = []
        if len(members) > 1:
            for term in top_terms[:4]:
                subset = [docs[i]["file"] for i in members if term in docs[i]["terms"]]
                if subset and len(subset) < len(members):
                    subthemes.append({"label": term, "top_terms": [term], "member_files": subset})
        themes.append({
            "theme_id": "T%d" % number,
            "label": label,
            "top_terms": top_terms,
            "member_files": member_files,
            "evidence_quotes": unique_quotes,
            "subthemes": subthemes[:3],
            "boundary": "本批 %d 篇纳入文献中出现的主题；仅反映传入材料，不代表领域全貌。" % len(member_files),
        })
    return themes


# ---------------------------------------------------------------------------
# 4. Evidence strength
# ---------------------------------------------------------------------------

def _quote_rows(quotes):
    if isinstance(quotes, dict):
        if isinstance(quotes.get("quotes"), list):
            quotes = quotes["quotes"]
        elif quotes.get("quote") or quotes.get("text"):
            quotes = [quotes]
        else:
            quotes = []
    rows = []
    for item in _list(quotes):
        if isinstance(item, str):
            rows.append({"quote": item})
        elif isinstance(item, dict):
            rows.append(item)
    return rows


def classify_evidence_strength(file_meta, metadata, quotes):
    """Classify conservatively; abstract-only material is never full evidence."""
    file_meta = _dict(file_meta)
    meta = _metadata(file_meta, metadata)
    rows = _quote_rows(quotes)
    source_type = _clean(meta.get("source_type") or meta.get("document_type") or
                         meta.get("literature_type")).lower()
    combined = "\n".join(_text(row.get("quote") or row.get("text")) for row in rows)
    kinds = {_clean(row.get("kind") or row.get("section_kind")).lower() for row in rows}
    basis, limitations = [], []

    explicit_full_text = None
    for source in (file_meta, meta):
        for key in ("full_text_available", "has_full_text", "full_text"):
            if key in source:
                explicit_full_text = source[key] is True
                break
        if explicit_full_text is not None:
            break
    quote_full_text = any(row.get("full_text") is True for row in rows)
    abstract_only = bool(kinds) and kinds <= {"abstract", "title", "metadata"}
    access_status = _clean(file_meta.get("extracted_status") or file_meta.get("extraction_status")).lower()
    access_denied = file_meta.get("readable") is False or access_status in {
        "permission_denied", "access_denied", "error", "failed", "corrupt", "corrupted"}
    if not rows:
        strength = "insufficient"
        basis.append("没有提供原文引文，不能评估证据强度")
    elif access_denied:
        strength = "insufficient"
        basis.append("文件权限或提取状态不允许读取完整证据")
        limitations.append("需恢复可读全文后才能评估")
    elif explicit_full_text is False or (not quote_full_text and abstract_only):
        strength = "insufficient"
        basis.append("仅有摘要、标题或元数据；摘要不能替代完整证据")
        limitations.append("需要全文的方法、结果与局限部分后才能升级判断")
    elif re.search(r"inconsisten|controvers|conflicting|mixed evidence|heterogeneous|"
                   r"相反|不一致|争议|混杂", combined, re.I):
        strength = "controversial"
        basis.append("原文措辞包含不一致、争议或异质性信号")
    elif re.search(r"\b(may|might|could|suggest|associated|potential|possibly|preliminary)\b|"
                   r"可能|提示|初步|或许|倾向于", combined, re.I):
        strength = "suggestive"
        basis.append("原文使用可能、提示或初步等谨慎措辞，不能视为已证实")
    else:
        direct = bool(re.search(
            r"\b(shows?|demonstrat\w*|reduc\w*|increas\w*|improv\w*|worsen\w*|significant|"
            r"randomi[sz]ed|reproduced|confirmed)\b|显示|表明|提高|降低|改善|恶化|显著|重复验证",
            combined, re.I))
        formal_review = any(token in source_type for token in ("systematic", "meta-analysis", "meta analysis"))
        primary = "primary" in source_type or not source_type
        full_text = explicit_full_text is True or quote_full_text or not abstract_only
        if full_text and direct and (formal_review or primary):
            strength = "proven"
            basis.append("来源类型与全文结果措辞支持直接、可回链的材料证据")
        else:
            strength = "suggestive"
            basis.append("有原文引文，但来源类型或措辞不足以支持 proven")
    if source_type:
        basis.append("来源类型：%s" % source_type)
    else:
        basis.append("来源类型未提供，未自行补造")
        limitations.append("来源类型未知可能影响强度判断")
    if not rows:
        limitations.append("没有 quote/offset 证据，不得据此写强结论")

    refs = []
    for row in rows:
        ref = {key: row.get(key) for key in
               ("evidence_id", "file", "sha256", "chunk_id", "offset", "quote", "kind", "full_text")
               if row.get(key) is not None}
        if ref:
            refs.append(ref)
    return {
        "strength": strength,
        "basis": basis,
        "rationale": "；".join(basis),
        "limitations": limitations,
        "evidence_refs": refs,
    }


# ---------------------------------------------------------------------------
# 5. Contradiction candidates
# ---------------------------------------------------------------------------

_DIRECTION_PATTERNS = [
    ("down", re.compile(r"\b(decreas\w*|reduc\w*|lower\w*|worsen\w*|impair\w*|diminish\w*)\b|"
                        r"降低|减少|下降|恶化|减弱", re.I)),
    ("up", re.compile(r"\b(increas\w*|higher|improv\w*|enhanc\w*|promot\w*|elevat\w*|rais\w*)\b|"
                      r"提高|增加|上升|改善|增强|促进", re.I)),
    ("negative", re.compile(r"\b(ineffective|refut\w*|not significant|no effect|harm\w*)\b|"
                            r"无效|否定|不显著|无效果|有害", re.I)),
    ("positive", re.compile(r"\b(effective|support\w*|significant|benefit\w*)\b|"
                            r"有效|支持|显著|有益", re.I)),
]
_METHOD_PAIRS = [
    (re.compile(r"\bin\s+silico\b|\bcomputational\b|\bDFT\b", re.I),
     re.compile(r"\bin\s+vivo\b|\bexperimental\b|\boperando\b", re.I)),
    (re.compile(r"\bsupervised\b", re.I), re.compile(r"\bunsupervised\b", re.I)),
    (re.compile(r"\bsingle[- ]task\b", re.I), re.compile(r"\bmulti[- ]task\b", re.I)),
]


def _sentence_records(chunk):
    body = _text(chunk.get("text"))
    records = []
    for match in _SENTENCE_RE.finditer(body):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        sentence = raw.strip()
        if not sentence:
            continue
        start = match.start() + leading
        records.append((sentence, start, start + len(sentence)))
    return records


def _direction(sentence):
    found = []
    for label, pattern in _DIRECTION_PATTERNS:
        match = pattern.search(sentence)
        if match:
            found.append((match.start(), label))
    if not found:
        return ""
    found.sort()
    return found[0][1]


def _method_side(sentence):
    for left, right in _METHOD_PAIRS:
        if left.search(sentence):
            return "left"
        if right.search(sentence):
            return "right"
    return ""


def detect_contradictions(index, included_files, themes=None):
    """Find opposite-conclusion or explicitly alternative-method candidates."""
    docs = _resolve_included(index, included_files)
    docs = [doc for doc in docs if doc["chunks"]]
    if len(docs) < 2:
        return []

    theme_by_file = {}
    for theme in _list(themes):
        if not isinstance(theme, dict):
            continue
        for name in _list(theme.get("member_files")):
            theme_by_file[_text(name)] = theme

    statements = []
    for doc in docs:
        for chunk in doc["chunks"]:
            for sentence, start, end in _sentence_records(chunk):
                terms = _term_set(sentence)
                if len(terms) < 2:
                    continue
                direction = _direction(sentence)
                method = _method_side(sentence)
                if not direction and not method:
                    continue
                try:
                    base = int(chunk.get("start") or 0)
                except (TypeError, ValueError):
                    base = 0
                ref = _clip_source_quote(
                    chunk, sentence, max_chars=max(len(sentence) + 20, 120),
                    file_sha=_text(_dict(doc["row"]).get("sha256") or
                                   _dict(doc["row"]).get("sha256_file")))
                if not ref:
                    continue
                ref["offset"] = [base + start, base + end]
                ref["quote"] = sentence
                statements.append({
                    "file": doc["file"], "sentence": sentence, "terms": terms,
                    "direction": direction, "method": method, "ref": ref,
                })

    conflicts, seen = [], set()
    for i, left in enumerate(statements):
        for right in statements[i + 1:]:
            if left["file"] == right["file"]:
                continue
            shared = left["terms"] & right["terms"]
            if not shared:
                continue
            left_theme = theme_by_file.get(left["file"])
            right_theme = theme_by_file.get(right["file"])
            if left_theme is not None and right_theme is not None and \
               left_theme.get("theme_id") != right_theme.get("theme_id"):
                continue

            direction_conflict = bool(left["direction"] and right["direction"] and
                                      left["direction"] != right["direction"])
            # Positive/negative and up/down are the two meaningful opposition axes.
            axis_left = "effect" if left["direction"] in {"positive", "negative"} else "direction"
            axis_right = "effect" if right["direction"] in {"positive", "negative"} else "direction"
            if axis_left != axis_right:
                direction_conflict = False
            method_conflict = bool(left["method"] and right["method"] and
                                   left["method"] != right["method"])
            if not direction_conflict and not method_conflict:
                continue

            pair = tuple(sorted((left["file"], right["file"])))
            theme_name = _clean((left_theme or {}).get("label") or
                                (right_theme or {}).get("label") or
                                sorted(shared, key=lambda x: (-len(x), x))[0])
            key = (pair, theme_name, "direction" if direction_conflict else "method")
            if key in seen:
                continue
            seen.add(key)
            confidence = "high" if direction_conflict and len(shared) >= 2 else \
                "medium" if direction_conflict else "low"
            conflicts.append({
                "theme": theme_name,
                "side_a": {"file": left["file"], "claim": left["sentence"],
                           "evidence_ref": left["ref"]},
                "side_b": {"file": right["file"], "claim": right["sentence"],
                           "evidence_ref": right["ref"]},
                "evidence_refs": [left["ref"], right["ref"]],
                "confidence": confidence,
                "kind": "opposite_conclusion" if direction_conflict else "alternative_method_candidate",
                "basis": "两侧共享主题词 %s，且呈现相反方向或显式互斥方法候选" %
                         ", ".join(sorted(shared)[:5]),
            })
    return conflicts


# ---------------------------------------------------------------------------
# 6. Batch-bounded research gaps
# ---------------------------------------------------------------------------

_METHOD_HINTS = [
    ("DFT", re.compile(r"\bDFT\b|density functional", re.I)),
    ("molecular dynamics", re.compile(r"molecular dynamics|\bMD\b simulations?", re.I)),
    ("operando XRD", re.compile(r"operando\s+XRD", re.I)),
    ("cryo-TEM", re.compile(r"cryo[- ]TEM", re.I)),
    ("impedance spectroscopy", re.compile(r"impedance spectroscopy", re.I)),
    ("randomized trial", re.compile(r"randomi[sz]ed (?:controlled )?trial|\bRCT\b", re.I)),
    ("observational study", re.compile(r"observational study|cohort study|case[- ]control", re.I)),
    ("systematic review", re.compile(r"systematic review", re.I)),
    ("meta-analysis", re.compile(r"meta[- ]analysis", re.I)),
    ("in vitro", re.compile(r"\bin\s+vitro\b", re.I)),
    ("in vivo", re.compile(r"\bin\s+vivo\b", re.I)),
    ("questionnaire", re.compile(r"questionnaire|survey instrument", re.I)),
]


def _methods_for(row, chunks):
    meta = _metadata(row)
    methods = meta.get("methods") or meta.get("method") or []
    if isinstance(methods, str):
        methods = [methods]
    out = [_clean(x) for x in _list(methods) if _clean(x)]
    text = _file_text(row, chunks)
    for label, pattern in _METHOD_HINTS:
        if pattern.search(text) and label not in out:
            out.append(label)
    return out


def _objects_for(row, chunks, fallback_terms):
    meta = _metadata(row)
    objects = meta.get("objects") or meta.get("object") or meta.get("subjects") or []
    if isinstance(objects, str):
        objects = [objects]
    out = [_clean(x) for x in _list(objects) if _clean(x)]
    if not out:
        out = [term for term in fallback_terms if len(term) >= 3][:3]
    return out


def detect_research_gaps(themes, included_files, question=""):
    """Identify missing theme×method/object cells within this batch only."""
    index_like = {"files": included_files if isinstance(included_files, list) else []}
    docs = _resolve_included(index_like, included_files)
    by_file = {doc["file"]: doc for doc in docs}
    theme_rows = [theme for theme in _list(themes) if isinstance(theme, dict) and
                  _list(theme.get("member_files"))]
    if not theme_rows:
        return []

    matrix = []
    for theme in theme_rows:
        members = [_text(x) for x in _list(theme.get("member_files")) if _text(x)]
        methods, objects = set(), set()
        fallback = _list(theme.get("top_terms"))
        for name in members:
            doc = by_file.get(name)
            if not doc:
                continue
            methods.update(_methods_for(doc["row"], doc["chunks"]))
            objects.update(_objects_for(doc["row"], doc["chunks"], fallback))
        matrix.append({
            "theme_id": _text(theme.get("theme_id")),
            "label": _clean(theme.get("label")),
            "member_files": members,
            "methods": sorted(methods),
            "objects": sorted(objects),
        })
    if len(matrix) < 2:
        return []

    all_methods = sorted({method for row in matrix for method in row["methods"]})
    all_objects = sorted({obj for row in matrix for obj in row["objects"]})
    gaps, seen = [], set()

    def add_gap(row, kind, value):
        key = (row["theme_id"], kind, value)
        if key in seen:
            return
        seen.add(key)
        gaps.append({
            "gap": "本批文献中，主题「%s」未观察到%s「%s」的证据；不能推断全球研究空白。" %
                   (row["label"] or row["theme_id"], "方法" if kind == "method" else "对象", value),
            "evidence_matrix": copy_matrix(matrix),
            "affected_files": list(row["member_files"]),
            "limitations": [
                "仅基于传入的 %d 篇纳入文献和可提取字段" % len({f for r in matrix for f in r["member_files"]}),
                "未观察到不等于未开展；可能是检索、提取或报告局限",
                "方法与对象来自元数据或显式文本术语，可能漏检同义表达",
            ],
        })

    for row in matrix:
        if not row["methods"]:
            continue
        for method in all_methods:
            if method not in row["methods"]:
                add_gap(row, "method", method)
        if row["objects"]:
            for obj in all_objects:
                if obj not in row["objects"]:
                    add_gap(row, "object", obj)
    return gaps[:24]


def copy_matrix(matrix):
    return [{
        "theme_id": row["theme_id"], "label": row["label"],
        "methods": list(row["methods"]), "objects": list(row["objects"]),
        "member_files": list(row["member_files"]),
    } for row in matrix]


# ---------------------------------------------------------------------------
# 7. PRISMA-style flow accounting
# ---------------------------------------------------------------------------

def build_prisma_flow(index, screening_log):
    files = _files_from_index(index)
    rows = [row for row in _list(screening_log) if isinstance(row, dict)]
    identified = max(len(files), len(rows))
    duplicate_rows = [row for row in rows if _text(row.get("reason_code")) == "duplicate" or
                      row.get("duplicate_of") or _dict(row.get("metadata")).get("duplicate_of")]
    duplicate_files = {_file_id(row) for row in duplicate_rows}
    nonduplicate = [row for row in rows if _file_id(row) not in duplicate_files]
    included_rows = [row for row in nonduplicate if row.get("decision") == "include"]
    excluded_rows = [row for row in nonduplicate if row.get("decision") == "exclude"]
    review_rows = [row for row in nonduplicate if row.get("decision") == "needs_review"]
    reasons = Counter(_text(row.get("reason_code")) or "unspecified" for row in excluded_rows)

    identified_files = [_file_id(row) for row in files]
    if not identified_files:
        identified_files = [_file_id(row) for row in rows]
    return {
        "identified": identified,
        "duplicates_removed": len(duplicate_rows),
        "screened": max(0, identified - len(duplicate_rows)),
        "excluded": len(excluded_rows),
        "included": len(included_rows),
        "needs_review": len(review_rows),
        "excluded_with_reasons": dict(sorted(reasons.items())),
        "identified_files": identified_files,
        "duplicate_files": [_file_id(row) for row in duplicate_rows],
        "screened_files": [_file_id(row) for row in nonduplicate],
        "excluded_files": [_file_id(row) for row in excluded_rows],
        "included_files": [_file_id(row) for row in included_rows],
        "needs_review_files": [_file_id(row) for row in review_rows],
        "exclusion_details": [{
            "file": _file_id(row), "sha256": _text(row.get("sha256")),
            "reason_code": row.get("reason_code"), "reason": row.get("reason"),
            "evidence_refs": _list(row.get("evidence_refs")),
        } for row in excluded_rows],
        "accounting_note": "screened = identified - duplicates_removed; include/exclude/needs_review 以非重复记录计",
    }


# ---------------------------------------------------------------------------
# 8. Evidence-cited review draft
# ---------------------------------------------------------------------------

def _matrix_rows(evidence_matrix):
    rows = []
    for item in _list(evidence_matrix):
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _evidence_quote(row):
    quote = _clean(row.get("quote") or row.get("extracted_text") or row.get("text"))
    if not quote and isinstance(row.get("locator"), dict):
        quote = _clean(row["locator"].get("quote"))
    return quote


def _row_file(row):
    row = _dict(row)
    return _text(row.get("file") or _dict(row.get("source")).get("file"))


def _row_sha(row):
    row = _dict(row)
    return _text(row.get("sha256") or row.get("sha256_file") or
                 _dict(row.get("source")).get("sha256"))


def _row_locator(row):
    row = _dict(row)
    locator = row.get("locator") if isinstance(row.get("locator"), dict) else row
    return locator


def _first_source_sentence(text, limit=280):
    text = _text(text).strip()
    if not text:
        return ""
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        if sentence:
            return sentence if len(sentence) <= limit else sentence[:limit - 1].rstrip() + "…"
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _placeholder_support(text):
    return bool(re.search(r"candidate evidence|agent must assess|unassessed|placeholder|"
                          r"候选证据|待评估|占位", _text(text), re.I))


def build_review_draft(question, themes, evidence_matrix, conflicts, gaps, protocol):
    """Return Markdown that separates facts, inferences, and unknowns."""
    question = _clean(question) or _clean(_dict(protocol).get("question")) or "未提供研究问题"
    protocol = _dict(protocol)
    matrix = _matrix_rows(evidence_matrix)
    theme_rows = [row for row in _list(themes) if isinstance(row, dict)]
    conflict_rows = [row for row in _list(conflicts) if isinstance(row, dict)]
    gap_rows = [row for row in _list(gaps) if isinstance(row, dict)]

    evidence_registry, source_registry = {}, {}
    source_by_file = {}
    evidence_by_file = defaultdict(list)
    for number, row in enumerate(matrix, 1):
        file_name = _row_file(row)
        source_id = _source_id(row.get("sid") or row.get("source_id"), "S", number)
        evidence_id = _source_id(row.get("evidence_id") or row.get("eid"), "E", number)
        if not evidence_id.startswith("E"):
            evidence_id = "E%d" % number
        if not source_id.startswith("S"):
            source_id = "S%d" % number
        evidence_registry[evidence_id] = row
        source_registry[source_id] = row
        if file_name:
            source_by_file[file_name] = source_id
            evidence_by_file[file_name].append(evidence_id)

    # Theme quotes can supply citations when the caller has not built a matrix.
    next_e = len(evidence_registry) + 1
    next_s = len(source_registry) + 1
    for theme in theme_rows:
        for file_name in _list(theme.get("member_files")):
            file_name = _text(file_name)
            if file_name and file_name not in source_by_file:
                source_id = "S%d" % next_s
                next_s += 1
                source_by_file[file_name] = source_id
                source_registry[source_id] = {"file": file_name}
        for quote in _list(theme.get("evidence_quotes")):
            if not isinstance(quote, dict):
                continue
            file_name = _text(quote.get("file"))
            quote_id = _source_id(quote.get("evidence_id"), "E", next_e)
            if not quote_id.startswith("E"):
                quote_id = "E%d" % next_e
            if quote_id not in evidence_registry:
                evidence_registry[quote_id] = quote
                next_e += 1
            if file_name:
                evidence_by_file[file_name].append(quote_id)
            if file_name and file_name not in source_by_file:
                source_id = "S%d" % next_s
                next_s += 1
                source_by_file[file_name] = source_id
                source_registry[source_id] = {"file": file_name}

    lines = ["# 综述草稿", "", "研究问题：%s" % question, ""]
    lines.extend(["## 筛选协议与范围", ""])
    if protocol.get("prospective"):
        lines.append("- 输入声明为前瞻性/预注册筛选协议；本稿只执行传入标准，不补造未声明条件。")
    else:
        lines.append("- 输入未明确声明前瞻性协议；本稿不推断其注册状态。")
    if protocol.get("inclusion"):
        lines.append("- 纳入标准：%s。" % "；".join(_unique_strings(protocol.get("inclusion"))))
    if protocol.get("exclusion"):
        lines.append("- 排除标准：%s。" % "；".join(_unique_strings(protocol.get("exclusion"))))
    constraints = _dict(protocol.get("constraints"))
    if constraints:
        lines.append("- 已声明约束：%s。" % ", ".join(
            "%s=%s" % (key, value) for key, value in sorted(constraints.items()) if value not in (None, [], {})))
    lines.append("")

    lines.extend(["## 材料事实", ""])
    material_lines = []
    for number, row in enumerate(matrix, 1):
        quote = _evidence_quote(row)
        if not quote:
            continue
        evidence_id = _source_id(row.get("evidence_id") or row.get("eid"), "E", number)
        source_id = _source_id(row.get("sid") or row.get("source_id"), "S", number)
        if not evidence_id.startswith("E"):
            evidence_id = "E%d" % number
        if not source_id.startswith("S"):
            source_id = "S%d" % number
        strength = _clean(row.get("strength")).lower()
        support = _clean(row.get("supports") or row.get("claim"))
        if strength == "proven":
            lead = "材料证据表明"
        elif strength == "suggestive":
            lead = "材料提示（证据强度 suggestive，不能视为已证实）"
        elif strength == "controversial":
            lead = "材料存在争议性表述"
        elif strength == "insufficient":
            lead = "材料证据不足，仅记录为待核线索"
        else:
            lead = "材料记录（尚未完成证据强度判定）"
        sentence = support or quote
        if len(sentence) > 280:
            sentence = sentence[:277].rstrip() + "…"
        material_lines.append("- %s：%s %s%s。" %
                              (lead, sentence, _citation(evidence_id), _citation(source_id)))
        material_lines.append("  > 原文：%s %s" % (quote if len(quote) <= 420 else quote[:417] + "…",
                                                  _citation(evidence_id)))
    if not material_lines:
        material_lines.append("- 无法确定：传入材料中没有带原文引文的证据矩阵，因此不写实质结论。")
    lines.extend(material_lines)
    lines.append("")

    lines.extend(["## 主题归纳", ""])
    if not theme_rows:
        lines.append("- 无法确定：没有可归纳的主题。")
    for theme in theme_rows:
        quotes = [q for q in _list(theme.get("evidence_quotes")) if isinstance(q, dict)]
        refs = []
        for quote in quotes:
            eid = _source_id(quote.get("evidence_id"), "E", 0)
            if eid.startswith("E") and eid[1:].isdigit() and int(eid[1:]) == 0:
                # Locate the registered quote by source locator.
                for registered_id, registered in evidence_registry.items():
                    if registered is quote:
                        eid = registered_id
                        break
            if eid.startswith("E"):
                refs.append(_citation(eid))
        for file_name in _list(theme.get("member_files")):
            sid = source_by_file.get(_text(file_name))
            if sid:
                refs.append(_citation(sid))
        refs = list(dict.fromkeys(refs))
        if refs:
            lines.append("- 材料事实：在本批纳入文献中，主题「%s」覆盖 %d 个来源， recurrent terms 为 %s。%s" %
                         (_clean(theme.get("label")) or _text(theme.get("theme_id")),
                          len(_list(theme.get("member_files"))),
                          ", ".join(_list(theme.get("top_terms"))[:5]), "".join(refs)))
        else:
            lines.append("- 无法确定：主题「%s」只有文件分组，缺少可回链原文，不能据此下实质结论。" %
                         (_clean(theme.get("label")) or _text(theme.get("theme_id"))))
    lines.append("")

    lines.extend(["## 合理推断", ""])
    inference_lines = []
    for theme in theme_rows:
        quotes = [q for q in _list(theme.get("evidence_quotes")) if isinstance(q, dict)]
        refs = []
        for quote in quotes:
            for registered_id, registered in evidence_registry.items():
                if registered is quote:
                    refs.append(_citation(registered_id))
                    break
        for file_name in _list(theme.get("member_files")):
            sid = source_by_file.get(_text(file_name))
            if sid:
                refs.append(_citation(sid))
        refs = list(dict.fromkeys(refs))
        if refs:
            inference_lines.append(
                "- 合理推断：主题「%s」可能涉及 %s 的重复关注；这是对本批材料的归纳，不是领域共识。%s" %
                (_clean(theme.get("label")) or _text(theme.get("theme_id")),
                 ", ".join(_list(theme.get("top_terms"))[:4]), "".join(refs)))
    if not inference_lines:
        inference_lines.append("- 无法确定：缺少原文证据，不提出合理推断。")
    lines.extend(inference_lines)
    lines.append("")

    lines.extend(["## 无法确定：冲突、空白与边界", ""])
    unknown_lines = []
    for conflict in conflict_rows:
        refs = []
        for key in ("evidence_refs",):
            for ref in _list(conflict.get(key)):
                if isinstance(ref, dict):
                    eid = _text(ref.get("evidence_id"))
                    if eid:
                        refs.append(_citation(eid))
        if not refs:
            for side in (conflict.get("side_a"), conflict.get("side_b")):
                ref = _dict(side).get("evidence_ref")
                if isinstance(ref, dict) and ref.get("evidence_id"):
                    refs.append(_citation(ref["evidence_id"]))
        lines.append("- 冲突候选：%s；当前无法确定哪一侧更可靠。%s" %
                     (_clean(conflict.get("theme")) or "同一主题", "".join(refs)))
        unknown_lines.append(conflict)
    for gap in gap_rows:
        refs = []
        for file_name in _list(gap.get("affected_files")):
            sid = source_by_file.get(_text(file_name))
            if sid:
                refs.append(_citation(sid))
        lines.append("- 研究空白（仅本批范围）：%s%s" %
                     (_clean(gap.get("gap")) or "传入材料未给出可陈述的空白",
                      "".join(list(dict.fromkeys(refs)))))
        unknown_lines.append(gap)
    if not conflict_rows and not gap_rows:
        lines.append("- 无法确定：没有传入冲突或覆盖矩阵空白记录；这不代表不存在冲突或全球空白。")
    lines.append("")

    lines.extend(["## 引用索引", ""])
    if evidence_registry:
        for eid in sorted(evidence_registry, key=lambda x: (0, int(x[1:])) if x[1:].isdigit() else (1, x)):
            row = evidence_registry[eid]
            file_name = _text(row.get("file") or _dict(row.get("source")).get("file"))
            lines.append("- %s %s | sha256=%s | chunk=%s | offset=%s" %
                         (_citation(eid), file_name, _text(row.get("sha256")),
                          _text(row.get("chunk_id")), _text(row.get("offset"))))
    else:
        lines.append("- 无 E 类证据引用。")
    if source_registry:
        for sid in sorted(source_registry, key=lambda x: (0, int(x[1:])) if x[1:].isdigit() else (1, x)):
            row = source_registry[sid]
            file_name = _text(row.get("file") or _dict(row.get("source")).get("file"))
            lines.append("- %s %s | sha256=%s" %
                         (_citation(sid), file_name, _text(row.get("sha256"))))
    else:
        lines.append("- 无 S 类来源引用。")
    return "\n".join(lines).rstrip() + "\n"
