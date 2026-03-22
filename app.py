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
        
        # FUZZY MAPPING: Fixes "NaN" errors by finding headers containing these keywords
        new_cols = {}
        for col in df.columns:
            if "NAM" in col: new_cols[col] = "F_NAME"
            elif "EMAIL" in col: new_cols[col] = "F_EMAIL"
            elif "INFO" in col: new_cols[col] = "F_INFO"
            elif "PAIN" in col or "STRUGGLE" in col: new_cols[col] = "F_PAIN"
        
        df = df.rename(columns=new_cols)
        # Drop rows where critical data is missing to avoid "Hi nan"
        df = df.dropna(subset=['F_NAME', 'F_EMAIL'], how='any')
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE (STRICT LIMITS) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        # Fallbacks to ensure professional text even if data is thin
        safe_name = "there" if pd.isna(lead_name) or str(lead_name).lower() == 'nan' else str(lead_name)
        safe_role = "Business Owner" if pd.isna(lead_role) or str(lead_role).lower() == 'nan' else str(lead_role)
        safe_pain = "scaling operations" if pd.isna(lead_pain) or str(lead_pain).lower() == 'nan' else str(lead_pain)

        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a professional cold email from {client_name} to {safe_name}.
        
        DATA: Name: {safe_name}, Role: {safe_role}, Struggle: {safe_pain}.
        CONTEXT: {client_info['desc']}. CTA: {client_info['cta_purpose']} ({client_info['cta_link']}).

        STRICT RULES:
        1. NO fake stats (e.g., NO "75% study" mentions).
        2. NO placeholders like [Your Name] or [Client Name].
        3. Sign off ONLY: 'Best regards, {client_name}'. 
        4. Tone: {client_info['cta_tone']}. Under 80 words.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question regarding {safe_pain[:20]}..."
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
            desc = st.text_area("Company Context (What do you do?)")
            email = st.text_input("Sender Email")
            pw = st.text_input("App Password", type="password")
        with c2:
            link = st.text_input("CTA Link")
            purp = st.text_input("CTA Purpose")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct"])
            interval = st.number_input("Days Between Sends", min_value=1, value=1)
            leads_file = st.file_uploader("Initial Leads (NAME, EMAIL, INFO, PAIN)", type=["csv", "xlsx"])
        
        if st.form_submit_button("📁 Save to Vault"):
            if name:
                df = process_leads(leads_file) if leads_file else pd.DataFrame()
                st.session_state.clients[name] = {
                    "desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone,
                    "interval": interval, "leads": df, "email": {"user": email, "pass": pw}, 
                    "send_log": [], "auto_on": False
                }
                save_data(); st.rerun()

# TAB 2: VAULT
with t2:
    if not st.session_state.clients:
        st.info("Vault is empty.")
    for name, data in list(st.session_state.clients.items()):
        l_count = len(data.get('leads', []))
        with st.expander(f"🏢 {name} | 📊 {l_count} Leads"):
            
            st.write(f"**Report:** {l_count} leads currently active in database.")
            
            col1, col2, col3, col4 = st.columns(4)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in data['leads'].iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()

            if col2.button("✏️ Edit Details", key=f"e_{name}"):
                st.session_state.editing_client = name
            
            if col3.button("🗑️ Delete", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            data['auto_on'] = col4.toggle("Auto-Mode", value=data.get('auto_on', False), key=f"t_{name}")

            # Safe Lead Update Form
            st.divider()
            st.subheader("🔄 Update Leads Only")
            with st.form(key=f"upd_form_{name}"):
                new_leads = st.file_uploader("Upload New Lead List", type=["csv", "xlsx"])
                if st.form_submit_button("Update Spreadsheet"):
                    if new_leads:
                        data['leads'] = process_leads(new_leads)
                        save_data(); st.success(f"Success! {len(data['leads'])} leads loaded."); st.rerun()

# --- 4. SIDEBAR DASHBOARD ---
with st.sidebar:
    st.header("⚙️ Agency Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    
    if st.session_state.clients:
        st.divider()
        st.subheader("📊 Global Performance Report")
        t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        
        st.metric("Total Managed Leads", t_leads)
        st.metric("Total Successful Sends", t_sent)
        
        st.divider()
        st.write("**Client Breakdown:**")
        for c_name, c_data in st.session_state.clients.items():
            st.write(f"- {c_name}: {len(c_data.get('leads', []))} leads")
