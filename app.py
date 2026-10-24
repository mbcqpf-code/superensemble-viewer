import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import folium
from streamlit_folium import st_folium
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential

# 1. Set up the Streamlit page layout
st.set_page_config(page_title="Superensemble Viewer", page_icon="🌤️", layout="wide")

# --- INITIALIZE SESSION STATE & CALLBACKS ---
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False
if "lat" not in st.session_state:
    st.session_state.lat = 39.0890
if "lon" not in st.session_state:
    st.session_state.lon = -76.7870

# Toggle Group Keys
spread_keys = ["show_aifs_ens", "show_ifs_ens", "show_gefs_ens", "show_wn2_ens"]
median_keys = ["show_super_med", "show_aifs_med", "show_ifs_med", "show_gefs_med", "show_wn2_med"]
det_keys = ["show_aifs_det", "show_ifs_det", "show_gfs_det", "show_nbm"]

for k in spread_keys:
    if k not in st.session_state: st.session_state[k] = True
for k in median_keys:
    if k not in st.session_state: st.session_state[k] = (k == "show_super_med")
for k in det_keys:
    if k not in st.session_state: st.session_state[k] = True

def set_group_state(keys, value):
    for k in keys:
        st.session_state[k] = value

st.title("🌤️ Open-Meteo Superensemble Viewer")
st.markdown("Compare the world's top weather models or combine them into a massive Superensemble.")

# --- DATA EXPLANATION TAB ---
with st.expander("ℹ️ Understanding the Data & Model Resolutions"):
    st.markdown("""
    **Temporal Resolution & Daily Aggregations**
    Open-Meteo calculates daily maximums, minimums, and precipitation/snow sums by extracting the highest/lowest or accumulated values from hourly data. The accuracy of this peak depends on the model's native output frequency:
    * **IFS (9km HRES) & NBM:** Highly accurate. They output data natively at 1-hourly and 3-hourly intervals, easily capturing the exact diurnal heating peak.
    * **GEFS & IFS Ensembles:** Output at 3-hourly intervals, providing a close approximation of true daily extremes.
    * **AIFS & WeatherNext 2:** Global AI models operating at 0.25° resolution with 6-hourly temporal steps (00z, 06z, 12z, 18z).
    
    **The AI Diurnal Heating Correction (AIFS & WeatherNext 2)**
    Because 6-hourly output skips the peak afternoon heating window (typically 20z-22z), raw daily high temperatures from 6-hourly AI models exhibit a systemic cool bias.
    
    *🛠️ **How we fix it:** This dashboard applies a dynamic diurnal correction engine to maximum temperatures. It samples the shape of the afternoon heating curve from high-resolution deterministic models (NBM, IFS, GFS), extracts the missing sub-synoptic delta, and dynamically restores peak afternoon temperatures for both AIFS and WeatherNext 2.*
    
    **Dew Point Hourly Meteograms & Hydrologic Cycles**
    * **Dew Point:** Handled at native hourly resolution. Instead of crowded box plots, dew point is rendered as an operational **Ensemble Plume (Meteogram)** featuring 10th–90th percentile and 25th–75th percentile shaded bands.
    * **Precipitation & Snowfall:** Custom-aggregated from 12Z to 12Z UTC to match standard operational hydrologic cycles.
    """)

# 2. Create the Sidebar for User Inputs
st.sidebar.header("🗺️ Forecast Location")
st.sidebar.markdown("Click the map to drop a pin, or enter exact coordinates below.")

# --- THE INTERACTIVE MAP ---
m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=4)
folium.Marker(
    [st.session_state.lat, st.session_state.lon], 
    tooltip="Forecast Target",
    icon=folium.Icon(color="red", icon="cloud")
).add_to(m)

map_data = st_folium(m, height=250, use_container_width=True)

if map_data and map_data.get("last_clicked"):
    clicked_lat = map_data["last_clicked"]["lat"]
    clicked_lon = map_data["last_clicked"]["lng"]
    if clicked_lat != st.session_state.lat or clicked_lon != st.session_state.lon:
        st.session_state.lat = clicked_lat
        st.session_state.lon = clicked_lon
        st.rerun()

# --- THE NUMBER INPUTS ---
lat = st.sidebar.number_input("Latitude", value=st.session_state.lat, format="%.4f")
lon = st.sidebar.number_input("Longitude", value=st.session_state.lon, format="%.4f")

if lat != st.session_state.lat or lon != st.session_state.lon:
    st.session_state.lat = lat
    st.session_state.lon = lon
    st.rerun()

st.sidebar.header("⚙️ Forecast Settings")

variable = st.sidebar.selectbox(
    "Weather Variable",
    [
        "temperature_2m_max", 
        "apparent_temperature_max", 
        "temperature_2m_min", 
        "apparent_temperature_min", 
        "dew_point_2m",
        "precipitation_sum", 
        "snowfall_sum"
    ],
    format_func=lambda x: "Dew Point (Hourly Meteogram)" if x == "dew_point_2m" else x.replace("_", " ").title()
)

# --- Checkbox Toggles ---
st.sidebar.markdown("---")

cumulative_toggle = False
if variable in ["precipitation_sum", "snowfall_sum"]:
    icon = "❄️" if variable == "snowfall_sum" else "💧"
    cumulative_toggle = st.sidebar.checkbox(f"{icon} Show Cumulative (Running Total)", value=False)
    st.sidebar.markdown("---")

st.sidebar.markdown("### 🎛️ Plot Display Toggles")

# 1. Ensemble Spread Toggles
st.sidebar.markdown("**Ensemble Spread**" if variable != "dew_point_2m" else "**Ensemble Spread (Plume Bands)**")
col_s1, col_s2 = st.sidebar.columns(2)
col_s1.button("Select All", key="btn_sel_spread", on_click=set_group_state, args=(spread_keys, True))
col_s2.button("Clear All", key="btn_clr_spread", on_click=set_group_state, args=(spread_keys, False))

show_aifs_ens = st.sidebar.checkbox("AIFS Ensemble", key="show_aifs_ens")
show_ifs_ens = st.sidebar.checkbox("IFS Ensemble", key="show_ifs_ens")
show_gefs_ens = st.sidebar.checkbox("GEFS Ensemble", key="show_gefs_ens")
show_wn2_ens = st.sidebar.checkbox("WeatherNext 2 Ensemble", key="show_wn2_ens")

st.sidebar.markdown("---")

# 2. Ensemble Median Line Toggles
st.sidebar.markdown("**Ensemble Medians (Lines)**")
col_m1, col_m2 = st.sidebar.columns(2)
col_m1.button("Select All", key="btn_sel_med", on_click=set_group_state, args=(median_keys, True))
col_m2.button("Clear All", key="btn_clr_med", on_click=set_group_state, args=(median_keys, False))

show_super_med = st.sidebar.checkbox("Superensemble Median", key="show_super_med")
show_aifs_med = st.sidebar.checkbox("AIFS Ens Median", key="show_aifs_med")
show_ifs_med = st.sidebar.checkbox("IFS Ens Median", key="show_ifs_med")
show_gefs_med = st.sidebar.checkbox("GEFS Ens Median", key="show_gefs_med")
show_wn2_med = st.sidebar.checkbox("WeatherNext 2 Ens Median", key="show_wn2_med")

st.sidebar.markdown("---")

# 3. Deterministic Line Toggles
st.sidebar.markdown("**Deterministic (Lines)**")
col_d1, col_d2 = st.sidebar.columns(2)
col_d1.button("Select All", key="btn_sel_det", on_click=set_group_state, args=(det_keys, True))
col_d2.button("Clear All", key="btn_clr_det", on_click=set_group_state, args=(det_keys, False))

show_aifs_det = st.sidebar.checkbox("AIFS Single", key="show_aifs_det")
show_ifs_det = st.sidebar.checkbox("IFS High-Res", key="show_ifs_det")
show_gfs_det = st.sidebar.checkbox("GFS Operational", key="show_gfs_det")
show_nbm = st.sidebar.checkbox("NBM (US Only)", key="show_nbm")


# --- CACHE: Fetching both Daily and Hourly simultaneously ---
@st.cache_data(ttl=3600, show_spinner="Fetching ensemble and deterministic data...")
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10))
def get_weather_data(lat, lon, var):
    base_params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "forecast_days": 15
    }
    if var != "dew_point_2m":
        base_params["daily"] = var
    
    # 1. Fetch All Ensembles
    ens_params = base_params.copy()
    ens_params["models"] = "ecmwf_aifs025_ensemble,ecmwf_ifs025_ensemble,gfs_seamless,google_weathernext2_ensemble"
    
    if var in ["precipitation_sum", "snowfall_sum"]:
        ens_params["hourly"] = "precipitation" if var == "precipitation_sum" else "snowfall"
    elif var == "dew_point_2m":
        ens_params["hourly"] = "dew_point_2m"
        
    ens_response = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params=ens_params)
    ens_response.raise_for_status()
    
    # 2. Fetch All Deterministic Runs
    if "apparent_temperature" in var:
        hourly_var = "apparent_temperature"
    elif "temperature" in var:
        hourly_var = "temperature_2m"
    elif var == "snowfall_sum":
        hourly_var = "snowfall"
    elif var == "dew_point_2m":
        hourly_var = "dew_point_2m"
    else:
        hourly_var = "precipitation"
        
    det_params = base_params.copy()
    det_params["models"] = "ecmwf_aifs025_single,ecmwf_ifs,gfs_seamless,ncep_nbm_conus"
    det_params["hourly"] = hourly_var 
    det_response = requests.get("https://api.open-meteo.com/v1/forecast", params=det_params)
    det_response.raise_for_status()
    
    fetch_time = datetime.now().strftime('%b %d, %Y at %I:%M %p')
            
    return ens_response.json(), det_response.json(), fetch_time


# 3. Action Button
if st.sidebar.button("Generate Forecast", type="primary", use_container_width=True):
    st.session_state.data_loaded = True

if st.session_state.data_loaded:
    try:
        ens_data, det_data, fetch_time = get_weather_data(lat, lon, variable)
        
        # --- DIURNAL CORRECTION ENGINE (AIFS & WEATHERNEXT 2 MAX TEMPS) ---
        if variable in ["temperature_2m_max", "apparent_temperature_max"] and "hourly" in det_data:
            h_data = det_data["hourly"]
            
            h_dict = {"local_time": pd.to_datetime(h_data["time"])}
            for k, v in h_data.items():
                if k != "time": h_dict[k] = pd.to_numeric(v, errors='coerce')
                    
            df_h = pd.DataFrame(h_dict)
            utc_offset = det_data.get("utc_offset_seconds", 0)
            df_h["utc_time"] = df_h["local_time"] - pd.to_timedelta(utc_offset, unit='s')
            df_h["local_date"] = df_h["local_time"].dt.floor('D')

            ifs_cols = [c for c in h_data.keys() if "ifs" in c and "aifs" not in c and "weathernext" not in c]
            gfs_cols = [c for c in h_data.keys() if "gfs" in c]
            nbm_cols = [c for c in h_data.keys() if "nbm" in c]

            deltas = []
            for col_list in [ifs_cols, gfs_cols, nbm_cols]:
                if col_list and col_list[0] in df_h.columns:
                    c = col_list[0]
                    true_max = df_h.groupby("local_date")[c].max()
                    synoptic_max = df_h[df_h["utc_time"].dt.hour.isin([0, 6, 12, 18])].groupby("local_date")[c].max()
                    deltas.append(true_max - synoptic_max)

            if deltas:
                avg_delta = pd.concat(deltas, axis=1).mean(axis=1)
                daily_dates = pd.to_datetime(ens_data["daily"]["time"])
                aligned_delta = daily_dates.map(avg_delta).fillna(0)

                for k in ens_data["daily"].keys():
                    if ("aifs" in k or "weathernext" in k) and k.startswith(variable):
                        orig = pd.Series(ens_data["daily"][k])
                        ens_data["daily"][k] = (orig + aligned_delta).tolist()

                if "daily" in det_data:
                    for k in det_data["daily"].keys():
                        if "aifs" in k and k.startswith(variable):
                            orig = pd.Series(det_data["daily"][k])
                            det_data["daily"][k] = (orig + aligned_delta).tolist()

        # --- CUSTOM 12Z-12Z HYDROLOGIC AGGREGATION (Precip & Snow) ---
        if variable in ["precipitation_sum", "snowfall_sum"] and "hourly" in ens_data and "hourly" in det_data:
            target_hourly_var = "precipitation" if variable == "precipitation_sum" else "snowfall"
            
            # 1. Aggregate Ensemble Hourly to 12z-12z
            ens_h = ens_data["hourly"]
            ens_h_dict = {"local_time": pd.to_datetime(ens_h["time"])}
            for k, v in ens_h.items():
                if k != "time": ens_h_dict[k] = pd.to_numeric(v, errors='coerce')
            df_ens_h = pd.DataFrame(ens_h_dict)
            
            utc_offset = ens_data.get("utc_offset_seconds", 0)
            df_ens_h["utc_time"] = df_ens_h["local_time"] - pd.to_timedelta(utc_offset, unit='s')
            df_ens_h["12z_date"] = (df_ens_h["utc_time"] - pd.Timedelta(hours=12)).dt.floor('D')
            ens_12z_sums = df_ens_h.groupby("12z_date").sum(numeric_only=True)
            
            daily_ens_new = {"time": ens_12z_sums.index.strftime('%Y-%m-%d').tolist()}
            for col in ens_12z_sums.columns:
                daily_ens_new[col.replace(target_hourly_var, variable)] = ens_12z_sums[col].tolist()
            ens_data["daily"] = daily_ens_new
            
            # 2. Aggregate Deterministic Hourly to 12z-12z
            det_h = det_data["hourly"]
            det_h_dict = {"local_time": pd.to_datetime(det_h["time"])}
            for k, v in det_h.items():
                if k != "time": det_h_dict[k] = pd.to_numeric(v, errors='coerce')
            df_det_h = pd.DataFrame(det_h_dict)
            
            df_det_h["utc_time"] = df_det_h["local_time"] - pd.to_timedelta(utc_offset, unit='s')
            df_det_h["12z_date"] = (df_det_h["utc_time"] - pd.Timedelta(hours=12)).dt.floor('D')
            det_12z_sums = df_det_h.groupby("12z_date").sum(numeric_only=True)
            
            daily_det_new = {"time": det_12z_sums.index.strftime('%Y-%m-%d').tolist()}
            for col in det_12z_sums.columns:
                daily_det_new[col.replace(target_hourly_var, variable)] = det_12z_sums[col].tolist()
            det_data["daily"] = daily_det_new

        # --- PROCESS ENSEMBLE DATA ---
        if variable == "dew_point_2m":
            source_ens = ens_data["hourly"]
        else:
            source_ens = ens_data["daily"]

        member_columns = [col for col in source_ens.keys() if col.startswith(variable)]

        ens_dict = {"time": pd.to_datetime(source_ens["time"])}
        for col in member_columns:
            ens_dict[col] = pd.to_numeric(source_ens[col], errors='coerce')
            
        df = pd.DataFrame(ens_dict)
        df.set_index("time", inplace=True)

        unit = "°F" if "temperature" in variable or "dew_point" in variable else "inch"
        if "daily_units" in ens_data:
            for k, v in ens_data["daily_units"].items():
                if k.startswith(variable): unit = v; break
        elif "hourly_units" in ens_data:
            for k, v in ens_data["hourly_units"].items():
                if k.startswith(variable): unit = v; break

        display_name = variable.replace("_", " ").title()
        if variable == "precipitation_sum": display_name = "Precipitation Sum (12Z to 12Z)"
        elif variable == "snowfall_sum": display_name = "Snowfall Sum (12Z to 12Z)"
        elif variable == "dew_point_2m": display_name = "Dew Point (Hourly Meteogram)"

        # --- BUCKET MEMBERS ---
        aifs_cols = [c for c in member_columns if "aifs" in c]
        ifs_cols = [c for c in member_columns if "ifs" in c and "aifs" not in c and "weathernext" not in c]
        gefs_cols = [c for c in member_columns if "gfs" in c or "gefs" in c]
        wn2_cols = [c for c in member_columns if "weathernext" in c]
        
        if not gefs_cols:
            assigned = set(aifs_cols + ifs_cols + wn2_cols)
            gefs_cols = [c for c in member_columns if c not in assigned]

        # --- PROCESS DETERMINISTIC DATA ---
        source_det = det_data["hourly"] if variable == "dew_point_2m" else det_data["daily"]
        det_lines = {}
        
        k_aifs = [k for k in source_det.keys() if k.startswith(variable) and "aifs" in k]
        if k_aifs: det_lines["AIFS Deterministic"] = source_det[k_aifs[0]]
        
        k_ifs = [k for k in source_det.keys() if k.startswith(variable) and "ifs" in k and "aifs" not in k and "weathernext" not in k]
        if k_ifs: det_lines["IFS Deterministic"] = source_det[k_ifs[0]]
        
        k_gfs = [k for k in source_det.keys() if k.startswith(variable) and "gfs" in k]
        if k_gfs: det_lines["GFS Deterministic"] = source_det[k_gfs[0]]
        
        k_nbm = [k for k in source_det.keys() if k.startswith(variable) and "nbm" in k]
        if k_nbm: det_lines["NBM (US Only)"] = source_det[k_nbm[0]]

        # --- CUMULATIVE SUM ENGINE (Precip & Snow) ---
        if variable in ["precipitation_sum", "snowfall_sum"] and cumulative_toggle:
            df[member_columns] = df[member_columns].cumsum(axis=0)
            for k in det_lines.keys():
                det_lines[k] = pd.Series(det_lines[k]).cumsum().tolist()
            if variable == "precipitation_sum": display_name = "Cumulative Precipitation (12Z to 12Z)"
            else: display_name = "Cumulative Snowfall (12Z to 12Z)"

        # --- CALCULATE MEDIANS ---
        df["ensemble_median"] = df[member_columns].median(axis=1)
        df["AIFS Median"] = df[aifs_cols].median(axis=1) if aifs_cols else 0
        df["IFS Median"] = df[ifs_cols].median(axis=1) if ifs_cols else 0
        df["GEFS Median"] = df[gefs_cols].median(axis=1) if gefs_cols else 0
        df["WeatherNext2 Median"] = df[wn2_cols].median(axis=1) if wn2_cols else 0

        # --- The Expected Forecast Readout (Static Table) ---
        st.markdown("---")
        precision = 2 if variable in ["precipitation_sum", "snowfall_sum"] else 1
        
        if variable == "dew_point_2m":
            st.markdown("### 📅 15-Day Expected Forecast (Daily Mean Dew Point)")
            st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}** | *For Dew Point, the table displays 24-hour daily averages, while the meteogram below provides the full hourly evolution.*")
            df_daily_table = df.resample('D').mean()
            readout_data = {
                f"AIFS Ens Median ({unit})": df_daily_table["AIFS Median"].round(precision),
                f"IFS Ens Median ({unit})": df_daily_table["IFS Median"].round(precision),
                f"GEFS Ens Median ({unit})": df_daily_table["GEFS Median"].round(precision),
                f"WeatherNext2 Ens Median ({unit})": df_daily_table["WeatherNext2 Median"].round(precision),
            }
            if "NBM (US Only)" in det_lines:
                nbm_s = pd.Series(det_lines["NBM (US Only)"], index=df.index[:len(det_lines["NBM (US Only)"])])
                readout_data[f"NBM Deterministic ({unit})"] = nbm_s.resample('D').mean().reindex(df_daily_table.index).round(precision)
            readout_data[f"Superensemble Median ({unit})"] = df_daily_table["ensemble_median"].round(precision)
            readout_df = pd.DataFrame(readout_data, index=df_daily_table.index.strftime('%b %d')).T
        else:
            if variable in ["temperature_2m_max", "apparent_temperature_max"]:
                st.markdown("### 📅 15-Day Expected Forecast (All Models) 🛠️")
                st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}** | *🛠️ AIFS & WeatherNext 2 Max Temperatures corrected for diurnal heating using high-res models.*")
            else:
                st.markdown("### 📅 15-Day Expected Forecast (All Models)")
                st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}**")
            
            readout_data = {
                f"AIFS Ens Median ({unit})": df["AIFS Median"].round(precision),
                f"IFS Ens Median ({unit})": df["IFS Median"].round(precision),
                f"GEFS Ens Median ({unit})": df["GEFS Median"].round(precision),
                f"WeatherNext2 Ens Median ({unit})": df["WeatherNext2 Median"].round(precision),
            }
            if "NBM (US Only)" in det_lines:
                nbm_vals = det_lines["NBM (US Only)"]
                nbm_series = pd.Series(nbm_vals)
                nbm_series.index = df.index[:len(nbm_series)] 
                nbm_series = nbm_series.reindex(df.index)
                readout_data[f"NBM Deterministic ({unit})"] = nbm_series.round(precision)
                
            readout_data[f"Superensemble Median ({unit})"] = df["ensemble_median"].round(precision)
            readout_df = pd.DataFrame(readout_data)
            
            if variable in ["precipitation_sum", "snowfall_sum"]:
                readout_df.index = df.index.strftime('%b %d (12z)')
            else:
                readout_df.index = df.index.strftime('%b %d')
            readout_df = readout_df.T 
            
        st.dataframe(readout_df, use_container_width=True)

        # --- DYNAMIC PLOTTING PREP ---
        active_members = []
        if show_aifs_ens: active_members.extend(aifs_cols)
        if show_ifs_ens: active_members.extend(ifs_cols)
        if show_gefs_ens: active_members.extend(gefs_cols)
        if show_wn2_ens: active_members.extend(wn2_cols)

        fig = go.Figure()

        # --- PLOT LAYER 1: SPREAD (Plume for Dew Point, Box Plot for Daily) ---
        if variable == "dew_point_2m":
            if active_members:
                p10 = df[active_members].quantile(0.10, axis=1)
                p25 = df[active_members].quantile(0.25, axis=1)
                p75 = df[active_members].quantile(0.75, axis=1)
                p90 = df[active_members].quantile(0.90, axis=1)
                
                # Outer 10th - 90th percentile envelope
                fig.add_trace(go.Scatter(
                    x=df.index, y=p90, mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'
                ))
                fig.add_trace(go.Scatter(
                    x=df.index, y=p10, mode='lines', line=dict(width=0), fill='tonexty',
                    fillcolor='rgba(70, 130, 180, 0.15)', name='Ensemble 10th–90th %ile', hoverinfo='skip'
                ))
                # Inner 25th - 75th percentile (IQR) envelope
                fig.add_trace(go.Scatter(
                    x=df.index, y=p75, mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'
                ))
                fig.add_trace(go.Scatter(
                    x=df.index, y=p25, mode='lines', line=dict(width=0), fill='tonexty',
                    fillcolor='rgba(70, 130, 180, 0.35)', name='Ensemble 25th–75th %ile (IQR)', hoverinfo='skip'
                ))
        else:
            if active_members:
                df_melted = df.reset_index().melt(
                    id_vars=['time'], value_vars=active_members, var_name='Member', value_name='Value'
                )
                fig.add_trace(go.Box(
                    x=df_melted['time'], y=df_melted['Value'], name='Ensemble Spread',
                    marker_color='steelblue', boxpoints='outliers', line=dict(width=1.5)
                ))

        # --- PLOT LAYER 2: ENSEMBLE MEDIAN OVERLAYS ---
        median_overlays = {
            "Superensemble Median": (df["ensemble_median"], "#1e3a8a", "solid", 2.8, show_super_med),
            "AIFS Ens Median": (df["AIFS Median"], "#a855f7", "dash", 1.8, show_aifs_med),
            "IFS Ens Median": (df["IFS Median"], "#f59e0b", "dash", 1.8, show_ifs_med),
            "GEFS Ens Median": (df["GEFS Median"], "#10b981", "dash", 1.8, show_gefs_med),
            "WeatherNext 2 Ens Median": (df["WeatherNext2 Median"], "#06b6d4", "dash", 1.8, show_wn2_med),
        }

        for name, (series_data, color, dash_style, line_width, is_active) in median_overlays.items():
            if is_active:
                fig.add_trace(go.Scatter(
                    x=df.index, y=series_data, mode='lines',
                    line=dict(color=color, dash=dash_style, width=line_width), name=name
                ))

        # --- PLOT LAYER 3: DETERMINISTIC RUNS ---
        super_colors = {
            "AIFS Deterministic": "darkviolet", 
            "IFS Deterministic": "darkorange", 
            "GFS Deterministic": "forestgreen", 
            "NBM (US Only)": "black"
        }

        det_toggles = {
            "AIFS Deterministic": show_aifs_det,
            "IFS Deterministic": show_ifs_det,
            "GFS Deterministic": show_gfs_det,
            "NBM (US Only)": show_nbm
        }

        for name, data_array in det_lines.items():
            if det_toggles.get(name, False):
                color = super_colors.get(name, "black")
                fig.add_trace(go.Scatter(
                    x=df.index[:len(data_array)], y=data_array, mode='lines',
                    line=dict(color=color, width=2.2), name=name
                ))

        fig.update_layout(
            title=f"Forecast Spread vs Models: {display_name}<br><sup>Lat: {lat}, Lon: {lon} | Members Active: {len(active_members)}</sup>",
            xaxis_title="Date",
            yaxis_title=f"Value ({unit})",
            hovermode="x unified",
            showlegend=True,
            margin=dict(l=20, r=20, t=60, b=20)
        )

        st.plotly_chart(fig, use_container_width=True)
        
        # --- Threshold Probability Calculator ---
        st.markdown("---")
        st.markdown(f"### 📊 Threshold Probability Calculator ({len(active_members)} Active Members)")
        
        col1, col2 = st.columns(2)
        with col1:
            if variable == "dew_point_2m": default_val = 65.0
            elif "temperature" in variable: default_val = 32.0
            else: default_val = 1.0
            threshold = st.number_input(f"Target Threshold ({unit})", value=default_val)
        with col2:
            condition = st.selectbox("Condition", ["Greater than or equal to (≥)", "Less than or equal to (≤)"])

        total_members = len(active_members)
        operator_symbol = "≥" if "Greater" in condition else "≤"
        
        if total_members > 0:
            if variable == "dew_point_2m":
                # For hourly dew point, calculate if the member touches or crosses threshold during that day
                if "Greater" in condition:
                    daily_extremes = df[active_members].resample('D').max()
                    probabilities = (daily_extremes >= threshold).sum(axis=1) / total_members * 100
                else:
                    daily_extremes = df[active_members].resample('D').min()
                    probabilities = (daily_extremes <= threshold).sum(axis=1) / total_members * 100
                prob_x = daily_extremes.index.strftime('%b %d')
            else:
                if "Greater" in condition:
                    probabilities = (df[active_members] >= threshold).sum(axis=1) / total_members * 100
                else:
                    probabilities = (df[active_members] <= threshold).sum(axis=1) / total_members * 100
                if variable in ["precipitation_sum", "snowfall_sum"]:
                    prob_x = df.index.strftime('%b %d (12z)')
                else:
                    prob_x = df.index.strftime('%b %d')
        else:
            prob_x = df.index.strftime('%b %d')
            probabilities = pd.Series(0, index=df.index)

        fig_prob = go.Figure()
        fig_prob.add_trace(go.Bar(
            x=prob_x,
            y=probabilities.values,
            marker_color='mediumseagreen',
            text=[f"{val:.0f}%" for val in probabilities.values], 
            textposition='auto'
        ))

        fig_prob.update_layout(
            title=f"Probability that {display_name} will be {operator_symbol} {threshold}{unit}",
            xaxis_title="Date",
            yaxis_title="Probability (%)",
            yaxis=dict(range=[0, 105]),
            margin=dict(l=20, r=20, t=40, b=20)
        )

        st.plotly_chart(fig_prob, use_container_width=True)

        with st.expander("View Raw Ensemble Data"):
            st.dataframe(df)

    except Exception as e:
        import tenacity
        import requests
        
        if isinstance(e, tenacity.RetryError):
            original_error = e.last_attempt.exception()
            if isinstance(original_error, requests.exceptions.HTTPError):
                status = original_error.response.status_code
                message = original_error.response.text
                st.error(f"📡 Open-Meteo API Error {status}: {message}")
            else:
                st.error(f"Connection failed after multiple retries: {original_error}")
        else:
            st.error(f"Failed to fetch or process data: {e}")
else:
    st.info("👈 Use the map or text boxes in the sidebar to select your location, then click 'Generate Forecast' to start.")
