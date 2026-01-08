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

# Logo URL (預設)
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

    # [修正版] 更強壯的讀取函式 (改用 get_all_values)
    def get_df(self, table_name):
        ws = self.ws_users if table_name == "users" else (self.ws_resumes if table_name == "resumes" else self.ws_settings)
        
        try:
            # 改用 get_all_values (讀取原始資料列表)
            data = ws.get_all_values()
            
            # 如果完全沒資料，或只有標題列
            if len(data) < 2:
                # 嘗試回傳空 DataFrame，但保留預設標題 (避免後續報錯)
                if len(data) == 1:
                     df = pd.DataFrame(columns=data[0])
                     # 清洗標題
                     df.columns = df.columns.astype(str).str.strip().str.lower()
                     return df
                return pd.DataFrame()

            # 將第一列設為標題
            headers = data.pop(0)
            df = pd.DataFrame(data, columns=headers)
            
            # 強制清洗標題 (轉小寫、去空白)
            df.columns = df.columns.astype(str).str.strip().str.lower()
            
            return df
        except Exception as e:
            # print(f"讀取錯誤: {e}") # 除錯用
            return pd.DataFrame()
            
    def verify_login(self, email, password):
        try:
            df = self.get_df("users")
            if df.empty: return None
            
            user = df[df['email'].astype(str).str.strip().str.lower() == str(email).strip().lower()]
            if not user.empty:
                row = user.iloc[0]
                if str(row['password']) == str(password):
                    return {
                        "email": row['email'], 
                        "name": row['name'], 
                        "role": row['role'], 
                        "creator": row.get('creator_email', '')
                    }
            return None
        except: return None

    def create_candidate(self, hr_email, candidate_email, candidate_name, r_type):
        try:
            df = self.get_df("users")
            if not df.empty and str(candidate_email) in df['email'].astype(str).values:
                return False, "此 Email 已存在"
            
            self.ws_users.append_row([candidate_email, candidate_email, candidate_name, "candidate", hr_email, str(date.today())])
            
            row_data = [candidate_email, "New", candidate_name] + [""] * 14
            row_data.append("") 
            row_data.append(r_type)
            row_data.append("") 
            row_data.append("") 
            
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
                self.ws_resumes.update_cell(row, 2, status)
                self.ws_resumes.update_cell(row, 3, data_dict.get('name_cn', ''))
                self.ws_resumes.update_cell(row, 4, data_dict.get('name_en', ''))
                self.ws_resumes.update_cell(row, 5, data_dict.get('phone', ''))
                self.ws_resumes.update_cell(row, 6, data_dict.get('address', ''))
                self.ws_resumes.update_cell(row, 7, str(data_dict.get('dob', '')))
                self.ws_resumes.update_cell(row, 8, data_dict.get('edu_school', ''))
                self.ws_resumes.update_cell(row, 9, data_dict.get('edu_major', ''))
                self.ws_resumes.update_cell(row, 10, data_dict.get('edu_degree', ''))
                self.ws_resumes.update_cell(row, 11, data_dict.get('exp_co', ''))
                self.ws_resumes.update_cell(row, 12, data_dict.get('exp_title', ''))
                self.ws_resumes.update_cell(row, 13, str(data_dict.get('exp_years', 0)))
                self.ws_resumes.update_cell(row, 14, data_dict.get('skills', ''))
                self.ws_resumes.update_cell(row, 15, data_dict.get('self_intro', ''))
                
                if 'branch_location' in data_dict:
                    self.ws_resumes.update_cell(row, 20, data_dict['branch_location'])
                if 'shift_avail' in data_dict:
                    self.ws_resumes.update_cell(row, 21, data_dict['shift_avail'])

                return True, "儲存成功"
            return False, "找不到資料庫紀錄"
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

# --- Email ---
def send_email(to_email, subject, body):
    try:
        email_config = st.secrets["email"]
        sender_email = email_config["sender_email"]
        sender_password = email_config["sender_password"]
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
    except:
        st.warning("⚠️ 模擬發信模式 (未設定 Secrets)")
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
        return False

# --- UI Components ---
def render_sidebar_info(user):
    """
    統一的側邊欄資訊，包含 Logo、使用者資訊、登出按鈕、修改密碼
    """
    with st.sidebar:
        # 1. Logo
        try:
            raw_logo = sys.get_logo()
            logo = str(raw_logo).strip() if raw_logo else None
            if logo and len(logo) > 10:
                if logo.startswith("http"): st.image(logo, use_container_width=True)
                elif logo.startswith("data:image"): st.image(logo, use_container_width=True)
                else: st.image(f"data:image/png;base64,{logo}", use_container_width=True)
            else: st.image(LOGO_URL, use_container_width=True)
        except: st.image(LOGO_URL, use_container_width=True)
        
        st.divider()

        # 2. 使用者資訊
        role_label = "管理員 (HR/PM)" if user['role'] == 'admin' else "面試者"
        st.write(f"👋 **{user['name']}**")
        st.caption(f"身分: {role_label}")
        st.caption(f"帳號: {user['email']}")

        # 3. 登出按鈕
        if st.button("🚪 登出", type="primary", use_container_width=True):
            st.session_state.user = None
            st.rerun()

        st.divider()

        # 4. 修改密碼
        with st.expander("🔑 修改密碼"):
            p1 = st.text_input("新密碼", type="password", key="p1")
            p2 = st.text_input("確認新密碼", type="password", key="p2")
            if st.button("確認修改"):
                if p1==p2 and p1:
                    if sys.change_password(user['email'], p1): 
                        st.success("密碼已更新，下次請用新密碼登入")
                    else: st.error("失敗")
                else: st.error("密碼不一致")

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
            else: st.error("帳號或密碼錯誤")
    with c2: st.info("預設密碼通常為您的 Email")

def admin_page():
    user = st.session_state.user
    render_sidebar_info(user) # 呼叫側邊欄元件
    
    st.header(f"👨‍💼 管理後台")
    
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
                    
                    df_users = sys.get_df("users")
                    if not df_users.empty and str(c_email) in df_users['email'].astype(str).values:
                        st.error("此 Email 已經存在，請勿重複發送。")
                    else:
                        succ, msg = sys.create_candidate(user['email'], c_email, c_name, type_code)
                        if succ:
                            try: app_link = st.secrets["email"]["app_url"]
                            except: app_link = "https://share.streamlit.io/" 

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
                        send_email(sel_email, "【聯成電腦】履歷審核通過", f"恭喜，您的履歷已通過審核。\nHR 留言：{cmt}")
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
    render_sidebar_info(user) # 呼叫側邊欄元件
    
    st.header(f"📝 履歷填寫")
    
    df = sys.get_df("resumes")
    if df.empty or 'email' not in df.columns:
        st.error("系統資料異常，請聯繫 HR (Resumes Table Empty)")
        return

    my_resume_df = df[df['email'].astype(str).str.strip().str.lower() == str(user['email']).strip().lower()]

    if my_resume_df.empty:
        st.error(f"⚠️ 找不到您的履歷檔案 ({user['email']})。")
        st.info("可能是您的資料已被移除，請聯繫 HR 重新發送邀請。")
        return

    my_resume = my_resume_df.iloc[0]
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
        
        try: dval = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
        except: dval = date(1995,1,1)
        dob = c4.date_input("生日", value=dval)
        
        addr = st.text_input("地址", value=my_resume['address'])
        
        st.subheader("學經歷")
        e1, e2, e3 = st.columns(3)
        esch = e1.text_input("學校", value=my_resume['education_school'])
        emaj = e2.text_input("科系", value=my_resume['education_major'])
        edeg = e3.selectbox("學位", ["學士", "碩士", "博士"], index=0)
        
        w1, w2, w3 = st.columns([2,2,1])
        eco = w1.text_input("前公司", value=my_resume['experience_company'])
        eti = w2.text_input("職稱", value=my_resume['experience_title'])
        try: y_val = float(my_resume['experience_years'])
        except: y_val = 0.0
        eyr = w3.number_input("年資", value=y_val)

        loc_pref = []
        shift_yn = ""
        if r_type == "Branch":
            st.markdown("---")
            st.subheader("🏪 分公司專屬調查 (必填)")
            curr_loc = str(my_resume.get('branch_location', ''))
            d_loc = curr_loc.split(',') if curr_loc else []
            valid_opts = ["忠孝", "館前", "士林", "公館", "基隆", "羅東", "其他"]
            d_loc = [x for x in d_loc if x in valid_opts]
            
            loc_pref = st.multiselect("希望工作地點", valid_opts, default=d_loc)
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

