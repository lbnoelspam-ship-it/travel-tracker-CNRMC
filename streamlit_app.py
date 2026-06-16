import streamlit as st
from datetime import date, timedelta
import pandas as pd
import urllib.request
import json
import math
import re
import os
import requests

# --- PAGE SETUP ---
st.set_page_config(
    page_title="AI Travel Estimator & Corporate Tracker",
    page_icon="✈️",
    layout="wide",
)

st.title("✈️ Advanced AI Travel Estimator & Multi-Leg Tracker")
st.markdown("Enter localized travel itineraries. Rates are driven dynamically by your uploaded `rates.csv` file.")

# ─── PERSISTENT DATA STORAGE (CSV LEDGER) ─────────────────────────────

LEDGER_FILE = "trip_ledger.csv"

def load_ledger():
    """Loads historical trips from the persistent CSV file."""
    if os.path.exists(LEDGER_FILE):
        try:
            return pd.read_csv(LEDGER_FILE).to_dict('records')
        except Exception:
            return []
    return []

def save_ledger(data):
    """Saves trips permanently to the CSV file."""
    df = pd.DataFrame(data)
    df.to_csv(LEDGER_FILE, index=False)

if "trip_database" not in st.session_state:
    st.session_state["trip_database"] = load_ledger()
if "num_legs" not in st.session_state:
    st.session_state["num_legs"] = 1

# ─── DATA INGESTION (BULLETPROOF CSV LOADER) ──────────────────────────

@st.cache_data
def load_rates(uploaded_file=None):
    """Aggressively hunts for the CSV file, or accepts a direct manual upload."""
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
    st.info("💡 **Quick Fix:** Drag and drop your `rates.csv` file below to keep working immediately.")
    
    manual_upload = st.file_uploader("Upload rates.csv here", type=['csv'])
    if manual_upload:
        FEDERAL_RATES_DB = load_rates(manual_upload)
        if FEDERAL_RATES_DB:
            st.success("✅ Rates loaded! Please interact with the dropdowns below to refresh.")
        else:
            st.stop()
    else:
        st.stop() 

LOCATIONS_LIST = sorted(list(FEDERAL_RATES_DB.keys()))

# ─── MATH, GEO, AND LIVE API UTILITIES ────────────────────────────────

def clean_to_english_ascii(text: str) -> str:
    """Removes non-English character scripts to ensure plain English strings."""
    ascii_clean = text.encode("ascii", errors="ignore").decode("ascii")
    ascii_clean = re.sub(r',\s*,', ',', ascii_clean)
    return ascii_clean.strip().strip(",")

@st.cache_data(ttl=86400)
def get_coordinates(location_query: str):
    """Fetches lat/lon and foreign status via OpenStreetMap for flight math."""
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
        return {
            "clean_name": clean_to_english_ascii(location_query.title()),
            "lat": 38.89, "lon": -77.03, 
            "is_foreign": False
        }

def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3956 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(2 * r * math.asin(math.sqrt(a)), 1)

def calculate_tiered_flight_cost(distance, is_foreign):
    """Mimics real-world airline pricing using economies of scale."""
    if not is_foreign:
        if distance < 400:     return 150.0 + (distance * 0.20)  
        elif distance < 1500:  return 200.0 + (distance * 0.12)  
        else:                  return 250.0 + (distance * 0.08)  
    else:
        if distance < 1000:    return 250.0 + (distance * 0.15)  
        elif distance < 4000:  return 400.0 + (distance * 0.08)  
        else:                  return 600.0 + (distance * 0.04)  

# IATA Mapping for Federal/Navy locations to Commercial Airports
AIRPORT_MAP = {
    "Washington, DC": "DCA", "Chesapeake, VA": "ORF", "Norfolk, VA": "ORF",
    "Lexington, VA": "ROA", "Groton, CT": "BDL", "Portsmouth, ME": "PSM",
    "Philadelphia, PA": "PHL", "New York, NY": "JFK", "Kings Bay, GA": "JAX",
    "Jacksonville, FL": "JAX", "Mayport, FL": "JAX", "Orlando, FL": "MCO",
    "Mobile, AL": "MOB", "San Diego, CA": "SAN", "Point Loma, CA": "SAN",
    "Seattle, WA": "SEA", "Everett, WA": "SEA", "Portland, OR": "PDX",
    "Pearl Harbor, HI": "HNL", "Guam": "GUM", "Rota, Spain": "XRY",
    "Deveselu, Romania": "OTP", "Bucharest, Romania": "OTP", "Poland": "WAW",
    "Singapore": "SIN", "South Korea": "ICN", "Yokosuka, Japan": "HND",
    "Sasebo, Japan": "FUK", "Yokohama, Japan": "HND",
    "Houston, Texas": "IAH", "Houston, TX": "IAH"
}

@st.cache_data(ttl=86400) 
def fetch_live_airfare(origin_name, dest_name, flight_date):
    """Hits Google Flights via SerpApi. Falls back to None if fails."""
    try:
        api_key = st.secrets.get("SERPAPI_KEY")
        if not api_key: return None
        
        o_code = origin_name if len(origin_name) == 3 and origin_name.isupper() else AIRPORT_MAP.get(origin_name, "JFK")
        d_code = dest_name if len(dest_name) == 3 and dest_name.isupper() else AIRPORT_MAP.get(dest_name, "JFK")
        
        date_str = flight_date.strftime("%Y-%m-%d")
        url = f"https://serpapi.com/search.json?engine=google_flights&departure_id={o_code}&arrival_id={d_code}&outbound_date={date_str}&currency=USD&hl=en&api_key={api_key}"
        
        res = requests.get(url)
        data = res.json()
        
        if "best_flights" in data and len(data["best_flights"]) > 0:
            return float(data["best_flights"][0]["price"])
        elif "other_flights" in data and len(data["other_flights"]) > 0:
            return float(data["other_flights"][0]["price"])
    except Exception:
        pass
    return None

# ─── SIDEBAR CONFIGURATION CONTROLS ───────────────────────────────────

with st.sidebar:
    st.header("1. Core Metadata")
    traveler_name = st.text_input("Traveler Name", placeholder="e.g. Larry")
    purpose_input = st.text_input("Purpose of Trip (Max 64 Characters)", max_chars=64, placeholder="e.g. System Integration Assessment")
    
    st.markdown("---")
    st.header("2. Base Departure Origin")
    
    origin_input = st.text_input("Starting Location", value="Houston, TX")
    origin_geo = get_coordinates(origin_input)
    if origin_geo:
        st.caption(f"✔️ Origin locked: {origin_geo['clean_name']}")

# ─── ITINERARY CONSOLE & SEAMLESS DATE TRACKER ────────────────────────

st.subheader("🗓️ Multi-Leg Destination & Date Configuration")
st.markdown("Specify destinations chronologically. Dates must sequence perfectly with no gaps or overlaps.")

col_add, col_rem, _ = st.columns([2, 2, 6])
with col_add:
    if st.button("＋ Add Next Destination Leg"):
        st.session_state["num_legs"] += 1
        st.rerun()
with col_rem:
    if st.button("➖ Remove Last Leg") and st.session_state["num_legs"] > 1:
        st.session_state["num_legs"] -= 1
        st.rerun()

raw_legs_inputs = []
date_sequencing_valid = True

for i in range(st.session_state["num_legs"]):
    st.markdown(f"##### Destination Leg #{i+1}")
    l_col1, l_col2, l_col3 = st.columns([4, 3, 3])
    
    default_start = date.today()
    if i > 0 and len(raw_legs_inputs) > i-1:
        default_start = raw_legs_inputs[i-1]["end"] + timedelta(days=1)

    with l_col1:
        leg_name = st.selectbox(f"Location", options=LOCATIONS_LIST, key=f"loc_raw_{i}", index=0)
    with l_col2:
        leg_start = st.date_input(f"Arrival Date", default_start, key=f"start_{i}")
    with l_col3:
        leg_end = st.date_input(f"Departure Date", default_start + timedelta(days=3), key=f"end_{i}")
        
    if FEDERAL_RATES_DB:
        raw_legs_inputs.append({
            "index": i,
            "name": leg_name,
            "data": FEDERAL_RATES_DB[leg_name],
            "start": leg_start,
            "end": leg_end
        })

legs_data = []
for idx, leg in enumerate(raw_legs_inputs):
    if leg["start"] > leg["end"]:
        st.error(f"❌ **Chronological Error on Leg #{idx+1}:** Arrival date cannot exceed departure date.")
        date_sequencing_valid = False
        
    if idx > 0:
        prior_end_date = raw_legs_inputs[idx-1]["end"]
        expected_start_date = prior_end_date + timedelta(days=1)
        
        if leg["start"] < expected_start_date:
            st.error(f"❌ **Timeline Overlap Error on Leg #{idx+1}:** Must start exactly on **{expected_start_date.strftime('%B %d, %Y')}**.")
            date_sequencing_valid = False
        elif leg["start"] > expected_start_date:
            st.error(f"❌ **Timeline Gap Error on Leg #{idx+1}:** Must start exactly on **{expected_start_date.strftime('%B %d, %Y')}**.")
            date_sequencing_valid = False

    leg_geo = get_coordinates(leg["name"])
    if not leg_geo:
        leg_geo = {"lat": 0.0, "lon": 0.0, "is_foreign": "GSA" not in leg["data"]["authority"]}

    legs_data.append({
        "index": leg["index"],
        "name": leg["name"],
        "lodging_rate": leg["data"]["lodging"],
        "mie_rate": leg["data"]["mie"],
        "authority": leg["data"]["authority"],
        "lat": leg_geo["lat"],
        "lon": leg_geo["lon"],
        "is_foreign": leg_geo["is_foreign"],
        "start": leg["start"],
        "end": leg["end"],
        "days": (leg["end"] - leg["start"]).days + 1
    })

# ─── FINANCIAL CALCULATIONS AND COMPILATION ───────────────────────────

if date_sequencing_valid and origin_geo and FEDERAL_RATES_DB and len(legs_data) == st.session_state["num_legs"]:
    st.markdown("---")
    st.subheader("📊 Dynamic Budget Analysis")
    
    flight_chain = [{"name": origin_geo["clean_name"], "lat": origin_geo["lat"], "lon": origin_geo["lon"], "is_foreign": origin_geo["is_foreign"], "start": date.today()}]
    for leg in legs_data:
        flight_chain.append(leg)
    flight_chain.append({"name": origin_geo["clean_name"], "lat": origin_geo["lat"], "lon": origin_geo["lon"], "is_foreign": origin_geo["is_foreign"], "start": legs_data[-1]["end"]})
    
    total_airfare_cost = 0.0
    airfare_log = []
    
    for idx in range(len(flight_chain) - 1):
        p1 = flight_chain[idx]
        p2 = flight_chain[idx+1]
        dist = haversine_miles(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        
        flight_date = p2["start"] if idx > 0 else legs_data[0]["start"]
        live_price = fetch_live_airfare(p1["name"], p2["name"], flight_date)
        
        if live_price is not None:
            total_airfare_cost += live_price
            airfare_log.append(f"Live API Data: ${live_price:,.2f}")
        else:
            is_intl_leg = p2.get("is_foreign", False) or p1.get("is_foreign", False)
            leg_flight_cost = calculate_tiered_flight_cost(dist, is_intl_leg)
            total_airfare_cost += leg_flight_cost
            airfare_log.append(f"Tiered Distance Estimate: ${leg_flight_cost:,.2f}")

    total_lodging_cost = 0.0
    total_rental_cost = 0.0
    total_per_diem_cost = 0.0
    total_misc_cost = 0.0
    
    global_start = min(l["start"] for l in legs_data)
    global_end = max(l["end"] for l in legs_data)
    
    breakdown_table_rows = []
    
    for idx, leg in enumerate(legs_data):
        leg_lodging_rate = leg["lodging_rate"]
        leg_mie_rate = leg["mie_rate"]
        leg_car_rate = round(65.0 if not leg["is_foreign"] else 95.0, 2)
        
        leg_nights = leg["days"] - 1 if idx == (len(legs_data) - 1) else leg["days"]
        total_lodging_cost += (leg_nights * leg_lodging_rate)
        total_rental_cost += (leg["days"] * leg_car_rate)
        total_misc_cost += (140.0 if leg["is_foreign"] else 90.0) + (15.0 * leg["days"])
        
        leg_pd_sum = 0.0
        
        for day_offset in range(leg["days"]):
            current_day = leg["start"] + timedelta(days=day_offset)
            if current_day == global_start or current_day == global_end:
                leg_pd_sum += (leg_mie_rate * 0.75)
            else:
                leg_pd_sum += leg_mie_rate
                
        total_per_diem_cost += leg_pd_sum
        
        breakdown_table_rows.append({
            "Travel Segment": f"Leg #{idx+1}: {leg['name']}",
            "Lodging Limit / Night": f"${leg_lodging_rate:,.2f}",
            "First/Last Day Per Diem": f"${leg_mie_rate * 0.75:,.2f}",
            "Middle Day Per Diem": f"${leg_mie_rate:,.2f}",
            "Subtotal Days": f"{leg_nights} Nights / {leg['days']} Days",
            "Governing Authority": leg["authority"]
        })

    ledger_df = pd.DataFrame([
        {"Category": "Airfare", "Estimated Cost": round(total_airfare_cost, 2), "Details": " | ".join(airfare_log)},
        {"Category": "Lodging", "Estimated Cost": round(total_lodging_cost, 2), "Details": "Sum of combined multi-leg lodging limits across dates"},
        {"Category": "Economy Rental Vehicle", "Estimated Cost": round(total_rental_cost, 2), "Details": "Rental vehicles computed across active itinerary windows"},
        {"Category": "Per Diem (M&IE)", "Estimated Cost": round(total_per_diem_cost, 2), "Details": "Calculated via strict limits specified in rates.csv"},
        {"Category": "Miscellaneous", "Estimated Cost": round(total_misc_cost, 2), "Details": "Aggregated fuel allocations, baggage costs, and local transport"}
    ])
    
    edited_df = st.data_editor(
        ledger_df,
        num_rows="fixed",
        column_config={
            "Category": st.column_config.TextColumn("Category", disabled=True),
            "Estimated Cost": st.column_config.NumberColumn("Estimated Cost", min_value=0.0, format="$%.2f"),
            "Details": st.column_config.TextColumn("Details", disabled=True),
        },
        use_container_width=True,
    )
    
    final_calculated_sum = edited_df["Estimated Cost"].sum()
    
    st.markdown("#### 📋 Comprehensive Per Diem & Lodging Rates Reference Table")
    st.table(pd.DataFrame(breakdown_table_rows)) 
        
    st.markdown(f"### **Total Multi-Leg Projected Budget:** ${final_calculated_sum:,.2f}")
    
    if st.button("💾 Commit & Log Trip to Persistent Ledger"):
        comma_locations = ", ".join([l["name"] for l in legs_data])
        
        # NOTE: .strip() has been added here to eliminate ghost spaces!
        safe_traveler = traveler_name.strip() if traveler_name.strip() else "Unknown Traveler"
        
        new_entry = {
            "Month": global_start.strftime("%B %Y"),
            "Traveler": safe_traveler,
            "Location": comma_locations,
            "Dates": f"{global_start.strftime('%m/%d')} - {global_end.strftime('%m/%d/%y')}",
            "Cost": round(final_calculated_sum, 2)
        }
        st.session_state["trip_database"].append(new_entry)
        save_ledger(st.session_state["trip_database"])
        st.success("Itinerary permanently saved to trip_ledger.csv!")
        st.rerun()

# ─── MASTER CONSOLIDATED ACCUMULATOR LEDGER ───────────────────────────

st.markdown("---")
st.subheader("📊 Centralized Travel Tracker Archive")

if st.session_state["trip_database"]:
    st.info("💡 **Interactive Ledger:** Trips with identical Months, Travelers, Locations, and Dates are automatically paired and summed. Delete rows by checking the box on the left and pressing 'Delete'.")
    
    df = pd.DataFrame(st.session_state["trip_database"])
    
    # PAIRING/MERGING LOGIC
    merged_df = df.groupby(['Month', 'Traveler', 'Location', 'Dates'], as_index=False)['Cost'].sum()
    
    edited_archive = st.data_editor(
        merged_df,
        num_rows="dynamic",
        use_container_width=True,
        key="archive_editor"
    )
    
    # EXPORT LOGIC
    csv = edited_archive.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Export Ledger to Spreadsheet (CSV)",
        data=csv,
        file_name='travel_ledger_export.csv',
        mime='text/csv',
    )
    
    col1, col2 = st.columns([2, 8])
    with col1:
        if st.button("💾 Save Archive Changes"):
            st.session_state["trip_database"] = edited_archive.dropna(how="all").to_dict('records')
            save_ledger(st.session_state["trip_database"])
            st.success("Archive updated & synced!")
            st.rerun()
    with col2:
        if st.button("❌ Wipe Entire Database"):
            st.session_state["trip_database"] = []
            if os.path.exists(LEDGER_FILE):
                os.remove(LEDGER_FILE)
            st.rerun()
else:
    st.caption("No records currently established inside the historical ledger.")
