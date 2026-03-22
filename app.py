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
        prompt = f"""
        Company: {client_name}
        Recipient: {lead_name}
        Context: {client_info['desc']}
        Strategy: {client_info['strategy']}
        Offer: {client_info['offer']}
        Rules: Start 'Hi {lead_name},'. Sign off as '{client_name}'. Under 80 words. Professional tone.
        """
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

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Automation OS", layout="wide", page_icon="🏢")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

# --- 4. SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Global Settings")
    groq_key = st.text_input("Groq API Key", type="password")
    day_interval = st.number_input("Send interval (Days)", min_value=1, value=1)
    auto_on = st.toggle("🚀 Activate Automation Loop")
    st.divider()
    st.info("Keep this tab open for the automation to run.")

# --- 5. MAIN INTERFACE ---
t1, t2 = st.tabs(["📂 Client Manager", "📜 Live Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        edit_mode = st.session_state.editing_client is not None
        st.subheader("📝 Edit Client" if edit_mode else "➕ Add New Client")
        
        current_client = st.session_state.clients.get(st.session_state.editing_client, {}) if edit_mode else {}

        # Use keys to allow manual clearing
        new_name = st.text_input("Company Name", value=st.session_state.editing_client if edit_mode else "", key="name_input")
        new_desc = st.text_area("Context (What do they do?)", value=current_client.get("desc", ""), key="desc_input")
        new_strat = st.selectbox("Strategy", ["Value-First", "Direct Pitch", "Audit"], 
                                index=["Value-First", "Direct Pitch", "Audit"].index(current_client.get("strategy", "Value-First")), key="strat_input")
        new_offer = st.text_input("Specific Offer", value=current_client.get("offer", ""), key="offer_input")
        new_email = st.text_input("Sender Email", value=current_client.get("email", {}).get("user", ""), key="email_input")
        new_pass = st.text_input("App Password", type="password", value=current_client.get("email", {}).get("pass", ""), key="pass_input")
        new_leads = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])

        c1, c2 = st.columns(2)
        if c1.button("💾 Save Folder", use_container_width=True):
            if new_name:
                df = current_client.get("leads", pd.DataFrame())
                if new_leads:
                    try:
                        df = pd.read_excel(new_leads) if new_leads.name.endswith('.xlsx') else pd.read_csv(new_leads, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        # Column Mapping Logic
                        for col in ['NAME','FIRST NAME','FULL NAME','CONTACT']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                        for col in ['EMAIL','EMAIL ADDRESS','MAIL']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                    except: st.error("Error loading file.")

                st.session_state.clients[new_name] = {
                    "desc": new_desc, "strategy": new_strat, "offer": new_offer,
                    "leads": df, "email": {"user": new_email, "pass": new_pass, "host": "smtp.gmail.com" if "gmail" in new_email else "smtp.office365.com"},
                    "send_log": current_client.get("send_log", []), 
                    "last_run_time": current_client.get("last_run_time", None)
                }
                save_data()
                st.session_state.editing_client = None
                st.rerun()

        if edit_mode:
            if c2.button("❌ Cancel Edit", use_container_width=True):
                st.session_state.editing_client = None
                st.rerun()

    with col_b:
        st.subheader("Active Folders")
        if not st.session_state.clients:
            st.info("No clients found. Add one on the left.")
        
        for name, data in list(st.session_state.clients.items()):
            with st.expander(f"📂 {name}"):
                b1, b2, b3 = st.columns(3)
                
                if b1.button(f"✏️ Edit", key=f"ed_{name}"):
                    st.session_state.editing_client = name
                    st.rerun()
                
                if b2.button(f"🚀 Send Now", key=f"run_{name}"):
                    if 'FINAL_EMAIL' in data['leads'].columns:
                        for i, row in data['leads'].iterrows():
                            l_email = row['FINAL_EMAIL']
                            l_name = row.get('FINAL_NAME', 'Target')
                            res = send_ai_email(data, name, l_name, l_email, groq_key)
                            data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Sent" if res==True else res})
                        save_data()
                        st.success("Batch Sent")
                    else: st.error("No Email column found.")

                # DELETE BUTTON with confirmation
                if b3.button(f"🗑️ Delete", key=f"del_{name}"):
                    del st.session_state.clients[name]
                    save_data()
                    st.rerun()

# --- 6. LOGS & AUTOMATION LOOP ---
with t2:
    if st.session_state.clients:
        sel = st.selectbox("View History For:", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel]["send_log"]))

if auto_on and groq_key:
    for name, data in st.session_state.clients.items():
        should_send = False
        if data['last_run_time'] is None: should_send = True
        else:
            last_run = datetime.fromisoformat(data['last_run_time'])
            if datetime.now() > (last_run + timedelta(days=day_interval)): should_send = True
        
        if should_send and 'FINAL_EMAIL' in data['leads'].columns:
            for i, row in data['leads'].iterrows():
                l_email = row['FINAL_EMAIL']
                l_name = row.get('FINAL_NAME', 'Target')
                res = send_ai_email(data, name, l_name, l_email, groq_key)
                data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": l_email, "Name": l_name, "Status": "Auto-Sent ✅"})
            data['last_run_time'] = datetime.now().isoformat()
            save_data()
    time.sleep(60)
    st.rerun()
