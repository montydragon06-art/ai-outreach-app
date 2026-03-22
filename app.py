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

# --- 2. THE MAILING ENGINE ---
def send_ai_email(client_info, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        # Refined Prompt to ensure the name is used correctly
        prompt = f"""
        Write a short, professional cold email.
        Sender: {client_info['email']['user']}
        Recipient Name: {lead_name}
        About: {client_info['desc']}
        Strategy: {client_info['strategy']}
        Offer: {client_info['offer']}
        
        Rules:
        1. Address the recipient as {lead_name} in the greeting.
        2. Do not use any brackets like [Name].
        3. Under 80 words.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        body = completion.choices[0].message.content

        msg = MIMEMultipart()
        msg['From'] = client_info['email']['user']
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {lead_name}"
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(client_info['email']['host'], 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        return str(e)

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Fully Automated", layout="wide")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 4. SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Global Settings")
    groq_key = st.text_input("Groq API Key", type="password")
    st.divider()
    day_interval = st.number_input("Global Interval (Days)", min_value=1, value=1)
    auto_on = st.toggle("🚀 Activate Automation Loop")
    
    if st.button("Reset Storage"):
        if os.path.exists(DATA_FILE): os.remove(DATA_FILE)
        st.session_state.clients = {}
        st.rerun()

# --- 5. MAIN INTERFACE ---
t1, t2 = st.tabs(["📂 Client Manager", "📜 Live Send Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.subheader("Add/Update Client")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Context")
        c_strat = st.selectbox("Strategy", ["Value-First", "Direct Pitch", "Audit"])
        c_off = st.text_input("Specific Offer")
        c_email = st.text_input("Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Leads (Must have 'Name' and 'Email' columns)", type=["csv", "xlsx"])
        
        if st.button("📁 Save Folder"):
            if c_name:
                df = pd.DataFrame()
                if c_leads:
                    try:
                        df = pd.read_excel(c_leads) if c_leads.name.endswith('.xlsx') else pd.read_csv(c_leads, encoding='latin1')
                        df.columns = [str(c).strip().title() for c in df.columns]
                    except: st.error("File format error.")
                
                st.session_state.clients[c_name] = {
                    "desc": c_desc, "strategy": c_strat, "offer": c_off,
                    "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                    "send_log": [],
                    "last_run_time": None # Tracks the last automated send
                }
                save_data()
                st.success(f"Saved {c_name}")

    with col_b:
        st.subheader("Client Folders")
        for name, data in st.session_state.clients.items():
            with st.expander(f"📂 {name}"):
                st.write(f"**Next Run After:** { (datetime.fromisoformat(data['last_run_time']) + timedelta(days=day_interval)).strftime('%Y-%m-%d %H:%M') if data['last_run_time'] else 'Pending'}")
                
                if st.button(f"⚡ Instant Send: {name}"):
                    for i, row in data['leads'].iterrows():
                        email = row.get('Email')
                        name_lead = row.get('Name', row.get('First Name', 'Friend'))
                        res = send_ai_email(data, name_lead, email, groq_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": email, "Name": name_lead, "Status": "Sent" if res==True else res})
                    save_data()
                    st.rerun()

with t2:
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel]["send_log"]))

# --- 6. AUTOMATION LOOP (The Timer) ---
if auto_on and groq_key:
    for name, data in st.session_state.clients.items():
        # Check if enough time has passed
        should_send = False
        if data['last_run_time'] is None:
            should_send = True
        else:
            last_run = datetime.fromisoformat(data['last_run_time'])
            if datetime.now() > (last_run + timedelta(days=day_interval)):
                should_send = True
        
        if should_send:
            for i, row in data['leads'].iterrows():
                email = row.get('Email'); name_lead = row.get('Name', 'Friend')
                if email:
                    res = send_ai_email(data, name_lead, email, groq_key)
                    data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": email, "Name": name_lead, "Status": "Sent ✅"})
            
            data['last_run_time'] = datetime.now().isoformat()
            save_data()
            st.toast(f"Automated batch completed for {name}")
    
    time.sleep(60) # Wait 1 minute before checking the clock again
    st.rerun()
