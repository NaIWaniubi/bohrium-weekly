"""bohrium-weekly skill engine — shared stdlib helpers.

stdlib only, no third-party dependencies. Every helper is deterministic and
writes machine-readable JSON so the agent can run one command per phase and
spend its model budget on judgment, not on parsing.
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path, obj):
    """Atomic-ish JSON writer: write temp then rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except Exception as e:
        return {"_parse_error": str(e), "_path": path}


def sha256_file(path, chunk_size=1 << 20):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff.\- ]+")
VERSION_SUFFIX_RE = re.compile(
    r"[_\-\s]*(draft|rev|revision|v?\d{1,2}|final|frozen|投稿稿|修改稿|终稿|草稿|v\d)"
)


def norm_title(raw):
    """Normalize a title for lineage/dedup comparison."""
    t = SAFE_RE.sub("", str(raw)).strip().lower()
    return VERSION_SUFFIX_RE.sub(" ", t).strip()


def variant_rank(title):
    """Cheap rank to sort draft->final chains newest-last within one family."""
    t = str(title).lower()
    score = 0
    for kw in ("final", "终稿", "投稿稿"):
        if kw in t:
            score += 100
    for kw in ("v1", "v2", "v3", "修订", "rev", "rev1", "rev2", "修改稿", "草稿"):
        m = re.search(r"(v?)(\d)(\d?)\b", t)
        if m:
            score += int(m.group(2)) * 10
    m = re.search(r"(?:draft|草稿|rev|修订|修改)[\s._-]*(\d)?", t)
    if m:
        score += (int(m.group(1) or 1)) * 5
    return score


# Atomic UTF-8 BOM/GBK tolerant text reading. PDFs/office get parsed by
# the platform-provided pdf-text-tool / office-renderer; this helper is for
# .md/.txt/.csv/.json artifacts.
def read_text_safe(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"


def ledger_append(ledger_path, action, subject, detail, status=STATUS_OK, extra=None):
    entry = {
        "ts": now_iso(),
        "action": action,
        "subject": subject,
        "detail": str(detail)[:500],
        "status": status,
    }
    if extra:
        entry.update(extra)
    ledger = read_json(ledger_path, [])
    if isinstance(ledger, list):
        ledger.append(entry)
    else:
        ledger = [ledger, entry]
    write_json(ledger_path, ledger)
    return entry


def banner(msg, code=0):
    print(msg, file=sys.stderr)
    sys.exit(code)