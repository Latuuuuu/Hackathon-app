"""Client for the cloud task server (LLM agent: prompt -> BT XML -> bt_engine).

The real API is not known yet. HttpCloudClient uses provisional routes; only this file
has to change once the real contract arrives.
"""
import json
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .bt import BtClient, BtEngineError
from .config import Settings


class RetryOf(BaseModel):
    run_id: str
    notes: list[str] = []


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    retry_of: RetryOf | None = None


class Feedback(BaseModel):
    run_id: str | None = None
    outcome: Literal["success", "failure", "canceled", "error", "unknown"] = "unknown"
    grip_force: Literal["too_weak", "ok", "too_strong"] | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class CloudError(Exception):
    pass


class CloudTaskClient(ABC):
    @abstractmethod
    async def submit_task(self, task: TaskRequest) -> dict[str, Any]: ...

    @abstractmethod
    async def send_feedback(self, fb: Feedback) -> dict[str, Any]: ...

    async def aclose(self) -> None:
        pass


# Default demo tree for the mock: uses only nodes from the bt_engine palette.
DEMO_TREE = """<root BTCPP_format="4" main_tree_to_execute="demo">
  <BehaviorTree ID="demo">
    <Sequence name="demo_find_and_grab">
      <VisualizeObject name="start_looking" object_name="cup">
        <TrackWhenFound name="stop_when_found" object_name="cup" poll_ms="500">
          <Repeat name="keep_turning" num_cycles="-1">
            <RotateInPlace name="turn_a_little" angle_deg="45"/>
          </Repeat>
        </TrackWhenFound>
      </VisualizeObject>
      <SetGripper name="open_gripper" position="0"/>
      <TrackObject name="close_in" object_name="cup" distance="contact"/>
      <SetGripper name="close_gripper" position="100"/>
    </Sequence>
  </BehaviorTree>
</root>
"""


class MockCloudClient(CloudTaskClient):
    """Logs to data/*.jsonl. With MOCK_EXECUTE=true it also runs a demo tree on bt_engine."""

    def __init__(self, settings: Settings, bt: BtClient):
        self._dir = Path(settings.data_dir)
        self._bt = bt
        self._execute = settings.mock_execute
        self._tree = Path(settings.mock_tree_file).read_text() if settings.mock_tree_file else DEMO_TREE

    def _log(self, name: str, record: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def submit_task(self, task: TaskRequest) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        self._log("tasks.jsonl", {"t": time.time(), "task_id": task_id, **task.model_dump()})
        out: dict[str, Any] = {"ok": True, "task_id": task_id, "mock": True, "executed": False}
        if self._execute:
            try:
                code, body = await self._bt.execute(self._tree)
            except BtEngineError as e:
                raise CloudError(str(e)) from e
            if code >= 300:
                raise CloudError(f"bt_engine /execute {code}: {body.get('error')}")
            out.update(executed=True, run_id=body.get("run_id"))
        return out

    async def send_feedback(self, fb: Feedback) -> dict[str, Any]:
        self._log("feedback.jsonl", {"t": time.time(), **fb.model_dump()})
        return {"ok": True, "mock": True}


class HttpCloudClient(CloudTaskClient):
    """Provisional contract: POST {CLOUD_URL}/tasks and POST {CLOUD_URL}/feedback."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.cloud_url:
            raise ValueError("CLOUD_MODE=http needs CLOUD_URL")
        headers = {"Authorization": f"Bearer {settings.cloud_token}"} if settings.cloud_token else {}
        self._client = httpx.AsyncClient(
            base_url=settings.cloud_url.rstrip("/"),
            headers=headers,
            timeout=settings.cloud_timeout_s,
            transport=transport,
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.post(path, json=payload)
        except httpx.HTTPError as e:
            raise CloudError(f"cloud server unreachable: {e}") from e
        if r.status_code >= 300:
            raise CloudError(f"cloud server {path} {r.status_code}: {r.text[:300]}")
        try:
            body = r.json()
        except ValueError:
            body = {}
        return {"ok": True, **body} if isinstance(body, dict) else {"ok": True, "data": body}

    async def submit_task(self, task: TaskRequest) -> dict[str, Any]:
        return await self._post("/tasks", task.model_dump(exclude_none=True))

    async def send_feedback(self, fb: Feedback) -> dict[str, Any]:
        return await self._post("/feedback", fb.model_dump(exclude_none=True))

    async def aclose(self) -> None:
        await self._client.aclose()


def make_cloud_client(settings: Settings, bt: BtClient) -> CloudTaskClient:
    if settings.cloud_mode == "http":
        return HttpCloudClient(settings)
    return MockCloudClient(settings, bt)
