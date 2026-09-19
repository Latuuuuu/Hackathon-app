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


# ---------------------------------------------------------------- cloud mock

def test_mock_task_logs_and_does_not_execute(client, settings):
    with respx.mock(assert_all_called=False) as mock:
        exe = mock.post(f"{BT}/execute")
        r = client.post("/api/tasks", json={"prompt": "把杯子拿過來"})
        assert not exe.called
    assert r.status_code == 200 and r.json()["executed"] is False
    rec = json.loads((settings.data_dir / "tasks.jsonl").read_text().splitlines()[-1])
    assert rec["prompt"] == "把杯子拿過來"


def test_mock_task_executes_when_enabled(tmp_path, settings):
    settings.mock_execute = True
    with respx.mock:
        respx.post(f"{BT}/execute").mock(
            return_value=httpx.Response(202, json={"ok": True, "run_id": "run-7", "preempted_previous": False})
        )
        with TestClient(create_app(settings)) as c:
            r = c.post("/api/tasks", json={"prompt": "grab cup", "retry_of": {"run_id": "run-6", "notes": ["x"]}})
    assert r.json()["run_id"] == "run-7"


def test_mock_execute_rejected_tree_is_502(settings):
    settings.mock_execute = True
    with respx.mock:
        respx.post(f"{BT}/execute").mock(return_value=httpx.Response(422, json={"ok": False, "error": "bad"}))
        with TestClient(create_app(settings)) as c:
            r = c.post("/api/tasks", json={"prompt": "grab cup"})
    assert r.status_code == 502 and "422" in r.json()["error"]


def test_empty_prompt_rejected(client):
    assert client.post("/api/tasks", json={"prompt": ""}).status_code == 422


def test_feedback_logged(client, settings):
    fb = {"run_id": "run-1", "outcome": "success", "grip_force": "too_strong", "rating": 4, "comment": "太用力"}
    assert client.post("/api/feedback", json=fb).json()["ok"] is True
    rec = json.loads((settings.data_dir / "feedback.jsonl").read_text().splitlines()[-1])
    assert rec["grip_force"] == "too_strong" and rec["comment"] == "太用力"


def test_feedback_validation(client):
    assert client.post("/api/feedback", json={"grip_force": "huge"}).status_code == 422
    assert client.post("/api/feedback", json={"rating": 9}).status_code == 422


def test_http_cloud_client(settings):
    settings.cloud_mode = "http"
    settings.cloud_url = "http://cloud.test"
    settings.cloud_token = "ct"
    with respx.mock:
        route = respx.post("http://cloud.test/tasks").mock(return_value=httpx.Response(200, json={"task_id": "t1"}))
        with TestClient(create_app(settings)) as c:
            r = c.post("/api/tasks", json={"prompt": "hi"})
    assert r.json() == {"ok": True, "task_id": "t1"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer ct"


# ---------------------------------------------------------------- calibration

def test_field_read_from_template(client):
    assert client.get("/api/calib/field").json() == {
        "length": 1.8, "depth": 0.6, "count": 2, "disabled_segments": []
    }


def test_field_file_seeded_on_startup(client, settings):
    data = yaml.safe_load(settings.field_file.read_text())
    assert data["table"] == {"length": 1.8, "depth": 0.6, "count": 2}


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
