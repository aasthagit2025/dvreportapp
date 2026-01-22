import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re
from openpyxl.styles import PatternFill

# --------------------------------------------------
# Page Config & Branding
# --------------------------------------------------
st.set_page_config(page_title="Ultimate Survey DV Engine", layout="wide")
st.title("🛡️ Professional Survey Data Validation Engine")

# --------------------------------------------------
# 1. Resources & Setup
# --------------------------------------------------
st.subheader("1. Resources & Setup")
col_dl1, col_dl2 = st.columns(2)

with col_dl1:
    def generate_template():
        return pd.DataFrame({
            "Question": ["hAGE", "qAP12r", "q3", "hFAVr", "Q9_sum", "OE1"],
            "Check_Type": ["Range;Missing", "Skip;Multi-Select", "Skip;Range", "Skip;Straightliner", "ConstantSum", "Skip;OpenEnd_Junk"],
            "Condition": [
                "1-7;Not Null", 
                "IF hAGE IN (2) THEN ANSWERED ELSE BLANK;Min=1", 
                "IF q2 IN (12) THEN ANSWERED ELSE BLANK;1-8", 
                "IF q1 IN (1) THEN ANSWERED;Threshold=1",
                "Total=100",
                "IF q3 IN (1-5) THEN ANSWERED;MinLen=10"
            ],
            "Severity": ["Critical", "Critical", "Critical", "Warning", "Critical", "Warning"]
        })
    st.download_button("📥 Download Rules Template", generate_template().to_csv(index=False), "DV_Rules_Template.csv")

with col_dl2:
    try:
        # Note: Ensure 'DV_Syntax_Macro.xlsm' is in your deployment folder
        with open("DV_Syntax_Macro.xlsm", "rb") as f:
            st.download_button("📑 Download DV Syntax Macro", f, "DV_Syntax_Macro.xlsm")
    except FileNotFoundError:
        st.info("💡 Upload 'DV_Syntax_Macro.xlsm' to enable the macro download button.")

# --------------------------------------------------
# 2. File Uploads
# --------------------------------------------------
st.divider()
u_col1, u_col2 = st.columns(2)
with u_col1:
    raw_file = st.file_uploader("Upload Raw Data (CSV/XLSX)", type=["csv", "xlsx"])
with u_col2:
    rules_file = st.file_uploader("Upload Validation Rules (CSV/XLSX)", type=["csv", "xlsx"])

if raw_file and rules_file:
    # Load Data
    df = pd.read_csv(raw_file) if raw_file.name.endswith('.csv') else pd.read_excel(raw_file)
    rules_df = pd.read_csv(rules_file) if rules_file.name.endswith('.csv') else pd.read_excel(rules_file)
    
    # Preprocessing
    df.columns = df.columns.str.strip()
    resp_id_col = df.columns[0] # Assuming 1st col is RespID
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

        # Column Mapping (Wildcard Support for Grids)
        pattern = re.compile(rf"^{re.escape(q_name)}(_.*)?$", re.IGNORECASE)
        target_cols = [c for c in df.columns if pattern.match(c)]
        if not target_cols: continue

        # --- REFINED SKIP/REQUIREMENT PARSER ---
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
            except: pass

        for idx in df.index:
            row_raw = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]
            
            # Meaningful Data Flags
            any_ans = row_raw.notna().any() and not (row_raw.astype(str).str.strip() == "").all()
            all_ans = row_raw.notna().all() and not (row_raw.astype(str).str.strip() == "").any()

            # A. SKIP VIOLATION: Should be blank but contains data
            if "Skip" in checks and not is_required[idx] and any_ans:
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Skip Violation: Should be Blank", "Severity": severity})
                rows_with_errors.add(idx)
                for col in target_cols: error_locations.append((idx, col))
                continue 

            # B. REVERSE SKIP / MISSING / GRID INTEGRITY
            if is_required[idx]:
                if not any_ans:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response (Required)", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))
                elif not all_ans:
                    # Specific check for grids: All columns must be filled
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Partial Data: Grid incomplete", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols:
                        if pd.isna(df.loc[idx, col]) or str(df.loc[idx, col]).strip() == "":
                            error_locations.append((idx, col))

            # C. CONSTANT SUM CHECK
            if "ConstantSum" in checks and is_required[idx] and any_ans:
                target_sum = 100
                sum_match = re.search(r"Total=(\d+)", conds)
                if sum_match: target_sum = int(sum_match.group(1))
                
                actual_sum = row_num.sum()
                if actual_sum != target_sum:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Sum Error: Expected {target_sum}, got {actual_sum}", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # D. RANGE / SINGLE SELECT
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

            # E. MULTI-SELECT (Min/Max Count)
            if "Multi-Select" in checks and is_required[idx]:
                select_count = (row_num > 0).sum()
                min_v = 1
                if "Min=" in conds:
                    try: min_v = int(re.search(r"Min=(\d+)", conds).group(1))
                    except: pass
                if select_count < min_v:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Selection Count: {select_count} (Min {min_v})", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # F. STRAIGHTLINER
            if "Straightliner" in checks and len(target_cols) > 1 and all_ans:
                if row_raw.nunique() == 1:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner (Flat-lining)", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

            # G. OPEN END JUNK
            if "OpenEnd_Junk" in checks and is_required[idx] and any_ans:
                text_val = str(row_raw.iloc[0]).lower().strip()
                min_l = 5
                if "MinLen=" in conds:
                    try: min_l = int(re.search(r"MinLen=(\d+)", conds).group(1))
                    except: pass
                
                junk_list = ["asdf", "test", "none", "na", "n/a", "nothing", "abc", "good", "...", "nil"]
                if len(text_val) < min_l or text_val in junk_list or len(set(text_val)) < 3:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "OE Quality: Junk/Too Short", "Severity": severity})
                    rows_with_errors.add(idx)
                    for col in target_cols: error_locations.append((idx, col))

    # --------------------------------------------------
    # 4. Export & Results
    # --------------------------------------------------
    report_df = pd.DataFrame(failed_rows)
    if not report_df.empty:
        st.write(f"### 🚩 Found {len(report_df)} Validation Issues")
        st.dataframe(report_df, use_container_width=True)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
            
            # Summary Table
            summary = report_df.groupby(["Question", "Issue"]).size().reset_index(name="Count")
            summary.to_excel(writer, index=False, sheet_name='Summary')
            
            # Highlighted Raw Data
            df.to_excel(writer, index=False, sheet_name='Highlighted_Data')
            ws = writer.sheets['Highlighted_Data']
            red_fill = PatternFill(start_color='FFFF0000', end_color='FFFF0000', fill_type='solid')
            
            # Highlight specific cell errors
            for r_idx, col_name in error_locations:
                c_idx = df.columns.get_loc(col_name) + 1
                ws.cell(row=r_idx + 2, column=c_idx).fill = red_fill
            
            # Highlight RespID for any row with issues
            rid_idx = df.columns.get_loc(resp_id_col) + 1
            for r_idx in rows_with_errors:
                ws.cell(row=r_idx + 2, column=rid_idx).fill = red_fill

        st.download_button("💾 Download Full Highlighted Report", output.getvalue(), "Survey_DV_Report.xlsx")
    else:
        st.success("✅ Clean Data! No validation issues found.")