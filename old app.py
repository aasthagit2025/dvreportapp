import streamlit as st
import pandas as pd
from io import BytesIO

# --------------------------------------------------
# Page Config
# --------------------------------------------------
st.set_page_config(page_title="Survey Validation Engine", layout="wide")
st.title("📊 Survey Validation Rules & Report Generator")

# --------------------------------------------------
# Download Rule Template
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
    template_df.to_excel(writer, index=False, sheet_name="Validation_Rules")

st.download_button(
    label="📥 Download Rule Template",
    data=buf.getvalue(),
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

    # Read raw data
    if raw_file.name.endswith(".csv"):
        df = pd.read_csv(raw_file, encoding="utf-8", low_memory=False)
    else:
        df = pd.read_excel(raw_file)

    # Read rules
    rules_df = pd.read_excel(rules_file)

    st.success("Files uploaded successfully")

    # Respondent ID = FIRST COLUMN (MANDATORY)
    resp_id_col = df.columns[0]

    failed_rows = []

    # --------------------------
    # Apply Rules
    # --------------------------
    for _, rule in rules_df.iterrows():

        question = str(rule["Question"]).strip()
        check_types = [c.strip() for c in str(rule["Check_Type"]).split(";")]
        condition = str(rule["Condition"])

        # Skip rule if question not in data
        if question not in df.columns:
            continue

        # --------------------------
        # Range Check
        # --------------------------
        if "Range" in check_types and "-" in condition:
            try:
                min_v, max_v = map(float, condition.split("-"))
                mask = ~df[question].between(min_v, max_v, inclusive="both")

                for _, row in df[mask].iterrows():
                    failed_rows.append({
                        "RespID": row[resp_id_col],
                        "Question": question,
                        "Issue": f"Out of range ({min_v}-{max_v})"
                    })
            except:
                pass

        # --------------------------
        # Missing Check
        # --------------------------
        if "Missing" in check_types:
            mask = df[question].isna()

            for _, row in df[mask].iterrows():
                failed_rows.append({
                    "RespID": row[resp_id_col],
                    "Question": question,
                    "Issue": "Missing value"
                })

        # --------------------------
        # Placeholder hooks (future-ready)
        # --------------------------
        # Skip logic
        # Straightliner
        # Multi-select
        # Open-end junk / AI detection

    # --------------------------
    # Final Report
    # --------------------------
    failed_report = pd.DataFrame(failed_rows)

    if failed_report.empty:
        st.info("✅ No validation failures found based on applied rules.")
    else:
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            failed_report.to_excel(writer, index=False, sheet_name="Failed_Checks")

        st.download_button(
            label="📥 Download Validation Report (Failed Only)",
            data=out.getvalue(),
            file_name="Validation_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
