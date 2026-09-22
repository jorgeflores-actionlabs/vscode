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
        color = Colors.GREEN if normalized_status == "PASSED" else Colors.RED
        message = f"[ {normalized_status:^10} ]"
        if details:
            message = f"{message} {details}"
        print_log(message, f"{Colors.BOLD}{color}")


SERVER = "sqlag_pdxsql.external.pie.pdx.dealerspike.com"
DATABASE = "DMS_Imports"
INPUT_FILE = Path(__file__).with_name("input.txt")
EXPECTED_PRODUCT = "CDKLightspeed"
REQUIRED_DIRECTORY_TEXT = "CDKLS"

CONNECTION_STRING = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server={SERVER};"
    f"Database={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

DMS_CONFIG_QUERY = """
SELECT [Directory]
FROM [dbo].[DMS_Config] WITH (NOLOCK)
WHERE DealerId = ?
  AND [Directory] LIKE ?;
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


def read_input_records(input_file: Path) -> list[tuple[int, str]]:
    """Read DealerId and product from the first and third input sections."""
    records = []

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

            dealer_id = validate_dealer_id(
                fields[0], source=f"Line {line_number}: DealerId"
            )
            records.append((dealer_id, fields[2]))

    if not records:
        raise ValueError(f"The file {input_file} contains no records.")

    return records


def fetch_matching_directories(cursor, dealer_id: int) -> list[str]:
    """Return DMS_Config directories containing the required CDKLS text."""
    cursor.execute(DMS_CONFIG_QUERY, (dealer_id, f"%{REQUIRED_DIRECTORY_TEXT}%"))
    return [str(row[0]) for row in cursor.fetchall() if row[0] is not None]


def validate_dealer(cursor, dealer_id: int, product: str) -> bool:
    """Validate the dealer directory and the product from input.txt."""
    directories = fetch_matching_directories(cursor, dealer_id)
    product_matches = product.casefold() == EXPECTED_PRODUCT.casefold()
    directory_matches = bool(directories)
    passed = directory_matches and product_matches

    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_metric("Input product", product, Colors.WHITE)
    print_metric("Required Directory text", REQUIRED_DIRECTORY_TEXT, Colors.WHITE)
    print_metric("Matching DMS_Config rows", len(directories))

    if directories:
        for directory in directories:
            print_log(f"  Directory: {directory}", Colors.GRAY)
    else:
        print_log("  No DMS_Config.Directory contains CDKLS.", Colors.YELLOW)

    if not product_matches:
        print_log(
            f"  Expected product: {EXPECTED_PRODUCT}; received: {product}",
            Colors.YELLOW,
        )

    print_status(
        "PASSED" if passed else "NOT PASSED",
        "Directory and product match" if passed else "Directory or product mismatch",
    )
    return passed


def validate_outputs(records: list[tuple[int, str]]):
    """Validate all requested dealer/product records."""
    connection = None
    cursor = None
    failed_dealers = []

    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for dealer_id, product in records:
            if not validate_dealer(cursor, dealer_id, product):
                failed_dealers.append(dealer_id)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print_log("  Database connection closed.", Colors.GRAY)

    if failed_dealers:
        print_banner(
            "VALIDATION FAILED",
            f"{len(failed_dealers)} DealerId value(s) require attention",
        )
        raise RuntimeError(
            "Dealer configuration validation failed for DealerId(s): "
            + ", ".join(str(dealer_id) for dealer_id in failed_dealers)
        )

    print_banner(
        "VALIDATION COMPLETE",
        f"{len(records)} DealerId value(s) passed successfully",
    )


def parse_arguments():
    """Parse the optional DealerId and product command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate DMS_Config.Directory and the product in input.txt. "
            "Without a DealerId, values are read from input.txt."
        )
    )
    parser.add_argument(
        "dealer_id",
        nargs="?",
        help="Optional DealerId. When provided, input.txt is not read.",
    )
    parser.add_argument(
        "product",
        nargs="?",
        default=EXPECTED_PRODUCT,
        help=f"Optional product. Default: {EXPECTED_PRODUCT}.",
    )
    return parser.parse_args()


def main():
    """Validate one DealerId or every DealerId listed in input.txt."""
    try:
        args = parse_arguments()

        if args.dealer_id is None:
            records = read_input_records(INPUT_FILE)
            mode_description = (
                f"File mode | {len(records)} DealerId value(s) loaded from {INPUT_FILE}"
            )
        else:
            records = [(validate_dealer_id(args.dealer_id), args.product)]
            mode_description = (
                f"Single-dealer mode | DealerId={records[0][0]} | "
                f"Product={records[0][1]}"
            )

        print_banner("DMS CONFIGURATION VALIDATION", mode_description)
        validate_outputs(records)
    except (RuntimeError, ValueError) as error:
        print_log(f"Validation error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1
    except Exception as error:
        print_log(f"Execution error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1

    return 0


if __name__ == "__main__":
    # File mode: python 0_ValidateDealerConfig.py
    # Single-dealer mode: python 0_ValidateDealerConfig.py 5221
    # Single-dealer/product mode: python 0_ValidateDealerConfig.py 5221 CDKLightspeed
    sys.exit(main())
