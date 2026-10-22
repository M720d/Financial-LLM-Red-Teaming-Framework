import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from finguard import config
from finguard.assistant import Defenses, FinGuardAssistant
from finguard.data import load_client_store, load_knowledge_base
from finguard.redteam.cases import EvalCase
from finguard.redteam.report import render_markdown
from finguard.redteam.scoring import CaseResult, all_secrets, score_case, summarize

ProgressCallback = Callable[[int, int, EvalCase, str], None]


def run_case(case: EvalCase, mode: str, llm: BaseChatModel) -> CaseResult:
    store, kb = load_client_store(), load_knowledge_base()
    principal = store.principals[case.persona]
    assistant = FinGuardAssistant(principal, Defenses.preset(mode), llm=llm, knowledge_base=kb, store=store)
    turns = []
    try:
        for message in case.turns:
            turns.append(assistant.chat(message).to_dict())
    except Exception as exc:
        return CaseResult(case.id, case.category, mode, case.is_attack, "error", turns=turns, error=repr(exc))
    return score_case(case, mode, principal, turns, all_secrets(kb, store))


def run_suite(
    cases: list[EvalCase],
    modes: list[str],
    llm: BaseChatModel,
    on_progress: ProgressCallback | None = None,
) -> dict:
    results: list[CaseResult] = []
    total, done = len(cases) * len(modes), 0
    for mode in modes:
        for case in cases:
            if on_progress:
                on_progress(done, total, case, mode)
            results.append(run_case(case, mode, llm))
            done += 1
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": getattr(llm, "model_name", None) or type(llm).__name__,
        "modes": {m: Defenses.preset(m).enabled() for m in modes},
        "cases": [asdict(c) for c in cases],
        "summary": {m: summarize([r for r in results if r.mode == m]) for m in modes},
        "results": [asdict(r) for r in results],
    }


def save_run(run: dict, directory: Path = config.REPORTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")[:15]
    payload = json.dumps(run, indent=2)
    (directory / f"run_{stamp}.json").write_text(payload)
    json_path = directory / "latest.json"
    json_path.write_text(payload)
    md_path = directory / "REPORT.md"
    md_path.write_text(render_markdown(run))
    return json_path, md_path


def load_latest(directory: Path = config.REPORTS_DIR) -> dict | None:
    path = directory / "latest.json"
    return json.loads(path.read_text()) if path.exists() else None
