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
from reportlab.lib import colors

# --- 1. 系統設定 ---
st.set_page_config(page_title="聯成電腦 - 面試人員履歷表", layout="wide", page_icon="📝")

# Email 設定
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = ""      
SENDER_PASSWORD = ""   

# Logo URL
LOGO_URL = "https://www.lccnet.com.tw/img/logo.png"

# 分公司區域資料 (連動選單用)
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
            "resumes": ["email", "status", "name_cn", "name_en", "phone", "address", "dob", "education_school", "education_major", "education_degree", "experience_company", "experience_title", "experience_years", "skills", "self_intro", "hr_comment", "interview_date", "resume_type", "branch_region", "branch_location", "shift_avail", "source", "relative_name", "teach_exp", "computer_course", "travel_history", "hospitalization", "chronic_disease", "military_status", "family_support", "family_debt", "commute_method", "commute_time", "height", "weight", "blood_type", "marital_status", "emergency_contact", "emergency_phone", "home_phone"],
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
            user = df[df['email'].astype(str).str.strip().str.lower() == str(email).strip().lower()]
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
                # 補足 40 欄
                row_data = [email, "New", name] + [""] * 14 + ["", r_type] + [""] * 22
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
                # 欄位 mapping (A=1)
                mapping = {
                    'name_cn': 3, 'name_en': 4, 'phone': 5, 'address': 6, 'dob': 7,
                    'education_school': 8, 'education_major': 9, 'education_degree': 10,
                    'experience_company': 11, 'experience_title': 12, 'experience_years': 13,
                    'skills': 14, 'self_intro': 15, 
                    'branch_region': 19, 'branch_location': 20, 'shift_avail': 21,
                    'source': 22, 'relative_name': 23, 'teach_exp': 24, 'computer_course': 25,
                    'travel_history': 26, 'hospitalization': 27, 'chronic_disease': 28,
                    'military_status': 29, 'family_support': 30, 'family_debt': 31,
                    'commute_method': 32, 'commute_time': 33, 'height': 34, 'weight': 35,
                    'blood_type': 36, 'marital_status': 37, 'emergency_contact': 38,
                    'emergency_phone': 39, 'home_phone': 40
                }
                
                self.ws_resumes.update_cell(r, 2, status)
                for k, col_idx in mapping.items():
                    if k in data:
                        val = data[k]
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
    except: return True 

# --- PDF Generation (Enhanced) ---
def generate_pdf(data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Font
    try:
        pdfmetrics.registerFont(TTFont('TaipeiSans', 'TaipeiSansTCBeta-Regular.ttf'))
        font_name = 'TaipeiSans'
    except: font_name = 'Helvetica'
    
    c.setFont(font_name, 18)
    c.drawString(50, height-50, "聯成電腦面試人員履歷表")
    
    c.setFont(font_name, 10)
    y = height - 80
    
    # 繪製表格框線與內容 (模擬)
    # 這裡只列出關鍵欄位，完整還原需要大量座標 coding
    # Row 1: Name, Email
    c.drawString(50, y, f"姓名: {data.get('name_cn','')}  (英: {data.get('name_en','')})")
    c.drawString(300, y, f"Email: {data.get('email','')}")
    y -= 20
    c.drawString(50, y, f"電話: {data.get('phone','')} / {data.get('home_phone','')}")
    c.drawString(300, y, f"生日: {data.get('dob','')}")
    y -= 20
    c.drawString(50, y, f"地址: {data.get('address','')}")
    y -= 30
    
    c.drawString(50, y, "[學歷]")
    y -= 15
    c.drawString(50, y, f"{data.get('education_school','')} | {data.get('education_major','')} | {data.get('education_degree','')}")
    y -= 30
    
    c.drawString(50, y, "[工作經歷]")
    y -= 15
    c.drawString(50, y, f"{data.get('experience_company','')} | {data.get('experience_title','')} | {data.get('experience_years','')}年")
    y -= 30
    
    c.drawString(50, y, "[其他資訊]")
    y -= 15
    c.drawString(50, y, f"來源: {data.get('source','')}")
    y -= 15
    c.drawString(50, y, f"兵役: {data.get('military_status','')}")
    y -= 15
    c.drawString(50, y, f"出國史: {data.get('travel_history','')}")
    
    if data.get('resume_type') == 'Branch':
        y -= 30
        c.drawString(50, y, "[分公司專屬]")
        y -= 15
        c.drawString(50, y, f"區域: {data.get('branch_region','')}")
        c.drawString(200, y, f"分校: {data.get('branch_location','')}")
        y -= 15
        c.drawString(50, y, f"配合輪班: {data.get('shift_avail','')}")

    # QR Code (假設有圖片)
    try:
        c.drawImage("qrcode.png", 450, height-100, width=80, height=80)
    except: pass
    
    # 簽名欄
    c.line(50, 100, 550, 100)
    c.drawString(50, 110, "應徵人員親簽：______________________   日期：_____/_____/_____")

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

                    with st.expander("查看履歷詳細內容", expanded=True):
                        st.write(target.to_dict()) # 暫時以 JSON 顯示完整內容，可再優化 UI

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

    with st.form("resume_form"):
        st.markdown(f"### {'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'} 履歷表")
        
        # --- 基本資料 (擬真表格) ---
        with st.container(border=True):
            st.caption("基本資料")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'])
            n_en = c2.text_input("英文姓名", value=my_resume['name_en'])
            # 性別/血型
            # 這裡簡化，若需完整還原可加 radio
            
            c3.text_input("身高(cm)", value=my_resume.get('height',''))
            c4.text_input("體重(kg)", value=my_resume.get('weight',''))

            c5, c6, c7 = st.columns([2, 1, 1])
            phone = c5.text_input("手機", value=my_resume['phone'])
            h_phone = c6.text_input("市話 (H)", value=my_resume.get('home_phone',''))
            m_status = c7.selectbox("婚姻", ["未婚", "已婚"], index=0)

            addr = st.text_input("通訊地址", value=my_resume['address'])
            
            c8, c9 = st.columns(2)
            c8.text_input("緊急聯絡人", value=my_resume.get('emergency_contact',''))
            c9.text_input("緊急聯絡電話", value=my_resume.get('emergency_phone',''))

        # --- 雜項調查 ---
        with st.container(border=True):
            st.caption("其他資訊")
            q1 = st.text_input("您是透過何種管道前來應徵？", value=my_resume.get('source',''))
            q2 = st.text_input("是否有現在在本公司任職的親友？(姓名)", value=my_resume.get('relative_name',''))
            q3 = st.radio("您是否曾在美語或電腦補習班任職過？", ["無", "有"], horizontal=True, index=0)
            q4 = st.radio("今年度您是否有出國旅遊史？", ["無", "有"], horizontal=True, index=0)
            q5 = st.radio("兵役狀況", ["未役", "免役", "役畢"], horizontal=True, index=0)

        # --- 學經歷 ---
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

        # --- 分公司邏輯 ---
        loc_val = ""
        shift_val = ""
        
        if r_type == "Branch":
            with st.container(border=True):
                st.caption("🏪 分公司意願調查")
                # 區域選單
                region = st.selectbox("請選擇希望任職區域/分校", list(BRANCH_DATA.keys()))
                
                # 是否配合輪班
                shift_idx = 0 if my_resume.get('shift_avail') == "是" else 1
                shift_val = st.radio("是否可配合輪班？", ["是", "否"], index=shift_idx, horizontal=True)
                
                # 只有選是，才出現分校選單 (或者都出現)
                available_branches = BRANCH_DATA[region]
                old_loc = str(my_resume.get('branch_location', ''))
                default_loc = [x for x in old_loc.split(',') if x in available_branches]
                
                if shift_val == "是":
                    selected_branches = st.multiselect("希望分校 (可複選，至少選一)", available_branches, default=default_loc)
                    loc_val = ",".join(selected_branches)
                else:
                    st.warning("⚠️ 分公司職務通常需要配合輪班，若選擇「否」可能影響錄取機會。")
                    loc_val = "無法配合輪班"

        with st.container(border=True):
            st.caption("技能與自傳")
            skills = st.text_area("專業技能", value=my_resume['skills'], height=100)
            intro = st.text_area("自傳 / 工作成就", value=my_resume['self_intro'], height=150)
            
            # QR Code 提示
            c_qr1, c_qr2 = st.columns([4, 1])
            c_qr1.info("本人所填資料均屬事實，若有不實或虛構，願隨時接受取消資格或無條件免職之處分。")
            try: c_qr2.image("qrcode.png", caption="追蹤職缺消息")
            except: pass

        # 收集資料
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': "", 'address': addr,
            'edu_school': esch, 'edu_major': emaj, 'edu_degree': edeg,
            'exp_co': eco, 'exp_title': eti, 'exp_years': eyr, 'skills': skills, 'self_intro': intro,
            'source': q1, 'relative_name': q2, 'teach_exp': q3, 'travel_history': q4, 'military_status': q5,
            'home_phone': h_phone, 'marital_status': m_status, 'emergency_contact': "", 'emergency_phone': ""
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
            if not n_cn or not phone: st.error("姓名與電話為必填")
            elif r_type == "Branch" and shift_val=="是" and not loc_val: st.error("請選擇希望分校")
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
