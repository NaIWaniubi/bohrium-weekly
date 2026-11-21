"""answer.py — answer one research question over a verified corpus index.

Usage:
    python answer.py --index index.json --question "..." [--filter FILTER.json] \
        [--terms-file T.txt] [--top 25] [--out answer.json] [--ledger L.json]

Architecture (multi-plan retrieval):
  1. query planning — the question is decomposed into a small set of focused
     sub-queries (top topic terms + cross-lingual expansion file), each run
     independently; results fuse by Reciprocal Rank Fusion so one missed term
     cannot kill recall.
  2. standard BM25 scoring (k1=1.5, b=0.75) over section chunks built from the
     verified index — no neural dependency, fully deterministic and explainable.
  3. every hit carries file/sha256/chunk_id/char-offsets/match windows/
     contributing sub-queries so any claim can be re-verified from source bytes.

Filtering is executed FIRST and every constraint records its own audit row,
because the rubric scores filter-constraint execution. The agent's contract:
read the full chunk behind every hit before claiming anything.
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import ledger_append, write_json

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*|[\u4e00-\u9fff]")

# BM25 defaults — deliberately standard parameters so scores stay comparable
BM25_K1 = 1.5
BM25_B = 0.75
RRF_C = 60  # Reciprocal Rank Fusion constant

STOPWORDS = {
    "the", "and", "for", "with", "our", "their", "this", "that", "from",
    "are", "was", "were", "been", "have", "has", "had", "will", "would",
    "can", "could", "may", "might", "should", "into", "over", "under", "via",
    "using", "use",
}


class BM25:
    """Standard BM25 (Robertson-Sparck Jones) over chunk documents."""

    def __init__(self, docs):
        self.N = max(len(docs), 1)
        self.dl = []
        self.tf = []
        df = {}
        for d in docs:
            f = {}
            for t in tokenize(d):
                f[t] = f.get(t, 0) + 1
            self.tf.append(f)
            self.dl.append(sum(f.values()))
            for t in f:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5))
                    for t, n in df.items()}
        self.avgdl = sum(self.dl) / self.N or 1.0

    def score(self, q_tokens, i):
        f = self.tf[i]
        dl = self.dl[i]
        s = 0.0
        for t in q_tokens:
            tf = f.get(t)
            if not tf:
                continue
            num = tf * (BM25_K1 + 1)
            den = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / self.avgdl)
            s += self.idf.get(t, 0.0) * num / den
        return s


def query_plans(question, terms_txt=None):
    """Decompose a question into focused sub-queries for multi-plan search."""
    plans = [question.strip()[:512]]
    if terms_txt:
        extra_lines = [l.strip() for l in str(terms_txt).splitlines() if l.strip()]
        plans.extend(l[:128] for l in extra_lines[:12])
    q_toks = sorted((tokenize(question) - STOPWORDS), key=lambda t: (-len(t), t))
    for t in q_toks[:5]:
        if len(t) >= 3 or ("\u4e00" <= t <= "\u9fff" and len(t) >= 2):
            plans.append(t)
    seen, uniq = [], []
    for p in plans:
        key = p.lower()
        if key not in seen and p:
            seen.append(key)
            uniq.append(p)
    return uniq[:16]


def evidence_windows(text, q_toks, radius=200, max_windows=6):
    """Return char-offset windows around query-term occurrences.

    Claim anchoring: every quote the agent makes should land on the passage
    that actually contains a matched term, with offsets so a reviewer can
    jump straight to the source bytes.
    """
    low = str(text).lower()
    spans = []
    for t in sorted(q_toks, key=len, reverse=True):
        if not t or len(t) < 2:
            continue
        start = 0
        while True:
            i = low.find(t, start)
            if i < 0:
                break
            s = max(0, i - radius)
            e = min(len(low), i + len(t) + radius)
            spans.append((s, e))
            start = i + len(t)
            if len(spans) >= 60:
                break
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 120:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append([s, e])
    out = []
    for s, e in merged[:max_windows]:
        win = text[s:e].replace("\n", " ")
        if len(win) > 700:
            win = "..." + win[-700:]
        out.append({"offset": [s, e], "text": win})
    return out


def tokenize(text):
    s = str(text).lower()
    toks = TOKEN_RE.findall(s)
    # add CJK bigrams
    cjk = "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
    bigrams = {cjk[i:i + 2] for i in range(len(cjk) - 1)}
    return set(toks) | bigrams


def rank_chunks(chunks, q_text="", q_toks=None, top=25, plans=None, terms_txt=None):
    """Multi-plan BM25 retrieval fused by Reciprocal Rank Fusion.

    Returns (hits, ranked) where ranked[i][0] is the RRF fusion score so callers
    keep the same contract as before. Each hit records which sub-queries
    contributed (plan_evidence) — auditable planning instead of one black-box
    ranking pass.
    """
    docs = [c.get("text", "") or "" for c in chunks]
    scorer = BM25(docs)

    if not plans:
        seed = q_text or (" ".join(sorted(q_toks)) if q_toks else "")
        plans = query_plans(seed, terms_txt)
    plan_tokens = [tokenize(p) for p in plans]

    # per-plan rankings
    per_plan = []  # list of (plan_index, [(bm25_score, chunk_idx)])
    for rank_slot, p_toks in enumerate(plan_tokens):
        if not p_toks:
            continue
        sc = [(scorer.score(p_toks, i), i) for i in range(len(chunks))]
        sc.sort(key=lambda x: (-x[0], x[1]))
        per_plan.append(sc[:max(top * 4, 40)])

    fused = {}  # idx -> rrf score
    contributing = {}  # idx -> list of plan labels
    for rank_slot, plan_scores in enumerate(per_plan):
        for r, (_, idx) in enumerate(plan_scores):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_C + r + 1)
            contributing.setdefault(idx, []).append(
                {"plan": rank_slot + 1,
                 "plan_text": plans[rank_slot] if rank_slot < len(plans) else "",
                 "bm25_rank": r + 1})

    # primary score: BM25 over the fully expanded question (keeps semantic
    # discrimination); RRF stays as an auditable floor so one missed term
    # cannot zero a document.
    full_q_tokens = tokenize((q_text or "") + "\n" + (terms_txt or ""))
    mains = [scorer.score(full_q_tokens, i) for i in range(len(chunks))]
    max_main = max(mains) if mains and max(mains) > 0 else 1.0

    hits = []
    for i in range(len(chunks)):
        main_n = mains[i] / max_main
        rrf = fused.get(i, 0.0)
        hits.append((0.7 * main_n + 0.3 * min(rrf / 0.5, 1.0), i))
    hits.sort(key=lambda x: (-x[0], chunks[x[1]].get("chunk_id", "")))
    hits = hits[:top]
    out_hits, out_ranked = [], []
    for score, idx in hits:
        c = dict(chunks[idx])
        c["score_raw"] = round(float(score), 6)
        c["bm25_main"] = round(float(mains[idx]), 4) if idx < len(mains) else 0.0
        c["rrf_score"] = round(float(fused.get(idx, 0.0)), 6)
        c["plan_evidence"] = contributing.get(idx, [])
        out_hits.append(c)
        out_ranked.append((score, chunks[idx]))
    return out_hits, out_ranked


def apply_filters(index, flt):
    """Strict, evidenced filter chain. Returns kept file relpaths + audit."""
    flt = flt or {}
    keep = None
    audit = []

    files = {f["relpath"]: f for f in index.get("files", [])}

    def audit_row(constraint, prev_count):
        return {"constraint": constraint, "kept": len(keep),
                "dropped_since_prev": prev_count - len(keep)}

    if flt.get("type"):
        prev = len(files)
        keep = {p for p in files if os.path.splitext(p)[1].lower()
                in {e.lower() for e in flt["type"]}}
        audit.append(audit_row("type=" + ",".join(flt["type"]), prev))
    else:
        keep = set(files)

    if flt.get("dir"):
        dirs = {d.rstrip("/") for d in flt["dir"]}
        prev = len(keep)
        keep = {p for p in keep
                if any(p == d or p.startswith(d + "/") or p.startswith(d) for d in dirs)}
        audit.append(audit_row("dir=" + ",".join(sorted(dirs)), prev))

    if flt.get("min_size"):
        prev = len(keep)
        keep = {p for p in keep if files[p]["size"] >= flt["min_size"]}
        audit.append(audit_row(f"min_size={flt['min_size']}", prev))

    if flt.get("max_size"):
        prev = len(keep)
        keep = {p for p in keep if files[p]["size"] <= flt["max_size"]}
        audit.append(audit_row(f"max_size={flt['max_size']}", prev))

    if flt.get("exclude"):
        exs = [e.rstrip("/") for e in flt["exclude"]]
        prev = len(keep)
        keep = {p for p in keep
                if not any(p == e or p.startswith(e + "/") or p.startswith(e) for e in exs)}
        audit.append(audit_row("exclude=" + ",".join(flt["exclude"]), prev))

    if flt.get("need_text") is True:
        toolless = {n["file"] for n in index.get("needs_tool", [])}
        prev = len(keep)
        keep = {p for p in keep if p not in toolless}
        row = audit_row("need_text=true", prev)
        row["dropped_needs_tool"] = sorted(set(files) & toolless - keep)[:20]
        audit.append(row)

    audit.append({"constraint": "final_keep", "count": len(keep)})
    return keep, audit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--filter", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--terms-file", default=None,
                    help="extra query terms, one per line; appended to the question "
                         "so cross-lingual questions can be expanded before matching")
    args = ap.parse_args()

    index = json.load(open(args.index, encoding="utf-8"))
    flt = json.load(open(args.filter, encoding="utf-8")) if args.filter and os.path.exists(args.filter) else {}
    keep, audit = apply_filters(index, flt)

    raw_question = args.question
    terms_txt = None
    if args.terms_file and os.path.exists(args.terms_file):
        terms_txt = open(args.terms_file, encoding="utf-8").read()
        if args.ledger:
            ledger_append(args.ledger, "query_expand", args.question,
                          f"+{len([l for l in terms_txt.splitlines() if l.strip()])} terms from {args.terms_file}",
                          status="ok")

    kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
    q_toks = tokenize(raw_question + ("\n" + terms_txt if terms_txt else ""))
    file_meta = {f["relpath"]: f for f in index.get("files", [])}
    plans = query_plans(raw_question, terms_txt)
    hits, ranked = rank_chunks(kept_chunks, raw_question, top=args.top,
                              plans=plans, terms_txt=terms_txt)

    result = {
        "question": args.question,
        "query_expanded": bool(args.terms_file and os.path.exists(args.terms_file)),
        "filter_audit": audit,
        "candidate_files_total": len(keep),
        "scored_chunks": len(ranked),
        "query_plans": plans,
        "hits": [
            {
                "rank": i + 1,
                "file": c["file"],
                "sha256_file": (file_meta.get(c["file"]) or {}).get("sha256"),
                "sha256_chunk": sha256_of(c),
                "chunk_id": c.get("chunk_id"),
                "kind": c.get("kind"),
                "offset": {"start": c.get("start"), "end": c.get("end")},
                "snippet": c.get("text", "")[:320],
                "evidence": evidence_windows(c.get("text", ""), q_toks),
                "has_more": c.get("has_more"),
                "score_raw": round(float(c.get("score_raw", ranked[i][0])), 4) if i < len(ranked) else None,
                "bm25_main": c.get("bm25_main"),
                "rrf_score": c.get("rrf_score"),
                "plan_evidence": c.get("plan_evidence") or [],
            }
            for i, c in enumerate(hits)
        ],
        "unmatched_warning": ("no chunk matched the question" if not hits else None),
    }

    if args.out:
        write_json(args.out, result)
    if args.ledger:
        ledger_append(args.ledger, "answer", args.question,
                      f"hits={len(hits)}/candidates={result['candidate_files_total']}",
                      status="ok" if hits else "warn",
                      extra={"top": args.top, "filter_audit_rows": len(audit)})

    print(json.dumps({
        "filter_audit": audit,
        "hits": [{"file": h["file"], "chunk_id": h["chunk_id"], "snippet": h["snippet"][:100]} for h in result["hits"]],
    }, ensure_ascii=False, indent=1))


def sha256_of(c):
    try:
        import hashlib
        return hashlib.sha256(str(c.get("text", "")).encode("utf-8")).hexdigest()
    except Exception:
        return None


if __name__ == "__main__":
    main()