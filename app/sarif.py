"""SARIF 2.1.0 export for completed validation runs.

Maps the four diagnostic types (broken links, broken anchors, anchor
collisions, cycles) onto a single SARIF run with one rule per type.
Result order is made fully deterministic here rather than relying on
database collation, so the same validation always serializes identically.

See the SARIF v2.1.0 OASIS standard:
https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""
from __future__ import annotations

from urllib.parse import quote

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

TOOL_NAME = "markdown-reference-validator"

# Diagnostic severities to SARIF result levels (SARIF allows only these).
_LEVELS = {"error": "error", "warning": "warning"}

# Canonical rule order; ruleIndex in results refers into this list.
_RULE_ORDER = ("broken_link", "broken_anchor", "anchor_collision", "cycle")

_RULE_METADATA = {
    "broken_link": {
        "name": "BrokenMarkdownLink",
        "level": "error",
        "short": "Broken markdown link",
        "full": (
            "A relative markdown link target does not resolve to any document "
            "in the imported set."
        ),
    },
    "broken_anchor": {
        "name": "BrokenMarkdownAnchor",
        "level": "error",
        "short": "Broken markdown anchor",
        "full": (
            "The linked document exists, but its fragment does not match any "
            "heading anchor (GitHub-style slug or explicit {#id}) in that document."
        ),
    },
    "anchor_collision": {
        "name": "DuplicateHeadingAnchor",
        "level": "warning",
        "short": "Duplicate or ambiguous heading anchor",
        "full": (
            "Two headings resolve to the same anchor: either the generated "
            "slugs collide (ambiguous anchor) or an explicit anchor is defined "
            "more than once."
        ),
    },
    "cycle": {
        "name": "CircularReference",
        "level": "warning",
        "short": "Circular document reference",
        "full": (
            "The document participates in a cross-document reference cycle "
            "(a strongly connected component, including self-references)."
        ),
    },
}


def build_rules() -> tuple[list[dict], dict[str, int]]:
    """Return the SARIF rules array and a type -> ruleIndex map."""
    rules: list[dict] = []
    index: dict[str, int] = {}
    for rule_id in _RULE_ORDER:
        meta = _RULE_METADATA[rule_id]
        index[rule_id] = len(rules)
        rules.append(
            {
                "id": rule_id,
                "name": meta["name"],
                "shortDescription": {"text": meta["short"]},
                "fullDescription": {"text": meta["full"]},
                "defaultConfiguration": {"level": meta["level"]},
            }
        )
    return rules, index


def _artifact_uri(path: str) -> str:
    """Percent-encode a document path as a valid relative URI reference
    (spaces become %20; '/' and CJK characters stay readable)."""
    return quote(path, safe="/")


def _result_location(diag: dict) -> list[dict]:
    """Physical location of the offending link/heading, or document level
    for cycle diagnostics which carry no line number."""
    physical: dict = {"artifactLocation": {"uri": _artifact_uri(diag["path"])}}
    if diag["line"] is not None:
        physical["region"] = {"startLine": diag["line"]}
    return [{"physicalLocation": physical}]


def _result(diag: dict, rule_index: dict[str, int]) -> dict:
    rule_id = diag["type"]
    result: dict = {
        "ruleId": rule_id,
        "ruleIndex": rule_index[rule_id],
        "level": _LEVELS.get(diag["severity"], "warning"),
        "message": {"text": diag["message"]},
        "locations": _result_location(diag),
    }
    # Diagnostic-specific structured detail (resolved target, fragment,
    # candidate anchors, cycle members, ...) rides in the property bag.
    properties = {"severity": diag["severity"]}
    properties.update(diag.get("details") or {})
    result["properties"] = properties
    return result


def build_sarif(validation: dict, diagnostics: list[dict], tool_version: str) -> dict:
    """Build a SARIF 2.1.0 log for one completed validation.

    Diagnostics are sorted by (document path, line, type, message, row id)
    so serialization is stable regardless of storage order. Document-level
    diagnostics (line is None) sort before line-specific ones.
    """
    rules, rule_index = build_rules()
    ordered = sorted(
        diagnostics,
        key=lambda d: (
            d["path"],
            d["line"] if d["line"] is not None else 0,
            d["type"],
            d["message"],
            d["id"],
        ),
    )
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": tool_version,
                        "rules": rules,
                    }
                },
                "automationDetails": {"id": f"validation/{validation['id']}"},
                "results": [_result(d, rule_index) for d in ordered],
            }
        ],
    }
