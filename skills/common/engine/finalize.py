"""finalize.py — deterministic, complete first delivery for every fixed case.

The retrieval engine deliberately avoids pretending to understand science.  This
module turns its verified facts into a complete result.json anyway: a grounded
answer, evidence references, source inventory, version lineage, failure recovery
and (for the classification track) an actual organized copy of the library.

Safety contract:
  * source files are never moved, renamed, overwritten or deleted;
  * organized files are regular-file copies under <out>/organized;
  * exact duplicates become manifest aliases to one canonical copy;
  * every copy/alias has a source->target record and a rollback plan;
  * metadata is taken from bytes/filesystem facts only, never invented.
"""
import argparse
import copy
import fnmatch
import json
import os
import re
import shutil
from datetime import datetime, timezone

from lib import ledger_append, now_iso, read_text_safe, sha256_file, sha256_text, write_json

ENGINE_REVISION = "bohrium-weekly-common@5.0"
ORG_MARKER = ".bohrium-organized"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_source_path(root, relpath):
    """Resolve a source path and reject absolute/traversal paths outside root."""
    if not root or not relpath:
        raise ValueError("source root and relpath are required")
    root_real = os.path.normcase(os.path.realpath(root))
    candidate = os.path.normcase(os.path.realpath(os.path.join(root, str(relpath))))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise ValueError("source relpath escapes library root: " + str(relpath))
    return os.path.join(root, str(relpath))


def validate_index_paths(index):
    """Fail before writing evidence if any readable indexed path escapes root."""
    root = index.get("root")
    for file_meta in index.get("files", []):
        if file_meta.get("readable") and file_meta.get("sha256"):
            safe_source_path(root, file_meta.get("relpath"))
    for chunk in index.get("chunks", []):
        safe_source_path(root, chunk.get("file"))


def _norm_ext(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if value == "*":
        return "*"
    return value if value.startswith(".") else "." + value


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def resolve_library_root(case, task_md="", override=None, base_dir=None):
    """Resolve common case schema variants without guessing file contents."""
    candidates = [override]
    explicit_requested = bool(override)
    base_abs = os.path.abspath(base_dir or os.getcwd())
    if override:
        raw_override = os.path.expandvars(os.path.expanduser(str(override).strip()))
        override_path = raw_override if os.path.isabs(raw_override) else os.path.join(base_abs, raw_override)
        if not os.path.isdir(override_path):
            raise FileNotFoundError("explicit --library path is missing or invalid: " + str(override))
        return os.path.abspath(override_path)
    for key in ("library", "corpus_root", "input_dir", "materials_root", "materials",
                "files_root", "document_root", "source_root", "files"):
        value = case.get(key)
        if value not in (None, "", [], {}):
            explicit_requested = True
        if isinstance(value, dict):
            value = value.get("path") or value.get("root") or value.get("dir")
        if isinstance(value, list):
            raw_paths = [v.get("path") or v.get("file") or v.get("relpath") if isinstance(v, dict) else v
                         for v in value]
            paths = [os.path.expandvars(os.path.expanduser(str(v))) for v in raw_paths if v not in (None, "")]
            paths = [v if os.path.isabs(v) else os.path.join(base_abs, v) for v in paths]
            missing = [v for v in paths if not os.path.exists(v)]
            if missing:
                raise FileNotFoundError("explicit material list contains missing paths: " + ", ".join(missing))
            if paths:
                try:
                    if all(os.path.isfile(v) for v in paths):
                        candidates.append(os.path.commonpath([os.path.dirname(v) for v in paths]))
                    elif all(os.path.isdir(v) for v in paths):
                        candidates.append(os.path.commonpath(paths))
                    else:
                        raise FileNotFoundError("explicit material list mixes files and directories")
                except ValueError as exc:
                    raise FileNotFoundError("material paths do not share one library root") from exc
            continue
        candidates.append(value)

    for line in task_md.splitlines():
        m = re.match(r"\s*(?:library|corpus_root|source_root|root)\s*[:=]\s*[\"']?([^\"'\n]+)",
                     line, re.I)
        if m:
            explicit_requested = True
            candidates.append(m.group(1).strip())

    base_dir = os.path.abspath(base_dir or os.getcwd())
    for raw in candidates:
        if not raw:
            continue
        raw = os.path.expandvars(os.path.expanduser(str(raw).strip()))
        path = raw if os.path.isabs(raw) else os.path.join(base_dir, raw)
        if os.path.isdir(path):
            return os.path.abspath(path)

    if explicit_requested:
        raise FileNotFoundError("explicit library path is missing or invalid: " +
                                ", ".join(str(x) for x in candidates if x))

    # The platform convention is /input/case.json plus a material directory.
    if os.path.isdir(base_dir):
        preferred = ("library", "materials", "files", "corpus", "documents", "data")
        children = []
        try:
            children = sorted(os.listdir(base_dir))
        except OSError:
            pass
        for name in preferred:
            path = os.path.join(base_dir, name)
            if os.path.isdir(path):
                return os.path.abspath(path)
        for name in children:
            path = os.path.join(base_dir, name)
            if os.path.isdir(path) and not name.startswith("."):
                return os.path.abspath(path)
        if any(os.path.isfile(os.path.join(base_dir, n)) for n in children
               if n.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md"))):
            return base_dir
    return None


def infer_profile(case, task_md=""):
    for key in ("profile", "task_type", "challenge_type", "track"):
        value = str(case.get(key) or "").lower()
        if any(word in value for word in ("classify", "classification", "分类", "整理")):
            return "classify"
        if any(word in value for word in ("audit", "审查", "合规", "引用", "事实核验", "质量评价", "批注", "论证")):
            return "audit"
        if any(word in value for word in ("review", "literature", "综述", "筛选")):
            return "review"
        if any(word in value for word in ("search", "retrieval", "检索")):
            return "search"
        if any(word in value for word in ("code", "代码", "执行", "调试")):
            return "code"
        if any(word in value for word in ("data", "分析", "统计", "可视化", "数据库")):
            return "data"
        if any(word in value for word in ("experiment", "实验方案", "参数优化", "可行性")):
            return "experiment"
    title = str(case.get("title") or "")
    if not title:
        match = re.search(r"(?m)^#{1,4}\s*(.+)$", task_md)
        title = match.group(1) if match else ""
    text = "\n".join([str(case.get("question") or ""), str(case.get("task") or ""),
                      title, task_md[:12000]]).lower()
    scores = {"classify": 0, "search": 0, "review": 0, "data": 0, "code": 0, "experiment": 0, "audit": 0}
    for word in ("分类", "整理", "重命名", "去重", "目录", "版本关系", "元数据", "批量"):
        scores["classify"] += text.count(word)
    for word in ("综述", "筛选", "主题归纳", "证据矩阵", "研究空白", "纳排"):
        scores["review"] += text.count(word)
    for word in ("检索", "定位", "过滤条件", "证据回链", "查询", "章节"):
        scores["search"] += text.count(word)
    for word in ("数据", "清洗", "统计", "可视化", "图表", "数据库", "缺失", "异常值", "重复记录", "效应量", "置信区间"):
        scores["data"] += text.count(word)
    for word in ("代码", "执行", "调试", "环境配置", "依赖", "运行日志", "脚本", "退出状态"):
        scores["code"] += text.count(word)
    for word in ("实验方案", "参数优化", "可行性", "对照组", "设备材料", "时间成本", "风险与异常"):
        scores["experiment"] += text.count(word)
    for word in ("审查", "合规", "引用", "事实核验", "质量评价", "批注", "论证", "学术规范", "抄袭", "重复率"):
        scores["audit"] += text.count(word)
    if "科研文件智能检索" in title or title.startswith("科研文件检索"):
        scores["search"] += 100
    elif "文献与资料自动分类" in title or title.startswith("文献分类"):
        scores["classify"] += 100
    elif any(word in title for word in ("审查", "合规", "引用", "事实核验", "质量评价", "批注", "论证")):
        scores["audit"] += 100
    elif "文献综述生成" in title or title.startswith("文献综述"):
        scores["review"] += 100
    elif any(word in title for word in ("代码生成", "代码执行", "科研代码")):
        scores["code"] += 100
    elif any(word in title for word in ("实验方案", "实验参数", "可行性评估", "方案比较与选择")):
        scores["experiment"] += 100
    elif any(word in title for word in ("数据分析", "统计分析", "数据库查询", "结果解读")):
        scores["data"] += 100
    best = max(scores, key=scores.get)
    return best if scores[best] else "search"


def _first_meaningful_line(text):
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("# ").strip()
        if not line or line.startswith(("[page ", "[slide ", "[sheet ", "[sheet:")):
            continue
        if re.match(r"(?i)^(administrative|version|alias|metadata)\s+(note|record|info)", line):
            continue
        if 4 <= len(line) <= 220:
            return line
    return None


def extract_metadata(file_meta, chunks):
    """Extract only fields that are directly present in metadata or text."""
    meta = dict(file_meta.get("meta") or {})
    texts = [c.get("text", "") for c in chunks if c.get("file") == file_meta.get("relpath")]
    text = "\n".join(texts)[:30000]
    title = meta.get("title") or _first_meaningful_line(text) or os.path.splitext(file_meta["name"])[0]
    author = meta.get("author") or meta.get("creator") or meta.get("authors")
    if not author:
        m = re.search(r"(?im)^\s*(?:authors?|作者)\s*[:：]\s*(.+)$", text)
        if m:
            author = m.group(1).strip()[:240]
    year = meta.get("year")
    if not year:
        m = YEAR_RE.search(text[:12000])
        if m:
            year = int(m.group(1))
    doi = meta.get("doi")
    if not doi:
        m = DOI_RE.search(text[:20000])
        if m:
            doi = m.group(0).rstrip(".,;")
    journal = meta.get("journal") or meta.get("publication")
    if not journal:
        m = re.search(r"(?im)^\s*(?:journal|期刊|publication)\s*[:：]\s*(.+)$", text)
        if m:
            journal = m.group(1).strip()[:200]
    return {
        "title": str(title).strip() if title else None,
        "author": str(author).strip() if author else None,
        "year": year,
        "journal": str(journal).strip() if journal else None,
        "doi": str(doi).strip().lower() if doi else None,
        "source": "file metadata / extracted text",
        "missing": [k for k, v in {"title": title, "author": author, "year": year,
                                    "journal": journal, "doi": doi}.items() if v in (None, "")],
    }


def _bucket_definitions(case):
    """Normalize likely organization-rule schemas into bucket definitions."""
    defs = []
    for key in ("buckets", "categories", "organization_rules", "directory_rules",
                "classification_rules", "target_layout"):
        value = case.get(key)
        if isinstance(value, dict):
            for name, spec in value.items():
                if isinstance(spec, dict):
                    item = dict(spec)
                    item.setdefault("id", name)
                    item.setdefault("title", name)
                else:
                    item = {"id": name, "title": name, "criteria": str(spec)}
                defs.append(item)
        elif isinstance(value, list):
            defs.extend(value)
    rules = case.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            target = rule.get("bucket") or rule.get("target") or rule.get("then")
            if not target:
                continue
            item = dict(rule)
            item["id"] = rule.get("id") or rule.get("rule_id") or target
            item["title"] = rule.get("title") or rule.get("name") or target
            item["match"] = rule.get("if") or rule.get("when") or rule.get("match") or {}
            if isinstance(target, dict):
                item["bucket_id"] = target.get("id") or target.get("bucket") or target.get("name")
            else:
                item["bucket_id"] = target
            defs.append(item)
    normalized = []
    for i, spec in enumerate(defs, 1):
        if isinstance(spec, str):
            spec = {"title": spec, "match": {"keywords": [spec]}}
        if not isinstance(spec, dict):
            continue
        title = spec.get("title") or spec.get("name") or spec.get("bucket") or f"bucket-{i}"
        normalized.append({
            "id": str(spec.get("id") or spec.get("bucket_id") or f"B{i:02d}"),
            "title": str(title),
            "criteria": spec.get("criteria") or spec.get("description") or spec.get("rule") or "",
            "match": spec.get("match") or spec.get("if") or spec.get("when") or {},
            "priority": spec.get("priority", i),
        })
    indexed = list(enumerate(normalized))
    indexed.sort(key=lambda pair: (
        pair[1].get("priority") if isinstance(pair[1].get("priority"), (int, float)) else pair[0],
        pair[0]))
    return [item for _, item in indexed]


def _text_blob(file_meta, metadata, chunks):
    parts = [file_meta.get("relpath", ""), file_meta.get("name", ""),
             metadata.get("title") or "", metadata.get("doi") or ""]
    parts.extend(c.get("text", "")[:5000] for c in chunks if c.get("file") == file_meta.get("relpath"))
    return "\n".join(parts).lower()


def _match_rule(file_meta, metadata, chunks, matcher):
    if isinstance(matcher, str):
        matcher = {"keywords": [matcher]}
    if not isinstance(matcher, dict) or not matcher:
        return False, "no machine-readable matcher"
    allowed = {"extensions", "ext", "type", "dir", "directories", "path", "keywords",
               "any_keywords", "all_keywords", "patterns", "filename_pattern", "regex",
               "doi", "year_min", "year_max", "metadata"}
    unknown = sorted(set(matcher) - allowed)
    if unknown:
        return None, "unsupported hard matcher keys: " + ",".join(unknown)
    rel = file_meta.get("relpath", "").replace("\\", "/")
    name = file_meta.get("name", "")
    ext = _norm_ext(file_meta.get("ext"))
    blob = _text_blob(file_meta, metadata, chunks)
    tests = []

    exts = {_norm_ext(x) for x in _as_list(matcher.get("extensions") or matcher.get("ext") or matcher.get("type"))}
    if exts:
        tests.append(("*" in exts or ext in exts, f"extension={sorted(exts)}"))
    dirs = _as_list(matcher.get("dir") or matcher.get("directories") or matcher.get("path"))
    if dirs:
        tests.append((any(rel == str(d).rstrip("/") or rel.startswith(str(d).rstrip("/") + "/")
                          or fnmatch.fnmatch(rel, str(d)) for d in dirs), f"path={dirs}"))
    keywords = _as_list(matcher.get("keywords") or matcher.get("any_keywords"))
    if keywords:
        tests.append((any(str(k).lower() in blob for k in keywords), f"any keyword in {keywords}"))
    all_keywords = _as_list(matcher.get("all_keywords"))
    if all_keywords:
        tests.append((all(str(k).lower() in blob for k in all_keywords), f"all keywords {all_keywords}"))
    patterns = _as_list(matcher.get("patterns") or matcher.get("filename_pattern"))
    if patterns:
        tests.append((any(fnmatch.fnmatch(name, str(p)) or fnmatch.fnmatch(rel, str(p)) for p in patterns),
                      f"filename pattern={patterns}"))
    if matcher.get("regex"):
        try:
            tests.append((re.search(str(matcher["regex"]), rel + "\n" + name, re.I) is not None,
                          f"regex={matcher['regex']}"))
        except re.error:
            tests.append((False, "invalid regex"))
    if matcher.get("doi"):
        tests.append((str(matcher["doi"]).lower() == str(metadata.get("doi") or "").lower(),
                      f"doi={matcher['doi']}"))
    year_min, year_max = matcher.get("year_min"), matcher.get("year_max")
    if year_min or year_max:
        year = metadata.get("year")
        tests.append((isinstance(year, int) and (not year_min or year >= int(year_min))
                      and (not year_max or year <= int(year_max)), f"year range={year_min}..{year_max}"))
    meta_match = matcher.get("metadata")
    if isinstance(meta_match, dict):
        tests.append((all(str(metadata.get(k) or "").lower() == str(v).lower()
                          for k, v in meta_match.items()), f"metadata={meta_match}"))
    if not tests:
        return False, "no supported matcher"
    return all(ok for ok, _ in tests), "; ".join(reason for ok, reason in tests if ok) or "rule did not match"


def _fallback_bucket(file_meta):
    rel = file_meta.get("relpath", "").replace("\\", "/")
    parts = rel.split("/")
    ext = _norm_ext(file_meta.get("ext"))
    if len(parts) > 1 and parts[0] not in (".", ""):
        return parts[0], "source top-level directory"
    mapping = {
        ".pdf": "papers", ".doc": "papers", ".docx": "papers", ".txt": "notes",
        ".md": "notes", ".csv": "data", ".tsv": "data", ".xlsx": "data",
        ".xls": "data", ".json": "data", ".pptx": "slides", ".ppt": "slides",
        ".png": "images", ".jpg": "images", ".jpeg": "images", ".tif": "images",
        ".tiff": "images",
    }
    if ext in mapping:
        return mapping[ext], "format-based fallback"
    return "unclassified", "unknown format and no matching organization rule"


def _explicit_assignment(case, rel):
    for key in ("file_assignments", "assignments", "classification", "file_buckets"):
        value = case.get(key)
        if isinstance(value, dict) and rel in value:
            target = value[rel]
            if isinstance(target, dict):
                return target.get("bucket") or target.get("bucket_id") or target.get("target")
            return target
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict) and row.get("file") == rel:
                    return row.get("bucket") or row.get("bucket_id") or row.get("target")
    return None


def classify_files(index, case, chunks=None):
    """Build a complete catalog and a relpath->bucket assignment map."""
    chunks = chunks if chunks is not None else index.get("chunks", [])
    defs = _bucket_definitions(case)
    assignments, unclassified = {}, []
    buckets = {}

    for file_meta in index.get("files", []):
        rel = file_meta.get("relpath")
        metadata = extract_metadata(file_meta, chunks)
        explicit = _explicit_assignment(case, rel)
        chosen, reason = None, None
        blocked_reason = None
        if explicit:
            chosen, reason = str(explicit), "explicit case file assignment"
        if not chosen:
            for definition in defs:
                ok, why = _match_rule(file_meta, metadata, chunks, definition["match"])
                if ok is None:
                    blocked_reason = why
                    break
                if ok:
                    chosen, reason = definition["id"], why
                    break
        if blocked_reason:
            unclassified.append({"relpath": rel, "why": blocked_reason,
                                 "sha256": file_meta.get("sha256")})
            continue
        if not chosen:
            fallback, reason = _fallback_bucket(file_meta)
            if fallback == "unclassified":
                unclassified.append({"relpath": rel, "why": reason,
                                     "sha256": file_meta.get("sha256")})
                continue
            chosen = fallback
            if not any(d["id"] == chosen for d in defs):
                defs.append({"id": chosen, "title": chosen, "criteria": reason,
                             "match": {}, "priority": 999})
        definition = next((d for d in defs if d["id"] == chosen),
                          {"id": chosen, "title": chosen, "criteria": reason})
        assignments[rel] = {"bucket_id": chosen, "reason": reason,
                            "metadata": metadata, "file": file_meta}
        bucket = buckets.setdefault(chosen, {"bucket_id": chosen, "title": definition["title"],
                                             "criteria": definition.get("criteria") or reason,
                                             "members": []})
        bucket["members"].append({
            "relpath": rel, "sha256": file_meta.get("sha256"), "ext": file_meta.get("ext"),
            "size": file_meta.get("size"), "metadata": metadata,
            "assignment_basis": reason,
        })

    ordered = sorted(buckets.values(), key=lambda b: b["bucket_id"])
    for i, bucket in enumerate(ordered, 1):
        bucket["display_order"] = i
        bucket["member_count"] = len(bucket["members"])
    catalog = {"bucket_count": len(ordered), "file_count": sum(len(b["members"]) for b in ordered),
               "buckets": ordered, "unclassified": unclassified,
               "rules_source": "case organization rules + explicit assignments + declared fallbacks"}
    return catalog, assignments, unclassified


def _safe_component(value, fallback="untitled", limit=90):
    text = SAFE_NAME_RE.sub("-", str(value or "")).strip("-_.")
    text = re.sub(r"-{2,}", "-", text)
    while text and len(text.encode("utf-8")) > limit:
        text = text[:-1]
    return (text.rstrip("-_.") or fallback)


def _rename_template(case, task_md):
    for key in ("rename_template", "naming_template", "filename_template"):
        if case.get(key):
            return str(case[key])
    m = re.search(r"(?im)^\s*(?:rename|naming|filename)\s+template\s*[:=]\s*(.+)$", task_md)
    return m.group(1).strip() if m else None


def proposed_filename(file_meta, metadata, case, task_md):
    ext = file_meta.get("ext") or os.path.splitext(file_meta["name"])[1]
    title = metadata.get("title") or os.path.splitext(file_meta["name"])[0]
    author = metadata.get("author") or "unknown-author"
    year = metadata.get("year") or "undated"
    doi = metadata.get("doi") or "no-doi"
    template = _rename_template(case, task_md)
    if template:
        try:
            stem = template.format(author=_safe_component(author, limit=36), year=year,
                                   title=_safe_component(title), doi=_safe_component(doi, limit=60),
                                   ext=ext.lstrip("."))
            if stem.lower().endswith(ext.lower()):
                stem = stem[:-len(ext)]
            return _safe_component(stem, limit=150) + ext.lower()
        except (KeyError, ValueError):
            pass
    return _safe_component(title, limit=120) + ext.lower()


def _canonical_for_hash(index):
    choose = {}
    for sha, paths in (index.get("dedup_groups") or {}).items():
        meta = {f["relpath"]: f for f in index.get("files", [])}
        ranked = []
        for path in paths:
            f = meta.get(path, {})
            family_rank = 0
            for fam in (index.get("families") or {}).values():
                for member in fam.get("members", []):
                    if member.get("relpath") == path:
                        family_rank = max(family_rank, int(member.get("rank") or 0))
            score = (family_rank, len(f.get("meta") or {}), -len(path))
            ranked.append((score, path))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        choose[sha] = ranked[0][1]
    return choose


def organize_library(index, catalog, assignments, out_dir, case, task_md, ledger_path=None):
    """Create a real organized copy; never mutate the source library."""
    source_root = index.get("root")
    target_root = os.path.join(out_dir, "organized")
    if source_root:
        source_abs = os.path.realpath(source_root)
        target_abs = os.path.realpath(target_root)
        if (source_abs == target_abs or source_abs.startswith(target_abs + os.sep)
                or target_abs.startswith(source_abs + os.sep)):
            raise RuntimeError("refusing to organize when source and output trees overlap")
    marker = os.path.join(target_root, ORG_MARKER)
    if os.path.islink(target_root):
        raise RuntimeError("refusing to replace symlinked output directory: " + target_root)
    if os.path.exists(target_root) and not os.path.isfile(marker):
        raise RuntimeError("refusing to replace unorganized output directory: " + target_root)
    tmp_root = os.path.join(out_dir, ".organized.tmp")
    if os.path.exists(tmp_root):
        shutil.rmtree(tmp_root)
    os.makedirs(tmp_root, exist_ok=True)

    operations, aliases, failures = [], [], []
    copied_count = 0
    canonical_by_hash = _canonical_for_hash(index)
    planned = {}
    used_targets = set()

    for rel, assignment in assignments.items():
        file_meta = assignment["file"]
        filename = proposed_filename(file_meta, assignment["metadata"], case, task_md)
        bucket_dir = _safe_component(assignment["bucket_id"], limit=60)
        target_rel = bucket_dir + "/" + filename
        if target_rel in used_targets:
            stem, ext = os.path.splitext(filename)
            suffix = str(file_meta.get("sha256") or sha256_text(rel))[:8]
            target_rel = bucket_dir + "/" + stem + "--" + suffix + ext
            counter = 2
            while target_rel in used_targets:
                target_rel = bucket_dir + "/" + stem + "--" + suffix + f"-{counter}" + ext
                counter += 1
        used_targets.add(target_rel)
        planned[rel] = target_rel

    # Canonical files first so aliases always point at an existing target.
    ordered = sorted(planned, key=lambda rel: (
        0 if canonical_by_hash.get(assignments[rel]["file"].get("sha256"), rel) == rel else 1, rel))
    completed = {}
    for rel in ordered:
        assignment = assignments[rel]
        file_meta = assignment["file"]
        sha = file_meta.get("sha256")
        target_rel = planned[rel]
        canonical = canonical_by_hash.get(sha, rel)
        try:
            if canonical != rel:
                if canonical not in completed:
                    raise RuntimeError("canonical copy not completed: " + canonical)
                alias = {"alias": rel, "canonical_source": canonical,
                         "canonical_target": completed[canonical], "sha256": sha,
                         "reason": "identical sha256; one regular-file copy retained"}
                aliases.append(alias)
                operations.append({"action": "alias", "source": rel, "target": completed[canonical],
                                   "sha256": sha, "status": "ok", "reversible": True,
                                   "rollback": "remove alias row from manifest; source untouched"})
                completed[rel] = completed[canonical]
                continue
            src = safe_source_path(source_root, rel)
            if not os.path.isfile(src):
                raise RuntimeError("source file missing or unreadable")
            dst = os.path.join(tmp_root, target_rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            target_sha = sha256_file(dst)
            if sha and target_sha != sha:
                raise RuntimeError("copied-file sha256 mismatch")
            sidecar = {
                "schema": "bohrium-file-metadata@1", "source_relpath": rel,
                "source_sha256": sha, "target_relpath": target_rel,
                "file_facts": {k: file_meta.get(k) for k in
                               ("name", "ext", "size", "mtime", "permissions", "readable")},
                "metadata": assignment["metadata"], "bucket": assignment["bucket_id"],
                "assignment_basis": assignment["reason"], "generated_at": _now(),
            }
            write_json(dst + ".meta.json", sidecar)
            completed[rel] = target_rel
            copied_count += 1
            operations.append({"action": "copy+rename+metadata", "source": rel,
                               "target": target_rel, "sha256": sha, "target_sha256": target_sha,
                               "status": "ok", "reversible": True,
                               "rollback": "delete generated target and sidecar; source untouched"})
            if ledger_path:
                ledger_append(ledger_path, "organize_copy", rel,
                              f"-> organized/{target_rel}; sha256={sha}", status="ok")
        except Exception as exc:
            failure = {"file": rel, "error": str(exc), "recovery": "fix source/permission then rerun with --force"}
            failures.append(failure)
            operations.append({"action": "copy+rename+metadata", "source": rel,
                               "target": target_rel, "sha256": sha, "status": "failed",
                               "reversible": True, "error": str(exc)})
            if ledger_path:
                ledger_append(ledger_path, "organize_copy", rel, str(exc), status="fail")

    with open(os.path.join(tmp_root, ORG_MARKER), "w", encoding="utf-8") as f:
        f.write("Generated by bohrium-weekly-common@5.0; source files untouched.\n")
    manifest = {
        "schema": "bohrium-organized-library@1", "generated_at": _now(),
        "source_root": source_root, "source_files_total": len(index.get("files", [])),
        "organized_files": copied_count, "alias_rows": len(aliases), "failures": failures,
        "catalog": catalog, "operations": operations, "aliases": aliases,
        "rollback": {"originals_modified": False, "action": "delete only <out>/organized",
                     "note": "all originals remain at source_root; manifest records every target"},
    }
    write_json(os.path.join(tmp_root, "manifest.json"), manifest)
    write_json(os.path.join(tmp_root, "aliases.json"), aliases)
    backup_root = os.path.join(out_dir, ".organized.backup")
    if os.path.exists(backup_root):
        shutil.rmtree(backup_root)
    if os.path.exists(target_root):
        os.replace(target_root, backup_root)
    try:
        os.replace(tmp_root, target_root)
    except Exception:
        if os.path.exists(backup_root) and not os.path.exists(target_root):
            os.replace(backup_root, target_root)
        raise
    else:
        if os.path.exists(backup_root):
            shutil.rmtree(backup_root)
    rollback_md = os.path.join(out_dir, "ROLLBACK.md")
    with open(rollback_md, "w", encoding="utf-8") as f:
        f.write("# 回滚说明\n\n"
                "本次整理只生成 `organized/` 下的常规文件副本，未移动、改名、覆盖或删除任何源文件。\n"
                "回滚时删除 `organized/` 即可；逐文件对应关系见 `organized/manifest.json`。\n"
                "若异常中断后存在 `.organized.backup/`，将它改回 `organized/` 即可恢复上一版；源文件始终未修改。\n"
                "精确重复项在 `organized/aliases.json` 中记录为 canonical 别名，不产生第二份副本。\n")
    return {
        "path": "organized", "manifest": "organized/manifest.json",
        "aliases": "organized/aliases.json", "rollback": "ROLLBACK.md",
        "operations": operations, "alias_rows": aliases, "failures": failures,
        "organized_files": copied_count, "source_files_total": len(index.get("files", [])),
        "originals_modified": False,
    }


def version_lineage(index):
    lineages = []
    file_meta = {f["relpath"]: f for f in index.get("files", [])}
    for key, family in (index.get("families") or {}).items():
        members = sorted(family.get("members", []),
                         key=lambda m: (int(m.get("rank") or 0), file_meta.get(m.get("relpath"), {}).get("mtime", 0),
                                        m.get("relpath", "")))
        if not members:
            continue
        canonical = members[-1]["relpath"]
        lineages.append({
            "family_key": key, "title_guess": family.get("title_guess"),
            "identity_basis": family.get("identity_basis") or "normalized title",
            "canonical": canonical,
            "members": [{"relpath": m.get("relpath"), "sha256": m.get("sha256"),
                         "variant_rank": m.get("rank")} for m in members],
            "evidence": "same DOI when present, otherwise same normalized title; ordered by variant_rank+mtime",
        })
    exact = []
    for sha, paths in (index.get("dedup_groups") or {}).items():
        exact.append({"sha256": sha, "paths": paths,
                      "resolution": "retain one canonical copy; record remaining paths as aliases"})
    near = copy.deepcopy(index.get("near_duplicates") or [])
    return lineages, exact, near


def _absolute_quote(hit, citation, quote):
    """Convert chunk-relative citation quote offsets to extracted-text offsets."""
    text = hit.get("text", "") or ""
    try:
        rel_start, rel_end = int(quote.get("offset", [0, 0])[0]), int(quote.get("offset", [0, 0])[1])
        raw = text[rel_start:rel_end]
    except Exception:
        rel_start = rel_end = 0
        raw = ""
    base = int(hit.get("start") or 0)
    raw = raw[:240]
    abs_offset = [base + rel_start, base + rel_start + len(raw)]
    return raw, abs_offset, bool(raw and raw.strip())


def _extracted_text_artifact(index, rel, out_dir, cache):
    """Materialize full extracted text so binary quotes have a inspectable artifact."""
    if rel in cache:
        return cache[rel]
    file_meta = next((f for f in index.get("files", []) if f.get("relpath") == rel), {})
    source = safe_source_path(index.get("root"), rel)
    ext = str(file_meta.get("ext") or "").lower()
    text = None
    if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"} and os.path.isfile(source):
        try:
            text = read_text_safe(source)
        except OSError:
            text = None
    if text is None:
        chunks = sorted((c for c in index.get("chunks", []) if c.get("file") == rel),
                        key=lambda c: (int(c.get("start") or 0), int(c.get("end") or 0)))
        parts, cursor = [], 0
        complete = True
        for chunk in chunks:
            start, end = int(chunk.get("start") or 0), int(chunk.get("end") or 0)
            body = chunk.get("text", "") or ""
            if start > cursor:
                complete = False
                break
            if end > cursor:
                skip = max(0, cursor - start)
                parts.append(body[skip:])
                cursor = end
        text = "".join(parts) if complete else None
    artifact = None
    if out_dir and text is not None:
        folder = os.path.join(out_dir, "evidence", "extracted_text")
        os.makedirs(folder, exist_ok=True)
        name = str(file_meta.get("sha256") or sha256_text(rel))[:12] + "_" + _safe_component(rel.replace("/", "__"), limit=80) + ".txt"
        artifact = os.path.join(folder, name)
        with open(artifact, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    artifact_hash = sha256_text(text) if text is not None else None
    expected_extraction_hash = (file_meta.get("extraction") or {}).get("sha256")
    artifact_hash_ok = bool(artifact_hash and expected_extraction_hash
                            and artifact_hash == expected_extraction_hash)
    result = (text, artifact, artifact_hash, artifact_hash_ok)
    cache[rel] = result
    return result


def evidence_references(index, hit_chunks, citations, hit_rows=None, limit=16, out_dir=None):
    """Build refs only from real quotes and verify file/chunk/offset consistency."""
    refs = []
    file_meta = {f["relpath"]: f for f in index.get("files", [])}
    row_map = {(h.get("file"), h.get("chunk_id")): h for h in (hit_rows or [])}
    artifact_cache = {}
    for citation_index, citation in enumerate(citations[:limit]):
        quotes = citation.get("quotes") or []
        if not quotes:
            continue
        hit = hit_chunks[citation_index] if citation_index < len(hit_chunks) else {}
        quote = quotes[0]
        raw, absolute, offset_ok = _absolute_quote(hit, citation, quote)
        if not offset_ok:
            continue
        rel = citation.get("file")
        row = row_map.get((rel, hit.get("chunk_id"))) or {}
        chunk_hash = sha256_text(hit.get("text", "") or "")
        expected_chunk_hash = row.get("sha256_chunk")
        chunk_hash_ok = bool(expected_chunk_hash) and chunk_hash == expected_chunk_hash
        source = None
        source_path_error = None
        try:
            source = safe_source_path(index.get("root"), rel)
        except ValueError as exc:
            source_path_error = str(exc)
        actual_sha = sha256_file(source) if source and os.path.isfile(source) else None
        expected_sha = citation.get("sha256_file") or (file_meta.get(rel) or {}).get("sha256")
        file_hash_ok = bool(actual_sha and expected_sha and actual_sha == expected_sha)
        try:
            artifact_text, artifact_path, artifact_hash, artifact_hash_ok = _extracted_text_artifact(
                index, rel, out_dir, artifact_cache)
        except ValueError as exc:
            artifact_text = artifact_path = artifact_hash = None
            artifact_hash_ok = False
            source_path_error = source_path_error or str(exc)
        slice_ok = bool(artifact_hash_ok and artifact_text is not None
                        and artifact_text[absolute[0]:absolute[1]] == raw)
        refs.append({
            "evidence_id": f"E{len(refs) + 1:02d}", "file": rel, "sha256": expected_sha,
            "sha256_verified": file_hash_ok,
            "chunk_id": hit.get("chunk_id") or citation.get("section_kind"),
            "sha256_chunk": chunk_hash, "chunk_hash_verified": chunk_hash_ok,
            "quote_verified": bool(offset_ok and chunk_hash_ok and slice_ok),
            "source_slice_verified": slice_ok,
            "extracted_text": os.path.relpath(artifact_path, out_dir).replace("\\", "/") if artifact_path and out_dir else None,
            "extracted_text_sha256": artifact_hash,
            "artifact_hash_verified": artifact_hash_ok,
            "source_path_error": source_path_error,
            "offset_space": "extracted_text_chars" if (file_meta.get(rel) or {}).get("ext", "").lower()
                            in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"} else "source_text_chars",
            "pages": citation.get("pages"), "printed_page_offset": citation.get("printed_page_offset"),
            "chunk_offset": citation.get("offset"), "offset": absolute,
            "anchor": (citation.get("anchor") or {}).get("first_sentence"),
            "quote": raw,
            "meaning": "查询词命中的原文候选；用于定位与复核，实质结论须由 Agent 阅读上下文后确认",
        })
    return refs


def _answer_content(profile, question, hit_rows, refs, catalog, organization, lineages, exact, near, unclassified):
    if profile == "classify":
        total_files = catalog["file_count"] + len(unclassified)
        lines = [f"已盘点 {total_files} 个输入文件，完成分类 {catalog['file_count']} 个、"
                 f"共 {catalog['bucket_count']} 个目录桶；识别 {len(exact)} 组精确重复与 {len(lineages)} 个版本族。"]
        if organization:
            lines.append(f"实体整理副本位于 organized/，规范文件 {organization['organized_files']} 个，"
                         f"重复别名 {len(organization['alias_rows'])} 条；源文件未修改。")
        for bucket in catalog["buckets"]:
            sample = ", ".join(m["relpath"] for m in bucket["members"][:3])
            lines.append(f"- [{bucket['bucket_id']}] {bucket['title']}：{bucket['member_count']} 个；示例 {sample}")
        if unclassified:
            lines.append(f"未分类 {len(unclassified)} 个，详见 unclassified/uncertainty.missing。")
        return "\n".join(lines)
    if profile == "review":
        if not refs:
            return "未检索到可支持综述的证据。保持空结果，不补造文献或结论；请扩展检索范围或检查输入。"
        lines = [f"已建立 {len(refs)} 条可回链候选证据，覆盖 {len({r['file'] for r in refs})} 个文件。"
                 "以下仅为证据候选，不把相关性误写为因果或共识；Agent 应据此完成筛选、主题归纳和带引用的综述。"]
        for ref in refs[:6]:
            quote = re.sub(r"\s+", " ", ref["quote"]).strip()
            lines.append(f"- [{ref['evidence_id']}] {ref['file']}：{quote[:180]}")
        return "\n".join(lines)
    if not hit_rows:
        return "在执行过滤约束后没有检索到相关章节。结果保持为空，未放宽条件，也未用文件名相似项填充。"
    lines = [f"检索完成：过滤后 {len(hit_rows)} 个候选章节进入排序，以下为前 {min(5, len(hit_rows))} 条定位。"
             "进入 evidence_refs 的条目均带源文件哈希、章节/页码、提取文本 offset 与逐字 quote；完整结果见 hits。"]
    for i, hit in enumerate(hit_rows[:5], 1):
        pages = ",".join(str(p) for p in (hit.get("pages") or [])) or "—"
        snippet = re.sub(r"\s+", " ", hit.get("snippet") or "").strip()
        lines.append(f"{i}. {hit.get('file')} · pages={pages} · offset={hit.get('offset')} · {snippet[:180]}")
    return "\n".join(lines)


def _contract_default(contract):
    if isinstance(contract, str):
        kind = contract.strip().lower()
        if kind in ("array", "list"):
            return []
        if kind in ("object", "dict"):
            return {}
        if kind in ("integer", "number"):
            return 0
        if kind in ("boolean", "bool"):
            return False
        if kind == "string":
            return ""
        try:
            return _contract_default(json.loads(contract))
        except Exception:
            return None
    if isinstance(contract, dict):
        if "default" in contract:
            return copy.deepcopy(contract["default"])
        if ("properties" in contract and
                set(contract).issubset({"properties", "type", "description", "required", "additionalProperties"})):
            return _contract_default(contract["properties"])
        if (isinstance(contract.get("fields"), list) and
                set(contract).issubset({"fields", "type", "description", "required"})):
            return _contract_default(contract["fields"])
        kind = str(contract.get("type") or "").lower()
        if kind in ("array", "list"):
            return []
        if kind in ("object", "dict"):
            return _contract_default(contract.get("properties") or {k: v for k, v in contract.items() if k != "type"})
        if kind in ("integer", "number"):
            return 0
        if kind in ("boolean", "bool"):
            return False
        if kind == "string":
            return ""
        return {k: _contract_default(v) for k, v in contract.items() if k != "type"}
    if isinstance(contract, list):
        if not contract:
            return []
        if all(isinstance(item, str) for item in contract):
            return {item: None for item in contract}
        if all(isinstance(item, dict) and any(k in item for k in ("name", "field", "key"))
               for item in contract):
            out = {}
            for item in contract:
                key = item.get("name") or item.get("field") or item.get("key")
                if "schema" in item:
                    out[key] = _contract_default(item["schema"])
                elif "default" in item:
                    out[key] = copy.deepcopy(item["default"])
                elif "type" in item:
                    out[key] = _contract_default(item["type"])
                else:
                    out[key] = None
            return out
        if len(contract) == 1 and isinstance(contract[0], dict):
            return [_contract_default(contract[0])]
        return []
    return None


def _deep_fill(target, defaults):
    if isinstance(defaults, dict) and isinstance(target, dict):
        for key, value in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(value)
            elif target[key] is None and value is not None:
                target[key] = copy.deepcopy(value)
            else:
                _deep_fill(target[key], value)
    elif isinstance(defaults, list) and defaults and isinstance(defaults[0], dict) and isinstance(target, list):
        for item in target:
            if isinstance(item, dict):
                _deep_fill(item, defaults[0])


def _missing_contract_paths(target, defaults, prefix=""):
    missing = []
    if isinstance(defaults, dict):
        if not isinstance(target, dict):
            return [prefix or "$"]
        for key, value in defaults.items():
            path = f"{prefix}.{key}" if prefix else key
            if key not in target:
                missing.append(path)
            else:
                missing.extend(_missing_contract_paths(target[key], value, path))
    elif isinstance(defaults, list) and defaults and isinstance(defaults[0], dict):
        if not isinstance(target, list):
            return [prefix or "$"]
        for i, item in enumerate(target):
            missing.extend(_missing_contract_paths(item, defaults[0], f"{prefix}[{i}]"))
    elif defaults is not None and target is None:
        missing.append(prefix or "$")
    return missing


def fill_output_contract(result, case):
    """Recursively preserve original output_contract keys and report gaps."""
    contract = (case.get("output_contract") or case.get("result_schema") or
                case.get("output_schema") or case.get("required_output_fields"))
    defaults = _contract_default(contract)
    if not isinstance(defaults, dict):
        result["output_contract_preserved"] = False
        result["output_contract_validation"] = {"missing": [], "error": "top-level contract is not a field object"}
        return result
    _deep_fill(result, defaults)
    missing = _missing_contract_paths(result, defaults)
    result["output_contract_preserved"] = not missing
    result["output_contract_validation"] = {"missing": missing}
    return result


def finalize_result(index, case, task_md, question, hit_chunks, hit_rows, citations,
                    filter_audit, keep, out_dir, work_dir, ledger_path=None,
                    profile=None, index_path=None, plans=None):
    profile = profile or infer_profile(case, task_md)
    validate_index_paths(index)
    catalog, assignments, unclassified = classify_files(index, case)
    organization = None
    if profile == "classify":
        organization = organize_library(index, catalog, assignments, out_dir, case, task_md, ledger_path)
        for rel, target in ((op["source"], op["target"]) for op in organization["operations"] if op.get("status") == "ok"):
            if rel in assignments:
                assignments[rel]["target"] = "organized/" + target
        for bucket in catalog["buckets"]:
            for member in bucket["members"]:
                member["target"] = assignments.get(member["relpath"], {}).get("target")

    lineages, exact, near = version_lineage(index)
    refs = evidence_references(index, hit_chunks, citations, hit_rows=hit_rows, out_dir=out_dir)
    sources = []
    for f in index.get("files", []):
        row = {k: f.get(k) for k in ("relpath", "name", "ext", "size", "mtime", "sha256",
                                      "permissions", "readable", "writable", "is_symlink",
                                      "symlink_target", "extracted_status", "extraction", "meta")}
        row["bucket"] = assignments.get(f["relpath"], {}).get("bucket_id")
        row["target"] = assignments.get(f["relpath"], {}).get("target")
        sources.append(row)

    missing, conflicts, limits, recovery = [], [], [], []
    in_scope = set(keep) if profile != "classify" else {f.get("relpath") for f in index.get("files", [])}
    for item in index.get("needs_tool", []):
        if item.get("file") not in in_scope:
            continue
        missing.append({"file": item.get("file"), "reason": item.get("why"),
                        "kind": "text extraction unavailable"})
        recovery.append({"file": item.get("file"), "status": "pending",
                         "action": "call official pdf/office/ocr tool, then rerun with --force",
                         "reversible": True})
    for item in index.get("errors", []):
        if item.get("file") not in in_scope:
            continue
        missing.append({"file": item.get("file"), "reason": item.get("error"), "kind": "scan error"})
    for item in unclassified:
        missing.append({"file": item.get("relpath"), "reason": item.get("why"), "kind": "unclassified"})
    if organization:
        for failure in organization["failures"]:
            missing.append({"file": failure["file"], "reason": failure["error"], "kind": "organization failure"})
            recovery.append(failure)
    if near:
        limits.append(f"{len(near)} 组近重复/版本变体仅按标题族与体积启发式识别，需结合行政版本说明确认")
    if profile == "review":
        limits.append("确定性首版提供候选证据矩阵；跨文献主题归纳与综述正文需 Agent 读证后增补")
    if not refs:
        limits.append("无查询词命中；未生成事实性答案")
    if any(h.get("has_more") for h in hit_chunks):
        limits.append("部分命中章节含续块；证据文件保存当前块，需同时阅读相邻 chunk 或回源文")

    fully_verified = sum(1 for r in refs if r.get("sha256_verified")
                         and r.get("chunk_hash_verified") and r.get("quote_verified")
                         and r.get("artifact_hash_verified"))
    evidence_rate = round(fully_verified / max(len(refs), 1), 4) if refs else 0.0
    if refs and fully_verified != len(refs):
        limits.append("部分证据的文件/chunk/quote 校验未全部通过，已在 evidence_refs 逐条标记")
    hard_failure = bool(index.get("errors")) and not index.get("files")
    if organization:
        status = "ok" if not missing and not organization["failures"] else "partial"
    elif profile == "review":
        status = "partial" if refs or not hard_failure else "failed"
    else:
        status = "ok" if refs and len(refs) == len(hit_rows) and fully_verified == len(refs) and not missing else ("failed" if hard_failure else "partial")

    answer = {
        "content": _answer_content(profile, question, hit_rows, refs, catalog, organization,
                                    lineages, exact, near, unclassified),
        "evidence_refs": refs,
        "profile": profile,
    }
    operation_evidence = {
        "source_root": index.get("root"), "source_files_total": len(index.get("files", [])),
        "filtered_files": len(keep), "index_chunks": len(index.get("chunks", [])),
        "index_path": index_path, "ledger": ledger_path,
        "scan_summary": index.get("summary", {}),
        "run_recipe": [
            "python -I scan_index.py --root <library> --out <work>/index.json --ledger <work>/ledger.json",
            "python -I run_case.py --case <case.json> --index <work>/index.json --work <work> --out <out>",
        ],
        "hash_verification": {"evidence_refs": len(refs), "verified": fully_verified,
                              "rate": evidence_rate,
                              "method": "source sha256 + chunk sha256 + extracted-text quote/offset slice"},
        "originals_modified": False,
    }
    if organization:
        operation_evidence.update({k: organization[k] for k in
                                   ("path", "manifest", "aliases", "rollback", "organized_files",
                                    "alias_rows", "failures")})

    screening = []
    hit_files = {h.get("file") for h in hit_rows}
    for f in index.get("files", []):
        screening.append({"file": f["relpath"], "sha256": f.get("sha256"),
                          "status": "candidate" if f["relpath"] in hit_files else "not_retrieved",
                          "reason": "appears in ranked evidence" if f["relpath"] in hit_files
                                    else "no query-term evidence in deterministic pass; not proof of irrelevance"})
    evidence_matrix = [{"evidence_id": r["evidence_id"], "file": r["file"], "sha256": r["sha256"],
                        "pages": r["pages"], "offset": r["offset"], "offset_space": r["offset_space"],
                        "extracted_text": r["extracted_text"], "quote": r["quote"],
                        "quote_verified": r["quote_verified"], "artifact_hash_verified": r["artifact_hash_verified"],
                        "supports": "candidate evidence; agent must assess claim support",
                        "strength": "unassessed"} for r in refs]

    return {
        "status": status,
        "answer": answer,
        "sources": sources,
        "uncertainty": {"missing": missing, "conflicts": conflicts, "limits": limits},
        "failure_recovery": recovery,
        "change_records": (organization or {}).get("operations", []),
        "catalog": catalog,
        "version_lineage": lineages,
        "exact_duplicates": exact,
        "near_duplicates": near,
        "unclassified": unclassified,
        "operation_evidence": operation_evidence,
        "search_results": [dict(h, pages=h.get("pages")) for h in hit_rows],
        "evidence_matrix": evidence_matrix,
        "screening_log": screening,
        "review_draft": answer["content"] if profile == "review" else None,
        "query_plans": plans or [],
        "provenance": {
            "engine_revision": ENGINE_REVISION, "generated_at": _now(),
            "input_case_digest": sha256_text(json.dumps(case, ensure_ascii=False, sort_keys=True)),
            "task_md_digest": sha256_text(task_md) if task_md else None,
            "index_digest": sha256_file(index_path) if index_path else None,
            "profile": profile, "deterministic_first_delivery": True,
            "delivery_contract_version": 2,
            "source_files_modified": False,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Upgrade an existing result.json with a complete deterministic delivery")
    ap.add_argument("--result", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True, help="same output directory as result.json")
    ap.add_argument("--work", default=None)
    ap.add_argument("--profile", choices=("auto", "classify", "search", "review", "data", "code", "experiment", "audit"), default="auto")
    args = ap.parse_args(argv)
    # Lazy import avoids a run_case<->finalize import cycle.
    from run_case import load_case
    case, task_md = load_case(args.case)
    index = json.load(open(args.index, encoding="utf-8"))
    result = json.load(open(args.result, encoding="utf-8"))
    hit_rows = result.get("hits") or []
    by_key = {(c.get("file"), c.get("chunk_id")): c for c in index.get("chunks", [])}
    hit_chunks = [by_key.get((h.get("file"), h.get("chunk_id")), {}) for h in hit_rows]
    profile = None if args.profile == "auto" else args.profile
    extra = finalize_result(index, case, task_md, result.get("question"), hit_chunks, hit_rows,
                            result.get("citations") or [], result.get("filter_audit") or [],
                            {f["relpath"] for f in index.get("files", [])}, args.out,
                            args.work or args.out, profile=profile, index_path=args.index,
                            plans=result.get("query_plans"))
    result.update(extra)
    fill_output_contract(result, case)
    write_json(args.result, result)
    print(json.dumps({"status": result["status"], "profile": profile or infer_profile(case, task_md),
                      "sources": len(result["sources"]), "evidence_refs": len(result["answer"]["evidence_refs"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
