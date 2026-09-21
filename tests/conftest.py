import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.ai.gemini_client import LLMTurn, ToolCall
from app.extensions import limiter as _limiter
from config import Config


class TestConfig(Config):
    # Never actually dialed -- the mongo_client fixture below injects a
    # mongomock client directly into app.extensions before any request runs.
    MONGODB_URI = "mongodb://localhost/test"
    GEMINI_API_KEY = None


@pytest.fixture
def app():
    flask_app = create_app(TestConfig)
    with flask_app.app_context():
        flask_app.extensions["mongo_client"] = mongomock.MongoClient()
        # Flask-Limiter's in-memory storage is a process-wide singleton (the
        # `limiter` object is constructed once at import time in
        # app/extensions.py), not reset by create_app()/init_app() -- without
        # this, rate-limit counters leak across tests (they all share the
        # same "127.0.0.1" key from the Flask test client) and a later test
        # can start already partially-throttled by an earlier, unrelated
        # test's requests. Note: app.extensions["limiter"] is a Flask-Limiter
        # internal bookkeeping set, not this object -- use the import instead.
        _limiter.reset()
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


class FakeGeminiClient:
    """Returns a scripted sequence of LLMTurns -- no network access.

    Usage: FakeGeminiClient([LLMTurn(function_calls=[...]), LLMTurn(text="...")])
    """

    def __init__(self, turns: list[LLMTurn]):
        self._turns = list(turns)
        self.calls = []

    def generate(self, contents, tools, system_instruction):
        self.calls.append({"contents": contents, "tools": tools, "system_instruction": system_instruction})
        if not self._turns:
            return LLMTurn(text="(FakeGeminiClient ran out of scripted turns)")
        return self._turns.pop(0)


__all__ = ["FakeGeminiClient", "LLMTurn", "ToolCall"]
