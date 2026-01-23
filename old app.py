import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re
from openpyxl.styles import PatternFill

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Survey DV Engine", layout="wide")
st.title("🛡️ Survey Data Validation Engine")

# --------------------------------------------------
# 1. Validation Rules Template
# --------------------------------------------------
st.subheader("Setup Validation Rules")
col_dl1, col_dl2 = st.columns(2)

with col_dl1:
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

with col_dl2:
    # --- MACRO DOWNLOAD LOGIC ---
    try:
        with open("DV_Syntax_Macro.xlsm", "rb") as f:
            st.download_button(
                label="📑 Download DV Syntax Macro",
                data=f,
                file_name="DV_Syntax_Macro.xlsm",
                mime="application/vnd.ms-excel.sheet.macroEnabled.12"
            )
    except FileNotFoundError:
        st.warning("⚠️ 'DV_Syntax_Macro.xlsm' not found in folder. Please add it to enable download.")

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


# --- 3. Validation Core Engine ---
    for _, rule in rules_df.iterrows():
        q_name = str(rule["Question"]).strip()
        checks = str(rule["Check_Type"])
        conds = str(rule["Condition"])
        severity = rule.get("Severity", "Critical")

        # --- STEP 1: SMART COLUMN SELECTION (FIXED INDENTATION) ---
        # Look for exact match first to prevent RQ1 matching RQ11/RQ15
        target_cols = [c for c in df.columns if c.lower() == q_name.lower()]
        
        # If no exact match, look for grid children (e.g., RQ9_1, RQ9_2)
        if not target_cols:
            if q_name[-1].isdigit():
                # If name ends in digit (RQ1), suffix must be non-digit (prevents RQ11 match)
                pattern = re.compile(rf"^{re.escape(q_name)}(_|[a-zA-Z]).*$", re.IGNORECASE)
            else:
                # If name ends in letter (RQ), suffix can be anything
                pattern = re.compile(rf"^{re.escape(q_name)}(_|[a-zA-Z]|\d)+$", re.IGNORECASE)
            target_cols = [c for c in df.columns if pattern.match(c)]
        
        if not target_cols:
            continue

        # --- STEP 2: SKIP LOGIC (AIGNS WITH COLUMN SELECTION) ---
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                trigger_match = re.search(r"IF\s+(.*?)\s+THEN", conds.upper())
                if trigger_match:
                    trigger = trigger_match.group(1)
                    if " IN " in trigger:
                        base_q, val_part = trigger.split(" IN ")
                        val_str = val_part.strip().replace('(', '[').replace(')', ']').replace('-', ',')
                        valid_vals = eval(val_str)
                        if isinstance(valid_vals, int): valid_vals = [valid_vals]
                        actual_base = next((c for c in df.columns if c.upper() == base_q.strip()), None)
                        if actual_base:
                            is_required = df_numeric[actual_base].isin(valid_vals)
            except:
                pass

        # --- STEP 3: ROW VALIDATION ---
        for idx in df.index:
            row_raw = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]
            
            any_ans = row_raw.notna().any() and not (row_raw.astype(str).str.strip() == "").all()
            all_ans = row_raw.notna().all() and not (row_raw.astype(str).str.strip() == "").any()

            # Skip Violation
            if "Skip" in checks and not is_required[idx] and any_ans:
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Skip Violation", "Severity": severity})
                rows_with_errors.add(idx)
                for col in target_cols: error_locations.append((idx, col))
                continue 

            # Missing/Grid Check
            if is_required[idx]:
                if not any_ans:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))
                elif not all_ans and len(target_cols) > 1:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Incomplete Grid", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols:
                        if pd.isna(df.loc[idx, col]): error_locations.append((idx, col))


            # 3. RANGE / SINGLE SELECT
            if "Range" in checks and is_required[idx] and any_ans:
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
            if "Straightliner" in checks and len(target_cols) > 1 and all_ans:
                if row_data.nunique() == 1:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner detected", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # 6. RANKING (Robust Uniqueness)
            if "Ranking" in checks and is_required[idx] and any_ans:
                clean_ranks = row_num.dropna()
                if len(clean_ranks) != len(clean_ranks.unique()):
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Ranking Error: Duplicates", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # 7. OPEN END JUNK (Robust)
            if "OpenEnd_Junk" in checks and is_required[idx] and any_ans:
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

                    #8. Constant Sum
            if "ConstantSum" in checks and is_required[idx] and any_ans:
                target_sum = 100
                if "Total=" in conds:
                    try: target_sum = float(re.search(r"Total=(\d+)", conds).group(1))
                    except: pass
                if row_num.sum() != target_sum:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Sum {row_num.sum()} != {target_sum}", "Severity": severity})
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