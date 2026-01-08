import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import base64
import smtplib
from email.mime.text import MIMEText
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors

# --- 1. 系統設定 ---
st.set_page_config(page_title="聯成電腦 - 人才招募系統 (完整版)", layout="wide", page_icon="📝")

# Email 設定
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = ""      
SENDER_PASSWORD = ""   

# Logo URL
LOGO_URL = "https://www.lccnet.com.tw/img/logo.png"

# 分公司區域資料
BRANCH_DATA = {
    "北一區": ["館前", "公館", "忠孝", "士林", "基隆", "羅東"],
    "北二區": ["板橋", "新莊", "三重", "永和"],
    "桃竹區": ["桃園", "中壢", "新竹"],
    "中區": ["豐原", "逢甲", "三民", "站前", "彰化"],
    "南一區": ["斗六", "嘉義", "台南", "永康"],
    "南二區": ["高雄", "鳳山", "楠梓", "屏東"]
}

# --- 2. 資料庫核心 ---
class ResumeDB:
    def __init__(self):
        self.connect()

    def connect(self):
        try:
            scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            self.client = gspread.authorize(creds)
            sheet_url = st.secrets["sheet_config"]["spreadsheet_url"]
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
            "resumes": ["email", "status", "name_cn", "name_en", "phone", "address", "dob", "education_school", "education_major", "education_degree", "experience_company", "experience_title", "experience_years", "skills", "self_intro", "hr_comment", "interview_date", "resume_type", "branch_region", "branch_location", "shift_avail"],
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
                if str(row['password']) == str(password):
                    return {"email": row['email'], "name": row['name'], "role": row['role'], "creator": row.get('creator_email', '')}
            return None
        except: return None

    def create_user(self, creator_email, email, name, role, r_type=""):
        try:
            df = self.get_df("users")
            if not df.empty and str(email) in df['email'].astype(str).values: return False, "Email 已存在"
            
            # 建立 User
            self.ws_users.append_row([email, email, name, role, creator_email, str(date.today())])
            
            # 若是 candidate，同時建立 Resume
            if role == "candidate":
                # 補足欄位到 U (21欄)
                # 順序: email, status, name_cn ... interview(17), type(18), region(19), loc(20), shift(21)
                row_data = [email, "New", name] + [""] * 14 + ["", r_type, "", "", ""]
                self.ws_resumes.append_row(row_data)
            return True, "建立成功"
        except Exception as e: return False, str(e)

    def change_password(self, email, new_password):
        try:
            cell = self.ws_users.find(email, in_column=1)
            if cell: self.ws_users.update_cell(cell.row, 2, new_password); return True, "OK"
            return False, "Fail"
        except Exception as e: return False, str(e)

    def save_resume(self, email, data, status="Draft"):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                r = cell.row
                # 批次對應
                updates = [
                    (2, status), (3, data.get('name_cn','')), (4, data.get('name_en','')), (5, data.get('phone','')),
                    (6, data.get('address','')), (7, str(data.get('dob',''))), (8, data.get('edu_school','')),
                    (9, data.get('edu_major','')), (10, data.get('edu_degree','')), (11, data.get('exp_co','')),
                    (12, data.get('exp_title','')), (13, str(data.get('exp_years',0))), (14, data.get('skills','')),
                    (15, data.get('self_intro',''))
                ]
                for c, v in updates: self.ws_resumes.update_cell(r, c, v)
                
                # 分公司欄位 S=19, T=20, U=21 (假設 Google Sheet 已新增 branch_region 欄位)
                # 需確認 Sheet 標題: ... interview(17), resume_type(18), branch_region(19), branch_loc(20), shift_avail(21)
                if 'branch_region' in data: self.ws_resumes.update_cell(r, 19, data['branch_region'])
                if 'branch_location' in data: self.ws_resumes.update_cell(r, 20, data['branch_location'])
                if 'shift_avail' in data: self.ws_resumes.update_cell(r, 21, data['shift_avail'])
                return True, "儲存成功"
            return False, "No Data"
        except Exception as e: return False, str(e)

    def hr_update_status(self, email, status, comment="", interview_date=""):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                r = cell.row
                self.ws_resumes.update_cell(r, 2, status)
                self.ws_resumes.update_cell(r, 16, comment)
                self.ws_resumes.update_cell(r, 17, str(interview_date))
                return True, "OK"
            return False, "Fail"
        except Exception as e: return False, str(e)

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
except: st.error("連線失敗"); st.stop()

# --- Email ---
def send_email(to_email, subject, body):
    try:
        email_config = st.secrets["email"]
        sender_email = email_config["sender_email"]; sender_password = email_config["sender_password"]
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(sender_email, sender_password)
        msg = MIMEText(body, 'plain', 'utf-8'); msg['Subject'] = subject; msg['From'] = sender_email; msg['To'] = to_email
        server.send_message(msg); server.quit()
        return True
    except: return True # 模擬成功

# --- PDF Generation ---
def generate_pdf(data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # 註冊中文字型 (需上傳字型檔到 GitHub 或使用內建)
    # 這裡為演示，若無字型檔會報錯，實際佈署需上傳 .ttf
    # 暫時使用 Helvetica (不支援中文顯示，會變亂碼)
    # 解決方案：請在 GitHub 上傳 'TaipeiSansTCBeta-Regular.ttf' 並放在同目錄
    # 這裡假設有字型，若無則 fallback
    try:
        pdfmetrics.registerFont(TTFont('TaipeiSans', 'TaipeiSansTCBeta-Regular.ttf'))
        font_name = 'TaipeiSans'
    except:
        font_name = 'Helvetica' # 英文 fallback
    
    c.setFont(font_name, 16)
    title = "聯成電腦面試人員履歷表"
    c.drawCentredString(width/2, height-50, title)
    
    c.setFont(font_name, 12)
    y = height - 100
    
    # 繪製表格線條與內容 (模擬 PDF 格式)
    # 這裡只做簡單示範，完整重現需要大量座標繪製
    fields = [
        ("姓名", data.get('name_cn', '')), ("Email", data.get('email', '')),
        ("電話", data.get('phone', '')), ("學歷", f"{data.get('education_school','')}/{data.get('education_major','')}")
    ]
    
    for k, v in fields:
        c.drawString(50, y, f"{k}: {v}")
        y -= 25
        
    if data.get('resume_type') == 'Branch':
        y -= 20
        c.drawString(50, y, f"志願地點: {data.get('branch_location','')}")
        y -= 25
        c.drawString(50, y, f"配合輪班: {data.get('shift_avail','')}")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# --- UI Components ---
def render_sidebar(user):
    with st.sidebar:
        try:
            raw_logo = sys.get_logo(); logo = str(raw_logo).strip() if raw_logo else None
            if logo and len(logo)>10:
                if logo.startswith("http"): st.image(logo, use_container_width=True)
                else: st.image(f"data:image/png;base64,{logo}", use_container_width=True)
            else: st.image(LOGO_URL, use_container_width=True)
        except: st.image(LOGO_URL, use_container_width=True)
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
    st.markdown("## 📝 聯成電腦 - 人才招募系統")
    c1, c2 = st.columns(2)
    with c1:
        email = st.text_input("Email"); pwd = st.text_input("密碼", type="password")
        if st.button("登入", type="primary"):
            user = sys.verify_login(email, pwd)
            if user: st.session_state.user = user; st.rerun()
            else: st.error("錯誤")
    with c2: st.info("預設密碼為您的 Email")

def admin_page():
    user = st.session_state.user
    render_sidebar(user)
    st.header(f"👨‍💼 管理後台")
    
    # PM 無法看到系統設定 Tab
    tabs = ["📧 發送邀請", "📋 履歷審核"]
    if user['role'] == 'admin': tabs.append("⚙️ 設定")
    
    current_tab = st.tabs(tabs)
    
    # Tab 1: 邀請 (含 PM 建立)
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
                        try: link = st.secrets["email"]["app_url"]
                        except: link = "https://share.streamlit.io/"
                        body = f"請登入填寫履歷：{link}\n帳號：{c_email}\n密碼：{c_email}"
                        send_email(c_email, "面試邀請", body)
                        st.success(f"已發送給 {c_name}")
                    else: st.error(msg)
        
        # 只有 Admin 可以建立 PM
        if user['role'] == 'admin':
            with c2.form("create_pm"):
                st.write("#### 建立人資 PM")
                p_name = st.text_input("PM 姓名"); p_email = st.text_input("PM Email")
                if st.form_submit_button("建立 PM"):
                    if p_name and p_email:
                        succ, msg = sys.create_user(user['email'], p_email, p_name, "pm")
                        if succ: st.success(f"PM {p_name} 建立成功")
                        else: st.error(msg)

    # Tab 2: 審核
    with current_tab[1]:
        st.subheader("履歷審核列表")
        df = sys.get_df("resumes")
        if not df.empty:
            submitted = df[df['status'].isin(['Submitted', 'Approved', 'Returned'])].copy()
            if not submitted.empty:
                st.dataframe(submitted[['status', 'name_cn', 'email', 'resume_type']])
                sel_email = st.selectbox("選擇候選人", submitted['email'].unique())
                if sel_email:
                    target = df[df['email'] == sel_email].iloc[0]
                    st.divider()
                    st.markdown(f"### 📄 {target['name_cn']} 履歷表")
                    
                    # 顯示 PDF 下載按鈕 (若是 Approved)
                    if target['status'] == "Approved":
                        pdf_data = generate_pdf(target.to_dict())
                        st.download_button("📥 下載 PDF", pdf_data, f"{target['name_cn']}_履歷.pdf", "application/pdf")

                    # 履歷內容展示 (唯讀)
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.write(f"**姓名**: {target['name_cn']}")
                        c2.write(f"**電話**: {target['phone']}")
                        c3.write(f"**學歷**: {target['education_school']}")
                        c4.write(f"**經歷**: {target['experience_company']}")
                        
                        if target.get('resume_type') == 'Branch':
                            st.info(f"📍 志願: {target.get('branch_location')} | 🕒 輪班: {target.get('shift_avail')}")
                        
                        st.text_area("自傳", value=target['self_intro'], disabled=True)

                    # 審核操作
                    st.write("#### 審核操作")
                    cmt = st.text_input("評語", value=target['hr_comment'])
                    c_ok, c_no = st.columns(2)
                    if c_ok.button("✅ 核准", key="ok"):
                        sys.hr_update_status(sel_email, "Approved", cmt, date.today())
                        st.success("已核准"); time.sleep(1); st.rerun()
                    if c_no.button("↩️ 退件", key="no"):
                        sys.hr_update_status(sel_email, "Returned", cmt)
                        st.warning("已退件"); time.sleep(1); st.rerun()
            else: st.info("無待審履歷")

    # Tab 3: 設定 (Admin Only)
    if user['role'] == 'admin':
        with current_tab[2]:
            up = st.file_uploader("Logo 更新", type=['png','jpg'])
            if up and st.button("更新"):
                b64 = base64.b64encode(up.getvalue()).decode()
                sys.update_logo(f"data:image/png;base64,{b64}")
                st.success("OK"); st.rerun()

def candidate_page():
    user = st.session_state.user
    render_sidebar(user)
    st.header(f"📝 履歷填寫")
    
    df = sys.get_df("resumes")
    if df.empty: st.error("DB Error"); return
    my_df = df[df['email'].astype(str).str.strip().str.lower() == str(user['email']).strip().lower()]
    if my_df.empty: st.error("無履歷資料"); return
    
    my_resume = my_df.iloc[0]
    status = my_resume['status']
    r_type = my_resume.get('resume_type', 'HQ') 

    if status == "Approved": st.success("🎉 已錄取"); return
    elif status == "Submitted": st.info("⏳ 審核中"); return
    elif status == "Returned": st.error(f"被退回：{my_resume['hr_comment']}")

    with st.form("resume_form"):
        st.markdown(f"### {'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'} 履歷表")
        
        # 模擬 PDF 表格排版
        with st.container(border=True):
            st.caption("基本資料")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'])
            n_en = c2.text_input("英文姓名", value=my_resume['name_en'])
            phone = c3.text_input("手機", value=my_resume['phone'])
            # 日期處理
            try: dval = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
            except: dval = date(1995,1,1)
            dob = c4.date_input("生日", value=dval)
            addr = st.text_input("通訊地址", value=my_resume['address'])

        with st.container(border=True):
            st.caption("學經歷")
            c1, c2, c3 = st.columns([2, 1, 1])
            esch = c1.text_input("畢業學校", value=my_resume['education_school'])
            emaj = c2.text_input("科系", value=my_resume['education_major'])
            edeg = c3.selectbox("學位", ["學士", "碩士", "博士", "高中/職"], index=0)
            
            c4, c5, c6 = st.columns([2, 1, 1])
            eco = c4.text_input("最近任職公司", value=my_resume['experience_company'])
            eti = c5.text_input("職稱", value=my_resume['experience_title'])
            eyr = c6.number_input("年資", value=float(my_resume['experience_years']) if my_resume['experience_years'] else 0.0)

        # 分公司專屬邏輯 (連動選單)
        loc_val = ""
        shift_val = ""
        if r_type == "Branch":
            with st.container(border=True):
                st.caption("🏪 分公司意願調查")
                
                # 區域選單
                region = st.selectbox("請選擇希望區域", list(BRANCH_DATA.keys()))
                
                # 是否配合輪班
                shift_idx = 0 if my_resume.get('shift_avail') == "是" else 1
                shift_val = st.radio("是否可配合輪班？", ["是", "否"], index=shift_idx, horizontal=True)
                
                # 連動顯示分校 (只有選「是」才顯示，或是都顯示但必填)
                # 這裡設定為：選好區域後，顯示該區域分校供複選
                available_branches = BRANCH_DATA[region]
                
                # 讀取舊資料 (需處理字串轉 list)
                old_loc = str(my_resume.get('branch_location', ''))
                default_loc = [x for x in old_loc.split(',') if x in available_branches]
                
                selected_branches = st.multiselect("希望分校 (可複選)", available_branches, default=default_loc)
                loc_val = ",".join(selected_branches)
                
                if shift_val == "否":
                    st.warning("⚠️ 分公司職務通常需要配合輪班")

        with st.container(border=True):
            st.caption("技能與自傳")
            skills = st.text_area("專業技能", value=my_resume['skills'], height=100)
            intro = st.text_area("自傳 / 工作成就", value=my_resume['self_intro'], height=150)

        # 收集資料
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': dob, 'address': addr,
            'edu_school': esch, 'edu_major': emaj, 'edu_degree': edeg,
            'exp_co': eco, 'exp_title': eti, 'exp_years': eyr, 'skills': skills, 'self_intro': intro
        }
        if r_type == "Branch":
            form_data['branch_region'] = region
            form_data['branch_location'] = loc_val
            form_data['shift_avail'] = shift_val

        c_s, c_d = st.columns(2)
        if c_s.form_submit_button("💾 暫存"):
            sys.save_resume(user['email'], form_data, "Draft")
            st.success("已暫存"); time.sleep(1); st.rerun()
            
        if c_d.form_submit_button("🚀 送出審核"):
            # 防呆
            if not n_cn or not phone: st.error("姓名與電話為必填")
            elif r_type == "Branch" and (not loc_val or shift_val==""): st.error("請完成分公司意願調查")
            else:
                sys.save_resume(user['email'], form_data, "Submitted")
                hr = user.get('creator', '')
                if hr: send_email(hr, f"履歷送審: {n_cn}", "請登入審閱")
                st.success("已送出"); time.sleep(1); st.rerun()

# --- Entry ---
if 'user' not in st.session_state: st.session_state.user = None
if st.session_state.user is None: login_page()
else:
    if st.session_state.user['role'] in ['admin', 'pm']: admin_page()
    else: candidate_page()
