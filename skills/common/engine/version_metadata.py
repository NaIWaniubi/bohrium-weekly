"""Canonical version and metadata resolution for document-library repair.

The organizer's administrative notes are authoritative: a ``Canonical`` line
sets the current primary document, and following ``Alias`` / ``Supersedes``
lines inherit that context.  Exact-content duplicates are then folded into the
same canonical map.  No source file is modified.
"""
import json
import os
import re
from collections import defaultdict

from classify_enhance import (
    _ADMIN_MARKER_RE, _ALIAS_MARKER_RE, _CANONICAL_RE, _CANONICAL_SUFFIX_RE,
    _CURRENT_ALIAS_RE, _SUPERSEDE_RE, _as_dict, _as_list, _as_text,
    _extract_refs, _file_lookup, _identity_groups, _iter_chunk_lines,
    _norm_relpath, _resolve_ref, _safe_relpath,
)

__all__ = [
    "resolve_canonical", "enrich_metadata", "apply_canonical_to_hits",
    "apply_canonical_to_screening", "canonical_report",
]

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_ADMIN_PATH_HINT = re.compile(r"(?i)(?:^|/)(?:admin|aliases|versions?|copies)(?:/|$)")
_CANONICAL_DOC_RE = re.compile(
    r"(?i)(?:canonical|preferred|authoritative|正式版|主版本|规范版)"
    r"(?:\s+(?:file|version|copy|document|文档|文件|版本))?\s*(?:is|are|=|:|：|为|是)\s*(.+)$")


def _files(index):
    return [row for row in _as_list(index.get("files")) if isinstance(row, dict) and row.get("relpath")]


def _doc_type(rel, row=None):
    ext = str((row or {}).get("ext") or os.path.splitext(rel)[1]).lower()
    if ext in (".pdf", ".doc", ".docx"):
        return "paper"
    if ext in (".xlsx", ".xls", ".csv", ".tsv"):
        return "data"
    if ext in (".pptx", ".ppt"):
        return "slides"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return "image"
    return "text"


def _texts(index):
    by_file = defaultdict(list)
    for chunk in _as_list(index.get("chunks")):
        if isinstance(chunk, dict):
            by_file[chunk.get("file")].append(chunk.get("text") or "")
    return {rel: "\n".join(parts) for rel, parts in by_file.items()}


def _choose_primary(members, files, explicit=None):
    if explicit:
        for member in members:
            if member == explicit:
                return member
    def rank(rel):
        row = files.get(rel, {})
        path = rel.replace("\\", "/").lower()
        meta = row.get("meta") or {}
        return (
            0 if _ADMIN_PATH_HINT.search(path) else 1,
            0 if re.search(r"(?:draft|草稿|v1|old|copy)", os.path.basename(path)) else 1,
            len(meta), int(row.get("mtime") or 0), -len(rel), rel,
        )
    return max(members, key=rank)


def resolve_canonical(index, admin_notes=None):
    """Return canonical/alias/version maps with evidence and conflicts."""
    index = _as_dict(index)
    files = {row["relpath"]: row for row in _files(index)}
    lookup = _file_lookup(index)
    admin_notes = admin_notes if isinstance(admin_notes, dict) else None
    if admin_notes is None:
        try:
            from classify_enhance import extract_admin_notes
            admin_notes = extract_admin_notes(index)
        except Exception:
            admin_notes = {}

    current_by_file = {}
    canonical_claims = []
    alias_claims = []
    relations = []
    conflicts = []
    unresolved = []

    for item in _iter_chunk_lines(index):
        line = item["line"].strip()
        if not line or not _ADMIN_MARKER_RE.search(line):
            continue
        refs = _extract_refs(line)
        resolved = [_resolve_ref(ref, lookup) for ref in refs]
        valid = [ref for ref in resolved if ref]
        for ref, target in zip(refs, resolved):
            if target is None and _looks_like_path(ref) and not _DOI_RE.fullmatch(ref):
                unresolved.append({"file": item["file"], "reference": ref,
                                   "evidence_offset": item["evidence_offset"], "raw": line})
        current = current_by_file.get(item["file"])
        negated = bool(re.search(r"(?i)\b(?:not|non)[-\s]+(?:canonical|preferred|authoritative|alias|supersedes|replaces)\b|非正式版|不是主版本|不替代", line))
        match = None if negated else _CANONICAL_DOC_RE.search(line)
        suffix = None if negated or match else _CANONICAL_SUFFIX_RE.search(line)
        if match or suffix:
            candidate_refs = _extract_refs((match or suffix).group(1))
            candidate = _resolve_ref(candidate_refs[0], lookup) if candidate_refs else None
            if candidate is None and len(valid) == 1:
                candidate = valid[0]
            if candidate:
                current_by_file[item["file"]] = candidate
                canonical_claims.append({"canonical": candidate, "file": item["file"],
                                         "evidence_offset": item["evidence_offset"], "raw": line})
            continue
        if not negated and _ALIAS_MARKER_RE.search(line):
            if len(valid) >= 2:
                alias, canonical = valid[0], valid[1]
            elif len(valid) == 1:
                alias, canonical = valid[0], current
            else:
                alias, canonical = None, None
            if alias and canonical and alias != canonical:
                alias_claims.append({"alias": alias, "canonical": canonical, "file": item["file"],
                                     "evidence_offset": item["evidence_offset"], "raw": line})
            continue
        if not negated and _SUPERSEDE_RE.search(line):
            older = newer = None
            if len(valid) >= 2:
                left, right = valid[0], valid[1]
                passive = bool(re.search(r"(?i)superseded\s+by|replaced\s+by|被替代", line))
                older, newer = (left, right) if passive else (right, left)
            elif len(valid) == 1:
                if re.search(r"(?i)^\s*(?:superseded|replaced)\s+by", line):
                    older, newer = current, valid[0]
                else:
                    older, newer = valid[0], current
            if older and newer and older != newer:
                relations.append({"older": older, "newer": newer, "file": item["file"],
                                  "evidence_offset": item["evidence_offset"], "raw": line})

    # Start from explicit declarations, then exact-content identity groups.
    parent = {rel: rel for rel in files}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(a, b):
        if a not in parent or b not in parent:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for claim in canonical_claims:
        union(claim["canonical"], claim["canonical"])
    for claim in alias_claims:
        union(claim["alias"], claim["canonical"])
    for relation in relations:
        union(relation["older"], relation["newer"])
    for group in _identity_groups(index):
        # Administrative notes and different document types are not literature
        # versions merely because they share a title or DOI string.
        by_type = defaultdict(list)
        for member in group:
            if member not in files or _ADMIN_PATH_HINT.search(member):
                continue
            by_type[_doc_type(member, files.get(member))].append(member)
        for members in by_type.values():
            if len(members) < 2:
                continue
            primary = _choose_primary(members, files)
            for member in members:
                union(member, primary)

    groups = defaultdict(list)
    for rel in files:
        groups[find(rel)].append(rel)
    explicit_by_group = defaultdict(list)
    for claim in canonical_claims:
        explicit_by_group[find(claim["canonical"])].append(claim["canonical"])
    canonical_map = {}
    for root, members in groups.items():
        explicit = explicit_by_group.get(root, [])
        primary = _choose_primary(members, files, explicit[0] if explicit else None)
        for member in members:
            canonical_map[member] = primary

    # Alias declarations are direct evidence and override heuristic grouping.
    for claim in alias_claims:
        canonical_map[claim["alias"]] = claim["canonical"]
    for relation in relations:
        canonical_map[relation["older"]] = relation["newer"]

    # Transitive resolution with cycle reporting.
    def resolve(start):
        seen = []
        current = start
        while canonical_map.get(current, current) != current:
            if current in seen:
                return None, seen + [current]
            seen.append(current)
            current = canonical_map.get(current, current)
        return current, None
    for rel in list(canonical_map):
        target, cycle = resolve(rel)
        if cycle:
            conflicts.append({"kind": "canonical_cycle", "files": cycle,
                              "reason": "canonical/version declarations form a cycle"})
            canonical_map[rel] = rel
        elif target:
            canonical_map[rel] = target

    alias_map = {alias: claim["canonical"] for alias, claim in
                 ((c["alias"], c) for c in alias_claims)}
    for alias, canonical in alias_map.items():
        canonical_map[alias] = canonical
    for rel in files:
        canonical_map.setdefault(rel, rel)

    for ref in unresolved:
        conflicts.append({"kind": "unresolved_reference", **ref,
                          "reason": "administrative reference does not resolve to an indexed file"})
    # Multiple explicit canonicals in one identity group are contradictory.
    for root, claims in explicit_by_group.items():
        targets = sorted(set(claims))
        if len(targets) > 1:
            conflicts.append({"kind": "canonical_conflict", "canonicals": targets,
                              "reason": "multiple files in one identity group declared canonical"})

    return {
        "canonical_map": dict(sorted(canonical_map.items())),
        "alias_map": dict(sorted(alias_map.items())),
        "version_relations": relations,
        "canonical_claims": canonical_claims,
        "conflicts": conflicts,
        "summary": {"files": len(files), "canonical_files": len(set(canonical_map.values())),
                    "aliases": len(alias_map), "version_relations": len(relations),
                    "conflicts": len(conflicts)},
    }


def _looks_like_path(value):
    return bool(value and ("/" in value or "\\" in value or re.search(r"\.[A-Za-z0-9]{1,12}$", value)))


def enrich_metadata(index, canonical_map=None):
    """Extract metadata only from file metadata, text and administrative notes."""
    index = _as_dict(index)
    canonical_map = canonical_map or {}
    texts = _texts(index)
    output = []
    for row in _files(index):
        rel = row["relpath"]
        meta = row.get("meta") or {}
        text = texts.get(rel, "")
        provenance = []
        def add(field, value, source, offset=None):
            if value in (None, ""):
                return
            provenance.append({"field": field, "value": value, "source": source, "offset": offset})
        title = meta.get("title")
        if title:
            add("title", title, "file.metadata")
        else:
            for line in text.splitlines():
                candidate = line.strip().lstrip("# ").strip()
                if 4 <= len(candidate) <= 220 and not candidate.startswith(("[page ", "[slide ", "[sheet ", "DOI:")):
                    title = candidate
                    add("title", title, "first_body_line", text.find(line))
                    break
        author = meta.get("author") or meta.get("creator") or meta.get("authors")
        if author:
            add("author", author, "file.metadata")
        else:
            match = re.search(r"(?im)^\s*(?:authors?|作者)\s*[:：]\s*(.+)$", text)
            if match:
                author = match.group(1).strip()[:240]
                add("author", author, "body_author_line", match.start())
        year = meta.get("year")
        if not year:
            match = _YEAR_RE.search(text[:12000])
            if match:
                year = int(match.group(1))
                add("year", year, "body_year", match.start())
        else:
            add("year", year, "file.metadata")
        doi = meta.get("doi")
        if not doi:
            match = _DOI_RE.search(text[:20000])
            if match:
                doi = match.group(0).rstrip(".,;")
                add("doi", doi, "body_doi", match.start())
        else:
            add("doi", doi, "file.metadata")
        journal = meta.get("journal") or meta.get("publication")
        if journal:
            add("journal", journal, "file.metadata")
        else:
            match = re.search(r"(?im)^\s*(?:journal|期刊|publication)\s*[:：]\s*(.+)$", text)
            if match:
                journal = match.group(1).strip()[:200]
                add("journal", journal, "body_journal_line", match.start())
        ext = str(row.get("ext") or "").lower()
        source_type = "paper" if ext in (".pdf", ".docx", ".doc") else "data" if ext in (".xlsx", ".csv", ".tsv") else "slides" if ext in (".pptx", ".ppt") else "text"
        if re.search(r"(?i)(?:^|/)(?:admin|aliases|versions?)(?:/|$)", rel):
            source_type = "administrative_or_alias"
        add("source_type", source_type, "path_and_extension")
        values = {item["field"]: item["value"] for item in provenance}
        missing = [field for field in ("title", "author", "year", "journal", "doi") if field not in values]
        output.append({"relpath": rel, "canonical": canonical_map.get(rel, rel),
                       "title": values.get("title"), "author": values.get("author"),
                       "year": values.get("year"), "journal": values.get("journal"),
                       "doi": values.get("doi"), "source_type": source_type,
                       "metadata_provenance": provenance, "missing": missing, "conflicts": []})
    return output


def apply_canonical_to_hits(index, hits, canonical_map):
    """Canonicalize hits while retaining distinct chunks and audit rows."""
    canonical_map = canonical_map or {}
    grouped = {}
    dropped = []
    for hit in _as_list(hits):
        if not isinstance(hit, dict):
            continue
        original = hit.get("file")
        canonical = canonical_map.get(original, original)
        item = dict(hit)
        item["alias_of"] = original if canonical != original else None
        item["canonical_file"] = canonical
        key = (canonical, hit.get("chunk_id"))
        if key in grouped:
            old = grouped[key]
            if float(hit.get("score_raw") or 0) > float(old.get("score_raw") or 0):
                grouped[key] = item
            if canonical != original:
                dropped.append({"file": original, "canonical_file": canonical,
                                "chunk_id": hit.get("chunk_id"), "sha256": hit.get("sha256_file"),
                                "reason": "alias_or_older_version"})
        else:
            grouped[key] = item
    result = sorted(grouped.values(), key=lambda h: (-float(h.get("score_raw") or 0),
                                                      str(h.get("canonical_file") or ""),
                                                      str(h.get("chunk_id") or "")))
    return {"hits": result, "dropped": dropped,
            "version_resolution": {"canonical_map": canonical_map, "dropped_count": len(dropped)}}


def apply_canonical_to_screening(index, screening_log, canonical_map):
    """Ensure canonical documents are included and aliases/older versions excluded."""
    canonical_map = canonical_map or {}
    rows = [dict(row) for row in _as_list(screening_log) if isinstance(row, dict)]
    by_file = {row.get("file"): row for row in rows}
    for row in rows:
        rel = row.get("file")
        canonical = canonical_map.get(rel, rel)
        if canonical == rel:
            row["canonical"] = rel
            row.pop("duplicate_of", None)
            continue
        primary = by_file.get(canonical) or {}
        row.update({"decision": "exclude", "reason_code": "duplicate",
                    "reason": f"重复或旧版本归并到 {canonical}",
                    "duplicate_of": canonical, "duplicate_basis": "administrative_version_or_sha256",
                    "canonical": canonical,
                    "evidence_refs": primary.get("evidence_refs") or row.get("evidence_refs") or []})
        if primary.get("decision") == "exclude" and primary.get("reason_code") == "duplicate":
            primary.update({"decision": "include" if primary.get("metadata", {}).get("matched_criteria") else "needs_review",
                            "reason_code": "included", "reason": "canonical version restored",
                            "duplicate_of": None})
    return rows


def canonical_report(index, canonical_map, metadata):
    rows = _as_list(metadata)
    return {
        "files": len(_files(index)),
        "canonical_files": len(set((canonical_map or {}).values())),
        "alias_count": sum(1 for rel, canonical in (canonical_map or {}).items() if rel != canonical),
        "metadata_records": len(rows),
        "metadata_complete": sum(1 for row in rows if isinstance(row, dict) and not row.get("missing")),
        "missing_fields": {field: sum(1 for row in rows if isinstance(row, dict) and field in (row.get("missing") or []))
                           for field in ("title", "author", "year", "journal", "doi")},
        "canonical_map": canonical_map or {},
    }
