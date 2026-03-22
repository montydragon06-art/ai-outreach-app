import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
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
            # Unique column fix for JSON
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
        df = df.dropna(axis=1, how='all') # Drop empty A, B, C cols
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Literal Scanning for your 4 specific columns
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO", "PAINPOINT": "F_PAIN"}
        found_map = {target: col for col in df.columns for key, target in mapping.items() if col == key}
        
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns:
            df = df.dropna(subset=['F_NAME']) # Ensure Tim/Jim exist
        
        df.attrs['map_report'] = found_map
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}"); return pd.DataFrame()

# --- 2. MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        s_name = str(lead_name).strip() if not pd.isna(lead_name) else "there"
        client = Groq(api_key=groq_key)
        prompt = f"""Write a professional cold email from {client_name} to {s_name}.
        Lead: {s_name}, Info: {lead_role}, Pain: {lead_pain}.
        Context: {client_info['desc']}. CTA: {client_info['cta_purpose']} ({client_info['cta_link']}).
        STRICT RULES: 1. Address ONLY as {s_name}. 2. NO fake stats. 3. Sign off: Best regards, {client_name}."""
        
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart(); msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email; msg['Subject'] = f"Quick question for {s_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(client_info['email']['user'], client_info['email']['pass'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. UI ---
st.set_page_config(page_title="Agency Command Center", layout="wide")
if 'clients' not in st.session_state: st.session_state.clients = {}; load_data()
if 'edit_target' not in st.session_state: st.session_state.edit_target = None

st.title("📂 Agency Command Center")
t1, t2, t3 = st.tabs(["➕ Add Client", "🗄️ Client Vault", "📜 Master Logs"])

# TAB 1: ADD
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

# TAB 2: VAULT (WITH EDIT BUTTON)
with t2:
    # --- Edit Overlay ---
    if st.session_state.edit_target:
        target = st.session_state.edit_target
        with st.container(border=True):
            st.subheader(f"✏️ Editing: {target}")
            ed_data = st.session_state.clients[target]
            e1, e2 = st.columns(2)
            new_desc = e1.text_area("Update Context", value=ed_data['desc'])
            new_email = e1.text_input("Update Email", value=ed_data['email']['user'])
            new_pw = e1.text_input("Update Password", value=ed_data['email']['pass'], type="password")
            new_link = e2.text_input("Update CTA Link", value=ed_data['cta_link'])
            new_purp = e2.text_input("Update CTA Purpose", value=ed_data['cta_purpose'])
            new_tone = e2.selectbox("Update Tone", ["Professional", "Friendly", "Direct"], index=["Professional", "Friendly", "Direct"].index(ed_data.get('cta_tone', 'Professional')))
            
            ec1, ec2 = st.columns(2)
            if ec1.button("💾 Save Changes"):
                st.session_state.clients[target].update({"desc": new_desc, "cta_link": new_link, "cta_purpose": new_purp, "cta_tone": new_tone, "email": {"user": new_email, "pass": new_pw}})
                save_data(); st.session_state.edit_target = None; st.rerun()
            if ec2.button("❌ Cancel"):
                st.session_state.edit_target = None; st.rerun()
        st.divider()

    for name, data in list(st.session_state.clients.items()):
        df = data.get('leads', pd.DataFrame())
        with st.expander(f"🏢 {name} | 📊 {len(df)} Leads"):
            if len(df) > 0:
                report = df.attrs.get('map_report', {})
                st.info(f"🔍 **Column Mapping:** NAME found in '{report.get('F_NAME', 'MISSING')}' | EMAIL found in '{report.get('F_EMAIL', 'MISSING')}'")
            
            col1, col2, col3, col4 = st.columns(4)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in df.iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
            
            if col2.button("✏️ Edit Details", key=f"e_{name}"):
                st.session_state.edit_target = name; st.rerun()
                
            if col3.button("🗑️ Delete", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            with st.form(key=f"upd_{name}"): # Fixed update form
                new_f = st.file_uploader("Swap Lead List", type=["csv", "xlsx"])
                if st.form_submit_button("Sync New Leads"):
                    if new_f:
                        data['leads'] = process_leads(new_f)
                        save_data(); st.rerun()

# SIDEBAR
with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        st.divider(); st.subheader("📈 Performance")
        t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        st.metric("Total Leads", t_leads); st.metric("Total Sent", t_sent)
