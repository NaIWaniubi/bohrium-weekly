"""End-to-end preflight for the three main submission packages.

Runs each packaged engine (not the source tree) against the profile9 fixture and
checks the result contract, enhancement status, evidence artifacts and GitHub
link.  This catches packaging/runtime failures before quota is spent.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "_submit"
SELFTEST = ROOT / ".selftest"
OUT = SELFTEST / "preflight_out"
CASES = {
    "challenge-666fefb0": "classify",
    "challenge-139745c7": "search",
    "challenge-e97137c9": "review",
}


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    index = SELFTEST / "profile9_index.json"
    if not index.exists():
        rc, log = run([sys.executable, "-I", str(SELFTEST / "gen_profile9.py")])
        rc, log = run([sys.executable, "-I", str(ROOT / "skills/common/engine/scan_index.py"),
                       "--root", str(SELFTEST / "profile9"), "--out", str(index),
                       "--ledger", str(SELFTEST / "profile9_ledger.json")])
        if rc != 0:
            print(log)
            return 1
    report = {}
    ok_all = True
    for slug, profile in CASES.items():
        package = SUBMIT / slug
        engine = package / "common" / "engine"
        case = SELFTEST / f"profile9_{profile}_case.json"
        out = OUT / slug
        work = OUT / f"{slug}_work"
        rc, log = run([sys.executable, "-I", str(engine / "run_case.py"),
                       "--case", str(case), "--index", str(index), "--work", str(work),
                       "--out", str(out), "--profile", profile])
        checks = {"exit_code": rc == 0}
        if rc == 0 and (out / "result.json").exists():
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            refs = (result.get("answer") or {}).get("evidence_refs") or []
            if profile == "classify":
                operation = result.get("operation_evidence") or {}
                checks["delivery_verified"] = bool(
                    operation.get("organized_files", 0) > 0
                    and (out / "organized" / "manifest.json").exists()
                    and result.get("tags") and result.get("permission_audit")
                    and result.get("before_after") and result.get("idempotency_digest")
                    and result.get("canonical_map") and result.get("metadata_provenance"))
            else:
                checks["delivery_verified"] = bool(refs) and all(
                    r.get("sha256_verified") and r.get("chunk_hash_verified")
                    and r.get("quote_verified") and r.get("artifact_hash_verified") for r in refs)
                if profile == "search":
                    checks["delivery_verified"] = checks["delivery_verified"] and bool(
                        result.get("section_evidence") and result.get("synthesized_answer"))
                if profile == "review":
                    checks["delivery_verified"] = checks["delivery_verified"] and bool(
                        result.get("claim_matrix") and result.get("composed_review"))
            checks.update({
                "status": result.get("status"),
                "answer_nonempty": bool((result.get("answer") or {}).get("content")),
                "sources_complete": len(result.get("sources") or []) == 9,
                "enhancement_ok": (result.get("enhancement_status") or {}).get("state") == "ok",
                "enhancement_v7_ok": (result.get("enhancement_v7_status") or {}).get("state") == "ok",
                "artifacts_exist": all(r.get("extracted_text") and (out / r["extracted_text"]).exists()
                                       for r in refs),
            })
        else:
            checks["error"] = log[-1000:]
        url = (package / "GITHUB_URL.txt").read_text(encoding="utf-8").splitlines()[0].strip()
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                checks["github_http"] = response.status
        except Exception as exc:
            checks["github_http"] = str(exc)
        checks["ok"] = (checks.get("exit_code") and checks.get("answer_nonempty")
                        and checks.get("sources_complete") and checks.get("enhancement_ok")
                        and checks.get("enhancement_v7_ok") and checks.get("delivery_verified")
                        and checks.get("artifacts_exist") and checks.get("github_http") == 200)
        ok_all = ok_all and checks["ok"]
        report[slug] = checks
        print(slug, json.dumps(checks, ensure_ascii=False))
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PREFLIGHT", "PASS" if ok_all else "FAIL", "—", OUT / "report.json")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
