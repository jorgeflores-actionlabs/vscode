import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
WEBHOOK_URL = "https://dova.cloudata.solutions/lv/webhook/qa_count"
INVENTORY_PATH = "/useradmin.asp?page=xinv-grid"
REQUEST_TIMEOUT_SECONDS = 30

CONNECTION_STRING = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server={SERVER};"
    f"Database={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

SITE_CONFIG_QUERY = """
SELECT [hostname]
FROM [Harley].[dbo].[Site_Config] WITH (NOLOCK)
WHERE dealerid = ?;
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
    """Read DealerId values from the first section of input.txt."""
    dealer_ids = []

    with input_file.open(encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 4:
                raise ValueError(
                    f"Line {line_number}: expected 4 fields separated by '|', "
                    f"but found {len(fields)}."
                )

            if not all(fields):
                raise ValueError(f"Line {line_number}: one or more fields are empty.")

            dealer_ids.append(
                validate_dealer_id(fields[0], source=f"Line {line_number}: DealerId")
            )

    if not dealer_ids:
        raise ValueError(f"The file {input_file} contains no records.")

    return dealer_ids


def fetch_hostname(cursor, dealer_id: int) -> str:
    """Fetch and validate the Site_Config hostname for a dealer."""
    cursor.execute(SITE_CONFIG_QUERY, (dealer_id,))
    row = cursor.fetchone()

    if row is None or row[0] is None or not str(row[0]).strip():
        raise ValueError(f"No hostname was found in Site_Config for DealerId={dealer_id}.")

    return str(row[0]).strip()


def build_inventory_url(hostname: str) -> str:
    """Build the inventory URL from a hostname returned by Site_Config."""
    normalized_hostname = hostname.strip().rstrip("/")
    if not normalized_hostname.startswith(("http://", "https://")):
        normalized_hostname = f"https://{normalized_hostname}"

    return f"{normalized_hostname}{INVENTORY_PATH}"


def post_count_request(dealer_id: int, url: str) -> dict:
    """POST the dealer count request and return the decoded JSON response."""
    payload = {
        "action": "COUNT",
        "dealer_id": str(dealer_id),
        "url": url,
    }
    request = Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Webhook returned HTTP {error.code} for DealerId={dealer_id}: "
            f"{error_body or error.reason}"
        ) from None
    except URLError as error:
        raise RuntimeError(
            f"Webhook request failed for DealerId={dealer_id}: {error.reason}"
        ) from None

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Webhook returned invalid JSON for DealerId={dealer_id}: {error.msg}"
        ) from None

    if not isinstance(result, dict):
        raise RuntimeError(f"Webhook response must be a JSON object for DealerId={dealer_id}.")

    return result


def validate_count_value(result: dict, dealer_id: int) -> int:
    """Validate the webhook response and return its numeric count."""
    if str(result.get("status", "")).casefold() != "success":
        raise RuntimeError(
            f"Webhook status was not success for DealerId={dealer_id}: "
            f"{result.get('status', 'missing')}"
        )

    count = result.get("count")
    if isinstance(count, bool) or not isinstance(count, (int, float)):
        raise RuntimeError(
            f"Webhook count is not numeric for DealerId={dealer_id}: {count!r}"
        )

    if isinstance(count, float) and not count.is_integer():
        raise RuntimeError(
            f"Webhook count must be a whole number for DealerId={dealer_id}: {count}"
        )

    return int(count)


def validate_dealer(cursor, dealer_id: int) -> int:
    """Fetch the hostname, call the webhook, and return the numeric count."""
    hostname = fetch_hostname(cursor, dealer_id)
    inventory_url = build_inventory_url(hostname)
    result = post_count_request(dealer_id, inventory_url)
    count = validate_count_value(result, dealer_id)

    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_metric("Hostname", hostname, Colors.WHITE)
    print_metric("Inventory URL", inventory_url, Colors.GRAY)
    print_metric("Webhook", WEBHOOK_URL, Colors.GRAY)
    print_metric("Count", count, Colors.WHITE)
    print_status("PASSED", "numeric count received")
    return count


def validate_outputs(dealer_ids: list[int]):
    """Validate all requested dealers and display their numeric counts."""
    connection = None
    cursor = None
    results = {}

    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for dealer_id in dealer_ids:
            results[dealer_id] = validate_dealer(cursor, dealer_id)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print_log("  Database connection closed.", Colors.GRAY)

    print_banner(
        "VALIDATION COMPLETE",
        f"{len(results)} DealerId value(s) passed successfully",
    )


def parse_arguments():
    """Parse the optional DealerId command-line argument."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a dealer hostname and retrieve its inventory count. "
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
    try:
        args = parse_arguments()

        if args.dealer_id is None:
            dealer_ids = read_dealer_ids(INPUT_FILE)
            mode_description = (
                f"File mode | {len(dealer_ids)} DealerId value(s) loaded from {INPUT_FILE}"
            )
        else:
            dealer_ids = [validate_dealer_id(args.dealer_id)]
            mode_description = f"Single-dealer mode | DealerId={dealer_ids[0]}"

        print_banner("DEALER QA COUNT VALIDATION", mode_description)
        validate_outputs(dealer_ids)
    except Exception as error:
        print_log(f"Validation error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1

    return 0


if __name__ == "__main__":
    # File mode: python 7_ValidateDealerQaCount.py
    # Single-dealer mode: python 7_ValidateDealerQaCount.py 7781
    sys.exit(main())
