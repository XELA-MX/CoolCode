"""Async HTTP client for Perplexity Sonar (`POST /v1/sonar`)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

SONAR_URL = "https://api.perplexity.ai/v1/sonar"


class PerplexityAPIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Perplexity API error {status_code}: {body[:500]}")


class PerplexityClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 120.0,
        base_url: str = SONAR_URL,
    ) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_s, connect=30.0)
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if extra:
            payload.update(extra)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if not stream:
                r = await client.post(
                    self._base_url,
                    headers=self._headers(),
                    json=payload,
                )
                text = r.text
                if r.status_code >= 400:
                    raise PerplexityAPIError(r.status_code, text)
                return r.json()

            r = await client.send(
                client.build_request(
                    "POST",
                    self._base_url,
                    headers=self._headers(),
                    json=payload,
                ),
                stream=True,
            )
            try:
                if r.status_code >= 400:
                    body = await r.aread()
                    raise PerplexityAPIError(r.status_code, body.decode("utf-8", errors="replace"))
                chunks: list[str] = []
                async for line in r.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for choice in obj.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                chunks.append(content)
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "".join(chunks),
                            }
                        }
                    ]
                }
            finally:
                await r.aclose()

    async def chat_completion_stream_text(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if extra:
            payload.update(extra)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                self._base_url,
                headers=self._headers(),
                json=payload,
            ) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    raise PerplexityAPIError(r.status_code, body.decode("utf-8", errors="replace"))
                async for line in r.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for choice in obj.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                yield content
