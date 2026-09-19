import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from app import calib
from app.config import Settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
BT = "http://bt.test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        bt_engine_url=BT,
        bt_engine_token="secret",
        cloud_mode="mock",
        calib_mode="mock",
        field_file=tmp_path / "field_app.yaml",
        field_template=FIXTURES / "field.yaml",
        calib_output_dir=tmp_path / "calib_out",
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "no_web",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c


# ---------------------------------------------------------------- BT proxy

@respx.mock
def test_status_passthrough_sends_token(client):
    body = {"run_id": "run-3", "state": "running", "running_leaves": ["look"], "notes": []}
    route = respx.get(f"{BT}/status").mock(return_value=httpx.Response(200, json=body))
    r = client.get("/api/bt/status")
    assert r.status_code == 200 and r.json() == body
    assert route.calls.last.request.headers["X-BT-Token"] == "secret"


@respx.mock
@pytest.mark.parametrize("code,err", [(401, "missing or wrong token"), (404, "no such run")])
def test_status_errors_pass_through(client, code, err):
    respx.get(f"{BT}/status/run-9").mock(return_value=httpx.Response(code, json={"ok": False, "error": err}))
    r = client.get("/api/bt/status/run-9")
    assert r.status_code == code and r.json()["error"] == err


@respx.mock
def test_full_trace_query(client):
    route = respx.get(f"{BT}/status/run-1").mock(return_value=httpx.Response(200, json={"state": "success"}))
    client.get("/api/bt/status/run-1?trace=full")
    assert route.calls.last.request.url.params["trace"] == "full"


@respx.mock
def test_cancel(client):
    respx.post(f"{BT}/cancel").mock(return_value=httpx.Response(200, json={"ok": True, "was_running": True}))
    assert client.post("/api/bt/cancel").json()["was_running"] is True


@respx.mock
def test_engine_down_is_502(client):
    respx.get(f"{BT}/health").mock(side_effect=httpx.ConnectError("refused"))
    r = client.get("/api/bt/health")
    assert r.status_code == 502 and "unreachable" in r.json()["error"]


# ---------------------------------------------------------------- cloud: helpers

def test_gripper_position_and_adjustment():
    from app.cloud import adjusted_grip, gripper_position

    chat = json.loads((FIXTURES / "manta_chat.json").read_text())
    assert gripper_position(chat["candidate"]["bt_xml"]) == 80
    assert gripper_position('<root><SetGripper position="0"/><SetGripper position="55.4"/></root>') == 55
    assert gripper_position("<root><Sequence/></root>") is None
    assert gripper_position("not xml") is None
    assert adjusted_grip(80, "too_weak") == 90
    assert adjusted_grip(80, "too_strong") == 70
    assert adjusted_grip(80, "ok") == 80
    assert adjusted_grip(95, "too_weak") == 100 and adjusted_grip(5, "too_strong") == 0
    assert adjusted_grip(None, "too_weak") is None and adjusted_grip(80, None) is None


# ---------------------------------------------------------------- cloud: mock

def test_mock_chat_clarifies_then_plans(client):
    r = client.post("/api/chat", json={"message": "杯子"}).json()
    assert r["status"] == "NEED_MORE_INFO" and r["questions"] and not r["executable"]
    r2 = client.post("/api/chat", json={"message": "找到杯子並夾起來", "session_id": r["session_id"]}).json()
    assert r2["executable"] and r2["session_id"] == r["session_id"] and r2["gripper_position"] == 80


def test_mock_execute_disabled_by_default(client):
    mid = client.post("/api/chat", json={"message": "找到杯子並夾起來"}).json()["mission_id"]
    r = client.post(f"/api/missions/{mid}/execute", json={"prompt": "x"})
    assert r.status_code == 409 and "MOCK_EXECUTE" in r.json()["error"]


def test_mock_execute_records_run_and_feedback(settings):
    settings.mock_execute = True
    with respx.mock:
        respx.post(f"{BT}/execute").mock(
            return_value=httpx.Response(202, json={"ok": True, "run_id": "run-7", "preempted_previous": False})
        )
        with TestClient(create_app(settings)) as c:
            mid = c.post("/api/chat", json={"message": "找到杯子並夾起來"}).json()["mission_id"]
            r = c.post(f"/api/missions/{mid}/execute", json={"prompt": "找到杯子並夾起來"}).json()
            assert r["run_id"] == "run-7"
            assert c.get("/api/runs/run-7/mission").json()["mission_id"] == mid
            assert c.get("/api/runs/run-8/mission").status_code == 404
            fb = c.post(f"/api/missions/{mid}/feedback", json={"rating": 4, "grip_force": "too_strong", "comment": "太緊"}).json()
    assert fb["sent"] == {"rating": 4, "comment": "太緊", "parameters": {"set_gripper_position": 70}}
    rec = json.loads((settings.data_dir / "feedback.jsonl").read_text().splitlines()[-1])
    assert rec["parameters"]["set_gripper_position"] == 70


def test_feedback_validation(client):
    assert client.post("/api/missions/m1/feedback", json={"grip_force": "ok"}).status_code == 422  # rating required
    assert client.post("/api/missions/m1/feedback", json={"rating": 9}).status_code == 422
    assert client.post("/api/missions/m1/feedback", json={"rating": 3, "grip_force": "huge"}).status_code == 422


def test_bad_ids_rejected(client):
    assert client.post("/api/missions/..%2F..%2Fadmin/execute", json={}).status_code in (404, 422)
    assert client.post("/api/missions/a%20b/cancel").status_code == 422
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


# ---------------------------------------------------------------- cloud: Manta

MANTA = "http://manta.test"


@pytest.fixture
def manta_settings(settings):
    settings.cloud_mode = "http"
    settings.cloud_url = MANTA
    return settings


def test_manta_chat_is_summarized(manta_settings):
    chat = json.loads((FIXTURES / "manta_chat.json").read_text())
    with respx.mock:
        route = respx.post(f"{MANTA}/api/chat").mock(return_value=httpx.Response(200, json=chat))
        with TestClient(create_app(manta_settings)) as c:
            r = c.post("/api/chat", json={"message": "找到杯子並夾起來", "session_id": "s-1"}).json()
    sent = json.loads(route.calls.last.request.content)
    assert sent["message"] == "找到杯子並夾起來" and sent["session_id"] == "s-1"
    assert sent["pipeline_mode"] == "hybrid" and sent["options"] == {"allow_vision": False}
    assert r["executable"] and r["mission_id"] == chat["candidate"]["mission_id"]
    assert r["session_id"] == chat["session_id"] and r["gripper_position"] == 80
    assert [s["action"] for s in r["steps"]] == ["VisualizeObject", "NavigateToDetectedObject", "SetGripper"]
    assert "rag_context" not in r  # the huge candidate is not forwarded


def test_manta_need_more_info(manta_settings):
    # Real shape seen from Manta 6.4.0: questions are objects, not strings.
    reply = {"session_id": "s-2", "candidate": {
        "status": "NEED_MORE_INFO", "message": "要拿哪一個？",
        "questions": [{"field": "object_name", "question": "哪個杯子？"}, "放哪裡？", {"field": "x"}],
        "missing_capabilities": [{"name": "open_door"}],
    }}
    with respx.mock:
        respx.post(f"{MANTA}/api/chat").mock(return_value=httpx.Response(200, json=reply))
        with TestClient(create_app(manta_settings)) as c:
            r = c.post("/api/chat", json={"message": "拿杯子"}).json()
    assert r["status"] == "NEED_MORE_INFO" and not r["executable"]
    assert r["questions"] == ["哪個杯子？", "放哪裡？", '{"field": "x"}']
    assert r["missing_capabilities"] == ["open_door"]


@pytest.mark.parametrize("execute_reply,status_reply", [
    ({"ok": True, "run_id": "run-3"}, None),                                   # run_id in the reply
    ({"ok": True, "execution": {"engine": {"run_id": "run-3"}}}, None),        # nested
    ({"ok": True}, {"execution": {"run_id": "run-3", "state": "running"}}),     # only in status
])
def test_manta_execute_finds_run_id(manta_settings, execute_reply, status_reply):
    with respx.mock:
        respx.post(f"{MANTA}/api/missions/m-1/engine/execute").mock(return_value=httpx.Response(200, json=execute_reply))
        respx.get(f"{MANTA}/api/missions/m-1/engine/status").mock(return_value=httpx.Response(200, json=status_reply or {}))
        with TestClient(create_app(manta_settings)) as c:
            r = c.post("/api/missions/m-1/execute", json={"prompt": "拿杯子"}).json()
            rec = c.get("/api/runs/run-3/mission").json()
    assert r["run_id"] == "run-3" and rec["mission_id"] == "m-1" and rec["prompt"] == "拿杯子"


def test_manta_errors_pass_through(manta_settings):
    with respx.mock:
        respx.post(f"{MANTA}/api/missions/nope/engine/execute").mock(
            return_value=httpx.Response(404, json={"detail": "Mission not found"}))
        respx.post(f"{MANTA}/api/missions/m-1/feedback").mock(
            return_value=httpx.Response(409, json={"detail": "execution not terminal"}))
        respx.get(f"{MANTA}/api/missions/m-1").mock(return_value=httpx.Response(200, json={"bt_xml": None}))
        respx.post(f"{MANTA}/api/chat").mock(side_effect=httpx.ConnectError("down"))
        with TestClient(create_app(manta_settings)) as c:
            r404 = c.post("/api/missions/nope/execute", json={})
            r409 = c.post("/api/missions/m-1/feedback", json={"rating": 3})
            r502 = c.post("/api/chat", json={"message": "hi"})
    assert r404.status_code == 404 and "Mission not found" in r404.json()["error"]
    assert r409.status_code == 409
    assert r502.status_code == 502 and "unreachable" in r502.json()["error"]


def test_manta_feedback_uses_mission_tree(manta_settings):
    mission = json.loads((FIXTURES / "manta_mission.json").read_text())
    with respx.mock:
        respx.get(f"{MANTA}/api/missions/m-1").mock(return_value=httpx.Response(200, json=mission))
        fb = respx.post(f"{MANTA}/api/missions/m-1/feedback").mock(return_value=httpx.Response(200, json={"ok": True}))
        with TestClient(create_app(manta_settings)) as c:
            r = c.post("/api/missions/m-1/feedback", json={"rating": 5, "grip_force": "too_weak"}).json()
    assert json.loads(fb.calls.last.request.content) == {
        "rating": 5, "comment": "", "parameters": {"set_gripper_position": 90}}
    assert r["base_gripper_position"] == 80


def test_manta_cancel_falls_back_to_engine(manta_settings):
    with respx.mock:
        respx.post(f"{MANTA}/api/missions/m-1/engine/cancel").mock(side_effect=httpx.ConnectError("down"))
        bt = respx.post(f"{BT}/cancel").mock(return_value=httpx.Response(200, json={"ok": True, "was_running": True}))
        with TestClient(create_app(manta_settings)) as c:
            r = c.post("/api/missions/m-1/cancel").json()
    assert bt.called and r["fallback"] == "bt_engine" and r["was_running"] is True


# ---------------------------------------------------------------- calibration

def test_field_read_from_template(client):
    assert client.get("/api/calib/field").json() == {
        "length": 1.8, "depth": 0.6, "count": 2, "disabled_segments": []
    }


def test_field_file_seeded_on_startup(client, settings):
    data = yaml.safe_load(settings.field_file.read_text())
    assert data["table"] == {"length": 1.8, "depth": 0.6, "count": 2}


def test_unwritable_field_file_keeps_gateway_up(settings, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    settings.field_file = blocker / "field_app.yaml"  # parent is a file: mkdir fails
    with respx.mock:
        respx.get(f"{BT}/health").mock(return_value=httpx.Response(200, json={"ok": True}))
        with TestClient(create_app(settings)) as c:
            assert c.get("/api/bt/health").status_code == 200
            state = c.get("/api/calib/state").json()
            assert state["error"] and state["connected"] is False
            assert c.post("/api/calib/run").status_code == 503
            body = {"length": 1.8, "depth": 0.6, "count": 1}
            assert c.put("/api/calib/field", json=body).status_code == 503


def test_field_write_keeps_tuning_keys(client, settings):
    r = client.put("/api/calib/field", json={"length": 1.2, "depth": 0.6, "count": 3, "disabled_segments": ["near"]})
    assert r.status_code == 200
    data = yaml.safe_load(settings.field_file.read_text())
    assert data["table"] == {"length": 1.2, "depth": 0.6, "count": 3}
    assert data["disabled_segments"] == ["near"]
    assert data["corner_exclusion"] == 0.08 and data["use_seam"] is True
    assert client.get("/api/calib/field").json()["count"] == 3


@pytest.mark.parametrize("body", [
    {"length": 0, "depth": 0.6, "count": 1},
    {"length": 1.8, "depth": 0.6, "count": 0},
    {"length": 1.8, "depth": 0.6, "count": 1, "disabled_segments": ["../etc"]},
])
def test_field_validation(client, body):
    assert client.put("/api/calib/field", json=body).status_code == 422


def _seed_run(settings: Settings, name: str) -> Path:
    d = settings.calib_output_dir / name
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "cam_tf.yaml", d / "cam_tf.yaml")
    (d / "final_overlay.png").write_bytes(b"\x89PNG fake")
    return d


def test_result_and_images(client, settings):
    assert client.get("/api/calib/result").status_code == 404
    _seed_run(settings, "20260919_095507")
    shutil.copy(FIXTURES / "cam_tf.yaml", settings.calib_output_dir / "cam_tf.yaml")
    res = client.get("/api/calib/result").json()
    assert res["passed"] is True and res["quality"]["rms_px"] == 0.6126
    assert "debug_dir" not in res
    assert client.get("/api/calib/image/final_overlay").content == b"\x89PNG fake"
    assert client.get("/api/calib/image/final_overlay?run=20260919_095507").status_code == 200
    assert client.get("/api/calib/image/final_overlay?run=..").status_code == 400
    assert client.get("/api/calib/image/secret").status_code == 404


def test_latest_run_and_frame_counts(client, settings):
    assert client.get("/api/calib/runs/latest").status_code == 404
    _seed_run(settings, "20260919_095507")
    r = client.get("/api/calib/runs/latest").json()
    assert r["run"] == "20260919_095507" and r["success"] is True and r["result"]["quality"]["rms_px"] == 0.6126
    assert client.get("/api/calib/state").json()["frame_counts"] == {"live": 0, "calib": 0, "camera": 0}


def test_mock_run_reports_new_run_only(settings):
    class Fake(calib.MockCalibBackend):
        async def _trigger(self):
            _seed_run(self.settings, "20260920_000000")
            return True, "ok"

    app = create_app(settings)
    with TestClient(app) as c:
        app.state.calib = Fake(settings)
        _seed_run(settings, "20260919_095507")
        r = c.post("/api/calib/run").json()
    assert r["success"] is True and r["run"] == "20260920_000000"
    assert r["result"]["cam_tf"]["z"] == 1.29113


def test_stream_without_frames_is_503(client):
    assert client.get("/api/calib/stream/live").status_code == 503
    assert client.get("/api/calib/snapshot/calib").status_code == 503
    assert client.get("/api/calib/stream/camera").status_code == 503
    assert client.get("/api/calib/stream/other").status_code == 422


def test_snapshot_serves_latest_frame(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.calib.frames.put("live", b"jpeg-1")
        app.state.calib.frames.put("live", b"jpeg-2")
        r = c.get("/api/calib/snapshot/live")
        state = c.get("/api/calib/state").json()
    assert r.content == b"jpeg-2" and r.headers["content-type"] == "image/jpeg"
    assert state["mode"] == "mock" and state["streams"]["live"] is not None
