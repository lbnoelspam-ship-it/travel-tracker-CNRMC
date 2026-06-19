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
        elif distance < 4000: return 400.0 + (distance * 0.08)  
        else: return 600.0 + (distance * 0.04)  

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

def fetch_live_airfare(origin_name, dest_name, flight_date):
    try:
        api_key = st.secrets.get("SERPAPI_KEY")
        if not api_key: 
            st.toast("⚠️ ERROR: No API Key found in Streamlit Settings!")
            return None
        
        o_code = origin_name if len(origin_name) == 3 and origin_name.isupper() else AIRPORT_MAP.get(origin_name, "JFK")
        d_code = dest_name if len(dest_name) == 3 and dest_name.isupper() else AIRPORT_MAP.get(dest_name, "JFK")
        
        date_str = flight_date.strftime("%Y-%m-%d") if isinstance(flight_date, date) else flight_date[:10]
        url = f"https://serpapi.com/search.json?engine=google_flights&departure_id={o_code}&arrival_id={d_code}&outbound_date={date_str}&type=2&currency=USD&hl=en&api_key={api_key}"
        
        res = requests.get(url)
        data = res.json()
        
        if "error" in data:
            st.toast(f"🛑 SerpApi Error: {data['error']}")
            return None
        
        if "best_flights" in data and len(data["best_flights"]) > 0:
            return float(data["best_flights"][0]["price"])
        elif "other_flights" in data and len(data["other_flights"]) > 0:
            return float(data["other_flights"][0]["price"])
        else:
            st.toast(f"🤷‍♂️ Google found no flights for {o_code} to {d_code} on {date_str}.")
            return None
            
    except Exception as e:
        st.toast(f"💥 Code Error: {e}")
        pass
    return None

def get_google_driving_distance(origin: str, destination: str):
    """Hits the modern Google Routes API for exact driving mileage."""
    try:
        api_key = st.secrets.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            return None
            
        url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.distanceMeters"
        }
        
        payload = {
            "origin": {"address": origin.strip()},
            "destination": {"address": destination.strip()},
            "travelMode": "DRIVE"
        }
        
        res = requests.post(url, headers=headers, json=payload)
        data = res.json()
        
        # Loud Top-Level API Error Check
        if res.status_code != 200:
            error_msg = data.get('error', {}).get('message', 'Unknown Error')
            st.toast(f"🛑 Google Routes API Error: {error_msg}")
            return None
            
        # Extract the exact driving distance
        if "routes" in data and len(data["routes"]) > 0:
            meters = data["routes"][0].get("distanceMeters", 0)
            return meters / 1609.344 # Convert to miles
        else:
            st.toast("🗺️ Routing Error: No route found. (Are these connected by a road?)")
            return None
            
    except Exception as e:
        st.toast(f"💥 Code Error: {e}")
        pass
    return None
            
        safe_orig = urllib.parse.quote(origin.strip())
        safe_dest = urllib.parse.quote(destination.strip())
        
        url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={safe_orig}&destinations={safe_dest}&units=imperial&key={api_key}"
        res = requests.get(url).json()
        
        # Loud Top-Level API Error Check
        if res.get('status') != 'OK':
            error_msg = res.get('error_message', 'No specific error provided by Google.')
            st.toast(f"🛑 Google API Error: {res.get('status')} | {error_msg}")
            return None
            
        element = res['rows'][0]['elements'][0]
        
        # Loud Routing Error Check
        if element.get('status') != 'OK':
            st.toast(f"🗺️ Routing Error: {element.get('status')}. (Are these connected by a road?)")
            return None
            
        # Convert from meters to miles precisely
        meters = element['distance']['value']
        return meters / 1609.344
        
    except Exception as e:
        st.toast(f"💥 Code Error: {e}")
        pass
    return None

# ─── INLINE CORE DATA ─────────────────────────────────────────────

st.markdown("---")
st.subheader("1. Core Data")

col_meta1, col_meta2, col_meta3 = st.columns(3)
with col_meta1:
    traveler_name = st.text_input("Traveler Name", key=f"traveler_name_{st.session_state['form_key']}", placeholder="e.g. Larry", on_change=hide_budget)
with col_meta2:
    purpose_input = st.text_input("Purpose of Trip (Max 64 Characters)", key=f"purpose_{st.session_state['form_key']}", max_chars=64, placeholder="e.g. System Integration Assessment", on_change=hide_budget)
with col_meta3:
    origin_input = st.text_input("Starting Location", key=f"origin_{st.session_state['form_key']}", value="Houston, TX", on_change=hide_budget)

origin_geo = get_coordinates(origin_input)
if origin_geo:
    st.caption(f"✔️ Origin locked: **{origin_geo['clean_name']}**")

# ─── DESTINATIONS & DATES ─────────────────────────────────────────

st.markdown("---")
st.subheader("2. Destinations and Dates")
st.markdown("Specify destinations chronologically. Dates must sequence perfectly with no gaps or overlaps.")

col_add, col_rem, _ = st.columns([2, 2, 6])
with col_add:
    if st.button("＋ Add Next Destination Leg"):
        st.session_state["num_legs"] += 1
        st.session_state["show_budget"] = False
        st.rerun()
with col_rem:
    if st.button("➖ Remove Last Leg") and st.session_state["num_legs"] > 1:
        st.session_state["num_legs"] -= 1
        st.session_state["show_budget"] = False
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
        leg_name = st.selectbox(f"Location", options=DROPDOWN_OPTIONS, key=f"loc_raw_{i}_{st.session_state['form_key']}", index=0, on_change=hide_budget)
    with l_col2:
        leg_start = st.date_input(f"Arrival Date", default_start, key=f"start_{i}_{st.session_state['form_key']}", on_change=hide_budget)
    with l_col3:
        leg_end = st.date_input(f"Departure Date", default_start + timedelta(days=3), key=f"end_{i}_{st.session_state['form_key']}", on_change=hide_budget)
        
    if FEDERAL_RATES_DB and leg_name != "-- Select Destination --":
        raw_legs_inputs.append({
            "index": i, "name": leg_name, "data": FEDERAL_RATES_DB[leg_name], "start": leg_start, "end": leg_end
        })

legs_data = []
for idx, leg in enumerate(raw_legs_inputs):
    if leg["start"] > leg["end"]:
        st.error(f"❌ **Chronological Error on Leg #{idx+1}:** Arrival date cannot exceed departure date.")
        date_sequencing_valid = False
        
    if idx > 0:
        prior_end_date = raw_legs_inputs[idx-1]["end"]
        expected_start_date = prior_end_date + timedelta(days=1)
        if leg["start"] < expected_start_date or leg["start"] > expected_start_date:
            st.error(f"❌ **Timeline Error on Leg #{idx+1}:** Must start exactly on **{expected_start_date.strftime('%B %d, %Y')}**.")
            date_sequencing_valid = False

    leg_geo = get_coordinates(leg["name"])
    if not leg_geo: leg_geo = {"lat": 0.0, "lon": 0.0, "is_foreign": "GSA" not in leg["data"]["authority"]}

    legs_data.append({
        "index": leg["index"], "name": leg["name"], "lodging_rate": leg["data"]["lodging"],
        "mie_rate": leg["data"]["mie"], "authority": leg["data"]["authority"],
        "lat": leg_geo["lat"], "lon": leg_geo["lon"], "is_foreign": leg_geo["is_foreign"],
        "start": leg["start"], "end": leg["end"], "days": (leg["end"] - leg["start"]).days + 1
    })

# ─── CALCULATE BUDGET BUTTON ──────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🧮 Calculate Budget", type="primary", use_container_width=True):
    if not date_sequencing_valid:
        st.error("Please fix chronological errors in your dates before calculating.")
    elif len(legs_data) < st.session_state["num_legs"] or len(legs_data) == 0:
        st.error("Please select a valid destination for every leg.")
    else:
        st.session_state["show_budget"] = True

# ─── FINANCIAL CALCULATIONS AND COMPILATION ───────────────────────────

if st.session_state.get("show_budget", False):
    st.markdown("---")
    st.subheader("3. Dynamic Budget Analysis")
    
    flight_chain = [{"name": origin_geo["clean_name"], "lat": origin_geo["lat"], "lon": origin_geo["lon"], "is_foreign": origin_geo["is_foreign"], "start": date.today()}]
    for leg in legs_data: flight_chain.append(leg)
    flight_chain.append({"name": origin_geo["clean_name"], "lat": origin_geo["lat"], "lon": origin_geo["lon"], "is_foreign": origin_geo["is_foreign"], "start": legs_data[-1]["end"]})
    
    total_airfare_cost = 0.0
    airfare_log = []
    
    with st.spinner("Hitting APIs for Live Airfare..."):
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

    total_lodging_cost, total_rental_cost, total_per_diem_cost, total_misc_cost, total_days = 0.0, 0.0, 0.0, 0.0, 0
    
    global_start = min(l["start"] for l in legs_data)
    global_end = max(l["end"] for l in legs_data)
    
    breakdown_table_rows = []
    
    for idx, leg in enumerate(legs_data):
        total_days += leg["days"]
        leg_lodging_rate = leg["lodging_rate"]
        leg_mie_rate = leg["mie_rate"]
        leg_car_rate = round(65.0 if not leg["is_foreign"] else 95.0, 2)
        
        leg_nights = leg["days"] - 1 if idx == (len(legs_data) - 1) else leg["days"]
        total_lodging_cost += (leg_nights * leg_lodging_rate)
        total_rental_cost += (leg["days"] * leg_car_rate)
        total_misc_cost += (140.0 if leg["is_foreign"] else 90.0) + (15.0 * leg["days"])
        
        for day_offset in range(leg["days"]):
            current_day = leg["start"] + timedelta(days=day_offset)
            if current_day == global_start or current_day == global_end: total_per_diem_cost += (leg_mie_rate * 0.75)
            else: total_per_diem_cost += leg_mie_rate

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
    
    costs_mapped = dict(zip(edited_df["Category"], edited_df["Estimated Cost"]))
    final_calculated_sum = edited_df["Estimated Cost"].sum()
    
    st.markdown("#### 📋 Comprehensive Per Diem & Lodging Rates Reference Table")
    st.table(pd.DataFrame(breakdown_table_rows)) 
        
    st.markdown(f"### **Total Multi-Leg Projected Budget:** ${final_calculated_sum:,.2f}")
    
    col_save, col_clear = st.columns([3, 7])
    
    with col_save:
        if st.button("💾 Commit & Log Trip"):
            safe_traveler = traveler_name.strip() if traveler_name.strip() else "Unknown Traveler"
            flight_json = json.dumps([{"name": f["name"], "start": f["start"].isoformat() if isinstance(f["start"], date) else f["start"]} for f in flight_chain])
            
            new_entry = {
                "Month": global_start.strftime("%B %Y"),
                "Traveler": safe_traveler,
                "Location": ", ".join([l["name"] for l in legs_data]),
                "Start_Date": global_start.isoformat(), 
                "End_Date": global_end.isoformat(),
                "Dates": f"{global_start.strftime('%m/%d')} - {global_end.strftime('%m/%d/%y')}",
                "Days": total_days,
                "Airfare": round(costs_mapped.get("Airfare", total_airfare_cost), 2),
                "Per Diem": round(costs_mapped.get("Per Diem (M&IE)", total_per_diem_cost), 2),
                "Lodging": round(costs_mapped.get("Lodging", total_lodging_cost), 2),
                "Rental Car": round(costs_mapped.get("Economy Rental Vehicle", total_rental_cost), 2),
                "Misc": round(costs_mapped.get("Miscellaneous", total_misc_cost), 2),
                "Cost": round(final_calculated_sum, 2),
                "Flight_Chain_JSON": flight_json,
                "Refresh Live Airfare": False
            }
            
            st.session_state["trip_database"].append(new_entry)
            save_ledger(st.session_state["trip_database"])
            
            reset_estimator()  
            st.success("Itinerary permanently saved to trip_ledger.csv! Form reset.")
            st.rerun()
            
    with col_clear:
        if st.button("🔄 Clear Estimate & Start Over"):
            reset_estimator()
            st.rerun()

# ─── STANDALONE UTILITIES ─────────────────────────────────────────────

st.markdown("---")
st.subheader("4. Standalone Utilities")

col_util1, col_util2 = st.columns(2)

with col_util1:
    st.markdown("#### 💱 Currency Converter")
    st.caption("Powered by real-time & historical exchange APIs")
    
    @st.cache_data(ttl=86400)
    def fetch_exchange_rate(base, target, fetch_date):
        if base == target: return 1.0
        try:
            date_str = fetch_date.strftime("%Y-%m-%d")
            url = f"https://api.frankfurter.app/{date_str}?from={base}&to={target}"
            if fetch_date >= date.today():
                url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
            res = requests.get(url).json()
            return res["rates"][target]
        except Exception:
            return None
            
    c_amt, c_base, c_tgt = st.columns([2, 1, 1])
    amt = c_amt.number_input("Amount", value=100.0, min_value=0.0)
    curr_from = c_base.selectbox("From", ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "MXN", "CHF"], index=0)
    curr_to = c_tgt.selectbox("To", ["EUR", "USD", "GBP", "JPY", "CAD", "AUD", "MXN", "CHF"], index=1)
    
    c_date, c_btn = st.columns([2, 1])
    conv_date = c_date.date_input("Conversion Date", value=date.today())
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("Convert", use_container_width=True):
        rate = fetch_exchange_rate(curr_from, curr_to, conv_date)
        if rate:
            st.success(f"**{amt:,.2f} {curr_from}** = **{amt * rate:,.2f} {curr_to}** (Rate: {rate})")
        else:
            st.error("Data unavailable for that date/currency.")

with col_util2:
    st.markdown("#### 🚗 Mileage Estimator")
    st.caption("Federal Standard Rate: $0.725 / mile")
    
    m_start = st.text_input("Start Location", placeholder="e.g. Norfolk, VA", key="mileage_start")
    m_end = st.text_input("End Location", placeholder="e.g. Washington, DC", key="mileage_end")
    
    m_rt, m_btn = st.columns([1, 1])
    is_rt = m_rt.checkbox("Round Trip", value=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    if m_btn.button("Estimate Mileage Cost", use_container_width=True):
        api_key = st.secrets.get("GOOGLE_MAPS_API_KEY")
        
        if api_key:
            with st.spinner("Calculating exact route..."):
                base_dist = get_google_driving_distance(m_start, m_end)
                if base_dist is not None:
                    total_dist = (base_dist * 2) if is_rt else base_dist
                    cost = total_dist * 0.725
                    st.success(f"📍 Precise Route: **{total_dist:,.1f} miles** | Cost: **${cost:,.2f}**")
                else:
                    st.error("Google Maps could not calculate a route. Please verify the locations.")
        else:
            st.warning("⚠️ GOOGLE_MAPS_API_KEY missing in Streamlit Secrets. Falling back to straight-line math.")
            geo_start = get_coordinates(m_start)
            geo_end = get_coordinates(m_end)
            if geo_start and geo_end:
                base_dist = haversine_miles(geo_start['lat'], geo_start['lon'], geo_end['lat'], geo_end['lon']) * 1.2
                total_dist = (base_dist * 2) if is_rt else base_dist
                cost = total_dist * 0.725
                st.success(f"Est. Driving Distance: **{total_dist:,.1f} miles** | Cost: **${cost:,.2f}**")
            else:
                st.error("Could not locate coordinates for those locations.")

# ─── MASTER CONSOLIDATED ACCUMULATOR LEDGER ───────────────────────────

st.markdown("---")
st.subheader("5. Centralized Travel Tracker Archive")

if st.session_state["trip_database"]:
    st.info("💡 **Interactive Ledger:** Individual line item entries. Check 'Refresh Live Airfare' and hit Save to update flight costs.")
    
    df = pd.DataFrame(st.session_state["trip_database"])
    
    display_cols = ['Refresh Live Airfare', 'Month', 'Traveler', 'Location', 'Dates', 'Days', 'Airfare', 'Per Diem', 'Lodging', 'Rental Car', 'Misc', 'Cost']
    available_cols = [c for c in display_cols if c in df.columns]
    
    edited_archive = st.data_editor(
        df[available_cols],
        num_rows="dynamic",
        use_container_width=True,
        key="archive_editor"
    )
    
    csv = edited_archive.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Export Ledger to Spreadsheet (CSV)", data=csv, file_name='travel_ledger_export.csv', mime='text/csv')
    
    # ─── GANTT CHART TIMELINE ─────────────────────────────────────────
    if st.button("📊 Generate Deployment Timeline"):
        st.markdown("### Contractor Travel Schedule")
        try:
            plot_df = pd.DataFrame(st.session_state["trip_database"])
            
            if 'Start_Date' not in plot_df.columns or 'End_Date' not in plot_df.columns:
                st.error("⚠️ Your ledger contains older trips missing exact start/end data. Please click **'Wipe Entire Database'** below to reset the tracker so the timeline can function properly.")
            else:
                plot_df['Start_Date'] = pd.to_datetime(plot_df['Start_Date'])
                plot_df['End_Date'] = pd.to_datetime(plot_df['End_Date'])
                
                fig = px.timeline(
                    plot_df, 
                    x_start="Start_Date", 
                    x_end="End_Date", 
                    y="Traveler", 
                    color="Location", 
                    hover_data={"Cost": ":$,.2f", "Days": True, "Start_Date": False, "End_Date": False}
                )
                fig.update_yaxes(autorange="reversed") 
                fig.update_traces(marker_cornerradius=12)
                
                st.plotly_chart(fig, use_container_width=True)
                
        except Exception as e:
            st.error(f"Not enough valid date data to generate timeline yet. Error: {e}")

    col1, col2 = st.columns([2, 8])
    with col1:
        if st.button("💾 Save Archive Changes & Process Refreshes"):
            updated_records = df.to_dict('records')
            
            for idx, row in edited_archive.iterrows():
                if idx < len(updated_records):
                    if row.get('Refresh Live Airfare', False) and 'Flight_Chain_JSON' in updated_records[idx]:
                        st.toast(f"Fetching live airfare for {row['Traveler']}...")
                        try:
                            chain = json.loads(updated_records[idx]['Flight_Chain_JSON'])
                            new_airfare = 0.0
                            for i in range(len(chain) - 1):
                                p1, p2 = chain[i], chain[i+1]
                                price = fetch_live_airfare(p1['name'], p2['name'], p2['start'])
                                new_airfare += price if price else 0.0
                            
                            if new_airfare > 0:
                                updated_records[idx]['Airfare'] = round(new_airfare, 2)
                                updated_records[idx]['Cost'] = sum([updated_records[idx][c] for c in ['Airfare', 'Per Diem', 'Lodging', 'Rental Car', 'Misc']])
                        except Exception:
                            pass
                    
                    for c in available_cols:
                        if c != 'Refresh Live Airfare':
                            updated_records[idx][c] = row[c]
                    updated_records[idx]['Refresh Live Airfare'] = False
            
            st.session_state["trip_database"] = updated_records
            save_ledger(st.session_state["trip_database"])
            st.success("Archive updated & Airfares Refreshed!")
            st.rerun()
            
    with col2:
        if st.button("❌ Wipe Entire Database"):
            st.session_state["trip_database"] = []
            if os.path.exists(LEDGER_FILE): os.remove(LEDGER_FILE)
            st.rerun()
else:
    st.caption("No records currently established inside the historical ledger.")
