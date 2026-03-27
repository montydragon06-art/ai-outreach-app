import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
import os
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. CONFIGURATION ---
DATA_FILE = "agency_database.json"
# Your Google Apps Script Web App URL
TRACKER_URL = "https://script.google.com/macros/s/AKfycbw0mdkl4yfLLHQcDh4B6nDqi39N8ZyetIdcSMrt5lrTKwuLWtV4CfIKRdR5tGxUXlTz/exec"
# Your Google Sheet ID (The long string in the browser URL of your sheet)
SHEET_ID = "1fqMwLHV51IgbcjHM0y6rLIG1zciLPL7m_Z2gJ4ZA-tk"

def save_data():
    serializable = {}
    for name, info in st.session_state.clients.items():
        serializable[name] = info.copy()
        if isinstance(info['leads'], pd.DataFrame):
            temp_df = info['leads'].copy()
            temp_df.columns = [f"{col}_{i}" if duplicated else col 
                              for i, (col, duplicated) in enumerate(zip(temp_df.columns, temp_df.columns.duplicated()))]
            serializable[name]['leads'] = temp_df.to_json()
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f)

def sync_clicks_from_google():
    try:
        # Construct the export URL to get the sheet as a CSV
        csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
        df = pd.read_csv(csv_url)
        # Update local session state with click counts from the sheet
        for index, row in df.iterrows():
            c_name = str(row['ClientName']).strip()
            if c_name in st.session_state.clients:
                st.session_state.clients[c_name]['clicks'] = int(row['Clicks'])
        save_data()
        return True
    except Exception as e:
        return f"Sync Error: {str(e)}"

# --- 2. DATA INITIALIZATION ---
if 'clients' not in st.session_state:
    st.session_state.clients = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                raw = json.load(f)
                for name, info in raw.items():
                    # This part handles converting the stored JSON back into a table
                    if isinstance(info['leads'], str):
                        info['leads'] = pd.read_json(info['leads'])
                    st.session_state.clients[name] = info
        except Exception as e:
            # If the file is corrupted or empty, start with a blank dictionary
            st.session_state.clients = {}
    else:
        # If the file doesn't exist yet, start with a blank dictionary
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
        
        # This creates the link for your tracker
        tracking_url = f"{TRACKER_URL}?client={client_info['name'].replace(' ', '%20')}"
        
        # --- 1. THE UPDATED PROMPT ---
        # Tells the AI to write plain text and NOT include its own HTML
        prompt = f"""
        Write a professional, friendly plain-text email from {client_info['name']} to {s_name}.
        Lead Context: {lead.get('F_INFO', 'Business owner')}.
        Business Description: {client_info['desc']}.
        Goal: {cta_details['aim']}.
        Tone: {client_info.get('tone', 'Professional')}.

        STRICT RULES:
        1. Do NOT use any HTML tags like <div>, <button>, or <html>.
        2. Write only the message body as natural text.
        3. Do not include the link yourself; I will add it.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}]
        )
        
        email_body = completion.choices[0].message.content
        
        # --- 2. THE CLEAN HYPERLINK ---
        # Converts AI newlines to HTML breaks and adds a simple blue link
        formatted_body = email_body.replace('\n', '<br>')
        hyperlink_html = f'<br><br><a href="{tracking_url}" style="color: #007bff; text-decoration: underline;">Visit Our Store</a>'
        
        full_content = f"""
        <html>
          <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #333;">
            {formatted_body}
            {hyperlink_html}
          </body>
        </html>
        """

        # --- 3. SENDING ---
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Quick question for {s_name}"
        
        # We send as 'html' so the link is clickable, but the style is simple text
        msg.attach(MIMEText(full_content, 'html'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: 
        return str(e)

# --- 4. UI NAVIGATION ---
st.set_page_config(page_title="Agency Pro", layout="wide")

with st.sidebar:
    st.title("⚙️ Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    st.divider()
    if st.button("🔄 Sync Clicks from Google"):
        res = sync_clicks_from_google()
        if res == True: st.success("Clicks Updated!"); st.rerun()
        else: st.error("Make sure your Google Sheet is 'Shared with link'")

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("Create New Client")
    with st.form("create_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Business Name")
            desc = st.text_area("Business Description")
            b_email = st.text_input("Sender Email")
            app_pw = st.text_input("App Password", type="password")
            tone = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"])
            file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        with c2:
            st.write("### Automation Settings")
            auto_on = st.checkbox("Enable Automation")
            days = st.number_input("Days Between", min_value=1, value=7)
            cta_aim = st.text_input("Default CTA Goal")
            cta_link = st.text_input("Default CTA Link (Destination)")
        if st.form_submit_button("Submit"):
            if name and file:
                df = process_spreadsheet(file)
                st.session_state.clients[name] = {
                    "name": name, "desc": desc, "email": b_email, "app_pw": app_pw,
                    "auto_on": auto_on, "auto_days": days, "cta_aim": cta_aim, "cta_link": cta_link,
                    "tone": tone, "leads": df, "send_log": [], "clicks": 0 
                }
                save_data(); st.success("Client Saved!")

# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    for c_name, c_data in list(st.session_state.clients.items()):
        with st.expander(f" {c_name}"):
            t1, t2, t3 = st.tabs(["Edit Full Profile", "Automation", "Manual Send"])
            with t1:
                c1, c2 = st.columns(2)
                with c1:
                    c_data['name'] = st.text_input("Biz Name", c_data['name'], key=f"n_{c_name}")
                    c_data['desc'] = st.text_area("Description", c_data['desc'], key=f"d_{c_name}")
                    c_data['email'] = st.text_input("Sender Email", c_data['email'], key=f"e_{c_name}")
                    c_data['app_pw'] = st.text_input("App PW", c_data['app_pw'], type="password", key=f"p_{c_name}")
                with c2:
                    c_data['tone'] = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"], key=f"t_{c_name}")
                    st.write("---")
                    st.write("**Update Leads Spreadsheet**")
                    new_file = st.file_uploader("Upload New Leads (Replaces Current)", type=["csv", "xlsx"], key=f"f_{c_name}")
                if st.button("Save Profile Changes", key=f"save_{c_name}"):
                    if new_file: c_data['leads'] = process_spreadsheet(new_file)
                    save_data(); st.success("Profile Updated!"); st.rerun()

            with t2:
                c_data['auto_on'] = st.toggle("Automation Active", c_data['auto_on'], key=f"at_{c_name}")
                c_data['auto_days'] = st.number_input("Interval (Days)", 1, 30, int(c_data['auto_days']), key=f"ad_{c_name}")
                c_data['cta_aim'] = st.text_input("Auto CTA Goal", c_data['cta_aim'], key=f"aa_{c_name}")
                c_data['cta_link'] = st.text_input("Auto CTA Link", c_data['cta_link'], key=f"al_{c_name}")
                if st.button("Update Automation", key=f"ua_{c_name}"): save_data(); st.success("Updated")

            with t3:
                m_aim = st.text_input("Manual Goal", c_data.get('cta_aim', ''), key=f"ma_{c_name}")
                m_link = st.text_input("Manual Link", c_data.get('cta_link', ''), key=f"ml_{c_name}")
                if st.button("Start Batch", key=f"sb_{c_name}"):
                    for _, lead in c_data['leads'].iterrows():
                        res = send_email_logic(c_data, lead, st.session_state.g_key, {"aim": m_aim, "link": m_link})
                        c_data['send_log'].append({"Client": c_name, "Time": datetime.now().strftime("%Y-%m-%d"), "Lead": lead.get('F_EMAIL', 'N/A'), "Status": "Success" if res==True else res})
                    save_data(); st.rerun()

# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("Email History")
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data.get('send_log', []):
            log_item = entry.copy()
            if 'Client' not in log_item: log_item['Client'] = c_name
            all_logs.append(log_item)
    if all_logs: st.dataframe(pd.DataFrame(all_logs), use_container_width=True)
    else: st.info("No emails sent yet.")

# --- PAGE 4: STATISTICS ---
elif page == "Statistics":
    st.header("Click Performance")
    for c_name, c_data in st.session_state.clients.items():
        sent = len(c_data.get('send_log', []))
        clicks = c_data.get('clicks', 0)
        rate = (clicks / sent * 100) if sent > 0 else 0
        st.subheader(f" {c_name}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Sent", sent)
        c2.metric("Total Clicks", clicks)
        c3.metric("CTR %", f"{rate:.1f}%")
        st.divider()
