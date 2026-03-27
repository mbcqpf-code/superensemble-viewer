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

model_choice = st.sidebar.selectbox(
    "Ensemble Model",
    ["AIFS (ECMWF AI)", "IFS (ECMWF Physics)", "GEFS (NCEP/American)", "Superensemble (All 3)"]
)

# Using the validated model mapping strings provided
model_mapping = {
    "AIFS (ECMWF AI)": {
        "ens": "ecmwf_aifs025_ensemble", 
        "det": "ecmwf_aifs025_single"
    },
    "IFS (ECMWF Physics)": {
        "ens": "ecmwf_ifs025_ensemble", 
        "det": "ecmwf_ifs025"
    },
    "GEFS (NCEP/American)": {
        "ens": "gfs_seamless", 
        "det": "gfs_seamless"
    },
    "Superensemble (All 3)": {
        "ens": "ecmwf_aifs025_ensemble,ecmwf_ifs025_ensemble,gfs_seamless", 
        "det": "ecmwf_aifs025_single,ecmwf_ifs025,gfs_seamless,ncep_nbm_conus"
    }
}
selected_models = model_mapping[model_choice]

variable = st.sidebar.selectbox(
    "Weather Variable",
    ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
    format_func=lambda x: x.replace("_", " ").title()
)

# --- REVERTED CACHE: Clean two-call API approach ---
@st.cache_data(ttl=3600, show_spinner="Fetching ensemble and deterministic data...")
def get_weather_data(lat, lon, var, ens_models, det_models):
    base_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": var,
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "forecast_days": 15
    }
    
    # 1. Fetch Ensembles
    ens_params = base_params.copy()
    ens_params["models"] = ens_models
    ens_response = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params=ens_params)
    ens_response.raise_for_status()
    
    # 2. Fetch Deterministic Runs
    det_params = base_params.copy()
    det_params["models"] = det_models
    det_response = requests.get("https://api.open-meteo.com/v1/forecast", params=det_params)
    det_response.raise_for_status()
    
    fetch_time = datetime.now().strftime('%b %d, %Y at %I:%M %p')
            
    return ens_response.json(), det_response.json(), fetch_time

# 3. Action Button
if st.sidebar.button("Generate Forecast", type="primary", use_container_width=True):
    st.session_state.data_loaded = True

if st.session_state.data_loaded:
    try:
        is_super = model_choice == "Superensemble (All 3)"
        ens_data, det_data, fetch_time = get_weather_data(lat, lon, variable, selected_models["ens"], selected_models["det"])

        # --- PROCESS ENSEMBLE DATA ---
        daily_ens = ens_data["daily"]
        df = pd.DataFrame({"time": pd.to_datetime(daily_ens["time"])})
        df.set_index("time", inplace=True)

        member_columns = [col for col in daily_ens.keys() if col.startswith(variable)]

        for col in member_columns:
            df[col] = daily_ens[col]

        df["ensemble_median"] = df[member_columns].median(axis=1)

        # Get units robustly
        unit = ""
        if "daily_units" in ens_data:
            for k, v in ens_data["daily_units"].items():
                if k.startswith(variable):
                    unit = v
                    break

        display_name = variable.replace("_", " ").title()

        # --- PROCESS DETERMINISTIC DATA ---
        daily_det = det_data["daily"]
        det_lines = {}
        
        if is_super:
            k_aifs = [k for k in daily_det.keys() if k.startswith(variable) and "aifs" in k]
            if k_aifs: det_lines["AIFS Deterministic"] = daily_det[k_aifs[0]]
            
            k_ifs = [k for k in daily_det.keys() if k.startswith(variable) and "ifs" in k and "aifs" not in k]
            if k_ifs: det_lines["IFS Deterministic"] = daily_det[k_ifs[0]]
            
            k_gfs = [k for k in daily_det.keys() if k.startswith(variable) and "gfs" in k]
            if k_gfs: det_lines["GFS Deterministic"] = daily_det[k_gfs[0]]
            
            k_nbm = [k for k in daily_det.keys() if k.startswith(variable) and "nbm" in k]
            if k_nbm: det_lines["NBM (US Only)"] = daily_det[k_nbm[0]]
        else:
            det_name_map = {
                "AIFS (ECMWF AI)": "AIFS Deterministic",
                "IFS (ECMWF Physics)": "IFS Deterministic",
                "GEFS (NCEP/American)": "GFS Deterministic"
            }
            target_name = det_name_map[model_choice]
            var_keys = [k for k in daily_det.keys() if k.startswith(variable)]
            if var_keys:
                det_lines[target_name] = daily_det[var_keys[0]]


        # --- The Expected Forecast Readout (Table) ---
        st.markdown("---")
        st.markdown(f"### 📅 15-Day Expected Forecast ({model_choice})")
        st.caption(f"Data dynamically fetched from Open-Meteo on: **{fetch_time}**")
        
        if is_super:
            aifs_cols = [c for c in member_columns if "aifs" in c]
            ifs_cols = [c for c in member_columns if "ifs" in c and "aifs" not in c]
            gefs_cols = [c for c in member_columns if "gfs" in c or "gefs" in c]
            
            if not gefs_cols:
                assigned = set(aifs_cols + ifs_cols)
                gefs_cols = [c for c in member_columns if c not in assigned]
            
            df["AIFS Median"] = df[aifs_cols].median(axis=1)
            df["IFS Median"] = df[ifs_cols].median(axis=1)
            df["GEFS Median"] = df[gefs_cols].median(axis=1)
            
            readout_data = {
                f"AIFS Ens Median ({unit})": df["AIFS Median"].round(1),
                f"IFS Ens Median ({unit})": df["IFS Median"].round(1),
                f"GEFS Ens Median ({unit})": df["GEFS Median"].round(1),
            }
            
            # Inject NBM into the table using Pandas to safely align lengths
            if "NBM (US Only)" in det_lines:
                nbm_vals = det_lines["NBM (US Only)"]
                nbm_series = pd.Series(nbm_vals)
                nbm_series.index = df.index[:len(nbm_series)] 
                nbm_series = nbm_series.reindex(df.index)
                readout_data[f"NBM Deterministic ({unit})"] = nbm_series.round(1)
                
            readout_data[f"Superensemble Median ({unit})"] = df["ensemble_median"].round(1)
            
        else:
            readout_data = {
                f"Expected Ens Median {display_name} ({unit})": df["ensemble_median"].round(1)
            }
            
        readout_df = pd.DataFrame(readout_data)
        readout_df.index = df.index.strftime('%b %d')
        readout_df = readout_df.T 
        st.dataframe(readout_df, use_container_width=True)

        # Reshape ensemble data for the Box Plot
        df_melted = df.reset_index().melt(
            id_vars=['time'], 
            value_vars=member_columns, 
            var_name='Member', 
            value_name='Value'
        )

        # --- PLOT 1: The Box Plot ---
        fig = go.Figure()

        # Background Spread
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

        # Deterministic Lines
        for name, data_array in det_lines.items():
            color = "black" if not is_super else super_colors.get(name, "black")
            
            fig.add_trace(go.Scatter(
                x=df.index[:len(data_array)], 
                y=data_array, 
                mode='lines+markers',
                line=dict(color=color, width=2.5),
                marker=dict(size=6),
                name=name
            ))

        fig.update_layout(
            title=f"Forecast Spread vs Deterministic Runs: {display_name}<br><sup>Lat: {lat}, Lon: {lon} | Members Used: {len(member_columns)}</sup>",
            xaxis_title="Date",
            yaxis_title=f"Value ({unit})",
            hovermode="x unified",
            showlegend=True,
            margin=dict(l=20, r=20, t=60, b=20)
        )

        st.plotly_chart(fig, use_container_width=True)
        
        # --- Threshold Probability Calculator ---
        st.markdown("---")
        st.markdown(f"### 📊 Threshold Probability Calculator ({len(member_columns)} Members)")
        
        col1, col2 = st.columns(2)
        with col1:
            default_val = 32.0 if "temperature" in variable else 1.0
            threshold = st.number_input(f"Target Threshold ({unit})", value=default_val)
        with col2:
            condition = st.selectbox("Condition", ["Greater than or equal to (≥)", "Less than or equal to (≤)"])

        total_members = len(member_columns)
        if "Greater" in condition:
            probabilities = (df[member_columns] >= threshold).sum(axis=1) / total_members * 100
            operator_symbol = "≥"
        else:
            probabilities = (df[member_columns] <= threshold).sum(axis=1) / total_members * 100
            operator_symbol = "≤"

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