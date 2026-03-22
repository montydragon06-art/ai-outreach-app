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

# --- 2. MAILING ENGINE (Using Fixed Model) ---
def send_ai_email(client_info, client_name, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        # Using llama-3.1-8b-instant to fix decommissioned model error
        prompt = f"Write a professional cold email from {client_name} to {lead_name}. Context: {client_info['desc']}. Offer: {client_info['offer']}. Rules: Start 'Hi {lead_name},'. Under 80 words. No corporate jargon."
        
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
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Command Center", layout="wide", page_icon="🏢")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. TOP METRICS (Lean Text) ---
st.title("📂 Agency Command Center")
total_sends = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
m1, m2, m3 = st.columns(3)
m1.metric("Active Clients", len(st.session_state.clients))
m2.metric("Total Agency Sends", total_sends)
m3.metric("System Status", "Ready ✅")

st.divider()

# --- 5. TABS ---
t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: CREATION
with t1:
    st.subheader("Register New Client")
    with st.form("new_client_form", clear_on_submit=True):
        name = st.text_input("Company Name")
        desc = st.text_area("Company Context/Niche")
        offer = st.text_input("Specific Offer/CTA")
        email = st.text_input("Sender Email")
        pw = st.text_input("App Password", type="password")
        leads_file = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
        interval = st.number_input("Days Between Sends", min_value=1, value=1)
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = pd.DataFrame()
                if leads_file:
                    try:
                        if leads_file.name.endswith('.xlsx'):
                            df = pd.read_excel(leads_file)
                        else:
                            df = pd.read_csv(leads_file, encoding='latin1')
                        
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        # Robust Name/Email Mapping to stop "Target" issue
                        for col in ['NAME','FIRST NAME','FULL NAME','RECIPIENT','CONTACT','PERSON']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                        for col in ['EMAIL','EMAIL ADDRESS','MAIL','E-MAIL']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                    except: st.error("Error processing leads file.")

                st.session_state.clients[name] = {
                    "desc": desc, "offer": offer, "interval": interval,
                    "leads": df, "email": {"user": email, "pass": pw},
                    "send_log": [], "last_run_time": None, "auto_on": False
                }
                save_data()
                st.success(f"Client {name} successfully added.")
                st.rerun()

# TAB 2: VAULT (Management Center)
with t2:
    if not st.session_state.clients:
        st.info("No clients currently in the vault.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 Client: {name}"):
            c1, c2, c3 = st.columns([2, 1, 1])
            
            # Use .get to prevent KeyError if key doesn't exist
            c1.write(f"**Email:** {data['email']['user']}")
            c1.write(f"**Sending Every:** {data.get('interval', 1)} Days")
            c1.write(f"**Total Sends:** {len(data.get('send_log', []))}")
            
            if c2.button(f"🗑️ Delete {name}", key=f"del_{name}"):
                del st.session_state.clients[name]
                save_data()
                st.rerun()
            
            auto_val = st.toggle("Automation Status", value=data.get('auto_on', False), key=f"tog_{name}")
            if auto_val != data.get('auto_on'):
                data['auto_on'] = auto_val
                save_data()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("View History For:", list(st.session_state.clients.keys()))
        log_data = pd.DataFrame(st.session_state.clients[sel]["send_log"])
        if not log_data.empty:
            st.table(log_data)
            st.download_button("📥 Download Log CSV", log_data.to_csv(index=False), f"{sel}_history.csv", "text/csv")
        else:
            st.write("No logs found for this client.")

# --- 6. AUTOMATION LOGIC ---
with st.sidebar:
    st.header("⚙️ Global Settings")
    g_key = st.text_input("Groq API Key", type="password")
    master_switch = st.toggle("🚀 Start Automation Engine")

if master_switch and g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            last = data.get('last_run_time')
            interval_days = data.get('interval', 1)
            
            # Check if it's time to send
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=interval_days)):
                leads_df = data.get('leads', pd.DataFrame())
                if not leads_df.empty and 'FINAL_EMAIL' in leads_df.columns:
                    for i, row in leads_df.iterrows():
                        l_email = row['FINAL_EMAIL']
                        l_name = row.get('FINAL_NAME', 'Target')
                        res = send_ai_email(data, name, l_name, l_email, g_key)
                        
                        status_text = "Auto-Sent ✅" if res == True else f"Error: {res}"
                        data["send_log"].append({
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Name": l_name,
                            "Recipient": l_email,
                            "Status": status_text
                        })
                    
                    data['last_run_time'] = datetime.now().isoformat()
                    save_data()
    
    time.sleep(60) # Check every minute
    st.rerun()
