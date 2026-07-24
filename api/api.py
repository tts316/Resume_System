# -*- coding: utf-8 -*-
"""聯成電腦 求職履歷系統 — 新增求職者 API（獨立 Cloud Run 服務）。

供「聯成電腦管理系統」以 POST /api/v1/candidate 傳入求職者資料。
與 Streamlit 主站共用同一個 Cloud SQL PostgreSQL。合約見『新增求職者_API技術規格書.pdf』。

環境變數：
  PG_USER / PG_PASSWORD / PG_DB / PG_CONNECTION_NAME（Cloud Run 用 unix socket）或 PG_HOST（本機 proxy）
  EMAIL_SENDER / EMAIL_PASSWORD（Gmail SMTP，寄邀請信）
  AUTO_LOGIN_SECRET（**必須與 Streamlit 主站相同**，待辦連結才能免帳密登入）
  APP_URL（Streamlit 主站網址，待辦連結指向此處）
其餘（inbound Token、待辦 API URL/Token）由主站 admin 於「設定」寫入 system_settings，本服務即時讀取。
"""
import os, json, smtplib, hmac, hashlib, base64, urllib.request
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText

import psycopg2
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="求職履歷系統 - 新增求職者 API", version="1.0")


# ── DB ────────────────────────────────────────────────────────────────
def _pg_kwargs():
    user = os.environ.get("PG_USER", "resume_app")
    pw = os.environ.get("PG_PASSWORD", "")
    db = os.environ.get("PG_DB", "resume")
    conn = os.environ.get("PG_CONNECTION_NAME") or os.environ.get("INSTANCE_CONNECTION_NAME")
    host = os.environ.get("PG_HOST")
    port = int(os.environ.get("PG_PORT", "5432"))
    if host:
        return dict(host=host, port=port, dbname=db, user=user, password=pw)
    if conn:
        return dict(host=f"/cloudsql/{conn}", dbname=db, user=user, password=pw)
    return dict(host="127.0.0.1", port=port, dbname=db, user=user, password=pw)


def _db():
    c = psycopg2.connect(**_pg_kwargs())
    c.autocommit = True
    return c


def _get_setting(cur, key):
    cur.execute("SELECT value FROM system_settings WHERE key=%s", (key,))
    r = cur.fetchone()
    return r[0] if r else None


# ── 自動登入連結（須與主站 AUTO_LOGIN_SECRET 相同）──────────────────────
def _login_link(email):
    base = os.environ.get("APP_URL", "https://lcc-resume-sys-780693737981.asia-east1.run.app/")
    secret = os.environ.get("AUTO_LOGIN_SECRET", "").strip()
    if not secret:
        return base
    exp = int((datetime.now() + timedelta(days=14)).timestamp())
    msg = f"{email.strip().lower()}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    t = base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()
    return f"{base}{'&' if '?' in base else '?'}lt={t}"


def _send_email(to, subject, body):
    sender = os.environ.get("EMAIL_SENDER", "")
    pw = os.environ.get("EMAIL_PASSWORD", "")
    if not sender or not pw:
        return False
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587)
        s.starttls()
        s.login(sender, pw)
        m = MIMEText(body, "plain", "utf-8")
        m["Subject"] = subject
        m["From"] = sender
        m["To"] = to
        s.send_message(m)
        s.quit()
        return True
    except Exception:
        return False


def _todo_create(cur, emp_id, desc, link):
    url = _get_setting(cur, "todo_create_url")
    tok = _get_setting(cur, "todo_create_token")
    if not str(url or "").strip() or not str(tok or "").strip():
        return None
    try:
        req = urllib.request.Request(
            url.strip(),
            data=json.dumps({"UserId": int(emp_id), "Desc": desc[:60], "Type": 2, "Link": link}).encode("utf-8"),
            method="POST",
            headers={"Content-type": "application/json", "Authorization": f"Bearer {tok.strip()}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ── 請求模型 ──────────────────────────────────────────────────────────
class Candidate(BaseModel):
    EmpId: int
    CandNo: str                       # 代號（管理系統表單手動輸入）
    CandId: Optional[str] = ""        # 求職者編號（管理系統新增完成後自動產生的 id）
    Name: str
    Email: str
    ReqNo: str
    Mobile: Optional[str] = ""
    HomePhone: Optional[str] = ""
    Education: Optional[str] = ""
    School: Optional[str] = ""
    Major: Optional[str] = ""
    Source: Optional[str] = ""
    Interviewer: Optional[str] = ""
    InterviewTime: Optional[str] = ""
    OnlineInterview: Optional[bool] = False


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/v1/candidate")
def create_candidate(payload: Candidate, authorization: str = Header(default="")):
    conn = None
    try:
        conn = _db()
        cur = conn.cursor()
        token = _get_setting(cur, "inbound_api_token")
        if not str(token or "").strip():
            return JSONResponse(status_code=503, content={"Success": False, "Desc": "API 尚未設定 Token"})
        if authorization.strip() != f"Bearer {str(token).strip()}":
            return JSONResponse(status_code=401, content={"Success": False, "Desc": "Token 無效或未帶"})

        email = str(payload.Email).strip()
        if not email or "@" not in email:
            return {"Success": False, "Desc": "Email 格式不正確"}

        # 對應在職人資 PM
        cur.execute(
            "SELECT email FROM users WHERE emp_id=%s AND role IN ('admin','pm') "
            "AND coalesce(active,'Y')<>'N' ORDER BY _rn LIMIT 1", (str(payload.EmpId),))
        pr = cur.fetchone()
        if not pr:
            return {"Success": False, "Desc": f"EmpId {payload.EmpId} 對應不到在職人資 PM"}
        pm_email = pr[0]

        # 欄位合併（只寫有值的，避免以空白覆蓋既有資料）
        rmap = {
            "name_cn": payload.Name, "phone": payload.Mobile, "home_phone": payload.HomePhone,
            "edu_1_degree": payload.Education, "edu_1_school": payload.School, "edu_1_major": payload.Major,
            "source": payload.Source, "interview_manager": payload.Interviewer,
            "interview_time": payload.InterviewTime,
            "mgmt_cand_no": payload.CandId,   # 求職者編號(自動產生id) → 填入原手動輸入欄位
            "cand_code": payload.CandNo,      # 代號
            "req_no": payload.ReqNo,
            "online_interview": "是" if payload.OnlineInterview else "",
        }
        rmap = {k: str(v).strip() for k, v in rmap.items() if v is not None and str(v).strip() != ""}

        cur.execute("SELECT 1 FROM users WHERE lower(email)=lower(%s)", (email,))
        exists = cur.fetchone() is not None

        def _insert_resume():
            cols = ["email", "status", "resume_type"] + list(rmap.keys())
            vals = [email, "New", "HQ"] + list(rmap.values())
            ph = ",".join(["%s"] * len(cols))
            collist = ",".join('"' + c + '"' for c in cols)
            cur.execute(f"INSERT INTO resumes ({collist}) VALUES ({ph})", vals)

        if not exists:
            cur.execute(
                "INSERT INTO users (email,password,name,role,creator_email,created_at,active) "
                "VALUES (%s,%s,%s,'candidate',%s,%s,'Y')",
                (email, email, str(payload.Name).strip(), pm_email, str(date.today())))
            _insert_resume()
        else:
            cur.execute("SELECT 1 FROM resumes WHERE lower(email)=lower(%s)", (email,))
            if cur.fetchone() is None:
                _insert_resume()
            elif rmap:
                sets = ",".join(f'"{k}"=%s' for k in rmap)
                cur.execute(f"UPDATE resumes SET {sets} WHERE lower(email)=lower(%s)",
                            list(rmap.values()) + [email])
            # 更新邀請人歸屬為本次 PM
            cur.execute("UPDATE users SET creator_email=%s WHERE lower(email)=lower(%s)", (pm_email, email))

        # 寄邀請信給求職者 + 通知 PM
        base = os.environ.get("APP_URL", "https://lcc-resume-sys-780693737981.asia-east1.run.app/")
        _send_email(
            email, "【聯成電腦】歡迎您參加聯成電腦面試",
            f"親愛的 {payload.Name}，\n\n感謝您對聯成電腦的關注！\n請點以下連結登入填寫履歷：\n{base}\n"
            f"帳號：{email}\n密碼：{email}\n\n聯成電腦 人資部")
        _send_email(
            pm_email, f"【聯成電腦】新進求職者：{payload.Name}",
            f"您好，\n\n管理系統已轉入新求職者 {payload.Name}（{email}），系統已寄送履歷填寫邀請。\n"
            f"請登入『表單管理』追蹤。\n\n聯成電腦 招募系統")

        # 回傳待辦給管理系統（Type=2），連結帶自動登入 + ci=email（到站自動取消）
        link = _login_link(pm_email)
        link = f"{link}{'&' if '?' in link else '?'}ci={email}"
        r = _todo_create(cur, payload.EmpId, f"新求職者待追蹤：{payload.Name}", link)
        if r and r.get("Success") and r.get("TodoId"):
            cur.execute(
                "INSERT INTO todo_refs (cand_email,event,todo_id,pm_email) VALUES (%s,'invite',%s,%s) "
                "ON CONFLICT (cand_email,event) DO UPDATE SET todo_id=EXCLUDED.todo_id, "
                "pm_email=EXCLUDED.pm_email, created_at=now()",
                (email, int(r["TodoId"]), pm_email))

        return {"Success": True, "Desc": ""}
    except Exception as e:
        return {"Success": False, "Desc": str(e)}
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass
