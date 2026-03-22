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

# --- 2. MAILING ENGINE (STRICT PERSONALIZATION & NO HALLUCINATIONS) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_info_snippet, groq_key):
    try:
        client = Groq(api_key=groq_key)
        
        # Strict prompt to fix [Your Name] and stop fake statistics
        prompt = f"""
        Write a professional cold email from {client_name} to {lead_name}.
        
        STRICT SOURCE MATERIAL:
        - Lead Personal Fact: {lead_info_snippet}
        - Company Context: {client_info['desc']}
        - Call to Action Goal: {client_info['cta_purpose']}
        - Link to include: {client_info['cta_link']}
        - Required Tone: {client_info['cta_tone']}

        STRICT RULES:
        1. DO NOT invent any statistics, percentages, or case studies.
        2. DO NOT mention services not listed in the 'Company Context'.
        3. ONLY use the 'Lead Personal Fact' for the opening.
        4. Start with 'Hi {lead_name},'.
        5. SIGN OFF ONLY AS: 'Best regards, {client_name}'. Never use brackets [].
        6. Keep the total length under 80 words.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", # Fixed decommissioned model error
            messages=[{"role": "user", "content": prompt}]
        )
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {lead_name}"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP CONFIG ---
st.set_page_config(page_title="Agency Command Center", layout="wide", page_icon="🏢")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# --- TAB 1: ADD CLIENT (WITH CTA & INFO SUITE) ---
with t1:
    with st.form("new_client_form", clear_on_submit=True):
        colA, colB = st.columns(2)
        with colA:
            name = st.text_input("Company Name")
            desc = st.text_area("Company Context (The AI only knows what you put here)")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        with colB:
            cta_link = st.text_input("CTA Link (URL)")
            cta_purpose = st.text_input("CTA Purpose (e.g., Book a Call)")
            cta_tone = st.selectbox("CTA Tone", ["Professional", "Friendly", "Direct", "Urgent"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Leads (Needs NAME, EMAIL, INFORMATION)", type=["csv", "xlsx"])
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = pd.DataFrame()
                if leads_file:
                    try:
                        df = pd.read_excel(leads_file) if leads_file.name.endswith('.xlsx') else pd.read_csv(leads_file, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        # Mapping columns
                        for col in ['NAME','FIRST NAME']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_NAME'}); break
                        for col in ['EMAIL','EMAIL ADDRESS']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_EMAIL'}); break
                        for col in ['INFORMATION','INFO','NOTES']:
                            if col in df.columns: df = df.rename(columns={col: 'FINAL_INFO'}); break
                    except: st.error("Lead file error.")
                st.session_state.clients[name] = {
                    "desc": desc, "cta_link": cta_link, "cta_purpose": cta_purpose, "cta_tone": cta_tone,
                    "interval": interval, "leads": df, "email": {"user": email, "pass": pw},
                    "send_log": [], "last_run_time": None, "auto_on": False
                }
                save_data(); st.rerun()

# --- TAB 2: VAULT (EDIT & MANUAL SEND) ---
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 Client: {name}"):
            st.write(f"**Goal:** {data.get('cta_purpose')} | **Tone:** {data.get('cta_tone')}")
            
            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
            
            # Manual Batch Send
            if c1.button(f"🚀 Manual Batch", key=f"man_{name}"):
                if 'g_key' in st.session_state and st.session_state.g_key:
                    leads_df = data.get('leads', pd.DataFrame())
                    with st.spinner(f"Sending batch for {name}..."):
                        for i, row in leads_df.iterrows():
                            res = send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your business'), st.session_state.g_key)
                            data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('FINAL_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.success("Batch Complete!"); st.rerun()
                else: st.warning("Enter Groq Key in Sidebar.")

            # Edit Strategy Button
            if c2.button(f"✏️ Edit", key=f"edit_btn_{name}"):
                st.session_state.editing_client = name

            if c3.button(f"🗑️ Delete", key=f"del_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()
            
            auto_val = c4.toggle("Automation", value=data.get('auto_on', False), key=f"tog_{name}")
            if auto_val != data.get('auto_on'):
                data['auto_on'] = auto_val; save_data()

            # Inline Editor Form
            if st.session_state.editing_client == name:
                with st.form(f"edit_form_{name}"):
                    e_desc = st.text_area("Context", value=data['desc'])
                    e_link = st.text_input("CTA Link", value=data['cta_link'])
                    e_purp = st.text_input("CTA Purpose", value=data['cta_purpose'])
                    if st.form_submit_button("Update Strategy"):
                        data.update({"desc": e_desc, "cta_link": e_link, "cta_purpose": e_purp})
                        save_data(); st.session_state.editing_client = None; st.rerun()

# --- TAB 3: LOGS ---
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        log_df = pd.DataFrame(st.session_state.clients[sel].get("send_log", [])) # Fixed KeyError
        if not log_df.empty:
            st.table(log_df)

# --- SIDEBAR & ENGINE ---
with st.sidebar:
    st.header("⚙️ Settings")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    master_switch = st.toggle("🚀 Start Engine")

if master_switch and st.session_state.g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            last = data.get('last_run_time')
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=data.get('interval', 1))):
                leads_df = data.get('leads', pd.DataFrame())
                for i, row in leads_df.iterrows():
                    send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your business'), st.session_state.g_key)
                data['last_run_time'] = datetime.now().isoformat(); save_data()
    time.sleep(60); st.rerun()
