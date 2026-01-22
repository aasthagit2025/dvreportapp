import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re
from openpyxl.styles import PatternFill

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Pro DV Automation Engine", layout="wide")
st.title("🛡️ Pro Survey Data Validation Engine")

# --------------------------------------------------
# 1. Validation Rules Template
# --------------------------------------------------
st.subheader("1. Setup Validation Rules")

def generate_template():
    return pd.DataFrame({
        "Question": ["hAGE", "qAP12r", "q3", "qRank", "OE1"],
        "Check_Type": ["Range;Missing", "Skip;Multi-Select", "Skip;Range", "Skip;Ranking", "Skip;OpenEnd_Junk"],
        "Condition": [
            "1-7;Not Null", 
            "IF hAGE IN (2) THEN ANSWERED ELSE BLANK;Min=1", 
            "IF q2 IN (12) THEN ANSWERED ELSE BLANK;1-8", 
            "IF q2 IN (12) THEN ANSWERED;Unique",
            "IF q3 IN (1-5) THEN ANSWERED;MinLen=5"
        ],
        "Severity": ["Critical", "Critical", "Critical", "Warning", "Warning"]
    })

st.download_button("Download Rules Template", generate_template().to_csv(index=False), "DV_Rules.csv")

# --------------------------------------------------
# 2. File Uploads
# --------------------------------------------------
st.divider()
col1, col2 = st.columns(2)
with col1:
    raw_file = st.file_uploader("Upload Raw Data (CSV/XLSX)", type=["csv", "xlsx"])
with col2:
    rules_file = st.file_uploader("Upload Validation Rules (CSV/XLSX)", type=["csv", "xlsx"])

if raw_file and rules_file:
    df = pd.read_csv(raw_file) if raw_file.name.endswith('.csv') else pd.read_excel(raw_file)
    rules_df = pd.read_csv(rules_file) if rules_file.name.endswith('.csv') else pd.read_excel(rules_file)
    
    df.columns = df.columns.str.strip()
    resp_id_col = df.columns[0]
    df_numeric = df.apply(pd.to_numeric, errors='coerce')
    
    failed_rows = []
    error_locations = []

    # --------------------------------------------------
    # 3. Validation Core Engine
    # --------------------------------------------------
    for _, rule in rules_df.iterrows():
        q_name = str(rule["Question"]).strip()
        checks = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        conds = str(rule["Condition"])
        severity = rule.get("Severity", "Critical")

        # Wildcard matching for grids and single selects
        pattern = re.compile(rf"^{re.escape(q_name)}(_r\d+|_?\d+)?$", re.IGNORECASE)
        target_cols = [c for c in df.columns if pattern.match(c)]
        
        if not target_cols:
            continue

        # --- MASTER SKIP LOGIC FILTER ---
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                cond_upper = conds.upper()
                trigger = cond_upper.split("THEN")[0].replace("IF", "").strip()
                if " IN " in trigger:
                    base_q_name, val_part = trigger.split(" IN ")
                    # Cleanup val_part for eval
                    clean_vals = val_part.strip().replace('(', '[').replace(')', ']')
                    valid_vals = eval(clean_vals)
                    if isinstance(valid_vals, int): valid_vals = [valid_vals]
                    
                    actual_base = next((c for c in df.columns if c.upper() == base_q_name.strip()), None)
                    if actual_base:
                        is_required = df_numeric[actual_base].isin(valid_vals)
            except: pass

        for idx in df.index:
            row_data = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]

            # 1. SKIP VIOLATION
            if "Skip" in checks and not is_required[idx]:
                if row_data.notna().any() and not (row_data.astype(str).str.strip() == "").all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Skip Violation: Should be Blank", "Severity": severity})
                    for col in target_cols: error_locations.append((idx, col))
                    continue 

            # 2. MISSING CHECK (Single & Multi)
            if ("Missing" in checks or "Not Null" in conds) and is_required[idx]:
                if row_data.isna().all() or (row_data.astype(str).str.strip() == "").all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response", "Severity": severity})
                    for col in target_cols: error_locations.append((idx, col))

            # 3. RANGE / SINGLE SELECT CHECK
            if "Range" in checks and is_required[idx]:
                rng_match = re.search(r"(\d+)-(\d+)", conds)
                if rng_match:
                    low, high = map(int, rng_match.groups())
                    for col in target_cols:
                        val = row_num[col]
                        if pd.notna(val) and not (low <= val <= high):
                            failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": col, "Issue": f"Out of Range ({low}-{high})", "Severity": severity})
                            error_locations.append((idx, col))

            # 4. RANKING CHECK (Unique Ranks)
            if "Ranking" in checks and is_required[idx]:
                answered_vals = row_num[row_num.notna()]
                if len(answered_vals) > len(answered_vals.unique()):
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Duplicate Ranks", "Severity": severity})
                    for col in target_cols: error_locations.append((idx, col))

            # 5. STRAIGHTLINER CHECK
            if "Straightliner" in checks and len(target_cols) > 1 and is_required[idx]:
                if row_data.nunique() == 1 and row_data.notna().all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner", "Severity": severity})
                    for col in target_cols: error_locations.append((idx, col))

            # 6. OPEN END JUNK
            if "OpenEnd_Junk" in checks and is_required[idx]:
                val_str = str(row_data.values[0]).lower().strip()
                min_l = 5
                if "MinLen=" in conds: min_l = int(conds.split("MinLen=")[1].split(";")[0])
                if len(val_str) < min_l or val_str in ["asdf", "test", "none", "na", "n/a"]:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "OE Junk/Too Short", "Severity": severity})
                    for col in target_cols: error_locations.append((idx, col))

    # --------------------------------------------------
    # 4. Excel Export with Highlights
    # --------------------------------------------------
    report_df = pd.DataFrame(failed_rows)
    if not report_df.empty:
        st.write(f"### 🚩 Found {len(report_df)} Validation Issues")
        st.dataframe(report_df)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
            df.to_excel(writer, index=False, sheet_name='Highlighted_Raw_Data')
            
            ws = writer.sheets['Highlighted_Raw_Data']
            red_fill = PatternFill(start_color='FFFF0000', end_color='FFFF0000', fill_type='solid')
            for r_idx, col_name in error_locations:
                c_idx = df.columns.get_loc(col_name) + 1
                ws.cell(row=r_idx + 2, column=c_idx).fill = red_fill
        
        st.download_button("Download Full Report & Highlighting", output.getvalue(), "Survey_Validation_Report.xlsx")
    else:
        st.success("✅ Your data passed all validation checks!")