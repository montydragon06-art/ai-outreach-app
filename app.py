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
SHEET_ID = st.secrets.get("gsheet_id", "")
TRACKER_URL = st.secrets.get("tracker_url", "https://your-tracker-link.com")

# --- 2. CORE HELPER FUNCTIONS (Must be defined before they are called) ---

def get_cipher():
    """Retrieves the key from Streamlit Secrets."""
    try:
        key = st.secrets["master_key"]
        return Fernet(key.encode())
    except:
        st.error("Master Key missing or invalid in Streamlit Secrets!")
        return None

def get_conn():
    """Establishes connection to Google Sheets."""
    return st.connection("gsheets", type=GSheetsConnection)

def save_data():
    """Encrypts and saves entire vault to Google Sheets."""
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
    # REMOVED ttl=0 to fix the TypeError
    conn.update(worksheet="Clients", data=df)

def load_data():
    """Loads and decrypts data from Google Sheets."""
    cipher = get_cipher()
    conn = get_conn()
    if not cipher: return
    try:
        # REMOVED ttl=0 to fix the TypeError
        df = conn.read(worksheet="Clients")
        if df.empty: return

        encrypted_blob = df.iloc[0, 1]
        decrypted_json = cipher.decrypt(encrypted_blob.encode()).decode()
        raw = json.loads(decrypted_json)
        
        for name, info in raw.items():
            if isinstance(info.get('leads'), str):
                info['leads'] = pd.read_json(info['leads'])
            st.session_state.clients[name] = info
    except:
        pass

def add_to_blacklist(email):
    """Adds an email to the Google Sheet blacklist."""
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
    """Checks if an email is in the blacklist."""
    conn = get_conn()
    try:
        df = conn.read(worksheet="Blacklist")
        return email in df.values
    except:
        return False

def sync_clicks_from_google():
    """Syncs click data from the Clicks worksheet."""
    conn = get_conn()
    try:
        df = conn.read(worksheet="Clicks")
        for _, row in df.iterrows():
            c_name = row.get('Client')
            if c_name in st.session_state.clients:
                st.session_state.clients[c_name]['clicks'] = int(row.get('Clicks', 0))
        return True
    except:
        return False

def process_spreadsheet(file):
    try:
        df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"}
        df = df.rename(columns=mapping)
        return df.dropna(subset=['F_NAME']) if "F_NAME" in df.columns else df
    except Exception as e:
        st.error(f"File Error: {e}")
        return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, cta_details):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        groq_client = Groq(api_key=groq_key)
        
        prompt = f"Write a professional email body for {s_name}. Context: {client_info['desc']}. Mention we found them via {s_source}."
        completion = groq_client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        ai_meat = completion.choices[0].message.content.strip().replace('\n', '<br>')

        full_html = f"<html><body>Dear {s_name},<br><br>{ai_meat}</body></html>"
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Question for {s_name}"
        msg.attach(MIMEText(full_html, 'html'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: 
        return str(e)

# --- 3. EXECUTION LOGIC (The actual running of the app) ---

# Initialize session state and load cloud data
if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# Handle Unsubscribes
if "unsubscribe" in st.query_params:
    email_to_block = st.query_params["unsubscribe"]
    add_to_blacklist(email_to_block)
    st.title("Unsubscribed")
    st.success(f"The address {email_to_block} has been removed.")
    st.stop()

# --- 4. UI INTERFACE ---
st.set_page_config(page_title="Agency Pro", layout="wide")

with st.sidebar:
    st.title("Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    if st.button("🔄 Sync Clicks"):
        if sync_clicks_from_google(): st.success("Updated!"); st.rerun()
        else: st.error("Tab 'Clicks' missing in Sheet.")

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
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {"name": name, "desc": desc, "email": b_email, "app_pw": app_pw, "leads": df, "send_log": [], "clicks": 0}
                save_data()
                st.success("Client Saved to Cloud!")
                st.rerun()

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
                        if not check_blacklist(lead.get('F_EMAIL')):
                            res = send_email_logic(c_data, lead, st.session_state.g_key, {})
                            status = "Success" if res == True else res
                        else:
                            status = "Skipped (Unsubscribed)"
                        
                        c_data.setdefault('send_log', []).append({
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Lead": lead.get('F_EMAIL'),
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
