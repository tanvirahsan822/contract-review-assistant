import streamlit as st
from pathlib import Path
from engine import assess_contract, STANDARDS, polish_reason_with_llm

st.set_page_config(page_title="Contract Review Assistant", page_icon="📄", layout="wide")

RISK_COLORS = {
    "Low Risk": "#2e7d32",
    "Medium Risk": "#f9a825",
    "High Risk": "#c62828",
    "Not Enough Information": "#616161",
}

SAMPLE_DIR = Path(__file__).parent / "sample_contracts"

if "review_status" not in st.session_state:
    st.session_state.review_status = {}
if "feedback" not in st.session_state:
    st.session_state.feedback = {}

st.title("📄 Contract Review Assistant")
st.caption(
    "An AI-assisted tool that finds contract clauses, compares them with company "
    "standards, and shows risk **with evidence**. This tool does not give legal "
    "advice — a human reviewer always makes the final decision."
)

# ---------------------------------------------------------------------
# Sidebar: contract selection + settings
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("1. Select a Contract")
    sample_files = sorted(SAMPLE_DIR.glob("*.txt"))
    sample_names = [f.name for f in sample_files]
    choice = st.radio("Sample contracts", sample_names + ["Upload / paste my own"])

    if choice == "Upload / paste my own":
        uploaded = st.file_uploader("Upload a .txt contract", type=["txt"])
        pasted = st.text_area("...or paste contract text", height=200)
        contract_text = uploaded.read().decode("utf-8") if uploaded else pasted
    else:
        contract_text = (SAMPLE_DIR / choice).read_text(encoding="utf-8")

    st.header("2. Clause Types")
    all_types = list(STANDARDS.keys())
    selected_types = st.multiselect(
        "Select at least 3 clause types to review",
        options=all_types,
        default=all_types,
        format_func=lambda x: STANDARDS[x]["label"],
    )

    st.header("3. Optional: AI Phrasing")
    use_llm = st.toggle("Use LLM to polish explanation wording", value=False)
    api_key = ""
    if use_llm:
        api_key = st.text_input("Anthropic API key", type="password")
        st.caption(
            "The model only ever sees the already-extracted clause + standard "
            "text — never the full contract, never outside knowledge. It cannot "
            "change the risk level, only the wording of the explanation."
        )

run = st.button("🔍 Run Review", type="primary", use_container_width=True)

# ---------------------------------------------------------------------
# Main: results
# ---------------------------------------------------------------------
if run:
    if not contract_text or not contract_text.strip():
        st.error("Please select, upload, or paste a contract first.")
    elif len(selected_types) < 3:
        st.error("Please select at least 3 clause types (hackathon requirement).")
    else:
        with st.expander("📃 Full contract text", expanded=False):
            st.text(contract_text)

        results = assess_contract(contract_text, selected_types)
        st.session_state["results"] = results

if "results" in st.session_state:
    results = st.session_state["results"]
    st.subheader("Review Results")

    for r in results:
        color = RISK_COLORS[r["risk_level"]]
        with st.container(border=True):
            top_col1, top_col2 = st.columns([3, 1])
            with top_col1:
                st.markdown(f"### {r['label']}")
            with top_col2:
                st.markdown(
                    f"<div style='background-color:{color};color:white;padding:6px 12px;"
                    f"border-radius:6px;text-align:center;font-weight:600'>{r['risk_level']}</div>",
                    unsafe_allow_html=True,
                )

            if not r["evidence_ok"]:
                st.warning(r["reason"])
                st.caption("No clause of this type was found in the contract text. "
                           "No result is shown, per the system's safety rule.")
            else:
                ev1, ev2 = st.columns(2)
                with ev1:
                    st.markdown("**Contract Clause (verbatim):**")
                    st.info(r["contract_clause"])
                with ev2:
                    st.markdown("**Matching Company Standard:**")
                    st.success(r["standard_text"])

                reason_text = r["reason"]
                if use_llm and api_key:
                    try:
                        reason_text = polish_reason_with_llm(
                            api_key, r["contract_clause"], r["standard_text"],
                            r["risk_level"], r["reason"]
                        )
                    except Exception as e:
                        st.caption(f"(LLM polish unavailable, showing rule-based reason: {e})")

                st.markdown(f"**Reason:** {reason_text}")
                st.caption(f"Source: {r['source']}")

            # --- Human-in-the-loop controls ---
            st.markdown("**Human Review Required**")
            key = r["clause_type"]
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("✅ Approve", key=f"approve_{key}"):
                st.session_state.review_status[key] = "Approved"
            if b2.button("❌ Reject", key=f"reject_{key}"):
                st.session_state.review_status[key] = "Rejected"
            if b3.button("🔖 Mark for Review", key=f"mark_{key}"):
                st.session_state.review_status[key] = "Marked for Review"
            status = st.session_state.review_status.get(key)
            if status:
                b4.markdown(f"**Status:** {status}")

            fb = st.text_input("Add feedback (optional)", key=f"fb_{key}",
                                value=st.session_state.feedback.get(key, ""))
            st.session_state.feedback[key] = fb

    st.divider()
    st.caption(
        "⚠️ This tool is an assistant only. It does not provide legal advice and "
        "does not make final decisions. All results must be confirmed by a human reviewer."
    )
