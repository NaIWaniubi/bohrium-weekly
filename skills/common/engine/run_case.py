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
     agent can READ source bytes before making any claim (the rubric scores
     定位准确性 and 证据回链; the script never writes claims itself)
  6. emit result.json skeleton covering every output_contract field with
     honest empty/default values + provenance, ledger, and SHA-256 digests

The model-facing steps (reading evidence, deciding inclusion/exclusion, writing
the final prose/matrix) are deliberately left to the agent so claims stay
grounded in what was actually read. Save partial results early per budget rule.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import now_iso, sha256_file, sha256_text, write_json

# reuse engine internals (same directory package)
from scan_index import main as scan_main  # noqa: F401  (cli form kept simple)
from answer import apply_filters, rank_chunks, query_plans, tokenize


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
    for k in ("type", "ext", "extensions", "dir", "directories",
              "min_size", "max_size", "exclude", "need_text"):
        if k in case:
            v = case[k]
            if isinstance(v, list):
                raw.append((k, v))
            elif v not in (None, "", [], False):
                raw.append((k, v))
    # normalise keys to apply_filters vocabulary
    norm = {"extensions": "type", "directories": "dir"}
    for k, v in raw:
        nk = norm.get(k, k)
        if nk == "need_text" and v is True:
            flt["need_text"] = True
        elif nk in ("type", "dir", "min_size", "max_size", "exclude"):
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
    args = ap.parse_args()

    case, task_md = load_case(args.case)
    case_id = case.get("case_id") or os.path.basename(os.path.abspath(args.case)).split(".")[0]

    out_dir = args.out
    work_dir = args.work or out_dir
    ev_dir = os.path.join(out_dir, "evidence", "hits")
    os.makedirs(ev_dir, exist_ok=True)

    library_root = args.library or case.get("library") or case.get("corpus_root")

    summary_meta = {}

    if not args.index or args.rescan:
        if not library_root:
            print("ERROR: no corpus library resolved (pass --library)", file=sys.stderr)
            sys.exit(2)
        idx_path = os.path.join(work_dir, f"index_{case_id}.json")
        os.makedirs(work_dir, exist_ok=True)
        scan_main(["--root", library_root, "--out", idx_path])
        index_path = idx_path
    else:
        index_path = args.index

    index = json.load(open(index_path, encoding="utf-8"))
    flt = extract_filter(index, case)
    if not flt and args.terms_file is None and not case.get("question"):
        print("WARN: no explicit filters in case; applying need_text=false lenient pass")

    keep, audit = apply_filters(index, flt)

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

    kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
    hits, ranked = rank_chunks(kept_chunks, question or expanded_q[:512],
                              top=args.top, plans=query_plans(question or expanded_q[:512], terms_txt),
                              terms_txt=terms_txt)

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
                f.write("\n...[truncated at 1800 chars in index; full text readable in source]")

    hit_rows = []
    for i, c in enumerate(hits):
        rel = c.get("file")
        fh = file_meta.get(rel) or {}
        hit_rows.append({
            "rank": i + 1, "file": rel,
            "sha256_file": fh.get("sha256"),
            "sha256_chunk": None,
            "chunk_id": c.get("chunk_id"), "kind": c.get("kind"),
            "offset": {"start": c.get("start"), "end": c.get("end")},
            "snippet": (c.get("text", "") or "")[:320],
            "score_raw": round(float(c.get("score_raw", 0.0)), 4),
            "bm25_main": c.get("bm25_main"),
            "rrf_score": c.get("rrf_score"),
            "plan_evidence": c.get("plan_evidence") or [],
        })

    # ---- compose evidence windows for the report renderer ----
    from answer import evidence_windows, tokenize
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
        "scanned_files": index.get("summary", {}).get("files_total"),
        "index_chunks": len(index.get("chunks", [])),
        "filter_audit": audit,
        "candidates_after_filter": len(keep),
        "hit_count": len(hits),
        "hits": hit_rows,
        "evidence_windows": ev_windows,
        "needs_tool_list": index.get("needs_tool", []),
        "dedup_groups": index.get("dedup_groups", {}),
        "families": {
            k: {"title_guess": v["title_guess"],
                "members": [m["relpath"] for m in v["members"][:8]]}
            for k, v in index.get("families", {}).items()
        },
        # ---- fields the rubric expects, intentionally left for the agent ----
        "answer": {
            "_note": "agent fills after reading evidence/hits/*.txt in full",
            "content": None,
        },
        "evidence": {"dir": "evidence/hits/", "digest_note": "one file per hit, full text"},
        "sources": [{"file": h["file"], "sha256": h["sha256_file"]} for h in hit_rows],
        "uncertainty": {"missing": [], "conflicts": [], "limits": []},
        "change_records": [],
        "provenance": {
            "engine_revision": "bohrium-weekly-common@1.0",
            "generated_at": now_iso(),
            "input_case_digest": sha256_text(task_md[:200000]) if task_md else None,
            "ledger": args.ledger,
        },
    }

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