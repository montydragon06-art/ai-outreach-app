import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Multi-Strategy", layout="wide", page_icon="🎯")

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
t1, t2 = st.tabs(["📂 Client & Strategy Manager", "📊 Monitoring Center"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        st.subheader("Create New Client Folder")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Client Company Context", placeholder="e.g. A digital marketing agency for plumbers")
        
        st.divider()
        st.write("🎯 **Client-Specific Strategy**")
        c_strategy = st.selectbox("Outreach Framework", ["Value-First (Free Gift)", "Direct Pitch", "Problem/Solution Audit"], key="strat_new")
        c_offer = st.text_input("The Specific Offer", placeholder="e.g. Free 15-min video audit")
        
        st.divider()
        c_email = st.text_input("Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        
        if st.button("📁 Save Client & Strategy"):
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
                
                # BUNDLING EVERYTHING TOGETHER
                st.session_state.clients[c_name] = {
                    "desc": c_desc,
                    "strategy": c_strategy,
                    "offer": c_offer,
                    "leads": df,
                    "email": {"user": c_email, "pass": c_pass},
                    "logs": [f"Folder created with '{c_strategy}' strategy on {datetime.now().strftime('%d/%m/%Y')}"],
                    "last_run": None
                }
                st.success(f"Profile for {c_name} saved with unique strategy.")

    with col_b:
        st.subheader("Active Client Folders")
        if not st.session_state.clients:
            st.info("No folders found. Use the left panel to add your first client.")
        else:
            for name in st.session_state.clients.keys():
                client_data = st.session_state.clients[name]
                with st.expander(f"📂 {name} | Strategy: {client_data['strategy']}", expanded=(st.session_state.active_view == name)):
                    st.write(f"**Context:** {client_data['desc']}")
                    st.write(f"**Current Offer:** {client_data['offer']}")
                    st.write(f"**Leads Loaded:** {len(client_data['leads'])}")
                    
                    c1, c2 = st.columns(2)
                    if c1.button(f"⚡ Instant Send: {name}", key=f"inst_{name}"):
                        st.session_state.clients[name]["logs"].append(f"MANUAL SEND triggered using {client_data['strategy']} framework.")
                        st.toast(f"Running {name}'s unique campaign...")
                        
                    if c2.button(f"🔎 View Logs: {name}", key=f"view_{name}"):
                        st.session_state.active_view = name

with t2:
    if st.session_state.active_view:
        v = st.session_state.active_view
        st.header(f"Live Monitor: {v}")
        st.write(f"**Strategy in use:** {st.session_state.clients[v]['strategy']}")
        st.divider()
        for log in reversed(st.session_state.clients[v]["logs"]):
            st.caption(log)
    else:
        st.info("Select a client folder to view their campaign logs.")

# --- 4. ENGINE ---
if auto_on and groq_key:
    # Logic here would now pull client_obj['strategy'] and client_obj['offer'] 
    # for every individual email sent in the loop.
    time.sleep(10) 
    st.rerun()
