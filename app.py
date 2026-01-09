import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import base64
import smtplib
from email.mime.text import MIMEText
import gspread
from google.oauth2.service_account import Credentials
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- 1. 系統設定 ---
st.set_page_config(page_title="聯成電腦 - 人才招募系統", layout="wide", page_icon="📝")

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
            "resumes": ["email", "status", "name_cn", "name_en", "phone", "address", "dob", 
                        "edu_1_school", "edu_1_major", "edu_1_degree", "edu_1_state",
                        "edu_2_school", "edu_2_major", "edu_2_degree", "edu_2_state",
                        "edu_3_school", "edu_3_major", "edu_3_degree", "edu_3_state",
                        "exp_1_co", "exp_1_title", "exp_1_years", "exp_1_salary", "exp_1_boss", "exp_1_phone", "exp_1_reason",
                        "exp_2_co", "exp_2_title", "exp_2_years", "exp_2_salary", "exp_2_boss", "exp_2_phone", "exp_2_reason",
                        "exp_3_co", "exp_3_title", "exp_3_years", "exp_3_salary", "exp_3_boss", "exp_3_phone", "exp_3_reason",
                        "exp_4_co", "exp_4_title", "exp_4_years", "exp_4_salary", "exp_4_boss", "exp_4_phone", "exp_4_reason",
                        "skills", "self_intro", "hr_comment", "interview_date", "resume_type", "branch_region", "branch_location", "shift_avail", 
                        "source", "relative_name", "teach_exp", "computer_course", "travel_history", "hospitalization", "chronic_disease", 
                        "military_status", "family_support", "family_debt", "commute_method", "commute_time", "height", "weight", "blood_type", 
                        "marital_status", "emergency_contact", "emergency_phone", "home_phone"],
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
            self.ws_users.append_row([email, email, name, role, creator_email, str(date.today())])
            if role == "candidate":
                # 建立空資料列，補足所有欄位 (74欄)
                # 欄位順序需對應 get_df 的 defaults
                # email(0), status(1), name_cn(2) ... resume_type(51)
                empty_row = [""] * 74
                empty_row[0] = email
                empty_row[1] = "New"
                empty_row[2] = name
                empty_row[51] = r_type 
                self.ws_resumes.append_row(empty_row)
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
                headers = self.ws_resumes.row_values(1)
                headers = [h.strip().lower() for h in headers]
                
                self.ws_resumes.update_cell(r, headers.index('status')+1, status)
                
                for key, val in data.items():
                    key_lower = key.lower()
                    if key_lower in headers:
                        col_idx = headers.index(key_lower) + 1
                        if isinstance(val, (date, datetime)): val = str(val)
                        self.ws_resumes.update_cell(r, col_idx, val)
                return True, "儲存成功"
            return False, "No Data"
        except Exception as e: return False, str(e)

    def hr_update_status(self, email, status, comment="", interview_date=""):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                r = cell.row
                headers = self.ws_resumes.row_values(1)
                headers = [h.strip().lower() for h in headers]
                
                self.ws_resumes.update_cell(r, headers.index('status')+1, status)
                self.ws_resumes.update_cell(r, headers.index('hr_comment')+1, comment)
                self.ws_resumes.update_cell(r, headers.index('interview_date')+1, str(interview_date))
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
    except: return True 

# --- PDF Generation ---
def generate_pdf(data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    try:
        pdfmetrics.registerFont(TTFont('TaipeiSans', 'TaipeiSansTCBeta-Regular.ttf'))
        font_name = 'TaipeiSans'
    except: font_name = 'Helvetica'
    
    y = height - 50
    c.setFont(font_name, 18)
    c.drawCentredString(width/2, y, "聯成電腦面試人員履歷表")
    y -= 40
    c.setFont(font_name, 10)
    
    # 基本資料
    c.drawString(50, y, f"姓名: {data.get('name_cn','')} ({data.get('name_en','')})")
    c.drawString(300, y, f"Email: {data.get('email','')}")
    y -= 15
    c.drawString(50, y, f"電話: {data.get('phone','')} / {data.get('home_phone','')}")
    c.drawString(300, y, f"生日: {data.get('dob','')}")
    y -= 15
    c.drawString(50, y, f"地址: {data.get('address','')}")
    y -= 20
    
    # 學歷
    c.drawString(50, y, "【學歷】")
    y -= 15
    for i in range(1, 4):
        s = data.get(f'edu_{i}_school', '')
        if s:
            c.drawString(50, y, f"{s} | {data.get(f'edu_{i}_major','')} | {data.get(f'edu_{i}_degree','')} | {data.get(f'edu_{i}_state','')}")
            y -= 15
    y -= 10
    
    # 經歷
    c.drawString(50, y, "【工作經歷】")
    y -= 15
    for i in range(1, 5):
        co = data.get(f'exp_{i}_co', '')
        if co:
            c.drawString(50, y, f"公司: {co} | 職稱: {data.get(f'exp_{i}_title','')}")
            y -= 15
            c.drawString(60, y, f"主管: {data.get(f'exp_{i}_boss','')} | 薪資: {data.get(f'exp_{i}_salary','')} | 離職: {data.get(f'exp_{i}_reason','')}")
            y -= 20
    y -= 10

    # 分公司
    if data.get('resume_type') == 'Branch':
        c.drawString(50, y, "【分公司意願】")
        y -= 15
        c.drawString(50, y, f"區域: {data.get('branch_region','')}")
        y -= 15
        c.drawString(50, y, f"地點: {data.get('branch_location','')}")
        y -= 15
        c.drawString(50, y, f"配合輪班: {data.get('shift_avail','')}")
        y -= 25

    try:
        c.drawImage("qrcode.png", 450, height-100, width=80, height=80)
    except: pass
    
    c.line(50, 50, 550, 50)
    c.drawString(50, 35, "應徵人員親簽：______________________   日期：_____/_____/_____")

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
    tabs = ["📧 發送邀請", "📋 履歷審核"]
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
                        try: link = st.secrets["email"]["app_url"]
                        except: link = "https://share.streamlit.io/"
                        body = f"請登入填寫履歷：{link}\n帳號：{c_email}\n密碼：{c_email}"
                        send_email(c_email, "面試邀請", body)
                        st.success(f"已發送給 {c_name}")
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
                    
                    if target['status'] == "Approved":
                        pdf_data = generate_pdf(target.to_dict())
                        st.download_button("📥 下載 PDF", pdf_data, f"{target['name_cn']}_履歷.pdf", "application/pdf")

                    with st.expander("詳細內容", expanded=True):
                        st.write(target.to_dict())

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

    # --- 分公司意願區塊 (移出 Form) ---
    branch_data_to_save = {}
    if r_type == "Branch":
        st.info("👇 請先完成此區塊，再填寫下方詳細履歷")
        with st.container(border=True):
            st.subheader("🏪 分公司意願調查")
            
            # 1. 區域
            saved_region = str(my_resume.get('branch_region', ''))
            try: reg_idx = list(BRANCH_DATA.keys()).index(saved_region)
            except: reg_idx = 0
            region = st.selectbox("1. 請選擇希望任職區域", list(BRANCH_DATA.keys()), index=reg_idx, key="reg_sel")
            
            # 2. 首選
            available_branches = BRANCH_DATA[region]
            db_loc_str = str(my_resume.get('branch_location', ''))
            saved_primary = db_loc_str.split(' (')[0].strip()
            try: p_idx = available_branches.index(saved_primary)
            except: p_idx = 0
            primary_branch = st.selectbox(f"2. 請選擇 {region} 的首選分校 (單選)", available_branches, index=p_idx, key="pri_sel")
            
            # 3. 輪班意願
            saved_shift = str(my_resume.get('shift_avail', ''))
            shift_idx = 0 if saved_shift == "是" else 1
            shift_val = st.radio("3. 是否可配合輪班？", ["是", "否"], index=shift_idx, horizontal=True, key="shift_sel")
            
            # 4. 輪班複選
            final_loc_str = primary_branch
            if shift_val == "是":
                saved_backups = []
                if "(輪班: " in db_loc_str:
                    try:
                        content = db_loc_str.split("(輪班: ")[1].replace(")", "")
                        saved_backups = [x.strip() for x in content.split(",")]
                    except: pass
                
                backup_opts = [b for b in available_branches if b != primary_branch]
                valid_defaults = [b for b in saved_backups if b in backup_opts]
                
                selected_backups = st.multiselect("4. 請勾選可配合輪班的分校 (複選)", backup_opts, default=valid_defaults, key="back_sel")
                
                if selected_backups:
                    final_loc_str = f"{primary_branch} (輪班: {', '.join(selected_backups)})"
            
            branch_data_to_save = {
                'branch_region': region,
                'branch_location': final_loc_str,
                'shift_avail': shift_val
            }

    # --- 主表單 ---
    with st.form("resume_form"):
        st.markdown(f"### {'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'} 履歷表")
        
        with st.container(border=True):
            st.caption("基本資料")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'])
            n_en = c2.text_input("英文姓名", value=my_resume['name_en'])
            st.session_state['height'] = c3.text_input("身高(cm)", value=my_resume.get('height',''))
            st.session_state['weight'] = c4.text_input("體重(kg)", value=my_resume.get('weight',''))
            c5, c6, c7 = st.columns([2, 1, 1])
            phone = c5.text_input("手機", value=my_resume['phone'])
            st.session_state['home_phone'] = c6.text_input("市話 (H)", value=my_resume.get('home_phone',''))
            
            curr_mar = my_resume.get('marital_status', '未婚')
            m_idx = ["未婚", "已婚"].index(curr_mar) if curr_mar in ["未婚", "已婚"] else 0
            st.session_state['marital_status'] = c7.selectbox("婚姻", ["未婚", "已婚"], index=m_idx)
            
            try: dval = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
            except: dval = date(1995,1,1)
            dob = c1.date_input("生日", value=dval)
            addr = st.text_input("通訊地址", value=my_resume['address'])
            c8, c9 = st.columns(2)
            st.session_state['emergency_contact'] = c8.text_input("緊急聯絡人", value=my_resume.get('emergency_contact',''))
            st.session_state['emergency_phone'] = c9.text_input("緊急聯絡電話", value=my_resume.get('emergency_phone',''))

        with st.container(border=True):
            st.caption("其他資訊")
            st.session_state['source'] = st.text_input("應徵管道", value=my_resume.get('source',''))
            st.session_state['relative_name'] = st.text_input("任職親友", value=my_resume.get('relative_name',''))
            
            # Radios
            def get_idx(val): return 0 if val != "有" else 1
            st.session_state['teach_exp'] = st.radio("補教經驗", ["無", "有"], index=get_idx(my_resume.get('teach_exp')), horizontal=True)
            st.session_state['travel_history'] = st.radio("出國史", ["無", "有"], index=get_idx(my_resume.get('travel_history')), horizontal=True)
            
            mil_val = my_resume.get('military_status', '未役')
            mil_idx = ["未役", "免役", "役畢"].index(mil_val) if mil_val in ["未役", "免役", "役畢"] else 0
            st.session_state['military_status'] = st.radio("兵役", ["未役", "免役", "役畢"], index=mil_idx, horizontal=True)

        with st.container(border=True):
            st.caption("學歷 (請填寫最高及次高學歷)")
            for i in range(1, 4):
                st.markdown(f"**學歷 {i}**")
                rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
                st.session_state[f'edu_{i}_school'] = rc1.text_input(f"學校 {i}", value=my_resume.get(f'edu_{i}_school',''))
                st.session_state[f'edu_{i}_major'] = rc2.text_input(f"科系 {i}", value=my_resume.get(f'edu_{i}_major',''))
                deg_val = my_resume.get(f'edu_{i}_degree', '學士')
                deg_opts = ["學士", "碩士", "博士", "高中/職", "其他"]
                d_idx = deg_opts.index(deg_val) if deg_val in deg_opts else 0
                st.session_state[f'edu_{i}_degree'] = rc3.selectbox(f"學位 {i}", deg_opts, index=d_idx)
                
                state_val = my_resume.get(f'edu_{i}_state', '畢業')
                state_idx = 0 if state_val != "肄業" else 1
                st.session_state[f'edu_{i}_state'] = rc4.radio(f"狀態 {i}", ["畢業", "肄業"], index=state_idx, horizontal=True)

        with st.container(border=True):
            st.caption("曾任職公司 (最近4筆)")
            for i in range(1, 5):
                with st.expander(f"經歷 {i}"):
                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    st.session_state[f'exp_{i}_co'] = ec1.text_input(f"公司名稱 {i}", value=my_resume.get(f'exp_{i}_co',''))
                    st.session_state[f'exp_{i}_title'] = ec2.text_input(f"職稱 {i}", value=my_resume.get(f'exp_{i}_title',''))
                    try: y_val = float(my_resume.get(f'exp_{i}_years',0) or 0)
                    except: y_val = 0.0
                    st.session_state[f'exp_{i}_years'] = ec3.number_input(f"年資 {i}", value=y_val)
                    
                    ec4, ec5, ec6 = st.columns([1, 1, 1])
                    st.session_state[f'exp_{i}_boss'] = ec4.text_input(f"主管 {i}", value=my_resume.get(f'exp_{i}_boss',''))
                    st.session_state[f'exp_{i}_phone'] = ec5.text_input(f"電話 {i}", value=my_resume.get(f'exp_{i}_phone',''))
                    st.session_state[f'exp_{i}_salary'] = ec6.text_input(f"薪資 {i}", value=my_resume.get(f'exp_{i}_salary',''))
                    st.session_state[f'exp_{i}_reason'] = st.text_input(f"離職原因 {i}", value=my_resume.get(f'exp_{i}_reason',''))

        st.subheader("技能與自傳")
        skills = st.text_area("專業技能", value=my_resume['skills'])
        intro = st.text_area("自傳", value=my_resume['self_intro'])
        
        c_qr1, c_qr2 = st.columns([4, 1])
        c_qr1.info("本人所填資料均屬事實，若有不實或虛構，願隨時接受取消資格或無條件免職之處分。")
        try: c_qr2.image("qrcode.png", caption="追蹤職缺")
        except: pass

        c_s, c_d = st.columns(2)
        
        # 收集資料
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': dob, 'address': addr,
            'skills': skills, 'self_intro': intro
        }
        # 加入動態欄位
        for k, v in st.session_state.items():
            if k not in ['user', 'logged_in'] and isinstance(k, str):
                form_data[k] = v
        
        # 合併分公司資料
        form_data.update(branch_data_to_save)

        if c_s.form_submit_button("💾 暫存"):
            sys.save_resume(user['email'], form_data, "Draft")
            st.success("已暫存"); time.sleep(1); st.rerun()
            
        if c_d.form_submit_button("🚀 送出審核"):
            if not n_cn or not phone: st.error("姓名與電話為必填")
            elif r_type == "Branch" and branch_data_to_save['shift_avail'] == "是" and "輪班" not in branch_data_to_save['branch_location']:
                st.error("請勾選可配合輪班的分校")
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
