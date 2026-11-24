"""v7 enhancement orchestration: canonical versions, claim-level review, answer synthesis."""
import json
import os


def _canonical(index, admin_notes=None):
    import version_metadata as vm
    resolved = vm.resolve_canonical(index, admin_notes)
    metadata = vm.enrich_metadata(index, resolved.get("canonical_map"))
    report = vm.canonical_report(index, resolved.get("canonical_map"), metadata)
    return resolved, metadata, report


def _merge_version_resolution(result, resolved):
    merged = dict(result.get("version_resolution") or {})
    merged.update(resolved or {})
    merged.setdefault("aliases", [])
    merged.setdefault("dedup_groups", {})
    merged.setdefault("near_duplicates", [])
    return merged


def _classify(result, index, case, task_md, out_dir):
    import version_metadata as vm
    resolved, metadata, report = _canonical(index, (result.get("administrative_notes") or {}))
    catalog = result.get("catalog") or {}
    # Attach canonical/metadata facts to catalog members without changing paths.
    meta_by_file = {row.get("relpath"): row for row in metadata} if isinstance(metadata, list) else metadata
    for bucket in catalog.get("buckets", []):
        for member in bucket.get("members", []):
            rel = member.get("relpath")
            member["canonical"] = (resolved.get("canonical_map") or {}).get(rel, rel)
            member["metadata_enriched"] = meta_by_file.get(rel) if isinstance(meta_by_file, dict) else None
    return {
        "canonical_map": resolved.get("canonical_map"),
        "alias_map": resolved.get("alias_map"),
        "version_resolution": _merge_version_resolution(result, resolved),
        "metadata_provenance": metadata,
        "canonical_report": report,
        "catalog": catalog,
    }


def _search(result, index, case, task_md, out_dir, hits, kept_chunks, plans):
    import search_quality as sq
    import version_metadata as vm
    resolved, metadata, canonical_report = _canonical(index)
    question = result.get("question") or ""
    terms_txt = "\n".join(str(x) for x in case.get("expanded_terms", [])) if isinstance(case.get("expanded_terms"), list) else str(case.get("expanded_terms") or "")
    refined = sq.refine_query_plans(question, terms_txt, case)
    canonicalized = sq.canonicalize_hits(index, hits, resolved.get("canonical_map"))
    canonical_hits = canonicalized.get("hits") if isinstance(canonicalized, dict) else canonicalized
    section_evidence = sq.select_section_evidence(index, canonical_hits or [], question)
    synthesized = sq.synthesize_answer(question, section_evidence)
    coverage = sq.coverage_report(index, question, canonical_hits or [], section_evidence,
                                  result.get("filter_audit") or [], resolved.get("canonical_map"))
    rubric = sq.rubric_report(index, question, canonical_hits or [], section_evidence,
                              result.get("filter_audit") or [], resolved.get("canonical_map"))
    if isinstance(synthesized, dict):
        text = synthesized.get("text") or synthesized.get("answer") or synthesized.get("content")
    else:
        text = synthesized
    if text:
        result["answer"]["content"] = text
    return {
        "canonical_map": resolved.get("canonical_map"),
        "alias_map": resolved.get("alias_map"),
        "version_resolution": _merge_version_resolution(result, resolved),
        "metadata_provenance": metadata,
        "canonical_report": canonical_report,
        "refined_query_plans": refined,
        "canonicalized_hits": canonicalized,
        "section_evidence": section_evidence,
        "synthesized_answer": synthesized,
        "coverage": coverage,
        "rubric_mapped_v7": rubric,
    }


def _review(result, index, case, task_md, out_dir):
    import review_quality as rq
    import version_metadata as vm
    resolved, metadata, canonical_report = _canonical(index)
    question = result.get("question") or ""
    protocol = result.get("screening_protocol") or rq.parse_screening_protocol(case, task_md)
    screening = rq.refine_screening(index, result.get("screening_log") or [],
                                    resolved.get("canonical_map"), protocol)
    if isinstance(screening, dict):
        screening_log = screening.get("screening_log") or screening.get("log") or []
        prisma = screening.get("prisma_flow") or result.get("prisma_flow")
    else:
        screening_log = screening
        prisma = result.get("prisma_flow")
    included = [row.get("file") for row in screening_log if row.get("decision") == "include"]
    claims = rq.build_claim_matrix(index, included, resolved.get("canonical_map"))
    themes = rq.induce_themes(index, included, resolved.get("canonical_map"), question)
    conflicts = rq.detect_claim_conflicts(index, included, claims, themes)
    gaps = rq.build_gap_matrix(themes, included, claims, question)
    composed = rq.compose_review(question, protocol, screening_log, prisma, themes, claims, conflicts, gaps)
    quality = rq.quality_report(index=index, screening_log=screening_log, prisma_flow=prisma,
                                themes=themes, claim_matrix=claims, conflicts=conflicts, gaps=gaps,
                                review=composed, included_files=included,
                                canonical_map=resolved.get("canonical_map"))
    if isinstance(composed, dict):
        text = composed.get("text") or composed.get("draft") or composed.get("content")
    else:
        text = composed
    if text:
        result["answer"]["content"] = text
    result["screening_log"] = screening_log
    result["themes"] = themes
    result["conflicts"] = conflicts
    result["research_gaps"] = gaps
    if prisma is not None:
        result["prisma_flow"] = prisma
    return {
        "canonical_map": resolved.get("canonical_map"),
        "alias_map": resolved.get("alias_map"),
        "version_resolution": _merge_version_resolution(result, resolved),
        "metadata_provenance": metadata,
        "canonical_report": canonical_report,
        "refined_screening": screening,
        "screening_log": screening_log,
        "claim_matrix": claims,
        "themes": themes,
        "conflicts": conflicts,
        "research_gaps": gaps,
        "gap_matrix": gaps,
        "prisma_flow": prisma,
        "composed_review": composed,
        "review_draft": composed,
        "rubric_mapped_v7": quality,
        "status": "partial",
    }


def apply_v7(result, index, case, task_md, profile, out_dir, work_dir=None,
             hits=None, kept_chunks=None, plans=None):
    if profile not in ("classify", "search", "review"):
        return {}
    try:
        if profile == "classify":
            extra = _classify(result, index, case, task_md, out_dir)
        elif profile == "search":
            extra = _search(result, index, case, task_md, out_dir, hits or [], kept_chunks or [], plans or [])
        else:
            extra = _review(result, index, case, task_md, out_dir)
        result["enhancement_v7_status"] = {"state": "ok", "profile": profile}
        return extra
    except Exception as exc:
        result["enhancement_v7_status"] = {"state": "failed", "profile": profile, "error": str(exc)}
        return {}
