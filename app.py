import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import unicodedata
import os
import hashlib
from datetime import date, datetime, timedelta
import calendar
from supabase import create_client, Client

# --- PDF Imports ---
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    REPORTLAB_OK = True
except Exception as e:
    REPORTLAB_OK = False

# ==========================================
# 1. SUPABASE VERBINDUNG (Liest Keys automatisch aus dem Server)
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 2. DATENBANK FUNKTIONEN
# ==========================================
def add_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    response = supabase.table("users").insert({"username": clean_username, "password": hashed_pw}).execute()
    return len(response.data) > 0

def verify_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    response = supabase.table("users").select("*").eq("username", clean_username).eq("password", hashed_pw).execute()
    return len(response.data) > 0

def save_settings(username, data_dict):
    supabase.table("settings").upsert({"username": username, "name": data_dict.get('name',''), "pnr": data_dict.get('pnr',''), "wohnort": data_dict.get('wohnort',''), "dienstort": data_dict.get('dienstort',''), "entfernung": data_dict.get('entfernung',0)}).execute()

def load_settings(username):
    response = supabase.table("settings").select("*").eq("username", username).execute()
    return response.data[0] if response.data else {}

def save_fahrzeuge(username, df):
    supabase.table("fahrzeuge").delete().eq("username", username).execute()
    df_insert = df.drop(columns=['id', 'username'], errors='ignore').to_dict('records')
    for row in df_insert: row["username"] = username
    if df_insert: supabase.table("fahrzeuge").insert(df_insert).execute()

def load_fahrzeuge(username):
    response = supabase.table("fahrzeuge").select("*").eq("username", username).order("id").execute()
    if response.data: return pd.DataFrame(response.data)
    return pd.DataFrame(columns=["id", "bezeichnung", "kennzeichen", "start_km_vorjahr", "privat_km_min", "privat_km_max"])

def save_fahrten(username, jahr, monat, df):
    if df.empty: return
    supabase.table("fahrten").delete().eq("username", username).eq("jahr", jahr).eq("monat", monat).execute()
    df_insert = df.to_dict('records')
    for row in df_insert: row["username"] = username; row["jahr"] = jahr; row["monat"] = monat
    if df_insert: supabase.table("fahrten").insert(df_insert).execute()

def load_fahrten(username, jahr, monat):
    response = supabase.table("fahrten").select("datum, fahrzeug_id, fahrzeug, route, km_d, km_p, abf, ank, dauer, abfahrt_km").eq("username", username).eq("jahr", jahr).eq("monat", monat).order("datum").execute()
    return pd.DataFrame(response.data) if response.data else pd.DataFrame()

def berechne_taggeld(dauer_minuten):
    if dauer_minuten < 300: return "11,00"
    if dauer_minuten < 600: return "22,00"
    return "26,40"

# ==========================================
# 3. PDF GENERATOR (Gekürzt fürs Beispiel, dein voller Code kommt hier hin)
# ==========================================
def create_monats_pdf(df, monat, jahr, user_info, fahrzeuge_df):
    # HIER KOMMT SPÄTER DEIN GANZER REPORTLAB CODE AUS MEINER ERSTEN ANTWORT HIN
    # Für den ersten Test erstellen wir nur ein minimales PDF:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    story = [Paragraph(f"Test PDF für {user_info.get('name')} - {calendar.month_name[monat]}", getSampleStyleSheet()['Heading1'])]
    doc.build(story)
    buf.seek(0)
    return buf

# ==========================================
# 4. STREAMLIT APP
# ==========================================
st.set_page_config(page_title="Fahrtenbuch", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.title("🚗 Login")
    tab1, tab2 = st.tabs(["Anmelden", "Registrieren"])
    with tab1:
        u = st.text_input("User"); p = st.text_input("Passwort", type="password")
        if st.button("Login") and verify_user(u, p):
            st.session_state['logged_in'] = True; st.session_state['username'] = u.strip().lower(); st.rerun()
    with tab2:
        nu = st.text_input("Neuer User"); np_ = st.text_input("Neues Passwort", type="password")
        if st.button("Registrieren") and add_user(nu, np_): st.success("Erstellt!")
    st.stop()

user = st.session_state['username']
with st.sidebar:
    st.success(f"Eingeloggt: **{user}**")
    if st.button("Logout"): st.session_state['logged_in'] = False; st.rerun()

user_info = load_settings(user)
fahrzeuge_df = load_fahrzeuge(user)

st.title("Fahrtenbuch v6.0 (Supabase Edition)")
st.subheader("Stammdaten")

c1, c2 = st.columns(2)
with c1:
    user_info['name'] = st.text_input("Name", user_info.get('name', ''))
    user_info['pnr'] = st.text_input("PNR", user_info.get('pnr', ''))
with c2:
    user_info['wohnort'] = st.text_input("Wohnort", user_info.get('wohnort', ''))
    user_info['dienstort'] = st.text_input("Dienstort", user_info.get('dienstort', ''))

user_info['entfernung'] = st.number_input("Entfernung (km)", 0, 300, int(user_info.get('entfernung', 25)))
jahr = st.number_input("Jahr", 2000, 2100, date.today().year)
monat = st.number_input("Monat", 1, 12, date.today().month)

if st.button("💾 Stammdaten speichern"):
    save_settings(user, user_info)
    st.toast("Gespeichert!")

st.markdown("---")
st.subheader("Fahrzeuge")
fahrzeuge_df = st.data_editor(fahrzeuge_df.drop(columns=['username'], errors='ignore'), num_rows="dynamic")
if st.button("💾 Fahrzeuge speichern"):
    save_fahrzeuge(user, fahrzeuge_df)
    st.toast("Fahrzeuge gespeichert!")

st.markdown("---")
st.subheader("Test: Datenbank & PDF")
if st.button("Erstelle Testfahrt und lade PDF"):
    test_df = pd.DataFrame([{"datum": date.today(), "fahrzeug_id": 1, "fahrzeug": "Test Auto", "route": "Testweg", "km_d": 50, "km_p": 10, "abf": "08:00", "ank": "09:00", "dauer": "01:00", "abfahrt_km": 1000}])
    save_fahrten(user, jahr, monat, test_df)
    pdf_bytes = create_monats_pdf(test_df, monat, jahr, user_info, fahrzeuge_df)
    st.download_button("Download Test-PDF", data=pdf_bytes, file_name="test.pdf", mime="application/pdf")
