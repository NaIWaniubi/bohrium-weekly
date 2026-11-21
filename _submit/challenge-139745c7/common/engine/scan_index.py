"""scan_index.py — build a verified corpus index for a research file library.

Usage:
    python scan_index.py --root <library_dir> --out <index.json> [--ledger <ledger.json>] [--limit N]

Outputs index.json with:
  files[]        every reachable file: relpath/name/type/size/sha256/mtime/extracted_status
  chunks[]       section-level text chunks {chunk_id, file, kind, start, end, text}
  families       version lineage groups keyed by normalized title family
  dedup          exact-duplicate groups (identical sha256 under different names)
  aliases        same-family aliases and their resolution chain
  summary        counts + per-status rollup

Text extraction falls back gracefully: stdlib text formats first, then any
importable python libs, then PATH CLIs; if none can parse, the file is still
indexed (hash/size/type/mtime are honest facts) and flagged needs_tool so the
agent reports the gap instead of pretending it read the content.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import banner, ledger_append, norm_title, sha256_file, sha256_text, variant_rank, write_json

TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".rst"}
BINARY_TYPES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".zip", ".tar", ".gz"}
IMG_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

HEADING_RE = re.compile(r"^(#{1,6}\s+|[ \t]*(?:第[一二三四五六七八九十百0-9]+[章节篇]|(\d{1,2})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?)[ \t.)、:：]\s*.*$)")
CHUNK_MAX_CHARS = 1800


def extract_native(path):
    """Extract text using whatever python lib happens to be importable."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            try:
                import fitz  # pymupdf
                doc = fitz.open(path)
                return "\n".join(p.get_text() for p in doc), {"pages": doc.page_count}
            except Exception:
                pass
            try:
                from pypdf import PdfReader
                r = PdfReader(path)
                return "\n".join((pg.extract_text() or "") for pg in r.pages), {"pages": len(r.pages)}
            except Exception:
                pass
        if ext == ".docx":
            try:
                import docx
                d = docx.Document(path)
                parts = [p.text for p in d.paragraphs]
                for tbl in d.tables:
                    for row in tbl.rows:
                        parts.append("\t".join(c.text for c in row.cells))
                return "\n".join(parts), {}
            except Exception:
                pass
        if ext == ".xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
                out = []
                for ws in wb.worksheets:
                    out.append(f"[sheet: {ws.title}]")
                    for row in ws.iter_rows(values_only=True):
                        out.append(" | ".join("" if c is None else str(c) for c in row))
                return "\n".join(out[:20000]), {"sheets": wb.sheetnames}
            except Exception:
                pass
        if ext == ".pptx":
            try:
                from pptx import Presentation
                prs = Presentation(path)
                out = []
                for i, slide in enumerate(prs.slides, 1):
                    out.append(f"[slide {i}]")
                    for shp in slide.shapes:
                        if getattr(shp, "has_text_frame", False):
                            out.append(shp.text_frame.text)
                return "\n".join(out), {"slides": len(prs.slides)}
            except Exception:
                pass
    except Exception:
        pass
    return None, None


def extract_cli(path, ext):
    """Try small external CLIs when python libs are absent.

    The platform also ships pdf-text-tool / office-renderer as named resources;
    if neither is importable and no CLI exists, the file stays in needs_tool so
    the skill can honestly report the gap instead of inventing content.
    """
    try:
        if ext == ".pdf":
            r = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", errors="replace"), {}
            # macOS/Windows fallbacks
            for cmd in (["python", "-c", "import sys,fitz;d=fitz.open(sys.argv[1]);[sys.stdout.write(p.get_text()) for p in d]", path],
                       ["soffice", "--headless", "--convert-to", "txt", "--outdir", os.path.dirname(path), path]):
                r = subprocess.run(cmd, capture_output=True, timeout=90)
                if r.returncode == 0:
                    out = r.stdout or b""
                    cand = os.path.splitext(path)[0] + ".txt"
                    if os.path.exists(cand):
                        return open(cand, "rb").read().decode("utf-8", errors="replace"), {}
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return None, None


def split_sections(text, base_id):
    """Split extracted text into section chunks with char offsets."""
    lines = text.splitlines()
    chunks = []
    cur, cur_lines, cur_start = [], [], 0
    def flush(end, kind):
        if not cur_lines:
            return
        body = "\n".join(cur_lines)
        chunks.append({
            "chunk_id": f"{base_id}#{len(chunks)+1}",
            "kind": kind,
            "start": cur_start,
            "end": end,
            "text": body[:CHUNK_MAX_CHARS],
            "has_more": len(body) > CHUNK_MAX_CHARS,
            "n_chars": len(body),
        })
    offset = 0
    for ln in lines:
        m = HEADING_RE.match(ln)
        if m and offset > 1500:  # real headings mid-text only after some prose
            flush(offset, "section")
            cur, cur_lines, cur_start = [], [], offset
        cur_lines.append(ln)
        offset += len(ln) + 1
    flush(len(text), "tail")
    if not chunks and text.strip():
        chunks.append({"chunk_id": f"{base_id}#1", "kind": "body",
                       "start": 0, "end": len(text), "text": text[:CHUNK_MAX_CHARS],
                       "has_more": len(text) > CHUNK_MAX_CHARS, "n_chars": len(text)})
    return chunks


def walk_library(root):
    files = []
    seen_dirs = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__MACOSX", "node_modules"})
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            if p in seen_dirs:
                continue
            seen_dirs.add(p)
            ext = os.path.splitext(name)[1].lower()
            rel = os.path.relpath(p, root).replace("\\", "/")
            stat = os.stat(p)
            files.append({
                "relpath": rel, "name": name, "ext": ext,
                "size": stat.st_size, "mtime": int(stat.st_mtime),
                "sha256": sha256_file(p),
            })
    return files


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args(argv)

    files = walk_library(args.root)[: args.limit]

    chunks, errors, warns, needs_tool = [], [], [], []
    dedup = {}
    families = {}

    for f in files:
        p = os.path.join(args.root, f["relpath"])
        ext = f["ext"]
        text = None
        meta_extra = {}
        if ext in TEXT_EXTS:
            try:
                text = open(p, "rb").read().decode("utf-8-sig", errors="replace")
            except OSError as e:
                errors.append({"file": f["relpath"], "error": f"unreadable:{e}"})
        elif ext in BINARY_TYPES or ext in IMG_TYPES:
            t, extra = extract_native(p)
            text, meta_extra = t, extra or {}
            if text is None:
                t2, _ = extract_cli(p, ext)
                if t2:
                    text, meta_extra = t2, {}
        if text is None:
            st = "needs_tool"
            why = f"{ext or 'no-ext'}: no extractor (native lib absent, no CLI)"
            if ext in IMG_TYPES:
                why = "image raster — OCR text layer only via ocr-tool"
                st = "needs_tool"
            needs_tool.append({"file": f["relpath"], "status": st, "why": why})
            if args.ledger:
                ledger_append(args.ledger, "extract_skip", f["relpath"], why, status="warn")
            # still index the honest facts below
        if text:
            cks = split_sections(text, f["sha256"][:12])
            for ck in cks:
                ck["file"] = f["relpath"]
            chunks.extend(cks)

        fam_key = None
        if text or ext in TEXT_EXTS:
            # crude title guess: first non-empty short line of native text
            guess = None
            if text:
                for ln in text.splitlines():
                    t = ln.strip().lstrip("# ").strip()
                    if 4 < len(t) < 120 and not t.startswith("["):
                        guess = t
                        break
            if guess:
                fam_key = norm_title(guess)
                if not fam_key:
                    fam_key = "unknown-family"

        if fam_key and fam_key != "unknown-family":
            fam = families.setdefault(fam_key, {"title_guess": guess or "", "members": []})
            fam["members"].append({
                "relpath": f["relpath"], "sha256": f["sha256"],
                "variant_rank": variant_rank(f["name"]),
                "is_image": ext in IMG_TYPES,
            })
        # exact-dup detection
        if f["sha256"]:
            dedup.setdefault(f["sha256"], []).append(f["relpath"])

        if args.ledger:
            ledger_append(args.ledger, "scan_file", f["relpath"],
                          f"size={f['size']} sha256={f['sha256']} ext={ext}",
                          status="ok",
                          extra={"type": ext or "none"})

    alias_chains = []
    for fam_key, fam in families.items():
        if len(fam["members"]) < 2:
            continue
        members = sorted(fam["members"], key=lambda m: m["variant_rank"])
        by_hash = {}
        for m in members:
            by_hash.setdefault(m["sha256"], []).append(m["relpath"])
        chains = []
        for h, paths in by_hash.items():
            if len(paths) > 1:
                chains.append({"sha256": h, "paths": paths,
                               "resolution": f"identical content: prefer canonical relpath {paths[0]}"})
        if chains:
            alias_chains.append({"family": fam_key, "title_guess": fam["title_guess"],
                                 "chains": chains, "recommended_canonical": members[-1]["relpath"]})

    summary = {
        "files_total": len(files),
        "chunks_total": len(chunks),
        "chunks_with_text": sum(1 for c in chunks if c["text"].strip()),
        "families": len(families),
        "version_families_multi": sum(1 for v in families.values() if len(v["members"]) >= 2),
        "exact_dup_groups": sum(1 for v in dedup.values() if len(v) > 1),
        "needs_tool": len(needs_tool),
        "errors": len(errors),
    }
    index = {
        "root": os.path.abspath(args.root),
        "scanned_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": summary,
        "files": files,
        "chunks": chunks,
        "dedup_groups": {h: v for h, v in dedup.items() if len(v) > 1},
        "families": {k: {"title_guess": v["title_guess"], "member_count": len(v["members"]),
                         "members": [{"relpath": m["relpath"], "sha256": m["sha256"],
                                     "rank": m["variant_rank"], "is_image": m["is_image"]} for m in v["members"]]}
                     for k, v in families.items() if len(v["members"]) >= 2},
        "aliases": alias_chains,
        "needs_tool": needs_tool,
        "errors": errors,
    }
    write_json(args.out, index)
    print(json.dumps(summary, ensure_ascii=False))

    if needs_tool:
        print("WARN files needing an extractor:", len(needs_tool), file=sys.stderr)


if __name__ == "__main__":
    main()