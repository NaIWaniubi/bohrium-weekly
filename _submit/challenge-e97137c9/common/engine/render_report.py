"""render_report.py — turn result.json into a single self-contained HTML report.

No external assets, no JS dependencies: inline CSS only. Designed so a reviewer
opens the file in any browser and sees the whole evidence chain at a glance:
question -> filter audit (with drop counts) -> hits table with sha256 +
char offsets + contributing query plans -> verbatim evidence windows ->
uncertainty/failure buckets.

Usage:
    python -I render_report.py --result result.json --out report.html [--title ...]
"""
import argparse
import html
import json
import os
from datetime import datetime


def esc(s):
    return html.escape(str(s if s is not None else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Skill case report")
    args = ap.parse_args()

    r = json.load(open(args.result, encoding="utf-8"))
    body = []

    body.append(f"<h1>{esc(args.title)}</h1>")
    meta = r.get("provenance", {}) or {}
    body.append(f"<p class=muted>case_id {esc(r.get('case_id'))} · "
                f"status <b>{esc(r.get('status'))}</b> · "
                f"{esc(meta.get('generated_at') or datetime.now().isoformat(timespec='seconds'))}</p>")

    # question
    body.append(f"<h2>研究问题</h2><p class=q>{esc(r.get('question'))}</p>")

    # filter audit
    fa = r.get("filter_audit") or []
    if fa:
        body.append("<h2>过滤约束执行（逐条留痕）</h2><table>"
                    "<tr><th>约束</th><th>保留</th><th>较上条淘汰</th></tr>")
        for row in fa:
            body.append("<tr>" + "".join(
                f"<td>{esc(row.get(k))}</td>" for k in ("constraint", "kept",
                                                          "dropped_since_prev")) + "</tr>")
        body.append("</table>")

    # hits
    hits = r.get("hits") or []
    if hits:
        body.append("<h2>命中（含可复核定位）</h2><table>"
                    "<tr><th>#</th><th>文件</th><th>sha256(前12)</th>"
                    "<th>chunk</th><th>offset</th><th>贡献计划数</th></tr>")
        for h in hits:
            off = h.get("offset") or {}
            plans = h.get("plan_evidence") or []
            body.append(
                f"<tr><td>{esc(h.get('rank'))}</td><td class=f>{esc(h.get('file'))}</td>"
                f"<td><code>{esc((h.get('sha256_file') or '')[:12])}</code></td>"
                f"<td>{esc(h.get('chunk_id'))}</td>"
                f"<td>[{esc(off.get('start'))}, {esc(off.get('end'))}]</td>"
                f"<td>{len(plans)}</td></tr>")
        body.append("</table>")

    # evidence windows
    evs = r.get("evidence_windows") or []
    if evs:
        body.append("<h2>证据窗口（原文摘录，offset 可回链源字节）</h2>")
        for i, ev in enumerate(evs, 1):
            body.append(
                f"<div class=ev><span class=tag>E{i} · {esc(ev.get('file'))}</span>"
                f"<blockquote>{esc(ev.get('text'))}</blockquote></div>")

    # uncertainty
    unc = r.get("uncertainty") or {}
    if unc:
        body.append("<h2>不确定性分区</h2><ul>")
        for k in ("missing", "conflicts", "limits"):
            for it in unc.get(k) or []:
                body.append(f"<li><b>{k}</b>: {esc(it if isinstance(it, str) else json.dumps(it, ensure_ascii=False))}</li>")
        body.append("</ul>")

    css = """
    :root{--ink:#1c2733;--mut:#6b7a88;--acc:#2737d4;--bg:#f6f7f9;--line:#dfe4ea}
    *{box-sizing:border-box}
    body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;margin:0;
         background:var(--bg);color:var(--ink);line-height:1.55}
    .wrap{max-width:960px;margin:28px auto;padding:36px 44px;background:#fff;
           border:1px solid var(--line);border-radius:10px}
    h1{font-size:22px;margin:.2em 0 .6em}
    h2{font-size:16px;margin:1.6em 0 .6em;color:var(--acc)}
    p.q{font-size:15px;background:#f0f3ff;border-left:3px solid var(--acc);
        padding:10px 14px;border-radius:4px}
    table{border-collapse:collapse;width:100%;font-size:13px;margin:.4em 0}
    th{background:#eef1f6;text-align:left;padding:7px 10px;border:1px solid var(--line)}
    td{padding:7px 10px;border:1px solid var(--line);vertical-align:top}
    td.f{font-family:Consolas,"Courier New",monospace;font-size:12px}
    code{font-family:Consolas,monospace;color:#435}
    .ev{margin:10px 0}
    .tag{display:inline-block;font-size:12px;color:var(--acc);font-weight:600;
         margin-bottom:3px}
    blockquote{margin:.3em 0 0;padding:10px 14px;background:#fafbfc;
                border-left:3px solid #b9c2d0;font-size:13px}
    .muted{color:var(--mut);font-size:12px;margin-top:-6px}
    ul{font-size:13px}
    li b{color:var(--acc)}
    """

    doc = (f"<!doctype html><html lang=zh><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{esc(args.title)}</title><style>{css}</style></head>"
           f"<body><div class=wrap>{''.join(body)}</div></body></html>")
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, args.out)
    print("rendered:", args.out, len(doc), "bytes")


if __name__ == "__main__":
    main()