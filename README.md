# Hackathon-app：機器人任務控制台

讓使用者發布任務、看機器人執行狀態、中斷任務、校正相機外參，並在任務結束後回饋的 Web App（PWA，手機／平板／電腦都能開）。

```
瀏覽器 (React PWA)
   │ REST / MJPEG（同源，port 8000）
   ▼
App Gateway (FastAPI + rclpy)
   ├─ /api/chat, /api/missions/* ─▶ 雲端任務 server Manta（APP_API.md）
   ├─ /api/bt/*                 ─▶ bt_engine（mc_main_nav，HTTP，帶 X-BT-Token）
   └─ /api/calib/*              ─▶ field_calib_node（ROS2 Trigger + overlay 影像 + YAML）
```

金鑰與各服務位址只放在 gateway 的 `.env`，前端碰不到。

## 功能

| 頁面 | 內容 |
|---|---|
| 任務 `/` | 和 Manta 對話規劃任務（資訊不足時會追問）→ 規劃好後顯示步驟與夾爪值，按「確認執行」才會讓機器人動；即時顯示 BT 狀態、目前動作、執行紀錄；中斷任務；最近任務列表 |
| 校正 `/calibration` | ① 輸入桌子長、寬、張數（可忽略被擋住的桌緣）→ ② 即時畫面確認桌子入鏡，按開始校正後顯示逐輪桌緣偵測疊圖 → ③ 結果：通過/未通過、採用率、重投影誤差、相機外參、各桌緣品質、最終疊圖 |
| 回饋 `/feedback/:runId` | 任務結束後自動進入。失敗時列出原因（`notes`、`last_leaf_failure`、`error`），可修改指令在原對話重新規劃（附上失敗原因）；滿意度（必填）、夾取力道（太小／剛好／太大 → 以這次行為樹的夾爪值 +10／不變／−10 送給 Manta）、文字意見 |

## 部署（定位 server 那台）

Gateway 要和 `field_calib_node` 在同一個 ROS domain（`ROS_DOMAIN_ID=0`），並且和它共用定位 repo 的資料夾，所以要跑在定位 server 那台主機上。

```bash
cp .env.example .env        # 填 BT_ENGINE_TOKEN（向 kesler 要）
mkdir -p data
docker compose up -d --build        # 在 repo 根目錄執行
# 開 http://<主機IP>:8000/
```

`field_calib_node` 啟動時**要加上** `field_file`，才會讀 App 寫的桌子設定：

```bash
ros2 launch field_calib field_calib.launch.py \
  field_file:=/home/vision/vision_ws/tools/calib/field_app.yaml
```

Gateway 第一次啟動時，如果這個檔案還不存在，會從 `src/field_calib/config/field.yaml` 複製一份。

定位 repo 不在 `../Hackathon-vision-server-localization` 時，在 `.env` 設 `VISION_WS=<路徑>`（相對於 repo 根目錄，或用絕對路徑）。**改了程式碼一定要加 `--build`**，否則會沿用舊 image。
這個路徑或 `data/` 不存在時，`docker compose up` 會直接報錯 `bind source path does not exist`。這是刻意的：只有跟 field_calib 在同一台主機上，校正才會生效。

Gateway 使用自己的 CycloneDDS 設定 [docker/cyclonedds.xml](docker/cyclonedds.xml)：field_calib 的疊圖一張約 4.3 MB，預設 2 MB 的接收緩衝區會讓每張都掉片段而整張作廢，所以把緩衝區請求調大（實際上限是主機的 `net.core.rmem_max`，目前 4 MB → 生效 8 MB）。`GET /api/calib/state` 的 `frame_counts` 可以看 Gateway 實際收到幾張。

如果讀寫不到校正檔案，Gateway 仍然會啟動，任務和回饋功能照常可用。校正頁會顯示錯誤，`GET /api/calib/state` 的 `error` 欄位會寫出原因。

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
| `CLOUD_MODE` | `http`：接 Manta。`mock`：離線替身，對話與回饋寫進 `data/*.jsonl` |
| `CLOUD_URL` / `CLOUD_TOKEN` | Manta 位址（目前 `http://210.61.209.139:45343`）；token 目前不需要 |
| `CLOUD_PIPELINE_MODE` / `CLOUD_ALLOW_VISION` / `CLOUD_CHAT_TIMEOUT_S` | 送給 `/api/chat` 的 `pipeline_mode`（預設 hybrid）、`options.allow_vision`、規劃逾時秒數（預設 240） |
| `MOCK_EXECUTE` | mock 模式下按「確認執行」是否把 demo 行為樹送到 bt_engine。**會讓真的機器人動**，預設關閉 |
| `CALIB_MODE` | `auto`（有 rclpy 就用 ros）／`ros`／`mock` |
| `FIELD_FILE` / `CALIB_OUTPUT_DIR` | 桌子設定檔、校正輸出資料夾（docker compose 已設好） |
| `CALIB_CAMERA_TOPIC` / `CAMERA_STREAM_FPS` / `CAMERA_STREAM_MAX_WIDTH` | 校正頁「相機畫面」的來源（預設 `/camera/camera/color/image_raw/compressed`）、輸出幀率（預設 15）、縮圖寬度（預設 960 px） |

## Gateway API

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/chat` | `{message, session_id?}` → Manta `/api/chat`，回傳精簡後的 `{session_id, status, message, questions, mission_id, executable, goal, steps, gripper_position, bt_xml}` |
| POST | `/api/sessions/reset` | 開新對話 |
| POST | `/api/missions/{id}/execute` | `{prompt}` → Manta execute，回傳 `{run_id}` 並記下 run → mission 對應 |
| POST | `/api/missions/{id}/cancel` | Manta cancel；Manta 連不上時改直接叫 bt_engine `/cancel` |
| POST | `/api/missions/{id}/feedback` | `{rating 1-5（必填）, comment, grip_force: too_weak\|ok\|too_strong}` → Manta，夾爪值由 Gateway 換算 |
| GET | `/api/runs/{run_id}/mission` | 這個 run 對應的 mission（只有從 App 執行的才有） |
| GET | `/api/cloud/health` | Manta 是否正常 |
| GET | `/api/bt/status[/{run_id}]` | bt_engine `/status` 原樣轉送（`?trace=full`） |
| GET | `/api/bt/runs`、`/api/bt/health` | 同上 |
| POST | `/api/bt/cancel` | 中斷目前的行為樹 |
| GET/PUT | `/api/calib/field` | 桌子設定 `{length, depth, count, disabled_segments}`（公尺） |
| POST | `/api/calib/run` | 觸發校正（阻塞到結束），回傳 `{success, message, run, result}` |
| GET | `/api/calib/result` | 目前生效的 `cam_tf.yaml` |
| GET | `/api/calib/state` | 模式、是否連到 field_calib_node、影像最後更新時間 |
| GET | `/api/calib/stream/{live\|calib\|camera}` | MJPEG 串流（camera 為相機原始畫面，約 15 fps；live／calib 為 field_calib 疊圖，約 1 fps） |
| GET | `/api/calib/snapshot/{live\|calib}` | 單張 JPEG |
| GET | `/api/calib/image/{final_overlay\|final_strips\|final_residuals}?run=` | 校正輸出的 PNG |

## 測試

```bash
cd gateway && .venv/bin/pytest -q     # BT 代理、雲端 mock/http、桌子設定、校正結果
cd web && npm run build               # 型別檢查 + 打包
```

## 待辦與限制

- **Manta execute 的回傳格式還沒實測**（會讓機器人動）。Gateway 會在 execute 回應和 `engine/status` 裡找 bt_engine 的 `run_id`；找不到時退回「執行後第一個新出現的 run」，同時有別人送樹可能抓錯。第一次實際執行的回應會記在 `data/manta_execute.jsonl`。
- **對話紀錄存在各瀏覽器**（localStorage），換裝置會看不到先前對話；Manta 端的 session 仍在。
- **沒有真正的桌面遮罩。** 校正畫面用 `field_calib_node` 的 overlay 影像（投影桌緣 + 偵測邊點）代替。要真的 mask，需要在定位 repo 新增 topic。
- **`/cancel` 會停掉 bt_engine 上任何正在跑的樹**，包括別的 client 送的。
