import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import base64
import smtplib
import io
from email.mime.text import MIMEText
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
                if str(row['password']) == str(password):
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
                # 補足 95 欄 (確保 index 足夠)
                row_data = [email, "New", name] + [""] * 48 + [r_type] + [""] * 45
                self.ws_resumes.append_row(row_data)
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
except: st.error("連線失敗，請檢查 secrets.toml"); st.stop()

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
    except:
        return False

# --- PDF Generation ---
def generate_pdf(data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    elements = []
    
    font_name = 'Helvetica'
    try:
        pdfmetrics.registerFont(TTFont('TaipeiSans', 'TaipeiSansTCBeta-Regular.ttf'))
        font_name = 'TaipeiSans'
    except: pass

    styles = getSampleStyleSheet()
    styleN = ParagraphStyle('Normal', fontName=font_name, fontSize=10, leading=14)
    styleH = ParagraphStyle('Heading1', fontName=font_name, fontSize=18, leading=22, alignment=TA_CENTER)
    
    title = "聯成電腦面試人員履歷表" if data.get('resume_type') != 'Branch' else "聯成電腦 (分公司) 面試人員履歷表"
    elements.append(Paragraph(title, styleH))
    elements.append(Spacer(1, 12))

    tbl_style = TableStyle([
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (0,-1), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 6),
    ])

    # 1. 基本資料
    p_data = [
        ["姓名", f"{data.get('name_cn','')} ({data.get('name_en','')})", "應徵職務", "一般人員"],
        ["Email", data.get('email',''), "電話", f"{data.get('phone','')} / {data.get('home_phone','')}"],
        ["生日", data.get('dob',''), "婚姻/血型", f"{data.get('marital_status','')} / {data.get('blood_type','')}"],
        ["地址", data.get('address',''), "緊急聯絡", f"{data.get('emergency_contact','')} ({data.get('emergency_phone','')})"],
        ["身高/體重", f"{data.get('height','')} cm / {data.get('weight','')} kg", "交通", f"{data.get('commute_method','')} ({data.get('commute_time','')}分)"]
    ]
    t1 = Table(p_data, colWidths=[60, 210, 60, 200])
    t1.setStyle(tbl_style)
    elements.append(t1)
    elements.append(Spacer(1, 10))

    # 2. 學歷
    elements.append(Paragraph("【學歷】", styleN))
    edu_data = [["起訖", "學校名稱", "科系", "學位", "狀態"]]
    for i in range(1, 4):
        s_date = f"{data.get(f'edu_{i}_start','')}~{data.get(f'edu_{i}_end','')}"
        edu_data.append([
            s_date,
            data.get(f'edu_{i}_school',''), 
            data.get(f'edu_{i}_major',''), 
            data.get(f'edu_{i}_degree',''), 
            data.get(f'edu_{i}_state','')
        ])
    t2 = Table(edu_data, colWidths=[100, 150, 130, 80, 70])
    t2.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 10))

    # 3. 經歷
    elements.append(Paragraph("【工作經歷】", styleN))
    exp_data = [["起訖", "公司名稱", "職位", "主管/電話", "薪資", "離職原因"]]
    for i in range(1, 5):
        s_date = f"{data.get(f'exp_{i}_start','')}~{data.get(f'exp_{i}_end','')}"
        boss = f"{data.get(f'exp_{i}_boss','')} ({data.get(f'exp_{i}_phone','')})"
        exp_data.append([
            s_date,
            data.get(f'exp_{i}_co',''), 
            data.get(f'exp_{i}_title',''), 
            boss, 
            data.get(f'exp_{i}_salary',''), 
            data.get(f'exp_{i}_reason','')
        ])
    t3 = Table(exp_data, colWidths=[80, 100, 80, 100, 50, 120])
    t3.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), font_name),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
    ]))
    elements.append(t3)
    elements.append(Spacer(1, 10))

    # 4. 其他資訊
    other_data = [
        ["應徵管道", data.get('source',''), "任職親友", data.get('relative_name','')],
        ["補教經驗", data.get('teach_exp',''), "出國史", data.get('travel_history','')],
        ["兵役", data.get('military_status',''), "慢性病", data.get('chronic_disease','')],
        ["獨力扶養", data.get('family_support',''), "獨力負擔", data.get('family_debt','')]
    ]
    t4 = Table(other_data, colWidths=[70, 195, 70, 195])
    t4.setStyle(tbl_style)
    elements.append(t4)
    elements.append(Spacer(1, 10))

    if data.get('resume_type') == 'Branch':
        elements.append(Paragraph("【分公司排班意願調查】", styleN))
        br_data = [
            ["希望區域", data.get('branch_region','')],
            ["希望分校", data.get('branch_location','')],
            ["配合輪調", data.get('accept_rotation','')],
            ["配合輪班", data.get('shift_avail','')],
            ["國定假日輪值", data.get('holiday_shift','')],
            ["早晚輪班(9-18/14-22)", data.get('rotate_shift','')],
            ["家人同意輪班", data.get('family_support_shift','')],
            ["經濟/扶養需求", f"扶養: {data.get('care_dependent','')} / 負擔: {data.get('financial_burden','')}"]
        ]
        t5 = Table(br_data, colWidths=[150, 380])
        t5.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), font_name),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('BACKGROUND', (0,0), (0,-1), colors.lightgrey),
        ]))
        elements.append(t5)
        elements.append(Spacer(1, 10))

    elements.append(Paragraph("【專業技能與自傳】", styleN))
    elements.append(Paragraph(f"技能：{data.get('skills','')}", styleN))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph(f"自傳：{data.get('self_intro','')}", styleN))
    elements.append(Spacer(1, 20))

    elements.append(Paragraph("_" * 80, styleN))
    elements.append(Spacer(1, 10))
    sign_text = "本人所填資料均屬事實，若有不實，願接受免職處分。     應徵人員親簽：______________________   日期：_____/_____/_____"
    elements.append(Paragraph(sign_text, styleN))

    try:
        qr = PDFImage("qrcode.png", width=60, height=60)
        elements.append(Spacer(1, 10))
        elements.append(qr)
    except: pass

    doc.build(elements)
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
        df_users = sys.get_df("users")
        
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
                        
                        if row['status'] == "Approved":
                            pdf_data = generate_pdf(row.to_dict())
                            st.download_button("📥 下載完整 PDF", pdf_data, f"{row['name_cn']}_履歷.pdf", "application/pdf")
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
                        st.text_area("自傳", value=row['self_intro'], disabled=True, height=150)

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
                                    send_email(row['email'], "【聯成電腦】面試通知", body)
                                    st.success("已核准並發送通知信！"); time.sleep(2); st.rerun()

                            if c_no.form_submit_button("↩️ 退件 (通知修改)"):
                                if not hr_comment:
                                    st.error("請填寫退件原因")
                                else:
                                    details = {'hr_comment': hr_comment}
                                    sys.hr_update_status(row['email'], "Returned", details)
                                    send_email(row['email'], "【聯成電腦】履歷需修改", f"您的履歷被退回。\n原因：{hr_comment}\n請登入修改後重送。")
                                    st.warning("已退件通知"); time.sleep(2); st.rerun()

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
            st.caption("基本資料")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名", value=my_resume['name_cn'], key='name_cn')
            n_en = c2.text_input("英文姓名", value=my_resume['name_en'], key='name_en')
            c3.text_input("身高(cm)", value=my_resume.get('height',''), key='height')
            c4.text_input("體重(kg)", value=my_resume.get('weight',''), key='weight')
            
            c5, c6, c7 = st.columns([2, 1, 1])
            phone = c5.text_input("手機", value=my_resume['phone'], key='phone')
            c6.text_input("市話 (H)", value=my_resume.get('home_phone',''), key='home_phone')
            
            m_val = my_resume.get('marital_status', '未婚')
            m_idx = ["未婚", "已婚"].index(m_val) if m_val in ["未婚", "已婚"] else 0
            c7.selectbox("婚姻", ["未婚", "已婚"], index=m_idx, key='marital_status')
            
            try: dval = pd.to_datetime(my_resume['dob']) if my_resume['dob'] else date(1995,1,1)
            except: dval = date(1995,1,1)
            dob = c1.date_input("生日", value=dval, min_value=date(1900, 1, 1), key='dob')
            addr = st.text_input("通訊地址", value=my_resume['address'], key='address')
            
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
                st.session_state[f'edu_{i}_school'] = rc1.text_input(f"學校 {i}", value=my_resume.get(f'edu_{i}_school',''), key=f'edu_{i}_school_in')
                st.session_state[f'edu_{i}_major'] = rc2.text_input(f"科系 {i}", value=my_resume.get(f'edu_{i}_major',''), key=f'edu_{i}_major_in')
                
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
