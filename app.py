import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import json
import unicodedata
from datetime import date, datetime, timedelta
import calendar
import os
import hashlib
import secrets
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

# ==========================================
# 0. NOTFALL-ADMIN & KONFIGURATION
# ==========================================
CONFIG_FILE = "config.json"
ADMIN_SETUP_PASSWORD = "Lowdrivertest12345" # <<< DIESES PASSWORT FÜR DAS NOTFALL-PANEL ÄNDERN!

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)

def init_db_and_env():
    global supabase
    config = load_config()
    url = os.environ.get("SUPABASE_URL") or config.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or config.get("SUPABASE_KEY")
    smtp_s = os.environ.get("SMTP_SERVER") or config.get("SMTP_SERVER")
    smtp_p = os.environ.get("SMTP_PORT") or config.get("SMTP_PORT")
    smtp_u = os.environ.get("SMTP_USER") or config.get("SMTP_USER")
    smtp_pw = os.environ.get("SMTP_PASSWORD") or config.get("SMTP_PASSWORD")
    
    if smtp_s: os.environ["SMTP_SERVER"] = smtp_s
    if smtp_p: os.environ["SMTP_PORT"] = str(smtp_p)
    if smtp_u: os.environ["SMTP_USER"] = smtp_u
    if smtp_pw: os.environ["SMTP_PASSWORD"] = smtp_pw
    
    if url and key:
        try:
            from supabase import create_client, Client
            supabase = create_client(url, key)
            return True
        except Exception as e: print(f"Supabase Error: {e}")
    return False

supabase = None
st.set_page_config(page_title="Fahrtenbuch System", layout="wide", page_icon="🚗")

if not init_db_and_env():
    st.title("🛠️ System-Setup (Erstmalige Konfiguration)")
    st.error("Keine Supabase-Zugangsdaten gefunden! Bitte tragen Sie hier Ihre Daten ein.")
    with st.form("setup_form"):
        setup_pw = st.text_input("Admin-Passwort für dieses Panel eingeben:", type="password")
        st.subheader("☁️ Supabase Datenbank")
        c1, c2 = st.columns(2)
        s_url = c1.text_input("SUPABASE_URL", value="https://xxxxx.supabase.co")
        s_key = c2.text_input("SUPABASE_KEY (Service Role!)", type="password", value="eyJ...")
        st.subheader("📧 E-Mail / SMTP Daten (z.B. GMX)")
        c3, c4 = st.columns(2)
        smtp_s = c3.text_input("SMTP Server", value="mail.gmx.net")
        smtp_p = c4.text_input("SMTP Port", value="465")
        smtp_u = st.text_input("SMTP User (E-Mail)", value="deine.email@gmx.at")
        smtp_pw = st.text_input("SMTP Passwort (App-Passwort)", type="password", value="...")
        if st.form_submit_button("💾 Speichern und starten", type="primary"):
            if setup_pw != ADMIN_SETUP_PASSWORD: st.error("Falsches Admin-Passwort!")
            elif not s_url or not s_key: st.error("Supabase Daten fehlen!")
            else:
                save_config({"SUPABASE_URL": s_url, "SUPABASE_KEY": s_key, "SMTP_SERVER": smtp_s, "SMTP_PORT": smtp_p, "SMTP_USER": smtp_u, "SMTP_PASSWORD": smtp_pw})
                st.success("Gespeichert! Neustart in 3 Sekunden...")
                import time; time.sleep(3); st.rerun()
    st.stop()

# --- E-Mail Versand (GMX optimiert) ---
def send_reset_email(to_email, new_plain_password):
    try:
        smtp_server = os.environ.get("SMTP_SERVER"); smtp_port = os.environ.get("SMTP_PORT")
        smtp_user = os.environ.get("SMTP_USER"); smtp_password = os.environ.get("SMTP_PASSWORD")
        if not all([smtp_server, smtp_port, smtp_user, smtp_password]): return False
        msg = EmailMessage(); msg['From'] = f"Fahrtenbuch System <{smtp_user}>"; msg['To'] = to_email
        msg['Subject'] = "Ihr neues Passwort für das Fahrtenbuch"
        domain = smtp_user.split('@')[1]; msg['Message-ID'] = make_msgid(domain=domain)
        msg.set_content(f"Hallo,\n\nIhr neues Passwort lautet: {new_plain_password}\n\nBitte loggen Sie sich ein und ändern Sie es sofort.\n\nViele Grüße\nAdmin-Team", subtype='plain', charset='utf-8')
        if int(smtp_port) == 465:
            with smtplib.SMTP_SSL(smtp_server, int(smtp_port), timeout=10) as server: server.login(smtp_user, smtp_password); server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, int(smtp_port), timeout=10) as server: server.ehlo(); server.starttls(); server.ehlo(); server.login(smtp_user, smtp_password); server.send_message(msg)
        return True
    except Exception as e: print(f"Mail Error: {e}"); return False

try:
    from reportlab.lib.pagesizes import A4; from reportlab.lib.units import mm; from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet; REPORTLAB_OK = True
except Exception as e: st.error(f"ReportLab Error: {e}"); REPORTLAB_OK = False

# ==========================================
# 1. DATENBANK-FUNKTIONEN
# ==========================================
def add_user(username, password, email):
    try: supabase.table("users").insert({"username": username.strip().lower(), "password": hashlib.sha256(password.encode()).hexdigest(), "email": email.strip().lower(), "force_pw_change": True}).execute(); return True
    except: return False

def verify_user(username, password):
    u = username.strip().lower(); p = hashlib.sha256(password.encode()).hexdigest()
    r = supabase.table("users").select("username, password, email, force_pw_change").eq("username", u).eq("password", p).execute()
    return (True, r.data[0]) if r.data else (False, {})

def save_settings(username, d): supabase.table("settings").upsert({"username": username, "name": d.get('name',''), "pnr": d.get('pnr',''), "wohnort": d.get('wohnort',''), "dienstort": d.get('dienstort',''), "entfernung": d.get('entfernung', 0), "taggeld_kurz": d.get('taggeld_kurz', 14.70), "taggeld_mittel": d.get('taggeld_mittel', 29.40), "taggeld_lang": d.get('taggeld_lang', 29.40), "km_geld": d.get('km_geld', 0.42)}).execute()
def load_settings(username): r = supabase.table("settings").select("*").eq("username", username).execute(); return r.data[0] if r.data else {}

def save_fahrzeuge(username, df):
    try:
        supabase.table("fahrzeuge").delete().eq("username", username).execute(); df = df.dropna(how='all')
        ci = []
        for _, row in df.drop(columns=['username'], errors='ignore').iterrows():
            ci.append({"id": int(row["id"]) if pd.notna(row.get("id")) and str(row.get("id")) not in ["nan", "None"] else None, "username": username, "bezeichnung": str(row.get("bezeichnung", "")).strip(), "kennzeichen": str(row.get("kennzeichen", "")).strip(), "start_km_vorjahr": int(str(row.get("start_km_vorjahr", 0)).replace('.', '').replace(',', '').strip() or 0), "privat_km_min": int(str(row.get("privat_km_min", 0)).replace('.', '').replace(',', '').strip() or 0), "privat_km_max": int(str(row.get("privat_km_max", 0)).replace('.', '').replace(',', '').strip() or 0)})
        if ci: supabase.table("fahrzeuge").insert(ci).execute()
    except Exception as e: st.error(f"Fehler Fahrzeuge: {e}")

def load_fahrzeuge(username): r = supabase.table("fahrzeuge").select("*").eq("username", username).order("id").execute(); return pd.DataFrame(r.data) if r.data else pd.DataFrame(columns=["id", "bezeichnung", "kennzeichen", "start_km_vorjahr", "privat_km_min", "privat_km_max"])

def save_zeitraeume(username, df):
    try:
        supabase.table("zeitraeume").delete().eq("username", username).execute(); df = df.dropna(how='all'); ci = []
        for _, row in df[["fahrzeug_id", "von", "bis"]].iterrows():
            ci.append({"username": username, "fahrzeug_id": int(row["fahrzeug_id"]) if pd.notna(row.get("fahrzeug_id")) else None, "von": str(row.get("von", ""))[:10] if pd.notna(row.get("von")) and "NaT" not in str(row["von"]) else None, "bis": str(row.get("bis", ""))[:10] if pd.notna(row.get("bis")) and "NaT" not in str(row["bis"]) else None})
        if ci: supabase.table("zeitraeume").insert(ci).execute()
    except Exception as e: st.error(f"Fehler Zeiträume: {e}")
        
def load_zeitraeume(username): r = supabase.table("zeitraeume").select("fahrzeug_id, von, bis").eq("username", username).execute(); return pd.DataFrame(r.data) if r.data else pd.DataFrame(columns=["fahrzeug_id", "von", "bis"])

def save_fahrten_to_db(username, gdata):
    for (j, m), md in gdata.items():
        df = md["data"]
        if not df.empty:
            supabase.table("fahrten").delete().eq("username", username).eq("jahr", j).eq("monat", m).execute(); ci = []
            for _, row in df.iterrows(): ci.append({"username": username, "jahr": int(j), "monat": int(m), "datum": str(row["datum"]), "fahrzeug_id": int(row["fahrzeug_id"]) if pd.notna(row.get("fahrzeug_id")) else None, "fahrzeug": str(row["fahrzeug"]), "route": str(row["route"]), "km_d": int(row["km_d"]), "km_p": int(row["km_p"]), "abf": str(row["abf"]), "ank": str(row["ank"]), "dauer": str(row["dauer"]), "abfahrt_km": int(row["abfahrt_km"])})
            for i in range(0, len(ci), 500): supabase.table("fahrten").insert(ci[i:i+500]).execute()
                
def update_month_in_db(username, j, m, df):
    supabase.table("fahrten").delete().eq("username", username).eq("jahr", j).eq("monat", m).execute(); ci = []
    for _, row in df.iterrows(): ci.append({"username": username, "jahr": int(j), "monat": int(m), "datum": str(row["datum"]), "fahrzeug_id": int(row["fahrzeug_id"]) if row.get("fahrzeug_id") is not None and str(row.get("fahrzeug_id")) != "nan" else None, "fahrzeug": str(row.get("fahrzeug", "")), "route": str(row.get("route", "")), "km_d": int(row["km_d"]) if str(row.get("km_d")) != "nan" else 0, "km_p": int(row["km_p"]) if str(row.get("km_p")) != "nan" else 0, "abf": str(row.get("abf", "00:00")), "ank": str(row.get("ank", "00:00")), "dauer": str(row.get("dauer", "00:00")), "abfahrt_km": int(row["abfahrt_km"]) if str(row.get("abfahrt_km")) != "nan" else 0})
    for i in range(0, len(ci), 500): supabase.table("fahrten").insert(ci[i:i+500]).execute()

# ==========================================
# 2. HELPERS
# ==========================================
def normalize_col(s): 
    if s is None: return ""
    s = unicodedata.normalize("NFKD", str(s)); s = "".join(ch for ch in s if not unicodedata.combining(ch)); s = s.strip().lower(); return re.sub(r"[^\w]+", "_", s).strip("_")
def normalize_df_cols(df): df = df.copy(); df.columns = [normalize_col(c) for c in df.columns]; return df
def coalesce(df, cands, to_name):
    for c in cands:
        if c in df.columns: return df.rename(columns={c: to_name})
    for col in df.columns:
        for c in cands:
            if c in col: return df.rename(columns={col: to_name})
    return df
def drop_empty_rows(df): return df.dropna(how="all").reset_index(drop=True)

def easter_sunday(y):
    a=y%19;b=y//100;c=y%100;d=b//4;e=b%4;f=(b+8)//25;g=(b-f+1)//3;h=(19*a+b-d-g+15)%30;i=c//4;k=c%4;l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451;return date(y,(h+l-7*m+114)//31,((h+l-7*m+114)%31)+1)
def austria_holidays(y):
    E=easter_sunday(y); return {date(y,1,1),date(y,1,6),date(y,5,1),date(y,8,15),date(y,10,26),date(y,11,1),date(y,12,8),date(y,12,25),date(y,12,26),E+timedelta(days=1),E+timedelta(days=39),E+timedelta(days=50),E+timedelta(days=60)}
def berechne_taggeld(d, s):
    if d<300: return f"{s.get('kurz',14.70):.2f}".replace('.',',')
    if d<600: return f"{s.get('mittel',29.40):.2f}".replace('.',',')
    return f"{s.get('lang',29.40):.2f}".replace('.',',')
def extrahiere_ort(a):
    if not a: return "Unbekannt"
    p = a.split(',')[-1].strip() if ',' in a else a.strip(); c = re.sub(r'^[A-Za-z]?-?\d{4,5}\s+', '', p); return c if c else p

def scan_for_red_flags(df, j, m):
    flags = []
    if df.empty: return flags
    def is_ignore(r):
        rs = str(r)
        return any(rs == i or rs.startswith(i+":") or rs.startswith(i+" ") for i in ["Keine Fahrt","Sonntag","Feiertag","Urlaub"])
    for _, row in df.iterrows():
        ds = str(row["datum"]); tk = int(row.get("km_d",0))+int(row.get("km_p",0))
        if tk > 0:
            try:
                h2,m2=map(int,str(row["dauer"]).split(':')); dm=h2*60+m2
                if dm>0 and (tk/dm)*60 > 130: flags.append(f"🚨 {ds}: Unrealistische Geschwindigkeit! {tk}km in {row['dauer']}.")
            except: pass
        if tk==0 and not is_ignore(row["route"]): flags.append(f"🚨 {ds}: Dienstreise '{str(row['route'])[:30]}' hat 0 km!")
        try:
            ah,am=map(int,str(row["abf"]).split(':')); ah2,am2=map(int,str(row["ank"]).split(':'))
            if (ah2*60+am2)<(ah*60+am) and tk>0: flags.append(f"🚨 {ds}: Ankunft vor Abfahrt!")
        except: pass
    for d,g in df.groupby("datum"):
        if len(g)>1 and g["abf"].nunique()<len(g): flags.append(f"🚨 {str(d)[:10]}: Doppelte Abfahrtszeiten!")
    return flags

# ==========================================
# 3. PDF GENERATOREN
# ==========================================
def create_monats_pdf(df, monat, jahr, ui, fzg_df):
    monate=["Jänner","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]
    buf=io.BytesIO(); doc=SimpleDocTemplate(buf,pagesize=A4,leftMargin=12*mm,rightMargin=12*mm,topMargin=12*mm,bottomMargin=25*mm); story=[]; styles=getSampleStyleSheet()
    def footer(c,d): c.saveState(); pn=c.getPageNumber(); c.setFont("Helvetica",8); c.drawRightString(A4[0]-12*mm,10*mm,f"Erstellt von: {ui.get('name','')} | Seite {pn} | {datetime.now().strftime('%d.%m.%Y %H:%M')}"); c.restoreState()
    story.append(Paragraph("Fahrtenbuch Monatsübersicht",styles['Heading2'])); story.append(Spacer(1,8*mm))
    fzgL=[f"{r.get('bezeichnung','')} ({r.get('kennzeichen','')})" for _,r in fzg_df.iterrows()]
    stamm=[[Paragraph(f"Name: {ui.get('name','')}",styles['Normal'])],[Paragraph(f"PNR: {ui.get('pnr','')}",styles['Normal'])],[Paragraph(f"Wohnort: {ui.get('wohnort','')}",styles['Normal'])],[Paragraph(f"Dienstort: {ui.get('dienstort','')}",styles['Normal'])],[Paragraph(f"Entfernung: {int(ui.get('entfernung',0) or 0)} km",styles['Normal'])],[Paragraph("Fahrzeug(e): "+" | ".join(fzgL),styles['Normal'])]]
    stT=Table(stamm,colWidths=[doc.width]); stT.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),9)])); story.append(stT); story.append(Spacer(1,6*mm))
    ws=ParagraphStyle('w',parent=styles['Normal'],fontName='Helvetica',fontSize=8,leading=9.6)
    h=["Tag","Abf.","Ank.","Dauer","Reiseweg - Ziel - Zweck","Abfahrt","gefahrene km","amtlich.","Taggeld","KFZ"]; sh=["","","","","","","dienstl.","privat","",""]; data=[h,sh]
    ts={"kurz":ui.get('taggeld_kurz',14.70),"mittel":ui.get('taggeld_mittel',29.40),"lang":ui.get('taggeld_lang',29.40)}
    for _,r in df.iterrows():
        dt=pd.to_datetime(r["datum"]); tg=f"{['Mo','Di','Mi','Do','Fr','Sa','So'][dt.weekday()][:2]}.{dt.day:02d}."
        try: dm=int(r["dauer"].split(':')[0])*60+int(r["dauer"].split(':')[1])
        except: dm=0
        data.append([tg,r["abf"],r["ank"],r["dauer"],Paragraph(str(r["route"]),ws),int(r["abfahrt_km"]),int(r["km_d"]),int(r["km_p"]),berechne_taggeld(dm,ts),r["fahrzeug"]])
    sd=int(df["km_d"].sum()); sp=int(df["km_p"].sum()); stg=sum(float(berechne_taggeld(int(r["dauer"].split(':')[0])*60+int(r["dauer"].split(':')[1]),ts).replace(',','.')) for _,r in df.iterrows())
    data.append(["Einzelsummen:","","","","","",sd,sp,f"{stg:.2f}".replace('.',','),""])
    cw=[12*mm,10*mm,10*mm,12*mm,71*mm,15*mm,15*mm,15*mm,12*mm,18*mm]; t=Table(data,colWidths=cw,repeatRows=2)
    t.setStyle(TableStyle([("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,1),(-1,1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,1),9),("FONTSIZE",(0,2),(-1,-1),8),("ALIGN",(0,0),(-1,-1),"CENTER"),("ALIGN",(4,2),(4,-1),"LEFT"),("GRID",(0,0),(-1,-1),0.25,colors.grey),("BACKGROUND",(0,0),(-1,1),colors.whitesmoke),("SPAN",(6,0),(7,0)),("SPAN",(0,-1),(4,-1)),("BACKGROUND",(0,-1),(-1,-1),colors.whitesmoke)]))
    story.append(t); story.append(Spacer(1,5*mm))
    for n in [f"Privat-KM beinhalten Wohnung-Arbeitsplatz.", f"km-Geld Satz: EUR {float(ui.get('km_geld',0.42)):.2f}".replace('.',',')]: story.append(Paragraph(n,styles['Normal']))
    doc.build(story,onFirstPage=footer,onLaterPages=footer); buf.seek(0); return buf

def create_jahres_pdf(gdata, jahr, ui, fzg_df):
    buf=io.BytesIO(); doc=SimpleDocTemplate(buf,pagesize=A4,leftMargin=12*mm,rightMargin=12*mm,topMargin=12*mm,bottomMargin=10*mm); story=[]; styles=getSampleStyleSheet()
    monate=["Jänner","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]; ts={"kurz":ui.get('taggeld_kurz',14.70),"mittel":ui.get('taggeld_mittel',29.40),"lang":ui.get('taggeld_lang',29.40)}
    story.append(Paragraph(f"Fahrtenbuch Jahresübersicht {jahr}",styles['Heading2'])); story.append(Spacer(1,8*mm))
    fzgL=[f"{r.get('bezeichnung','')} ({r.get('kennzeichen','')})" for _,r in fzg_df.iterrows()]
    for t in [f"Name: {ui.get('name','')}",f"PNR: {ui.get('pnr','')}",f"Wohnort: {ui.get('wohnort','')}",f"Dienstort: {ui.get('dienstort','')}",f"Entfernung: {int(ui.get('entfernung',0) or 0)} km",f"Fahrzeug(e): {', '.join(fzgL)}"]: story.append(Paragraph(t,styles['Normal']))
    story.append(Spacer(1,10*mm))
    h=["Monat","gefahrene km","km-Geld","Taggeld"]; sh=["","dienstl.","privat","EUR","EUR"]; data=[h,sh]; kgs=float(ui.get('km_geld',0.42)); td=0; tp=0; tkg=0; ttg=0
    for i,mn in enumerate(monate):
        mk=(jahr,i+1)
        if mk in gdata:
            df=gdata[mk]["data"]
            if df.empty: continue
            sd=int(df["km_d"].sum()); sp=int(df["km_p"].sum()); stg=sum(float(berechne_taggeld(int(r["dauer"].split(':')[0])*60+int(r["dauer"].split(':')[1]),ts).replace(',','.')) for _,r in df.iterrows())
            td+=sd; tp+=sp; tkg+=(sd+sp)*kgs; ttg+=stg; data.append([mn,sd,sp,f"{(sd+sp)*kgs:.2f}".replace('.',','),f"{stg:.2f}".replace('.',',')])
    data.append(["Summen",td,tp,f"{tkg:.2f}".replace('.',','),f"{ttg:.2f}".replace('.',',')])
    t=Table(data,colWidths=[30*mm,25*mm,25*mm,30*mm,30*mm],repeatRows=2)
    t.setStyle(TableStyle([("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8),("ALIGN",(0,0),(-1,-1),"CENTER"),("GRID",(0,0),(-1,-1),0.25,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("SPAN",(1,0),(2,0)),("LINEBELOW",(0,-1),(-1,-1),1.5,colors.black)]))
    story.append(t); story.append(Spacer(1,20*mm))
    vks={}; 
    for mk,md in gdata.items():
        df=md['data']
        if not df.empty:
            for fid,row in df.groupby('fahrzeug_id')[['km_d','km_p']].sum().iterrows():
                if fid not in vks: vks[fid]={'km_d':0,'km_p':0}
                vks[fid]['km_d']+=int(row['km_d']); vks[fid]['km_p']+=int(row['km_p'])
    data=[["Fahrzeug","Kennzeichen","dienstl."]]; tkd=0
    for fid,kms in vks.items():
        if not fzg_df[fzg_df['id']==fid].empty: vi=fzg_df[fzg_df['id']==fid].iloc[0]; data.append([vi['bezeichnung'],vi['kennzeichen'],f"{kms['km_d']}"]); tkd+=kms['km_d']
    data.append(["Summen","",f"{tkd}"])
    vt=Table(data,colWidths=[33*mm,20*mm,20*mm]); vt.setStyle(TableStyle([("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),("ALIGN",(0,0),(-1,-1),"CENTER"),("GRID",(0,0),(-1,-1),0.25,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("LINEBELOW",(0,-1),(-1,-1),1.5,colors.black)]))
    story.append(vt); doc.build(story); buf.seek(0); return buf

# ==========================================
# 4. STREAMLIT APP & LOGIK
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in']=False; st.session_state['username']=""

if not st.session_state['logged_in']:
    st.title("🚗 Fahrtenbuch Login")
    tab1,tab2,tab3=st.tabs(["Anmelden","Registrieren","Passwort vergessen"])
    with tab1:
        u=st.text_input("Benutzername",key="login_user"); p=st.text_input("Passwort",type="password",key="login_pw")
        if st.button("Login",type="primary"):
            s,ud=verify_user(u,p)
            if s:
                if ud.get('force_pw_change',False): st.session_state['temp_user_data']=ud; st.session_state['force_pw_change_flow']=True; st.rerun()
                else: st.session_state['logged_in']=True; st.session_state['username']=u.strip().lower(); st.rerun()
            else: st.error("Falsche Zugangsdaten")
    if st.session_state.get('force_pw_change_flow',False):
        st.warning("🔑 Sie müssen Ihr Passwort ändern!")
        with st.form("force_change_form"):
            p1=st.text_input("Neues Passwort",type="password",key="fn1"); p2=st.text_input("Bestätigen",type="password",key="fn2")
            if st.form_submit_button("Speichern"):
                if p1!=p2: st.error("Passwörter stimmen nicht überein!")
                elif len(p1)<4: st.error("Mindestens 4 Zeichen.")
                else:
                    tu=st.session_state.get('temp_user_data'); supabase.table("users").update({"password":hashlib.sha256(p1.encode()).hexdigest(),"force_pw_change":False}).eq("username",tu['username']).execute()
                    st.session_state['force_pw_change_flow']=False; st.session_state['logged_in']=True; st.session_state['username']=tu['username']; st.success("Geändert!"); st.rerun()
        st.stop()
    with tab2:
        nu=st.text_input("Neuer Benutzer",key="reg_user"); ne=st.text_input("E-Mail",key="reg_email"); np=st.text_input("Passwort",type="password",key="reg_pw")
        if st.button("Erstellen"):
            if not ne or "@" not in ne: st.error("Ungültige E-Mail.")
            elif add_user(nu,np,ne): st.success("Erstellt! Bitte einloggen.")
            else: st.error("Name existiert bereits.")
    with tab3:
        st.info("Benutzername eingeben. Ein neues Passwort wird an die E-Mail gesendet.")
        ru=st.text_input("Benutzername",key="reset_user")
        if st.button("🔄 Anfordern"):
            if ru:
                cu=ru.strip().lower(); dbu=supabase.table("users").select("username,email").eq("username",cu).execute()
                if not dbu.data: st.error("Nicht gefunden.")
                elif not dbu.data[0].get('email'): st.error("Keine E-Mail hinterlegt.")
                else:
                    ue=dbu.data[0]['email']; npw=secrets.token_urlsafe(8); hpw=hashlib.sha256(npw.encode()).hexdigest()
                    supabase.table("users").update({"password":hpw,"force_pw_change":True}).eq("username",cu).execute()
                    if send_reset_email(ue,npw): st.success("Gesendet! (Auch Spam prüfen)")
                    else: st.error("Fehler beim Senden. Admin kontaktieren.")
    st.stop()

user=st.session_state['username']
with st.sidebar:
    st.success(f"Eingeloggt als: **{user}**")
    if st.button("Logout"): st.session_state['logged_in']=False; st.session_state['username']=""; st.rerun()

user_info=load_settings(user); fahrzeuge_df=load_fahrzeuge(user); zeitraeume_df=load_zeitraeume(user)
if "generated_months_data" not in st.session_state: st.session_state["generated_months_data"]={}
st.title("Fahrtenbuch Generator v6.0")

with st.sidebar:
    st.header("📋 Stammdaten")
    user_info['name']=st.text_input("Name",user_info.get('name',''))
    user_info['pnr']=st.text_input("PNR",user_info.get('pnr',''))
    user_info['wohnort']=st.text_input("Wohnort",user_info.get('wohnort',''))
    user_info['dienstort']=st.text_input("Dienstort",user_info.get('dienstort',''))
    user_info['entfernung']=st.number_input("Entfernung (km)",0,300,int(user_info.get('entfernung',25)))
    st.markdown("**💰 Finanzielle Eckwerte:**")
    user_info['taggeld_kurz']=st.number_input("Taggeld < 5h (EUR)",0.0,50.0,float(user_info.get('taggeld_kurz',14.70)),step=0.10,format="%.2f")
    user_info['taggeld_mittel']=st.number_input("Taggeld 5-10h (EUR)",0.0,50.0,float(user_info.get('taggeld_mittel',29.40)),step=0.10,format="%.2f")
    user_info['taggeld_lang']=st.number_input("Taggeld > 10h (EUR)",0.0,50.0,float(user_info.get('taggeld_lang',29.40)),step=0.10,format="%.2f")
    user_info['km_geld']=st.number_input("Km-Geld (EUR)",0.0,2.0,float(user_info.get('km_geld',0.42)),step=0.01,format="%.2f")
    jahr=st.number_input("Jahr",2000,2100,value=date.today().year,step=1)
    if st.button("💾 Stammdaten speichern"): save_settings(user,user_info); st.toast("Gespeichert!")

st.markdown("---"); st.subheader("🚗 Eckdaten & Keywords")
with st.expander("⛱ Urlaubswochen"):
    cU1,cU2,cU3=st.columns(3)
    with cU1: anzahl_wochen=st.slider("Anzahl Urlaubswochen",0,4,0,key="anz_url")
    with cU2:
        if anzahl_wochen>0: verteilung_art=st.selectbox("Verteilung",["1x4 Wochen","2x2 Wochen","4x1 Woche"],key="vert_url")
    with cU3:
        if anzahl_wochen>0: start_woche_1=st.date_input("Start 1. Woche",value=date(jahr,4,1),key="start_w1")
    if anzahl_wochen>0:
        st.markdown("**Private KM Urlaub:**"); cU4,cU5,cU6=st.columns(3)
        with cU4: fahrzeug_optionen={row['bezeichnung']:row['id'] for _,row in fahrzeuge_df.iterrows()}; urlaub_fahrzeug=st.selectbox("Urlaubs-FZ",options=list(fahrzeug_optionen.keys()),key="url_fzg")
        with cU5: urlaub_km_min=st.number_input("Min KM/Tag",0,500,30,step=5,key="url_km_min")
        with cU6: urlaub_km_max=st.number_input("Max KM/Tag",0,500,80,step=5,key="url_km_max")

cW1,cW2=st.columns(2)
with cW1: w_wende=st.slider("Wahrsch. Dienstfahrt WE/Feiertag (%)",0,100,10,key="w_we")
with cW2: st.info("Rest = Privatfahrten.")

cA,cB,cC,cD=st.columns(4)
with cA:
    modus=st.radio("Modus",["Einzelner Monat","Ganzes Jahr"],key="modus_sel")
    monat_name=st.selectbox("Monat",["Jänner","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"],index=date.today().month-1,disabled=(modus=="Ganzes Jahr"))
    name2num={n:i+1 for i,n in enumerate(["Jänner","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"])}; monat=name2num[monat_name]
with cB: d_fahrt=st.slider("Ø Fahrten/Woche",1,10,4)
with cC: p_strecke=st.slider("Ø Privat-Km FE/SO",10,500,50)
with cD: w_werktag=st.slider("Wahrsch. Dienstfahrt Werktag (%)",0,100,75)

cKM1,cKM2=st.columns(2)
with cKM1: target_km_min=st.number_input("Ø Dienst-KM Min",0,5000,1650,step=50)
with cKM2: target_km_max=st.number_input("Ø Dienst-KM Max",0,5000,2000,step=50)

cF1,cF2=st.columns(2)
with cF1: w_feiertag=st.slider("Wahrsch. Dienstfahrt Feiertag/Urlaub (%)",0,100,5,key="w_fei")
with cF2: st.info("Rest = Privatfahrten.")

st.markdown("---"); st.subheader("📁 Optionale Excel Uploads")
cU1,cU2,cU3=st.columns(3)
fzg_xlsx=cU1.file_uploader("Fahrzeuge.xlsx",type=["xlsx"],key="upl_fzg"); zeit_xlsx=cU2.file_uploader("Zeiträume.xlsx",type=["xlsx"],key="upl_zeit"); kw_xlsx=cU3.file_uploader("Keywords.xlsx",key="upl_kw")

keyword_text=st.text_area("Keywords (Ort:Zweck1,Zweck2)",value="Straßwalchen:Büro\nOberhofen am Irrsee:KB\nStraßwalchen:Schaden,Angebot\nMondsee:Antrag,KFZ\nNeumarkt:Angebot,KFZ\nHenndorf:Angebot,KB\nZell am Moos:KB,Schaden\nKöstendorf:Angebot,KB\nFrankenmarkt:Angebot,KFZ\nEugendorf:KFZ,Angebot\nMattighofen:KFZ,Angebot\nObertrum:KFZ\nSeekirchen:Angebot,KB\nLochen:KB,Angebot\nFriedburg:Angebot,KB\nVöcklamarkt:KFZ,Angebot\nSt. Georgen:Angebot,Schaden\nSt. Gilgen:Angebot\nUnterach:KB\nOberwang:Angebot\nKirchberg:Antrag,KB\nFornach:Angebot\nSalzburg:Schaden,Angebot\nMunderfing:KB\nSeeham:KB\nHof bei Salzburg:KFZ\nLamprechtshausen:Schaden\nOberndorf:KB\nHallwang:Angebot,KB\nSchachen:Antrag\nVöcklabruck:Angebot")
keywords=pd.DataFrame(columns=["Ort","Zweck"])
if kw_xlsx is None and keyword_text:
    kl=[]
    for line in keyword_text.strip().split('\n'):
        p=line.split(':')
        if len(p)==2:
            ort=p[0].strip(); zwecke=[z.strip() for z in p[1].split(',')]
            for z in zwecke: kl.append({"Ort":ort,"Zweck":z})
    keywords=pd.DataFrame(kl)

def process_fahrzeuge(file):
    df=normalize_df_cols(pd.read_excel(file)); df=coalesce(df,["id","fahrzeug_id"],"id"); df=coalesce(df,["bezeichnung","fahrzeug","name","modell"],"bezeichnung"); df=coalesce(df,["kennzeichen","kennz"],"kennzeichen"); df=coalesce(df,["start_km_vorjahr","startkmvorjahr","start_km","startkm","vorjahr_km","endkilometer_2023","endkilometer_2023_"],"start_km_vorjahr"); df=coalesce(df,["privat_km_min","privatkmmin","min_privat_km"],"privat_km_min"); df=coalesce(df,["privat_km_max","privatkmmax","max_privat_km"],"privat_km_max")
    if "start_km_vorjahr" not in df.columns: df["start_km_vorjahr"]=0
    if "privat_km_min" not in df.columns: df["privat_km_min"]=5  
    if "privat_km_max" not in df.columns: df["privat_km_max"]=20 
    df["id"]=pd.to_numeric(df["id"],errors="coerce").astype("Int64"); df["start_km_vorjahr"]=pd.to_numeric(df["start_km_vorjahr"],errors="coerce").fillna(0).astype(int); df["privat_km_min"]=pd.to_numeric(df["privat_km_min"],errors="coerce").fillna(5).astype(int); df["privat_km_max"]=pd.to_numeric(df["privat_km_max"],errors="coerce").fillna(20).astype(int)
    return df

def process_zeitraeume(file,fzg_df):
    raw=pd.read_excel(file); raw=drop_empty_rows(raw); df=normalize_df_cols(raw); df=coalesce(df,["fahrzeug_id","id","kfz_id"],"fahrzeug_id"); df=coalesce(df,["kennzeichen","kennz"],"kennzeichen"); df=coalesce(df,["bezeichnung","fahrzeug","name","modell"],"bezeichnung"); df=coalesce(df,["von","beginn","start","from"],"von"); df=coalesce(df,["bis","ende","end","to"],"bis")
    for col in ["von","bis"]:
        if col in df.columns: df[col]=pd.to_datetime(df[col],errors="coerce",dayfirst=True)
    if "fahrzeug_id" in df.columns:
        ic=pd.to_numeric(df["fahrzeug_id"],errors="coerce")
        if ic.notna().mean()<0.5:
            m=pd.Series([pd.NA]*len(df),dtype="Int64")
            if "kennzeichen" in df.columns and "kennzeichen" in fzg_df.columns: mk=df["kennzeichen"].astype(str).str.strip().str.upper(); fk=fzg_df.set_index(fzg_df["kennzeichen"].astype(str).str.strip().str.upper())["id"]; m=mk.map(fk).astype("Int64")
            if m.isna().any() and "bezeichnung" in df.columns: be=df["bezeichnung"].astype(str).str.strip().str.upper(); fk=fzg_df.set_index(fzg_df["bezeichnung"].astype(str).str.strip().str.upper())["id"]; m2=be.map(fk).astype("Int64"); m=m.fillna(m2)
            df["fahrzeug_id"]=m
        else: df["fahrzeug_id"]=ic.astype("Int64")
    else:
        m=pd.Series([pd.NA]*len(df),dtype="Int64")
        if "kennzeichen" in df.columns and "kennzeichen" in fzg_df.columns: mk=df["kennzeichen"].astype(str).str.strip().str.upper(); fk=fzg_df.set_index(fzg_df["kennzeichen"].astype(str).str.strip().str.upper())["id"]; m=mk.map(fk).astype("Int64")
        if m.isna().any() and "bezeichnung" in df.columns: be=df["bezeichnung"].astype(str).str.strip().str.upper(); fk=fzg_df.set_index(fzg_df["bezeichnung"].astype(str).str.strip().str.upper())["id"]; m2=be.map(fk).astype("Int64"); m=m.fillna(m2)
        df["fahrzeug_id"]=m
    keep=[c for c in ["fahrzeug_id","von","bis"] if c in df.columns]; df=df[keep].dropna(subset=["fahrzeug_id","von","bis"]).reset_index(drop=True)
    return df

if fzg_xlsx is not None: fahrzeuge_df=process_fahrzeuge(fzg_xlsx)
if zeit_xlsx is not None and not fahrzeuge_df.empty: zeitraeume_df=process_zeitraeume(zeit_xlsx,fahrzeuge_df)
if kw_xlsx is not None:
    df=normalize_df_cols(pd.read_excel(kw_xlsx)); df=coalesce(df,["ort","ziel","stadt"],"ort"); df=coalesce(df,["zweck","grund"],"zweck")
    if "ort" in df.columns: df.rename(columns={"ort":"Ort"},inplace=True)
    if "zweck" in df.columns: df.rename(columns={"zweck":"Zweck"},inplace=True)
    keywords=df; st.info("✅ Keywords aus Excel geladen.")

if fahrzeuge_df.empty: fahrzeuge_df=pd.DataFrame(columns=["id","bezeichnung","kennzeichen","start_km_vorjahr","privat_km_min","privat_km_max"])
if zeitraeume_df.empty: zeitraeume_df=pd.DataFrame(columns=["fahrzeug_id","von","bis"])

st.subheader("📝 Fahrzeuge & Zeiträume")
cF1,cF2=st.columns(2)
with cF1: fahrzeuge_df=st.data_editor(fahrzeuge_df,num_rows="dynamic",key="ed_fzg",use_container_width=True)
with cF2: zeitraeume_df=st.data_editor(zeitraeume_df,num_rows="dynamic",key="ed_zeit",use_container_width=True)
if st.button("💾 Fahrzeuge & Zeiträume speichern"): save_fahrzeuge(user,fahrzeuge_df); save_zeitraeume(user,zeitraeume_df); st.toast("Gespeichert!")

st.markdown("---"); wohnort_clean=extrahiere_ort(user_info.get('wohnort','Oberhofen am Irrsee')); dienstort_clean=extrahiere_ort(user_info.get('dienstort','Thalgau'))

ready=(not fahrzeuge_df.empty and not zeitraeume_df.empty and not keywords.empty)
cG1,cG2=st.columns([1,1])
gen_btn=cG1.button(f"🚀 Fahrten für {'Ganzes Jahr' if modus=='Ganzes Jahr' else monat_name} {jahr} generieren",type="primary",disabled=not ready)
clear_btn=cG2.button("🗑️ Alle generierten Daten löschen")
if clear_btn: st.session_state["fahrten_df"]=None; st.session_state["generated_months_data"]={}; st.rerun()

if gen_btn:
    fahrzeug_optionen={row['bezeichnung']:row['id'] for _,row in fahrzeuge_df.iterrows()}; vacation_days=set()
    anzahl_wochen=st.session_state.get('anz_url',0)
    if anzahl_wochen>0:
        verteilung=st.session_state.get('vert_url','4x1 Woche'); sw1g=st.session_state.get('start_w1',date(jahr,4,1)); sw1=date(jahr,sw1g.month,sw1g.day)
        if verteilung=="1x4 Wochen":
            for j in range(4*7): vacation_days.add(sw1+timedelta(days=j))
        elif verteilung=="2x2 Wochen":
            for i in range(2): bsd=sw1+timedelta(weeks=i*26)
                for j in range(2*7): vacation_days.add(bsd+timedelta(days=j))
        elif verteilung=="4x1 Woche":
            for i in range(4): bsd=sw1+timedelta(weeks=i*13)
                for j in range(7): vacation_days.add(bsd+timedelta(days=j))
    monate_zum_gen=list(range(1,13)) if modus=="Ganzes Jahr" else [monat]
    pb=st.progress(0,text="Generiere..."); cur_km={row['id']:row['start_km_vorjahr'] for _,row in fahrzeuge_df.iterrows()}; priv_rng={row['id']:(int(row['privat_km_min']),int(row['privat_km_max'])) for _,row in fahrzeuge_df.iterrows()}
    for i,mk in enumerate(monate_zum_gen):
        pb.progress((i+1)/len(monate_zum_gen),text=f"Monat {mk}..."); tage=pd.date_range(date(jahr,mk,1),date(jahr,mk,calendar.monthrange(jahr,mk)[1]),freq="D"); out=[]; hol=austria_holidays(jahr); rng=np.random.default_rng(); p_fei=st.session_state.get('w_fei',5)/100.0; spec_done=False
        for t in tage:
            route="Keine Fahrt"; km_d=0; km_p=0; abf="00:00"; ank="00:00"; dauer="00:00"; fzg_id=None; fzg_name="Kein Fahrzeug"; ts=pd.Timestamp(t.date())
            gfa=pd.to_numeric(zeitraeume_df[(zeitraeume_df["von"]<=ts)&(zeitraeume_df["bis"]>=ts)]["fahrzeug_id"],errors="coerce").dropna().astype(int).tolist()
            if gfa: fzg_id=rng.choice(gfa); fzg_name=fahrzeuge_df[fahrzeuge_df["id"]==fzg_id]["bezeichnung"].values[0]
            if t.weekday()==0: spec_done=False
            is_sat=t.weekday()==5; is_sun=t.weekday()==6; is_hol=t.date() in hol; is_vac=(t.date() in vacation_days)
            if is_hol or is_vac:
                if rng.random()<p_fei:
                    ns=rng.integers(1,3); sk=keywords.sample(min(ns,len(keywords))); rs=[f"{r['Ort']} ({r['Zweck']})" for _,r in sk.iterrows()]; fr=[wohnort_clean]+rs+[wohnort_clean]
                    km_d=rng.integers(15,25)+sum(rng.integers(10,25) for _ in range(ns)); km_p=0; prefix="Feiertag: " if is_hol else "Urlaub: "; route=prefix+" - ".join(fr)
                    fm=int(km_d/80*60); pm=int(rng.integers(20,60)); dm=fm+pm; sm=int(np.clip(rng.normal(480,20),420,540)); ad=datetime.combine(t.date(),datetime.min.time())+timedelta(minutes=sm); ak=ad+timedelta(minutes=int(dm)); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{dm//60:02d}:{dm%60:02d}"
                else:
                    if is_vac:
                        ufn=st.session_state.get('url_fzg',''); ufid=fahrzeug_optionen.get(ufn,None)
                        if ufid is not None and ufid in gfa: fzg_id=ufid; fzg_name=fahrzeuge_df[fahrzeuge_df["id"]==fzg_id]["bezeichnung"].values[0]; km_p=rng.integers(st.session_state.get('url_km_min',30),st.session_state.get('url_km_max',80)); route="Urlaub"
                        else:
                            if fzg_id in priv_rng: km_p=rng.integers(priv_rng[fzg_id][0],priv_rng[fzg_id][1])
                            else: km_p=rng.integers(5,21); route="Urlaub (FZ nicht verfügbar)"
                    else:
                        if fzg_id in priv_rng: km_p=rng.integers(priv_rng[fzg_id][0],priv_rng[fzg_id][1])
                        else: km_p=rng.integers(5,21); route="Feiertag"
                    km_d=0; sh=int(rng.integers(9,18)); ft=int(rng.integers(15,45)); ad=datetime.combine(t.date(),datetime.min.time())+timedelta(hours=sh); ak=ad+timedelta(minutes=ft); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{ft//60:02d}:{ft%60:02d}"
            elif is_sat:
                if rng.random()<0.4:
                    km_d=rng.integers(25,55); km_p=0; ns=rng.integers(1,2); sk=keywords.sample(min(ns,len(keywords))); rs=[f"{r['Ort']} ({r['Zweck']})" for _,r in sk.iterrows()]; fr=[wohnort_clean]+rs+[wohnort_clean]; route=" - ".join(fr)
                    fm=int(km_d/80*60); pm=int(rng.integers(15,30)); dm=fm+pm; ad=datetime.combine(t.date(),datetime.min.time())+timedelta(hours=9); ak=ad+timedelta(minutes=int(dm)); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{dm//60:02d}:{dm%60:02d}"
            elif is_sun:
                if fzg_id in priv_rng: km_p=rng.integers(priv_rng[fzg_id][0],priv_rng[fzg_id][1])
                else: km_p=rng.integers(5,21)
                km_d=0; route="Sonntag"; sh=int(rng.integers(9,18)); ft=int(rng.integers(15,45)); ad=datetime.combine(t.date(),datetime.min.time())+timedelta(hours=sh); ak=ad+timedelta(minutes=ft); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{ft//60:02d}:{ft%60:02d}"
            else:
                cw=t.isocalendar()[1]; td=0 if cw%2==1 else 1
                if t.weekday()==td and not spec_done:
                    spec_done=True; km_p=int(user_info.get('entfernung',25)); rp=[wohnort_clean,f"{dienstort_clean} (Büro)"]; ns=rng.integers(1,4); sk=keywords.sample(min(ns,len(keywords)))
                    for _,r in sk.iterrows(): rp.append(f"{r['Ort']} ({r['Zweck']})")
                    rp.append(wohnort_clean); route=" - ".join(rp); km_d=int(km_p)+sum(rng.integers(15,35) for _ in range(ns))
                    sm=int(np.clip(rng.normal(480,5),470,490)); ad=datetime.combine(t.date(),datetime.min.time())+timedelta(minutes=sm); eh=int(rng.integers(17,23)); em=int(rng.integers(0,60)); ak=datetime.combine(t.date(),datetime.min.time())+timedelta(hours=eh,minutes=em); dm=int((ak-ad).total_seconds()/60); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{dm//60:02d}:{dm%60:02d}"
                elif rng.random()<(w_werktag/100.0):
                    if w_werktag>=90: ns=rng.integers(1,3)
                    elif w_werktag>=70: ns=rng.integers(1,4)
                    elif w_werktag>=50: ns=rng.integers(2,4)
                    else: ns=rng.integers(2,5)
                    sk=keywords.sample(min(ns,len(keywords))); rs=[f"{r['Ort']} ({r['Zweck']})" for _,r in sk.iterrows()]; fr=[wohnort_clean]+rs+[wohnort_clean]; route=" - ".join(fr); km_d=sum(rng.integers(15,35) for _ in range(ns)); km_p=0
                    sm=int(np.clip(rng.normal(480,5),470,490)); ad=datetime.combine(t.date(),datetime.min.time())+timedelta(minutes=sm); eh=int(rng.integers(17,23)); em=int(rng.integers(0,60)); ak=datetime.combine(t.date(),datetime.min.time())+timedelta(hours=eh,minutes=em); dm=int((ak-ad).total_seconds()/60); abf=ad.strftime("%H:%M"); ank=ak.strftime("%H:%M"); dauer=f"{dm//60:02d}:{dm%60:02d}"
            abfahrt_km=cur_km.get(fzg_id,0) if fzg_id is not None else 0
            out.append({"datum":t.date(),"fahrzeug_id":fzg_id,"fahrzeug":fzg_name,"route":route,"km_d":km_d,"km_p":km_p,"abf":abf,"ank":ank,"dauer":dauer,"abfahrt_km":abfahrt_km})
            if fzg_id is not None and (km_d>0 or km_p>0): cur_km[fzg_id]+=km_d+km_p
        df=pd.DataFrame(out).sort_values(["datum"]).reset_index(drop=True)
        if not df.empty and target_km_max>0:
            ckd=df["km_d"].sum()
            if ckd>0 and not(target_km_min<=ckd<=target_km_max):
                tk=(target_km_min+target_km_max)/2; sf=tk/ckd; df['km_d']=df.apply(lambda r:int(r['km_d']*sf) if r['km_d']>0 else 0,axis=1)
                msk={fid:km-df[df['fahrzeug_id']==fid][['km_d','km_p']].sum().sum() for fid,km in cur_km.items()}; cr=[]
                for _,row in df.iterrows(): fid=row['fahrzeug_id']
                if fid is not None: ak=msk.get(fid,0); crr=row.to_dict(); crr['abfahrt_km']=ak; cr.append(crr); msk[fid]+=row['km_d']+row['km_p']
                else: cr.append(row.to_dict())
                df=pd.DataFrame(cr)
                for fid,km in msk.items(): cur_km[fid]=km
        st.session_state["generated_months_data"][(jahr,mk)]={"data":df,"end_km":max(cur_km.values()) if cur_km else 0}
    pb.empty(); st.success(f"Fahrten für {len(monate_zum_gen)} Monat(e) generiert.")
    lmk=(jahr,monate_zum_gen[-1]); st.session_state["fahrten_df"]=st.session_state["generated_months_data"][lmk]["data"]; save_fahrten_to_db(user,st.session_state["generated_months_data"]); st.toast("In Cloud gespeichert!")

df=st.session_state.get("fahrten_df")
if df is not None:
    rf=scan_for_red_flags(df,jahr,monat)
    if rf:
        st.error("⚠️ Plausibilitätsprüfung fehlgeschlagen:"); 
        for f in rf: st.warning(f)
        st.markdown("---")
    st.subheader("✏️ Fahrten anpassen"); edited_df=st.data_editor(df,num_rows="dynamic",use_container_width=True,key="edit_fahrten")
    cs,ca=st.columns([1,1])
    with cs:
        if st.button("💾 Änderungen speichern"):
            edited_df=edited_df.sort_values(by="datum").reset_index(drop=True); update_month_in_db(user,jahr,monat,edited_df); st.session_state["generated_months_data"][(jahr,monat)]["data"]=edited_df; st.session_state["fahrten_df"]=edited_df; st.toast("Gespeichert!",icon="✅"); st.rerun()
    with ca:
        if st.button("➕ Fahrt hinzufügen"): st.session_state['show_add_form']=True
    if st.session_state.get('show_add_form',False):
        with st.form("add_trip_form"):
            c1,c2,c3=st.columns(3)
            with c1: nd=st.date_input("Datum")
            with c2: nf=st.selectbox("Fahrzeug",fahrzeuge_df['bezeichnung'].tolist())
            with c3: nr=st.text_input("Reiseweg")
            c4,c5,c6,c7=st.columns(4)
            with c4: nkd=st.number_input("Dienst-KM",0,999,0)
            with c5: nkp=st.number_input("Privat-KM",0,999,0)
            with c6: nabf=st.text_input("Abfahrt",value="08:00")
            with c7: nank=st.text_input("Ankunft",value="17:00")
            if st.form_submit_button("✅ Einfügen"):
                try:
                    h1,m1=map(int,nabf.split(':')); h2,m2=map(int,nank.split(':')); dmin=(h2*60+m2)-(h1*60+m1); ds=f"{dmin//60:02d}:{dmin%60:02d}"
                except: ds="00:00"
                fr=fahrzeuge_df[fahrzeuge_df['bezeichnung']==nf]; fid=fr['id'].values[0] if not fr.empty else 1; lk=int(edited_df['abfahrt_km'].max()) if not edited_df.empty else 0
                nrow={"datum":nd,"fahrzeug_id":int(fid),"fahrzeug":nf,"route":nr,"km_d":int(nkd),"km_p":int(nkp),"abf":nabf,"ank":nank,"dauer":ds,"abfahrt_km":lk}
                ndf=pd.concat([edited_df,pd.DataFrame([nrow])],ignore_index=True).sort_values(by="datum").reset_index(drop=True)
                update_month_in_db(user,jahr,monat,ndf); st.session_state["generated_months_data"][(jahr,monat)]["data"]=ndf; st.session_state["fahrten_df"]=ndf; st.session_state['show_add_form']=False; st.rerun()
    st.markdown("---"); st.subheader("📄 PDF-Export")
    if modus=="Ganzes Jahr" and st.session_state["generated_months_data"]:
        mn=["Jänner","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]; am=sorted([k for k in st.session_state["generated_months_data"].keys()]); mfs={f"{mn[m-1]} {y}":(y,m) for y,m in am}; sms=st.selectbox("Monat für PDF",list(mfs.keys())); smj=mfs[sms]; pm,py=smj[1],smj[0]
    else: pm,py=monat,jahr
    cP1,cP2=st.columns(2)
    with cP1:
        if st.button(f"📄 Monats-PDF ({calendar.month_name[pm]} {py})"):
            if not REPORTLAB_OK: st.error("ReportLab fehlt!"); st.stop()
            buf=create_monats_pdf(st.session_state["generated_months_data"][(py,pm)]["data"],pm,py,user_info,fahrzeuge_df); st.download_button(label=f"Download {calendar.month_name[pm]} {py}",data=buf,file_name=f"Fahrtenbuch_Monat_{calendar.month_name[pm]}_{py}.pdf",mime="application/pdf")
    with cP2:
        if st.button("📊 Jahresbericht-PDF"):
            if not REPORTLAB_OK: st.error("ReportLab fehlt!"); st.stop()
            if not st.session_state["generated_months_data"]: st.warning("Keine Daten.")
            else: buf=create_jahres_pdf(st.session_state["generated_months_data"],jahr,user_info,fahrzeuge_df); st.download_button(label=f"Download Jahresbericht {jahr}",data=buf,file_name=f"Fahrtenbuch_Jahr_{jahr}.pdf",mime="application/pdf")

st.markdown("---")
if st.session_state.get("generated_months_data") and len(st.session_state["generated_months_data"])==12:
    st.subheader("🔧 Kilometerstand anpassen (HU)"); st.info("Kilometerstand anpassen und Werkstattfahrt erstellen.")
    with st.expander("Korrekturen vornehmen"):
        hu_df=pd.DataFrame(columns=["Fahrzeug","Datum der HU","Kilometerstand bei HU","Werkstattort (Dropdown)","Kundenstopps vor HU (mehrere möglich)","Zusätzliche Stopps (mehrere möglich)"])
        fo={row['bezeichnung']:row['id'] for _,row in fahrzeuge_df.iterrows()}; wo=sorted(list(keywords['Ort'].unique()))
        cc={"Fahrzeug":st.column_config.SelectboxColumn("FZ",options=list(fo.keys()),required=True),"Datum der HU":st.column_config.DateColumn("Datum",format="DD.MM.YYYY",required=True),"Kilometerstand bei HU":st.column_config.NumberColumn("KM bei HU",min_value=0,step=1,required=True),"Werkstattort (Dropdown)":st.column_config.SelectboxColumn("Werkstatt",options=wo,required=True),"Kundenstopps vor HU (mehrere möglich)":st.column_config.TextColumn("Kunden davor",max_chars=200),"Zusätzliche Stopps (mehrere möglich)":st.column_config.TextColumn("Zusätze",max_chars=200)}
        ehu=st.data_editor(hu_df,column_config=cc,num_rows="dynamic",use_container_width=True,key="hu_ed")
        if st.button("🛠️ Korrigieren"):
            if ehu.empty: st.warning("Bitte Eingaben machen.")
            else:
                cd=[]
                for _,row in ehu.iterrows():
                    if row["Fahrzeug"] in fo and row["Werkstattort (Dropdown)"]:
                        zs=[s.strip() for s in str(row.get("Zusätzliche Stopps (mehrere möglich)","")).split(',') if s.strip()]; sv=[s.strip() for s in str(row.get("Kundenstopps vor HU (mehrere möglich)","")).split(',') if s.strip()]
                        cd.append({"fid":fo[row["Fahrzeug"]],"datum":pd.to_datetime(row["Datum der HU"]).date(),"km":int(row["Kilometerstand bei HU"]),"wo":row["Werkstattort (Dropdown)"],"zs":zs,"sv":sv})
                if not cd: st.error("Ungültig.")
                else:
                    with st.spinner("Verarbeite..."):
                        rng=np.random.default_rng(); rw=str(user_info.get('wohnort','')); wc=rw.split(',')[-1].strip() if ',' in rw else rw
                        atv={}
                        for fid in fo.values():
                            atf=[]
                            for mk,md in st.session_state["generated_months_data"].items():
                                df=md["data"]; fd=df[df['fahrzeug_id']==fid].copy()
                                if not fd.empty: atf.append(fd)
                            if atf: atv[fid]=pd.concat(atf).sort_values(by="datum").reset_index(drop=True)
                        for c in cd:
                            fid=c["fid"]; hd=c["datum"]; kmh=c["km"]; wo=c["wo"]; zs=c["zs"]; sv=c["sv"]
                            if fid not in atv: st.warning(f"Keine Fahrten für FZ ID {fid}."); continue
                            fn=fahrzeuge_df[fahrzeuge_df['id']==fid]['bezeichnung'].iloc[0]; st.write(f"**Korrektur:** {fn}")
                            adf=atv[fid]; sk=fahrzeuge_df[fahrzeuge_df['id']==fid]['start_km_vorjahr'].iloc[0]; bh=adf[adf['datum']<hd].copy()
                            if not bh.empty:
                                csh=sk+(bh['km_d']+bh['km_p']).sum(); tks=kmh-sk
                                if csh>sk: sf=tks/(csh-sk); st.write(f"  - Skalierung: {sf:.4f}"); bh['km_d']=(bh['km_d']*sf).astype(int); bh['km_p']=(bh['km_p']*sf).astype(int)
                                else: st.write("  - Keine Skalierung.")
                                for _,ct in bh.iterrows():
                                    mk=(ct['datum'].year,ct['datum'].month); od=st.session_state["generated_months_data"][mk]["data"]; oi=od[(od['datum']==ct['datum'])&(od['fahrzeug_id']==fid)].index[0]
                                    st.session_state["generated_months_data"][mk]["data"].at[oi,'km_d']=ct['km_d']; st.session_state["generated_months_data"][mk]["data"].at[oi,'km_p']=ct['km_p']
                            st.write(f"  - Erstelle HU-Fahrt am {hd.strftime('%d.%m.%Y')}.")
                            hmk=(hd.year,hd.month); hmd=st.session_state["generated_months_data"][hmk]["data"]; hti=hmd[(hmd['datum']==hd)&(hmd['fahrzeug_id']==fid)].index
                            if hti.empty: hti=hmd[hmd['datum']==hd].index
                            ts=len(sv)+1+len(zs); hkm=rng.integers(20,40)+ts*rng.integers(10,20)
                            rp=[wc]
                            for s in sv: rp.append(f"{s} ({rng.choice(['Angebot','Schaden','KB'])})")
                            rp.append(f"{wo} (HU)"); rp.append(wc); hr=" - ".join(rp); hdm=90+ts*20; had=datetime.combine(hd,datetime.min.time())+timedelta(hours=8,minutes=30); hak=had+timedelta(minutes=hdm)
                            if not hti.empty:
                                tui=hti[0]; st.session_state["generated_months_data"][hmk]["data"].at[tui,'fahrzeug_id']=fid; st.session_state["generated_months_data"][hmk]["data"].at[tui,'fahrzeug']=fn; st.session_state["generated_months_data"][hmk]["data"].at[tui,'km_d']=hkm; st.session_state["generated_months_data"][hmk]["data"].at[tui,'km_p']=0; st.session_state["generated_months_data"][hmk]["data"].at[tui,'route']=hr; st.session_state["generated_months_data"][hmk]["data"].at[tui,'abf']=had.strftime("%H:%M"); st.session_state["generated_months_data"][hmk]["data"].at[tui,'ank']=hak.strftime("%H:%M"); st.session_state["generated_months_data"][hmk]["data"].at[tui,'dauer']=f"{hdm//60:02d}:{hdm%60:02d}"
                                st.write(f"    - Fahrt ersetzt ({hkm} km).")
                            else: st.error(f"    - Fehler: Kein Tag gefunden.")
                        with st.spinner("Berechne Kilometerstände neu..."):
                            ckr={row['id']:row['start_km_vorjahr'] for _,row in fahrzeuge_df.iterrows()}; smk=sorted(st.session_state["generated_months_data"].keys())
                            for mk in smk:
                                d=st.session_state["generated_months_data"][mk]["data"].sort_values(by="datum").reset_index(drop=True); cr=[]
                                for _,row in d.iterrows(): fid=row['fahrzeug_id']
                                if fid is not None: akn=ckr.get(fid,0); crr=row.to_dict(); crr['abfahrt_km']=akn; cr.append(crr); ckr[fid]+=row['km_d']+row['km_p']
                                else: cr.append(row.to_dict())
                                st.session_state["generated_months_data"][mk]["data"]=pd.DataFrame(cr)
                        lmk=sorted(st.session_state["generated_months_data"].keys())[-1]; st.session_state["fahrten_df"]=st.session_state["generated_months_data"][lmk]["data"]; st.success("Erfolgreich korrigiert!"); save_fahrten_to_db(user,st.session_state["generated_months_data"]); st.toast("Gespeichert!"); st.rerun()
else: st.info("Für HU-Korrektur muss erst das ganze Jahr generiert werden.")

ADMIN_USERS=["christian mayerhofer"]
if user in ADMIN_USERS:
    st.markdown("---")
    with st.expander("👑 Admin-Bereich"):
        st.subheader("Passwort zurücksetzen")
        r=supabase.table("users").select("username").execute(); au=[x["username"] for x in r.data]
        if au:
            su=st.selectbox("User",au,key="adm_sel"); np=st.text_input("Neues PW",type="password",key="adm_pw")
            if st.button("🔐 Ändern"):
                if np: supabase.table("users").update({"password":hashlib.sha256(np.encode()).hexdigest(),"force_pw_change":True}).eq("username",su).execute(); st.success(f"PW für {su} geändert.")
                else: st.warning("PW eingeben.")
            st.markdown("---"); st.subheader("📧 E-Mail verwalten")
            uai=supabase.table("users").select("username,email").eq("username",su).execute(); ce=uai.data[0].get('email','') if uai.data else ''
            ne=st.text_input("E-Mail",value=ce,key="adm_email")
            if st.button("💾 E-Mail speichern"):
                if ne and "@" in ne: supabase.table("users").update({"email":ne.strip().lower()}).eq("username",su).execute(); st.success("Gespeichert."); st.rerun()
                else: st.error("Ungültige E-Mail.")
            st.markdown("---"); st.subheader(f"📁 Daten von {su}"); us=load_settings(su); uf=load_fahrzeuge(su)
            c1,c2=st.columns(2)
            with c1: st.write("**Stammdaten:**"); st.json(us) if us else st.info("Leer.")
            with c2: st.write("**Fahrzeuge:**"); st.dataframe(uf) if not uf.empty else st.info("Leer.")
