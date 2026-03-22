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

# --- 2. THE MAILING ENGINE (PERSONALIZED & SIGNATURE FIXED) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_info_snippet, groq_key):
    try:
        client = Groq(api_key=groq_key)
        
        # Updated prompt with strict signature rules to fix [Your Name] issues
        prompt = f"""
        Write a short, high-converting cold email from {client_name} to {lead_name}.
        
        LEAD DATA: {lead_info_snippet}
        CLIENT CONTEXT: {client_info['desc']}
        
        CALL TO ACTION:
        - Link to use: {client_info['cta_link']}
        - Action Goal: {client_info['cta_purpose']}
        - Voice Tone: {client_info['cta_tone']}
        
        STRICT RULES:
        1. Start with 'Hi {lead_name},'.
        2. Use the 'LEAD DATA' to show you've researched them personally.
        3. Transition to the '{client_info['cta_purpose']}' using a '{client_info['cta_tone']}' tone.
        4. Include the link: {client_info['cta_link']}.
        5. SIGN OFF ONLY WITH: 'Best regards, {client_name}'.
        6. DO NOT use brackets like [Your Name] or [Link]. Use the actual text provided.
        7. Total length: Under 90 words.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", # Fix for decommissioned model
            messages=[{"role": "user", "content": prompt}]
        )
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Question for {lead_name}"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP CONFIG & STATE ---
st.set_page_config(page_title="Agency OS", layout="wide", page_icon="📂")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# --- TAB 1: ADD CLIENT (WITH CTA SUITE) ---
with t1:
    st.subheader("New Strategy Setup")
    with st.form("new_client_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Company Name")
            desc = st.text_area("What does this company do?")
            email_user = st.text_input("Sender Email Address")
            email_pass = st.text_input("App Password", type="password")
        
        with col2:
            cta_url = st.text_input("CTA Link (URL)")
            cta_goal = st.text_input("CTA Purpose (e.g., Book an Appointment)")
            cta_voice = st.selectbox("CTA Tone", ["Professional", "Casual", "Urgent", "Value-Driven"])
            send_interval = st.number_input("Days Between Sends", min_value=1, value=1)
            file = st.file_uploader("Upload Leads (CSV/XLSX)", type=["csv", "xlsx"])
            
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                leads_df = pd.DataFrame()
                if file:
                    try:
                        leads_df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
                        leads_df.columns = [str(c).strip().upper() for c in leads_df.columns]
                        # Mapping columns to ensure personalization works
                        for c in ['NAME','FIRST NAME','FULL NAME']:
                            if c in leads_df.columns: leads_df = leads_df.rename(columns={c: 'FINAL_NAME'}); break
                        for c in ['EMAIL','EMAIL ADDRESS','MAIL']:
                            if c in leads_df.columns: leads_df = leads_df.rename(columns={c: 'FINAL_EMAIL'}); break
                        for c in ['INFORMATION','INFO','NOTES','DATA']:
                            if c in leads_df.columns: leads_df = leads_df.rename(columns={c: 'FINAL_INFO'}); break
                    except: st.error("Error loading lead file.")

                st.session_state.clients[name] = {
                    "desc": desc, "cta_link": cta_url, "cta_purpose": cta_goal, "cta_tone": cta_voice,
                    "interval": send_interval, "leads": leads_df, "email": {"user": email_user, "pass": email_pass},
                    "send_log": [], "last_run_time": None, "auto_on": False
                }
                save_data(); st.success(f"{name} added!"); st.rerun()

# --- TAB 2: CLIENT VAULT (EDIT & MANUAL SEND) ---
with t2:
    if not st.session_state.clients:
        st.info("No clients found.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {name}"):
            st.write(f"**Goal:** {data.get('cta_purpose')} | **Tone:** {data.get('cta_tone')}")
            
            # Action Buttons
            b1, b2, b3, b4 = st.columns(4)
            
            if b1.button(f"🚀 Manual Batch", key=f"m_{name}"):
                if 'g_key' in st.session_state and st.session_state.g_key:
                    df = data.get('leads', pd.DataFrame())
                    with st.spinner("Processing manual batch..."):
                        for i, row in df.iterrows():
                            res = send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your business'), st.session_state.g_key)
                            data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('FINAL_EMAIL'), "Name": row.get('FINAL_NAME'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.success("Batch Sent!"); st.rerun()
                else: st.warning("Enter Groq Key in Sidebar.")

            if b2.button(f"✏️ Edit", key=f"e_{name}"):
                st.session_state.editing_client = name

            if b3.button(f"🗑️ Delete", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()
            
            auto = b4.toggle("Automation", value=data.get('auto_on', False), key=f"t_{name}")
            if auto != data.get('auto_on'):
                data['auto_on'] = auto; save_data()

            # Inline Edit Form
            if st.session_state.editing_client == name:
                st.divider()
                with st.form(f"f_edit_{name}"):
                    new_desc = st.text_area("Context", value=data['desc'])
                    new_link = st.text_input("CTA Link", value=data['cta_link'])
                    new_purp = st.text_input("CTA Purpose", value=data['cta_purpose'])
                    if st.form_submit_button("Update Strategy"):
                        data.update({"desc": new_desc, "cta_link": new_link, "cta_purpose": new_purp})
                        save_data(); st.session_state.editing_client = None; st.rerun()

# --- TAB 3: LOGS ---
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Client Log:", list(st.session_state.clients.keys()))
        log_df = pd.DataFrame(st.session_state.clients[sel]["send_log"])
        # Robust handling for empty or missing 'Time' to prevent ValueErrors
        if not log_df.empty:
            st.table(log_df)

# --- SIDEBAR & AUTOMATION ENGINE ---
with st.sidebar:
    st.header("⚙️ Settings")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    master_run = st.toggle("🚀 Start Global Engine")

if master_run and st.session_state.g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            # Check interval logic
            last = data.get('last_run_time')
            int_val = data.get('interval', 1)
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=int_val)):
                df = data.get('leads', pd.DataFrame())
                for i, row in df.iterrows():
                    send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your industry'), st.session_state.g_key)
                    data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('FINAL_EMAIL'), "Name": row.get('FINAL_NAME'), "Status": "Auto ✅"})
                data['last_run_time'] = datetime.now().isoformat(); save_data()
    time.sleep(60); st.rerun()
