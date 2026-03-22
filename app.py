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

# --- 1. DATA PERSISTENCE HELPERS ---
DATA_FILE = "agency_data.json"

def save_data():
    """Saves the entire clients dictionary to a JSON file."""
    # We convert dataframes to JSON strings because JSON can't store DataFrames directly
    serializable_data = {}
    for name, info in st.session_state.clients.items():
        serializable_data[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            serializable_data[name]['leads'] = info['leads'].to_json()
    
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_data, f)

def load_data():
    """Loads data from the JSON file into session state."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            raw_data = json.load(f)
            for name, info in raw_data.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info

# --- 2. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Permanent", layout="wide", page_icon="💾")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data() # Load everything from the file on startup

# --- HELPERS ---
def send_ai_email(client_info, lead_name, lead_email, groq_key):
    try:
        client = Groq(api_key=groq_key)
        prompt = f"Write a short cold email to {lead_name} about {client_info['offer']} for {client_info['desc']}. Use {client_info['strategy']} style. Under 80 words."
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        body = completion.choices[0].message.content
        msg = MIMEMultipart(); msg['From'] = client_info['email']['user']; msg['To'] = lead_email; msg['Subject'] = f"Hi {lead_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(client_info['email']['host'], 587); server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. SIDEBAR ---
with st.sidebar:
    st.header("🤖 Settings")
    groq_key = st.text_input("Groq API Key", type="password")
    if st.button("Clear All Stored Data"):
        if os.path.exists(DATA_FILE): os.remove(DATA_FILE)
        st.session_state.clients = {}
        st.rerun()

# --- 4. MAIN INTERFACE ---
t1, t2 = st.tabs(["📂 Manager", "📜 Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.subheader("New Client Folder")
        c_name = st.text_input("Name")
        c_desc = st.text_area("Context")
        c_strat = st.selectbox("Strategy", ["Value-First", "Direct", "Audit"])
        c_off = st.text_input("Offer")
        c_email = st.text_input("Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Leads", type=["csv", "xlsx"])
        
        if st.button("📁 Save to Permanent Storage"):
            if c_name:
                df = pd.DataFrame()
                if c_leads:
                    try:
                        df = pd.read_excel(c_leads) if c_leads.name.endswith('.xlsx') else pd.read_csv(c_leads, encoding='latin1')
                        df.columns = [str(c).strip().title() for c in df.columns]
                    except: pass
                
                st.session_state.clients[c_name] = {
                    "desc": c_desc, "strategy": c_strat, "offer": c_off,
                    "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                    "send_log": []
                }
                save_data() # Save to JSON file
                st.success("Data saved and backed up!")

    with col_b:
        st.subheader("Client Folders")
        for name, data in st.session_state.clients.items():
            with st.expander(f"📂 {name}"):
                if st.button(f"🚀 Run Batch: {name}"):
                    for i, row in data['leads'].iterrows():
                        email = row.get('Email'); name_lead = row.get('Name', 'Friend')
                        if email:
                            res = send_ai_email(data, name_lead, email, groq_key)
                            data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": email, "Status": "Sent" if res==True else res})
                    save_data() # Save logs to JSON after sending
                    st.success("Batch finished and logged.")

with t2:
    if st.session_state.clients:
        sel = st.selectbox("Logs", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel]["send_log"]))
