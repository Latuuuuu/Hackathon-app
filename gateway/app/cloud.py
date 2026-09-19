"""Client for the cloud task server (Manta, see APP_API.md).

Flow: chat (possibly several turns) -> mission_id + bt_xml -> user confirms -> execute on
Manta -> bt_engine run_id -> status is followed on bt_engine directly -> feedback per mission.
"""
import asyncio
import json
import logging
import time
import uuid
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .bt import BtClient, BtEngineError
from .config import Settings

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class ResetRequest(BaseModel):
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=4000)
    grip_force: Literal["too_weak", "ok", "too_strong"] | None = None


class CloudError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------- helpers

GRIP_STEP = 10


def gripper_position(bt_xml: str | None) -> int | None:
    """Closing position used by the tree: the largest SetGripper position (0 = open)."""
    if not bt_xml:
        return None
    try:
        root = ET.fromstring(bt_xml)
    except ET.ParseError:
        return None
    values = []
    for node in root.iter("SetGripper"):
        try:
            values.append(round(float(node.get("position", ""))))
        except ValueError:
            continue
    return max(values) if values else None


def adjusted_grip(base: int | None, grip_force: str | None) -> int | None:
    """too_weak -> grip harder (+10), too_strong -> softer (-10), ok -> keep."""
    if base is None or grip_force is None:
        return None
    delta = {"too_weak": GRIP_STEP, "ok": 0, "too_strong": -GRIP_STEP}[grip_force]
    return max(0, min(100, base + delta))


def find_key(obj: Any, key: str) -> Any:
    """First non-empty value of `key` anywhere in a nested JSON structure."""
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = find_key(child, key)
        if found:
            return found
    return None


def as_text(item: Any) -> str:
    """Manta sends some lists as strings and some as objects, e.g. {"field", "question"}."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("question", "message", "description", "name", "text"):
            if isinstance(item.get(key), str):
                return item[key]
    return json.dumps(item, ensure_ascii=False)


def summarize_candidate(session_id: str | None, candidate: dict[str, Any]) -> dict[str, Any]:
    """The browser only needs a small part of Manta's (very large) candidate."""
    plan = candidate.get("compact_semantic_plan") or {}
    bt_xml = candidate.get("bt_xml")
    mission_id = candidate.get("mission_id")
    return {
        "session_id": session_id,
        "status": candidate.get("status"),
        "message": as_text(candidate.get("message") or ""),
        "questions": [as_text(q) for q in candidate.get("questions") or []],
        "missing_capabilities": [as_text(c) for c in candidate.get("missing_capabilities") or []],
        "mission_id": mission_id,
        "executable": bool(mission_id and bt_xml and candidate.get("status") == "SUCCESS"),
        "goal": as_text(plan.get("goal_description") or ""),
        "steps": [
            {
                "action": as_text(s.get("action") or ""),
                "arguments": s.get("arguments") if isinstance(s.get("arguments"), dict) else {},
                "objective": as_text(s.get("objective") or ""),
            }
            for s in plan.get("steps") or []
        ],
        "gripper_position": gripper_position(bt_xml),
        "bt_xml": bt_xml,
    }


class RunIndex:
    """run_id -> mission, so the feedback page (any device) can find the mission of a run."""

    def __init__(self, path: Path):
        self._path = path

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def put(self, run_id: str, record: dict[str, Any]) -> None:
        data = self._load()
        data[run_id] = record
        # Keep the file small: only the latest 500 runs.
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("t", 0))[-500:])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._load().get(run_id)


# ---------------------------------------------------------------- clients

class CloudTaskClient(ABC):
    def __init__(self, settings: Settings):
        self.runs = RunIndex(Path(settings.data_dir) / "runs.json")
        self._data_dir = Path(settings.data_dir)

    def _log(self, name: str, record: dict[str, Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._data_dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **record}, ensure_ascii=False) + "\n")

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...

    @abstractmethod
    async def chat(self, req: ChatRequest) -> dict[str, Any]: ...

    @abstractmethod
    async def reset(self, req: ResetRequest) -> dict[str, Any]: ...

    @abstractmethod
    async def mission_xml(self, mission_id: str) -> str | None: ...

    @abstractmethod
    async def _execute(self, mission_id: str) -> str | None:
        """Start the mission on bt_engine and return its run_id (None if unknown)."""

    @abstractmethod
    async def cancel(self, mission_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def _feedback(self, mission_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def execute(self, mission_id: str, prompt: str = "") -> dict[str, Any]:
        run_id = await self._execute(mission_id)
        if run_id:
            self.runs.put(run_id, {"mission_id": mission_id, "prompt": prompt, "t": time.time()})
        return {"ok": True, "mission_id": mission_id, "run_id": run_id}

    async def feedback(self, mission_id: str, fb: FeedbackRequest) -> dict[str, Any]:
        base = gripper_position(await self.mission_xml(mission_id))
        grip = adjusted_grip(base, fb.grip_force)
        payload: dict[str, Any] = {"rating": fb.rating, "comment": fb.comment}
        if grip is not None:
            payload["parameters"] = {"set_gripper_position": grip}
        out = await self._feedback(mission_id, payload)
        return {"ok": True, "sent": payload, "base_gripper_position": base, **out}

    async def aclose(self) -> None:
        pass


class MockCloudClient(CloudTaskClient):
    """Offline stand-in for Manta. With MOCK_EXECUTE=true it runs a demo tree on bt_engine."""

    DEMO_TREE = """<root BTCPP_format="4" main_tree_to_execute="demo">
  <BehaviorTree ID="demo">
    <Sequence name="demo_find_and_grab">
      <VisualizeObject name="start_looking" object_name="cup"/>
      <SetGripper name="open_gripper" position="0"/>
      <TrackObject name="close_in" object_name="cup" distance="contact"/>
      <SetGripper name="close_gripper" position="80"/>
    </Sequence>
  </BehaviorTree>
</root>
"""

    def __init__(self, settings: Settings, bt: BtClient):
        super().__init__(settings)
        self._bt = bt
        self._execute_enabled = settings.mock_execute
        self._tree = Path(settings.mock_tree_file).read_text() if settings.mock_tree_file else self.DEMO_TREE
        self._missions: dict[str, str] = {}

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "mock"}

    async def chat(self, req: ChatRequest) -> dict[str, Any]:
        session_id = req.session_id or uuid.uuid4().hex
        self._log("chat.jsonl", {"session_id": session_id, "message": req.message})
        await asyncio.sleep(1.0)
        if len(req.message.strip()) < 4:
            return summarize_candidate(session_id, {
                "status": "NEED_MORE_INFO",
                "message": "（mock）請再說清楚一點：要找什麼物品？找到之後要做什麼？",
                "questions": ["要找的物品是什麼？", "找到後要夾起來嗎？"],
            })
        mission_id = uuid.uuid4().hex
        self._missions[mission_id] = self._tree
        return summarize_candidate(session_id, {
            "status": "SUCCESS",
            "message": "（mock）已產生行為樹。",
            "mission_id": mission_id,
            "bt_xml": self._tree,
            "compact_semantic_plan": {
                "goal_description": req.message,
                "steps": [
                    {"action": "VisualizeObject", "arguments": {"object_name": "cup"}, "objective": "找到杯子"},
                    {"action": "TrackObject", "arguments": {"object_name": "cup"}, "objective": "靠近杯子"},
                    {"action": "SetGripper", "arguments": {"position": 80}, "objective": "夾住杯子"},
                ],
            },
        })

    async def reset(self, req: ResetRequest) -> dict[str, Any]:
        return {"session_id": uuid.uuid4().hex, "previous_session_id": req.session_id}

    async def mission_xml(self, mission_id: str) -> str | None:
        return self._missions.get(mission_id)

    async def _execute(self, mission_id: str) -> str | None:
        if mission_id not in self._missions:
            raise CloudError("Mission not found", 404)
        if not self._execute_enabled:
            raise CloudError("mock mode: MOCK_EXECUTE=false, nothing was sent to bt_engine", 409)
        try:
            code, body = await self._bt.execute(self._missions[mission_id])
        except BtEngineError as e:
            raise CloudError(str(e)) from e
        if code >= 300:
            raise CloudError(f"bt_engine /execute {code}: {body.get('error')}")
        return body.get("run_id")

    async def cancel(self, mission_id: str) -> dict[str, Any]:
        code, body = await self._bt.cancel()
        return {"ok": code < 300, **body}

    async def _feedback(self, mission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._log("feedback.jsonl", {"mission_id": mission_id, **payload})
        return {"mock": True}


class MantaCloudClient(CloudTaskClient):
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        if not settings.cloud_url:
            raise ValueError("CLOUD_MODE=http needs CLOUD_URL")
        headers = {"Authorization": f"Bearer {settings.cloud_token}"} if settings.cloud_token else {}
        self._client = httpx.AsyncClient(
            base_url=settings.cloud_url.rstrip("/"),
            headers=headers,
            timeout=settings.cloud_timeout_s,
            transport=transport,
        )
        self._chat_timeout = settings.cloud_chat_timeout_s
        self._pipeline = settings.cloud_pipeline_mode
        self._allow_vision = settings.cloud_allow_vision

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise CloudError(f"Manta did not answer {path} in time", 504) from e
        except httpx.HTTPError as e:
            raise CloudError(f"Manta unreachable: {e.__class__.__name__}: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text[:300]}
        if r.status_code >= 300:
            detail = body.get("detail") if isinstance(body, dict) else body
            if isinstance(detail, list):  # FastAPI validation errors
                detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
            # Pass 404/409/422 through so the app can explain them; the rest is a gateway problem.
            raise CloudError(f"Manta {path}: {detail}", r.status_code if r.status_code in (404, 409, 422) else 502)
        return body

    async def health(self) -> dict[str, Any]:
        body = await self._call("GET", "/health")
        return {"ok": bool(body.get("ok")), "mode": "http", "version": body.get("version")}

    async def chat(self, req: ChatRequest) -> dict[str, Any]:
        body = await self._call(
            "POST",
            "/api/chat",
            json={
                "message": req.message,
                "session_id": req.session_id,
                "pipeline_mode": self._pipeline,
                "world_state": {},
                "options": {"allow_vision": self._allow_vision},
            },
            timeout=self._chat_timeout,
        )
        return summarize_candidate(body.get("session_id"), body.get("candidate") or {})

    async def reset(self, req: ResetRequest) -> dict[str, Any]:
        return await self._call("POST", "/api/sessions/reset", json={"previous_session_id": req.session_id})

    async def mission_xml(self, mission_id: str) -> str | None:
        try:
            body = await self._call("GET", f"/api/missions/{mission_id}")
        except CloudError:
            return None
        return body.get("bt_xml")

    async def _execute(self, mission_id: str) -> str | None:
        body = await self._call("POST", f"/api/missions/{mission_id}/engine/execute")
        self._log("manta_execute.jsonl", {"mission_id": mission_id, "response": body})
        run_id = find_key(body, "run_id")
        # Not in the reply: Manta's status knows it once the engine accepted the tree.
        for _ in range(10):
            if run_id:
                break
            await asyncio.sleep(0.5)
            status = await self._call("GET", f"/api/missions/{mission_id}/engine/status", params={"refresh": "true"})
            run_id = find_key(status.get("execution"), "run_id")
        if not run_id:
            log.warning("no run_id for mission %s; execute reply: %s", mission_id, str(body)[:500])
        return run_id

    async def cancel(self, mission_id: str) -> dict[str, Any]:
        body = await self._call("POST", f"/api/missions/{mission_id}/engine/cancel")
        return {"ok": True, "response": body}

    async def _feedback(self, mission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = await self._call("POST", f"/api/missions/{mission_id}/feedback", json=payload)
        return {"response": body}

    async def aclose(self) -> None:
        await self._client.aclose()


def make_cloud_client(settings: Settings, bt: BtClient) -> CloudTaskClient:
    if settings.cloud_mode == "http":
        return MantaCloudClient(settings)
    return MockCloudClient(settings, bt)

