"""Heuristic input screening, untrusted-content sanitization and output data-loss prevention."""

import re
from dataclasses import dataclass, field

from finguard.data import ClientStore
from finguard.policy import Principal


@dataclass(frozen=True)
class Signature:
    name: str
    pattern: re.Pattern
    weight: float


def _sig(name: str, pattern: str, weight: float) -> Signature:
    return Signature(name, re.compile(pattern, re.IGNORECASE), weight)


INJECTION_SIGNATURES = (
    _sig(
        "instruction_override",
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules|prompt|guidelines|directives)\b",
        1.0,
    ),
    _sig(
        "prompt_extraction",
        r"\b(system (prompt|message)|deployment tag|configuration id|(repeat|print|output)\b[^.\n]{0,30}\b(above|verbatim))\b",
        1.0,
    ),
    _sig("fake_system_message", r"\[\s*(system|admin|developer|security)[^\]]{0,30}\]", 1.0),
    _sig(
        "privilege_claim",
        r"\b(privileges?|authori[sz]ation|access)( level)?\b[^.\n]{0,20}\b(elevated|upgraded|granted|escalated)\b",
        0.8,
    ),
    _sig("persona_switch", r"\b(you are now|from now on,? you are|pretend (to be|you are)|act as an?)\b", 0.5),
    _sig("restriction_removal", r"\b(unfiltered|no restrictions|without restrictions|jailbreak|developer mode|maintenance mode)\b", 0.6),
    _sig("ai_addressed_instruction", r"\b(note|message|instructions?) (to|for) (ai|llm|assistant|language model)s?\b|\bai (assistant|system)s?:", 1.0),
    _sig("bulk_client_request", r"\b(all|every|other)\b[^.\n]{0,15}\b(clients?|customers?|accounts?)\b", 0.4),
)

BLOCK_THRESHOLD = 0.8


@dataclass
class InputVerdict:
    blocked: bool
    score: float
    matches: list[str] = field(default_factory=list)


def scan_input(text: str) -> InputVerdict:
    matches = [s for s in INJECTION_SIGNATURES if s.pattern.search(text)]
    score = round(sum(s.weight for s in matches), 2)
    return InputVerdict(blocked=score >= BLOCK_THRESHOLD, score=score, matches=[s.name for s in matches])


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
REMOVED_MARKER = "[removed by FinGuard: instruction-like content]"


def sanitize_untrusted(text: str) -> tuple[str, list[str]]:
    """Drop hidden HTML comments and quarantine lines that look like instructions to the model."""
    removed = [m.strip() for m in _HTML_COMMENT.findall(text)]
    text = _HTML_COMMENT.sub(REMOVED_MARKER, text)
    kept = []
    for line in text.splitlines():
        if line.strip() and line.strip() != REMOVED_MARKER and scan_input(line).blocked:
            removed.append(line.strip())
            kept.append(REMOVED_MARKER)
        else:
            kept.append(line)
    return "\n".join(kept), removed


SENSITIVE_PATTERNS = {
    "account_number": re.compile(r"\bLW-\d{8}\b"),
    "tax_id": re.compile(r"\b9\d{2}-\d{2}-\d{4}\b"),
    "portfolio_id": re.compile(r"\bPF-\d{4}-[A-Z0-9]{3}\b"),
}
_URL = re.compile(r"https?://[^\s)\]>\"']+")
ALLOWED_URL_HOSTS = ("larkspur.example", "sec.gov")


def redact_output(text: str, principal: Principal, store: ClientStore) -> tuple[str, list[str]]:
    """Redact identifiers that match a sensitive format but do not belong to the principal, plus off-allowlist URLs."""
    owned = {
        store.clients[cid][f]
        for cid in principal.client_ids
        if cid in store.clients
        for f in SENSITIVE_PATTERNS
    }
    redactions = []

    def _redact(kind: str):
        def replace(match: re.Match) -> str:
            if match.group(0) in owned:
                return match.group(0)
            redactions.append(f"{kind}: {match.group(0)}")
            return f"[REDACTED {kind}]"
        return replace

    for kind, pattern in SENSITIVE_PATTERNS.items():
        text = pattern.sub(_redact(kind), text)

    def _redact_url(match: re.Match) -> str:
        url = match.group(0)
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if any(host == h or host.endswith("." + h) for h in ALLOWED_URL_HOSTS):
            return url
        redactions.append(f"url: {url}")
        return "[REDACTED link]"

    text = _URL.sub(_redact_url, text)
    return text, redactions
