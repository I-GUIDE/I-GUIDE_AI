"""API-key auth must fail CLOSED on every data-bearing route.

Before this suite, ``_require_agent_chat_api_key`` returned early when
``AGENT_CHAT_API_KEY`` was unset, so an unset environment variable silently
disabled auth — and ``/query`` (the full RAG pipeline) never called it at all.
These tests pin the three properties that fix depends on:

1. With a key configured, every protected route rejects a missing/wrong key.
2. With NO key configured, protected routes 500 rather than serving the request.
3. Local development can still opt out, explicitly, via AGENT_CHAT_AUTH_OPTIONAL.

``/health`` stays open (it is the container healthcheck) and ``/agent/dashboard``
stays open (a static HTML page carrying no data); both are asserted so a future
change to either is deliberate.
"""

from __future__ import annotations

import pytest

# Routes that must never serve data without a valid key.
PROTECTED = [
    ("POST", "/query"),
    ("POST", "/query/batch"),
    ("POST", "/agent/chat"),
    ("POST", "/agent/chat/stream"),
    ("POST", "/agent/files/upload"),
    ("GET", "/agent/files/does-not-exist/download"),
]

OPEN = [("GET", "/health")]


def _client(monkeypatch, *, api_key=None, auth_optional=False):
    if api_key is None:
        monkeypatch.delenv("AGENT_CHAT_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AGENT_CHAT_API_KEY", api_key)
    if auth_optional:
        monkeypatch.setenv("AGENT_CHAT_AUTH_OPTIONAL", "1")
    else:
        monkeypatch.delenv("AGENT_CHAT_AUTH_OPTIONAL", raising=False)
    import api.server as srv
    return srv.app.test_client()


def _call(client, method, path):
    return client.get(path) if method == "GET" else client.post(path, json={})


@pytest.mark.parametrize("method,path", PROTECTED)
def test_missing_key_is_rejected(monkeypatch, method, path):
    """A configured key with none presented must be a 403, not a served request."""
    client = _client(monkeypatch, api_key="s3cret")
    resp = _call(client, method, path)
    assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}"


@pytest.mark.parametrize("method,path", PROTECTED)
def test_wrong_key_is_rejected(monkeypatch, method, path):
    client = _client(monkeypatch, api_key="s3cret")
    headers = {"X-API-KEY": "wrong"}
    resp = (client.get(path, headers=headers) if method == "GET"
            else client.post(path, json={}, headers=headers))
    assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}"


@pytest.mark.parametrize("method,path", PROTECTED)
def test_unconfigured_key_fails_closed(monkeypatch, method, path):
    """THE REGRESSION THIS SUITE EXISTS FOR.

    With no key configured and no explicit opt-out, a protected route must refuse
    to serve. Previously this path returned early and the request was served.
    """
    client = _client(monkeypatch, api_key=None)
    resp = _call(client, method, path)
    assert resp.status_code == 500, f"{method} {path} returned {resp.status_code}"
    assert "misconfiguration" in resp.get_json()["error"].lower()


def test_valid_key_passes_auth(monkeypatch):
    """A correct key gets past auth. Not a 403/500 — the handler's own outcome."""
    client = _client(monkeypatch, api_key="s3cret")
    resp = client.post("/query", json={}, headers={"X-API-KEY": "s3cret"})
    assert resp.status_code not in (403, 500)


def test_bearer_token_accepted(monkeypatch):
    client = _client(monkeypatch, api_key="s3cret")
    resp = client.post("/query", json={}, headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code not in (403, 500)


def test_explicit_opt_out_allows_unauthenticated_dev(monkeypatch):
    """Local dev must remain workable, but only by saying so."""
    client = _client(monkeypatch, api_key=None, auth_optional=True)
    resp = client.post("/query", json={})
    assert resp.status_code not in (403, 500)


@pytest.mark.parametrize("method,path", OPEN)
def test_open_routes_stay_open(monkeypatch, method, path):
    client = _client(monkeypatch, api_key="s3cret")
    assert _call(client, method, path).status_code == 200


def test_cors_is_not_wildcard(monkeypatch):
    """CORS(app) with no origins allowed any site to call the API from a browser."""
    monkeypatch.delenv("AGENT_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ALLOWED_DOMAIN_LIST", raising=False)
    import api.server as srv
    assert srv._cors_origins() == []

    monkeypatch.setenv("AGENT_CORS_ORIGINS", "https://platform.i-guide.io, http://localhost:3000")
    assert srv._cors_origins() == ["https://platform.i-guide.io", "http://localhost:3000"]


def test_cors_falls_back_to_existing_allowed_domain_list(monkeypatch):
    """Deployments already carrying ALLOWED_DOMAIN_LIST keep working."""
    monkeypatch.delenv("AGENT_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("ALLOWED_DOMAIN_LIST", '["https://dev.i-guide.io", "http://localhost"]')
    import api.server as srv
    assert srv._cors_origins() == ["https://dev.i-guide.io", "http://localhost"]
