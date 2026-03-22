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
            temp_df = info['leads'].copy()
            # Unique column fix
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
        df = df.dropna(axis=1, how='all') # Clean A, B, C
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO", "PAINPOINT": "F_PAIN"}
        found_map = {target: col for col in df.columns for key, target in mapping.items() if col == key}
        df = df.rename(columns=mapping)
        if "F_NAME" in df.columns: df = df.dropna(subset=['F_NAME']) # Target Tim/Jim
        df.attrs['map_report'] = found_map
        return df
    except Exception as e:
        st.error(f"Spreadsheet Error: {e}"); return pd.DataFrame()

# --- 2. MAILING ENGINE (WITH FRAMEWORKS) ---
def send_personalized_email(client_info, client_name, lead_name, lead_email, lead_role, lead_pain, groq_key, framework):
    try:
        s_name = str(lead_name).strip() if not pd.isna(lead_name) else "there"
        client = Groq(api_key=groq_key)
        
        framework_prompts = {
            "AIDA (Attention, Interest, Desire, Action)": "Focus on creating a high-energy hook followed by desire for our solution.",
            "PAS (Problem, Agitation, Solution)": "Heavily agitate the lead's pain point before offering our service as the fix.",
            "Quick & Direct": "Keep it under 50 words. Be blunt and professional."
        }

        prompt = f"""Write an email using the {framework} framework. 
        From: {client_name} to {s_name}. Lead Info: {lead_role}, Pain: {lead_pain}.
        Context: {client_info['desc']}. CTA: {client_info['cta_purpose']} ({client_info['cta_link']}).
        STRICT: 1. Hi {s_name}. 2. NO fake stats. 3. Sign off: Best regards, {client_name}.
        Framework Instruction: {framework_prompts.get(framework)}"""
        
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        body = completion.choices[0].message.content
        msg = MIMEMultipart(); msg['From'] = f"{client_name} <{client_info['email']['user']}>"
        msg['To'] = lead_email; msg['Subject'] = f"Regarding {lead_pain[:20]}..."
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

with t1:
    with st.form("new_client", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Company Name")
        desc = c1.text_area("Context")
        email = c1.text_input("Sender Email")
        pw = c1.text_input("App Password", type="password")
        link = c2.text_input("CTA Link")
        purp = c2.text_input("CTA Purpose")
        tone = c2.selectbox("Default Tone", ["Professional", "Friendly", "Direct"])
        leads = c2.file_uploader("Leads", type=["csv", "xlsx"])
        if st.form_submit_button("📁 Save to Vault"):
            df = process_leads(leads) if leads else pd.DataFrame()
            st.session_state.clients[name] = {"desc": desc, "cta_link": link, "cta_purpose": purp, "cta_tone": tone, "leads": df, "email": {"user": email, "pass": pw}, "send_log": []}
            save_data(); st.rerun()

with t2:
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
            new_tone = e2.selectbox("Update Tone", ["Professional", "Friendly", "Direct"], index=0)
            if st.button("💾 Save"):
                st.session_state.clients[target].update({"desc": new_desc, "cta_link": new_link, "cta_purpose": new_purp, "cta_tone": new_tone, "email": {"user": new_email, "pass": new_pw}})
                save_data(); st.session_state.edit_target = None; st.rerun()
        st.divider()

    for name, data in list(st.session_state.clients.items()):
        df = data.get('leads', pd.DataFrame())
        with st.expander(f"🏢 {name} | 📊 {len(df)} Leads"):
            # FRAMEWORK SELECTOR
            frame = st.selectbox("Select Strategy Framework", ["AIDA (Attention, Interest, Desire, Action)", "PAS (Problem, Agitation, Solution)", "Quick & Direct"], key=f"f_{name}")
            
            col1, col2, col3, col4 = st.columns(4)
            if col1.button("🚀 Batch Send", key=f"s_{name}"):
                if st.session_state.get('g_key'):
                    for _, r in df.iterrows():
                        res = send_personalized_email(data, name, r.get('F_NAME'), r.get('F_EMAIL'), r.get('F_INFO'), r.get('F_PAIN'), st.session_state.g_key, frame)
                        data["send_log"].append({"Client": name, "Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Recipient": r.get('F_EMAIL'), "Status": "Sent ✅" if res==True else f"Err: {res}"})
                    save_data(); st.rerun()
            
            if col2.button("✏️ Edit", key=f"e_{name}"): st.session_state.edit_target = name; st.rerun()
            if col3.button("🗑️ Delete", key=f"d_{name}"): del st.session_state.clients[name]; save_data(); st.rerun()

            with st.form(key=f"upd_{name}"):
                new_f = st.file_uploader("Update Leads", type=["csv", "xlsx"])
                if st.form_submit_button("Sync"):
                    if new_f: data['leads'] = process_leads(new_f); save_data(); st.rerun()

with t3:
    st.header("📜 Master Logs")
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        all_logs.extend(c_data.get('send_log', []))
    if all_logs:
        st.table(pd.DataFrame(all_logs))
    else:
        st.info("No logs found.")

with st.sidebar:
    st.header("⚙️ Dashboard")
    st.session_state.g_key = st.text_input("Groq API Key", type="password")
    if st.session_state.clients:
        st.divider(); st.subheader("📈 Agency Report")
        t_leads = sum(len(c.get('leads', [])) for c in st.session_state.clients.values())
        t_sent = sum(len(c.get('send_log', [])) for c in st.session_state.clients.values())
        st.metric("Total Leads Managed", t_leads)
        st.metric("Total Emails Sent", t_sent)
