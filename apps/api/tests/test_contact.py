"""Phase 10B.7 — POST /contact endpoint tests.

Covers:
  * 422 for missing / malformed / too-short fields
  * 503 graceful failure when Resend isn't configured
  * 200 + Resend call when fully configured (mocked)
  * Honeypot silently returns 200
  * Per-IP rate limit kicks in after 3 requests
  * No secrets are echoed in the response body
"""
from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from assembly.api import contact as contact_mod
from assembly.main import app


@pytest.fixture(autouse=True)
def _reset_rate_buckets() -> Iterator[None]:
    """Clear the in-memory rate-limit state between tests so tests
    don't leak per-IP request counts into each other."""
    contact_mod._rate_buckets.clear()
    yield
    contact_mod._rate_buckets.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ------------------------- validation -------------------------------------


def test_empty_body_returns_422(client: TestClient) -> None:
    assert client.post("/contact", json={}).status_code == 422


def test_invalid_email_returns_422(client: TestClient) -> None:
    r = client.post("/contact", json={
        "name": "Alice",
        "email": "not-an-email",
        "message": "hello world this is a long-enough message",
    })
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), list)


def test_message_too_short_returns_422(client: TestClient) -> None:
    r = client.post("/contact", json={
        "name": "Alice",
        "email": "alice@example.com",
        "message": "hi",
    })
    assert r.status_code == 422


def test_name_required(client: TestClient) -> None:
    r = client.post("/contact", json={
        "name": "",
        "email": "alice@example.com",
        "message": "hello there this is a real message",
    })
    assert r.status_code == 422


# ------------------------- graceful failure -------------------------------


def test_returns_503_when_resend_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "assembly.api.contact.get_settings",
        lambda: type(
            "S", (), {
                "resend_api_key": None,
                "contact_to_email": "team@assemblysimulator.com",
                "contact_from_email": "no-reply@assemblysimulator.com",
            },
        )(),
    )
    r = client.post("/contact", json={
        "name": "Alice",
        "email": "alice@example.com",
        "message": "I'd love to chat about Assembly for my startup.",
    })
    assert r.status_code == 503
    detail = r.json()["detail"]
    # The error message should NOT contain the api key (None or not),
    # the SMTP host, or any other secret.
    assert "Bearer" not in detail
    assert "Authorization" not in detail
    assert "team@assemblysimulator.com" in detail


# ------------------------- happy path (Resend mocked) ---------------------


def test_happy_path_calls_resend_and_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the API key is set, the endpoint POSTs to Resend and
    returns 200 + a user-visible 'thanks' message. We mock the
    httpx client so the test doesn't actually hit Resend."""
    monkeypatch.setattr(
        "assembly.api.contact.get_settings",
        lambda: type(
            "S", (), {
                "resend_api_key": "rs-test-key",
                "contact_to_email": "team@assemblysimulator.com",
                "contact_from_email": "no-reply@assemblysimulator.com",
            },
        )(),
    )

    captured: dict[str, object] = {}

    class _MockResp:
        status_code = 200
        content = b'{"id":"em_test"}'

    class _MockClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        async def __aenter__(self) -> "_MockClient":
            return self
        async def __aexit__(self, *args: object) -> None: ...
        async def post(
            self, url: str, **kwargs: object,
        ) -> "_MockResp":
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            return _MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    r = client.post("/contact", json={
        "name": "Alice Smith",
        "email": "alice@example.com",
        "message": "Hi — I'd love a demo for my company.",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "back to you" in body["detail"].lower()
    # Verify the outgoing email shape — name + email + message all
    # land in the rendered body.
    sent = captured["json"]
    assert isinstance(sent, dict)
    assert sent["to"] == ["team@assemblysimulator.com"]
    assert sent["from"] == "no-reply@assemblysimulator.com"
    assert sent["reply_to"] == "alice@example.com"
    assert "Alice Smith" in (sent["subject"] + sent["text"] + sent["html"])
    assert "love a demo" in sent["text"]
    # The Resend bearer token must be in the Authorization header,
    # not the response or the email body.
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer rs-test-key"
    # And not leaked to the user
    assert "rs-test-key" not in r.text


def test_resend_5xx_returns_502_graceful(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "assembly.api.contact.get_settings",
        lambda: type(
            "S", (), {
                "resend_api_key": "rs-test-key",
                "contact_to_email": "team@assemblysimulator.com",
                "contact_from_email": "no-reply@assemblysimulator.com",
            },
        )(),
    )

    class _BadResp:
        status_code = 500
        content = b'{"internal_secret":"do-not-leak"}'

    class _MockClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        async def __aenter__(self) -> "_MockClient":
            return self
        async def __aexit__(self, *args: object) -> None: ...
        async def post(
            self, url: str, **kwargs: object,
        ) -> "_BadResp":
            return _BadResp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    r = client.post("/contact", json={
        "name": "Alice",
        "email": "alice@example.com",
        "message": "this should fail at the email provider step.",
    })
    assert r.status_code == 502
    # Resend's internal body content must not leak to the user.
    assert "internal_secret" not in r.text
    assert "do-not-leak" not in r.text


# ------------------------- honeypot ---------------------------------------


def test_honeypot_silently_succeeds(client: TestClient) -> None:
    """A non-empty `company` field means a bot. We silently 200 so
    the bot doesn't learn it was flagged — and we never call Resend."""
    r = client.post("/contact", json={
        "name": "Bot",
        "email": "bot@spam.com",
        "message": "buy cheap stuff click here now",
        "company": "EvilCorp Inc",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ------------------------- rate limit -------------------------------------


def test_rate_limit_kicks_in_after_three_requests(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint allows 3 contact submissions per IP per 5-minute
    sliding window. The 4th gets 429."""
    monkeypatch.setattr(
        "assembly.api.contact.get_settings",
        lambda: type(
            "S", (), {
                "resend_api_key": "rs-test-key",
                "contact_to_email": "team@assemblysimulator.com",
                "contact_from_email": "no-reply@assemblysimulator.com",
            },
        )(),
    )

    class _OkResp:
        status_code = 200
        content = b'{"id":"em_test"}'

    class _MockClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        async def __aenter__(self) -> "_MockClient":
            return self
        async def __aexit__(self, *args: object) -> None: ...
        async def post(self, url: str, **kwargs: object) -> "_OkResp":
            return _OkResp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    payload = {
        "name": "Alice",
        "email": "alice@example.com",
        "message": "Hello there this is a long-enough message body",
    }
    statuses = []
    for _ in range(4):
        statuses.append(client.post("/contact", json=payload).status_code)
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429
