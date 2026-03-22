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

# --- 2. MAILING ENGINE (Fixes Decommissioned Model) ---
def send_ai_email(client_info, client_name, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        # Updated to llama-3.1-8b-instant to fix "model_decommissioned" error
        prompt = f"Write a professional cold email from {client_name} to {lead_name}. Context: {client_info['desc']}. Offer: {client_info['offer']}. Rules: Start 'Hi {lead_name},' Sign '{client_name}'. Under 80 words. No 'Friend'."
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}]
        )
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Question for {lead_name}"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(client_info['email']['host'], 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Command Center", layout="wide", page_icon="📈")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. GLOBAL DASHBOARD (Fixes ValueError/Date Crash) ---
st.title("📈 Agency Global Dashboard")

all_logs = []
for c_name, c_data in st.session_state.clients.items():
    for entry in c_data.get('send_log', []):
        entry_copy = entry.copy()
        entry_copy['Client'] = c_name
        all_logs.append(entry_copy)

if all_logs:
    df_all = pd.DataFrame(all_logs)
    # Convert dates safely. 'coerce' turns bad dates into "NaT" so the graph doesn't crash
    df_all['Time'] = pd.to_datetime(df_all['Time'], errors='coerce')
    df_all = df_all.dropna(subset=['Time'])
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Clients", len(st.session_state.clients))
    m2.metric("Total Sends", len(df_all))
    m3.metric("Last Send", df_all['Time'].max().strftime('%Y-%m-%d'))

    st.subheader("Global Sending Volume")
    df_all['Date'] = df_all['Time'].dt.date
    chart_data = df_all.groupby('Date').size()
    st.area_chart(chart_data)

st.divider()

# --- 5. TABS ---
t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Logs"])

with t1:
    with st.form("new_client", clear_on_submit=True):
        c_name = st.text_input("Company Name")
        c_desc = st.text_area("Context")
        c_off = st.text_input("Offer")
        col1, col2 = st.columns(2)
        c_email = col1.text_input("Email")
        c_pass = col2.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        c_int = st.number_input("Interval (Days)", min_value=1, value=1)
        
        if st.form_submit_button("Save to Vault"):
            df = pd.DataFrame()
            if c_leads:
                try:
                    if c_leads.name.endswith('.xlsx'):
                        df = pd.read_excel(c_leads)
                    else:
                        df = pd.read_csv(c_leads, encoding='latin1')
                    
                    df.columns = [str(c).strip().upper() for c in df.columns]
                    # Map Name/Email properly to avoid "Target" fallback
                    for col in ['NAME','FIRST NAME','FULL NAME','CONTACT']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                    for col in ['EMAIL','EMAIL ADDRESS']:
                        if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                except: st.error("File error")

            st.session_state.clients[c_name] = {
                "desc": c_desc, "offer": c_off, "strategy": "Direct",
                "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com"},
                "send_log": [], "last_run_time": None, "interval": c_int, "auto_on": False
            }
            save_data()
            st.rerun()

with t2:
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {name}"):
            # Client specific chart
            if data['send_log']:
                df_c = pd.DataFrame(data['send_log'])
                df_c['Time'] = pd.to_datetime(df_c['Time'], errors='coerce')
                df_c = df_c.dropna(subset=['Time'])
                df_c['Date'] = df_c['Time'].dt.date
                st.line_chart(df_c.groupby('Date').size())
            
            c1, c2 = st.columns(2)
            if c1.button(f"🗑️ Delete {name}"):
                del st.session_state.clients[name]
                save_data(); st.rerun()
            
            auto = c2.toggle("Automation On", value=data.get('auto_on', False), key=f"tog_{name}")
            if auto != data.get('auto_on'):
                data['auto_on'] = auto
                save_data()

# --- 6. AUTOMATION ---
# (Automation logic continues here checking data['interval'] against data['last_run_time'])
