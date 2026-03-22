import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. APP CONFIG ---
st.set_page_config(page_title="Agency OS | Fixed & Live", layout="wide", page_icon="🚀")

if 'clients' not in st.session_state:
    st.session_state.clients = {} 
if 'active_view' not in st.session_state:
    st.session_state.active_view = None

# --- HELPERS ---
def send_ai_email(client_info, lead_name, lead_email, groq_key):
    """Generates AI content using the new Llama 3.1 model and sends via SMTP."""
    try:
        # A. Generate Content with Groq (Using the updated 3.1 model)
        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a short, professional cold email from {client_info['email']['user']}.
        Target Name: {lead_name}
        Our Company Context: {client_info['desc']}
        Campaign Strategy: {client_info['strategy']}
        Our Specific Offer: {client_info['offer']}
        
        Rules:
        - Keep it under 100 words.
        - No placeholders like [Name].
        - Sound helpful, not salesy.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
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
t1, t2 = st.tabs(["📂 Client & Strategy Manager", "📜 Detailed Send Logs"])

with t1:
    col_a, col_b = st.columns([1, 2])
    
    with col_a:
        st.subheader("Create New Client Folder")
        c_name = st.text_input("Client Name")
        c_desc = st.text_area("Company Context")
        c_strategy = st.selectbox("Strategy", ["Value-First (Free Gift)", "Direct Pitch", "Problem/Solution"])
        c_offer = st.text_input("The Specific Offer")
        c_email = st.text_input("Sender Email")
        c_pass = st.text_input("App Password", type="password")
        c_leads = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
        
        if st.button("📁 Save Client Folder"):
            if c_name:
                df = pd.DataFrame()
                if c_leads:
                    # THE FIX: Handles Excel and various CSV encodings to prevent UnicodeDecodeError
                    try:
                        if c_leads.name.endswith('.xlsx'):
                            df = pd.read_excel(c_leads)
                        else:
                            try:
                                df = pd.read_csv(c_leads)
                            except UnicodeDecodeError:
                                c_leads.seek(0)
                                df = pd.read_csv(c_leads, encoding='latin1')
                        
                        # Clean column names for easier matching
                        df.columns = [str(c).strip().title() for c in df.columns]
                        st.success(f"Imported {len(df)} leads successfully!")
                    except Exception as e:
                        st.error(f"File import error: {e}")
                
                st.session_state.clients[c_name] = {
                    "desc": c_desc, "strategy": c_strategy, "offer": c_offer,
                    "leads": df, "email": {"user": c_email, "pass": c_pass, "host": "smtp.gmail.com" if "gmail" in c_email else "smtp.office365.com"},
                    "send_log": []
                }
                st.success(f"Folder '{c_name}' created.")

    with col_b:
        st.subheader("Active Client Folders")
        if not st.session_state.clients:
            st.info("No folders found.")
        else:
            for name, data in st.session_state.clients.items():
                with st.expander(f"📂 {name} ({len(data['leads'])} leads)"):
                    if st.button(f"🚀 Execute Instant Batch: {name}", key=f"run_{name}"):
                        if not groq_key:
                            st.error("Please enter your Groq Key in the sidebar.")
                        elif data['leads'].empty:
                            st.warning("No leads found in this folder.")
                        else:
                            # Loop through the actual file data
                            for index, row in data['leads'].iterrows():
                                l_email = row.get('Email', None)
                                l_name = row.get('Name', row.get('First Name', 'Friend'))
                                
                                if l_email:
                                    res = send_ai_email(data, l_name, l_email, groq_key)
                                    status = "Sent ✅" if res is True else f"Error: {res}"
                                    
                                    # LOG THE ACTUAL RECIPIENT
                                    st.session_state.clients[name]["send_log"].append({
                                        "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "Recipient": l_email,
                                        "Name": l_name,
                                        "Status": status
                                    })
                            st.success(f"Finished processing batch for {name}")

with t2:
    st.subheader("Client Send History")
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        history = st.session_state.clients[sel]["send_log"]
        if history:
            st.table(pd.DataFrame(history))
        else:
            st.warning("No history for this client yet.")
