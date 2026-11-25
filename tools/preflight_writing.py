"""End-to-end preflight for the ten writing packages that previously scored zero."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "_submit"

MANUSCRIPT = """# Graph Networks for Molecular Property Prediction

## Abstract
We propose a graph neural network for molecular property prediction. On QM9, the model reduces mean absolute error by 12 percent relative to the MLP baseline.

## Introduction
Molecular property prediction is important for drug discovery. However, existing methods show a limitation on small datasets. Prior work proposes message passing [1].

## Methods
We use the QM9 dataset with a random 80/10/10 split. The learning rate is 0.01 and the batch size is 32. We train for 100 epochs. The MLP baseline uses the same split.

## Results
The proposed model improves accuracy by 12 percent. Table 1 reports a significant result. A negative result was observed for the ablation without message passing.

## Discussion
The method has a limitation on larger datasets. However, the result is consistent with prior work.

## Conclusion
The proposed graph neural network improves molecular property prediction. Future work will examine larger datasets.

## References
[1] Chen (2025). Message passing for molecular property prediction. Journal of ML.
"""

CASES = {
    "product-arena-02-7051d92d": ("polish", "Polish the manuscript sentence by sentence and preserve meaning"),
    "challenge-96263d10": ("format", "Adapt this manuscript to the target academic template"),
    "product-arena-03-429cc14d": ("references", "Manage references, deduplicate entries and synchronize citations"),
    "challenge-b810ef56": ("evidence", "Write a claim-evidence matrix and a grounded draft"),
    "challenge-8021aacb": ("structure", "Diagnose and optimize the manuscript structure and logic"),
    "product-arena-04-4b82ad21": ("intro", "Write the introduction and related work with citations"),
    "product-arena-05-64faa4a2": ("methods", "Write the methods and experimental section from the materials"),
    "challenge-caca73b2": ("results", "Write the results and discussion section from the data"),
    "challenge-2815a418": ("rebuttal", "Write a point-by-point response to the reviewer comments"),
    "product-arena-07-865de7e4": ("latex", "Prepare a LaTeX manuscript and compile it"),
}


def run(command, timeout=180):
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def main():
    failures = []
    for package, (expected, question) in CASES.items():
        root = SUBMIT / package
        engine = root / "common" / "engine"
        if not (engine / "run_case.py").exists():
            failures.append(package + ": missing packaged engine")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            library = tmp / "library"
            library.mkdir()
            (library / "manuscript.md").write_text(MANUSCRIPT, encoding="utf-8")
            (library / "reference.md").write_text(
                "Chen et al. (2025). Message passing for molecular property prediction. "
                "The method improves accuracy on QM9 and uses a graph neural network.", encoding="utf-8")
            index = tmp / "index.json"
            rc, out = run([sys.executable, "-I", str(engine / "scan_index.py"),
                           "--root", str(library), "--out", str(index),
                           "--ledger", str(tmp / "ledger.json")])
            if rc != 0:
                failures.append(package + ": scan failed: " + out[-300:])
                continue
            case = {"case_id": "preflight-" + package, "question": question,
                    "source_text": MANUSCRIPT, "title": "Writing preflight",
                    "citation_style": "apa", "output_contract": {"main_result": None, "change_records": []}}
            if expected == "rebuttal":
                case["review_comments"] = ["Please clarify the dataset split and baseline."]
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            out_dir = tmp / "out"
            rc, out = run([sys.executable, "-I", str(engine / "run_case.py"),
                           "--case", str(case_path), "--index", str(index),
                           "--work", str(tmp / "work"), "--out", str(out_dir),
                           "--profile", "writing"])
            if rc != 0 or not (out_dir / "result.json").exists():
                failures.append(package + ": run failed: " + out[-500:])
                continue
            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            checks = {
                "status": result.get("status") == "ok",
                "task": result.get("task_type") == expected,
                "answer": len(str((result.get("answer") or {}).get("content") or "")) > 100,
                "not_report": not str((result.get("answer") or {}).get("content") or "").startswith("# Writing delivery report"),
                "docx": (out_dir / "writing_output.docx").exists(),
                "pdf": (out_dir / "writing_preview.pdf").exists(),
                "artifacts": bool(result.get("artifacts")),
                "contract": result.get("output_contract_preserved") is True,
            }
            bad = [name for name, ok in checks.items() if not ok]
            if bad:
                failures.append(package + ": " + ",".join(bad))
            else:
                print(f"PASS {package}: {expected}, {len(result.get('artifacts') or [])} artifacts")
    if failures:
        print("WRITING PACKAGE PREFLIGHT FAILED:")
        for row in failures:
            print(" -", row)
        return 1
    print("WRITING PACKAGE PREFLIGHT ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
