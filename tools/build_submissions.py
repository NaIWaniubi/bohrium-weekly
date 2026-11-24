"""Rebuild every local Playground submission package from tracked sources.

This script only writes the ignored _submit/ tree.  It never contacts the
platform and never changes source files.  Each package is self-contained and
validated against the 50-file / 500KB-per-file / 2MB-total text-resource limit.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
SUBMIT = ROOT / "_submit"
ENGINE_FILES = (
    "lib.py", "scan_index.py", "answer.py", "run_case.py", "finalize.py",
    "enhance.py", "classify_enhance.py", "search_enhance.py", "review_enhance.py",
    "enhance_v7.py", "version_metadata.py", "review_quality.py", "search_quality.py",
    "datacode_enhance.py",
    "baseline.py", "benchmark.py", "render_report.py",
)


def skill_dir_from_url(package):
    url_file = package / "GITHUB_URL.txt"
    if not url_file.exists():
        raise ValueError(f"missing {url_file}")
    first = url_file.read_text(encoding="utf-8").splitlines()[0].strip()
    match = re.search(r"/skills/([^/\s]+)/?", first)
    if not match:
        raise ValueError(f"cannot resolve skill directory from {first}")
    return SKILLS / match.group(1)


def copy_text(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def build_package(package):
    skill_dir = skill_dir_from_url(package)
    if not (skill_dir / "SKILL.md").exists():
        raise ValueError(f"missing source skill: {skill_dir}")
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    copy_text(skill_dir / "SKILL.md", package / "SKILL.md")
    copy_text(skill_dir / "GITHUB_URL.txt", package / "GITHUB_URL.txt")
    copy_text(ROOT / "README.md", package / "USAGE_README.md")
    copy_text(SKILLS / "common" / "README.md", package / "common" / "README.md")
    for name in ENGINE_FILES:
        copy_text(SKILLS / "common" / "engine" / name, package / "common" / "engine" / name)
    copy_text(SKILLS / "common" / "bench" / "queries.json",
              package / "common" / "bench" / "queries.json")


def validate_package(package):
    files = []
    total = 0
    for path in package.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink not allowed: {path}")
        if not path.is_file():
            continue
        size = path.stat().st_size
        files.append((path, size))
        total += size
        if size > 500 * 1024:
            raise ValueError(f"file exceeds 500KB: {path} ({size})")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"resource is not UTF-8 text: {path}") from exc
    if len(files) > 50:
        raise ValueError(f"package exceeds 50 files: {package} ({len(files)})")
    if total > 2 * 1024 * 1024:
        raise ValueError(f"package exceeds 2MB: {package} ({total})")
    required = {"SKILL.md", "GITHUB_URL.txt", "USAGE_README.md",
                "common/engine/finalize.py", "common/engine/run_case.py"}
    present = {path.relative_to(package).as_posix() for path, _ in files}
    missing = required - present
    if missing:
        raise ValueError(f"package missing required files: {sorted(missing)}")
    engine_dir = package / "common" / "engine"
    modules = sorted(path.stem for path in engine_dir.glob("*.py"))
    code = "import sys; sys.path.insert(0, " + repr(str(engine_dir)) + "); " + "; ".join(
        "import " + name for name in modules)
    proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise ValueError(f"isolated package import failed: {proc.stderr[-500:]}")
    return len(files), total


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-existing", action="store_true",
                    help="only rebuild packages already present (default)")
    args = ap.parse_args(argv)
    all_engine = {path.name for path in (SKILLS / "common" / "engine").glob("*.py")}
    missing = sorted(all_engine - set(ENGINE_FILES))
    if missing:
        raise SystemExit("ENGINE_FILES is missing shipped modules: " + ", ".join(missing))
    if not SUBMIT.exists():
        raise SystemExit("no _submit directory; create package folders with GITHUB_URL.txt first")
    packages = sorted(p for p in SUBMIT.iterdir() if p.is_dir())
    rows = []
    for package in packages:
        build_package(package)
        count, size = validate_package(package)
        rows.append((package.name, count, size))
    print(f"rebuilt {len(rows)} packages")
    for name, count, size in rows:
        print(f"{name}: {count} files, {size} bytes")
    print(f"total: {sum(r[2] for r in rows)} bytes")


if __name__ == "__main__":
    main()
