import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import base64
import smtplib
import io
import os
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import gspread
from google.oauth2.service_account import Credentials
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as PDFImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# --- 1. 系統設定 ---
st.set_page_config(page_title="聯成電腦 - 人才招募系統", layout="wide", page_icon="📝")
st.markdown("<style>div[data-testid='stStatusWidget']{display:none}</style>", unsafe_allow_html=True)
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap');
html,body,[class*="css"],.stMarkdown,.stTextInput input,.stSelectbox select{
  font-family:'Noto Sans TC',sans-serif!important}
.stButton>button[kind="primary"]{
  background:#1F3864!important;border-color:#1F3864!important;color:#fff!important}
.stButton>button[kind="primary"]:hover{background:#162a4a!important}
@media(max-width:768px){
  [data-testid="column"]{min-width:100%!important;flex:0 0 100%!important}
  .stTextInput input,.stSelectbox>div[data-baseweb]{padding:10px 12px!important;font-size:16px!important}
  .stRadio>div{gap:12px!important}
}
</style>""", unsafe_allow_html=True)

# Email 設定
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = ""      
SENDER_PASSWORD = ""   

# Logo URL
LOGO_URL = "https://www.lccnet.com.tw/lccnet/img/nav-logo.png"

# 分公司區域資料
BRANCH_DATA = {
    "北一區": ["館前", "公館", "忠孝", "士林", "基隆", "羅東"],
    "北二區": ["板橋", "新莊", "三重", "永和"],
    "桃竹區": ["桃園", "中壢", "新竹"],
    "中區": ["豐原", "逢甲", "三民", "站前", "彰化"],
    "南一區": ["斗六", "嘉義", "台南", "永康"],
    "南二區": ["高雄", "鳳山", "楠梓", "屏東"]
}

# --- 機密設定讀取：雲端(Cloud Run)優先讀環境變數，本機/Streamlit Cloud fallback 讀 secrets.toml ---
def _secret(env_key, *secret_path, default=None):
    val = os.environ.get(env_key)
    if val not in (None, ""):
        return val
    try:
        node = st.secrets
        for k in secret_path:
            node = node[k]
        return node
    except Exception:
        return default

def _gcp_creds_dict():
    raw = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw)
    return dict(st.secrets["gcp_service_account"])

# --- PG 雙後端轉接層（DB_BACKEND=postgres 才啟用；以 PG 資料表模擬 gspread worksheet 介面）---
# 依搬遷 Playbook §3：不重寫商業邏輯，用轉接層讓現有讀寫函式原封不動。
# 樞紐是每表的 _rn(BIGSERIAL)：Sheets 有天然列序、PG 沒有 → 全部 ORDER BY _rn。
# 機密一律純讀 os.environ（Playbook 踩雷#5：勿用會 fallback st.secrets 的 helper）。
try:
    import psycopg2 as _psycopg2
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

def _pg_conn_kwargs():
    user = os.environ.get("PG_USER", "resume_app")
    password = os.environ.get("PG_PASSWORD", "")
    dbname = os.environ.get("PG_DB", "resume")
    conn_name = os.environ.get("PG_CONNECTION_NAME") or os.environ.get("INSTANCE_CONNECTION_NAME")
    host = os.environ.get("PG_HOST")
    port = int(os.environ.get("PG_PORT", "5432"))
    if host:            # 本機：Cloud SQL Auth Proxy(TCP)
        return dict(host=host, port=port, dbname=dbname, user=user, password=password)
    if conn_name:       # Cloud Run：unix socket /cloudsql/<connection_name>
        return dict(host=f"/cloudsql/{conn_name}", dbname=dbname, user=user, password=password)
    return dict(host="127.0.0.1", port=port, dbname=dbname, user=user, password=password)

class _Cell:
    def __init__(self, row, col, value=""):
        self.row = row; self.col = col; self.value = value

class PGBackend:
    """psycopg2 連線；autocommit（每句即時寫入，語意同 Sheets 逐格寫）。斷線自動重連一次。"""
    def __init__(self):
        if not _PSYCOPG2_OK:
            raise RuntimeError("DB_BACKEND=postgres 但未安裝 psycopg2-binary")
        self._connect()
        for t in ("users", "resumes", "system_settings"):   # _rn 冪等自癒(schema 已建，通常 no-op)
            try: self.exec(f'ALTER TABLE "{t}" ADD COLUMN IF NOT EXISTS _rn BIGSERIAL')
            except Exception: pass

    def _connect(self):
        self.conn = _psycopg2.connect(**_pg_conn_kwargs())
        self.conn.autocommit = True

    def exec(self, sql, params=(), fetch=None):
        for attempt in (1, 2):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    if fetch == "all": return cur.fetchall()
                    if fetch == "one": return cur.fetchone()
                    return None
            except _psycopg2.OperationalError:
                if attempt == 2: raise
                self._connect()   # 斷線重連再試一次

class PGWorksheet:
    """模擬單一 worksheet：列1=表頭(不含 _rn)、資料列依 _rn 排序對映列2起。"""
    def __init__(self, backend, table):
        self.b = backend; self.t = table
        rows = self.b.exec(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name <> '_rn' "
            "ORDER BY ordinal_position", (table,), fetch="all")
        self.cols = [r[0] for r in rows]

    def _q(self, name): return '"' + str(name).replace('"', '""') + '"'

    def _rn_for_row(self, row):   # row 1-based，1=表頭；資料列 row>=2 → ORDER BY _rn 第(row-2)筆
        r = self.b.exec(f'SELECT _rn FROM {self._q(self.t)} ORDER BY _rn OFFSET %s LIMIT 1',
                        (row - 2,), fetch="one")
        return r[0] if r else None

    def get_all_values(self):
        collist = ", ".join(self._q(c) for c in self.cols)
        rows = self.b.exec(f'SELECT {collist} FROM {self._q(self.t)} ORDER BY _rn', fetch="all")
        out = [list(self.cols)]
        for row in rows:
            out.append(["" if v is None else str(v) for v in row])
        return out

    def row_values(self, rownum):
        if rownum == 1: return list(self.cols)
        collist = ", ".join(self._q(c) for c in self.cols)
        r = self.b.exec(f'SELECT {collist} FROM {self._q(self.t)} ORDER BY _rn OFFSET %s LIMIT 1',
                        (rownum - 2,), fetch="one")
        return ["" if v is None else str(v) for v in r] if r else []

    def find(self, query, in_column=1):
        col = self.cols[in_column - 1]
        r = self.b.exec(
            f'SELECT _rn FROM {self._q(self.t)} WHERE {self._q(col)} = %s ORDER BY _rn LIMIT 1',
            (str(query),), fetch="one")
        if not r: return None
        p = self.b.exec(f'SELECT count(*) FROM {self._q(self.t)} WHERE _rn <= %s',
                        (r[0],), fetch="one")[0]
        return _Cell(row=p + 1, col=in_column, value=str(query))

    def cell(self, row, col):
        rn = self._rn_for_row(row); val = ""
        if rn is not None:
            r = self.b.exec(f'SELECT {self._q(self.cols[col-1])} FROM {self._q(self.t)} WHERE _rn=%s',
                            (rn,), fetch="one")
            if r and r[0] is not None: val = str(r[0])
        return _Cell(row=row, col=col, value=val)

    def update_cell(self, row, col, value):
        rn = self._rn_for_row(row)
        if rn is None: return
        self.b.exec(f'UPDATE {self._q(self.t)} SET {self._q(self.cols[col-1])}=%s WHERE _rn=%s',
                    ("" if value is None else str(value), rn))

    def append_row(self, values, **kwargs):
        vals = list(values)[:len(self.cols)]
        vals += [""] * (len(self.cols) - len(vals))
        vals = ["" if v is None else str(v) for v in vals]
        collist = ", ".join(self._q(c) for c in self.cols)
        ph = ", ".join(["%s"] * len(self.cols))
        self.b.exec(f'INSERT INTO {self._q(self.t)} ({collist}) VALUES ({ph})', tuple(vals))

    def delete_rows(self, start, end=None):
        end = start if end is None else end
        rns = [rn for row in range(start, end + 1)
               if (rn := self._rn_for_row(row)) is not None]
        if rns:
            ph = ", ".join(["%s"] * len(rns))
            self.b.exec(f'DELETE FROM {self._q(self.t)} WHERE _rn IN ({ph})', tuple(rns))

class PGSpreadsheet:
    """模擬整份試算表：worksheet(title) 回 PGWorksheet。"""
    def __init__(self):
        self.backend = PGBackend()
    def worksheet(self, title):
        return PGWorksheet(self.backend, title)

# --- 2. 資料庫核心 ---
class ResumeDB:
    def __init__(self):
        self.connect()

    def connect(self):
        try:
            if os.environ.get("DB_BACKEND", "").strip().lower() == "postgres":
                self.client = None
                self.sh = PGSpreadsheet()          # 走 PG，介面同 gspread
            else:
                scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
                creds_dict = _gcp_creds_dict()
                creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
                self.client = gspread.authorize(creds)
                sheet_url = _secret("SPREADSHEET_URL", "sheet_config", "spreadsheet_url")
                self.sh = self.client.open_by_url(sheet_url)
            self.ws_users = self.sh.worksheet("users")
            self.ws_resumes = self.sh.worksheet("resumes")
            self.ws_settings = self.sh.worksheet("system_settings")
        except Exception as e:
            st.error(f"資料庫連線失敗: {e}")
            st.stop()

    def get_df(self, table_name):
        defaults = {
            "users": ["email", "password", "name", "role", "creator_email", "created_at"],
            "resumes": [
                "email", "status", "name_cn", "name_en", "phone", "address", "dob", 
                "edu_1_school", "edu_1_major", "edu_1_degree", "edu_1_state", "edu_1_start", "edu_1_end",
                "edu_2_school", "edu_2_major", "edu_2_degree", "edu_2_state", "edu_2_start", "edu_2_end",
                "edu_3_school", "edu_3_major", "edu_3_degree", "edu_3_state", "edu_3_start", "edu_3_end",
                "exp_1_start", "exp_1_end", "exp_1_co", "exp_1_title", "exp_1_salary", "exp_1_boss", "exp_1_phone", "exp_1_reason",
                "exp_2_start", "exp_2_end", "exp_2_co", "exp_2_title", "exp_2_salary", "exp_2_boss", "exp_2_phone", "exp_2_reason",
                "exp_3_start", "exp_3_end", "exp_3_co", "exp_3_title", "exp_3_salary", "exp_3_boss", "exp_3_phone", "exp_3_reason",
                "exp_4_start", "exp_4_end", "exp_4_co", "exp_4_title", "exp_4_salary", "exp_4_boss", "exp_4_phone", "exp_4_reason",
                "skills", "self_intro", "hr_comment", "interview_date", "resume_type", "branch_region", "branch_location", "shift_avail", 
                "source", "relative_name", "teach_exp", "computer_course", "travel_history", "hospitalization", "chronic_disease", 
                "military_status", "family_support", "family_debt", "commute_method", "commute_time", "height", "weight", "blood_type", 
                "marital_status", "emergency_contact", "emergency_phone", "home_phone",
                "holiday_shift", "rotate_shift", "family_support_shift", "care_dependent", "financial_burden", "accept_rotation",
                "interview_time", "interview_location", "interview_dept", "interview_manager", "interview_notes"
            ],
            "system_settings": ["key", "value"]
        }
        
        ws = self.ws_users if table_name == "users" else (self.ws_resumes if table_name == "resumes" else self.ws_settings)
        
        try:
            data = ws.get_all_values()
            if len(data) < 2: return pd.DataFrame(columns=defaults[table_name])
            headers = data.pop(0)
            df = pd.DataFrame(data, columns=headers)
            df.columns = df.columns.astype(str).str.strip().str.lower()
            if defaults[table_name][0] not in df.columns: return pd.DataFrame(columns=defaults[table_name])
            return df
        except: return pd.DataFrame(columns=defaults.get(table_name, []))

    def verify_login(self, email, password):
        try:
            df = self.get_df("users")
            if df.empty: return None
            email_clean = str(email).strip().lower()
            user = df[df['email'].astype(str).str.strip().str.lower() == email_clean]
            if not user.empty:
                row = user.iloc[0]
                # 防呆：帳號(上方已 strip)與密碼兩邊都去空格，避免多打空白造成登入失敗
                if str(row['password']).strip() == str(password).strip():
                    return {"email": row['email'], "name": row['name'], "role": row['role'], "creator": row.get('creator_email', '')}
            return None
        except: return None

    def create_user(self, creator_email, email, name, role, r_type=""):
        try:
            email = str(email).strip()
            name = str(name).strip()
            creator_email = str(creator_email).strip()
            df = self.get_df("users")
            if not df.empty and email in df['email'].astype(str).values: return False, "Email 已存在"
            self.ws_users.append_row([email, email, name, role, creator_email, str(date.today())])
            if role == "candidate":
                # 依真實表頭「按欄名」放值，避免硬編碼位置放錯欄
                # (舊 bug：r_type 被放到 index 51=exp_4_co，而非 resume_type@61)
                headers = [str(h).strip().lower() for h in self.ws_resumes.row_values(1)]
                row_data = [""] * len(headers)
                for col, val in [("email", email), ("status", "New"),
                                 ("name_cn", name), ("resume_type", r_type)]:
                    if col in headers:
                        row_data[headers.index(col)] = val
                self.ws_resumes.append_row(row_data)
            _invalidate_cache()
            return True, "建立成功"
        except Exception as e: return False, str(e)

    def change_password(self, email, new_password):
        try:
            cell = self.ws_users.find(email, in_column=1)
            if cell: self.ws_users.update_cell(cell.row, 2, new_password); return True, "OK"
            return False, "Fail"
        except Exception as e: return False, str(e)

    # [關鍵修復]：自動移除 Key 後面的 `_in`，以匹配資料庫欄位
    def save_resume(self, email, data, status="Draft"):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                r = cell.row
                headers = self.ws_resumes.row_values(1)
                headers = [h.strip().lower() for h in headers]
                
                self.ws_resumes.update_cell(r, headers.index('status')+1, status)
                
                for key, val in data.items():
                    clean_key = key.lower()
                    if clean_key.endswith("_in"):
                        clean_key = clean_key[:-3] # 去掉 _in
                    
                    if clean_key == 'status':
                        continue
                    if clean_key in headers:
                        col_idx = headers.index(clean_key) + 1
                        if isinstance(val, (date, datetime)):
                            val = str(val)
                        self.ws_resumes.update_cell(r, col_idx, val)
                _invalidate_cache()
                return True, "儲存成功"
            return False, "No Data"
        except Exception as e: return False, str(e)

    # [縮排修復]
    def hr_update_status(self, email, status, details=None):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                r = cell.row
                headers = self.ws_resumes.row_values(1)
                headers = [h.strip().lower() for h in headers]
                
                if 'status' in headers:
                    self.ws_resumes.update_cell(r, headers.index('status')+1, status)
                
                if details:
                    for k, v in details.items():
                        if k in headers:
                            col = headers.index(k) + 1
                            val = str(v) if v else ""
                            self.ws_resumes.update_cell(r, col, val)
                _invalidate_cache()
                return True, "OK"
            return False, "Fail"
        except Exception as e: return False, str(e)

    def delete_user_account(self, email):
        """刪除求職者帳號：resumes + users 兩表對應列。防護：已審查核可(Approved)不可刪。回傳 (bool, msg)。"""
        try:
            email = str(email).strip()
            cell_r = self.ws_resumes.find(email, in_column=1)
            if cell_r:
                headers = [h.strip().lower() for h in self.ws_resumes.row_values(1)]
                if 'status' in headers:
                    st_val = self.ws_resumes.cell(cell_r.row, headers.index('status') + 1).value
                    if str(st_val).strip() == 'Approved':
                        return False, "已審查核可，不可刪除"
                self.ws_resumes.delete_rows(cell_r.row)
            cell_u = self.ws_users.find(email, in_column=1)
            if cell_u:
                self.ws_users.delete_rows(cell_u.row)
            _invalidate_cache()
            return True, "OK"
        except Exception as e:
            return False, str(e)

    def get_logo(self):
        try:
            cell = self.ws_settings.find("logo", in_column=1)
            if cell: return self.ws_settings.cell(cell.row, 2).value
        except: pass
        return None

    def update_logo(self, base64_str):
        try:
            try: cell = self.ws_settings.find("logo", in_column=1)
            except: time.sleep(1); cell = self.ws_settings.find("logo", in_column=1)
            if cell: self.ws_settings.update_cell(cell.row, 2, base64_str)
            else: self.ws_settings.append_row(["logo", base64_str])
            return True
        except: return False

@st.cache_resource
def get_db(): return ResumeDB()

try: sys = get_db()
except: st.error("連線失敗，請檢查 secrets.toml"); st.stop()

# --- AI (claude-api) ---
try:
    import anthropic as _anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

def _ai_analyze_resume(row):
    api_key = _secret("ANTHROPIC_API_KEY", "anthropic", "api_key")
    if not api_key or not _ANTHROPIC_OK:
        return None, "未設定 ANTHROPIC_API_KEY 或未安裝 anthropic 套件"
    edu_parts = [f"{row.get(f'edu_{i}_school','')} {row.get(f'edu_{i}_major','')} ({row.get(f'edu_{i}_degree','')})"
                 for i in range(1,4) if row.get(f'edu_{i}_school','')]
    exp_parts = [f"{row.get(f'exp_{i}_start','')}~{row.get(f'exp_{i}_end','')} {row.get(f'exp_{i}_co','')} {row.get(f'exp_{i}_title','')} 薪{row.get(f'exp_{i}_salary','')}"
                 for i in range(1,5) if row.get(f'exp_{i}_co','')]
    prompt = f"""你是聯成電腦（台灣電腦培訓補習班龍頭）的資深人資顧問，請分析以下求職者履歷並提供評估。

求職者：{row.get('name_cn','')}
履歷類型：{'分公司門市' if row.get('resume_type')=='Branch' else '總公司內勤'}
學歷：{'; '.join(edu_parts) or '未填'}
工作經歷：{'; '.join(exp_parts) or '無'}
專業技能：{str(row.get('skills',''))[:200] or '未填'}
自傳摘要：{str(row.get('self_intro',''))[:300] or '未填'}

請提供：
1. **適合度評分** (1-10分)
2. **主要優勢** (2-3點)
3. **潛在風險** (1-2點)
4. **建議面試問題** (3題)

以繁體中文回答，格式清晰簡潔。"""
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(model="claude-haiku-4-5", max_tokens=800,
                                       messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text, None
    except Exception as e:
        return None, str(e)

# --- Email ---
def send_email(to_email, subject, body, html_body=None):
    try:
        sender_email = _secret("EMAIL_SENDER", "email", "sender_email")
        sender_password = _secret("EMAIL_PASSWORD", "email", "sender_password")
        if not sender_email or not sender_password:
            return False, "未設定 EMAIL_SENDER / EMAIL_PASSWORD 環境變數"
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(sender_email, sender_password)
        if html_body:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject; msg['From'] = sender_email; msg['To'] = to_email
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        else:
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subject; msg['From'] = sender_email; msg['To'] = to_email
        server.send_message(msg); server.quit()
        return True, None
    except Exception as e:
        return False, str(e)

# --- PDF Generation ---
def generate_pdf(data):
    buffer = io.BytesIO()

    # ── 字型（先決定，供頁首頁尾 callback 使用）─────────────────────
    font_name = 'Helvetica'
    try:
        pdfmetrics.registerFont(TTFont('TaipeiSans', 'TaipeiSansTCBeta-Regular.ttf'))
        font_name = 'TaipeiSans'
    except: pass

    # ── 頁首頁尾 callback（D2）────────────────────────────────────
    def _draw_page(c, doc):
        c.saveState()
        c.setFont(font_name, 7)
        c.setFillColor(colors.HexColor('#1F3864'))
        c.drawString(30, A4[1] - 16, "聯成電腦 人才招募系統")
        c.drawRightString(A4[0] - 30, A4[1] - 16, datetime.now().strftime('%Y/%m/%d'))
        c.setStrokeColor(colors.HexColor('#AAAAAA'))
        c.setLineWidth(0.3)
        c.line(30, A4[1] - 20, A4[0] - 30, A4[1] - 20)
        c.line(30, 20, A4[0] - 30, 20)
        c.setFont(font_name, 7)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawCentredString(A4[0] / 2, 8, f"第 {doc.page} 頁")
        c.restoreState()

    _title_text = "聯成電腦面試人員履歷表" if data.get('resume_type') != 'Branch' else "聯成電腦 (分公司) 面試人員履歷表"
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=42, bottomMargin=30)
    # ── PDF Metadata（D4）─────────────────────────────────────────
    doc.title = _title_text
    doc.author = "聯成電腦 人資部"
    doc.subject = "面試人員履歷表"
    doc.creator = "聯成電腦人才招募系統"
    elements = []

    # ── 色彩 ──────────────────────────────────────────────────────
    HDR_BG = colors.HexColor('#1F3864')   # 深藍 – 區塊標題背景
    LBL_BG = colors.HexColor('#BDD7EE')   # 淡藍 – 標籤欄背景

    # ── 樣式 ──────────────────────────────────────────────────────
    styleN = ParagraphStyle('Normal', fontName=font_name, fontSize=10, leading=14)
    styleH = ParagraphStyle('Heading1', fontName=font_name, fontSize=16, leading=20, alignment=TA_CENTER)
    styleS = ParagraphStyle('Small',   fontName=font_name, fontSize=9,  leading=13)

    # ── 輔助：深藍色區塊標題列 ────────────────────────────────────
    def sec_hdr(text, width=535):
        t = Table([[text]], colWidths=[width])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), HDR_BG),
            ('TEXTCOLOR',     (0,0), (-1,-1), colors.white),
            ('FONTNAME',      (0,0), (-1,-1), font_name),
            ('FONTSIZE',      (0,0), (-1,-1), 10),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('RIGHTPADDING',  (0,0), (-1,-1), 8),
        ]))
        return t

    # ── 共用標籤欄樣式（4欄表格：col 0, col 2 為標籤）────────────
    lbl_style = TableStyle([
        ('FONTNAME',      (0,0), (-1,-1), font_name),
        ('FONTSIZE',      (0,0), (-1,-1), 9),
        ('GRID',          (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND',    (0,0), (0,-1),  LBL_BG),
        ('BACKGROUND',    (2,0), (2,-1),  LBL_BG),
        ('ALIGN',         (0,0), (-1,-1), 'LEFT'),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
    ])

    # ── 輔助：把文字包成 Paragraph，長文字自動換行不超出格線 ────────
    styleC = ParagraphStyle('Cell', fontName=font_name, fontSize=8, leading=11)

    def wp(text):
        return Paragraph(str(text) if text else '', styleC)

    # ── 標題 ──────────────────────────────────────────────────────
    title = "聯成電腦面試人員履歷表" if data.get('resume_type') != 'Branch' else "聯成電腦 (分公司) 面試人員履歷表"
    elements.append(Paragraph(title, styleH))
    elements.append(Spacer(1, 8))

    # ── 1. 基本資料（全寬 535pt，無照片欄）──────────────────────
    elements.append(sec_hdr("▌ 基本資料"))
    elements.append(Spacer(1, 2))

    p_data = [
        ["姓　名",   wp(f"{data.get('name_cn','')}  {data.get('name_en','')}"),
         "應徵職務", wp("一般人員")],
        ["電子信箱", wp(data.get('email','')),
         "聯絡電話", wp(data.get('phone',''))],
        ["出生日期", wp(data.get('dob','')),
         "婚姻/血型", wp(f"{data.get('marital_status','')} / {data.get('blood_type','')}")],
        ["通訊地址", wp(data.get('address','')),
         "緊急聯絡", wp(f"{data.get('emergency_contact','')} {data.get('emergency_phone','')}")],
        ["身高/體重", wp(f"{data.get('height','')} cm / {data.get('weight','')} kg"),
         "交通方式", wp(f"{data.get('commute_method','')} 約{data.get('commute_time','')}分")],
    ]
    info_tbl = Table(p_data, colWidths=[75, 192, 75, 193])
    info_tbl.setStyle(lbl_style)
    elements.append(info_tbl)
    elements.append(Spacer(1, 8))

    # ── 2. 學歷 ───────────────────────────────────────────────────
    elements.append(sec_hdr("▌ 學歷"))
    edu_data = [["起訖年月", "學校名稱", "科系", "學位", "狀態"]]
    for i in range(1, 4):
        s = data.get(f'edu_{i}_school', '')
        if not s: continue
        s_date = f"{data.get(f'edu_{i}_start','')} ~ {data.get(f'edu_{i}_end','')}"
        edu_data.append([wp(s_date), wp(s), wp(data.get(f'edu_{i}_major','')),
                         wp(data.get(f'edu_{i}_degree','')), wp(data.get(f'edu_{i}_state',''))])
    t2 = Table(edu_data, colWidths=[100, 155, 130, 80, 70])
    t2.setStyle(TableStyle([
        ('FONTNAME',      (0,0), (-1,-1), font_name),
        ('FONTSIZE',      (0,0), (-1,-1), 9),
        ('GRID',          (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND',    (0,0), (-1, 0), LBL_BG),
        ('ALIGN',         (0,0), (-1,-1), 'LEFT'),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(Spacer(1, 2))
    elements.append(t2)
    elements.append(Spacer(1, 8))

    # ── 3. 工作經歷 ───────────────────────────────────────────────
    elements.append(sec_hdr("▌ 工作經歷"))
    styleXS = ParagraphStyle('XS', fontName=font_name, fontSize=7, leading=10)
    def wpx(text):
        return Paragraph(str(text) if text else '', styleXS)
    exp_data = [["起訖年月", "公司名稱", "職稱", "主管/電話", "薪資", "離職原因"]]
    for i in range(1, 5):
        co = data.get(f'exp_{i}_co', '')
        if not co: continue
        s_date = f"{data.get(f'exp_{i}_start','')} ~ {data.get(f'exp_{i}_end','')}"
        boss = f"{data.get(f'exp_{i}_boss','')} {data.get(f'exp_{i}_phone','')}"
        exp_data.append([wpx(s_date), wpx(co), wpx(data.get(f'exp_{i}_title','')),
                         wpx(boss), wpx(data.get(f'exp_{i}_salary','')),
                         wpx(data.get(f'exp_{i}_reason',''))])
    t3 = Table(exp_data, colWidths=[80, 100, 70, 110, 55, 120])
    t3.setStyle(TableStyle([
        ('FONTNAME',      (0,0), (-1,-1), font_name),
        ('FONTSIZE',      (0,0), (-1,-1), 8),
        ('GRID',          (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND',    (0,0), (-1, 0), LBL_BG),
        ('ALIGN',         (0,0), (-1,-1), 'LEFT'),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(Spacer(1, 2))
    elements.append(t3)
    elements.append(Spacer(1, 8))

    # ── 4. 其他資料 ───────────────────────────────────────────────
    elements.append(sec_hdr("▌ 其他資料"))
    other_data = [
        ["應徵管道", wp(data.get('source','')),         "任職親友", wp(data.get('relative_name',''))],
        ["補教經驗", wp(data.get('teach_exp','')),       "出國史",   wp(data.get('travel_history',''))],
        ["兵　　役", wp(data.get('military_status','')), "慢性病",   wp(data.get('chronic_disease',''))],
        ["獨力扶養", wp(data.get('family_support','')),  "獨力負擔", wp(data.get('family_debt',''))],
    ]
    t4 = Table(other_data, colWidths=[65, 202, 65, 203])
    t4.setStyle(lbl_style)
    elements.append(Spacer(1, 2))
    elements.append(t4)
    elements.append(Spacer(1, 8))

    # ── 5. 分公司排班（Branch 限定）──────────────────────────────
    if data.get('resume_type') == 'Branch':
        elements.append(sec_hdr("▌ 分公司排班意願調查"))
        br_data = [
            ["希望區域",             data.get('branch_region','')],
            ["希望分校",             data.get('branch_location','')],
            ["配合輪調",             data.get('accept_rotation','')],
            ["配合輪班",             data.get('shift_avail','')],
            ["國定假日輪值",         data.get('holiday_shift','')],
            ["早晚輪班(9-18/14-22)", data.get('rotate_shift','')],
            ["家人同意輪班",         data.get('family_support_shift','')],
            ["經濟/扶養需求",        f"扶養: {data.get('care_dependent','')} / 負擔: {data.get('financial_burden','')}"],
        ]
        t5 = Table(br_data, colWidths=[150, 385])
        t5.setStyle(TableStyle([
            ('FONTNAME',      (0,0), (-1,-1), font_name),
            ('FONTSIZE',      (0,0), (-1,-1), 9),
            ('GRID',          (0,0), (-1,-1), 0.5, colors.grey),
            ('BACKGROUND',    (0,0), (0,-1),  LBL_BG),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ]))
        elements.append(Spacer(1, 2))
        elements.append(t5)
        elements.append(Spacer(1, 8))

    # ── 6. 專業技能與自傳 ─────────────────────────────────────────
    elements.append(sec_hdr("▌ 專業技能與自傳"))
    bio_data = [
        ["專業技能", Paragraph(str(data.get('skills','')),    styleS)],
        ["自　　傳", Paragraph(str(data.get('self_intro','')), styleS)],
    ]
    t6 = Table(bio_data, colWidths=[65, 470])
    t6.setStyle(TableStyle([
        ('FONTNAME',      (0,0), (-1,-1), font_name),
        ('FONTSIZE',      (0,0), (-1,-1), 9),
        ('GRID',          (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND',    (0,0), (0,-1),  LBL_BG),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
    ]))
    elements.append(Spacer(1, 2))
    elements.append(t6)
    elements.append(Spacer(1, 16))

    # ── 7. 簽名行 ─────────────────────────────────────────────────
    elements.append(Paragraph("─" * 60, styleN))
    elements.append(Spacer(1, 6))
    sign_text = "本人所填資料均屬事實，若有不實，願接受免職處分。　　應徵人員親簽：＿＿＿＿＿＿＿＿＿　日期：　　　年　　月　　日"
    elements.append(Paragraph(sign_text, styleN))

    try:
        qr = PDFImage("qrcode.png", width=60, height=60)
        elements.append(Spacer(1, 10))
        elements.append(qr)
    except: pass

    doc.build(elements, onFirstPage=_draw_page, onLaterPages=_draw_page)
    buffer.seek(0)
    return buffer

# --- 效能：DB 讀取快取 + PDF 快取（寫入時自動失效）---
@st.cache_data(ttl=30, show_spinner=False)
def load_df(table_name):
    """快取版讀取(ttl 30s)；任何寫入後由 _invalidate_cache() 清除，確保即時。
    取代散落各頁的 sys.get_df()，避免每次 Streamlit rerun(每個 widget 互動)重複整表讀取。"""
    return sys.get_df(table_name)

def _invalidate_cache():
    try: load_df.clear()
    except Exception: pass

@st.cache_data(ttl=600, show_spinner=False)
def _cached_pdf_bytes(row_items):
    """依整列內容快取 PDF bytes；資料一變動 key 就變、自動重建，
    避免審核列表每次 rerun 都對每筆履歷重跑 generate_pdf(高 CPU)。"""
    return generate_pdf(dict(row_items)).getvalue()

# --- UI Components ---
def _logo_src():
    """取得 logo 來源：優先讀 DB(system_settings.logo)，正確處理已含 data: 前綴的值，失敗才 fallback 到 LOGO_URL。"""
    try:
        raw = sys.get_logo()
        lg = str(raw).strip() if raw else ""
        if lg.startswith("http") or lg.startswith("data:"):
            return lg
        if len(lg) > 10:
            return f"data:image/png;base64,{lg}"
    except Exception:
        pass
    return LOGO_URL

def render_sidebar(user):
    with st.sidebar:
        st.image(_logo_src(), use_container_width=True)
        st.divider()
        role_map = {"admin": "人資主管", "pm": "人資 PM", "candidate": "面試者"}
        st.write(f"👋 **{user['name']}**"); st.caption(f"身分: {role_map.get(user['role'], 'User')}")
        if st.button("🚪 登出", use_container_width=True): st.session_state.user=None; st.rerun()
        st.divider()
        with st.expander("🔑 修改密碼"):
            p1 = st.text_input("新密碼", type="password"); p2 = st.text_input("確認", type="password")
            if st.button("修改"):
                if p1==p2 and p1: 
                    if sys.change_password(user['email'], p1): st.success("成功")
                else: st.error("錯誤")

# --- Pages ---
def login_page():
    # 左側欄：LOGO（左右分割版面）
    with st.sidebar:
        st.image(_logo_src(), use_container_width=True)
        st.divider()
    # 右側主區塊：登入表單
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("### 📝 人才招募系統登入")
        # 用 st.form：欄位按 Enter 也能送出登入(不再「按 Enter 沒反應也沒訊息」)
        with st.form("login_form"):
            email = st.text_input("📧 Email 帳號", placeholder="your@email.com")
            pwd = st.text_input("🔒 密碼", type="password", placeholder="預設密碼為您的 Email")
            submitted = st.form_submit_button("登入", type="primary", use_container_width=True)
        if submitted:
            _e, _p = str(email).strip(), str(pwd).strip()   # 輸入層再去一次空格(防呆)
            if not _e or not _p:
                st.error("請輸入帳號與密碼")
                st.warning("⚠️ 若你是用瀏覽器「自動填入」：系統有時沒接收到自動帶入的值（收到空白）。"
                           "請在欄位點一下、隨意補打一個字再刪掉，讓系統讀到值後再按登入。")
            else:
                user = sys.verify_login(_e, _p)
                if user:
                    st.session_state.user = user; st.rerun()
                else:
                    st.error(f"帳號或密碼錯誤（收到帳號 {len(_e)} 碼、密碼 {len(_p)} 碼）。"
                             "若長度正常仍失敗，可能是瀏覽器存了舊密碼。")
        st.caption("如有問題請聯繫人資部 ◆ © 聯成電腦")

def admin_page():
    user = st.session_state.user
    render_sidebar(user)
    st.header(f"👨‍💼 管理後台")

    tabs = ["📧 發送邀請", "📋 履歷審核", "📊 表單管理"]
    if user['role'] == 'admin': tabs.append("⚙️ 設定")
    current_tab = st.tabs(tabs)
    
    with current_tab[0]:
        st.subheader("邀請與帳號管理")
        c1, c2 = st.columns(2)
        with c1.form("invite"):
            st.write("#### 邀請面試者")
            c_name = st.text_input("姓名"); c_email = st.text_input("Email")
            r_type = st.radio("履歷類型", ["總公司 (HQ)", "分公司 (Branch)"], horizontal=True)
            if st.form_submit_button("發送面試邀請"):
                if c_name and c_email:
                    type_code = "Branch" if "分公司" in r_type else "HQ"
                    succ, msg = sys.create_user(user['email'], c_email, c_name, "candidate", type_code)
                    if succ:
                        link = _secret("APP_URL", "email", "app_url", default="https://lcc-resume-sys-780693737981.asia-east1.run.app/")
                        plain = f"親愛的 {c_name}，\n\n感謝您對聯成電腦的關注！\n請點以下連結登入填寫履歷：\n{link}\n帳號：{c_email}\n密碼：{c_email}\n\n聯成電腦 人資部"
                        html = f"""<html><body><div style="font-family:Arial,sans-serif;max-width:580px;margin:auto;padding:24px">
<img src="{LOGO_URL}" style="height:56px;margin-bottom:20px"/>
<h2 style="color:#1F3864;margin-bottom:8px">面試邀請通知</h2>
<p>親愛的 <strong>{c_name}</strong>，</p>
<p>感謝您對聯成電腦的關注！誠摯邀請您填寫履歷表，讓我們進一步認識您。</p>
<p style="margin:24px 0"><a href="{link}" style="background:#1F3864;color:white;padding:12px 28px;text-decoration:none;border-radius:6px;font-size:15px">立即填寫履歷</a></p>
<p style="color:#555;font-size:13px">登入帳號：{c_email}<br>預設密碼：{c_email}</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0"/>
<p style="color:#999;font-size:12px">此信由聯成電腦人才招募系統自動發送，請勿回覆。</p>
</div></body></html>"""
                        _ok, _err = send_email(c_email, "【聯成電腦】面試邀請", plain, html_body=html)
                        if _ok:
                            st.success(f"✅ 帳號已建立，邀請信已發送給 {c_name}")
                        else:
                            st.warning(f"⚠️ 帳號已建立，但 Email 發送失敗：{_err}")
                    else: st.error(msg)
        
        if user['role'] == 'admin':
            with c2.form("create_pm"):
                st.write("#### 建立人資 PM")
                p_name = st.text_input("PM 姓名"); p_email = st.text_input("PM Email")
                if st.form_submit_button("建立 PM"):
                    if p_name and p_email:
                        succ, msg = sys.create_user(user['email'], p_email, p_name, "pm")
                        if succ: st.success(f"PM {p_name} 建立成功")
                        else: st.error(msg)

    with current_tab[1]:
        st.subheader("履歷審核列表")
        df = load_df("resumes")
        df_users = load_df("users")
        
        if not df.empty and not df_users.empty:
            merged_df = df.merge(df_users[['email', 'creator_email']], on='email', how='left')
            
            if user['role'] == 'admin':
                filtered_df = merged_df
            else:
                filtered_df = merged_df[merged_df['creator_email'] == user['email']]
            
            submitted = filtered_df[filtered_df['status'].isin(['Submitted', 'Approved', 'Returned'])].copy()
            
            if not submitted.empty:
                for i, row in submitted.iterrows():
                    r_badge = "🏢" if row['resume_type'] == "HQ" else "🏪"
                    status_badge = "✅" if row['status'] == "Approved" else "⏳" if row['status'] == "Submitted" else "↩️"
                    
                    with st.expander(f"{status_badge} {r_badge} {row['name_cn']} ({row['email']})"):
                        
                        pdf_data = _cached_pdf_bytes(tuple(sorted(row.to_dict().items())))
                        btn_c1, btn_c2 = st.columns(2)
                        btn_c1.download_button("📥 下載完整 PDF", pdf_data, f"{row['name_cn']}_履歷.pdf", "application/pdf", key=f"dl_pdf_{row['email']}")
                        if btn_c2.button("🤖 AI 履歷分析", key=f"ai_{row['email']}"):
                            with st.spinner("Claude AI 分析中..."):
                                _analysis, _err = _ai_analyze_resume(row.to_dict())
                            if _analysis:
                                st.info(_analysis)
                            else:
                                st.warning(f"AI 分析未啟用：{_err}")
                        st.divider()

                        st.markdown("#### 📄 履歷內容 (唯讀)")
                        
                        # [關鍵修正] 完整顯示所有欄位
                        st.markdown("**【基本資料】**")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.write(f"**姓名**: {row['name_cn']} ({row.get('name_en','')})")
                        c2.write(f"**電話**: {row['phone']} / {row.get('home_phone')}")
                        c3.write(f"**Email**: {row['email']}")
                        c4.write(f"**生日**: {row['dob']}")
                        
                        c5, c6, c7, c8 = st.columns(4)
                        c5.write(f"**地址**: {row['address']}")
                        c6.write(f"**市話**: {row.get('home_phone')}")
                        c7.write(f"**婚姻**: {row.get('marital_status')}")
                        c8.write(f"**血型**: {row.get('blood_type')}")

                        c9, c10 = st.columns(2)
                        c9.write(f"**緊急聯絡**: {row.get('emergency_contact')} ({row.get('emergency_phone')})")
                        c10.write(f"**通勤**: {row.get('commute_method')} ({row.get('commute_time')}分)")

                        st.markdown("**【學歷】**")
                        for x in range(1, 4):
                            s = row.get(f'edu_{x}_school')
                            if s: 
                                date_range = f"{row.get(f'edu_{x}_start','')} ~ {row.get(f'edu_{x}_end','')}"
                                st.write(f"**{x}. {s}** ({date_range}) | {row.get(f'edu_{x}_major')} | {row.get(f'edu_{x}_degree')} | {row.get(f'edu_{x}_state')}")
                        
                        st.markdown("**【工作經歷】**")
                        for x in range(1, 5):
                            co = row.get(f'exp_{x}_co')
                            if co:
                                date_range = f"{row.get(f'exp_{x}_start','')} ~ {row.get(f'exp_{x}_end','')}"
                                st.markdown(f"**{x}. {co}** ({date_range})")
                                st.write(f"- 職稱: {row.get(f'exp_{x}_title')} | 薪資: {row.get(f'exp_{x}_salary')}")
                                st.write(f"- 主管: {row.get(f'exp_{x}_boss')} ({row.get(f'exp_{x}_phone')}) | 離職: {row.get(f'exp_{x}_reason')}")
                        
                        if row.get('resume_type') == 'Branch':
                            st.markdown("**【分公司意願】**")
                            st.write(f"區域: {row.get('branch_region')} | 地點: {row.get('branch_location')}")
                            st.write(f"輪調: {row.get('accept_rotation')} | 輪班: {row.get('shift_avail')}")
                            st.write(f"排班: 假日({row.get('holiday_shift')}) | 早晚({row.get('rotate_shift')}) | 家人({row.get('family_support_shift')})")
                            st.write(f"經濟: 扶養({row.get('care_dependent')}) | 負擔({row.get('financial_burden')})")

                        st.markdown("**【其他】**")
                        st.write(f"應徵管道: {row.get('source')} | 親友: {row.get('relative_name')}")
                        st.write(f"補教: {row.get('teach_exp')} | 出國: {row.get('travel_history')} | 兵役: {row.get('military_status')}")
                        st.write(f"病史: 住院({row.get('hospitalization')}) | 慢性病({row.get('chronic_disease')})")
                        st.write(f"經濟: 扶養({row.get('family_support')}) | 負擔({row.get('family_debt')})")

                        st.markdown("**【自傳】**")
                        st.write(f"**技能**: {row.get('skills')}")
                        st.text_area("自傳", value=str(row.get('self_intro','')), disabled=True, height=150, key=f"intro_ta_{row['email']}")

                        st.divider()
                        st.write("#### 👨‍⚖️ 審核決定")
                        
                        with st.form(f"hr_review_{row['email']}"):
                            st.caption("若核准，請填寫面試資訊 (將寄送給面試者)")
                            c1, c2 = st.columns(2)
                            int_date = c1.date_input("日期", value=date.today())
                            int_time = c2.text_input("時間", placeholder="例如：14:30")
                            
                            c3, c4 = st.columns(2)
                            int_loc = c3.text_input("地點", placeholder="總公司 502 會議室")
                            int_dept = c4.text_input("單位", placeholder="行銷部")
                            
                            c5, c6 = st.columns(2)
                            int_mgr = c5.text_input("主管", placeholder="王經理")
                            int_note = c6.text_input("注意事項", placeholder="請攜帶作品集")
                            
                            hr_comment = st.text_input("評語 / 退件原因", value=row['hr_comment'])
                            
                            c_ok, c_no = st.columns(2)
                            
                            if c_ok.form_submit_button("✅ 核准 (發送通知)"):
                                if not int_loc or not int_time:
                                    st.error("核准請填寫時間與地點")
                                else:
                                    details = {
                                        'hr_comment': hr_comment,
                                        'interview_date': str(int_date),
                                        'interview_time': int_time,
                                        'interview_location': int_loc,
                                        'interview_dept': int_dept,
                                        'interview_manager': int_mgr,
                                        'interview_notes': int_note
                                    }
                                    sys.hr_update_status(row['email'], "Approved", details)
                                    
                                    body = f"""
{row['name_cn']} 您好，

恭喜您通過履歷初審！我們誠摯邀請您前來參加面試。

📅 日期：{int_date}
⏰ 時間：{int_time}
📍 地點：{int_loc}
🏢 單位：{int_dept}
👤 主管：{int_mgr}

⚠️ 注意事項：{int_note}

請準時出席，若有變動請提前聯繫。
聯成電腦 人資部
                                    """
                                    _ok, _err = send_email(row['email'], "【聯成電腦】面試通知", body)
                                    if _ok:
                                        st.success("已核准並發送通知信！")
                                    else:
                                        st.warning(f"已核准，但通知信發送失敗：{_err}")
                                    time.sleep(2); st.rerun()

                            if c_no.form_submit_button("↩️ 退件 (通知修改)"):
                                if not hr_comment:
                                    st.error("請填寫退件原因")
                                else:
                                    details = {'hr_comment': hr_comment}
                                    sys.hr_update_status(row['email'], "Returned", details)
                                    _ok2, _err2 = send_email(row['email'], "【聯成電腦】履歷需修改", f"您的履歷被退回。\n原因：{hr_comment}\n請登入修改後重送。")
                                    if _ok2:
                                        st.warning("已退件通知")
                                    else:
                                        st.warning(f"已退件，但通知信發送失敗：{_err2}")
                                    time.sleep(2); st.rerun()

            else: st.info("無待審履歷")

    # ── 表單管理 ──────────────────────────────────────────────────
    with current_tab[2]:
        st.subheader("表單發送管理")

        STATUS_MAP = {
            "New":       ("已發送",    "🔵"),
            "Draft":     ("已發送",    "🔵"),
            "Submitted": ("已回覆履歷", "🟡"),
            "Approved":  ("已審查核可", "✅"),
            "Returned":  ("已退件",    "↩️"),
        }

        df_u2 = load_df("users")
        df_r2 = load_df("resumes")

        if df_u2.empty:
            st.info("尚無資料")
        else:
            cands = df_u2[df_u2['role'] == 'candidate'].copy()
            if user['role'] == 'pm':
                cands = cands[cands['creator_email'] == user['email']]

            if cands.empty:
                st.info("尚無邀請記錄")
            else:
                if not df_r2.empty:
                    r_cols = [c for c in ['email','status','name_cn','interview_dept','resume_type','hr_comment'] if c in df_r2.columns]
                    merged2 = cands.merge(df_r2[r_cols], on='email', how='left')
                else:
                    merged2 = cands.copy()
                    for col in ['status','name_cn','interview_dept','resume_type','hr_comment']:
                        merged2[col] = ''

                merged2['status'] = merged2['status'].fillna('New').replace('', 'New')
                merged2['name_cn'] = merged2.apply(lambda r: r['name_cn'] if str(r.get('name_cn','')).strip() else r['name'], axis=1)
                merged2['interview_dept'] = merged2.apply(
                    lambda r: r.get('interview_dept','') if str(r.get('interview_dept','')).strip()
                              else ("總公司" if str(r.get('resume_type','')) == 'HQ' else
                                    "分公司" if str(r.get('resume_type','')) == 'Branch' else '—'),
                    axis=1
                )
                merged2['created_at'] = pd.to_datetime(merged2['created_at'], errors='coerce')
                merged2['ym'] = merged2['created_at'].dt.strftime('%Y-%m').fillna('未知')
                merged2 = merged2.sort_values('created_at', ascending=False)

                # ── 起訖月份查詢（預設近 2 個月，避免過多月份塞滿畫面）──────────
                def _mlabel(m):
                    try:    return datetime.strptime(m, '%Y-%m').strftime('%Y 年 %m 月')
                    except: return m
                _months = sorted([m for m in merged2['ym'].unique() if m and m != '未知'], reverse=True)
                if _months:
                    _si = 1 if len(_months) > 1 else 0   # 預設起始=次新月 → 近 2 個月
                    fcs, fce = st.columns(2)
                    _mstart = fcs.selectbox("起始月份", _months, index=_si, format_func=_mlabel, key="fm_start")
                    _mend   = fce.selectbox("結束月份", _months, index=0,   format_func=_mlabel, key="fm_end")
                    _lo, _hi = sorted([_mstart, _mend])
                    merged2 = merged2[(merged2['ym'] >= _lo) & (merged2['ym'] <= _hi)]
                    st.caption(f"顯示 {_mlabel(_lo)} ～ {_mlabel(_hi)}，共 {len(merged2)} 筆")
                    if merged2.empty:
                        st.info("此區間無資料")

                app_url = _secret("APP_URL", "email", "app_url", default="https://lcc-resume-sys-780693737981.asia-east1.run.app/")

                for ym, grp in merged2.groupby('ym', sort=False):
                    try:    mlabel = datetime.strptime(ym, '%Y-%m').strftime('%Y 年 %m 月')
                    except: mlabel = ym
                    is_first = (ym == merged2['ym'].iloc[0])
                    with st.expander(f"📅 {mlabel}（共 {len(grp)} 筆）", expanded=is_first):
                        # 表頭
                        hc = st.columns([0.8, 2, 2, 2, 2, 2])
                        for h, t in zip(hc, ["選取", "發送日期", "求職者姓名", "面試單位", "狀態", "操作"]):
                            h.markdown(f"**{t}**")
                        st.divider()

                        for _row_idx, fr in grp.iterrows():
                            raw_st  = str(fr.get('status', 'New'))
                            lbl, badge = STATUS_MAP.get(raw_st, (raw_st, "❓"))
                            sent_date = fr['created_at'].strftime('%Y/%m/%d') if pd.notna(fr['created_at']) else '—'
                            cand_email = str(fr['email']).strip()
                            cand_name  = str(fr.get('name_cn', fr['name'])).strip()

                            rc = st.columns([0.8, 2, 2, 2, 2, 2])
                            # 勾選：僅「尚未審查核可」(status != Approved) 可刪
                            if raw_st != 'Approved':
                                rc[0].checkbox("選取", key=f"del_chk_{cand_email}", label_visibility="collapsed")
                            else:
                                rc[0].write("🔒")
                            rc[1].write(sent_date)
                            rc[2].write(cand_name)
                            rc[3].write(str(fr.get('interview_dept', '—')))
                            rc[4].write(f"{badge} {lbl}")

                            btn_key = f"resend_{cand_email}_{_row_idx}"
                            if raw_st in ('New', 'Draft'):
                                if rc[5].button("📧 催促填寫", key=btn_key):
                                    body = (f"{cand_name} 您好，\n\n"
                                            f"提醒您尚未完成履歷填寫，請盡快登入系統填寫並送出。\n"
                                            f"系統連結：{app_url}\n"
                                            f"帳號：{cand_email}\n密碼：{cand_email}\n\n"
                                            f"如有任何問題，歡迎聯繫人資部。\n聯成電腦 人資部")
                                    ok, _ = send_email(cand_email, "【聯成電腦】提醒您完成履歷填寫", body)
                                    if ok: st.toast(f"已發送催促通知給 {cand_name}", icon="✅")
                                    else:  st.toast("發送失敗，請確認 Email 設定", icon="⚠️")
                            elif raw_st == 'Returned':
                                reason = str(fr.get('hr_comment', '')).strip()
                                if rc[5].button("📧 催促修改", key=btn_key):
                                    body = (f"{cand_name} 您好，\n\n"
                                            f"您的履歷已被退回，請登入系統依照退件原因修改後重新送出。\n"
                                            f"退件原因：{reason or '請參閱系統說明'}\n\n"
                                            f"系統連結：{app_url}\n"
                                            f"帳號：{cand_email}\n\n"
                                            f"請盡快完成修改，謝謝。\n聯成電腦 人資部")
                                    ok, _ = send_email(cand_email, "【聯成電腦】請修改履歷後重新送出", body)
                                    if ok: st.toast(f"已發送催促通知給 {cand_name}", icon="✅")
                                    else:  st.toast("發送失敗，請確認 Email 設定", icon="⚠️")
                            else:
                                rc[5].write("—")

                # ── 刪除求職者帳號（勾選 → 確認 → 摘要）──────────────
                st.divider()
                st.caption("🗑️ 勾選上方求職者後可刪除其帳號與履歷資料（僅限**尚未審查核可**者；🔒 表示已核可、不可刪）")
                if st.button("🗑️ 刪除勾選的帳號"):
                    _deletable = merged2[merged2['status'] != 'Approved']
                    _sel = [(str(r['email']).strip(), str(r.get('name_cn') or r['name']).strip())
                            for _, r in _deletable.iterrows()
                            if st.session_state.get(f"del_chk_{str(r['email']).strip()}")]
                    if not _sel:
                        st.warning("尚未勾選任何求職者")
                    else:
                        st.session_state['pending_del'] = _sel
                        st.rerun()

                if st.session_state.get('pending_del'):
                    _sel = st.session_state['pending_del']
                    st.warning(f"⚠️ 確定刪除以下 {len(_sel)} 個求職者的帳號與履歷資料？**此動作無法復原。**")
                    for _em, _nm in _sel:
                        st.write(f"- {_nm}（{_em}）")
                    _c1, _c2, _ = st.columns([1, 1, 3])
                    if _c1.button("✅ 確認刪除", type="primary"):
                        _results = [(_nm, _em) + sys.delete_user_account(_em) for _em, _nm in _sel]
                        st.session_state['del_summary'] = _results
                        del st.session_state['pending_del']
                        st.rerun()
                    if _c2.button("取消"):
                        del st.session_state['pending_del']
                        st.rerun()

                if st.session_state.get('del_summary'):
                    _results = st.session_state['del_summary']
                    _ok = sum(1 for r in _results if r[2])
                    _fail = len(_results) - _ok
                    st.success(f"刪除完成：成功 {_ok} 筆" + (f"、失敗 {_fail} 筆" if _fail else ""))
                    for _nm, _em, _okf, _msg in _results:
                        st.write(f"✅ {_nm}（{_em}）已刪除" if _okf else f"❌ {_nm}（{_em}）失敗：{_msg}")
                    if st.button("關閉摘要"):
                        for _nm, _em, _okf, _msg in _results:
                            st.session_state.pop(f"del_chk_{_em}", None)
                        del st.session_state['del_summary']
                        st.rerun()

    if user['role'] == 'admin':
        with current_tab[3]:
            up = st.file_uploader("Logo 更新", type=['png','jpg'])
            if up and st.button("更新"):
                b64 = base64.b64encode(up.getvalue()).decode()
                sys.update_logo(f"data:image/png;base64,{b64}")
                st.success("OK"); st.rerun()

def candidate_page():
    user = st.session_state.user
    render_sidebar(user)
    st.header(f"📝 履歷填寫")

    # B1: 步驟進度條
    _steps = ["① 基本資料", "② 學歷", "③ 工作經歷", "④ 其他資訊", "⑤ 確認送出"]
    st.markdown("".join(
        f'<span style="display:inline-block;padding:5px 14px;margin:0 2px 8px;'
        f'background:{"#1F3864" if i==0 else "#D9E8F5"};'
        f'color:{"white" if i==0 else "#1F3864"};'
        f'border-radius:20px;font-size:13px;font-weight:{"700" if i==0 else "400"}">{s}</span>'
        for i, s in enumerate(_steps)
    ) + "<br>", unsafe_allow_html=True)

    df = load_df("resumes")
    if df.empty: st.error("DB Error"); return
    my_df = df[df['email'].astype(str).str.strip().str.lower() == str(user['email']).strip().lower()]
    if my_df.empty: st.error("無履歷資料"); return
    
    my_resume = my_df.iloc[0]
    status = my_resume['status']
    r_type = my_resume.get('resume_type', 'HQ') 

    if status == "Approved": 
        st.balloons(); st.success("🎉 恭喜！您的履歷已審核通過。")
        with st.expander("查看面試資訊", expanded=True):
            st.write(f"📅 日期: {my_resume.get('interview_date')}")
            st.write(f"⏰ 時間: {my_resume.get('interview_time')}")
            st.write(f"📍 地點: {my_resume.get('interview_location')}")
            st.write(f"⚠️ 注意: {my_resume.get('interview_notes')}")
        return
    
    if status == "Submitted":
        st.info("ℹ️ 您已送出履歷，目前正在審核中。若需補充資料，可直接修改並再次送出。")
    elif status == "Returned":
        st.error(f"⚠️ 您的履歷被退回。原因：{my_resume['hr_comment']}")

    with st.form("resume_form"):
        st.markdown(f"### {'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'} 履歷表")
        
        # 基本資料
        with st.container(border=True):
            st.caption("基本資料　　:red[* 為必填欄位]")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名 *", value=my_resume['name_cn'], key='name_cn')
            n_en = c2.text_input("英文姓名", value=my_resume['name_en'], key='name_en')
            c3.text_input("身高(cm)", value=my_resume.get('height',''), key='height')
            c4.text_input("體重(kg)", value=my_resume.get('weight',''), key='weight')
            
            c5, c6, c7 = st.columns([2, 1, 1])
            phone = c5.text_input("手機 *", value=my_resume['phone'], key='phone')
            c6.text_input("市話 (H)", value=my_resume.get('home_phone',''), key='home_phone')
            
            m_val = my_resume.get('marital_status', '未婚')
            m_idx = ["未婚", "已婚"].index(m_val) if m_val in ["未婚", "已婚"] else 0
            c7.selectbox("婚姻", ["未婚", "已婚"], index=m_idx, key='marital_status')
            
            try: dval = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
            except: dval = date(1995,1,1)
            dob = c1.date_input("生日", value=dval, min_value=date(1900, 1, 1), key='dob')
            addr = st.text_input("通訊地址 *", value=my_resume['address'], key='address')
            
            c8, c9 = st.columns(2)
            c8.text_input("緊急聯絡人", value=my_resume.get('emergency_contact',''), key='emergency_contact')
            c9.text_input("緊急聯絡電話", value=my_resume.get('emergency_phone',''), key='emergency_phone')
            
            b_type_val = my_resume.get('blood_type', 'O')
            c3.selectbox("血型", ["O", "A", "B", "AB"], index=["O", "A", "B", "AB"].index(b_type_val) if b_type_val in ["O", "A", "B", "AB"] else 0, key="blood_type")

        # 學歷
        with st.container(border=True):
            st.caption("學歷 (請填寫最高及次高學歷)")
            for i in range(1, 4):
                st.markdown(f"**學歷 {i}**")
                c_d1, c_d2 = st.columns(2)
                st.session_state[f'edu_{i}_start'] = c_d1.text_input(f"入學年月 (YYYY/MM) #{i}", value=my_resume.get(f'edu_{i}_start',''), key=f'edu_{i}_start_in')
                st.session_state[f'edu_{i}_end'] = c_d2.text_input(f"畢/肄業年月 (YYYY/MM) #{i}", value=my_resume.get(f'edu_{i}_end',''), key=f'edu_{i}_end_in')

                rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
                _sch_lbl = f"學校 {i} *" if i == 1 else f"學校 {i}"
                _maj_lbl = f"科系 {i} *" if i == 1 else f"科系 {i}"
                st.session_state[f'edu_{i}_school'] = rc1.text_input(_sch_lbl, value=my_resume.get(f'edu_{i}_school',''), key=f'edu_{i}_school_in')
                st.session_state[f'edu_{i}_major'] = rc2.text_input(_maj_lbl, value=my_resume.get(f'edu_{i}_major',''), key=f'edu_{i}_major_in')
                
                d_val = my_resume.get(f'edu_{i}_degree', '學士')
                d_opts = ["學士", "碩士", "博士", "高中/職", "其他"]
                d_idx = d_opts.index(d_val) if d_val in d_opts else 0
                st.session_state[f'edu_{i}_degree'] = rc3.selectbox(f"學位 {i}", d_opts, index=d_idx, key=f'edu_{i}_degree_in')
                
                s_val = my_resume.get(f'edu_{i}_state', '畢業')
                s_idx = 0 if s_val != "肄業" else 1
                st.session_state[f'edu_{i}_state'] = rc4.radio(f"狀態 {i}", ["畢業", "肄業"], index=s_idx, horizontal=True, key=f'edu_{i}_state_in', label_visibility="collapsed")
                
                if i < 3: st.divider()

        # 經歷
        with st.container(border=True):
            st.caption("曾任職公司 (最近4筆)")
            for i in range(1, 5):
                with st.expander(f"經歷 {i}"):
                    c_ym1, c_ym2 = st.columns(2)
                    st.session_state[f'exp_{i}_start'] = c_ym1.text_input(f"起始年月 (YYYY/MM)", value=my_resume.get(f'exp_{i}_start',''), key=f'exp_{i}_start_in')
                    st.session_state[f'exp_{i}_end'] = c_ym2.text_input(f"結束年月 (YYYY/MM)", value=my_resume.get(f'exp_{i}_end',''), key=f'exp_{i}_end_in')

                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    st.session_state[f'exp_{i}_co'] = ec1.text_input(f"公司名稱", value=my_resume.get(f'exp_{i}_co',''), key=f'exp_{i}_co_in')
                    st.session_state[f'exp_{i}_title'] = ec2.text_input(f"職稱", value=my_resume.get(f'exp_{i}_title',''), key=f'exp_{i}_title_in')
                    try: y_val = float(my_resume.get(f'exp_{i}_years',0) or 0)
                    except: y_val = 0.0
                    st.session_state[f'exp_{i}_years'] = ec3.number_input(f"年資", value=y_val, key=f'exp_{i}_years_in')
                    
                    ec4, ec5, ec6 = st.columns([1, 1, 1])
                    st.session_state[f'exp_{i}_boss'] = ec4.text_input(f"主管姓名/職稱", value=my_resume.get(f'exp_{i}_boss',''), key=f'exp_{i}_boss_in')
                    st.session_state[f'exp_{i}_phone'] = ec5.text_input(f"聯絡電話", value=my_resume.get(f'exp_{i}_phone',''), key=f'exp_{i}_phone_in')
                    st.session_state[f'exp_{i}_salary'] = ec6.text_input(f"薪資", value=my_resume.get(f'exp_{i}_salary',''), key=f'exp_{i}_salary_in')
                    st.session_state[f'exp_{i}_reason'] = st.text_input(f"離職原因", value=my_resume.get(f'exp_{i}_reason',''), key=f'exp_{i}_reason_in')

        loc_val = ""
        shift_val = ""
        rot_val = ""
        region = ""
        holiday_shift = ""
        rotate_shift = ""
        family_support_shift = ""
        care_dependent = ""
        financial_burden = ""
        
        if r_type == "Branch":
            with st.container(border=True):
                st.caption("🏪 分公司意願調查")
                saved_region = str(my_resume.get('branch_region', ''))
                try: reg_idx = list(BRANCH_DATA.keys()).index(saved_region)
                except: reg_idx = 0
                region = st.selectbox("請選擇希望任職區域", list(BRANCH_DATA.keys()), index=reg_idx, key="reg_sel")
                available_branches = BRANCH_DATA[region]
                
                db_loc_str = str(my_resume.get('branch_location', ''))
                saved_primary = db_loc_str.split(' (')[0].strip()
                try: p_idx = available_branches.index(saved_primary)
                except: p_idx = 0
                primary_branch = st.selectbox(f"請選擇 {region} 的首選分校 (單選)", available_branches, index=p_idx, key="pri_sel")
                
                saved_rot = str(my_resume.get('accept_rotation', ''))
                rot_idx = 1 if saved_rot == "否" else 0
                rot_val = st.radio("是否可配合輪調 (支援不同分校)？", ["是", "否"], index=rot_idx, horizontal=True, key="rot_sel")
                
                if rot_val == "是":
                    saved_backups = []
                    if "(輪調: " in db_loc_str:
                        try:
                            content = db_loc_str.split("(輪調: ")[1].replace(")", "")
                            saved_backups = [x.strip() for x in content.split(", ")]
                        except: pass
                    backup_opts = [b for b in available_branches if b != primary_branch]
                    st.caption("請勾選可配合輪調的分校 (可複選)：")
                    selected_backups = []
                    cb_cols = st.columns(min(4, max(1, len(backup_opts))))
                    for idx, branch in enumerate(backup_opts):
                        if cb_cols[idx % len(cb_cols)].checkbox(branch, value=(branch in saved_backups), key=f"rot_cb_{branch}"):
                            selected_backups.append(branch)
                    if selected_backups: loc_val = f"{primary_branch} (輪調: {', '.join(selected_backups)})"
                    else: loc_val = primary_branch

                st.divider()
                saved_shift = str(my_resume.get('shift_avail', ''))
                shift_idx = 1 if saved_shift == "否" else 0
                shift_val = st.radio("是否可配合輪班 (同一分校不同時間)？", ["是", "否"], index=shift_idx, horizontal=True, key="shift_sel")
                if shift_val == "否": st.warning("⚠️ 分公司職務通常需要配合輪班")
                
                st.divider()
                def get_yn_idx(v): return 0 if v in ["可以", "同意", "需要"] else 1
                c_h1, c_h2 = st.columns(2)
                holiday_shift = c_h1.radio("國定假日輪值？", ["可以", "不可以"], index=get_yn_idx(my_resume.get('holiday_shift')), horizontal=True, key='holiday_shift')
                rotate_shift = c_h2.radio("配合輪早晚班？", ["可以", "不可以"], index=get_yn_idx(my_resume.get('rotate_shift')), horizontal=True, key='rotate_shift')
                c_f1, c_f2 = st.columns(2)
                family_support_shift = c_f1.radio("家人同意輪班？", ["同意", "不同意"], index=get_yn_idx(my_resume.get('family_support_shift')), horizontal=True, key='family_support_shift')
                c_d1, c_d2 = st.columns(2)
                care_dependent = c_d1.radio("需獨力扶養長幼？", ["需要", "不需要"], index=get_yn_idx(my_resume.get('care_dependent')), horizontal=True, key='care_dependent')
                financial_burden = c_d2.radio("需獨力負擔家計？", ["需要", "不需要"], index=get_yn_idx(my_resume.get('financial_burden')), horizontal=True, key='financial_burden')

        with st.container(border=True):
            st.caption("其他資訊")
            st.text_input("應徵管道", value=my_resume.get('source',''), key='source')
            st.text_input("任職親友", value=my_resume.get('relative_name',''), key='relative_name')
            def get_idx01(v): return 0 if v != "有" else 1
            st.radio("補教經驗", ["無", "有"], index=get_idx01(my_resume.get('teach_exp')), horizontal=True, key='teach_exp')
            st.radio("出國史", ["無", "有"], index=get_idx01(my_resume.get('travel_history')), horizontal=True, key='travel_history')
            st.radio("兵役", ["未役", "免役", "役畢"], index=0, horizontal=True, key='military_status')
            st.radio("近年住院史？", ["無", "有"], index=get_idx01(my_resume.get('hospitalization')), horizontal=True, key='hospitalization')
            st.radio("慢性病藥控？", ["無", "有"], index=get_idx01(my_resume.get('chronic_disease')), horizontal=True, key='chronic_disease')
            c_fam1, c_fam2 = st.columns(2)
            st.radio("獨力扶養？", ["需要", "不需要"], index=0 if my_resume.get('family_support')!="需要" else 1, horizontal=True, key='family_support')
            st.radio("獨力負擔？", ["需要", "不需要"], index=0 if my_resume.get('family_debt')!="需要" else 1, horizontal=True, key='family_debt')
            c_com1, c_com2 = st.columns(2)
            st.text_input("通勤方式", value=my_resume.get('commute_method',''), key='commute_method')
            st.text_input("通勤時間(分)", value=my_resume.get('commute_time',''), key='commute_time')

        with st.container(border=True):
            st.caption("技能與自傳")
            skills = st.text_area("專業技能", value=my_resume['skills'], height=100, key='skills')
            intro = st.text_area("自傳 / 工作成就", value=my_resume['self_intro'], height=150, key='self_intro')
            try: st.image("qrcode.png", caption="追蹤職缺", width=100)
            except: pass

        c_s, c_d = st.columns(2)
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': dob, 'address': addr,
            'skills': skills, 'self_intro': intro,
            'shift_avail': shift_val, 'holiday_shift': holiday_shift, 'rotate_shift': rotate_shift,
            'family_support_shift': family_support_shift, 'care_dependent': care_dependent, 'financial_burden': financial_burden
        }
        for k in st.session_state:
            if isinstance(k, str) and k not in ['user', 'logged_in']: form_data[k] = st.session_state[k]
        
        if r_type == "Branch":
            form_data['branch_region'] = region
            form_data['branch_location'] = loc_val
            form_data['accept_rotation'] = rot_val

        if c_s.form_submit_button("💾 暫存"):
            sys.save_resume(user['email'], form_data, "Draft")
            st.success("已暫存"); time.sleep(1); st.rerun()
            
        if c_d.form_submit_button("🚀 送出"):
            edu1_school = st.session_state.get('edu_1_school_in', '').strip()
            edu1_major  = st.session_state.get('edu_1_major_in',  '').strip()
            edu1_start  = st.session_state.get('edu_1_start_in',  '').strip()
            edu1_end    = st.session_state.get('edu_1_end_in',    '').strip()
            height_val  = str(st.session_state.get('height', '')).strip()
            weight_val  = str(st.session_state.get('weight', '')).strip()
            n_en_val    = str(n_en).strip()
            addr_val    = str(addr).strip()
            def _yyyymm(s): return bool(re.match(r'^\d{4}/\d{1,2}$', s)) if s else False
            def _num(s):
                try: float(s); return True
                except: return False
            if not str(n_cn).strip() or not str(phone).strip():
                st.error("中文姓名與手機為必填")
            elif not addr_val:
                st.error("通訊地址為必填")
            elif n_en_val and not re.match(r'^[A-Za-z\s]+$', n_en_val):
                st.error("英文姓名只能包含英文字母及空格")
            elif height_val and not _num(height_val):
                st.error("身高請填寫數字")
            elif weight_val and not _num(weight_val):
                st.error("體重請填寫數字")
            elif not edu1_school:
                st.error("⚠️ 學歷1：學校名稱為必填")
            elif not edu1_major:
                st.error("⚠️ 學歷1：科系名稱為必填")
            elif not _yyyymm(edu1_start):
                st.error("⚠️ 學歷1：入學年月格式須為 YYYY/MM（例如：2010/09）")
            elif not _yyyymm(edu1_end):
                st.error("⚠️ 學歷1：畢業年月格式須為 YYYY/MM（例如：2014/06）")
            elif r_type == "Branch" and rot_val == "是" and "輪調" not in loc_val:
                st.error("請至少勾選一個可配合輪調的分校")
            else:
                sys.save_resume(user['email'], form_data, "Submitted")
                hr = user.get('creator', '')
                if hr and '@' in str(hr): send_email(hr, f"履歷送審: {n_cn}", f"求職者 {n_cn} 已送出履歷，請登入系統審閱。")
                st.success("已送出"); time.sleep(1); st.rerun()

# --- Entry ---
if 'user' not in st.session_state: st.session_state.user = None
if st.session_state.user is None: login_page()
else:
    if st.session_state.user['role'] in ['admin', 'pm']: admin_page()
    else: candidate_page()
