"""Profile-specific enhancement orchestration.

The base finalizer guarantees a complete, honest delivery.  These enhancers add
rubric-specific depth (administrative notes/tags, NL constraints/PRF/consensus,
screening/themes/conflicts/gaps) without weakening the base verification.
"""
import json
import os
import re


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def prepare_search(index, case, task_md, question, terms_txt, structured_filter, top=25):
    """Two-pass search: NL constraints -> rewritten plans -> PRF -> diversify."""
    from answer import apply_filters, query_plans, rank_chunks
    from search_enhance import (diversify_results, extract_nl_constraints,
                                pseudo_relevance_feedback, rewrite_queries)
    terms_txt = terms_txt or ""
    nl = extract_nl_constraints(question, "", case)
    # Natural-language "version" is ambiguous; only trust explicit syntax.
    if "version" in (nl.get("filters") or {}):
        explicit_version = any(row.get("field") == "version" and row.get("structured")
                               for row in (nl.get("constraints") or []))
        raw_version = " ".join(str(row.get("raw") or "") for row in (nl.get("constraints") or [])
                               if row.get("field") == "version")
        if not explicit_version and not re.search(r"(?i)\bversion\s*[:=]|版本\s*[:：=]", raw_version):
            nl["filters"].pop("version", None)
    merged_filter = dict(nl.get("filters") or {})
    merged_filter.update(structured_filter or {})  # explicit case constraints win
    keep, audit = apply_filters(index, merged_filter)
    kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
    rewritten = rewrite_queries(question, terms_txt)
    plans = [question] + [line.strip() for line in terms_txt.splitlines() if line.strip()]
    plans += [p.get("text") for p in rewritten if isinstance(p, dict) and p.get("text")]
    # preserve order while removing duplicates
    plans = list(dict.fromkeys(p for p in plans if p))
    hits, _ = rank_chunks(kept_chunks, question, top=max(top * 2, top), plans=plans, terms_txt=terms_txt)
    if not hits:
        fallback_plans = query_plans(question, terms_txt)
        hits, _ = rank_chunks(kept_chunks, question, top=max(top * 2, top),
                              plans=fallback_plans, terms_txt=terms_txt)
        plans = fallback_plans
    prf = pseudo_relevance_feedback(kept_chunks, hits, question, terms_txt)
    expansion_terms = [t for t in (prf.get("expansion_terms") or []) if t]
    if expansion_terms and len({h.get("file") for h in hits}) >= 2:
        expanded_terms = (terms_txt + "\n" + "\n".join(expansion_terms)).strip()
        expanded_plans = plans + expansion_terms
        expanded_hits, _ = rank_chunks(kept_chunks, question, top=max(top * 2, top),
                                       plans=expanded_plans, terms_txt=expanded_terms)
        merged = {}
        for hit in hits + expanded_hits:
            key = (hit.get("file"), hit.get("chunk_id"))
            old = merged.get(key)
            if old is None or float(hit.get("score_raw") or 0) > float(old.get("score_raw") or 0):
                merged[key] = hit
        hits = sorted(merged.values(), key=lambda h: (-float(h.get("score_raw") or 0),
                                                      str(h.get("file") or ""), str(h.get("chunk_id") or "")))
    diversified = diversify_results(hits)
    final_hits = (diversified.get("hits") if isinstance(diversified, dict) else diversified) or []
    # Exact-content aliases are one evidence item, not independent support.
    from lib import sha256_text
    file_meta = {f.get("relpath"): f for f in index.get("files", [])}
    final_hits = sorted(final_hits, key=lambda h: (
        1 if str(h.get("file") or "").lower().startswith(("aliases/", "copies/")) or "copy" in os.path.basename(str(h.get("file") or "")).lower() else 0,
        -float(h.get("score_raw") or 0), str(h.get("file") or "")))
    deduped, duplicate_rows, seen_content = [], [], {}
    for hit in final_hits:
        file_sha = hit.get("sha256_file") or (file_meta.get(hit.get("file")) or {}).get("sha256")
        content_hash = file_sha or sha256_text(hit.get("text", "") or "")
        if content_hash in seen_content:
            duplicate_rows.append({"file": hit.get("file"), "chunk_id": hit.get("chunk_id"),
                                   "sha256": file_sha, "content_hash": content_hash,
                                   "canonical_file": seen_content[content_hash],
                                   "reason": "exact_duplicate_content"})
            continue
        seen_content[content_hash] = hit.get("file")
        deduped.append(hit)
    final_hits = deduped
    return keep, audit, kept_chunks, final_hits[:top], plans, {
        "nl_constraints": nl,
        "rewritten_query_plans": rewritten,
        "prf_expansion": prf,
        "diversified_results": diversified,
        "deduplicated_results": {"hits": final_hits, "dropped": duplicate_rows},
    }


def _classify(result, index, case, task_md, out_dir):
    import classify_enhance as enhancer
    catalog = result.get("catalog") or {}
    organization = result.get("operation_evidence") or {}
    admin = enhancer.extract_admin_notes(index)
    tags = {}
    for file_meta in index.get("files", []):
        rel = file_meta.get("relpath")
        tags[rel] = enhancer.generate_tags(file_meta, file_meta.get("meta") or {}, index.get("chunks", []), case)
    permissions = enhancer.permission_audit(index)
    before_after = enhancer.build_before_after(index, organization, out_dir)
    manifest = _load(os.path.join(out_dir, organization.get("manifest", "organized/manifest.json")))
    digest = enhancer.deterministic_manifest_digest(manifest)
    report = enhancer.build_classification_report(index, case, catalog, organization, admin, tags,
                                                   before_after, digest)
    return {
        "administrative_notes": admin,
        "tags": tags,
        "permission_audit": permissions,
        "before_after": before_after,
        "idempotency_digest": digest,
        "rubric_mapped": report,
    }


def _search(result, index, case, task_md, out_dir, hits, kept_chunks, plans, search_meta=None):
    import search_enhance as enhancer
    question = result.get("question") or ""
    terms_txt = "\n".join(str(x) for x in case.get("expanded_terms", [])) if isinstance(case.get("expanded_terms"), list) else str(case.get("expanded_terms") or "")
    search_meta = search_meta or {}
    nl = search_meta.get("nl_constraints") or enhancer.extract_nl_constraints(question, "", case)
    rewritten = search_meta.get("rewritten_query_plans") or enhancer.rewrite_queries(question, terms_txt)
    prf = search_meta.get("prf_expansion")
    if prf is None:
        prf = enhancer.pseudo_relevance_feedback(kept_chunks, hits, question, terms_txt)
    diversified = search_meta.get("diversified_results") or enhancer.diversify_results(hits)
    deduplicated = search_meta.get("deduplicated_results")
    claim_evidence = [enhancer.select_claim_evidence(hit, question) for hit in hits]
    consensus = enhancer.cross_file_consensus(index, hits, question)
    refs = (result.get("answer") or {}).get("evidence_refs") or []
    report = enhancer.build_search_report(index, case, question, result.get("filter_audit") or [],
                                           rewritten, hits, refs, consensus)
    return {
        "nl_constraints": nl,
        "rewritten_query_plans": rewritten,
        "prf_expansion": prf,
        "diversified_results": diversified,
        "deduplicated_results": deduplicated,
        "claim_evidence": claim_evidence,
        "cross_file_consensus": consensus,
        "coverage": report.get("coverage") if isinstance(report, dict) else None,
        "rubric_mapped": report,
    }


def _review(result, index, case, task_md, out_dir):
    import review_enhance as enhancer
    question = result.get("question") or ""
    protocol = enhancer.parse_screening_protocol(case, task_md)
    screening = enhancer.screen_documents(index, protocol)
    included = [row.get("file") for row in screening if row.get("decision") == "include"]
    themes = enhancer.cluster_themes(index, included, question)
    strength = {}
    for file_meta in index.get("files", []):
        rel = file_meta.get("relpath")
        quotes = [r.get("quote") for r in (result.get("answer") or {}).get("evidence_refs", [])
                  if r.get("file") == rel]
        strength[rel] = enhancer.classify_evidence_strength(file_meta, file_meta.get("meta") or {}, quotes)
    conflicts = enhancer.detect_contradictions(index, included, themes)
    gaps = enhancer.detect_research_gaps(themes, included, question)
    prisma = enhancer.build_prisma_flow(index, screening)
    evidence_matrix = result.get("evidence_matrix") or []
    draft = enhancer.build_review_draft(question, themes, evidence_matrix, conflicts, gaps, protocol)
    # The deterministic draft is still explicitly partial until an agent reads
    # the full sources and verifies every substantive claim.
    result["screening_log"] = screening
    result["evidence_matrix"] = evidence_matrix
    if isinstance(draft, dict):
        text = draft.get("text") or draft.get("draft") or draft.get("content")
        if text:
            result["answer"]["content"] = text
        result["review_draft"] = draft
    elif isinstance(draft, str):
        result["answer"]["content"] = draft
        result["review_draft"] = draft
    return {
        "screening_protocol": protocol,
        "screening_log": screening,
        "themes": themes,
        "evidence_strength": strength,
        "conflicts": conflicts,
        "research_gaps": gaps,
        "prisma_flow": prisma,
        "review_draft": result.get("review_draft"),
        "status": "partial",
    }


def apply_enhancements(result, index, case, task_md, profile, out_dir, work_dir=None,
                       hits=None, kept_chunks=None, plans=None, search_meta=None):
    """Merge profile-specific fields into result; never hide base failures."""
    if profile == "experiment":
        try:
            from experiment_enhance import apply_experiment
            extra = apply_experiment(result, index, case, task_md, out_dir, work_dir or out_dir)
            if extra.get("report"):
                result["answer"]["content"] = extra["report"]
            result["status"] = "ok"
            result["enhancement_status"] = {"state": "ok", "profile": profile}
            return extra
        except Exception as exc:
            result["enhancement_status"] = {"state": "failed", "profile": profile, "error": str(exc)}
            return {}
    if profile in ("data", "code"):
        try:
            from datacode_enhance import apply_datacode
            extra = apply_datacode(result, index, case, task_md, out_dir, work_dir or out_dir)
            if extra.get("report"):
                result["answer"]["content"] = extra["report"]
            state = (extra.get("datacode_status") or {}).get("state")
            if state == "ok":
                result["status"] = "ok"
            elif state == "partial":
                result["status"] = "partial"
            result["enhancement_status"] = {"state": "ok" if state in ("ok", "partial") else "failed",
                                            "profile": profile,
                                            "error": (extra.get("datacode_status") or {}).get("error")}
            return extra
        except Exception as exc:
            result["enhancement_status"] = {"state": "failed", "profile": profile, "error": str(exc)}
            return {}
    if profile not in ("classify", "search", "review"):
        return {}
    try:
        if profile == "classify":
            extra = _classify(result, index, case, task_md, out_dir)
        elif profile == "search":
            extra = _search(result, index, case, task_md, out_dir, hits or [], kept_chunks or [],
                            plans or [], search_meta=search_meta)
        else:
            extra = _review(result, index, case, task_md, out_dir)
        result["enhancement_status"] = {"state": "ok", "profile": profile}
        return extra
    except Exception as exc:
        result["enhancement_status"] = {"state": "failed", "profile": profile, "error": str(exc)}
        return {}
