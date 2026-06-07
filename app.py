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
# 1. SUPABASE VERBINDUNG
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 2. DATENBANK FUNKTIONEN (MIT FEHLER-ÜBERSETZER)
# ==========================================
def add_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    try:
        response = supabase.table("users").insert({"username": clean_username, "password": hashed_pw}).execute()
        return len(response.data) > 0
    except Exception as e:
        # Zwingt die App, den ECHTEN Datenbankfehler zu zeigen
        fehler_details = getattr(e, 'message', str(e))
        raise Exception(f"Supabase sagt NEIN: {fehler_details}")

def verify_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    response = supabase.table("users").select("*").eq("username", clean_username).eq("password", hashed_pw).execute()
    return len(response.data) > 0

def save_settings(username, data_dict):
    try:
        supabase.table("settings").upsert({"username": username, "name": data_dict.get('name',''), "pnr": data_dict.get('pnr',''), "wohnort": data_dict.get('wohnort',''), "dienstort": data_dict.get('dienstort',''), "entfernung": data_dict.get('entfernung',0)}).execute()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Stammdaten: {getattr(e, 'message', str(e))}")

def load_settings(username):
    response = supabase.table("settings").select("*").eq("username", username).execute()
    return response.data[0] if response.data else {}

def save_fahrzeuge(username, df):
    try:
        supabase.table("fahrzeuge").delete().eq("username", username).execute()
        df_insert = df.drop(columns=['id', 'username'], errors='ignore').to_dict('records')
        for row in df_insert: row["username"] = username
        if df_insert: supabase.table("fahrzeuge").insert(df_insert).execute()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Fahrzeuge: {getattr(e, 'message', str(e))}")

def load_fahrzeuge(username):
    response = supabase.table("fahrzeuge").select("*").eq("username", username).order("id").execute()
    if response.data: return pd.DataFrame(response.data)
    return pd.DataFrame(columns=["id", "bezeichnung", "kennzeichen", "start_km_vorjahr", "privat_km_min", "privat_km_max"])

def save_fahrten(username, jahr, monat, df):
    if df.empty: return
    try:
        supabase.table("fahrten").delete().eq("username", username).eq("jahr", jahr).eq("monat", monat).execute()
        df_insert = df.to_dict('records')
        for row in df_insert: row["username"] = username; row["jahr"] = jahr; row["monat"] = monat
        if df_insert: supabase.table("fahrten").insert(df_insert).execute()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Fahrten: {getattr(e, 'message', str(e))}")

def load_fahrten(username, jahr, monat):
    response = supabase.table("fahrten").select("datum, fahrzeug_id, fahrzeug, route, km_d, km_p, abf, ank, dauer, abfahrt_km").eq("username", username).eq("jahr", jahr).eq("monat", monat).order("datum").execute()
    return pd.DataFrame(response.data) if response.data else pd.DataFrame()

def berechne_taggeld(dauer_minuten):
    if dauer_minuten < 300: return "11,00"
    if dauer_minuten < 600: return "22,00"
    return "26,40"

# ==========================================
# 3. ECHTER PDF GENERATOR
# ==========================================
def create_monats_pdf(df, monat, jahr, user_info, fahrzeuge_df):
    """Erstellt ein PDF, das exakt das Layout der Vorlage nachbildet."""
    monate = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    monat_name = monate[monat - 1]
    
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12*mm, rightMargin=12*mm, topMargin=12*mm, bottomMargin=25*mm)
    story = []
    styles = getSampleStyleSheet()

    def footer(canvas, doc):
        canvas.saveState()
        page_num = canvas.getPageNumber()
        footer_text = f"Erstellt von:{user_info.get('username', '')} Seite{page_num}von2 gedruckt:{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(A4[0] - 12*mm, 10*mm, footer_text)
        canvas.restoreState()

    titel_para = Paragraph("Fahrtenbuch Monatsübersicht", styles['Heading2'])
    datum_para = Paragraph(f"{monat_name} {jahr}", styles['Heading2'])
    title_table = Table([[titel_para, datum_para]], colWidths=[doc.width/2, doc.width/2])
    title_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 16)]))
    story.append(title_table)
    story.append(Spacer(1, 8*mm))

    fzg_list = [f"{i+1}-{r.get('bezeichnung','')} ({r.get('kennzeichen','')})" for i, r in fahrzeuge_df.iterrows()]
    fzg_string = "  |  ".join(fzg_list)
    
    stamm_data = [
        [Paragraph(f"Name: {user_info.get('name','')}", styles['Normal'])],
        [Paragraph(f"PNR: {user_info.get('pnr','')}", styles['Normal'])],
        [Paragraph(f"Wohnort: {user_info.get('wohnort','')}", styles['Normal'])],
        [Paragraph(f"Dienstort: {user_info.get('dienstort','')}", styles['Normal'])],
        [Paragraph(f"Entfernung zwischen Arbeitsplatz und Wohnung: {int(user_info.get('entfernung',0) or 0)} km", styles['Normal'])],
        [Paragraph("Fahrzeug(e): " + fzg_string, styles['Normal'])],
    ]
    stamm_table = Table(stamm_data, colWidths=[doc.width])
    stamm_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story.append(stamm_table)
    story.append(Spacer(1, 6*mm))

    story.append(Table([['']], colWidths=[doc.width]))
    story[-1].setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, 0), 1, colors.black)]))
    story.append(Spacer(1, 4*mm))

    wrap_style = ParagraphStyle('wrap', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=9.6)
    headers = ["Tag", "Abf.", "Ank.", "Dauer", "Reiseweg - Ziel - Zweck", "Abfahrt", "gefahrene km", "amtlich.", "Taggeld", "KFZ"]
    sub_headers = ["", "", "", "", "", "", "dienstl.", "privat", "", ""]
    data = [headers, sub_headers]
    
    for _, r in df.iterrows():
        dt = pd.to_datetime(r["datum"])
        german_day_abbr = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        tag_name = german_day_abbr[dt.weekday()]
        tag = f"{tag_name[:2]}.{dt.day:02d}."
        route_para = Paragraph(str(r["route"]), wrap_style)
        try:
            dauer_parts = r["dauer"].split(':')
            dauer_min = int(dauer_parts[0]) * 60 + int(dauer_parts[1])
        except: dauer_min = 0
            
        data.append([tag, r["abf"], r["ank"], r["dauer"], route_para, int(r["abfahrt_km"]), int(r["km_d"]), int(r["km_p"]), berechne_taggeld(dauer_min), r["fahrzeug"]])
    
    sum_dienstl = int(df["km_d"].sum())
    sum_privat = int(df["km_p"].sum())
    sum_taggeld = sum(float(berechne_taggeld(int(r["dauer"].split(':')[0])*60 + int(r["dauer"].split(':')[1])).replace(',', '.')) for _, r in df.iterrows())

    data.append(["Einzelsummen:", "", "", "", "", "", sum_dienstl, sum_privat, f"{sum_taggeld:.2f}".replace('.', ','), ""])
    
    col_widths = [12*mm, 10*mm, 10*mm, 12*mm, 71*mm, 15*mm, 15*mm, 15*mm, 12*mm, 18*mm]
    table = Table(data, colWidths=col_widths, repeatRows=2)
    
    style = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 1), 9), ("FONTSIZE", (0, 2), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("ALIGN", (4, 2), (4, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 1), colors.whitesmoke), ("SPAN", (6, 0), (7, 0)),
        ("SPAN", (0, -1), (4, -1)), ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
    ])
    table.setStyle(style)
    story.append(table)
    story.append(Spacer(1, 5*mm))
    
    notes = ["Privat-KM beinhalten die Fahrtstrecke Wohnung-Arbeitsplatz.", "Monatliches KM-Maximum dienstlich ab 01.01.2023 0,00 km", "km-Geld Satz PKW amtlich ab 01.01.2023 EUR 0,42", "km-Geld Satz PKW dienstlich ab 01.01.2023 EUR 0,00"]
    for note in notes:
        story.append(Paragraph(note, styles['Normal']))
        story.append(Spacer(1, 3*mm))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf
# ==========================================
# 4. STREAMLIT APP & LOGIN
# ==========================================
st.set_page_config(page_title="Fahrtenbuch", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.title("🚗 Login")
    tab1, tab2 = st.tabs(["Anmelden", "Registrieren"])
    
    with tab1:
        u = st.text_input("User")
        p = st.text_input("Passwort", type="password")
        if st.button("Login"):
            if verify_user(u, p):
                st.session_state['logged_in'] = True
                st.session_state['username'] = u.strip().lower()
                st.rerun()
            else:
                st.error("Falsche Zugangsdaten")
                
    with tab2:
        nu = st.text_input("Neuer User")
        np_ = st.text_input("Neues Passwort", type="password")
        if st.button("Registrieren"):
            try:
                if add_user(nu, np_):
                    st.success("User erstellt! Du kannst dich jetzt einloggen.")
            except Exception as e:
                st.error(str(e))
                
    st.stop()

# --- AB HIER EINGELOGGT ---
user = st.session_state['username']

with st.sidebar:
    st.success(f"Eingeloggt: **{user}**")
    if st.button("Logout"): 
        st.session_state['logged_in'] = False
        st.rerun()

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
