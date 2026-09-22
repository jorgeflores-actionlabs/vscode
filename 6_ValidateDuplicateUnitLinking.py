import argparse
import sys
from pathlib import Path

import pyodbc

try:
    from console_colors import (
        Colors,
        print_banner,
        print_log,
        print_metric,
        print_status,
    )
except ModuleNotFoundError:
    class Colors:
        """Fallback colors when the reusable helper is not beside this script."""

        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        GRAY = "\033[90m"
        WHITE = "\033[97m"
        BOLD = "\033[1m"

    def print_log(message: str, color: str = Colors.RESET):
        """Print a colored log message without requiring another local file."""
        print(f"{color}{message}{Colors.RESET}")

    def print_banner(title: str, subtitle: str = ""):
        """Print a section banner without requiring another local file."""
        line = "=" * 72
        print_log(line, Colors.CYAN)
        print_log(f"  {title}", f"{Colors.BOLD}{Colors.CYAN}")
        if subtitle:
            print_log(f"  {subtitle}", Colors.GRAY)
        print_log(line, Colors.CYAN)

    def print_metric(label: str, value, color: str = Colors.RESET):
        """Print an aligned label/value pair."""
        print_log(f"  {label:<28} {value}", color)

    def print_status(status: str, details: str = ""):
        """Print a prominently colored validation status."""
        normalized_status = status.upper()
        color = Colors.GREEN if normalized_status == "SUCCESS" else Colors.RED
        message = f"[ {normalized_status:^10} ]"
        if details:
            message = f"{message} {details}"
        print_log(message, f"{Colors.BOLD}{color}")


SERVER = "sqlag_pdxsql.external.pie.pdx.dealerspike.com"
DATABASE = "DMS_Imports"
INPUT_FILE = Path(__file__).with_name("input.txt")

CONNECTION_STRING = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server={SERVER};"
    f"Database={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

DUPLICATE_UNIT_LINKING_QUERY = """
WITH DuplicateHubids AS (
    SELECT hubid, COUNT(*) AS event_count
    FROM [Harley].[dbo].[SQS_Unit_Linking] WITH (NOLOCK)
    WHERE dealerId = ?
      AND hubid IN (
          SELECT harley_usedbike_id
          FROM [Harley].[dbo].[harley_used_bikes] WITH (NOLOCK)
          WHERE dealership_id = ?
            AND Automaintained = 1
            AND deleted = 0
      )
    GROUP BY hubid
    HAVING COUNT(*) > 1
)
SELECT DISTINCT linking.unitId, linking.hubid, duplicates.event_count
FROM [Harley].[dbo].[SQS_Unit_Linking] AS linking WITH (NOLOCK)
INNER JOIN DuplicateHubids AS duplicates
    ON duplicates.hubid = linking.hubid
WHERE linking.dealerId = ?
ORDER BY linking.hubid, linking.unitId;
"""


def validate_dealer_id(value: str, source: str = "DealerId") -> int:
    """Convert a DealerId to a positive SQL Server int."""
    try:
        dealer_id = int(value)
    except ValueError as error:
        raise ValueError(f"{source} must be an integer.") from error

    if not 1 <= dealer_id <= 2_147_483_647:
        raise ValueError(f"{source} must be between 1 and 2147483647.")

    return dealer_id


def read_dealer_ids(input_file: Path) -> list[int]:
    """Read DealerId values from the first column of input.txt."""
    dealer_ids = []

    with input_file.open(encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            fields = [field.strip() for field in line.split("|")]
            if len(fields) not in {4, 5}:
                raise ValueError(
                    f"Line {line_number}: expected 4 or 5 fields separated by '|', "
                    f"but found {len(fields)}."
                )

            if not all(fields[:4]):
                raise ValueError(f"Line {line_number}: one or more fields are empty.")

            dealer_ids.append(
                validate_dealer_id(fields[0], source=f"Line {line_number}: DealerId")
            )

    if not dealer_ids:
        raise ValueError(f"The file {input_file} contains no records.")

    return dealer_ids


def fetch_duplicate_links(cursor, dealer_id: int) -> list[tuple]:
    """Return duplicate unitId/hubid groups for one dealer."""
    cursor.execute(
        DUPLICATE_UNIT_LINKING_QUERY,
        (dealer_id, dealer_id, dealer_id),
    )
    return cursor.fetchall()


def validate_dealer(cursor, dealer_id: int) -> dict:
    """Validate that no duplicate unit-linking events exist for a dealer."""
    duplicate_rows = fetch_duplicate_links(cursor, dealer_id)
    duplicate_groups = {}

    for unit_id, hubid, event_count in duplicate_rows:
        group = duplicate_groups.setdefault(
            hubid,
            {"unit_ids": set(), "event_count": event_count},
        )
        group["unit_ids"].add(unit_id)

    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_metric("Duplicate hubid groups", len(duplicate_groups))

    if not duplicate_groups:
        print_status("SUCCESS", "no duplicate events found")
        return {}

    print_status("ERROR", f"{len(duplicate_groups)} duplicate hubid group(s) found")
    print_log("  hubid                         events   [unitId]", Colors.YELLOW)
    print_log("  ----------------------------------------------------------------", Colors.YELLOW)
    for hubid in sorted(duplicate_groups, key=lambda value: str(value)):
        group = duplicate_groups[hubid]
        unit_ids = sorted(group["unit_ids"], key=lambda value: str(value))
        unit_ids_text = ", ".join(str(unit_id) for unit_id in unit_ids)
        print_log(
            f"  {str(hubid):<30} {group['event_count']:<8} [{unit_ids_text}]",
            Colors.RED,
        )

    return duplicate_groups


def validate_outputs(dealer_ids: list[int]):
    """Run the duplicate-event validation for all requested dealers."""
    connection = None
    cursor = None
    errors = {}

    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for dealer_id in dealer_ids:
            duplicate_groups = validate_dealer(cursor, dealer_id)
            if duplicate_groups:
                errors[dealer_id] = duplicate_groups
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print_log("  Database connection closed.", Colors.GRAY)

    if errors:
        total_groups = sum(len(groups) for groups in errors.values())
        print_banner(
            "VALIDATION FAILED",
            f"{total_groups} duplicate group(s) require attention",
        )
        raise RuntimeError(
            "Duplicate unit-linking events were found for DealerId(s): "
            + ", ".join(str(dealer_id) for dealer_id in errors)
        )

    print_banner(
        "VALIDATION COMPLETE",
        f"{len(dealer_ids)} DealerId value(s) passed successfully",
    )


def parse_arguments():
    """Parse the optional DealerId command-line argument."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate duplicate unit-linking events. "
            "Without a DealerId, values are read from input.txt."
        )
    )
    parser.add_argument(
        "dealer_id",
        nargs="?",
        help="Optional DealerId. When provided, input.txt is not read.",
    )
    return parser.parse_args()


def main():
    """Validate one DealerId or every DealerId listed in input.txt."""
    args = parse_arguments()

    if args.dealer_id is None:
        dealer_ids = read_dealer_ids(INPUT_FILE)
        mode_description = (
            f"File mode | {len(dealer_ids)} DealerId value(s) loaded from {INPUT_FILE}"
        )
    else:
        dealer_ids = [validate_dealer_id(args.dealer_id)]
        mode_description = f"Single-dealer mode | DealerId={dealer_ids[0]}"

    print_banner("DUPLICATE UNIT-LINKING VALIDATION", mode_description)
    try:
        validate_outputs(dealer_ids)
    except RuntimeError as error:
        print_log(f"Validation error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1

    return 0


if __name__ == "__main__":
    # File mode: python 6_ValidateDuplicateUnitLinking.py
    # Single-dealer mode: python 6_ValidateDuplicateUnitLinking.py 3563
    sys.exit(main())
