import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. DATA & SESSION INITIALIZATION ---
DATA_FILE = "agency_database.json"

def save_data():
    serializable = {}
    for name, info in st.session_state.clients.items():
        serializable[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            temp_df = info['leads'].copy()
            # Unique column fix for JSON safety
            temp_df.columns = [f"{col}_{i}" if duplicated else col 
                              for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable[name]['leads'] = temp_df.to_json()
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            raw = json.load(f)
            for name, info in raw.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info

if 'clients' not in st.session_state:
    st.session_state.clients = {}
    load_data()

# --- 2. CORE FUNCTIONS ---
def process_spreadsheet(file):
    try:
        df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
        df = df.dropna(axis=1, how='all') # Skips empty A, B, C cols
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO"}
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns:
            df = df.dropna(subset=['F_NAME'])
        return df
    except Exception as e:
        st.error(f"File Error: {e}"); return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, framework=None, cta_details=None):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        client = Groq(api_key=groq_key)
        
        # --- TRACKER LINK ---
        # REPLACE 'yourusername' with your actual PythonAnywhere username
        tracking_url = f"https://yourusername.pythonanywhere.com/click/{client_info['name']}"
        
        mode_text = f"Use this framework: {framework}" if framework else "Write freehand."
        prompt = f"""
        {mode_text}
        From: {client_info['name']} to {s_name}.
        Lead Info: {lead.get('F_INFO', 'Business owner')}.
        Client Biz: {client_info['desc']}.
        Goal: {cta_details['aim']}. 
        STRICT RULE: Use this EXACT link for the Call to Action: {tracking_url}
        Tone: {client_info.get('tone', 'Professional')}.
        """
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Quick question for {s_name}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP("smtp.gmail.com", 587); server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg); server.quit()
        return True
    except Exception as e: return str(e)

# --- 3. UI NAVIGATION ---
st.set_page_config(page_title="Agency Pro", layout="wide")

with st.sidebar:
    st.title("⚙️ Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    
    st.divider()
    # --- DATA SYNC TOOLS ---
    st.write("### 🔄 Sync Tracker Data")
    
    # 1. DOWNLOAD BUTTON
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            st.download_button(
                label="Step 1: Download to Computer",
                data=f,
                file_name="agency_database.json",
                mime="application/json",
                key="side_dl"
            )
    
    # 2. UPLOAD BUTTON (To bring data back FROM PythonAnywhere)
    st.write("---")
    uploaded_sync = st.file_uploader("Step 2: Upload updated file from PA", type="json")
    if uploaded_sync:
        if st.button("Confirm Sync Update"):
            new_data = json.load(uploaded_sync)
            # This updates the current session with the file from PythonAnywhere
            for name, info in new_data.items():
                if isinstance(info['leads'], str):
                    info['leads'] = pd.read_json(info['leads'])
                st.session_state.clients[name] = info
            save_data()
            st.success("Database Updated with Click Counts!")
            st.rerun()

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("➕ Create New Client")
    with st.form("create_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Business Email")
            app_pw = st.text_input("App Password", type="password")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"])
            file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        with c2:
            st.write("### 🤖 Automation Settings")
            auto_on = st.checkbox("Activate Automated Emails?")
            if auto_on:
                days = st.number_input("Days between emails", min_value=1, value=7)
                cta_aim = st.text_input("CTA Goal")
                cta_link = st.text_input("CTA Link (Destination)")
            else: days, cta_aim, cta_link = 0, "", ""

        if st.form_submit_button("Submit Client"):
            if name and file:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "auto_on": auto_on, "auto_days": days, 
                    "next_send": (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d") if auto_on else "N/A",
                    "cta_aim": cta_aim, "cta_link": cta_link, "tone": tone,
                    "leads": df, "send_log": [], "clicks": 0 
                }
                save_data(); st.success(f"Client {name} saved!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    st.header("🗄️ Client Vault")
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {c_name}"):
            tab_edit, tab_auto, tab_manual = st.tabs(["✏️ Edit", "🤖 Auto", "🚀 Manual"])
            with tab_edit:
                edit_name = st.text_input("Name", value=c_data['name'], key=f"nm_{c_name}")
                new_file = st.file_uploader("Update Leads", type=["csv", "xlsx"], key=f"f_{c_name}")
                if st.button("Save", key=f"s_{c_name}"):
                    c_data['name'] = edit_name
                    if new_file: c_data['leads'] = process_spreadsheet(new_file)
                    save_data(); st.rerun()

            with tab_auto:
                # Safety fix for 0 days crash
                new_days = st.number_input("Days", min_value=1, value=max(1, int(c_data.get('auto_days', 7))), key=f"d_{c_name}")
                if st.button("Update Frequency", key=f"uf_{c_name}"):
                    c_data['auto_days'] = new_days
                    save_data(); st.success("Updated")

            with tab_manual:
                if st.button("🔥 Send Batch", key=f"send_{c_name}"):
                    for _, lead in c_data['leads'].iterrows():
                        res = send_email_logic(c_data, lead, st.session_state.g_key, None, {"aim": c_data['cta_aim'], "link": c_data['cta_link']})
                        c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d"), "Lead": lead['F_EMAIL'], "Status": "Success" if res==True else res})
                    save_data(); st.rerun()

# --- PAGE 3: LOGS ---
elif page == "Email Logs":
    st.header("📜 Logs")
    if st.button("Clear Logs"):
        for c in st.session_state.clients.values(): c['send_log'] = []
        save_data(); st.rerun()
    # Simple display logic
    for c_name, c_data in st.session_state.clients.items():
        if c_data['send_log']: st.write(f"**{c_name}**"); st.table(c_data['send_log'])

# --- PAGE 4: STATISTICS ---
elif page == "Statistics":
    st.header("📊 Stats")
    for c_name, c_data in st.session_state.clients.items():
        sent = len(c_data['send_log'])
        clicks = c_data.get('clicks', 0)
        rate = (clicks / sent * 100) if sent > 0 else 0
        st.subheader(c_name)
        c1, c2, c3 = st.columns(3)
        c1.metric("Sent", sent)
        c2.metric("Clicks", clicks)
        c3.metric("CTR", f"{rate:.1f}%")
