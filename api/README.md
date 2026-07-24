# 新增求職者 API（獨立服務 lcc-resume-api）

供聯成電腦管理系統打入求職者資料。與 Streamlit 主站共用同一個 Cloud SQL PG。
合約：專案根目錄 `新增求職者_API技術規格書.pdf`。

## 部署（Cloud Shell，hq.lccnet.com.tw 帳號）

先把本 `api/` 目錄上傳到 Cloud Shell（或 clone repo 後 `cd api`），再執行：

> ⚠️ **正式 DB 位置**：正式站 `lcc-resume-sys` 實際連的是 **KPI 個體 `lcc-kpi-sys:asia-east1:lcc-kpi-pg` 內的 `resume` 資料庫**（非獨立的 `resume-pg`；履歷 DB 已併入 KPI 個體）。API 服務必須連同一處，設定如下（皆已對齊主站）。

```bash
gcloud config set project procuresys-499802

# 跨專案引用 KPI 專案的 secret 需專案號
KPI_NUM=$(gcloud projects describe lcc-kpi-sys --format="value(projectNumber)")

# 授權 compute SA 讀取該 secret（在 lcc-kpi-sys 專案）
gcloud secrets add-iam-policy-binding kpi-pg-resume-app-password \
  --project=lcc-kpi-sys \
  --member="serviceAccount:780693737981-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud run deploy lcc-resume-api \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-cloudsql-instances lcc-kpi-sys:asia-east1:lcc-kpi-pg \
  --set-env-vars PG_CONNECTION_NAME=lcc-kpi-sys:asia-east1:lcc-kpi-pg,PG_DB=resume,PG_USER=resume_app,APP_URL=https://lcc-resume-sys-780693737981.asia-east1.run.app/ \
  --set-secrets PG_PASSWORD=projects/${KPI_NUM}/secrets/kpi-pg-resume-app-password:latest
```

部署後網址：`https://lcc-resume-api-780693737981.asia-east1.run.app`（端點 `POST /api/v1/candidate`）。

### 之後只改程式碼的重新部署（保留既有設定）
```bash
cd ~/Resume_System && git pull && cd api
gcloud run deploy lcc-resume-api --source . --region asia-east1
```
> ⚠️ 純改程式時**不要**再帶 `--set-env-vars`／`--set-secrets`——`--set-*` 會「整組覆蓋」而把 `EMAIL_SENDER`/`EMAIL_PASSWORD`/`AUTO_LOGIN_SECRET` 洗掉。不帶任何 env/secret 旗標時，`gcloud run deploy` 會**保留**現有 env、secret、cloudsql 掛載，只更新程式映像。若真要改某個 env，用 `--update-env-vars`（只更新指定的、不動其他）。

### email 與自動登入密鑰（首次部署後補、與主站相同）
```bash
ES=$(gcloud run services describe lcc-resume-sys --region asia-east1 --format=json | jq -r '.spec.template.spec.containers[0].env[]?|select(.name=="EMAIL_SENDER")|.value')
EP=$(gcloud run services describe lcc-resume-sys --region asia-east1 --format=json | jq -r '.spec.template.spec.containers[0].env[]?|select(.name=="EMAIL_PASSWORD")|.value')
AL=$(gcloud run services describe lcc-resume-sys --region asia-east1 --format=json | jq -r '.spec.template.spec.containers[0].env[]?|select(.name=="AUTO_LOGIN_SECRET")|.value')
gcloud run services update lcc-resume-api --region asia-east1 \
  --update-env-vars EMAIL_SENDER="$ES",EMAIL_PASSWORD="$EP",AUTO_LOGIN_SECRET="$AL"
```

接著補上 email 與自動登入密鑰（與主站相同值）：

```bash
# EMAIL_SENDER / EMAIL_PASSWORD：與主站 lcc-resume-sys 相同（寄邀請信）
# AUTO_LOGIN_SECRET：必須與主站「完全相同」，待辦連結才能免帳密登入
gcloud run services update lcc-resume-api --region asia-east1 \
  --update-env-vars EMAIL_SENDER=hr.lccnet.com.tw@gmail.com \
  --update-env-vars AUTO_LOGIN_SECRET='<與主站相同的那組>' \
  --update-env-vars EMAIL_PASSWORD='<主站的 Gmail 應用程式密碼>'
```
> 若主站的 EMAIL_PASSWORD/AUTO_LOGIN_SECRET 是用 Secret Manager 掛的，這裡也改用 `--set-secrets` 掛同一個 secret。

部署完成後 `gcloud run services describe lcc-resume-api --region asia-east1 --format='value(status.url)'` 取得網址，
填回 API 技術規格書、交給管理系統。管理系統呼叫時帶 `Authorization: Bearer <inbound token>`。

## inbound Token 從哪來
主站 admin 進「⚙️ 設定 → 🔑 新增求職者 API」產生/填入，存於 `system_settings.inbound_api_token`。
本服務即時讀取該值驗證，**改 Token 不需重新部署本服務**。

## 測試
```bash
URL=$(gcloud run services describe lcc-resume-api --region asia-east1 --format='value(status.url)')
curl -sS -X POST "$URL/api/v1/candidate" \
  -H "Content-type: application/json" \
  -H "Authorization: Bearer <inbound token>" \
  -d '{"EmpId":12345,"CandNo":"T001","Name":"測試","Email":"test@example.com","ReqNo":"REQ-1"}'
# 預期 {"Success":true,"Desc":""}
```

## 安全
- 服務允許未驗證存取（`--allow-unauthenticated`），但**由 inbound Bearer Token 把關**；Token 請用強亂數（設定頁可一鍵產生）。
- Token/密碼一律不入 git、不明文外流。
