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
PRIVACY_PDF_URL = "https://docs.google.com/document/d/1OjaVW-V5VSXJ9k-mjncAj-xF4gHmVUQwVwrBlXTMxow/edit?usp=sharing"
TRACKER_URL = "https://email-tracker.montydragon06.workers.dev/"
UNSUBSCRIBE_URL = "https://email-unsubscribe.montydragon06.workers.dev"
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
def check_blacklist(email):
    """Returns True if this email has previously unsubscribed."""
    if not email:
        return False
    try:
        conn = get_conn()
        df = conn.read(worksheet="Unsubscribes", ttl=60)
        if df.empty:
            return False
        unsubscribed = df.iloc[:, 1].astype(str).str.lower().tolist()
        return email.strip().lower() in unsubscribed
    except Exception:
        return False


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
            if 'campaigns' not in info: info['campaigns'] = []
            if 'send_log' not in info: info['send_log'] = []
            if 'logo_url' not in info: info['logo_url'] = ''
            if 'signature' not in info: info['signature'] = ''
            loaded_clients[name] = info
        return loaded_clients
    except Exception as e:
        return {}

# --- NEW: AUTOMATION HEARTBEAT ---
def run_automation_check():
    """Handles both automation heartbeat and scheduled campaign execution."""
    if 'clients' not in st.session_state or not st.session_state.get('g_key'):
        return

    now = datetime.now()
    updated = False

    for c_name, c_data in st.session_state.clients.items():

        # --- AUTOMATION HEARTBEAT ---
        auto = c_data.get('auto_settings', {})
        if auto.get('active') and auto.get('next_run'):
            next_run_dt = datetime.strptime(auto['next_run'], "%Y-%m-%d %H:%M")
            if now >= next_run_dt:
                leads = c_data.get('leads')
                if leads is not None and not leads.empty:
                    for _, lead in leads.iterrows():
                        l_email = lead.get('F_EMAIL')
                        if check_blacklist(l_email):
                            status = "Skipped"
                        else:
                            status = "Success" if send_email_logic(
                                c_data, lead, st.session_state.g_key,
                                'link' if auto['method'] == "Link to click" else 'reply',
                                auto['cta'], auto['offer'], auto['tone'],
                                show_logo=auto.get('show_logo', True)
                            ) == True else "Failed"
                        c_data['send_log'].append({
                            "Time":   now.strftime("%Y-%m-%d %H:%M"),
                            "Lead":   l_email,
                            "Status": status
                        })
                new_next_run = now + timedelta(days=int(auto.get('freq_days', 1)))
                c_data['auto_settings']['next_run'] = new_next_run.strftime("%Y-%m-%d %H:%M")
                updated = True

        # --- CAMPAIGN EXECUTION ---
        campaigns = c_data.get('campaigns', [])
        for idx, campaign in enumerate(campaigns):

            # Only process scheduled campaigns whose start time has passed
            if campaign.get('status') != 'Scheduled':
                continue

            camp_start = datetime.strptime(
                f"{campaign['start_date']} {campaign['start_time']}",
                "%Y-%m-%d %H:%M"
            )
            if now < camp_start:
                continue

            # Get leads for this campaign
            leads = c_data.get('leads', pd.DataFrame())
            if leads.empty:
                c_data['campaigns'][idx]['status'] = 'Completed'
                updated = True
                continue

            email_count  = int(campaign.get('email_count', len(leads)))
            period_days  = int(campaign.get('period_days', 1))
            send_type    = 'link' if campaign.get('method') == 'Link to click' else 'reply'
            emails_sent  = int(campaign.get('emails_sent', 0))

            # Work out how many emails should have been sent by now
            # Spread email_count evenly across period_days
            total_minutes   = period_days * 24 * 60
            minutes_elapsed = max(0, (now - camp_start).total_seconds() / 60)
            progress_ratio  = min(1.0, minutes_elapsed / total_minutes)
            target_sent     = min(email_count, int(progress_ratio * email_count) + 1)

            # Send the gap between what's been sent and what should be sent by now
            emails_to_send_now = target_sent - emails_sent

            if emails_to_send_now <= 0:
                continue

            # Pick the next unsent leads
            camp_leads = leads.iloc[emails_sent: emails_sent + emails_to_send_now]

            sent_this_run = 0
            for _, lead in camp_leads.iterrows():
                l_email = lead.get('F_EMAIL')
                if check_blacklist(l_email):
                    status = "Skipped"
                else:
                    res = send_email_logic(
                        c_data, lead, st.session_state.g_key,
                        send_type,
                        campaign.get('cta', ''),
                        campaign.get('offer', ''),
                        campaign.get('tone', 'Professional'),
                        show_logo=campaign.get('show_logo', True)
                    )
                    status = "Success" if res == True else "Failed"

                c_data['send_log'].append({
                    "Time":     now.strftime("%Y-%m-%d %H:%M"),
                    "Lead":     l_email,
                    "Status":   status,
                    "Campaign": campaign['name']
                })
                sent_this_run += 1

            # Update campaign progress
            new_emails_sent = emails_sent + sent_this_run
            c_data['campaigns'][idx]['emails_sent'] = new_emails_sent

            # Mark complete if all emails sent
            if new_emails_sent >= email_count:
                c_data['campaigns'][idx]['status'] = 'Completed'

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
def build_email_prompt(client_info, lead, send_type, cta_input, offer_input, tone, show_logo=True):
    """
    Builds prompts and HTML components for an email.
    show_logo=True uses the client's logo URL if one exists.
    If no logo URL is stored, no logo is shown regardless of show_logo.
    If no signature is stored, falls back to a default sign-off.
    """

    s_name   = str(lead.get('F_NAME', 'there')).strip()
    s_email  = str(lead.get('F_EMAIL', '')).strip()
    s_source = str(lead.get('F_SOURCE', 'Public Records')).strip()
    biz_name = client_info['name']
    biz_desc = str(client_info.get('desc', '')).strip()
    logo_url  = client_info.get('logo_url', '').strip()
    signature = client_info.get('signature', '').strip()

    # --- CTA ---
    if send_type == 'link' and str(cta_input).strip().startswith("http"):
        tracking_link = (
            f"{TRACKER_URL}?dest={cta_input}"
            f"&client={biz_name.replace(' ', '%20')}"
            f"&email={s_email}"
        )
        cta_context = (
            "End the email with this exact clickable link and no other call to action: "
            f"<a href='{tracking_link}'>Click here to view details</a>"
        )
    else:
        cta_context = (
            f"End the email with this exact phrase and no other call to action: "
            f"{str(cta_input).strip()}"
        )

    # --- Offer ---
    offer_stripped = str(offer_input).strip() if offer_input else ""
    if offer_stripped:
        offer_block = (
            "OFFER TO INCLUDE: Reproduce this offer faithfully and do not add to it, "
            f"modify it, or embellish it in any way: {offer_stripped}"
        )
    else:
        offer_block = (
            "OFFER: There is no offer. You MUST NOT mention discounts, deals, savings, "
            "promotions, special prices, free trials, guarantees, or any benefit or incentive "
            "that has not been explicitly provided to you. Do not hint at or imply any offer."
        )

    # --- System message ---
    system_msg = (
        f"You are a professional email copywriter writing on behalf of {biz_name}. "
        f"You are writing to {s_name}.\n\n"
        f"TONE: {tone}\n\n"
        "ABSOLUTE RULES — every one of these is a hard requirement. "
        "Breaking any of them makes the email unusable:\n"
        "1. FACTUAL ONLY: Every sentence must be directly supported by the Business Description "
        "or the Offer provided. Do not invent, assume, imply, or embellish any fact, feature, "
        "benefit, claim, statistic, or detail that you have not been explicitly given.\n"
        "2. NO GREETING: Do not start with 'Hi', 'Dear', 'Hello', or any salutation. "
        "The greeting is added separately. Begin with the first sentence of the body.\n"
        "3. NO SIGN-OFF: Do not write 'Best regards', 'Sincerely', 'Thanks', or any closing. "
        "The signature is added separately.\n"
        "4. NO PLACEHOLDERS: Do not write [Name], [Company], [Link], [Date], or any bracketed "
        "or placeholder text. If you do not have a value, omit that point entirely.\n"
        "5. OFFER DISCIPLINE: Follow the OFFER instruction below exactly. "
        "If no offer is provided, write a clean professional outreach email using only "
        "the Business Description. Do not compensate for the missing offer by inventing benefits.\n"
        "6. ONE CALL TO ACTION: Use only the CTA provided. Do not add extra links, contact details, "
        "phone numbers, or secondary actions unless they appear in the Business Description.\n"
        "7. LENGTH: 3 to 5 short paragraphs. Concise and direct."
    )

    user_msg = (
        f"Business Description:\n{biz_desc}\n\n"
        f"{offer_block}\n\n"
        f"Call to Action:\n{cta_context}"
    )

    # --- Logo HTML (top right, only if URL provided and show_logo is True) ---
    if show_logo and logo_url:
        logo_html = (
            "<div style='text-align:right; padding-bottom:16px; "
            "border-bottom:2px solid #f0f0f0; margin-bottom:24px;'>"
            f"<img src='{logo_url}' alt='{biz_name} logo' "
            "style='max-height:60px; max-width:200px; object-fit:contain;' "
            "onerror=\"this.style.display='none'\"/>"
            "</div>"
        )
    else:
        logo_html = ""

    # --- Signature HTML ---
    # If a custom signature exists, use it. Otherwise fall back to default.
    if signature:
        sig_lines = signature.replace('\n', '<br>')
        signature_html = (
            "<div style='margin-top:32px; padding-top:16px; "
            "border-top:1px solid #f0f0f0; font-size:13px; "
            "color:#444; line-height:1.8;'>"
            f"{sig_lines}"
            "</div>"
        )
    else:
        signature_html = (
            "<div style='margin-top:32px; font-size:13px; color:#444;'>"
            f"Best regards,<br><strong>{biz_name}</strong>"
            "</div>"
        )

    # --- Legal footer (always present) ---
    client_privacy = client_info.get('privacy_url', PRIVACY_PDF_URL)

    unsubscribe_link = (
        f"{UNSUBSCRIBE_URL}"
        f"?email={s_email}"
        f"&client={biz_name.replace(' ', '%20')}"
    )

    legal_footer = (
        "<div style='margin-top:24px; padding-top:12px; "
        "border-top:1px solid #f0f0f0; font-size:10px; color:#aaa;'>"
        f"Found via: {s_source} &nbsp;|&nbsp; "
        f"<a href='{unsubscribe_link}' style='color:#aaa;'>Unsubscribe</a> &nbsp;|&nbsp; "
        f"<a href='{client_privacy}' style='color:#aaa;'>Privacy Policy</a>"
        "</div>"
    )

    footer = signature_html + legal_footer

    return system_msg, user_msg, s_name, s_email, biz_name, footer, logo_html


def generate_preview_email(client_info, lead, groq_key, send_type, cta_input,
                            offer_input, tone="professional", show_logo=True):
    system_msg, user_msg, s_name, s_email, biz_name, footer, logo_html = build_email_prompt(
        client_info, lead, send_type, cta_input, offer_input, tone, show_logo
    )
    groq_client = Groq(api_key=groq_key)
    completion  = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg}
        ],
        temperature=0.1,
        max_tokens=600
    )
    ai_body  = completion.choices[0].message.content.strip().replace('\n', '<br>')
    full_html = (
        "<html><body style='font-family:Arial,sans-serif; max-width:600px; "
        "margin:auto; padding:32px; color:#222;'>"
        f"{logo_html}"
        f"Dear {s_name},<br><br>"
        f"{ai_body}"
        f"{footer}"
        "</body></html>"
    )
    return f"A message from {biz_name}", full_html, s_email


def send_email_logic(client_info, lead, groq_key, send_type, cta_input,
                     offer_input, tone="professional", show_logo=True):
    try:
        system_msg, user_msg, s_name, s_email, biz_name, footer, logo_html = build_email_prompt(
            client_info, lead, send_type, cta_input, offer_input, tone, show_logo
        )
        if not s_email:
            return "No email address for lead"

        groq_client = Groq(api_key=groq_key)
        completion  = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg}
            ],
            temperature=0.1,
            max_tokens=600
        )
        ai_body  = completion.choices[0].message.content.strip().replace('\n', '<br>')
        full_html = (
            "<html><body style='font-family:Arial,sans-serif; max-width:600px; "
            "margin:auto; padding:32px; color:#222;'>"
            f"{logo_html}"
            f"Dear {s_name},<br><br>"
            f"{ai_body}"
            f"{footer}"
            "</body></html>"
        )
        msg            = MIMEMultipart()
        msg['From']    = f"{biz_name} <{client_info['email']}>"
        msg['To']      = s_email
        msg['Subject'] = f"A message from {biz_name}"
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
        st.subheader("Business Details")
        name    = st.text_input("Business Name")
        desc    = st.text_area("Business Description")
        b_email = st.text_input("Sender Email")
        app_pw  = st.text_input("App Password", type="password")
        p_url   = st.text_input("Privacy Policy URL")

        st.markdown("---")
        st.subheader("Branding (Optional)")
        st.caption("Leave these blank for a default signature and no logo.")

        logo_url = st.text_input(
            "Logo URL",
            placeholder="https://yourwebsite.com/logo.png",
            help="Right-click your logo on your website → Copy image address. Must be a direct .png/.jpg link."
        )
        signature = st.text_area(
            "Email Signature",
            placeholder="John Smith\nSales Director\nAcme Ltd  |  0800 123 456  |  acme.com",
            help="Appears at the bottom of every email. Leave blank for a default 'Best regards, [Business Name]' signature.",
            height=100
        )

        st.markdown("---")
        file = st.file_uploader("Leads Spreadsheet", type=["csv", "xlsx"])

        if st.form_submit_button("Create Client"):
            if name and file and p_url:
                df = pd.read_excel(file) if file.name.endswith('.xlsx') else pd.read_csv(file, encoding='latin1')
                df.columns = [str(c).strip().upper() for c in df.columns]
                df = df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                st.session_state.clients[name] = {
                    "name":          name,
                    "desc":          desc,
                    "email":         b_email,
                    "app_pw":        app_pw,
                    "privacy_url":   p_url,
                    "logo_url":      logo_url.strip(),
                    "signature":     signature.strip(),
                    "leads":         df,
                    "send_log":      [],
                    "auto_settings": {},
                    "campaigns":     []
                }
                save_data()
                st.success("Client created!")
                st.rerun()
            else:
                st.error("Business Name, Privacy URL and a Leads file are all required.")
elif page == "Client Vault":
    if not st.session_state.clients:
        st.info("No clients found.")

    for c_name in list(st.session_state.clients.keys()):
        c_data = st.session_state.clients[c_name]

        # Ensure campaigns key exists on older records
        if 'campaigns' not in c_data:
            c_data['campaigns'] = []

        with st.expander(f"🏢 {c_name}"):
            tab_info, tab_auto, tab_manual, tab_new_campaign, tab_saved_campaigns = st.tabs([
                "Edit Account",
                "Automation",
                "Manual Batch",
                "Create Campaign",
                "Saved Campaigns"
            ])

            # ----------------------------------------------------------------
            # TAB 1: EDIT ACCOUNT
            # ----------------------------------------------------------------
            with tab_info:
                st.subheader("Update Client Data & Leads")
                new_name    = st.text_input("Business Name",      value=c_data.get('name', c_name),               key=f"en_{c_name}")
                new_email   = st.text_input("Sender Email",        value=c_data.get('email', ''),                  key=f"ee_{c_name}")
                new_pw      = st.text_input("App Password",        value=c_data.get('app_pw', ''),                 key=f"ep_{c_name}", type="password")
                new_desc    = st.text_area("Description",          value=c_data.get('desc', ''),                   key=f"ed_{c_name}")
                new_privacy = st.text_input("Privacy Policy URL",  value=c_data.get('privacy_url', PRIVACY_PDF_URL), key=f"epriv_{c_name}")

                st.markdown("---")
                st.subheader("Branding")
                st.caption("Leave blank for default signature and no logo.")
                new_logo = st.text_input(
                    "Logo URL",
                    value=c_data.get('logo_url', ''),
                    key=f"elogo_{c_name}",
                    placeholder="https://yourwebsite.com/logo.png"
                )
                new_signature = st.text_area(
                    "Email Signature",
                    value=c_data.get('signature', ''),
                    key=f"esig_{c_name}",
                    placeholder="John Smith\nSales Director\nAcme Ltd  |  0800 123 456  |  acme.com",
                    height=100
                )

                st.markdown("---")
                st.write("📂 **Replace Leads CSV/XLSX** (Leave blank to keep current leads)")
                new_file = st.file_uploader("Upload new leads file", type=["csv", "xlsx"], key=f"efile_{c_name}")

                if st.button("💾 Save All Changes", key=f"sv_{c_name}"):
                    st.session_state.clients[c_name].update({
                        "name":        new_name,
                        "email":       new_email,
                        "app_pw":      new_pw,
                        "desc":        new_desc,
                        "privacy_url": new_privacy,
                        "logo_url":    new_logo.strip(),
                        "signature":   new_signature.strip()
                    })
                    if new_file:
                        try:
                            new_df = pd.read_excel(new_file) if new_file.name.endswith('.xlsx') else pd.read_csv(new_file, encoding='latin1')
                            new_df.columns = [str(c).strip().upper() for c in new_df.columns]
                            new_df = new_df.rename(columns={"NAME": "F_NAME", "EMAIL": "F_EMAIL", "SOURCE": "F_SOURCE"})
                            st.session_state.clients[c_name]['leads'] = new_df
                            st.info("Lead database updated.")
                        except Exception as e:
                            st.error(f"Error processing file: {e}")
                    save_data()
                    st.success("Client information synced to cloud.")
                    st.rerun()

                if st.button("🗑️ Delete Client", key=f"del_{c_name}", type="primary"):
                    del st.session_state.clients[c_name]
                    save_data()
                    st.rerun()
            # ----------------------------------------------------------------
            # TAB 2: AUTOMATION
            # ----------------------------------------------------------------
            with tab_auto:
                st.subheader("Schedule Campaigns")
                col_a, col_b = st.columns(2)
                with col_a:
                    start_date = st.date_input("Start Date",           key=f"date_{c_name}")
                    start_time = st.time_input("Start Time",           key=f"time_{c_name}")
                    freq_days  = st.number_input("Repeat every (days):", min_value=1, value=1, step=1, key=f"freq_{c_name}")
                with col_b:
                    a_tone   = st.selectbox("Email Tone", ["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"], key=f"atone_{c_name}")
                    a_method = st.radio("CTA Type", ["Link to click", "Direct reply"], key=f"am_{c_name}")

                a_cta   = st.text_input("CTA Link/Action", key=f"ac_{c_name}")
                a_offer = st.text_input("Offer (Optional)", key=f"ao_{c_name}")
                a_show_logo = st.checkbox(
                    "Include company logo in automated emails",
                    value=True,
                    key=f"alogo_{c_name}"
                )

                if st.button("Enable Automation", key=f"ba_{c_name}"):
                    next_run_val = datetime.combine(start_date, start_time)
                    st.session_state.clients[c_name]['auto_settings'] = {
                        "active":    True,
                        "next_run":  next_run_val.strftime("%Y-%m-%d %H:%M"),
                        "freq_days": freq_days,
                        "cta":       a_cta,
                        "offer":     a_offer,
                        "method":    a_method,
                        "tone":      a_tone,
                        "show_logo": a_show_logo
                    }
                    save_data()
                    st.success(f"Scheduled for {next_run_val.strftime('%Y-%m-%d %H:%M')}...")
                    st.rerun()

                if c_data.get('auto_settings', {}).get('active'):
                    st.info(f"📍 Next Run: {c_data['auto_settings']['next_run']} | Tone: {c_data['auto_settings'].get('tone')}")

            # ----------------------------------------------------------------
            # TAB 3: MANUAL BATCH
            # ----------------------------------------------------------------
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
                    m_cta = st.text_input("Destination URL (Link)", placeholder="https://yourwebsite.com", key=f"mc_{c_name}")
                else:
                    m_cta = st.text_input("Call to Action (Reply Instruction)", placeholder="e.g., Let me know if you're interested.", key=f"mc_{c_name}")

                m_show_logo = st.checkbox(
                    "Include company logo in emails",
                    value=True,
                    key=f"mlogo_{c_name}"
                )

                st.write("")

                preview_key   = f"preview_data_{c_name}"
                confirmed_key = f"preview_confirmed_{c_name}"

                if st.button("🔍 Preview Sample Emails First", key=f"prev_{c_name}", use_container_width=True):
                    if not st.session_state.get('g_key'):
                        st.error("⚠️ Enter your GROQ Key in the sidebar first!")
                    elif m_method == "Link to click" and not m_cta.startswith("http"):
                        st.error("⚠️ Please enter a valid URL starting with http:// or https://")
                    elif not m_offer or not m_cta:
                        st.error("⚠️ Please fill in both the Offer and the CTA/Link.")
                    else:
                        leads     = c_data.get('leads', pd.DataFrame())
                        send_type = 'link' if m_method == "Link to click" else 'reply'
                        if leads.empty:
                            st.warning("No leads found for this client.")
                        else:
                            previews = []
                            with st.spinner("Generating preview emails via GROQ..."):
                                for _, lead in leads.head(2).iterrows():
                                    try:
                                        subj, html_body, recipient = generate_preview_email(
                                            c_data, lead, st.session_state.g_key,
                                            send_type, m_cta, m_offer, m_tone,
                                            show_logo=m_show_logo
                                        )
                                        previews.append({"to": recipient, "subject": subj, "html": html_body})
                                    except Exception as e:
                                        previews.append({"to": lead.get('F_EMAIL', '?'), "subject": "Error", "html": f"<p>Failed to generate: {e}</p>"})
                            st.session_state[preview_key] = {"previews": previews, "send_type": send_type, "cta": m_cta, "offer": m_offer, "tone": m_tone, "show_logo": m_show_logo}
                            st.session_state[confirmed_key] = False

                if preview_key in st.session_state and not st.session_state.get(confirmed_key, False):
                    preview_data = st.session_state[preview_key]
                    st.markdown("---")
                    st.write(f"### 📧 Sample Preview ({len(preview_data['previews'])} of {len(c_data.get('leads', pd.DataFrame()))} leads)")
                    st.caption("These are exactly what your leads would receive. Review before confirming.")
                    for i, p in enumerate(preview_data['previews']):
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

                if st.session_state.get(confirmed_key, False) and preview_key in st.session_state:
                    preview_data = st.session_state[preview_key]
                    st.info("✅ Confirmed! Sending to all leads now...")
                    progress = st.progress(0)
                    leads    = c_data.get('leads', pd.DataFrame())
                    for i, (_, lead) in enumerate(leads.iterrows()):
                        l_email = lead.get('F_EMAIL')
                        is_blacklisted = check_blacklist(l_email)
                        if is_blacklisted:
                            status = "Skipped"
                        else:
                            res = send_email_logic(c_data, lead, st.session_state.g_key, preview_data["send_type"], preview_data["cta"], preview_data["offer"], preview_data["tone"], show_logo=preview_data.get("show_logo", True))
                            status = "Success" if res == True else "Failed"
                        c_data['send_log'].append({"Time": datetime.now().strftime("%Y-%m-%d %H:%M"), "Lead": l_email, "Status": status})
                        progress.progress((i + 1) / len(leads))
                    del st.session_state[preview_key]
                    del st.session_state[confirmed_key]
                    save_data()
                    st.success(f"✅ Batch Complete! {len(leads)} leads processed.")
                    st.rerun()

            # ----------------------------------------------------------------
            # TAB 4: CREATE CAMPAIGN
            # ----------------------------------------------------------------
            with tab_new_campaign:
                st.subheader("Create New Campaign")
                st.markdown("---")

                with st.form(key=f"new_campaign_form_{c_name}"):
                    camp_name = st.text_input(
                        "Campaign Name",
                        placeholder="e.g., Summer Promotion 2026"
                    )

                    col_c1, col_c2 = st.columns(2)
                    with col_c1:
                        camp_start_date = st.date_input("Start Date")
                        camp_email_count = st.number_input(
                            "Number of Emails to Send",
                            min_value=1,
                            value=1,
                            step=1,
                            help="How many leads from your list to contact in this campaign."
                        )
                    with col_c2:
                        camp_start_time = st.time_input("Start Time")
                        camp_period_days = st.number_input(
                            "Send Period (days)",
                            min_value=1,
                            value=7,
                            step=1,
                            help="Spread the emails evenly across this many days."
                        )

                    camp_offer = st.text_area(
                        "Special Offer",
                        placeholder="e.g., 20% off for new customers — use code SUMMER20 at checkout."
                    )

                    col_c3, col_c4 = st.columns(2)
                    with col_c3:
                        camp_tone = st.selectbox(
                            "Email Tone",
                            ["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"]
                        )
                        camp_method = st.radio(
                            "CTA Type",
                            ["Link to click", "Direct reply"]
                        )
                    with col_c4:
                        camp_cta = st.text_input(
                            "CTA Link or Instruction",
                            placeholder="https://yoursite.com or 'Reply to this email'"
                        )
                        camp_show_logo = st.checkbox(
                            "Include company logo in emails",
                            value=True
                        )

                    submitted = st.form_submit_button("💾 Save Campaign", use_container_width=True)

                    if submitted:
                        if not camp_name:
                            st.error("Please give the campaign a name.")
                        elif not camp_cta:
                            st.error("Please provide a CTA link or instruction.")
                        else:
                            import uuid
                            new_campaign = {
                                "id":           str(uuid.uuid4()),
                                "name":         camp_name,
                                "start_date":   camp_start_date.strftime("%Y-%m-%d"),
                                "start_time":   camp_start_time.strftime("%H:%M"),
                                "email_count":  int(camp_email_count),
                                "period_days":  int(camp_period_days),
                                "offer":        camp_offer,
                                "tone":         camp_tone,
                                "method":       camp_method,
                                "cta":          camp_cta,
                                "show_logo":   camp_show_logo,
                                "status":       "Scheduled",
                                "created_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "emails_sent":  0
                            }
                            st.session_state.clients[c_name]['campaigns'].append(new_campaign)
                            save_data()
                            st.success(f"✅ Campaign '{camp_name}' saved successfully!")
                            st.rerun()

            # ----------------------------------------------------------------
            # TAB 5: SAVED CAMPAIGNS
            # ----------------------------------------------------------------
            with tab_saved_campaigns:
                st.subheader("Saved Campaigns")
                st.markdown("---")

                campaigns = c_data.get('campaigns', [])

                if not campaigns:
                    st.info("No campaigns yet. Create one in the 'Create Campaign' tab.")
                else:
                    for idx, campaign in enumerate(campaigns):
                        camp_id     = campaign.get('id', str(idx))
                        camp_status = campaign.get('status', 'Scheduled')

                        # Status badge colour
                        badge_colour = {
                            "Scheduled": "🔵",
                            "Running":   "🟡",
                            "Completed": "🟢",
                            "Cancelled": "🔴"
                        }.get(camp_status, "⚪")

                        with st.expander(
                            f"{badge_colour} {campaign['name']}  |  "
                            f"Start: {campaign['start_date']} {campaign['start_time']}  |  "
                            f"Emails: {campaign['email_count']}  |  "
                            f"Period: {campaign['period_days']}d  |  "
                            f"Status: {camp_status}",
                            expanded=False
                        ):
                            with st.form(key=f"edit_campaign_{c_name}_{camp_id}"):
                                st.write("#### Edit Campaign")

                                e_name = st.text_input("Campaign Name", value=campaign['name'])

                                col_e1, col_e2 = st.columns(2)
                                with col_e1:
                                    e_start_date = st.date_input(
                                        "Start Date",
                                        value=datetime.strptime(campaign['start_date'], "%Y-%m-%d").date()
                                    )
                                    e_email_count = st.number_input(
                                        "Number of Emails to Send",
                                        min_value=1,
                                        value=int(campaign['email_count']),
                                        step=1
                                    )
                                with col_e2:
                                    e_start_time = st.time_input(
                                        "Start Time",
                                        value=datetime.strptime(campaign['start_time'], "%H:%M").time()
                                    )
                                    e_period_days = st.number_input(
                                        "Send Period (days)",
                                        min_value=1,
                                        value=int(campaign['period_days']),
                                        step=1
                                    )

                                e_offer = st.text_area("Special Offer", value=campaign.get('offer', ''))

                                col_e3, col_e4 = st.columns(2)
                                with col_e3:
                                    e_tone = st.selectbox(
                                        "Email Tone",
                                        ["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"],
                                        index=["Professional", "Friendly & Casual", "Urgent", "Direct & Short", "Salesy"].index(campaign.get('tone', 'Professional'))
                                    )
                                    e_method = st.radio(
                                        "CTA Type",
                                        ["Link to click", "Direct reply"],
                                        index=["Link to click", "Direct reply"].index(campaign.get('method', 'Link to click'))
                                    )
                                with col_e4:
                                    e_cta = st.text_input("CTA Link or Instruction", value=campaign.get('cta', ''))
                                    e_status = st.selectbox(
                                        "Status",
                                        ["Scheduled", "Running", "Completed", "Cancelled"],
                                        index=["Scheduled", "Running", "Completed", "Cancelled"].index(camp_status)
                                    )
                                e_show_logo = st.checkbox(
                                    "Include company logo in emails",
                                    value=campaign.get('show_logo', True),
                                    key=f"esl_{c_name}_{camp_id}"
                                )
                                
                                    

                                col_save, col_delete = st.columns(2)
                                with col_save:
                                    save_edit = st.form_submit_button("💾 Save Changes", use_container_width=True)
                                with col_delete:
                                    delete_camp = st.form_submit_button("🗑️ Delete Campaign", use_container_width=True)

                                if save_edit:
                                    updated = {
                                        "id":          camp_id,
                                        "name":        e_name,
                                        "start_date":  e_start_date.strftime("%Y-%m-%d"),
                                        "start_time":  e_start_time.strftime("%H:%M"),
                                        "email_count": int(e_email_count),
                                        "period_days": int(e_period_days),
                                        "offer":       e_offer,
                                        "tone":        e_tone,
                                        "method":      e_method,
                                        "cta":         e_cta,
                                        "show_logo":   e_show_logo,
                                        "status":      e_status,
                                        "created_at":  campaign.get('created_at', ''),
                                        "emails_sent": campaign.get('emails_sent', 0)
                                    }
                                    st.session_state.clients[c_name]['campaigns'][idx] = updated
                                    save_data()
                                    st.success("Campaign updated.")
                                    st.rerun()

                                if delete_camp:
                                    st.session_state.clients[c_name]['campaigns'].pop(idx)
                                    save_data()
                                    st.success("Campaign deleted.")
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
