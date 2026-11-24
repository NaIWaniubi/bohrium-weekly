# -*- coding: utf-8 -*-
"""Evidence-bounded review helpers; stdlib only, Python >= 3.8."""
import re
from collections import defaultdict

import review_enhance as _review
try:
    from version_metadata import apply_canonical_to_screening as _apply_canonical
except Exception:
    _apply_canonical = None
try:
    from classify_enhance import extract_admin_notes as _admin_notes
except Exception:
    _admin_notes = None

__all__ = ["build_claim_matrix", "refine_screening", "induce_themes",
           "detect_claim_conflicts", "build_gap_matrix", "compose_review",
           "quality_report"]

_SENT = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
_HEAD = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_LABEL = re.compile(r"^(abstract|introduction|background|methods?|materials?|results?|findings?|limitations?|conclusions?|摘要|引言|背景|方法|结果|结论|局限|限制)\s*[:：]\s*(.*)$", re.I)
_PLACE = re.compile(r"candidate\s+evidence|agent\s+must\s+assess|unassessed|placeholder|候选证据|待评估|占位|尚未完成证据强度判定", re.I)
_NEG = re.compile(r"\b(?:not|no|without|never|neither|nor|cannot|can't|unable\s+to|fail(?:s|ed|ure)?\s+to|lack\s+of)\b|不|未|无", re.I)
_DIR = re.compile(r"\b(increas\w*|higher|improv\w*|enhanc\w*|promot\w*|elevat\w*|rais\w*|benefit\w*|effective|support\w*|decreas\w*|reduc\w*|lower\w*|worsen\w*|impair\w*|diminish\w*|harm\w*|ineffective)\b|提高|增加|上升|改善|增强|促进|有效|支持|降低|减少|下降|恶化|减弱|有害|无效", re.I)
_UP = re.compile(r"^(?:increas|higher|improv|enhanc|promot|elevat|rais|benefit|effective|support)|提高|增加|上升|改善|增强|促进|有效|支持", re.I)
_METHODS = [
    ("graph neural network", r"graph neural network|\bGNNs?\b|图神经网络"),
    ("edge-conditioned convolution", r"edge[- ]conditioned convolution"),
    ("DFT", r"\bDFT\b|density functional"),
    ("molecular dynamics", r"molecular dynamics|\bMD\b simulations?"),
    ("cryo-TEM", r"cryo[- ]TEM"), ("operando XRD", r"operando\s+XRD"),
    ("impedance spectroscopy", r"impedance spectroscopy"),
    ("randomized trial", r"randomi[sz]ed (?:controlled )?trial|\bRCT\b"),
    ("observational study", r"observational study|cohort study|case[- ]control"),
    ("in vitro", r"\bin\s+vitro\b"), ("in vivo", r"\bin\s+vivo\b"),
    ("questionnaire", r"questionnaire|survey instrument"),
]
_METHODS = [(name, re.compile(pattern, re.I)) for name, pattern in _METHODS]
_PAIRS = [
    (re.compile(r"\bDFT\b|density functional|\bin\s+silico\b|computational", re.I), re.compile(r"\bin\s+vivo\b|experimental|operando", re.I)),
    (re.compile(r"\bsupervised\b", re.I), re.compile(r"\bunsupervised\b", re.I)),
    (re.compile(r"\bsingle[- ]task\b", re.I), re.compile(r"\bmulti[- ]task\b", re.I)),
]
_EXCLUSIVE = re.compile(r"incompatible|mutually exclusive|cannot be combined|not compatible|instead of|rather than|alternative|versus|\bvs\.?\b|互斥|不相容|不能结合", re.I)
_COMPATIBLE = re.compile(r"compatible|can be combined|combined|integrat\w*|hybrid|可结合|兼容|联合", re.I)


def _d(x): return x if isinstance(x, dict) else {}
def _l(x): return list(x) if isinstance(x, (list, tuple)) else []
def _t(x): return x if isinstance(x, str) else (str(x) if isinstance(x, (int, float, bool)) else "")
def _c(x): return re.sub(r"\s+", " ", _t(x)).strip()
def _fid(x):
    x = _d(x)
    return _t(x.get("relpath") or x.get("file") or x.get("path") or x.get("name"))
def _p(x): return _t(x).strip().replace("\\", "/")
def _files(index): return [r for r in _l(index.get("files") if isinstance(index, dict) else index) if isinstance(r, dict) and _fid(r)]
def _chunks(index, chunks=None):
    if chunks is None: chunks = index.get("chunks") if isinstance(index, dict) else None
    if isinstance(chunks, dict): chunks = chunks.get("chunks")
    return [r for r in _l(chunks) if isinstance(r, dict)]
def _fc(chunks, name): return [r for r in chunks if _fid(r) == name]
def _meta(row):
    out = {}
    for src in (_d(row).get("meta"), _d(row).get("metadata"), _d(row)):
        if isinstance(src, dict): out.update({k: v for k, v in src.items() if k not in {"meta", "metadata"} and v is not None})
    return out
def _sha(row): return _t(_d(row).get("sha256") or _d(row).get("sha256_file"))
def _base(chunk):
    try: return int(chunk.get("start") or chunk.get("offset", [0])[0] or 0)
    except (TypeError, ValueError, IndexError): return 0
def _names(included):
    if isinstance(included, dict): included = included.get("files") or included.get("included_files") or []
    if isinstance(included, str): included = [included]
    out = []
    for item in _l(included):
        name = _fid(item) if isinstance(item, dict) else _p(item)
        if name and name not in out: out.append(name)
    return out


def _choose(paths, index):
    by = {_fid(r): r for r in _files(index)}
    def score(path):
        low, row = path.casefold(), by.get(path, {})
        meta = _meta(row)
        return (100 if re.search(r"(?:^|[_\-.])final(?:$|[_\-.])", low) and not re.search(r"draft|old|copy|alias", low) else 0,
                30 if not re.search(r"(?:^|/)(?:aliases?|copies|duplicates?)(?:/|$)", low) else 0,
                20 if row.get("readable") is True else 0,
                sum(bool(meta.get(k)) for k in ("title", "year", "doi", "source_type")), len(path))
    return max(paths, key=score)


def _merge(target, source):
    if not isinstance(source, dict): return
    for key in ("alias_map", "canonical_map", "canonical_overrides"):
        for alias, primary in _d(source.get(key)).items():
            if isinstance(primary, str) and alias and primary: target[_p(alias)] = _p(primary)
    for alias, primary in source.items():
        if alias in {"alias_map", "canonical_map", "canonical_overrides"}: continue
        if isinstance(primary, str) and alias and primary: target[_p(alias)] = _p(primary)


def _cmap(index, canonical_map):
    index, out = _d(index), {}
    by_sha = defaultdict(list)
    for row in _files(index):
        if _sha(row): by_sha[_sha(row).lower()].append(_fid(row))
    for paths in by_sha.values():
        if len(paths) > 1:
            primary = _choose(paths, index)
            out.update({path: primary for path in paths if path != primary})
    for row in _l(index.get("aliases")):
        if not isinstance(row, dict): continue
        primary = _p(row.get("recommended_canonical") or row.get("canonical") or row.get("target"))
        paths = []
        for item in _l(row.get("members")) + _l(row.get("paths")):
            paths.append(_fid(item) if isinstance(item, dict) else _p(item))
        for chain in _l(row.get("chains")):
            if isinstance(chain, dict): paths.extend(_p(x) for x in _l(chain.get("paths")))
        if primary: out.update({path: primary for path in paths if path and path != primary})
    if _admin_notes:
        try:
            admin = _admin_notes(index)
            out.update({_p(a): _p(p) for a, p in _d(admin.get("alias_map")).items()})
            for rel in _l(admin.get("version_relations")):
                if isinstance(rel, dict) and rel.get("older") and rel.get("newer"):
                    out[_p(rel["older"])] = _p(rel["newer"])
        except Exception: pass
    if isinstance(canonical_map, (list, tuple)):
        for row in canonical_map:
            if isinstance(row, dict):
                a = row.get("alias") or row.get("alias_path") or row.get("source") or row.get("path")
                p = row.get("canonical") or row.get("canonical_path") or row.get("target") or row.get("recommended_canonical")
                if a and p: out[_p(a)] = _p(p)
    else: _merge(out, canonical_map)
    resolved = {}
    for start in list(out):
        cur, seen = start, set()
        while cur in out and cur not in seen:
            seen.add(cur); nxt = out[cur]
            if not nxt or nxt == cur: break
            cur = nxt
        resolved[start] = cur
    for row in _files(index): resolved.setdefault(_fid(row), _fid(row))
    return resolved


def _resolve(name, mapping):
    cur, seen = _p(name), set()
    while cur in mapping and cur not in seen:
        seen.add(cur); nxt = mapping.get(cur)
        if not nxt or nxt == cur: break
        cur = nxt
    return cur


def _canon(index, included, canonical_map):
    mapping = _cmap(index, canonical_map)
    by = {_fid(r): r for r in _files(index)}
    chunks = _chunks(index)
    selected = []
    for requested in _names(included):
        primary = _resolve(requested, mapping)
        if primary not in by: continue
        if _review._is_administrative(by[primary], _fc(chunks, primary)): continue
        if primary not in selected: selected.append(primary)
    return selected, mapping


def _stype(label):
    low = _c(label).casefold()
    if re.search(r"limit|局限|限制", low): return "limitation"
    if re.search(r"method|material|方法", low): return "method"
    if re.search(r"result|finding|conclusion|结果|结论", low): return "result"
    return "background"


def _infer(sentence):
    if re.search(r"limitation|does not|do not|without|fails?|lack|unable|cannot|only|remains?|beyond|not handle|restricted|局限|不能|无法|未", sentence, re.I): return "limitation"
    if re.search(r"\b(used|using|evaluate\w*|train\w*|method|approach|model|DFT|simulation|experiment)\b|方法|模型|训练|计算", sentence, re.I): return "method"
    if re.search(r"\b(reduc\w*|increas\w*|improv\w*|worsen\w*|show\w*|found|significant|relative to|outperform\w*)\b|显示|表明|提高|降低|改善|显著", sentence, re.I): return "result"
    return "background"


def _units(chunk):
    body, section, cursor = _t(chunk.get("text")), "background", 0
    for raw in body.splitlines(keepends=True):
        line, stripped = raw.rstrip("\r\n"), raw.strip()
        head = _HEAD.match(stripped)
        if head:
            section = _stype(head.group(1)); cursor += len(raw); continue
        if not stripped or re.match(r"^\[(?:page|slide|sheet|table)\b", stripped, re.I):
            cursor += len(raw); continue
        label = _LABEL.match(stripped)
        content = label.group(2) if label else line
        if label: section = _stype(label.group(1))
        leading = len(line) - len(line.lstrip())
        base = leading + (label.start(2) if label else 0)
        for match in _SENT.finditer(content):
            sentence = match.group(0).strip()
            if sentence:
                skip = len(match.group(0)) - len(match.group(0).lstrip())
                start = cursor + base + match.start() + skip
                yield sentence, start, start + len(sentence), section
        cursor += len(raw)


def _ref(claim, number):
    return {"evidence_id": _t(claim.get("evidence_id")) or "E%d" % number,
            "file": _t(claim.get("file")), "sha256": _t(claim.get("sha256")),
            "chunk_id": claim.get("chunk_id"), "offset": list(claim.get("offset") or []),
            "quote": _t(claim.get("quote")), "kind": _t(claim.get("claim_type"))}


def build_claim_matrix(index, included_files, canonical_map=None, chunks=None):
    index = _d(index); names, _ = _canon(index, included_files, canonical_map)
    rows, seen = _chunks(index, chunks), set()
    by = {_fid(r): r for r in _files(index)}; claims = []
    for name in names:
        row, fchunks = by[name], _fc(rows, name)
        full = bool(_review._has_full_text(row, fchunks))
        for chunk in sorted(fchunks, key=lambda x: (_base(x), _t(x.get("chunk_id")))):
            base = _base(chunk)
            for sentence, start, end, section in _units(chunk):
                if len(sentence) < 12 or len(_review._term_set(sentence)) < 2: continue
                if re.match(r"^(?:doi\s*[:：]?\s*)?10\.\d{4,9}/\S+$", sentence, re.I): continue
                key = (name, chunk.get("chunk_id"), start, end)
                if key in seen: continue
                seen.add(key)
                quote = _t(chunk.get("text"))[start - base:end - base]
                if quote != sentence: continue
                ctype = section if section != "background" else _infer(sentence)
                ref = {"file": name, "sha256": _sha(row), "chunk_id": chunk.get("chunk_id"),
                       "offset": [base + start, base + end], "quote": quote,
                       "kind": chunk.get("kind") or chunk.get("section_kind") or ctype, "full_text": full}
                assessed = _review.classify_evidence_strength(row, _meta(row), [ref])
                limits = list(assessed.get("limitations") or [])
                if ctype == "background": limits.append("背景性陈述不等同于本研究的结果证据")
                claims.append({"claim_id": "C%d" % (len(claims) + 1), "file": name,
                               "sha256": _sha(row), "chunk_id": chunk.get("chunk_id"),
                               "offset": [base + start, base + end], "quote": quote,
                               "claim_text": _c(sentence), "claim_type": ctype,
                               "strength": _t(assessed.get("strength")) or "insufficient",
                               "supports": _c(sentence), "limitations": limits})
    return claims


def _dup_row(original, primary, index):
    out, by = dict(_d(original)), {_fid(r): r for r in _files(index)}
    name = _fid(out); same = bool(name in by and primary in by and _sha(by[name]) and _sha(by[name]) == _sha(by[primary]))
    out.update({"decision": "exclude", "reason_code": "duplicate",
                "reason": "别名或旧版本按 canonical_map 归并到 %s" % primary,
                "duplicate_of": primary, "duplicate_basis": "sha256" if same else "canonical_map/version",
                "metadata": dict(_d(out.get("metadata")), duplicate_of=primary,
                                 duplicate_basis="sha256" if same else "canonical_map/version")})
    out["evidence_refs"] = _review._evidence_refs(_fc(_chunks(index), primary), "", 3, _sha(by.get(primary, {})))
    return out


def refine_screening(index, screening_log, canonical_map=None, protocol=None):
    index = _d(index); mapping = _cmap(index, canonical_map)
    if _apply_canonical:
        try:
            applied = _apply_canonical(index, screening_log, canonical_map=mapping, protocol=protocol)
            if isinstance(applied, dict) and isinstance(applied.get("screening_log"), list): screening_log = applied["screening_log"]
        except Exception: pass
    if not isinstance(screening_log, list) or not screening_log:
        screening_log = _review.screen_documents(index, _d(protocol), _chunks(index))
    originals = [r for r in _l(screening_log) if isinstance(r, dict)]
    bylog, byfile = {_fid(r): r for r in originals}, {_fid(r): r for r in _files(index)}
    rerun = {}
    if protocol is not None:
        filtered = dict(index); filtered["files"] = [r for r in _files(index) if _resolve(_fid(r), mapping) == _fid(r)]
        if filtered["files"]: rerun = {_fid(r): r for r in _review.screen_documents(filtered, _d(protocol), _chunks(index))}
    corrected = []
    for name in ([_fid(r) for r in _files(index)] or list(bylog)):
        original = bylog.get(name) or rerun.get(name) or {"file": name}
        row = byfile.get(name, {})
        if _review._is_administrative(row, _fc(_chunks(index), name)):
            out = dict(original)
            out.update({"decision": "exclude", "reason_code": "non_literature",
                        "reason": "行政说明、版本说明或清单文件，不是文献来源",
                        "metadata": dict(_d(out.get("metadata")), source_class="administrative_non_literature")})
            corrected.append(out); continue
        primary = _resolve(name, mapping)
        if primary != name:
            corrected.append(_dup_row(original, primary, index)); continue
        out = dict(rerun.get(name) or original); out["file"] = name
        if out.get("decision") == "exclude" and out.get("reason_code") == "duplicate":
            group = [r for r in originals if _resolve(_fid(r), mapping) == name]
            if any(r.get("decision") == "include" for r in group):
                out.update({"decision": "include", "reason_code": "included", "reason": "canonical 主文献；同组材料满足纳入条件"})
            else:
                out.update({"decision": "needs_review", "reason_code": "insufficient_information", "reason": "canonical 主文献的原筛选记录不一致，需要复核"})
        corrected.append(out)
    return {"screening_log": corrected, "prisma_flow": _review.build_prisma_flow(index, corrected), "canonical_map": mapping}


def induce_themes(index, included_files, canonical_map=None, question=""):
    index = _d(index); names, mapping = _canon(index, included_files, canonical_map)
    if not names: return []
    by = {_fid(r): r for r in _files(index)}
    filtered = dict(index); filtered["files"] = [by[n] for n in names]
    base = _review.cluster_themes(filtered, names, question)
    claims = build_claim_matrix(filtered, names, mapping, _chunks(index))
    byfile = defaultdict(list)
    for number, claim in enumerate(claims, 1): byfile[claim["file"]].append((number, claim))
    themes = []
    for theme in base:
        members = [n for n in _l(theme.get("member_files")) if n in names]
        if not members: continue
        numbered = [x for n in members for x in byfile.get(n, [])]
        quotes = [_ref(claim, number) for number, claim in numbered[:6]]
        if not quotes: quotes = [q for q in _l(theme.get("evidence_quotes")) if isinstance(q, dict)]
        themes.append({"theme_id": _t(theme.get("theme_id")) or "T%d" % (len(themes) + 1),
                       "label": _c(theme.get("label")) or " / ".join(_l(theme.get("top_terms"))[:3]),
                       "top_terms": list(_l(theme.get("top_terms"))), "member_files": members,
                       "claim_ids": [claim["claim_id"] for _, claim in numbered],
                       "evidence_quotes": quotes,
                       "boundary": "本批 %d 篇 canonical 纳入文献中归纳；仅反映传入材料，不代表领域全貌。" % len(members)})
    return themes


def _direction(sentence):
    if re.search(r"\b(?:no\s+effect|not\s+significant|ineffective)\b|无效|无效果|不显著", sentence, re.I) and not _DIR.search(sentence): return "down"
    for match in _DIR.finditer(sentence):
        value = "up" if _UP.match(match.group(0)) else "down"
        if _NEG.search(sentence[max(0, match.start() - 45):match.start()]): value = "down" if value == "up" else "up"
        return value
    return ""


def _msides(sentence):
    out = []
    for number, (left, right) in enumerate(_PAIRS, 1):
        if left.search(sentence): out.append((number, "left"))
        if right.search(sentence): out.append((number, "right"))
    return out


def _claims(rows):
    out, seen = [], set()
    for number, raw in enumerate(_l(rows), 1):
        if not isinstance(raw, dict) or not _t(raw.get("quote")): continue
        row = dict(raw); cid = _t(row.get("claim_id")) or "C%d" % number
        if cid in seen: cid = "C%d" % number
        seen.add(cid); row["claim_id"] = cid
        row["evidence_id"] = _t(row.get("evidence_id")) or "E%d" % number
        out.append(row)
    return out


def detect_claim_conflicts(index, included_files, claim_matrix, themes=None):
    index = _d(index); names, _ = _canon(index, included_files, None)
    allowed = set(names) if names else {_t(r.get("file")) for r in _l(claim_matrix) if isinstance(r, dict)}
    claims = [r for r in _claims(claim_matrix) if _t(r.get("file")) in allowed]
    theme_by = {}
    for theme in _l(themes):
        if isinstance(theme, dict):
            for name in _l(theme.get("member_files")): theme_by[_t(name)] = theme
    statements = []
    for claim in claims:
        sentence = _c(claim.get("claim_text") or claim.get("quote")); terms = _review._term_set(sentence)
        direction, methods = _direction(sentence), _msides(sentence)
        if len(terms) >= 2 and (direction or methods): statements.append((claim, sentence, terms, direction, methods))
    conflicts, seen = [], set()
    for i, left in enumerate(statements):
        for right in statements[i + 1:]:
            a, b = left[0], right[0]
            if a["file"] == b["file"]: continue
            shared = left[2] & right[2]
            if len(shared) < 2: continue
            ta, tb = theme_by.get(a["file"]), theme_by.get(b["file"])
            if ta and tb and ta.get("theme_id") != tb.get("theme_id"): continue
            dc = bool(left[3] and right[3] and left[3] != right[3]); mc = False
            for pair, side in left[4]:
                if any(p == pair and s != side for p, s in right[4]):
                    both = left[1] + " " + right[1]
                    if _EXCLUSIVE.search(both) or (_EXCLUSIVE.search(left[1]) and _COMPATIBLE.search(right[1])) or (_COMPATIBLE.search(left[1]) and _EXCLUSIVE.search(right[1])): mc = True; break
            if not dc and not mc: continue
            theme = _c((ta or tb or {}).get("label") or sorted(shared, key=lambda x: (-len(x), x))[0])
            kind = "opposite_conclusion" if dc else "method_incompatibility"
            key = (a["file"], b["file"], theme, kind)
            if key in seen: continue
            seen.add(key)
            ra, rb = _ref(a, claims.index(a) + 1), _ref(b, claims.index(b) + 1)
            conflicts.append({"theme": theme,
                "side_a": {"file": a["file"], "claim_id": a["claim_id"], "claim": left[1], "evidence_ref": ra},
                "side_b": {"file": b["file"], "claim_id": b["claim_id"], "claim": right[1], "evidence_ref": rb},
                "claim_ids": [a["claim_id"], b["claim_id"]], "evidence_refs": [ra, rb],
                "confidence": "high" if dc and len(shared) >= 2 else "medium",
                "limitations": ["仅基于本批 claim 级原文引文，未评估研究设计、样本与测量差异", "方向词和否定词采用词法规则，仍需人工复核上下文"],
                "kind": kind})
    return conflicts


def _methods_objects(theme, claims, included):
    methods, objects, by = set(), set(), {_fid(r): r for r in _l(included) if isinstance(r, dict)}
    for name in [_t(x) for x in _l(theme.get("member_files"))]:
        meta = _meta(by.get(name, {}))
        rm, ro = meta.get("methods") or meta.get("method") or [], meta.get("objects") or meta.get("object") or meta.get("subjects") or []
        methods.update(_c(x) for x in _l([rm] if isinstance(rm, str) else rm) if _c(x))
        objects.update(_c(x) for x in _l([ro] if isinstance(ro, str) else ro) if _c(x))
    text = " ".join(_c(c.get("claim_text") or c.get("quote")) for c in claims)
    methods.update(name for name, pattern in _METHODS if pattern.search(text))
    if not objects: objects.update(_c(x) for x in _l(theme.get("top_terms"))[:3] if _c(x))
    return sorted(methods), sorted(objects)


def build_gap_matrix(themes, included_files, claim_matrix, question=""):
    claims, byfile = _claims(claim_matrix), defaultdict(list)
    for claim in claims: byfile[_t(claim.get("file"))].append(claim)
    rows = []
    for theme in _l(themes):
        if not isinstance(theme, dict): continue
        members = [_t(x) for x in _l(theme.get("member_files")) if _t(x)]
        tc = [c for n in members for c in byfile.get(n, [])]
        methods, objects = _methods_objects(theme, tc, included_files)
        strengths = sorted({_t(c.get("strength")) or "insufficient" for c in tc})
        cell = {"theme_id": _t(theme.get("theme_id")), "label": _c(theme.get("label")),
                "methods": methods, "objects": objects, "strengths": strengths,
                "claim_ids": [_t(c.get("claim_id")) for c in tc]}
        rows.append({"theme": theme, "members": members, "claims": tc, "methods": methods,
                     "objects": objects, "strengths": strengths, "coverage_cell": cell})
    if not rows: return []
    def insufficient(row, detail):
        return [{"gap": "insufficient_evidence：%s；不能据此声称全球研究空白。" % detail,
                 "coverage_cell": row["coverage_cell"], "missing_combination": "insufficient_evidence",
                 "affected_files": list(row["members"]),
                 "limitations": ["仅基于传入的本批材料和可提取字段", "未观察到不等于未开展，可能是检索、提取或报告局限"]}]
    if len(rows) < 2: return insufficient(rows[0], "只有一个主题或缺少跨主题比较基线")
    allm, allo = sorted({m for r in rows for m in r["methods"]}), sorted({o for r in rows for o in r["objects"]})
    if not allm and not allo: return insufficient(rows[0], "主题缺少可提取的方法、对象与证据强度")
    gaps = []
    for row in rows:
        label = _c(row["theme"].get("label")) or row["coverage_cell"]["theme_id"]
        if not row["claims"] or not row["strengths"] or set(row["strengths"]) <= {"insufficient"}:
            gaps.extend(insufficient(row, "主题「%s」缺少足够的 claim 级证据" % label))
        for method in allm:
            if method not in row["methods"]:
                gaps.append({"gap": "本批材料中，主题「%s」未观察到方法「%s」的证据；不能推断全球研究空白。" % (label, method),
                             "coverage_cell": row["coverage_cell"], "missing_combination": "method=%s" % method,
                             "affected_files": list(row["members"]), "limitations": ["仅表示本批未观察到该方法", "同义方法可能因提取词表而漏检"]})
        for obj in allo:
            if obj not in row["objects"]:
                gaps.append({"gap": "本批材料中，主题「%s」未观察到对象「%s」的证据；不能推断全球研究空白。" % (label, obj),
                             "coverage_cell": row["coverage_cell"], "missing_combination": "object=%s" % obj,
                             "affected_files": list(row["members"]), "limitations": ["仅表示本批未观察到该对象", "对象字段可能来自主题词回退，需人工复核"]})
    return gaps[:24]


def _safe_text(claim):
    value = _c(claim.get("claim_text") or claim.get("quote"))
    if _PLACE.search(value): value = _c(claim.get("quote"))
    return "" if _PLACE.search(value) else value


def compose_review(question, protocol, screening_log, prisma_flow, themes, claim_matrix, conflicts, gaps):
    protocol, flow = _d(protocol), _d(prisma_flow)
    claims = _claims(claim_matrix); byid = {c["claim_id"]: c for c in claims}
    themes = [r for r in _l(themes) if isinstance(r, dict)]
    conflicts = [r for r in _l(conflicts) if isinstance(r, dict)]; gaps = [r for r in _l(gaps) if isinstance(r, dict)]
    cites = defaultdict(list)
    def cite(c): return ("[%s]" % c["claim_id"] if c.get("claim_id") else "") + ("[%s]" % c["evidence_id"] if c.get("evidence_id") else "")
    lines = ["# 综述草稿", "", "研究问题：%s" % (_c(question) or _c(protocol.get("question")) or "未提供"), "", "## 筛选范围", "", "| 项目 | 计数 |", "| --- | ---: |"]
    for label, key in (("识别记录", "identified"), ("去重", "duplicates_removed"), ("筛选", "screened"), ("纳入", "included"), ("排除", "excluded"), ("待复核", "needs_review")):
        lines.append("| %s | %s |" % (label, flow.get(key, 0)))
    if protocol.get("inclusion"): lines.extend(["", "纳入标准：%s" % "；".join(_c(x) for x in _l(protocol.get("inclusion")))])
    if protocol.get("exclusion"): lines.extend(["", "排除标准：%s" % "；".join(_c(x) for x in _l(protocol.get("exclusion")))])
    lines.extend(["", "## 材料报告", ""]); count = 0
    for claim in claims:
        text = _safe_text(claim)
        if not text: continue
        strength = (_t(claim.get("strength")) or "insufficient").lower(); ref = cite(claim)
        if strength == "insufficient": line = "- 无法确定：原文写道“%s”，但证据强度不足，不能据此形成强结论。%s" % (text, ref)
        elif strength == "controversial": line = "- 材料报告：原文存在争议性表述：“%s”。%s" % (text, ref)
        elif strength == "suggestive": line = "- 材料报告：原文提示“%s”（证据强度 suggestive，不能视为已证实）。%s" % (text, ref)
        else: line = "- 材料报告：%s（证据强度 %s）。%s" % (text.rstrip("。.!！?"), strength, ref)
        lines.append(line); cites["material_reports"].extend(["[%s]" % claim["claim_id"], "[%s]" % claim["evidence_id"]]); count += 1
    if not count: lines.append("- 无法确定：没有可回链的 claim/evidence，未生成实质结论。")
    lines.extend(["", "## 主题归纳", ""]); inferences = []
    for theme in themes:
        tc = [byid[_t(x)] for x in _l(theme.get("claim_ids")) if _t(x) in byid]
        label = _c(theme.get("label")) or _t(theme.get("theme_id"))
        if not tc:
            lines.append("- 无法确定：主题「%s」缺少 claim 级原文证据。" % label); continue
        refs = "".join(cite(c) for c in tc[:4])
        lines.append("- 材料报告：主题「%s」由 %d 篇主文献中的 %d 条可回链主张支持。%s" % (label, len({c["file"] for c in tc}), len(tc), refs))
        cites["themes"].extend(["[%s]" % c["claim_id"] for c in tc[:4]] + ["[%s]" % c["evidence_id"] for c in tc[:4]])
        if len(tc) >= 2:
            inferences.append("- 合理推断：在本批材料中，主题「%s」可能围绕 %s 形成重复关注；该推断不超出本批证据。%s" % (label, ", ".join(_l(theme.get("top_terms"))[:4]) or label, refs))
            cites["inferences"].extend(["[%s]" % c["claim_id"] for c in tc[:4]] + ["[%s]" % c["evidence_id"] for c in tc[:4]])
    if not themes: lines.append("- 无法确定：没有可归纳的主题。")
    lines.extend(["", "## 合理推断", ""]); lines.extend(inferences or ["- 无法确定：证据数量或主题覆盖不足，不提出实质推断。"]); lines.extend(["", "## 无法确定：冲突、覆盖缺口与边界", ""])
    for conflict in conflicts:
        cc = [byid[_t(x)] for x in _l(conflict.get("claim_ids")) if _t(x) in byid]
        refs = "".join(cite(c) for c in cc)
        lines.append("- 无法确定：主题「%s」两侧材料报告方向或方法不相容，当前无法判断哪一侧更可靠。%s" % (_c(conflict.get("theme")) or "同一主题", refs))
        cites["conflicts"].extend(["[%s]" % c["claim_id"] for c in cc] + ["[%s]" % c["evidence_id"] for c in cc])
    for gap in gaps:
        affected = {_t(x) for x in _l(gap.get("affected_files"))}; cc = [c for c in claims if c["file"] in affected][:4]
        refs, text = "".join(cite(c) for c in cc), _c(gap.get("gap"))
        if "insufficient_evidence" in text: line = "- 无法确定：覆盖矩阵证据不足，不能识别有证据支持的研究缺口。%s" % refs
        else: line = "- 无法确定：%s该判断仅限本批覆盖矩阵，不代表全球研究空白。%s" % (text if text.endswith("。") else text + "。", refs)
        lines.append(line); cites["gaps"].extend(["[%s]" % c["claim_id"] for c in cc] + ["[%s]" % c["evidence_id"] for c in cc])
    if not conflicts and not gaps: lines.append("- 无法确定：当前证据不足以识别冲突或覆盖缺口；这不代表不存在。%s" % "".join(cite(c) for c in claims[:3]))
    lines.extend(["", "## 引用索引", ""])
    for c in claims:
        lines.append("- [%s] %s | sha256=%s | chunk=%s | offset=%s" % (c["claim_id"], c["file"], c.get("sha256", ""), c.get("chunk_id", ""), c.get("offset", [])))
        lines.append("- [%s] %s | chunk=%s | offset=%s | quote=%s" % (c["evidence_id"], c["file"], c.get("chunk_id", ""), c.get("offset", []), _c(c.get("quote"))))
    if not claims: lines.append("- 无 C/E 类证据引用。")
    uncertainty = ["仅基于传入材料，不代表领域全貌。"]
    if not claims: uncertainty.append("没有可回链的 claim/evidence，未生成实质结论。")
    if conflicts: uncertainty.append("冲突检测为词法启发式，需要人工复核研究情境。")
    if gaps: uncertainty.append("覆盖矩阵缺口仅表示本批未观察到，不等于全球空白。")
    if any(_t(c.get("strength")) == "insufficient" for c in claims): uncertainty.append("部分主张证据强度不足，仅作为待核线索。")
    return {"text": "\n".join(lines).rstrip() + "\n",
            "section_citations": {k: list(dict.fromkeys(v)) for k, v in cites.items()},
            "uncertainty": uncertainty, "status": "partial"}


def _verifiable(claim, index):
    quote, offset = _t(claim.get("quote")), claim.get("offset")
    if not quote or not isinstance(offset, (list, tuple)) or len(offset) != 2: return False
    try: start, end = int(offset[0]), int(offset[1])
    except (TypeError, ValueError): return False
    if start < 0 or end < start: return False
    chunk = next((c for c in _chunks(index) if _fid(c) == _t(claim.get("file")) and c.get("chunk_id") == claim.get("chunk_id")), None)
    return True if chunk is None else _t(chunk.get("text"))[start - _base(chunk):end - _base(chunk)] == quote


def quality_report(index=None, screening_log=None, prisma_flow=None, themes=None,
                   claim_matrix=None, conflicts=None, gaps=None, review=None,
                   included_files=None, canonical_map=None, **kwargs):
    index = _d(index); log = [r for r in _l(screening_log) if isinstance(r, dict)]
    flow = _d(prisma_flow) or _review.build_prisma_flow(index, log)
    themes = [r for r in _l(themes) if isinstance(r, dict)]; claims = _claims(claim_matrix)
    conflicts = [r for r in _l(conflicts) if isinstance(r, dict)]; gaps = [r for r in _l(gaps) if isinstance(r, dict)]
    review = _d(review or kwargs.get("draft")); text = _t(review.get("text")); mapping = _cmap(index, canonical_map)
    verified = sum(_verifiable(c, index) for c in claims)
    included = int(flow.get("included") or sum(r.get("decision") == "include" for r in log))
    duplicates = int(flow.get("duplicates_removed") or sum(r.get("reason_code") == "duplicate" for r in log))
    canonical_included = sum(r.get("decision") == "include" and _resolve(_fid(r), mapping) == _fid(r) for r in log)
    dupmembers = sum(len(_l(t.get("member_files"))) != len({_t(x) for x in _l(t.get("member_files"))}) for t in themes)
    themec = sum(bool(_l(t.get("claim_ids"))) for t in themes); placeholders = len(_PLACE.findall(text))
    uncited = sum(bool(re.match(r"^- (?:材料报告|合理推断|无法确定)", line)) and "[C" not in line and "[E" not in line for line in text.splitlines())
    prisma_ok = (int(flow.get("included", 0)) + int(flow.get("excluded", 0)) + int(flow.get("needs_review", 0)) == int(flow.get("screened", 0)) and int(flow.get("identified", 0)) == int(flow.get("screened", 0)) + int(flow.get("duplicates_removed", 0)))
    counts = {"indexed_files": len(_files(index)) or len(log), "included": included, "duplicates_removed": duplicates,
              "canonical_included": canonical_included, "claims": len(claims), "verifiable_claims": verified,
              "claim_files": len({c["file"] for c in claims}), "themes": len(themes), "themes_with_claims": themec,
              "duplicate_theme_members": dupmembers, "conflicts": len(conflicts), "gaps": len(gaps),
              "insufficient_gaps": sum("insufficient_evidence" in _t(g.get("gap")) for g in gaps),
              "placeholder_hits": placeholders, "uncited_substantive_lines": uncited,
              "section_citation_groups": len(_d(review.get("section_citations"))),
              "uncertainty_items": len(_l(review.get("uncertainty"))), "prisma_consistent": prisma_ok}
    def group(label, maximum, earned):
        earned = max(0, min(maximum, int(round(earned))))
        return {"label": label, "max_score": maximum, "earned_score": earned,
                "status": "met" if earned == maximum else ("partial" if earned else "missing"), "metrics": counts}
    er = verified / max(1, len(claims)); cr = canonical_included / max(1, included); tr = themec / max(1, len(themes))
    rubric = {
        "actual_effect": group("实际效果", 40, 20 * er + 10 * cr + (10 if prisma_ok else 0)),
        "usage_gain": group("使用增益", 25, 10 * er + 10 * tr + (5 if review.get("status") == "partial" and not placeholders else 0)),
        "innovation": group("创新", 15, (5 if conflicts or claims else 0) + (5 if gaps or themes else 0) + (5 if not placeholders and claims else 0)),
        "reliability": group("可靠", 10, (4 if prisma_ok else 0) + (3 if not dupmembers else 0) + (3 if claims and er >= 0.999 else 0)),
        "ecosystem": group("生态", 10, (4 if review.get("section_citations") else 0) + (3 if review.get("uncertainty") else 0) + (3 if review.get("status") == "partial" else 0)),
    }
    return {"rubric_mapped": rubric, "counts": counts,
            "summary": {"total_possible": 100, "total_earned": sum(v["earned_score"] for v in rubric.values()),
                        "status": "met" if all(v["status"] == "met" for v in rubric.values()) else "partial"}}
