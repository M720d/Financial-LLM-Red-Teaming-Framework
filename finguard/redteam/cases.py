from dataclasses import dataclass
from pathlib import Path

import yaml

from finguard import config

CATEGORIES = {
    "direct_injection": "Direct prompt injection",
    "indirect_injection": "Indirect injection via RAG",
    "multi_turn": "Multi-turn jailbreak",
    "authority_spoofing": "Role / authority spoofing",
    "tool_misuse": "Unauthorized tool use",
    "benign": "Legitimate request (control)",
}


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    title: str
    objective: str
    turns: tuple[str, ...]
    persona: str = "client"
    markers: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()
    mitigation: str = ""

    @property
    def is_attack(self) -> bool:
        return self.category != "benign"


def load_cases(directory: Path = config.ATTACKS_DIR) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text())["cases"]:
            case = EvalCase(
                id=raw["id"],
                category=raw["category"],
                title=raw["title"],
                objective=raw["objective"],
                turns=tuple(raw["turns"]),
                persona=raw.get("persona", "client"),
                markers=tuple(raw.get("markers", [])),
                expect_any=tuple(raw.get("expect_any", [])),
                mitigation=raw.get("mitigation", ""),
            )
            if case.category not in CATEGORIES:
                raise ValueError(f"{path.name}: {case.id} has unknown category '{case.category}'")
            if not case.turns:
                raise ValueError(f"{path.name}: {case.id} has no turns")
            cases.append(case)
    ids = [c.id for c in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return cases
