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

# --- 1. DATA PERSISTENCE (FIXED FOR DUPLICATE COLUMNS) ---
DATA_FILE = "agency_data.json"

def save_data():
    serializable_data = {}
    for name, info in st.session_state.clients.items():
        serializable_data[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            # FIX for image_054318.png: Force unique columns before JSON export
            temp_df = info['leads'].copy()
            temp_df.columns = [f"{col}_{i}" if duplicated else col 
                              for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable_data[name]['leads'] = temp_df.to_json()
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
        
        # FUZZY MAPPING: Prevents "NaN" by mapping once per category
        mapping = {"F_NAME": ["NAM"], "F_EMAIL": ["EMAIL"], "F_INFO": ["INFO", "ROLE"], "F_PAIN": ["PAIN", "STRUGGLE"]}
        new_cols = {}
        used_targets = set()
        
        for col in df.columns:
            for target, keywords in mapping.items():
                if any(k in col for k in keywords) and target not in used_targets:
                    new_cols[col] = target
                    used_targets.add(target)
                    break
        
        df = df.rename(columns=new_cols)
        # Drop rows missing critical info to prevent "Hi nan"
        if 'F_EMAIL' in df.columns:
            df = df.dropna(subset=['F_EMAIL'])
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE (ANTI-HALLUCINATION) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        # Fallbacks to prevent broken text
        s_name = "there" if pd.isna(lead_name) or str(lead_name).lower() == 'nan' else str(lead_name)
        s_role = "Business Owner" if pd.isna(lead_role) or str(lead_role).lower() == 'nan' else str(lead_role)
        s_pain = "scaling" if pd.isna(lead_pain) or str(lead_pain).lower() == 'nan' else str(lead_pain)

        client = Groq(api_key=groq_key)
        prompt = f"""
        Professional cold email from {client_name} to {s_name}.
        Lead: {s_name} ({s_role}), Struggle: {s_pain}.
        Context: {client_info['desc']}. CTA: {client_info['cta_purpose']} ({client_info['cta_link']}).
        
        STRICT RULES:
        1. NO fake stats/studies (e.g. NO "75% study").
        2. NO placeholders like [Your Name].
        3. Sign off ONLY: 'Best regards, {client_name}'.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Question for {s_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. UI ---
st.set_page_config(page_title="Agency Command Center", layout="wide")
if 'clients' not in st.session_state:
    st.session_state.clients = {}; load_data()

st.title("📂 Agency Command Center")
t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

with t1:
    with st.form("new_client", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Company Name")
        desc = c1.text_area("Context")
        email = c1.text_input("Sender Email")
        pw = c1.text_input("App Password", type="password")
        link = c2.text_input("CTA Link")
        purp = c2.text_input("CTA Purpose")
        tone = c2.selectbox("Tone", ["Professional", "Friendly", "Direct"])
        leads = c2.file_uploader("Leads", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads) if leads else pd.DataFrame()
            st.session_state.clients[name] = {"desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone, "leads": df, "email": {"user": email, "pass": pw}, "send_log": []}
            save_data(); st.rerun()

with t2:
    for name, data in list(st.session_state.clients.items()):
        l_count = len(data.get('leads', []))
        with st.expander(f"🏢 {name} | 📊 {l_count} Leads"):
            st.write(f"**Status Report:** {l_count} leads currently loaded.")
            col1, col2, col3 = st.columns(3)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in data['leads'].iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
                else: st.warning("Enter Groq Key in Sidebar.")
            if col2.button("🗑️ Delete Client", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()
            
            # FIXED LEAD UPDATE FORM
            with st.form(key=f"upd_{name}"):
                new_file = st.file_uploader("Swap Lead List", type=["csv", "xlsx"])
                if st.form_submit_button("Update Spreadsheet"):
                    if new_file:
                        data['leads'] = process_leads(new_file)
                        save_data(); st.success("Updated!"); st.rerun()

with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        st.divider()
        st.subheader("📈 Agency Report")
        t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        st.metric("Total Leads", t_leads)
        st.metric("Total Sent", t_sent)
        for c_name, c_data in st.session_state.clients.items():
            st.write(f"- {c_name}: {len(c_data.get('leads', []))} leads")
