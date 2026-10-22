import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from finguard import config
from finguard.policy import Principal

_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from has have how i in is it its me my "
    "of on or our s say saying the their this to was were what which who will with you your".split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


def _parse_markdown(path: Path) -> tuple[dict, str]:
    _, front, body = path.read_text().split("---", 2)
    return yaml.safe_load(front), body.strip()


@dataclass(frozen=True)
class Secret:
    label: str
    value: str
    client_id: str | None = None
    classification: str | None = None

    def visible_to(self, principal: Principal) -> bool:
        if self.client_id is not None:
            return self.client_id in principal.client_ids
        if self.classification is not None:
            return principal.can_read(self.classification)
        return False


class KnowledgeBase:
    """Markdown corpus split on level-2 headings and indexed with BM25."""

    def __init__(self, documents_dir: Path):
        self.documents: list[dict] = []
        self.chunks: list[Document] = []
        self.secrets: list[Secret] = []
        for path in sorted(documents_dir.rglob("*.md")):
            meta, body = _parse_markdown(path)
            self.documents.append({k: meta[k] for k in ("id", "title", "source", "trust", "classification")})
            for value in meta.get("secrets", []):
                self.secrets.append(Secret(f"{meta['id']}:{value}", value, classification=meta["classification"]))
            for i, section in enumerate(re.split(r"\n(?=## )", body)):
                metadata = {
                    "doc_id": meta["id"],
                    "chunk_id": f"{meta['id']}#{i}",
                    "title": meta["title"],
                    "source": meta["source"],
                    "trust": meta["trust"],
                    "classification": meta["classification"],
                }
                self.chunks.append(Document(page_content=section.strip(), metadata=metadata))
        self._index = BM25Okapi(
            [_tokenize(c.metadata["title"] + " " + c.page_content) for c in self.chunks]
        )

    def search(self, query: str, k: int, principal: Principal | None = None) -> list[tuple[Document, float]]:
        """Rank chunks for the query. With a principal, chunks above its clearance are excluded before ranking."""
        scores = self._index.get_scores(_tokenize(query))
        ranked = sorted(zip(self.chunks, scores), key=lambda pair: pair[1], reverse=True)
        results = []
        for chunk, score in ranked:
            if score <= 0:
                break
            if principal is not None and not principal.can_read(chunk.metadata["classification"]):
                continue
            results.append((chunk, float(score)))
            if len(results) == k:
                break
        return results


class ClientStore:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text())
        self.clients: dict = raw["clients"]
        self.quotes: dict = raw["quotes"]
        self.principals = {
            key: Principal(
                user_id=p["user_id"],
                display_name=p["display_name"],
                role=p["role"],
                client_ids=frozenset(p["client_ids"]),
                clearance=p["clearance"],
            )
            for key, p in raw["principals"].items()
        }

    def _client(self, client_id: str) -> dict:
        if client_id not in self.clients:
            raise KeyError(f"unknown client_id {client_id}")
        return self.clients[client_id]

    def quote(self, ticker: str) -> dict:
        ticker = ticker.upper()
        if ticker not in self.quotes:
            raise KeyError(f"unknown ticker {ticker}")
        return {"ticker": ticker, **self.quotes[ticker]}

    def portfolio(self, client_id: str) -> dict:
        client = self._client(client_id)
        positions = []
        for h in client["holdings"]:
            price = self.quotes[h["ticker"]]["price"]
            positions.append(
                {
                    "ticker": h["ticker"],
                    "name": self.quotes[h["ticker"]]["name"],
                    "shares": h["shares"],
                    "price": price,
                    "market_value": round(h["shares"] * price, 2),
                    "cost_basis_per_share": h["cost_basis"],
                }
            )
        return {
            "client_id": client_id,
            "portfolio_id": client["portfolio_id"],
            "account_number": client["account_number"],
            "positions": positions,
            "total_value": round(sum(p["market_value"] for p in positions), 2),
        }

    def metrics(self, client_id: str) -> dict:
        portfolio = self.portfolio(client_id)
        total = portfolio["total_value"]
        weights, gains = [], 0.0
        for p in portfolio["positions"]:
            asset_type = self.quotes[p["ticker"]]["type"]
            weights.append(
                {"ticker": p["ticker"], "type": asset_type, "weight_pct": round(100 * p["market_value"] / total, 2)}
            )
            gains += p["market_value"] - p["shares"] * p["cost_basis_per_share"]
        equities = [w for w in weights if w["type"] == "equity"]
        largest_issuer = max(equities, key=lambda w: w["weight_pct"]) if equities else None
        return {
            "client_id": client_id,
            "risk_profile": self._client(client_id)["risk_profile"],
            "total_value": total,
            "unrealized_gain": round(gains, 2),
            "weights": sorted(weights, key=lambda w: w["weight_pct"], reverse=True),
            "largest_single_issuer": largest_issuer,
        }

    def profile(self, client_id: str) -> dict:
        client = self._client(client_id)
        fields = ("name", "email", "risk_profile", "account_number", "tax_id")
        return {"client_id": client_id, **{f: client[f] for f in fields}}

    def list_clients(self) -> list[dict]:
        return [
            {"client_id": cid, "name": c["name"], "account_number": c["account_number"], "risk_profile": c["risk_profile"]}
            for cid, c in self.clients.items()
        ]

    def secrets(self) -> list[Secret]:
        return [
            Secret(f"{cid}:{field}", c[field], client_id=cid)
            for cid, c in self.clients.items()
            for field in ("account_number", "tax_id", "portfolio_id")
        ]


@lru_cache(maxsize=1)
def load_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase(config.DOCUMENTS_DIR)


@lru_cache(maxsize=1)
def load_client_store() -> ClientStore:
    return ClientStore(config.CLIENTS_FILE)
