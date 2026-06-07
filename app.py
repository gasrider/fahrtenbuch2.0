import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
import calendar
import os
import hashlib
from supabase import create_client, Client

# ---- PDF deps ----
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, KeepTogether, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        pdfmetrics.registerFont(TTFont('Helvetica', 'arial.ttf'))
    except:
        pass
    REPORTLAB_OK = True
except Exception as e:
    st.error(f"ReportLab konnte nicht geladen werden: {e}")
    REPORTLAB_OK = False

# ==========================================
# 1. SUPABASE DATENBANK-SCHICHT
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def add_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    try:
        supabase.table("users").insert({"username": clean_username, "password": hashed_pw}).execute()
        return True
    except: return False

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

def save_zeitraeume(username, df):
    supabase.table("zeitraeume").delete().eq("username", username).execute()
    df_save = df[["fahrzeug_id", "von", "bis"]].copy()
    df_save["username"] = username
    df_insert = df_save.to_dict('records')
    if df_insert: supabase.table("zeitraeume").insert(df_insert).execute()

def load_zeitraeume(username):
    response = supabase.table("zeitraeume").select("fahrzeug_id, von, bis").eq("username", username).execute()
    if response.data: return pd.DataFrame(response.data)
    return pd.DataFrame(columns=["fahrzeug_id", "von", "bis"])

def save_fahrten(username, jahr, monat, df):
    if df.empty: return
    supabase.table("fahrten").delete().eq("username", username).eq("jahr", jahr).eq("monat", monat).execute()
    df_insert = df.to_dict('records')
    for row in df_insert: row["username"] = username; row["jahr"] = jahr; row["monat"] = monat
    if df_insert: supabase.table("fahrten").insert(df_insert).execute()

def load_fahrten(username, jahr, monat):
    response = supabase.table("fahrten").select("datum, fahrzeug_id, fahrzeug, route, km_d, km_p, abf, ank, dauer, abfahrt_km").eq("username", username).eq("jahr", jahr).eq("monat", monat).order("datum").execute()
    return pd.DataFrame(response.data) if response.data else pd.DataFrame()

# ==========================================
# 2. HELPERS (Aus deinem alten Code)
# ==========================================
def normalize_col(s: str) -> str:
    if s is None: return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = re.sub(r"[^\w]+", "_", s)
    return s.strip("_")

def normalize_df_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_col(c) for c in df.columns]
    return df

def coalesce(df: pd.DataFrame, candidates, to_name):
    for cand in candidates:
        if cand in df.columns: return df.rename(columns={cand: to_name})
    for c in df.columns:
        for cand in candidates:
            if cand in c: return df.rename(columns={c: to_name})
    return df

def easter_sunday(year):
    a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def austria_holidays(year):
    E = easter_sunday(year)
    return {date(year, 1, 1), date(year, 1, 6), date(year, 5, 1), date(year, 8, 15), date(year, 10, 26), date(year, 11, 1), date(year, 12, 8), date(year, 12, 25), date(year, 12, 26), E + timedelta(days=1), E + timedelta(days=39), E + timedelta(days=50), E + timedelta(days=60)}

def berechne_taggeld(dauer_minuten):
    if dauer_minuten < 5 * 60: return "11,00"
    if dauer_minuten < 10 * 60: return "22,00"
    return "26,40"

def extrahiere_ort(vollstaendige_adresse):
    if not vollstaendige_adresse: return "Unbekannt"
    part = vollstaendige_adresse.split(',')[-1].strip() if ',' in vollstaendige_adresse else vollstaendige_adresse.strip()
    part_cleaned = re.sub(r'^[A-Za-z]?-?\d{4,5}\s+', '', part)
    return part_cleaned if part_cleaned else part

# ==========================================
# 3. PDF GENERATOREN (Dein Original-Layout!)
# ==========================================
def create_monats_pdf(df, monat, jahr, user_info, fahrzeuge_df):
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

def create_jahres_pdf(generated_data, jahr, user_info, fahrzeuge_df):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12*mm, rightMargin=12*mm, topMargin=12*mm, bottomMargin=10*mm)
    story = []
    styles = getSampleStyleSheet()
    monate = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

    story.append(Paragraph(f"Fahrtenbuch Jahresübersicht {jahr}", styles['Heading2']))
    story.append(Spacer(1, 8*mm))

    fzg_list = [f"{r.get('bezeichnung','')} ({r.get('kennzeichen','')})" for _, r in fahrzeuge_df.iterrows()]
    story.append(Paragraph(f"Name: {user_info.get('name','')}", styles['Normal']))
    story.append(Paragraph(f"PNR: {user_info.get('pnr','')}", styles['Normal']))
    story.append(Paragraph(f"Wohnort: {user_info.get('wohnort','')}", styles['Normal']))
    story.append(Paragraph(f"Dienstort: {user_info.get('dienstort','')}", styles['Normal']))
    story.append(Paragraph(f"Entfernung zwischen Arbeitsplatz und Wohnung: {int(user_info.get('entfernung',0) or 0)} km", styles['Normal']))
    story.append(Paragraph(f"Fahrzeug(e): {', '.join(fzg_list)}", styles['Normal']))
    story.append(Spacer(1, 10*mm))

    headers = ["Monat", "gefahrene km", "km-Geld", "amtlich.", "Taggeld"]
    sub_headers = ["", "dienstl.", "privat", "PKW", "EUR", "EUR"]
    data = [headers, sub_headers]
    km_geld_satz = 0.42
    total_dienstl = total_privat = total_km_geld = total_taggeld = 0

    for i, monat_name in enumerate(monate):
        monat_key = (jahr, i + 1)
        if monat_key in generated_data:
            df = generated_data[monat_key]
            sum_dienstl = int(df["km_d"].sum())
            sum_privat = int(df["km_p"].sum())
            sum_taggeld = sum(float(berechne_taggeld(int(r["dauer"].split(':')[0])*60 + int(r["dauer"].split(':')[1])).replace(',', '.')) for _, r in df.iterrows())
            total_dienstl += sum_dienstl; total_privat += sum_privat
            total_km_geld += (sum_dienstl + sum_privat) * km_geld_satz; total_taggeld += sum_taggeld
            data.append([monat_name, sum_dienstl, sum_privat, f"{(sum_dienstl + sum_privat) * km_geld_satz:.2f}".replace('.', ','), f"{sum_taggeld:.2f}".replace('.', ',')])
    
    data.append(["Summen", total_dienstl, total_privat, f"{total_km_geld:.2f}".replace('.', ','), f"{total_taggeld:.2f}".replace('.', ',')])
    
    col_widths = [25*mm, 20*mm, 20*mm, 25*mm, 25*mm, 25*mm]
    table = Table(data, colWidths=col_widths, repeatRows=2)
    style = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 1), 9), ("FONTSIZE", (0, 2), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 1), colors.whitesmoke),
        ("SPAN", (2, 0), (4, 0)), ("SPAN", (3, 1), (4, 1)), ("LINEBELOW", (0, -1), (-1, -1), 1.5, colors.black),
    ])
    table.setStyle(style)
    story.append(table)
    story.append(Spacer(1, 20*mm))

    small_style = ParagraphStyle('small', parent=styles['Normal'], fontName='Helvetica', fontSize=7)
    notes = [
        f"km-Geld Satz PKW amtlich ab 01.01.2023 EUR {km_geld_satz:.2f}",
        "km-Geld Satz PKW dienstlich ab 01.01.2023 EUR 0.00",
        f"km-Geld dienstlich für {total_dienstl}km: EUR 0,00",
        f"km-Geld amtlich für {total_dienstl + total_privat}km inkl. Mitfahrer: EUR {total_km_geld:.2f}".replace('.', ','),
        f"Taggeld amtlich: EUR {total_taggeld:.2f}".replace(','),
        f"Taggeld + km-Geld amtlich: EUR {(total_km_geld + total_taggeld):.2f}".replace('.', ','),
        "Vom Dienstgeber vergütete Reisekosten: EUR 0,00",
        "Für die Arbeitnehmerveranlagung zu berücksichtigen: EUR ......................",
        "Die angegebenen Daten beruhen auf persönlichen Aufzeichnungen der oben genannten Person. UNIQA übernimmt keine Haftung für die Richtigkeit der Angaben."
    ]
    for note in notes:
        story.append(Paragraph(note, small_style))
        story.append(Spacer(1, 2*mm))

    doc.build(story)
    buf.seek(0)
    return buf

# ==========================================
# 4. STREAMLIT APP & LOGIK
# ==========================================
st.set_page_config(page_title="Fahrtenbuch Generator v6.0 - Multi-User Edition", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.title("🚗 Fahrtenbuch Login")
    tab1, tab2 = st.tabs(["Anmelden", "Registrieren"])
    with tab1:
        u = st.text_input("Benutzername"); p = st.text_input("Passwort", type="password")
        if st.button("Login", type="primary"):
            if verify_user(u, p):
                st.session_state['logged_in'] = True; st.session_state['username'] = u.strip().lower(); st.rerun()
            else: st.error("Falsche Zugangsdaten")
    with tab2:
        nu = st.text_input("Neuer Benutzername", key="reg_user"); np_ = st.text_input("Neues Passwort", type="password", key="reg_pw")
        if st.button("Account erstellen"):
            try:
                if add_user(nu, np_): st.success("Account erstellt! Bitte loggen Sie sich ein.")
                else: st.error("Benutzername existiert bereits.")
            except Exception as e: st.error(str(e))
    st.stop()

# --- AB HIER EINGELOGGT ---
user = st.session_state['username']
with st.sidebar:
    st.success(f"Eingeloggt als: **{user}**")
    if st.button("Logout"): st.session_state['logged_in'] = False; st.rerun()

# Daten aus Supabase laden
user_info = load_settings(user)
fahrzeuge_df = load_fahrzeuge(user)
zeitraeume_df = load_zeitraeume(user)

# Defaults setzen
if not user_info: user_info = {'name': '', 'pnr': '', 'wohnort': '', 'dienstort': '', 'entfernung': 25}
if fahrzeuge_df.empty:
    fahrzeuge_df = pd.DataFrame([{"id": 1, "bezeichnung": "VW T6.1", "kennzeichen": "VB-900AN", "start_km_vorjahr": 55283, "privat_km_min": 10, "privat_km_max": 40}])
if zeitraeume_df.empty:
    zeitraeume_df = pd.DataFrame([{"fahrzeug_id": 1, "von": date(date.today().year, 1, 1), "bis": date(date.today().year, 12, 31)}])

st.title("Fahrtenbuch Generator v6.0 - Multi-User Edition")

# ========= Stammdaten =========
with st.sidebar:
    st.header("📋 Stammdaten")
    user_info['name'] = st.text_input("Name", user_info.get('name', ''))
    user_info['pnr'] = st.text_input("PNR", user_info.get('pnr', ''))
    user_info['wohnort'] = st.text_input("Wohnort", user_info.get('wohnort', ''))
    user_info['dienstort'] = st.text_input("Dienstort", user_info.get('dienstort', ''))
    user_info['entfernung'] = st.number_input("Entfernung Wohnung ↔ Arbeitsplatz (km)", 0, 300, int(user_info.get('entfernung', 25)))
    jahr = st.number_input("Jahr", min_value=2000, max_value=2100, value=date.today().year, step=1)

# ========= Generierungs Einstellungen (Dein kompletter UI Block) =========
st.markdown("---")
st.subheader("🚗 Eckdaten & Keywords für die Generierung")

keyword_text = st.text_area(
    "Geben Sie hier Ihre Orte und Zwecke ein (Format: `Ort:Zweck1,Zweck2,...`).",
    value="Thalgau:Büro\nOberhofen am Irrsee:KB\nStraßwalchen:Schaden,Angebot"
)
if keyword_text:
    kw_list = []
    for line in keyword_text.strip().split('\n'):
        parts = line.split(':')
        if len(parts) == 2:
            ort = parts[0].strip()
            zwecke = [z.strip() for z in parts[1].split(',')]
            for zweck in zwecke: kw_list.append({"Ort": ort, "Zweck": zweck})
    keywords = pd.DataFrame(kw_list)
else:
    keywords = pd.DataFrame(columns=["Ort", "Zweck"])

colA, colB, colC, colD = st.columns(4)
with colA:
    modus = st.radio("Generierungs-Modus", ["Einzelner Monat", "Ganzes Jahr"], key="modus_auswahl")
    monat_name = st.selectbox("Monat für Generierung", ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"], index=date.today().month - 1, disabled=(modus == "Ganzes Jahr"))
    name2num = {n: i + 1 for i, n in enumerate(["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"])}
    monat = name2num[monat_name]
with colB:
    durchschnittliche_fahrten_pro_woche = st.slider("Ø Fahrten pro Woche", 1, 10, 4)
with colC:
    anteil_privat_strecke = st.slider("Ø Privat-Km an Feiertagen/Sonntagen", 10, 500, 50)
with colD:
    wahrscheinlichkeit_dienstfahrt_werktag = st.slider("Wahrscheinlichkeit Dienstfahrt (Werktag %)", 0, 100, value=75)

colKM1, colKM2 = st.columns(2)
with colKM1:
    target_km_min = st.number_input("Ø Dienst-KM pro Monat (Minimum)", min_value=0, max_value=5000, value=1650, step=50)
with colKM2:
    target_km_max = st.number_input("Ø Dienst-KM pro Monat (Maximum)", min_value=0, max_value=5000, value=2000, step=50)

# ========= Editors =========
st.markdown("---")
st.subheader("📝 Fahrzeuge & Zeiträume (editierbar)")
colF1, colF2 = st.columns(2)
with colF1:
    fahrzeuge_df = st.data_editor(fahrzeuge_df, num_rows="dynamic", key="fahrzeuge_editor", use_container_width=True)
with colF2:
    zeitraeume_df = st.data_editor(zeitraeume_df, num_rows="dynamic", key="zeiten_editor", use_container_width=True)

# Speichern Buttons
if st.button("💾 Alle Einstellungen & Fahrzeuge speichern", type="primary"):
    save_settings(user, user_info)
    save_fahrzeuge(user, fahrzeuge_df)
    save_zeitraeume(user, zeitraeume_df)
    st.toast("Alles in der Cloud gespeichert!")

# ========= Generator Logik (Dein kompletter Code!) =========
wohnort_clean = extrahiere_ort(user_info.get('wohnort', ''))
dienstort_clean = extrahiere_ort(user_info.get('dienstort', ''))

if 'generated_months_data' not in st.session_state:
    st.session_state["generated_months_data"] = {}

ready = (not fahrzeuge_df.empty and not zeitraeume_df.empty and not keywords.empty)
colG1, colG2 = st.columns([1, 1])
gen_btn_text = f"🚀 Fahrten für {'Ganzes Jahr' if modus == 'Ganzes Jahr' else monat_name} {jahr} generieren"
gen_btn = colG1.button(gen_btn_text, type="primary", disabled=not ready)
clear_btn = colG2.button("🗑️ Alle generierten Daten löschen")

if clear_btn:
    st.session_state["generated_months_data"] = {}
    st.rerun()

if gen_btn:
    monate_zum_generieren = list(range(1, 13)) if modus == "Ganzes Jahr" else [monat]
    progress_bar = st.progress(0, text="Generiere Fahrten...")
    current_km = {row['id']: row['start_km_vorjahr'] for _, row in fahrzeuge_df.iterrows()}
    privat_km_ranges = {row['id']: (int(row['privat_km_min']), int(row['privat_km_max'])) for _, row in fahrzeuge_df.iterrows()}
    
    for i, monat_key in enumerate(monate_zum_generieren):
        progress_bar.progress((i + 1) / len(monate_zum_generieren), text=f"Generiere Monat {monat_key}...")
        tage = pd.date_range(date(jahr, monat_key, 1), date(jahr, monat_key, calendar.monthrange(jahr, monat_key)[1]), freq="D")
        out = []
        hol = austria_holidays(jahr)
        rng = np.random.default_rng()
        
        for t in tage:
            route = "Keine Fahrt"; km_d = 0; km_p = 0; abf = "00:00"; ank = "00:00"; dauer = "00:00"; fahrzeug_id = None; fahrzeug_name = "Kein Fahrzeug"
            tag_ts = pd.Timestamp(t.date())
            gueltige_fz = pd.to_numeric(zeitraeume_df[(zeitraeume_df["von"] <= tag_ts) & (zeitraeume_df["bis"] >= tag_ts)]["fahrzeug_id"], errors="coerce").dropna().astype(int).tolist()
            
            if gueltige_fz:
                fahrzeug_id = rng.choice(gueltige_fz)
                fahrzeug_name = fahrzeuge_df[fahrzeuge_df["id"] == fahrzeug_id]["bezeichnung"].values[0]

            is_sunday = t.weekday() == 6
            is_saturday = t.weekday() == 5
            is_holiday = t.date() in hol

            if is_holiday or is_sunday or is_saturday:
                if fahrzeug_id in privat_km_ranges: km_p = rng.integers(privat_km_ranges[fahrzeug_id][0], privat_km_ranges[fahrzeug_id][1])
                else: km_p = rng.integers(5, 21)
                route = "Feiertag/Wochenende"
                start_hour = int(rng.integers(9, 18)); fahrzeit_min = int(rng.integers(15, 45))
                abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=start_hour)
                ank_dt = abf_dt + timedelta(minutes=fahrzeit_min)
                abf, ank, dauer = abf_dt.strftime("%H:%M"), ank_dt.strftime("%H:%M"), f"{fahrzeit_min // 60:02d}:{fahrzeit_min % 60:02d}"
            else:
                if rng.random() < (wahrscheinlichkeit_dienstfahrt_werktag / 100.0):
                    num_stops = rng.integers(1, 4)
                    selected_kw = keywords.sample(min(num_stops, len(keywords)))
                    route_stops = [f"{r['Ort']} ({r['Zweck']})" for _, r in selected_kw.iterrows()]
                    full_route = [wohnort_clean] + route_stops + [wohnort_clean]
                    route = " - ".join(full_route)
                    km_d = sum(rng.integers(15, 35) for _ in range(num_stops))
                    fahrzeit_min = int(km_d / 80 * 60); pause_min = int(rng.integers(20, 60)); dauer_min = fahrzeit_min + pause_min
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=8)
                    ank_dt = abf_dt + timedelta(minutes=int(dauer_min))
                    abf, ank, dauer = abf_dt.strftime("%H:%M"), ank_dt.strftime("%H:%M"), f"{dauer_min // 60:02d}:{dauer_min % 60:02d}"

            abfahrt_km = current_km.get(fahrzeug_id, 0) if fahrzeug_id is not None else 0
            out.append({"datum": t.date(), "fahrzeug_id": fahrzeug_id, "fahrzeug": fahrzeug_name, "route": route, "km_d": km_d, "km_p": km_p, "abf": abf, "ank": ank, "dauer": dauer, "abfahrt_km": abfahrt_km})
            if fahrzeug_id is not None and (km_d > 0 or km_p > 0): current_km[fahrzeug_id] += km_d + km_p

        df = pd.DataFrame(out).sort_values(["datum"]).reset_index(drop=True)
        if not df.empty and target_km_max > 0:
            current_km_d_total = df["km_d"].sum()
            if not (target_km_min <= current_km_d_total <= target_km_max):
                target_km = (target_km_min + target_km_max) / 2
                scaling_factor = target_km / current_km_d_total
                df['km_d'] = df.apply(lambda row: int(row['km_d'] * scaling_factor) if row['km_d'] > 0 else 0, axis=1)
                month_start_km = {fz_id: km - df[df['fahrzeug_id'] == fz_id][['km_d', 'km_p']].sum().sum() for fz_id, km in current_km.items()}
                corrected_rows = []
                for index, row in df.iterrows():
                    fz_id = row['fahrzeug_id']
                    if fz_id is not None:
                        corrected_row = row.to_dict(); corrected_row['abfahrt_km'] = month_start_km.get(fz_id, 0)
                        corrected_rows.append(corrected_row); month_start_km[fz_id] += row['km_d'] + row['km_p']
                    else: corrected_rows.append(row.to_dict())
                df = pd.DataFrame(corrected_rows)
                for fz_id, km in month_start_km.items(): current_km[fz_id] = km

        st.session_state["generated_months_data"][(jahr, monat_key)] = df
        # WICHTIG: In Supabase speichern!
        save_fahrten(user, jahr, monat_key, df)

    progress_bar.empty()
    st.success(f"Fahrten für {len(monate_zum_generieren)} Monat(e) generiert und in der Cloud gespeichert!")

# ========= Anzeige & PDF =========
last_monat_key = (jahr, monat)
df = st.session_state["generated_months_data"].get(last_monat_key)

if df is not None and not df.empty:
    st.dataframe(df, use_container_width=True)
    st.subheader("📄 PDF-Export")
    
    available_months = sorted([key for key in st.session_state["generated_months_data"].keys() if not st.session_state["generated_months_data"][key].empty])
    if available_months:
        monate_namen = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        month_names_for_select = {f"{monate_namen[m - 1]} {y}": (y, m) for y, m in available_months}
        selected_month_str = st.selectbox("Monat für PDF-Export auswählen:", list(month_names_for_select.keys()))
        pdf_jahr, pdf_monat = month_names_for_select[selected_month_str]
        
        colP1, colP2 = st.columns(2)
        with colP1:
            if st.button(f"📄 Monats-PDF erstellen"):
                pdf_buffer = create_monats_pdf(st.session_state["generated_months_data"][(pdf_jahr, pdf_monat)], pdf_monat, pdf_jahr, user_info, fahrzeuge_df)
                st.download_button(label=f"Download PDF {monate_namen[pdf_monat-1]} {pdf_jahr}", data=pdf_buffer, file_name=f"Fahrtenbuch_Monatsuebersicht_{monate_namen[pdf_monat-1]}_{pdf_jahr}.pdf", mime="application/pdf")
        with colP2:
            if st.button("📊 Jahresbericht-PDF erstellen"):
                pdf_buffer = create_jahres_pdf(st.session_state["generated_months_data"], jahr, user_info, fahrzeuge_df)
                st.download_button(label=f"Download Jahresbericht {jahr}", data=pdf_buffer, file_name=f"Fahrtenbuch_Jahresuebersicht_{jahr}.pdf", mime="application/pdf")
