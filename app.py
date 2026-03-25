import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go

# 1. Set up the Streamlit page layout
st.set_page_config(page_title="Superensemble Viewer", page_icon="🌤️", layout="wide")

if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False

st.title("🌤️ Open-Meteo Superensemble Viewer")
st.markdown("Compare the world's top weather models or combine them into a massive Superensemble.")

# 2. Create the Sidebar for User Inputs
st.sidebar.header("Forecast Settings")

lat = st.sidebar.number_input("Latitude", value=39.089, format="%.4f")
lon = st.sidebar.number_input("Longitude", value=-76.787, format="%.4f")

model_choice = st.sidebar.selectbox(
    "Ensemble Model",
    ["AIFS (ECMWF AI)", "IFS (ECMWF Physics)", "GEFS (NCEP/American)", "Superensemble (All 3)"]
)

# Map the UI choice to BOTH the Ensemble API and Deterministic API model strings
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

# --- UPDATED: Fetch from both APIs simultaneously ---
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
    
    return ens_response.json(), det_response.json()

# 3. Action Button
if st.sidebar.button("Generate Forecast"):
    st.session_state.data_loaded = True

if st.session_state.data_loaded:
    try:
        ens_data, det_data = get_weather_data(lat, lon, variable, selected_models["ens"], selected_models["det"])

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

        # --- The Expected Forecast Readout (Table) ---
        st.markdown("---")
        st.markdown(f"### 📅 15-Day Expected Forecast ({model_choice})")
        
        if model_choice == "Superensemble (All 3)":
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
                f"Superensemble Median ({unit})": df["ensemble_median"].round(1)
            }
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

        # --- PROCESS DETERMINISTIC DATA ---
        daily_det = det_data["daily"]
        det_lines = {}
        
        if model_choice == "Superensemble (All 3)":
            # Extract the 4 distinct lines for the Superensemble
            k_aifs = [k for k in daily_det.keys() if k.startswith(variable) and "aifs" in k]
            if k_aifs: det_lines["AIFS Deterministic"] = daily_det[k_aifs[0]]
            
            k_ifs = [k for k in daily_det.keys() if k.startswith(variable) and "ifs" in k and "aifs" not in k]
            if k_ifs: det_lines["IFS Deterministic"] = daily_det[k_ifs[0]]
            
            k_gfs = [k for k in daily_det.keys() if k.startswith(variable) and "gfs" in k]
            if k_gfs: det_lines["GFS Deterministic"] = daily_det[k_gfs[0]]
            
            k_nbm = [k for k in daily_det.keys() if k.startswith(variable) and "nbm" in k]
            if k_nbm: det_lines["NBM (US Only)"] = daily_det[k_nbm[0]]
        else:
            # Single models just get one red line
            det_name_map = {
                "AIFS (ECMWF AI)": "AIFS Deterministic",
                "IFS (ECMWF Physics)": "IFS Deterministic",
                "GEFS (NCEP/American)": "GFS Deterministic"
            }
            det_name = det_name_map[model_choice]
            var_keys = [k for k in daily_det.keys() if k.startswith(variable)]
            if var_keys:
                det_lines[det_name] = daily_det[var_keys[0]]

        # --- PLOT 1: The Box Plot ---
        fig = go.Figure()

        # 1. Background Spread
        fig.add_trace(go.Box(
            x=df_melted['time'],
            y=df_melted['Value'],
            name='Ensemble Spread',
            marker_color='steelblue',
            boxpoints='outliers',
            line=dict(width=1.5)
        ))

        # 2. Deterministic Lines
        # For superensemble, we color code them. For single models, we just use Crimson.
        super_colors = {
            "AIFS Deterministic": "crimson", 
            "IFS Deterministic": "darkorange", 
            "GFS Deterministic": "forestgreen", 
            "NBM (US Only)": "darkorchid"
        }

        for name, data_array in det_lines.items():
            color = "crimson" if model_choice != "Superensemble (All 3)" else super_colors.get(name, "crimson")
            
            fig.add_trace(go.Scatter(
                x=df.index, 
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
    st.info("👈 Set your settings in the sidebar and click 'Generate Forecast' to start.")