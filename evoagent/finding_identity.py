"""Canonical finding identities shared by review and evaluation code."""

import re
from typing import Any


# The evaluator compares canonical CWE identities, not reviewer-specific rule names.
RULE_TO_CWE = {
    "SEC-EVAL": "CWE-95",
    "SEC-SUBPROCESS-SHELL": "CWE-78",
    "SEC-HARDCODED-SECRET": "CWE-798",
    "SEC-SQL-CONCAT": "CWE-89",
    "REL-EMPTY-EXCEPT": "CWE-703",
    "REL-DEBUG-PRINT": "CWE-532",
    "SEC-PATH-TRAVERSAL": "CWE-22",
    "SEC-YAML-LOAD": "CWE-502",
    "SEC-WEAK-HASH": "CWE-328",
    "SEC-INSECURE-TEMPFILE": "CWE-377",
    "SEC-WEAK-RANDOM": "CWE-330",
    "REL-UNBOUNDED-RETRY": "CWE-835",
    "SEC-ASSERT-AUTH": "CWE-617",
    "SEC-INSECURE-COOKIE": "CWE-614",
    "SEC-PICKLE-LOAD": "CWE-502",
    "REL-FLOAT-MONEY": "CWE-682",
    "REL-NAIVE-DATETIME": "CWE-367",
    "REL-BLOCKING-ASYNC": "CWE-400",
    "REL-NONATOMIC-WRITE": "CWE-362",
    "SEC-OPEN-REDIRECT": "CWE-601",
    "SEC-LOG-FORGING": "CWE-117",
}

_CWE_RE = re.compile(r"^CWE-[0-9]+$", re.IGNORECASE)


def canonical_cwe(rule_id: Any = "", cwe: Any = "") -> str:
    """Return a canonical CWE, preferring an explicit valid CWE field."""
    explicit = str(cwe or "").strip().upper()
    if _CWE_RE.fullmatch(explicit):
        return explicit
    rule = str(rule_id or "").strip().upper()
    mapped = RULE_TO_CWE.get(rule, "")
    return mapped.upper() if mapped else ""


def is_valid_cwe(value: Any) -> bool:
    """Validate the transport format for an explicit CWE value."""
    return bool(_CWE_RE.fullmatch(str(value or "").strip()))


def canonical_identity(rule_id: Any = "", cwe: Any = "") -> str:
    """Return the scoring identity, with a legacy rule fallback.

    Known rules and explicit CWE values are scored by CWE. Unknown legacy rule
    IDs remain matchable only against the exact same legacy ID; an arbitrary
    model-generated ID cannot match a CWE-labelled truth.
    """
    value = canonical_cwe(rule_id, cwe)
    if value:
        return value
    rule = str(rule_id or "").strip().upper()
    return "RULE:" + rule if rule else ""
