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
TRACKER_URL = "https://script.google.com/macros/s/AKfycbw0mdkl4yfLLHQcDh4B6nDqi39N8ZyetIdcSMrt5lrTKwuLWtV4CfIKRdR5tGxUXlTz/exec"
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
        csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
        df = pd.read_csv(csv_url)
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
                    if isinstance(info['leads'], str):
                        info['leads'] = pd.read_json(info['leads'])
                    st.session_state.clients[name] = info
        except Exception as e:
            st.session_state.clients = {}
    else:
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
        
        # 1. IDENTIFY THE STRATEGY
        is_reply_campaign = cta_details.get('type') == "Direct Reply"
        
        # 2. CREATE THE STRICTOR INSTRUCTION BLOCK
        if is_reply_campaign:
            # The AI is told ONLY about the offer and the reply action
            strategy_instruction = f"""
            CAMPAIGN TYPE: Direct Reply / No-Link Advertising.
            GOAL: Get the recipient to reply to this email.
            OFFER TO MENTION: {cta_details['aim']}
            
            STRICT RULES FOR THIS EMAIL:
            - DO NOT mention any websites, URLs, links, or 'clicking' anything.
            - DO NOT use phrases like 'Check out our site' or 'Link in bio'.
            - END the email with a natural, open-ended question that encourages a direct reply.
            """
        else:
            # The AI is told about the value, knowing a link will be added by the system later
            strategy_instruction = f"""
            CAMPAIGN TYPE: Link Click / Traffic Generation.
            GOAL: Highlight the value of {cta_details['aim']}.
            
            STRICT RULES FOR THIS EMAIL:
            - Write the persuasive body text only. 
            - DO NOT write the link yourself.
            - DO NOT use placeholders like [Link Here]. 
            - I will append the technical link at the bottom; your job is just to build interest.
            """

        # 3. THE "UNBREAKABLE" PROMPT ASSEMBLY
        prompt = f"""
        You are an expert copywriter for {client_info['name']}. 
        Business Description: {client_info['desc']}
        Recipient: {s_name}

        {strategy_instruction}

        GENERAL CONSTRAINTS:
        - Write EXACTLY 2 paragraphs of body text.
        - NO greetings (No 'Hi', No 'Dear').
        - NO sign-offs (No 'Best regards', No 'Sincerely').
        - NO placeholders of any kind (No brackets [] or caps).
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}]
        )
        
        ai_meat = completion.choices[0].message.content.strip().replace('\n', '<br>')
        
        # 4. SYSTEM-SIDE LINK ASSEMBLY (The AI never sees this part)
        link_html = ""
        if not is_reply_campaign:
            tracking_url = f"{TRACKER_URL}?client={client_info['name'].replace(' ', '%20')}"
            link_html = f'<br><br><a href="{tracking_url}" style="color: #007bff; text-decoration: underline; font-weight: bold;">Visit Our Store</a>'

        # 5. FINAL EMAIL "SANDWICH"
        full_html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333;">
            Dear {s_name},<br><br>
            {ai_meat}
            {link_html}<br><br>
            Best regards,<br>
            The {client_info['name']} Team
          </body>
        </html>
        """

        # SMTP Sending Code...
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Question for {s_name}"
        msg.attach(MIMEText(full_html, 'html'))
        
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
                    new_file = st.file_uploader("Upload New Leads", type=["csv", "xlsx"], key=f"f_{c_name}")
                if st.button("Save Profile Changes", key=f"save_{c_name}"):
                    if new_file: c_data['leads'] = process_spreadsheet(new_file)
                    save_data(); st.success("Profile Updated!"); st.rerun()
                # --- DELETE CLIENT BUTTON ---
                st.divider()
                with st.expander("⚠️ Danger Zone"):
                    st.write("Deleting a client will permanently remove their leads, logs, and settings.")
                    if st.button(f"Delete {c_name} Permanently", key=f"del_{c_name}", type="primary"):
                        # Remove from session state
                        del st.session_state.clients[c_name]
                        # Update the JSON file
                        save_data()
                        st.success(f"Client '{c_name}' has been deleted.")
                        # Refresh the page to update the list
                        st.rerun()

            with t2:
                c_data['auto_on'] = st.toggle("Automation Active", c_data['auto_on'], key=f"at_{c_name}")
                c_data['auto_days'] = st.number_input("Interval (Days)", 1, 30, int(c_data['auto_days']), key=f"ad_{c_name}")
                
                c_data['auto_cta_type'] = st.selectbox("Campaign Strategy", ["Link Click", "Direct Reply"], key=f"acta_{c_name}")
                
                if c_data['auto_cta_type'] == "Link Click":
                    c_data['cta_aim'] = st.text_input("CTA Purpose", c_data.get('cta_aim', ''), key=f"aa_{c_name}")
                    c_data['cta_link'] = st.text_input("CTA Link", c_data.get('cta_link', ''), key=f"al_{c_name}")
                else:
                    # We store the "Offer" in the same 'cta_aim' slot so the AI still sees it
                    c_data['cta_aim'] = st.text_area("The Offer", c_data.get('cta_aim', ''), key=f"off_{c_name}")
                    # We store the "Action" (Reply 'YES') in a new slot
                    c_data['cta_action'] = st.text_input("Action Required", c_data.get('cta_action', "Reply to this email"), key=f"act_{c_name}")

                if st.button("Update Automation", key=f"ua_{c_name}"): 
                    save_data()
                    st.success("Settings Saved!")
            with t3:
                m_type = st.radio("Strategy", ["Link Click", "Direct Reply"], horizontal=True, key=f"mt_{c_name}")
                
                if m_type == "Link Click":
                    m_aim = st.text_input("CTA Purpose", key=f"ma_{c_name}")
                    m_link = st.text_input("Manual Link", c_data.get('cta_link', ''), key=f"ml_{c_name}")
                    m_action = "" # Not needed for link
                else:
                    m_aim = st.text_area("The Offer", key=f"moff_{c_name}")
                    m_action = st.text_input("Action Required", "Reply 'YES' to this email", key=f"mact_{c_name}")
                    m_link = "" # Not needed for reply

                if st.button("Start Batch", key=f"sb_{c_name}"):
                    # We pass 'm_action' into the aim so the AI knows exactly what the lead should do
                    final_aim = f"{m_aim} and get them to {m_action}" if m_type == "Direct Reply" else m_aim
                    
                    for _, lead in c_data['leads'].iterrows():
                        res = send_email_logic(c_data, lead, st.session_state.g_key, 
                                               {"aim": final_aim, "link": m_link, "type": m_type})
                        
                        c_data['send_log'].append({
                            "Client": c_name, 
                            "Time": datetime.now().strftime("%Y-%m-%d"), 
                            "Lead": lead.get('F_EMAIL', 'N/A'), 
                            "Status": "Success" if res==True else res
                        })
                    save_data()
                    st.success("Batch Complete!")
                    st.rerun()

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
