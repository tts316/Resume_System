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
st.set_page_config(page_title="聯成電腦 - 人才招募系統", layout="wide", page_icon="📝")

# Logo URL
LOGO_URL = "https://www.lccnet.com.tw/img/logo.png"

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

    # --- [修正版] 讀取資料函式 (加入欄位清洗功能) ---
    def get_df(self, table_name):
        defaults = {
            "users": ["email", "password", "name", "role", "creator_email", "created_at"],
            "resumes": ["email", "status", "name_cn", "name_en", "phone", "address", "dob", "education_school", "education_major", "education_degree", "experience_company", "experience_title", "experience_years", "skills", "self_intro", "hr_comment", "interview_date", "resume_type", "branch_location", "shift_avail"],
            "system_settings": ["key", "value"]
        }
        
        ws = self.ws_users if table_name == "users" else (self.ws_resumes if table_name == "resumes" else self.ws_settings)
        
        try:
            data = ws.get_all_records()
            df = pd.DataFrame(data)
            
            # 【關鍵修正】：如果資料表不是空的，強制把欄位名稱轉為「小寫」並「去除空白」
            if not df.empty:
                df.columns = df.columns.astype(str).str.strip().str.lower()

            # 檢查關鍵欄位是否存在
            check_col = defaults[table_name][0]
            
            # 如果欄位還是對不上，回傳空表
            if check_col not in df.columns:
                return pd.DataFrame(columns=defaults[table_name])
            
            return df
        except: 
            return pd.DataFrame(columns=defaults.get(table_name, []))
    def verify_login(self, email, password):
        try:
            cell = self.ws_users.find(email, in_column=1)
            if cell:
                row = self.ws_users.row_values(cell.row)
                if str(row[1]) == str(password):
                    return {"email": row[0], "name": row[2], "role": row[3], "creator": row[4] if len(row)>4 else ""}
            return None
        except: return None

    def create_candidate(self, hr_email, candidate_email, candidate_name, r_type):
        try:
            if self.ws_users.find(candidate_email, in_column=1):
                return False, "此 Email 已存在"
            self.ws_users.append_row([candidate_email, candidate_email, candidate_name, "candidate", hr_email, str(date.today())])
            row_data = [candidate_email, "New", candidate_name] + [""] * 15
            row_data.append(r_type); row_data.append(""); row_data.append("")
            self.ws_resumes.append_row(row_data)
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

    def save_resume(self, email, data_dict, status="Draft"):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                row = cell.row
                updates = [
                    (2, status), (3, data_dict.get('name_cn', '')), (4, data_dict.get('name_en', '')),
                    (5, data_dict.get('phone', '')), (6, data_dict.get('address', '')), (7, str(data_dict.get('dob', ''))),
                    (8, data_dict.get('edu_school', '')), (9, data_dict.get('edu_major', '')), (10, data_dict.get('edu_degree', '')),
                    (11, data_dict.get('exp_co', '')), (12, data_dict.get('exp_title', '')), (13, str(data_dict.get('exp_years', 0))),
                    (14, data_dict.get('skills', '')), (15, data_dict.get('self_intro', ''))
                ]
                for col, val in updates: self.ws_resumes.update_cell(row, col, val)
                if 'branch_location' in data_dict: self.ws_resumes.update_cell(row, 20, data_dict['branch_location'])
                if 'shift_avail' in data_dict: self.ws_resumes.update_cell(row, 21, data_dict['shift_avail'])
                return True, "儲存成功"
            return False, "找不到資料"
        except Exception as e: return False, str(e)

    def hr_update_status(self, email, status, comment="", interview_date=""):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                row = cell.row
                self.ws_resumes.update_cell(row, 2, status)
                self.ws_resumes.update_cell(row, 16, comment)
                self.ws_resumes.update_cell(row, 17, str(interview_date))
                return True, "更新成功"
            return False, "錯誤"
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
except: st.error("連線失敗，請檢查 secrets.toml"); st.stop()

# --- Email 服務 (從 Secrets 讀取) ---
def send_email(to_email, subject, body):
    # 讀取 Secrets
    try:
        email_config = st.secrets["email"]
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = email_config["sender_email"]
        sender_password = email_config["sender_password"]
    except:
        st.warning("⚠️ 尚未設定 Email Secrets，目前為模擬發送模式。")
        print(f"【模擬寄信】To: {to_email}")
        return True

    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"寄信失敗: {e}")
        return False

# --- UI ---
def change_password_ui(email):
    with st.expander("🔑 修改密碼"):
        p1 = st.text_input("新密碼", type="password", key="p1")
        p2 = st.text_input("確認新密碼", type="password", key="p2")
        if st.button("修改"):
            if p1==p2 and p1:
                if sys.change_password(email, p1): st.success("成功")
                else: st.error("失敗")
            else: st.error("密碼不一致")

def render_logo():
    try:
        raw_logo = sys.get_logo()
        logo = str(raw_logo).strip() if raw_logo else None
        if logo and len(logo) > 10:
            if logo.startswith("http"): st.sidebar.image(logo, use_container_width=True)
            elif logo.startswith("data:image"): st.sidebar.image(logo, use_container_width=True)
            else: st.sidebar.image(f"data:image/png;base64,{logo}", use_container_width=True)
        else: st.sidebar.image(LOGO_URL, use_container_width=True)
    except: st.sidebar.image(LOGO_URL, use_container_width=True)

# --- Pages ---

def login_page():
    st.markdown("## 📝 聯成電腦 - 人才招募系統")
    c1, c2 = st.columns(2)
    with c1:
        email = st.text_input("Email")
        pwd = st.text_input("密碼", type="password")
        if st.button("登入", type="primary"):
            user = sys.verify_login(email, pwd)
            if user:
                st.session_state.user = user
                st.rerun()
            else: st.error("錯誤")
    with c2: st.info("預設密碼通常為您的 Email")

def admin_page():
    user = st.session_state.user
    st.header(f"👨‍💼 管理後台 - {user['name']}")
    render_logo()
    change_password_ui(user['email'])
    
    tab1, tab2, tab3 = st.tabs(["📧 發送邀請", "📋 履歷審核", "⚙️ 設定"])

    with tab1:
        st.subheader("邀請面試者")
        with st.form("invite"):
            c_name = st.text_input("姓名")
            c_email = st.text_input("Email")
            r_type = st.radio("履歷類型", ["總公司 (HQ)", "分公司 (Branch)"], horizontal=True)
            
            if st.form_submit_button("建立並發送"):
                if c_name and c_email:
                    type_code = "Branch" if "分公司" in r_type else "HQ"
                    
                    # 檢查 Email 是否已存在
                    df_users = sys.get_df("users")
                    if not df_users.empty and c_email in df_users['email'].values:
                        st.error("此 Email 已經存在，請勿重複發送。")
                    else:
                        succ, msg = sys.create_candidate(user['email'], c_email, c_name, type_code)
                        if succ:
                            # 讀取 Secrets 中的 APP 網址
                            try: app_link = st.secrets["email"]["app_url"]
                            except: app_link = "https://share.streamlit.io/" # 預設值

                            subj = f"【聯成電腦面試邀請】{c_name} 您好"
                            body = f"""{c_name} 您好，

誠摯邀請您參加聯成電腦面試。
請點擊以下連結，登入系統填寫您的履歷資料：

👉 登入網址：{app_link}

---------------------------
登入資訊：
帳號：{c_email}
密碼：{c_email} (預設密碼與帳號相同)
---------------------------

填寫完畢後，請務必點擊「送出審核」按鈕。
謝謝您！
"""
                            if send_email(c_email, subj, body):
                                st.success(f"✅ 已成功建立帳號，並發送 Email 給 {c_name}")
                            else:
                                st.warning("帳號已建立，但 Email 發送失敗，請檢查系統設定。")
                        else: st.error(msg)
                else: st.error("欄位必填")

    with tab2:
        st.subheader("列表")
        df = sys.get_df("resumes")
        if not df.empty:
            cols_show = ['status', 'name_cn', 'email', 'resume_type']
            if 'resume_type' not in df.columns: df['resume_type'] = "HQ"
            submitted = df[df['status'].isin(['Submitted', 'Approved', 'Returned'])].copy()
            if not submitted.empty:
                st.dataframe(submitted[cols_show])
                sel_email = st.selectbox("選擇候選人", submitted['email'].unique())
                if sel_email:
                    target = df[df['email'] == sel_email].iloc[0]
                    st.divider()
                    rtype_badge = "🏢 總公司" if target.get('resume_type') == "HQ" else "🏪 分公司"
                    st.markdown(f"### {rtype_badge} - {target['name_cn']}")
                    c1, c2 = st.columns(2)
                    c1.write(f"電話: {target['phone']}")
                    c1.write(f"學歷: {target['education_school']}")
                    if target.get('resume_type') == 'Branch':
                        st.info(f"📍 志願地點: {target.get('branch_location', '未填')}")
                        st.info(f"🕒 輪班意願: {target.get('shift_avail', '未填')}")
                    st.text_area("自傳", value=target['self_intro'], disabled=True)
                    cmt = st.text_input("評語", value=target['hr_comment'])
                    c_ok, c_no = st.columns(2)
                    if c_ok.button("✅ 核准"):
                        sys.hr_update_status(sel_email, "Approved", cmt, date.today())
                        send_email(sel_email, "【聯成電腦】履歷審核通過", f"恭喜，您的履歷已通過審核。\nHR 留言：{cmt}\n我們將盡快聯繫您安排面試時間。")
                        st.success("OK"); time.sleep(1); st.rerun()
                    if c_no.button("↩️ 退件"):
                        sys.hr_update_status(sel_email, "Returned", cmt)
                        send_email(sel_email, "【聯成電腦】履歷需修改通知", f"您的履歷被退回。\n原因：{cmt}\n\n請登入系統修正後，重新送出審核。")
                        st.warning("退回"); time.sleep(1); st.rerun()
            else: st.info("無待審")

    with tab3:
        st.write("設定 Logo (建議使用小圖)")
        up = st.file_uploader("Logo", type=['png','jpg'])
        if up and st.button("更新 Logo"):
            b64 = base64.b64encode(up.getvalue()).decode()
            sys.update_logo(f"data:image/png;base64,{b64}")
            st.success("OK"); st.rerun()

def candidate_page():
    user = st.session_state.user
    st.header(f"📝 履歷填寫 - {user['name']}")
    render_logo()
    change_password_ui(user['email'])
    
    df = sys.get_df("resumes")
    my_resume = df[df['email'] == user['email']].iloc[0]
    status = my_resume['status']
    r_type = my_resume.get('resume_type', 'HQ') 

    if status == "Approved":
        st.balloons(); st.success("已錄取"); return
    elif status == "Submitted":
        st.info("已送出審核"); return
    elif status == "Returned":
        st.error(f"被退回：{my_resume['hr_comment']}")

    with st.form("resume"):
        st.caption(f"履歷版本：{'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'}")
        c1, c2 = st.columns(2)
        n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'])
        n_en = c2.text_input("英文姓名", value=my_resume['name_en'])
        c3, c4 = st.columns(2)
        phone = c3.text_input("電話", value=my_resume['phone'])
        dob_val = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
        dob = c4.date_input("生日", value=dob_val)
        addr = st.text_input("地址", value=my_resume['address'])
        
        st.subheader("學經歷")
        e1, e2, e3 = st.columns(3)
        esch = e1.text_input("學校", value=my_resume['education_school'])
        emaj = e2.text_input("科系", value=my_resume['education_major'])
        edeg = e3.selectbox("學位", ["學士", "碩士", "博士"], index=0)
        
        w1, w2, w3 = st.columns([2,2,1])
        eco = w1.text_input("前公司", value=my_resume['experience_company'])
        eti = w2.text_input("職稱", value=my_resume['experience_title'])
        eyr = w3.number_input("年資", value=float(my_resume['experience_years']) if my_resume['experience_years'] else 0.0)

        loc_pref = []
        shift_yn = ""
        if r_type == "Branch":
            st.markdown("---")
            st.subheader("🏪 分公司專屬調查 (必填)")
            loc_pref = st.multiselect("希望工作地點", ["忠孝", "館前", "士林", "公館", "基隆", "羅東", "其他"], default=str(my_resume.get('branch_location', '')).split(',') if my_resume.get('branch_location') else [])
            c_shift1, c_shift2 = st.columns(2)
            shift_yn = c_shift1.radio("是否可配合輪班？", ["是", "否"], index=0 if my_resume.get('shift_avail')=="是" else 1)
            st.markdown("---")

        st.subheader("技能與自傳")
        skills = st.text_area("技能", value=my_resume['skills'])
        intro = st.text_area("自傳", value=my_resume['self_intro'])
        
        c_s, c_d = st.columns(2)
        form_data = {
            'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': dob, 'address': addr,
            'edu_school': esch, 'edu_major': emaj, 'edu_degree': edeg,
            'exp_co': eco, 'exp_title': eti, 'exp_years': eyr, 'skills': skills, 'self_intro': intro
        }
        if r_type == "Branch":
            form_data['branch_location'] = ",".join(loc_pref)
            form_data['shift_avail'] = shift_yn

        if c_s.form_submit_button("💾 暫存"):
            sys.save_resume(user['email'], form_data, "Draft")
            st.success("已暫存"); time.sleep(1); st.rerun()
            
        if c_d.form_submit_button("🚀 送出"):
            if not n_cn or not phone: st.error("姓名電話必填")
            elif r_type == "Branch" and not loc_pref: st.error("分公司請選擇希望地點")
            else:
                sys.save_resume(user['email'], form_data, "Submitted")
                hr = user.get('creator', '')
                if hr: send_email(hr, f"【履歷送審】{n_cn} 已提交", "請登入系統審閱")
                st.success("已送出"); time.sleep(1); st.rerun()

# --- Entry ---
if 'user' not in st.session_state: st.session_state.user = None
if st.session_state.user is None: login_page()
else:
    if st.session_state.user['role'] == 'admin': admin_page()
    else: candidate_page()


