"""
Core logic for the Contract Review Assistant.

Design principle (safety-first, per hackathon rules):
- Clause extraction is done with keyword + regex matching directly on the
  contract text. Nothing is generated -- every "clause" shown to the user
  is a verbatim substring of the uploaded contract.
- Risk comparison is done with explicit numeric rules against the company
  standard (e.g. "60 days" vs "standard: >=30 days"). No black-box model
  decides the risk level -- the rule is inspectable and explainable.
- If a clause cannot be found, the system returns the exact required
  safety string instead of guessing. It never invents a clause or a rule.
- An OPTIONAL LLM step exists only to phrase the "reason" in nicer English.
  It is given ONLY the already-extracted clause + standard text (never the
  full contract, never outside knowledge) so it cannot introduce facts
  that aren't already grounded in evidence.
"""

import re
import json
from pathlib import Path

NOT_ENOUGH_INFO = "Not enough information to make a reliable assessment."

with open(Path(__file__).parent / "standards.json", "r", encoding="utf-8") as f:
    STANDARDS = json.load(f)

# Keyword triggers used to find the paragraph that discusses each clause type.
# Keeping this in one place makes it easy to explain / extend during judging.
CLAUSE_KEYWORDS = {
    "payment": ["payment", "invoice", "pay ", "fees", "amount due"],
    "termination": ["terminat", "end this agreement", "cancel the agreement"],
    "automatic_renewal": ["automatic", "auto-renew", "renew for another", "renews for"],
    "confidentiality": ["confidential", "non-disclosure", "nda"],
    "data_protection": ["data breach", "personal data", "gdpr", "data protection", "notify"],
}

# Regex patterns to pull the specific number the risk rule needs
NUM_DAYS = re.compile(r"(\d+)\s*(?:calendar\s+)?days?", re.IGNORECASE)
NUM_HOURS = re.compile(r"(\d+)\s*hours?", re.IGNORECASE)
NUM_YEARS = re.compile(r"(\d+)\s*years?", re.IGNORECASE)

# Phrases indicating a sentence is describing the ABSENCE of a clause,
# not the clause itself (e.g. "does not include any confidentiality provisions").
NEGATION_PHRASES = [
    "does not include", "do not include", "no provision", "not include any",
    "does not contain", "excludes", "not addressed", "not covered",
]


def split_into_paragraphs(contract_text: str):
    # Split on blank lines or numbered clauses; fall back to sentences.
    raw = re.split(r"\n\s*\n|\n(?=\d+\.\s)", contract_text.strip())
    return [p.strip().replace("\n", " ") for p in raw if p.strip()]


def find_clause(contract_text: str, clause_type: str):
    """Return the verbatim paragraph/sentence discussing this clause type, or None."""
    keywords = CLAUSE_KEYWORDS[clause_type]
    paragraphs = split_into_paragraphs(contract_text)
    for para in paragraphs:
        low = para.lower()
        if any(neg in low for neg in NEGATION_PHRASES):
            continue  # this sentence is describing an absence, not a real clause
        if any(kw in low for kw in keywords):
            return para
    return None


def extract_number(clause_text: str, rule: str):
    if rule in ("max_days", "min_notice_days"):
        m = NUM_DAYS.search(clause_text)
    elif rule == "max_hours":
        m = NUM_HOURS.search(clause_text)
    elif rule == "min_years":
        m = NUM_YEARS.search(clause_text)
    else:
        m = None
    return int(m.group(1)) if m else None


def assess_clause(contract_text: str, clause_type: str):
    """
    Returns a dict with: clause_type, contract_clause, standard_text, source,
    risk_level, reason, evidence_ok (bool)
    """
    std = STANDARDS[clause_type]
    contract_clause = find_clause(contract_text, clause_type)

    if not contract_clause:
        return {
            "clause_type": clause_type,
            "label": std["label"],
            "contract_clause": None,
            "standard_text": std["standard_text"],
            "source": std["source"],
            "risk_level": "Not Enough Information",
            "reason": NOT_ENOUGH_INFO,
            "evidence_ok": False,
        }

    number = extract_number(contract_clause, std["rule"])
    threshold = std["threshold"]

    if number is None:
        return {
            "clause_type": clause_type,
            "label": std["label"],
            "contract_clause": contract_clause,
            "standard_text": std["standard_text"],
            "source": std["source"],
            "risk_level": "Medium Risk",
            "reason": (
                "A clause of this type was found, but the specific number "
                "(days/hours/years) could not be reliably extracted. "
                "Human review is required to confirm the exact terms."
            ),
            "evidence_ok": True,
        }

    direction = std["direction"]
    if direction == "lower_is_better":       # e.g. payment window
        risk = "Low Risk" if number <= threshold else ("High Risk" if number > threshold * 1.5 else "Medium Risk")
        cmp_word = "within" if number <= threshold else "beyond"
    elif direction == "higher_is_better":     # e.g. termination notice, confidentiality survival
        risk = "Low Risk" if number >= threshold else ("High Risk" if number < threshold * 0.5 else "Medium Risk")
        cmp_word = "meets or exceeds" if number >= threshold else "falls short of"
    elif direction == "lower_is_better_for_customer":  # auto-renewal notice: MORE notice required = worse for customer
        risk = "Low Risk" if number <= threshold else ("High Risk" if number > threshold * 1.5 else "Medium Risk")
        cmp_word = "matches or is more lenient than" if number <= threshold else "requires more advance notice than"
    else:
        risk = "Medium Risk"
        cmp_word = "differs from"

    reason = (
        f"The contract states {number} (vs. standard threshold {threshold}), "
        f"which {cmp_word} the company standard."
    )

    return {
        "clause_type": clause_type,
        "label": std["label"],
        "contract_clause": contract_clause,
        "standard_text": std["standard_text"],
        "source": std["source"],
        "risk_level": risk,
        "reason": reason,
        "evidence_ok": True,
        "extracted_number": number,
        "threshold": threshold,
    }


def assess_contract(contract_text: str, clause_types=None):
    clause_types = clause_types or list(STANDARDS.keys())
    return [assess_clause(contract_text, ct) for ct in clause_types]


# ---------------------------------------------------------------------------
# Optional LLM polish -- ONLY rewrites the reason sentence in plain English.
# It is given the already-extracted clause + standard text, nothing else,
# so it cannot fabricate facts not already present in the evidence.
# ---------------------------------------------------------------------------
def polish_reason_with_llm(api_key: str, contract_clause: str, standard_text: str,
                            risk_level: str, base_reason: str):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "You are rewriting a risk explanation for a contract review tool. "
        "Rewrite the REASON in one simple, plain-English sentence. "
        "Use ONLY the two texts given below. Do not add any fact, number, "
        "or rule that is not already present in them. Do not give legal advice.\n\n"
        f"Contract clause: {contract_clause}\n"
        f"Company standard: {standard_text}\n"
        f"Risk level already decided by rules: {risk_level}\n"
        f"Draft reason: {base_reason}\n\n"
        "Rewrite the reason sentence only, nothing else:"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()
