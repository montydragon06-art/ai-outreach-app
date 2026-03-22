import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- APP CONFIG ---
st.set_page_config(page_title="Agency Automation Pro", layout="wide")

# --- INITIALIZE MULTI-CLIENT STORAGE ---
if 'clients' not in st.session_state:
    st.session_state.clients = {} # Stores {client_name: {desc: "", leads: df}}
if 'logs' not in st.session_state:
    st.session_state.logs = []

# --- SIDEBAR: SETTINGS ---
with st.sidebar:
    st.header("⚙️ Global Settings")
    groq_api_key = st.text_input("Groq API Key", type="password")
    
    st.divider()
    st.header("⏳ Automation Timer")
    interval = st.number_input("Send interval (minutes)", min_value=1, value=60)
    is_running = st.toggle("Activate Automation")

# --- MAIN INTERFACE ---
tab1, tab2, tab3 = st.tabs(["👥 Client Manager", "📧 Campaign Editor", "🤖 Automation Center"])

with tab1:
    st.subheader("Manage Client Profiles")
    c_name = st.text_input("Client/Company Name")
    c_desc = st.text_area("Company Value Proposition / Description")
    
    uploaded_leads = st.file_uploader("Add Leads for this Client", type=["csv", "xlsx"])
    
    if st.button("Save/Update Client Profile"):
        if c_name and c_desc:
            leads_df = pd.read_csv(uploaded_leads) if uploaded_leads else pd.DataFrame()
            st.session_state.clients[c_name] = {"desc": c_desc, "leads": leads_df}
            st.success(f"Profile for {c_name} saved!")

with tab2:
    st.subheader("Outreach Strategy")
    strategy = st.selectbox("Email Template Style", [
        "The Free Gift (Value First)",
        "The Direct Audit (Problem/Solution)",
        "The Case Study (Social Proof)",
        "Custom Instructions"
    ])
    custom_instr = st.text_area("Specific Instructions (e.g., 'Mention we are giving a free 30-min SEO audit')")

with tab3:
    st.subheader("Live Operations")
    if not st.session_state.clients:
        st.info("Add a client in Tab 1 to start.")
    else:
        active_clients = st.multiselect("Select Clients to Run", list(st.session_state.clients.keys()))
        
        if is_running:
            st.warning("🚀 Automation is LIVE. Do not close this tab.")
            
            # Simple Automation Loop
            while is_running:
                now = datetime.now().strftime("%H:%M:%S")
                for client in active_clients:
                    data = st.session_state.clients[client]
                    st.write(f"[{now}] Processing {client}...")
                    
                    # Logic to pick 1-5 leads that haven't been sent to yet 
                    # (In a real app, you'd track 'Sent' status in the dataframe)
                    
                    # Example placeholders for the log
                    st.session_state.logs.append(f"Sent batch for {client} at {now}")
                
                time.sleep(interval * 60)
                st.rerun()

    st.divider()
    st.write("### Activity Logs")
    for log in reversed(st.session_state.logs[-10:]):
        st.text(log)
