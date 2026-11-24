"""Graphics-track enhancement: editable SVG charts, diagrams and graphical abstracts."""
import json
import os
import re
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _svg_start(width, height, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{width/2}" y="34" text-anchor="middle" font-family="sans-serif" font-size="22">{title}</text>']


def bar_chart(data, title="Bar chart", xlabel="", ylabel="Value"):
    data = _dict(data) or {str(i): v for i, v in enumerate(_list(data))}
    width, height = 760, 440
    left, right, top, bottom = 90, 40, 70, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    values = [_num(v) for v in data.values()]
    ymax = max(values + [1]) * 1.2
    out = _svg_start(width, height, title)
    out.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#222"/>')
    out.append(f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#222"/>')
    slot = plot_w / max(len(data), 1)
    colors = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]
    for i, (label, value) in enumerate(data.items()):
        h = _num(value) / ymax * plot_h
        x = left + slot * i + slot * 0.2
        y = height - bottom - h
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot*0.6:.1f}" height="{h:.1f}" fill="{colors[i%len(colors)]}"/>')
        out.append(f'<text x="{x+slot*0.3:.1f}" y="{height-bottom+25}" text-anchor="middle" font-family="sans-serif" font-size="13">{label}</text>')
        out.append(f'<text x="{x+slot*0.3:.1f}" y="{y-8:.1f}" text-anchor="middle" font-family="sans-serif" font-size="12">{value}</text>')
    out.append(f'<text x="{width/2}" y="{height-25}" text-anchor="middle" font-family="sans-serif" font-size="14">{xlabel}</text>')
    out.append(f'<text x="25" y="{height/2}" transform="rotate(-90 25 {height/2})" text-anchor="middle" font-family="sans-serif" font-size="14">{ylabel}</text>')
    out.append('</svg>')
    return "\n".join(out)


def line_chart(series, title="Line chart", xlabel="", ylabel="Value"):
    series = _dict(series)
    width, height = 760, 440
    left, right, top, bottom = 90, 40, 70, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    all_values = [_num(v) for values in series.values() for v in _list(values)]
    ymax = max(all_values + [1]) * 1.2
    out = _svg_start(width, height, title)
    out.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fafafa" stroke="#ddd"/>')
    colors = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444"]
    for i, (name, values) in enumerate(series.items()):
        values = _list(values)
        if not values:
            continue
        points = []
        for j, value in enumerate(values):
            x = left + (j / max(len(values) - 1, 1)) * plot_w
            y = top + plot_h - (_num(value) / ymax) * plot_h
            points.append(f"{x:.1f},{y:.1f}")
        out.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[i%len(colors)]}" stroke-width="3"/>')
    out.append(f'<text x="{width/2}" y="{height-25}" text-anchor="middle" font-family="sans-serif" font-size="14">{xlabel}</text>')
    out.append(f'<text x="25" y="{height/2}" transform="rotate(-90 25 {height/2})" text-anchor="middle" font-family="sans-serif" font-size="14">{ylabel}</text>')
    out.append('</svg>')
    return "\n".join(out)


def flowchart(steps, title="Workflow"):
    steps = _list(steps)
    width = 900
    height = max(240, 130 * len(steps) + 80)
    out = _svg_start(width, height, title)
    for i, step in enumerate(steps):
        y = 70 + i * 120
        out.append(f'<rect x="170" y="{y}" width="560" height="70" rx="12" fill="#eef2ff" stroke="#4f46e5" stroke-width="2"/>')
        out.append(f'<text x="450" y="{y+42}" text-anchor="middle" font-family="sans-serif" font-size="16">{step}</text>')
        if i < len(steps) - 1:
            out.append(f'<line x1="450" y1="{y+70}" x2="450" y2="{y+120}" stroke="#4f46e5" stroke-width="3" marker-end="url(#arrow)"/>')
    out.insert(2, '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#4f46e5"/></marker></defs>')
    out.append('</svg>')
    return "\n".join(out)


def pathway(nodes, edges, title="Pathway"):
    nodes = _list(nodes)
    width = 1000
    height = max(360, len(nodes) * 90 + 100)
    out = _svg_start(width, height, title)
    positions = {}
    for i, node in enumerate(nodes):
        x = 100 + (i % 3) * 300
        y = 80 + (i // 3) * 120
        positions[str(node)] = (x, y)
        out.append(f'<rect x="{x}" y="{y}" width="220" height="60" rx="10" fill="#ecfeff" stroke="#0891b2" stroke-width="2"/>')
        out.append(f'<text x="{x+110}" y="{y+36}" text-anchor="middle" font-family="sans-serif" font-size="14">{node}</text>')
    for edge in _list(edges):
        if not isinstance(edge, dict):
            continue
        source, target = positions.get(str(edge.get("source"))), positions.get(str(edge.get("target")))
        if source and target:
            out.append(f'<line x1="{source[0]+220}" y1="{source[1]+30}" x2="{target[0]}" y2="{target[1]+30}" stroke="#0891b2" stroke-width="2" marker-end="url(#arrow)"/>')
    out.insert(2, '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#0891b2"/></marker></defs>')
    out.append('</svg>')
    return "\n".join(out)


def apparatus(components, title="Experimental setup"):
    return flowchart(components, title)


def graphical_abstract(title, panels):
    panels = _list(panels)
    width = 1200
    height = 500
    out = _svg_start(width, height, title)
    slot = width / max(len(panels), 1)
    colors = ["#eef2ff", "#ecfeff", "#f0fdf4", "#fff7ed", "#fef2f2"]
    for i, panel in enumerate(panels):
        x = 30 + i * slot
        out.append(f'<rect x="{x:.1f}" y="90" width="{slot-50:.1f}" height="300" rx="16" fill="{colors[i%len(colors)]}" stroke="#94a3b8"/>')
        out.append(f'<text x="{x+(slot-50)/2:.1f}" y="130" text-anchor="middle" font-family="sans-serif" font-size="16">{panel}</text>')
        if i < len(panels) - 1:
            out.append(f'<line x1="{x+slot-50:.1f}" y1="240" x2="{x+slot:.1f}" y2="240" stroke="#475569" stroke-width="3" marker-end="url(#arrow)"/>')
    out.insert(2, '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#475569"/></marker></defs>')
    out.append('</svg>')
    return "\n".join(out)


def render_preview(svg, out_dir, name="figure"):
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, name + ".svg")
    with open(svg_path, "w", encoding="utf-8") as handle:
        handle.write(svg)
    png_path = None
    try:
        import cairosvg
        png_path = os.path.join(out_dir, name + ".png")
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=png_path)
    except Exception:
        pass
    return svg_path, png_path


def apply_graphics(result, index, case, task_md, out_dir, work_dir=None):
    question = (case.get("question") or task_md).lower()
    data = case.get("data") or case.get("chart_data") or {}
    if any(k in question for k in ("pathway", "通路", "机制")):
        svg = pathway(case.get("nodes", ["A", "B", "C"]), case.get("edges", []), case.get("title", "Pathway"))
    elif any(k in question for k in ("apparatus", "装置", "技术路线")):
        svg = apparatus(case.get("components", ["Sample", "Measurement", "Analysis"]), case.get("title", "Experimental setup"))
    elif any(k in question for k in ("graphical abstract", "图形摘要")):
        svg = graphical_abstract(case.get("title", "Graphical abstract"), case.get("panels", ["Input", "Method", "Result"]))
    elif any(k in question for k in ("line", "折线", "trend")):
        svg = line_chart(data, case.get("title", "Line chart"))
    else:
        svg = bar_chart(data, case.get("title", "Scientific chart"))
    svg_path, png_path = render_preview(svg, out_dir)
    payload = {"svg": svg, "svg_path": svg_path, "png_path": png_path,
               "editable": True, "title": case.get("title") or question[:80],
               "limitations": ["layout is deterministic; agent should verify labels, units and scientific accuracy"]}
    with open(os.path.join(out_dir, "graphics_source.json"), "w", encoding="utf-8") as handle:
        json.dump({"case": case, "svg": svg}, handle, ensure_ascii=False, indent=2)
    return {"graphics_status": {"state": "ok", "generated_at": _now()}, **payload}
