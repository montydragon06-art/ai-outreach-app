import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from cryptography.fernet import Fernet
from streamlit_gsheets import GSheetsConnection
import io

# --- 1. SETTINGS & SECRETS ---
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScBMsqCrO8tKVW4nYLUOVgAewzqUdrom-VXPPPrhsgxPY0rzg/viewform"
PRIVACY_PDF_URL = "https://docs.google.com/document/d/1OjaVW-V5VSXJ9k-mjncAj-xF4gHmVUQwVwrBlXTMxow/edit?usp=sharing"
TRACKER_URL = "https://script.google.com/macros/s/AKfycbxQ45IQwiRw0WxDs9H8QZAFnkkFIHCIQHtMrc6sfEOYJBycj47Q_4EfaycZUJq96K0/exec"

# --- 2. CORE FUNCTIONS ---

def get_conn():
    return st.connection("gsheets", type=GSheetsConnection)

def get_cipher():
    try:
        key = st.secrets["master_key"]
        return Fernet(key.encode())
    except:
        st.error("Master Key missing in Streamlit Secrets!")
        return None

def decrypt_data(encrypted_blob):
    cipher = get_cipher()
    if not cipher: return {}
    try:
        decrypted_data = cipher.decrypt(encrypted_blob.encode()).decode()
        return json.loads(decrypted_data)
    except Exception as e:
        # Silently fail or log to avoid breaking the UI on empty loads
        return {}

def save_data():
    cipher = get_cipher()
    conn = get_conn()
    if not cipher or 'clients' not in st.session_state or not st.session_state.clients: 
        return
    try:
        serializable = {}
        for name, info in st.session_state.clients.items():
            client_copy = info.copy()
            if isinstance(info.get('leads'), pd.DataFrame):
                client_copy['leads'] = info['leads'].to_json()
            serializable[name] = client_copy
        encrypted_blob = cipher.encrypt(json.dumps(serializable).encode()).decode()
        df_to_save = pd.DataFrame([["Master_Vault", encrypted_blob]], columns=["Name", "Data"])
        conn.update(worksheet="Clients", data=df_to_save)
        st.toast("✅ Cloud Backup Synced") 
    except Exception as e:
        st.error(f"❌ Save Failed: {str(e)}")

def load_data():
    conn = get_conn()
    try:
        df = conn.read(worksheet="Clients", ttl=0)
        if df.empty: return {}
        
        raw = {}
        # Fixed the decryption call to handle potential empty/corrupt rows
        for _, row in df.iterrows():
            decrypted = decrypt_data(row['Data'])
            if decrypted:
                raw.update(decrypted)
        
        loaded_clients = {}
        for name, info in raw.items():
            if isinstance(info.get('leads'), str):
                info['leads'] = pd.read_json(io.StringIO(info['leads']))
            if 'send_log' not in info: info['send_log'] = []
            if 'auto_settings' not in info: info['auto_settings'] = {}
            loaded_clients[name] = info
        return loaded_clients
    except Exception as e:
        return {}

# --- NEW: AUTOMATION HEARTBEAT ---
def run_automation_check():
    """Immediately sends emails if current time is past next_run and updates schedule."""
    if 'clients' not in st.session_state or not st.session_state.get('g_key'):
        return

    now = datetime.now()
    updated = False

    for c_name, c_data in st.session_state.clients.items():
        auto = c_data.get('auto_settings', {})
        if auto.get('active') and auto.get('next_run'):
            next_run_dt = datetime.strptime(auto['next_run'], "%Y-%m-%d %H:%M")
            
            if now >= next_run_dt:
                # Trigger sending
                leads = c_data.get('leads')
                if leads is not None and not leads.empty:
                    for _, lead in leads.iterrows():
                        l_email = lead.get('F_EMAIL')
                        status = "Success" if send_email_logic(
                            c_data, lead, st.session_state.g_key, 
                            'link' if auto['method'] == "Link to click" else 'reply', 
                            auto['cta'], auto['offer'], auto['tone']
                        ) == True else "Failed"
                        c_data['send_log'].append({"Time": now.strftime("%Y-%m-%d %H:%M"), "Lead": l_email, "Status": status})
                
                # Calculate next run time based on current time + freq_days
                new_next_run = now + timedelta(days=int(auto.get('freq_days', 1)))
                c_data['auto_settings']['next_run'] = new_next_run.strftime("%Y-%m-%d %H:%M")
                updated = True
    
    if updated:
        save_data()

def get_statistics():
    conn = get_conn()
    stats_data = []
    try:
        clicks_df = conn.read(worksheet="Clicks", ttl=0)
        for c_name, c_data in st.session_state.clients.items():
            sent_log = c_data.get('send_log', [])
            total_sent = len([log for log in sent_log if log.get('Status') == "Success"])
            client_clicks = len(clicks_df[clicks_df["Client"] == c_name]) if not clicks_df.empty and "Client" in clicks_df.columns else 0
            percentage = (client_clicks / total_sent * 100) if total_sent > 0 else 0
            stats_data.append({
                "Client Name": c_name, "Emails Sent": total_sent,
                "Total Clicks": client_clicks, "Click Rate": f"{percentage:.1f}%"
            })
        return pd.DataFrame(stats_data)
    except:
        return pd.DataFrame()

# --- ADD THIS HELPER NEAR send_email_logic ---

def generate_preview_email(client_info, lead, groq_key, send_type, cta_input, offer_input, tone="professional"):
    """Generates the HTML body of an email without sending it. Returns (subject, html_body) or raises."""
    from groq import Groq

    s_name = str(lead.get('F_NAME', 'there')).strip()
    s_email = str(lead.get('F_EMAIL', '')).strip()
    s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
    biz_name = client_info['name']

    if send_type == 'link' and str(cta_input).startswith("http"):
        tracking_link = f"{TRACKER_URL}?dest={cta_input}&client={biz_name.replace(' ', '%20')}&email={s_email}"
        cta_context = f"At the end, include this exact link: <a href='{tracking_link}'>Click here to view details</a>"
    else:
        cta_context = f"End by telling them this exact phrase: {cta_input}"

    groq_client = Groq(api_key=groq_key)
    system_msg = (
        f"You are a factual assistant for {biz_name}. Writing to {s_name}. "
        f"TONE: {tone}. "
        "STRICT RULES: "
        "1. Start immediately with the first sentence. NO GREETINGS (No 'Hi', 'Dear', etc). "
        "2. DO NOT invent details. ONLY use the 'Offer' provided. If no discount is mentioned, do not add one. "
        "3. NO SIGN-OFF or signature. 4. NO PLACEHOLDERS like [Name]."
    )
    user_msg = f"Business Description: {client_info['desc']}\nProvided Offer: {offer_input}\nRequired Action: {cta_context}"

    completion = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
        temperature=0.2
    )
    ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')
    client_privacy = client_info.get('privacy_url', PRIVACY_PDF_URL)
    footer = (
        f"<br><br>Best regards,<br>{biz_name}<br><br><hr/>"
        f"<p style='font-size:10px;color:#888;'>Found via: {s_source} | "
        f"<a href='{FORM_URL}'>Unsubscribe</a> | <a href='{client_privacy}'>Privacy Policy</a></p>"
    )
    full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
    subject = f"Regarding {biz_name}"
    return subject, full_html, s_email
def send_email_logic(client_info, lead, groq_key, send_type, cta_input, offer_input, tone="professional"):
    try:
        s_name = str(lead.get('F_NAME', 'there')).strip()
        s_email = str(lead.get('F_EMAIL', '')).strip()
        s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
        biz_name = client_info['name']
        
        if send_type == 'link' and str(cta_input).startswith("http"):
            tracking_link = f"{TRACKER_URL}?dest={cta_input}&client={biz_name.replace(' ', '%20')}&email={s_email}"
            cta_context = f"At the end, include this exact link: <a href='{tracking_link}'>Click here to view details</a>"
        else:
            cta_context = f"End by telling them this exact phrase: {cta_input}"

        groq_client = Groq(api_key=groq_key)
        
        # FIX 1: Tightened System Message to prevent hallucinations and greetings
        system_msg = (
            f"You are a factual assistant for {biz_name}. Writing to {s_name}. "
            f"TONE: {tone}. "
            "STRICT RULES: "
            "1. Start immediately with the first sentence. NO GREETINGS (No 'Hi', 'Dear', etc). "
            "2. DO NOT invent details. ONLY use the 'Offer' provided. If no discount is mentioned, do not add one. "
            "3. NO SIGN-OFF or signature. 4. NO PLACEHOLDERS like [Name]."
        )
        
        user_msg = f"Business Description: {client_info['desc']}\nProvided Offer: {offer_input}\nRequired Action: {cta_context}"
        
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=0.2 # Lower temperature reduces "creativity" (hallucinations)
        )
        
        ai_body = completion.choices[0].message.content.strip().replace('\n', '<br>')
        
        # FIX 2: Fixed the HTML assembly to ensure only ONE greeting exists
        client_privacy = client_info.get('privacy_url', PRIVACY_PDF_URL)
        footer = f"<br><br>Best regards,<br>{biz_name}<br><br><hr/><p style='font-size:10px;color:#888;'>Found via: {s_source} | <a href='{FORM_URL}'>Unsubscribe</a> | <a href='{client_privacy}'>Privacy Policy</a></p>"
        
        # Ensure the greeting is only here, and the AI body (ai_body) starts without one
        full_html = f"<html><body>Dear {s_name},<br><br>{ai_body}{footer}</body></html>"
        
        msg = MIMEMultipart()
        msg['From'] = f"{biz_name} <{client_info['email']}>"
        msg['To'] = s_email
        msg['Subject'] = f"Regarding {biz_name}" # Better generic subject
        
        msg.attach(MIMEText(full_html, 'html'))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(client_info['email'], client_info['app_pw'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e: 
        return str(e)

# --- 3. SESSION INITIALIZATION ---
st.set_page_config(page_title="Agency Pro CRM", layout="wide")

if 'clients' not in st.session_state:
    st.session_state.clients = load_data()

# Run the automation check every time the app is loaded/refreshed
run_automation_check()

# --- 4. UI INTERFACE ---
with st.sidebar:
    st.title("Command Center")
    st.session_state.g_key = st.text_input("GROQ API Key", type="password")
    page = st.radio("Navigate", ["Create Client", "Client Vault", "Email Logs", "Statistics"])

if page == "Create Client":
    st.header("Create New Client")
    with st.form("create_form"):
        name = st.text_input("Business Name")
        desc = st.text_area("Description")
        b_email = st.text_input("Sender Email")
        app_pw = st.text_input("App Password", type="password")
        
        # NEW: Custom Privacy Link
        p_url = st.text_input("Privacy Policy URL (Link to their PDF/Doc)")
        
        file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])
        
        if st.form_submit_button("Submit"):
            if name and file and p_url:
                df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
                df.columns = [str(c).strip().upper() for c in df.columns]
                df = df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                
                # Added 'privacy_url' to the dictionary
                st.session_state.clients[name] = {
                    "name": name, 
                    "desc": desc, 
                    "email": b_email, 
                    "app_pw": app_pw, 
                    "privacy_url": p_url,
                    "leads": df, 
                    "send_log": [], 
                    "auto_settings": {}
                }
                save_data()
                st.success("Client Created with Custom Privacy Policy!")
                st.rerun()
            else:
                st.error("Please fill in all fields and upload a file.")

elif page == "Client Vault":
    if not st.session_state.clients: 
        st.info("No clients found.")
    
    # Use list() to prevent dictionary size change errors during deletion
    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]
        
        with st.expander(f"🏢 {c_name}"):
            tab_info, tab_auto, tab_manual = st.tabs(["Edit Account", "Automation", "Manual Batch"])
            
            # --- TAB 1: EDIT ACCOUNT (Privacy Link & Leads CSV Update) ---
            with tab_info:
                st.subheader("Update Client Data & Leads")
                new_name = st.text_input("Business Name", value=c_data.get('name', c_name), key=f"en_{c_name}")
                new_email = st.text_input("Sender Email", value=c_data.get('email', ''), key=f"ee_{c_name}")
                new_pw = st.text_input("App Password", value=c_data.get('app_pw', ''), type="password", key=f"ep_{c_name}")
                new_desc = st.text_area("Description", value=c_data.get('desc', ''), key=f"ed_{c_name}")
                
                # Editable Privacy Link
                new_privacy = st.text_input("Privacy Policy URL", value=c_data.get('privacy_url', PRIVACY_PDF_URL), key=f"epriv_{c_name}")
                
                st.write("---")
                st.write("📂 **Replace Leads CSV/XLSX** (Leave blank to keep current leads)")
                new_file = st.file_uploader("Upload new leads file", type=["csv", "xlsx"], key=f"efile_{c_name}")

                if st.button("💾 Save All Changes", key=f"sv_{c_name}"):
                    # Update text fields
                    st.session_state.clients[c_name].update({
                        "name": new_name, 
                        "email": new_email, 
                        "app_pw": new_pw, 
                        "desc": new_desc,
                        "privacy_url": new_privacy
                    })
                    
                    # Process new file if uploaded
                    if new_file:
                        try:
                            new_df = pd.read_excel(new_file) if new_file.name.endswith('.xlsx') else pd.read_csv(new_file, encoding='latin1')
                            new_df.columns = [str(c).strip().upper() for c in new_df.columns]
                            new_df = new_df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                            st.session_state.clients[c_name]['leads'] = new_df
                            st.info("Lead database updated successfully.")
                        except Exception as e:
                            st.error(f"Error processing file: {e}")

                    save_data()
                    st.success("Client information and leads synced to cloud.")
                    st.rerun()

                if st.button("🗑️ Delete Client", key=f"del_{c_name}", type="primary"):
                    del st.session_state.clients[c_name]
                    save_data()
                    st.rerun()

            # --- TAB 2: AUTOMATION ---
            with tab_auto:
                st.subheader("Schedule Campaigns")
                col_a, col_b = st.columns(2)
                with col_a:
                    start_date = st.date_input("Start Date", key=f"date_{c_name}")
                    start_time = st.time_input("Start Time", key=f"time_{c_name}")
                    freq_days = st.number_input("Repeat every (days):", min_value=1, value=1, step=1, key=f"freq_{c_name}")
                with col_b:
                    a_tone = st.selectbox("Email Tone", ["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"], key=f"atone_{c_name}")
                    a_method = st.radio("CTA Type", ["Link to click", "Direct reply"], key=f"am_{c_name}")
                
                a_cta = st.text_input("CTA Link/Action", key=f"ac_{c_name}")
                a_offer = st.text_input("Offer (Optional)", key=f"ao_{c_name}")
                
                if st.button("Enable Automation", key=f"ba_{c_name}"):
                    next_run_val = datetime.combine(start_date, start_time)
                    st.session_state.clients[c_name]['auto_settings'] = {
                        "active": True, 
                        "next_run": next_run_val.strftime("%Y-%m-%d %H:%M"), 
                        "freq_days": freq_days, 
                        "cta": a_cta, 
                        "offer": a_offer, 
                        "method": a_method, 
                        "tone": a_tone
                    }
                    save_data()
                    st.success(f"Scheduled for {next_run_val.strftime('%Y-%m-%d %H:%M')}...")
                    st.rerun()
                
                if c_data.get('auto_settings', {}).get('active'):
                    st.info(f"📍 Next Run: {c_data['auto_settings']['next_run']} | Tone: {c_data['auto_settings'].get('tone')}")

            # --- TAB 3: MANUAL BATCH ---
            # --- TAB 3: MANUAL BATCH ---
            with tab_manual:
                st.subheader("🚀 Execute One-Time Batch")
                st.markdown("---")

                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    m_method = st.radio(
                        "1. How should they respond?",
                        ["Link to click", "Direct reply to email"],
                        key=f"mm_{c_name}",
                        help="Choose 'Link' to include a tracking URL, or 'Direct reply' to encourage a conversation."
                    )
                with col_m2:
                    m_tone = st.selectbox(
                        "2. Choose the Email Tone",
                        ["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"],
                        key=f"mtone_{c_name}"
                    )

                st.markdown("---")
                st.write("### 3. Customize the Message Content")

                m_offer = st.text_area(
                    "The Special Offer",
                    placeholder="e.g., A 20% discount code for first-time buyers...",
                    key=f"mo_{c_name}"
                )

                if m_method == "Link to click":
                    m_cta = st.text_input(
                        "Destination URL (Link)",
                        placeholder="https://yourwebsite.com",
                        key=f"mc_{c_name}"
                    )
                else:
                    m_cta = st.text_input(
                        "Call to Action (Reply Instruction)",
                        placeholder="e.g., Let me know if you're interested.",
                        key=f"mc_{c_name}"
                    )

                st.write("")

                # --- Preview state keys ---
                preview_key = f"preview_data_{c_name}"
                confirmed_key = f"preview_confirmed_{c_name}"

                # --- STEP 1: Preview button ---
                if st.button("🔍 Preview Sample Emails First", key=f"prev_{c_name}", use_container_width=True):
                    if not st.session_state.get('g_key'):
                        st.error("⚠️ Enter your GROQ Key in the sidebar first!")
                    elif m_method == "Link to click" and not m_cta.startswith("http"):
                        st.error("⚠️ Please enter a valid URL starting with http:// or https://")
                    elif not m_offer or not m_cta:
                        st.error("⚠️ Please fill in both the Offer and the CTA/Link.")
                    else:
                        leads = c_data.get('leads', pd.DataFrame())
                        if leads.empty:
                            st.warning("No leads found for this client.")
                        else:
                            sample_leads = leads.head(2)  # Preview first 2 leads
                            previews = []
                            send_type = 'link' if m_method == "Link to click" else 'reply'
                            with st.spinner("Generating preview emails via GROQ..."):
                                for _, lead in sample_leads.iterrows():
                                    try:
                                        subj, html_body, recipient = generate_preview_email(
                                            c_data, lead, st.session_state.g_key,
                                            send_type, m_cta, m_offer, m_tone
                                        )
                                        previews.append({"to": recipient, "subject": subj, "html": html_body})
                                    except Exception as e:
                                        previews.append({"to": lead.get('F_EMAIL', '?'), "subject": "Error", "html": f"<p>Failed to generate: {e}</p>"})

                            st.session_state[preview_key] = {
                                "previews": previews,
                                "send_type": send_type,
                                "cta": m_cta,
                                "offer": m_offer,
                                "tone": m_tone,
                            }
                            st.session_state[confirmed_key] = False

                # --- STEP 2: Show previews if they exist ---
                if preview_key in st.session_state and not st.session_state.get(confirmed_key, False):
                    preview_data = st.session_state[preview_key]
                    previews = preview_data["previews"]

                    st.markdown("---")
                    st.write(f"### 📧 Sample Preview ({len(previews)} of {len(c_data.get('leads', pd.DataFrame()))} leads)")
                    st.caption("These are exactly what your leads would receive. Review before confirming.")

                    for i, p in enumerate(previews):
                        with st.expander(f"Preview {i+1} → To: {p['to']} | Subject: {p['subject']}", expanded=(i == 0)):
                            st.components.v1.html(p["html"], height=320, scrolling=True)

                    st.markdown("---")
                    col_confirm, col_cancel = st.columns(2)

                    with col_confirm:
                        if st.button("✅ Looks Good — Send to All Leads", key=f"confirm_{c_name}", use_container_width=True, type="primary"):
                            st.session_state[confirmed_key] = True
                            st.rerun()

                    with col_cancel:
                        if st.button("❌ Cancel — Go Back and Edit", key=f"cancel_{c_name}", use_container_width=True):
                            del st.session_state[preview_key]
                            st.session_state.pop(confirmed_key, None)
                            st.rerun()

                # --- STEP 3: Execute full batch after confirmation ---
                if st.session_state.get(confirmed_key, False) and preview_key in st.session_state:
                    preview_data = st.session_state[preview_key]
                    st.info("✅ Confirmed! Sending to all leads now...")

                    progress = st.progress(0)
                    leads = c_data.get('leads', pd.DataFrame())

                    for i, (_, lead) in enumerate(leads.iterrows()):
                        l_email = lead.get('F_EMAIL')
                        try:
                            is_blacklisted = check_blacklist(l_email)
                        except NameError:
                            is_blacklisted = False

                        if is_blacklisted:
                            status = "Skipped"
                        else:
                            res = send_email_logic(
                                c_data, lead, st.session_state.g_key,
                                preview_data["send_type"],
                                preview_data["cta"],
                                preview_data["offer"],
                                preview_data["tone"]
                            )
                            status = "Success" if res == True else "Failed"

                        c_data['send_log'].append({
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Lead": l_email,
                            "Status": status
                        })
                        progress.progress((i + 1) / len(leads))

                    # Clean up preview state
                    del st.session_state[preview_key]
                    del st.session_state[confirmed_key]

                    save_data()
                    st.success(f"✅ Batch Complete! {len(leads)} leads processed.")
                    st.rerun()
elif page == "Email Logs":
    st.header("📋 History")
    all_logs = []
    for c_name, c_data in st.session_state.clients.items():
        for entry in c_data.get('send_log', []):
            all_logs.append({**entry, "Company": c_name})
    if all_logs: st.dataframe(pd.DataFrame(all_logs), use_container_width=True)

elif page == "Statistics":
    st.header("📊 Stats")
    df_stats = get_statistics()
    if not df_stats.empty:
        st.dataframe(df_stats, use_container_width=True, hide_index=True)
