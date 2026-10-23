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

# --- INITIALIZE SESSION STATE ---
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False
if "lat" not in st.session_state:
    st.session_state.lat = 39.0890
if "lon" not in st.session_state:
    st.session_state.lon = -76.7870

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
    
    **Hydrologic Timing (12Z to 12Z)**
    *While Temperature variables are calculated midnight-to-midnight (Local Time), Precipitation and Snowfall are custom-aggregated from 12Z to 12Z to match standard operational hydrologic cycles.*
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
        "precipitation_sum", 
        "snowfall_sum"
    ],
    format_func=lambda x: x.replace("_", " ").title()
)

# --- Checkbox Toggles ---
st.sidebar.markdown("---")

cumulative_toggle = False
if variable in ["precipitation_sum", "snowfall_sum"]:
    icon = "❄️" if variable == "snowfall_sum" else "💧"
    cumulative_toggle = st.sidebar.checkbox(f"{icon} Show Cumulative (Running Total)", value=False)
    st.sidebar.markdown("---")

st.sidebar.markdown("### 🎛️ Plot Display Toggles")

st.sidebar.markdown("**Ensembles (Box Plots)**")
show_aifs_ens = st.sidebar.checkbox("AIFS Ensemble Spread", value=True)
show_ifs_ens = st.sidebar.checkbox("IFS Ensemble Spread", value=True)
show_gefs_ens = st.sidebar.checkbox("GEFS Ensemble Spread", value=True)
show_wn2_ens = st.sidebar.checkbox("WeatherNext 2 Ensemble Spread", value=True)

st.sidebar.markdown("**Deterministic (Lines)**")
show_aifs_det = st.sidebar.checkbox("AIFS Single", value=True)
show_ifs_det = st.sidebar.checkbox("IFS High-Res", value=True)
show_gfs_det = st.sidebar.checkbox("GFS Operational", value=True)
show_nbm = st.sidebar.checkbox("NBM (US Only)", value=True)


# --- CACHE: Fetching both Daily and Hourly simultaneously ---
@st.cache_data(ttl=3600, show_spinner="Fetching ensemble and deterministic data...")
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10))
def get_weather_data(lat, lon, var):
    base_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": var,
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "forecast_days": 15
    }
    
    # 1. Fetch All Ensembles
    ens_params = base_params.copy()
    ens_params["models"] = "ecmwf_aifs025_ensemble,ecmwf_ifs025_ensemble,gfs_seamless,google_weathernext2_ensemble"
    
    if var in ["precipitation_sum", "snowfall_sum"]:
        ens_params["hourly"] = "precipitation" if var == "precipitation_sum" else "snowfall"
        
    ens_response = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params=ens_params)
    ens_response.raise_for_status()
    
    # 2. Fetch All Deterministic Runs
    if "apparent_temperature" in var:
        hourly_var = "apparent_temperature"
    elif "temperature" in var:
        hourly_var = "temperature_2m"
    elif var == "snowfall_sum":
        hourly_var = "snowfall"
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
                if k != "time":
                    h_dict[k] = pd.to_numeric(v, errors='coerce')
                    
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

                # Correct both AIFS and WeatherNext 2 ensemble members
                for k in ens_data["daily"].keys():
                    if ("aifs" in k or "weathernext" in k) and k.startswith(variable):
                        orig = pd.Series(ens_data["daily"][k])
                        ens_data["daily"][k] = (orig + aligned_delta).tolist()

                # Correct AIFS Deterministic Single run
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
                if k != "time":
                    ens_h_dict[k] = pd.to_numeric(v, errors='coerce')
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
                if k != "time":
                    det_h_dict[k] = pd.to_numeric(v, errors='coerce')
            df_det_h = pd.DataFrame(det_h_dict)
            
            df_det_h["utc_time"] = df_det_h["local_time"] - pd.to_timedelta(utc_offset, unit='s')
            df_det_h["12z_date"] = (df_det_h["utc_time"] - pd.Timedelta(hours=12)).dt.floor('D')
            det_12z_sums = df_det_h.groupby("12z_date").sum(numeric_only=True)
            
            daily_det_new = {"time": det_12z_sums.index.strftime('%Y-%m-%d').tolist()}
            for col in det_12z_sums.columns:
                daily_det_new[col.replace(target_hourly_var, variable)] = det_12z_sums[col].tolist()
            det_data["daily"] = daily_det_new

        # --- PROCESS ENSEMBLE DATA ---
        daily_ens = ens_data["daily"]
        member_columns = [col for col in daily_ens.keys() if col.startswith(variable)]

        ens_dict = {"time": pd.to_datetime(daily_ens["time"])}
        for col in member_columns:
            ens_dict[col] = daily_ens[col]
            
        df = pd.DataFrame(ens_dict)
        df.set_index("time", inplace=True)

        unit = ""
        if "daily_units" in ens_data:
            for k, v in ens_data["daily_units"].items():
                if k.startswith(variable):
                    unit = v
                    break

        display_name = variable.replace("_", " ").title()
        if variable == "precipitation_sum":
            display_name = "Precipitation Sum (12Z to 12Z)"
        elif variable == "snowfall_sum":
            display_name = "Snowfall Sum (12Z to 12Z)"

        # --- BUCKET MEMBERS ---
        aifs_cols = [c for c in member_columns if "aifs" in c]
        ifs_cols = [c for c in member_columns if "ifs" in c and "aifs" not in c and "weathernext" not in c]
        gefs_cols = [c for c in member_columns if "gfs" in c or "gefs" in c]
        wn2_cols = [c for c in member_columns if "weathernext" in c]
        
        if not gefs_cols:
            assigned = set(aifs_cols + ifs_cols + wn2_cols)
            gefs_cols = [c for c in member_columns if c not in assigned]

        # --- PROCESS DETERMINISTIC DATA ---
        daily_det = det_data["daily"]
        det_lines = {}
        
        k_aifs = [k for k in daily_det.keys() if k.startswith(variable) and "aifs" in k]
        if k_aifs:
            det_lines["AIFS Deterministic"] = daily_det[k_aifs[0]]
        
        k_ifs = [k for k in daily_det.keys() if k.startswith(variable) and "ifs" in k and "aifs" not in k and "weathernext" not in k]
        if k_ifs:
            det_lines["IFS Deterministic"] = daily_det[k_ifs[0]]
        
        k_gfs = [k for k in daily_det.keys() if k.startswith(variable) and "gfs" in k]
        if k_gfs:
            det_lines["GFS Deterministic"] = daily_det[k_gfs[0]]
        
        k_nbm = [k for k in daily_det.keys() if k.startswith(variable) and "nbm" in k]
        if k_nbm:
            det_lines["NBM (US Only)"] = daily_det[k_nbm[0]]

        # --- CUMULATIVE SUM ENGINE (Applied Before Medians) ---
        if variable in ["precipitation_sum", "snowfall_sum"] and cumulative_toggle:
            df[member_columns] = df[member_columns].cumsum(axis=0)
            for k in det_lines.keys():
                det_lines[k] = pd.Series(det_lines[k]).cumsum().tolist()
            if variable == "precipitation_sum":
                display_name = "Cumulative Precipitation (12Z to 12Z)"
            else:
                display_name = "Cumulative Snowfall (12Z to 12Z)"

        # --- CALCULATE MEDIANS ---
        df["ensemble_median"] = df[member_columns].median(axis=1)
        df["AIFS Median"] = df[aifs_cols].median(axis=1) if aifs_cols else 0
        df["IFS Median"] = df[ifs_cols].median(axis=1) if ifs_cols else 0
        df["GEFS Median"] = df[gefs_cols].median(axis=1) if gefs_cols else 0
        df["WeatherNext2 Median"] = df[wn2_cols].median(axis=1) if wn2_cols else 0

        # --- The Expected Forecast Readout (Static Table) ---
        st.markdown("---")
        if variable in ["temperature_2m_max", "apparent_temperature_max"]:
            st.markdown("### 📅 15-Day Expected Forecast (All Models) 🛠️")
            st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}** | *🛠️ AIFS & WeatherNext 2 Max Temperatures corrected for diurnal heating using high-res models.*")
        else:
            st.markdown("### 📅 15-Day Expected Forecast (All Models)")
            st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}**")
        
        precision = 2 if variable in ["precipitation_sum", "snowfall_sum"] else 1
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

        # --- DYNAMIC PLOTTING & CALCULATION PREP ---
        active_members = []
        if show_aifs_ens: active_members.extend(aifs_cols)
        if show_ifs_ens: active_members.extend(ifs_cols)
        if show_gefs_ens: active_members.extend(gefs_cols)
        if show_wn2_ens: active_members.extend(wn2_cols)

        # --- PLOT 1: The Box Plot ---
        fig = go.Figure()

        if active_members:
            df_melted = df.reset_index().melt(
                id_vars=['time'], 
                value_vars=active_members, 
                var_name='Member', 
                value_name='Value'
            )
            fig.add_trace(go.Box(
                x=df_melted['time'],
                y=df_melted['Value'],
                name='Ensemble Spread',
                marker_color='steelblue',
                boxpoints='outliers',
                line=dict(width=1.5)
            ))

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
                    x=df.index[:len(data_array)], 
                    y=data_array, 
                    mode='lines+markers',
                    line=dict(color=color, width=2.5),
                    marker=dict(size=6),
                    name=name
                ))

        fig.update_layout(
            title=f"Forecast Spread vs Deterministic Runs: {display_name}<br><sup>Lat: {lat}, Lon: {lon} | Members Active: {len(active_members)}</sup>",
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
            default_val = 32.0 if "temperature" in variable else 1.0
            threshold = st.number_input(f"Target Threshold ({unit})", value=default_val)
        with col2:
            condition = st.selectbox("Condition", ["Greater than or equal to (≥)", "Less than or equal to (≤)"])

        total_members = len(active_members)
        
        if total_members > 0:
            if "Greater" in condition:
                probabilities = (df[active_members] >= threshold).sum(axis=1) / total_members * 100
                operator_symbol = "≥"
            else:
                probabilities = (df[active_members] <= threshold).sum(axis=1) / total_members * 100
                operator_symbol = "≤"
        else:
            probabilities = pd.Series(0, index=df.index)
            operator_symbol = "≥" if "Greater" in condition else "≤"

        fig_prob = go.Figure()
        fig_prob.add_trace(go.Bar(
            x=probabilities.index,
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
