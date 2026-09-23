import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.ai.conversation import LLMTurn, ToolCall
from app.extensions import limiter as _limiter
from config import Config

TEST_ENCRYPTION_KEY = "i7PzLC-A21LUnz4uXKQVjP58nCNiwHkuQEKR6WmOgdw="


class TestConfig(Config):
    # Never actually dialed -- the mongo_client fixture below injects a
    # mongomock client directly into app.extensions before any request runs.
    MONGODB_URI = "mongodb://localhost/test"
    # A fixed, test-only Fernet key -- never used outside the suite.
    AI_CREDENTIALS_ENCRYPTION_KEY = TEST_ENCRYPTION_KEY
    # Admin login/logout tests post plain forms without a real browser
    # session to fetch a token from -- CSRF itself is Flask-WTF's concern,
    # not app logic, so it's off for the suite and left on in real config.
    WTF_CSRF_ENABLED = False
    # Explicitly unset (not just inherited) -- otherwise a real UPI_ID set in
    # a developer's own .env leaks into the "not configured" test cases via
    # Config's os.environ.get() fallback, since load_dotenv() runs for real.
    UPI_ID = None


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


class FakeAIClient:
    """Stands in for any provider adapter -- returns a scripted sequence of LLMTurns, no network access.

    Usage: FakeAIClient([LLMTurn(function_calls=[...]), LLMTurn(text="...")])
    """

    def __init__(self, turns: list[LLMTurn]):
        self._turns = list(turns)
        self.calls = []

    def generate(self, history, tools, system_instruction):
        # Snapshot the list -- ChatAgent keeps appending to the same one.
        self.calls.append({"history": list(history), "tools": tools, "system_instruction": system_instruction})
        if not self._turns:
            return LLMTurn(text="(FakeAIClient ran out of scripted turns)")
        return self._turns.pop(0)


def activate_provider(provider="gemini", api_key="test-gemini-key", model="gemini-test-model"):
    """Save working credentials for `provider` and make it the active one. Needs an app context."""
    from app.services import ai_provider_service

    ai_provider_service.save_credentials(
        provider, api_key=api_key, model=model, updated_by="usr_test", test_result=ai_provider_service.TEST_OK
    )
    ai_provider_service.set_active(provider, updated_by="usr_test")


__all__ = ["FakeAIClient", "LLMTurn", "ToolCall", "TEST_ENCRYPTION_KEY", "activate_provider"]
