import streamlit as st
import pandas as pd
from io import BytesIO
import re

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Survey Validation Engine", layout="wide")
st.title("📊 Survey Validation Rules & Report Generator")

# --------------------------------------------------
# DOWNLOAD RULE TEMPLATE (UPDATED)
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

buf = BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    template_df.to_excel(writer, index=False, sheet_name="Validation_Rules")

st.download_button(
    "📥 Download Rule Template",
    buf.getvalue(),
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

    st.success("Files uploaded successfully")

    resp_id_col = df.columns[0]
    respondent_base = df[resp_id_col].nunique()

    failed_rows = []

    # --------------------------------------------------
    # Apply Rules
    # --------------------------------------------------
    for _, rule in rules_df.iterrows():

        question = str(rule["Question"]).strip()
        check_types = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        condition = str(rule["Condition"])
        condition_parts = [c.strip() for c in condition.split(";")]

        # Detect grid columns via prefix
        grid_cols = [c for c in df.columns if c.startswith(question)]
        is_grid = len(grid_cols) > 1

        if not grid_cols and question not in df.columns:
            continue

        # --------------------------------------------------
        # STEP 1: SKIP GATING
        # --------------------------------------------------
        expected_answered = pd.Series(True, index=df.index)

        if "Skip" in check_types:
            try:
                cond, action = condition.upper().split("THEN")
                trigger = cond.replace("IF", "").strip()
                base_q, values = trigger.split("IN")

                base_q = base_q.strip()
                values = [int(v) for v in values.replace("(", "").replace(")", "").split(",")]

                for idx, row in df.iterrows():
                    base_val = row.get(base_q)
                    if base_val in values:
                        expected_answered.loc[idx] = "ANSWERED" in action
                    else:
                        expected_answered.loc[idx] = "BLANK" not in action
            except:
                pass

        # --------------------------------------------------
        # RANGE (GRID-SAFE)
        # --------------------------------------------------
        if "Range" in check_types:
            range_part = next((c for c in condition_parts if "-" in c), None)
            if range_part:
                min_v, max_v = map(float, range_part.split("-"))
                target_cols = grid_cols if is_grid else [question]

                for col in target_cols:
                    mask = (
                        expected_answered &
                        df[col].notna() &
                        ~df[col].between(min_v, max_v)
                    )
                    for _, row in df[mask].iterrows():
                        failed_rows.append({
                            "RespID": row[resp_id_col],
                            "Question": question,
                            "Issue": f"{col} out of range ({min_v}-{max_v})"
                        })

        # --------------------------------------------------
        # MISSING (GRID-SAFE)
        # --------------------------------------------------
        if "Missing" in check_types:
            target_cols = grid_cols if is_grid else [question]

            for col in target_cols:
                mask = expected_answered & df[col].isna()
                for _, row in df[mask].iterrows():
                    failed_rows.append({
                        "RespID": row[resp_id_col],
                        "Question": question,
                        "Issue": f"{col} missing"
                    })

        # --------------------------------------------------
        # STRAIGHTLINER (GRID)
        # --------------------------------------------------
        if "Straightliner" in check_types and grid_cols:
            mask = expected_answered & (df[grid_cols].nunique(axis=1) == 1)
            for _, row in df[mask].iterrows():
                failed_rows.append({
                    "RespID": row[resp_id_col],
                    "Question": question,
                    "Issue": "Straightliner detected"
                })

        # --------------------------------------------------
        # MULTI-SELECT
        # --------------------------------------------------
        if "Multi-Select" in check_types and grid_cols:
            mask = expected_answered & (df[grid_cols].fillna(0).sum(axis=1) == 0)
            for _, row in df[mask].iterrows():
                failed_rows.append({
                    "RespID": row[resp_id_col],
                    "Question": question,
                    "Issue": "No option selected"
                })

        # --------------------------------------------------
        # OPEN-END JUNK
        # --------------------------------------------------
        if "OpenEnd_Junk" in check_types and question in df.columns:
            min_len = 3
            for c in condition_parts:
                if c.upper().startswith("MINLEN"):
                    min_len = int(c.split("=")[1])

            junk_words = {"asdf", "test", "xxx", "na", "n/a", "none", "nothing"}

            for idx, row in df.iterrows():
                if not expected_answered.loc[idx]:
                    continue

                val = row.get(question)
                if pd.isna(val):
                    continue

                text = str(val).strip().lower()
                if (
                    len(text) < min_len or
                    text in junk_words or
                    re.fullmatch(r"(.)\1{3,}", text)
                ):
                    failed_rows.append({
                        "RespID": row[resp_id_col],
                        "Question": question,
                        "Issue": "Open-end junk text"
                    })

    # --------------------------------------------------
    # REPORTS
    # --------------------------------------------------
    failed_df = pd.DataFrame(failed_rows)

    if not failed_df.empty:
        summary_df = (
            failed_df.groupby("Question")
            .agg(Failed_Count=("RespID", "count"))
            .reset_index()
        )
        summary_df["% Failed"] = (
            summary_df["Failed_Count"] / respondent_base * 100
        ).round(2)
    else:
        summary_df = pd.DataFrame()

    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        failed_df.to_excel(writer, index=False, sheet_name="Failed_Checks")
        summary_df.to_excel(writer, index=False, sheet_name="Summary")

    st.download_button(
        "📥 Download Validation Report",
        out.getvalue(),
        file_name="Validation_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
