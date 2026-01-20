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
# Download Validation Rule Template
# --------------------------------------------------
st.subheader("⬇ Download Validation Rules Template")

template_df = pd.DataFrame({
    "Question": [
        "Q1", "Q4_r1", "Q4a", "Q9_", "Q11_", "Q2_", "Q3_", "AGE", "OE1"
    ],
    "Check_Type": [
        "Range;Missing",
        "Range",
        "Skip;OpenEnd_Junk",
        "Straightliner",
        "Straightliner",
        "Multi-Select",
        "Skip",
        "Range",
        "OpenEnd_Junk"
    ],
    "Condition": [
        "1-5;Not Null",
        "1-11",
        "If Q4_r1 IN (10,11) THEN ANSWERED ELSE BLANK;MinLen=3",
        "Q9_r1 to Q9_r9",
        "Q11_r1 to Q11_r12",
        "At least one selected",
        "If Q2_1=1 THEN ANSWERED ELSE BLANK",
        "18-65",
        "Detect junk or AI text"
    ]
})

buf = BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    template_df.to_excel(writer, index=False)

st.download_button(
    "Download Validation Rules Template",
    buf.getvalue(),
    "Validation_Rules_Template.xlsx"
)

# --------------------------------------------------
# Upload Section
# --------------------------------------------------
st.divider()
st.subheader("Upload Files")

raw_file = st.file_uploader("Upload Raw Data (CSV / XLSX)", ["csv", "xlsx"])
rules_file = st.file_uploader("Upload Validation Rules (XLSX)", ["xlsx"])

# --------------------------------------------------
# STOP if files not uploaded
# --------------------------------------------------
if raw_file is None or rules_file is None:
    st.info("Please upload both Raw Data and Validation Rules files.")
    st.stop()

# --------------------------------------------------
# Read Raw Data (SAFE)
# --------------------------------------------------
if raw_file.name.lower().endswith(".csv"):
    df = pd.read_csv(raw_file, low_memory=False)
else:
    df = pd.read_excel(raw_file, engine="openpyxl")

rules_df = pd.read_excel(rules_file, engine="openpyxl")

# --------------------------------------------------
# Normalize columns
# --------------------------------------------------
df.columns = df.columns.str.strip()
resp_id_col = df.columns[0]

for col in df.columns:
    if col == resp_id_col:
        continue
    df[col] = (
        df[col]
        .astype(str)
        .str.strip()
        .replace({"": None, "nan": None})
    )
    df[col] = pd.to_numeric(df[col], errors="ignore")

col_map = {c.lower(): c for c in df.columns}
respondent_base = df[resp_id_col].nunique()

failed_rows = []
highlight_cells = []

# ==================================================
# APPLY VALIDATION RULES (FINAL ENGINE)
# ==================================================

debug_rows = []          # per-rule debug info
failed_rows = []
highlight_cells = []

for rule_idx, rule in rules_df.iterrows():

    question = str(rule["Question"]).strip()
    check_types = [c.strip() for c in str(rule["Check_Type"]).split(";")]
    condition = str(rule["Condition"])
    cond_parts = [c.strip() for c in condition.split(";")]

    # ------------------------------
    # Resolve columns
    # ------------------------------
    grid_cols = [c for c in df.columns if c.startswith(question)]
    is_grid = len(grid_cols) > 1

    if not grid_cols and question not in df.columns:
        continue

    target_cols = grid_cols if grid_cols else [question]

    # ------------------------------
    # STEP 1: SKIP BASE CALCULATION
    # ------------------------------
    skip_ok = pd.Series(True, index=df.index)

    if "Skip" in check_types:
        try:
            cond_part, then_part = condition.upper().split("THEN", 1)
            if "ELSE" not in then_part:
                then_part += " ELSE BLANK"

            trigger = cond_part.replace("IF", "").strip()
            base_q_raw, values_raw = trigger.split("IN", 1)
            base_q = col_map.get(base_q_raw.strip().lower())

            values = [
                float(v.strip())
                for v in values_raw.replace("(", "").replace(")", "").split(",")
            ]

            for i in df.index:
                base_val = df.loc[i, base_q]
                if pd.isna(base_val):
                    skip_ok.loc[i] = False
                elif float(base_val) in values:
                    skip_ok.loc[i] = "ANSWERED" in then_part
                else:
                    skip_ok.loc[i] = "BLANK" in then_part
        except:
            pass

    skip_violation = ~skip_ok

    # ------------------------------
    # STEP 2: REPORT SKIP VIOLATIONS
    # ------------------------------
    if "Skip" in check_types:
        for i in df.index:
            if skip_violation.loc[i] and df.loc[i, target_cols].notna().any():
                for col in target_cols:
                    highlight_cells.append((i, col, "skip"))
                failed_rows.append({
                    "RespID": df.loc[i, resp_id_col],
                    "Question": question,
                    "Issue": "Skip violation"
                })

    # ------------------------------
    # STEP 3: RANGE (ONLY skip_ok)
    # ------------------------------
    if "Range" in check_types:
        rng = next((c for c in cond_parts if "-" in c), None)
        if rng:
            lo, hi = map(float, rng.split("-"))
            for col in target_cols:
                bad = (
                    skip_ok
                    & df[col].notna()
                    & ~df[col].between(lo, hi)
                )
                for i in df[bad].index:
                    highlight_cells.append((i, col, "range"))
                    failed_rows.append({
                        "RespID": df.loc[i, resp_id_col],
                        "Question": question,
                        "Issue": f"{col} out of range ({lo}-{hi})"
                    })

    # ------------------------------
    # STEP 4: MISSING (ONLY skip_ok)
    # ------------------------------
    if "Missing" in check_types:
        for col in target_cols:
            bad = skip_ok & df[col].isna()
            for i in df[bad].index:
                highlight_cells.append((i, col, "missing"))
                failed_rows.append({
                    "RespID": df.loc[i, resp_id_col],
                    "Question": question,
                    "Issue": f"{col} missing"
                })

    # ------------------------------
    # STEP 5: STRAIGHTLINER (GRID ONLY)
    # ------------------------------
    if "Straightliner" in check_types and is_grid:
        bad = skip_ok & (df[grid_cols].nunique(axis=1) == 1)
        for i in df[bad].index:
            for col in grid_cols:
                highlight_cells.append((i, col, "straightliner"))
            failed_rows.append({
                "RespID": df.loc[i, resp_id_col],
                "Question": question,
                "Issue": "Straightliner"
            })

    # ------------------------------
    # STEP 6: MULTI-SELECT (GRID)
    # ------------------------------
    if "Multi-Select" in check_types and is_grid:
        bad = skip_ok & (df[grid_cols].notna().sum(axis=1) == 0)
        for i in df[bad].index:
            for col in grid_cols:
                highlight_cells.append((i, col, "multiselect"))
            failed_rows.append({
                "RespID": df.loc[i, resp_id_col],
                "Question": question,
                "Issue": "No option selected"
            })

    # ------------------------------
    # STEP 7: OPEN-END JUNK
    # ------------------------------
    if "OpenEnd_Junk" in check_types and question in df.columns:
        min_len = 3
        for c in cond_parts:
            if c.upper().startswith("MINLEN"):
                min_len = int(c.split("=")[1])

        junk_words = {"asdf", "test", "xxx", "na", "none"}

        for i in df.index:
            if not skip_ok.loc[i]:
                continue
            val = df.loc[i, question]
            if pd.isna(val):
                continue
            txt = str(val).strip().lower()
            if len(txt) < min_len or txt in junk_words:
                highlight_cells.append((i, question, "oe"))
                failed_rows.append({
                    "RespID": df.loc[i, resp_id_col],
                    "Question": question,
                    "Issue": "Open-end junk"
                })

    # ------------------------------
    # DEBUG INFO (PER RULE)
    # ------------------------------
    debug_rows.append({
        "Question": question,
        "Skip_OK_Count": int(skip_ok.sum()),
        "Skip_Violation_Count": int(skip_violation.sum()),
        "Total_Rows": len(df)
    })

# --------------------------------------------------
# Reports
# --------------------------------------------------
failed_df = pd.DataFrame(failed_rows)

summary_df = (
    failed_df.groupby("Question").size().reset_index(name="Failed_Count")
    if not failed_df.empty
    else pd.DataFrame(columns=["Question", "Failed_Count"])
)

# --------------------------------------------------
# Export
# --------------------------------------------------
out = BytesIO()
with pd.ExcelWriter(out, engine="openpyxl") as writer:
    failed_df.to_excel(writer, index=False, sheet_name="Failed_Checks")
    summary_df.to_excel(writer, index=False, sheet_name="Summary")
    df.to_excel(writer, index=False, sheet_name="Data_With_Errors")

st.download_button("Download Validation Report", out.getvalue(), "Validation_Report.xlsx")