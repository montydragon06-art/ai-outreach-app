import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
import json
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. DATA PERSISTENCE ---
DATA_FILE = "agency_data.json"

def save_data():
    serializable_data = {}
    for name, info in st.session_state.clients.items():
        serializable_data[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            serializable_data[name]['leads'] = info['leads'].to_json()
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_data, f)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            raw_data = json.load(f)
            for name, info in raw_data.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info

# --- 2. MAILING ENGINE ---
def send_ai_email(client_info, client_name, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        prompt = f"Write a cold email from {client_name} to {lead_name}. Context: {client_info['desc']}. Offer: {client_info['offer']}. Strategy: {client_info['strategy']}. Rules: Start 'Hi {lead_name},' Sign '{client_name}'. Under 80 words."
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {lead_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(client_info['email']['host'], 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP CONFIG & STATE ---
st.set_page_config(page_title="Agency Command Center", layout="wide", page_icon="📊")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

# --- 4. GLOBAL DASHBOARD ---
total_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
st.title("📊 Agency Global Dashboard")
m1, m2, m3 = st.columns(3)
m1.metric("Total Clients", len(st.session_state.clients))
m2.metric("Total Emails Sent", total_sent)
m3.metric("Active Automations", sum(1 for c in st.session_state.clients.values() if c.get('auto_on', False)))

st.divider()

# --- 5. TABS ---
t1, t2, t3 = st.tabs(["➕ Create New Client", "🗄️ Client Vault & Editor", "📜 Master Logs"])

# TAB 1: CLEAN CREATION WINDOW
with t1:
    st.subheader("Register New Client")
    with st.form("creation_form", clear_on_submit=True):
        c_name = st.text_input("Company Name")
        c_desc = st.text_area("Company Context")
        col1, col2 = st.columns(2)
        c_strat = col1.selectbox("Strategy", ["Value-First", "Direct Pitch", "Audit"])
        c_off = col2.text_input("Specific Offer")
        c_email = col1.text_input("Sender Email")
        c_pass = col2.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        c_interval = st.number_input("Send Every X Days", min_value=1, value=1)
        
        submit = st.form_submit_button("📁 Save to Vault")
        if submit and c_name:
            df = pd.DataFrame()
            if c_leads:
                try:
                    df = pd.read_excel(c_leads) if c_leads.name.endswith('.xlsx') else pd.read_csv(c_leads, encoding='latin1')
                    df.columns = [str(c).strip().upper() for c in df.columns]
                    for col in ['NAME','FIRST NAME','FULL NAME']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                    for col in ['EMAIL','EMAIL ADDRESS','MAIL']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                except: st.error("File error")

            st.session_state.clients[c_name] = {
                "desc": c_desc, "strategy": c_strat, "offer": c_off, "interval": c_interval, "auto_on": False,
                "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                "send_log": [], "last_run_time": None
            }
            save_data()
            st.success(f"Client {c_name} added to vault!")
            st.rerun()

# TAB 2: SEPARATE VAULT & EDITOR
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    else:
        for name, data in list(st.session_state.clients.items()):
            with st.expander(f"🏢 CLIENT: {name}"):
                # Dashboard for this specific client
                client_sent = len(data.get('send_log', []))
                st.write(f"**Client Stats:** {client_sent} emails sent | **Interval:** {data.get('interval', 1)} days")
                
                # Action Buttons
                col1, col2, col3, col4 = st.columns(4)
                
                if col1.button("✏️ Edit Details", key=f"ed_{name}"):
                    st.session_state.editing_client = name
                
                # Logic for Editing (Popup-style form)
                if st.session_state.editing_client == name:
                    with st.form(f"edit_form_{name}"):
                        st.write(f"Editing {name}")
                        e_desc = st.text_area("Update Context", value=data['desc'])
                        e_off = st.text_input("Update Offer", value=data['offer'])
                        e_int = st.number_input("Update Interval", value=data.get('interval', 1))
                        e_auto = st.toggle("Enable Automation for this Client", value=data.get('auto_on', False))
                        
                        if st.form_submit_button("Update Vault"):
                            data['desc'] = e_desc
                            data['offer'] = e_off
                            data['interval'] = e_int
                            data['auto_on'] = e_auto
                            save_data()
                            st.session_state.editing_client = None
                            st.rerun()

                if col2.button("🚀 Run Manual Batch", key=f"run_{name}"):
                    groq_key = st.sidebar.text_input("Confirm Groq Key", type="password", key=f"gk_{name}")
                    if groq_key:
                        for i, row in data['leads'].iterrows():
                            l_email = row.get('FINAL_EMAIL')
                            l_name = row.get('FINAL_NAME', 'Target')
                            if l_email:
                                res = send_ai_email(data, name, l_name, l_email, groq_key)
                                data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Sent" if res==True else res})
                        save_data()
                        st.success("Batch Sent!")

                if col3.button("🗑️ Delete Client", key=f"del_{name}"):
                    del st.session_state.clients[name]
                    save_data()
                    st.rerun()

# TAB 3: MASTER LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Select Client Log", list(st.session_state.clients.keys()))
        st.dataframe(pd.DataFrame(st.session_state.clients[sel]["send_log"]), use_container_width=True)

# --- 6. INDIVIDUALIZED AUTOMATION ENGINE ---
# This runs in the background while the app is open
groq_key_sidebar = st.sidebar.text_input("Global Groq Key for Automation", type="password")

if groq_key_sidebar:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            interval = data.get('interval', 1)
            should_send = False
            
            if data['last_run_time'] is None:
                should_send = True
            else:
                last_run = datetime.fromisoformat(data['last_run_time'])
                if datetime.now() > (last_run + timedelta(days=interval)):
                    should_send = True
            
            if should_send:
                for i, row in data['leads'].iterrows():
                    l_email = row.get('FINAL_EMAIL')
                    l_name = row.get('FINAL_NAME', 'Target')
                    if l_email:
                        send_ai_email(data, name, l_name, l_email, groq_key_sidebar)
                        data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Auto-Sent ✅"})
                data['last_run_time'] = datetime.now().isoformat()
                save_data()
    
    time.sleep(60)
    st.rerun()
