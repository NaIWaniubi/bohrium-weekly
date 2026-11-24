"""Evidence-bounded experiment design, comparison and parameter optimization.

The module turns a research brief into a reviewable design: operational
variables, controls, run plan, resource formulas, parameter ranges, risks and an
optimization plan.  It never invents equipment models, costs or experimental
results; missing inputs stay explicit.
"""
import csv
import itertools
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


def parse_brief(case, task_md=""):
    case = _dict(case)
    constraints = _dict(case.get("constraints"))
    brief = {
        "objective": case.get("objective") or case.get("question") or "",
        "hypothesis": case.get("hypothesis"),
        "constraints": constraints,
        "equipment": _list(case.get("equipment")),
        "materials": _list(case.get("materials")),
        "controls_required": _list(case.get("controls")),
        "risks": _list(case.get("risks")),
        "source": "case.json",
    }
    if not brief["objective"]:
        match = re.search(r"(?im)^\s*objective\s*[:：]\s*(.+)$", task_md)
        if match:
            brief["objective"] = match.group(1).strip()
            brief["source"] = "task.md"
    for key, pattern in (
        ("max_runs", r"(?im)^\s*maximum\s+(\d+)\s+experimental\s+runs"),
        ("max_hours", r"(?im)^\s*(\d+)\s+hours"),
        ("budget_cny", r"(?im)^\s*budget\s+(\d+)\s*CNY"),
    ):
        if key not in constraints:
            match = re.search(pattern, task_md)
            if match:
                constraints[key] = int(match.group(1))
    for key, pattern in (
        ("temperature_C", r"temperature\s+range\s+(\d+)\s*-\s*(\d+)\s*C"),
        ("time_h", r"reaction\s+time\s+(\d+)\s*-\s*(\d+)\s*h"),
    ):
        if key not in constraints:
            match = re.search(pattern, task_md, re.I)
            if match:
                constraints[key] = [int(match.group(1)), int(match.group(2))]
    return brief


def build_variable_matrix(case, brief):
    variables = []
    for row in _list(case.get("variables")):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("operational_definition", item.get("description") or item.get("name"))
        item.setdefault("measurement", item.get("unit") or "categorical" if item.get("role") == "independent" else item.get("unit"))
        item.setdefault("range_source", "case.json")
        variables.append(item)
    if not variables:
        variables = [
            {"name": "independent_variable", "role": "independent", "operational_definition": None,
             "measurement": None, "unit": None, "range_source": "not provided"},
            {"name": "dependent_variable", "role": "dependent", "operational_definition": None,
             "measurement": None, "unit": None, "range_source": "not provided"},
            {"name": "confounders", "role": "confounder", "operational_definition": "batch/operator/time",
             "measurement": "record for every run", "unit": None, "range_source": "proposed"},
        ]
    return variables


def design_controls(case, brief):
    controls = []
    for name in brief.get("controls_required") or []:
        controls.append({"control": name, "purpose": "exclude " + str(name).replace("_", " ") + " related confounding",
                         "runs": 1, "status": "required_by_brief"})
    if not controls:
        controls = [
            {"control": "blank", "purpose": "background signal / contamination", "runs": 1, "status": "proposed"},
            {"control": "positive_control", "purpose": "verify assay and procedure sensitivity", "runs": 1, "status": "proposed"},
        ]
    return controls


def _levels(spec):
    if spec.get("levels"):
        return _list(spec["levels"])
    if spec.get("range"):
        lo, hi = spec["range"]
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            return [lo, (lo + hi) / 2.0, hi]
    return [None]


def plan_runs(case, brief, variables, controls):
    max_runs = int(brief.get("constraints", {}).get("max_runs") or 0)
    control_runs = sum(int(c.get("runs") or 1) for c in controls)
    independent = [v for v in variables if v.get("role") == "independent" and len(_levels(v)) > 1]
    required_replicates = int(case.get("min_replicates") or brief.get("constraints", {}).get("min_replicates") or 3)
    if max_runs <= 0:
        return {"runs": [], "status": "needs_constraint", "reason": "max_runs not provided"}
    remaining = max(0, max_runs - control_runs)
    max_conditions = remaining // required_replicates
    if not independent or max_conditions < 2:
        return {"runs": [], "status": "needs_constraint",
                "reason": "insufficient runs for primary-factor comparison and required replicates",
                "max_runs": max_runs, "control_runs": control_runs,
                "required_replicates": required_replicates}
    # Prioritize the primary factor, then add secondary factors only when every
    # condition can still meet the required replicate count.
    chosen = {}
    current_conditions = 1
    ordered = sorted(independent, key=lambda v: 0 if str(v.get("name")).lower() in ("catalyst", "treatment", "group", "condition") else 1)
    for variable in ordered:
        name = variable["name"]
        full = _levels(variable)
        if not chosen:
            take = min(len(full), max_conditions)
            chosen[name] = full[:take]
            current_conditions = take
        elif len(full) >= 2 and current_conditions * 2 <= max_conditions:
            chosen[name] = full[:2]
            current_conditions *= 2
        else:
            chosen[name] = [full[len(full) // 2]]
    conditions = list(itertools.product(*[chosen[v["name"]] for v in ordered])) if ordered else [()]
    runs = []
    for control in controls:
        for i in range(int(control.get("runs") or 1)):
            runs.append({"run_id": len(runs) + 1, "type": "control", "control": control["control"],
                         "replicate": i + 1, "factors": {}})
    for condition in conditions:
        factors = {v["name"]: condition[i] for i, v in enumerate(ordered)}
        for rep in range(required_replicates):
            runs.append({"run_id": len(runs) + 1, "type": "experimental", "replicate": rep + 1,
                         "factors": factors})
    return {"runs": runs, "status": "ok" if runs else "needs_constraint",
            "max_runs": max_runs, "control_runs": control_runs,
            "required_replicates": required_replicates, "replicates_per_condition": required_replicates,
            "conditions": len(conditions), "unused_run_slots": max_runs - len(runs),
            "next_stage": "use unused slots for confirmation or secondary-factor refinement"}


def build_protocol(case, brief, variables, controls, run_plan):
    steps = [
        {"step": 1, "name": "preflight", "action": "verify equipment calibration, materials and data dictionary",
         "depends_on": [], "qc": "calibration and inventory recorded"},
        {"step": 2, "name": "randomize", "action": "randomize run order and block by batch/operator",
         "depends_on": [1], "qc": "randomization seed and run order saved"},
        {"step": 3, "name": "execute_controls", "action": "run required controls before experimental runs",
         "depends_on": [1, 2], "qc": "control acceptance criteria met"},
        {"step": 4, "name": "execute_experiments", "action": "execute planned runs with full parameter logging",
         "depends_on": [3], "qc": "no missing critical fields; deviations logged"},
        {"step": 5, "name": "analyze", "action": "compute descriptive statistics, effect sizes and uncertainty",
         "depends_on": [4], "qc": "results reproduce from raw data and code"},
        {"step": 6, "name": "review", "action": "review failures, risks and next iteration",
         "depends_on": [5], "qc": "decision and limitations recorded"},
    ]
    return steps


def estimate_resources(case, brief, run_plan):
    constraints = brief.get("constraints", {})
    runs = run_plan.get("runs", [])
    per_run_hours = case.get("per_run_hours")
    per_run_cost = case.get("per_run_cost_cny")
    total_hours = per_run_hours * len(runs) if isinstance(per_run_hours, (int, float)) else None
    total_cost = per_run_cost * len(runs) if isinstance(per_run_cost, (int, float)) else None
    return {
        "planned_runs": len(runs), "max_runs": constraints.get("max_runs"),
        "max_hours": constraints.get("max_hours"), "budget_cny": constraints.get("budget_cny"),
        "per_run_hours": per_run_hours, "per_run_cost_cny": per_run_cost,
        "estimated_total_hours": total_hours, "estimated_total_cost_cny": total_cost,
        "within_run_limit": len(runs) <= int(constraints.get("max_runs") or 0),
        "within_time_limit": None if total_hours is None or not constraints.get("max_hours") else total_hours <= constraints["max_hours"],
        "within_budget": None if total_cost is None or not constraints.get("budget_cny") else total_cost <= constraints["budget_cny"],
        "missing_inputs": [k for k, v in (("per_run_hours", per_run_hours), ("per_run_cost_cny", per_run_cost)) if v is None],
        "note": "unknown costs/times are not fabricated; provide per-run values to complete feasibility",
    }


def parameter_ranges(case, brief):
    output = {}
    for variable in _list(case.get("variables")):
        if not isinstance(variable, dict):
            continue
        name = variable.get("name")
        if variable.get("range"):
            output[name] = {"range": variable["range"], "unit": variable.get("unit"), "source": "case.json"}
        elif variable.get("levels"):
            output[name] = {"levels": variable["levels"], "unit": variable.get("unit"), "source": "case.json"}
    for key in ("temperature_C", "time_h"):
        if key in brief.get("constraints", {}):
            output[key] = {"range": brief["constraints"][key], "source": "brief.constraints"}
    return output


def risk_register(case, brief):
    risks = []
    templates = {
        "temperature overshoot": ("independent temperature logger", "beyond declared range", "stop run and quarantine sample", "check controller/calibration"),
        "contamination": ("blank control and handling log", "blank above acceptance limit", "discard batch and repeat", "clean equipment and repeat controls"),
        "low signal": ("positive control and replicate consistency", "below quantified detection limit", "do not report quantitative result", "check assay and sample preparation"),
        "sample loss": ("sample inventory and mass balance", "missing or insufficient sample", "mark missing and preserve remaining sample", "reduce consumption or recollect"),
    }
    for risk in brief.get("risks") or []:
        detection, threshold, stop, fallback = templates.get(str(risk).lower(), (
            "monitor and record deviation", "predefined acceptance criterion", "pause and review", "root-cause then repeat"))
        risks.append({"risk": risk, "detection": detection, "threshold": threshold,
                      "stop_loss": stop, "fallback": fallback, "owner": "experiment operator"})
    return risks


def compare_designs(case, designs):
    criteria = ["validity", "feasibility", "cost", "time", "risk"]
    rows = []
    for design in _list(designs):
        if not isinstance(design, dict):
            continue
        scores = design.get("scores") or {}
        row = {"design": design.get("name") or design.get("id"), "scores": scores,
               "total": sum(float(scores.get(c) or 0) for c in criteria)}
        rows.append(row)
    rows.sort(key=lambda r: (-r["total"], str(r["design"])))
    return {"criteria": criteria, "ranking": rows, "note": "scores must be supplied and justified; no automatic fabrication"}


def optimization_plan(case, brief, prior_data=None):
    ranges = parameter_ranges(case, brief)
    plan = {
        "objective": brief.get("objective"), "parameter_ranges": ranges,
        "strategy": "sequential grid followed by local refinement",
        "stages": [
            {"stage": 1, "action": "screen declared factor levels within constraints", "replicates": 2},
            {"stage": 2, "action": "refine around best observed feasible region", "replicates": 3},
            {"stage": 3, "action": "confirm with independent batch and controls", "replicates": 3},
        ],
        "prior_observations": prior_data or [],
        "limitations": ["prior observations are not proof of optimum", "small-n rankings require confirmation"],
    }
    if prior_data:
        best = max(prior_data, key=lambda r: float(r.get("yield_percent") or -1))
        plan["best_observed"] = best
        plan["best_observed_status"] = "candidate_only_small_n"
    return plan


def _load_prior(path):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def build_report(brief, variables, controls, run_plan, protocol, resources, ranges, risks, comparison, optimization):
    lines = ["# Experiment design report", "", "## Objective", brief.get("objective") or "not provided",
             "", "## Hypothesis", brief.get("hypothesis") or "not explicitly provided; proposed hypotheses must be labeled",
             "", "## Variable matrix", "```json", json.dumps(variables, ensure_ascii=False, indent=2), "```",
             "", "## Controls", "```json", json.dumps(controls, ensure_ascii=False, indent=2), "```",
             "", "## Run plan", "```json", json.dumps(run_plan, ensure_ascii=False, indent=2), "```",
             "", "## Protocol", "```json", json.dumps(protocol, ensure_ascii=False, indent=2), "```",
             "", "## Resources and feasibility", "```json", json.dumps(resources, ensure_ascii=False, indent=2), "```",
             "", "## Parameter ranges", "```json", json.dumps(ranges, ensure_ascii=False, indent=2), "```",
             "", "## Risk register", "```json", json.dumps(risks, ensure_ascii=False, indent=2), "```",
             "", "## Design comparison", "```json", json.dumps(comparison, ensure_ascii=False, indent=2), "```",
             "", "## Optimization plan", "```json", json.dumps(optimization, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def apply_experiment(result, index, case, task_md, out_dir, work_dir=None):
    brief = parse_brief(case, task_md)
    variables = build_variable_matrix(case, brief)
    controls = design_controls(case, brief)
    run_plan = plan_runs(case, brief, variables, controls)
    protocol = build_protocol(case, brief, variables, controls, run_plan)
    resources = estimate_resources(case, brief, run_plan)
    ranges = parameter_ranges(case, brief)
    risks = risk_register(case, brief)
    comparison = compare_designs(case, case.get("designs") or [])
    prior_path = case.get("prior_data")
    if not prior_path:
        library = case.get("library") or (index or {}).get("root")
        if library and os.path.isdir(library):
            for name in sorted(os.listdir(library)):
                if name.lower().endswith(".csv"):
                    prior_path = os.path.join(library, name)
                    break
    prior = _load_prior(prior_path)
    optimization = optimization_plan(case, brief, prior)
    report = build_report(brief, variables, controls, run_plan, protocol, resources, ranges, risks, comparison, optimization)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "experiment_report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)
    return {"experiment_status": {"state": "ok", "generated_at": _now()},
            "brief": brief, "hypothesis": brief.get("hypothesis"), "variable_matrix": variables,
            "controls": controls, "run_plan": run_plan, "protocol": protocol, "resources": resources,
            "parameter_ranges": ranges, "risk_register": risks, "comparison": comparison,
            "optimization_plan": optimization, "report": report}
