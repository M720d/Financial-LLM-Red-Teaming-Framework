"""Authorization rules for tools and documents, enforced in code rather than in the prompt."""

from dataclasses import dataclass

CLEARANCE_LEVELS = ("public", "internal", "confidential")

ROLE_TOOLS = {
    "client": {"get_portfolio", "get_portfolio_metrics", "get_client_profile", "get_market_quote"},
    "advisor": {
        "get_portfolio",
        "get_portfolio_metrics",
        "get_client_profile",
        "get_market_quote",
        "list_clients",
    },
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    role: str
    client_ids: frozenset[str]
    clearance: str

    def can_read(self, classification: str) -> bool:
        return CLEARANCE_LEVELS.index(classification) <= CLEARANCE_LEVELS.index(self.clearance)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def authorize_tool(principal: Principal, tool_name: str, args: dict) -> Decision:
    if tool_name not in ROLE_TOOLS.get(principal.role, set()):
        return Decision(False, f"role '{principal.role}' may not call {tool_name}")
    client_id = args.get("client_id")
    if client_id is not None and client_id not in principal.client_ids:
        return Decision(False, f"{principal.user_id} has no access to client {client_id}")
    return Decision(True, "allowed")
