import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from cryptography.fernet import Fernet
from streamlit_gsheets import GSheetsConnection

# --- 1. SETTINGS & SECRETS ---
TRACKER_URL = st.secrets.get("tracker_url", "")

# --- 2. DEFINE ALL FUNCTIONS FIRST (Crucial for Python) ---

def get_conn():
    return st.connection("gsheets", type=GSheetsConnection)

def get_cipher():
    try:
        key = st.secrets["master_key"]
        return Fernet(key.encode())
    except:
        st.error("Master Key missing in Secrets!")
        return None

def add_to_blacklist(email):
    conn = get_conn()
    try:
        df = conn.read(worksheet="Blacklist")
        new_row = pd.DataFrame([[email, datetime.now().strftime("%Y-%m-%d")]], columns=["Email", "Date"])
        df = pd.concat([df, new_row], ignore_index=True).drop_duplicates()
        conn.update(worksheet="Blacklist", data=df)
    except:
        df = pd.DataFrame([[email, datetime.now().strftime("%Y-%m-%d")]], columns=["Email", "Date"])
        conn.update(worksheet="Blacklist", data=df)

def check_blacklist(email):
    """Checks the Google Form response sheet to see if an email has opted out."""
    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        # 1. Change "Form Responses 1" to the EXACT name of the tab in your Sheet
        df = conn.read(worksheet="Form Responses 1") 
        
        # 2. iloc[:, 1] looks at the second column (Column B) where the email is stored
        # We make everything lowercase so 'Test@Email.com' matches 'test@email.com'
        blacklisted_emails = df.iloc[:, 1].astype(str).str.lower().values 
        
        return email.lower() in blacklisted_emails
    except Exception as e:
        # If the sheet is empty or the tab name is wrong, no one is blacklisted yet
        return False

def save_data():
    cipher = get_cipher()
    conn = get_conn()
    if not cipher or 'clients' not in st.session_state: return
    serializable = {}
    for name, info in st.session_state.clients.items():
        client_copy = info.copy()
        if isinstance(info.get('leads'), pd.DataFrame):
            client_copy['leads'] = info['leads'].to_json()
        serializable[name] = client_copy
    encrypted_blob = cipher.encrypt(json.dumps(serializable).encode()).decode()
    df = pd.DataFrame([["Master_Vault", encrypted_blob]], columns=["Name", "Data"])
    conn.update(worksheet="Clients", data=df)

def load_data():
    cipher = get_cipher()
    conn = get_conn()
    if not cipher: return
    try:
        df = conn.read(worksheet="Clients")
        if df.empty: return
        encrypted_blob = df.iloc[0, 1]
        decrypted_json = cipher.decrypt(encrypted_blob.encode()).decode()
        raw = json.loads(decrypted_json)
        for name, info in raw.items():
            if isinstance(info.get('leads'), str):
                info['leads'] = pd.read_json(info['leads'])
            st.session_state.clients[name] = info
    except: pass

def send_email_logic(client_info, lead, groq_key):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        s_email = lead.get('F_EMAIL')
        
        groq_client = Groq(api_key=groq_key)
        system_msg = "You are a professional assistant. Output ONLY the email body. No conversational filler, no 'Sure!', no sign-offs."
        user_msg = f"Write a 2-sentence outreach email to {s_name} regarding {client_info['desc']}. Mention we found them via {s_source}."

        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=0.3
        )
        ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')

        footer = f"""<br><br><hr/><p style="font-size:11px;color:#666;">Found via: {s_source} | <a href="{TRACKER_URL}?unsubscribe={s_email}">Unsubscribe</a></p>"""
        full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = s_email
        msg['Subject'] = f"Regarding {s_name}"
        msg.attach(MIMEText(full_html, 'html'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. HANDLE ACTIONS ---

# This MUST be here, after add_to_blacklist is defined
if "unsubscribe" in st.query_params:
    email_to_blacklist = st.query_params["unsubscribe"]
    add_to_blacklist(email_to_blacklist)
    st.success(f"Successfully unsubscribed: {email_to_blacklist}")
    st.stop()

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. UI INTERFACE ---
st.set_page_config(page_title="Agency Pro", layout="wide")

with st.sidebar:
    st.title("Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])

if page == "Create Client":
    st.header("Create New Client")
    with st.form("create_form"):
        name = st.text_input("Business Name")
        desc = st.text_area("Description")
        b_email = st.text_input("Sender Email")
        app_pw = st.text_input("App Password", type="password")
        file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        if st.form_submit_button("Submit"):
            if name and file:
                # Basic processing
                try:
                    df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
                    df.columns = [str(c).strip().upper() for c in df.columns]
                    df = df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                    st.session_state.clients[name] = {"name": name, "desc": desc, "email": b_email, "app_pw": app_pw, "leads": df, "send_log": [], "clicks": 0}
                    save_data()
                    st.success("Client Saved!")
                except Exception as e: st.error(e)

elif page == "Client Vault":
    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]
        with st.expander(f"🏢 {c_name}"):
            if st.button("🚀 Execute Batch Send", key=f"sb_{c_name}"):
                if not st.session_state.get('g_key'):
                    st.error("Enter GROQ Key in sidebar!")
                else:
                    progress = st.progress(0)
                    leads = c_data['leads']
                    for i, (_, lead) in enumerate(leads.iterrows()):
                        l_email = lead.get('F_EMAIL')
                        if not check_blacklist(l_email):
                            # FIXED: Only passing 3 arguments now
                            res = send_email_logic(c_data, lead, st.session_state.g_key)
                            status = "Success" if res == True else res
                        else:
                            status = "Skipped (Unsubscribed)"
                        
                        c_data.setdefault('send_log', []).append({
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Lead": l_email,
                            "Status": status
                        })
                        progress.progress((i + 1) / len(leads))
                    save_data()
                    st.success("Batch Complete!")
                    st.rerun()

elif page == "Email Logs":
    for c_name, c_data in st.session_state.clients.items():
        st.subheader(f"History for {c_name}")
        st.dataframe(c_data.get('send_log', []), use_container_width=True)

elif page == "Statistics":
    for c_name, c_data in st.session_state.clients.items():
        st.metric(f"{c_name} Clicks", c_data.get('clicks', 0))
