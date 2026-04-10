import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from cryptography.fernet import Fernet
from streamlit_gsheets import GSheetsConnection
import io

# --- 1. SETTINGS & SECRETS ---
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScBMsqCrO8tKVW4nYLUOVgAewzqUdrom-VXPPPrhsgxPY0rzg/viewform"
PRIVACY_PDF_URL = "https://docs.google.com/document/d/1OjaVW-V5VSXJ9k-mjncAj-xF4gHmVUQwVwrBlXTMxow/edit?usp=sharing"
TRACKER_URL = "https://script.google.com/macros/s/AKfycbxQ45IQwiRw0WxDs9H8QZAFnkkFIHCIQHtMrc6sfEOYJBycj47Q_4EfaycZUJq96K0/exec"

# --- 2. CORE FUNCTIONS ---

def get_conn():
    return st.connection("gsheets", type=GSheetsConnection)

def get_cipher():
    try:
        key = st.secrets["master_key"]
        return Fernet(key.encode())
    except:
        st.error("Master Key missing in Streamlit Secrets!")
        return None

def decrypt_data(encrypted_blob):
    """FIX: Added missing decryption function"""
    cipher = get_cipher()
    if not cipher:
        return None
    try:
        decrypted_data = cipher.decrypt(encrypted_blob.encode()).decode()
        return json.loads(decrypted_data)
    except Exception as e:
        st.error(f"Decryption failed: {e}")
        return {}

def check_blacklist(email):
    conn = get_conn()
    try:
        df = conn.read(worksheet="Form Responses 1") 
        blacklisted_emails = df.iloc[:, 1].astype(str).str.lower().values 
        return email.lower() in blacklisted_emails
    except:
        return False

def save_data():
    cipher = get_cipher()
    conn = get_conn()
    if not cipher or 'clients' not in st.session_state or not st.session_state.clients: 
        return
    
    try:
        serializable = {}
        for name, info in st.session_state.clients.items():
            client_copy = info.copy()
            # Convert leads DataFrame to JSON for storage
            if isinstance(info.get('leads'), pd.DataFrame):
                client_copy['leads'] = info['leads'].to_json()
            serializable[name] = client_copy
        
        # Encrypt the entire state including automation settings
        encrypted_blob = cipher.encrypt(json.dumps(serializable).encode()).decode()
        df_to_save = pd.DataFrame([["Master_Vault", encrypted_blob]], columns=["Name", "Data"])
        
        conn.update(worksheet="Clients", data=df_to_save)
        st.toast("✅ Cloud Backup Synced") 
    except Exception as e:
        st.error(f"❌ Save Failed: {str(e)}")

def load_data():
    conn = get_conn()
    try:
        df = conn.read(worksheet="Clients", ttl=0)
        # Identify the Master_Vault row
        vault_row = df[df['Name'] == "Master_Vault"]
        if vault_row.empty:
            return {}

        raw = decrypt_data(vault_row.iloc[0]['Data'])
        
        loaded_clients = {}
        for name, info in raw.items():
            # FIX: Convert JSON leads back to DataFrame
            if isinstance(info.get('leads'), str):
                info['leads'] = pd.read_json(io.StringIO(info['leads']))
            
            # Ensure keys exist
            if 'send_log' not in info: info['send_log'] = []
            if 'auto_settings' not in info: info['auto_settings'] = {}
                
            loaded_clients[name] = info
        st.session_state.clients = loaded_clients
        return loaded_clients
    except Exception as e:
        st.error(f"Vault Error: {e}")
        return {}

def get_statistics():
    conn = get_conn()
    stats_data = []
    try:
        clicks_df = conn.read(worksheet="Clicks", ttl=0)
        for c_name, c_data in st.session_state.clients.items():
            sent_log = c_data.get('send_log', [])
            total_sent = len([log for log in sent_log if log.get('Status') == "Success"])
            
            if not clicks_df.empty and "Client" in clicks_df.columns:
                client_clicks = len(clicks_df[clicks_df["Client"] == c_name])
            else:
                client_clicks = 0
            
            percentage = (client_clicks / total_sent * 100) if total_sent > 0 else 0
            stats_data.append({
                "Client Name": c_name,
                "Emails Sent": total_sent,
                "Total Clicks": client_clicks,
                "Click Rate": f"{percentage:.1f}%"
            })
        return pd.DataFrame(stats_data)
    except Exception as e:
        st.error(f"Error calculating stats: {e}")
        return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, send_type, cta_input, offer_input):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_email = str(lead.get('F_EMAIL', '')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        biz_name = client_info['name']
        
        if send_type == 'link' and str(cta_input).startswith("http"):
            tracking_link = (
                f"{TRACKER_URL}?"
                f"dest={cta_input}&"
                f"client={biz_name.replace(' ', '%20')}&"
                f"email={s_email}"
            )
            cta_context = f"Include this HTML hyperlink: <a href='{tracking_link}'>Click here to view details</a>"
        else:
            cta_context = "Tell them to reply directly to this email."

        groq_client = Groq(api_key=groq_key)
        system_msg = (
            f"You are an assistant for {biz_name}. Writing to {s_name}.\n"
            "STRICT RULES: No greetings, no sign-offs, no placeholders. Use HTML for links."
        )
        user_msg = f"Description: {client_info['desc']}\nOffer: {offer_input}\nAction: {cta_context}"

        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=0.1
        )
        
        ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')
        footer = f"<br><br><hr/><p style='font-size:10px;color:#888;'>Found via: {s_source} | <a href='{FORM_URL}'>Unsubscribe</a> | <a href='{PRIVACY_PDF_URL}'>Privacy Policy</a></p>"
        full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
        
        msg = MIMEMultipart()
        msg['From'] = f"{biz_name} <{client_info['email']}>"
        msg['To'] = s_email
        msg['Subject'] = f"Quick Update for {s_name}"
        msg.attach(MIMEText(full_html, 'html'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: 
        return str(e)

# --- 3. SESSION INITIALIZATION ---
if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. UI INTERFACE ---
st.set_page_config(page_title="Agency Pro CRM", layout="wide")

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
                df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
                df.columns = [str(c).strip().upper() for c in df.columns]
                df = df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw, 
                    "leads": df, "send_log": [], "auto_settings": {"active": False}
                }
                save_data()
                st.success("Client Saved!")
                st.rerun()

elif page == "Client Vault":
    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]
        with st.expander(f"🏢 {c_name}"):
            tab_info, tab_auto, tab_manual = st.tabs(["Information", "Automation Settings", "Manual Batch"])
            
            with tab_info:
                c_data['name'] = st.text_input("Business Name", value=c_data['name'], key=f"n_{c_name}")
                c_data['desc'] = st.text_area("Description", value=c_data['desc'], key=f"d_{c_name}")
                if st.button("Save Changes", key=f"s_{c_name}"):
                    save_data()

            with tab_auto:
                st.subheader("Campaign Schedule")
                auto = c_data.get('auto_settings', {})
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("Start Date", key=f"sd_{c_name}")
                    start_time = st.time_input("Start Time", key=f"st_{c_name}")
                with col2:
                    freq = st.selectbox("Frequency", ["Every 24 hours", "Every 48 hours", "Weekly"], key=f"f_{c_name}")
                
                cta = st.text_input("CTA Content", value=auto.get('cta', ''), key=f"cta_{c_name}")
                
                if st.button("Update Automation Schedule", key=f"ua_{c_name}"):
                    next_run = datetime.combine(start_date, start_time)
                    c_data['auto_settings'] = {
                        "active": True, 
                        "next_run": next_run.strftime("%Y-%m-%d %H:%M"),
                        "freq": freq,
                        "cta": cta
                    }
                    save_data()
                    st.success(f"Next send scheduled for: {next_run}")
                
                if auto.get('active'):
                    st.info(f"📍 Scheduled: {auto.get('next_run')} ({auto.get('freq')})")

            with tab_manual:
                m_cta = st.text_input("CTA", key=f"mcta_{c_name}")
                if st.button("🚀 Run Manual Batch", key=f"mb_{c_name}"):
                    # Logic remains same as previous version but ensures save_data is called
                    progress = st.progress(0)
                    for i, (_, lead) in enumerate(c_data['leads'].iterrows()):
                        res = send_email_logic(c_data, lead, st.session_state.g_key, 'link', m_cta, "")
                        c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Lead": lead.get('F_EMAIL'), "Status": "Success" if res==True else res})
                        progress.progress((i+1)/len(c_data['leads']))
                    save_data()
                    st.rerun()

elif page == "Email Logs":
    # (Display logic remains identical to your working version)
    st.header("📋 Communication History")
    for c_name, c_data in st.session_state.clients.items():
        if c_data.get('send_log'):
            st.write(f"### {c_name}")
            st.dataframe(pd.DataFrame(c_data['send_log']))

elif page == "Statistics":
    st.header("📊 Performance Statistics")
    if st.button("🔄 Sync from Sheets"):
        st.cache_data.clear()
        st.rerun()
    df_stats = get_statistics()
    if not df_stats.empty:
        st.dataframe(df_stats, use_container_width=True, hide_index=True)
