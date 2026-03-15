import streamlit as st
import pandas as pd
from groq import Groq
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- APP CONFIG ---
st.set_page_config(page_title="Agency AI Outreach", layout="wide")
st.title("🚀 Agency Lead Reactivator")

# --- SIDEBAR: SETTINGS ---
with st.sidebar:
    st.header("🔑 Credentials")
    groq_api_key = st.text_input("Groq API Key", type="password")
    
    st.divider()
    st.header("📧 Email Server (One-Click Send)")
    sender_email = st.text_input("Your Email Address")
    sender_password = st.text_input("App Password (not regular password)", type="password")
    smtp_server = st.selectbox("Server", ["smtp.gmail.com", "smtp.office365.com"])
    
    st.divider()
    st.info("💡 Pro-Tip: Use a Gmail 'App Password' if 2FA is enabled.")

# --- MAIN INTERFACE: INPUTS ---
st.subheader("1. Tell AI about your Company")
company_desc = st.text_area(
    "Description", 
    placeholder="Example: We are a high-end SEO agency specializing in e-commerce stores doing $1M+ revenue."
)

st.subheader("2. Upload your Leads")
uploaded_file = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"])

# --- PROCESSING LOGIC ---
if uploaded_file and groq_api_key:
    # Load Data
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
    
    st.write("### Previewing Leads", df.head(3))
    
    # Ensure correct columns exist
    required_cols = ['Name', 'Email', 'Last_Note']
    if all(col in df.columns for col in required_cols):
        
        # --- BUTTON: GENERATE EMAILS ---
        if st.button("Generate Personalised Drafts"):
            client = Groq(api_key=groq_api_key)
            results = []
            
            progress_bar = st.progress(0)
            for i, row in df.iterrows():
                # The Personalized Prompt Logic
                prompt = f"""
                Company Context: {company_desc}
                Lead Name: {row['Name']}
                Lead History: {row['Last_Note']}
                
                Task: Write a 3-sentence email re-engaging them. 
                Reference their specific history naturally. 
                Keep it professional but concise.
                """
                
                try:
                    chat = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": prompt}]
                    )
                    results.append(chat.choices[0].message.content)
                except Exception as e:
                    results.append(f"Error: {e}")
                
                progress_bar.progress((i + 1) / len(df))
            
            df['Generated_Email'] = results
            st.session_state['df'] = df  # Store in memory
            st.success("Drafts Complete!")
            st.write(df[['Name', 'Email', 'Generated_Email']])

        # --- BUTTON: SEND EMAILS ---
        if 'df' in st.session_state and st.button("🚀 Send All Emails Now"):
            if not sender_email or not sender_password:
                st.error("Please provide email credentials in the sidebar.")
            else:
                success_count = 0
                for i, row in st.session_state['df'].iterrows():
                    try:
                        # Setup SMTP
                        msg = MIMEMultipart()
                        msg['From'] = sender_email
                        msg['To'] = row['Email']
                        msg['Subject'] = f"Quick question for {row['Name']}"
                        msg.attach(MIMEText(row['Generated_Email'], 'plain'))

                        with smtplib.SMTP(smtp_server, 587) as server:
                            server.starttls()
                            server.login(sender_email, sender_password)
                            server.send_message(msg)
                        success_count += 1
                    except Exception as e:
                        st.error(f"Failed to send to {row['Name']}: {e}")
                
                st.balloons()
                st.success(f"Successfully sent {success_count} emails!")

    else:
        st.warning(f"Spreadsheet must have these columns: {required_cols}")