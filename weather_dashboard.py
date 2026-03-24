import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import time
import re

# ─────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Peak Temp Tracker",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    .peak-confirmed  { color: #ff4b4b; font-weight: 700; }
    .peak-likely     { color: #ffa500; font-weight: 700; }
    .peak-not-yet    { color: #21c354; font-weight: 700; }
    .peak-unknown    { color: #aaaaaa; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

st.title("🌡️ Peak Temperature Tracker")
st.caption("Live METAR + Open-Meteo signals • Refreshes only when new METAR arrives")

# ─────────────────────────────────────────────
#  CONFIGURATION — edit this section only
# ─────────────────────────────────────────────
target_date = "2026-03-25"   # ← Change for Polymarket target date

cities = {
    "Lucknow": {
        "icao":             "VILK",
        "wu_city":          "lucknow",
        "lat":              26.85,
        "lon":              80.92,
        "typical_peak_utc": 9,      # 14:30 IST ≈ 09:00 UTC
    },
    "Delhi": {
        "icao":             "VIDP",
        "wu_city":          "delhi",
        "lat":              28.61,
        "lon":              77.23,
        "typical_peak_utc": 9,
    },
    "Mumbai": {
        "icao":             "VABB",
        "wu_city":          "mumbai",
        "lat":              19.08,
        "lon":              72.88,
        "typical_peak_utc": 8,      # sea breeze → earlier peak ~13:30 IST
    },
    # ── Add more cities below ──────────────────────────────────────────────
    # "Kolkata": {
    #     "icao": "VECC", "wu_city": "kolkata",
    #     "lat": 22.65, "lon": 88.45, "typical_peak_utc": 9,
    # },
}

# ─────────────────────────────────────────────
#  METAR PARSING HELPERS
# ─────────────────────────────────────────────

def parse_temp_dew(raw: str):
    """Extract temp and dew point from raw METAR string. Handles M (minus) prefix."""
    match = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
    if match:
        temp = float(match.group(1).replace("M", "-"))
        dew  = float(match.group(2).replace("M", "-"))
        return temp, dew
    return None, None

def parse_wind(raw: str):
    """Return (direction_deg_or_None, speed_kt, gust_kt_or_None) from raw METAR."""
    if "00000KT" in raw:
        return 0, 0, None
    match = re.search(r'(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT', raw)
    if match:
        direction = None if match.group(1) == "VRB" else int(match.group(1))
        speed = int(match.group(2))
        gust  = int(match.group(3)) if match.group(3) else None
        return direction, speed, gust
    return None, None, None

def parse_clouds(raw: str):
    """Return the most significant cloud layer as a display string."""
    cover_rank = {"OVC": 4, "BKN": 3, "SCT": 2, "FEW": 1}
    layers = re.findall(r'(FEW|SCT|BKN|OVC)(\d{3})', raw)
    if not layers:
        if any(k in raw for k in ("SKC", "NCD", "CAVOK", "NSC")):
            return "Clear ☀️"
        return "N/A"
    best    = max(layers, key=lambda x: cover_rank.get(x[0], 0))
    alt_ft  = int(best[1]) * 100
    icons   = {"FEW": "🌤️", "SCT": "⛅", "BKN": "🌥️", "OVC": "☁️"}
    return f"{icons.get(best[0], '')} {best[0]} {alt_ft:,} ft"

def cloud_penalty(raw: str) -> int:
    """0 = clear … 3 = overcast/BKN — how much cloud suppresses heating."""
    if any(k in raw for k in ("OVC", "BKN")):
        return 3
    if "SCT" in raw:
        return 2
    if "FEW" in raw:
        return 1
    return 0

# ─────────────────────────────────────────────
#  OPEN-METEO HELPER  (free, no API key)
# ─────────────────────────────────────────────

def fetch_openmeteo(lat: float, lon: float) -> dict:
    """
    Returns: forecast_max (°C), uv_index (current hour), solar_w_m2 (current hour).
    All values may be None on failure.
    """
    result = {"forecast_max": None, "uv_index": None, "solar_w_m2": None}
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=temperature_2m_max"
            f"&hourly=uv_index,shortwave_radiation"
            f"&timezone=Asia%2FKolkata"
            f"&forecast_days=2"
        )
        r    = requests.get(url, timeout=10)
        data = r.json()

        # Today's forecast max
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dates     = data.get("daily", {}).get("time", [])
        maxtemps  = data.get("daily", {}).get("temperature_2m_max", [])
        if today_str in dates:
            result["forecast_max"] = maxtemps[dates.index(today_str)]

        # Current-hour UV + solar (Open-Meteo uses IST strings)
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        ist_now          = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
        current_hour_str = ist_now.strftime("%Y-%m-%dT%H:00")
        hourly_times     = data.get("hourly", {}).get("time", [])
        uv_values        = data.get("hourly", {}).get("uv_index", [])
        solar_values     = data.get("hourly", {}).get("shortwave_radiation", [])
        if current_hour_str in hourly_times:
            h = hourly_times.index(current_hour_str)
            result["uv_index"]   = uv_values[h]   if h < len(uv_values)   else None
            result["solar_w_m2"] = solar_values[h] if h < len(solar_values) else None
    except Exception:
        pass
    return result

# ─────────────────────────────────────────────
#  PEAK PREDICTION ENGINE
# ─────────────────────────────────────────────

def predict_peak(
    temp, forecast_max, rate_of_change,
    cloud_score, uv_index, solar_w_m2,
    current_utc_hour, typical_peak_utc,
) -> tuple:
    """
    Returns (status_label, css_class, reasons_list).
    score accumulates evidence that the peak is already IN.
    """
    reasons = []
    score   = 0

    # 1. Rate of change
    if rate_of_change is not None:
        if rate_of_change <= 0:
            score += 3
            reasons.append(f"🔴 Temp rate: {rate_of_change:+.1f}°C/hr — flat or falling")
        elif rate_of_change < 0.5:
            score += 1
            reasons.append(f"🟡 Temp rate: {rate_of_change:+.1f}°C/hr — slowing")
        else:
            reasons.append(f"🟢 Temp rate: {rate_of_change:+.1f}°C/hr — still rising")

    # 2. vs model forecast ceiling
    if forecast_max is not None and temp is not None:
        gap = round(forecast_max - float(temp), 1)
        if gap <= 0.5:
            score += 3
            reasons.append(f"🔴 Only {gap}°C below model max ({forecast_max}°C) — ceiling reached")
        elif gap <= 1.5:
            score += 1
            reasons.append(f"🟡 {gap}°C below model max ({forecast_max}°C) — approaching ceiling")
        else:
            reasons.append(f"🟢 {gap}°C below model max ({forecast_max}°C) — room to climb")

    # 3. Cloud cover
    if cloud_score >= 3:
        score += 2
        reasons.append("🔴 Heavy cloud (BKN/OVC) — solar heating blocked")
    elif cloud_score == 2:
        score += 1
        reasons.append("🟡 Scattered clouds — partial suppression")
    else:
        reasons.append("🟢 Clear / few clouds — full solar heating")

    # 4. Time of day vs typical peak
    hrs_past = current_utc_hour - typical_peak_utc
    if hrs_past >= 2:
        score += 3
        reasons.append(f"🔴 {hrs_past}h past typical peak time — likely in decline phase")
    elif hrs_past >= 0:
        score += 1
        reasons.append(f"🟡 At or just past typical peak window")
    else:
        reasons.append(f"🟢 {abs(hrs_past)}h before typical peak — heating still ongoing")

    # 5. UV Index
    if uv_index is not None:
        if uv_index <= 2:
            score += 2
            reasons.append(f"🔴 UV Index {uv_index} — very low solar input")
        elif uv_index <= 5:
            score += 1
            reasons.append(f"🟡 UV Index {uv_index} — moderate solar input")
        else:
            reasons.append(f"🟢 UV Index {uv_index} — strong solar input, heating ongoing")

    # 6. Solar radiation (informational)
    if solar_w_m2 is not None:
        reasons.append(f"☀️ Solar radiation: {solar_w_m2:.0f} W/m²")
        if solar_w_m2 < 50:
            score += 1
            reasons.append("🔴 Near-zero solar radiation — no meaningful surface heating")

    # Verdict
    if score >= 9:
        return "Peak Confirmed ✅",   "peak-confirmed", reasons
    elif score >= 6:
        return "Peak Likely In ⚠️",  "peak-likely",    reasons
    elif score >= 3:
        return "Possibly Peaking 🔔", "peak-likely",    reasons
    else:
        return "Still Climbing 📈",   "peak-not-yet",   reasons

# ─────────────────────────────────────────────
#  SESSION STATE INIT
# ─────────────────────────────────────────────
if "last_raw_obs"    not in st.session_state:
    st.session_state.last_raw_obs    = {}   # city → last rawOb string
if "temp_history"    not in st.session_state:
    st.session_state.temp_history    = {}   # city → [(utc_ts, temp), ...]
if "last_fetch_time" not in st.session_state:
    st.session_state.last_fetch_time = None

# ─────────────────────────────────────────────
#  MAIN DATA FETCH
# ─────────────────────────────────────────────

def fetch_all():
    rows        = []
    raw_obs_map = {}
    now_utc     = datetime.now(timezone.utc)

    for city, cfg in cities.items():

        # ── METAR (with retry) ─────────────────────────────────────────────
        metar_url = (
            f"https://aviationweather.gov/api/data/metar"
            f"?ids={cfg['icao']}&format=json"
        )
        metar_error = None
        metar       = {}

        for attempt in range(3):          # try up to 3 times
            try:
                r        = requests.get(metar_url, timeout=15)
                r.raise_for_status()
                payload  = r.json()
                if isinstance(payload, list) and len(payload) > 0:
                    metar = payload[0]
                    break                 # success — stop retrying
                else:
                    metar_error = f"Empty response from API (attempt {attempt+1}/3)"
            except requests.exceptions.Timeout:
                metar_error = f"Timeout on attempt {attempt+1}/3"
            except requests.exceptions.HTTPError as e:
                metar_error = f"HTTP {r.status_code} on attempt {attempt+1}/3"
                break                     # don't retry HTTP errors
            except Exception as e:
                metar_error = f"{type(e).__name__}: {e} (attempt {attempt+1}/3)"
            time.sleep(1)                 # brief pause before retry

        try:
            raw = metar.get("rawOb", "N/A") if metar else "N/A"

            # Temperature
            temp = metar.get("temp") if metar else None
            if temp is None:
                temp, _ = parse_temp_dew(raw)
            temp = round(float(temp), 1) if temp is not None else None

            # Dew point — try 4 field names, then parse raw
            dew = None
            if metar:
                dew = (
                    metar.get("dewpoint") or metar.get("dewp")
                    or metar.get("dwpt")  or metar.get("dewPt")
                )
            if dew is None:
                _, dew = parse_temp_dew(raw)
            dew = round(float(dew), 1) if dew is not None else None

            spread = round(temp - dew, 1) if (temp is not None and dew is not None) else None

            obs_ts   = metar.get("obsTime", 0) if metar else 0
            obs_time = (
                datetime.utcfromtimestamp(obs_ts).strftime("%H:%M UTC")
                if obs_ts else "N/A"
            )
            wind_dir, wind_spd, wind_gust = parse_wind(raw)
            cloud_str   = parse_clouds(raw)
            cloud_score = cloud_penalty(raw)

            if wind_spd is not None:
                if wind_spd == 0:
                    wind_str = "Calm"
                else:
                    wind_str = f"{wind_dir if wind_dir is not None else 'VRB'}° / {wind_spd}kt"
                    if wind_gust:
                        wind_str += f" (G{wind_gust}kt)"
            else:
                wind_str = "N/A"

        except Exception as e:
            metar_error = metar_error or f"{type(e).__name__}: {e}"
            temp = dew = spread = obs_ts = None
            raw = obs_time = wind_str = cloud_str = "N/A"
            cloud_score = 0

        raw_obs_map[city] = raw

        # ── Update temp history ─────────────────────────────────────────────
        if city not in st.session_state.temp_history:
            st.session_state.temp_history[city] = []
        history = st.session_state.temp_history[city]

        if (raw not in ("N/A", "Error")
                and raw != st.session_state.last_raw_obs.get(city)
                and temp is not None):
            history.append((obs_ts or now_utc.timestamp(), temp))
            st.session_state.temp_history[city] = history[-6:]

        # Rate of change (°C / hr) over last two readings
        rate_of_change = None
        if len(history) >= 2:
            t1_ts, t1_temp = history[-2]
            t2_ts, t2_temp = history[-1]
            hrs = (t2_ts - t1_ts) / 3600
            if hrs > 0:
                rate_of_change = round((t2_temp - t1_temp) / hrs, 2)

        # ── Open-Meteo ─────────────────────────────────────────────────────
        om = fetch_openmeteo(cfg["lat"], cfg["lon"])

        # ── Peak prediction ────────────────────────────────────────────────
        peak_label, peak_css, reasons = predict_peak(
            temp             = temp,
            forecast_max     = om["forecast_max"],
            rate_of_change   = rate_of_change,
            cloud_score      = cloud_score,
            uv_index         = om["uv_index"],
            solar_w_m2       = om["solar_w_m2"],
            current_utc_hour = now_utc.hour,
            typical_peak_utc = cfg["typical_peak_utc"],
        )

        rows.append({
            "City":            city,
            "ICAO":            cfg["icao"],
            "_metar_error":    metar_error,
            "Temp (°C)":       temp,
            "Dew Pt (°C)":     dew,
            "Spread (°C)":     spread,
            "Wind":            wind_str,
            "Cloud Cover":     cloud_str,
            "Δ Temp/hr":      rate_of_change,
            "Model Max (°C)":  om["forecast_max"],
            "UV Index":        om["uv_index"],
            "Solar W/m²":      om["solar_w_m2"],
            "Peak Status":     peak_label,
            "Last METAR":      obs_time,
            "Raw METAR":       raw,
            # internal
            "_reasons":        reasons,
            "_peak_css":       peak_css,
            "_city":           city,
        })

    return rows, raw_obs_map

# ─────────────────────────────────────────────
#  FETCH
# ─────────────────────────────────────────────
rows, raw_obs_map = fetch_all()
st.session_state.last_fetch_time = datetime.now(timezone.utc)

metar_updated = any(
    raw_obs_map.get(city) != st.session_state.last_raw_obs.get(city)
    for city in cities
)
st.session_state.last_raw_obs = raw_obs_map

# ─────────────────────────────────────────────
#  STATUS BANNER
# ─────────────────────────────────────────────
now_str = st.session_state.last_fetch_time.strftime("%H:%M:%S UTC")
if metar_updated:
    st.success(f"✅ New METAR detected at {now_str} — data refreshed")
else:
    st.info(f"⏳ No new METAR yet • Last checked: {now_str} • Polling every 30 s")

# ─────────────────────────────────────────────
#  PER-CITY CARDS
# ─────────────────────────────────────────────
cols = st.columns(len(cities))

for col, row in zip(cols, rows):
    city     = row["_city"]
    peak_css = row["_peak_css"]
    reasons  = row["_reasons"]

    with col:
        st.subheader(f"📍 {city}  ({row['ICAO']})")
        if row.get("_metar_error"):
            st.warning(f"⚠️ METAR issue: {row['_metar_error']}")
        st.markdown(
            f'<div class="{peak_css}" style="font-size:1.05rem;margin-bottom:0.4rem;">'
            f'{row["Peak Status"]}</div>',
            unsafe_allow_html=True
        )

        # Core metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Temp",    f"{row['Temp (°C)']}°C"    if row['Temp (°C)']   is not None else "N/A")
        m2.metric("Dew Pt",  f"{row['Dew Pt (°C)']}°C"  if row['Dew Pt (°C)'] is not None else "N/A")
        m3.metric("Spread",  f"{row['Spread (°C)']}°C"   if row['Spread (°C)'] is not None else "N/A")

        # Signal metrics
        s1, s2, s3 = st.columns(3)
        roc     = row["Δ Temp/hr"]
        roc_str = f"{roc:+.1f}°C/hr" if roc is not None else "—"
        s1.metric("Δ Temp/hr",   roc_str)
        s2.metric("Model Max",   f"{row['Model Max (°C)']}°C" if row['Model Max (°C)'] is not None else "—")
        s3.metric("UV Index",    row["UV Index"] if row["UV Index"] is not None else "—")

        st.caption(f"☁️ {row['Cloud Cover']}  •  💨 {row['Wind']}")
        solar = row["Solar W/m²"]
        st.caption(f"☀️ Solar: {solar:.0f} W/m²" if solar is not None else "☀️ Solar: —")
        st.caption(f"🕐 Last METAR: {row['Last METAR']}")

        with st.expander("🔍 Why this prediction?"):
            for reason in reasons:
                st.write(reason)

        wu_cur = f"https://www.wunderground.com/weather/in/{cities[city]['wu_city']}"
        wu_his = (
            f"https://www.wunderground.com/history/daily/in/"
            f"{cities[city]['wu_city']}/{cities[city]['icao']}/date/{target_date}"
        )
        st.markdown(f"[🌐 WU Current]({wu_cur})  |  [📅 WU History]({wu_his})")
        st.divider()

# ─────────────────────────────────────────────
#  FULL DATA TABLE
# ─────────────────────────────────────────────
with st.expander("📊 Full data table"):
    display_cols = [
        "City", "ICAO", "Temp (°C)", "Dew Pt (°C)", "Spread (°C)",
        "Wind", "Cloud Cover", "Δ Temp/hr", "Model Max (°C)",
        "UV Index", "Solar W/m²", "Peak Status", "Last METAR", "Raw METAR",
    ]
    df = pd.DataFrame([
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in rows
    ])
    st.dataframe(
        df[display_cols],
        column_config={
            "Temp (°C)":      st.column_config.NumberColumn(format="%.1f °C"),
            "Dew Pt (°C)":    st.column_config.NumberColumn(format="%.1f °C"),
            "Spread (°C)":    st.column_config.NumberColumn(format="%.1f °C"),
            "Δ Temp/hr":     st.column_config.NumberColumn(format="%.2f °C/hr"),
            "Model Max (°C)": st.column_config.NumberColumn(format="%.1f °C"),
            "UV Index":       st.column_config.NumberColumn(format="%.1f"),
            "Solar W/m²":     st.column_config.NumberColumn(format="%.0f W/m²"),
        },
        use_container_width=True,
        hide_index=True,
    )

# ─────────────────────────────────────────────
#  TEMP HISTORY CHART
# ─────────────────────────────────────────────
with st.expander("📈 Temperature history (last 6 METAR readings)"):
    chart_data = {}
    for city in cities:
        history = st.session_state.temp_history.get(city, [])
        if history:
            chart_data[city] = [t for _, t in history]

    if chart_data:
        max_len = max(len(v) for v in chart_data.values())
        padded  = {k: [None] * (max_len - len(v)) + v for k, v in chart_data.items()}
        st.line_chart(pd.DataFrame(padded))
    else:
        st.caption("Not enough data yet — waiting for 2+ METAR cycles.")

# ─────────────────────────────────────────────
#  SIGNAL LEGEND
# ─────────────────────────────────────────────
with st.expander("📖 Signal legend"):
    st.markdown("""
| Signal | What it means for peak prediction |
|---|---|
| **Spread (°C)** | Temp − Dew Pt. >10 = dry, room to rise. <5 = moisture loading, cap approaching |
| **Δ Temp/hr** | Rate of change over last 2 METARs. ≤0 = heating has stopped |
| **Model Max** | Open-Meteo GFS forecast ceiling for today. Gap ≤1°C → ceiling reached |
| **UV Index** | >6 = active solar heating. <3 = sun too low, heating effectively over |
| **Solar W/m²** | Raw solar radiation. <50 W/m² = negligible surface heating |
| **Cloud Cover** | FEW = minor. SCT = partial suppression. BKN/OVC = heating blocked |
| **Wind** | Sudden increase = mixing; may cause dip then partial recovery. Calm = max heating |
| **Peak Confirmed ✅** | Score ≥9: rate flat/falling + near model max + past peak time + low UV |
| **Peak Likely In ⚠️** | Score 6–8: most signals agree, minor ambiguity |
| **Possibly Peaking 🔔** | Score 3–5: mixed signals, watch next METAR |
| **Still Climbing 📈** | Score <3: signals point to continued heating |
    """)

# ─────────────────────────────────────────────
#  SMART POLL — 30 s interval, rerun every cycle
# ─────────────────────────────────────────────
time.sleep(30)
st.rerun()
