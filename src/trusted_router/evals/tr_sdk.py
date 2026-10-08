"""Gateway transport for the eval harness.

Originally this went through the official ``trusted-router-py`` SDK
(``pip install trusted-router-py``), which drives every chat completion as a
streaming (SSE) request under the hood regardless of what the caller asks
for, and strictly requires the stream to end with a literal ``data: [DONE]``
sentinel line (see that package's ``src/trustedrouter/_sse.py``,
``_iter_sse_chunks``/``_iter_sse_events``: they raise
``"TrustedRouter SSE stream ended before data: [DONE]"`` if that sentinel
never arrives, discarding every chunk already yielded).

0G router (``https://router-api.0g.ai/v1``) does not send that sentinel --
confirmed by hand with a raw streaming curl call against
``/v1/chat/completions``: the final SSE chunk (carrying ``usage`` and
0G's own ``x_0g_trace`` billing block) arrives correctly, complete, with
``finish_reason: "stop"``, and the connection simply closes with no trailing
``data: [DONE]`` line. Against the original SDK this makes EVERY call fail
with ``status: "failed"``, even when the model's answer was fully and
successfully generated -- the SDK has no partial-content salvage path; it is
all-or-nothing on any stream irregularity, so there is nothing to preserve
by keeping streaming. See git history / PR description for the full
diagnosis.

Fix: bypass the SDK entirely and speak plain, non-streaming OpenAI-compatible
HTTP directly (``"stream": false``), which is unaffected by this gap (0G
router's non-streaming responses are ordinary, complete JSON bodies -- this
was verified first, before touching any code, with the exact curl form 0G
router itself documents). This module keeps the SAME public interface
(``make_client``, ``chat``, ``chat_response``, ``SdkResponse``) so no other
file in this repo needs to change -- `agentic_tools.py` and
`draco_client_fusion.py` both only ever call these four names.

Known gap versus the original SDK: the SDK's built-in 429/5xx retry logic
is gone with it. A minimal replacement (exponential backoff on 429/5xx and
on read timeouts, up to `max_retries` attempts) is implemented below --
simpler than the SDK's own (no regional failover, since we're pointed at a
single explicit `base_url` throughout -- see tr_sdk's original docstring
history), but covers the common transient-failure case.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

import httpx

DEFAULT_GATEWAY = "https://router-api.0g.ai/v1"


class Tr0GClient:
    """Minimal stand-in for `trustedrouter.TrustedRouter`: just remembers
    where to send requests and how to authenticate -- no attestation, no
    regional failover, no streaming. See module docstring for why."""

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 600.0, max_retries: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()


def make_client(*, base_url: str | None = None, api_key: str,
                 timeout: float = 600.0, max_retries: int = 3) -> Tr0GClient:
    """One OpenAI-compatible, non-streaming client pointed at an OpenAI-
    compatible gateway (0G router by default; pass --base-url for any other
    one)."""
    return Tr0GClient(
        base_url=(base_url or DEFAULT_GATEWAY),
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
    )


def chat(client: Tr0GClient, body: Mapping[str, Any]) -> dict[str, Any]:
    """Send an OpenAI-shaped chat body as a plain non-streaming POST; return
    the response as a dict.

    Accepts the exact ``body`` dicts the harness already builds (model,
    messages, tools, reasoning_effort, max_tokens, temperature, ...).
    ``stream_options`` (only meaningful for streaming requests) is dropped;
    ``stream`` is forced to False.
    """
    payload = {k: v for k, v in body.items() if k != "stream_options"}
    payload["stream"] = False
    url = f"{client.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {client.api_key}", "Content-Type": "application/json"}

    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client._client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            if attempt > client.max_retries:
                raise
            time.sleep(min(2 ** attempt, 30))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt > client.max_retries:
                resp.raise_for_status()
            time.sleep(min(2 ** attempt, 30))
            continue
        resp.raise_for_status()
        return resp.json()


class SdkResponse:
    """Adapts a plain response dict to the small ``httpx.Response`` surface
    the harness touches (``.json()`` / ``.raise_for_status()`` /
    ``.status_code``). ``chat()`` already raised on HTTP errors, so
    ``raise_for_status`` here is a no-op."""

    status_code = 200

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.headers: dict[str, str] = {"x-tr-request-id": str(data.get("id") or "")}

    def json(self) -> dict[str, Any]:
        return self._data

    def raise_for_status(self) -> None:
        return None


def chat_response(client: Tr0GClient, body: Mapping[str, Any]) -> SdkResponse:
    """Like :func:`chat` but wrapped so code expecting an httpx.Response works."""
    return SdkResponse(chat(client, body))
