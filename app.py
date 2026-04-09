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
        
        # Encrypt
        encrypted_blob = cipher.encrypt(json.dumps(serializable).encode()).decode()
        
        # We MUST ensure the DataFrame has the exact columns Name and Data
        df_to_save = pd.DataFrame([["Master_Vault", encrypted_blob]], columns=["Name", "Data"])
        
        # Use clear=True to wipe old data so the blob doesn't just append forever
        conn.update(worksheet="Clients", data=df_to_save)
        st.toast("✅ Cloud Backup Synced") 
    except Exception as e:
        st.error(f"❌ Save Failed: {str(e)}")

def load_data():
    cipher = get_cipher()
    conn = get_conn()
    if not cipher: return
    try:
        # TTL=0 is the secret sauce. It prevents loading old/cached empty data.
        df = conn.read(worksheet="Clients", ttl=0) 
        
        if df is not None and not df.empty:
            # Look for the row where Name is Master_Vault
            vault_row = df[df["Name"] == "Master_Vault"]
            if not vault_row.empty:
                encrypted_blob = vault_row.iloc[0]["Data"]
                decrypted_json = cipher.decrypt(encrypted_blob.encode()).decode()
                raw = json.loads(decrypted_json)
                
                loaded_clients = {}
                for name, info in raw.items():
                    if isinstance(info.get('leads'), str):
                    # We use io.StringIO to make the string act like a file
                    info['leads'] = pd.read_json(io.StringIO(info['leads']))
                loaded_clients[name] = info
                
                st.session_state.clients = loaded_clients
    except Exception as e:
        # Only reset if the error is serious; otherwise, keep session state
        st.warning(f"Note: Could not refresh vault ({str(e)})")
def send_email_logic(client_info, lead, groq_key, send_type, cta_input, offer_input):
    """
    Enhanced Email Logic:
    - send_type: 'link' or 'reply'
    - cta_input: The link or the specific action required
    - offer_input: Special offer (if provided)
    """
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        s_email = lead.get('F_EMAIL')
        
        # Build AI context for offers
        offer_context = f"Special Offer to include: {offer_input}" if offer_input else ""
        
        # Build CTA context
        if send_type == 'link':
            cta_context = f"Include this link as the Call to Action: '{str(cta_input)}'"
        else:
            cta_context = f"Requirement from user: {cta_input}. Ensure they know to reply directly to this email."

        groq_client = Groq(api_key=groq_key)
        system_msg = "You are a professional assistant. Output ONLY the email body. No conversational filler."
        user_msg = f"""
        Write a concise outreach email to {s_name} regarding {client_info['desc']}. 
        Mention source: {s_source}.
        {offer_context}
        {cta_context}
        """

        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=0.4
        )
        ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')

        footer = f"""<br><br><hr/><p style="font-size:10px;color:#888;">
            Found via: {s_source} | <a href="{FORM_URL}">Unsubscribe</a> | <a href="{PRIVACY_PDF_URL}">Privacy Policy</a></p>"""
        full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
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
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs"])

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
                    "leads": df, "send_log": [], "auto_settings": {}
                }
                save_data()
                st.success("Client Saved!")
                st.rerun()

elif page == "Client Vault":
    if not st.session_state.clients:
        st.info("No clients found.")
    
    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]
        
        with st.expander(f"🏢 {c_name}"):
            tab_info, tab_auto, tab_manual = st.tabs(["Information", "Automatic Send", "Manual Send"])
            
            # --- TAB 1: INFORMATION ---
            with tab_info:
                st.subheader("Edit Client Details")
                c_data['name'] = st.text_input("Business Name", value=c_data['name'], key=f"edit_name_{c_name}")
                c_data['desc'] = st.text_area("Description", value=c_data['desc'], key=f"edit_desc_{c_name}")
                c_data['email'] = st.text_input("Sender Email", value=c_data['email'], key=f"edit_email_{c_name}")
                c_data['app_pw'] = st.text_input("App Password", value=c_data['app_pw'], type="password", key=f"edit_pw_{c_name}")
                if st.button("Save Changes", key=f"save_edit_{c_name}"):
                    save_data()
                    st.success("Information Updated!")

            # --- TAB 2: AUTOMATIC SEND ---
            with tab_auto:
                st.subheader("Schedule Campaigns")
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("Start Date", key=f"date_{c_name}")
                    start_time = st.time_input("Start Time", key=f"time_{c_name}")
                with col2:
                    freq = st.selectbox("Frequency", ["Every 24 hours", "Every 48 hours", "Weekly"], key=f"freq_{c_name}")
                
                send_method = st.radio("Call to Action Type", ["Link for receiver to click", "Direct reply required"], key=f"auto_method_{c_name}")
                
                cta_val = ""
                if send_method == "Link for receiver to click":
                    cta_val = st.text_input("Link URL (Hyperlink)", placeholder="https://example.com/booking", key=f"auto_link_{c_name}")
                else:
                    cta_val = st.text_input("Required Action", placeholder="e.g., 'Reply with YES to schedule'", key=f"auto_reply_{c_name}")
                
                offer_val = st.text_input("Special Offer/Sale (Leave blank if none)", key=f"auto_offer_{c_name}")
                
                if st.button("Enable Automation", key=f"btn_auto_{c_name}"):
                    # Note: Full automation requires a background worker (like APScheduler). 
                    # For now, this saves the settings so you can trigger them.
                    c_data['auto_settings'] = {
                        "active": True, "start": str(start_date), "method": send_method,
                        "cta": cta_val, "offer": offer_val, "freq": freq
                    }
                    save_data()
                    st.success(f"Automation schedule saved for {start_date} at {start_time}")

            # --- TAB 3: MANUAL SEND ---
            with tab_manual:
                st.subheader("One-Time Send to All")
                m_method = st.radio("CTA Type", ["Link to click", "Action Required"], key=f"m_method_{c_name}")
                
                m_cta = st.text_input("CTA (Link or Action description)", key=f"m_cta_{c_name}")
                m_offer = st.text_input("Special Offer/Sale (Optional)", key=f"m_offer_{c_name}")
                
                if st.button("🚀 Execute Manual Batch", key=f"m_btn_{c_name}"):
                    if not st.session_state.get('g_key'):
                        st.error("Enter GROQ Key in sidebar!")
                    else:
                        progress = st.progress(0)
                        leads = c_data['leads']
                        for i, (_, lead) in enumerate(leads.iterrows()):
                            l_email = lead.get('F_EMAIL')
                            if not check_blacklist(l_email):
                                type_key = 'link' if m_method == "Link to click" else 'reply'
                                res = send_email_logic(c_data, lead, st.session_state.g_key, type_key, m_cta, m_offer)
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
                        st.success("Manual Batch Complete!")
                        st.rerun()

elif page == "Email Logs":
    st.header("📋 Communication History")
    
    if not st.session_state.clients:
        st.info("No logs found. Create a client and send some emails first!")
    else:
        # 1. Create a list for the dropdown
        client_names = list(st.session_state.clients.keys())
        filter_options = ["All Clients"] + client_names
        selected_filter = st.selectbox("Filter logs by company:", filter_options)
        
        # --- CLEAR LOGS LOGIC ---
        st.sidebar.markdown("---")
        st.sidebar.subheader("Danger Zone")
        
        if selected_filter == "All Clients":
            if st.sidebar.button("🗑️ Clear ALL Logs (Global)", help="This wipes history for EVERY client."):
                for c_name in st.session_state.clients:
                    st.session_state.clients[c_name]['send_log'] = []
                save_data()
                st.success("All logs cleared globally!")
                st.rerun()
        else:
            if st.sidebar.button(f"🗑️ Clear {selected_filter} Logs", help=f"Only wipes logs for {selected_filter}"):
                st.session_state.clients[selected_filter]['send_log'] = []
                save_data()
                st.success(f"Logs for {selected_filter} have been cleared.")
                st.rerun()

        # --- DISPLAY LOGIC ---
        if selected_filter == "All Clients":
            all_logs = []
            for c_name, c_data in st.session_state.clients.items():
                for entry in c_data.get('send_log', []):
                    entry_with_company = entry.copy()
                    entry_with_company["Company"] = c_name
                    all_logs.append(entry_with_company)
            
            if all_logs:
                log_df = pd.DataFrame(all_logs)
                cols = ["Company"] + [c for c in log_df.columns if c != "Company"]
                st.dataframe(log_df[cols], use_container_width=True)
                
                csv = log_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download All Logs (CSV)", data=csv, file_name="all_email_logs.csv", mime="text/csv")
            else:
                st.warning("No logs found for any clients.")
        
        else:
            c_data = st.session_state.clients[selected_filter]
            specific_logs = c_data.get('send_log', [])
            
            if specific_logs:
                log_df = pd.DataFrame(specific_logs)
                st.subheader(f"History for {selected_filter}")
                st.dataframe(log_df, use_container_width=True)
                
                csv = log_df.to_csv(index=False).encode('utf-8')
                st.download_button(f"📥 Download {selected_filter} Logs", data=csv, file_name=f"{selected_filter}_logs.csv", mime="text/csv")
            else:
                st.info(f"No emails have been sent for {selected_filter} yet.")
