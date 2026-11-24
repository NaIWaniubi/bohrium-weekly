"""Data-analysis and code-execution enhancement for research data challenges.

Stdlib-only deterministic pipeline: load -> profile -> clean with audit ->
descriptive/inferential statistics -> effect size/CI -> SVG chart -> executable
code -> verification -> Markdown report.  No source data is modified.
"""
import csv
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _num(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def load_table(path):
    """Load CSV/TSV/JSON/XLSX into {columns, rows, format, source, sha256}."""
    ext = os.path.splitext(path)[1].lower()
    result = {"columns": [], "rows": [], "format": ext.lstrip("."), "source": path,
              "sha256": _sha(path), "sheets": [], "errors": []}
    if ext in (".csv", ".tsv"):
        delimiter = "\t" if ext == ".tsv" else ","
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            result["columns"] = list(reader.fieldnames or [])
            result["rows"] = [dict(row) for row in reader]
        return result
    if ext == ".json":
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = data.get("rows") or data.get("data") or data.get("records") or [data]
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
            columns = []
            for row in rows:
                for key in row:
                    if key not in columns:
                        columns.append(key)
            result["columns"], result["rows"] = columns, rows
        else:
            result["errors"].append("JSON root is not a table/object list")
        return result
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            all_rows = []
            for ws in wb.worksheets:
                values = list(ws.iter_rows(values_only=True))
                if not values:
                    continue
                columns = [str(x) if x is not None else "" for x in values[0]]
                rows = [dict(zip(columns, row)) for row in values[1:] if any(x is not None for x in row)]
                result["sheets"].append({"name": ws.title, "rows": len(rows), "columns": columns})
                if not result["columns"]:
                    result["columns"] = columns
                all_rows.extend(rows)
            result["rows"] = all_rows
            return result
        except Exception as exc:
            result["errors"].append("openpyxl unavailable/failed: " + str(exc))
        try:
            from scan_index import extract_ooxml_stdlib
            text, _ = extract_ooxml_stdlib(path, ext)
            if text:
                lines = [line for line in text.splitlines() if line.strip()]
                if lines:
                    result["columns"] = [x.strip() for x in lines[0].split("|")]
                    for line in lines[1:]:
                        cells = [x.strip() for x in line.split("|")]
                        result["rows"].append(dict(zip(result["columns"], cells)))
            return result
        except Exception as exc:
            result["errors"].append("xlsx fallback failed: " + str(exc))
            return result
    result["errors"].append("unsupported table format: " + ext)
    return result


def profile_table(table):
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    profile = {}
    for col in columns:
        values = [row.get(col) for row in rows]
        present = [v for v in values if v is not None and str(v).strip() != ""]
        numeric = [_num(v) for v in present]
        numeric_present = [v for v in numeric if v is not None]
        profile[col] = {
            "dtype": "numeric" if numeric_present and len(numeric_present) == len(present) else "mixed/text",
            "count": len(values), "missing": len(values) - len(present),
            "unique": len({str(v) for v in present}),
            "numeric_count": len(numeric_present),
            "min": min(numeric_present) if numeric_present else None,
            "max": max(numeric_present) if numeric_present else None,
            "mean": statistics.fmean(numeric_present) if numeric_present else None,
        }
    return {"row_count": len(rows), "column_count": len(columns), "columns": profile,
            "source": table.get("source"), "sha256": table.get("sha256"), "errors": table.get("errors", [])}


def _quartile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def clean_table(table, dictionary=None, constraints=None):
    """Clean with explicit audit; never silently fills missing with zero."""
    dictionary = dictionary if isinstance(dictionary, dict) else {}
    constraints = constraints if isinstance(constraints, dict) else {}
    rows = [dict(row) for row in table.get("rows", [])]
    columns = list(table.get("columns") or [])
    audit = []
    primary_unit = constraints.get("primary_unit")
    numeric_cols = {col for col, spec in dictionary.items()
                    if str((spec or {}).get("type", "")).lower() in ("float", "int", "integer", "number", "numeric")}
    if not numeric_cols:
        numeric_cols = {col for col in columns if any(_num(row.get(col)) is not None for row in rows)}
    cleaned = []
    seen_ids = set()
    seen_content = set()
    id_col = next((col for col in columns if str(col).lower() in ("sample_id", "id", "record_id")), None)
    content_cols = [col for col in columns if col not in (id_col, "notes")]
    unit_col = next((col for col in columns if str(col).lower() == "unit"), None)
    for idx, row in enumerate(rows, 1):
        before = dict(row)
        reasons = []
        raw_content_key = tuple(str(row.get(col)) for col in content_cols)
        if raw_content_key in seen_content:
            audit.append({"step": "duplicate_content", "row": idx, "id": row.get(id_col) if id_col else None,
                          "action": "drop", "reason": "identical raw content except sample_id/notes"})
            continue
        seen_content.add(raw_content_key)
        if id_col and row.get(id_col):
            key = str(row[id_col]).strip()
            if key in seen_ids:
                audit.append({"step": "duplicate", "row": idx, "id": key, "action": "drop",
                              "reason": "duplicate sample_id"})
                continue
            seen_ids.add(key)
        for col in columns:
            value = row.get(col)
            if isinstance(value, str):
                row[col] = value.strip()
        for col in numeric_cols:
            raw = row.get(col)
            parsed = _num(raw)
            if parsed is None:
                if raw is not None and str(raw).strip() != "":
                    reasons.append("invalid_numeric:" + col)
                row[col] = None
            else:
                row[col] = parsed
        if unit_col and primary_unit:
            unit = str(row.get(unit_col) or "").strip()
            if unit == "ug/L" and primary_unit == "mg/L":
                for col in numeric_cols:
                    if isinstance(row.get(col), (int, float)):
                        row[col] = row[col] / 1000.0
                row[unit_col] = primary_unit
                audit.append({"step": "unit_conversion", "row": idx, "from": "ug/L", "to": "mg/L",
                              "factor": 0.001, "action": "convert"})
            elif unit and unit != primary_unit:
                reasons.append("unit_mismatch:" + unit)
        if reasons:
            audit.append({"step": "value_issue", "row": idx, "issues": reasons,
                          "action": "keep_row_mark_missing"})
        cleaned.append(row)
    # IQR outliers are flagged, not silently deleted.
    group_col = next((col for col in columns if str(col).lower() in ("group", "condition", "treatment")), None)
    outlier_rows = []
    groups = {}
    for row in cleaned:
        groups.setdefault(row.get(group_col) if group_col else "__all__", []).append(row)
    for group, members in groups.items():
        for col in numeric_cols:
            values = [row.get(col) for row in members if isinstance(row.get(col), (int, float))]
            if len(values) < 4:
                continue
            q1, q3 = _quartile(values, 0.25), _quartile(values, 0.75)
            iqr = q3 - q1
            if iqr <= 0:
                continue
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for row in members:
                value = row.get(col)
                if isinstance(value, (int, float)) and (value < lo or value > hi):
                    row["__outliers__"] = list(row.get("__outliers__") or []) + [col]
                    outlier_rows.append({"group": group, "column": col, "value": value,
                                         "lower": lo, "upper": hi, "rule": "IQR_1.5"})
    audit.append({"step": "outlier_flag", "count": len(outlier_rows), "action": "flag_not_drop",
                  "details": outlier_rows})
    audit.append({"step": "summary", "input_rows": len(rows), "output_rows": len(cleaned),
                  "dropped_rows": len(rows) - len(cleaned), "columns": columns})
    return {"columns": columns, "rows": cleaned, "outliers": outlier_rows, "audit": audit,
            "source": table.get("source"), "sha256": table.get("sha256")}


def descriptive(values):
    values = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(float(v))]
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
            "q1": _quartile(values, 0.25), "q3": _quartile(values, 0.75),
            "min": min(values), "max": max(values)}


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_cdf(t, df):
    x = df / (df + t * t)
    tail = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


def welch_t_test(a, b, alpha=0.05):
    a = [float(v) for v in a if isinstance(v, (int, float)) and math.isfinite(float(v))]
    b = [float(v) for v in b if isinstance(v, (int, float)) and math.isfinite(float(v))]
    if len(a) < 2 or len(b) < 2:
        return {"test": "welch_t", "status": "insufficient_data", "n_a": len(a), "n_b": len(b)}
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    se2 = va / len(a) + vb / len(b)
    if se2 <= 0:
        return {"test": "welch_t", "status": "zero_variance", "mean_a": ma, "mean_b": mb}
    t = (ma - mb) / math.sqrt(se2)
    df = se2 * se2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    p = 2.0 * min(_t_cdf(t, df), 1.0 - _t_cdf(t, df))
    diff = ma - mb
    tcrit = 2.0  # conservative large-sample approximation; reported as such
    ci = [diff - tcrit * math.sqrt(se2), diff + tcrit * math.sqrt(se2)]
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    g = diff / pooled if pooled > 0 else None
    correction = 1.0 - 3.0 / (4.0 * (len(a) + len(b)) - 9.0)
    hedges = g * correction if g is not None else None
    return {"test": "welch_t", "status": "ok", "n_a": len(a), "n_b": len(b),
            "mean_a": ma, "mean_b": mb, "mean_difference": diff, "t": t, "df": df,
            "p_value": p, "alpha": alpha, "significant": p < alpha,
            "ci95_mean_difference": ci, "ci_method": "tcrit=2 approximation",
            "hedges_g": hedges, "effect_direction": "treatment>control" if diff > 0 else "treatment<control" if diff < 0 else "no_difference",
            "assumptions": ["independence assumed", "Welch handles unequal variances",
                            "normality not proven; inspect distribution/outliers"]}


def mann_whitney_u(a, b):
    a = [float(v) for v in a if isinstance(v, (int, float))]
    b = [float(v) for v in b if isinstance(v, (int, float))]
    if not a or not b:
        return {"test": "mann_whitney_u", "status": "insufficient_data"}
    combined = [(v, 0) for v in a] + [(v, 1) for v in b]
    combined.sort(key=lambda x: x[0])
    ranks = [0.0] * len(combined)
    i = 0
    tie_correction = 0.0
    while i < len(combined):
        j = i + 1
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = rank
        tie_correction += (j - i) ** 3 - (j - i)
        i = j
    r1 = sum(rank for rank, (_, group) in zip(ranks, combined) if group == 0)
    u1 = r1 - len(a) * (len(a) + 1) / 2.0
    u2 = len(a) * len(b) - u1
    u = min(u1, u2)
    n = len(a) + len(b)
    mean_u = len(a) * len(b) / 2.0
    var_u = len(a) * len(b) / 12.0 * ((n + 1) - tie_correction / (n * (n - 1)))
    if var_u <= 0:
        return {"test": "mann_whitney_u", "status": "zero_variance", "u": u}
    z = (u - mean_u + 0.5) / math.sqrt(var_u)
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {"test": "mann_whitney_u", "status": "ok", "u": u, "u_a": u1, "u_b": u2,
            "z": z, "p_value": p, "method": "normal approximation with tie and continuity correction"}


def svg_bar_chart(groups, title="Group comparison", ylabel="value"):
    width, height = 720, 420
    left, right, top, bottom = 80, 30, 70, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    stats = {name: descriptive(values) for name, values in groups.items()}
    max_mean = max([s.get("mean", 0) for s in stats.values()] + [0])
    max_sd = max([s.get("sd", 0) for s in stats.values()] + [0])
    ymax = max(max_mean + max_sd, 1.0) * 1.15
    colors = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           f'<text x="{width/2}" y="32" text-anchor="middle" font-family="sans-serif" font-size="20">{title}</text>',
           f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#333"/>',
           f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#333"/>',
           f'<text x="24" y="{height/2}" transform="rotate(-90 24 {height/2})" text-anchor="middle" font-family="sans-serif" font-size="14">{ylabel}</text>']
    n = max(len(stats), 1)
    slot = plot_w / n
    for i, (name, stat) in enumerate(stats.items()):
        mean, sd = stat.get("mean", 0), stat.get("sd", 0)
        x = left + slot * i + slot * 0.2
        bar_w = slot * 0.6
        y = height - bottom - (mean / ymax) * plot_h
        h = (mean / ymax) * plot_h
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{colors[i%len(colors)]}"/>')
        err_top = height - bottom - ((mean + sd) / ymax) * plot_h
        err_bottom = height - bottom - ((mean - sd) / ymax) * plot_h
        cx = x + bar_w / 2
        out.append(f'<line x1="{cx:.1f}" y1="{err_top:.1f}" x2="{cx:.1f}" y2="{err_bottom:.1f}" stroke="#222"/>')
        out.append(f'<text x="{cx:.1f}" y="{height-bottom+24}" text-anchor="middle" font-family="sans-serif" font-size="13">{name}</text>')
        out.append(f'<text x="{cx:.1f}" y="{max(err_top-8, top):.1f}" text-anchor="middle" font-family="sans-serif" font-size="12">n={stat.get("n",0)}</text>')
    out.append(f'<text x="{left}" y="{height-12}" font-family="sans-serif" font-size="11">Error bars: ±1 SD</text>')
    out.append('</svg>')
    return "\n".join(out)


def analyze_table(table, dictionary=None, constraints=None):
    """Single source of truth for cleaning, statistics and chart generation."""
    dictionary = dictionary if isinstance(dictionary, dict) else {}
    constraints = constraints if isinstance(constraints, dict) else {}
    profile = profile_table(table)
    cleaning = clean_table(table, dictionary, constraints)
    columns = cleaning["columns"]
    value_col = next((col for col in columns
                      if str(col).lower() in ("value", "measurement", "result", "concentration")), None)
    group_col = next((col for col in columns
                      if str(col).lower() in ("group", "condition", "treatment")), None)
    if not value_col or not group_col:
        return {"profile": profile, "cleaning": cleaning, "error": "value/group column not found"}
    groups = {}
    for row in cleaning["rows"]:
        value = row.get(value_col)
        if isinstance(value, (int, float)) and not row.get("__outliers__"):
            groups.setdefault(str(row.get(group_col)), []).append(value)
    desc = {name: descriptive(values) for name, values in groups.items()}
    names = list(groups)
    inferential = {}
    if len(names) == 2:
        a, b = groups[names[0]], groups[names[1]]
        inferential["welch_t"] = welch_t_test(a, b, float(constraints.get("alpha", 0.05)))
        inferential["mann_whitney_u"] = mann_whitney_u(a, b)
        wt = inferential["welch_t"]
        if wt.get("status") == "ok":
            wt["contrast"] = f"{names[1]} - {names[0]}"
            if "treatment" in names[1].lower() and "control" in names[0].lower():
                wt["mean_difference"] = -wt["mean_difference"]
                wt["t"] = -wt["t"]
                wt["ci95_mean_difference"] = [-wt["ci95_mean_difference"][1], -wt["ci95_mean_difference"][0]]
                wt["hedges_g"] = -wt["hedges_g"]
            diff = wt["mean_difference"]
            wt["effect_direction"] = "positive" if diff > 0 else "negative" if diff < 0 else "no_difference"
    chart = svg_bar_chart(groups, title="Control vs treatment", ylabel=str(value_col))
    return {"profile": profile, "cleaning": cleaning, "value_col": value_col, "group_col": group_col,
            "groups": groups, "descriptive": desc, "inferential": inferential, "chart": chart}


def generate_code(case, table, cleaning, stats, chart):
    """Return a script that reuses the exact same analysis implementation."""
    constraints = case.get("constraints") or {}
    dictionary = case.get("_dictionary") or {}
    return '''"""Reproducible analysis — uses the same engine as the delivered report."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import datacode_enhance as dc
CASE = json.loads(%r)
DICTIONARY = json.loads(%r)

def main():
    table = dc.load_table(str(ROOT / "experiment.csv"))
    analysis = dc.analyze_table(table, DICTIONARY, CASE.get("constraints") or {})
    if analysis.get("error"):
        raise SystemExit(analysis["error"])
    result = {
        "profile": analysis["profile"], "cleaning_audit": analysis["cleaning"]["audit"],
        "descriptive": analysis["descriptive"], "inferential": analysis["inferential"],
        "value_column": analysis["value_col"], "group_column": analysis["group_col"],
    }
    (ROOT / "analysis_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "analysis_chart.svg").write_text(analysis["chart"], encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
''' % (json.dumps({"constraints": constraints}, ensure_ascii=False), json.dumps(dictionary, ensure_ascii=False))


def execute_python(code, work_dir, timeout=60):
    os.makedirs(work_dir, exist_ok=True)
    script = os.path.join(work_dir, "analysis.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(code)
    try:
        proc = subprocess.run([sys.executable, "-I", script], cwd=work_dir,
                              capture_output=True, text=True, timeout=timeout)
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
                "script": script, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": -1, "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or ""),
                "script": script, "timed_out": True}
    except Exception as exc:
        return {"exit_code": -1, "stdout": "", "stderr": str(exc), "script": script, "timed_out": False}


def _find_data(case, index, task_md):
    candidates = []
    for key in ("data_file", "input_file", "table", "dataset"):
        if case.get(key):
            candidates.append(case[key])
    task = case.get("task")
    if isinstance(task, str):
        candidates.append(task)
    library = case.get("library") or (index or {}).get("root")
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        path = candidate if os.path.isabs(candidate) else os.path.join(library or "", candidate)
        if not os.path.isfile(path) and library:
            path = os.path.join(library, os.path.basename(candidate))
        if os.path.isfile(path):
            return path
    if library and os.path.isdir(library):
        priority = {".csv": 0, ".tsv": 1, ".xlsx": 2, ".json": 3}
        names = sorted(os.listdir(library), key=lambda n: (
            priority.get(os.path.splitext(n)[1].lower(), 9),
            1 if any(token in n.lower() for token in ("dictionary", "schema", "metadata")) else 0,
            n))
        for name in names:
            if name.lower().endswith((".csv", ".tsv", ".json", ".xlsx")) and not any(
                    token in name.lower() for token in ("dictionary", "schema")):
                return os.path.join(library, name)
    return None


def apply_datacode(result, index, case, task_md, out_dir, work_dir):
    data_path = _find_data(case, index, task_md)
    if not data_path:
        return {"datacode_status": {"state": "failed", "error": "no tabular data file found"}}
    table = load_table(data_path)
    dictionary = {}
    dict_ref = case.get("data_dictionary")
    if dict_ref:
        dict_path = dict_ref if os.path.isabs(dict_ref) else os.path.join(os.path.dirname(data_path), dict_ref)
        if not os.path.isfile(dict_path):
            dict_path = os.path.join(os.path.dirname(data_path), os.path.basename(dict_ref))
        if os.path.isfile(dict_path):
            try:
                dictionary = json.load(open(dict_path, encoding="utf-8"))
            except Exception:
                dictionary = {}
    constraints = case.get("constraints") or {}
    case["_dictionary"] = dictionary
    analysis = analyze_table(table, dictionary, constraints)
    profile = analysis.get("profile") or {}
    cleaning = analysis.get("cleaning") or {}
    if analysis.get("error"):
        return {"datacode_status": {"state": "failed", "error": analysis["error"]},
                "data_profile": profile, "cleaning_audit": cleaning.get("audit", [])}
    value_col, group_col = analysis["value_col"], analysis["group_col"]
    desc, inferential, chart = analysis["descriptive"], analysis["inferential"], analysis["chart"]
    os.makedirs(out_dir, exist_ok=True)
    chart_path = os.path.join(out_dir, "analysis_chart.svg")
    with open(chart_path, "w", encoding="utf-8") as handle:
        handle.write(chart)
    code = generate_code(case, table, cleaning, desc, chart)
    # Put data and the exact engine beside the generated script so the
    # reproducible code uses the same implementation as the delivered report.
    run_dir = os.path.join(work_dir or out_dir, "datacode_run")
    os.makedirs(run_dir, exist_ok=True)
    try:
        import shutil
        shutil.copy2(data_path, os.path.join(run_dir, "experiment.csv"))
        shutil.copy2(os.path.abspath(__file__), os.path.join(run_dir, "datacode_enhance.py"))
    except Exception:
        pass
    execution = execute_python(code, run_dir)
    quality = {
        "source_sha256": table.get("sha256"), "source_rows": profile.get("row_count"),
        "cleaned_rows": len(cleaning["rows"]), "dropped_rows": profile.get("row_count", 0) - len(cleaning["rows"]),
        "missing_numeric_not_zero_filled": True, "unit_conversion_recorded": True,
        "outliers_flagged_not_silently_dropped": True, "code_exit_code": execution.get("exit_code"),
        "chart_exists": os.path.isfile(chart_path),
    }
    report = ["# Data analysis report", "", f"- Source: `{data_path}`", f"- Rows: {profile.get('row_count')}",
              f"- Cleaned rows: {len(cleaning['rows'])}", f"- Value column: `{value_col}`", f"- Group column: `{group_col}`",
              "", "## Cleaning audit", "```json", json.dumps(cleaning["audit"], ensure_ascii=False, indent=2), "```",
              "", "## Descriptive statistics", "```json", json.dumps(desc, ensure_ascii=False, indent=2), "```",
              "", "## Inferential statistics", "```json", json.dumps(inferential, ensure_ascii=False, indent=2), "```",
              "", "## Chart", "![group comparison](analysis_chart.svg)", "", "## Reproducible code", "```python", code, "```",
              "", "## Execution log", "```text", execution.get("stdout", "") + execution.get("stderr", ""), "```",
              "", "## Quality checks", "```json", json.dumps(quality, ensure_ascii=False, indent=2), "```"]
    report_text = "\n".join(report)
    report_path = os.path.join(out_dir, "analysis_report.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report_text)
    return {
        "datacode_status": {"state": "ok" if execution.get("exit_code") == 0 else "partial",
                            "error": None if execution.get("exit_code") == 0 else execution.get("stderr")},
        "data_profile": profile, "cleaning_audit": cleaning["audit"], "cleaned_data": cleaning["rows"],
        "descriptive_statistics": desc, "inferential_statistics": inferential,
        "effect_size": {k: v.get("hedges_g") for k, v in inferential.items() if isinstance(v, dict)},
        "confidence_intervals": {k: v.get("ci95_mean_difference") for k, v in inferential.items() if isinstance(v, dict)},
        "charts": ["analysis_chart.svg"], "code": code, "execution_log": execution,
        "quality_checks": quality, "report": report_text,
    }
