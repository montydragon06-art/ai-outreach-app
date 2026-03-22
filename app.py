import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Multi-Client", layout="wide", page_icon="📈")

if 'clients' not in st.session_state:
    st.session_state.clients = {} 
if 'active_view' not in st.session_state:
    st.session_state.active_view = None

# --- 2. SIDEBAR: GLOBAL CONTROLS ---
with st.sidebar:
    st.header("🤖 Automation Hub")
    groq_key = st.text_input("Groq API Key", type="password")
    st.divider()
    day_interval = st.number_input("Send every X days", min_value=1, value=1)
    auto_on = st.toggle("Enable Scheduled Sending")
    
    if auto_on:
        next_run = datetime.now() + timedelta(days=day_interval)
        st.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M')}")

# --- 3. MAIN INTERFACE ---
t1, t2 = st.tabs(["📂 Client & Strategy Manager", "📜 Detailed Send Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        st.subheader("Create New Client Folder")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Client Company Context")
        
        st.write("🎯 **Strategy**")
        c_strategy = st.selectbox("Framework", ["Value-First (Free Gift)", "Direct Pitch", "Problem/Solution Audit"])
        c_offer = st.text_input("Specific Offer")
        
        st.write("📧 **Mail Settings**")
        c_email = st.text_input("Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        
        if st.button("📁 Save Client Folder"):
            if c_name:
                df = pd.DataFrame()
                if c_leads:
                    try:
                        if c_leads.name.endswith('.csv'):
                            try:
                                df = pd.read_csv(c_leads)
                            except:
                                c_leads.seek(0)
                                df = pd.read_csv(c_leads, encoding='latin1')
                        else:
                            df = pd.read_excel(c_leads)
                    except Exception as e:
                        st.error(f"File error: {e}")
                
                # Initialize client with an empty 'send_log' list
                st.session_state.clients[c_name] = {
                    "desc": c_desc,
                    "strategy": c_strategy,
                    "offer": c_offer,
                    "leads": df,
                    "email": {"user": c_email, "pass": c_pass},
                    "send_log": [], # List of dicts: {"time": ..., "recipient": ..., "status": ...}
                    "last_run": None
                }
                st.success(f"Folder '{c_name}' is live.")

    with col_b:
        st.subheader("Active Client Folders")
        if not st.session_state.clients:
            st.info("No folders found.")
        else:
            for name in st.session_state.clients.keys():
                client_data = st.session_state.clients[name]
                with st.expander(f"📂 {name} ({len(client_data['leads'])} leads)"):
                    st.write(f"**Strategy:** {client_data['strategy']}")
                    st.write(f"**Offer:** {client_data['offer']}")
                    
                    if st.button(f"⚡ Instant Send: {name}", key=f"inst_{name}"):
                        # Dummy Logic: In the real version, this triggers the SMTP loop
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        # Simulate logging the first lead for demo
                        new_entry = {"Time": timestamp, "Recipient": "example@lead.com", "Status": "Sent ✅"}
                        st.session_state.clients[name]["send_log"].append(new_entry)
                        st.toast(f"Manual batch started for {name}")

with t2:
    st.subheader("Client Send History")
    if not st.session_state.clients:
        st.info("Register a client to see logs.")
    else:
        log_client = st.selectbox("Select Client to View Logs", list(st.session_state.clients.keys()))
        
        history = st.session_state.clients[log_client]["send_log"]
        
        if history:
            # Display logs as a clean table
            log_df = pd.DataFrame(history)
            st.table(log_df)
            
            # Option to download these specific logs
            csv_logs = log_df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Log CSV", csv_logs, f"{log_client}_logs.csv", "text/csv")
        else:
            st.warning(f"No emails have been sent for {log_client} yet.")

# --- 4. ENGINE ---
if auto_on and groq_key:
    # Every time the engine 'pulses', it would process one lead per client
    # and add a row to st.session_state.clients[client_name]["send_log"]
    time.sleep(10) 
    st.rerun()
