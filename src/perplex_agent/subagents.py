"""Background subagent runs: isolated Sonar completions with concurrency limits."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

from perplex_agent.client import PerplexityClient


class SubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubagentRecord:
    id: str
    instruction: str
    model: str
    depth: int
    status: SubagentStatus = SubagentStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: str | None = None
    error: str | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)


class SubagentManager:
    def __init__(
        self,
        client: PerplexityClient,
        *,
        default_model: str,
        max_concurrent: int,
        subagent_timeout_s: float,
        system_prompt: str,
        on_complete: Callable[[SubagentRecord], Coroutine[Any, Any, None]] | None = None,
        state_file: Path | None = None,
        completion_extra: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._default_model = default_model
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._subagent_timeout_s = subagent_timeout_s
        self._system_prompt = system_prompt
        self._on_complete = on_complete
        self._state_file = state_file
        self._completion_extra = completion_extra or {}
        self._records: dict[str, SubagentRecord] = {}
        self._lock = asyncio.Lock()

    def list_records(self) -> list[SubagentRecord]:
        return list(self._records.values())

    def get(self, sid: str) -> SubagentRecord | None:
        return self._records.get(sid)

    async def spawn(
        self,
        instruction: str,
        *,
        model: str | None = None,
        depth: int = 0,
    ) -> str:
        sid = uuid.uuid4().hex[:12]
        rec = SubagentRecord(
            id=sid,
            instruction=instruction,
            model=model or self._default_model,
            depth=depth,
        )
        async with self._lock:
            self._records[sid] = rec
        self._persist_sync()

        async def _run() -> None:
            rec.status = SubagentStatus.RUNNING
            try:
                messages = [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": instruction},
                ]
                async with self._semaphore:
                    data = await asyncio.wait_for(
                        self._client.chat_completion(
                            model=rec.model,
                            messages=messages,
                            stream=False,
                            extra=self._completion_extra or None,
                        ),
                        timeout=self._subagent_timeout_s,
                    )
                content = extract_message_content(data)
                rec.result = content
                rec.status = SubagentStatus.DONE
            except asyncio.TimeoutError:
                rec.status = SubagentStatus.FAILED
                rec.error = "timeout"
            except asyncio.CancelledError:
                rec.status = SubagentStatus.CANCELLED
                raise
            except Exception as e:  # noqa: BLE001
                rec.status = SubagentStatus.FAILED
                rec.error = str(e)
            finally:
                self._persist_sync()
                if self._on_complete is not None:
                    await self._on_complete(rec)

        task = asyncio.create_task(_run(), name=f"subagent-{sid}")
        rec._task = task
        return sid

    async def wait_ids(self, ids: list[str]) -> dict[str, SubagentRecord]:
        tasks: list[asyncio.Task[Any]] = []
        for sid in ids:
            rec = self._records.get(sid)
            if rec and rec._task:
                tasks.append(rec._task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return {sid: self._records[sid] for sid in ids if sid in self._records}

    async def cancel(self, sid: str) -> bool:
        rec = self._records.get(sid)
        if not rec or not rec._task:
            return False
        rec._task.cancel()
        try:
            await rec._task
        except asyncio.CancelledError:
            pass
        return True

    async def cancel_all(self) -> None:
        tasks = [r._task for r in self._records.values() if r._task and not r._task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _persist_sync(self) -> None:
        if self._state_file is None:
            return
        try:
            rows: list[dict[str, Any]] = []
            for r in self._records.values():
                res = r.result
                if res and len(res) > 6000:
                    res = res[:6000] + "…(truncated)"
                rows.append(
                    {
                        "id": r.id,
                        "instruction": r.instruction[:2000],
                        "model": r.model,
                        "depth": r.depth,
                        "status": r.status.value,
                        "created_at": r.created_at.isoformat(),
                        "result": res,
                        "error": r.error,
                    }
                )
            rows = rows[-200:]
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
        except OSError:
            pass


def extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(str(chunk.get("text", "")))
        return "".join(parts)
    return str(content or "")
