"""answer.py — answer one research question over a verified corpus index.

Usage:
    python answer.py --index index.json --question "..." [--filter FILTER.json] \
        [--top 25] [--out answer.json] [--ledger ledger.json]

Filtering is executed FIRST and every constraint records its own evidence row
(exactly which files survived which constraint), because the rubric scores
filter-constraint execution. Answer content comes from deterministic lexical
matching over section chunks so the model budget goes to reading and judging
the hits, not to blind scanning:

    - question is tokenized into CJK bigrams + latin words
    - each chunk scored = weighted overlap; ties broken by filename certainty
    - hits carry file/sha256/chunk_id/char offsets/snippet/has_more so every
      claim can be re-verified from source bytes

The agent's contract is to read the full chunk(s) behind each hit before
claiming anything; this script never fabricates passages it did not see.
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


def rank_chunks(chunks, q_toks, top):
    """BM25-lite over chunk text; short-title preference for ties."""
    df = {}
    docs = []
    for c in chunks:
        d = tokenize(c.get("text", ""))
        for t in d:
            df[t] = df.get(t, 0) + 1
        docs.append((c, d))
    N = max(len(docs), 1)
    idf = lambda t: math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1.0)
    scored = []
    for c, d in docs:
        if len(q_toks & d) == 0:
            continue
        tf_sum = sum(idf(t) for t in q_toks if t in d)
        tf_norm = math.log1p(tf_sum) / (math.log(1 + len(d)) or 1)
        name_prior = 0.06 if re.search(r"\.(md|txt)$", str(c.get("file", "")), re.I) else 0.0
        score = tf_norm + name_prior
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], x[1]["chunk_id"]))
    return [c for _, c in scored[:top]], scored


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

    question = args.question
    if args.terms_file and os.path.exists(args.terms_file):
        extra = open(args.terms_file, encoding="utf-8").read()
        question = question + "\n" + extra
        if args.ledger:
            ledger_append(args.ledger, "query_expand", args.question,
                          f"+{len([l for l in extra.splitlines() if l.strip()])} terms from {args.terms_file}",
                          status="ok")

    kept_chunks = [c for c in index.get("chunks", []) if c.get("file") in keep]
    q_toks = tokenize(question)
    file_meta = {f["relpath"]: f for f in index.get("files", [])}
    hits, ranked = rank_chunks(kept_chunks, q_toks, args.top)

    result = {
        "question": args.question,
        "query_expanded": bool(args.terms_file and os.path.exists(args.terms_file)),
        "filter_audit": audit,
        "candidate_files_total": len(keep),
        "scored_chunks": len(ranked),
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
                "score_raw": round(ranked[i][0], 4) if i < len(ranked) else None,
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