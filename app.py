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
            # Ensure columns are unique for JSON export
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
        
        # FIX: Remove entirely empty columns (A, B, C)
        df = df.dropna(axis=1, how='all')
        
        # Standardize headers for mapping
        original_headers = {str(c).strip().upper(): str(c) for c in df.columns}
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # LITERAL MAPPING: Connects F_NAME specifically to your "NAME" column
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO", "PAINPOINT": "F_PAIN"}
        
        # Track what was actually mapped for the UI report
        applied_mapping = {}
        for key, target in mapping.items():
            if key in df.columns:
                applied_mapping[target] = original_headers[key]
        
        df = df.rename(columns=mapping)
        
        # Drop rows where Tim or Jim's name is missing
        if "F_NAME" in df.columns:
            df = df.dropna(subset=['F_NAME'])
            
        # Store the mapping info in the dataframe attributes for the UI to read
        df.attrs['mapping_report'] = applied_mapping
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}")
        return pd.DataFrame()

# --- 2. MAILING ENGINE ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key):
    try:
        # Address Tim/Jim correctly, no "nan"
        s_name = str(lead_name).strip() if not pd.isna(lead_name) else "there"
        
        client = Groq(api_key=groq_key)
        prompt = f"""
        Professional cold email from {client_name} to {s_name}.
        Lead: {s_name}, Info: {lead_role}, Pain: {lead_pain}.
        CTA: {client_info['cta_purpose']} at {client_info['cta_link']}.

        STRICT RULES:
        1. Start with 'Hi {s_name},'. 
        2. NO fake statistics or studies (image_045da0.png).
        3. Sign off ONLY as 'Best regards, {client_name}'.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        
        msg = MIMEMultipart()
        msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email
        msg['Subject'] = f"Quick question for {s_name}"
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
        leads = c2.file_uploader("Leads (Columns: NAME, EMAIL, INFORMATION, PAINPOINT)", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads) if leads else pd.DataFrame()
            st.session_state.clients[name] = {"desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone, "leads": df, "email": {"user": email, "pass": pw}, "send_log": []}
            save_data(); st.rerun()

with t2:
    for name, data in list(st.session_state.clients.items()):
        df = data.get('leads', pd.DataFrame())
        l_count = len(df)
        with st.expander(f"🏢 {name} | 📊 {l_count} Leads"):
            
            # --- NEW: COLUMN MAP CHECKER ---
            if l_count > 0:
                st.subheader("🔍 Data Map Verification")
                mapping_info = df.attrs.get('mapping_report', {})
                mc1, mc2, mc3 = st.columns(3)
                mc1.info(f"👤 Name Column: **{mapping_info.get('F_NAME', 'NOT FOUND')}**")
                mc2.info(f"📧 Email Column: **{mapping_info.get('F_EMAIL', 'NOT FOUND')}**")
                mc3.success(f"✅ Preview: {', '.join(df['F_NAME'].astype(str).head(3).tolist())}")
                st.divider()

            col1, col2, col3 = st.columns(3)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in df.iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key)
                        data["send_log"].append({"Time": datetime.now().strftime("%H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Error: {res}"})
                    save_data(); st.rerun()
            
            if col2.button("🗑️ Delete Client", key=f"d_{name}"):
                del st.session_state.clients[name]; save_data(); st.rerun()

            # Swap Leads Form
            with st.form(key=f"upd_form_{name}"):
                new_f = st.file_uploader("Replace Lead List", type=["csv", "xlsx"])
                if st.form_submit_button("Update Leads"):
                    if new_f:
                        data['leads'] = process_leads(new_f)
                        save_data(); st.success("Updated!"); st.rerun()

with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        st.divider()
        st.subheader("📈 Performance Report")
        t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        st.metric("Total Leads", t_leads)
        st.metric("Total Sent", t_sent)
        for c_name, c_data in st.session_state.clients.items():
            st.write(f"- **{c_name}:** {len(c_data.get('leads', []))} leads")
