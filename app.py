import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Live Sender", layout="wide", page_icon="✉️")

if 'clients' not in st.session_state:
    st.session_state.clients = {} 
if 'active_view' not in st.session_state:
    st.session_state.active_view = None

# --- HELPERS ---
def send_ai_email(client_info, lead_name, lead_email, groq_key):
    """Generates AI content and sends a real email."""
    try:
        # A. Generate Content with Groq
        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a short, professional cold email from {client_info['email']['user']}.
        Target: {lead_name}
        Context: {client_info['desc']}
        Strategy: {client_info['strategy']}
        Offer: {client_info['offer']}
        Keep it under 100 words. Do not use placeholders like [Name].
        """
        
        completion = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}]
        )
        email_body = completion.choices[0].message.content

        # B. SMTP Send
        msg = MIMEMultipart()
        msg['From'] = client_info['email']['user']
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {lead_name}"
        msg.attach(MIMEText(email_body, 'plain'))

        server = smtplib.SMTP(client_info['email']['host'], 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        return str(e)

# --- 2. SIDEBAR ---
with st.sidebar:
    st.header("🤖 Global Settings")
    groq_key = st.text_input("Groq API Key", type="password")
    st.divider()
    day_interval = st.number_input("Send interval (Days)", min_value=1, value=1)
    auto_on = st.toggle("Enable All Auto-Campaigns")

# --- 3. MAIN INTERFACE ---
t1, t2 = st.tabs(["📂 Client Manager", "📜 Live Send Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        st.subheader("Add New Client Folder")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Company Context")
        c_strategy = st.selectbox("Strategy", ["Value-First (Free Gift)", "Direct Pitch", "Problem/Solution"])
        c_offer = st.text_input("The Specific Offer")
        c_email = st.text_input("Sender Email (Gmail/Outlook)")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
        
        if st.button("📁 Save Client Folder"):
            if c_name and groq_key:
                df = pd.DataFrame()
                if c_leads:
                    try:
                        df = pd.read_excel(c_leads) if c_leads.name.endswith('.xlsx') else pd.read_csv(c_leads, encoding='latin1')
                        # Clean column names to find 'Email' and 'Name' easily
                        df.columns = [c.strip().title() for c in df.columns]
                    except Exception as e: st.error(f"File error: {e}")
                
                st.session_state.clients[c_name] = {
                    "desc": c_desc, "strategy": c_strategy, "offer": c_offer,
                    "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                    "send_log": [], "last_run": None
                }
                st.success(f"Folder '{c_name}' created!")

    with col_b:
        st.subheader("Active Folders")
        for name, data in st.session_state.clients.items():
            with st.expander(f"📂 {name} - {len(data['leads'])} Leads"):
                if st.button(f"🚀 Execute Instant Batch: {name}"):
                    if not groq_key:
                        st.error("Missing Groq Key in Sidebar!")
                    elif data['leads'].empty:
                        st.warning("No leads found.")
                    else:
                        # THE REAL LOOP: Process each row in the file
                        for index, row in data['leads'].iterrows():
                            # Try to find Name and Email columns
                            l_name = row.get('Name', row.get('First Name', 'Friend'))
                            l_email = row.get('Email', None)
                            
                            if l_email:
                                result = send_ai_email(data, l_name, l_email, groq_key)
                                status = "Sent ✅" if result is True else f"Error: {result}"
                                
                                # Log the REAL data
                                st.session_state.clients[name]["send_log"].append({
                                    "Time": datetime.now().strftime("%H:%M:%S"),
                                    "Recipient": l_email,
                                    "Name": l_name,
                                    "Status": status
                                })
                        st.success(f"Batch complete for {name}!")

with t2:
    if st.session_state.clients:
        sel = st.selectbox("View Logs For:", list(st.session_state.clients.keys()))
        log_data = st.session_state.clients[sel]["send_log"]
        if log_data:
            st.table(pd.DataFrame(log_data))
            st.download_button("Download CSV", pd.DataFrame(log_data).to_csv(index=False), "logs.csv")
