import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np

# --------------------------------------------------
# Page Config & Styling
# --------------------------------------------------
st.set_page_config(page_title="Pro DV Automation Engine", layout="wide")
st.title("🛡️ Advanced Survey Data Validation Engine")

# --------------------------------------------------
# 1. Architecture: Define Validation Rules Template
# --------------------------------------------------
def generate_template():
    return pd.DataFrame({
        "Question": ["Q1", "Q2_grid", "Q3_multi", "Q4_age", "Q5_oe"],
        "Check_Type": ["Range;Missing", "Straightliner", "Multi-Select", "Skip;Range", "OpenEnd_Junk"],
        "Condition": [
            "1-5;Not Null", 
            "Threshold=1", 
            "Min=1", 
            "If Q1 IN (1,2) THEN ANSWERED; 18-99", 
            "MinLen=5"
        ],
        "Severity": ["Critical", "Warning", "Critical", "Critical", "Warning"]
    })

st.subheader("1. Setup Validation Rules")
if st.download_button("Download Rule Template", generate_template().to_csv(index=False), "DV_Rules_Template.csv"):
    st.success("Template Downloaded!")

# --------------------------------------------------
# 2. File Uploads
# --------------------------------------------------
st.divider()
col1, col2 = st.columns(2)
with col1:
    raw_file = st.file_uploader("Upload Raw Data (CSV/XLSX)", type=["csv", "xlsx"])
with col2:
    rules_file = st.file_uploader("Upload Validation Rules (XLSX/CSV)", type=["csv", "xlsx"])

if raw_file and rules_file:
    # Load Data
    df = pd.read_csv(raw_file) if raw_file.name.endswith('.csv') else pd.read_excel(raw_file)
    rules_df = pd.read_csv(rules_file) if rules_file.name.endswith('.csv') else pd.read_excel(rules_file)
    
    # Data Normalization
    df.columns = df.columns.str.strip()
    resp_id_col = df.columns[0]
    
    # --------------------------------------------------
    # 3. Validation Core Engine
    # --------------------------------------------------
    failed_rows = []

    for _, rule in rules_df.iterrows():
        q_name = str(rule["Question"]).strip()
        checks = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        conds = str(rule["Condition"])
        severity = rule.get("Severity", "Critical")

        # Identify Columns (Handles Grids like Q2_r1, Q2_r2)
        target_cols = [c for c in df.columns if c == q_name or c.startswith(q_name + "_")]
        if not target_cols:
            continue

        # --- Rule: Skip Logic Calculation ---
        # Logic: Determine if the respondent was "supposed" to answer
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                # Simple Parser for "If Q1 IN (1,2) THEN ANSWERED"
                trigger_part = conds.split("THEN")[0].replace("If", "").strip()
                base_q = trigger_part.split("IN")[0].strip()
                valid_vals = eval(trigger_part.split("IN")[1].strip())
                
                # Check if base question meets criteria
                is_required = df[base_q].isin(valid_vals)
            except:
                pass

        # --- Rule: Straightliner (Grid Only) ---
        if "Straightliner" in checks and len(target_cols) > 1:
            # Identifies if variance across row is 0
            sl_mask = (df[target_cols].nunique(axis=1) == 1) & (df[target_cols].notna().all(axis=1))
            for idx in df[sl_mask & is_required].index:
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner detected", "Severity": severity})

        # --- Rule: Multi-Select (At least N) ---
        if "Multi-Select" in checks:
            min_sel = 1
            if "Min=" in conds:
                min_sel = int(conds.split("Min=")[1].split(";")[0])
            
            # Count non-nulls in multi-select columns
            count_mask = df[target_cols].notna().sum(axis=1) < min_sel
            for idx in df[count_mask & is_required].index:
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Selected < {min_sel} options", "Severity": severity})

        # --- Rule: Range Check ---
        if "Range" in checks:
            try:
                # Extract numbers (e.g., 18-99)
                nums = [int(s) for s in conds.replace(";", " ").split() if "-" in s][0]
                low, high = map(int, nums.split("-"))
                for col in target_cols:
                    out_of_range = ~df[col].between(low, high) & df[col].notna()
                    for idx in df[out_of_range & is_required].index:
                        failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": col, "Issue": f"Value out of range ({low}-{high})", "Severity": severity})
            except:
                pass

        # --- Rule: Missing Data ---
        if "Missing" in checks or "Not Null" in conds:
            for col in target_cols:
                missing_mask = df[col].isna() & is_required
                for idx in df[missing_mask].index:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": col, "Issue": "Missing response", "Severity": severity})

    # --------------------------------------------------
    # 4. Report Generation
    # --------------------------------------------------
    st.divider()
    report_df = pd.DataFrame(failed_rows)
    
    if not report_df.empty:
        st.subheader("🚩 Validation Results")
        
        # Summary Metrics
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Errors", len(report_df))
        c2.metric("Unique Respondents with Issues", report_df["RespID"].nunique())
        c3.metric("Critical Blocks", len(report_df[report_df["Severity"] == "Critical"]))

        st.dataframe(report_df, use_container_width=True)

        # Excel Export
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
            # Create a summary tab
            summary = report_df.groupby(["Question", "Issue"]).size().reset_index(name="Count")
            summary.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button("Download Full Validation Report", output.getvalue(), "Validation_Report.xlsx")
    else:
        st.success("✅ No validation errors found! Your data is clean.")