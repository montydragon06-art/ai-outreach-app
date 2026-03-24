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
            # Unique column fix to prevent JSON export errors
            temp_df = info['leads'].copy()
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
        df = df.dropna(axis=1, how='all') # Clean empty A, B, C cols
        df.columns = [str(c).strip().upper() for c in df.columns]
        # Literal Map: Strictly scanning for NAME, EMAIL, INFORMATION
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO"}
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns:
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
    st.title("⚙️ Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    
    st.divider()
    t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
    t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
    st.metric("Total Leads", t_leads)
    st.metric("Total Sent", t_sent)

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("➕ Create New Client")
    with st.form("create_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Business Email (Sender)")
            app_pw = st.text_input("App Password", type="password")
        with c2:
            auto_on = st.checkbox("Activate Automated Emails?")
            days = st.number_input("Send every X days", min_value=1, value=7) if auto_on else 0
            cta_aim = st.text_input("CTA: What should leads do?")
            cta_link = st.text_input("CTA: Link (if any)")
            tone = st.selectbox("Tone of Voice", ["Professional", "Friendly", "Direct", "Witty"])
            file = st.file_uploader("Upload Leads Spreadsheet (NAME, EMAIL, INFORMATION)", type=["csv", "xlsx"])

        if st.form_submit_button("Submit Client"):
            if name and file:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "auto_on": auto_on, "auto_days": days, "next_send": (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d") if auto_on else "N/A",
                    "cta_aim": cta_aim, "cta_link": cta_link, "tone": tone,
                    "leads": df, "send_log": []
                }
                save_data()
                st.success(f"Client {name} added to Vault!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    st.header("🗄️ Client Vault")
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {c_name} | 👥 {len(c_data['leads'])} Leads"):
            tab_edit, tab_auto, tab_manual = st.tabs(["✏️ Edit Info", "🤖 Automation", "🚀 Manual Send"])
            
            with tab_edit:
                new_desc = st.text_area("Description", value=c_data['desc'], key=f"ed_{c_name}")
                if st.button("Save Info", key=f"save_{c_name}"):
                    c_data['desc'] = new_desc
                    save_data(); st.rerun()
                if st.button("Delete Client", key=f"del_{c_name}"):
                    del st.session_state.clients[c_name]; save_data(); st.rerun()

            with tab_auto:
                c_data['auto_on'] = st.toggle("Enable Automation", value=c_data['auto_on'], key=f"tog_{c_name}")
                st.write(f"Next Send Scheduled: {c_data['next_send']}")
                if st.button("Update Automation", key=f"up_auto_{c_name}"):
                    save_data(); st.success("Automation settings saved.")

            with tab_manual:
                method = st.radio("Writing Method", ["Freehand", "Use Framework"], key=f"meth_{c_name}")
                framework = None
                if method == "Use Framework":
                    framework = st.text_area("Paste Framework here", key=f"frame_{c_name}")
                
                m_aim = st.text_input("Aim of this specific email", value=c_data['cta_aim'], key=f"maim_{c_name}")
                m_link = st.text_input("Link", value=c_data['cta_link'], key=f"mlink_{c_name}")
                
                if st.button("🔥 Send Batch Now", key=f"send_{c_name}"):
                    if st.session_state.g_key:
                        for _, lead in c_data['leads'].iterrows():
                            res = send_email_logic(c_data, lead, st.session_state.g_key, framework, {"aim": m_aim, "link": m_link})
                            c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Lead": lead['F_EMAIL'], "Status": "Success" if res==True else f"Error: {res}"})
                        save_data(); st.success("Batch Sent!"); st.rerun()
                    else: st.warning("Enter Groq Key in Sidebar")

# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("📜 Global Email History")
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data['send_log']:
            entry['Client'] = c_name
            all_logs.append(entry)
    if all_logs:
        st.dataframe(pd.DataFrame(all_logs), use_container_width=True)
    else:
        st.info("No emails have been sent yet.")

# --- PAGE 4: STATISTICS ---
elif page == "Statistics":
    st.header("📊 Agency Statistics")
    col1, col2 = st.columns(2)
    col1.metric("Total Clients", len(st.session_state.clients))
    col2.metric("Total Emails Sent", sum(len(c['send_log']) for c in st.session_state.clients.values()))
    
    st.divider()
    for c_name, c_data in st.session_state.clients.items():
        st.subheader(f"Client: {c_name}")
        sc1, sc2 = st.columns(2)
        sc1.metric("Leads in Database", len(c_data['leads']))
        sc2.metric("Emails Sent", len(c_data['send_log']))
