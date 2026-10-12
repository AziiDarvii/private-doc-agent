"""Local PII / PHI detection. Runs on the laptop; nothing is sent anywhere."""
import re
from typing import Dict, List

PII_PATTERNS = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "Credit card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "Email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "Phone": re.compile(r"(?:\+1[ .-]?)?\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    "Date of birth": re.compile(r"\b(?:DOB|Date of birth)[:\s]+(?:\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]+ \d{1,2}, \d{4})", re.IGNORECASE),
    "Medical record #": re.compile(r"\b(?:MRN|Medical record(?: number)?)[:#\s]+[\w-]{4,}", re.IGNORECASE),
    "Bank account": re.compile(r"\b(?:account|acct)(?: number| no\.?)?[:#\s]+\d{6,}", re.IGNORECASE),
}


def _mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def find_pii(text: str) -> List[Dict[str, object]]:
    found = []
    for label, pattern in PII_PATTERNS.items():
        matches = [m.group(0) for m in pattern.finditer(text)]
        if matches:
            found.append({"type": label, "count": len(matches), "examples": [_mask(m) for m in matches[:3]]})
    return found
