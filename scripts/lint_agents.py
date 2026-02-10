#!/usr/bin/env python3
"""
Regularization linter for AGENTS.md files.

Detects scenario-specific information that would make a policy non-transferable:
hardcoded message IDs, task IDs, person names, pre-computed conclusions, etc.

Two severity tiers:
  ERRORS  — hard fail (exit 1): policy is an "answer key", not generalizable
  WARNINGS — informational (exit 0): possibly coincidental, review manually

Usage:
    python scripts/lint_agents.py agents/AGENTS.md.general_miner
    python scripts/lint_agents.py --strict agents/AGENTS.md.general_miner
    python scripts/lint_agents.py --all-miners
"""

import argparse
import re
import sys
from pathlib import Path

SANDBOX_DIR = Path(__file__).resolve().parent.parent

# ── Allowed patterns (suppress false positives) ────────────────────────────

ALLOWED_PATTERNS = [
    # Channel IDs are infrastructure constants
    r"C_ENG", r"C_GENERAL", r"C_INCIDENTS", r"C_RANDOM", r"C_ONCALL",
    # Tool names
    r"himalaya", r"gcalcli", r"curl", r"notion\.so",
    r"memory_search", r"memory_get", r"web_search", r"web_fetch",
]

# ── Error patterns (hard fail) ─────────────────────────────────────────────

ERROR_RULES = [
    # Message IDs
    (r"\bmsg_\d+\b", "Message ID"),
    # Task/ticket IDs
    (r"\bTC-\d+\b", "Task/ticket ID (TC-NNN)"),
    (r"\bINC-\d{4}-\d+\b", "Incident ID (INC-YYYY-NNN)"),
    # PR numbers
    (r"\bPR\s*#?\d{2,}\b", "PR number"),
    # Finding IDs
    (r"\bF-\d{4}-\d+\b", "Finding ID (F-YYYY-NNN)"),
    # Version numbers
    (r"\bv\d+\.\d+\.\d+\b", "Version number (vX.Y.Z)"),
    # Dollar amounts
    (r"\$\d+", "Dollar amount"),
    # Scenario-specific company/product names
    (r"\bAcme\b", "Company name: Acme"),
    (r"\bZenith\b", "Company name: Zenith"),
    (r"\bGlobalTech\b", "Company name: GlobalTech"),
    (r"\bDataSentry\b", "Company name: DataSentry"),
    (r"\bObsAI\b", "Company name: ObsAI"),
    (r"\bBigCorp\b", "Company name: BigCorp"),
    (r"\bGrafana\b", "Vendor name: Grafana"),
    (r"\bDashboard V2\b", "Product name: Dashboard V2"),
    # Fixture database IDs
    (r"\bdb_\d+\b", "Fixture database ID"),
    # Person full names
    (r"\bMarcus Johnson\b", "Person name: Marcus Johnson"),
    (r"\bJames Liu\b", "Person name: James Liu"),
    (r"\bPriya Patel\b", "Person name: Priya Patel"),
    (r"\bTom Anderson\b", "Person name: Tom Anderson"),
    (r"\bSarah Kim\b", "Person name: Sarah Kim"),
    (r"\bElena Rodriguez\b", "Person name: Elena Rodriguez"),
    (r"\bDavid Park\b", "Person name: David Park"),
    (r"\bDana Reeves\b", "Person name: Dana Reeves"),
    (r"\bJordan Lee\b", "Person name: Jordan Lee"),
    (r"\bMarina Chen\b", "Person name: Marina Chen"),
    (r"\bMike Stevens\b", "Person name: Mike Stevens"),
    # Pre-computed conclusions
    (r"sprint goal NOT met", "Pre-computed conclusion"),
    (r"real-time was cut", "Pre-computed conclusion"),
    (r"cursor reset bug", "Pre-computed conclusion"),
    (r"auto-scaling deploy", "Pre-computed conclusion"),
    (r"NOT READY.*NO-GO", "Pre-computed conclusion"),
]

# ── Warning patterns (informational) ──────────────────────────────────────

WARNING_RULES = [
    # PM persona (used across all scenarios — not itself a leak, but suspicious in a general policy)
    (r"\bAlex Chen\b", "PM persona: Alex Chen"),
    # First names only (could be coincidental)
    (r"\bMarcus\b", "First name: Marcus"),
    (r"\bJames\b", "First name: James"),
    (r"\bPriya\b", "First name: Priya"),
    (r"\bTom\b", "First name: Tom"),
    (r"\bSarah\b", "First name: Sarah"),
    (r"\bElena\b", "First name: Elena"),
    (r"\bDavid\b", "First name: David"),
    (r"\bDana\b", "First name: Dana"),
    (r"\bJordan\b", "First name: Jordan"),
    (r"\bMarina\b", "First name: Marina"),
    (r"\bMike\b", "First name: Mike"),
    # Specific sprint numbers
    (r"\bSprint \d+\b", "Specific sprint number"),
    # Specific dates with year
    (r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+20\d{2}\b", "Specific date with year"),
]


def is_allowed(match_text: str) -> bool:
    """Check if the matched text is itself an allowed pattern (suppress false positive).

    This checks whether the match overlaps with an allowed string, e.g. a channel
    ID like C_ENG or a tool name. It does NOT suppress all matches on lines that
    happen to mention a tool — only matches where the flagged text is part of an
    allowed pattern.
    """
    for pattern in ALLOWED_PATTERNS:
        if re.search(pattern, match_text):
            return True
    return False


def lint_file(path: Path, strict: bool = False) -> tuple[list, list]:
    """Lint a single file. Returns (errors, warnings)."""
    text = path.read_text()
    lines = text.splitlines()

    errors = []
    warnings = []

    for line_num, line in enumerate(lines, 1):
        # Check error rules
        for pattern, label in ERROR_RULES:
            for match in re.finditer(pattern, line):
                match_text = match.group()
                if not is_allowed(match_text):
                    errors.append((line_num, label, match_text, line.strip()))

        # Check warning rules
        for pattern, label in WARNING_RULES:
            for match in re.finditer(pattern, line):
                match_text = match.group()
                if not is_allowed(match_text):
                    # Skip if first name is already covered by a full-name error on this line
                    # (e.g., "Marcus Johnson" error subsumes "Marcus" warning)
                    already_covered = any(
                        e_line == line_num and match_text in e_match
                        for e_line, _, e_match, _ in errors
                    )
                    if not already_covered:
                        warnings.append((line_num, label, match_text, line.strip()))

    return errors, warnings


def print_results(path: Path, errors: list, warnings: list, strict: bool = False):
    """Print lint results for a file."""
    label = path.relative_to(SANDBOX_DIR) if str(path).startswith(str(SANDBOX_DIR)) else path

    if not errors and not warnings:
        print(f"  PASS  {label}")
        return

    if errors:
        print(f"\n  FAIL  {label}  ({len(errors)} errors, {len(warnings)} warnings)")
    else:
        print(f"\n  {'FAIL' if strict else 'WARN'}  {label}  ({len(warnings)} warnings)")

    for line_num, rule_label, match_text, context in errors:
        print(f"    ERROR  L{line_num}: {rule_label} — \"{match_text}\"")
        print(f"           {context}")

    for line_num, rule_label, match_text, context in warnings:
        severity = "ERROR" if strict else "WARN "
        print(f"    {severity}  L{line_num}: {rule_label} — \"{match_text}\"")
        print(f"           {context}")


def main():
    parser = argparse.ArgumentParser(
        description="Lint AGENTS.md files for scenario-specific leaks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", help="AGENTS.md files to lint")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors (exit 1)")
    parser.add_argument("--all-miners", action="store_true",
                        help="Lint all per-scenario AGENTS.md.miner files")

    args = parser.parse_args()

    files = []

    if args.all_miners:
        miners = sorted(SANDBOX_DIR.glob("fixtures/*/AGENTS.md.miner"))
        files.extend(miners)

    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)
        files.append(p)

    if not files:
        parser.print_help()
        sys.exit(1)

    print(f"Linting {len(files)} file(s)...\n")

    total_errors = 0
    total_warnings = 0

    for path in files:
        errors, warnings = lint_file(path, strict=args.strict)
        print_results(path, errors, warnings, strict=args.strict)
        total_errors += len(errors)
        if args.strict:
            total_errors += len(warnings)
        else:
            total_warnings += len(warnings)

    print(f"\n{'='*50}")
    print(f"Total: {total_errors} errors, {total_warnings} warnings")

    if total_errors > 0:
        print("FAILED")
        sys.exit(1)
    else:
        print("PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
