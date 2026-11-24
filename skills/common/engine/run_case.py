"""run_case.py — execute one fixed research case end-to-end and emit evidence.

Per the execution contract, the platform installs the skill under
/skills/weekly-skill and runs official DeepSeek Harness on each fixed case with
original input at /input/case.json (+ task.md when provided). Material varies by
topic. This script performs the deterministic portion of the case:

  1. resolve the corpus library (task.md "library" field or --library default)
  2. build/verify the corpus index once (scan_index)
  3. extract explicit filter constraints from case.json (filters[] list)
  4. rank chunks against the case question, expanding cross-lingual terms
     from the same case file (expanded_terms field) or --terms-file
  5. dump full chunk texts of every hit to evidence/hits/<rank>.txt so the
     agent can READ source bytes before making any scientific claim
  6. call finalize.py to emit a complete deterministic first delivery:
     non-empty answer, full source inventory, evidence references/matrix,
     version lineage, failure recovery, hash verification and output-contract
     preservation; classify profile also creates a real organized copy

The model-facing steps (reading evidence, deciding inclusion/exclusion, writing
the final semantic prose/matrix) enrich that complete first delivery.  The
engine never invents scientific claims and source files are never modified.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import now_iso, sha256_file, sha256_text, write_json

# reuse engine internals (same directory package)
from scan_index import main as scan_main  # noqa: F401  (cli form kept simple)
from answer import (apply_filters, rank_chunks, query_plans, tokenize,
                    build_citations, evidence_windows)
from finalize import (finalize_result, fill_output_contract, infer_profile,
                      resolve_library_root, validate_index_paths)
from enhance import apply_enhancements
from enhance_v7 import apply_v7


def load_case(case_path):
    if os.path.isdir(case_path):
        cp = os.path.join(case_path, "case.json")
        tp = os.path.join(case_path, "task.md")
    else:
        cp = case_path
        d = os.path.dirname(os.path.abspath(cp)) or "."
        tp = os.path.join(d, "task.md")
    data = {}
    if os.path.exists(cp):
        try:
            data = json.load(open(cp, encoding="utf-8"))
        except Exception as e:
            data = {"_parse_error": str(e)}
    task_md = ""
    if os.path.exists(tp):
        task_md = open(tp, encoding="utf-8-sig", errors="replace").read()
    return data, task_md


def extract_filter(index, case):
    """Compose a filter dict from explicit case constraints, else lenient."""
    flt = {}
    raw = []
    for k in ("type", "ext", "extensions", "dir", "directories", "include",
              "min_size", "max_size", "exclude", "need_text", "year", "min_year",
              "max_year", "project", "batch", "path_contains", "name_contains",
              "author", "doi", "sha256", "version", "readable", "permission"):
        if k in case:
            v = case[k]
            if isinstance(v, list):
                raw.append((k, v))
            elif v not in (None, "", [], False):
                raw.append((k, v))
    for container_key in ("filter", "filters", "constraints"):
        spec = case.get(container_key)
        if isinstance(spec, dict):
            if isinstance(spec.get("properties"), dict):
                spec = spec["properties"]
            for key, value in spec.items():
                if key not in ("id", "name", "description", "reason") and value not in (None, "", [], False):
                    raw.append((key, value))
        elif isinstance(spec, list):
            for item in spec:
                if isinstance(item, dict):
                    field = item.get("field") or item.get("key") or item.get("constraint")
                    value = item.get("value") if "value" in item else item.get("equals")
                    op = str(item.get("op") or item.get("operator") or "eq").lower()
                    if field and value is not None:
                        field = str(field)
                        if field in ("year", "size") and op in ("gte", ">=", "min"):
                            raw.append(("min_year" if field == "year" else "min_size", value))
                        elif field in ("year", "size") and op in ("lte", "<=", "max"):
                            raw.append(("max_year" if field == "year" else "max_size", value))
                        elif op in ("contains", "in"):
                            raw.append((field if field in ("project", "batch", "name_contains", "path_contains")
                                        else field + "_contains", value))
                        else:
                            raw.append((field, value))
                        continue
                    for key, value in item.items():
                        if key not in ("id", "name", "description", "reason") and value not in (None, "", [], False):
                            raw.append((key, value))
                elif isinstance(item, str):
                    m = re.match(r"\s*([A-Za-z_]+)\s*[:=]\s*(.+?)\s*$", item)
                    if m:
                        raw.append((m.group(1), m.group(2)))
    # normalise keys to apply_filters vocabulary
    norm = {"ext": "type", "extensions": "type", "directories": "dir"}
    for k, v in raw:
        nk = norm.get(k, k)
        if nk == "need_text" and v is True:
            flt["need_text"] = True
        elif nk in ("type", "dir", "include", "min_size", "max_size", "exclude", "year",
                    "min_year", "max_year", "project", "batch", "path_contains",
                    "name_contains", "author", "doi", "sha256", "version", "readable",
                    "permission"):
            flt[nk] = v
        else:
            # Keep unknown explicit constraints so apply_filters can fail closed
            # and leave an audit row instead of silently relaxing the task.
            flt[nk] = v
    return flt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="fixed case: path to case.json or case dir")
    ap.add_argument("--index", required=False, help="existing corpus index.json")
    ap.add_argument("--rescan", action="store_true", help="force full rescan")
    ap.add_argument("--library", default=None, help="corpus root (overrides case/task)")
    ap.add_argument("--terms-file", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", required=True, help="output dir, e.g. /app/output")
    ap.add_argument("--work", default=None, help="working dir, e.g. /app/work")
    ap.add_argument("--ledger", default=None, help="operation ledger json")
    ap.add_argument("--force", action="store_true",
                    help="re-run even if result.json with matching case_id exists (resume default)")
    ap.add_argument("--profile", choices=("auto", "classify", "search", "review", "data", "code", "experiment"),
                    default="auto", help="delivery profile; auto infers from case/task text")
    args = ap.parse_args()

    case, task_md = load_case(args.case)
    case_id = case.get("case_id") or os.path.basename(os.path.abspath(args.case)).split(".")[0]
    case_base = args.case if os.path.isdir(args.case) else os.path.dirname(os.path.abspath(args.case))

    out_dir = args.out
    work_dir = args.work or out_dir

    # resume only a complete, current-schema delivery for the same inputs
    existing_result = os.path.join(out_dir, "result.json")
    if not args.force and os.path.exists(existing_result):
        try:
            prev = json.load(open(existing_result, encoding="utf-8"))
            provenance = prev.get("provenance") or {}
            expected_case_digest = sha256_text(json.dumps(case, ensure_ascii=False, sort_keys=True))
            expected_index_path = args.index or os.path.join(work_dir, f"index_{case_id}.json")
            expected_index_digest = sha256_file(expected_index_path) if os.path.exists(expected_index_path) else None
            prev_refs = (prev.get("answer") or {}).get("evidence_refs") or []
            contract_ok = (
                not any(case.get(k) is not None for k in ("output_contract", "result_schema", "output_schema", "required_output_fields"))
                or (prev.get("output_contract_preserved") is True
                    and not (prev.get("output_contract_validation") or {}).get("missing"))
            )
            refs_ok = all(
                r.get("sha256_verified") and r.get("chunk_hash_verified")
                and r.get("quote_verified") and r.get("artifact_hash_verified")
                and r.get("extracted_text")
                and os.path.isfile(os.path.join(out_dir, r["extracted_text"]))
                for r in prev_refs
            )
            complete = (
                prev.get("case_id") == case_id
                and prev.get("status") == "ok"
                and provenance.get("delivery_contract_version") == 2
                and bool((prev.get("answer") or {}).get("content"))
                and bool(prev.get("sources"))
                and bool(prev.get("operation_evidence"))
                and provenance.get("input_case_digest") == expected_case_digest
                and bool(provenance.get("index_digest"))
                and provenance.get("index_digest") == expected_index_digest
                and contract_ok and refs_ok
                and (not prev.get("hit_count") or (bool(prev_refs)
                     and os.path.isdir(os.path.join(out_dir, "evidence", "hits"))))
            )
            if complete:
                print(json.dumps({
                    "case_id": case_id,
                    "status": "resumed",
                    "result_path": existing_result,
                    "hit_count": prev.get("hit_count"),
                }, ensure_ascii=False))
                return
        except Exception:
            pass  # corrupt/old prior result — fall through to regenerate

    ev_dir = os.path.join(out_dir, "evidence", "hits")
    os.makedirs(ev_dir, exist_ok=True)

    try:
        library_root = resolve_library_root(case, task_md, args.library, case_base)
    except FileNotFoundError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(2)
    work_dir = args.work or out_dir
    os.makedirs(work_dir, exist_ok=True)
    ledger_path = args.ledger or os.path.join(work_dir, "ledger.json")

    summary_meta = {}

    if not args.index or args.rescan:
        if not library_root:
            print("ERROR: no corpus library resolved (pass --library)", file=sys.stderr)
            sys.exit(2)
        idx_path = os.path.join(work_dir, f"index_{case_id}.json")
        scan_main(["--root", library_root, "--out", idx_path, "--ledger", ledger_path])
        index_path = idx_path
    else:
        index_path = args.index

    index = json.load(open(index_path, encoding="utf-8"))
    try:
        validate_index_paths(index)
    except ValueError as exc:
        print("ERROR: unsafe corpus index: " + str(exc), file=sys.stderr)
        sys.exit(2)
    if not library_root:
        library_root = index.get("root")
    question = case.get("question") or case.get("research_question") or case.get("task")
    if isinstance(question, dict):
        question = question.get("text") or question.get("q") or json.dumps(question, ensure_ascii=False)
    if not question:
        # fall back to first non-empty markdown heading / first sentence of task
        q_lines = [l.strip("# ").strip() for l in task_md.splitlines() if l.strip()]
        question = q_lines[0] if q_lines else "未提供问题"

    terms_txt = None
    if args.terms_file and os.path.exists(args.terms_file):
        terms_txt = open(args.terms_file, encoding="utf-8").read()
    elif isinstance(case.get("expanded_terms"), list):
        terms_txt = "\n".join(str(t) for t in case["expanded_terms"])
    elif isinstance(case.get("expanded_terms"), str):
        terms_txt = case["expanded_terms"]
    expanded_q = (question + "\n" + terms_txt) if terms_txt else question

    profile = args.profile if args.profile != "auto" else infer_profile(case, task_md)
    flt = extract_filter(index, case)
    if not flt and args.terms_file is None and not case.get("question"):
        print("WARN: no explicit filters in case; applying need_text=false lenient pass")

    search_meta = {}
    if profile == "search":
        from enhance import prepare_search
        keep, audit, kept_chunks, hits, plans, search_meta = prepare_search(
            index, case, task_md, question, terms_txt, flt, top=args.top)
    else:
        keep, audit = apply_filters(index, flt)
        kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
        plans = query_plans(question or expanded_q[:512], terms_txt)
        hits, ranked = rank_chunks(kept_chunks, question or expanded_q[:512],
                                  top=args.top, plans=plans, terms_txt=terms_txt)

    file_meta = {f["relpath"]: f for f in index.get("files", [])}

    # ---- dump full chunk evidence for reading ----
    for i, c in enumerate(hits):
        body = c.get("text", "")
        rel = c.get("file")
        fh = file_meta.get(rel) or {}
        with open(os.path.join(ev_dir, f"{i+1:02d}_{rel.replace('/', '_')}.txt"),
                  "w", encoding="utf-8") as f:
            f.write(f"# evidence: rank={i+1} file={rel}\n")
            f.write(f"# sha256_file={fh.get('sha256')}\n")
            f.write(f"# chunk_id={c.get('chunk_id')}\n\n")
            f.write(body)
            if c.get("has_more"):
                f.write("\n...[continued in adjacent chunk; read all chunks for this section]")

    hit_rows = []
    for i, c in enumerate(hits):
        rel = c.get("file")
        fh = file_meta.get(rel) or {}
        hit_rows.append({
            "rank": i + 1, "file": rel,
            "sha256_file": fh.get("sha256"),
            "sha256_chunk": sha256_text(c.get("text", "") or ""),
            "chunk_id": c.get("chunk_id"), "kind": c.get("kind"),
            "offset": {"start": c.get("start"), "end": c.get("end")},
            "pages": c.get("pages"),
            "printed_page_offset": c.get("printed_page_offset"),
            "snippet": (c.get("text", "") or "")[:320],
            "has_more": bool(c.get("has_more")),
            "part_index": c.get("part_index"),
            "n_chars": c.get("n_chars"),
            "score_raw": round(float(c.get("score_raw", 0.0)), 4),
            "bm25_main": c.get("bm25_main"),
            "bm25_title": c.get("bm25_title"),
            "dense_cosine": c.get("dense_cosine"),
            "rrf_score": c.get("rrf_score"),
            "plan_evidence": c.get("plan_evidence") or [],
        })

    # ---- evidence-chain citations (file/sha256/pages/offset/anchor/quotes) ----
    citations = []
    for c in hits:
        citations.extend(build_citations(c, tokenize(question + "\n" + (terms_txt or "")),
                                          file_meta.get(c.get("file")) or {}))

    version_resolution = {
        "aliases": index.get("aliases", []),
        "dedup_groups": index.get("dedup_groups", {}),
        "near_duplicates": index.get("near_duplicates", []),
    }

    # ---- compose evidence windows for the report renderer ----
    ev_windows = []
    for h in hit_rows:
        win = None
        for c in kept_chunks:
            if c.get("chunk_id") == h["chunk_id"] and c.get("file") == h["file"]:
                wins = evidence_windows(c.get("text", "") or "",
                                        tokenize(question + "\n" + (terms_txt or "")))
                if wins:
                    win = {"file": h["file"],
                           "offset": wins[0]["offset"], "text": wins[0]["text"]}
                break
        if win:
            ev_windows.append(win)

    result = {
        "case_id": case_id,
        "status": "partial_deterministic_done",
        "question": question,
        "query_expanded": bool(terms_txt),
        "query_plans": plans,
        "scanned_files": index.get("summary", {}).get("files_total"),
        "index_chunks": len(index.get("chunks", [])),
        "filter_audit": audit,
        "candidates_after_filter": len(keep),
        "hit_count": len(hits),
        "hits": hit_rows,
        "evidence_windows": ev_windows,
        "citations": citations,
        "version_resolution": version_resolution,
        "needs_tool_list": index.get("needs_tool", []),
        "dedup_groups": index.get("dedup_groups", {}),
        "families": {
            k: {"title_guess": v["title_guess"],
                "members": [m["relpath"] for m in v["members"][:8]]}
            for k, v in index.get("families", {}).items()
        },
        # ---- base fields; finalize_result replaces these with complete values ----
        "answer": {
            "_note": "replaced by finalize_result before writing",
            "content": None,
        },
        "evidence": {"dir": "evidence/hits/", "digest_note": "one file per hit, full text"},
        "sources": [{"file": h["file"], "sha256": h["sha256_file"]} for h in hit_rows],
        "uncertainty": {"missing": [], "conflicts": [], "limits": []},
        "change_records": [],
        "provenance": {
            "engine_revision": "bohrium-weekly-common@2.0",
            "generated_at": now_iso(),
            "input_case_digest": sha256_text(task_md[:200000]) if task_md else None,
            "ledger": args.ledger,
        },
    }

    # ---- complete deterministic first delivery (never leaves answer null) ----
    complete = finalize_result(
        index=index, case=case, task_md=task_md, question=question,
        hit_chunks=hits, hit_rows=hit_rows, citations=citations,
        filter_audit=audit, keep=keep, out_dir=out_dir, work_dir=work_dir,
        ledger_path=ledger_path, profile=profile, index_path=index_path, plans=plans,
    )
    result.update(complete)
    enhanced = apply_enhancements(
        result=result, index=index, case=case, task_md=task_md,
        profile=profile, out_dir=out_dir, work_dir=work_dir,
        hits=hits, kept_chunks=kept_chunks, plans=plans, search_meta=search_meta,
    )
    result.update(enhanced)
    enhanced_v7 = apply_v7(
        result=result, index=index, case=case, task_md=task_md,
        profile=profile, out_dir=out_dir, work_dir=work_dir,
        hits=hits, kept_chunks=kept_chunks, plans=plans,
    )
    result.update(enhanced_v7)
    fill_output_contract(result, case)

    write_json(os.path.join(out_dir, "result.json"), result)
    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "candidates": result["candidates_after_filter"],
        "hits": len(hits),
        "evidence_dir": ev_dir,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()