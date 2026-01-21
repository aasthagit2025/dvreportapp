import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re

# --------------------------------------------------
# Page Config & Styling
# --------------------------------------------------
st.set_page_config(page_title="Pro DV Automation Engine", layout="wide")
st.title("🛡️ Advanced Survey Data Validation Engine")

# --------------------------------------------------
# 1. Architecture: Define Validation Rules Template
# --------------------------------------------------
st.subheader("1. Setup Validation Rules")

# Restored and expanded template generation
def generate_template():
    return pd.DataFrame({
        "Question": ["hAGE", "qAP12r", "Q3", "Q9", "OE1"],
        "Check_Type": ["Range;Missing", "Skip;Multi-Select", "Skip;Range", "ConstantSum", "OpenEnd_Junk"],
        "Condition": [
            "1-7;Not Null", 
            "IF hAGE IN (2) THEN ANSWERED;Min=1", 
            "IF Q2 IN (12) THEN ANSWERED;1-8", 
            "Total=100", 
            "MinLen=5"
        ],
        "Severity": ["Critical", "Critical", "Critical", "Warning", "Warning"]
    })

# Ensuring the download button is properly placed and functional
template_csv = generate_template().to_csv(index=False).encode('utf-8')
st.download_button(
    label="Download Validation Rules Template",
    data=template_csv,
    file_name="DV_Rules_Template.csv",
    mime="text/csv"
)

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
    df_numeric = df.apply(pd.to_numeric, errors='coerce')
    
    # --------------------------------------------------
    # 3. Validation Core Engine
    # --------------------------------------------------
    failed_rows = []

    for _, rule in rules_df.iterrows():
        q_name = str(rule["Question"]).strip()
        checks = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        conds = str(rule["Condition"])
        severity = rule.get("Severity", "Critical")

        # RESOLVE COLUMNS: Uses Regex to handle wildcards (e.g., Q9 matches Q9_r1, Q9_r2)
        pattern = re.compile(rf"^{re.escape(q_name)}(_r\d+|_?\d+)?$", re.IGNORECASE)
        target_cols = [c for c in df.columns if pattern.match(c)]
        
        if not target_cols:
            continue

        # --- Logic: Determine if Required (Skip Logic) ---
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                # Parser for "If Q1 IN (1,2) THEN ANSWERED"
                trigger_part = conds.upper().split("THEN")[0].replace("IF", "").strip()
                base_q_name, val_part = trigger_part.split("IN")
                valid_vals = eval(val_part.strip())
                
                # Find base column in data
                actual_base_col = next((c for c in df.columns if c.upper() == base_q_name.strip()), None)
                if actual_base_col:
                    is_required = df_numeric[actual_base_col].isin(valid_vals)
            except:
                pass

        for idx in df.index:
            row_data = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]

            # --- Rule: Multi-Select (Count non-zeros/non-nulls) ---
            if "Multi-Select" in checks and is_required[idx]:
                selected_count = (row_num > 0).sum()
                min_val = 1
                if "Min=" in conds:
                    min_val = int(conds.split("Min=")[1].split(";")[0])
                elif "At least one" in conds:
                    min_val = 1
                
                if selected_count < min_val:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Multi-Select: Only {selected_count} selected (Min {min_val})", "Severity": severity})

            # --- Rule: Missing Data ---
            if ("Missing" in checks or "Not Null" in conds) and is_required[idx]:
                if row_data.isna().all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response", "Severity": severity})

            # --- Rule: Range Check ---
            if "Range" in checks and is_required[idx]:
                rng_match = re.search(r"(\d+)-(\d+)", conds)
                if rng_match:
                    low, high = map(int, rng_match.groups())
                    for col in target_cols:
                        val = row_num[col]
                        if pd.notna(val) and not (low <= val <= high):
                            failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": col, "Issue": f"Value out of range ({low}-{high})", "Severity": severity})

            # --- Rule: Constant Sum ---
            if "ConstantSum" in checks and is_required[idx]:
                target_total = 100
                if "Total=" in conds:
                    target_total = float(conds.split("Total=")[1].split(";")[0])
                
                if row_num.sum() != target_total:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Sum is {row_num.sum()}, expected {target_total}", "Severity": severity})

            # --- Rule: Straightliner (Grid Only) ---
            if "Straightliner" in checks and len(target_cols) > 1:
                if row_data.nunique() == 1 and row_data.notna().all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner detected", "Severity": severity})

            # --- Rule: Open End Junk ---
            if "OpenEnd_Junk" in checks:
                text = str(row_data.values[0]).lower().strip()
                min_len = 5
                if "MinLen=" in conds:
                    min_len = int(conds.split("MinLen=")[1].split(";")[0])
                if len(text) < min_len or text in ["asdf", "none", "na", "test"]:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Junk or too short", "Severity": severity})

    # --------------------------------------------------
    # 4. Report Generation
    # --------------------------------------------------
    st.divider()
    report_df = pd.DataFrame(failed_rows)
    
    if not report_df.empty:
        st.subheader("🚩 Validation Results")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Errors", len(report_df))
        c2.metric("Unique Respondents", report_df["RespID"].nunique())
        c3.metric("Critical Issues", len(report_df[report_df["Severity"] == "Critical"]))

        st.dataframe(report_df, use_container_width=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
            summary = report_df.groupby(["Question", "Issue"]).size().reset_index(name="Count")
            summary.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button("Download Full Validation Report", output.getvalue(), "Validation_Report.xlsx")
    else:
        st.success("✅ No validation errors found! Your data is clean.")