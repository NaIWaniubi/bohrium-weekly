"""Local rubric-coverage evaluator for the three main Bohrium tracks.

This is not the official score.  It checks concrete artifacts against every
public rubric item so integration work targets observable gaps instead of
guessing.  Outputs are written under .selftest/rubric_eval_out/ (ignored).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "common" / "engine"
SELFTEST = ROOT / ".selftest"
OUT = SELFTEST / "rubric_eval_out"
PY = sys.executable


def run(cmd):
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr, time.time() - start


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def item(name, maximum, ok, evidence=""):
    return {"name": name, "max": maximum, "earned": maximum if ok else 0,
            "passed": bool(ok), "evidence": evidence}


def evaluate_classify(result, out_dir, index, bench, elapsed):
    checks = []
    op = result.get("operation_evidence") or {}
    manifest_path = out_dir / "organized" / "manifest.json"
    manifest = load(manifest_path) if manifest_path.exists() else {}
    operations = manifest.get("operations") or []
    copies = [x for x in operations if x.get("action") == "copy+rename+metadata" and x.get("status") == "ok"]
    aliases = manifest.get("aliases") or []
    checks.append(item("task_completion", 15, bool(copies) and bool(result.get("answer", {}).get("content")),
                       f"copies={len(copies)} aliases={len(aliases)}"))
    copy_ok = bool(copies)
    for operation in copies:
        target = out_dir / "organized" / operation["target"]
        if not target.exists() or sha(target) != operation.get("sha256"):
            copy_ok = False
            break
    checks.append(item("correctness", 15, copy_ok and result.get("provenance", {}).get("source_files_modified") is False,
                       "copied sha256 == source sha256; source untouched"))
    catalog = result.get("catalog") or {}
    sources = result.get("sources") or []
    complete = (len(sources) == len(index.get("files") or [])
                and catalog.get("file_count", 0) + len(catalog.get("unclassified") or []) == len(sources)
                and bool(result.get("version_lineage")) and bool(result.get("exact_duplicates"))
                and bool(result.get("tags")) and bool(result.get("permission_audit")))
    checks.append(item("completeness", 5, complete,
                       f"sources={len(sources)} catalog={catalog.get('file_count')} versions={len(result.get('version_lineage') or [])} tags+permissions"))
    verifiable = bool(manifest and (out_dir / "ROLLBACK.md").exists() and op.get("ledger"))
    checks.append(item("verifiability", 5, verifiable, "manifest + rollback + ledger"))
    checks.append(item("quality_gain", 15, bench.get("skill_mean", 0) > bench.get("baseline_mean", 0),
                       f"benchmark skill={bench.get('skill_mean')} baseline={bench.get('baseline_mean')}"))
    checks.append(item("efficiency_gain", 5, elapsed < 60 and len(operations) > 0,
                       f"elapsed={elapsed:.2f}s operations={len(operations)}"))
    checks.append(item("gain_verifiability", 5, (out_dir / "report.html").exists(),
                       "report.html + reproducible commands"))
    innovation = bool(copies and aliases and result.get("catalog")
                      and result.get("administrative_notes") and result.get("tags")
                      and (out_dir / "before_tree.json").exists() and (out_dir / "after_tree.json").exists()
                      and result.get("idempotency_digest"))
    checks.append(item("innovation", 15, innovation,
                       "organized copy + dedup alias + admin notes + controlled tags + before/after + idempotency"))
    reliable = bool(result.get("failure_recovery") is not None and result.get("uncertainty"))
    checks.append(item("reliability", 10, reliable, "failure_recovery + uncertainty"))
    docs = all((ROOT / "skills" / "bohr-literature-classify" / name).exists()
               for name in ("SKILL.md", "GITHUB_URL.txt"))
    checks.append(item("ecosystem", 10, docs, "SKILL.md + GITHUB_URL.txt + common README"))
    return checks


def evaluate_search(result, out_dir, index, bench, elapsed):
    checks = []
    refs = (result.get("answer") or {}).get("evidence_refs") or []
    hits = result.get("hits") or []
    checks.append(item("task_completion", 15, bool(hits) and bool(result.get("answer", {}).get("content")),
                       f"hits={len(hits)} refs={len(refs)}"))
    verified = bool(refs) and all(r.get("sha256_verified") and r.get("chunk_hash_verified")
                                  and r.get("quote_verified") and r.get("artifact_hash_verified") for r in refs)
    checks.append(item("correctness", 15, verified, "file/chunk/quote/artifact 4-way verification"))
    complete = (len(result.get("sources") or []) == len(index.get("files") or [])
                and bool(result.get("query_plans")) and bool(result.get("filter_audit"))
                and bool(result.get("coverage")))
    checks.append(item("completeness", 5, complete, "sources + query plans + filter audit + coverage"))
    artifacts = bool(refs) and all(r.get("extracted_text") and (out_dir / r["extracted_text"]).exists() for r in refs)
    checks.append(item("verifiability", 5, artifacts, "extracted-text artifacts + offsets"))
    checks.append(item("quality_gain", 15, bench.get("skill_mean", 0) > bench.get("baseline_mean", 0),
                       f"benchmark skill={bench.get('skill_mean')} baseline={bench.get('baseline_mean')}"))
    checks.append(item("efficiency_gain", 5, elapsed < 60 and bool(result.get("query_plans")),
                       f"elapsed={elapsed:.2f}s plans={len(result.get('query_plans') or [])}"))
    checks.append(item("gain_verifiability", 5, (out_dir / "report.html").exists(), "report.html"))
    innovation = bool(result.get("version_lineage") and result.get("evidence_matrix")
                      and result.get("nl_constraints") and result.get("rewritten_query_plans")
                      and result.get("prf_expansion") is not None
                      and result.get("claim_evidence") and result.get("cross_file_consensus"))
    checks.append(item("innovation", 15, innovation,
                       "BM25+dense+RRF + NL constraints + query rewrite + PRF + claim evidence + consensus"))
    reliable = result.get("status") in ("ok", "partial") and bool(result.get("uncertainty"))
    checks.append(item("reliability", 10, reliable, "honest status + uncertainty"))
    docs = all((ROOT / "skills" / "bohr-research-file-search" / name).exists()
               for name in ("SKILL.md", "GITHUB_URL.txt"))
    checks.append(item("ecosystem", 10, docs, "SKILL.md + GITHUB_URL.txt + common README"))
    return checks


def evaluate_review(result, out_dir, index, bench, elapsed):
    checks = []
    refs = (result.get("answer") or {}).get("evidence_refs") or []
    matrix = result.get("evidence_matrix") or []
    screening = result.get("screening_log") or []
    draft = result.get("review_draft") or ""
    checks.append(item("task_completion", 15, bool(matrix) and bool(screening) and bool(draft)
                       and bool(result.get("themes")) and result.get("prisma_flow") is not None
                       and result.get("conflicts") is not None and result.get("research_gaps") is not None,
                       f"matrix={len(matrix)} screening={len(screening)} draft={len(draft)} themes+prisma+conflicts+gaps"))
    verified = bool(refs) and all(r.get("sha256_verified") and r.get("chunk_hash_verified")
                                  and r.get("quote_verified") and r.get("artifact_hash_verified") for r in refs)
    checks.append(item("correctness", 15, verified, "file/chunk/quote/artifact 4-way verification"))
    complete = (len(result.get("sources") or []) == len(index.get("files") or [])
                and bool(result.get("uncertainty")))
    checks.append(item("completeness", 5, complete, "sources + uncertainty"))
    artifacts = bool(refs) and all(r.get("extracted_text") and (out_dir / r["extracted_text"]).exists() for r in refs)
    checks.append(item("verifiability", 5, artifacts, "extracted-text artifacts + offsets"))
    checks.append(item("quality_gain", 15, bench.get("skill_mean", 0) > bench.get("baseline_mean", 0),
                       f"benchmark skill={bench.get('skill_mean')} baseline={bench.get('baseline_mean')}"))
    checks.append(item("efficiency_gain", 5, elapsed < 60 and bool(screening),
                       f"elapsed={elapsed:.2f}s screening={len(screening)}"))
    checks.append(item("gain_verifiability", 5, (out_dir / "report.html").exists(), "report.html"))
    innovation = bool(result.get("themes") and result.get("screening_protocol")
                      and result.get("evidence_strength") and result.get("prisma_flow") is not None
                      and result.get("conflicts") is not None and result.get("research_gaps") is not None)
    checks.append(item("innovation", 15, innovation,
                       "protocol screening + data-derived themes + evidence strength + PRISMA + conflicts/gaps"))
    reliable = result.get("status") == "partial" and bool(result.get("uncertainty"))
    checks.append(item("reliability", 10, reliable, "partial until semantic synthesis complete"))
    docs = all((ROOT / "skills" / "bohr-litreview-gen" / name).exists()
               for name in ("SKILL.md", "GITHUB_URL.txt"))
    checks.append(item("ecosystem", 10, docs, "SKILL.md + GITHUB_URL.txt + common README"))
    return checks


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    index_path = SELFTEST / "profile9_index.json"
    if not index_path.exists():
        rc, out, _ = run([PY, "-I", str(SELFTEST / "gen_profile9.py")])
        rc, out, _ = run([PY, "-I", str(ENGINE / "scan_index.py"),
                          "--root", str(SELFTEST / "profile9"), "--out", str(index_path),
                          "--ledger", str(SELFTEST / "profile9_ledger.json")])
        if rc != 0:
            print(out)
            return 1
    index = load(index_path)
    bench = {"skill_mean": 0.0, "baseline_mean": 0.0}
    bench_path = SELFTEST / "regress_out" / "bench_v5.json"
    if bench_path.exists():
        bench_data = load(bench_path)
        bench = {"skill_mean": bench_data.get("summary", {}).get("R@5", {}).get("skill_mean", 0.0),
                 "baseline_mean": bench_data.get("summary", {}).get("R@5", {}).get("baseline_mean", 0.0)}
    cases = {profile: load(SELFTEST / f"profile9_{profile}_case.json")
             for profile in ("classify", "search", "review")}
    evaluators = {"classify": evaluate_classify, "search": evaluate_search, "review": evaluate_review}
    summary = {}
    for profile, case in cases.items():
        case_path = OUT / f"{profile}_case.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = OUT / f"{profile}_out"
        rc, log, elapsed = run([PY, "-I", str(ENGINE / "run_case.py"),
                                "--case", str(case_path), "--index", str(index_path),
                                "--work", str(OUT / f"{profile}_work"), "--out", str(out_dir),
                                "--profile", profile])
        if rc != 0:
            print(profile, "FAILED", log[-1000:])
            summary[profile] = {"error": log[-1000:]}
            continue
        result = load(out_dir / "result.json")
        rc, _, _ = run([PY, "-I", str(ENGINE / "render_report.py"),
                        "--result", str(out_dir / "result.json"), "--out", str(out_dir / "report.html"),
                        "--title", f"rubric {profile}"])
        checks = evaluators[profile](result, out_dir, index, bench, elapsed)
        earned = sum(c["earned"] for c in checks)
        maximum = sum(c["max"] for c in checks)
        summary[profile] = {"earned": earned, "maximum": maximum, "ratio": round(earned / maximum, 4),
                            "elapsed": round(elapsed, 3), "checks": checks}
        print(f"{profile}: {earned}/{maximum} ({earned/maximum:.1%}) in {elapsed:.2f}s")
        for check in checks:
            if not check["passed"]:
                print(f"  GAP {check['name']}: {check['evidence']}")
    (OUT / "scorecard.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("scorecard:", OUT / "scorecard.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
