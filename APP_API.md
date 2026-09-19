# App API

Base URL on Manta: `http://<manta-host>:8000` (or the reverse-proxy URL used by the UI).

FastAPI also publishes interactive OpenAPI documentation at `/docs` and the machine-readable schema at `/openapi.json`.

## 1. Send a task / continue a conversation

`POST /api/chat`

```json
{
  "message": "找到杯子，靠近它，然後用 60% 夾爪閉合",
  "session_id": null,
  "pipeline_mode": "hybrid",
  "world_state": {},
  "options": {"allow_vision": false}
}
```

- Omit `session_id` or send `null` on the first turn. Save the returned `session_id` and reuse it for later turns.
- Use `pipeline_mode: "hybrid"` for normal operation. `direct` and `compare` are development modes.
- The agent reply is `candidate.message`; generated XML is `candidate.bt_xml`; the saved task ID is `candidate.mission_id`.
- A clarification has `candidate.status: "NEED_MORE_INFO"` and questions in `candidate.questions`. Send the answer to the same endpoint with the same session ID.

Minimal response shape:

```json
{
  "session_id": "7bf...",
  "pipeline_mode": "hybrid",
  "candidate": {
    "status": "SUCCESS",
    "message": "...",
    "mission_id": "5d1...",
    "bt_xml": "<root ...>...</root>"
  }
}
```

## 2. Reset / start a new session

`POST /api/sessions/reset`

```json
{"previous_session_id": "7bf..."}
```

Response:

```json
{
  "session_id": "new-uuid",
  "previous_session_id": "7bf...",
  "history_carried_over": false
}
```

The old history is preserved for audit; the returned ID starts a clean conversation.

## 3. Submit post-execution feedback to Experience RAG

Call this only after the mission's BT Engine state is `success`, `failure`, `error`, or `canceled`.

`POST /api/missions/{mission_id}/feedback`

```json
{
  "rating": 4,
  "comment": "杯子有夾住，但可以再輕一點；靠近杯子時也應該慢一點。",
  "parameters": {
    "set_gripper_position": 55,
    "navigate_to_detected_object_speed": "slow",
    "navigate_to_point_speed": "normal"
  }
}
```

Validation:

- `rating`: required integer, 1–5.
- `comment`: optional free text, at most 4000 characters.
- `set_gripper_position`: optional integer, 0–100 (`0` fully open, `100` fully closed).
- Both speed fields are optional and accept only `slow`, `normal`, or `fast`.

The API saves/upserts a JSON file at `agent-runtime/state/experience-feedback/{mission_id}.json` and indexes a readable summary in Experience RAG. Later similar tasks can retrieve it as advisory parameter experience. Re-submitting for the same mission updates that mission's feedback instead of creating duplicates.

Read it back with `GET /api/missions/{mission_id}/feedback`.

## Execution endpoints commonly used by an App

```text
POST /api/missions/{mission_id}/engine/validate
POST /api/missions/{mission_id}/engine/execute
GET  /api/missions/{mission_id}/engine/status?refresh=true
POST /api/missions/{mission_id}/engine/cancel
GET  /api/missions/{mission_id}
GET  /api/missions/{mission_id}/bt.xml
```

For live status, connect a WebSocket to `/ws/{session_id}`. Messages include `execution_event`, `bt_engine_terminal`, and `bt_engine_monitor_error`.

Typical HTTP errors are `404` unknown mission, `409` feedback before terminal execution, and `422` invalid rating/parameter values.
