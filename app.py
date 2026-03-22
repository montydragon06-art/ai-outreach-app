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
        # Mapping Core Columns: NAME, EMAIL, INFORMATION, PAINPOINT
        mappings = [('NAME', 'F_NAME'), ('EMAIL', 'F_EMAIL'), ('INFORMATION', 'F_INFO'), ('PAINPOINT', 'F_PAIN')]
        for search, target in mappings:
            for col in df.columns:
                if search in col: 
                    df = df.rename(columns={col: target})
                    break
        return df
    except Exception as e:
        st.error(f"File Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        client = Groq(api_key=groq_key)
        prompt = f"""
        Write a cold email from {client_name} to {lead_name}.
        INPUT DATA:
        - Name: {lead_name}, Role: {lead_role}, Pain: {lead_pain}
        - Context: {client_info['desc']}, Goal: {client_info['cta_purpose']}
        - Link: {client_info['cta_link']}, Tone: {client_info['cta_tone']}
        RULES:
        1. ONLY use the 'Pain' and 'Role' provided. No fake stats/75% figures.
        2. Sign off ONLY as: 'Best regards, {client_name}'. No [Your Name].
        3. Under 80 words.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {lead_name}"
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

with t1:
    with st.form("new_client", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Company Name")
        desc = c1.text_area("Context")
        email = c1.text_input("Email")
        pw = c1.text_input("App Password", type="password")
        link = c2.text_input("CTA Link")
        purp = c2.text_input("CTA Purpose")
        tone = c2.selectbox("Tone", ["Professional", "Friendly", "Direct", "Urgent"])
        leads_file = c2.file_uploader("Upload Leads", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads_file) if leads_file else pd.DataFrame()
            st.session_state.clients[name] = {
                "desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone,
                "leads": df, "email": {"user": email, "pass": pw}, "send_log": [], "auto_on": False
            }
            save_data(); st.rerun()

with t2:
    for name, data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {name}"):
            # Action Buttons
            col1, col2, col3, col4 = st.columns(4)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in data['leads'].iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME','Target'), r.get('F_EMAIL'), r.get('F_INFO','Owner'), r.get('F_PAIN','growth'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
                else: st.warning("Enter Groq Key.")

            if col2.button("✏️ Edit Details", key=f"e_{name}"):
                st.session_state.editing_client = name
            
            if col3.button("🗑️ Delete", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            data['auto_on'] = col4.toggle("Auto", value=data.get('auto_on', False), key=f"t_{name}")

            # --- THE NEW SPREADSHEET UPDATE SECTION ---
            st.markdown("---")
            st.subheader("📊 Update Leads Only")
            new_leads = st.file_uploader("Upload New Lead Sheet", type=["csv", "xlsx"], key=f"fup_{name}")
            if st.button("🔄 Update Lead Spreadsheet", key=f"upd_btn_{name}"):
                if new_leads:
                    data['leads'] = process_leads(new_leads)
                    save_data()
                    st.success("Lead list updated!")
                else: st.error("Please select a file first.")

            # Full Information Editor
            if st.session_state.get('editing_client') == name:
                with st.form(f"edit_all_{name}"):
                    e_name = st.text_input("Company Name", value=name)
                    ed1, ed2 = st.columns(2)
                    e_desc = ed1.text_area("Context", value=data['desc'])
                    e_link = ed2.text_input("CTA Link", value=data['cta_link'])
                    e_purp = ed1.text_input("CTA Purpose", value=data['cta_purpose'])
                    e_user = ed1.text_input("Email", value=data['email']['user'])
                    e_pass = ed2.text_input("Password", value=data['email']['pass'], type="password")
                    if st.form_submit_button("Update Everything"):
                        upd = data.copy()
                        upd.update({"desc": e_desc, "cta_link": e_link, "cta_purpose": e_purp, "email": {"user": e_user, "pass": e_pass}})
                        if e_name != name:
                            st.session_state.clients[e_name] = upd; del st.session_state.clients[name]
                        else: st.session_state.clients[name] = upd
                        save_data(); st.session_state.editing_client = None; st.rerun()

with t3:
    if st.session_state.clients:
        sel = st.selectbox("Logs for:", list(st.session_state.clients.keys()))
        st.table(pd.DataFrame(st.session_state.clients[sel].get("send_log", [])))

with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        total = sum(len([l for l in c.get("send_log", []) if "Sent" in str(l)]) for c in st.session_state.clients.values())
        st.metric("Total Agency Volume", total)
