import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests # Add this to your imports at the top!

PA_URL = "https://mwilden.pythonanywhere.com"
PA_PW = "your_secret_password"

def save_data():
    serializable = {}
    for name, info in st.session_state.clients.items():
        serializable[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            temp_df = info['leads'].copy()
            temp_df.columns = [f"{col}_{i}" if duplicated else col for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable[name]['leads'] = temp_df.to_json()
    
    # Save locally
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f)
    
    # AUTO-SYNC: Send to PythonAnywhere
    try:
        requests.post(f"{PA_URL}/update_db", json=serializable, headers={"Authorization": PA_PW})
    except:
        pass # If tracker is down, it just saves locally

def sync_from_tracker():
    try:
        response = requests.get(f"{PA_URL}/get_db", headers={"Authorization": PA_PW})
        if response.status_code == 200:
            new_data = response.json()
            for name, info in new_data.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info
            # Save the new click counts locally
            with open(DATA_FILE, "w") as f:
                json.dump(new_data, f)
            return True
    except:
        return False



# --- 1. DATA & SESSION INITIALIZATION ---
DATA_FILE = "agency_database.json"

def save_data():
    serializable = {}
    for name, info in st.session_state.clients.items():
        serializable[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            temp_df = info['leads'].copy()
            temp_df.columns = [f"{col}_{i}" if duplicated else col 
                              for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable[name]['leads'] = temp_df.to_json()
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            raw = json.load(f)
            for name, info in raw.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 2. CORE FUNCTIONS ---
def process_spreadsheet(file):
    try:
        df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
        df = df.dropna(axis=1, how='all')
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO"}
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns:
            df = df.dropna(subset=['F_NAME'])
        return df
    except Exception as e:
        st.error(f"File Error: {e}"); return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, framework=None, cta_details=None):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        client = Groq(api_key=groq_key)
        
        # --- TRACKER LINK ---
        tracking_url = f"https://mwilden.pythonanywhere.com/click/{client_info['name']}"
        
        mode_text = f"Use this framework: {framework}" if framework else "Write freehand."
        prompt = f"""
        {mode_text}
        From: {client_info['name']} to {s_name}.
        Lead Info: {lead.get('F_INFO', 'Business owner')}.
        Client Biz: {client_info['desc']}.
        Goal: {cta_details['aim']}. 
        STRICT RULE: You MUST use this EXACT link for the Call to Action: {tracking_url}
        Tone: {client_info.get('tone', 'Professional')}.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Quick question for {s_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. UI NAVIGATION ---
st.set_page_config(page_title="Agency Pro", layout="wide")

with st.sidebar:
    st.title("⚙️ Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    
    st.divider()
    st.write("### 🔄 Sync Tracker Data")
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            st.download_button("Step 1: Download to Computer", f, "agency_database.json", "application/json")
    
    uploaded_sync = st.file_uploader("Step 2: Upload from PythonAnywhere", type="json")
    if uploaded_sync:
        if st.button("Confirm Sync Update"):
            new_data = json.load(uploaded_sync)
            for name, info in new_data.items():
                if isinstance(info['leads'], str): info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info
            save_data(); st.success("Sync Complete!"); st.rerun()

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("➕ Create New Client")
    with st.form("create_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Sender Email")
            app_pw = st.text_input("App Password", type="password")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"])
            file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        with c2:
            st.write("### 🤖 Automation Settings")
            auto_on = st.checkbox("Enable Automation")
            days = st.number_input("Days Between", min_value=1, value=7)
            cta_aim = st.text_input("Default CTA Goal")
            cta_link = st.text_input("Default CTA Link")

        if st.form_submit_button("Submit"):
            if name and file:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "auto_on": auto_on, "auto_days": days, "cta_aim": cta_aim, "cta_link": cta_link,
                    "tone": tone, "leads": df, "send_log": [], "clicks": 0 
                }
                save_data(); st.success("Client Saved!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {c_name}"):
            t1, t2, t3 = st.tabs(["✏️ Edit Full Profile", "🤖 Automation", "🚀 Manual Send"])
            
            with t1:
                c_data['name'] = st.text_input("Biz Name", c_data['name'], key=f"n_{c_name}")
                c_data['desc'] = st.text_area("Description", c_data['desc'], key=f"d_{c_name}")
                c_data['email'] = st.text_input("Sender Email", c_data['email'], key=f"e_{c_name}")
                c_data['app_pw'] = st.text_input("App PW", c_data['app_pw'], type="password", key=f"p_{c_name}")
                c_data['tone'] = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"], index=0, key=f"t_{c_name}")
                new_file = st.file_uploader("Replace Lead List", type=["csv", "xlsx"], key=f"f_{c_name}")
                if st.button("Save Profile Changes", key=f"save_{c_name}"):
                    if new_file: c_data['leads'] = process_spreadsheet(new_file)
                    save_data(); st.rerun()

            with t2:
                c_data['auto_on'] = st.toggle("Automation Active", c_data['auto_on'], key=f"at_{c_name}")
                c_data['auto_days'] = st.number_input("Interval (Days)", 1, 30, int(c_data['auto_days']), key=f"ad_{c_name}")
                c_data['cta_aim'] = st.text_input("Auto CTA Goal", c_data['cta_aim'], key=f"aa_{c_name}")
                c_data['cta_link'] = st.text_input("Auto CTA Link", c_data['cta_link'], key=f"al_{c_name}")
                if st.button("Update Automation", key=f"ua_{c_name}"): save_data(); st.success("Updated")

            with t3:
                m_aim = st.text_input("Manual Goal", c_data['cta_aim'], key=f"ma_{c_name}")
                m_link = st.text_input("Manual Link", c_data['cta_link'], key=f"ml_{c_name}")
                if st.button("🔥 Start Batch", key=f"sb_{c_name}"):
                    for _, lead in c_data['leads'].iterrows():
                        res = send_email_logic(c_data, lead, st.session_state.g_key, None, {"aim": m_aim, "link": m_link})
                        c_data['send_log'].append({"Client": c_name, "Time": datetime.now().strftime("%Y-%m-%d"), "Lead": lead['F_EMAIL'], "Status": "Success" if res==True else res})
                    save_data(); st.rerun()

# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("📜 Email History")
    
    if st.button("🗑️ Clear All Logs"):
        for c in st.session_state.clients.values():
            c['send_log'] = []
        save_data()
        st.rerun()

    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data.get('send_log', []):
            # This line ensures every log entry has a 'Client' name
            log_entry = entry.copy()
            if 'Client' not in log_entry:
                log_entry['Client'] = c_name
            all_logs.append(log_entry)
            
    if all_logs:
        df_logs = pd.DataFrame(all_logs)
        
        # This list defines the order we WANT, but checks if they exist first
        desired_cols = ['Client', 'Time', 'Lead', 'Status']
        # Only show columns that actually exist in the data to prevent KeyError
        existing_cols = [c for c in desired_cols if c in df_logs.columns]
        
        st.dataframe(df_logs[existing_cols], use_container_width=True)
    else:
        st.info("No emails have been sent yet.")
# --- PAGE 4: STATISTICS ---
elif page == "Statistics":
    st.header("📊 Click Performance")
    for c_name, c_data in st.session_state.clients.items():
        sent = len(c_data['send_log'])
        clicks = c_data.get('clicks', 0)
        rate = (clicks / sent * 100) if sent > 0 else 0
        st.subheader(f"Client: {c_name}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Sent", sent)
        c2.metric("Total Clicks", clicks)
        c3.metric("CTR %", f"{rate:.1f}%")
        st.divider()
