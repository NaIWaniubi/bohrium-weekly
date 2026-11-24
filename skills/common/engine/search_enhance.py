# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only semantic search enhancements.

The helpers in this module operate only on text and metadata supplied by the
caller.  They never open a corpus, call an external search service, or invent a
quote.  Offsets produced for quotes are Python string offsets into the exact
``hit["text"]`` value; ``source_offset`` is supplied when the chunk's original
``start`` offset is known.
"""
import json
import math
import os
import posixpath
import re
from collections import defaultdict


__all__ = [
    "extract_nl_constraints",
    "rewrite_queries",
    "pseudo_relevance_feedback",
    "diversify_results",
    "select_claim_evidence",
    "cross_file_consensus",
    "build_search_report",
]


# ---------------------------------------------------------------------------
# Shared normalization and tokenization
# ---------------------------------------------------------------------------

_ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "could", "did", "do", "does", "doing", "for", "from", "had", "has",
    "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "itself", "may", "might", "more",
    "most", "of", "on", "or", "our", "ours", "over", "she", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "up",
    "use", "using", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "why", "will", "with", "would", "you", "your",
    "find", "show", "list", "tell", "only", "look",
}

_CJK_STOPWORDS = {
    "我们", "你们", "他们", "这个", "那个", "这些", "那些", "一个", "一种",
    "进行", "通过", "以及", "并且", "同时", "可以", "需要", "关于", "对于",
    "由于", "因此", "但是", "然而", "其中", "目前", "主要", "相关", "工作",
    "研究", "方法", "结果", "结论", "问题", "内容", "材料", "文献", "文档",
}

_CJK_LOW_INFO_MARKERS = (
    "年最终版中", "最终版中", "中关于", "关于", "只看", "找出", "中的", "说明",
    "如何", "什么", "是否", "哪些", "哪个", "哪里", "怎样", "多少", "请", "以及",
    "并且", "同时",
)
_CJK_SPLIT_RE = re.compile("|".join(re.escape(item) for item in
                                  sorted(set(_CJK_STOPWORDS) | set(_CJK_LOW_INFO_MARKERS),
                                         key=len, reverse=True)))

_INTENT_WORDS = {
    "compare", "comparison", "versus", "vs", "difference", "different", "similar",
    "time", "temporal", "trend", "evolution", "year", "date", "where", "which",
    "section", "chapter", "page", "location", "locate", "find", "search", "list",
    "what", "how", "why", "main", "please", "document", "file", "question",
    "比较", "对比", "差异", "不同", "相似", "时间", "趋势", "年份", "哪里",
    "哪个", "章节", "页码", "定位", "查找", "检索", "搜索", "列出", "请",
}

_RELATION_WORDS = {
    "improve", "improves", "improved", "improvement", "increase", "increases",
    "increased", "decrease", "decreases", "decreased", "higher", "lower", "better",
    "worse", "outperform", "outperforms", "effective", "ineffective", "significant",
    "significantly", "support", "supports", "supported", "consistent", "inconsistent",
    "contradict", "contradicts", "confirm", "confirms", "show", "shows", "suggest",
    "suggests", "indicate", "indicates", "associated", "association", "correlated",
    "correlation", "cause", "causes", "lead", "leads", "result", "results",
    "提高", "增加", "提升", "改善", "降低", "下降", "优于", "劣于", "有效", "无效",
    "显著", "支持", "不支持", "一致", "不一致", "相反", "证实", "表明", "显示",
    "提示", "相关", "关联", "导致", "结果", "发现", "未发现",
}

# Small, deliberately domain-oriented synonym table.  It is used for query
# rewriting and for matching paraphrases; it is not a source of factual claims.
_SYNONYM_GROUPS = [
    {"graph neural network", "graph neural networks", "gnn", "gnns", "图神经网络"},
    {"molecular property", "molecular properties", "分子性质"},
    {"neural network", "neural networks", "神经网络"},
    {"transformer", "transformers", "transformer model", "变换器"},
    {"prediction", "predict", "predicting", "预测"},
    {"retrieval", "search", "searching", "检索", "搜索"},
    {"document", "documents", "paper", "papers", "literature", "文献", "文档", "文件"},
    {"version", "revision", "rev", "版本", "修订"},
    {"metadata", "meta", "元数据", "元信息"},
    {"alias", "aliases", "别名"},
    {"repair", "recovery", "restore", "restoration", "修复", "恢复"},
    {"index", "indexing", "索引"},
    {"section", "chapter", "章节"},
    {"evidence", "citation", "quote", "证据", "引用", "引文"},
    {"author", "creator", "作者"},
    {"project", "项目"},
    {"batch", "批次"},
    {"method", "methodology", "方法"},
    {"result", "results", "conclusion", "finding", "findings", "结果", "结论", "发现"},
    {"compare", "comparison", "versus", "vs", "比较", "对比"},
    {"time", "temporal", "trend", "evolution", "时间", "趋势"},
]

_ALIAS_TO_CANONICAL = {}
_CANONICAL_TO_ALIASES = {}
for _group in _SYNONYM_GROUPS:
    _canonical = sorted(_group, key=lambda value: (len(value), value))[0]
    _ordered = sorted(_group, key=lambda value: (-len(value), value))
    _CANONICAL_TO_ALIASES[_canonical] = _ordered
    for _alias in _group:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canonical

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*|[\u4e00-\u9fff]+")
_ASCII_TERM_RE = re.compile(r"^[a-z0-9_+\-]+$", re.I)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"'，。；：！？、（）]+", re.I)


def _text(value):
    """Coerce a value to text without letting corrupt containers explode."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("text", "q", "question", "research_question", "value"):
            if key in value and isinstance(value[key], (str, int, float)):
                return str(value[key])
        return ""
    if isinstance(value, (list, tuple, set)):
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _list(value, wrapper_keys=()):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, dict):
        for key in wrapper_keys:
            if key in value:
                return _list(value[key])
        return [value]
    return [value]


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _norm_term(value):
    return re.sub(r"\s+", " ", _text(value).strip().lower())


def _is_stopword(term):
    term = _norm_term(term)
    if not term or term.isdigit():
        return True
    if term in _ENGLISH_STOPWORDS or term in _CJK_STOPWORDS:
        return True
    if term in _CJK_LOW_INFO_MARKERS or len(term) == 1:
        return True
    return False


def _contains_term(haystack, term):
    haystack = _text(haystack).lower()
    term = _norm_term(term)
    if not term:
        return False
    if _ASCII_TERM_RE.match(term):
        return re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", haystack) is not None
    return term in haystack


def _iter_lexemes(value):
    text = _text(value)
    for match in _WORD_RE.finditer(text):
        raw = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            yield raw.lower(), match.start(), match.end(), "cjk"
        else:
            yield raw.lower(), match.start(), match.end(), "ascii"


def _term_features(value, expand_aliases=False):
    """Return term -> weight features, including compact CJK n-grams."""
    text = _text(value)
    features = {}
    for raw, _start, _end, kind in _iter_lexemes(text):
        term = _norm_term(raw)
        if kind == "ascii":
            if _is_stopword(term):
                continue
            weight = 2.4 if len(term) >= 5 else (1.8 if len(term) >= 3 else 1.2)
            features[term] = max(features.get(term, 0.0), weight)
        else:
            # Split before making n-grams so instruction fragments such as
            # “只看并找出” do not turn into misleading entity plans.
            for segment in _CJK_SPLIT_RE.split(term):
                segment = segment.strip()
                if _is_stopword(segment):
                    continue
                if 2 <= len(segment) <= 12:
                    features[segment] = max(features.get(segment, 0.0), 3.0)
                for size in (2, 3, 4):
                    if len(segment) <= size:
                        continue
                    for index in range(len(segment) - size + 1):
                        piece = segment[index:index + size]
                        if not _is_stopword(piece):
                            features[piece] = max(features.get(piece, 0.0),
                                                  1.15 if size == 2 else 1.0)
    doi = _DOI_RE.search(text)
    if doi:
        features[doi.group(0).rstrip(".,;:!?)]}，。；：！？、").lower()] = 4.0
    if expand_aliases:
        expanded = dict(features)
        for term, weight in features.items():
            canonical = _ALIAS_TO_CANONICAL.get(term)
            if not canonical:
                continue
            for alias in _CANONICAL_TO_ALIASES.get(canonical, [canonical]):
                if alias != term:
                    expanded[alias] = max(expanded.get(alias, 0.0), weight * 0.82)
        features = expanded
    return features


def _focus_terms(value, limit=8):
    """Pick discriminative terms without returning many overlapping n-grams."""
    features = _term_features(value)
    ranked = sorted(features.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    selected = []
    for term, _weight in ranked:
        if any(term != kept and term in kept for kept in selected):
            continue
        if any(kept != term and kept in term and len(kept) >= 4 for kept in selected):
            continue
        selected.append(term)
        if len(selected) >= max(0, limit):
            break
    return selected


def _aliases_for(term):
    canonical = _ALIAS_TO_CANONICAL.get(_norm_term(term))
    if canonical:
        return _CANONICAL_TO_ALIASES.get(canonical, [canonical])
    return [_norm_term(term)]


def _matched_synonym_groups(value):
    """Return synonym groups explicitly present in text, longest match first."""
    text = _text(value).lower()
    matched = []
    for canonical, aliases in _CANONICAL_TO_ALIASES.items():
        present = [alias for alias in aliases if _contains_term(text, alias)]
        if present:
            matched.append((canonical, sorted(present, key=lambda item: (-len(item), item))))
    return matched


# ---------------------------------------------------------------------------
# 1. Natural-language hard constraints
# ---------------------------------------------------------------------------

_FILTER_KEYS = {
    "type", "ext", "extensions", "dir", "directories", "include", "min_size",
    "max_size", "exclude", "need_text", "year", "min_year", "max_year", "project",
    "batch", "path_contains", "name_contains", "author", "doi", "sha256",
    "version", "readable", "permission",
}

_TYPE_ALIASES = {
    "pdf": [".pdf"], "pdf文件": [".pdf"], "pdf 文件": [".pdf"],
    "word": [".doc", ".docx"], "word文档": [".doc", ".docx"], "docx": [".docx"],
    "doc": [".doc"], "excel": [".xls", ".xlsx"], "xlsx": [".xlsx"], "xls": [".xls"],
    "ppt": [".ppt", ".pptx"], "powerpoint": [".ppt", ".pptx"], "pptx": [".pptx"],
    "csv": [".csv"], "json": [".json"], "jsonl": [".jsonl"], "txt": [".txt"],
    "text": [".txt"], "文本": [".txt"], "文本文件": [".txt"], "markdown": [".md"],
    "md": [".md"], "图片": [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"],
    "image": [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"],
}

_TYPE_RE = re.compile(
    r"(?<![\w])(?:pdf|word|docx|excel|xlsx|xls|powerpoint|pptx|ppt|csv|jsonl?|txt|text|markdown|"
    r"文本文件|文本|图片|PDF|Word|Excel|PowerPoint|PPT)(?![\w])|\.(?:pdf|docx?|xlsx?|pptx?|csv|jsonl?|txt|md)\b",
    re.I,
)


def _normalise_type_value(value):
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            for ext in _normalise_type_value(item):
                if ext not in out:
                    out.append(ext)
        return out
    raw = _norm_term(value)
    if not raw:
        return []
    if raw in _TYPE_ALIASES:
        return list(_TYPE_ALIASES[raw])
    if raw.startswith("."):
        return [raw]
    if raw in ("*", "all", "any"):
        return ["*"]
    return ["." + raw]


def _normalise_permission(value):
    raw = _norm_term(value)
    if raw in {"read", "read-only", "readonly", "readable", "只读", "可读", "可读取"}:
        return "readable"
    if raw in {"write", "writable", "read-write", "可写", "可写入"}:
        return "writable"
    return _text(value).strip()


def _raw_value(value):
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _structured_constraint(field, value, source):
    return {
        "field": field,
        "value": value,
        "raw": _raw_value(value),
        "span": None,
        "evidence_span": None,
        "source": source,
        "structured": True,
        "applied": True,
    }


def _merge_structured_spec(spec, source, filters, constraints):
    if isinstance(spec, dict) and isinstance(spec.get("properties"), dict):
        spec = spec["properties"]
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key in {"id", "name", "description", "reason"} or value in (None, "", [], False):
                continue
            field = str(key)
            if field in {"ext", "extensions"}:
                field = "type"
            elif field in {"directories", "directory"}:
                field = "dir"
            if field in {"type", "ext", "extensions"}:
                value = _normalise_type_value(value)
            elif field == "permission":
                value = _normalise_permission(value)
            filters[field] = value
            constraints.append(_structured_constraint(field, value, source + "." + str(key)))
        return
    if not isinstance(spec, list):
        return
    for item in spec:
        if isinstance(item, str):
            match = re.match(r"\s*([A-Za-z_][\w.-]*)\s*[:=]\s*(.+?)\s*$", item)
            if match:
                _merge_structured_spec({match.group(1): match.group(2)}, source, filters, constraints)
            continue
        if not isinstance(item, dict):
            continue
        field = item.get("field") or item.get("key") or item.get("constraint")
        if not field:
            _merge_structured_spec(item, source, filters, constraints)
            continue
        value = item.get("value") if "value" in item else item.get("equals")
        op = _norm_term(item.get("op") or item.get("operator") or "eq")
        field = str(field)
        if field == "year" and op in {"gte", ">=", "min", "from"}:
            field, value = "min_year", value
        elif field == "year" and op in {"lte", "<=", "max", "to"}:
            field, value = "max_year", value
        elif field == "size" and op in {"gte", ">=", "min"}:
            field, value = "min_size", value
        elif field == "size" and op in {"lte", "<=", "max"}:
            field, value = "max_size", value
        elif field in {"ext", "extensions"}:
            field = "type"
        elif field in {"directories", "directory"}:
            field = "dir"
        if field == "type":
            value = _normalise_type_value(value)
        elif field == "permission":
            value = _normalise_permission(value)
        filters[field] = value
        constraints.append(_structured_constraint(field, value, source))


def _case_filters(case):
    filters, constraints = {}, []
    if not isinstance(case, dict):
        return filters, constraints
    for key in sorted(_FILTER_KEYS):
        if key not in case or case[key] in (None, "", [], False):
            continue
        value = case[key]
        field = "type" if key in {"ext", "extensions"} else ("dir" if key in {"directories", "directory"} else key)
        if field == "type":
            value = _normalise_type_value(value)
        elif field == "permission":
            value = _normalise_permission(value)
        filters[field] = value
        constraints.append(_structured_constraint(field, value, "case." + key))
    for container in ("filter", "filters", "constraints"):
        if container in case:
            _merge_structured_spec(case[container], "case." + container, filters, constraints)
    return filters, constraints


def _span(text, start, end):
    return [max(0, int(start)), max(0, int(end))]


def _nl_constraint(field, value, raw, source, text, start, end, applied=True):
    span = _span(text, start, end)
    return {
        "field": field,
        "value": value,
        "raw": raw,
        "span": span,
        "evidence_span": list(span),
        "start": span[0],
        "end": span[1],
        "source": source,
        "structured": False,
        "applied": bool(applied),
    }


def _merge_nl_value(filters, field, value, source, text, start, end, constraints):
    applied = field not in filters
    if applied:
        if field == "type" and isinstance(filters.get(field), list):
            current = list(filters[field])
            for ext in value:
                if ext not in current:
                    current.append(ext)
            filters[field] = current
        elif field == "year" and isinstance(value, dict) and isinstance(filters.get(field), dict):
            merged = dict(filters[field])
            merged.update(value)
            filters[field] = merged
        else:
            filters[field] = value
    constraints.append(_nl_constraint(field, value, text[start:end], source, text, start, end, applied))


def _occupied(spans, start, end):
    return any(not (end <= old_start or start >= old_end) for old_start, old_end in spans)


def _parse_nl_text(text, source, filters, constraints):
    text = _text(text)
    if not text.strip():
        return
    occupied = []

    # DOI is self-identifying.  Parse it before years so a year-looking DOI
    # suffix (for example 10.1234/2023.paper) is not misread as a date filter.
    for match in _DOI_RE.finditer(text):
        doi = match.group(0).rstrip(".,;:!?)]}，。；：！？、")
        end = match.start() + len(doi)
        _merge_nl_value(filters, "doi", doi, source, text, match.start(), end, constraints)
        occupied.append((match.start(), end))

    # Year ranges: 2021-2023, 2021至2023年, between 2021 and 2023.
    range_patterns = [
        re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:[-–—~～/]|至|到)\s*((?:19|20)\d{2})(?!\d)(?:\s*年)?"),
        re.compile(r"\bbetween\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})\b", re.I),
        re.compile(r"\bfrom\s+((?:19|20)\d{2})\s+to\s+((?:19|20)\d{2})\b", re.I),
    ]
    for pattern in range_patterns:
        for match in pattern.finditer(text):
            if _occupied(occupied, match.start(), match.end()):
                continue
            lo, hi = int(match.group(1)), int(match.group(2))
            if lo > hi:
                lo, hi = hi, lo
            _merge_nl_value(filters, "year", {"min": lo, "max": hi}, source, text,
                            match.start(), match.end(), constraints)
            occupied.append((match.start(), match.end()))

    # Open ranges and strict before/after phrasing.
    open_patterns = [
        (re.compile(r"((?:19|20)\d{2})\s*年?\s*(?:以来|以后|之后|起)"), "min"),
        (re.compile(r"\b(?:since|after|from)\s+((?:19|20)\d{2})\b", re.I), "min"),
        (re.compile(r"((?:19|20)\d{2})\s*年?\s*(?:以前|之前|前)"), "before"),
        (re.compile(r"\bbefore\s+((?:19|20)\d{2})\b", re.I), "before"),
    ]
    for pattern, mode in open_patterns:
        for match in pattern.finditer(text):
            if _occupied(occupied, match.start(), match.end()):
                continue
            year = int(match.group(1))
            if mode == "min":
                value = {"min": year + 1} if re.search(r"\bafter\b", match.group(0), re.I) else {"min": year}
            else:
                value = {"max": year - 1}
            _merge_nl_value(filters, "year", value, source, text, match.start(), match.end(), constraints)
            occupied.append((match.start(), match.end()))

    # Exact years.  Bare four-digit years are accepted only in the modern range;
    # range components already consumed above are skipped.
    exact_patterns = [
        re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)(?:\s*年)?"),
    ]
    for pattern in exact_patterns:
        for match in pattern.finditer(text):
            if _occupied(occupied, match.start(), match.end()):
                continue
            year = int(match.group(1))
            _merge_nl_value(filters, "year", year, source, text, match.start(), match.end(), constraints)
            occupied.append((match.start(), match.end()))

    # File types.  Negated types become exclude globs; positive types merge.
    for match in _TYPE_RE.finditer(text):
        if _occupied(occupied, match.start(), match.end()):
            continue
        raw = match.group(0)
        key = raw.lower().lstrip(".")
        exts = _normalise_type_value(key)
        if not exts:
            continue
        prefix = text[max(0, match.start() - 12):match.start()]
        negated = re.search(r"(?:排除|不包括|不包含|不含|不要|禁用|exclude|excluding|not)\s*$", prefix, re.I)
        if negated:
            field = "exclude"
            value = ["*" + ext for ext in exts]
        else:
            field = "type"
            value = exts
        _merge_nl_value(filters, field, value, source, text, match.start(), match.end(), constraints)
        occupied.append((match.start(), match.end()))

    # Labeled string constraints.  Values stop at punctuation or the next label.
    labels = [
        ("project", r"项目|project"),
        ("batch", r"批次|batch"),
        ("author", r"作者|author"),
        ("version", r"版本|version"),
    ]
    for field, label_pattern in labels:
        pattern = re.compile(
            r"(?:" + label_pattern + r")\s*[:：]?\s*[\"“']?"
            r"([^，,。；;\n\"”']+?)"
            r"(?=[，,。；;\n\"”']|$|\s*(?:项目|批次|作者|版本|权限|DOI|doi|project|batch|author|version|permission)\b)",
            re.I,
        )
        for match in pattern.finditer(text):
            if _occupied(occupied, match.start(), match.end()):
                continue
            value = match.group(1).strip()
            if not value:
                continue
            if field == "version":
                value = re.sub(r"^(?:version|版本)\s*[:：]?\s*", "", value, flags=re.I).strip()
            _merge_nl_value(filters, field, value, source, text, match.start(), match.end(), constraints)
            occupied.append((match.start(), match.end()))

    # Permissions are hard only when an access mode is explicitly named.
    permission_patterns = [
        (re.compile(r"(?:权限\s*)?(?:只读|可读|可读取|read[\s-]?only|readable)", re.I), "readable"),
        (re.compile(r"(?:权限\s*)?(?:可写|可写入|writable|read[\s-]?write)", re.I), "writable"),
    ]
    for pattern, permission in permission_patterns:
        for match in pattern.finditer(text):
            if _occupied(occupied, match.start(), match.end()):
                continue
            _merge_nl_value(filters, "permission", permission, source, text,
                            match.start(), match.end(), constraints)
            occupied.append((match.start(), match.end()))


def extract_nl_constraints(question, task_md="", case=None):
    """Extract explicit hard constraints and retain exact source spans.

    Structured ``case`` filters are authoritative and are kept compatible with
    ``answer.apply_filters``.  Natural-language constraints only fill fields not
    already supplied structurally.  Ambiguous prose is intentionally ignored.
    """
    filters, constraints = _case_filters(case)
    question = _text(question)
    task_md = _text(task_md)
    _parse_nl_text(question, "question", filters, constraints)
    _parse_nl_text(task_md, "task_md", filters, constraints)
    return {"filters": filters, "constraints": constraints}


# ---------------------------------------------------------------------------
# 2. Query rewriting
# ---------------------------------------------------------------------------

_METHOD_WORDS = {
    "method", "methods", "methodology", "model", "models", "algorithm", "network",
    "neural", "transformer", "gnn", "regression", "classification", "clustering",
    "方法", "模型", "算法", "网络",
}
_OBJECT_WORDS = {
    "property", "properties", "prediction", "dataset", "benchmark", "task", "target",
    "outcome", "performance", "accuracy", "solubility", "molecular", "性质", "预测",
    "数据", "任务", "性能", "准确率",
}


def _clauses(question):
    text = _text(question).strip()
    if not text:
        return []
    parts = re.split(r"[。！？!?；;\n]+|(?:，|,)\s*(?=(?:比较|对比|分别|同时|以及|并且|compare|which|what|how|where))", text)
    out = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip(" ，,。；;！？!?")
        if part and part not in out:
            out.append(part)
    return out


def _terms_from_file(terms_txt):
    if isinstance(terms_txt, (list, tuple, set)):
        lines = [_text(item) for item in terms_txt]
    else:
        lines = _text(terms_txt).splitlines()
    groups, loose = [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "|" in line or "=>" in line or "→" in line:
            group = [piece.strip() for piece in re.split(r"\||=>|→", line) if piece.strip()]
            if group:
                groups.append(group)
        else:
            pieces = [piece.strip() for piece in re.split(r"[,，;；\t]+", line) if piece.strip()]
            if len(pieces) > 1:
                groups.append(pieces)
            elif pieces:
                loose.append(pieces[0])
    return groups, loose


def _intent_kind(text):
    low = _text(text).lower()
    if re.search(r"比较|对比|差异|不同|compare|comparison|versus|\bvs\b|difference", low):
        return "comparison"
    if re.search(r"时间|趋势|变化|演变|over time|temporal|trend|evolution|histor", low):
        return "temporal"
    if re.search(r"哪里|哪个章节|哪一页|定位|where|which (?:section|chapter|page)|locate|location", low):
        return "localization"
    return None


def _plan(kind, text, source_terms):
    return {
        "plan_id": "",
        "text": re.sub(r"\s+", " ", _text(text)).strip(),
        "kind": kind,
        "source_terms": [str(term) for term in source_terms if _text(term)],
    }


def rewrite_queries(question, terms_txt="", max_plans=12):
    """Rewrite a question into focused, explainable retrieval plans."""
    limit = max(0, _int(max_plans, 12))
    if limit == 0:
        return []
    question = _text(question).strip()
    groups, loose_terms = _terms_from_file(terms_txt)
    clauses = _clauses(question)
    source = question if question else " ".join(loose_terms)
    focus = _focus_terms(source, limit=8)
    plans = []
    seen = set()

    def add(kind, text, source_terms):
        candidate = _plan(kind, text, source_terms)
        key = candidate["text"].casefold()
        if not key or key in seen:
            return
        seen.add(key)
        plans.append(candidate)

    overall_kind = _intent_kind(question)
    for clause in clauses:
        terms = _focus_terms(clause, limit=5)
        kind = _intent_kind(clause)
        if kind == "comparison":
            add(kind, clause + " comparison differences similarities", terms)
        elif kind == "temporal":
            add(kind, clause + " temporal trend evolution", terms)
        elif kind == "localization":
            add(kind, clause + " section chapter page location", terms)
        elif clause != question:
            add("subquestion", clause, terms)

    # Explicit expansion groups are evidence of user-supplied vocabulary, so
    # each member becomes its own focused plan rather than a boolean blob.
    # Process these before generic entity plans so a small max_plans budget
    # cannot silently discard the requested cross-language vocabulary.
    for group in groups:
        for alias in group:
            add("synonym", alias, group)
    for term in loose_terms:
        add("expansion", term, [term])

    # Entity/method/object plans ensure a single long sentence is not the only
    # retrieval request sent to a filename or lexical search channel.
    for term in focus[:6]:
        if term in _INTENT_WORDS or term in _RELATION_WORDS or _is_stopword(term):
            continue
        canonical = _ALIAS_TO_CANONICAL.get(term, term)
        if term in _METHOD_WORDS or canonical in _METHOD_WORDS:
            kind = "method"
            add(kind, term + " method methodology approach", [term])
        elif term in _OBJECT_WORDS or canonical in _OBJECT_WORDS:
            kind = "object"
            add(kind, term + " outcome result dataset", [term])
        else:
            kind = "entity"
            add(kind, term, [term])

    # Scan complete synonym aliases directly as well.  This catches a Chinese
    # alias embedded in a longer unsegmented phrase (for example 图神经网络在…).
    for canonical, present in _matched_synonym_groups(question):
        source_term = present[0]
        aliases = _CANONICAL_TO_ALIASES.get(canonical, [canonical])
        english = [alias for alias in aliases if alias.isascii()]
        multiword = [alias for alias in english if len(alias.split()) >= 2]
        preferred = min(multiword or english or aliases, key=lambda item: (len(item), item))
        add("synonym", preferred, [source_term])

    if overall_kind == "comparison":
        add("comparison", (focus[0] if focus else source) + " comparison versus differences", focus[:4])
    elif overall_kind == "temporal":
        add("temporal", (focus[0] if focus else source) + " time trend evolution", focus[:4])
    elif overall_kind == "localization":
        add("localization", (focus[0] if focus else source) + " section chapter page", focus[:4])

    # Cross-language synonyms from the question itself.
    for term in focus:
        canonical = _ALIAS_TO_CANONICAL.get(term)
        if not canonical:
            continue
        for alias in _CANONICAL_TO_ALIASES.get(canonical, []):
            if alias.lower() != term:
                add("synonym", alias, [term])

    if not plans and question:
        add("overview", question, focus)
    elif question:
        # Keep the original as an audit/recall plan, but never as the only plan
        # unless max_plans=1 and no focused rewrite was possible.
        if len(plans) < limit:
            add("overview", question, focus)

    for index, plan in enumerate(plans[:limit], 1):
        plan["plan_id"] = "plan-%02d" % index
    return plans[:limit]


# ---------------------------------------------------------------------------
# 3. Pseudo-relevance feedback
# ---------------------------------------------------------------------------

def _chunk_map(chunks):
    by_id = defaultdict(list)
    by_file = defaultdict(list)
    for item in _list(chunks):
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("chunk_id")
        file = item.get("file")
        if chunk_id is not None:
            by_id[str(chunk_id)].append(item)
        if file:
            by_file[str(file)].append(item)
    return by_id, by_file


def _resolve_hit(hit, by_id, by_file):
    if not isinstance(hit, dict):
        return None
    body = hit.get("text")
    if isinstance(body, str) and body:
        return hit
    chunk_id = hit.get("chunk_id")
    file = hit.get("file")
    candidates = by_id.get(str(chunk_id), []) if chunk_id is not None else []
    if file and len(candidates) > 1:
        candidates = [item for item in candidates if item.get("file") == file]
    if len(candidates) == 1:
        return candidates[0]
    if file:
        candidates = by_file.get(str(file), [])
        start = hit.get("start")
        if start is not None:
            matching = [item for item in candidates if item.get("start") == start]
            if matching:
                return matching[0]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _merge_hit_metadata(hit, resolved):
    """Keep verified file facts carried by a hit row when resolving its chunk."""
    if not isinstance(hit, dict) or not isinstance(resolved, dict):
        return resolved
    merged = dict(resolved)
    for key in ("sha256_file", "sha256", "file", "chunk_id", "start", "end", "kind"):
        if merged.get(key) in (None, "") and hit.get(key) not in (None, ""):
            merged[key] = hit[key]
    return merged


def _rank_value(hit, index):
    if not isinstance(hit, dict):
        return (2, 0.0, index)
    rank = hit.get("rank")
    if rank is not None:
        return (0, _number(rank, float("inf")), index)
    score = _number(hit.get("score_raw"), float("nan"))
    if not math.isnan(score):
        return (1, -score, index)
    return (2, 0.0, index)


def _candidate_expansion_terms(sentence):
    candidates = []
    for match in _WORD_RE.finditer(sentence):
        raw = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            term = raw.lower()
            for size in range(2, min(7, len(term)) + 1):
                for index in range(len(term) - size + 1):
                    piece = term[index:index + size]
                    if not _is_stopword(piece):
                        candidates.append((piece, match.start() + index, match.start() + index + size))
        else:
            term = raw.lower()
            if len(term) >= 3 and not _is_stopword(term) and not term.isdigit():
                candidates.append((term, match.start(), match.end()))
    return candidates


def pseudo_relevance_feedback(chunks, hits, question, terms_txt="", top_docs=3, max_terms=12):
    """Expand a query using only terms found in top-ranked supplied hits."""
    doc_limit = max(0, _int(top_docs, 3))
    term_limit = max(0, _int(max_terms, 12))
    if doc_limit == 0 or term_limit == 0:
        return {"expansion_terms": [], "evidence": [], "term_evidence": {}}

    base_features = _term_features(_text(question) + "\n" + _text(terms_txt))
    if not base_features:
        return {"expansion_terms": [], "evidence": [], "term_evidence": {}}
    base_terms = set(base_features)

    by_id, by_file = _chunk_map(chunks)
    ordered = [hit for hit in _list(hits, ("hits",)) if isinstance(hit, dict)]
    ordered = [item for item in sorted(enumerate(ordered), key=lambda pair: _rank_value(pair[1], pair[0]))]
    selected, seen_docs = [], set()
    for _index, hit in ordered:
        resolved = _resolve_hit(hit, by_id, by_file)
        if not resolved:
            continue
        resolved = _merge_hit_metadata(hit, resolved)
        file = resolved.get("file")
        chunk_id = resolved.get("chunk_id")
        doc_key = str(file or chunk_id or "")
        if not doc_key or doc_key in seen_docs:
            continue
        body = resolved.get("text")
        if not isinstance(body, str) or not body:
            continue
        seen_docs.add(doc_key)
        selected.append(resolved)
        if len(selected) >= doc_limit:
            break
    if not selected:
        return {"expansion_terms": [], "evidence": [], "term_evidence": {}}

    scores = defaultdict(float)
    occurrences = defaultdict(list)
    source_docs = defaultdict(set)
    for hit in selected:
        body = hit.get("text") or ""
        sentences = [body[start:end] for start, end in _iter_sentence_spans(body)]
        if not sentences:
            sentences = [body]
        for sentence in sentences:
            sentence_features = _term_features(sentence)
            query_overlap = [term for term in base_terms if _contains_term(sentence, term)]
            if not query_overlap:
                continue
            for term, start, end in _candidate_expansion_terms(sentence):
                if term in base_terms:
                    continue
                if any(term in base and len(base) > len(term) for base in base_terms):
                    continue
                score = 2.0 + min(2.0, len(query_overlap) * 0.35)
                score += 0.8 if len(term) >= 5 else 0.2
                if not term.isascii():
                    score += 0.35 if len(term) >= 3 else 0.0
                scores[term] += score
                source_docs[term].add(str(hit.get("file") or hit.get("chunk_id") or ""))
                if len(occurrences[term]) < 3:
                    chunk_start = hit.get("start")
                    source_offset = None
                    if isinstance(chunk_start, int):
                        source_offset = [chunk_start + start, chunk_start + end]
                    occurrences[term].append({
                        "term": term,
                        "file": hit.get("file"),
                        "chunk_id": hit.get("chunk_id"),
                        "sha256_file": hit.get("sha256_file"),
                        "offset": [start, end],
                        "source_offset": source_offset,
                        "file_offset": source_offset,
                        "offset_space": "chunk_text",
                        "text": body[start:end],
                    })

    ranked = sorted(
        scores,
        key=lambda term: (-scores[term], -len(source_docs[term]), -len(term), term),
    )[:term_limit]
    evidence = []
    term_evidence = {}
    for term in ranked:
        rows = occurrences.get(term, [])
        term_evidence[term] = rows
        evidence.extend(rows)
    return {
        "expansion_terms": ranked,
        "evidence": evidence,
        "term_evidence": term_evidence,
    }


# ---------------------------------------------------------------------------
# 4. Result diversification
# ---------------------------------------------------------------------------

_VERSION_TOKEN_RE = re.compile(
    r"(?:[_\-\s]*(?:v(?:er(?:sion)?)?\s*\d+(?:\.\d+)*|rev(?:ision)?\s*\d*|draft|final|"
    r"submitted|accepted|草稿|终稿|修订版?|修改稿?|投稿稿))+$",
    re.I,
)


def _filename_family(file):
    name = posixpath.basename(_text(file).replace("\\", "/"))
    stem = os.path.splitext(name)[0].strip().lower()
    previous = None
    while previous != stem:
        previous = stem
        stem = _VERSION_TOKEN_RE.sub("", stem).strip(" _-.")
    stem = re.sub(r"[^\w\u4e00-\u9fff]+", " ", stem, flags=re.UNICODE)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or name.lower()


def _hit_family(hit):
    if not isinstance(hit, dict):
        return ""
    for key in ("version_family", "version_family_key", "family_key", "family", "family_id"):
        value = hit.get(key)
        if isinstance(value, dict):
            value = value.get("key") or value.get("family") or value.get("id") or value.get("name")
        if value not in (None, ""):
            return str(value)
    file = hit.get("file")
    if file:
        return "title:" + _filename_family(file)
    sha = hit.get("sha256_file") or hit.get("sha256")
    if sha:
        return "content:" + str(sha)
    chunk_id = hit.get("chunk_id")
    return "chunk:" + str(chunk_id) if chunk_id is not None else ""


def _is_key_evidence(hit):
    if not isinstance(hit, dict):
        return False
    return any(bool(hit.get(key)) for key in
               ("key_evidence", "is_key_evidence", "must_keep", "critical", "anchor_evidence"))


def diversify_results(hits, max_per_file=2, max_per_version_family=3):
    """Cap repetitive files/version families while protecting key evidence."""
    file_limit = max(0, _int(max_per_file, 2))
    family_limit = max(0, _int(max_per_version_family, 3))
    kept, dropped = [], []
    file_counts, family_counts = defaultdict(int), defaultdict(int)

    for hit in _list(hits, ("hits",)):
        if not isinstance(hit, dict):
            dropped.append({"hit": hit, "reason": "invalid_hit", "detail": "hit is not an object"})
            continue
        item = dict(hit)
        file_key = str(item.get("file") or item.get("chunk_id") or "")
        family = _hit_family(item)
        key = _is_key_evidence(item)

        if key and file_counts[file_key] >= file_limit:
            replace = next((old for old in kept
                            if str(old.get("file") or old.get("chunk_id") or "") == file_key
                            and not _is_key_evidence(old)), None)
            if replace is not None:
                kept.remove(replace)
                file_counts[file_key] -= 1
                dropped.append({
                    "hit": replace,
                    "reason": "displaced_by_key_evidence",
                    "detail": "a key-evidence hit replaced a lower-value hit in the same file",
                })
        if key and family_counts[family] >= family_limit:
            replace = next((old for old in kept
                            if _hit_family(old) == family and not _is_key_evidence(old)), None)
            if replace is not None:
                kept.remove(replace)
                family_counts[family] -= 1
                old_file = str(replace.get("file") or replace.get("chunk_id") or "")
                file_counts[old_file] -= 1
                dropped.append({
                    "hit": replace,
                    "reason": "displaced_by_key_evidence",
                    "detail": "a key-evidence hit replaced a lower-value hit in the same version family",
                })

        file_ok = file_counts[file_key] < file_limit
        family_ok = family_counts[family] < family_limit
        if not file_ok or not family_ok:
            reason = "max_per_file" if not file_ok else "max_per_version_family"
            dropped.append({
                "hit": item,
                "reason": reason,
                "detail": "%s limit reached" % reason,
                "file": item.get("file"),
                "chunk_id": item.get("chunk_id"),
                "version_family": family,
            })
            continue

        item["diversified_rank"] = len(kept) + 1
        kept.append(item)
        file_counts[file_key] += 1
        family_counts[family] += 1

    return {"hits": kept, "dropped": dropped}


# ---------------------------------------------------------------------------
# 5. Sentence-level claim evidence
# ---------------------------------------------------------------------------

_ABBREVIATION_RE = re.compile(r"\b(?:e\.g|i\.e|etc|vs|fig|no|dr|mr|ms|prof)\.$", re.I)


def _trim_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _iter_sentence_spans(text):
    """Yield exact, non-overlapping sentence spans, retaining punctuation."""
    text = _text(text)
    if not text:
        return
    boundaries = []
    for match in re.finditer(r"[。！？!?；;]+", text):
        boundaries.append((match.start(), match.end()))
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text):
        prefix = text[max(0, match.start() - 16):match.start() + 1]
        if _ABBREVIATION_RE.search(prefix):
            continue
        boundaries.append((match.start(), match.end()))
    for match in re.finditer(r"\n\s*\n+", text):
        boundaries.append((match.start(), match.end()))
    boundaries.sort(key=lambda item: (item[0], item[1]))

    start = 0
    for boundary_start, boundary_end in boundaries:
        if boundary_end <= start:
            continue
        raw_start, raw_end = _trim_span(text, start, boundary_start)
        if raw_end > raw_start:
            yield raw_start, raw_end
        start = max(start, boundary_end)
    raw_start, raw_end = _trim_span(text, start, len(text))
    if raw_end > raw_start:
        yield raw_start, raw_end


_CONCLUSION_TERMS = [
    "show", "shows", "demonstrate", "demonstrates", "found", "find", "results",
    "conclude", "concludes", "therefore", "significantly", "improve", "improves",
    "increase", "increases", "decrease", "decreases", "outperform", "outperforms",
    "suggest", "suggests", "indicate", "indicates", "support", "supports",
    "consistent", "inconsistent", "contrast", "conflict", "effective", "ineffective",
    "表明", "显示", "证明", "证实", "结果", "发现", "结论", "因此", "显著", "提高",
    "提升", "增加", "降低", "下降", "优于", "劣于", "支持", "不支持", "一致", "不一致",
]


def select_claim_evidence(hit, question, max_quotes=3):
    """Select complete sentences that contain query and conclusion signals."""
    limit = max(0, _int(max_quotes, 3))
    if limit == 0 or not isinstance(hit, dict):
        return []
    body = hit.get("text")
    question = _text(question)
    if not isinstance(body, str) or not body.strip() or not question.strip():
        return []

    features = _term_features(question, expand_aliases=True)
    if not features:
        return []
    exact_phrase = re.sub(r"\s+", " ", question.strip().lower())
    candidates = []
    for start, end in _iter_sentence_spans(body):
        sentence = body[start:end]
        low = sentence.lower()
        matched = [term for term in features if _contains_term(low, term)]
        if not matched:
            continue
        score = sum(features[term] for term in set(matched))
        if len(exact_phrase) >= 5 and exact_phrase in low:
            score += 3.0
        conclusion_hits = [term for term in _CONCLUSION_TERMS if _contains_term(low, term)]
        if conclusion_hits:
            score += 2.0 + min(1.5, len(set(conclusion_hits)) * 0.35)
        if re.search(r"\b[A-Z]{2,}\b|v\d+(?:\.\d+)?|10\.\d{4,9}/", sentence):
            score += 0.5
        if len(sentence) < 12:
            score -= 0.5
        score += min(0.75, len(sentence) / 400.0)
        reason_parts = ["query_terms=" + ",".join(sorted(set(matched))[:8])]
        if conclusion_hits:
            reason_parts.append("conclusion_terms=" + ",".join(sorted(set(conclusion_hits))[:6]))
        if len(exact_phrase) >= 5 and exact_phrase in low:
            reason_parts.append("exact_question_phrase")
        source_offset = None
        if isinstance(hit.get("start"), int):
            source_offset = [hit["start"] + start, hit["start"] + end]
        candidates.append({
            "offset": [start, end],
            "source_offset": source_offset,
            "file_offset": source_offset,
            "offset_space": "chunk_text",
            "text": sentence,
            "score": round(float(score), 6),
            "reason": "; ".join(reason_parts),
            "_start": start,
        })

    candidates.sort(key=lambda item: (-item["score"], item["_start"]))
    selected, seen = [], set()
    for item in candidates:
        key = re.sub(r"\s+", " ", item["text"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        item.pop("_start", None)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


# ---------------------------------------------------------------------------
# 6. Cross-file consensus and disagreement candidates
# ---------------------------------------------------------------------------

_POSITIVE_RE = re.compile(
    r"\b(?:improve[ds]?|increase[ds]?|higher|better|outperform(?:s|ed)?|effective|"
    r"support(?:s|ed)?|consistent|confirm(?:s|ed)?|enhance[ds]?|beneficial)\b|"
    r"提高|增加|提升|改善|优于|有效|显著|支持|一致|证实",
    re.I,
)
_NEGATIVE_RE = re.compile(
    r"\b(?:decrease[ds]?|lower|worse|ineffective|inconsistent|contradict(?:s|ed)?|"
    r"reject(?:s|ed)?|fail(?:s|ed)?|degrad(?:e|es|ed))\b|"
    r"降低|下降|劣于|无效|不支持|不一致|相反|未发现|无显著",
    re.I,
)
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|fail(?:s|ed)?\s+to|does\s+not|do\s+not|did\s+not|"
    r"cannot|can't|don't|didn't)\b|没有|并未|未能|不支持|不一致|无显著|未发现",
    re.I,
)
_RELATION_MARKER_RE = re.compile(
    r"\b(?:improve[ds]?|increase[ds]?|decrease[ds]?|higher|lower|better|worse|"
    r"outperform(?:s|ed)?|effective|ineffective|significant(?:ly)?|support(?:s|ed)?|"
    r"consistent|inconsistent|contradict(?:s|ed)?|confirm(?:s|ed)?|show(?:s|ed)?|"
    r"suggest(?:s|ed)?|indicate(?:s|d)?|associated|association|correlat(?:e|ed|ion)|"
    r"cause[ds]?|lead(?:s|ing)?\s+to|result(?:s|ed)?|differ(?:s|ent)?|compar(?:e|ed|ison))\b|"
    r"提高|增加|提升|改善|降低|下降|优于|劣于|有效|无效|显著|支持|一致|相反|"
    r"证实|表明|显示|提示|相关|关联|导致|结果|发现|差异|比较",
    re.I,
)
_COOCCURRENCE_RE = re.compile(r"\bco[-\s]?occurr?|共现|同时出现", re.I)


def _relation_label(sentence):
    low = _text(sentence)
    if not _RELATION_MARKER_RE.search(low):
        return None
    negated = bool(_NEGATION_RE.search(low))
    positive = bool(_POSITIVE_RE.search(low))
    negative = bool(_NEGATIVE_RE.search(low))
    if negated:
        return "negative"
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    if _COOCCURRENCE_RE.search(low):
        return None
    return None


def _question_topics(question):
    features = _term_features(question)
    topics = []
    for term, weight in sorted(features.items(), key=lambda item: (-item[1], -len(item[0]), item[0])):
        if _is_stopword(term) or term in _INTENT_WORDS or term in _RELATION_WORDS:
            continue
        if term.isdigit():
            continue
        canonical = _ALIAS_TO_CANONICAL.get(term, term)
        if any(canonical == old["canonical"] for old in topics):
            continue
        topics.append({"term": term, "canonical": canonical, "weight": weight})
    for canonical, present in _matched_synonym_groups(question):
        if any(canonical == old["canonical"] for old in topics):
            continue
        topics.append({"term": present[0], "canonical": canonical, "weight": 5.0})
    return topics


def _sha_for_hit(index, hit):
    for key in ("sha256_file", "sha256", "file_sha256"):
        if hit.get(key):
            return hit.get(key)
    files = index.get("files") if isinstance(index, dict) else None
    for file in _list(files):
        if isinstance(file, dict) and file.get("relpath") == hit.get("file"):
            return file.get("sha256")
    return None


def _independent_file_count(items, index=None):
    files = {str(item.get("file") or "") for item in items if item.get("file")}
    shas = {str(item.get("sha256")) for item in items if item.get("sha256")}
    if len(files) < 2:
        return len(files)
    if len(shas) == 1:
        return 1
    family_by_file = {}
    families = index.get("families") if isinstance(index, dict) else None
    if isinstance(families, dict):
        for family_key, family in families.items():
            if not isinstance(family, dict):
                continue
            for member in _list(family.get("members")):
                if isinstance(member, dict) and member.get("relpath"):
                    family_by_file[str(member["relpath"])] = str(family_key)
    mapped = [family_by_file[file] for file in files if file in family_by_file]
    unmapped = len(files) - len(mapped)
    if mapped and len(set(mapped)) == 1:
        return 1 + unmapped
    return len(set(mapped)) + unmapped


def cross_file_consensus(index, hits, question):
    """Return conservative cross-file agreement/conflict candidates.

    Topic co-occurrence alone is always reported as ``unknown``.  Even explicit
    relational statements remain candidates, never causal conclusions.
    """
    empty = {"agreements": [], "conflicts": [], "unknown": []}
    if not isinstance(index, dict):
        index = {}
    topics = _question_topics(_text(question))
    if not topics:
        return empty
    by_id, by_file = _chunk_map(index.get("chunks"))
    ordered = [hit for hit in _list(hits, ("hits",)) if isinstance(hit, dict)]
    ordered = [item for item in sorted(enumerate(ordered), key=lambda pair: _rank_value(pair[1], pair[0]))]

    clusters = defaultdict(list)
    unclustered = []
    for _index, hit in ordered:
        resolved = _resolve_hit(hit, by_id, by_file)
        if not resolved:
            continue
        resolved = _merge_hit_metadata(hit, resolved)
        body = resolved.get("text")
        if not isinstance(body, str) or not body:
            continue
        quotes = select_claim_evidence(resolved, question, max_quotes=1)
        if not quotes:
            continue
        quote = quotes[0]
        sentence = quote["text"]
        low = sentence.lower()
        topic_matches = []
        for topic in topics:
            aliases = _aliases_for(topic["canonical"])
            if any(_contains_term(low, alias) for alias in aliases):
                topic_matches.append(topic)
        if not topic_matches:
            unclustered.append((resolved, quote, None))
            continue
        topic = max(topic_matches, key=lambda item: (item["weight"], len(item["canonical"])))
        clusters[topic["canonical"]].append((resolved, quote, topic))

    agreements, conflicts, unknown = [], [], []

    def evidence_item(resolved, quote, topic, relation, reason, cluster_id, files_in_cluster):
        start, end = quote["offset"]
        item = {
            "cluster_id": cluster_id,
            "topic": topic,
            "file": resolved.get("file"),
            "sha256": _sha_for_hit(index, resolved),
            "sha256_file": _sha_for_hit(index, resolved),
            "sha": _sha_for_hit(index, resolved),
            "chunk_id": resolved.get("chunk_id"),
            "quote": quote["text"],
            "offset": [start, end],
            "source_offset": quote.get("source_offset"),
            "file_offset": quote.get("file_offset", quote.get("source_offset")),
            "offset_space": "chunk_text",
            "relation": relation,
            "reason": reason,
            "confidence": "candidate",
            "causal_claim": False,
            "files_in_cluster": files_in_cluster,
        }
        return item

    for number, (topic, rows) in enumerate(clusters.items(), 1):
        cluster_id = "topic-%02d" % number
        files_in_cluster = sorted({str(row[0].get("file")) for row in rows if row[0].get("file")})
        independent = _independent_file_count([
            {"file": row[0].get("file"), "sha256": _sha_for_hit(index, row[0])} for row in rows
        ], index)
        relation_groups = defaultdict(list)
        for resolved, quote, topic_row in rows:
            relation = _relation_label(quote["text"])
            relation_groups[relation].append((resolved, quote, topic_row))

        non_neutral = {key: value for key, value in relation_groups.items() if key not in (None, "mixed")}
        if independent < 2:
            reason = "single_independent_file_or_duplicate_content"
            for resolved, quote, topic_row in rows:
                unknown.append(evidence_item(resolved, quote, topic, _relation_label(quote["text"]),
                                             reason, cluster_id, files_in_cluster))
            continue

        has_conflict = "positive" in non_neutral and "negative" in non_neutral
        if has_conflict:
            # A uniquely largest repeated stance can still be an agreement
            # subgroup.  Equal-sized opposing groups are conflicts throughout.
            largest_size = max(len(group) for group in non_neutral.values())
            largest_relations = [key for key, value in non_neutral.items()
                                 if len(value) == largest_size]
            agreement_relation = (largest_relations[0]
                                  if len(largest_relations) == 1 and largest_size >= 2
                                  else None)
            if agreement_relation is not None:
                for resolved, quote, topic_row in non_neutral[agreement_relation]:
                    agreements.append(evidence_item(
                        resolved, quote, topic, agreement_relation,
                        "multiple independent files report the same stance",
                        cluster_id, files_in_cluster,
                    ))
            for relation, group in non_neutral.items():
                if relation == agreement_relation:
                    continue
                opposing = sorted({str(row[0].get("file")) for key, rows2 in non_neutral.items()
                                   if key != relation for row in rows2 if row[0].get("file")})
                for resolved, quote, topic_row in group:
                    item = evidence_item(
                        resolved, quote, topic, relation,
                        "independent files report opposing stances",
                        cluster_id, files_in_cluster,
                    )
                    item["opposing_files"] = opposing
                    conflicts.append(item)
            for relation in (None, "mixed"):
                for resolved, quote, topic_row in relation_groups.get(relation, []):
                    unknown.append(evidence_item(
                        resolved, quote, topic, relation,
                        "ambiguous_or_cooccurrence_only_relation",
                        cluster_id, files_in_cluster,
                    ))
            continue

        if len(non_neutral) == 1:
            relation, group = next(iter(non_neutral.items()))
            if len({str(row[0].get("file")) for row in group}) >= 2:
                for resolved, quote, topic_row in group:
                    agreements.append(evidence_item(
                        resolved, quote, topic, relation,
                        "multiple independent files report the same stance",
                        cluster_id, files_in_cluster,
                    ))
                for resolved, quote, topic_row in relation_groups.get(None, []):
                    unknown.append(evidence_item(
                        resolved, quote, topic, None,
                        "ambiguous_or_cooccurrence_only_relation",
                        cluster_id, files_in_cluster,
                    ))
                continue

        for resolved, quote, topic_row in rows:
            unknown.append(evidence_item(
                resolved, quote, topic, _relation_label(quote["text"]),
                "insufficient_cross_file_relation_evidence",
                cluster_id, files_in_cluster,
            ))

    for resolved, quote, topic_row in unclustered:
        unknown.append(evidence_item(
            resolved, quote, topic_row["canonical"] if topic_row else "unresolved",
            _relation_label(quote["text"]),
            "no_shared_query_topic",
            "topic-unclustered", [str(resolved.get("file"))] if resolved.get("file") else [],
        ))

    return {"agreements": agreements, "conflicts": conflicts, "unknown": unknown}


# ---------------------------------------------------------------------------
# 7. Rubric-mapped search report
# ---------------------------------------------------------------------------

def _hashable(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_hashable(item) for item in value)
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _unique_hits(hits):
    out, seen = [], set()
    for hit in _list(hits, ("hits",)):
        if not isinstance(hit, dict):
            continue
        key = tuple(_hashable(hit.get(field))
                    for field in ("file", "chunk_id", "start", "offset"))
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _audit_rows(filter_audit):
    rows = _list(filter_audit, ("filter_audit", "audit", "rows"))
    return [row for row in rows if isinstance(row, dict)]


def _filter_dimensions(case, audit):
    dimensions = set()
    try:
        filters, _constraints = _case_filters(case)
        dimensions.update(filters)
    except Exception:
        pass
    for row in audit:
        constraint = _text(row.get("constraint"))
        if not constraint:
            continue
        if "=" in constraint:
            dimensions.add(constraint.split("=", 1)[0].strip())
        elif constraint.startswith("unsupported"):
            dimensions.add("unsupported")
        elif constraint == "final_keep":
            dimensions.add("final_keep")
    return sorted(dimensions)


def _ratio(numerator, denominator):
    denominator = max(0, _int(denominator, 0))
    if not denominator:
        return 0.0
    return max(0.0, min(1.0, _number(numerator) / denominator))


def _hit_in_index(hit, chunks):
    """Return true only when a hit resolves to a chunk present in the index."""
    if not isinstance(hit, dict) or not chunks:
        return False
    file = hit.get("file")
    candidates = [item for item in chunks
                  if isinstance(item, dict) and item.get("file") == file]
    if not candidates:
        return False
    chunk_id = hit.get("chunk_id")
    if chunk_id is not None:
        return any(str(item.get("chunk_id")) == str(chunk_id) for item in candidates)
    start = hit.get("start")
    offset = hit.get("offset")
    if start is None and isinstance(offset, dict):
        start = offset.get("start")
    if start is not None:
        return any(item.get("start") == start for item in candidates)
    text = hit.get("text")
    if isinstance(text, str):
        return any(item.get("text") == text for item in candidates)
    return False


def _hit_identity(hit):
    discriminator = hit.get("chunk_id")
    if discriminator is None:
        discriminator = hit.get("start")
    if discriminator is None:
        discriminator = hit.get("offset")
    if discriminator is None:
        discriminator = hit.get("text")
    return str(hit.get("file") or ""), str(discriminator or "")


def build_search_report(index, case, question, filter_audit, plans, hits, refs, consensus):
    """Build an evidence-based rubric/coverage/efficiency summary."""
    index = index if isinstance(index, dict) else {}
    case = case if isinstance(case, dict) else {}
    consensus = consensus if isinstance(consensus, dict) else {}
    files = [item for item in _list(index.get("files")) if isinstance(item, dict)]
    chunks = [item for item in _list(index.get("chunks")) if isinstance(item, dict)]
    hit_rows = _unique_hits(hits)
    ref_rows = [item for item in _list(refs, ("evidence_refs", "refs", "references")) if isinstance(item, dict)]
    plan_rows = [item for item in _list(plans, ("plans", "query_plans")) if isinstance(item, (dict, str))]
    audit = _audit_rows(filter_audit)

    summary = index.get("summary") if isinstance(index.get("summary"), dict) else {}
    total_files = len(files) or _int(summary.get("files_total"), 0)
    total_chunks = len(chunks) or _int(summary.get("chunks_total"), 0)
    known_files = {str(file.get("relpath")) for file in files if file.get("relpath")}
    hit_files = {str(hit.get("file")) for hit in hit_rows
                 if hit.get("file") and str(hit.get("file")) in known_files}
    ref_files = {str(ref.get("file")) for ref in ref_rows
                 if ref.get("file") and str(ref.get("file")) in known_files}
    verified_section_hits = [hit for hit in hit_rows if _hit_in_index(hit, chunks)]
    hit_chunk_ids = {_hit_identity(hit) for hit in verified_section_hits}
    section_kinds = sorted({str(hit.get("kind")) for hit in verified_section_hits
                            if hit.get("kind")})
    page_aware_hits = sum(1 for hit in verified_section_hits if hit.get("pages"))
    locator_hits = sum(1 for hit in verified_section_hits
                       if hit.get("kind") in {"section", "heading"} or hit.get("section")
                       or hit.get("chapter") or hit.get("anchor"))

    metadata_files = [file for file in files if isinstance(file.get("meta"), dict) and file["meta"]]
    meta_fields = defaultdict(int)
    for file in metadata_files:
        for key in file["meta"]:
            meta_fields[str(key)] += 1
    families = index.get("families") if isinstance(index.get("families"), dict) else {}
    aliases = _list(index.get("aliases"))
    dimensions = _filter_dimensions(case, audit)
    filter_eliminated = sum(max(0, _int(row.get("dropped_since_prev", row.get("dropped", 0)), 0))
                            for row in audit)
    valid_hit_rows = [item for item in _list(hits, ("hits",)) if isinstance(item, dict)]
    duplicate_hits = max(0, len(valid_hit_rows) - len(hit_rows))

    verification_flags = (
        "sha256_verified", "chunk_hash_verified", "quote_verified",
        "source_slice_verified", "artifact_hash_verified",
    )
    fully_verified = sum(1 for ref in ref_rows if all(ref.get(flag) is True for flag in verification_flags))
    any_verified = sum(1 for ref in ref_rows if any(ref.get(flag) is True for flag in verification_flags))
    consensus_counts = {key: len(_list(consensus.get(key))) for key in ("agreements", "conflicts", "unknown")}

    file_coverage = _ratio(len(hit_files), total_files)
    section_coverage = _ratio(len(hit_chunk_ids), total_chunks)
    metadata_coverage = _ratio(len(metadata_files), total_files)
    filter_coverage = _ratio(len([row for row in audit if row.get("constraint")]), max(1, len(dimensions)))

    actual_points = round(40 * (0.65 * file_coverage + 0.35 * section_coverage), 2)
    plan_kinds = sorted({str(plan.get("kind")) for plan in plan_rows if isinstance(plan, dict) and plan.get("kind")})
    usage_signal = min(1.0, 0.55 * _ratio(len(plan_rows), 6) + 0.45 * _ratio(len(plan_kinds), 5))
    usage_points = round(25 * usage_signal, 2)
    consensus_signal = min(1.0, _ratio(sum(consensus_counts.values()), 4))
    innovation_signal = min(1.0, 0.55 * _ratio(len(plan_kinds), 5) + 0.45 * consensus_signal)
    innovation_points = round(15 * innovation_signal, 2)
    reliability_signal = 0.0
    if ref_rows:
        reliability_signal = 0.75 * _ratio(fully_verified, len(ref_rows)) + 0.25 * _ratio(any_verified, len(ref_rows))
    reliability_points = round(10 * reliability_signal, 2)
    ecosystem_signal = 0.0
    ecosystem_parts = [
        bool(families), bool(aliases), bool(metadata_files), bool(dimensions),
        bool(case.get("output_contract") or case.get("result_schema") or case.get("output_schema")),
    ]
    ecosystem_signal = sum(1 for value in ecosystem_parts if value) / float(len(ecosystem_parts))
    ecosystem_points = round(10 * ecosystem_signal, 2)

    return {
        "case_id": case.get("case_id"),
        "question": _text(question),
        "rubric_mapped": {
            "actual_effect": {
                "weight": 40, "max_points": 40, "points": actual_points,
                "evidence": ["file_coverage=%.4f" % file_coverage,
                             "section_coverage=%.4f" % section_coverage],
            },
            "usage_gain": {
                "weight": 25, "max_points": 25, "points": usage_points,
                "evidence": ["query_plans=%d" % len(plan_rows),
                             "plan_kinds=" + ",".join(plan_kinds)],
            },
            "innovation": {
                "weight": 15, "max_points": 15, "points": innovation_points,
                "evidence": ["plan_kind_diversity=%d" % len(plan_kinds),
                             "consensus_candidates=%d" % sum(consensus_counts.values())],
            },
            "reliability": {
                "weight": 10, "max_points": 10, "points": reliability_points,
                "evidence": ["fully_verified_refs=%d/%d" % (fully_verified, len(ref_rows)),
                             "refs_with_any_verification=%d/%d" % (any_verified, len(ref_rows))],
            },
            "ecosystem": {
                "weight": 10, "max_points": 10, "points": ecosystem_points,
                "evidence": ["version_families=%d" % len(families),
                             "aliases=%d" % len(aliases),
                             "metadata_files=%d" % len(metadata_files),
                             "filter_dimensions=%d" % len(dimensions),
                             "output_contract=%s" % bool(case.get("output_contract") or case.get("result_schema") or case.get("output_schema"))],
            },
        },
        "coverage": {
            "files": {
                "total": total_files,
                "hit_files": len(hit_files),
                "referenced_files": len(ref_files),
                "ratio": round(file_coverage, 6),
            },
            "sections": {
                "total_chunks": total_chunks,
                "hit_chunks": len(hit_chunk_ids),
                "kinds": section_kinds,
                "page_aware_hits": page_aware_hits,
                "locator_hits": locator_hits,
                "ratio": round(section_coverage, 6),
            },
            "metadata": {
                "total_files": total_files,
                "files_with_metadata": len(metadata_files),
                "metadata_fields": dict(sorted(meta_fields.items())),
                "version_families": len(families),
                "aliases": len(aliases),
                "ratio": round(metadata_coverage, 6),
            },
            "filters": {
                "dimensions": dimensions,
                "audit_rows": len(audit),
                "executed_constraints": len([row for row in audit if row.get("constraint")]),
                "eliminated_events": filter_eliminated,
                "ratio": round(filter_coverage, 6),
            },
        },
        "efficiency_evidence": {
            "query_plan_count": len(plan_rows),
            "filter_eliminated_count": filter_eliminated,
            "deduplicated_count": duplicate_hits,
            "citation_review_count": len(ref_rows),
            "citation_fully_verified_count": fully_verified,
            "index_exact_duplicate_groups": len(index.get("dedup_groups") or {}),
            "consensus_counts": consensus_counts,
        },
    }
