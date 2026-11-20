"""baseline.py — simulate the "base Agent without this Skill".

The platform compares Skill-on vs base-agent-on for fixed cases. Locally we
approximate the failure mode the rubric calls out explicitly ("把提问丢给文件名
搜索"): match question tokens against FILENAMES ONLY, no filters, no section
locating, no hashes. Everything else (question set, k, metrics) is shared with
benchmark.py so the comparison table is apples-to-apples.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from answer import tokenize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    index = json.load(open(args.index, encoding="utf-8"))
    q_toks = tokenize(args.question)

    scored = []
    for f in index.get("files", []):
        name_toks = tokenize(f["name"])
        stem = os.path.splitext(f["name"])[0]
        stem_toks = tokenize(stem)
        # filename-contains semantics: raw substring check + token overlap
        raw_score = sum(1 for t in q_toks if t and len(t) >= 2 and t in f["name"].lower())
        tok_score = len(q_toks & name_toks) + 0.5 * len(q_toks & stem_toks)
        score = 2.0 * raw_score + tok_score
        if score > 0:
            scored.append((score, f["relpath"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [{"rank": i + 1, "file": p, "score": round(float(s), 3),
              "evidence": "filename-only match (no Skill)",
              "sha256_file": None}
           for i, (s, p) in enumerate(scored[:args.top])]

    result = {"mode": "baseline_filename_only",
            "question": args.question,
            "candidate_files_total": len(index.get("files", [])),
            "hits": top,
            "note": "base-agent approximation: no filter audit, no section "
                    "locating, no hash verification — mirrors the rubric's "
                    "'drop the question into filename search' failure mode."}
    if args.out:
        from lib import write_json
        write_json(args.out, result)
    print(json.dumps({"mode": result["mode"], "hits": len(top),
                      "top": [h["file"] for h in top[:5]]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()