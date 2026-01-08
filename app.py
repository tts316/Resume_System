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

# --- 1. 系統設定 ---
st.set_page_config(page_title="人才招募履歷系統", layout="wide", page_icon="📝")

# Email 設定 (請務必填寫以啟用通知功能)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = ""      # 您的 Gmail
SENDER_PASSWORD = ""   # 應用程式密碼

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
        ws = self.ws_users if table_name == "users" else self.ws_resumes
        try:
            return pd.DataFrame(ws.get_all_records())
        except: return pd.DataFrame()

    # --- 使用者管理 ---
    def verify_login(self, email, password):
        try:
            cell = self.ws_users.find(email, in_column=1)
            if cell:
                row = self.ws_users.row_values(cell.row)
                # email(0), password(1), name(2), role(3), creator(4)
                if str(row[1]) == str(password):
                    return {"email": row[0], "name": row[2], "role": row[3], "creator": row[4] if len(row)>4 else ""}
            return None
        except: return None

    def create_candidate(self, hr_email, candidate_email, candidate_name):
        try:
            # 檢查是否已存在
            if self.ws_users.find(candidate_email, in_column=1):
                return False, "此 Email 已經存在系統中"
            
            # 1. 建立帳號 (密碼預設同 Email)
            self.ws_users.append_row([candidate_email, candidate_email, candidate_name, "candidate", hr_email, str(date.today())])
            
            # 2. 建立空白履歷 (狀態 New)
            # 欄位對應: email, status, name_cn...
            # 我們先填入 email, status, name_cn, 其他留空
            empty_resume = [candidate_email, "New", candidate_name] + [""] * 14
            self.ws_resumes.append_row(empty_resume)
            
            return True, "建立成功"
        except Exception as e: return False, str(e)

    def change_password(self, email, new_password):
        try:
            cell = self.ws_users.find(email, in_column=1)
            if cell:
                self.ws_users.update_cell(cell.row, 2, new_password)
                return True, "密碼已更新"
            return False, "找不到帳號"
        except Exception as e: return False, str(e)

    # --- 履歷操作 ---
    def save_resume(self, email, data_dict, status="Draft"):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                row = cell.row
                # 欄位順序: email(1), status(2), name_cn(3), name_en(4), phone(5), address(6), dob(7), 
                # edu_school(8), edu_major(9), edu_degree(10), exp_co(11), exp_title(12), exp_years(13), 
                # skills(14), self_intro(15), hr_comment(16), interview_date(17)
                
                updates = [
                    (2, status),
                    (3, data_dict.get('name_cn', '')),
                    (4, data_dict.get('name_en', '')),
                    (5, data_dict.get('phone', '')),
                    (6, data_dict.get('address', '')),
                    (7, str(data_dict.get('dob', ''))),
                    (8, data_dict.get('edu_school', '')),
                    (9, data_dict.get('edu_major', '')),
                    (10, data_dict.get('edu_degree', '')),
                    (11, data_dict.get('exp_co', '')),
                    (12, data_dict.get('exp_title', '')),
                    (13, str(data_dict.get('exp_years', 0))),
                    (14, data_dict.get('skills', '')),
                    (15, data_dict.get('self_intro', ''))
                ]
                
                # 為了節省 API 使用 batch update cell (這裡簡化用逐個 update)
                # 實務上建議轉成 row list 一次 update
                # 這裡為了準確性，逐欄位更新 (如果欄位多建議用 batch_update range)
                for col, val in updates:
                    self.ws_resumes.update_cell(row, col, val)
                
                return True, "儲存成功"
            return False, "找不到履歷資料"
        except Exception as e: return False, str(e)

    def hr_update_status(self, email, status, comment="", interview_date=""):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                row = cell.row
                self.ws_resumes.update_cell(row, 2, status) # Status
                self.ws_resumes.update_cell(row, 16, comment) # Comment
                self.ws_resumes.update_cell(row, 17, str(interview_date)) # Date
                return True, "審核更新成功"
            return False, "錯誤"
        except Exception as e: return False, str(e)

    # --- Logo ---
    def get_logo(self):
        try:
            cell = self.ws_settings.find("logo", in_column=1)
            if cell: return self.ws_settings.cell(cell.row, 2).value
        except: pass
        return None

    def update_logo(self, base64_str):
        try:
            cell = self.ws_settings.find("logo", in_column=1)
            if cell: self.ws_settings.update_cell(cell.row, 2, base64_str)
            else: self.ws_settings.append_row(["logo", base64_str])
            return True
        except: return False

@st.cache_resource
def get_db(): return ResumeDB()

try: sys = get_db()
except: st.error("資料庫連線失敗"); st.stop()

# --- Email 服務 ---
def send_email(to_email, subject, body):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"【模擬寄信】To: {to_email} | Subject: {subject}")
        return True # 模擬成功
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"寄信失敗: {e}")
        return False

# --- UI Components ---
def change_password_ui(email):
    with st.expander("🔑 修改密碼"):
        p1 = st.text_input("新密碼", type="password", key="p1")
        p2 = st.text_input("確認新密碼", type="password", key="p2")
        if st.button("修改"):
            if p1==p2 and p1:
                succ, msg = sys.change_password(email, p1)
                if succ: st.success(msg)
                else: st.error(msg)
            else: st.error("密碼不一致或為空")

def render_logo():
    logo = sys.get_logo()
    if logo:
        if logo.startswith("http"): st.sidebar.image(logo)
        else: 
            if not logo.startswith("data:image"): logo = f"data:image/png;base64,{logo}"
            st.sidebar.image(logo)

# --- Pages ---

def login_page():
    st.markdown("## 📝 人才招募履歷填寫系統")
    c1, c2 = st.columns(2)
    with c1:
        email = st.text_input("Email (帳號)")
        pwd = st.text_input("密碼", type="password")
        if st.button("登入", type="primary"):
            user = sys.verify_login(email, pwd)
            if user:
                st.session_state.user = user
                st.rerun()
            else: st.error("帳號或密碼錯誤")
    with c2:
        st.info("面試者請使用收到邀請信中的 Email 與密碼登入。\n(預設密碼通常為您的 Email)")

def admin_page():
    user = st.session_state.user
    st.header(f"👨‍💼 管理後台 - {user['name']}")
    render_logo()
    change_password_ui(user['email'])
    
    tab1, tab2, tab3 = st.tabs(["📧 發送填寫邀請", "📋 履歷審核", "⚙️ 系統設定"])

    with tab1:
        st.subheader("邀請面試者")
        with st.form("invite"):
            c_name = st.text_input("面試者姓名")
            c_email = st.text_input("面試者 Email")
            if st.form_submit_button("建立帳號並發送通知"):
                if c_name and c_email:
                    succ, msg = sys.create_candidate(user['email'], c_email, c_name)
                    if succ:
                        # 發信
                        link = "https://your-app-url.streamlit.app" # 請換成真實網址
                        subject = f"【面試邀請】請填寫您的履歷資料 - {c_name}"
                        body = f"{c_name} 您好，\n\n誠摯邀請您參加面試。\n請點擊以下連結登入系統填寫履歷：\n{link}\n\n登入帳號：{c_email}\n預設密碼：{c_email}\n\n填寫完畢請按「送出審核」。"
                        send_email(c_email, subject, body)
                        st.success(f"已建立帳號並發送通知給 {c_email}")
                    else: st.error(msg)
                else: st.error("欄位不可為空")

    with tab2:
        st.subheader("履歷審核列表")
        df = sys.get_df("resumes")
        if not df.empty:
            # 篩選已送審 (Submitted) 或已核可但需查看的
            # 管理員可以看到所有，或者只看自己邀請的? 這裡做成看全部
            submitted = df[df['status'].isin(['Submitted', 'Approved', 'Returned'])].copy()
            
            if not submitted.empty:
                st.dataframe(submitted[['status', 'name_cn', 'email', 'updated_at' if 'updated_at' in submitted else 'status']])
                
                # 審核區
                selected_email = st.selectbox("選擇要審閱的候選人", submitted['email'].unique())
                if selected_email:
                    target = df[df['email'] == selected_email].iloc[0]
                    st.divider()
                    st.markdown(f"### 📄 {target['name_cn']} ({target['name_en']}) 的履歷")
                    
                    c1, c2 = st.columns(2)
                    c1.write(f"**電話**: {target['phone']}")
                    c1.write(f"**學歷**: {target['education_school']} / {target['education_major']}")
                    c2.write(f"**最近工作**: {target['experience_company']} ({target['experience_title']})")
                    c2.write(f"**技能**: {target['skills']}")
                    
                    st.text_area("自我介紹", value=target['self_intro'], disabled=True)
                    
                    st.markdown("---")
                    st.write("#### 📝 審核操作")
                    comment = st.text_input("評語 / 退件原因 / 面試地點", value=target['hr_comment'])
                    
                    c_ok, c_no = st.columns(2)
                    if c_ok.button("✅ 審核通過 (安排面試)"):
                        interview_date = date.today() # 或是讓主管選日期
                        sys.hr_update_status(selected_email, "Approved", comment, interview_date)
                        send_email(selected_email, "【通知】履歷審核通過", f"恭喜，您的履歷已通過。\nHR 留言：{comment}")
                        st.success("已核准並通知"); time.sleep(1); st.rerun()
                        
                    if c_no.button("↩️ 退件 (要求修改)"):
                        sys.hr_update_status(selected_email, "Returned", comment)
                        send_email(selected_email, "【通知】履歷需補件/修改", f"您的履歷被退回。\n原因：{comment}\n請修正後重新送出。")
                        st.warning("已退件"); time.sleep(1); st.rerun()

            else: st.info("目前無待審履歷")
        else: st.info("無資料")

    with tab3:
        st.subheader("系統設定")
        up_logo = st.file_uploader("上傳 Logo", type=['png', 'jpg'])
        if up_logo and st.button("更新 Logo"):
            b64 = base64.b64encode(up_logo.getvalue()).decode()
            sys.update_logo(f"data:image/png;base64,{b64}")
            st.success("更新成功"); st.rerun()

def candidate_page():
    user = st.session_state.user
    st.header(f"📝 履歷填寫 - {user['name']}")
    render_logo()
    change_password_ui(user['email'])
    
    # 讀取目前資料
    df = sys.get_df("resumes")
    my_resume = df[df['email'] == user['email']].iloc[0]
    
    status = my_resume['status']
    
    # 狀態提示
    if status == "Approved":
        st.balloons()
        st.success(f"🎉 恭喜！您的履歷已審核通過。")
        st.info(f"HR 訊息: {my_resume['hr_comment']}")
        return # 結束，不顯示表單
        
    elif status == "Submitted":
        st.info("⏳ 履歷已送出，正在等待 HR 審核中，目前無法修改。")
        st.write("若需修改，請聯繫 HR 退回您的履歷。")
        
        # 唯讀顯示
        with st.expander("查看已送出資料"):
            st.json(my_resume.to_dict())
        return

    elif status == "Returned":
        st.error(f"⚠️ 您的履歷被退回。原因：{my_resume['hr_comment']}")
        st.write("請依照指示修改後，重新送出。")

    # --- 填寫表單 (New / Draft / Returned) ---
    with st.form("resume_form"):
        st.subheader("基本資料")
        c1, c2 = st.columns(2)
        n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'])
        n_en = c2.text_input("英文姓名", value=my_resume['name_en'])
        c3, c4 = st.columns(2)
        phone = c3.text_input("聯絡電話", value=my_resume['phone'])
        dob = c4.date_input("出生年月日", value=pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1990,1,1))
        addr = st.text_input("通訊地址", value=my_resume['address'])
        
        st.subheader("學歷")
        e1, e2, e3 = st.columns(3)
        edu_sch = e1.text_input("學校名稱", value=my_resume['education_school'])
        edu_maj = e2.text_input("科系", value=my_resume['education_major'])
        edu_deg = e3.selectbox("學位", ["學士", "碩士", "博士", "其他"], index=["學士","碩士","博士","其他"].index(my_resume['education_degree']) if my_resume['education_degree'] in ["學士","碩士","博士","其他"] else 0)
        
        st.subheader("最近一份工作經歷")
        w1, w2, w3 = st.columns([2, 2, 1])
        exp_co = w1.text_input("公司名稱", value=my_resume['experience_company'])
        exp_ti = w2.text_input("職稱", value=my_resume['experience_title'])
        exp_yr = w3.number_input("年資 (年)", value=float(my_resume['experience_years']) if my_resume['experience_years'] else 0.0)
        
        st.subheader("專業技能與自傳")
        skills = st.text_area("專業技能 (列點式)", value=my_resume['skills'], height=100)
        intro = st.text_area("自我介紹 / 工作成就", value=my_resume['self_intro'], height=150)
        
        # 動作按鈕
        col_s, col_d = st.columns(2)
        
        # 收集資料
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': dob, 'address': addr,
            'edu_school': edu_sch, 'edu_major': edu_maj, 'edu_degree': edu_deg,
            'exp_co': exp_co, 'exp_title': exp_ti, 'exp_years': exp_yr,
            'skills': skills, 'self_intro': intro
        }

        if col_s.form_submit_button("💾 暫存 (Save Draft)"):
            succ, msg = sys.save_resume(user['email'], form_data, status="Draft")
            if succ: st.success("已暫存，HR 不會看到。"); time.sleep(1); st.rerun()
            else: st.error(msg)
            
        if col_d.form_submit_button("🚀 送出審核 (Submit)"):
            # 簡單防呆
            if not n_cn or not phone:
                st.error("姓名與電話為必填！")
            else:
                succ, msg = sys.save_resume(user['email'], form_data, status="Submitted")
                if succ:
                    # 通知 HR
                    hr_email = user.get('creator', '')
                    if hr_email:
                        send_email(hr_email, f"【履歷送審】{n_cn} 已提交履歷", "請登入系統進行審閱。")
                    st.success("已送出！請靜候通知。"); time.sleep(1); st.rerun()
                else: st.error(msg)

# --- 主程式入口 ---
if 'user' not in st.session_state: st.session_state.user = None

if st.session_state.user is None:
    login_page()
else:
    if st.session_state.user['role'] == 'admin':
        admin_page()
    else:
        candidate_page()