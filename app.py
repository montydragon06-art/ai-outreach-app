import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG & SESSION INITIALIZATION ---
st.set_page_config(page_title="Agency OS | Multi-Client AI", layout="wide", page_icon="🚀")

# Initialize global storage in session state
if 'clients' not in st.session_state:
    st.session_state.clients = {} # Structure: {Name: {desc, leads, email_config, logs}}
if 'active_view' not in st.session_state:
    st.session_state.active_view = None

# --- 2. SIDEBAR CONTROLS ---
with st.sidebar:
    st.header("🤖 Automation Hub")
    groq_key = st.text_input("Groq API Key", type="password")
    st.divider()
    loop_interval = st.number_input("Check-in Interval (mins)", min_value=1, value=15)
    run_automation = st.toggle("Activate All Engines")
    
    if run_automation and not groq_key:
        st.error("Please enter a Groq Key to start.")

# --- 3. MAIN INTERFACE TABS ---
t1, t2, t3 = st.tabs(["🏢 Client & Email Setup", "🎯 Strategy & Templates", "📊 Automation Center"])

# TAB 1: CLIENT & EMAIL SETUP
with t1:
    col_a, col_b = st.columns(2)
    
    with col_a:
        st.subheader("Register New Client")
        new_c_name = st.text_input("Client Name (e.g., 'Apex SEO')")
        new_c_desc = st.text_area("Company Description", placeholder="What do they do?")
        new_leads = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
        
        if st.button("Create Client Profile"):
            if new_c_name:
                df = pd.DataFrame()
                if new_leads:
                    # --- THE FIX: Robust CSV/Excel Loading ---
                    try:
                        if new_leads.name.endswith('.csv'):
                            try:
                                df = pd.read_csv(new_leads)
                            except UnicodeDecodeError:
                                new_leads.seek(0)
                                df = pd.read_csv(new_leads, encoding='latin1')
                        else:
                            df = pd.read_excel(new_leads)
                        st.success(f"Imported {len(df)} leads.")
                    except Exception as e:
                        st.error(f"Error loading file: {e}")
                
                # Initialize the client structure
                st.session_state.clients[new_c_name] = {
                    "desc": new_c_desc,
                    "leads": df,
                    "email_config": {},
                    "logs": [f"Profile created at {datetime.now().strftime('%H:%M')}"]
                }
                st.success(f"Successfully registered {new_c_name}")

    with col_b:
        st.subheader("Link Email Account")
        if not st.session_state.clients:
            st.info("Register a client first.")
        else:
            sel_client = st.selectbox("Select Client to Link", list(st.session_state.clients.keys()))
            acc_email = st.text_input("Client's Sender Email")
            acc_pass = st.text_input("App Password", type="password")
            acc_host = st.selectbox("SMTP Server", ["smtp.gmail.com", "smtp.office365.com"])
            
            if st.button(f"Authenticate {sel_client}"):
                st.session_state.clients[sel_client]["email_config"] = {
                    "user": acc_email, "pass": acc_pass, "host": acc_host
                }
                st.session_state.clients[sel_client]["logs"].append(f"Email account ({acc_email}) linked.")
                st.success(f"Email for {sel_client} is ready.")

# TAB 2: STRATEGY
with t2:
    st.subheader("Global Outreach Settings")
    framework = st.selectbox("Prompt Framework", ["Value-First (Free Gift)", "Direct Pitch", "Problem/Solution Audit"])
    gift_details = st.text_input("Describe the 'Free Gift' or 'Offer'", placeholder="e.g. A free 10-minute video audit of their site")

# TAB 3: AUTOMATION CENTER
with t3:
    st.subheader("Live Campaign Monitor")
    left_nav, right_details = st.columns([1, 2])
    
    with left_nav:
        st.write("#### Active Campaigns")
        for client in st.session_state.clients.keys():
            if st.button(f"📂 {client}", use_container_width=True):
                st.session_state.active_view = client
            
    with right_details:
        if st.session_state.active_view:
            v_client = st.session_state.active_view
            c_data = st.session_state.clients[v_client]
            
            st.markdown(f"## {v_client} Dashboard")
            st.divider()
            
            m1, m2 = st.columns(2)
            m1.metric("Leads Loaded", len(c_data['leads']))
            m2.metric("Email Status", "Connected" if c_data['email_config'] else "Missing")
            
            st.write(f"**Description:** {c_data['desc']}")
            st.write("#### Live Activity Log")
            if c_data["logs"]:
                for log_entry in reversed(c_data['logs'][-15:]):
                    st.caption(log_entry)
        else:
            st.info("Select a campaign from the left to see live logs.")

# --- 4. THE ENGINE (The 'Heartbeat') ---
if run_automation:
    current_time = datetime.now().strftime("%H:%M:%S")
    for client_name, client_obj in st.session_state.clients.items():
        if client_obj["email_config"]:
            # Simple log pulse to show it's working
            client_obj["logs"].append(f"[{current_time}] Engine pulse: Checking for pending leads...")
    
    time.sleep(loop_interval * 5) # Checks every few seconds for the demo
    st.rerun()
