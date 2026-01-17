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

# PDF ReportLab Imports
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

# Logo URL (預設)
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
                "edu_1_school", "edu_1_major", "edu_1_degree", "edu_1_state",
                "edu_2_school", "edu_2_major", "edu_2_degree", "edu_2_state",
                "edu_3_school", "edu_3_major", "edu_3_degree", "edu_3_state",
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
            df = self.get_df("users")
            if not df.empty and str(email) in df['email'].astype(str).values: return False, "Email 已存在"
            self.ws_users.append_row([email, email, name, role, creator_email, str(date.today())])
            if role == "candidate":
                # 補足 89 欄
                row_data = [email, "New", name] + [""] * 48 + [r_type] + [""] * 37
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
            # 1. 找到該使用者的列號
            cell = self.ws_resumes.find(email, in_column=1)
            if cell:
                row_idx = cell.row
                # 2. 取得標題列（第一列）來確定欄位順序
                headers = [h.strip().lower() for h in self.ws_resumes.row_values(1)]
                
                # 3. 取得目前該列的所有內容
                current_row_values = self.ws_resumes.row_values(row_idx)
                # 確保長度與標題一致，避免索引錯誤
                if len(current_row_values) < len(headers):
                    current_row_values += [""] * (len(headers) - len(current_row_values))
                
                # 4. 更新狀態
                if 'status' in headers:
                    current_row_values[headers.index('status')] = status
                
                # 5. 將 data 中的資料填入對應的欄位位置
                for key, val in data.items():
                    clean_key = key.lower().strip()
                    if clean_key in headers:
                        col_idx = headers.index(clean_key)
                        # 將日期或特殊物件轉為字串
                        current_row_values[col_idx] = str(val) if val is not None else ""
                
                # 6. 一次性整列寫入回 Google Sheets (這只會消耗 1 次 API 配額)
                # 使用 update 語法，範圍為 A{row}: 到最後一欄
# ... 這是優化後的 save_resume 結尾 ...
                range_label = f"A{row_idx}"
                self.ws_resumes.update(range_label, [current_row_values])
                
                return True, "儲存成功"
            return False, "找不到對應的 Email"
        except Exception as e:
            return False, f"API 寫入錯誤: {str(e)}"

# 確保這一行 (167行) 的 def 開頭跟上面的 def save_resume 完全垂直對齊
    def hr_update_status(self, email, status, details=None):
        try:
            cell = self.ws_resumes.find(email, in_column=1)
            # ... 後續程式碼 ...
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
            data = self.ws_settings.get_all_values()
            for row in data:
                if len(row) >= 2 and row[0].strip().lower() == "logo":
                    return row[1].strip()
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
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email
        
        server.send_message(msg)
        server.quit()
        return True
    except:
        return True 

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

# 1. 基本資料表格修正 (將 target 替換為 data)
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
            raw_logo = sys.get_logo()
            if raw_logo:
                logo_str = str(raw_logo).strip()
                if logo_str.startswith("http"):
                    st.image(logo_str, use_container_width=True)
                elif "base64," in logo_str:
                    st.image(logo_str, use_container_width=True)
                else:
                    st.image(f"data:image/png;base64,{logo_str}", use_container_width=True)
            else:
                st.image(LOGO_URL, use_container_width=True)
        except:
            st.image(LOGO_URL, use_container_width=True)

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
    try:
        raw_logo = sys.get_logo()
        if raw_logo:
            logo_str = str(raw_logo).strip()
            if logo_str.startswith("http"):
                st.image(logo_str, width=200)
            elif "base64," in logo_str:
                st.image(logo_str, width=200)
            else:
                st.image(f"data:image/png;base64,{logo_str}", width=200)
        else:
            st.image(LOGO_URL, width=200)
    except:
        st.image(LOGO_URL, width=200)

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
        df_resumes = sys.get_df("resumes")
        df_users = sys.get_df("users")
        
        if not df_resumes.empty and not df_users.empty:
            my_candidates = df_users[df_users['creator_email'] == user['email']]['email'].tolist()
            submitted = df_resumes[
                (df_resumes['status'].isin(['Submitted', 'Approved', 'Returned'])) & 
                (df_resumes['email'].isin(my_candidates))
            ].copy()
            
            if not submitted.empty:
                st.dataframe(submitted[['status', 'name_cn', 'email', 'resume_type']])
                sel_email = st.selectbox("選擇候選人", submitted['email'].unique())
                if sel_email:
                    target = df_resumes[df_resumes['email'] == sel_email].iloc[0]
                    st.divider()
                    st.markdown(f"### 📄 {target['name_cn']} 履歷表")
                    
                    if target['status'] == "Approved":
                        pdf_data = generate_pdf(target.to_dict())
                        st.download_button("📥 下載完整 PDF", pdf_data, f"{target['name_cn']}_履歷.pdf", "application/pdf")

                    with st.expander("查看履歷詳細內容", expanded=True):
                        st.markdown("**【基本資料】**")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.write(f"**姓名**: {target.get('name_cn','')} ({target.get('name_en', '')})")
                        c2.write(f"**電話**: {target.get('phone','')} / {target.get('home_phone', '')}")
                        c3.write(f"**Email**: {target.get('email','')}")
                        c4.write(f"**生日**: {target.get('dob','')}")
                        
                        # 新增顯示欄位
                        c5, c6, c7, c8 = st.columns(4)
                        c5.write(f"**身高**: {target.get('height','')} cm")
                        c6.write(f"**體重**: {target.get('weight','')} kg")
                        c7.write(f"**血型**: {target.get('blood_type','')}")
                        c8.write(f"**婚姻**: {target.get('marital_status','')}")
                        
                        st.write(f"**通訊地址**: {target.get('address','')}")
                        
                        c9, c10 = st.columns(2)
                        c9.write(f"**緊急聯絡人**: {target.get('emergency_contact','')} ({target.get('emergency_phone','')})")
                        c10.write(f"**交通方式**: {target.get('commute_method','')} (約 {target.get('commute_time','')} 分鐘)")
                        
                        st.markdown("**【學歷】**")
                        for x in range(1, 4):
                            s = target.get(f'edu_{x}_school')
                            if s: 
                                date_range = f"{target.get(f'edu_{x}_start','')} ~ {target.get(f'edu_{x}_end','')}"
                                st.write(f"**{x}. {s}** ({date_range}) | {target.get(f'edu_{x}_major', '')} | {target.get(f'edu_{x}_degree', '')} | {target.get(f'edu_{x}_state', '')}")
             
                        st.markdown("**【工作經歷】**")
                        for x in range(1, 5):
                            # 讀取公司名稱並去掉首尾空白
                            co = str(target.get(f'exp_{x}_co', '')).strip()
                            # 只有當公司名稱不是空值，且不是 "None" 時才顯示該區塊
                            if co and co.lower() != 'none' and co != "":
                                dr = f"{target.get(f'exp_{x}_start','')} ~ {target.get(f'exp_{x}_end','')}"
                                st.markdown(f"**{x}. {co}** ({dr})")
                                st.write(f"- 職稱: {target.get(f'exp_{x}_title','')} | 薪資: {target.get(f'exp_{x}_salary','')}")
                                st.write(f"- 主管: {target.get(f'exp_{x}_boss','')} ({target.get(f'exp_{x}_phone','')}) | 原因: {target.get(f'exp_{x}_reason','')}")
                                st.divider()

                        # [修正] 其他資訊顯示欄位
                    st.markdown("**【其他資訊】**")
                    c_o1, c_o2 = st.columns(2)
                    c_o1.write(f"**應徵管道**: {target.get('source', '')}")
                    c_o2.write(f"**任職親友**: {target.get('relative_name', '')}")
                    
                    c_o3, c_o4, c_o5 = st.columns(3)
                    c_o3.write(f"**補教經驗**: {target.get('teach_exp', '')}")
                    c_o4.write(f"**出國史**: {target.get('travel_history', '')}")
                    c_o5.write(f"**兵役狀況**: {target.get('military_status', '')}")
                    
                    c_o6, c_o7 = st.columns(2)
                    c_o6.write(f"**住院史**: {target.get('hospitalization', '')}")
                    c_o7.write(f"**慢性病**: {target.get('chronic_disease', '')}")
                    
                    c_o8, c_o9 = st.columns(2)
                    c_o8.write(f"**獨力扶養**: {target.get('family_support', '')}")
                    c_o9.write(f"**獨力負擔**: {target.get('family_debt', '')}")
                    
                    st.markdown("**【專業技能與自傳】**")
                    st.write(f"**技能**: {target.get('skills', '')}")
                    st.text_area("自傳全文", value=target.get('self_intro', ''), disabled=True, height=150)

                    st.write("#### 審核操作")
                    # --- 新增日期與時間選單 UI ---
                    c_iv_date, c_iv_time = st.columns(2)
                    
                    # 預設值處理：嘗試讀取現有資料，若無則預設為明天
                    try:
                        existing_dt = str(target.get('interview_time', ''))
                        default_date = datetime.strptime(existing_dt.split(' ')[0], '%Y-%m-%d').date()
                    except:
                        default_date = date.today() + pd.Timedelta(days=1)
                        
                    iv_date = c_iv_date.date_input("📅 選擇面試日期", value=default_date)
                    iv_time_val = c_iv_time.time_input("⏰ 選擇面試時間", value=datetime.strptime("14:30", "%H:%M").time())
                    
                    # 結合成一個整合欄位字串，方便存入 interview_time
                    combined_interview_info = f"{iv_date} {iv_time_val.strftime('%H:%M')}"

                    c_iv2, c_iv3 = st.columns(2)
                    iv_loc = c_iv2.text_input("📍 面試地點", value=target.get('interview_location', ''))
                    iv_dept = c_iv3.text_input("🏢 面試部門", value=target.get('interview_dept', ''))
                    
                    c_iv4 = st.columns(1)[0]
                    iv_man = c_iv4.text_input("👤 面試主管", value=target.get('interview_manager', ''))
                    
                    iv_notes = st.text_area("⚠️ 面試注意事項", value=target.get('interview_notes', ''))
                    cmt = st.text_input("💬 HR 評語/留言", value=target.get('hr_comment', ''))

                    c_ok, c_no = st.columns(2)
                    if c_ok.button("✅ 核准 (發送通知)", key="ok"):
                        # 完整的細節字典
                        details = {
                            'hr_comment': cmt,
                            'interview_date': str(iv_date), # 同步更新 interview_date 欄位 (如有)
                            'interview_time': combined_interview_info, # 整合後的日期時間存入 interview_time
                            'interview_location': iv_loc,
                            'interview_dept': iv_dept,
                            'interview_manager': iv_man,
                            'interview_notes': iv_notes
                        }
                        sys.hr_update_status(sel_email, "Approved", details)
                        
                        # 構建 Email 內容，使用整合後的 combined_interview_info
                        mail_body = f"""您好，您的履歷已通過初步審核。
以下是您的面試資訊：
📅 面試時間：{combined_interview_info}
📍 面試地點：{iv_loc}
🏢 面試部門：{iv_dept}
👤 面試主管：{iv_man}
⚠️ 注意事項：{iv_notes}

HR 留言：{cmt}
請準時參加面試，謝謝。"""
                        
                        send_email(sel_email, "【聯成電腦】面試邀約通知", mail_body)
                        st.success(f"已核准！面試時間設定為：{combined_interview_info}"); time.sleep(1); st.rerun()
            else:
                st.info("目前無您所發送的面試邀請待審核")
        else:
            st.info("無履歷數據")

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
    status = my_resume.get('status', 'New')
    r_type = my_resume.get('resume_type', 'HQ') 

# 這裡判斷狀態
    is_approved = (status == "Approved")

    if is_approved: 
        st.balloons()
        st.success("🎉 恭喜！您的履歷已審核通過。")
        with st.expander("📅 查看面試資訊", expanded=True):
            st.write(f"**面試日期**: {my_resume.get('interview_date','')}")
            st.write(f"**面試時間**: {my_resume.get('interview_time','')}")
            st.write(f"**面試地點**: {my_resume.get('interview_location','')}")
            st.write(f"**注意事項**: {my_resume.get('interview_notes','')}")
        # 注意：這裡不要放 return，讓程式繼續往下跑以顯示履歷內容
    
    # ... 接下來是原有的 if status == "Submitted" 等提示 ...    
    if status == "Submitted":
        st.info("ℹ️ 履歷審核中，若需補充資料可修改後再次送出。")
    elif status == "Returned":
        st.error(f"⚠️ 您的履歷被退回。原因：{my_resume.get('hr_comment', '')}")

    with st.form("resume_form"):
        st.markdown(f"### {'🏢 總公司內勤' if r_type == 'HQ' else '🏪 分公司門市'} 履歷表")
        
        # 1. 基本資料
        with st.container(border=True):
            st.caption("基本資料")
            c1, c2, c3, c4 = st.columns(4)
            n_cn = c1.text_input("中文姓名", value=my_resume.get('name_cn',''), key='name_cn_in', disabled=is_approved)
            n_en = c2.text_input("英文姓名", value=my_resume.get('name_en',''), key='name_en_in', disabled=is_approved)
            height = c3.text_input("身高(cm)", value=my_resume.get('height',''), key='height_in', disabled=is_approved)
            weight = c4.text_input("體重(kg)", value=my_resume.get('weight',''), key='weight_in', disabled=is_approved)
            
            c5, c6, c7 = st.columns([2, 1, 1])
            phone = c5.text_input("手機", value=my_resume.get('phone',''), key='phone_in', disabled=is_approved)
            home_phone = c6.text_input("市話 (H)", value=my_resume.get('home_phone',''), key='home_phone_in', disabled=is_approved)
            
            m_val = my_resume.get('marital_status', '未婚')
            m_opts = ["未婚", "已婚"]
            marital_status = c7.selectbox("婚姻", m_opts, index=m_opts.index(m_val) if m_val in m_opts else 0, key='marital_status_in')
            
            try: dval = pd.to_datetime(my_resume['dob']).date() if my_resume['dob'] else date(1995,1,1)
            except: dval = date(1995,1,1)
            dob = c1.date_input("生日", value=dval, min_value=date(1900, 1, 1), key='dob_in', disabled=is_approved)
            addr = st.text_input("通訊地址", value=my_resume.get('address',''), key='address_in', disabled=is_approved)
            
            c8, c9 = st.columns(2)
            emergency_contact = c8.text_input("緊急聯絡人", value=my_resume.get('emergency_contact',''), key='emergency_contact_in', disabled=is_approved)
            emergency_phone = c9.text_input("緊急聯絡電話", value=my_resume.get('emergency_phone',''), key='emergency_phone_in', disabled=is_approved)
            
            b_val = my_resume.get('blood_type', 'O')
            b_opts = ["O", "A", "B", "AB"]
            blood_type = c3.selectbox("血型", b_opts, index=b_opts.index(b_val) if b_val in b_opts else 0, key="blood_type_in")

        # 2. 學歷
        with st.container(border=True):
            st.caption("學歷 (請填寫最高及次高學歷)")
            for i in range(1, 4):
                st.markdown(f"**學歷 {i}**")
                c_d1, c_d2 = st.columns(2)
                st.text_input(f"入學 (YYYY/MM) {i}", value=my_resume.get(f'edu_{i}_start',''), key=f'edu_{i}_start_in', disabled=is_approved)
                st.text_input(f"畢/肄業 (YYYY/MM) {i}", value=my_resume.get(f'edu_{i}_end',''), key=f'edu_{i}_end_in', disabled=is_approved)

                rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
                st.text_input(f"學校 {i}", value=my_resume.get(f'edu_{i}_school',''), key=f'edu_{i}_school_in', disabled=is_approved)
                st.text_input(f"科系 {i}", value=my_resume.get(f'edu_{i}_major',''), key=f'edu_{i}_major_in', disabled=is_approved)
                
                d_opts = ["學士", "碩士", "博士", "高中/職", "其他"]
                d_curr = my_resume.get(f'edu_{i}_degree', '學士')
                st.selectbox(f"學位 {i}", d_opts, index=d_opts.index(d_curr) if d_curr in d_opts else 0, key=f'edu_{i}_degree_in')
                
                s_curr = my_resume.get(f'edu_{i}_state', '畢業')
                st.radio(f"狀態 {i}", ["畢業", "肄業"], index=1 if s_curr == "肄業" else 0, horizontal=True, key=f'edu_{i}_state_in')
                if i < 3: st.divider()

        # 3. 經歷
        with st.container(border=True):
            st.caption("曾任職公司 (最近4筆)")
            for i in range(1, 5):
                with st.expander(f"經歷 {i}"):
                    c_ym1, c_ym2 = st.columns(2)
                    st.text_input(f"起始年月 (YYYY/MM) {i}", value=my_resume.get(f'exp_{i}_start',''), key=f'exp_{i}_start_in', disabled=is_approved)
                    st.text_input(f"結束年月 (YYYY/MM) {i}", value=my_resume.get(f'exp_{i}_end',''), key=f'exp_{i}_end_in', disabled=is_approved)
                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    st.text_input(f"公司名稱 {i}", value=my_resume.get(f'exp_{i}_co',''), key=f'exp_{i}_co_in', disabled=is_approved)
                    st.text_input(f"職稱 {i}", value=my_resume.get(f'exp_{i}_title',''), key=f'exp_{i}_title_in', disabled=is_approved)
                    st.text_input(f"薪資 {i}", value=my_resume.get(f'exp_{i}_salary',''), key=f'exp_{i}_salary_in', disabled=is_approved)
                    ec4, ec5 = st.columns([2, 2])
                    st.text_input(f"主管姓名/職稱 {i}", value=my_resume.get(f'exp_{i}_boss',''), key=f'exp_{i}_boss_in', disabled=is_approved)
                    st.text_input(f"聯絡電話 {i}", value=my_resume.get(f'exp_{i}_phone',''), key=f'exp_{i}_phone_in', disabled=is_approved)
                    st.text_input(f"離職原因 {i}", value=my_resume.get(f'exp_{i}_reason',''), key=f'exp_{i}_reason_in', disabled=is_approved)

        # 4. 分公司意願區塊
        region = ""; loc_val = ""; rot_val = ""; shift_val = ""
        if r_type == "Branch":
            with st.container(border=True):
                st.caption("🏪 分公司意願調查")
                region = st.selectbox("區域", list(BRANCH_DATA.keys()), key="branch_region_in")
                loc_val = st.selectbox("首選分校", BRANCH_DATA.get(st.session_state.get('branch_region_in', '北一區'), []), key="branch_location_in")
                rot_val = st.radio("配合輪調？", ["是", "否"], key="accept_rotation_in", horizontal=True)
                shift_val = st.radio("配合輪班？", ["是", "否"], key="shift_avail_in", horizontal=True)
                
                c_h1, c_h2 = st.columns(2)
                st.radio("國定假日輪值？", ["可以", "不可以"], key='holiday_shift_in', horizontal=True)
                st.radio("配合輪早晚班？", ["可以", "不可以"], key='rotate_shift_in', horizontal=True)
                c_f1, c_f2 = st.columns(2)
                st.radio("家人同意輪班？", ["同意", "不同意"], key='family_support_shift_in', horizontal=True)
                st.radio("需獨力扶養長幼？", ["需要", "不需要"], key='care_dependent_in', horizontal=True)
                st.radio("需獨力負擔家計？", ["需要", "不需要"], key='financial_burden_in', horizontal=True)

        # 5. 其他資訊與自傳
        with st.container(border=True):
            st.caption("其他資訊與自傳")
            st.text_input("應徵管道", value=my_resume.get('source',''), key='source_in', disabled=is_approved)
            st.text_input("任職親友", value=my_resume.get('relative_name',''), key='relative_name_in', disabled=is_approved)
            
            c_ot1, c_ot2, c_ot3 = st.columns(3)
            with c_ot1: st.radio("補教經驗", ["無", "有"], index=1 if my_resume.get('teach_exp')=="有" else 0, key='teach_exp_in', horizontal=True)
            with c_ot2: st.radio("出國史", ["無", "有"], index=1 if my_resume.get('travel_history')=="有" else 0, key='travel_history_in', horizontal=True)
            with c_ot3: st.radio("兵役狀況", ["未役", "免役", "役畢"], key='military_status_in', horizontal=True)
            
            c_ot4, c_ot5 = st.columns(2)
            with c_ot4: st.radio("近年住院史？", ["無", "有"], index=1 if my_resume.get('hospitalization')=="有" else 0, key='hospitalization_in', horizontal=True)
            with c_ot5: st.radio("慢性病藥控？", ["無", "有"], index=1 if my_resume.get('chronic_disease')=="有" else 0, key='chronic_disease_in', horizontal=True)
            
            c_ot6, c_ot7 = st.columns(2)
            with c_ot6: st.radio("獨力扶養？", ["需要", "不需要"], index=1 if my_resume.get('family_support')=="不需要" else 0, key='family_support_in', horizontal=True)
            with c_ot7: st.radio("獨力負擔？", ["需要", "不需要"], index=1 if my_resume.get('family_debt')=="不需要" else 0, key='family_debt_in', horizontal=True)
            
            c_com1, c_com2 = st.columns(2)
            st.text_input("通勤方式", value=my_resume.get('commute_method',''), key='commute_method_in', disabled=is_approved)
            st.text_input("通勤時間(分)", value=my_resume.get('commute_time',''), key='commute_time_in', disabled=is_approved)
            
            skills = st.text_area("專業技能", value=my_resume.get('skills', ''), height=100, key='skills_in')
            intro = st.text_area("自傳 / 工作成就", value=my_resume.get('self_intro', ''), height=150, key='self_intro_in')

# --- 按鈕區塊修正 ---
        c_s, c_d = st.columns(2)
        
        if is_approved:
            # 如果已核准，顯示一個「停用」的提交按鈕，滿足 Streamlit 語法要求
            c_s.form_submit_button("💾 暫存 (已核准)", disabled=True)
            c_d.form_submit_button("✅ 履歷已核准 (唯讀)", disabled=True)
            save_clicked = False
            submit_clicked = False
        else:
            # 如果未核准，顯示正常的按鈕
            save_clicked = c_s.form_submit_button("💾 暫存")
            submit_clicked = c_d.form_submit_button("🚀 送出")
        
        if save_clicked or submit_clicked:
            # 建立資料字典
            form_data = {
                'name_cn': n_cn, 'name_en': n_en, 'phone': phone, 'dob': str(dob), 'address': addr,
                'height': height, 'weight': weight, 'blood_type': blood_type, 'marital_status': marital_status,
                'emergency_contact': emergency_contact, 'emergency_phone': emergency_phone,
                'home_phone': home_phone, 'skills': skills, 'self_intro': intro
            }
            
            # 動態抓取所有帶 _in 的 widget (edu, exp, 其他資訊)
            for k in st.session_state:
                if isinstance(k, str) and k.endswith("_in"):
                    db_key = k[:-3] 
                    form_data[db_key] = st.session_state[k]
            
            # 分公司欄位特別補強
            if r_type == "Branch":
                form_data.update({
                    'branch_region': st.session_state.get('branch_region_in', ''),
                    'branch_location': st.session_state.get('branch_location_in', ''),
                    'accept_rotation': st.session_state.get('accept_rotation_in', ''),
                    'shift_avail': st.session_state.get('shift_avail_in', '')
                })

            status_now = "Submitted" if submit_clicked else "Draft"
            
            if submit_clicked and (not n_cn or not phone):
                st.error("姓名與電話為必填")
            else:
                success, msg = sys.save_resume(user['email'], form_data, status_now)
                if success:
                    if submit_clicked:
                        hr = user.get('creator', '')
                        if hr: send_email(hr, f"履歷送審: {n_cn}", "面試者已送出履歷，請登入系統審閱。")
                        st.success("履歷已成功送出審核！")
                    else:
                        st.success("草稿已成功暫存！")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"儲存失敗: {msg}")
# --- Entry ---
if 'user' not in st.session_state: st.session_state.user = None
if st.session_state.user is None: login_page()
else:
    if st.session_state.user['role'] in ['admin', 'pm']: admin_page()
    else: candidate_page()








