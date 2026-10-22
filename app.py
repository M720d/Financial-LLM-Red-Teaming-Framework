import openai
import pandas as pd
import streamlit as st

from finguard import config
from finguard.assistant import DEFENSE_DESCRIPTIONS, Defenses, FinGuardAssistant, TurnResult
from finguard.data import load_client_store, load_knowledge_base
from finguard.llm import (
    PROVIDERS,
    MissingCredentialsError,
    check_connection,
    get_chat_model,
    has_credentials,
    model_name,
)
from finguard.policy import ROLE_TOOLS
from finguard.redteam.cases import CATEGORIES, load_cases
from finguard.redteam.report import METRICS
from finguard.redteam.runner import load_latest, run_case, run_suite, save_run

st.set_page_config(page_title="FinGuard AI", page_icon="🛡️", layout="wide")

OUTCOME_COLORS = {
    "compromised": "red",
    "contained": "orange",
    "blocked": "blue",
    "resisted": "green",
    "pass": "green",
    "false_positive": "orange",
    "incorrect": "gray",
    "error": "gray",
}
HEADLINE_METRICS = (
    "attack_success_rate",
    "leakage_rate",
    "tool_violation_executed_rate",
    "false_positive_rate",
    "benign_accuracy",
)
EXAMPLES = [
    "Show my current holdings and the total value of my portfolio.",
    "What was Northwind Analytics' total revenue in fiscal 2025?",
    "Summarize the latest analyst view on Northwind Analytics.",
    "What is the latest news on Cobalt Ridge Energy?",
]

store = load_client_store()
kb = load_knowledge_base()
cases = load_cases()


def badge(outcome: str) -> None:
    st.badge(outcome.replace("_", " "), color=OUTCOME_COLORS.get(outcome, "gray"))


def render_trace(turn: dict) -> None:
    if turn.get("input_verdict"):
        v = turn["input_verdict"]
        st.markdown(f"**Input guard** — score {v['score']}, {'blocked' if v['blocked'] else 'passed'}"
                    + (f" ({', '.join(v['matches'])})" if v["matches"] else ""))
    if turn["retrieved"]:
        st.markdown("**Retrieved context**")
        rows = [
            {
                "chunk": r["chunk_id"],
                "trust": r["trust"],
                "classification": r["classification"],
                "bm25": r["score"],
                "lines removed": len(r["removed"]),
            }
            for r in turn["retrieved"]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        for r in turn["retrieved"]:
            for line in r["removed"]:
                st.caption(f"Sanitized from `{r['doc_id']}`: {line[:220]}")
    if turn["tool_calls"]:
        st.markdown("**Tool calls**")
        for c in turn["tool_calls"]:
            status = "✅ executed" if c["executed"] else "⛔ denied"
            policy = "allowed by policy" if c["allowed"] else f"policy violation: {c['reason']}"
            st.markdown(f"- `{c['name']}({c['args']})` — {status} · {policy}")
    if turn["redactions"]:
        st.markdown("**Output guard redactions:** " + ", ".join(f"`{r}`" for r in turn["redactions"]))


def sidebar() -> tuple[str, Defenses]:
    st.sidebar.title("🛡️ FinGuard AI")
    st.sidebar.caption("Red teaming a RAG financial assistant. All data is synthetic.")
    persona = st.sidebar.radio(
        "Signed in as",
        list(store.principals),
        format_func=lambda k: f"{store.principals[k].display_name} ({store.principals[k].role})",
    )
    preset = st.sidebar.radio("Defenses", ["baseline", "defended", "custom"], index=1, horizontal=True)
    if preset == "custom":
        defenses = Defenses(**{name: st.sidebar.checkbox(name, help=desc) for name, desc in DEFENSE_DESCRIPTIONS.items()})
    else:
        defenses = Defenses.preset(preset)
        st.sidebar.caption("Active layers: " + (", ".join(defenses.enabled()) or "none"))
    st.sidebar.divider()
    st.sidebar.markdown(f"Model: `{config.PROVIDER}` · `{model_name()}`")
    if not has_credentials():
        key = PROVIDERS[config.PROVIDER].key_env if config.PROVIDER in PROVIDERS else "FINGUARD_PROVIDER"
        st.sidebar.error(f"{key} is not set or invalid. Fix it in `.env` and restart.")
    if st.sidebar.button("Reset conversation", width="stretch"):
        st.session_state.pop("assistant_key", None)
    return persona, defenses


def assistant_tab(persona: str, defenses: Defenses) -> None:
    key = (persona, defenses)
    if st.session_state.get("assistant_key") != key:
        st.session_state.assistant_key = key
        st.session_state.turns = []
        st.session_state.assistant = None

    principal = store.principals[persona]
    st.caption(
        f"Signed in as **{principal.display_name}** · role `{principal.role}` · clients {', '.join(sorted(principal.client_ids))} "
        f"· defenses: {', '.join(defenses.enabled()) or 'none'}"
    )
    cols = st.columns(len(EXAMPLES))
    clicked = next((q for col, q in zip(cols, EXAMPLES) if col.button(q, width="stretch")), None)

    for turn in st.session_state.turns:
        with st.chat_message("user"):
            st.write(turn["user"])
        with st.chat_message("assistant"):
            st.write(turn["answer"])
            with st.expander("Trace"):
                render_trace(turn)

    question = st.chat_input("Ask about filings, policies or your portfolio", disabled=not has_credentials()) or clicked
    if question and has_credentials():
        if st.session_state.assistant is None:
            st.session_state.assistant = FinGuardAssistant(principal, defenses)
        try:
            with st.spinner("Thinking…"):
                turn: TurnResult = st.session_state.assistant.chat(question)
        except openai.OpenAIError as exc:
            st.error(f"Model request failed: {exc}")
            return
        st.session_state.turns.append(turn.to_dict())
        st.rerun()


def attack_lab_tab() -> None:
    attack_cases = [c for c in cases if c.is_attack] + [c for c in cases if not c.is_attack]
    case = st.selectbox(
        "Test case",
        attack_cases,
        format_func=lambda c: f"{c.id} · {CATEGORIES[c.category]} · {c.title}",
    )
    st.markdown(f"**Objective:** {case.objective}")
    st.markdown(f"**Persona:** {store.principals[case.persona].display_name} ({case.persona})")
    for t in case.turns:
        st.code(t, language=None, wrap_lines=True)
    if case.mitigation:
        st.caption(f"Recommended mitigation: {case.mitigation}")

    if st.button("Run against baseline and defended", type="primary", disabled=not has_credentials()):
        llm = get_chat_model()
        with st.spinner("Running both configurations…"):
            st.session_state.lab = {mode: run_case(case, mode, llm) for mode in ("baseline", "defended")}
            st.session_state.lab_case = case.id

    if st.session_state.get("lab_case") == case.id:
        for col, (mode, result) in zip(st.columns(2), st.session_state.lab.items()):
            with col:
                st.subheader(mode)
                badge(result.outcome)
                for label, items in (
                    ("Leaked", result.leaked),
                    ("Unauthorized calls executed", result.violations_executed),
                    ("Unauthorized calls attempted", result.violations_attempted),
                    ("Payload markers", result.markers_hit),
                ):
                    if items:
                        st.markdown(f"**{label}:** " + ", ".join(f"`{i}`" for i in items))
                if result.error:
                    st.error(result.error)
                for turn in result.turns:
                    with st.chat_message("user"):
                        st.write(turn["user"])
                    with st.chat_message("assistant"):
                        st.write(turn["answer"])
                        with st.expander("Trace"):
                            render_trace(turn)


def evaluation_tab() -> None:
    run = load_latest()
    left, right = st.columns([3, 1])
    with right:
        start = st.button("Run full suite", type="primary", width="stretch", disabled=not has_credentials())
        st.caption(f"{len(cases)} cases × 2 configurations")
    if start:
        llm = get_chat_model()
        try:
            check_connection(llm)
        except MissingCredentialsError as exc:
            st.error(str(exc))
            return
        bar = st.progress(0.0)

        def progress(done, total, case, mode):
            bar.progress(done / total, text=f"{mode}: {case.id} {case.title}")

        run = run_suite(cases, ["baseline", "defended"], llm, on_progress=progress)
        save_run(run)
        bar.empty()

    if run is None:
        left.info("No evaluation results yet. Run the full suite here or with `python -m finguard redteam`.")
        return
    left.markdown(f"Latest run **{run['run_at']}** · model `{run['model']}`")

    modes = list(run["modes"])
    summary = run["summary"]
    headline = [m for m in METRICS if m[0] in HEADLINE_METRICS]
    for col, (key, label, _) in zip(st.columns(len(headline)), headline):
        base, current = summary[modes[0]][key], summary[modes[-1]][key]
        if current is None:
            continue
        delta = None if len(modes) < 2 or base is None else f"{100 * (current - base):+.0f} pts vs {modes[0]}"
        col.metric(
            f"{label} ({modes[-1]})",
            f"{100 * current:.0f}%",
            delta,
            delta_color="normal" if key == "benign_accuracy" else "inverse",
        )

    st.subheader("Attack success rate by category")
    chart = pd.DataFrame(
        {
            mode: {CATEGORIES[c]: 100 * v["attack_success_rate"] for c, v in summary[mode]["by_category"].items()}
            for mode in modes
        }
    )
    st.bar_chart(chart, stack=False, y_label="% compromised", color=["#d64545", "#2f7d5b"][: len(modes)])

    st.subheader("Per-case outcomes")
    by_key = {(r["case_id"], r["mode"]): r for r in run["results"]}
    rows = []
    for c in run["cases"]:
        row = {"case": c["id"], "category": CATEGORIES[c["category"]], "title": c["title"]}
        for mode in modes:
            row[mode] = by_key.get((c["id"], mode), {}).get("outcome", "")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    report = config.REPORTS_DIR / "REPORT.md"
    if report.exists():
        st.download_button("Download REPORT.md", report.read_text(), file_name="FinGuard_REPORT.md")


def system_tab() -> None:
    st.subheader("Knowledge base")
    st.dataframe(pd.DataFrame(kb.documents), hide_index=True, width="stretch")
    st.subheader("Tools and role permissions")
    tool_names = sorted(set().union(*ROLE_TOOLS.values()))
    st.dataframe(
        pd.DataFrame({role: ["✓" if t in tools else "" for t in tool_names] for role, tools in ROLE_TOOLS.items()}, index=tool_names),
        width="stretch",
    )
    st.caption("Client-scoped tools additionally require the client_id to belong to the signed-in principal.")
    st.subheader("Defense layers")
    st.dataframe(
        pd.DataFrame([{"layer": k, "control": v} for k, v in DEFENSE_DESCRIPTIONS.items()]), hide_index=True, width="stretch"
    )


persona, defenses = sidebar()
tabs = st.tabs(["Assistant", "Attack Lab", "Evaluation", "System"])
with tabs[0]:
    assistant_tab(persona, defenses)
with tabs[1]:
    attack_lab_tab()
with tabs[2]:
    evaluation_tab()
with tabs[3]:
    system_tab()
