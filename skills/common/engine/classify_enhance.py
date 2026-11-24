# -*- coding: utf-8 -*-
"""Deterministic classification enhancements for the document-library repair case.

The module deliberately uses only the Python standard library and treats all
input as untrusted data.  It never mutates the source library.  The only write
API is :func:`build_before_after`, which creates new JSON snapshot files under
its ``out_dir`` and refuses to overwrite existing files.
"""
import hashlib
import json
import os
import posixpath
import re
import stat as stat_module
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


__all__ = [
    "extract_admin_notes",
    "generate_tags",
    "permission_audit",
    "build_before_after",
    "deterministic_manifest_digest",
    "build_classification_report",
]


# ---------------------------------------------------------------------------
# Small defensive helpers
# ---------------------------------------------------------------------------

def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _norm_relpath(value: Any) -> str:
    text = _as_text(value).strip().replace("\\", "/")
    if not text:
        return ""
    normalized = posixpath.normpath(text)
    return "" if normalized == "." else normalized


def _safe_relpath(value: Any) -> Optional[str]:
    rel = _norm_relpath(value)
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None
    if any(part == ".." for part in rel.split("/")):
        return None
    return rel


def _is_within(root: str, path: str) -> bool:
    try:
        root_real = os.path.normcase(os.path.realpath(root))
        path_real = os.path.normcase(os.path.realpath(path))
        return path_real == root_real or path_real.startswith(root_real + os.sep)
    except Exception:
        return False


def _sha256_file(path: str) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json_file(path: str) -> Tuple[Any, Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:
        return None, str(exc)


def _write_json_new(out_dir: str, filename: str, value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Create a JSON file exclusively; never replace an existing path."""
    path = os.path.join(out_dir, filename)
    if os.path.lexists(path):
        return path, "exists"
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    created = False
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(payload)
        return path, None
    except FileExistsError:
        return path, "exists"
    except Exception as exc:
        if created:
            try:
                os.remove(path)
            except OSError:
                pass
        return path, str(exc)


def _bool_or_none(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Administrative version notes
# ---------------------------------------------------------------------------

_ADMIN_MARKER_RE = re.compile(
    r"(?i)\b(?:administrative|admin|metadata|version\s+(?:note|record|info)|"
    r"revision\s+note|alias(?:es)?|canonical|preferred|authoritative|"
    r"supersedes|superseded|replaces|replaced)\b|"
    r"正式版|主版本|规范版|版本说明|版本备注|行政说明|元数据|别名|副本|替代|被替代"
)
_EXPLICIT_NOTE_RE = re.compile(
    r"(?i)\b(?:administrative|admin(?:istrative)?\s+note|metadata(?:\s+note)?|"
    r"version\s+(?:note|record|info)|revision\s+note)\b|"
    r"行政(?:版本)?说明|版本说明|版本备注|元数据(?:说明)?"
)
_VERSION_NOTE_MARKER_RE = re.compile(
    r"(?i)\bversion\s+(?:note|record|info)\b|版本说明|版本备注"
)
_CANONICAL_RE = re.compile(
    r"(?i)(?:canonical|preferred|authoritative|正式版|主版本|规范版)"
    r"(?:\s+(?:file|version|copy|文档|文件|版本))?\s*(?:is|are|=|:|：|为|是)\s*(.+)$"
)
_CANONICAL_SUFFIX_RE = re.compile(
    r"(?i)(.+?)\s+(?:is|are|为|是)\s+(?:the\s+)?"
    r"(?:canonical|preferred|authoritative|正式版|主版本|规范版)\b"
)
_ALIAS_MARKER_RE = re.compile(r"(?i)\b(?:alias|aliases|copy|copies)\b|别名|副本")
_SUPERSEDE_RE = re.compile(
    r"(?i)\b(?:supersedes|superseded|replaces|replaced)\b|替代|被替代"
)
_CURRENT_ALIAS_RE = re.compile(
    r"(?i)(?:this|current)\s+file.*alias|本文件.*别名|本文档.*别名"
)
_REF_RES = [
    re.compile(r"`([^`\n]+)`"),
    re.compile(r'"([^"\n]+)"'),
    re.compile(r"'([^'\n]+)'"),
]
_REF_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?(?:[^\s`\"<>|,，;；:：]+[\\/])*"
    r"[^\s`\"<>|,，;；:：]+\.[A-Za-z0-9]{1,12}"
)


def _clean_ref(value: Any) -> str:
    text = _as_text(value).strip()
    text = text.strip(" \t\r\n`'\"()[]{}<>.,，。；：:、")
    return text[:1000]


def _looks_like_ref(value: str) -> bool:
    return bool(value and ("/" in value or "\\" in value or re.search(r"\.[A-Za-z0-9]{1,12}$", value)))


def _extract_refs(text: str) -> List[str]:
    """Extract explicit file-like references, preserving their textual order."""
    found: List[Tuple[int, str]] = []
    for pattern in _REF_RES:
        for match in pattern.finditer(text):
            candidate = _clean_ref(match.group(1))
            if _looks_like_ref(candidate):
                found.append((match.start(1), candidate))
    for match in _REF_TOKEN_RE.finditer(text):
        candidate = _clean_ref(match.group(0))
        if _looks_like_ref(candidate):
            found.append((match.start(), candidate))
    found.sort(key=lambda item: item[0])
    output: List[str] = []
    seen = set()
    for _, candidate in found:
        normalized = _norm_relpath(candidate)
        key = normalized.casefold()
        if key and key not in seen:
            seen.add(key)
            output.append(normalized)
    return output


def _file_lookup(index: Dict[str, Any]) -> Dict[str, List[str]]:
    lookup: Dict[str, List[str]] = {}

    def add(key: str, rel: str) -> None:
        key = key.strip().casefold()
        if key and rel not in lookup.setdefault(key, []):
            lookup[key].append(rel)

    for row in _as_list(index.get("files")):
        row = _as_dict(row)
        rel = _safe_relpath(row.get("relpath"))
        if not rel:
            continue
        name = _as_text(row.get("name") or posixpath.basename(rel))
        add(rel, rel)
        add(name, rel)
        stem = posixpath.splitext(name)[0]
        if stem:
            add(stem, rel)
    return lookup


def _resolve_ref(ref: str, lookup: Dict[str, List[str]]) -> Optional[str]:
    normalized = _norm_relpath(ref)
    if not normalized:
        return None
    matches = lookup.get(normalized.casefold()) or []
    if len(matches) == 1:
        return matches[0]
    basename = posixpath.basename(normalized)
    matches = lookup.get(basename.casefold()) or []
    if len(matches) == 1:
        return matches[0]
    stem = posixpath.splitext(basename)[0]
    matches = lookup.get(stem.casefold()) or []
    if len(matches) == 1:
        return matches[0]
    return None


def _identity_groups(index: Dict[str, Any]) -> List[set]:
    groups: List[set] = []
    by_sha: Dict[str, set] = {}
    for row in _as_list(index.get("files")):
        row = _as_dict(row)
        rel = _safe_relpath(row.get("relpath"))
        sha = _as_text(row.get("sha256"))
        if rel and sha:
            by_sha.setdefault(sha, set()).add(rel)
    for normalized in by_sha.values():
        if len(normalized) > 1:
            groups.append(normalized)
    for paths in _as_dict(index.get("dedup_groups")).values():
        normalized = {_norm_relpath(path) for path in _as_list(paths)}
        normalized.discard("")
        if len(normalized) > 1:
            groups.append(normalized)
    for family in _as_dict(index.get("families")).values():
        family = _as_dict(family)
        normalized = {
            _norm_relpath(_as_dict(member).get("relpath"))
            for member in _as_list(family.get("members"))
        }
        normalized.discard("")
        if len(normalized) > 1:
            groups.append(normalized)
    return groups


def _iter_chunk_lines(index: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    chunks = [chunk for chunk in _as_list(index.get("chunks")) if isinstance(chunk, dict)]
    chunks.sort(key=lambda chunk: (
        _as_text(chunk.get("file")),
        _int_or_zero(chunk.get("start")),
        _as_text(chunk.get("chunk_id")),
    ))
    for chunk in chunks:
        text = _as_text(chunk.get("text"))
        base = _int_or_zero(chunk.get("start"))
        cursor = 0
        for raw_line in text.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            yield {
                "file": _as_text(chunk.get("file")),
                "chunk_id": _as_text(chunk.get("chunk_id")),
                "chunk_text": text,
                "line": line,
                "chunk_offset": cursor,
                "evidence_offset": base + cursor,
                "evidence_end": base + cursor + len(line),
            }
            cursor += len(raw_line)


def extract_admin_notes(index: Any) -> Dict[str, Any]:
    """Parse explicit administrative/version facts from indexed chunk text.

    No canonical file is inferred from names, hashes, timestamps or variant
    ranks.  Only statements that explicitly declare canonical/preferred/
    authoritative status, aliases, or supersession relationships are used.
    """
    index = _as_dict(index)
    lookup = _file_lookup(index)
    notes: List[Dict[str, Any]] = []
    canonical_claims: List[Dict[str, Any]] = []
    alias_claims: List[Dict[str, Any]] = []
    version_relations: List[Dict[str, Any]] = []
    unresolved_refs: List[Dict[str, Any]] = []
    seen_notes = set()

    for item in _iter_chunk_lines(index):
        line = item["line"].strip()
        if not line:
            continue
        refs = _extract_refs(line)
        has_marker = bool(_ADMIN_MARKER_RE.search(line))
        explicit_note = bool(_EXPLICIT_NOTE_RE.search(line))
        if not has_marker and len(refs) < 2:
            continue
        if not explicit_note and not refs:
            # A body sentence such as "Superseded paragraph kept ..." is not a
            # file-level administrative fact.
            continue

        resolved_refs = [_resolve_ref(ref, lookup) for ref in refs]
        for ref, resolved in zip(refs, resolved_refs):
            if resolved is None:
                unresolved_refs.append({
                    "file": item["file"],
                    "reference": ref,
                    "evidence_offset": item["evidence_offset"],
                    "raw": item["line"],
                })

        negated = bool(re.search(
            r"(?i)\b(?:not|non)[-\s]+(?:an?\s+)?(?:canonical|preferred|authoritative|alias|supersedes|replaces)\b|"
            r"非正式版|不是正式版|不是主版本|不是别名|不替代|未被替代",
            line,
        ))
        canonical_target: Optional[str] = None
        match = None if negated else _CANONICAL_RE.search(line)
        if match:
            after = _extract_refs(match.group(1))
            if after:
                canonical_target = after[0]
        else:
            suffix = None if negated else _CANONICAL_SUFFIX_RE.search(line)
            if suffix:
                before = _extract_refs(suffix.group(1))
                if before:
                    canonical_target = before[-1]
            elif not negated and len(refs) == 1 and re.search(
                    r"(?i)canonical|preferred|authoritative|正式版|主版本|规范版", line):
                canonical_target = refs[0]

        alias_relation: Optional[Tuple[str, str]] = None
        if not negated and _ALIAS_MARKER_RE.search(line):
            if len(refs) >= 2:
                alias_relation = (refs[0], refs[1])
            elif len(refs) == 1 and _CURRENT_ALIAS_RE.search(line) and item["file"]:
                alias_relation = (item["file"], refs[0])

        supersede_relation: Optional[Tuple[str, str]] = None
        super_match = None if negated else _SUPERSEDE_RE.search(line)
        if super_match:
            if len(refs) >= 2:
                if "被" in line and "替代" in line:
                    older, newer = refs[0], refs[1]
                else:
                    before_refs = _extract_refs(line[:super_match.start()])
                    after_refs = _extract_refs(line[super_match.end():])
                    left = before_refs[-1] if before_refs else refs[0]
                    right = after_refs[0] if after_refs else refs[1]
                    phrase = line[super_match.start():super_match.end()].casefold()
                    passive = ("superseded by" in line.casefold()
                               or "replaced by" in line.casefold()
                               or phrase in {"superseded", "replaced", "被替代"})
                    older, newer = (left, right) if passive else (right, left)
                supersede_relation = (older, newer)
            elif len(refs) == 1 and item["file"]:
                if re.search(r"(?i)^\s*(?:superseded|replaced)\s+by\s*[:：]", line):
                    supersede_relation = (item["file"], refs[0])
                elif re.search(r"(?i)^\s*(?:supersedes|replaces|替代)\s*[:：]", line):
                    supersede_relation = (refs[0], item["file"])

        if canonical_target:
            kind = "canonical"
            target = canonical_target
            subject = item["file"] if _CURRENT_ALIAS_RE.search(line) else None
        elif alias_relation:
            kind = "alias"
            subject, target = alias_relation
        elif supersede_relation:
            kind = "supersedes"
            subject, target = supersede_relation
        elif explicit_note:
            kind = "metadata" if re.search(r"(?i)metadata|元数据", line) else "version_note"
            subject, target = None, None
        else:
            continue

        note_key = (item["file"], item["evidence_offset"], kind, item["line"])
        if note_key in seen_notes:
            continue
        seen_notes.add(note_key)
        note = {
            "file": item["file"],
            "chunk_id": item["chunk_id"],
            "kind": kind,
            "raw": item["line"],
            "text": item["line"],
            "chunk_text": item["chunk_text"],
            "chunk_offset": item["chunk_offset"],
            "evidence_offset": item["evidence_offset"],
            "evidence_end": item["evidence_end"],
            "subject": subject,
            "target": target,
            "references": refs,
            "resolved_references": [ref for ref in resolved_refs if ref],
        }
        notes.append(note)
        if kind != "version_note" and _VERSION_NOTE_MARKER_RE.search(line):
            version_note = dict(note)
            version_note["kind"] = "version_note"
            version_note["subject"] = None
            version_note["target"] = None
            notes.append(version_note)

        if canonical_target:
            canonical_claims.append({
                "target_ref": canonical_target,
                "target": _resolve_ref(canonical_target, lookup) or _norm_relpath(canonical_target),
                "note": note,
            })
        if alias_relation:
            alias_claims.append({
                "alias_ref": alias_relation[0],
                "canonical_ref": alias_relation[1],
                "alias": _resolve_ref(alias_relation[0], lookup) or _norm_relpath(alias_relation[0]),
                "canonical": _resolve_ref(alias_relation[1], lookup) or _norm_relpath(alias_relation[1]),
                "note": note,
            })
        if supersede_relation:
            older_ref, newer_ref = supersede_relation
            relation = {
                "older_ref": older_ref,
                "newer_ref": newer_ref,
                "older": _resolve_ref(older_ref, lookup) or _norm_relpath(older_ref),
                "newer": _resolve_ref(newer_ref, lookup) or _norm_relpath(newer_ref),
                "evidence_offset": item["evidence_offset"],
                "file": item["file"],
                "raw": item["line"],
            }
            version_relations.append(relation)
            note["version_relation"] = relation

    notes.sort(key=lambda note: (note["file"], note["evidence_offset"], note["kind"], note["raw"]))

    # Alias graph, with ambiguity retained as a conflict rather than guessed.
    alias_targets: Dict[str, set] = {}
    for claim in alias_claims:
        if claim["alias"] and claim["canonical"] and claim["alias"] != claim["canonical"]:
            alias_targets.setdefault(claim["alias"], set()).add(claim["canonical"])

    conflicts: List[Dict[str, Any]] = []
    alias_map: Dict[str, str] = {}
    for alias in sorted(alias_targets):
        targets = sorted(alias_targets[alias])
        if len(targets) == 1:
            alias_map[alias] = targets[0]
        else:
            conflicts.append({
                "kind": "alias_conflict",
                "alias": alias,
                "canonicals": targets,
                "reason": "one alias has multiple explicitly declared canonical targets",
            })

    # Resolve transitive aliases and detect cycles.
    def resolve_alias(start: str) -> Tuple[Optional[str], Optional[List[str]]]:
        seen: List[str] = []
        current = start
        while current in alias_map and alias_map[current] != current:
            if current in seen:
                return None, seen + [current]
            seen.append(current)
            current = alias_map[current]
        return current, None

    for alias in list(alias_map):
        resolved, cycle = resolve_alias(alias)
        if cycle:
            conflicts.append({
                "kind": "alias_cycle",
                "aliases": cycle,
                "reason": "alias declarations form a cycle",
            })
        elif resolved:
            alias_map[alias] = resolved

    canonical_overrides: Dict[str, str] = {}
    for claim in canonical_claims:
        target = claim["target"]
        if target:
            canonical_overrides[target] = target
    for alias, canonical in alias_map.items():
        canonical_overrides[alias] = canonical

    # Canonical claims are conflicting only when they refer to the same
    # identity group (same hash/family).  Unrelated documents may each have a
    # legitimate canonical file.
    groups = _identity_groups(index)
    claims_by_target: Dict[str, List[Dict[str, Any]]] = {}
    for claim in canonical_claims:
        claims_by_target.setdefault(claim["target"], []).append(claim)
    reported_groups = set()
    for group in groups:
        targets = sorted(target for target in claims_by_target if target in group)
        if len(targets) > 1:
            key = tuple(targets)
            if key not in reported_groups:
                reported_groups.add(key)
                conflicts.append({
                    "kind": "canonical_conflict",
                    "canonicals": targets,
                    "reason": "multiple files in one identity group are declared canonical",
                    "evidence_offsets": [
                        claim["note"]["evidence_offset"]
                        for target in targets for claim in claims_by_target[target]
                    ],
                })

    # A declared canonical target that is itself an alias to another file is
    # contradictory unless both declarations resolve to the same final file.
    declared_canonicals = {claim["target"] for claim in canonical_claims if claim["target"]}
    for target in sorted(declared_canonicals):
        if target in alias_map and alias_map[target] != target:
            conflicts.append({
                "kind": "canonical_alias_conflict",
                "file": target,
                "canonical": canonical_overrides.get(target),
                "alias_target": alias_map[target],
                "reason": "a file is declared canonical and also an alias of another file",
            })

    for unresolved in unresolved_refs:
        conflicts.append({
            "kind": "unresolved_reference",
            "file": unresolved["file"],
            "reference": unresolved["reference"],
            "evidence_offset": unresolved["evidence_offset"],
            "raw": unresolved["raw"],
            "reason": "declared reference does not resolve uniquely to an indexed file",
        })

    conflicts.sort(key=lambda item: (item.get("kind", ""), item.get("file", ""),
                                     item.get("reference", ""), item.get("evidence_offset", 0)))
    return {
        "notes": notes,
        "alias_map": dict(sorted(alias_map.items())),
        "canonical_overrides": dict(sorted(canonical_overrides.items())),
        "conflicts": conflicts,
        "version_relations": version_relations,
        "summary": {
            "notes": len(notes),
            "canonical_claims": len(canonical_claims),
            "alias_claims": len(alias_claims),
            "aliases": len(alias_map),
            "supersession_relations": len(version_relations),
            "conflicts": len(conflicts),
        },
    }


# ---------------------------------------------------------------------------
# Controlled tags
# ---------------------------------------------------------------------------

_TAG_VOCABULARY: List[Tuple[str, Sequence[str]]] = [
    ("graph neural network", ("gnn", "gnns", "graph neural network", "graph neural networks", "图神经网络")),
    ("graph convolutional network", ("gcn", "gcns", "graph convolutional network", "graph convolutional networks", "图卷积网络")),
    ("message passing neural network", ("mpnn", "mpnns", "message passing neural network", "message-passing neural network", "消息传递神经网络")),
    ("message passing", ("message passing", "message-passing", "消息传递")),
    ("molecular property prediction", ("molecular property prediction", "molecular-property prediction", "分子性质预测")),
    ("molecular graph", ("molecular graph", "molecular graphs", "分子图")),
    ("quantum chemistry", ("quantum chemistry", "量子化学")),
    ("deep learning", ("deep learning", "深度学习")),
    ("machine learning", ("machine learning", "机器学习")),
    ("survey", ("survey", "review", "综述")),
    ("benchmark", ("benchmark", "benchmarks", "基准", "基准测试")),
    ("dataset", ("dataset", "data set", "数据集")),
    ("abstract", ("abstract", "摘要")),
    ("introduction", ("introduction", "引言")),
    ("methods", ("methods", "methodology", "方法")),
    ("results", ("results", "结果")),
    ("limitations", ("limitations", "limitation", "限制", "局限性")),
    ("conclusion", ("conclusion", "conclusions", "结论")),
    ("preprint", ("preprint", "预印本")),
    ("draft", ("draft", "草稿", "修改稿")),
    ("final", ("final", "final version", "正式版", "终稿")),
    ("metadata", ("metadata", "元数据")),
    ("version note", ("version note", "版本说明", "版本备注")),
    ("administrative note", ("administrative note", "admin note", "行政说明", "行政版本说明")),
]

_SECTION_TAGS = {
    "abstract": "abstract",
    "摘要": "abstract",
    "introduction": "introduction",
    "引言": "introduction",
    "methods": "methods",
    "methodology": "methods",
    "方法": "methods",
    "results": "results",
    "结果": "results",
    "limitations": "limitations",
    "limitation": "limitations",
    "限制": "limitations",
    "局限性": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "结论": "conclusion",
}
_STOP_TAGS = {
    "a", "an", "and", "or", "the", "of", "for", "to", "in", "on", "with",
    "研究", "文献", "资料", "文件", "文档", "paper", "study",
}


def _normalise_tag(value: Any) -> str:
    return re.sub(r"\s+", " ", _as_text(value).strip().casefold())


def _split_keywords(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [_as_text(item).strip() for item in value if _as_text(item).strip()]
    text = _as_text(value).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[;；,，\n]+", text) if part.strip()]


def _merge_tag_vocabulary(case: Dict[str, Any]) -> Tuple[Dict[str, str], set, set]:
    synonyms: Dict[str, str] = {}
    allowed = set()

    def add(canonical: str, aliases: Sequence[str]) -> None:
        canonical = _normalise_tag(canonical)
        if not canonical:
            return
        synonyms[canonical] = canonical
        allowed.add(canonical)
        for alias in aliases:
            normalized = _normalise_tag(alias)
            if normalized:
                synonyms[normalized] = canonical
                allowed.add(canonical)

    for canonical, aliases in _TAG_VOCABULARY:
        add(canonical, aliases)

    custom = case.get("tag_synonyms") or case.get("synonyms")
    if isinstance(custom, dict):
        for alias, canonical in custom.items():
            alias_norm = _normalise_tag(alias)
            canonical_norm = _normalise_tag(canonical)
            if alias_norm and canonical_norm:
                synonyms[alias_norm] = canonical_norm
                allowed.add(canonical_norm)

    vocabulary = case.get("tag_vocabulary") or case.get("controlled_tags")
    if isinstance(vocabulary, dict):
        for canonical, aliases in vocabulary.items():
            if isinstance(aliases, str):
                aliases = [aliases]
            add(canonical, _as_list(aliases))
    elif isinstance(vocabulary, list):
        for canonical in vocabulary:
            add(canonical, [])

    for key in ("allowed_tags", "tags"):
        for tag in _as_list(case.get(key)):
            normalized = _normalise_tag(tag)
            if normalized:
                allowed.add(normalized)
                synonyms.setdefault(normalized, normalized)
    return synonyms, allowed, set()


def _find_vocab_matches(text: str, synonyms: Dict[str, str], source: str,
                        base_offset: int, priority: int, chunk_id: str = "") -> List[Dict[str, Any]]:
    if not text:
        return []
    candidates: List[Dict[str, Any]] = []
    for alias, canonical in synonyms.items():
        if not alias or alias == canonical and len(alias) < 3:
            continue
        try:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _+\-]*", alias):
                pattern = re.compile(
                    r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])",
                    re.I,
                )
            else:
                pattern = re.compile(re.escape(alias), re.I)
        except re.error:
            continue
        for match in pattern.finditer(text):
            candidates.append({
                "tag": canonical,
                "source": source,
                "evidence_offset": base_offset + match.start(),
                "evidence_end": base_offset + match.end(),
                "evidence_text": match.group(0),
                "chunk_id": chunk_id,
                "priority": priority,
            })
            break
    return candidates


def generate_tags(file_meta: Any, metadata: Any, chunks: Any, case: Any) -> List[Dict[str, Any]]:
    """Generate at most twelve evidence-backed, synonym-normalised tags."""
    file_meta = _as_dict(file_meta)
    metadata = _as_dict(metadata)
    case = _as_dict(case)
    synonyms, allowed, _ = _merge_tag_vocabulary(case)
    rel = _as_text(file_meta.get("relpath"))
    candidates: List[Dict[str, Any]] = []

    title = _as_text(metadata.get("title") or file_meta.get("title"))
    if title:
        candidates.extend(_find_vocab_matches(title, synonyms, "title", 0, 0))

    abstract = _as_text(metadata.get("abstract") or metadata.get("summary"))
    if abstract:
        candidates.extend(_find_vocab_matches(abstract, synonyms, "abstract", 0, 2))

    keyword_values: List[str] = []
    for key in ("keywords", "subjects", "tags"):
        keyword_values.extend(_split_keywords(metadata.get(key)))
    has_explicit_vocabulary = any(
        isinstance(case.get(key), (dict, list))
        for key in ("tag_synonyms", "synonyms", "tag_vocabulary", "controlled_tags", "allowed_tags")
    )
    allow_unknown_keywords = bool(case.get(
        "allow_keyword_tags", not has_explicit_vocabulary))
    for keyword in keyword_values:
        normalized = _normalise_tag(keyword)
        if not normalized or normalized in _STOP_TAGS:
            continue
        canonical = synonyms.get(normalized)
        if canonical is None and normalized in allowed:
            canonical = normalized
        if canonical is None and allow_unknown_keywords:
            # Explicit keywords are source facts; unlike arbitrary body words,
            # they may extend the controlled vocabulary for this file.
            canonical = normalized
        if canonical:
            candidates.append({
                "tag": canonical,
                "source": "keywords",
                "evidence_offset": 0,
                "evidence_end": len(keyword),
                "evidence_text": keyword,
                "chunk_id": "",
                "priority": 1,
            })

    chunk_rows = [chunk for chunk in _as_list(chunks)
                  if isinstance(chunk, dict) and (not rel or chunk.get("file") == rel)]
    chunk_rows.sort(key=lambda chunk: (_int_or_zero(chunk.get("start")), _as_text(chunk.get("chunk_id"))))
    heading_re = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
    for chunk in chunk_rows:
        text = _as_text(chunk.get("text"))
        base = _int_or_zero(chunk.get("start"))
        chunk_id = _as_text(chunk.get("chunk_id"))
        if not text:
            continue
        lines = text.splitlines(keepends=True)
        cursor = 0
        abstract_ranges: List[Tuple[int, int]] = []
        current_abstract: Optional[int] = None
        for raw_line in lines:
            line = raw_line.rstrip("\r\n")
            heading = heading_re.match(line)
            if heading:
                label = _normalise_tag(heading.group(1)).strip(" :：")
                if label in _SECTION_TAGS:
                    candidates.append({
                        "tag": _SECTION_TAGS[label],
                        "source": "body_structure",
                        "evidence_offset": base + cursor,
                        "evidence_end": base + cursor + len(line),
                        "evidence_text": line,
                        "chunk_id": chunk_id,
                        "priority": 3,
                    })
                if current_abstract is not None:
                    abstract_ranges.append((current_abstract, cursor))
                    current_abstract = None
                if label == "abstract":
                    current_abstract = cursor + len(raw_line)
            cursor += len(raw_line)
        if current_abstract is not None:
            abstract_ranges.append((current_abstract, len(text)))

        candidates.extend(_find_vocab_matches(text, synonyms, "body", base, 4, chunk_id))
        for start, end in abstract_ranges:
            candidates.extend(_find_vocab_matches(text[start:end], synonyms, "abstract",
                                                  base + start, 2, chunk_id))

    best: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        tag = candidate["tag"]
        previous = best.get(tag)
        if previous is None:
            best[tag] = candidate
            continue
        new_key = (candidate["priority"], candidate["evidence_offset"], candidate["source"])
        old_key = (previous["priority"], previous["evidence_offset"], previous["source"])
        if new_key < old_key:
            best[tag] = candidate

    try:
        limit = int(case.get("max_tags_per_file", 12))
    except (TypeError, ValueError):
        limit = 12
    limit = max(1, min(12, limit))
    ordered = sorted(best.values(), key=lambda row: (
        row["priority"], row["evidence_offset"], row["tag"], row["source"]))
    return ordered[:limit]


# ---------------------------------------------------------------------------
# Permission audit
# ---------------------------------------------------------------------------

def permission_audit(index: Any) -> Dict[str, Any]:
    """Audit indexed permission facts without upgrading unknowns to writable."""
    index = _as_dict(index)
    rows: List[Dict[str, Any]] = []
    for raw in _as_list(index.get("files")):
        file_meta = _as_dict(raw)
        rel = _safe_relpath(file_meta.get("relpath")) or _as_text(file_meta.get("relpath"))
        readable = _bool_or_none(file_meta.get("readable"))
        writable = _bool_or_none(file_meta.get("writable"))
        is_symlink = _bool_or_none(file_meta.get("is_symlink"))
        symlink_target = file_meta.get("symlink_target")
        if symlink_target is not None:
            symlink_target = _as_text(symlink_target)
        permissions = file_meta.get("permissions")
        if permissions is not None:
            permissions = _as_text(permissions)
        error = _as_text(file_meta.get("error"))
        unsafe_link = error.startswith("unsafe_symlink:") or "escapes library root" in error

        reasons: List[str] = []
        if readable is not True:
            reasons.append("readable is false or unknown")
        if writable is None:
            reasons.append("writable is unknown; fail closed")
        elif writable is False:
            reasons.append("source is explicitly read-only")
        if unsafe_link:
            reasons.append("unsafe or unresolved symlink")
        elif is_symlink:
            reasons.append("symlink is archived read-only rather than followed for organization")

        if readable is True and writable is True and not is_symlink and not unsafe_link:
            action = "organize"
        elif readable is True and (writable is False or is_symlink) and not unsafe_link:
            action = "read_only_archive"
        else:
            action = "skip_pending_permissions"

        recommendation = {
            "organize": "可整理",
            "read_only_archive": "只读归档",
            "skip_pending_permissions": "跳过待权限",
        }[action]
        rows.append({
            "relpath": rel,
            "readable": readable,
            "writable": writable,
            "permissions": permissions,
            "is_symlink": is_symlink,
            "symlink_target": symlink_target,
            "action": action,
            "recommended_action": action,
            "recommendation": recommendation,
            "reasons": reasons,
            "sha256": file_meta.get("sha256"),
        })

    rows.sort(key=lambda row: row["relpath"])
    summary = {
        "files": len(rows),
        "organize": sum(row["action"] == "organize" for row in rows),
        "read_only_archive": sum(row["action"] == "read_only_archive" for row in rows),
        "skip_pending_permissions": sum(row["action"] == "skip_pending_permissions" for row in rows),
        "unknown_writable": sum(row["writable"] is None for row in rows),
    }
    return {"files": rows, "summary": summary}


# ---------------------------------------------------------------------------
# Before/after snapshots and tree diff
# ---------------------------------------------------------------------------

def _source_snapshot(index: Dict[str, Any]) -> Dict[str, Any]:
    root = _as_text(index.get("root"))
    files: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for raw in _as_list(index.get("files")):
        file_meta = _as_dict(raw)
        rel = _safe_relpath(file_meta.get("relpath"))
        if not rel:
            errors.append({"file": _as_text(file_meta.get("relpath")), "error": "unsafe or missing relpath"})
            continue
        expected_sha = file_meta.get("sha256")
        entry = {
            "relpath": rel,
            "name": _as_text(file_meta.get("name") or posixpath.basename(rel)),
            "ext": _as_text(file_meta.get("ext") or posixpath.splitext(rel)[1]).lower(),
            "size": file_meta.get("size"),
            "sha256": expected_sha,
            "sha256_verified": False,
            "readable": _bool_or_none(file_meta.get("readable")),
            "writable": _bool_or_none(file_meta.get("writable")),
            "permissions": file_meta.get("permissions"),
            "is_symlink": _bool_or_none(file_meta.get("is_symlink")),
            "symlink_target": file_meta.get("symlink_target"),
        }
        if root:
            full = os.path.join(root, *rel.split("/"))
            try:
                if os.path.islink(full):
                    entry["is_symlink"] = True
                    entry["symlink_target"] = os.readlink(full)
                    if not _is_within(root, full):
                        raise ValueError("symlink escapes source root")
                file_stat = os.stat(full)
                if not stat_module.S_ISREG(file_stat.st_mode):
                    raise ValueError("source path is not a regular file")
                entry["size"] = file_stat.st_size
                entry["permissions"] = oct(stat_module.S_IMODE(file_stat.st_mode))
                actual_sha = _sha256_file(full)
                if actual_sha is None:
                    raise ValueError("source file could not be read for sha256")
                entry["sha256"] = actual_sha
                entry["sha256_verified"] = not expected_sha or actual_sha == expected_sha
                if expected_sha and actual_sha != expected_sha:
                    errors.append({
                        "file": rel,
                        "error": "source sha256 differs from index",
                        "index_sha256": expected_sha,
                        "actual_sha256": actual_sha,
                    })
            except Exception as exc:
                errors.append({"file": rel, "error": str(exc)})
        files.append(entry)
    files.sort(key=lambda row: row["relpath"])
    return {
        "schema": "bohrium-library-tree@1",
        "kind": "source_read_only_snapshot",
        "root": os.path.abspath(root) if root else None,
        "snapshot_mode": "verified_read_only" if root and os.path.isdir(root) else "index_only",
        "source_modified": False,
        "file_count": len(files),
        "files": files,
        "errors": errors,
    }


def _load_organization_manifest(organization: Any, out_dir: str) -> Tuple[Dict[str, Any], Optional[str]]:
    if isinstance(organization, (str, os.PathLike)):
        path = _as_text(organization)
        if not os.path.isabs(path):
            path = os.path.join(out_dir, path)
        data, error = _read_json_file(path)
        return (_as_dict(data), os.path.abspath(path) if error is None else None)
    organization = _as_dict(organization)
    for key in ("manifest_path", "manifest"):
        value = organization.get(key)
        if isinstance(value, dict):
            return value, None
        if isinstance(value, (str, os.PathLike)) and value:
            path = _as_text(value)
            if not os.path.isabs(path):
                path = os.path.join(out_dir, path)
            data, error = _read_json_file(path)
            if error is None:
                return _as_dict(data), os.path.abspath(path)
    if "operations" in organization or "schema" in organization:
        return organization, None
    return {}, None


def _organized_snapshot(organization: Any, out_dir: str) -> Dict[str, Any]:
    organization = _as_dict(organization)
    target_value = organization.get("organized_root") or organization.get("target_root")
    if not target_value:
        path_value = organization.get("path")
        if isinstance(path_value, str) and path_value:
            target_value = path_value if os.path.isabs(path_value) else os.path.join(out_dir, path_value)
        else:
            target_value = os.path.join(out_dir, "organized")
    target_root = os.path.abspath(_as_text(target_value))
    files: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if not os.path.isdir(target_root):
        errors.append({"file": target_root, "error": "organized directory is missing"})
    else:
        for dirpath, dirnames, filenames in os.walk(target_root, followlinks=False):
            dirnames.sort()
            filenames.sort()
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, target_root).replace("\\", "/")
                entry = {
                    "relpath": rel,
                    "name": name,
                    "size": None,
                    "sha256": None,
                    "sha256_verified": False,
                    "is_symlink": os.path.islink(full),
                    "symlink_target": None,
                    "artifact_type": "organized_file",
                }
                if name.endswith(".meta.json"):
                    entry["artifact_type"] = "metadata_sidecar"
                elif name == "manifest.json":
                    entry["artifact_type"] = "manifest"
                elif name == "aliases.json":
                    entry["artifact_type"] = "aliases"
                elif name == ".bohrium-organized":
                    entry["artifact_type"] = "marker"
                try:
                    if entry["is_symlink"]:
                        entry["symlink_target"] = os.readlink(full)
                        if not _is_within(target_root, full):
                            raise ValueError("organized symlink escapes output root")
                    file_stat = os.stat(full)
                    if not stat_module.S_ISREG(file_stat.st_mode):
                        raise ValueError("organized path is not a regular file")
                    entry["size"] = file_stat.st_size
                    entry["sha256"] = _sha256_file(full)
                    entry["sha256_verified"] = entry["sha256"] is not None
                except Exception as exc:
                    errors.append({"file": rel, "error": str(exc)})
                files.append(entry)
    files.sort(key=lambda row: row["relpath"])
    manifest_path = os.path.join(target_root, "manifest.json")
    manifest_sha = _sha256_file(manifest_path) if os.path.isfile(manifest_path) else None
    return {
        "schema": "bohrium-library-tree@1",
        "kind": "organized_snapshot",
        "root": target_root,
        "snapshot_mode": "verified_read_only" if os.path.isdir(target_root) else "missing",
        "source_modified": False,
        "file_count": len(files),
        "files": files,
        "manifest_path": manifest_path if manifest_sha else None,
        "manifest_sha256": manifest_sha,
        "errors": errors,
    }


def _dedupe_rows(rows: Iterable[Dict[str, Any]], fields: Sequence[str]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = tuple(_as_text(row.get(field)) for field in fields)
        if key not in seen:
            seen.add(key)
            output.append(row)
    output.sort(key=lambda row: tuple(_as_text(row.get(field)) for field in fields))
    return output


def _tree_diff(before: Dict[str, Any], after: Dict[str, Any], manifest: Dict[str, Any],
               organization: Dict[str, Any]) -> Dict[str, Any]:
    source_files = {row["relpath"]: row for row in _as_list(before.get("files"))}
    target_files = {row["relpath"]: row for row in _as_list(after.get("files"))}
    operations = _as_list(manifest.get("operations"))
    if not operations:
        operations = _as_list(organization.get("operations"))
    aliases = _as_list(manifest.get("aliases"))
    if not aliases:
        aliases = _as_list(organization.get("alias_rows") or organization.get("aliases"))
    failures = _as_list(manifest.get("failures"))
    if not failures:
        failures = _as_list(organization.get("failures"))
    catalog = _as_dict(manifest.get("catalog") or organization.get("catalog"))
    unclassified = _as_list(catalog.get("unclassified") or organization.get("unclassified"))

    source_for_target: Dict[str, str] = {}
    moved: List[Dict[str, Any]] = []
    renamed: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    hash_checks: List[Dict[str, Any]] = []

    for operation in operations:
        operation = _as_dict(operation)
        source = _safe_relpath(operation.get("source")) or _norm_relpath(operation.get("source"))
        target = _safe_relpath(operation.get("target")) or _norm_relpath(operation.get("target"))
        status = _as_text(operation.get("status") or "ok").casefold()
        action = _as_text(operation.get("action")).casefold()
        if not source:
            continue
        if action == "alias" or source in {
            _as_text(_as_dict(alias).get("alias")) for alias in aliases
        }:
            row = {
                "alias": source,
                "canonical_source": operation.get("canonical_source"),
                "canonical_target": target or operation.get("canonical_target"),
                "sha256": operation.get("sha256"),
                "status": status,
            }
            alias_rows.append(row)
            continue
        if status not in {"ok", "success", "completed"}:
            failed.append({
                "file": source,
                "target": target,
                "error": operation.get("error") or "operation status is " + status,
                "source": "operation",
            })
            continue
        if target:
            source_for_target[target] = source
        target_row = target_files.get(target)
        if target_row is None:
            failed.append({
                "file": source,
                "target": target,
                "error": "organized target is missing",
                "source": "operation",
            })
            continue
        source_row = source_files.get(source)
        source_sha = operation.get("sha256") or (source_row or {}).get("sha256")
        target_sha = target_row.get("sha256")
        verified = bool(source_sha and target_sha and source_sha == target_sha)
        hash_checks.append({
            "source": source,
            "target": target,
            "source_sha256": source_sha,
            "target_sha256": target_sha,
            "verified": verified,
        })
        if source and target:
            if posixpath.dirname(source) != posixpath.dirname(target):
                moved.append({"source": source, "target": target, "sha256": target_sha})
            if posixpath.basename(source) != posixpath.basename(target):
                renamed.append({"source": source, "target": target, "sha256": target_sha})

    for alias in aliases:
        alias = _as_dict(alias)
        alias_path = _safe_relpath(alias.get("alias")) or _norm_relpath(alias.get("alias"))
        if not alias_path:
            continue
        alias_rows.append({
            "alias": alias_path,
            "canonical_source": alias.get("canonical_source"),
            "canonical_target": alias.get("canonical_target"),
            "sha256": alias.get("sha256"),
            "status": "ok",
        })

    for failure in failures:
        failure = _as_dict(failure)
        failed.append({
            "file": _norm_relpath(failure.get("file") or failure.get("source")),
            "target": _norm_relpath(failure.get("target")),
            "error": failure.get("error") or failure.get("reason") or "unspecified failure",
            "source": "manifest",
        })

    added = []
    for rel, row in sorted(target_files.items()):
        added.append({
            "path": rel,
            "sha256": row.get("sha256"),
            "artifact_type": row.get("artifact_type"),
            "source_relpath": source_for_target.get(rel),
        })

    hash_checks.sort(key=lambda row: (row["source"], row["target"]))
    moved = _dedupe_rows(moved, ("source", "target"))
    renamed = _dedupe_rows(renamed, ("source", "target"))
    alias_rows = _dedupe_rows(alias_rows, ("alias", "canonical_target"))
    failed = _dedupe_rows(failed, ("file", "target", "error"))
    unclassified = _dedupe_rows([_as_dict(row) for row in unclassified], ("relpath", "why"))

    all_hashes_verified = bool(hash_checks) and all(row["verified"] for row in hash_checks)
    status = "ok"
    if before.get("errors") or after.get("errors") or failed or unclassified:
        status = "partial"
    if not target_files:
        status = "partial"
    return {
        "added": added,
        "moved": moved,
        "renamed": renamed,
        "aliases": alias_rows,
        "unclassified": unclassified,
        "failed": failed,
        "hash_checks": hash_checks,
        "counts": {
            "added": len(added),
            "moved": len(moved),
            "renamed": len(renamed),
            "aliases": len(alias_rows),
            "unclassified": len(unclassified),
            "failed": len(failed),
            "hash_checks": len(hash_checks),
            "hash_checks_verified": sum(bool(row["verified"]) for row in hash_checks),
        },
        "all_hashes_verified": all_hashes_verified,
        "source_unchanged": before.get("source_modified") is False,
        "status": status,
    }


def build_before_after(index: Any, organization: Any, out_dir: Any) -> Dict[str, Any]:
    """Write new before/after/diff JSON files under ``out_dir``.

    Existing files are never overwritten.  The source tree and any existing
    ``organized`` directory are opened read-only.
    """
    index = _as_dict(index)
    out_dir_text = _as_text(out_dir).strip()
    if not out_dir_text:
        return {
            "status": "error",
            "error": "out_dir is required",
            "before_tree": _source_snapshot(index),
            "after_tree": _organized_snapshot({}, "."),
            "tree_diff": {"added": [], "moved": [], "renamed": [], "aliases": [],
                          "unclassified": [], "failed": [], "counts": {}, "status": "partial"},
            "write_errors": ["out_dir is required"],
        }
    out_dir_abs = os.path.abspath(out_dir_text)
    source_root = _as_text(index.get("root"))
    if source_root and _is_within(source_root, out_dir_abs):
        return {
            "status": "error",
            "error": "refusing to write snapshots inside the source library",
            "out_dir": out_dir_abs,
            "before_tree": _source_snapshot(index),
            "after_tree": _organized_snapshot({}, out_dir_abs),
            "tree_diff": {"added": [], "moved": [], "renamed": [], "aliases": [],
                          "unclassified": [], "failed": [], "counts": {}, "status": "partial"},
            "write_errors": [{"file": out_dir_abs, "error": "out_dir is inside source root"}],
        }
    manifest, manifest_path = _load_organization_manifest(organization, out_dir_abs)
    if not isinstance(organization, dict) and manifest_path:
        organization = {"path": os.path.dirname(manifest_path), "manifest": manifest_path}
    before = _source_snapshot(index)
    after = _organized_snapshot(organization, out_dir_abs)
    diff = _tree_diff(before, after, manifest, _as_dict(organization))

    before_path, before_error = _write_json_new(out_dir_abs, "before_tree.json", before)
    after_path, after_error = _write_json_new(out_dir_abs, "after_tree.json", after)
    diff_path, diff_error = _write_json_new(out_dir_abs, "tree_diff.json", diff)
    write_errors = []
    for filename, error in (("before_tree.json", before_error),
                            ("after_tree.json", after_error),
                            ("tree_diff.json", diff_error)):
        if error:
            write_errors.append({"file": filename, "error": error})

    status = "ok" if not write_errors and diff.get("status") == "ok" else "partial"
    return {
        "status": status,
        "out_dir": out_dir_abs,
        "before_tree_path": before_path,
        "after_tree_path": after_path,
        "tree_diff_path": diff_path,
        "before_tree": before,
        "after_tree": after,
        "tree_diff": diff,
        "manifest_path": manifest_path or after.get("manifest_path"),
        "write_errors": write_errors,
    }


# ---------------------------------------------------------------------------
# Stable manifest digest
# ---------------------------------------------------------------------------

_VOLATILE_KEYS = {
    "generated_at", "generated_time", "timestamp", "ts", "time", "created_at",
    "updated_at", "scanned_at", "started_at", "completed_at", "finished_at",
    "run_at", "duration", "duration_ms", "elapsed", "elapsed_ms", "runtime",
    "runtime_seconds",
}


def _is_volatile_key(key: Any) -> bool:
    text = _as_text(key).casefold()
    return text in _VOLATILE_KEYS or text.endswith("_at")


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _as_text(key): _semantic_value(item)
            for key, item in value.items()
            if not _is_volatile_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _as_text(value)


def deterministic_manifest_digest(manifest: Any) -> str:
    """Return a stable SHA-256 over semantic manifest fields."""
    if isinstance(manifest, (str, bytes)):
        try:
            manifest = json.loads(manifest)
        except Exception:
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    semantic = _semantic_value(manifest)
    payload = json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return _sha256_text(payload)


# ---------------------------------------------------------------------------
# Rubric mapping
# ---------------------------------------------------------------------------

def _existing_evidence_paths(index: Dict[str, Any], organization: Dict[str, Any],
                             before_after: Dict[str, Any]) -> List[str]:
    bases: List[str] = []
    for value in (before_after.get("out_dir"), organization.get("out_dir"),
                  _as_dict(before_after.get("after_tree")).get("root")):
        text = _as_text(value)
        if text:
            bases.append(os.path.abspath(text))
    bases.append(os.getcwd())

    candidates: List[str] = []
    for key in ("before_tree_path", "after_tree_path", "tree_diff_path",
                "before_tree", "after_tree", "tree_diff"):
        value = before_after.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for key in ("manifest", "manifest_path", "aliases", "aliases_path", "rollback",
                "rollback_path", "index_path", "_path"):
        value = organization.get(key) or index.get(key)
        if isinstance(value, str):
            candidates.append(value)
    after_tree = _as_dict(before_after.get("after_tree"))
    for key in ("manifest_path",):
        value = after_tree.get(key)
        if isinstance(value, str):
            candidates.append(value)

    existing: List[str] = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        paths = [candidate] if os.path.isabs(candidate) else [os.path.join(base, candidate) for base in bases]
        paths.append(candidate)
        for path in paths:
            if os.path.isfile(path):
                absolute = os.path.abspath(path)
                key = os.path.normcase(absolute)
                if key not in seen:
                    seen.add(key)
                    existing.append(absolute)
                break
    return sorted(existing)


def _tag_count(tags: Any) -> Tuple[int, int, int]:
    files_with_tags = 0
    total = 0
    max_per_file = 0
    if isinstance(tags, dict):
        values = tags.values()
    elif isinstance(tags, list):
        values = tags
    else:
        values = []
    for value in values:
        if isinstance(value, dict):
            rows = _as_list(value.get("tags"))
        else:
            rows = _as_list(value)
        count = len(rows)
        if count:
            files_with_tags += 1
            total += count
            max_per_file = max(max_per_file, count)
    return files_with_tags, total, max_per_file


def _subitem(max_score: int, status: str, metrics: Dict[str, Any],
             evidence_paths: List[str]) -> Dict[str, Any]:
    if status != "missing" and not evidence_paths:
        # In-memory claims and prose are not independent evidence.
        status = "missing"
    if status == "met":
        earned = max_score
    elif status == "partial":
        earned = int(round(max_score * 0.5))
    else:
        earned = 0
    return {
        "max_score": max_score,
        "earned_score": earned,
        "status": status,
        "metrics": metrics,
        "evidence_paths": list(evidence_paths),
    }


def _overall_status(subitems: Dict[str, Dict[str, Any]]) -> str:
    statuses = {item["status"] for item in subitems.values()}
    if statuses == {"met"}:
        return "met"
    if "met" in statuses or "partial" in statuses:
        return "partial"
    return "missing"


def build_classification_report(index: Any, case: Any, catalog: Any, organization: Any,
                                 admin_notes: Any, tags: Any, before_after: Any,
                                 idempotency_digest: Any) -> Dict[str, Any]:
    """Map observable artifacts and counts to the fixed 100-point rubric."""
    index = _as_dict(index)
    case = _as_dict(case)
    catalog = _as_dict(catalog)
    organization = _as_dict(organization)
    admin_notes = _as_dict(admin_notes)
    before_after = _as_dict(before_after)
    evidence_paths = _existing_evidence_paths(index, organization, before_after)

    source_files = len(_as_list(index.get("files")))
    catalog_unclassified = len(_as_list(catalog.get("unclassified")))
    catalog_file_count = _int_or_zero(catalog.get("file_count"))
    if not catalog_file_count:
        catalog_file_count = sum(
            len(_as_list(_as_dict(bucket).get("members")))
            for bucket in _as_list(catalog.get("buckets"))
        )
    organized_files = _int_or_zero(organization.get("organized_files"))
    alias_rows = organization.get("alias_rows")
    if isinstance(alias_rows, list):
        alias_count = len(alias_rows)
    else:
        alias_count = _int_or_zero(alias_rows)
    if not alias_count:
        alias_count = len(_as_list(organization.get("aliases")))
    failures = _as_list(organization.get("failures"))
    operations = _as_list(organization.get("operations"))
    if not organized_files:
        organized_files = sum(
            1 for operation in operations
            if _as_dict(operation).get("status") in (None, "ok", "success", "completed")
            and _as_text(_as_dict(operation).get("action")) != "alias"
        )
    failure_count = len(failures)
    processed = organized_files + alias_count + failure_count + catalog_unclassified
    completion_ratio = processed / source_files if source_files else 0.0

    copy_checks = [
        row for row in _as_list(_as_dict(before_after.get("tree_diff")).get("hash_checks"))
        if _as_dict(row).get("source") and _as_dict(row).get("target")
    ]
    verified_checks = sum(_as_dict(row).get("verified") is True for row in copy_checks)
    verification_rate = verified_checks / len(copy_checks) if copy_checks else 0.0

    tag_files, tag_total, max_tags_per_file = _tag_count(tags)
    tag_coverage = tag_files / source_files if source_files else 0.0
    note_count = len(_as_list(admin_notes.get("notes")))
    alias_map = _as_dict(admin_notes.get("alias_map"))
    canonical_overrides = _as_dict(admin_notes.get("canonical_overrides"))
    conflict_count = len(_as_list(admin_notes.get("conflicts")))
    unknown_permissions = sum(
        1 for row in _as_list(index.get("files"))
        if _bool_or_none(_as_dict(row).get("readable")) is not True
        or _bool_or_none(_as_dict(row).get("writable")) is None
    )
    digest_valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", _as_text(idempotency_digest)))
    manifest_exists = any(os.path.basename(path) == "manifest.json" for path in evidence_paths)
    before_exists = any(os.path.basename(path) == "before_tree.json" for path in evidence_paths)
    after_exists = any(os.path.basename(path) == "after_tree.json" for path in evidence_paths)
    diff_exists = any(os.path.basename(path) == "tree_diff.json" for path in evidence_paths)
    rollback_exists = any("rollback" in os.path.basename(path).casefold() for path in evidence_paths)
    aliases_exists = any(os.path.basename(path) == "aliases.json" for path in evidence_paths)
    organization_evidence = manifest_exists or diff_exists

    base_metrics = {
        "source_files": source_files,
        "catalog_file_count": catalog_file_count,
        "catalog_unclassified": catalog_unclassified,
        "organized_files": organized_files,
        "alias_rows": alias_count,
        "failures": failure_count,
        "processed_files": processed,
        "completion_ratio": round(completion_ratio, 6),
        "copy_hash_checks": len(copy_checks),
        "copy_hash_checks_verified": verified_checks,
        "copy_hash_verification_rate": round(verification_rate, 6),
        "files_with_tags": tag_files,
        "tag_count": tag_total,
        "max_tags_per_file": max_tags_per_file,
        "tag_coverage": round(tag_coverage, 6),
        "admin_notes": note_count,
        "admin_aliases": len(alias_map),
        "canonical_overrides": len(canonical_overrides),
        "admin_conflicts": conflict_count,
        "unknown_permission_files": unknown_permissions,
        "idempotency_digest_valid": digest_valid,
        "evidence_path_count": len(evidence_paths),
    }

    if source_files and completion_ratio >= 0.999 and failure_count == 0 and catalog_unclassified == 0 and manifest_exists:
        completion_status = "met"
    elif source_files and processed > 0:
        completion_status = "partial"
    else:
        completion_status = "missing"

    if copy_checks and verification_rate >= 0.999 and manifest_exists:
        correctness_status = "met"
    elif manifest_exists and (organized_files > 0 or copy_checks):
        correctness_status = "partial"
    else:
        correctness_status = "missing"

    if source_files and completion_ratio >= 0.999 and failure_count == 0 and catalog_unclassified == 0 and conflict_count == 0 and manifest_exists:
        integrity_status = "met"
    elif manifest_exists and source_files and processed > 0:
        integrity_status = "partial"
    else:
        integrity_status = "missing"

    if before_exists and after_exists and diff_exists and copy_checks and verification_rate >= 0.999:
        verifiability_status = "met"
    elif before_exists and after_exists and diff_exists:
        verifiability_status = "partial"
    else:
        verifiability_status = "missing"

    actual_effect_sub = {
        "completion": _subitem(15, completion_status, {
            "source_files": source_files, "processed_files": processed,
            "completion_ratio": round(completion_ratio, 6),
            "failures": failure_count, "unclassified": catalog_unclassified,
        }, evidence_paths),
        "correctness": _subitem(15, correctness_status, {
            "copy_hash_checks": len(copy_checks), "verified": verified_checks,
            "verification_rate": round(verification_rate, 6),
        }, evidence_paths),
        "integrity": _subitem(5, integrity_status, {
            "accounted_files": processed, "failures": failure_count,
            "unclassified": catalog_unclassified, "admin_conflicts": conflict_count,
        }, evidence_paths),
        "verifiability": _subitem(5, verifiability_status, {
            "before_tree": before_exists, "after_tree": after_exists,
            "tree_diff": diff_exists, "hash_checks": len(copy_checks),
        }, evidence_paths),
    }

    if organization_evidence and tag_coverage >= 0.8 and note_count > 0:
        quality_status = "met"
    elif organization_evidence and (tag_files > 0 or note_count > 0):
        quality_status = "partial"
    else:
        quality_status = "missing"
    if source_files and (organized_files + alias_count) >= source_files and manifest_exists:
        efficiency_status = "met"
    elif manifest_exists and source_files and (organized_files + alias_count) > 0:
        efficiency_status = "partial"
    else:
        efficiency_status = "missing"
    if len(evidence_paths) >= 3 and copy_checks and verification_rate >= 0.999:
        gain_verification_status = "met"
    elif organization_evidence:
        gain_verification_status = "partial"
    else:
        gain_verification_status = "missing"
    usage_gain_sub = {
        "quality": _subitem(15, quality_status, {
            "files_with_tags": tag_files, "tag_coverage": round(tag_coverage, 6),
            "admin_notes": note_count,
        }, evidence_paths),
        "efficiency": _subitem(5, efficiency_status, {
            "organized_or_aliased": organized_files + alias_count,
            "source_files": source_files,
        }, evidence_paths),
        "verifiability": _subitem(5, gain_verification_status, {
            "evidence_paths": len(evidence_paths), "hash_checks": len(copy_checks),
            "verification_rate": round(verification_rate, 6),
        }, evidence_paths),
    }

    capability_count = sum(bool(value) for value in (note_count, tag_total, alias_map, canonical_overrides))
    if organization_evidence and capability_count >= 3:
        capability_status = "met"
    elif organization_evidence and capability_count >= 1:
        capability_status = "partial"
    else:
        capability_status = "missing"
    if organization_evidence and alias_map and canonical_overrides:
        method_status = "met"
    elif organization_evidence and (alias_map or canonical_overrides):
        method_status = "partial"
    else:
        method_status = "missing"
    if organization_evidence and digest_valid and (before_exists or diff_exists):
        innovation_evidence_status = "met"
    elif organization_evidence:
        innovation_evidence_status = "partial"
    else:
        innovation_evidence_status = "missing"
    innovation_sub = {
        "new_capability": _subitem(5, capability_status, {
            "admin_notes": note_count, "tags": tag_total,
            "aliases": len(alias_map), "canonical_overrides": len(canonical_overrides),
        }, evidence_paths),
        "method_improvement": _subitem(5, method_status, {
            "aliases": len(alias_map), "canonical_overrides": len(canonical_overrides),
            "normalized_tag_count": tag_total,
        }, evidence_paths),
        "innovation_evidence": _subitem(5, innovation_evidence_status, {
            "idempotency_digest_valid": digest_valid,
            "before_tree": before_exists, "tree_diff": diff_exists,
        }, evidence_paths),
    }

    if manifest_exists and (rollback_exists or diff_exists):
        usability_status = "met"
    elif manifest_exists:
        usability_status = "partial"
    else:
        usability_status = "missing"
    if digest_valid and manifest_exists:
        stability_status = "met"
    elif manifest_exists:
        stability_status = "partial"
    else:
        stability_status = "missing"
    if manifest_exists and failure_count == 0 and unknown_permissions == 0 and conflict_count == 0 and source_files > 0:
        exception_status = "met"
    elif manifest_exists and failure_count == 0 and source_files > 0:
        exception_status = "partial"
    else:
        exception_status = "missing"
    reliability_sub = {
        "usability": _subitem(3, usability_status, {
            "manifest": manifest_exists, "rollback": rollback_exists,
            "tree_diff": diff_exists,
        }, evidence_paths),
        "stability": _subitem(4, stability_status, {
            "idempotency_digest_valid": digest_valid, "manifest": manifest_exists,
        }, evidence_paths),
        "exception_handling": _subitem(3, exception_status, {
            "failures": failure_count, "unknown_permission_files": unknown_permissions,
            "admin_conflicts": conflict_count,
        }, evidence_paths),
    }

    if rollback_exists:
        documentation_status = "met"
    elif manifest_exists:
        documentation_status = "partial"
    else:
        documentation_status = "missing"
    if manifest_exists and aliases_exists and diff_exists:
        reusability_status = "met"
    elif manifest_exists or diff_exists:
        reusability_status = "partial"
    else:
        reusability_status = "missing"
    if organization_evidence and case.get("output_contract"):
        sharing_status = "met"
    elif organization_evidence:
        sharing_status = "partial"
    else:
        sharing_status = "missing"
    ecosystem_sub = {
        "documentation": _subitem(3, documentation_status, {
            "rollback": rollback_exists, "manifest": manifest_exists,
        }, evidence_paths),
        "reusability": _subitem(4, reusability_status, {
            "manifest": manifest_exists, "aliases": aliases_exists,
            "tree_diff": diff_exists,
        }, evidence_paths),
        "sharing": _subitem(3, sharing_status, {
            "output_contract_present": bool(case.get("output_contract")),
            "evidence_paths": len(evidence_paths),
        }, evidence_paths),
    }

    groups = {
        "actual_effect": (40, "实际效果", actual_effect_sub),
        "usage_gain": (25, "使用增益", usage_gain_sub),
        "innovation": (15, "创新增益", innovation_sub),
        "reliability": (10, "可靠体验", reliability_sub),
        "ecosystem": (10, "生态贡献", ecosystem_sub),
    }
    rubric: Dict[str, Dict[str, Any]] = {}
    for key, (max_score, label, subitems) in groups.items():
        earned = sum(item["earned_score"] for item in subitems.values())
        rubric[key] = {
            "label": label,
            "max_score": max_score,
            "earned_score": earned,
            "status": _overall_status(subitems),
            "metrics": dict(base_metrics),
            "evidence_paths": list(evidence_paths),
            "subitems": subitems,
        }

    total_earned = sum(item["earned_score"] for item in rubric.values())
    return {
        "rubric_mapped": rubric,
        "summary": {
            "total_possible": 100,
            "total_earned": total_earned,
            "status": "met" if total_earned == 100 else ("partial" if total_earned else "missing"),
            "evidence_paths": evidence_paths,
            "metrics": base_metrics,
        },
    }
