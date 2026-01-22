import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re
from openpyxl.styles import PatternFill

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Ultimate Survey DV Engine", layout="wide")
st.title("🛡️ Professional Survey Data Validation Engine")

# --------------------------------------------------
# 1. Validation Rules Template
# --------------------------------------------------
st.subheader("1. Setup Validation Rules")

def generate_template():
    return pd.DataFrame({
        "Question": ["hAGE", "qAP12r", "q3", "qRank", "OE1"],
        "Check_Type": ["Range;Missing", "Skip;Multi-Select", "Skip;Range", "Skip;Ranking", "Skip;OpenEnd_Junk;Straightliner"],
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
    rows_with_errors = set() 

    # --------------------------------------------------
    # 3. Validation Core Engine
    # --------------------------------------------------
    for _, rule in rules_df.iterrows():
        q_name = str(rule["Question"]).strip()
        checks = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        conds = str(rule["Condition"])
        severity = rule.get("Severity", "Critical")

        pattern = re.compile(rf"^{re.escape(q_name)}(_r\d+|_?\d+)?$", re.IGNORECASE)
        target_cols = [c for c in df.columns if pattern.match(c)]
        if not target_cols: continue

        # --- MASTER SKIP/REQUIREMENT PARSER ---
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                cond_upper = conds.upper()
                trigger_match = re.search(r"IF\s+(.*?)\s+THEN", cond_upper)
                if trigger_match:
                    trigger = trigger_match.group(1)
                    if " IN " in trigger:
                        base_q, val_part = trigger.split(" IN ")
                        val_str = val_part.strip().replace('(', '[').replace(')', ']').replace('-', ',')
                        valid_vals = eval(val_str)
                        if isinstance(valid_vals, int): valid_vals = [valid_vals]
                        
                        actual_base = next((c for c in df.columns if c.upper() == base_q.strip()), None)
                        if actual_base:
                            meets_cond = df_numeric[actual_base].isin(valid_vals)
                            # Logic: If condition met, it's required. If not met, it's NOT required.
                            is_required = meets_cond
            except: pass

        for idx in df.index:
            row_data = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]
            
            # Boolean Flags
            any_answered = row_data.notna().any() and not (row_data.astype(str).str.strip() == "").all()
            all_answered = row_data.notna().all() and not (row_data.astype(str).str.strip() == "").any()

            # 1. SKIP VIOLATION (Answered when should be Blank)
            if "Skip" in checks and not is_required[idx] and any_answered:
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Skip Violation: Should be Blank", "Severity": severity})
                rows_with_errors.add(idx)
                for col in target_cols: error_locations.append((idx, col))
                continue 

            # 2. MISSING CHECK (Blank or Partial when should be Answered)
            # Check for grid completion specifically
            if is_required[idx]:
                if not any_answered:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response (Required)", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))
                elif not all_answered and len(target_cols) > 1:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Partial Grid: Some rows are blank", "Severity": severity})
                    rows_with_errors.add(idx)
                    # Highlight only the blank cells in the grid
                    for col in target_cols:
                        if pd.isna(df.loc[idx, col]): error_locations.append((idx, col))

            # 3. RANGE / SINGLE SELECT
            if "Range" in checks and is_required[idx] and any_answered:
                rng = re.search(r"(\d+)-(\d+)", conds)
                if rng:
                    low, high = map(int, rng.groups())
                    for col in target_cols:
                        val = row_num[col]
                        if pd.notna(val) and not (low <= val <= high):
                            failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": col, "Issue": f"Out of Range ({low}-{high})", "Severity": severity})
                            rows_with_errors.add(idx)
                            error_locations.append((idx, col))

            # 4. MULTI-SELECT (Restored logic)
            if "Multi-Select" in checks and is_required[idx]:
                select_count = (row_num > 0).sum()
                min_v = 1
                if "Min=" in conds:
                    try: min_v = int(re.search(r"Min=(\d+)", conds).group(1))
                    except: pass
                if select_count < min_v:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Multi-Select: {select_count} selected (Min {min_v})", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # 5. STRAIGHTLINER (Robust)
            if "Straightliner" in checks and len(target_cols) > 1 and all_answered:
                if row_data.nunique() == 1:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner detected", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # 6. RANKING (Robust Uniqueness)
            if "Ranking" in checks and is_required[idx] and any_answered:
                clean_ranks = row_num.dropna()
                if len(clean_ranks) != len(clean_ranks.unique()):
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Ranking Error: Duplicates", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # 7. OPEN END JUNK (Robust)
            if "OpenEnd_Junk" in checks and is_required[idx] and any_answered:
                text_val = str(row_data.iloc[0]).lower().strip()
                min_l = 5
                if "MinLen=" in conds:
                    try: min_l = int(re.search(r"MinLen=(\d+)", conds).group(1))
                    except: pass
                junk = ["asdf", "test", "none", "na", "abc", "n/a", "nothing", "good"]
                if len(text_val) < min_l or text_val in junk or len(set(text_val)) < 3:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "OE Junk or Too Short", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

    # --------------------------------------------------
    # 4. Report Generation & Summary
    # --------------------------------------------------
    report_df = pd.DataFrame(failed_rows)
    if not report_df.empty:
        st.write(f"### 🚩 Found {len(report_df)} Validation Issues")
        st.dataframe(report_df, use_container_width=True)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
            
            # Create Summary Tab
            summary = report_df.groupby(["Question", "Issue"]).size().reset_index(name="Error_Count")
            summary.to_excel(writer, index=False, sheet_name='Summary')
            
            # Highlighted Raw Data
            df.to_excel(writer, index=False, sheet_name='Highlighted_Data')
            ws = writer.sheets['Highlighted_Data']
            red_fill = PatternFill(start_color='FFFF0000', end_color='FFFF0000', fill_type='solid')
            
            # Highlight Cells
            for r_idx, col_name in error_locations:
                c_idx = df.columns.get_loc(col_name) + 1
                ws.cell(row=r_idx + 2, column=c_idx).fill = red_fill
            
            # Highlight RespIDs
            rid_idx = df.columns.get_loc(resp_id_col) + 1
            for r_idx in rows_with_errors:
                ws.cell(row=r_idx + 2, column=rid_idx).fill = red_fill

        st.download_button("Download Full Report & Highlighting", output.getvalue(), "Final_Validation_Report.xlsx")
    else:
        st.success("✅ Your data is clean!")