import argparse
import sys

import openai

from finguard import config
from finguard.assistant import Defenses, FinGuardAssistant
from finguard.data import load_client_store
from finguard.llm import MissingCredentialsError, check_connection, get_chat_model
from finguard.redteam.cases import load_cases
from finguard.redteam.report import render_markdown
from finguard.redteam.runner import load_latest, run_suite, save_run

MODES = ["baseline", "defended"]


def cmd_ask(args: argparse.Namespace) -> None:
    principal = load_client_store().principals[args.persona]
    assistant = FinGuardAssistant(principal, Defenses.preset(args.mode))
    turn = assistant.chat(args.question)
    print(turn.answer)
    if args.verbose:
        print("\n--- retrieved")
        for r in turn.retrieved:
            print(f"  {r['chunk_id']:<32} trust={r['trust']:<9} class={r['classification']:<12} score={r['score']}")
        print("--- tool calls")
        for c in turn.tool_calls:
            print(f"  {c.name}({c.args}) allowed={c.allowed} executed={c.executed} ({c.reason})")
        if turn.redactions:
            print("--- redactions:", ", ".join(turn.redactions))


def cmd_redteam(args: argparse.Namespace) -> None:
    cases = load_cases()
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]
    if args.category:
        cases = [c for c in cases if c.category in set(args.category)]
    if not cases:
        sys.exit("No cases match the given filters.")

    def progress(done: int, total: int, case, mode: str) -> None:
        print(f"[{done + 1}/{total}] {mode:<9} {case.id}  {case.title}", flush=True)

    llm = get_chat_model()
    check_connection(llm)
    run = run_suite(cases, args.mode, llm, on_progress=progress)
    json_path, md_path = save_run(run)
    print()
    for mode, s in run["summary"].items():
        print(
            f"{mode:<9} ASR={_pct(s['attack_success_rate'])}  leakage={_pct(s['leakage_rate'])}  "
            f"tool-violations-executed={_pct(s['tool_violation_executed_rate'])}  "
            f"FPR={_pct(s['false_positive_rate'])}  benign-accuracy={_pct(s['benign_accuracy'])}  errors={s['errors']}"
        )
    print(f"\nResults: {json_path}\nReport:  {md_path}")


def cmd_report(args: argparse.Namespace) -> None:
    run = load_latest()
    if run is None:
        sys.exit("No results yet. Run `python -m finguard redteam` first.")
    path = config.REPORTS_DIR / "REPORT.md"
    path.write_text(render_markdown(run))
    print(f"Report written to {path}")


def cmd_cases(args: argparse.Namespace) -> None:
    for c in load_cases():
        print(f"{c.id:<8} {c.category:<19} {c.title}")


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.0f}%"


def main() -> None:
    parser = argparse.ArgumentParser(prog="finguard", description="FinGuard AI red teaming framework")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask the assistant a single question")
    ask.add_argument("question")
    ask.add_argument("--mode", choices=MODES, default="defended")
    ask.add_argument("--persona", choices=["client", "advisor"], default="client")
    ask.add_argument("-v", "--verbose", action="store_true", help="show retrieval and tool-call trace")
    ask.set_defaults(func=cmd_ask)

    redteam = sub.add_parser("redteam", help="run the attack suite and write a report")
    redteam.add_argument("--mode", nargs="+", choices=MODES, default=MODES)
    redteam.add_argument("--case", nargs="+", help="run only these case ids")
    redteam.add_argument("--category", nargs="+", help="run only these categories")
    redteam.set_defaults(func=cmd_redteam)

    report = sub.add_parser("report", help="re-render REPORT.md from the latest results")
    report.set_defaults(func=cmd_report)

    cases = sub.add_parser("cases", help="list test cases")
    cases.set_defaults(func=cmd_cases)

    args = parser.parse_args()
    try:
        args.func(args)
    except (MissingCredentialsError, openai.OpenAIError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
