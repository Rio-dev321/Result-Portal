import io
import re
import requests
import pdfplumber
import pandas as pd
import streamlit as st
from groq import Groq

# -------------------- Page setup --------------------
st.set_page_config(
    page_title="Imran Public School | Result Portal",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.stApp { background: #08090d; color: #f4f4f5; }
[data-testid="stHeader"] { background: rgba(8,9,13,0.92); }
.block-container { max-width: 1120px; padding-top: 2rem; padding-bottom: 3rem; }
.hero {
    padding: 28px 30px; border: 1px solid #292b36; border-radius: 20px;
    background: linear-gradient(135deg, #151720 0%, #0d0f16 65%, #101827 100%);
    margin-bottom: 22px;
}
.eyebrow { color: #a5b4fc; letter-spacing: 2px; font-size: 12px; font-weight: 700; }
.hero h1 { margin: 8px 0 6px 0; font-size: clamp(28px, 4vw, 42px); color: #ffffff; }
.muted { color: #a1a1aa; }
.result-card {
    padding: 22px; border: 1px solid #303241; border-radius: 16px;
    background: #11131b; margin-top: 18px;
}
.metric {
    padding: 16px; border-radius: 12px; border: 1px solid #303241;
    background: #0c0e14;
}
.metric-label { color: #a1a1aa; font-size: 12px; }
.metric-value { font-size: 25px; font-weight: 750; margin-top: 5px; }
div.stButton > button {
    border-radius: 10px; min-height: 44px; font-weight: 700;
}
div[data-testid="stForm"] {
    border: 1px solid #292b36; border-radius: 16px; padding: 20px;
    background: #101118;
}
footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# Public Google Drive file ID from the link supplied for this project.
DRIVE_FILE_ID = "1uWupFm2LUEFyDQImS_bDUxwNSowiI0hY"

def normalize_header(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[\n_\-./]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text

def classify_column(header):
    h = normalize_header(header)
    if any(k in h for k in ["father", "guardian", " والد", "والد", "والدہ"]):
        return "Father Name"
    if any(k in h for k in ["roll", "registration", "reg no", "student id", "admission no"]):
        return "Roll Number"
    if any(k in h for k in ["obtained", "marks obtained", "score", " حاصل کردہ"]):
        return "Obtained Marks"
    if any(k in h for k in ["total", "maximum marks", "max marks", "کل نمبر"]):
        return "Total Marks"
    if any(k in h for k in ["student name", "name of student", "candidate name", "نام طالب", "نام"]):
        return "Student Name"
    if "class" in h or "grade" in h:
        return "Class"
    return None

def clean_cell(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

@st.cache_data(ttl=900, show_spinner=False)
def load_results_from_drive():
    """Download a publicly shared Drive PDF and attempt to extract its tables."""
    url = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    response = requests.get(url, timeout=35, allow_redirects=True)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(
            "Google Drive did not return a PDF. Check that link sharing is set to "
            "'Anyone with the link – Viewer', or upload the PDF directly."
        )

    extracted = []
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table or len(table) < 2:
                    continue
                cleaned = [[clean_cell(c) for c in row] for row in table if row]
                if not cleaned:
                    continue
                header_idx = None
                mapping = {}
                for idx, row in enumerate(cleaned[:5]):
                    candidate = {}
                    for col_idx, cell in enumerate(row):
                        mapped = classify_column(cell)
                        if mapped:
                            candidate[mapped] = col_idx
                    if "Student Name" in candidate and (
                        "Obtained Marks" in candidate or "Total Marks" in candidate
                    ):
                        header_idx, mapping = idx, candidate
                        break
                if header_idx is None:
                    continue
                for row in cleaned[header_idx + 1:]:
                    def get(key):
                        pos = mapping.get(key)
                        return row[pos] if pos is not None and pos < len(row) else ""
                    name = get("Student Name")
                    father = get("Father Name")
                    obtained = get("Obtained Marks")
                    total = get("Total Marks")
                    roll = get("Roll Number")
                    klass = get("Class")
                    if name and any(ch.isalpha() for ch in name) and (obtained or total):
                        extracted.append({
                            "Roll Number": roll,
                            "Student Name": name,
                            "Father Name": father,
                            "Class": klass,
                            "Obtained Marks": obtained,
                            "Total Marks": total,
                        })

    # Remove duplicate rows that may appear in repeated PDF table headers/pages.
    frame = pd.DataFrame(extracted)
    if frame.empty:
        raise ValueError(
            "I downloaded the PDF, but could not recognise its table columns. "
            "The PDF may be scanned or use different headings. Please provide a "
            "text-based PDF with clear columns, or adapt the column mapping in app.py."
        )
    frame = frame.drop_duplicates().reset_index(drop=True)
    return frame

def safe_number(value):
    digits = re.sub(r"[^0-9.]", "", str(value or ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None

def show_ai_help(question):
    try:
        api_key = st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        api_key = ""
    if not api_key:
        st.info("AI help is optional. Add GROQ_API_KEY in Streamlit Secrets to enable it.")
        return
    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the friendly help assistant for Imran Public School's "
                        "result portal. Answer general questions about how to use the "
                        "portal. Never ask for or reveal any student's marks or personal "
                        "data. If asked about a specific result, explain that the student "
                        "must use the result lookup form."
                    ),
                },
                {"role": "user", "content": question},
            ],
            temperature=0.3,
            max_completion_tokens=350,
        )
        st.markdown(completion.choices[0].message.content)
    except Exception as exc:
        st.error(f"AI help is temporarily unavailable. Check the Groq key and model settings. ({exc})")

st.markdown("""
<div class="hero">
  <div class="eyebrow">STUDENT SERVICES · ACADEMIC SESSION</div>
  <h1>Imran Public School</h1>
  <div style="font-size:18px;color:#d4d4d8;">Student Result Portal</div>
  <p class="muted" style="margin-bottom:0;">Check your result securely using the details requested below.</p>
</div>
""", unsafe_allow_html=True)

with st.spinner("Loading result records…"):
    try:
        results = load_results_from_drive()
        data_error = None
    except Exception as exc:
        results = pd.DataFrame()
        data_error = str(exc)

if data_error:
    st.error("Result data could not be loaded.")
    st.caption(data_error)
    st.markdown(
        "Make sure the Google Drive PDF is shared as **Anyone with the link — Viewer**. "
        "This first version can extract tables from text-based PDFs; scanned PDFs may need OCR."
    )
    st.stop()

st.markdown("### Find your result")
st.markdown('<p class="muted">Enter the details exactly as printed on your result record.</p>', unsafe_allow_html=True)

name_col, father_col = st.columns(2)
with name_col:
    student_name = st.text_input("Student name", placeholder="Enter your full name")
with father_col:
    father_name = st.text_input("Father's name", placeholder="Enter your father's full name")

roll_col, class_col = st.columns(2)
has_roll = "Roll Number" in results.columns and results["Roll Number"].fillna("").astype(str).str.strip().ne("").any()
has_class = "Class" in results.columns and results["Class"].fillna("").astype(str).str.strip().ne("").any()
with roll_col:
    roll_number = st.text_input("Roll number (if available)", placeholder="Enter roll number") if has_roll else ""
with class_col:
    class_name = st.text_input("Class (if available)", placeholder="Enter class") if has_class else ""

if st.button("Check My Result", type="primary", use_container_width=True):
    if not student_name.strip() or not father_name.strip():
        st.warning("Please enter both the student name and father's name.")
    else:
        matches = results[
            results["Student Name"].fillna("").astype(str).str.strip().str.casefold()
            .eq(student_name.strip().casefold())
            & results["Father Name"].fillna("").astype(str).str.strip().str.casefold()
            .eq(father_name.strip().casefold())
        ]
        if has_roll and roll_number.strip():
            matches = matches[
                matches["Roll Number"].fillna("").astype(str).str.strip().str.casefold()
                .eq(roll_number.strip().casefold())
            ]
        if has_class and class_name.strip():
            matches = matches[
                matches["Class"].fillna("").astype(str).str.strip().str.casefold()
                .eq(class_name.strip().casefold())
            ]

        if matches.empty:
            st.error("No matching result was found. Check the spelling and try again.")
        elif len(matches) > 1:
            st.warning("More than one record matched. Add the roll number or class to identify the correct record.")
        else:
            row = matches.iloc[0]
            obtained = safe_number(row.get("Obtained Marks", ""))
            total = safe_number(row.get("Total Marks", ""))
            percent = (obtained / total * 100) if obtained is not None and total not in (None, 0) else None

            st.markdown('<div class="result-card">', unsafe_allow_html=True)
            st.success("Result record found")
            st.subheader(str(row.get("Student Name", "Student")))
            st.caption("IMRAN PUBLIC SCHOOL · RESULT STATEMENT")
            info1, info2, info3 = st.columns(3)
            with info1:
                st.markdown(f'<div class="metric"><div class="metric-label">Obtained Marks</div><div class="metric-value">{row.get("Obtained Marks", "—") or "—"}</div></div>', unsafe_allow_html=True)
            with info2:
                st.markdown(f'<div class="metric"><div class="metric-label">Total Marks</div><div class="metric-value">{row.get("Total Marks", "—") or "—"}</div></div>', unsafe_allow_html=True)
            with info3:
                shown_percent = f"{percent:.2f}%" if percent is not None else "—"
                st.markdown(f'<div class="metric"><div class="metric-label">Percentage</div><div class="metric-value">{shown_percent}</div></div>', unsafe_allow_html=True)

            details = {}
            for label in ["Father Name", "Roll Number", "Class"]:
                value = clean_cell(row.get(label, ""))
                if value:
                    details[label] = value
            if details:
                st.divider()
                for label, value in details.items():
                    st.write(f"**{label}:** {value}")
            st.markdown('</div>', unsafe_allow_html=True)

st.divider()
with st.expander("Need help using the portal? Ask the AI assistant"):
    question = st.text_area(
        "General portal question",
        placeholder="Example: What details do I need to check my result?",
        max_chars=500,
    )
    if st.button("Ask AI assistant"):
        if question.strip():
            show_ai_help(question.strip())
        else:
            st.warning("Please type a question first.")

st.markdown(
    '<p class="muted" style="text-align:center;font-size:12px;margin-top:30px;">'
    '© Imran Public School · Result Portal prototype</p>',
    unsafe_allow_html=True,
)
