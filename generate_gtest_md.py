#!/usr/bin/env python3
"""Generate a Markdown summary from a GoogleTest XML report.

Reads a gtest XML file (e.g. report.xml) and prints Markdown to stdout.
Optionally writes to a file if --out is passed.

Usage (Windows cmd):
  python generate_gtest_md.py --xml "D:/qaa/xhp/quick-print/lib64/report.xml" --out gtest_summary.md

If run inside GitHub Actions and you want to append to the Job Summary:
  python generate_gtest_md.py --xml quick-print/librel64/report.xml --summary-env

The script is dependency-free (only stdlib).
"""
from __future__ import annotations
import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert gtest XML to Markdown summary")
    p.add_argument("--xml", required=True, help="Path to gtest XML report (report.xml)")
    p.add_argument("--out", help="Optional output file to write Markdown")
    p.add_argument("--summary-env", action="store_true", help="Append Markdown to $GITHUB_STEP_SUMMARY if defined")
    p.add_argument("--max-fail", type=int, default=50, help="Max failing testcases to list (default 50, 0=none, -1=all)")
    p.add_argument("--truncate-message", type=int, default=300, help="Max chars per failure message")
    p.add_argument("--show-passed", action="store_true", help="Also list passed testcases (can be noisy)")
    p.add_argument("--no-emoji", action="store_true", help="Do not use emoji in status line (ASCII only)")
    return p.parse_args()


def load_suites(root: ET.Element):
    if root.tag == "testsuites":
        return root.findall("testsuite")
    elif root.tag == "testsuite":
        return [root]
    else:
        # Some generators may nest differently, fallback to any testsuite elements
        return root.findall(".//testsuite")


def escape_md(s: str) -> str:
    # Minimal escaping for pipes in table cells
    return s.replace("|", "\\|")


def main():
    args = parse_args()
    xml_path = Path(args.xml)
    if not xml_path.exists():
        print(f"[ERROR] XML file not found: {xml_path}", file=sys.stderr)
        sys.exit(1)

    try:
        tree = ET.parse(str(xml_path))
    except ET.ParseError as e:
        print(f"[ERROR] Failed to parse XML: {e}", file=sys.stderr)
        sys.exit(2)

    root = tree.getroot()
    suites = load_suites(root)

    total_tests = total_fail = total_err = total_skip = 0
    table_rows = []

    failing_cases = []
    passed_cases = []

    for suite in suites:
        name = suite.get("name", "")
        tests = int(suite.get("tests", 0) or 0)
        failures = int(suite.get("failures", 0) or 0)
        errors = int(suite.get("errors", 0) or 0)
        skipped = int(suite.get("skipped", 0) or 0)
        time = suite.get("time", "")
        passed = tests - failures - errors - skipped

        total_tests += tests
        total_fail += failures
        total_err += errors
        total_skip += skipped

        table_rows.append([name or "(unnamed)", tests, passed, failures, errors, skipped, time])

        for case in suite.findall("testcase"):
            cname = case.get("classname", "")
            tname = case.get("name", "")
            full_name = f"{cname}.{tname}" if cname else tname
            failure_el = case.find("failure")
            error_el = case.find("error")
            skipped_el = case.find("skipped")
            if failure_el is not None or error_el is not None:
                msg_parts = []
                if failure_el is not None:
                    msg_parts.append((failure_el.get("message", "") + " " + (failure_el.text or "")).strip())
                if error_el is not None:
                    msg_parts.append((error_el.get("message", "") + " " + (error_el.text or "")).strip())
                message = " | ".join(p for p in msg_parts if p)
                if args.truncate_message > 0 and len(message) > args.truncate_message:
                    message = message[: args.truncate_message] + "..."
                failing_cases.append((full_name, message))
            elif skipped_el is not None:
                # treat skipped separately if needed later
                pass
            else:
                if args.show_passed:
                    passed_cases.append(full_name)

    passed_total = total_tests - total_fail - total_err - total_skip
    if args.no_emoji:
        status = "ALL PASSED" if (total_fail + total_err) == 0 else "FAILURES" if total_fail else "ERRORS" if total_err else "ISSUES"
    else:
        # Use emoji; some Windows consoles might not support them (we'll handle later)
        status = "✅ All Passed" if (total_fail + total_err) == 0 else "❌ Failures" if total_fail else "⚠️ Errors" if total_err else "⚠️ Issues"

    md_lines = []
    md_lines.append("# GTest Summary")
    md_lines.append(f"Status: **{status}**")
    md_lines.append("")
    md_lines.append("| TestSuite | Total | Passed | Failed | Errors | Skipped | Time(s) |")
    md_lines.append("|-----------|-------|--------|--------|--------|---------|---------|")
    for r in table_rows:
        md_lines.append("| " + " | ".join(escape_md(str(x)) for x in r) + " |")

    md_lines.append("")
    md_lines.append("## Totals")
    md_lines.append("| Metric | Value |")
    md_lines.append("|--------|-------|")
    md_lines.append(f"| Total | {total_tests} |")
    md_lines.append(f"| Passed | {passed_total} |")
    md_lines.append(f"| Failed | {total_fail} |")
    md_lines.append(f"| Errors | {total_err} |")
    md_lines.append(f"| Skipped | {total_skip} |")

    # Fail details
    if failing_cases and (args.max_fail != 0):
        md_lines.append("")
        md_lines.append("## Failed / Error TestCases")
        shown = failing_cases if args.max_fail < 0 else failing_cases[: args.max_fail]
        for name, msg in shown:
            md_lines.append(f"- **{escape_md(name)}**: {escape_md(msg)}")
        hidden = len(failing_cases) - len(shown)
        if hidden > 0:
            md_lines.append(f"\n... ({hidden} more not shown) ...")

    if passed_cases:
        md_lines.append("")
        md_lines.append("<details><summary>Passed TestCases (" + str(len(passed_cases)) + ")</summary>")
        for name in passed_cases:
            md_lines.append(f"- {escape_md(name)}")
        md_lines.append("</details>")

    markdown = "\n".join(md_lines) + "\n"

    # Try to ensure stdout is UTF-8 to avoid UnicodeEncodeError on Windows GitHub runners
    try:
        if not args.no_emoji and hasattr(sys.stdout, "reconfigure"):
            # Only attempt if we still have emoji; if user disabled emoji it's ASCII safe
            sys.stdout.reconfigure(encoding="utf-8")  # Python 3.7+
    except Exception:
        pass

    def safe_print(text: str):
        try:
            print(text)
        except UnicodeEncodeError:
            # Fallback: remove/replace emoji and unencodable chars
            ascii_fallback = text
            # Simple replacements
            ascii_fallback = (ascii_fallback
                              .replace("✅", "[PASS]")
                              .replace("❌", "[FAIL]")
                              .replace("⚠️", "[WARN]"))
            enc = sys.stdout.encoding or "cp1252"
            ascii_fallback = ascii_fallback.encode(enc, errors="replace").decode(enc, errors="replace")
            print(ascii_fallback)

    # Write to --out if provided
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"[INFO] Markdown written to {out_path}")

    # Append to GitHub summary if requested and env var present
    if args.summary_env:
        summary_target = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_target:
            with open(summary_target, "a", encoding="utf-8") as fh:
                fh.write(markdown)
            print(f"[INFO] Appended to $GITHUB_STEP_SUMMARY")
        else:
            print("[WARN] $GITHUB_STEP_SUMMARY not set; skipping append")

    # Always print to stdout so user can redirect or inspect
    safe_print(markdown)


if __name__ == "__main__":
    main()
