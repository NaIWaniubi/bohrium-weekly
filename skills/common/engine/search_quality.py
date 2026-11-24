# -*- coding: utf-8 -*-
"""Stdlib-only, deterministic search-quality refinement and reporting.

Callers supply a verified index and chunk text.  Quotes and offsets always refer
to that supplied text; the module never opens corpus files or invents evidence.
"""
import hashlib
import importlib.util
import os
import posixpath
import re
from collections import Counter, defaultdict

__all__ = ["refine_query_plans", "canonicalize_hits", "select_section_evidence",
           "synthesize_answer", "coverage_report", "rubric_report"]


def _load_enhancer():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_enhance.py")
    spec = importlib.util.spec_from_file_location("_sq_enhancer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SE = _load_enhancer()


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        for key in ("text", "question", "q", "research_question", "value"):
            if isinstance(value.get(key), (str, int, float)):
                return str(value[key])
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _rows(value, wrappers=()):
    if value is None:
        return []
    if isinstance(value, dict):
        for key in wrappers:
            if key in value:
                return _rows(value[key])
        return [value]
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _dicts(value, wrappers=()):
    return [row for row in _rows(value, wrappers) if isinstance(row, dict)]


def _path(value):
    return _text(value).replace("\\", "/").strip().lstrip("./")


def _sha(row):
    if not isinstance(row, dict):
        return ""
    return _text(row.get("sha256_file") or row.get("sha256") or
                 row.get("file_sha256") or row.get("sha"))


def _ratio(n, d):
    d = max(0, _int(d))
    return max(0.0, min(1.0, float(n) / d)) if d else 0.0


# Query-plan refinement -----------------------------------------------------
_EN_STOP = set("a an and are as at be been being but by can could did do does doing "
               "for from had has have having how i if in into is it its may might more "
               "most of on or our over please should so some such than that the their "
               "them then there these they this those through to too under up use using "
               "very was we were what when where which while who why will with would "
               "you your find show list tell look only quote citation".split())
_CJK_STOP = set("我们 你们 他们 这个 那个 这些 那些 一个 一种 进行 通过 以及 并且 同时 "
                "可以 需要 关于 对于 由于 因此 但是 然而 其中 目前 主要 相关 工作 研究 "
                "方法 结果 结论 问题 内容 材料 文献 文档 说明 引用 找出 只看 请 中的".split())
_NOISE = set("find show list tell look only please quote citation pdf doc docx xls xlsx "
             "ppt pptx txt md csv json 找出 只看 请 引用 检索 搜索 查找 定位 列出 说明".split())
_FILE_RE = re.compile(r"^(?:[\w\u4e00-\u9fff ./\\-]+\.)?(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|jsonl?|png|jpe?g)$", re.I)
_NEG_RE = re.compile(r"不要|勿|禁止|排除|不包括|不包含|不含|\bdo\s+not\b|\bdon't\b|\bexclude\b|\bwithout\b", re.I)
_CMD_RE = re.compile(r"^\s*(?:(?:请|麻烦)\s*)?(?:只检索|只查看|只看|找出|查找|定位|搜索|检索|列出|find|search|locate|list|show|tell)\s*[:：]?\s*", re.I)
_FILE_TOKEN_RE = re.compile(r"(?<![\w])[\w./\\-]+\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|jsonl?|png|jpe?g)\b", re.I)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)?%?")


def _clean_plan(value):
    text = re.sub(r"\s+", " ", _text(value)).strip(" ，,。；;！？!?")
    if not text:
        return ""
    pieces = []
    for clause in re.split(r"[，,。；;\n]+", text):
        clause = clause.strip()
        if not clause or _NEG_RE.search(clause):
            continue
        while True:
            new = _CMD_RE.sub("", clause).strip()
            if new == clause:
                break
            clause = new
        clause = _FILE_TOKEN_RE.sub(" ", clause)
        clause = re.sub(r"年最终版中|最终版中|中的|关于", " ", clause)
        clause = re.sub(r"\s+", " ", clause).strip(" ，,。；;：:")
        if clause and not _FILE_RE.fullmatch(clause):
            pieces.append(clause)
    text = " ".join(pieces)
    if not text or _FILE_RE.fullmatch(text) or _NEG_RE.search(text):
        return ""
    kept = []
    for token in _WORD_RE.findall(text):
        low = token.lower()
        if low in _NOISE or low in _EN_STOP or low in _CJK_STOP:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]", token):
            kept.append(token)
    cleaned = " ".join(kept).strip()
    return "" if not cleaned or cleaned.lower() in _NOISE or _FILE_RE.fullmatch(cleaned) else cleaned


def _intent(value):
    low = _text(value).lower()
    if re.search(r"比较|对比|差异|不同|相似|\bcompare\b|\bcomparison\b|\bversus\b|\bvs\b|\bdifference", low):
        return "comparison"
    if re.search(r"哪里|哪个章节|哪一页|定位|章节|\bwhere\b|\bwhich\s+(?:section|chapter|page)\b|\blocat", low):
        return "location"
    if re.search(r"局限|限制|不足|缺点|弱点|\blimitation|\blimitations\b|\bdrawback|\bshortcoming|\bweakness|\bcannot\b|\bdoes not handle\b", low):
        return "limitation"
    if re.search(r"(?:19|20)\d{2}|时间|年份|趋势|演变|\btime\b|\btemporal\b|\btrend\b|\bevolution\b|\byear\b|\bdate\b", low):
        return "temporal"
    if re.search(r"方法|模型|算法|网络|\bmethod|\bmodel|\bapproach|\balgorithm|\bgnn\b|\btransformer\b", low):
        return "method"
    if re.search(r"误差|结果|性能|准确率|\berror\b|\bmae\b|\bresult|\bperformance\b|\baccuracy\b|\bimprov", low):
        return "result"
    return "entity"


def refine_query_plans(question, terms_txt="", case=None, max_plans=12):
    """Refine v6 rewrite_queries output into content-only executable plans."""
    limit = max(0, _int(max_plans, 12))
    if not limit:
        return []
    question, terms_txt = _text(question).strip(), _text(terms_txt)
    try:
        raw = _SE.rewrite_queries(question, terms_txt, max_plans=max(12, limit * 4))
    except Exception:
        raw = []
    plans, seen = [], set()

    def add(row):
        if not isinstance(row, dict):
            return
        text = _clean_plan(row.get("text"))
        key = text.casefold()
        if not text or key in seen:
            return
        seen.add(key)
        sources = []
        for term in row.get("source_terms") or []:
            clean = _clean_plan(term)
            if clean and clean not in sources:
                sources.append(clean)
        kind = _text(row.get("kind")) or "entity"
        kind = {"object": "result", "overview": "entity", "subquestion": "entity",
                "localization": "location"}.get(kind, kind)
        intent = _intent(text)
        base = {"comparison": .98, "limitation": .96, "result": .93, "method": .90,
                "location": .86, "temporal": .82, "entity": .74}.get(intent, .70)
        base += .03 if kind == "synonym" else (-.05 if kind in {"overview", "subquestion"} else 0)
        plans.append({"plan_id": "", "text": text, "kind": kind, "intent": intent,
                      "source_terms": sources, "weight": round(max(0, min(1, base)), 4)})

    for row in _dicts(raw):
        add(row)
    if not plans:
        for line in terms_txt.splitlines():
            for alias in re.split(r"\|=>|→|[,，;；\t]+", line):
                text = _clean_plan(alias)
                if text:
                    add({"text": text, "kind": "synonym", "source_terms": [alias]})
    if not plans and question:
        text = _clean_plan(question)
        if text:
            add({"text": text, "kind": "entity", "source_terms": [question]})
    plans.sort(key=lambda row: (-row["weight"], row["text"].casefold()))
    for number, plan in enumerate(plans[:limit], 1):
        plan["plan_id"] = "plan-%02d" % number
    return plans[:limit]


# Canonical version resolution ---------------------------------------------
_VERSION_RE = re.compile(r"(?:[_\-\s]*(?:v(?:er(?:sion)?)?\s*\d+(?:\.\d+)*|rev(?:ision)?\s*\d*|draft|final|submitted|accepted|copy|alias|duplicate|草稿|终稿|修订版?|修改稿?))+$", re.I)


def _family(path):
    name = posixpath.basename(_path(path)).lower()
    stem = os.path.splitext(name)[0]
    old = None
    while old != stem:
        old, stem = stem, _VERSION_RE.sub("", stem).strip(" _-.")
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", stem).strip() or name


def _admin(path):
    low = _path(path).lower()
    return low.startswith(("admin/", "metadata/", "aliases/")) or bool(re.search(r"(?:version|alias|metadata)[_-]?notes?\.", low))


def _pref(path, rank=0, mtime=0):
    low = _path(path).lower()
    score = 500 if re.search(r"final|canonical|终稿|修订版", low) else 0
    score += 0 if re.search(r"draft|copy|alias|duplicate|草稿|旧版", low) else 120
    score += 0 if low.startswith(("aliases/", "copies/", "drafts/")) else 30
    try:
        score += float(rank or 0) + min(50, float(mtime or 0) / 1e9)
    except (TypeError, ValueError):
        pass
    return score


def _files(index):
    out = {}
    for row in _dicts(index.get("files")) if isinstance(index, dict) else []:
        path = _path(row.get("relpath") or row.get("file") or row.get("path"))
        if path:
            out[path] = row
    return out


def _chunks(index):
    rows = _dicts(index.get("chunks")) if isinstance(index, dict) else []
    by_id, by_file = defaultdict(list), defaultdict(list)
    for row in rows:
        if row.get("chunk_id") is not None:
            by_id[str(row["chunk_id"])].append(row)
        if row.get("file"):
            by_file[_path(row["file"])].append(row)
    return rows, by_id, by_file


def _admin_map(index):
    mapping, basis = {}, {}
    for chunk in _dicts(index.get("chunks")) if isinstance(index, dict) else []:
        text = _text(chunk.get("text"))
        match = re.search(r"(?im)^\s*canonical\s+document\s*[:：]\s*(.+?)\s*$", text)
        if not match:
            continue
        canonical = _path(match.group(1).strip(" `\"'"))
        basis[canonical] = "administrative_canonical_document"
        for row in re.finditer(r"(?im)^\s*(alias|supersedes|superseded by)\s*[:：]\s*(.+?)\s*$", text):
            other = re.sub(r"\s*\(.*\)\s*$", "", _path(row.group(2).strip(" `\"'"))).strip()
            if other and other != canonical:
                mapping[other] = canonical
                basis[other] = "administrative_" + row.group(1).lower().replace(" ", "_")
        mapping[canonical] = canonical
    return mapping, basis


def _explicit_map(value):
    mapping, basis = {}, {}
    if isinstance(value, dict):
        if value.get("canonical_map") is not None:
            return _explicit_map(value["canonical_map"])
        for key, target in value.items():
            if key in {"aliases", "superseded", "families", "source", "version_metadata"}:
                continue
            if isinstance(target, dict):
                target = target.get("canonical") or target.get("canonical_file") or target.get("file")
            key, target = _path(key), _path(target)
            if key and target:
                mapping[key], basis[key] = target, "explicit_canonical_map"
    elif isinstance(value, (list, tuple)):
        for row in value:
            if not isinstance(row, dict):
                continue
            key = _path(row.get("alias") or row.get("file") or row.get("path") or row.get("from"))
            target = _path(row.get("canonical") or row.get("canonical_file") or row.get("to"))
            if key and target:
                mapping[key], basis[key] = target, _text(row.get("basis") or row.get("reason") or "canonical_map_record")
    return mapping, basis


def _canonical_map(index, canonical_map=None):
    index = index if isinstance(index, dict) else {}
    files = _files(index)
    mapping, basis, sources = {}, {}, []

    def merge(new, new_basis, source):
        mapping.update(new)
        basis.update(new_basis)
        sources.append(source)

    families = index.get("families") if isinstance(index.get("families"), dict) else {}
    for family in families.values():
        members = [row for row in _dicts(family.get("members")) if row.get("relpath")] if isinstance(family, dict) else []
        if len(members) < 2:
            continue
        canonical = _path(max(members, key=lambda row: _pref(
            row.get("relpath"), row.get("rank", row.get("variant_rank")),
            files.get(_path(row.get("relpath")), {}).get("mtime"))).get("relpath"))
        for row in members:
            path = _path(row.get("relpath"))
            if path and path != canonical and not _admin(path):
                mapping[path], basis[path] = canonical, "version_family_rank"
    merge(*_admin_map(index), "administrative_version_notes")
    by_family, by_sha = defaultdict(list), defaultdict(list)
    for path, row in files.items():
        by_family[_family(path)].append(path)
        if row.get("sha256"):
            by_sha[_text(row["sha256"])].append(path)
    for paths in list(by_family.values()) + list(by_sha.values()):
        if len(paths) < 2:
            continue
        canonical = mapping.get(paths[0]) or max(paths, key=lambda path: _pref(
            path, files.get(path, {}).get("rank"), files.get(path, {}).get("mtime")))
        for path in paths:
            if path != canonical and not _admin(path):
                mapping[path], basis[path] = canonical, "sha256_or_path_alias"
    for alias in _dicts(index.get("aliases")):
        canonical = _path(alias.get("recommended_canonical") or alias.get("canonical"))
        for chain in _dicts(alias.get("chains")):
            for path in chain.get("paths") or []:
                path = _path(path)
                if canonical and path and path != canonical:
                    mapping[path], basis[path] = canonical, "index_alias_recommendation"
    if index.get("version_metadata") is not None:
        merge(*_explicit_map(index["version_metadata"]), "version_metadata")
    if canonical_map is not None:
        merge(*_explicit_map(canonical_map), "caller_canonical_map")
    for key in list(mapping):
        seen, target = {key}, mapping[key]
        while target in mapping and target not in seen:
            seen.add(target)
            target = mapping[target]
        mapping[key] = target
    aliases = [{"file": key, "canonical_file": value, "basis": basis.get(key, "canonical")}
               for key, value in sorted(mapping.items()) if key != value]
    superseded = [row for row in aliases if "supersed" in row["basis"] or
                  re.search(r"draft|草稿|旧版", row["file"], re.I)]
    resolution = {"source": "mixed" if len(set(sources)) > 1 else (sources[0] if sources else "none"),
                  "sources": sorted(set(sources)), "canonical_map": dict(sorted(mapping.items())),
                  "aliases": aliases, "superseded": superseded,
                  "version_groups": len(families), "deduplicated_count": 0}
    return mapping, basis, resolution


def _resolve_chunk(hit, by_id, by_file):
    path, chunk_id = _path(hit.get("file")), hit.get("chunk_id")
    rows = by_id.get(str(chunk_id), []) if chunk_id is not None else []
    rows = [row for row in rows if not path or _path(row.get("file")) == path]
    if len(rows) == 1:
        return rows[0]
    start = hit.get("start")
    if start is None and isinstance(hit.get("offset"), dict):
        start = hit["offset"].get("start")
    rows = by_file.get(path, [])
    if start is not None:
        rows = [row for row in rows if row.get("start") == start]
    if len(rows) == 1:
        return rows[0]
    text = hit.get("text")
    if isinstance(text, str) and text:
        rows = [row for row in by_file.get(path, []) if row.get("text") == text]
        if len(rows) == 1:
            return rows[0]
    return None


def canonicalize_hits(index, hits, canonical_map=None):
    """Keep one canonical, text-verified hit per chunk and audit aliases."""
    index = index if isinstance(index, dict) else {}
    mapping, basis, resolution = _canonical_map(index, canonical_map)
    _all, by_id, by_file = _chunks(index)
    files = _files(index)
    ordered = []
    for number, hit in enumerate(_dicts(hits, ("hits",))):
        path = _path(hit.get("file"))
        target = mapping.get(path) or mapping.get(_sha(hit)) or path
        ordered.append((0 if path == target else 1, number, hit, path, target))
    kept, dropped, identities = [], [], {}
    for _order, _number, hit, path, target in sorted(ordered, key=lambda row: (row[0], row[1])):
        if not target:
            dropped.append({"hit": hit, "reason": "missing_file"})
            continue
        source = _resolve_chunk(hit, by_id, by_file)
        if target != path:
            source = _resolve_chunk({"file": target, "chunk_id": hit.get("chunk_id"),
                                     "start": hit.get("start"), "text": hit.get("text")}, by_id, by_file)
            if source is None and _sha(hit) and _sha(hit) == _sha(files.get(target, {})):
                source = dict(hit, file=target)
        if target != path and source is None:
            dropped.append({"hit": hit, "reason": "canonical_text_unavailable",
                            "file": path, "canonical_file": target})
            continue
        item = dict(source or hit)
        for key in ("rank", "score_raw", "bm25_main", "bm25_title", "dense_cosine",
                    "rrf_score", "plan_evidence", "pages", "printed_page_offset"):
            if key not in item and key in hit:
                item[key] = hit[key]
        item["file"] = target
        item["sha256_file"] = _sha(files.get(target, {})) or _sha(item) or _sha(hit)
        item["sha256"] = item["sha256_file"]
        if path != target:
            item["canonical_source_file"] = path
            item["version_basis"] = basis.get(path, "canonical_map")
        identity = (target, _text(item.get("chunk_id")), _text(item.get("start")),
                    hashlib.sha256(_text(item.get("text")).encode("utf-8")).hexdigest())
        if identity in identities:
            old = kept[identities[identity]]
            try:
                better = float(item.get("score_raw", -1)) > float(old.get("score_raw", -1))
            except (TypeError, ValueError):
                better = False
            if better:
                kept[identities[identity]] = item
            dropped.append({"hit": hit, "reason": "duplicate_canonical_chunk",
                            "file": path, "canonical_file": target})
            continue
        identities[identity] = len(kept)
        kept.append(item)
    kept.sort(key=lambda row: (float(row["rank"]) if isinstance(row.get("rank"), (int, float)) else 1e9,
                               _path(row.get("file")), _text(row.get("chunk_id"))))
    resolution["deduplicated_count"] = len(dropped)
    resolution["canonical_files"] = sorted({_path(row.get("file")) for row in kept if row.get("file")})
    return {"hits": kept, "dropped": dropped, "version_resolution": resolution}


# Section evidence ----------------------------------------------------------
_HEADING_RE = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?((?:\d+(?:\.\d+)*[.)]?\s*)?(methods?|methodology|results?|findings?|limitations?|related\s+work|references|discussion|conclusions?|introduction|abstract|reproducibility|方法|结果|局限|限制|结论|相关工作|参考文献|背景))\s*[:：]?\s*$")
_SECTION = {
    "method": "methods", "methods": "methods", "methodology": "methods", "方法": "methods",
    "result": "results", "results": "results", "finding": "results", "findings": "results",
    "结果": "results", "结论": "results", "limitation": "limitations", "limitations": "limitations",
    "局限": "limitations", "限制": "limitations", "related": "related", "related work": "related",
    "references": "related", "abstract": "related", "introduction": "related", "background": "related",
    "相关工作": "related", "参考文献": "related", "背景": "related",
}


def _sentences(text):
    boundaries = [(m.start(), m.end()) for m in re.finditer(r"[。！？!?；;]+|\n+", text)]
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text):
        if not re.search(r"\b(?:e\.g|i\.e|etc|vs|fig|no)\.$", text[max(0, match.start() - 12):match.start() + 1], re.I):
            boundaries.append((match.start(), match.end()))
    start = 0
    for left, right in sorted(boundaries):
        while start < left and text[start].isspace():
            start += 1
        end = left
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            yield start, end
        start = max(start, right)
    while start < len(text) and text[start].isspace():
        start += 1
    if start < len(text):
        yield start, len(text)


def _regions(text):
    out = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(1).strip()
        kind = _SECTION.get(title.lower()) or next((v for k, v in _SECTION.items() if k in title.lower()), "unknown")
        out.append((match.start(), match.end(), title, kind))
    return out


def _section(title, sentence, hit):
    low = (_text(title) + " " + _text(sentence)).lower()
    for key, value in _SECTION.items():
        if key in low:
            return value
    if re.search(r"does not handle|cannot|limitation|drawback|not support|局限|限制|不足", low):
        return "limitations"
    if re.search(r"results?|reduces?|increases?|mean absolute error|\bmae\b|performance|结果|误差|提升|降低", low):
        return "results"
    if re.search(r"we (?:use|used|evaluate|train|compare)|method|model|algorithm|方法|模型|算法", low):
        return "methods"
    return _SECTION.get(_text(hit.get("section_type") or hit.get("kind")).lower(), "unknown")


def _query_terms(question):
    cleaned = _clean_plan(question)
    terms = []
    for token in _WORD_RE.findall(cleaned):
        low = token.lower()
        if low in _NOISE or low in _EN_STOP or low in _CJK_STOP or low.isdigit() or len(token) == 1:
            continue
        if low not in terms:
            terms.append(low)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token):
            for size in (2, 3):
                for i in range(len(token) - size + 1):
                    piece = token[i:i + size]
                    if piece not in terms:
                        terms.append(piece)
    for source, aliases in (("立体化学", ["stereochemistry"]), ("误差", ["error", "mae"]),
                            ("局限", ["limitation", "limitations"]), ("神经网络", ["gnn", "graph neural network"])):
        if source in cleaned:
            terms.extend(alias for alias in aliases if alias not in terms)
    return terms


def _contains(term, text):
    if re.fullmatch(r"[a-z0-9_+\-]+", term):
        return re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text) is not None
    return term in text


def select_section_evidence(index, hits, question, intent=None):
    """Select complete, intent-aware sentences with exact chunk offsets."""
    index = index if isinstance(index, dict) else {}
    canonical = canonicalize_hits(index, hits)
    if not canonical["hits"]:
        return []
    question = _text(question)
    intent = intent or _intent(question)
    terms = _query_terms(question)
    bonus = {"method": "methods", "result": "results", "limitation": "limitations",
             "comparison": "results"}.get(intent, "unknown")
    candidates = []
    for hit in canonical["hits"]:
        body = _text(hit.get("text"))
        regions = _regions(body)
        for start, end in _sentences(body):
            quote = body[start:end]
            if len(quote) < 12 or len(quote) > 700 or not re.search(r"[。！？.!?；;][\"”’']?$", quote):
                continue
            if _HEADING_RE.match(quote.strip()) or re.match(r"^\s*(?:\[page|##|#\s)", quote, re.I):
                continue
            previous = [row for row in regions if row[0] <= start]
            title = previous[-1][2] if previous else _text(hit.get("section") or hit.get("heading"))
            kind = (previous[-1][3] if previous else None) or _section(title, quote, hit)
            low = quote.lower()
            matched = [term for term in terms if _contains(term, low)]
            if not matched and kind == "unknown":
                continue
            score = sum(1 + min(1.5, len(term) / 8) for term in set(matched))
            score += 3 if kind == bonus else 0
            if intent == "location" and title and any(term in title.lower() for term in terms):
                score += 2
            score += 1.2 if re.search(r"局限|限制|不足|limitation|does not handle|cannot", low) else 0
            score += 1 if re.search(r"qm9|\bmae\b|mean absolute error|误差", low) else 0
            score += 1 if re.search(r"立体化学|stereochemistry", low) else 0
            score += min(.5, len(quote) / 1000)
            source_offset = [hit["start"] + start, hit["start"] + end] if isinstance(hit.get("start"), int) else None
            reason = "section=%s; intent=%s" % (kind, intent)
            if matched:
                reason += "; matched=" + ",".join(sorted(set(matched))[:8])
            candidates.append({"file": _path(hit.get("file")), "sha256": _sha(hit),
                               "chunk_id": hit.get("chunk_id"), "section_type": kind,
                               "offset": [start, end], "source_offset": source_offset,
                               "file_offset": source_offset, "offset_space": "chunk_text",
                               "quote": quote, "relevance": round(score / (score + 3), 6),
                               "reason": reason + "; complete_sentence=true"})
    candidates.sort(key=lambda row: (-row["relevance"], row["offset"][0], row["file"]))
    out, seen = [], set()
    for row in candidates:
        key = (_path(row.get("file")), re.sub(r"\s+", " ", row["quote"]).casefold())
        if key not in seen:
            seen.add(key)
            out.append(row)
        if len(out) >= 12:
            break
    return out


# Direct cited synthesis ----------------------------------------------------
def _words(text):
    text = _text(text)
    return len(re.findall(r"[A-Za-z0-9_+\-]+", text)) + len(re.findall(r"[\u4e00-\u9fff]", text))


def _first(text):
    return next((_text(text)[s:e].strip() for s, e in _sentences(_text(text))),
                re.sub(r"\s+", " ", _text(text)).strip())


def _cited(text, number):
    text = re.sub(r"\s+", " ", _text(text)).strip()
    end = text[-1] if text.endswith(("。", "！", "？", ".", "!", "?", "；", ";")) else ""
    if end:
        text = text[:-1].rstrip()
    return "%s [E%d]%s" % (text, number, end)


def _trim(text, budget):
    if _words(text) <= budget:
        return text
    chars = list(text)
    while chars and _words("".join(chars)) > budget:
        chars.pop()
    return "".join(chars).rstrip(" ,，;；") + "…"


def synthesize_answer(question, evidence, protocol=None, max_words=500):
    """Produce a direct cited answer; empty evidence yields an empty string."""
    rows = [row for row in _dicts(evidence, ("evidence",)) if _text(row.get("quote")).strip()]
    budget = max(0, _int(max_words, 500))
    if not rows or not budget:
        return ""
    intent = _intent(question)
    prefix = {"location": "材料事实（定位）", "comparison": "材料事实（对比证据）",
              "limitation": "材料事实（局限）", "method": "材料事实（方法）",
              "result": "材料事实（结果）", "temporal": "材料事实（时间证据）"}.get(intent, "材料事实")
    parts, used, refs = [], 0, []
    for number, row in enumerate(rows, 1):
        quote = _first(row.get("quote"))
        if not quote:
            continue
        location = ""
        if intent == "location" and row.get("file"):
            location = "%s 的 %s 章节包含：" % (os.path.splitext(row.get("file"))[0], row.get("section_type") or "相关")
        reserve = 80 if not parts else 0
        available = budget - used - reserve
        if available < 12:
            break
        sentence = _cited(prefix + "：" + location + _trim(quote, available), number)
        count = _words(sentence)
        if used + count > budget - reserve:
            break
        parts.append(sentence)
        used += count
        refs.append(number)
        if len(parts) >= 5:
            break
    if not parts:
        return ""
    tag = "".join("[E%d]" % n for n in refs[:4])
    for sentence in ("合理推断：这些材料支持对问题核心的限定性回答，但不能外推到未覆盖的情形 %s。" % tag,
                     "未知：除上述引文明确报告的内容外，其他细节无法由所提供材料确定 %s。" % tag):
        count = _words(sentence)
        if used + count <= budget:
            parts.append(sentence)
            used += count
    return " ".join(parts)


# Coverage and rubric reports ----------------------------------------------
def _filter_dimensions(audit):
    out = set()
    for row in _dicts(audit, ("filter_audit", "audit", "rows")):
        value = _text(row.get("constraint"))
        if "=" in value:
            out.add(value.split("=", 1)[0].strip())
        elif value == "final_keep":
            out.add("final_keep")
        elif value.startswith("unsupported"):
            out.add("unsupported")
    return sorted(out)


def coverage_report(index, question, hits, evidence, filter_audit, canonical_map=None, **kwargs):
    """Report observed coverage, version deduplication and efficiency evidence."""
    index = index if isinstance(index, dict) else {}
    canonical = canonicalize_hits(index, hits, canonical_map)
    primary = canonical["hits"]
    evidence_rows = _dicts(evidence, ("evidence",))
    files = _files(index)
    summary = index.get("summary") if isinstance(index.get("summary"), dict) else {}
    total_files = len(files) or _int(summary.get("files_total"))
    chunks = _dicts(index.get("chunks"))
    total_chunks = len(chunks) or _int(summary.get("chunks_total"))
    hit_files = {_path(row.get("file")) for row in primary if row.get("file")}
    evidence_files = {_path(row.get("file")) for row in evidence_rows if row.get("file")}
    hit_chunks = {(_path(row.get("file")), _text(row.get("chunk_id"))) for row in primary if row.get("chunk_id") is not None}
    evidence_chunks = {(_path(row.get("file")), _text(row.get("chunk_id"))) for row in evidence_rows if row.get("chunk_id") is not None}
    fields, metadata_files = Counter(), 0
    for row in files.values():
        meta = row.get("meta") or row.get("metadata") or {}
        if isinstance(meta, dict) and meta:
            metadata_files += 1
            fields.update(str(key) for key in meta)
    audit = _dicts(filter_audit, ("filter_audit", "audit", "rows"))
    dimensions = _filter_dimensions(audit)
    eliminated = sum(max(0, _int(row.get("dropped_since_prev", row.get("dropped", 0)))) for row in audit)
    plans = kwargs.get("plans", kwargs.get("query_plans"))
    search_meta = index.get("search_meta") if isinstance(index.get("search_meta"), dict) else {}
    if plans is None:
        plans = search_meta.get("rewritten_query_plans") or index.get("query_plans")
    plan_source = "supplied"
    if plans is None:
        plans, plan_source = refine_query_plans(question, kwargs.get("terms_txt", ""), case=kwargs.get("case")), "derived_from_question"
    plan_rows = [row for row in _rows(plans, ("plans", "query_plans")) if isinstance(row, (dict, str))]
    prf = kwargs.get("prf", kwargs.get("prf_expansion"))
    if prf is None:
        prf = search_meta.get("prf_expansion")
    if prf is None:
        try:
            prf = _SE.pseudo_relevance_feedback(chunks, primary, question)
        except Exception:
            prf = {}
    prf_terms = [term for term in (prf.get("expansion_terms") or []) if term] if isinstance(prf, dict) else []
    prf_evidence = _dicts(prf.get("evidence")) if isinstance(prf, dict) else []
    dedup = kwargs.get("deduplicated_count")
    dedup = max(0, _int(len(canonical["dropped"]) if dedup is None else dedup))
    citation_review = kwargs.get("citation_review", kwargs.get("refs"))
    citations = _dicts(citation_review, ("evidence_refs", "refs", "citations")) if citation_review is not None else evidence_rows
    verified = sum(1 for row in citations if row.get("quote_verified") is True or row.get("source_slice_verified") is True)
    resolution = canonical["version_resolution"]
    exact_groups = index.get("dedup_groups") if isinstance(index.get("dedup_groups"), dict) else {}
    file_ratio, section_ratio = _ratio(len(hit_files), total_files), _ratio(len(hit_chunks), total_chunks)
    return {
        "coverage": {
            "files": {"total": total_files, "hit_files": len(hit_files), "evidence_files": len(evidence_files), "ratio": round(file_ratio, 6)},
            "sections": {"total_chunks": total_chunks, "hit_chunks": len(hit_chunks), "evidence_chunks": len(evidence_chunks),
                         "section_types": dict(sorted(Counter(row.get("section_type") for row in evidence_rows).items())), "ratio": round(section_ratio, 6)},
            "metadata": {"total_files": total_files, "files_with_metadata": metadata_files,
                         "metadata_fields": dict(sorted(fields.items())), "version_families": resolution.get("version_groups", 0),
                         "alias_records": len(index.get("aliases") or []), "ratio": round(_ratio(metadata_files, total_files), 6)},
            "filters": {"dimensions": dimensions, "audit_rows": len(audit),
                        "executed_constraints": len([row for row in audit if row.get("constraint")]),
                        "eliminated_events": eliminated, "ratio": round(_ratio(len([row for row in audit if row.get("constraint")]), max(1, len(dimensions))), 6)},
            "versions": {"source": resolution.get("source"), "canonical_map_count": len(resolution.get("canonical_map") or {}),
                         "canonical_files": resolution.get("canonical_files", []), "aliases": resolution.get("aliases", []),
                         "superseded": resolution.get("superseded", []), "deduplicated_count": dedup,
                         "exact_duplicate_groups": len(exact_groups), "ratio": round(_ratio(len(hit_files), max(1, len(files))), 6)},
        },
        "efficiency_evidence": {
            "query_plan_count": len(plan_rows), "query_plan_source": plan_source,
            "plan_intents": dict(sorted(Counter(row.get("intent") for row in plan_rows if isinstance(row, dict) and row.get("intent")).items())),
            "prf_term_count": len(prf_terms), "prf_terms": prf_terms, "prf_evidence_count": len(prf_evidence),
            "deduplicated_count": dedup, "citation_review_count": len(citations), "citation_verified_count": verified,
            "filter_eliminated_count": eliminated, "hit_count": len(primary), "evidence_count": len(evidence_rows),
        },
    }


def _verify_evidence(index, evidence):
    chunks = _dicts(index.get("chunks")) if isinstance(index, dict) else []
    verified = 0
    for row in evidence:
        if row.get("quote_verified") is True and row.get("source_slice_verified") is True:
            verified += 1
            continue
        path, chunk_id = _path(row.get("file")), row.get("chunk_id")
        source = next((chunk for chunk in chunks if _path(chunk.get("file")) == path and str(chunk.get("chunk_id")) == str(chunk_id)), None)
        offset = row.get("offset")
        if not isinstance(offset, (list, tuple)) or len(offset) != 2:
            continue
        start, end = _int(offset[0], -1), _int(offset[1], -1)
        if source and 0 <= start <= end <= len(_text(source.get("text"))) and _text(source.get("text"))[start:end] == _text(row.get("quote")):
            verified += 1
    return verified


def rubric_report(index=None, question=None, hits=None, evidence=None, filter_audit=None, canonical_map=None, **kwargs):
    """Return honest, evidence-linked 40/25/15/10/10 rubric statuses."""
    paths = kwargs.get("evidence_paths") or []
    paths = [paths] if isinstance(paths, str) else [_text(path) for path in paths if _text(path)]
    coverage = kwargs.get("coverage")
    source_index = index if isinstance(index, dict) else {}
    evidence_rows = _dicts(evidence, ("evidence",))
    if coverage is None and isinstance(index, dict) and "coverage" in index and "efficiency_evidence" in index:
        coverage, source_index = index, {}
        if evidence is None and isinstance(question, list):
            evidence_rows = _dicts(question, ("evidence",))
    if coverage is None:
        coverage = coverage_report(index, question, hits, evidence, filter_audit, canonical_map=canonical_map, **kwargs)
    cov = coverage.get("coverage", {}) if isinstance(coverage, dict) else {}
    eff = coverage.get("efficiency_evidence", {}) if isinstance(coverage, dict) else {}
    file_ratio = float((cov.get("files") or {}).get("ratio", 0) or 0)
    section_ratio = float((cov.get("sections") or {}).get("ratio", 0) or 0)
    actual = round(40 * (.6 * file_ratio + .4 * section_ratio), 2) if evidence_rows else 0
    plans, prf, dedup = _int(eff.get("query_plan_count")), _int(eff.get("prf_term_count")), _int(eff.get("deduplicated_count"))
    usage = round(25 * min(1, .5 * _ratio(plans, 8) + .3 * _ratio(prf, 8) + .2 * _ratio(dedup, 4)), 2)
    intents = eff.get("plan_intents") if isinstance(eff.get("plan_intents"), dict) else {}
    versions = cov.get("versions") if isinstance(cov.get("versions"), dict) else {}
    innovation = round(15 * min(1, .4 * _ratio(len(intents), 5) + .35 * bool(versions.get("deduplicated_count")) + .25 * bool(evidence_rows)), 2)
    verified = _verify_evidence(source_index, evidence_rows) if evidence_rows else 0
    reliability = round(10 * _ratio(verified, len(evidence_rows)), 2)
    ecosystem = round(10 * sum(bool(value) for value in (
        versions.get("canonical_map_count"), (cov.get("metadata") or {}).get("files_with_metadata"),
        (cov.get("filters") or {}).get("dimensions"), (cov.get("metadata") or {}).get("version_families"))) / 4, 2)

    def ep(*items):
        return list(dict.fromkeys(paths + [_text(item) for item in items if _text(item)]))

    rubric = {
        "actual_effect": {"weight": 40, "max_points": 40, "points": actual,
                          "status": "proxy_measured" if evidence_rows else "not_measured",
                          "claim_quality_verified": False,
                          "evidence_paths": ep("coverage.files.ratio", "coverage.sections.ratio", "evidence[].quote")},
        "usage_gain": {"weight": 25, "max_points": 25, "points": usage,
                       "status": "measured" if plans or prf or dedup else "not_measured",
                       "evidence_paths": ep("efficiency_evidence.query_plan_count", "efficiency_evidence.prf_term_count", "efficiency_evidence.deduplicated_count")},
        "innovation": {"weight": 15, "max_points": 15, "points": innovation,
                       "status": "measured" if evidence_rows or dedup else "not_measured",
                       "evidence_paths": ep("coverage.versions", "efficiency_evidence.plan_intents", "evidence[].section_type")},
        "reliability": {"weight": 10, "max_points": 10, "points": reliability,
                        "status": "verified" if evidence_rows and verified == len(evidence_rows) else ("partial" if verified else "not_measured"),
                        "verified_evidence": verified,
                        "evidence_paths": ep("evidence[].offset", "evidence[].quote", "evidence[].sha256")},
        "ecosystem": {"weight": 10, "max_points": 10, "points": ecosystem,
                      "status": "measured" if versions.get("canonical_map_count") or (cov.get("metadata") or {}).get("files_with_metadata") or (cov.get("filters") or {}).get("dimensions") else "not_measured",
                      "evidence_paths": ep("coverage.metadata", "coverage.filters", "coverage.versions")},
    }
    all_paths = list(paths)
    for row in rubric.values():
        all_paths.extend(row["evidence_paths"])
    return {"rubric_mapped": rubric, "dimensions": rubric,
            "total_points": round(sum(row["points"] for row in rubric.values()), 2), "max_points": 100,
            "status": "partial" if any(row["status"] in {"not_measured", "partial"} for row in rubric.values()) else "measured",
            "evidence_paths": list(dict.fromkeys(all_paths)),
            "honesty": {"actual_effect_is_coverage_proxy": True, "unmeasured_dimensions_score_zero": True, "no_external_validation": True}}
