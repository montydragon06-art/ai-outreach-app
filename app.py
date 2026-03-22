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

# --- 2. PERSONALIZED MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_info_snippet, groq_key):
    try:
        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a high-converting cold email from {client_name} to {lead_name}.
        LEAD SPECIFIC INFO: {lead_info_snippet}
        CLIENT CONTEXT: {client_info['desc']}
        CALL TO ACTION:
        - Link: {client_info['cta_link']}
        - Purpose: {client_info['cta_purpose']}
        - Tone: {client_info['cta_tone']}
        RULES:
        1. Mention 'LEAD SPECIFIC INFO' naturally.
        2. Flow toward '{client_info['cta_purpose']}' using a '{client_info['cta_tone']}' tone.
        3. Include the link: {client_info['cta_link']}
        4. Under 100 words. Start 'Hi {lead_name},'.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
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

# --- 3. APP CONFIG & STATE ---
st.set_page_config(page_title="Agency Command Center", layout="wide")

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: CREATION
with t1:
    with st.form("new_client_form", clear_on_submit=True):
        colA, colB = st.columns(2)
        with colA:
            name = st.text_input("Company Name")
            desc = st.text_area("Company Context")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        with colB:
            cta_link = st.text_input("CTA Link (URL)")
            cta_purpose = st.text_input("CTA Purpose")
            cta_tone = st.selectbox("CTA Tone", ["Professional", "Friendly", "Direct", "Urgent"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Upload Leads (NAME, EMAIL, INFORMATION)", type=["csv", "xlsx"])
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = pd.DataFrame()
                if leads_file:
                    try:
                        df = pd.read_excel(leads_file) if leads_file.name.endswith('.xlsx') else pd.read_csv(leads_file, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
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

# TAB 2: VAULT (Manual Send & Edit Added)
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 Client: {name}"):
            # Info Display
            st.write(f"**Current Goal:** {data.get('cta_purpose')} | **Tone:** {data.get('cta_tone')}")
            
            # Action Buttons
            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
            
            if c1.button(f"🚀 Manual Batch", key=f"man_{name}"):
                if 'g_key' in st.session_state and st.session_state.g_key:
                    leads_df = data.get('leads', pd.DataFrame())
                    if not leads_df.empty:
                        with st.spinner(f"Sending batch..."):
                            for i, row in leads_df.iterrows():
                                res = send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your business'), st.session_state.g_key)
                                data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('FINAL_EMAIL'), "Status": "Manual Sent ✅" if res == True else f"Error: {res}"})
                        save_data(); st.success("Batch Complete!"); st.rerun()
                else: st.warning("Enter Groq Key in Sidebar.")

            if c2.button(f"✏️ Edit Strategy", key=f"edit_btn_{name}"):
                st.session_state.editing_client = name

            if c3.button(f"🗑️ Delete", key=f"del_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()
            
            auto_val = c4.toggle("Automation", value=data.get('auto_on', False), key=f"tog_{name}")
            if auto_val != data.get('auto_on'):
                data['auto_on'] = auto_val; save_data()

            # Inline Editor Form
            if st.session_state.editing_client == name:
                st.divider()
                with st.form(f"edit_form_{name}"):
                    st.subheader(f"Edit {name} Settings")
                    e_desc = st.text_area("Context", value=data['desc'])
                    col_e1, col_e2 = st.columns(2)
                    e_link = col_e1.text_input("CTA Link", value=data['cta_link'])
                    e_purp = col_e2.text_input("CTA Purpose", value=data['cta_purpose'])
                    e_tone = col_e1.selectbox("Tone", ["Professional", "Friendly", "Direct", "Urgent"], index=0)
                    e_int = col_e2.number_input("Interval", value=data.get('interval', 1))
                    
                    if st.form_submit_button("Update Client"):
                        data.update({"desc": e_desc, "cta_link": e_link, "cta_purpose": e_purp, "cta_tone": e_tone, "interval": e_int})
                        save_data()
                        st.session_state.editing_client = None
                        st.success("Updated!")
                        st.rerun()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel]["send_log"]))

# --- 4. SIDEBAR & AUTOMATION ---
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
                    res = send_personalized_email(data, name, row.get('FINAL_NAME', 'Target'), row.get('FINAL_EMAIL'), row.get('FINAL_INFO', 'your business'), st.session_state.g_key)
                    data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('FINAL_EMAIL'), "Status": "Auto-Sent ✅" if res == True else f"Error: {res}"})
                data['last_run_time'] = datetime.now().isoformat(); save_data()
    time.sleep(60); st.rerun()
