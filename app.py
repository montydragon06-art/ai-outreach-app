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

# --- 2. MAILING ENGINE (Using Supported Model) ---
def send_ai_email(client_info, client_name, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        # Using llama-3.1-8b-instant to avoid decommissioned model errors
        prompt = f"Write a professional cold email from {client_name} to {lead_name}. Context: {client_info['desc']}. Offer: {client_info['offer']}. Rules: Start 'Hi {lead_name},'. Under 80 words."
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}]
        )
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

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Command Center", layout="wide")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. TOP METRICS (Simple Text) ---
st.title("📂 Agency Command Center")
total_sends = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
c1, c2, c3 = st.columns(3)
c1.metric("Total Clients", len(st.session_state.clients))
c2.metric("Total Emails Sent", total_sends)
c3.metric("System Status", "Online ✅")

st.divider()

# --- 5. TABS ---
t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: CREATION (Wipes on submit)
with t1:
    st.subheader("New Client Setup")
    with st.form("new_client_form", clear_on_submit=True):
        name = st.text_input("Company Name")
        desc = st.text_area("Context")
        offer = st.text_input("Offer")
        email = st.text_input("Sender Email")
        pw = st.text_input("App Password", type="password")
        leads = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
        interval = st.number_input("Days Between Sends", min_value=1, value=1)
        
        if st.form_submit_button("📁 Save to Vault"):
            df = pd.DataFrame()
            if leads:
                try:
                    if leads.name.endswith('.xlsx'):
                        df = pd.read_excel(leads)
                    else:
                        df = pd.read_csv(leads, encoding='latin1')
                    
                    df.columns = [str(c).strip().upper() for c in df.columns]
                    # Map Name/Email properly to avoid "Target"
                    for col in ['NAME','FIRST NAME','FULL NAME','CONTACT']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                    for col in ['EMAIL','EMAIL ADDRESS']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                except: st.error("Lead file error.")

            st.session_state.clients[name] = {
                "desc": desc, "offer": offer, "strategy": "Standard",
                "leads": df, "email": {"user": email, "pass": pw, "host": "smtp.gmail.com"},
                "send_log": [], "last_run_time": None, "interval": interval, "auto_on": False
            }
            save_data()
            st.rerun()

# TAB 2: VAULT (Management)
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {name}"):
            col_info, col_actions = st.columns([2, 1])
            with col_info:
                st.write(f"**Total Sent:** {len(data['send_log'])}")
                st.write(f"**Interval:** {data['interval']} days")
                st.write(f"**Email:** {data['email']['user']}")
            
            with col_actions:
                if st.button(f"🗑️ Delete {name}", key=f"del_{name}"):
                    del st.session_state.clients[name]
                    save_data(); st.rerun()
                
                auto = st.toggle("Enable Automation", value=data.get('auto_on', False), key=f"tog_{name}")
                if auto != data.get('auto_on'):
                    data['auto_on'] = auto
                    save_data()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel]["send_log"]))

# --- 6. SIDEBAR & AUTOMATION ---
with st.sidebar:
    st.header("Settings")
    g_key = st.text_input("Groq API Key", type="password")
    run_auto = st.toggle("Global Automation Switch")

if run_auto and g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            # Individualized timing check
            last = data.get('last_run_time')
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=data['interval'])):
                for i, row in data['leads'].iterrows():
                    l_email = row.get('FINAL_EMAIL')
                    l_name = row.get('FINAL_NAME', 'Target')
                    if l_email:
                        send_ai_email(data, name, l_name, l_email, g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Auto-Sent ✅"})
                data['last_run_time'] = datetime.now().isoformat()
                save_data()
    time.sleep(60); st.rerun()
