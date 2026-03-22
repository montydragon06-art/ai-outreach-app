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

def process_leads(file):
    try:
        df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # FUZZY MAPPING: Stops "NaN" by looking for partial header matches
        new_cols = {}
        for col in df.columns:
            if "NAM" in col: new_cols[col] = "F_NAME"
            elif "EMAIL" in col: new_cols[col] = "F_EMAIL"
            elif "INFO" in col: new_cols[col] = "F_INFO"
            elif "PAIN" in col or "STRUGGLE" in col: new_cols[col] = "F_PAIN"
        
        df = df.rename(columns=new_cols)
        # Drop rows where the AI would have nothing to send to
        df = df.dropna(subset=['F_EMAIL'])
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE (ANTI-HALLUCINATION) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        # Fallbacks to prevent "nan" in email text
        safe_name = "there" if pd.isna(lead_name) or str(lead_name).lower() == 'nan' else str(lead_name)
        safe_role = "Business Owner" if pd.isna(lead_role) or str(lead_role).lower() == 'nan' else str(lead_role)
        safe_pain = "scaling your operations" if pd.isna(lead_pain) or str(lead_pain).lower() == 'nan' else str(lead_pain)

        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a professional cold email from {client_name} to {safe_name}.
        
        DATA SOURCE:
        - Lead Name: {safe_name}
        - Role: {safe_role}
        - Struggle: {safe_pain}
        - My Agency: {client_info['desc']}
        - CTA: {client_info['cta_purpose']} ({client_info['cta_link']})

        STRICT LIMITATIONS:
        1. DO NOT mention statistics, 75% figures, or fake studies.
        2. DO NOT use [Your Name] or any brackets.
        3. Sign off ONLY as: 'Best regards, {client_name}'.
        4. Start with 'Hi {safe_name},'. Tone: {client_info['cta_tone']}.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {safe_name}"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. APP UI ---
st.set_page_config(page_title="Agency Command Center", layout="wide")
if 'clients' not in st.session_state:
    st.session_state.clients = {}; load_data()

st.title("📂 Agency Command Center")
st.divider()

t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: ADD CLIENT
with t1:
    with st.form("new_client", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Company Name")
            desc = st.text_area("Context")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        with c2:
            link = st.text_input("CTA Link")
            purp = st.text_input("CTA Purpose")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Upload Leads", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads_file) if leads_file else pd.DataFrame()
            st.session_state.clients[name] = {
                "desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone,
                "interval": interval, "leads": df, "email": {"user": email, "pass": pw}, 
                "send_log": [], "auto_on": False
            }
            save_data(); st.rerun()

# TAB 2: VAULT (WITH LEAD REPORT & UPDATE)
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        lead_count = len(data.get('leads', []))
        with st.expander(f"🏢 {name} | 📊 {lead_count} Leads Loaded"):
            # Lead Report Metrics
            st.write(f"**Lead Database Report:** {lead_count} total targets found.")
            
            c1, c2, c3, c4 = st.columns(4)
            if c1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in data['leads'].iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()

            if c2.button("✏️ Edit Client", key=f"e_{name}"):
                st.session_state.editing_client = name
            
            if c3.button("🗑️ Delete", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            data['auto_on'] = c4.toggle("Auto", value=data.get('auto_on', False), key=f"t_{name}")

            # Swap Leads Section
            st.divider()
            new_leads = st.file_uploader("Upload New Spreadsheet", type=["csv", "xlsx"], key=f"fup_{name}")
            if st.button("🔄 Update Lead Database", key=f"upd_btn_{name}"):
                if new_leads:
                    data['leads'] = process_leads(new_leads)
                    save_data(); st.success(f"Updated! Now tracking {len(data['leads'])} leads."); st.rerun()

# TAB 3: LOGS
with t3:
    if st.session_state.clients:
        sel = st.selectbox("Logs for:", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel].get("send_log", [])))

# SIDEBAR DASHBOARD
with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        st.divider()
        st.subheader("📈 Performance Report")
        total_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        total_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        st.metric("Total Leads Managed", total_leads)
        st.metric("Total Successful Sends", total_sent)
