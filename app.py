import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import time
import json
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. DATA PERSISTENCE ---
DATA_FILE = "agency_data.json"

def save_data():
    serializable_data = {}
    for name, info in st.session_state.clients.items():
        serializable_data[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            # Fix for unique columns
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
        # Standardize: Remove spaces and make uppercase
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # LITERAL MAPPING: No more "fuzzy" guessing that leads to "Terease"
        new_cols = {}
        cols = list(df.columns)
        
        # 1. FIND NAME (Priority: Exact match "NAME")
        if "NAME" in cols: new_cols["NAME"] = "F_NAME"
        elif "FIRST NAME" in cols: new_cols["FIRST NAME"] = "F_NAME"
        
        # 2. FIND EMAIL
        if "EMAIL" in cols: new_cols["EMAIL"] = "F_EMAIL"
        
        # 3. FIND INFO/ROLE
        if "INFORMATION" in cols: new_cols["INFORMATION"] = "F_INFO"
        elif "ROLE" in cols: new_cols["ROLE"] = "F_INFO"

        # 4. FIND PAINPOINT
        if "PAINPOINT" in cols: new_cols["PAINPOINT"] = "F_PAIN"
        elif "PAIN" in cols: new_cols["PAIN"] = "F_PAIN"

        df = df.rename(columns=new_cols)
        
        # Safety: Drop rows where the mapped Name or Email is empty
        if "F_NAME" in df.columns:
            df = df.dropna(subset=['F_NAME'])
        if "F_EMAIL" in df.columns:
            df = df.dropna(subset=['F_EMAIL'])
            
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        # Strict handling of the name to ensure "Tim/Jim" and not "nan"
        clean_name = str(lead_name).strip()
        if not clean_name or clean_name.lower() == "nan":
            clean_name = "there"

        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a professional cold email from {client_name} to {clean_name}.
        Using Lead Name: {clean_name}, Role: {lead_role}, Pain: {lead_pain}.
        Context: {client_info['desc']}. CTA: {client_info['cta_purpose']} ({client_info['cta_link']}).

        STRICT RULES:
        1. Address the lead ONLY as {clean_name}.
        2. NO invented statistics or studies (NO 75% study).
        3. NO [Your Name] placeholders.
        4. Sign off: 'Best regards, {client_name}'.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {clean_name}"
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
        leads = c2.file_uploader("Leads (Columns must be: NAME, EMAIL, INFORMATION, PAINPOINT)", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads) if leads else pd.DataFrame()
            st.session_state.clients[name] = {"desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone, "leads": df, "email": {"user": email, "pass": pw}, "send_log": []}
            save_data(); st.rerun()

with t2:
    for name, data in list(st.session_state.clients.items()):
        l_count = len(data.get('leads', []))
        with st.expander(f"🏢 {name} | 📊 {l_count} Leads"):
            
            # Lead Debugger: Shows user exactly what names were found
            if l_count > 0:
                st.write("**Top 3 Names Found:**", ", ".join(data['leads']['F_NAME'].astype(str).head(3).tolist()))
            
            col1, col2, col3 = st.columns(3)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in data['leads'].iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
            
            if col2.button("🗑️ Delete Client", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            # The 2nd Button: Update Only Spreadsheet
            with st.form(key=f"upd_{name}"):
                new_f = st.file_uploader("Swap Lead List", type=["csv", "xlsx"])
                if st.form_submit_button("Update Spreadsheet"):
                    if new_f:
                        data['leads'] = process_leads(new_f)
                        save_data(); st.success("Leads Refreshed!"); st.rerun()

# SIDEBAR REPORTING
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
            st.write(f"**{c_name}:** {len(c_data.get('leads', []))} leads")
