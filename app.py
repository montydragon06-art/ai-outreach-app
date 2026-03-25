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
            # Fix for duplicate columns causing ValueError
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
        # Drop entirely empty columns (A, B, C) to find real data
        df = df.dropna(axis=1, how='all')
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO"}
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns:
            # Drop rows where Name is missing to avoid "nan" displays
            df = df.dropna(subset=['F_NAME'])
        return df
    except Exception as e:
        st.error(f"File Error: {e}")
        return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, framework=None, cta_details=None):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        client = Groq(api_key=groq_key)
        mode_text = f"Use this framework: {framework}" if framework else "Write freehand."
        prompt = f"""
        {mode_text}
        From: {client_info['name']} to {s_name}.
        Lead Info: {lead.get('F_INFO', 'Business owner')}.
        Client Biz: {client_info['desc']}.
        Goal: {cta_details['aim']}. Link: {cta_details['link']}. Tone: {client_info.get('tone', 'Professional')}.
        RULES: 1. Address as 'Hi {s_name},'. 2. NO fake stats. 3. Sign off: Best regards, {client_info['name']}.
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
    st.title("Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    st.divider()
    t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
    t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
    st.metric("Total Leads", t_leads)
    st.metric("Total Sent", t_sent)

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("Create New Client")
    with st.form("create_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Business Email (Sender)")
            app_pw = st.text_input("App Password", type="password")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"])
            file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        with c2:
            st.write("### Automation Settings")
            auto_on = st.checkbox("Activate Automated Emails?")
            if auto_on:
                days = st.number_input("Days between emails", min_value=1, value=7)
                cta_aim = st.text_input("CTA: What should leads do?")
                cta_link = st.text_input("CTA: Link")
            else:
                days, cta_aim, cta_link = 0, "", ""

        if st.form_submit_button("Submit Client"):
            if name and file:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "auto_on": auto_on, "auto_days": days, 
                    "next_send": (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d") if auto_on else "N/A",
                    "cta_aim": cta_aim, "cta_link": cta_link, "tone": tone,
                    "leads": df, "send_log": []
                }
                save_data()
                st.success(f"Client {name} saved!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    st.header("Client Vault")
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f"{c_name} |  {len(c_data['leads'])} Leads"):
            tab_edit, tab_auto, tab_manual = st.tabs(["Edit Profile", "Automation", "Manual Send"])
            
            with tab_edit:
                edit_name = st.text_input("Name", value=c_data['name'], key=f"nm_{c_name}")
                edit_desc = st.text_area("Description", value=c_data['desc'], key=f"ed_{c_name}")
                edit_email = st.text_input("Email", value=c_data['email'], key=f"em_{c_name}")
                edit_pw = st.text_input("App Password", value=c_data['app_pw'], type="password", key=f"pw_{c_name}")
                if st.button("Update Profile", key=f"save_{c_name}"):
                    c_data.update({"name": edit_name, "desc": edit_desc, "email": edit_email, "app_pw": edit_pw})
                    if edit_name != c_name: st.session_state.clients[edit_name] = st.session_state.clients.pop(c_name)
                    save_data(); st.rerun()
                if st.button("Delete Client", key=f"del_{c_name}"):
                    del st.session_state.clients[c_name]; save_data(); st.rerun()

            with tab_auto:
                c_data['auto_on'] = st.toggle("Enable Automation", value=c_data['auto_on'], key=f"tog_{c_name}")
                # FIX: Added max(1, ...) to prevent ValueBelowMinError
                new_days = st.number_input("Days between emails", min_value=1, value=max(1, int(c_data.get('auto_days', 7))), key=f"day_{c_name}")
                if c_data['auto_on']:
                    st.write(f"Next Send: {c_data['next_send']}")
                if st.button("Update Automation Frequency", key=f"up_f_{c_name}"):
                    c_data['auto_days'] = new_days
                    save_data(); st.success("Frequency Updated!")

            with tab_manual:
                method = st.radio("Writing Method", ["Freehand", "Use Framework"], key=f"meth_{c_name}")
                framework = st.text_area("Paste Framework", key=f"fr_{c_name}") if method == "Use Framework" else None
                m_aim = st.text_input("Aim", value=c_data['cta_aim'], key=f"maim_{c_name}")
                m_link = st.text_input("Link", value=c_data['cta_link'], key=f"mlink_{c_name}")
                if st.button("Send Batch", key=f"send_{c_name}"):
                    if st.session_state.g_key:
                        for _, lead in c_data['leads'].iterrows():
                            res = send_email_logic(c_data, lead, st.session_state.g_key, framework, {"aim": m_aim, "link": m_link})
                            c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Lead": lead['F_EMAIL'], "Status": "Success" if res==True else f"Error: {res}"})
                        save_data(); st.rerun()

# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("Global History")
    if st.button("Clear All History"):
        for c in st.session_state.clients.values(): c['send_log'] = []
        save_data(); st.rerun()
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data['send_log']:
            log_entry = entry.copy(); log_entry['Client'] = c_name
            all_logs.append(log_entry)
    if all_logs: st.dataframe(pd.DataFrame(all_logs), use_container_width=True)

# --- PAGE 4: STATISTICS ---
elif page == "Statistics":
    st.header("Stats")
    col1, col2 = st.columns(2)
    col1.metric("Total Clients", len(st.session_state.clients))
    col2.metric("Total Sent", sum(len(c['send_log']) for c in st.session_state.clients.values()))
    for c_name, c_data in st.session_state.clients.items():
        st.subheader(f"{c_name}: {len(c_data['leads'])} Leads | {len(c_data['send_log'])} Sent")
