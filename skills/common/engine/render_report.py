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

    # question + deterministic answer
    body.append(f"<h2>研究问题</h2><p class=q>{esc(r.get('question'))}</p>")
    answer = r.get("answer") or {}
    if answer.get("content"):
        body.append(f"<h2>完整首版结果</h2><div class=answer>{esc(answer.get('content'))}</div>")

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

    # actual organized catalog (classification track)
    catalog = r.get("catalog") or {}
    if catalog.get("buckets"):
        body.append(f"<h2>资料目录（{esc(catalog.get('bucket_count'))} 桶 / "
                    f"{esc(catalog.get('file_count'))} 文件）</h2><table>"
                    "<tr><th>桶</th><th>规则</th><th>数量</th><th>成员（前 8）</th></tr>")
        for bucket in catalog["buckets"]:
            members = ", ".join(str(m.get("relpath")) for m in (bucket.get("members") or [])[:8])
            body.append("<tr>" + "".join(
                f"<td>{esc(v)}</td>" for v in
                (bucket.get("bucket_id"), bucket.get("criteria"), bucket.get("member_count"), members)) + "</tr>")
        body.append("</table>")

    # hits — score-detailed, evidence-chain columns
    hits = r.get("hits") or []
    if hits:
        body.append("<h2>命中（含可复核定位）</h2><table>"
                    "<tr><th>#</th><th>文件</th><th>sha256(前12)</th>"
                    "<th>chunk</th><th>offset</th><th>pages</th>"
                    "<th>raw分</th><th>bm25/dense/RRF</th></tr>")
        for h in hits:
            off = h.get("offset") or {}
            pages = h.get("pages")
            pages_s = ",".join(str(p) for p in pages) if pages else "—"
            body.append(
                f"<tr><td>{esc(h.get('rank'))}</td><td class=f>{esc(h.get('file'))}</td>"
                f"<td><code>{esc((h.get('sha256_file') or '')[:12])}</code></td>"
                f"<td>{esc(h.get('chunk_id'))}</td>"
                f"<td>[{esc(off.get('start'))}, {esc(off.get('end'))}]</td>"
                f"<td>{esc(pages_s)}</td>"
                f"<td>{esc(h.get('score_raw'))}</td>"
                f"<td>bm25={esc(h.get('bm25_main'))} · dense={esc(h.get('dense_cosine'))}"
                f" · rrf={esc(h.get('rrf_score'))}</td></tr>")
        body.append("</table>")

    # citations (evidence-chain)
    cits = r.get("citations") or []
    if cits:
        body.append("<h2>证据链引用（页码+锚句+逐字引用）</h2><table>"
                    "<tr><th>#</th><th>文件</th><th>pages</th>"
                    "<th>页码偏移</th><th>段落首句锚点</th>"
                    "<th>逐字引用</th></tr>")
        for i, cit in enumerate(cits, 1):
            anchor = cit.get("anchor") or {}
            first = anchor.get("first_sentence") or ""
            quotes = cit.get("quotes") or []
            q_text = quotes[0].get("text", "")[:120] if quotes else "—"
            pages_s = ",".join(str(p) for p in cit.get("pages") or []) or "—"
            offset_ = cit.get("printed_page_offset")
            offset_s = str(offset_) if offset_ is not None else "—"
            body.append(
                f"<tr><td>{i}</td><td class=f>{esc(cit.get('file'))}</td>"
                f"<td>{esc(pages_s)}</td>"
                f"<td>{esc(offset_s)}</td>"
                f"<td>{esc(first)}</td>"
                f"<td><blockquote>{esc(q_text)}</blockquote></td></tr>")
        body.append("</table>")

    # independently verified evidence references
    answer_refs = (r.get("answer") or {}).get("evidence_refs") or []
    if answer_refs:
        body.append("<h2>证据引用独立复核</h2><table>"
                    "<tr><th>ID</th><th>文件</th><th>offset 空间</th><th>offset</th>"
                    "<th>文件/chunk/quote</th><th>提取文本工件</th></tr>")
        for ref in answer_refs:
            flags = f"file={ref.get('sha256_verified')} · chunk={ref.get('chunk_hash_verified')} · quote={ref.get('quote_verified')} · artifact={ref.get('artifact_hash_verified')}"
            body.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in
                        (ref.get("evidence_id"), ref.get("file"), ref.get("offset_space"),
                         ref.get("offset"), flags, ref.get("extracted_text"))) + "</tr>")
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

    # operation evidence + rollback
    op = r.get("operation_evidence") or {}
    if op:
        body.append("<h2>操作证据与回滚</h2><table>"
                    "<tr><th>源文件</th><th>过滤后</th><th>实体副本</th><th>重复别名</th></tr>"
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        esc(op.get("source_files_total")), esc(op.get("filtered_files")),
                        esc(op.get("organized_files", "—")), esc(len(op.get("alias_rows") or []))))
        hv = op.get("hash_verification") or {}
        body.append(f"<tr><th>证据哈希复核</th><td colspan=3>{esc(hv.get('verified'))}/"
                    f"{esc(hv.get('evidence_refs'))} · rate={esc(hv.get('rate'))}</td></tr>")
        body.append(f"<tr><th>清单</th><td colspan=3>{esc(op.get('manifest') or op.get('index_path'))}</td></tr>")
        body.append(f"<tr><th>回滚</th><td colspan=3>{esc(op.get('rollback'))} · "
                    f"源文件修改={esc(op.get('originals_modified'))}</td></tr></table>")

    # version lineage
    lineages = r.get("version_lineage") or []
    if lineages:
        body.append("<h2>版本族与 canonical</h2><table>"
                    "<tr><th>族</th><th>canonical</th><th>成员</th><th>依据</th></tr>")
        for item in lineages:
            members = ", ".join(str(m.get("relpath")) for m in (item.get("members") or []))
            body.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in
                        (item.get("family_key"), item.get("canonical"), members,
                         item.get("evidence"))) + "</tr>")
        body.append("</table>")

    # version resolution
    vr = r.get("version_resolution") or {}
    near = vr.get("near_duplicates") or []
    if near:
        body.append(f"<h2>版本解析（近重复组：{len(near)}）</h2><table>"
                    "<tr><th>族</th><th>paths</th></tr>")
        for nd in near[:12]:
            paths = ", ".join(str(p) for p in (nd.get("paths") or []))
            body.append(f"<tr><td>{esc(nd.get('family'))}</td><td class=f>{esc(paths)}</td></tr>")
        body.append("</table>")

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
    .answer{white-space:pre-wrap;font-size:14px;line-height:1.7;padding:14px 16px;
        border:1px solid var(--line);border-radius:6px;background:#fbfcfe}
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