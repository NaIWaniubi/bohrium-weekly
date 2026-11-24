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
import fnmatch
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import ledger_append, write_json, TFIDFVectorizer

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


SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?\n])")

def split_paragraphs(text):
    """Paragraph-aware splitting for citation anchoring.

    Blank-line groups are primary units; long groups that carry several single
    newlines (PDF/xlsx dumps) fall back to per-line units so an anchor can point
    at the exact sentence a term landed in.
    """
    paras = []
    offset = 0
    for blk in re.split(r"\n\s*\n", text):
        if not blk.strip():
            continue
        raw_start = text.find(blk, offset)
        if raw_start < 0:
            raw_start = offset
        leading = len(blk) - len(blk.lstrip())
        start = raw_start + leading
        para = blk.strip()
        flat = (len(para) > 400 and "\n" in para) or para.startswith("[")
        if flat:
            sub_offset = start
            for ln in para.splitlines():
                s_ln = text.find(ln.strip(), sub_offset)
                if s_ln < 0:
                    s_ln = sub_offset
                t = ln.strip()
                if t and not t.startswith("["):
                    paras.append({"start": s_ln, "end": s_ln + len(t), "text": t})
                sub_offset = max(sub_offset, s_ln + max(len(t), len(ln)) + 1)
            offset = max(offset, raw_start + len(blk))
            continue
        if para and not para.startswith("["):
            paras.append({"start": start, "end": start + len(para), "text": para})
        offset = max(offset, start + len(blk))
    return paras


def first_sentence(text, limit=160):
    """First sentence of a passage, capped — the anchor a reviewer jumps to."""
    t = text.strip().splitlines()[0].strip() if text.strip() else ""
    t = re.sub(r"\s+", " ", t)
    parts = SENTENCE_SPLIT_RE.split(t, 1)
    s = parts[0] if parts else t
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def build_citations(chunk, q_toks, file_meta=None, max_quotes=6):
    """Evidence-chain citations grounded in bytes actually read.

    Each citation carries: file, sha256, physical pages, printed-page offset,
    section anchor (paragraph first sentence), char offsets and verbatim quote —
    the exact bundle the rubric's 证据回链 item asks for.
    """
    out = []
    body = chunk.get("text") or ""
    off = {"start": chunk.get("start"), "end": chunk.get("end")}
    base = {
        "file": chunk.get("file"),
        "sha256_file": (file_meta or {}).get("sha256"),
        "pages": chunk.get("pages"),
        "printed_page_offset": chunk.get("printed_page_offset"),
        "offset": off,
        "section_kind": chunk.get("kind"),
        "anchor": None,
        "quotes": [],
    }
    low = body.lower()
    # choose the paragraph that contains query terms; anchor on its first sentence
    paras = split_paragraphs(body)
    chosen = None
    best_ov = -1
    for para in paras:
        pt = tokenize(para["text"])
        ov = len(q_toks & pt)
        if ov <= 0:
            continue
        # prefer substantial prose over short title/heading lines that merely share terms
        weight = ov + (10 if len(para["text"]) > 80 else 0)
        if weight > best_ov:
            best_ov = weight
            chosen = para
    if chosen:
        base["anchor"] = {
            "first_sentence": first_sentence(chosen["text"]),
            "paragraph_offset": [chosen["start"], chosen["end"]],
        }
    windows = evidence_windows(body, q_toks, radius=140, max_windows=max_quotes)
    for w in windows:
        quote = w["text"].replace("\n", " ").strip()
        if len(quote) > 180:
            quote = "..." + quote[-180:]
        base["quotes"].append({"offset": w["offset"], "text": quote})
    out.append(base)
    return out


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
        sc = [item for item in sc if item[0] > 0.0]
        sc.sort(key=lambda x: (-x[0], x[1]))
        if sc:
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
    # discrimination) plus a title-field boost — filenames carry authoring
    # signals ("…_v2_final") that body text cannot. RRF stays as an auditable
    # floor so one missed term cannot zero a document.
    full_q_tokens = tokenize((q_text or "") + "\n" + (terms_txt or ""))
    mains = [scorer.score(full_q_tokens, i) for i in range(len(chunks))]
    max_main = max(mains) if mains and max(mains) > 0 else 1.0

    titles = []
    for c in chunks:
        fname = str(c.get("file") or "")
        stem = os.path.splitext(os.path.basename(fname))[0]
        titles.append(stem.replace("_", " ").replace("-", " "))
    title_scorer = BM25(titles)
    title_scores = [title_scorer.score(full_q_tokens, i) for i in range(len(chunks))]
    max_title = max(title_scores) if title_scores and max(title_scores) > 0 else 1.0

    # --- dense channel: TF-IDF + cosine over chunk bodies ---
    # complements lexical BM25: captures conceptual paraphrase and synonym
    # drift that token overlap misses. Pure-stdlib (lib.TFIDFVectorizer).
    doc_tok_lists = [sorted(tokenize(d)) for d in docs]
    try:
        tv = TFIDFVectorizer(binary_weights=False)
        tv.fit(doc_tok_lists)
        rows, norms = tv.transform_docs(doc_tok_lists)
        qv, qn = tv.transform_query(sorted(full_q_tokens))
        dense_scores = tv.cosine_scores(rows, norms, qv, qn)
    except Exception:
        dense_scores = [0.0] * len(chunks)

    max_dense = max(dense_scores) if dense_scores and max(dense_scores) > 0 else 1.0

    hits = []
    for i in range(len(chunks)):
        main_n = mains[i] / max_main
        title_n = title_scores[i] / max_title
        dense_n = dense_scores[i] / max_dense
        rrf = fused.get(i, 0.0)
        if mains[i] <= 0.0 and dense_scores[i] <= 0.0 and rrf <= 0.0:
            continue
        score = 0.45 * main_n + 0.15 * title_n + 0.20 * dense_n + 0.20 * min(rrf / 0.5, 1.0)
        hits.append((score, i))
    hits.sort(key=lambda x: (-x[0], chunks[x[1]].get("chunk_id", "")))
    hits = hits[:top]
    out_hits, out_ranked = [], []
    for score, idx in hits:
        c = dict(chunks[idx])
        c["score_raw"] = round(float(score), 6)
        c["bm25_main"] = round(float(mains[idx]), 4) if idx < len(mains) else 0.0
        c["bm25_title"] = round(float(title_scores[idx]), 4) if idx < len(title_scores) else 0.0
        c["dense_cosine"] = round(float(dense_scores[idx]), 4) if idx < len(dense_scores) else 0.0
        c["rrf_score"] = round(float(fused.get(idx, 0.0)), 6)
        c["plan_evidence"] = contributing.get(idx, [])
        out_hits.append(c)
        out_ranked.append((score, chunks[idx]))
    return out_hits, out_ranked


def apply_filters(index, flt):
    """Strict, evidenced filter chain. Returns kept file relpaths + audit.

    Unsupported explicit constraints fail closed (empty result + audit row)
    instead of being silently relaxed.  Text-derived fields use only bytes in
    the verified index; missing values do not satisfy a positive constraint.
    """
    flt = flt or {}
    files = {f["relpath"]: f for f in index.get("files", [])}
    chunks_by_file = {}
    for chunk in index.get("chunks", []):
        chunks_by_file.setdefault(chunk.get("file"), []).append(chunk.get("text", "") or "")
    file_text = {rel: "\n".join(parts)[:30000] for rel, parts in chunks_by_file.items()}
    keep = set(files)
    audit = []

    def vals(value):
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple, set)) else [value]

    def norm_ext(value):
        text = str(value or "").strip().lower()
        if text == "*":
            return text
        return text if text.startswith(".") else "." + text

    def row(constraint, prev, **extra):
        item = {"constraint": constraint, "kept": len(keep),
                "dropped_since_prev": prev - len(keep)}
        item.update(extra)
        audit.append(item)

    def text_blob(rel):
        f = files[rel]
        return (rel + "\n" + f.get("name", "") + "\n" + file_text.get(rel, "")).lower()

    def file_year(rel):
        value = (files[rel].get("meta") or {}).get("year")
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)",
                      files[rel].get("name", "") + "\n" + file_text.get(rel, "")[:12000])
        return int(m.group(1)) if m else None

    if flt.get("type") or flt.get("ext") or flt.get("extensions"):
        prev = len(keep)
        exts = {norm_ext(x) for x in vals(flt.get("type") or flt.get("ext") or flt.get("extensions"))}
        keep = {p for p in keep if "*" in exts or os.path.splitext(p)[1].lower() in exts}
        row("type=" + ",".join(sorted(exts)), prev)

    if flt.get("dir") or flt.get("directories"):
        prev = len(keep)
        dirs = {str(d).rstrip("/") for d in vals(flt.get("dir") or flt.get("directories"))}
        keep = {p for p in keep
                if any(p == d or p.startswith(d + "/") or fnmatch.fnmatch(p, d) for d in dirs)}
        row("dir=" + ",".join(sorted(dirs)), prev)

    if flt.get("include"):
        prev = len(keep)
        patterns = [str(x) for x in vals(flt["include"])]
        keep = {p for p in keep if any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(files[p]["name"], pat)
                                       for pat in patterns)}
        row("include=" + ",".join(patterns), prev)

    if flt.get("min_size") is not None:
        prev = len(keep)
        minimum = int(flt["min_size"])
        keep = {p for p in keep if files[p].get("size", 0) >= minimum}
        row(f"min_size={minimum}", prev)

    if flt.get("max_size") is not None:
        prev = len(keep)
        maximum = int(flt["max_size"])
        keep = {p for p in keep if files[p].get("size", 0) <= maximum}
        row(f"max_size={maximum}", prev)

    if flt.get("exclude"):
        prev = len(keep)
        exs = [str(e).rstrip("/") for e in vals(flt["exclude"])]
        keep = {p for p in keep
                if not any(p == e or p.startswith(e + "/") or fnmatch.fnmatch(p, e)
                           or fnmatch.fnmatch(files[p]["name"], e) for e in exs)}
        row("exclude=" + ",".join(exs), prev)

    if flt.get("need_text") is True:
        prev = len(keep)
        toolless = {n["file"] for n in index.get("needs_tool", [])}
        keep = {p for p in keep if p not in toolless}
        row("need_text=true", prev, dropped_needs_tool=sorted(toolless & (set(files) - keep))[:20])

    if flt.get("year") is not None or flt.get("min_year") is not None or flt.get("max_year") is not None:
        prev = len(keep)
        spec = flt.get("year")
        lo = hi = exact = None
        invalid = None
        if isinstance(spec, dict):
            allowed = {"min", "from", "year_min", "max", "to", "year_max", "equals", "exact"}
            unknown_year_keys = sorted(set(spec) - allowed)
            if unknown_year_keys:
                invalid = "unsupported year keys: " + ",".join(unknown_year_keys)
            lo = spec.get("min", spec.get("from", spec.get("year_min")))
            hi = spec.get("max", spec.get("to", spec.get("year_max")))
            exact = spec.get("equals", spec.get("exact"))
        elif isinstance(spec, (list, tuple)):
            if len(spec) == 2 and all(isinstance(x, (int, float)) for x in spec):
                lo, hi = spec
            else:
                invalid = "year range must contain two numbers"
        elif isinstance(spec, str) and spec.strip().isdigit():
            exact = spec.strip()
        elif isinstance(spec, str) and re.fullmatch(r"\s*(?:19|20)\d{2}\s*[-–/]\s*(?:19|20)\d{2}\s*", spec):
            lo, hi = [int(x) for x in re.split(r"[-–/]", spec.strip())]
        elif spec not in (None, ""):
            invalid = "year must be a year, range, or min/max object"
        lo = lo if lo is not None else flt.get("min_year")
        hi = hi if hi is not None else flt.get("max_year")
        try:
            if exact is not None:
                if not str(exact).isdigit():
                    raise ValueError("exact year is not numeric")
                lo = hi = int(exact)
            if lo is not None:
                lo = int(lo)
            if hi is not None:
                hi = int(hi)
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("year min exceeds max")
        except (TypeError, ValueError) as exc:
            invalid = str(exc)
        if invalid:
            keep = set()
            row("year=INVALID", prev, unsupported=invalid + "; fail closed")
        else:
            keep = {p for p in keep if (y := file_year(p)) is not None
                    and (lo is None or y >= lo) and (hi is None or y <= hi)}
            row(f"year={lo}..{hi}" + (f" exact={exact}" if exact is not None else ""), prev)

    for key in ("project", "batch", "path_contains", "name_contains", "author", "doi", "sha256"):
        if key not in flt or flt[key] in (None, "", []):
            continue
        prev = len(keep)
        wanted = [str(x).lower() for x in vals(flt[key])]
        if key == "author":
            keep = {p for p in keep if any(w in str((files[p].get("meta") or {}).get("author") or
                                                   (files[p].get("meta") or {}).get("creator") or "").lower()
                                         for w in wanted)}
        elif key == "doi":
            keep = {p for p in keep if str((files[p].get("meta") or {}).get("doi") or "").lower() in wanted}
        elif key == "sha256":
            keep = {p for p in keep if str(files[p].get("sha256") or "").lower() in wanted}
        elif key == "name_contains":
            keep = {p for p in keep if any(w in files[p].get("name", "").lower() for w in wanted)}
        elif key == "path_contains":
            keep = {p for p in keep if any(w in p.lower() for w in wanted)}
        else:
            keep = {p for p in keep if any(w in text_blob(p) for w in wanted)}
        row(key + "=" + ",".join(wanted), prev)

    if flt.get("version") is not None:
        prev = len(keep)
        spec = str(flt["version"]).lower()
        if spec in ("latest", "canonical", "final", "终稿"):
            canonical = set()
            for family in (index.get("families") or {}).values():
                members = family.get("members") or []
                if members:
                    canonical.add(max(members, key=lambda m: (int(m.get("rank") or 0),
                                                              files.get(m.get("relpath"), {}).get("mtime", 0)))["relpath"])
            family_members = {m.get("relpath") for fam in (index.get("families") or {}).values()
                              for m in fam.get("members", [])}
            keep = {p for p in keep if p in canonical or p not in family_members}
            row("version=latest/canonical", prev, canonical=sorted(canonical & keep)[:30])
        else:
            keep = {p for p in keep if spec in text_blob(p)}
            row("version=" + spec, prev)

    if flt.get("readable") is True or str(flt.get("permission", "")).lower() in ("readable", "read"):
        prev = len(keep)
        keep = {p for p in keep if files[p].get("readable") is True}
        row("permission=readable", prev)
    elif str(flt.get("permission", "")).lower() in ("writable", "write"):
        prev = len(keep)
        keep = {p for p in keep if files[p].get("writable") is True}
        row("permission=writable", prev)
    elif flt.get("permission") not in (None, "", False):
        prev = len(keep)
        keep = set()
        row("permission=" + str(flt["permission"]), prev,
            unsupported="permission value cannot be verified from available file facts; fail closed")

    supported = {"type", "ext", "extensions", "dir", "directories", "include", "min_size",
                 "max_size", "exclude", "need_text", "year", "min_year", "max_year", "project",
                 "batch", "path_contains", "name_contains", "author", "doi", "sha256", "version",
                 "readable", "permission"}
    unknown = sorted(set(flt) - supported)
    if unknown:
        prev = len(keep)
        keep = set()
        row("unsupported=" + ",".join(unknown), prev,
            unsupported="explicit constraint has no verified evaluator; fail closed")

    audit.append({"constraint": "final_keep", "count": len(keep), "kept": len(keep)})
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

    citations = []
    for h in hits:
        citations.extend(build_citations(h, q_toks, file_meta.get(h.get("file")) or {}))

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
                "pages": c.get("pages"),
                "printed_page_offset": c.get("printed_page_offset"),
                "part_index": c.get("part_index"),
                "snippet": c.get("text", "")[:320],
                "evidence": evidence_windows(c.get("text", ""), q_toks),
                "has_more": c.get("has_more"),
                "score_raw": round(float(c.get("score_raw", ranked[i][0])), 4) if i < len(ranked) else None,
                "bm25_main": c.get("bm25_main"),
                "bm25_title": c.get("bm25_title"),
                "dense_cosine": c.get("dense_cosine"),
                "rrf_score": c.get("rrf_score"),
                "plan_evidence": c.get("plan_evidence") or [],
            }
            for i, c in enumerate(hits)
        ],
        "citations": citations,
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