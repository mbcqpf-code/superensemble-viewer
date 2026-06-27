import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import folium
from streamlit_folium import st_folium
from datetime import datetime

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
    Open-Meteo calculates daily maximums, minimums, and precipitation sums by extracting the highest/lowest values from a 24-hour block of hourly data. The accuracy of this peak depends on the model's native output frequency:
    * **IFS (9km HRES) & NBM:** Highly accurate. They output data natively at 1-hourly and 3-hourly intervals, easily capturing the exact afternoon diurnal heating peak.
    * **GEFS & IFS Ensembles:** Output at 3-hourly intervals, providing a very close approximation of true daily extremes.
    
    **The AIFS Diurnal Correction**
    The ECMWF AIFS (Artificial Intelligence Forecasting System) outputs data strictly at 6-hour intervals (00z, 06z, 12z, 18z). Because this completely skips the peak afternoon heating window (typically 20z-22z), the raw AIFS data often exhibits a mathematically artificial "cool bias." 
    
    *🛠️ **How we fix it:** This dashboard features a custom diurnal correction engine. It calculates the exact shape of the afternoon heating curve from the high-res deterministic models (NBM, IFS, GFS) for each specific day, extracts the missing delta, and dynamically applies it to the AIFS members to reconstruct the true physical high.*
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
    ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
    format_func=lambda x: x.replace("_", " ").title()
)

# --- Checkbox Toggles ---
st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Plot Display Toggles")

st.sidebar.markdown("**Ensembles (Box Plots)**")
show_aifs_ens = st.sidebar.checkbox("AIFS Ensemble Spread", value=True)
show_ifs_ens = st.sidebar.checkbox("IFS Ensemble Spread", value=True)
show_gefs_ens = st.sidebar.checkbox("GEFS Ensemble Spread", value=True)

st.sidebar.markdown("**Deterministic (Lines)**")
show_aifs_det = st.sidebar.checkbox("AIFS Single", value=True)
show_ifs_det = st.sidebar.checkbox("IFS High-Res", value=True)
show_gfs_det = st.sidebar.checkbox("GFS Operational", value=True)
show_nbm = st.sidebar.checkbox("NBM (US Only)", value=True)


# --- CACHE: Fetching both Daily and Hourly simultaneously ---
@st.cache_data(ttl=3600, show_spinner="Fetching ensemble and deterministic data...")
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
    ens_params["models"] = "ecmwf_aifs025_ensemble,ecmwf_ifs025_ensemble,gfs_seamless"
    ens_response = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params=ens_params)
    ens_response.raise_for_status()
    
    # 2. Fetch All Deterministic Runs (UPDATED: ecmwf_ifs at native 9km)
    hourly_var = "temperature_2m" if "temperature" in var else "precipitation"
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
        
        # --- DIURNAL CORRECTION ENGINE FOR AIFS ---
        if variable == "temperature_2m_max" and "hourly" in det_data:
            h_data = det_data["hourly"]
            df_h = pd.DataFrame({"local_time": pd.to_datetime(h_data["time"])})
            utc_offset = det_data.get("utc_offset_seconds", 0)
            df_h["utc_time"] = df_h["local_time"] - pd.to_timedelta(utc_offset, unit='s')
            df_h["local_date"] = df_h["local_time"].dt.floor('D')

            for k, v in h_data.items():
                if k != "time":
                    df_h[k] = pd.to_numeric(v, errors='coerce')

            ifs_cols = [c for c in h_data.keys() if "ifs" in c and "aifs" not in c]
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
                    if "aifs" in k and k.startswith(variable):
                        orig = pd.Series(ens_data["daily"][k])
                        ens_data["daily"][k] = (orig + aligned_delta).tolist()

                if "daily" in det_data:
                    for k in det_data["daily"].keys():
                        if "aifs" in k and k.startswith(variable):
                            orig = pd.Series(det_data["daily"][k])
                            det_data["daily"][k] = (orig + aligned_delta).tolist()
                            
        # --- PROCESS ENSEMBLE DATA ---
        daily_ens = ens_data["daily"]
        df = pd.DataFrame({"time": pd.to_datetime(daily_ens["time"])})
        df.set_index("time", inplace=True)

        member_columns = [col for col in daily_ens.keys() if col.startswith(variable)]

        for col in member_columns:
            df[col] = daily_ens[col]

        df["ensemble_median"] = df[member_columns].median(axis=1)

        unit = ""
        if "daily_units" in ens_data:
            for k, v in ens_data["daily_units"].items():
                if k.startswith(variable):
                    unit = v
                    break

        display_name = variable.replace("_", " ").title()

        # --- BUCKET MEMBERS ---
        aifs_cols = [c for c in member_columns if "aifs" in c]
        ifs_cols = [c for c in member_columns if "ifs" in c and "aifs" not in c]
        gefs_cols = [c for c in member_columns if "gfs" in c or "gefs" in c]
        
        if not gefs_cols:
            assigned = set(aifs_cols + ifs_cols)
            gefs_cols = [c for c in member_columns if c not in assigned]
        
        df["AIFS Median"] = df[aifs_cols].median(axis=1)
        df["IFS Median"] = df[ifs_cols].median(axis=1)
        df["GEFS Median"] = df[gefs_cols].median(axis=1)

        # --- PROCESS DETERMINISTIC DATA ---
        daily_det = det_data["daily"]
        det_lines = {}
        
        k_aifs = [k for k in daily_det.keys() if k.startswith(variable) and "aifs" in k]
        if k_aifs: det_lines["AIFS Deterministic"] = daily_det[k_aifs[0]]
        
        k_ifs = [k for k in daily_det.keys() if k.startswith(variable) and "ifs" in k and "aifs" not in k]
        if k_ifs: det_lines["IFS Deterministic"] = daily_det[k_ifs[0]]
        
        k_gfs = [k for k in daily_det.keys() if k.startswith(variable) and "gfs" in k]
        if k_gfs: det_lines["GFS Deterministic"] = daily_det[k_gfs[0]]
        
        k_nbm = [k for k in daily_det.keys() if k.startswith(variable) and "nbm" in k]
        if k_nbm: det_lines["NBM (US Only)"] = daily_det[k_nbm[0]]


        # --- The Expected Forecast Readout (Always Static Table) ---
        st.markdown("---")
        if variable == "temperature_2m_max":
            st.markdown(f"### 📅 15-Day Expected Forecast (All Models) 🛠️")
            st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}** |  *🛠️ AIFS Max Temperatures have been dynamically corrected for diurnal heating using high-res models.*")
        else:
            st.markdown(f"### 📅 15-Day Expected Forecast (All Models)")
            st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}**")
        
        readout_data = {
            f"AIFS Ens Median ({unit})": df["AIFS Median"].round(1),
            f"IFS Ens Median ({unit})": df["IFS Median"].round(1),
            f"GEFS Ens Median ({unit})": df["GEFS Median"].round(1),
        }
        
        if "NBM (US Only)" in det_lines:
            nbm_vals = det_lines["NBM (US Only)"]
            nbm_series = pd.Series(nbm_vals)
            nbm_series.index = df.index[:len(nbm_series)] 
            nbm_series = nbm_series.reindex(df.index)
            readout_data[f"NBM Deterministic ({unit})"] = nbm_series.round(1)
            
        readout_data[f"Superensemble Median ({unit})"] = df["ensemble_median"].round(1)
            
        readout_df = pd.DataFrame(readout_data)
        readout_df.index = df.index.strftime('%b %d')
        readout_df = readout_df.T 
        st.dataframe(readout_df, use_container_width=True)

        # --- DYNAMIC PLOTTING & CALCULATION PREP ---
        active_members = []
        if show_aifs_ens: active_members.extend(aifs_cols)
        if show_ifs_ens: active_members.extend(ifs_cols)
        if show_gefs_ens: active_members.extend(gefs_cols)

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
        st.error(f"Failed to fetch or process data: {e}")
else:
    st.info("👈 Use the map or text boxes in the sidebar to select your location, then click 'Generate Forecast' to start.")
