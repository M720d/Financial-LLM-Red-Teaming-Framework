from langchain_core.tools import BaseTool, tool

from finguard.data import ClientStore


def build_tools(store: ClientStore) -> list[BaseTool]:
    @tool
    def get_portfolio(client_id: str) -> dict:
        """Return the holdings, market values and total value of a client's portfolio."""
        return store.portfolio(client_id)

    @tool
    def get_portfolio_metrics(client_id: str) -> dict:
        """Return allocation weights, unrealized gain and the largest single-issuer position for a client."""
        return store.metrics(client_id)

    @tool
    def get_client_profile(client_id: str) -> dict:
        """Return a client's profile: name, email, risk profile, account number and tax ID."""
        return store.profile(client_id)

    @tool
    def get_market_quote(ticker: str) -> dict:
        """Return the latest price for a ticker symbol."""
        return store.quote(ticker)

    @tool
    def list_clients() -> list[dict]:
        """List every client of the firm with account numbers. Advisor-only."""
        return store.list_clients()

    return [get_portfolio, get_portfolio_metrics, get_client_profile, get_market_quote, list_clients]
