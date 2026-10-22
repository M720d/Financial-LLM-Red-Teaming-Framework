from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from finguard.data import load_client_store, load_knowledge_base


class ScriptedChatModel(BaseChatModel):
    """Returns pre-written replies in order, so the assistant loop can be tested without an API."""

    replies: list[AIMessage]
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        reply = self.replies[min(self._index, len(self.replies) - 1)]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])


def tool_call(name: str, call_id: str = "call_1", **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def store():
    return load_client_store()


@pytest.fixture
def kb():
    return load_knowledge_base()


@pytest.fixture
def client(store):
    return store.principals["client"]
