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
st.set_page_config(page_title="Agency Command Center", layout="wide", page_icon="📈")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

# --- 4. GLOBAL ANALYTICS DASHBOARD ---
st.title("📈 Agency Global Command Center")

all_logs = []
for c_name, c_data in st.session_state.clients.items():
    for entry in c_data.get('send_log', []):
        entry_copy = entry.copy()
        entry_copy['Client'] = c_name
        all_logs.append(entry_copy)

m1, m2, m3 = st.columns(3)
m1.metric("Total Clients", len(st.session_state.clients))
m2.metric("Agency Total Sends", len(all_logs))
m3.metric("Running Automations", sum(1 for c in st.session_state.clients.values() if c.get('auto_on', False)))

if all_logs:
    with st.container():
        st.subheader("Agency Sending Volume (All Clients)")
        df_all = pd.DataFrame(all_logs)
        df_all['Time'] = pd.to_datetime(df_all['Time'])
        df_all['Date'] = df_all['Time'].dt.date
        chart_data = df_all.groupby('Date').size().reset_index(name='Emails Sent')
        chart_data = chart_data.set_index('Date')
        st.area_chart(chart_data, color="#29b5e8")

st.divider()

# --- 5. OPERATIONAL TABS ---
t1, t2, t3 = st.tabs(["➕ Add New Client", "🗄️ Client Vault & Dashboards", "📜 Master Audit Logs"])

# TAB 1: CLEAN CREATION WINDOW
with t1:
    st.subheader("Register New Agency Client")
    with st.form("new_client_form", clear_on_submit=True):
        c_name = st.text_input("Company Name")
        c_desc = st.text_area("Company Context / Value Proposition")
        col1, col2 = st.columns(2)
        c_strat = col1.selectbox("Email Strategy", ["Value-First", "Direct Pitch", "Audit"])
        c_off = col2.text_input("Specific Call to Action / Offer")
        c_email = col1.text_input("Sender Email Address")
        c_pass = col2.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Lead List", type=["csv", "xlsx"])
        c_interval = st.number_input("Days Between Automatic Sends", min_value=1, value=1)
        
        if st.form_submit_button("📁 Finalize & Save to Vault"):
            if c_name:
                df = pd.DataFrame()
                if c_leads:
                    try:
                        df = pd.read_excel(c_leads) if c_leads.name.endswith('.xlsx') else pd.read_csv(c_leads, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        for col in ['NAME','FIRST NAME','FULL NAME']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                        for col in ['EMAIL','EMAIL ADDRESS','MAIL']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                    except: st.error("Lead file processing error.")

                st.session_state.clients[c_name] = {
                    "desc": c_desc, "strategy": c_strat, "offer": c_off, "interval": c_interval, "auto_on": False,
                    "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                    "send_log": [], "last_run_time": None
                }
                save_data()
                st.success(f"Client '{c_name}' is now active in the vault!")
                st.rerun()

# TAB 2: SEPARATE CLIENT VAULT & PERFORMANCE DASHBOARDS
with t2:
    if not st.session_state.clients:
        st.info("No active clients. Use the 'Add New Client' tab to begin.")
    else:
        for name, data in list(st.session_state.clients.items()):
            with st.expander(f"📂 Client: {name}"):
                # Local Dashboard
                logs = data.get('send_log', [])
                df_l = pd.DataFrame(logs)
                
                dash_col1, dash_col2 = st.columns([1, 2])
                with dash_col1:
                    st.metric("Total Sent", len(logs))
                    st.write(f"**Automation:** {'🟢 Active' if data.get('auto_on', False) else '🔴 Paused'}")
                    st.write(f"**Cadence:** Every {data.get('interval', 1)} days")
                
                with dash_col2:
                    if not df_l.empty:
                        df_l['Time'] = pd.to_datetime(df_l['Time'])
                        df_l['Date'] = df_l['Time'].dt.date
                        l_chart = df_l.groupby('Date').size().reset_index(name='Volume')
                        st.line_chart(l_chart.set_index('Date'))
                    else:
                        st.caption("No sending history available for graph.")

                st.divider()
                
                # Management Controls
                ctrl1, ctrl2, ctrl3 = st.columns(3)
                
                if ctrl1.button("✏️ Edit Strategy", key=f"edit_btn_{name}"):
                    st.session_state.editing_client = name
                
                if st.session_state.editing_client == name:
                    with st.form(f"quick_edit_{name}"):
                        e_desc = st.text_area("Context", value=data['desc'])
                        e_off = st.text_input("Offer", value=data['offer'])
                        e_int = st.number_input("Interval", value=data.get('interval', 1))
                        e_auto = st.toggle("Enable Automation", value=data.get('auto_on', False))
                        if st.form_submit_button("Update Strategy"):
                            data.update({"desc": e_desc, "offer": e_off, "interval": e_int, "auto_on": e_auto})
                            save_data(); st.session_state.editing_client = None; st.rerun()

                if ctrl2.button("🚀 Force Manual Batch", key=f"manual_{name}"):
                    # Sidebar Groq key check
                    if 'global_groq' in st.session_state and st.session_state.global_groq:
                        for i, row in data['leads'].iterrows():
                            l_email = row.get('FINAL_EMAIL')
                            l_name = row.get('FINAL_NAME', 'Target')
                            if l_email:
                                res = send_ai_email(data, name, l_name, l_email, st.session_state.global_groq)
                                data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Sent" if res==True else res})
                        save_data(); st.success("Batch Completed."); st.rerun()
                    else: st.warning("Enter Groq Key in Sidebar first.")

                if ctrl3.button("🗑️ Terminate Client", key=f"del_btn_{name}"):
                    del st.session_state.clients[name]
                    save_data(); st.rerun()

# TAB 3: MASTER LOGS
with t3:
    if st.session_state.clients:
        sel_c = st.selectbox("Select Client Logs", list(st.session_state.clients.keys()))
        st.dataframe(pd.DataFrame(st.session_state.clients[sel_c]["send_log"]), use_container_width=True)

# --- 6. INDEPENDENT AUTOMATION LOOP ---
with st.sidebar:
    st.header("🔑 Automation Control")
    st.session_state.global_groq = st.text_input("Global Groq API Key", type="password")
    auto_master = st.toggle("Start All Active Automations")

if auto_master and st.session_state.global_groq:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            interval = data.get('interval', 1)
            last = data.get('last_run_time')
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=interval)):
                for i, row in data['leads'].iterrows():
                    l_email, l_name = row.get('FINAL_EMAIL'), row.get('FINAL_NAME', 'Target')
                    if l_email:
                        send_ai_email(data, name, l_name, l_email, st.session_state.global_groq)
                        data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Auto-Sent ✅"})
                data['last_run_time'] = datetime.now().isoformat()
                save_data()
    time.sleep(60); st.rerun()
