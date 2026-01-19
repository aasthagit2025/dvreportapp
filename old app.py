import streamlit as st
import pandas as pd
from io import BytesIO
import re
from openpyxl.styles import PatternFill

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Survey Validation Engine", layout="wide")
st.title("📊 Survey Validation Rules & Report Generator")

# --------------------------------------------------
# DOWNLOAD VALIDATION RULE TEMPLATE
# --------------------------------------------------
st.subheader("⬇ Download Validation Rules Template")

template_df = pd.DataFrame({
    "Question": [
        "Q1",
        "AGE",
        "Q5",
        "Q7_",
        "Q12r",
        "Q2_",
        "OE1"
    ],
    "Check_Type": [
        "Range;Missing",
        "Range;Missing",
        "Skip;Range",
        "Straightliner;Range",
        "Straightliner;Range",
        "Skip;Multi-Select",
        "Skip;OpenEnd_Junk"
    ],
    "Condition": [
        "1-5;Not Null",
        "18-65;Not Null",
        "IF Q1 IN (1,2) THEN ANSWERED ELSE BLANK;1-5",
        "Q7_1 to Q7_5;1-5",
        "Q12r1 to Q12r5;1-5",
        "IF Q3 IN (1) THEN ANSWERED ELSE BLANK;At least one selected",
        "IF Q5 IN (1) THEN ANSWERED ELSE BLANK;MinLen=3"
    ]
})

rule_buf = BytesIO()
with pd.ExcelWriter(rule_buf, engine="openpyxl") as writer:
    template_df.to_excel(writer, index=False, sheet_name="Validation_Rules")

st.download_button(
    label="📥 Download Validation Rules Template",
    data=rule_buf.getvalue(),
    file_name="Validation_Rules_Template.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# --------------------------------------------------
# Upload Section
# --------------------------------------------------
st.divider()
st.subheader("📤 Upload Files")

raw_file = st.file_uploader("Upload Raw Data (CSV / XLSX)", type=["csv", "xlsx"])
rules_file = st.file_uploader("Upload Filled Validation Rules (XLSX)", type=["xlsx"])

# --------------------------------------------------
# Validation Logic
# --------------------------------------------------
if raw_file and rules_file:

    if raw_file.name.endswith(".csv"):
        df = pd.read_csv(raw_file, low_memory=False)
    else:
        df = pd.read_excel(raw_file)

    rules_df = pd.read_excel(rules_file)

    resp_id_col = df.columns[0]
    respondent_base = df[resp_id_col].nunique()

    failed_rows = []
    highlight_cells = []  # (row_idx, col_name, error_type)

    # --------------------------------------------------
    # Apply Rules
    # --------------------------------------------------
    for _, rule in rules_df.iterrows():

        question = str(rule["Question"]).strip()
        check_types = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        condition = str(rule["Condition"])
        condition_parts = [c.strip() for c in condition.split(";")]

        grid_cols = [c for c in df.columns if c.startswith(question)]
        is_grid = len(grid_cols) > 1

        if not grid_cols and question not in df.columns:
            continue

        # --------------------------
        # Skip gating
        # --------------------------
        expected_answered = pd.Series(True, index=df.index)

        if "Skip" in check_types:
            try:
                cond, action = condition.upper().split("THEN")
                # Auto-add ELSE BLANK if missing
                if "ELSE" not in action:
                    action = action.strip() + " ELSE BLANK"
                trigger = cond.replace("IF", "").strip()
                base_q_raw, values = trigger.split("IN")

             
                base_q_raw = base_q_raw.strip().lower()
                # map rule variable to actual data column 
                if base_q_raw not in col_map:
                   continue  # invalid skip rule → ignore safely

                base_q = col_map[base_q_raw] 

                values = [v.strip() for v in values.replace("(", "").replace(")", "").split(",")].spl

                for i, row in df.iterrows():
                    base_val = str(row.get(base_q)).strip()
                    
                    if base_val in values:
                        expected_answered.loc[i] = "ANSWERED" in action
                    else:
                        expected_answered.loc[i] = "BLANK" not in action
            except Exception:
                pass

        # --------------------------
        # Range
        # --------------------------
        if "Range" in check_types:
            range_part = next((c for c in condition_parts if "-" in c), None)
            if range_part:
                min_v, max_v = map(float, range_part.split("-"))
                targets = grid_cols if is_grid else [question]

                for col in targets:
                    mask = expected_answered & df[col].notna() & ~df[col].between(min_v, max_v)
                    for i in df[mask].index:
                        failed_rows.append({
                            "RespID": df.loc[i, resp_id_col],
                            "Question": question,
                            "Issue": f"{col} out of range ({min_v}-{max_v})"
                        })
                        highlight_cells.append((i, col, "range"))

        # --------------------------
        # Missing
        # --------------------------
        if "Missing" in check_types:
            targets = grid_cols if is_grid else [question]
            for col in targets:
                mask = expected_answered & df[col].isna()
                for i in df[mask].index:
                    failed_rows.append({
                        "RespID": df.loc[i, resp_id_col],
                        "Question": question,
                        "Issue": f"{col} missing"
                    })
                    highlight_cells.append((i, col, "missing"))

        # --------------------------
        # Straightliner
        # --------------------------
        if "Straightliner" in check_types and grid_cols:
            mask = expected_answered & (df[grid_cols].nunique(axis=1) == 1)
            for i in df[mask].index:
                for col in grid_cols:
                    highlight_cells.append((i, col, "straightliner"))
                failed_rows.append({
                    "RespID": df.loc[i, resp_id_col],
                    "Question": question,
                    "Issue": "Straightliner detected"
                })

        # --------------------------
        # Multi-select (FINAL FIX)
        # --------------------------
        if "Multi-Select" in check_types and grid_cols:
      
            selected_mask = df[grid_cols].notna().any(axis=1) 
            mask = expected_answered & (~selected_mask)

            for i in df[mask].index:
                for col in grid_cols:
                    highlight_cells.append((i, col, "multiselect"))

                failed_rows.append({
                    "RespID": df.loc[i, resp_id_col],
                    "Question": question,
                    "Issue": "No option selected"
                })

        # --------------------------
        # Open-end Junk
        # --------------------------
        if "OpenEnd_Junk" in check_types and question in df.columns:
            min_len = 3
            for c in condition_parts:
                if c.upper().startswith("MINLEN"):
                    min_len = int(c.split("=")[1])

            junk_words = {"asdf", "test", "xxx", "na", "none"}

            for i, row in df.iterrows():
                if not expected_answered[i]:
                    continue

                val = row.get(question)
                if pd.isna(val):
                    continue

                text = str(val).strip().lower()
                if len(text) < min_len or text in junk_words or re.fullmatch(r"(.)\1{3,}", text):
                    failed_rows.append({
                        "RespID": row[resp_id_col],
                        "Question": question,
                        "Issue": "Open-end junk text"
                    })
                    highlight_cells.append((i, question, "oe"))

    # --------------------------------------------------
    # Reports
    # --------------------------------------------------
    failed_df = pd.DataFrame(failed_rows)

    summary_df = (
        failed_df.groupby("Question")
        .size()
        .reset_index(name="Failed_Count")
    )
    summary_df["% Failed"] = (summary_df["Failed_Count"] / respondent_base * 100).round(2)

    # --------------------------------------------------
    # Write Excel with highlighting
    # --------------------------------------------------
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:

        failed_df.to_excel(writer, index=False, sheet_name="Failed_Checks")
        summary_df.to_excel(writer, index=False, sheet_name="Summary")

        df.to_excel(writer, index=False, sheet_name="Data_With_Errors")
        ws = writer.book["Data_With_Errors"]

        fills = {
            "range": PatternFill("solid", fgColor="FF9999"),
            "missing": PatternFill("solid", fgColor="FFFF99"),
            "straightliner": PatternFill("solid", fgColor="FFCCCC"),
            "multiselect": PatternFill("solid", fgColor="99CCFF"),
            "oe": PatternFill("solid", fgColor="CCCCCC")
        }

        for row_idx, col_name, err in highlight_cells:
            col_idx = df.columns.get_loc(col_name) + 1
            ws.cell(row=row_idx + 2, column=col_idx).fill = fills[err]

    st.download_button(
        "📥 Download Validation Report",
        out.getvalue(),
        file_name="Validation_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
