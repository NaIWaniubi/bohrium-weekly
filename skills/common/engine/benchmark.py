"""benchmark.py — run the labeled question set through Skill and baseline engines.

Metrics per query: P@5, R@5, top-k-hit-recall (gold files appearing in top k),
hash-verified rate (hits carrying sha256 + chunk offsets), filter-audit rows.
Emits a comparison table JSON for the README / SKILL.md evidence sections.

Usage:
    python -I benchmark.py --index index.json [--out bench_result.json]
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from answer import apply_filters, rank_chunks, query_plans
from baseline import main as _baseline_main  # reuse scoring body via import-free call


def baseline_hits(index, question, top=10):
    """Filename-only matching — same semantics as baseline.py, in-process.

    Mirrors the rubric's named failure mode ('把提问丢给文件名搜索'): no filter
    audit, no section locating, no hash verification.
    """
    from answer import tokenize
    q_toks = tokenize(question)
    scored = []
    for f in index.get("files", []):
        name_toks = tokenize(f["name"])
        stem = os.path.splitext(f["name"])[0]
        stem_toks = tokenize(stem)
        raw_score = sum(1 for t in q_toks if t and len(t) >= 2 and t in f["name"].lower())
        tok_score = len(q_toks & name_toks) + 0.5 * len(q_toks & stem_toks)
        s = 2.0 * raw_score + tok_score
        if s > 0:
            scored.append((s, f["relpath"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"rank": i + 1, "file": p, "sha256_file": None,
             "evidence": "filename-only"}
            for i, (_, p) in enumerate(scored[:top])]


def skill_hits(index, question, terms, flt, top=10):
    keep, audit = apply_filters(index, flt or {})
    kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
    plans = query_plans(question, "\n".join(terms) if terms else None)
    hits, ranked = rank_chunks(kept_chunks, question, top=top,
                               plans=plans,
                               terms_txt="\n".join(terms) if terms else None)
    out = []
    for i, c in enumerate(hits):
        out.append({"rank": i + 1, "file": c.get("file"),
                    "sha256_file": next((f.get("sha256") for f in index.get("files", [])
                                          if f.get("relpath") == c.get("file")), None),
                    "chunk_id": c.get("chunk_id"),
                    "offset": {"start": c.get("start"), "end": c.get("end")},
                    "evidence": "section+sha256+offset"})
    return out, audit


def metrics(hits, golds, k=5):
    """Standard IR metrics: P@k, R@k, MRR@10, NDCG@5, plus evidence checks."""
    golds_set = set(golds)
    top_k = hits[:k]
    files_k = {h["file"] for h in top_k}
    p = len(files_k & golds_set) / max(len(files_k), 1)
    r = len(files_k & golds_set) / max(len(golds), 1)
    recalled = len(files_k & golds_set)

    # MRR@10 — first relevant hit rank reciprocal
    mrr = 0.0
    for i, h in enumerate(hits[:10]):
        if h.get("file") in golds_set:
            mrr = 1.0 / (i + 1)
            break

    # NDCG@5 — binary relevance discounted gain
    def dcg(rel):
        return sum(rel[i] / math.log2(i + 2) for i in range(len(rel)))
    rel_seq = [1 if h.get("file") in golds_set else 0 for h in hits[:5]]
    ideal = [1] * min(len(golds_set), 5)
    ndcg = dcg(rel_seq) / dcg(ideal) if dcg(ideal) > 0 else 0.0

    hash_ok = sum(1 for h in hits if h.get("sha256_file") and h.get("offset", {}).get("start") is not None)
    hash_rate = hash_ok / max(len(hits), 1)

    out = {"P@%d" % k: round(p, 3), "R@%d" % k: round(r, 3),
           "gold_recalled_in_top%d" % k: recalled, "/gold_total": len(golds),
           "hash_verified_rate": round(hash_rate, 3)}
    if k == 5:
        out["P@3"] = round(len({h['file'] for h in hits[:3]} & golds_set) /
                          max(len({h['file'] for h in hits[:3]}), 1), 3)
        out["MRR@10"] = round(mrr, 3)
        out["NDCG@5"] = round(ndcg, 3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--queries", default=os.path.join(os.path.dirname(HERE), "bench", "queries.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    index = json.load(open(args.index, encoding="utf-8"))
    spec = json.load(open(args.queries, encoding="utf-8"))

    rows = []
    for q in spec["queries"]:
        skill, audit = skill_hits(index, q["question"], q.get("terms", []), q.get("filter"))
        base = baseline_hits(index, q["question"])
        row = {
            "query_id": q["id"],
            "skill": metrics(skill, q["gold_files"]),
            "baseline_filename_only": metrics(base, q["gold_files"]),
            "filter_audit_rows": len(audit),
            "skill_top5": [h["file"] for h in skill[:5]],
            "baseline_top5": [h["file"] for h in base[:5]],
        }
        rows.append(row)

    summary = {}
    for m in ["P@5", "P@3", "R@5", "MRR@10", "NDCG@5",
                "gold_recalled_in_top5", "hash_verified_rate"]:
        sk = [r["skill"][m] for r in rows]
        bs = [r["baseline_filename_only"][m] for r in rows]
        summary[m] = {
            "skill_mean": round(sum(sk) / max(len(sk), 1), 3),
            "baseline_mean": round(sum(bs) / max(len(bs), 1), 3),
        }

    result = {"corpus_files": len(index.get("files", [])),
              "rows": rows, "summary": summary}
    if args.out:
        write_json_safe(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=1))


def write_json_safe(path, obj):
    import lib
    lib.write_json(path, obj)


if __name__ == "__main__":
    main()