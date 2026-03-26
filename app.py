import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. CONFIGURATION ---
DATA_FILE = "agency_database.json"
# Your Google Apps Script Web App URL
TRACKER_URL = "https://script.google.com/macros/s/AKfycbwopXSz26Lv56blNumGcjFV-M9yCvFCzh6r5SK0dF7rBWDrA2R_3mow0B18JzDtQcfc/exec"

def save_data():
    serializable = {}
    for name, info in st.session_state.clients.items():
        serializable[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            temp_df = info['leads'].copy()
            # Handle duplicate columns for JSON safety
            temp_df.columns = [f"{col}_{i}" if duplicated else col 
                              for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable[name]['leads'] = temp_df.to_json()
    
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f)

# --- 2. DATA INITIALIZATION ---
if 'clients' not in st.session_state:
    st.session_state.clients = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                raw = json.load(f)
                for name, info in raw.items():
                    if isinstance(info['leads'], str):
                        info['leads'] = pd.read_json(info['leads'])
                    st.session_state.clients[name] = info
        except:
            st.session_state.clients = {}

# --- 3. CORE FUNCTIONS ---
def process_spreadsheet(file):
    try:
        df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
        df = df.dropna(axis=1, how='all')
        df.columns = [str(c).strip().upper() for c in df.columns]
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO"}
        df = df.rename(columns=mapping)
        return df.dropna(subset=['F_NAME']) if "F_NAME" in df.columns else df
    except Exception as e:
        st.error(f"File Error: {e}"); return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, cta_details):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        client = Groq(api_key=groq_key)
        
        # This creates the link that Google Sheets tracks
        tracking_url = f"{TRACKER_URL}?client={client_info['name'].replace(' ', '%20')}"
        
        prompt = f"""
        From: {client_info['name']} to {s_name}.
        Lead Info: {lead.get('F_INFO', 'Business owner/Lead')}.
        Client Biz: {client_info['desc']}.
        Goal: {cta_details['aim']}. 
        STRICT RULE: You MUST use this EXACT link for the Call to Action: {tracking_url}
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

# --- 4. UI NAVIGATION ---
st.set_page_config(page_title="Agency Pro: Google Edition", layout="wide")

with st.sidebar:
    st.title("⚙️ Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs"])
    
    st.divider()
    st.info("Note: Click counts are now updated automatically in your Google Sheet!")

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("➕ Create New Client")
    with st.form("create_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name (Must match Google Sheet exactly)")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Sender Email")
            app_pw = st.text_input("App Password", type="password")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"])
        with c2:
            st.write("### 🔗 Tracking Settings")
            cta_aim = st.text_input("CTA Goal (e.g., Book a meeting)")
            cta_link = st.text_input("Final Destination URL (The real website)")
            file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])

        if st.form_submit_button("Submit"):
            if name and file and cta_link:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "cta_aim": cta_aim, "cta_link": cta_link,
                    "tone": tone, "leads": df, "send_log": []
                }
                save_data(); st.success(f"Client {name} Saved locally!")
                st.warning("Don't forget to add this client name to your Google Sheet Row!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    if not st.session_state.clients:
        st.info("No clients found. Go to 'Create Client' first.")
    
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f"🏢 {c_name}"):
            t1, t2 = st.tabs(["✏️ Edit Profile", "🚀 Manual Send"])
            
            with t1:
                c_data['name'] = st.text_input("Biz Name", c_data['name'], key=f"n_{c_name}")
                c_data['desc'] = st.text_area("Description", c_data['desc'], key=f"d_{c_name}")
                c_data['email'] = st.text_input("Sender Email", c_data['email'], key=f"e_{c_name}")
                c_data['cta_link'] = st.text_input("Destination URL", c_data['cta_link'], key=f"l_{c_name}")
                if st.button("Save Changes", key=f"save_{c_name}"):
                    save_data(); st.success("Updated!"); st.rerun()

            with t2:
                m_aim = st.text_input("Campaign Goal", c_data.get('cta_aim', ''), key=f"ma_{c_name}")
                if st.button("🔥 Start Batch", key=f"sb_{c_name}"):
                    if not st.session_state.g_key:
                        st.error("Please enter GROQ Key in sidebar")
                    else:
                        progress = st.progress(0)
                        leads_list = c_data['leads']
                        for i, (_, lead) in enumerate(leads_list.iterrows()):
                            res = send_email_logic(c_data, lead, st.session_state.g_key, {"aim": m_aim})
                            c_data['send_log'].append({
                                "Client": c_name, 
                                "Time": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                                "Lead": lead.get('F_EMAIL', 'N/A'), 
                                "Status": "Success" if res==True else res
                            })
                            progress.progress((i + 1) / len(leads_list))
                        save_data(); st.success("Batch Complete!"); st.rerun()

# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("📜 Email History")
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data.get('send_log', []):
            all_logs.append(entry)
            
    if all_logs:
        st.dataframe(pd.DataFrame(all_logs), use_container_width=True)
    else:
        st.info("No emails sent yet.")
