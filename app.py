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

# --- 2. MAILING ENGINE (STRICT ADHERENCE) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a cold email from {client_name} to {lead_name}.
        
        INPUT DATA:
        - Recipient Name: {lead_name}
        - Recipient Role/Info: {lead_role}
        - Their Specific Painpoint: {lead_pain}
        - My Company Context: {client_info['desc']}
        - Call to Action: {client_info['cta_purpose']}
        - Link: {client_info['cta_link']}
        - Tone: {client_info['cta_tone']}

        STRICT RULES:
        1. ONLY use the 'Painpoint' and 'Role' provided above.
        2. FORBIDDEN: Do not invent statistics, studies, or percentages (e.g., no "75%" claims).
        3. FORBIDDEN: Do not assume info not in the Input Data.
        4. Start with 'Hi {lead_name},'.
        5. SIGN OFF ONLY AS: 'Best regards, {client_name}'. No brackets [] or [Your Name].
        6. Length: Under 85 words.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Question regarding {lead_pain[:30]}..."
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

if 'editing_client' not in st.session_state:
    st.session_state.editing_client = None

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: ADD CLIENT
with t1:
    with st.form("new_client_form", clear_on_submit=True):
        colA, colB = st.columns(2)
        with colA:
            name = st.text_input("Company Name")
            desc = st.text_area("Company Context (What do you provide?)")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        with colB:
            cta_link = st.text_input("CTA Link")
            cta_purpose = st.text_input("CTA Purpose")
            cta_tone = st.selectbox("CTA Tone", ["Professional", "Friendly", "Direct", "Urgent"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Leads (Columns: NAME, EMAIL, INFORMATION, PAINPOINT)", type=["csv", "xlsx"])
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = pd.DataFrame()
                if leads_file:
                    try:
                        df = pd.read_excel(leads_file) if leads_file.name.endswith('.xlsx') else pd.read_csv(leads_file, encoding='latin1')
                        df.columns = [str(c).strip().upper() for c in df.columns]
                        # Mapping Core Columns
                        mappings = [('NAME', 'F_NAME'), ('EMAIL', 'F_EMAIL'), ('INFORMATION', 'F_INFO'), ('PAINPOINT', 'F_PAIN')]
                        for search, target in mappings:
                            for col in df.columns:
                                if search in col: df = df.rename(columns={col: target}); break
                    except: st.error("Lead file error.")
                st.session_state.clients[name] = {
                    "desc": desc, "cta_link": cta_link, "cta_purpose": cta_purpose, "cta_tone": cta_tone,
                    "interval": interval, "leads": df, "email": {"user": email, "pass": pw},
                    "send_log": [], "last_run_time": None, "auto_on": False
                }
                save_data(); st.rerun()

# TAB 2: VAULT (ENHANCED EDIT)
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 Client: {name}"):
            st.write(f"**Goal:** {data.get('cta_purpose', 'N/A')} | **Tone:** {data.get('cta_tone', 'Professional')}")
            
            c1, c2, c3, c4 = st.columns(4)
            if c1.button(f"🚀 Manual Batch", key=f"man_{name}"):
                if 'g_key' in st.session_state and st.session_state.g_key:
                    df = data.get('leads', pd.DataFrame())
                    with st.spinner("Sending..."):
                        for _, row in df.iterrows():
                            res = send_personalized_email(data, name, row.get('F_NAME', 'Target'), row.get('F_EMAIL'), row.get('F_INFO', 'Owner'), row.get('F_PAIN', 'growth'), st.session_state.g_key)
                            data["send_log"].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": row.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
                else: st.warning("Enter Groq Key.")

            if c2.button(f"✏️ Edit Client", key=f"edit_btn_{name}"):
                st.session_state.editing_client = name

            if c3.button(f"🗑️ Delete", key=f"del_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()
            
            data['auto_on'] = c4.toggle("Automation", value=data.get('auto_on', False), key=f"tog_{name}")

            if st.session_state.editing_client == name:
                st.markdown("---")
                with st.form(f"f_edit_{name}"):
                    tones = ["Professional", "Friendly", "Direct", "Urgent"]
                    t_idx = tones.index(data['cta_tone']) if data.get('cta_tone') in tones else 0
                    
                    e_name = st.text_input("Client Name", value=name)
                    ed1, ed2 = st.columns(2)
                    e_desc = ed1.text_area("Context", value=data['desc'])
                    e_link = ed2.text_input("CTA Link", value=data['cta_link'])
                    e_purp = ed1.text_input("CTA Purpose", value=data['cta_purpose'])
                    e_tone = ed2.selectbox("Tone", tones, index=t_idx)
                    e_user = ed1.text_input("Email", value=data['email']['user'])
                    e_pass = ed2.text_input("App Password", value=data['email']['pass'], type="password")
                    
                    if st.form_submit_button("Update All Info"):
                        upd = data.copy()
                        upd.update({"desc": e_desc, "cta_link": e_link, "cta_purpose": e_purp, "cta_tone": e_tone, "email": {"user": e_user, "pass": e_pass}})
                        if e_name != name:
                            st.session_state.clients[e_name] = upd; del st.session_state.clients[name]
                        else: st.session_state.clients[name] = upd
                        save_data(); st.session_state.editing_client = None; st.rerun()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Select Client", list(st.session_state.clients.keys()))
        logs = st.session_state.clients[sel].get("send_log", [])
        if logs: st.table(pd.DataFrame(logs))
        else: st.info("No logs found.")

# --- 4. SIDEBAR & DASHBOARD ---
with st.sidebar:
    st.header("⚙️ Control Panel")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    master_switch = st.toggle("🚀 Start Engine")
    
    if st.session_state.clients:
        st.divider()
        st.subheader("📊 Live Status")
        total_sent = 0
        for c_name, c_data in st.session_state.clients.items():
            sent_count = len([l for l in c_data.get("send_log", []) if "Sent ✅" in str(l.get("Status"))])
            total_sent += sent_count
            st.write(f"**{c_name}:** {sent_count} sent")
        st.metric("Total Agency Volume", total_sent)

# Automation Loop
if master_switch and st.session_state.g_key:
    for name, data in st.session_state.clients.items():
        if data.get('auto_on', False):
            last = data.get('last_run_time')
            if not last or (datetime.now() > datetime.fromisoformat(last) + timedelta(days=data.get('interval', 1))):
                leads = data.get('leads', pd.DataFrame())
                for _, row in leads.iterrows():
                    send_personalized_email(data, name, row.get('F_NAME','Target'), row.get('F_EMAIL'), row.get('F_INFO','Owner'), row.get('F_PAIN','growth'), st.session_state.g_key)
                data['last_run_time'] = datetime.now().isoformat(); save_data()
    time.sleep(60); st.rerun()
