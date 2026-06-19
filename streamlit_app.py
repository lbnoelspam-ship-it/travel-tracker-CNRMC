import streamlit as st
from datetime import date, timedelta
import pandas as pd
import urllib.request
import json
import math
import re
import os
import requests
import plotly.express as px

# --- PAGE SETUP ---
st.set_page_config(
    page_title="AI Travel Estimator & Corporate Tracker",
    page_icon="✈️",
    layout="wide",
)

st.title("✈️ Advanced AI Travel Estimator & Multi-Leg Tracker")
st.markdown("Enter localized travel itineraries. Rates are driven dynamically by your uploaded `rates.csv` file.")

# ─── PERSISTENT DATA & FORM RESET LOGIC ───────────────────────────────

LEDGER_FILE = "trip_ledger.csv"

def load_ledger():
    if os.path.exists(LEDGER_FILE):
        try:
            return pd.read_csv(LEDGER_FILE).to_dict('records')
        except Exception:
            return []
    return []

def save_ledger(data):
    df = pd.DataFrame(data)
    df.to_csv(LEDGER_FILE, index=False)

# Initialize Session States
if "trip_database" not in st.session_state:
    st.session_state["trip_database"] = load_ledger()
if "num_legs" not in st.session_state:
    st.session_state["num_legs"] = 1

# The hidden counter that forces widgets to wipe clean
if "form_key" not in st.session_state:
    st.session_state["form_key"] = 0
if "show_budget" not in st.session_state:
    st.session_state["show_budget"] = False

def hide_budget():
    st.session_state["show_budget"] = False

def reset_estimator():
    st.session_state["num_legs"] = 1
    st.session_state["form_key"] += 1
    st.session_state["show_budget"] = False

# ─── DATA INGESTION (BULLETPROOF CSV LOADER) ──────────────────────────

@st.cache_data
def load_rates(uploaded_file=None):
    df = None
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"Error reading uploaded file: {e}")
            return None
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            os.path.join(script_dir, "rates.csv"),
            os.path.join(script_dir, "Rates.csv"),
            "rates.csv",
            "Rates.csv"
        ]
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    df = pd.read_csv(path)
                    break 
                except Exception:
                    continue
                    
    if df is not None:
        try:
            df['Destination City / Country'] = df['Destination City / Country'].astype(str).str.strip()
            rates_dict = {}
            for _, row in df.iterrows():
                loc = row['Destination City / Country']
                rates_dict[loc] = {
                    "lodging": float(row['Max Lodging Rate']),
                    "mie": float(row['M&IE Rate']),
                    "authority": str(row['Governing Travel Authority'])
                }
            return rates_dict
        except Exception as e:
            st.error(f"⚠️ **Data Format Error:** Your CSV is missing required columns. {e}")
            return None
    return None 

FEDERAL_RATES_DB = load_rates()

if not FEDERAL_RATES_DB:
    st.warning("⚠️ **Server Sync Issue:** Streamlit cannot find `rates.csv` in the cloud directory.")
    manual_upload = st.file_uploader("Upload rates.csv here", type=['csv'])
    if manual_upload:
        FEDERAL_RATES_DB = load_rates(manual_upload)
        if FEDERAL_RATES_DB:
            st.success("✅ Rates loaded! Please interact with the dropdowns below to refresh.")
        else:
            st.stop()
    else:
        st.stop() 

DROPDOWN_OPTIONS = ["-- Select Destination --"] + sorted(list(FEDERAL_RATES_DB.keys()))

# ─── MATH, GEO, AND LIVE API UTILITIES ────────────────────────────────

def clean_to_english_ascii(text: str) -> str:
    ascii_clean = text.encode("ascii", errors="ignore").decode("ascii")
    ascii_clean = re.sub(r',\s*,', ',', ascii_clean)
    return ascii_clean.strip().strip(",")

@st.cache_data(ttl=86400)
def get_coordinates(location_query: str):
    if not location_query or len(location_query.strip()) < 3:
        return None
    try:
        safe_query = urllib.parse.quote(location_query.strip())
        url = f"https://nominatim.openstreetmap.org/search?q={safe_query}&format=json&addressdetails=1&limit=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'AITravelEstimatorProject/1.3'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
        if not data:
            return None
        top_match = data[0]
        address = top_match.get("address", {})
        city = address.get("city") or address.get("town") or address.get("suburb") or location_query.split(",")[0]
        state = address.get("state", "")
        country = address.get("country", "United States")
        country_code = address.get("country_code", "us").upper()
        display_name = f"{city}, {state}" if state and country_code == "US" else f"{city}, {country}"
        
        return {
            "clean_name": clean_to_english_ascii(display_name),
            "lat": float(top_match["lat"]),
            "lon": float(top_match["lon"]),
            "is_foreign": country_code != "US"
        }
    except Exception:
        return {"clean_name": clean_to_english_ascii(location_query.title()), "lat": 38.89, "lon": -77.03, "is_foreign": False}

def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3956 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(2 * r * math.asin(math.sqrt(a)), 1)

def calculate_tiered_flight_cost(distance, is_foreign):
    if not is_foreign:
        if distance < 400: return 150.0 + (distance * 0.20)  
        elif distance < 1500: return 200.0 + (distance * 0.12)  
        else: return 250.0 + (distance * 0.08)  
    else:
        if distance < 1000: return 250.0 + (distance * 0.15)  
        elif distance < 4000: return 400.0 +
