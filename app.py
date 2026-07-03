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
import secrets
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from supabase import create_client, Client

# ---- PDF deps ----
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

def send_reset_email(to_email, new_plain_password, smtp_settings=None):
    """Sendet eine Passwort-Reset-E-Mail. Lädt SMTP-Einstellungen aus DB (Fallback: env vars)."""
    try:
        if smtp_settings is None:
            smtp_settings = get_smtp_settings()

        smtp_server = smtp_settings.get("server", "")
        smtp_port = smtp_settings.get("port", "465")
        smtp_user = smtp_settings.get("user", "")
        smtp_password = smtp_settings.get("password", "")
        from_name = smtp_settings.get("from_name", "Fahrtenbuch System")

        if not all([smtp_server, smtp_port, smtp_user, smtp_password]):
            print("Fehler: SMTP-Einstellungen nicht vollständig konfiguriert (weder in DB noch als Umgebungsvariable).")
            return False

        msg = EmailMessage()
        msg['From'] = formataddr((from_name, smtp_user))
        msg['To'] = to_email
        msg['Subject'] = "Ihr neues Passwort für das Fahrtenbuch"
        msg['Reply-To'] = smtp_user

        # Message-ID mit korrekter Domain
        domain = smtp_user.split('@')[1] if '@' in smtp_user else 'localhost'
        msg['Message-ID'] = make_msgid(domain=domain)

        # ANTI-SPAM: X-Priority entfernt! Dieser Header triggert Spam-Filter.
        # Kein HTML, nur Plain-Text – reduces Spam-Score deutlich.

        text = f"""Hallo,

Sie haben ein neues Passwort fuer das Fahrtenbuch-System angefordert.

Ihr neues Passwort lautet: {new_plain_password}

Bitte loggen Sie sich ein und aendern Sie dieses Passwort sofort nach dem ersten Login.

Viele Gruesse
{from_name}"""

        msg.set_content(text, subtype='plain', charset='utf-8')

        # Port 465 = SSL, sonst STARTTLS
        if int(smtp_port) == 465:
            with smtplib.SMTP_SSL(smtp_server, int(smtp_port), timeout=15) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, int(smtp_port), timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

        return True

    except smtplib.SMTPAuthenticationError:
        print("SMTP Login fehlgeschlagen! Pruefen Sie Benutzername und App-Passwort.")
        return False
    except Exception as e:
        print(f"Fehler beim E-Mail Versand: {e}")
        return False

# ==========================================
# 1. SUPABASE DATENBANK-SCHICHT (KORRIGIERT)
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("❌ FEHLER: Umgebungsvariablen SUPABASE_URL und SUPABASE_KEY müssen gesetzt sein!")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def add_user(username, password, email):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    try: 
        # KORREKTUR: force_pw_change auf True gesetzt bei Registrierung
        supabase.table("users").insert({"username": clean_username, "password": hashed_pw, "email": email.strip().lower(), "force_pw_change": True}).execute()
        return True
    except Exception as e:
        print(f"Fehler bei Registrierung: {e}")
        return False

def verify_user(username, password):
    clean_username = username.strip().lower()
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    response = supabase.table("users").select("username, password, email, force_pw_change").eq("username", clean_username).eq("password", hashed_pw).execute()
    if len(response.data) > 0:
        return True, response.data[0]
    return False, {}

def save_settings(username, data_dict):
    supabase.table("settings").upsert({
        "username": username, 
        "name": data_dict.get('name',''), 
        "pnr": data_dict.get('pnr',''), 
        "wohnort": data_dict.get('wohnort',''), 
        "dienstort": data_dict.get('dienstort',''), 
        "entfernung": data_dict.get('entfernung', 0),
        "taggeld_min_stunden": data_dict.get('taggeld_min_stunden', 5),
        "taggeld_basis": data_dict.get('taggeld_basis', 10.00),
        "taggeld_stufung": data_dict.get('taggeld_stufung', 2.50),
        "taggeld_max_betrag": data_dict.get('taggeld_max_betrag', 30.00),
        "taggeld_max_stunden": data_dict.get('taggeld_max_stunden', 13),
        "km_geld": data_dict.get('km_geld', 0.42)
    }).execute()

def load_settings(username):
    response = supabase.table("settings").select("*").eq("username", username).execute()
    return response.data[0] if response.data else {}

def _safe_int(val, default=0):
    """Wandelt einen Wert sicher in int um. None, NaN, 'None', '' → default."""
    if val is None:
        return default
    if isinstance(val, (int, float)) and pd.notna(val):
        return int(val)
    s = str(val).strip().replace('.', '').replace(',', '')
    if s in ("", "none", "nan", "nat"):
        return default
    try:
        return int(s)
    except (ValueError, TypeError):
        return default

def _parse_date_iso(val):
    """Konvertiert einen Datumswert sicher in ISO-Format YYYY-MM-DD.
    Akzeptiert: date, datetime, pd.Timestamp, 'YYYY-MM-DD', 'DD.MM.YYYY'"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    if hasattr(val, 'date') and callable(val.date):
        return val.date().strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s.lower() in ("nat", "nan", "none", ""):
        return None
    # Versuch 1: ISO-Format YYYY-MM-DD (oder längerer Timestamp)
    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
        return s[:10]
    # Versuch 2: Europäisches Format DD.MM.YYYY
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', s)
    if m:
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None

def save_fahrzeuge(username, df):
    try:
        supabase.table("fahrzeuge").delete().eq("username", username).execute()
        df = df.dropna(how='all')
        df_insert = df.drop(columns=['username'], errors='ignore').to_dict('records')
        clean_insert = []
        for row in df_insert:
            clean_row = {
                "id": _safe_int(row.get("id"), default=None) if pd.notna(row.get("id")) and str(row.get("id")).strip() not in ("", "none") else None,
                "username": username,
                "bezeichnung": str(row.get("bezeichnung", "")).strip() if row.get("bezeichnung") is not None and str(row.get("bezeichnung")).strip().lower() not in ("none", "nan") else "",
                "kennzeichen": str(row.get("kennzeichen", "")).strip() if row.get("kennzeichen") is not None and str(row.get("kennzeichen")).strip().lower() not in ("none", "nan") else "",
                "start_km_vorjahr": _safe_int(row.get("start_km_vorjahr")),
                "privat_km_min": _safe_int(row.get("privat_km_min")),
                "privat_km_max": _safe_int(row.get("privat_km_max"))
            }
            clean_insert.append(clean_row)
        if clean_insert: supabase.table("fahrzeuge").insert(clean_insert).execute()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Fahrzeuge: {getattr(e, 'message', str(e))}")

def load_fahrzeuge(username):
    response = supabase.table("fahrzeuge").select("*").eq("username", username).order("id").execute()
    if response.data: return pd.DataFrame(response.data)
    return pd.DataFrame(columns=["id", "bezeichnung", "kennzeichen", "start_km_vorjahr", "privat_km_min", "privat_km_max"])

def save_zeitraeume(username, df):
    try:
        supabase.table("zeitraeume").delete().eq("username", username).execute()
        df = df.dropna(how='all') 
        df_save = df[["fahrzeug_id", "von", "bis"]].copy()
        df_insert = df_save.to_dict('records')
        clean_insert = []
        for row in df_insert:
            von_str = _parse_date_iso(row.get("von"))
            bis_str = _parse_date_iso(row.get("bis"))
            fzg_id = _safe_int(row.get("fahrzeug_id"), default=None)
            if not von_str or not bis_str or fzg_id is None:
                continue
            clean_row = {
                "username": username,
                "fahrzeug_id": fzg_id,
                "von": von_str, 
                "bis": bis_str
            }
            clean_insert.append(clean_row)
        if clean_insert: supabase.table("zeitraeume").insert(clean_insert).execute()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Zeiträume: {getattr(e, 'message', str(e))}")
        
def load_zeitraeume(username):
    response = supabase.table("zeitraeume").select("fahrzeug_id, von, bis").eq("username", username).execute()
    if response.data: return pd.DataFrame(response.data)
    return pd.DataFrame(columns=["fahrzeug_id", "von", "bis"])

def save_fahrten_to_db(username, generated_data):
    for (jahr, monat), month_data in generated_data.items():
        df = month_data["data"]
        if not df.empty:
            supabase.table("fahrten").delete().eq("username", username).eq("jahr", jahr).eq("monat", monat).execute()
            df_insert = df.to_dict('records')
            
            clean_insert = []
            for row in df_insert:
                clean_row = {
                    "username": username, 
                    "jahr": int(jahr), 
                    "monat": int(monat),
                    "datum": str(row["datum"]), 
                    "fahrzeug_id": int(row["fahrzeug_id"]) if pd.notna(row.get("fahrzeug_id")) else None,
                    "fahrzeug": str(row["fahrzeug"]),
                    "route": str(row["route"]),
                    "km_d": int(row["km_d"]),
                    "km_p": int(row["km_p"]),
                    "abf": str(row["abf"]),
                    "ank": str(row["ank"]),
                    "dauer": str(row["dauer"]),
                    "abfahrt_km": int(row["abfahrt_km"])
                }
                clean_insert.append(clean_row)
            
            for i in range(0, len(clean_insert), 500):
                supabase.table("fahrten").insert(clean_insert[i:i+500]).execute()
                
def update_month_in_db(username, jahr, monat, df):
    supabase.table("fahrten").delete().eq("username", username).eq("jahr", jahr).eq("monat", monat).execute()
    clean_insert = []
    for row in df.to_dict('records'):
        clean_row = {
            "username": username, "jahr": int(jahr), "monat": int(monat),
            "datum": str(row["datum"]), 
            "fahrzeug_id": int(row["fahrzeug_id"]) if row.get("fahrzeug_id") is not None and str(row.get("fahrzeug_id")) != "nan" else None,
            "fahrzeug": str(row.get("fahrzeug", "")),
            "route": str(row.get("route", "")),
            "km_d": int(row["km_d"]) if str(row.get("km_d")) != "nan" else 0,
            "km_p": int(row["km_p"]) if str(row.get("km_p")) != "nan" else 0,
            "abf": str(row.get("abf", "00:00")),
            "ank": str(row.get("ank", "00:00")),
            "dauer": str(row.get("dauer", "00:00")),
            "abfahrt_km": int(row["abfahrt_km"]) if str(row.get("abfahrt_km")) != "nan" else 0
        }
        clean_insert.append(clean_row)
    for i in range(0, len(clean_insert), 500):
        supabase.table("fahrten").insert(clean_insert[i:i+500]).execute()

# ==========================================
# 1.5 SYSTEMKONFIGURATION (DB-gestützte Einstellungen)
# ==========================================
def load_system_config():
    """Lädt die Systemkonfiguration aus der Tabelle 'system_config' (Zeile mit id=1)."""
    try:
        response = supabase.table("system_config").select("*").eq("id", 1).execute()
        if response.data:
            return response.data[0]
    except Exception:
        pass
    return {}

def save_system_config(config_data):
    """Speichert die Systemkonfiguration in die Tabelle 'system_config' (Upsert, id=1)."""
    try:
        config_data["id"] = 1
        supabase.table("system_config").upsert(config_data).execute()
        return True
    except Exception as e:
        st.error(f"Fehler beim Speichern der Systemkonfiguration: {e}")
        return False

def get_smtp_settings():
    """Lädt SMTP-Einstellungen: Zuerst aus DB, dann Fallback auf Umgebungsvariablen."""
    config = load_system_config()
    return {
        "server": config.get("smtp_server") or os.environ.get("SMTP_SERVER", ""),
        "port": str(config.get("smtp_port") or os.environ.get("SMTP_PORT", "465")),
        "user": config.get("smtp_user") or os.environ.get("SMTP_USER", ""),
        "password": config.get("smtp_password") or os.environ.get("SMTP_PASSWORD", ""),
        "from_name": config.get("smtp_from_name") or "Fahrtenbuch System",
    }

# ==========================================
# 2. HELPERS
# ==========================================
def normalize_col(s: str) -> str:
    if s is None: return ""
    s = unicodedata.normalize("NFKD", str(s)); s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower(); s = re.sub(r"[^\w]+", "_", s); return s.strip("_")

def normalize_df_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy(); df.columns = [normalize_col(c) for c in df.columns]; return df

def coalesce(df: pd.DataFrame, candidates, to_name):
    for cand in candidates:
        if cand in df.columns: return df.rename(columns={cand: to_name})
    for c in df.columns:
        for cand in candidates:
            if cand in c: return df.rename(columns={c: to_name})
    return df

def drop_empty_rows(df: pd.DataFrame) -> pd.DataFrame: return df.dropna(how="all").reset_index(drop=True)

def easter_sunday(year):
    a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def austria_holidays(year):
    E = easter_sunday(year)
    return {date(year, 1, 1), date(year, 1, 6), date(year, 5, 1), date(year, 8, 15), date(year, 10, 26), date(year, 11, 1), date(year, 12, 8), date(year, 12, 25), date(year, 12, 26), E + timedelta(days=1), E + timedelta(days=39), E + timedelta(days=50), E + timedelta(days=60)}

def _safe_dauer_min(dauer_val):
    """Extrahiert sicher die Dauer in Minuten aus einem 'HH:MM' String."""
    try:
        parts = str(dauer_val).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError, TypeError, AttributeError):
        return 0

def berechne_taggeld(dauer_minuten, params):
    """Berechnet das Taggeld linear:
    - Unter min_stunden: 0 EUR
    - Ab min_stunden: basis EUR
    - Jede weitere Stunde: +stufung EUR (anteilig)
    - Ab max_stunden: max_betrag EUR (gedeckelt)

    params: {min_stunden, basis, stufung, max_betrag, max_stunden}
    """
    min_st = params.get('min_stunden', 5)
    basis = params.get('basis', 10.00)
    stufung = params.get('stufung', 2.50)
    max_betrag = params.get('max_betrag', 30.00)
    max_st = params.get('max_stunden', 13)

    stunden = dauer_minuten / 60.0
    if stunden < min_st:
        return "0,00"
    if stunden >= max_st:
        return f"{max_betrag:.2f}".replace('.', ',')
    betrag = basis + (stunden - min_st) * stufung
    betrag = min(betrag, max_betrag)
    return f"{betrag:.2f}".replace('.', ',')

def extrahiere_ort(vollstaendige_adresse):
    if not vollstaendige_adresse: return "Unbekannt"
    part = vollstaendige_adresse.split(',')[-1].strip() if ',' in vollstaendige_adresse else vollstaendige_adresse.strip()
    part_cleaned = re.sub(r'^[A-Za-z]?-?\d{4,5}\s+', '', part)
    return part_cleaned if part_cleaned else part

# ==========================================
# RED FLAG SCANNER (KORRIGIERT)
# ==========================================
def scan_for_red_flags(df, jahr, monat):
    flags = []
    if df.empty: return flags
    
    # KORREKTUR: Bessere Erkennung von Ignore-Routes
    def is_ignore_route(route):
        route_str = str(route)
        ignores = ["Keine Fahrt", "Sonntag", "Feiertag", "Urlaub"]
        return any(route_str == ign or route_str.startswith(ign + ":") or route_str.startswith(ign + " ") for ign in ignores)
    
    for _, row in df.iterrows():
        datum_str = str(row["datum"])
        total_km = int(row.get("km_d", 0)) + int(row.get("km_p", 0))
        
        if total_km > 0:
            try:
                dauer_min = _safe_dauer_min(row["dauer"])
                if dauer_min > 0:
                    geschwindigkeit = (total_km / dauer_min) * 60
                    if geschwindigkeit > 130:
                        flags.append(f"🚨 {datum_str}: Unrealistische Geschwindigkeit! {total_km} km in {row['dauer']} Std. (~{geschwindigkeit:.0f} km/h).")
            except: pass
            
        if total_km == 0 and not is_ignore_route(row["route"]):
            flags.append(f"🚨 {datum_str}: Eine Dienstreise ('{str(row['route'])[:30]}...') wurde eingetragen, aber es stehen 0 km drin!")
            
        try:
            abf_h, abf_m = map(int, str(row["abf"]).split(':'))
            ank_h, ank_m = map(int, str(row["ank"]).split(':'))
            abf_min = abf_h * 60 + abf_m
            ank_min = ank_h * 60 + ank_m
            if ank_min < abf_min and total_km > 0:
                flags.append(f"🚨 {datum_str}: Ankunft ({row['ank']}) ist vor der Abfahrt ({row['abf']} Uhr) eingetragen!")
        except: pass

    for datum, group in df.groupby("datum"):
        if len(group) > 1:
            if group["abf"].nunique() < len(group):
                flags.append(f"🚨 {str(datum)[:10]}: Mehrere Fahrten haben exakt dieselbe Abfahrtszeit ({group['abf'].iloc[0]} Uhr).")

    return flags
    
# ==========================================
# 3. PDF GENERATOREN (KORRIGIERT)
# ==========================================
def create_monats_pdf(df, monat, jahr, user_info, fahrzeuge_df):
    monate = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    monat_name = monate[monat - 1]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12*mm, rightMargin=12*mm, topMargin=12*mm, bottomMargin=25*mm)
    story = []; styles = getSampleStyleSheet()

    def footer(canvas, doc):
        canvas.saveState(); page_num = canvas.getPageNumber()
        # KORREKTUR: Variablen Footer statt hardcodiertem "vbamc34"
        user_name = user_info.get('name', 'Unbekannt')
        footer_text = f"Erstellt von: {user_name} | Seite {page_num} | Gedruckt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        canvas.setFont("Helvetica", 8); canvas.drawRightString(A4[0] - 12*mm, 10*mm, footer_text); canvas.restoreState()

    titel_para = Paragraph("Fahrtenbuch Monatsübersicht", styles['Heading2'])
    datum_para = Paragraph(f"{monat_name} {jahr}", styles['Heading2'])
    title_table = Table([[titel_para, datum_para]], colWidths=[doc.width/2, doc.width/2])
    title_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 16)]))
    story.append(title_table); story.append(Spacer(1, 8*mm))

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
    story.append(stamm_table); story.append(Spacer(1, 6*mm))

    story.append(Table([['']], colWidths=[doc.width]))
    story[-1].setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, 0), 1, colors.black)])); story.append(Spacer(1, 4*mm))

    wrap_style = ParagraphStyle('wrap', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=9.6)
    headers = ["Tag", "Abf.", "Ank.", "Dauer", "Reiseweg - Ziel - Zweck", "Abfahrt", "gefahrene km", "amtlich.", "Taggeld", "KFZ"]
    sub_headers = ["", "", "", "", "", "", "dienstl.", "privat", "", ""]
    data = [headers, sub_headers]
    
    # Taggeld-Parameter für den Monat definieren
    taggeld_params = {
        'min_stunden': user_info.get('taggeld_min_stunden', 5),
        'basis': user_info.get('taggeld_basis', 10.00),
        'stufung': user_info.get('taggeld_stufung', 2.50),
        'max_betrag': user_info.get('taggeld_max_betrag', 30.00),
        'max_stunden': user_info.get('taggeld_max_stunden', 13),
    }
    
    for _, r in df.iterrows():
        dt = pd.to_datetime(r["datum"]); german_day_abbr = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        tag_name = german_day_abbr[dt.weekday()]; tag = f"{tag_name[:2]}.{dt.day:02d}."
        route_para = Paragraph(str(r["route"]), wrap_style)
        dauer_min = _safe_dauer_min(r["dauer"])
        
        data.append([tag, r["abf"], r["ank"], r["dauer"], route_para, int(r.get("abfahrt_km", 0) or 0), int(r.get("km_d", 0) or 0), int(r.get("km_p", 0) or 0), berechne_taggeld(dauer_min, taggeld_params), r.get("fahrzeug", "")])
    
    sum_dienstl = int(df["km_d"].sum()); sum_privat = int(df["km_p"].sum())
    sum_taggeld = sum(float(berechne_taggeld(_safe_dauer_min(r["dauer"]), taggeld_params).replace(',', '.')) for _, r in df.iterrows())
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
    table.setStyle(style); story.append(table); story.append(Spacer(1, 5*mm))
    
    km_satz = float(user_info.get('km_geld', 0.42))
    notes = [
        "Privat-KM beinhalten die Fahrtstrecke Wohnung-Arbeitsplatz.", 
        f"km-Geld Satz PKW amtlich: EUR {km_satz:.2f}".replace('.', ',')]
    for note in notes: story.append(Paragraph(note, styles['Normal'])); story.append(Spacer(1, 3*mm))
    doc.build(story, onFirstPage=footer, onLaterPages=footer); buf.seek(0); return buf

def create_jahres_pdf(generated_data, jahr, user_info, fahrzeuge_df):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=15*mm, bottomMargin=15*mm)
    story = []
    styles = getSampleStyleSheet()
    monate = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

    # Taggeld-Parameter fürs Jahr definieren
    taggeld_params = {
        'min_stunden': user_info.get('taggeld_min_stunden', 5),
        'basis': user_info.get('taggeld_basis', 10.00),
        'stufung': user_info.get('taggeld_stufung', 2.50),
        'max_betrag': user_info.get('taggeld_max_betrag', 30.00),
        'max_stunden': user_info.get('taggeld_max_stunden', 13),
    }

    # ===== TITEL (18pt) =====
    title_style = ParagraphStyle('JahrTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=18, spaceAfter=8*mm)
    story.append(Paragraph(f"Fahrtenbuch Jahresübersicht {jahr}", title_style))

    # ===== KOPFZEILEN (12pt) =====
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14)
    fzg_list = [f"{r.get('bezeichnung','')} ({r.get('kennzeichen','')})" for _, r in fahrzeuge_df.iterrows()]
    story.append(Paragraph(f"Name: {user_info.get('name','')}", info_style))
    story.append(Paragraph(f"PNR: {user_info.get('pnr','')}", info_style))
    story.append(Paragraph(f"Wohnort: {user_info.get('wohnort','')}", info_style))
    story.append(Paragraph(f"Dienstort: {user_info.get('dienstort','')}", info_style))
    story.append(Paragraph(f"Entfernung zwischen Arbeitsplatz und Wohnung: {int(user_info.get('entfernung',0) or 0)} km", info_style))
    story.append(Paragraph(f"Fahrzeug(e): {', '.join(fzg_list)}", info_style))
    story.append(Spacer(1, 8*mm))

    # ===== HAUPTTABELLE =====
    # Zeile 1: Monat | gefahren km (SPAN) | km-Geld (SPAN)
    # Zeile 2: Monat | dienstl. | privat | PKW | EUR
    header_row1 = ["Monat", "gefahren km", "", "km-Geld", ""]
    header_row2 = ["Monat", "dienstl.", "privat", "PKW", "EUR"]
    data = [header_row1, header_row2]

    km_geld_satz = float(user_info.get('km_geld', 0.42))
    total_dienstl = 0
    total_privat = 0
    total_km_geld = 0
    total_taggeld = 0

    for i, monat_name in enumerate(monate):
        monat_key = (jahr, i + 1)
        if monat_key in generated_data:
            month_data = generated_data[monat_key]
            df = month_data["data"] if isinstance(month_data, dict) and "data" in month_data else month_data
            if df.empty:
                data.append([monat_name, 0, 0, "0,00", "0,00"])
                continue

            sum_dienstl = int(df["km_d"].sum())
            sum_privat = int(df["km_p"].sum())
            sum_taggeld = sum(float(berechne_taggeld(_safe_dauer_min(r["dauer"]), taggeld_params).replace(',', '.')) for _, r in df.iterrows())
            month_km_geld = sum_dienstl * km_geld_satz

            total_dienstl += sum_dienstl
            total_privat += sum_privat
            total_km_geld += month_km_geld
            total_taggeld += sum_taggeld
            data.append([monat_name, sum_dienstl, sum_privat, f"{month_km_geld:.2f}".replace('.', ','), f"{sum_taggeld:.2f}".replace('.', ',')])
        else:
            data.append([monat_name, 0, 0, "0,00", "0,00"])

    total_all_km = total_dienstl + total_privat
    data.append(["Summen", total_dienstl, total_privat, f"{total_km_geld:.2f}".replace('.', ','), f"{total_taggeld:.2f}".replace('.', ',')])

    col_widths = [32*mm, 28*mm, 25*mm, 30*mm, 30*mm]
    table = Table(data, colWidths=col_widths, repeatRows=2)
    table_style = TableStyle([
        # Header Zeile 1
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 1), colors.whitesmoke),
        # Spans
        ("SPAN", (1, 0), (2, 0)),  # "gefahren km" über dienstl./privat
        ("SPAN", (3, 0), (4, 0)),  # "km-Geld" über PKW/EUR
        # Ausrichtung
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Rahmen
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        # Summen-Zeile
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 1.5, colors.black),
    ])
    table.setStyle(table_style)
    story.append(table)
    story.append(Spacer(1, 12*mm))

    # ===== ZUSAMMENFASSUNG (Finanzen) =====
    sum_style = ParagraphStyle('Summary', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13)
    gesamt_amtlich = total_km_geld + total_taggeld
    story.append(Paragraph(f"km-Geld Satz PKW amtlich ab 01.01.{jahr} EUR {km_geld_satz:.2f}", sum_style))
    story.append(Paragraph(f"km-Geld Satz PKW dienstlich ab 01.01.{jahr} EUR 0,00", sum_style))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(f"km-Geld dienstlich für {total_dienstl}km: EUR 0,00", sum_style))
    story.append(Paragraph(f"km-Geld amtlich für {total_dienstl}km inkl. Mitfahrer: EUR {total_km_geld:.2f}".replace('.', ','), sum_style))
    story.append(Paragraph(f"Taggeld amtlich: EUR {total_taggeld:.2f}".replace('.', ','), sum_style))
    story.append(Paragraph(f"Taggeld + km-Geld amtlich: EUR {gesamt_amtlich:.2f}".replace('.', ','), sum_style))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(f"Vom Dienstgeber vergütete Reisekosten: EUR 0,00", sum_style))
    story.append(Paragraph(f"Für die Arbeitnehmerveranlagung zu berücksichtigen: EUR {total_km_geld:.2f}".replace('.', ','), sum_style))
    story.append(Spacer(1, 10*mm))

    # ===== FAHRZEUGÜBERSICHT =====
    # Ansatz: Für JEDES Fahrzeug aus fahrzeuge_df die KM aus allen Monaten zusammenzählen.
    # Verwendet .tolist() für maximale Kompatibilität (konvertiert Arrow/numpy zu reinem Python).
    vehicle_rows = []  # list of (name, kennzeichen, km_d)
    total_km_d_veh = 0
    for _, fz_row in fahrzeuge_df.iterrows():
        fz_name = str(fz_row.get('bezeichnung', '')).strip()
        fz_kz = str(fz_row.get('kennzeichen', '')).strip()
        if not fz_name or fz_name.lower() in ('none', 'nan', ''):
            continue
        km_d_jahr = 0
        for month_key, month_data in generated_data.items():
            try:
                mdf = month_data.get('data')
                if mdf is None or (hasattr(mdf, 'empty') and mdf.empty):
                    continue
                col_list = list(mdf.columns)
                if 'fahrzeug' not in col_list or 'km_d' not in col_list:
                    continue
                # .tolist() konvertiert Arrow/numpy zu reinen Python-Typen
                fz_vals = mdf['fahrzeug'].tolist()
                km_vals = mdf['km_d'].tolist()
                for fz_val, km_val in zip(fz_vals, km_vals):
                    try:
                        cell_fz = str(fz_val).strip() if fz_val is not None else ''
                        if cell_fz.lower() == fz_name.lower():
                            if km_val is not None:
                                km_d_jahr += int(float(str(km_val)))
                    except (ValueError, TypeError):
                        continue
            except Exception:
                continue
        vehicle_rows.append((fz_name, fz_kz, km_d_jahr))
        total_km_d_veh += km_d_jahr

    veh_headers = ["Fahrzeug", "Kennzeichen", "dienstl."]
    veh_data = [veh_headers]
    for name, kz, km in vehicle_rows:
        veh_data.append([name, kz, f"{km}"])
    veh_data.append(["Summen", "", f"{total_km_d_veh}"])

    veh_col_widths = [40*mm, 30*mm, 25*mm]
    vehicle_table = Table(veh_data, colWidths=veh_col_widths)
    vehicle_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 1.5, colors.black),
    ]))
    story.append(vehicle_table)
    story.append(Spacer(1, 12*mm))

    # ===== DISCLAIMER =====
    disc_style = ParagraphStyle('Disclaimer', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=11, textColor=colors.grey)
    story.append(Paragraph("Die angegebenen Daten beruhen auf persönlichen Aufzeichnungen der oben genannten Person. UNIQA übernimmt keine Haftung für die Richtigkeit der Angaben.", disc_style))

    doc.build(story)
    buf.seek(0)
    return buf

# ==========================================
# 4. STREAMLIT APP & LOGIK
# ==========================================
st.set_page_config(page_title="Fahrtenbuch Generator v6.0 - Multi-User Edition", layout="wide")

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False; st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.title("🚗 Fahrtenbuch Login")
    tab1, tab2, tab3 = st.tabs(["Anmelden", "Registrieren", "Passwort vergessen"])
    
    with tab1:
        user = st.text_input("Benutzername", key="login_user")
        pw = st.text_input("Passwort", type="password", key="login_pw")
        if st.button("Login", type="primary"):
            success, user_data = verify_user(user, pw)
            if success:
                if user_data.get('force_pw_change', False):
                    st.session_state['temp_user_data'] = user_data
                    st.session_state['force_pw_change_flow'] = True
                    st.rerun()
                else:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user.strip().lower()
                    st.rerun()
            else:
                st.error("Falsche Zugangsdaten")

    if st.session_state.get('force_pw_change_flow', False):
        st.warning("🔑 Sicherheitshinweis: Sie müssen Ihr Passwort vor der ersten Nutzung ändern!")
        with st.form("force_change_form"):
            new_pw_1 = st.text_input("Neues Passwort", type="password", key="force_new_1")
            new_pw_2 = st.text_input("Neues Passwort bestätigen", type="password", key="force_new_2")
            submitted = st.form_submit_button("Passwort speichern und einloggen")
            if submitted:
                if new_pw_1 != new_pw_2:
                    st.error("Die Passwörter stimmen nicht überein!")
                elif len(new_pw_1) < 4:
                    st.error("Passwort muss mindestens 4 Zeichen haben.")
                else:
                    temp_user = st.session_state.get('temp_user_data')
                    hashed_new_pw = hashlib.sha256(new_pw_1.encode()).hexdigest()
                    supabase.table("users").update({"password": hashed_new_pw, "force_pw_change": False}).eq("username", temp_user['username']).execute()
                    st.session_state['force_pw_change_flow'] = False
                    st.session_state['temp_user_data'] = None
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = temp_user['username']
                    st.success("Passwort erfolgreich geändert! Willkommen.")
                    st.rerun()
        st.stop()

    with tab2:
        new_user = st.text_input("Neuer Benutzername", key="reg_user")
        new_email = st.text_input("Ihre E-Mail-Adresse", key="reg_email")
        new_pw = st.text_input("Neues Passwort", type="password", key="reg_pw")
        if st.button("Account erstellen"):
            if not new_email or "@" not in new_email:
                st.error("Bitte geben Sie eine gültige E-Mail-Adresse an.")
            elif add_user(new_user, new_pw, new_email): 
                st.success("Account erstellt! Bitte loggen Sie sich ein.")
            else: 
                st.error("Benutzername existiert bereits.")

    with tab3:
        st.info("Geben Sie Ihren Benutzernamen ein. Das System sendet Ihnen dann sofort ein neues, sicheres Passwort an Ihre hinterlegte E-Mail-Adresse.")
        reset_user = st.text_input("Ihr Benutzername", key="reset_user_req")
        if st.button("🔄 Neues Passwort anfordern"):
            if reset_user:
                clean_user = reset_user.strip().lower()
                db_user = supabase.table("users").select("username, email").eq("username", clean_user).execute()
                if not db_user.data:
                    st.error("Benutzername nicht gefunden.")
                elif not db_user.data[0].get('email'):
                    st.error("Für diesen Account ist keine E-Mail hinterlegt. Bitte wenden Sie sich an den Administrator.")
                else:
                    user_email = db_user.data[0]['email']
                    new_plain_pw = secrets.token_urlsafe(8)
                    hashed_pw = hashlib.sha256(new_plain_pw.encode()).hexdigest()
                    supabase.table("users").update({"password": hashed_pw, "force_pw_change": True}).eq("username", clean_user).execute()
                    if send_reset_email(user_email, new_plain_pw):
                        st.success("Ein neues Passwort wurde an Ihre E-Mail-Adresse gesendet! Bitte prüfen Sie auch Ihren Spam-Ordner.")
                    else:
                        st.error("Fehler beim Senden der E-Mail. Bitte kontaktieren Sie den Admin manuell.")
            else:
                st.warning("Bitte Benutzernamen eingeben.")
                
    st.stop()

user = st.session_state['username']
with st.sidebar:
    st.success(f"Eingeloggt als: **{user}**")
    if st.button("Logout"): st.session_state['logged_in'] = False; st.session_state['username'] = ""; st.rerun()

# Daten laden
user_info = load_settings(user)
fahrzeuge_df = load_fahrzeuge(user)
zeitraeume_df = load_zeitraeume(user)

if "generated_months_data" not in st.session_state: st.session_state["generated_months_data"] = {}

st.title("Fahrtenbuch Generator v6.0 - Multi-User Edition")

# ========= Stammdaten =========
with st.sidebar:
    st.header("📋 Stammdaten")
    user_info['name'] = st.text_input("Name", user_info.get('name', ''))
    user_info['pnr'] = st.text_input("PNR", user_info.get('pnr', ''))
    user_info['wohnort'] = st.text_input("Wohnort", user_info.get('wohnort', ''))
    user_info['dienstort'] = st.text_input("Dienstort", user_info.get('dienstort', ''))
    user_info['entfernung'] = st.number_input("Entfernung Wohnung ↔ Arbeitsplatz (km)", 0, 300, int(user_info.get('entfernung', 25)))
    
    st.markdown("**💰 Finanzielle Eckwerte (Sätze anpassen):**")
    st.caption("Taggeld linear: ab Mindeststunden Basis-Betrag, pro weitere Stunde +Stufung, gedeckelt bei Max.")
    tg_c1, tg_c2 = st.columns(2)
    with tg_c1:
        user_info['taggeld_min_stunden'] = st.number_input("Taggeld ab Std. (Mindestdauer)", 1.0, 12.0, float(user_info.get('taggeld_min_stunden', 5)), step=0.5, format="%.1f")
        user_info['taggeld_basis'] = st.number_input("Taggeld Basis-Betrag (EUR)", 0.0, 50.0, float(user_info.get('taggeld_basis', 10.00)), step=0.50, format="%.2f")
        user_info['taggeld_stufung'] = st.number_input("Zuschlag pro weitere Stunde (EUR)", 0.0, 20.0, float(user_info.get('taggeld_stufung', 2.50)), step=0.10, format="%.2f")
    with tg_c2:
        user_info['taggeld_max_betrag'] = st.number_input("Taggeld Maximalbetrag (EUR)", 0.0, 100.0, float(user_info.get('taggeld_max_betrag', 30.00)), step=0.50, format="%.2f")
        user_info['taggeld_max_stunden'] = st.number_input("Taggeld Max ab Std. (Deckelung)", 1.0, 24.0, float(user_info.get('taggeld_max_stunden', 13)), step=0.5, format="%.1f")
    # Vorschau der Taggeld-Stufen
    _tg_min = user_info.get('taggeld_min_stunden', 5)
    _tg_basis = user_info.get('taggeld_basis', 10.00)
    _tg_stuf = user_info.get('taggeld_stufung', 2.50)
    _tg_max = user_info.get('taggeld_max_betrag', 30.00)
    _tg_max_st = user_info.get('taggeld_max_stunden', 13)
    _prev = []
    for _h in range(int(_tg_min), int(_tg_max_st) + 2):
        _b = min(_tg_basis + (_h - _tg_min) * _tg_stuf, _tg_max)
        _prev.append(f"**{_h}h**={_b:.2f}€")
    st.caption("Vorschau: " + "  |  ".join(_prev))
    user_info['km_geld'] = st.number_input("Kilometergeld PKW amtlich (EUR)", 0.0, 2.0, float(user_info.get('km_geld', 0.42)), step=0.01, format="%.2f")

    jahr = st.number_input("Jahr", min_value=2000, max_value=2100, value=date.today().year, step=1)
    if st.button("💾 Stammdaten speichern"): save_settings(user, user_info); st.toast("Gespeichert!")

st.markdown("---")
st.subheader("🚗 Eckdaten & Keywords für die Generierung")

with st.expander("⛱ Urlaubswochen (optional)"):
    st.markdown("Definieren Sie hier Ihre Urlaubswochen. An diesen Tagen werden keine Fahrten generiert.")
    colU1, colU2, colU3 = st.columns(3)
    with colU1: anzahl_wochen = st.slider("Anzahl der Urlaubswochen", 0, 4, value=0, key="anzahl_urlaubswochen")
    with colU2:
        if anzahl_wochen > 0: verteilung_art = st.selectbox("Verteilung", ["1x4 Wochen", "2x2 Wochen", "4x1 Woche"], key="verteilung_urlaub")
    with colU3:
        if anzahl_wochen > 0: start_woche_1 = st.date_input("Start der 1. Urlaubswoche", value=date(jahr, 4, 1), key="start_woche_1")
    if anzahl_wochen > 0:
        st.markdown("**Private Kilometer im Urlaub:**")
        colU4, colU5, colU6 = st.columns(3)
        with colU4:
            fahrzeug_optionen = {row['bezeichnung']: row['id'] for _, row in fahrzeuge_df.iterrows()}
            urlaub_fahrzeug = st.selectbox("Fahrzeug für private Urlaubs-KM", options=list(fahrzeug_optionen.keys()), key="urlaub_fahrzeug")
        with colU5: urlaub_km_min = st.number_input("Private KM pro Urlaubstag (Minimum)", min_value=0, max_value=500, value=30, step=5, key="urlaub_km_min")
        with colU6: urlaub_km_max = st.number_input("Private KM pro Urlaubstag (Maximum)", min_value=0, max_value=500, value=80, step=5, key="urlaub_km_max")

st.markdown("**Feinabstimmung für Wochenenden/Feiertage:**")
colW1, colW2 = st.columns(2)
with colW1: wahrscheinlichkeit_dienstfahrt_wochenende = st.slider("Wahrscheinlichkeit für Dienstfahrt am Wochenende/Feiertag (%)", 0, 100, value=10, key="wahrscheinlichkeit_dienstfahrt_wochenende")
with colW2: st.info("Restliche Fahrten sind Privatfahrten.")

colA, colB, colC, colD = st.columns(4)
with colA:
    modus = st.radio("Generierungs-Modus", ["Einzelner Monat", "Ganzes Jahr"], key="modus_auswahl")
    monat_name = st.selectbox("Monat für Generierung", ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"], index=date.today().month - 1, disabled=(modus == "Ganzes Jahr"))
    name2num = {n: i + 1 for i, n in enumerate(["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"])}; monat = name2num[monat_name]
with colB: durchschnittliche_fahrten_pro_woche = st.slider("Ø Fahrten pro Woche", 1, 10, 4)
with colC: anteil_privat_strecke = st.slider("Ø Privat-Km an Feiertagen/Sonntagen", 10, 500, 50)
with colD: wahrscheinlichkeit_dienstfahrt_werktag = st.slider("Wahrscheinlichkeit Dienstfahrt (Werktag %)", 0, 100, value=75, help="Steuert, wie wahrscheinlich eine Dienstfahrt an einem normalen Werktag (Mo-Fr) ist.")

colKM1, colKM2 = st.columns(2)
with colKM1: target_km_min = st.number_input("Ø Dienst-KM pro Monat (Minimum)", min_value=0, max_value=5000, value=1650, step=50)
with colKM2: target_km_max = st.number_input("Ø Dienst-KM pro Monat (Maximum)", min_value=0, max_value=5000, value=2000, step=50)

st.markdown("**Feinabstimmung für Feiertage/Urlaub:**")
colF1, colF2 = st.columns(2)
with colF1: wahrscheinlichkeit_dienstfahrt_feiertag_urlaub = st.slider("Wahrscheinlichkeit für Dienstfahrt an Feiertagen/Urlaubstagen (%)", 0, 100, value=5, key="wahrscheinlichkeit_dienstfahrt_feiertag_urlaub", help="Steuert, wie wahrscheinlich eine Dienstfahrt an einem Feiertag oder während des Urlaubs ist.")
with colF2: st.info("Restliche Fahrten sind Privatfahrten.")

# ========= Uploads =========
st.markdown("---"); st.subheader("📁 oder Excel-Dateien hochladen (optional)")
colU1, colU2, colU3 = st.columns(3)
fzg_xlsx = colU1.file_uploader("Fahrzeugliste.xlsx", type=["xlsx"], key="upl_fzg")
zeit_xlsx = colU2.file_uploader("Fahrenzeuge Zeiträume.xlsx", type=["xlsx"], key="upl_zeit")
kw_xlsx = colU3.file_uploader("Keywords.xlsx (optional)", key="upl_kw")

keyword_text = st.text_area("Geben Sie hier Ihre Orte und Zwecke ein (Format: `Ort:Zweck1,Zweck2,...`).", value="Straßwalchen:Büro\nOberhofen am Irrsee:KB\nStraßwalchen:Schaden,Angebot\nMondsee:Antrag,KFZ\nNeumarkt:Angebot,KFZ\nHenndorf:Angebot,KB\nZell am Moos:KB,Schaden\nKöstendorf:Angebot,KB\nFrankenmarkt:Angebot,KFZ\nEugendorf:KFZ,Angebot\nMattighofen:KFZ,Angebot\nObertrum:KFZ\nSeekirchen:Angebot,KB\nLochen:KB,Angebot\nFriedburg:Angebot,KB\nVöcklamarkt:KFZ,Angebot\nSt. Georgen:Angebot,Schaden\nSt. Gilgen:Angebot\nUnterach:KB\nOberwang:Angebot\nKirchberg:Antrag,KB\nFornach:Angebot\nSalzburg:Schaden,Angebot\nMunderfing:KB\nSeeham:KB\nHof bei Salzburg:KFZ\nLamprechtshausen:Schaden\nOberndorf:KB\nHallwang:Angebot,KB\nSchachen:Antrag\nVöcklabruck:Angebot\nVöcklabruck:Angebot")

keywords = pd.DataFrame(columns=["Ort", "Zweck"])
if kw_xlsx is None and keyword_text:
    kw_list = []
    for line in keyword_text.strip().split('\n'):
        parts = line.split(':')
        if len(parts) == 2:
            ort = parts[0].strip(); zwecke = [z.strip() for z in parts[1].split(',')]
            for zweck in zwecke: kw_list.append({"Ort": ort, "Zweck": zweck})
    keywords = pd.DataFrame(kw_list)

def process_fahrzeuge(file):
    df = normalize_df_cols(pd.read_excel(file)); df = coalesce(df, ["id", "fahrzeug_id"], "id"); df = coalesce(df, ["bezeichnung", "fahrzeug", "name", "modell"], "bezeichnung"); df = coalesce(df, ["kennzeichen", "kennz"], "kennzeichen"); df = coalesce(df, ["start_km_vorjahr", "startkmvorjahr", "start_km", "startkm", "vorjahr_km", "endkilometer_2023", "endkilometer_2023_"], "start_km_vorjahr"); df = coalesce(df, ["privat_km_min", "privatkmmin", "min_privat_km"], "privat_km_min"); df = coalesce(df, ["privat_km_max", "privatkmmax", "max_privat_km"], "privat_km_max")
    if "start_km_vorjahr" not in df.columns: df["start_km_vorjahr"] = 0
    if "privat_km_min" not in df.columns: df["privat_km_min"] = 5  
    if "privat_km_max" not in df.columns: df["privat_km_max"] = 20 
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64"); df["start_km_vorjahr"] = pd.to_numeric(df["start_km_vorjahr"], errors="coerce").fillna(0).astype(int); df["privat_km_min"] = pd.to_numeric(df["privat_km_min"], errors="coerce").fillna(5).astype(int); df["privat_km_max"] = pd.to_numeric(df["privat_km_max"], errors="coerce").fillna(20).astype(int)
    return df

def process_zeitraeume(file, fahrzeuge_df):
    raw = pd.read_excel(file); raw = drop_empty_rows(raw); df = normalize_df_cols(raw); df = coalesce(df, ["fahrzeug_id", "id", "kfz_id"], "fahrzeug_id"); df = coalesce(df, ["kennzeichen", "kennz"], "kennzeichen"); df = coalesce(df, ["bezeichnung", "fahrzeug", "name", "modell"], "bezeichnung"); df = coalesce(df, ["von", "beginn", "start", "from"], "von"); df = coalesce(df, ["bis", "ende", "end", "to"], "bis")
    for col in ["von", "bis"]:
        if col in df.columns: df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
    if "fahrzeug_id" in df.columns:
        id_coerced = pd.to_numeric(df["fahrzeug_id"], errors="coerce")
        if id_coerced.notna().mean() < 0.5:
            mapped = pd.Series([pd.NA] * len(df), dtype="Int64")
            if "kennzeichen" in df.columns and "kennzeichen" in fahrzeuge_df.columns: m = df["kennzeichen"].astype(str).str.strip().str.upper(); fk = fahrzeuge_df.set_index(fahrzeuge_df["kennzeichen"].astype(str).str.strip().str.upper())["id"]; mapped = m.map(fk).astype("Int64")
            if mapped.isna().any() and "bezeichnung" in df.columns: be = df["bezeichnung"]; m = be.astype(str).str.strip().str.upper(); fk = fahrzeuge_df.set_index(fahrzeuge_df["bezeichnung"].astype(str).str.strip().str.upper())["id"]; mapped2 = m.map(fk).astype("Int64"); mapped = mapped.fillna(mapped2)
            df["fahrzeug_id"] = mapped
        else: df["fahrzeug_id"] = id_coerced.astype("Int64")
    else:
        mapped = pd.Series([pd.NA] * len(df), dtype="Int64")
        if "kennzeichen" in df.columns and "kennzeichen" in fahrzeuge_df.columns: m = df["kennzeichen"].astype(str).str.strip().str.upper(); fk = fahrzeuge_df.set_index(fahrzeuge_df["kennzeichen"].astype(str).str.strip().str.upper())["id"]; mapped = m.map(fk).astype("Int64")
        if mapped.isna().any() and "bezeichnung" in df.columns: m = df["bezeichnung"].astype(str).str.strip().str.upper(); fk = fahrzeuge_df.set_index(fahrzeuge_df["bezeichnung"].astype(str).str.strip().str.upper())["id"]; mapped2 = m.map(fk).astype("Int64"); mapped = mapped.fillna(mapped2)
        df["fahrzeug_id"] = mapped
    keep = [c for c in ["fahrzeug_id", "von", "bis"] if c in df.columns]; df = df[keep].dropna(subset=["fahrzeug_id", "von", "bis"]).reset_index(drop=True)
    return df

if fzg_xlsx is not None: fahrzeuge_df = process_fahrzeuge(fzg_xlsx)
if zeit_xlsx is not None and not fahrzeuge_df.empty: zeitraeume_df = process_zeitraeume(zeit_xlsx, fahrzeuge_df)
if kw_xlsx is not None:
    df = normalize_df_cols(pd.read_excel(kw_xlsx)); df = coalesce(df, ["ort", "ziel", "stadt"], "ort"); df = coalesce(df, ["zweck", "grund"], "zweck")
    if "ort" in df.columns: df.rename(columns={"ort": "Ort"}, inplace=True)
    if "zweck" in df.columns: df.rename(columns={"zweck": "Zweck"}, inplace=True)
    keywords = df; st.info("✅ Keywords werden aus der hochgeladenen Excel-Datei verwendet.")

if fahrzeuge_df.empty: fahrzeuge_df = pd.DataFrame(columns=["id", "bezeichnung", "kennzeichen", "start_km_vorjahr", "privat_km_min", "privat_km_max"])
if zeitraeume_df.empty: zeitraeume_df = pd.DataFrame(columns=["fahrzeug_id", "von", "bis"])

# ========= Editors =========
st.subheader("📝 Fahrzeuge & Zeiträume (editierbar)")
colF1, colF2 = st.columns(2)
with colF1: fahrzeuge_df = st.data_editor(fahrzeuge_df, num_rows="dynamic", key="fahrzeuge_editor", use_container_width=True)
with colF2: zeitraeume_df = st.data_editor(zeitraeume_df, num_rows="dynamic", key="zeiten_editor", use_container_width=True)
if st.button("💾 Fahrzeuge & Zeiträume speichern"): save_fahrzeuge(user, fahrzeuge_df); save_zeitraeume(user, zeitraeume_df); st.toast("Gespeichert!")

st.markdown("---")
wohnort_clean = extrahiere_ort(user_info.get('wohnort', 'Oberhofen am Irrsee'))
dienstort_clean = extrahiere_ort(user_info.get('dienstort', 'Thalgau'))

# ========= Generator =========
ready = (not fahrzeuge_df.empty and not zeitraeume_df.empty and not keywords.empty)
colG1, colG2 = st.columns([1, 1])
gen_btn_text = f"🚀 Fahrten für {'Ganzes Jahr' if modus == 'Ganzes Jahr' else monat_name} {jahr} generieren"
gen_btn = colG1.button(gen_btn_text, type="primary", disabled=not ready)
clear_btn = colG2.button("🗑️ Alle generierten Daten löschen")

if clear_btn:
    st.session_state["fahrten_df"] = None; st.session_state["generated_months_data"] = {}; st.rerun()

if gen_btn:
    fahrzeug_optionen = {row['bezeichnung']: row['id'] for _, row in fahrzeuge_df.iterrows()}
    vacation_days = set()
    anzahl_wochen = st.session_state.get('anzahl_urlaubswochen', 0)
    
    # KORREKTUR: Urlaubsberechnung mit korrekten Tagen (7 statt 6)
    if anzahl_wochen > 0:
        verteilung = st.session_state.get('verteilung_urlaub', '4x1 Woche'); start_woche_1_global = st.session_state.get('start_woche_1', date(jahr, 4, 1)); start_woche_1 = date(jahr, start_woche_1_global.month, start_woche_1_global.day)
        if verteilung == "1x4 Wochen":
            for j in range(4 * 7): vacation_days.add(start_woche_1 + timedelta(days=j))
        elif verteilung == "2x2 Wochen":
            for i in range(2):
                block_start_date = start_woche_1 + timedelta(weeks=i * 26)
                for j in range(2 * 7): vacation_days.add(block_start_date + timedelta(days=j))
        elif verteilung == "4x1 Woche":
            for i in range(4):
                block_start_date = start_woche_1 + timedelta(weeks=i * 13)
                for j in range(7): vacation_days.add(block_start_date + timedelta(days=j))

    monate_zum_generieren = list(range(1, 13)) if modus == "Ganzes Jahr" else [monat]
    progress_bar = st.progress(0, text="Generiere Fahrten...")
    current_km = {row['id']: _safe_int(row.get('start_km_vorjahr')) for _, row in fahrzeuge_df.iterrows()}
    privat_km_ranges = {row['id']: (_safe_int(row.get('privat_km_min'), default=5), _safe_int(row.get('privat_km_max'), default=20)) for _, row in fahrzeuge_df.iterrows()}
    rng = np.random.default_rng()  # Ein RNG für alles (kein Neustart pro Monat)

    # Vorberechnung: effektive Werktag-Anzahl pro Monat (für realistische KM-Variation)
    hol_full = austria_holidays(jahr)
    month_workday_counts = {}
    avg_workdays = 0
    for mk in monate_zum_generieren:
        days_in_month = pd.date_range(date(jahr, mk, 1), date(jahr, mk, calendar.monthrange(jahr, mk)[1]), freq="D")
        wd_count = sum(1 for t in days_in_month if t.weekday() < 5 and t.date() not in hol_full and t.date() not in vacation_days)
        month_workday_counts[mk] = wd_count
        avg_workdays += wd_count
    avg_workdays = avg_workdays / len(monate_zum_generieren) if monate_zum_generieren else 21

    for i, monat_key in enumerate(monate_zum_generieren):
        progress_bar.progress((i + 1) / len(monate_zum_generieren), text=f"Generiere Monat {monat_key} von {monate_zum_generieren[-1]}...")
        tage = pd.date_range(date(jahr, monat_key, 1), date(jahr, monat_key, calendar.monthrange(jahr, monat_key)[1]), freq="D")
        out = []; hol = austria_holidays(jahr)
        prob_dienstfahrt_feiertag_urlaub = st.session_state.get('wahrscheinlichkeit_dienstfahrt_feiertag_urlaub', 5) / 100.0
        special_trip_done_this_week = False

        for t in tage:
            route = "Keine Fahrt"; km_d = 0; km_p = 0; abf = "00:00"; ank = "00:00"; dauer = "00:00"; fahrzeug_id = None; fahrzeug_name = "Kein Fahrzeug"
            tag_str = t.strftime("%Y-%m-%d")
            gueltige_fahrzeuge_am_tag = pd.to_numeric(zeitraeume_df[(zeitraeume_df["von"] <= tag_str) & (zeitraeume_df["bis"] >= tag_str)]["fahrzeug_id"], errors="coerce").dropna().astype(int).tolist()
            if gueltige_fahrzeuge_am_tag:
                fahrzeug_id = rng.choice(gueltige_fahrzeuge_am_tag)
                match = fahrzeuge_df[fahrzeuge_df["id"] == fahrzeug_id]["bezeichnung"]
                fahrzeug_name = match.values[0] if len(match) > 0 else "Unbekannt"
            if t.weekday() == 0: special_trip_done_this_week = False
            is_saturday = t.weekday() == 5; is_sunday = t.weekday() == 6; is_holiday = t.date() in hol; is_vacation = (t.date() in vacation_days)

            if is_holiday or is_vacation:
                if rng.random() < prob_dienstfahrt_feiertag_urlaub:
                    num_stops = rng.integers(1, 3); selected_keywords = keywords.sample(min(num_stops, len(keywords))); route_stops = [f"{r['Ort']} ({r['Zweck']})" for _, r in selected_keywords.iterrows()]; full_route = [wohnort_clean] + route_stops + [wohnort_clean]
                    km_d = rng.integers(15, 25) + sum(rng.integers(10, 25) for _ in range(num_stops)); km_p = 0
                    prefix = "Feiertag: " if is_holiday else "Urlaub: "; route = prefix + " - ".join(full_route)
                    fahrzeit_min = int(km_d / 80 * 60)
                    pause_min = int(rng.integers(20, 60))
                    dauer_min = fahrzeit_min + pause_min
                    start_minute = int(np.clip(rng.normal(480, 20), 420, 540)) 
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(minutes=start_minute)
                    ank_dt = abf_dt + timedelta(minutes=int(dauer_min))
                    abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{dauer_min // 60:02d}:{dauer_min % 60:02d}"
                else:
                    if is_vacation:
                        urlaub_fahrzeug_name = st.session_state.get('urlaub_fahrzeug', ''); urlaub_fahrzeug_id = fahrzeug_optionen.get(urlaub_fahrzeug_name, None)
                        if urlaub_fahrzeug_id is not None and urlaub_fahrzeug_id in gueltige_fahrzeuge_am_tag:
                            fahrzeug_id = urlaub_fahrzeug_id
                            _urlaub_match = fahrzeuge_df[fahrzeuge_df["id"] == fahrzeug_id]["bezeichnung"]
                            fahrzeug_name = _urlaub_match.values[0] if len(_urlaub_match) > 0 else "Unbekannt"
                            km_p = rng.integers(st.session_state.get('urlaub_km_min', 30), st.session_state.get('urlaub_km_max', 80)); route = f"Urlaub"
                        else:
                            if fahrzeug_id in privat_km_ranges: km_p = rng.integers(privat_km_ranges[fahrzeug_id][0], privat_km_ranges[fahrzeug_id][1])
                            else: km_p = rng.integers(5, 21)
                            route = "Urlaub (Urlaubs-FZ nicht verfügbar)"
                    else:
                        if fahrzeug_id in privat_km_ranges: km_p = rng.integers(privat_km_ranges[fahrzeug_id][0], privat_km_ranges[fahrzeug_id][1])
                        else: km_p = rng.integers(5, 21)
                        route = "Feiertag"
                    km_d = 0; start_hour = int(rng.integers(9, 18)); fahrzeit_min = int(rng.integers(15, 45))
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=start_hour); ank_dt = abf_dt + timedelta(minutes=fahrzeit_min)
                    abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{fahrzeit_min // 60:02d}:{fahrzeit_min % 60:02d}"
            elif is_saturday:
                if rng.random() < 0.4:
                    km_d = rng.integers(25, 55); km_p = 0; num_stops = rng.integers(1, 2); selected_keywords = keywords.sample(min(num_stops, len(keywords))); route_stops = [f"{r['Ort']} ({r['Zweck']})" for _, r in selected_keywords.iterrows()]; full_route = [wohnort_clean] + route_stops + [wohnort_clean]; route = " - ".join(full_route)
                    fahrzeit_min = int(km_d / 80 * 60); pause_min = int(rng.integers(15, 30)); dauer_min = fahrzeit_min + pause_min
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=9); ank_dt = abf_dt + timedelta(minutes=int(dauer_min))
                    abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{dauer_min // 60:02d}:{dauer_min % 60:02d}"
            elif is_sunday:
                if fahrzeug_id in privat_km_ranges: km_p = rng.integers(privat_km_ranges[fahrzeug_id][0], privat_km_ranges[fahrzeug_id][1])
                else: km_p = rng.integers(5, 21)
                km_d = 0; route = "Sonntag"; start_hour = int(rng.integers(9, 18)); fahrzeit_min = int(rng.integers(15, 45))
                abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=start_hour); ank_dt = abf_dt + timedelta(minutes=fahrzeit_min)
                abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{fahrzeit_min // 60:02d}:{fahrzeit_min % 60:02d}"
                
            else:
                current_week = t.isocalendar()[1]; target_day_for_tour = 0 if current_week % 2 == 1 else 1
                
                if t.weekday() == target_day_for_tour and not special_trip_done_this_week:
                    special_trip_done_this_week = True; km_p = int(user_info.get('entfernung', 25)); route_parts = [wohnort_clean, f"{dienstort_clean} (Büro)"]
                    num_stops = rng.integers(1, 4); selected_keywords = keywords.sample(min(num_stops, len(keywords)))
                    for _, r in selected_keywords.iterrows(): route_parts.append(f"{r['Ort']} ({r['Zweck']})")
                    route_parts.append(wohnort_clean); route = " - ".join(route_parts); km_d = int(km_p) + sum(rng.integers(15, 35) for _ in range(num_stops))
                    
                    start_minute = int(np.clip(rng.normal(480, 5), 470, 490)) 
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(minutes=start_minute)
                    end_hour = int(rng.integers(17, 23)); end_minute = int(rng.integers(0, 60))
                    ank_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=end_hour, minutes=end_minute)
                    dauer_min = int((ank_dt - abf_dt).total_seconds() / 60)
                    abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{dauer_min // 60:02d}:{dauer_min % 60:02d}"
                    
                elif rng.random() < (wahrscheinlichkeit_dienstfahrt_werktag / 100.0):
                    prob = wahrscheinlichkeit_dienstfahrt_werktag
                    if prob >= 90: num_stops = rng.integers(1, 3)
                    elif prob >= 70: num_stops = rng.integers(1, 4)
                    elif prob >= 50: num_stops = rng.integers(2, 4)
                    else: num_stops = rng.integers(2, 5)
                    selected_keywords = keywords.sample(min(num_stops, len(keywords))); route_stops = [f"{r['Ort']} ({r['Zweck']})" for _, r in selected_keywords.iterrows()]; full_route = [wohnort_clean] + route_stops + [wohnort_clean]; route = " - ".join(full_route)
                    km_d = sum(rng.integers(15, 35) for _ in range(num_stops)); km_p = 0
                    
                    start_minute = int(np.clip(rng.normal(480, 5), 470, 490)) 
                    abf_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(minutes=start_minute)
                    end_hour = int(rng.integers(17, 23)); end_minute = int(rng.integers(0, 60))
                    ank_dt = datetime.combine(t.date(), datetime.min.time()) + timedelta(hours=end_hour, minutes=end_minute)
                    dauer_min = int((ank_dt - abf_dt).total_seconds() / 60)
                    abf = abf_dt.strftime("%H:%M"); ank = ank_dt.strftime("%H:%M"); dauer = f"{dauer_min // 60:02d}:{dauer_min % 60:02d}"

            # KORREKTUR: Doppelte Zeile entfernt
            abfahrt_km = current_km.get(fahrzeug_id, 0) if fahrzeug_id is not None else 0
            out.append({"datum": t.date(), "fahrzeug_id": fahrzeug_id, "fahrzeug": fahrzeug_name, "route": route, "km_d": km_d, "km_p": km_p, "abf": abf, "ank": ank, "dauer": dauer, "abfahrt_km": abfahrt_km})
            if fahrzeug_id is not None and (km_d > 0 or km_p > 0):
                if fahrzeug_id in current_km:
                    current_km[fahrzeug_id] += km_d + km_p
                else:
                    current_km[fahrzeug_id] = km_d + km_p

        df = pd.DataFrame(out).sort_values(["datum"]).reset_index(drop=True)
        
        # KM-Skalierung: monatsspezifischer Zielwert mit Werktag-Faktor
        if not df.empty and target_km_max > 0:
            current_km_d_total = df["km_d"].sum()
            if current_km_d_total > 0 and not (target_km_min <= current_km_d_total <= target_km_max):
                # 1) Zufälliger Zielwert innerhalb [min, max] für diesen Monat
                base_target = rng.uniform(target_km_min, target_km_max)
                # 2) Werktag-Faktor: Monate mit weniger Werktagen (Feiertage/Urlaub) bekommen weniger KM
                werktag_faktor = month_workday_counts.get(monat_key, 21) / avg_workdays if avg_workdays > 0 else 1.0
                werktag_faktor = np.clip(werktag_faktor, 0.70, 1.20)
                # 3) Zusätzliche Zufälligkeit (±12%) für natürliche Schwankung
                zufalls_faktor = rng.uniform(0.88, 1.12)
                # 4) Finaler Zielwert für diesen Monat
                month_target = base_target * werktag_faktor * zufalls_faktor
                month_target = np.clip(month_target, target_km_min * 0.55, target_km_max * 1.25)
                # 5) Skalieren
                scaling_factor = month_target / current_km_d_total
                df['km_d'] = df.apply(lambda row: int(row['km_d'] * scaling_factor) if row['km_d'] > 0 else 0, axis=1)
                month_start_km = {fz_id: km - df[df['fahrzeug_id'] == fz_id][['km_d', 'km_p']].sum().sum() for fz_id, km in current_km.items()}
                corrected_rows = []
                for index, row in df.iterrows():
                    fz_id = row['fahrzeug_id']
                    if fz_id is not None: abfahrt_km = month_start_km.get(fz_id, 0); corrected_row = row.to_dict(); corrected_row['abfahrt_km'] = abfahrt_km; corrected_rows.append(corrected_row); month_start_km[fz_id] += row['km_d'] + row['km_p']
                    else: corrected_rows.append(row.to_dict())
                df = pd.DataFrame(corrected_rows)
                for fz_id, km in month_start_km.items(): current_km[fz_id] = km

        st.session_state["generated_months_data"][(jahr, monat_key)] = {"data": df, "end_km": max(current_km.values()) if current_km else 0}

    progress_bar.empty()
    st.success(f"Fahrten für {len(monate_zum_generieren)} Monat(e) generiert.")
    last_monat_key = (jahr, monate_zum_generieren[-1])
    st.session_state["fahrten_df"] = st.session_state["generated_months_data"][last_monat_key]["data"]
    
    save_fahrten_to_db(user, st.session_state["generated_months_data"])
    st.toast("Fahrten in der Cloud gespeichert!")

# ========= Anzeige, Bearbeitung & PDF =========
df = st.session_state.get("fahrten_df")
if df is not None:
    red_flags = scan_for_red_flags(df, jahr, monat)
    if red_flags:
        st.error("⚠️ Plausibilitätsprüfung fehlgeschlagen! Bitte korrigiere folgende Fehler im Fahrtenbuch, bevor du das PDF exportierst:")
        for flag in red_flags:
            st.warning(flag)
        st.markdown("---")

    st.subheader("✏️ Fahrten anpassen & manuell hinzufügen")
    edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="edit_fahrten_editor")
    
    col_save, col_add = st.columns([1, 1])
    with col_save:
        if st.button("💾 Änderungen für diesen Monat in der Cloud speichern"):
            edited_df = edited_df.sort_values(by="datum").reset_index(drop=True)
            update_month_in_db(user, jahr, monat, edited_df)
            st.session_state["generated_months_data"][(jahr, monat)]["data"] = edited_df
            st.session_state["fahrten_df"] = edited_df
            st.toast("Änderungen erfolgreich gespeichert!", icon="✅")
            st.rerun()
            
    with col_add:
        if st.button("➕ Einzelne Fahrt manuell hinzufügen"):
            st.session_state['show_add_form'] = True
            
    if st.session_state.get('show_add_form', False):
        with st.form("add_trip_form"):
            st.write("**Neue Fahrt eintragen:**")
            c1, c2, c3 = st.columns(3)
            with c1: new_date = st.date_input("Datum")
            with c2: new_fzg = st.selectbox("Fahrzeug", fahrzeuge_df['bezeichnung'].tolist())
            with c3: new_route = st.text_input("Reiseweg - Ziel - Zweck")
            
            c4, c5, c6, c7 = st.columns(4)
            with c4: new_km_d = st.number_input("Dienst-KM", 0, 999, 0)
            with c5: new_km_p = st.number_input("Privat-KM", 0, 999, 0)
            with c6: new_abf = st.text_input("Abfahrt (HH:MM)", value="08:00")
            with c7: new_ank = st.text_input("Ankunft (HH:MM)", value="17:00")
            
            submitted = st.form_submit_button("✅ Fahrt einfügen")
            if submitted:
                try:
                    h1, m1 = map(int, new_abf.split(':'))
                    h2, m2 = map(int, new_ank.split(':'))
                    dauer_min = (h2*60 + m2) - (h1*60 + m1)
                    dauer_str = f"{dauer_min//60:02d}:{dauer_min%60:02d}"
                except: dauer_str = "00:00"
                
                fz_row = fahrzeuge_df[fahrzeuge_df['bezeichnung'] == new_fzg]
                fz_id = fz_row['id'].values[0] if not fz_row.empty else 1
                
                _km_max = pd.to_numeric(edited_df['abfahrt_km'], errors='coerce').max()
                last_km = int(_km_max) if pd.notna(_km_max) else 0
                
                new_row = {
                    "datum": new_date, "fahrzeug_id": int(fz_id), "fahrzeug": new_fzg,
                    "route": new_route, "km_d": int(new_km_d), "km_p": int(new_km_p),
                    "abf": new_abf, "ank": new_ank, "dauer": dauer_str, "abfahrt_km": last_km
                }
                
                new_df = pd.concat([edited_df, pd.DataFrame([new_row])], ignore_index=True)
                new_df = new_df.sort_values(by="datum").reset_index(drop=True)
                
                update_month_in_db(user, jahr, monat, new_df)
                st.session_state["generated_months_data"][(jahr, monat)]["data"] = new_df
                st.session_state["fahrten_df"] = new_df
                st.session_state['show_add_form'] = False
                st.rerun()

    st.markdown("---")
    st.subheader("📄 PDF-Export")

    if modus == "Ganzes Jahr" and st.session_state["generated_months_data"]:
        monate_namen = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        available_months = sorted([key for key in st.session_state["generated_months_data"].keys()])
        month_names_for_select = {f"{monate_namen[m - 1]} {y}": (y, m) for y, m in available_months}
        selected_month_str = st.selectbox("Monat für PDF-Export auswählen:", list(month_names_for_select.keys()))
        selected_monat_jahr = month_names_for_select[selected_month_str]; pdf_monat, pdf_jahr = selected_monat_jahr[1], selected_monat_jahr[0]
    else: pdf_monat, pdf_jahr = monat, jahr

    colP1, colP2 = st.columns(2)
    with colP1:
        if st.button(f"📄 Monats-PDF ({calendar.month_name[pdf_monat]} {pdf_jahr}) erstellen"):
            if not REPORTLAB_OK: st.error("ReportLab nicht installiert."); st.stop()
            pdf_buffer_monat = create_monats_pdf(st.session_state["generated_months_data"][(pdf_jahr, pdf_monat)]["data"], pdf_monat, pdf_jahr, user_info, fahrzeuge_df)
            st.download_button(label=f"Download PDF {calendar.month_name[pdf_monat]} {pdf_jahr}", data=pdf_buffer_monat, file_name=f"Fahrtenbuch_Monatsuebersicht_{calendar.month_name[pdf_monat]}_{pdf_jahr}.pdf", mime="application/pdf")
    with colP2:
        if st.button("📊 Jahresbericht-PDF erstellen"):
            if not REPORTLAB_OK: st.error("ReportLab nicht installiert."); st.stop()
            if not st.session_state["generated_months_data"]: st.warning("Noch keine Monatsdaten für den Jahresbericht vorhanden.")
            else:
                pdf_buffer_jahr = create_jahres_pdf(st.session_state["generated_months_data"], jahr, user_info, fahrzeuge_df)
                st.download_button(label=f"Download Jahresbericht {jahr}", data=pdf_buffer_jahr, file_name=f"Fahrtenbuch_Jahresuebersicht_{jahr}.pdf", mime="application/pdf")

st.markdown("---")
if st.session_state.get("generated_months_data") and len(st.session_state["generated_months_data"]) == 12:
    st.subheader("🔧 Kilometerstand anpassen (Hauptuntersuchung)")
    st.info("Hier kannst du für jedes Fahrzeug den Kilometerstand an einem bestimmten Datum (z.B. von einer Hauptuntersuchung) anpassen. Die Fahrten werden vor und nach diesem Datum neu berechnet und eine Fahrt zur Werkstatt erstellt.")
    with st.expander("Korrekturen vornehmen"):
        hu_events_df = pd.DataFrame(columns=["Fahrzeug", "Datum der HU", "Kilometerstand bei HU", "Werkstattort (Dropdown)", "Kundenstopps vor HU (mehrere möglich)", "Zusätzliche Stopps (mehrere möglich)"])
        fahrzeug_optionen = {row['bezeichnung']: row['id'] for _, row in fahrzeuge_df.iterrows()}
        werkstatt_orte = sorted(list(keywords['Ort'].unique()))
        column_config = {
            "Fahrzeug": st.column_config.SelectboxColumn("Fahrzeug", help="Wähle das Fahrzeug aus", options=list(fahrzeug_optionen.keys()), required=True),
            "Datum der HU": st.column_config.DateColumn("Datum der HU", help="Datum der Hauptuntersuchung", format="DD.MM.YYYY", required=True),
            "Kilometerstand bei HU": st.column_config.NumberColumn("Kilometerstand bei HU (km)", help="Kilometerstand, der am Tacho bei der HU abgelesen wurde", min_value=0, step=1, required=True),
            "Werkstattort (Dropdown)": st.column_config.SelectboxColumn("Werkstattort", help="Wähle den Ort der Werkstatt aus der Liste", options=werkstatt_orte, required=True),
            "Kundenstopps vor HU (mehrere möglich)": st.column_config.TextColumn("Kundenstopps vor HU", help="Gib hier mehrere Orte ein, getrennt durch ein Komma (z.B. Salzburg, Seekirchen)", max_chars=200, default="", required=False),
            "Zusätzliche Stopps (mehrere möglich)": st.column_config.TextColumn("Zusätzliche Stopps (Kunden)", help="Gib hier mehrere Orte ein, getrennt durch ein Komma (z.B. Eugendorf, Henndorf)", max_chars=200, default="", required=False),
        }
        edited_hu_events = st.data_editor(hu_events_df, column_config=column_config, num_rows="dynamic", use_container_width=True, key="hu_editor")

        if st.button("🛠️ Kilometerstände korrigieren"):
            if edited_hu_events.empty: st.warning("Bitte gib mindestens eine Korrektur ein.")
            else:
                correction_data = []
                for _, row in edited_hu_events.iterrows():
                    if row["Fahrzeug"] in fahrzeug_optionen and row["Werkstattort (Dropdown)"]:
                        zusaetzliche_stopps_raw = row.get("Zusätzliche Stopps (mehrere möglich)", ""); zusaetzliche_stopps = [s.strip() for s in zusaetzliche_stopps_raw.split(',') if s.strip()]
                        stopps_vor_hu_raw = row.get("Kundenstopps vor HU (mehrere möglich)", ""); stopps_vor_hu = [s.strip() for s in stopps_vor_hu_raw.split(',') if s.strip()]
                        correction_data.append({"fahrzeug_id": fahrzeug_optionen[row["Fahrzeug"]], "datum": pd.to_datetime(row["Datum der HU"]).date(), "km_at_hu": int(row["Kilometerstand bei HU"]), "werkstattort": row["Werkstattort (Dropdown)"], "zusaetzliche_stopps": zusaetzliche_stopps, "stopps_vor_hu": stopps_vor_hu})
                if not correction_data: st.error("Ungültige Eingabe. Bitte stelle sicher, dass alle Pflichtfelder ausgefüllt sind.")
                else:
                    with st.spinner("Wende Korrekturen an..."):
                        rng = np.random.default_rng()
                        raw_wohnort = str(user_info.get('wohnort', 'Oberhofen am Irrsee'))
                        if ',' in raw_wohnort: temp_wohnort = raw_wohnort.split(',')[-1].strip(); wohnort_clean = re.sub(r'^[A-Za-z]?-?\d{4,5}\s+', '', temp_wohnort)
                        else: wohnort_clean = raw_wohnort
                        
                        all_trips_by_vehicle = {}
                        for fz_id in fahrzeug_optionen.values():
                            all_trips_for_vehicle = []
                            for monat_key, data in st.session_state["generated_months_data"].items():
                                df = data["data"]; fahrten_des_fz = df[df['fahrzeug_id'] == fz_id].copy()
                                if not fahrten_des_fz.empty: all_trips_for_vehicle.append(fahrten_des_fz)
                            if all_trips_for_vehicle: all_trips_by_vehicle[fz_id] = pd.concat(all_trips_for_vehicle).sort_values(by="datum").reset_index(drop=True)

                        for correction in correction_data:
                            fz_id = correction["fahrzeug_id"]; hu_date = correction["datum"]; km_at_hu = correction["km_at_hu"]; werkstattort = correction["werkstattort"]; zusaetzliche_stopps = correction["zusaetzliche_stopps"]; stopps_vor_hu = correction["stopps_vor_hu"]
                            if fz_id not in all_trips_by_vehicle: st.warning(f"Keine Fahrten für Fahrzeug ID {fz_id} gefunden. Überspringe Korrektur."); continue
                            _fz_name_match = fahrzeuge_df[fahrzeuge_df['id'] == fz_id]['bezeichnung']
                            fahrzeug_name = _fz_name_match.iloc[0] if len(_fz_name_match) > 0 else f"FZ {fz_id}"
                            st.write(f"**Korrigiere Fahrzeug:** {fahrzeug_name} (ID: {fz_id})")
                            all_trips_df = all_trips_by_vehicle[fz_id]
                            _fz_start_match = fahrzeuge_df[fahrzeuge_df['id'] == fz_id]['start_km_vorjahr']
                            start_km = _safe_int(_fz_start_match.iloc[0]) if len(_fz_start_match) > 0 else 0
                            trips_before_hu = all_trips_df[all_trips_df['datum'] < hu_date].copy()
                            
                            if not trips_before_hu.empty:
                                st.write(f"  - Berechne {len(trips_before_hu)} Fahrten vor dem {hu_date.strftime('%d.%m.%Y')} neu.")
                                calculated_start_km_hu_day = start_km + (trips_before_hu['km_d'] + trips_before_hu['km_p']).sum(); target_km_span = km_at_hu - start_km
                                if calculated_start_km_hu_day > start_km:
                                    scaling_factor = target_km_span / (calculated_start_km_hu_day - start_km); st.write(f"    - Skalierungsfaktor: {scaling_factor:.4f}")
                                    trips_before_hu['km_d'] = (trips_before_hu['km_d'] * scaling_factor).astype(int); trips_before_hu['km_p'] = (trips_before_hu['km_p'] * scaling_factor).astype(int)
                                else: st.write("    - Keine Skalierung notwendig.")
                                for index, corrected_trip in trips_before_hu.iterrows():
                                    monat_key = (corrected_trip['datum'].year, corrected_trip['datum'].month)
                                    original_df = st.session_state["generated_months_data"][monat_key]["data"]
                                    _orig_match = original_df[(original_df['datum'] == corrected_trip['datum']) & (original_df['fahrzeug_id'] == fz_id)].index
                                    if _orig_match.empty:
                                        continue
                                    original_trip_index = _orig_match[0]
                                    st.session_state["generated_months_data"][monat_key]["data"].at[original_trip_index, 'km_d'] = corrected_trip['km_d']
                                    st.session_state["generated_months_data"][monat_key]["data"].at[original_trip_index, 'km_p'] = corrected_trip['km_p']

                            st.write(f"  - Erstelle/Ersetze Fahrt am {hu_date.strftime('%d.%m.%Y')} mit neuer Logik.")
                            hu_month_key = (hu_date.year, hu_date.month); hu_month_df = st.session_state["generated_months_data"][hu_month_key]["data"]
                            hu_trip_index = hu_month_df[(hu_month_df['datum'] == hu_date) & (hu_month_df['fahrzeug_id'] == fz_id)].index
                            if hu_trip_index.empty: hu_trip_index = hu_month_df[hu_month_df['datum'] == hu_date].index

                            total_stops = len(stopps_vor_hu) + 1 + len(zusaetzliche_stopps)
                            hu_km_d_total = rng.integers(20, 40) + total_stops * rng.integers(10, 20)

                            route_parts = [wohnort_clean]
                            for stop in stopps_vor_hu: zufalls_zweck = rng.choice(["Angebot", "Schaden", "KB"]); route_parts.append(f"{stop} ({zufalls_zweck})")
                            route_parts.append(f"{werkstattort} (HU)"); route_parts.append(wohnort_clean); hu_route = " - ".join(route_parts)
                            
                            hu_dauer_min = 90 + total_stops * 20
                            hu_abf_dt = datetime.combine(hu_date, datetime.min.time()) + timedelta(hours=8, minutes=30); hu_ank_dt = hu_abf_dt + timedelta(minutes=hu_dauer_min)

                            if not hu_trip_index.empty:
                                trip_to_update_index = hu_trip_index[0]
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'fahrzeug_id'] = fz_id
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'fahrzeug'] = fahrzeug_name
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'km_d'] = hu_km_d_total
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'km_p'] = 0
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'route'] = hu_route
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'abf'] = hu_abf_dt.strftime("%H:%M")
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'ank'] = hu_ank_dt.strftime("%H:%M")
                                st.session_state["generated_months_data"][hu_month_key]["data"].at[trip_to_update_index, 'dauer'] = f"{hu_dauer_min // 60:02d}:{hu_dauer_min % 60:02d}"
                                st.write(f"    - Vorhandene Fahrt wurde mit {hu_km_d_total} km und neuem Reiseweg ersetzt.")
                            else: st.error(f"    - FEHLER: Konnte keinen Eintrag für den {hu_date.strftime('%d.%m.%Y')} finden. Überspringe Korrektur.")

                        with st.spinner("Berechne alle Abfahrts-Kilometerstände neu..."):
                            current_km_recalc = {row['id']: row['start_km_vorjahr'] for _, row in fahrzeuge_df.iterrows()}
                            sortierte_monate = sorted(st.session_state["generated_months_data"].keys())
                            for monat_key in sortierte_monate:
                                data = st.session_state["generated_months_data"][monat_key]; df = data["data"].sort_values(by="datum").reset_index(drop=True)
                                corrected_rows = []
                                for index, row in df.iterrows():
                                    fz_id = row['fahrzeug_id']
                                    if fz_id is not None:
                                        abfahrt_km_neu = current_km_recalc.get(fz_id, 0)
                                        corrected_row = row.to_dict()
                                        corrected_row['abfahrt_km'] = abfahrt_km_neu
                                        corrected_rows.append(corrected_row)
                                        current_km_recalc[fz_id] = current_km_recalc.get(fz_id, 0) + _safe_int(row.get('km_d')) + _safe_int(row.get('km_p'))
                                    else: corrected_rows.append(row.to_dict())
                                st.session_state["generated_months_data"][monat_key]["data"] = pd.DataFrame(corrected_rows)

                        last_month_key = sorted(st.session_state["generated_months_data"].keys())[-1]
                        st.session_state["fahrten_df"] = st.session_state["generated_months_data"][last_month_key]["data"]
                        st.success("Kilometerstände und HU-Fahrten wurden erfolgreich korrigiert!")
                        
                        save_fahrten_to_db(user, st.session_state["generated_months_data"])
                        st.toast("Korrekturen in Cloud gespeichert!")
                        st.rerun()

else: 
    st.info("Um die Kilometerstände anpassen zu können, muss zuerst das gesamte Jahr generiert werden.")

# ==========================================
# 5. ADMIN BEREICH
# ==========================================
ADMIN_USERS = ["christian mayerhofer"]

if user in ADMIN_USERS:
    st.markdown("---")
    with st.expander("👑 Admin-Bereich", expanded=False):
        admin_tab_config, admin_tab_users, admin_tab_data = st.tabs([
            "⚙️ Systemkonfiguration",
            "👥 Benutzerverwaltung",
            "📁 Daten ansehen"
        ])

        # --------------------------------------------------
        # TAB 1: SYSTEMKONFIGURATION (SMTP + Supabase)
        # --------------------------------------------------
        with admin_tab_config:

            # --- SMTP / E-Mail Einstellungen ---
            st.subheader("📧 E-Mail / SMTP Konfiguration")
            st.caption("Hier werden die Zugangsdaten für den E-Mail-Versand (Passwort-Reset) konfiguriert. "
                       "Werden hier keine Werte gespeichert, werden die Umgebungsvariablen (Environment Variables) verwendet.")

            current_config = load_system_config()
            current_smtp = get_smtp_settings()

            smtp_col1, smtp_col2 = st.columns(2)
            with smtp_col1:
                cfg_server = st.text_input("SMTP Server", value=current_smtp.get("server", ""), key="cfg_smtp_server",
                                           help="z.B. mail.gmx.net, smtp.gmail.com")
                cfg_port = st.text_input("SMTP Port", value=current_smtp.get("port", "465"), key="cfg_smtp_port",
                                         help="465 = SSL (GMX), 587 = STARTTLS")
            with smtp_col2:
                cfg_user = st.text_input("SMTP Benutzer / E-Mail", value=current_smtp.get("user", ""), key="cfg_smtp_user",
                                         help="z.B. dein.name@gmx.at")
                cfg_password = st.text_input("SMTP Passwort", value="", type="password", key="cfg_smtp_password",
                                             help="App-Passwort verwenden, nicht das normale Login-Passwort!")

            cfg_from_name = st.text_input("Absendername", value=current_smtp.get("from_name", "Fahrtenbuch System"),
                                          key="cfg_smtp_from_name")

            # Prüfe ob SMTP in der DB oder in Env-Vars konfiguriert ist
            smtp_source = "Datenbank" if current_config.get("smtp_server") else "Umgebungsvariablen" if os.environ.get("SMTP_SERVER") else "nicht konfiguriert"
            st.caption(f"Aktuelle SMTP-Quelle: **{smtp_source}**")

            col_save_smtp, col_test_smtp = st.columns(2)
            with col_save_smtp:
                if st.button("💾 SMTP-Einstellungen speichern", key="btn_save_smtp"):
                    save_data = {
                        "smtp_server": cfg_server.strip(),
                        "smtp_port": cfg_port.strip(),
                        "smtp_user": cfg_user.strip(),
                        "smtp_from_name": cfg_from_name.strip(),
                    }
                    # Passwort nur überschreiben, wenn etwas eingegeben wurde
                    if cfg_password:
                        save_data["smtp_password"] = cfg_password
                    else:
                        # Behalte bestehendes Passwort explizit, damit Upsert es nicht mit NULL überschreibt
                        save_data["smtp_password"] = current_config.get("smtp_password", "")

                    if save_system_config(save_data):
                        st.success("SMTP-Einstellungen gespeichert!")
                        st.rerun()

            with col_test_smtp:
                test_email_target = st.text_input("Test-E-Mail an", value="", key="cfg_test_email",
                                                  help="Gib hier eine E-Mail-Adresse ein, an die eine Test-Nachricht gesendet wird.")
                if st.button("📧 Test-E-Mail senden", key="btn_test_email"):
                    if not test_email_target or "@" not in test_email_target:
                        st.error("Bitte eine gültige E-Mail-Adresse eingeben.")
                    else:
                        test_smtp = {
                            "server": cfg_server.strip() or current_smtp.get("server", ""),
                            "port": cfg_port.strip() or current_smtp.get("port", "465"),
                            "user": cfg_user.strip() or current_smtp.get("user", ""),
                            "password": cfg_password or current_smtp.get("password", ""),
                            "from_name": cfg_from_name.strip() or current_smtp.get("from_name", "Fahrtenbuch System"),
                        }
                        if send_reset_email(test_email_target, "TEST-PASSWORT-1234", smtp_settings=test_smtp):
                            st.success(f"Test-E-Mail erfolgreich an {test_email_target} gesendet! Prüfe Posteingang und Spam-Ordner.")
                        else:
                            st.error("Test-E-Mail konnte NICHT gesendet werden. Prüfe SMTP-Einstellungen und App-Passwort.")

            st.markdown("---")

            # --- Supabase Verbindungsinfos (nur Anzeige) ---
            st.subheader("🗄️ Supabase Verbindung")
            st.caption("Die Supabase-Zugangsdaten werden aus den Umgebungsvariablen geladen. "
                       "Änderungen erfordern eine Anpassung in der Deployment-Konfiguration und einen Neustart der App.")

            supabase_url_display = os.environ.get("SUPABASE_URL", "(nicht gesetzt)")
            supabase_key_display = os.environ.get("SUPABASE_KEY", "(nicht gesetzt)")
            if supabase_key_display != "(nicht gesetzt)" and len(supabase_key_display) > 10:
                supabase_key_display = supabase_key_display[:6] + "••••••••" + supabase_key_display[-4:]

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                st.text_input("Supabase URL", value=supabase_url_display, disabled=True, key="admin_supabase_url")
            with col_s2:
                st.text_input("Supabase Key", value=supabase_key_display, disabled=True, key="admin_supabase_key")

            st.markdown("---")

            # --- Hinweise zur Spam-Vermeidung ---
            with st.expander("💡 Hinweise: E-Mails landen im Spam – was tun?"):
                st.markdown("""
                **Das Spam-Problem hat zwei Ebenen – Code und Server-Konfiguration:**

                **Was im Code bereits optimiert wurde:**
                - ✅ `X-Priority` Header entfernt (triggert Spam-Filter)
                - ✅ `Reply-To` Header hinzugefügt
                - ✅ Korrekte `Message-ID` mit Domain
                - ✅ Nur Plain-Text (kein HTML)
                - ✅ Professioneller Absendername

                **Was auf Server-/Domain-Ebene nötig ist (wichtigste Maßnahmen):**
                1. **SPF-Record**: DNS-Eintrag der deiner Domain erlaubt, E-Mails über deinen SMTP-Server zu versenden
                2. **DKIM-Signatur**: Digitale Signatur deiner E-Mails (muss beim Mail-Provider eingerichtet werden)
                3. **DMARC-Policy**: DNS-Eintrag der Empfängern sagt, wie mit E-Mails ohne SPF/DKIM umgegangen werden soll

                **Empfehlung für zuverlässigen E-Mail-Versand:**
                - Für GMX: SPF/DKIM/DMARC über GMX-Admin einrichten
                - Bessere Alternative: Einen Transaktions-E-Mail-Dienst wie **SendGrid**, **Mailgun** oder **Resend** nutzen
                - Diese Dienste haben alle Spam-Einstellungen bereits korrekt konfiguriert
                """)

        # --------------------------------------------------
        # TAB 2: BENUTZERVERWALTUNG
        # --------------------------------------------------
        with admin_tab_users:
            st.subheader("Passwort eines Users zurücksetzen")

            response = supabase.table("users").select("username").execute()
            all_users = [r["username"] for r in response.data]

            if all_users:
                selected_user = st.selectbox("User auswählen", all_users, key="admin_user_select")
                new_pw = st.text_input("Neues Passwort für diesen User", type="password", key="admin_pw_reset")

                if st.button("🔐 Passwort jetzt ändern"):
                    if new_pw:
                        hashed_pw = hashlib.sha256(new_pw.encode()).hexdigest()
                        supabase.table("users").update({"password": hashed_pw, "force_pw_change": True}).eq("username", selected_user).execute()
                        st.success(f"Passwort für **{selected_user}** wurde geändert. User muss bei nächstem Login ein neues Passwort setzen.")
                    else:
                        st.warning("Bitte gib ein neues Passwort ein.")

                st.markdown("---")

                st.subheader("📧 E-Mail-Adresse verwalten (für Passwort-Reset)")
                user_account_info = supabase.table("users").select("username, email").eq("username", selected_user).execute()
                current_email = user_account_info.data[0].get('email', '') if user_account_info.data else ''

                new_email = st.text_input("E-Mail-Adresse eintragen/ändern", value=current_email, key="admin_email_input")
                if st.button("💾 E-Mail speichern"):
                    if new_email and "@" in new_email:
                        supabase.table("users").update({"email": new_email.strip().lower()}).eq("username", selected_user).execute()
                        st.success(f"E-Mail-Adresse für **{selected_user}** gespeichert. User kann nun selbst sein Passwort zurücksetzen.")
                        st.rerun()
                    else:
                        st.error("Bitte gib eine gültige E-Mail-Adresse ein (muss ein @ enthalten).")

        # --------------------------------------------------
        # TAB 3: DATEN ANSEHEN
        # --------------------------------------------------
        with admin_tab_data:
            response = supabase.table("users").select("username").execute()
            all_users = [r["username"] for r in response.data]
            if all_users:
                selected_data_user = st.selectbox("User-Daten anzeigen", all_users, key="admin_data_user_select")

                st.subheader(f"📁 Daten von {selected_data_user}")

                u_settings = load_settings(selected_data_user)
                u_fahrzeuge = load_fahrzeuge(selected_data_user)

                col1, col2 = st.columns(2)
                with col1:
                    st.write("**Stammdaten:**")
                    if u_settings:
                        st.json(u_settings)
                    else:
                        st.info("Noch keine Stammdaten hinterlegt.")
                with col2:
                    st.write("**Fahrzeuge:**")
                    if not u_fahrzeuge.empty:
                        st.dataframe(u_fahrzeuge)
                    else:
                        st.info("Noch keine Fahrzeuge hinterlegt.")
