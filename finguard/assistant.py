import json
from dataclasses import asdict, dataclass, field, fields

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from finguard import config
from finguard.data import ClientStore, KnowledgeBase, load_client_store, load_knowledge_base
from finguard.guards import redact_output, scan_input
from finguard.llm import get_chat_model
from finguard.policy import Principal, authorize_tool
from finguard.prompts import format_context, system_prompt
from finguard.tools import build_tools


@dataclass(frozen=True)
class Defenses:
    input_guard: bool = False
    trust_boundary: bool = False
    retrieval_acl: bool = False
    tool_authz: bool = False
    output_guard: bool = False

    @classmethod
    def preset(cls, name: str) -> "Defenses":
        if name == "baseline":
            return cls()
        if name == "defended":
            return cls(**{f.name: True for f in fields(cls)})
        raise ValueError(f"unknown defense preset '{name}'")

    def enabled(self) -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name)]


DEFENSE_DESCRIPTIONS = {
    "input_guard": "Screen user messages against prompt-injection signatures and block high-risk input.",
    "trust_boundary": "Hardened system prompt, tagged context with trust labels, sanitized untrusted documents.",
    "retrieval_acl": "Exclude documents above the user's clearance before ranking.",
    "tool_authz": "Check every tool call against role and client ownership before executing it.",
    "output_guard": "Redact identifiers the user may not see and links outside the allowlist.",
}

BLOCKED_REPLY = (
    "This request was blocked by input screening because it matches known prompt-injection patterns. "
    "Please rephrase your question."
)


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    allowed: bool
    reason: str
    executed: bool
    result: str = ""


@dataclass
class TurnResult:
    user: str
    answer: str
    blocked: bool = False
    input_verdict: dict | None = None
    retrieved: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(p.get("text", "") for p in message.content if isinstance(p, dict))


class FinGuardAssistant:
    """A RAG assistant with read-only tools. Each defense layer can be switched on independently."""

    def __init__(
        self,
        principal: Principal,
        defenses: Defenses,
        llm: BaseChatModel | None = None,
        knowledge_base: KnowledgeBase | None = None,
        store: ClientStore | None = None,
    ):
        self.principal = principal
        self.defenses = defenses
        self.kb = knowledge_base or load_knowledge_base()
        self.store = store or load_client_store()
        self.tools = {t.name: t for t in build_tools(self.store)}
        self.llm = (llm or get_chat_model()).bind_tools(list(self.tools.values()))
        self.history: list[BaseMessage] = []

    def chat(self, message: str) -> TurnResult:
        turn = TurnResult(user=message, answer="")

        if self.defenses.input_guard:
            verdict = scan_input(message)
            turn.input_verdict = asdict(verdict)
            if verdict.blocked:
                turn.blocked = True
                turn.answer = BLOCKED_REPLY
                self._remember(message, turn.answer)
                return turn

        acl_principal = self.principal if self.defenses.retrieval_acl else None
        hits = self.kb.search(message, k=config.RETRIEVAL_K, principal=acl_principal)
        context, audit = format_context([c for c, _ in hits], spotlight=self.defenses.trust_boundary)
        for (chunk, score), info in zip(hits, audit):
            turn.retrieved.append(
                {
                    **{k: chunk.metadata[k] for k in ("chunk_id", "doc_id", "title", "trust", "classification")},
                    "score": round(score, 2),
                    "removed": info["removed"],
                }
            )

        if self.defenses.trust_boundary:
            user_block = f"User question:\n{message}\n\n<context>\n{context}\n</context>"
        else:
            user_block = f"{message}\n\nContext:\n{context}"
        messages: list[BaseMessage] = [
            SystemMessage(system_prompt(self.principal, hardened=self.defenses.trust_boundary)),
            *self.history,
            HumanMessage(user_block),
        ]

        reply = self._run_tool_loop(messages, turn)
        answer = _text(reply)
        if self.defenses.output_guard:
            answer, turn.redactions = redact_output(answer, self.principal, self.store)
        turn.answer = answer
        self._remember(message, answer)
        return turn

    def _run_tool_loop(self, messages: list[BaseMessage], turn: TurnResult) -> AIMessage:
        reply = self.llm.invoke(messages)
        for _ in range(config.MAX_TOOL_ROUNDS):
            if not reply.tool_calls:
                return reply
            messages.append(reply)
            for call in reply.tool_calls:
                messages.append(ToolMessage(self._execute(call, turn), tool_call_id=call["id"]))
            reply = self.llm.invoke(messages)
        return reply

    def _execute(self, call: dict, turn: TurnResult) -> str:
        name, args = call["name"], call.get("args") or {}
        decision = authorize_tool(self.principal, name, args)
        record = ToolCallRecord(name=name, args=args, allowed=decision.allowed, reason=decision.reason, executed=False)
        turn.tool_calls.append(record)

        if name not in self.tools:
            output = json.dumps({"error": f"unknown tool {name}"})
        elif self.defenses.tool_authz and not decision.allowed:
            output = json.dumps({"error": f"access denied: {decision.reason}"})
        else:
            record.executed = True
            try:
                output = json.dumps(self.tools[name].invoke(args))
            except Exception as exc:
                output = json.dumps({"error": str(exc)})
        record.result = output
        return output

    def _remember(self, user: str, answer: str) -> None:
        self.history.extend([HumanMessage(user), AIMessage(answer)])
