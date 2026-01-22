import streamlit as st
import pandas as pd
from io import BytesIO
import numpy as np
import re

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Ultimate DV Automation Engine", layout="wide")
st.title("🛡️ Ultimate Survey Data Validation Engine")

# --------------------------------------------------
# 1. Validation Rules Template
# --------------------------------------------------
st.subheader("1. Setup Validation Rules")

def generate_template():
    return pd.DataFrame({
        "Question": ["hAGE", "qAP12r", "q3", "Q9", "OE1"],
        "Check_Type": ["Range;Missing", "Skip;Multi-Select", "Skip;Range", "ConstantSum;Straightliner", "OpenEnd_Junk"],
        "Condition": [
            "1-7;Not Null", 
            "IF hAGE IN (2) THEN ANSWERED;Min=1", 
            "IF q2 IN (12) THEN ANSWERED;1-8", 
            "Total=100;Threshold=1", 
            "MinLen=5"
        ],
        "Severity": ["Critical", "Critical", "Critical", "Warning", "Warning"]
    })

template_csv = generate_template().to_csv(index=False).encode('utf-8')
st.download_button("Download Validation Rules Template", template_csv, "DV_Rules_Template.csv", "text/csv")

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
    df = pd.read_csv(raw_file) if raw_file.name.endswith('.csv') else pd.read_excel(raw_file)
    rules_df = pd.read_csv(rules_file) if rules_file.name.endswith('.csv') else pd.read_excel(rules_file)
    
    df.columns = df.columns.str.strip()
    resp_id_col = df.columns[0]
    df_numeric = df.apply(pd.to_numeric, errors='coerce')
    
    failed_rows = []

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
        
        if not target_cols:
            continue

        # --- Skip Logic Requirement Parser ---
        is_required = pd.Series(True, index=df.index)
        if "Skip" in checks:
            try:
                cond_upper = conds.upper()
                trigger = cond_upper.split("THEN")[0].replace("IF", "").strip()
                if " IN " in trigger:
                    base_q_name, val_part = trigger.split(" IN ")
                    valid_vals = eval(val_part.strip())
                    if isinstance(valid_vals, int): valid_vals = [valid_vals]
                    actual_base = next((c for c in df.columns if c.upper() == base_q_name.strip()), None)
                    if actual_base:
                        meets_trigger = df_numeric[actual_base].isin(valid_vals)
                        if "ANSWERED" in cond_upper: is_required = meets_trigger
                        elif "BLANK" in cond_upper: is_required = ~meets_trigger
            except: pass

        for idx in df.index:
            row_data = df.loc[idx, target_cols]
            row_num = df_numeric.loc[idx, target_cols]

            # 1. Skip Violation (Q3/Q12 Answered when should be blank)
            if "Skip" in checks and not is_required[idx]:
                if row_data.notna().any() and not (row_data.astype(str).str.strip() == "").all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Should be Skipped but Answered", "Severity": severity})
                    continue

            # 2. Straightliner (Grid check)
            if "Straightliner" in checks and len(target_cols) > 1:
                if row_data.nunique() == 1 and row_data.notna().all():
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Straightliner detected", "Severity": severity})

            # 3. Multi-Select (Count selections)
            if "Multi-Select" in checks and is_required[idx]:
                selected = (row_num > 0).sum()
                min_val = 1
                if "Min=" in conds: min_val = int(conds.split("Min=")[1].split(";")[0])
                if selected < min_val:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Multi-Select: {selected} selected (Min {min_val})", "Severity": severity})

            # 4. Constant Sum (Total check)
            if "ConstantSum" in checks and is_required[idx]:
                target = 100
                if "Total=" in conds: target = float(conds.split("Total=")[1].split(";")[0])
                if row_num.sum() != target:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": f"Sum is {row_num.sum()}, expected {target}", "Severity": severity})

            # 5. Open End Junk (The restored check)
            if "OpenEnd_Junk" in checks and is_required[idx]:
                text = str(row_data.values[0]).lower().strip()
                min_len = 5
                if "MinLen=" in conds: min_len = int(conds.split("MinLen=")[1].split(";")[0])
                if len(text) < min_len or text in ["asdf", "test", "none", "na"]:
                    failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Open end junk or too short", "Severity": severity})

            # 6. Missing / Range (Generic checks)
            if ("Missing" in checks) and is_required[idx] and row_data.isna().all():
                failed_rows.append({"RespID": df.loc[idx, resp_id_col], "Question": q_name, "Issue": "Missing response", "Severity": severity})

    # --------------------------------------------------
    # 4. Report Generation
    # --------------------------------------------------
    report_df = pd.DataFrame(failed_rows)
    if not report_df.empty:
        st.write(f"### 🚩 Found {len(report_df)} Errors")
        st.dataframe(report_df, use_container_width=True)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='Error_Log')
        st.download_button("Download Full Report", output.getvalue(), "DV_Report.xlsx")
    else:
        st.success("✅ Clean data! No issues found.")