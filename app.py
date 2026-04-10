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
    cipher = get_cipher()
    if not cipher: return None
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
            if isinstance(info.get('leads'), pd.DataFrame):
                client_copy['leads'] = info['leads'].to_json()
            serializable[name] = client_copy
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
        raw = {}
        for _, row in df.iterrows():
            raw[row['Name']] = decrypt_data(row['Data'])
        
        loaded_clients = {}
        for name, info in raw.items():
            if isinstance(info.get('leads'), str):
                info['leads'] = pd.read_json(io.StringIO(info['leads']))
            if 'send_log' not in info: info['send_log'] = []
            if 'auto_settings' not in info: info['auto_settings'] = {}
            loaded_clients[name] = info
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
            client_clicks = len(clicks_df[clicks_df["Client"] == c_name]) if not clicks_df.empty and "Client" in clicks_df.columns else 0
            percentage = (client_clicks / total_sent * 100) if total_sent > 0 else 0
            stats_data.append({
                "Client Name": c_name, "Emails Sent": total_sent,
                "Total Clicks": client_clicks, "Click Rate": f"{percentage:.1f}%"
            })
        return pd.DataFrame(stats_data)
    except Exception as e:
        st.error(f"Error calculating stats: {e}")
        return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, send_type, cta_input, offer_input):
    try:
        # 1. Prepare Data
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_email = str(lead.get('F_EMAIL', '')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        biz_name = client_info['name']
        
        # 2. Build Tracking Link Context
        if send_type == 'link' and str(cta_input).startswith("http"):
            tracking_link = (
                f"{TRACKER_URL}?"
                f"dest={cta_input}&"
                f"client={biz_name.replace(' ', '%20')}&"
                f"email={s_email}"
            )
            # Instructing the AI to place this at the very end
            cta_context = f"At the very end of your message, include this exact HTML hyperlink: <a href='{tracking_link}'>Click here to view details</a>"
        else:
            cta_context = "End the message by telling them to simply reply to this email for more information."

        # 3. Secure AI Prompt
        groq_client = Groq(api_key=groq_key)
        system_msg = (
            f"You are a professional assistant for {biz_name}. Writing to {s_name}.\n"
            "STRICT RULES:\n"
            "1. NO GREETING. Do not write 'Dear' or 'Hi'. Start directly with the first sentence of the body.\n"
            "2. NO SIGN-OFF. Do not write 'Best regards' or a name. The script handles the signature.\n"
            "3. NO PLACEHOLDERS. Do not use square brackets like [Name] or [Company].\n"
            "4. HYPERLINK PLACEMENT. If a link is provided, it MUST be the very last thing in your response.\n"
            "5. LENGTH. Be concise (max 2 paragraphs)."
        )
        
        user_msg = f"Business Description: {client_info['desc']}\nSpecial Offer: {offer_input}\nRequired Action: {cta_context}"

        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=0.1
        )
        
        ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')

        # 4. Final HTML Assembly
        # This fixes the double greeting by being the ONLY place "Dear" is defined.
        # It adds a professional sign-off using the business name from client_info.
        footer = f"""<br><br>Best regards,<br>{biz_name}<br><br><hr/><p style="font-size:10px;color:#888;">
            Found via: {s_source} | <a href="{FORM_URL}">Unsubscribe</a> | <a href="{PRIVACY_PDF_URL}">Privacy Policy</a></p>"""
        
        full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
        
        # 5. SMTP Send
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
    st.session_state.clients = load_data()

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
                st.session_state.clients[name] = {"name": name, "desc": desc, "email": b_email, "app_pw": app_pw, "leads": df, "send_log": [], "auto_settings": {}}
                save_data(); st.success("Client Saved!"); st.rerun()

elif page == "Client Vault":
    if not st.session_state.clients: st.info("No clients found.")
    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]
        with st.expander(f"🏢 {c_name}"):
            tab_info, tab_auto, tab_manual = st.tabs(["Information", "Automatic Send", "Manual Send"])
            with tab_info:
                c_data['name'] = st.text_input("Name", value=c_data['name'], key=f"en_{c_name}")
                c_data['desc'] = st.text_area("Desc", value=c_data['desc'], key=f"ed_{c_name}")
                if st.button("Save Changes", key=f"sv_{c_name}"): save_data()
            with tab_auto:
                st.subheader("Schedule Campaigns")
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("Start Date", key=f"date_{c_name}")
                    start_time = st.time_input("Start Time", key=f"time_{c_name}")
                with col2:
                    freq = st.selectbox("Frequency", ["Every 24 hours", "Every 48 hours", "Weekly"], key=f"freq_{c_name}")
                
                send_method = st.radio("CTA Type", ["Link to click", "Direct reply"], key=f"am_{c_name}")
                cta_val = st.text_input("CTA Link/Action", key=f"ac_{c_name}")
                offer_val = st.text_input("Offer", key=f"ao_{c_name}")
                
                if st.button("Enable Automation", key=f"ba_{c_name}"):
                    next_run = datetime.combine(start_date, start_time)
                    c_data['auto_settings'] = {"active": True, "next_run": next_run.strftime("%Y-%m-%d %H:%M"), "freq": freq, "cta": cta_val, "offer": offer_val, "method": send_method}
                    save_data(); st.success(f"Scheduled for {next_run}")
                if c_data.get('auto_settings', {}).get('active'):
                    st.info(f"📍 Next Run: {c_data['auto_settings']['next_run']}")

            with tab_manual:
                m_method = st.radio("Type", ["Link to click", "Action Required"], key=f"mm_{c_name}")
                m_cta = st.text_input("CTA", key=f"mc_{c_name}")
                m_offer = st.text_input("Offer", key=f"mo_{c_name}")
                if st.button("🚀 Execute Batch", key=f"ex_{c_name}"):
                    if not st.session_state.get('g_key'): st.error("Enter GROQ Key!")
                    else:
                        progress = st.progress(0); leads = c_data['leads']
                        for i, (_, lead) in enumerate(leads.iterrows()):
                            l_email = lead.get('F_EMAIL')
                            status = "Skipped" if check_blacklist(l_email) else ("Success" if send_email_logic(c_data, lead, st.session_state.g_key, 'link' if m_method == "Link to click" else 'reply', m_cta, m_offer) == True else "Failed")
                            c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Lead": l_email, "Status": status})
                            progress.progress((i + 1) / len(leads))
                        save_data(); st.rerun()

elif page == "Email Logs":
    st.header("📋 History")
    client_names = list(st.session_state.clients.keys())
    selected_filter = st.selectbox("Filter:", ["All Clients"] + client_names)
    if st.sidebar.button("🗑️ Clear Logs"):
        for c in (st.session_state.clients if selected_filter == "All Clients" else [selected_filter]): st.session_state.clients[c]['send_log'] = []
        save_data(); st.rerun()
    
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        if selected_filter == "All Clients" or selected_filter == c_name:
            for entry in c_data.get('send_log', []):
                all_logs.append({**entry, "Company": c_name})
    if all_logs: st.dataframe(pd.DataFrame(all_logs), use_container_width=True)

elif page == "Statistics":
    st.header("📊 Stats")
    if st.button("🔄 Sync"): st.cache_data.clear(); st.rerun()
    df_stats = get_statistics()
    if not df_stats.empty:
        st.dataframe(df_stats, use_container_width=True, hide_index=True)
        st.metric("Total Sent", df_stats["Emails Sent"].sum())
