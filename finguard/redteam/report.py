from finguard.redteam.cases import CATEGORIES

METRICS = (
    ("attack_success_rate", "Attack success rate", "lower is better"),
    ("leakage_rate", "Data leakage rate", "lower is better"),
    ("tool_violation_attempt_rate", "Unauthorized tool call attempted", "model was manipulated"),
    ("tool_violation_executed_rate", "Unauthorized tool call executed", "lower is better"),
    ("input_block_rate", "Blocked by input guard", "share of attacks"),
    ("false_positive_rate", "False positive rate (benign)", "lower is better"),
    ("benign_accuracy", "Benign task accuracy", "higher is better"),
)


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.0f}%"


def _excerpt(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " …"


def render_markdown(run: dict) -> str:
    modes = list(run["modes"])
    lines = [
        "# FinGuard AI — Red Team Evaluation Report",
        "",
        f"- Run: {run['run_at']}",
        f"- Model: `{run['model']}`",
        f"- Cases: {sum(1 for c in run['cases'] if c['category'] != 'benign')} attacks, "
        f"{sum(1 for c in run['cases'] if c['category'] == 'benign')} benign controls",
    ]
    for mode, layers in run["modes"].items():
        lines.append(f"- `{mode}` defenses: {', '.join(layers) if layers else 'none'}")
    lines += ["", "## Summary", "", "| Metric | " + " | ".join(modes) + " | Note |", "|---|" + "---|" * len(modes) + "---|"]
    for key, label, note in METRICS:
        lines.append(f"| {label} | " + " | ".join(_pct(run["summary"][m][key]) for m in modes) + f" | {note} |")

    lines += ["", "### Attack success rate by category", "", "| Category | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    categories = sorted({c for m in modes for c in run["summary"][m]["by_category"]})
    for category in categories:
        cells = [_pct(run["summary"][m]["by_category"].get(category, {}).get("attack_success_rate")) for m in modes]
        lines.append(f"| {CATEGORIES[category]} | " + " | ".join(cells) + " |")

    results = {(r["case_id"], r["mode"]): r for r in run["results"]}
    lines += ["", "## Case details", ""]
    for case in run["cases"]:
        lines += [f"### {case['id']} — {case['title']}", "", f"**Category:** {CATEGORIES[case['category']]}  ", f"**Objective:** {case['objective']}", ""]
        lines.append("**Input:**")
        lines.append("")
        for i, turn in enumerate(case["turns"], 1):
            lines.append(f"{i}. {turn}")
        lines.append("")
        lines += ["| Mode | Outcome | Evidence |", "|---|---|---|"]
        for mode in modes:
            r = results.get((case["id"], mode))
            if r:
                lines.append(f"| {mode} | **{r['outcome']}** | {_evidence(r)} |")
        for mode in modes:
            r = results.get((case["id"], mode))
            if r and r["turns"]:
                lines += ["", f"<details><summary>{mode} response (final turn)</summary>", "", "> " + _excerpt(r["turns"][-1]["answer"]), "", "</details>"]
        if case["mitigation"]:
            lines += ["", f"**Recommended mitigation:** {case['mitigation']}"]
        lines.append("")
    return "\n".join(lines)


def _evidence(result: dict) -> str:
    parts = []
    if result["error"]:
        parts.append(f"error: {result['error']}")
    if result["leaked"]:
        parts.append("leaked " + ", ".join(f"`{s}`" for s in result["leaked"]))
    if result["violations_executed"]:
        parts.append("executed " + ", ".join(f"`{v}`" for v in result["violations_executed"]))
    elif result["violations_attempted"]:
        parts.append("denied " + ", ".join(f"`{v}`" for v in result["violations_attempted"]))
    if result["markers_hit"]:
        parts.append("payload markers " + ", ".join(f"`{m}`" for m in result["markers_hit"]))
    if result["blocked"]:
        parts.append("input guard blocked")
    return "; ".join(parts) or "–"
