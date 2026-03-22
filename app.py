import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG ---
st.set_page_config(page_title="Agency OS | automated", layout="wide", page_icon="📂")

if 'clients' not in st.session_state:
    st.session_state.clients = {} 
if 'active_view' not in st.session_state:
    st.session_state.active_view = None

# --- 2. SIDEBAR: AUTOMATION ---
with st.sidebar:
    st.header("🤖 Automation Hub")
    groq_key = st.text_input("Groq API Key", type="password")
    st.divider()
    # Changed from minutes to Days
    day_interval = st.number_input("Send every X days", min_value=1, value=1)
    auto_on = st.toggle("Enable Scheduled Sending")
    
    if auto_on:
        next_run = datetime.now() + timedelta(days=day_interval)
        st.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M')}")

# --- 3. MAIN INTERFACE ---
t1, t2, t3 = st.tabs(["📂 Client Manager", "🎯 Strategy", "📊 Monitoring"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        st.subheader("Add to Client Folder")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Client Context")
        c_email = st.text_input("Client Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        
        if st.button("📁 Create Client Folder"):
            if c_name:
                # Robust File Loading
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
                
                st.session_state.clients[c_name] = {
                    "desc": c_desc,
                    "leads": df,
                    "email": {"user": c_email, "pass": c_pass},
                    "logs": [f"Folder created {datetime.now().strftime('%d/%m/%Y')}"],
                    "last_run": None
                }
                st.success(f"Folder '{c_name}' added to manager.")

    with col_b:
        st.subheader("Client Folders")
        if not st.session_state.clients:
            st.info("No folders found. Add a client to the left.")
        else:
            for name in st.session_state.clients.keys():
                with st.expander(f"📂 {name}", expanded=(st.session_state.active_view == name)):
                    st.write(f"**Context:** {st.session_state.clients[name]['desc']}")
                    st.write(f"**Leads:** {len(st.session_state.clients[name]['leads'])} loaded")
                    
                    # --- TWO DISTINCT BUTTONS ---
                    c1, c2 = st.columns(2)
                    if c1.button(f"⚡ Instant Send: {name}", key=f"inst_{name}"):
                        # Logic for immediate execution
                        st.session_state.clients[name]["logs"].append(f"MANUAL SEND triggered at {datetime.now()}")
                        st.toast(f"Starting instant mail-out for {name}...")
                        
                    if c2.button(f"🔎 View Logs: {name}", key=f"view_{name}"):
                        st.session_state.active_view = name

with t2:
    st.subheader("Outreach Template")
    offer = st.text_input("What is the free gift/offer?", value="A free audit")

with t3:
    if st.session_state.active_view:
        v = st.session_state.active_view
        st.header(f"Monitoring: {v}")
        for log in reversed(st.session_state.clients[v]["logs"]):
            st.caption(log)
    else:
        st.info("Select a folder in Client Manager to view details.")

# --- 4. ENGINE ---
if auto_on and groq_key:
    # In a real scenario, this would check if (Current Time - Last Run) > day_interval
    time.sleep(10) # Checks every 10 seconds while tab is open
    st.rerun()
