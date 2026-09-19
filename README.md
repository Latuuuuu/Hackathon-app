# Hackathon-app：機器人任務控制台

讓使用者發布任務、看機器人執行狀態、中斷任務、校正相機外參，並在任務結束後回饋的 Web App（PWA，手機／平板／電腦都能開）。

```
瀏覽器 (React PWA)
   │ REST / MJPEG（同源，port 8000）
   ▼
App Gateway (FastAPI + rclpy)
   ├─ /api/tasks, /api/feedback ─▶ 雲端任務 server（LLM agent）  ※ 目前 mock
   ├─ /api/bt/*                 ─▶ bt_engine（mc_main_nav，HTTP，帶 X-BT-Token）
   └─ /api/calib/*              ─▶ field_calib_node（ROS2 Trigger + overlay 影像 + YAML）
```

金鑰與各服務位址只放在 gateway 的 `.env`，前端碰不到。

## 功能

| 頁面 | 內容 |
|---|---|
| 任務 `/` | 輸入 prompt 發布；即時顯示 BT 狀態、目前動作、執行紀錄；中斷任務；最近任務列表 |
| 校正 `/calibration` | ① 輸入桌子長、寬、張數（可忽略被擋住的桌緣）→ ② 即時畫面確認桌子入鏡，按開始校正後顯示逐輪桌緣偵測疊圖 → ③ 結果：通過/未通過、採用率、重投影誤差、相機外參、各桌緣品質、最終疊圖 |
| 回饋 `/feedback/:runId` | 任務結束後自動進入。失敗時列出原因（`notes`、`last_leaf_failure`、`error`），可修改 prompt 重來（失敗原因一併送雲端）；夾取力道（太小／剛好／太大）、滿意度、文字意見 |

## 部署（定位 server 那台）

Gateway 要和 `field_calib_node` 在同一個 ROS domain（`ROS_DOMAIN_ID=0`），並且和它共用定位 repo 的資料夾，所以要跑在定位 server 那台主機上。

```bash
cp .env.example .env        # 填 BT_ENGINE_TOKEN（向 kesler 要）
mkdir -p data
docker compose -f docker/compose.yaml up -d --build
# 開 http://<主機IP>:8000/
```

`field_calib_node` 啟動時**要加上** `field_file`，才會讀 App 寫的桌子設定：

```bash
ros2 launch field_calib field_calib.launch.py \
  field_file:=/home/vision/vision_ws/tools/calib/field_app.yaml
```

Gateway 第一次啟動時，如果這個檔案還不存在，會從 `src/field_calib/config/field.yaml` 複製一份。

定位 repo 不在 `../Hackathon-vision-server-lacalization` 時，在 `.env` 設 `VISION_WS=<路徑>`。

## 本機開發（不需要機器人或 ROS）

```bash
# 1. 假的 bt_engine（同一套 HTTP 介面，約 30% 會隨機失敗）
FAKE_PORT=8091 python3 tools/fake_bt_engine.py

# 2. Gateway（沒有 rclpy 時自動切到校正 mock 模式）
cd gateway
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
BT_ENGINE_URL=http://localhost:8091 MOCK_EXECUTE=true \
CALIB_OUTPUT_DIR=../../Hackathon-vision-server-lacalization/tools/calib/out/ros \
  .venv/bin/uvicorn app.main:app --port 8000 --reload

# 3. 前端（Vite dev server，/api 會轉到 :8000）
cd web && npm install && npm run dev
```

校正 mock 模式會重播 `CALIB_OUTPUT_DIR` 中最新一次的結果，沒有即時影像。

## 設定（`.env`）

| 變數 | 說明 |
|---|---|
| `BT_ENGINE_URL` / `BT_ENGINE_TOKEN` | bt_engine 位址與 token。區網 `http://192.168.50.125:8090`，公開 `https://mcpc.taile84e23.ts.net` |
| `CLOUD_MODE` | `mock`：prompt／回饋寫進 `data/tasks.jsonl`、`data/feedback.jsonl`。`http`：轉送到 `CLOUD_URL` |
| `CLOUD_URL` / `CLOUD_TOKEN` | 雲端任務 server（`Authorization: Bearer`） |
| `MOCK_EXECUTE` | mock 模式下是否把 demo 行為樹送到 bt_engine。**會讓真的機器人動**，預設關閉 |
| `CALIB_MODE` | `auto`（有 rclpy 就用 ros）／`ros`／`mock` |
| `FIELD_FILE` / `CALIB_OUTPUT_DIR` | 桌子設定檔、校正輸出資料夾（docker compose 已設好） |

## Gateway API

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/tasks` | `{prompt, retry_of?: {run_id, notes[]}}` → 雲端 |
| POST | `/api/feedback` | `{run_id, outcome, grip_force: too_weak\|ok\|too_strong, rating 1-5, comment}` → 雲端 |
| GET | `/api/bt/status[/{run_id}]` | bt_engine `/status` 原樣轉送（`?trace=full`） |
| GET | `/api/bt/runs`、`/api/bt/health` | 同上 |
| POST | `/api/bt/cancel` | 中斷目前的行為樹 |
| GET/PUT | `/api/calib/field` | 桌子設定 `{length, depth, count, disabled_segments}`（公尺） |
| POST | `/api/calib/run` | 觸發校正（阻塞到結束），回傳 `{success, message, run, result}` |
| GET | `/api/calib/result` | 目前生效的 `cam_tf.yaml` |
| GET | `/api/calib/state` | 模式、是否連到 field_calib_node、影像最後更新時間 |
| GET | `/api/calib/stream/{live\|calib}` | MJPEG 串流 |
| GET | `/api/calib/snapshot/{live\|calib}` | 單張 JPEG |
| GET | `/api/calib/image/{final_overlay\|final_strips\|final_residuals}?run=` | 校正輸出的 PNG |

## 測試

```bash
cd gateway && .venv/bin/pytest -q     # BT 代理、雲端 mock/http、桌子設定、校正結果
cd web && npm run build               # 型別檢查 + 打包
```

## 待辦與限制

- **雲端任務 server 的 API 還不知道。** 目前用 mock，`HttpCloudClient` 暫定 `POST /tasks`、`POST /feedback`。拿到真正的介面後只需要改 [gateway/app/cloud.py](gateway/app/cloud.py)。
- **雲端不回傳 BT `run_id`。** 發布任務後，App 把「送出後第一個新出現的 run」當成這次的任務。如果同時有別人在送行為樹，可能會抓錯。雲端若能回傳 `run_id`，App 會直接使用。
- **沒有真正的桌面遮罩。** 校正畫面用 `field_calib_node` 的 overlay 影像（投影桌緣 + 偵測邊點）代替。要真的 mask，需要在定位 repo 新增 topic。
- **`/cancel` 會停掉 bt_engine 上任何正在跑的樹**，包括別的 client 送的。
