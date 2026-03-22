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

# --- 2. ENHANCED MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_info_snippet, groq_key):
    try:
        client = Groq(api_key=groq_key)
        
        # Hyper-personalized prompt using the new fields
        prompt = f"""
        Write a high-converting cold email from {client_name} to {lead_name}.
        
        LEAD SPECIFIC INFO: {lead_info_snippet}
        CLIENT CONTEXT: {client_info['desc']}
        
        CALL TO ACTION:
        - Link: {client_info['cta_link']}
        - Purpose: {client_info['cta_purpose']}
        - Tone of CTA: {client_info['cta_tone']}
        
        RULES:
        1. Mention the 'LEAD SPECIFIC INFO' naturally to show you've done research.
        2. Ensure the email flows toward the '{client_info['cta_purpose']}' using the '{client_info['cta_tone']}' tone.
        3. Include the link: {client_info['cta_link']}
        4. Keep it under 100 words. Start 'Hi {lead_name},'.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
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

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Command Center", layout="wide")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: CREATION (Updated with CTA Suite)
with t1:
    st.subheader("Register New Client & CTA Strategy")
    with st.form("new_client_form", clear_on_submit=True):
        colA, colB = st.columns(2)
        with colA:
            name = st.text_input("Company Name")
            desc = st.text_area("Company Context (What do you do?)")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        
        with colB:
            cta_link = st.text_input("CTA Link (URL)")
            cta_purpose = st.text_input("CTA Purpose (e.g. Book an appointment, Watch a demo)")
            cta_tone = st.selectbox("CTA Tone", ["Professional", "Friendly/Casual", "Direct/Urgent", "Value-Driven"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Upload Leads (Must have NAME, EMAIL, INFORMATION columns)", type=["csv", "xlsx"])
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = pd.DataFrame()
                if leads_file:
                    try:
                        df = pd.read_excel(leads_file) if leads_file.name.endswith('.xlsx') else pd.read_csv(leads_file, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        
                        # Mapping Logic
                        for col in ['NAME','FIRST NAME','FULL NAME']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                        for col in ['EMAIL','EMAIL ADDRESS','MAIL']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                        for col in ['INFORMATION','INFO','NOTES','BIO']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_INFO'}); break
                    except: st.error("Error processing leads file.")

                st.session_state.clients[name] = {
                    "desc": desc, "cta_link": cta_link, "cta_purpose": cta_purpose, "cta_tone": cta_tone,
                    "interval": interval, "leads": df, "email": {"user": email, "pass": pw},
                    "send_log": [], "last_run_time": None, "auto_on": False
                }
                save_data()
                st.success(f"Client {name} saved with personalized CTA settings.")
                st.rerun()

# TAB 2: VAULT
with t2:
    if not st.session_state.clients:
        st.info("No clients in vault.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 Client: {name}"):
            st.write(f"**CTA Goal:** {data.get('cta_purpose')} ({data.get('cta_tone')})")
            st.write(f"**Target Link:** {data.get('cta_link')}")
            
            c1, c2 = st.columns(2)
            if c1.button(f"🗑️ Delete {name}", key=f"del_{name}"):
                del st.session_state.clients[name]
                save_data(); st.rerun()
            
            auto_val = c2.toggle("Automation Active", value=data.get('auto_on', False), key=f"tog_{name}")
            if auto_val != data.get('auto_on'):
                data['auto_on'] = auto_val
                save_data()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("View History For:", list(st.session_state.clients.keys()))
        log_data = pd.DataFrame(st.session_state.clients[sel]["send_log"])
        if not log_data.empty: st.table(log_data)

# --- 4. AUTOMATION ENGINE ---
with st.sidebar:
    st.header("⚙️ Settings")
    g_key = st.text_input("Groq API Key", type="password")
    master_switch = st.toggle("🚀 Start Engine")

if master_switch and g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            last = data.get('last_run_time')
            interval_days = data.get('interval', 1)
            
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=interval_days)):
                leads_df = data.get('leads', pd.DataFrame())
                if not leads_df.empty:
                    for i, row in leads_df.iterrows():
                        l_email = row.get('FINAL_EMAIL')
                        l_name = row.get('FINAL_NAME', 'Target')
                        l_info = row.get('FINAL_INFO', 'a leader in your industry') # Fallback if empty
                        
                        res = send_personalized_email(data, name, l_name, l_email, l_info, g_key)
                        
                        data["send_log"].append({
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Recipient": l_email,
                            "Status": "Sent ✅" if res == True else f"Error: {res}"
                        })
                    
                    data['last_run_time'] = datetime.now().isoformat()
                    save_data()
    time.sleep(60)
    st.rerun()
