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
from cryptography.fernet import Fernet
import base64
from cryptography.fernet import Fernet
import json
import os

# --- ENCRYPTION HELPERS ---
def get_cipher():
    """Retrieves the key from Streamlit Secrets and creates a Cipher object."""
    try:
        key = st.secrets["master_key"]
        return Fernet(key.encode())
    except Exception as e:
        st.error("Master Key missing or invalid in Streamlit Secrets!")
        return None

def save_data():
    """Encrypts and saves session state to disk."""
    cipher = get_cipher()
    if not cipher:
        return

    serializable = {}
    for name, info in st.session_state.clients.items():
        client_copy = info.copy()
        if isinstance(info.get('leads'), pd.DataFrame):
            client_copy['leads'] = info['leads'].to_json()
        serializable[name] = client_copy
    
    # 1. Convert to JSON string
    json_string = json.dumps(serializable)
    # 2. Encrypt
    encrypted_data = cipher.encrypt(json_string.encode())
    
    # 3. Write binary to file
    with open(DATA_FILE, "wb") as f:
        f.write(encrypted_data)

def load_data():
    """Loads and decrypts data from disk into session state."""
    # 1. YOU MUST DEFINE CIPHER FIRST
    cipher = get_cipher() 
    
    # 2. NOW YOU CAN CHECK IF IT EXISTS
    if os.path.exists(DATA_FILE) and cipher:
        try:
            with open(DATA_FILE, "rb") as f:
                encrypted_content = f.read()
            
            # Decrypt and parse
            decrypted_json = cipher.decrypt(encrypted_content).decode()
            raw = json.loads(decrypted_json)
            
            for name, info in raw.items():
                if isinstance(info.get('leads'), str):
                    try:
                        info['leads'] = pd.read_json(info['leads'])
                    except:
                        info['leads'] = pd.DataFrame()
                st.session_state.clients[name] = info
        except Exception as e:
            st.error(f"Security Load Error: {e}. Check your Master Key.")
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
        
        # Clean column names
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # MANDATORY CHECK: Look for SOURCE
        if "SOURCE" not in df.columns:
            st.error("❌ ERROR: Spreadsheet is missing the 'SOURCE' column. This is required for legal compliance.")
            return pd.DataFrame() # Returns empty so the form won't submit
            
        mapping = {"NAME": "F_NAME", "EMAIL": "F_EMAIL", "INFORMATION": "F_INFO", "SOURCE": "F_SOURCE"}
        df = df.rename(columns=mapping)
        
        return df.dropna(subset=['F_NAME']) if "F_NAME" in df.columns else df
    except Exception as e:
        st.error(f"File Error: {e}")
        return pd.DataFrame()

def send_email_logic(client_info, lead, groq_key, cta_details):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        # Retrieve the Source from the lead data
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        
        client = Groq(api_key=groq_key)
        
        # Determine the Strategy
        is_reply_campaign = cta_details.get('type') == "Direct Reply"
        
        if is_reply_campaign:
            strategy_instruction = f"""
            CAMPAIGN: Direct Reply (No Links).
            GOAL: Get a response about: {cta_details['aim']}.
            STRICT RULE: DO NOT mention any websites, URLs, or links. 
            END the email with a clear question asking them to reply to you.
            """
        else:
            strategy_instruction = f"""
            CAMPAIGN: Link Click.
            GOAL: Build interest in: {cta_details['aim']}.
            STRICT RULE: DO NOT write any links or placeholders like [Link]. 
            I will handle the link; you just write the persuasive body.
            """

        # Updated "Unbreakable" Prompt with Source Requirement
        prompt = f"""
        You are writing ONLY the body paragraphs of a professional email from {client_info['name']}.
        Context: {client_info['desc']}
        Recipient: {s_name}

        {strategy_instruction}

        STRICT CONSTRAINTS:
        1. Write ONLY the body paragraphs.
        2. NO greetings or sign-offs.
        3. NO placeholders.
        4. Tone: {client_info.get('tone', 'Professional')}.
        5. MANDATORY: The very last sentence must be a brief, professional disclosure stating we found their contact info via {s_source}.
           Example: "We reached out because your details were listed on {s_source}."
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}]
        )
        ai_meat = completion.choices[0].message.content.strip().replace('\n', '<br>')
        
        # Link Assembly
        link_html = ""
        if not is_reply_campaign:
            tracking_url = f"{TRACKER_URL}?client={client_info['name'].replace(' ', '%20')}"
            link_html = f'<br><br><a href="{tracking_url}" target="_top" style="color: #007bff; font-weight: bold; text-decoration: underline;">Visit Our Website</a>'

        # The Final Sandwich with Legal Footer
        full_html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333;">
            Dear {s_name},<br><br>
            {ai_meat}
            {link_html}<br><br>
            Best regards,<br>
            The {client_info['name']} Team
            
            <br><br>
            <hr style="border: 0; border-top: 1px solid #eee;">
            <div style="font-size: 10px; color: #888; line-height: 1.2;">
                This email was sent to {lead.get('F_EMAIL')} based on legitimate interest for business-to-business networking. 
                Data processed in accordance with UK GDPR. Information sourced via {s_source}. 
                To opt-out of future correspondence, please reply to this email with "Unsubscribe".
            </div>
          </body>
        </html>
        """

        # SMTP Sending
        msg = MIMEMultipart()
        msg['From'] = f"{client_info['name']} <{client_info['email']}>"
        msg['To'] = lead.get('F_EMAIL')
        msg['Subject'] = f"Quick question for {s_name}"
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
    st.title("Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])
    st.divider()
    if st.button("🔄 Sync Clicks from Google"):
        res = sync_clicks_from_google()
        if res == True: st.success("Clicks Updated!"); st.rerun()
        else: st.error("Make sure your Google Sheet is 'Shared with link'")
    st.divider()
    st.caption("🔐 **Data Protection Active**")
    st.caption("Client data and SMTP credentials are encrypted at rest using AES-128 via Fernet.")

# --- PAGE 1: CREATE CLIENT ---
if page == "Create Client":
    st.header("Create New Client")
    with st.form("create_form", clear_on_submit=True):
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
                # Save into session state with safety defaults
                st.session_state.clients[name] = {
                    "name": name, 
                    "desc": desc, 
                    "email": b_email, 
                    "app_pw": app_pw,
                    "auto_on": auto_on, 
                    "auto_days": days, 
                    "cta_aim": cta_aim, 
                    "cta_link": cta_link,
                    "auto_cta_type": "Link Click", # Default strategy
                    "cta_action": "Reply 'YES'",    # Default action
                    "tone": tone, 
                    "leads": df, 
                    "send_log": [], 
                    "clicks": 0 
                }
                save_data()
                st.success(f"Client '{name}' successfully saved to Vault!")
                st.rerun()
            else:
                st.error("Missing Business Name or Leads File.")
# --- PAGE 2: CLIENT VAULT ---
elif page == "Client Vault":
    st.header("Client Vault")
    
    if not st.session_state.clients:
        st.info("The vault is empty. Create a client to get started.")
    else:
        # We use a list to avoid "dictionary changed size during iteration" errors
        for c_name in list(st.session_state.clients.keys()):
            c_data = st.session_state.clients[c_name]
            
            with st.expander(f"🏢 {c_name}"):
                t1, t2, t3 = st.tabs(["Edit Profile", "Automation Settings", "Manual Batch Send"])
                
                # --- TAB 1: EDIT PROFILE (Restored All Fields) ---
                with t1:
                    col1, col2 = st.columns(2)
                    with col1:
                        c_data['name'] = st.text_input("Business Name", c_data.get('name', c_name), key=f"n_{c_name}")
                        c_data['email'] = st.text_input("Sender Email", c_data.get('email', ''), key=f"e_{c_name}")
                        c_data['app_pw'] = st.text_input("App Password", c_data.get('app_pw', ''), type="password", key=f"p_{c_name}")
                    with col2:
                        c_data['tone'] = st.selectbox("Tone", ["Professional", "Friendly", "Direct", "Witty"], 
                                                     index=["Professional", "Friendly", "Direct", "Witty"].index(c_data.get('tone', 'Professional')),
                                                     key=f"t_{c_name}")
                        c_data['desc'] = st.text_area("Business Description", c_data.get('desc', ''), key=f"d_{c_name}")
                    
                    if st.button("Save Profile Changes", key=f"sv_p_{c_name}"):
                        save_data()
                        st.success("Profile Updated and Saved!")

                    st.divider()
                    with st.expander("⚠️ Danger Zone"):
                        if st.button(f"Delete {c_name} Permanently", key=f"del_{c_name}", type="primary"):
                            del st.session_state.clients[c_name]
                            save_data()
                            st.rerun()

                # --- TAB 2: AUTOMATION SETTINGS ---
                with t2:
                    c_data['auto_on'] = st.toggle("Enable Automation", c_data.get('auto_on', False), key=f"at_{c_name}")
                    
                    # Logic to handle Strategy selection
                    current_strat = c_data.get('auto_cta_type', "Link Click")
                    strat_options = ["Link Click", "Direct Reply"]
                    strat_idx = strat_options.index(current_strat) if current_strat in strat_options else 0
                    
                    c_data['auto_cta_type'] = st.selectbox("Campaign Strategy", strat_options, index=strat_idx, key=f"acta_{c_name}")
                    
                    if c_data['auto_cta_type'] == "Link Click":
                        c_data['cta_aim'] = st.text_input("CTA Goal (e.g., Book a Call)", c_data.get('cta_aim', ''), key=f"aa_{c_name}")
                        c_data['cta_link'] = st.text_input("Link URL", c_data.get('cta_link', ''), key=f"al_{c_name}")
                    else:
                        c_data['cta_aim'] = st.text_area("The Offer/Ask", c_data.get('cta_aim', ''), key=f"off_{c_name}")
                        c_data['cta_action'] = st.text_input("Required Action (e.g., Reply YES)", c_data.get('cta_action', "Reply to this email"), key=f"act_{c_name}")

                    if st.button("Save Automation Settings", key=f"sv_a_{c_name}"):
                        save_data()
                        st.success("Automation Saved!")

                # --- TAB 3: MANUAL BATCH SEND (Fixed Batch Logic) ---
                with t3:
                    st.subheader("Send Batch Now")
                    m_type = st.radio("Strategy for this Batch", ["Link Click", "Direct Reply"], horizontal=True, key=f"mt_{c_name}")
                    
                    if m_type == "Link Click":
                        m_aim = st.text_input("Batch Goal", value=c_data.get('cta_aim', ''), key=f"ma_{c_name}")
                        m_link = st.text_input("Batch Link", value=c_data.get('cta_link', ''), key=f"ml_{c_name}")
                        m_action = ""
                    else:
                        m_aim = st.text_area("Batch Offer", value=c_data.get('cta_aim', ''), key=f"moff_{c_name}")
                        m_action = st.text_input("Batch Action", value=c_data.get('cta_action', "Reply to this email"), key=f"mact_{c_name}")
                        m_link = ""

                    if st.button("🚀 Execute Batch Send", key=f"sb_{c_name}"):
                        # Safety check for GROQ Key
                        if not st.session_state.get('g_key'):
                            st.error("Please enter your GROQ API Key in the sidebar first!")
                        else:
                            progress_bar = st.progress(0)
                            leads_df = c_data['leads']
                            total_leads = len(leads_df)
                            
                            # Prepare details for the email function
                            # We combine aim and action for the AI prompt here
                            final_aim = f"{m_aim}. Required Action: {m_action}" if m_type == "Direct Reply" else m_aim
                            
                            for i, (_, lead) in enumerate(leads_df.iterrows()):
                                # CALL THE SENDING LOGIC
                                res = send_email_logic(
                                    c_data, 
                                    lead, 
                                    st.session_state.g_key, 
                                    {"aim": final_aim, "link": m_link, "type": m_type}
                                )
                                
                                # Log the result
                                c_data.setdefault('send_log', []).append({
                                    "Client": c_name, 
                                    "Time": datetime.now().strftime("%Y-%m-%d %H:%M"), 
                                    "Lead": lead.get('F_EMAIL', 'Unknown'), 
                                    "Status": "Success" if res == True else f"Error: {res}"
                                })
                                progress_bar.progress((i + 1) / total_leads)
                            
                            save_data()
                            st.success(f"Batch complete! Sent to {total_leads} leads.")
                            st.rerun()
# --- PAGE 3: EMAIL LOGS ---
elif page == "Email Logs":
    st.header("Email History")

    if not st.session_state.clients:
        st.info("No clients created yet. Create a client to see logs.")
    else:
        # 1. Create a list of clients for the dropdown
        client_list = ["All Clients"] + list(st.session_state.clients.keys())
        
        # 2. Add the Filter UI
        selected_filter = st.selectbox("Filter by Client", client_list)
        
        st.divider()

        # 3. Gather the logs based on the filter
        all_logs = []
        for c_name, c_data in st.session_state.clients.items():
            # If "All Clients" is picked, or the specific client matches
            if selected_filter == "All Clients" or selected_filter == c_name:
                for entry in c_data.get('send_log', []):
                    log_item = entry.copy()
                    # Ensure the Client name is in the row for clarity
                    if 'Client' not in log_item: 
                        log_item['Client'] = c_name
                    all_logs.append(log_item)

        # 4. Display the Data
        if all_logs:
            # Convert to DataFrame and sort by time (newest first)
            log_df = pd.DataFrame(all_logs)
            if "Time" in log_df.columns:
                log_df = log_df.sort_values(by="Time", ascending=False)
            
            st.dataframe(log_df, use_container_width=True, hide_index=True)
            
            # 5. Add a "Clear Logs" button for the specific view
            if st.button(f"Clear Logs for {selected_filter}", type="secondary"):
                if selected_filter == "All Clients":
                    for c_name in st.session_state.clients:
                        st.session_state.clients[c_name]['send_log'] = []
                else:
                    st.session_state.clients[selected_filter]['send_log'] = []
                
                save_data()
                st.success(f"Logs for {selected_filter} cleared!")
                st.rerun()
        else:
            st.warning(f"No emails have been sent for {selected_filter} yet.")

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
