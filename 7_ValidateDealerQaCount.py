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
        color = Colors.GREEN if normalized_status == "MATCHED" else Colors.RED
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
    """Read one DealerId per line, optionally followed by legacy pipe fields."""
    dealer_ids = []

    with input_file.open(encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            dealer_id_value = line.split("|", maxsplit=1)[0].strip()
            dealer_ids.append(
                validate_dealer_id(
                    dealer_id_value,
                    source=f"Line {line_number}: DealerId",
                )
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


def post_webhook_request(dealer_id: int, action: str, url: str | None = None) -> dict:
    """POST either COUNT or DMS and return the decoded JSON response."""
    payload = {
        "action": action,
        "dealer_id": str(dealer_id),
    }
    if action == "COUNT":
        if not url:
            raise ValueError(f"A URL is required for COUNT, DealerId={dealer_id}.")
        payload["url"] = url
    elif action != "DMS":
        raise ValueError(f"Unsupported webhook action: {action}")

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
            f"Webhook returned HTTP {error.code} for {action}, DealerId={dealer_id}: "
            f"{error_body or error.reason}"
        ) from None
    except URLError as error:
        raise RuntimeError(
            f"Webhook request failed for {action}, DealerId={dealer_id}: {error.reason}"
        ) from None

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Webhook returned invalid JSON for {action}, DealerId={dealer_id}: {error.msg}"
        ) from None

    if not isinstance(result, dict):
        raise RuntimeError(
            f"Webhook response must be a JSON object for {action}, DealerId={dealer_id}."
        )

    return result


def extract_count(result: dict, dealer_id: int, action: str) -> int:
    """Validate webhook status and return a numeric count."""
    if str(result.get("status", "")).casefold() != "success":
        raise RuntimeError(
            f"Webhook status was not success for {action}, DealerId={dealer_id}: "
            f"{result.get('status', 'missing')}"
        )

    count = result.get("count")
    if isinstance(count, bool) or not isinstance(count, (int, float)):
        raise RuntimeError(
            f"Webhook count is not numeric for {action}, DealerId={dealer_id}: {count!r}"
        )

    if isinstance(count, float) and not count.is_integer():
        raise RuntimeError(
            f"Webhook count must be a whole number for {action}, DealerId={dealer_id}: {count}"
        )

    return int(count)


def validate_dealer(cursor, dealer_id: int) -> tuple[int, int]:
    """Compare COUNT and DMS webhook counts for a dealer."""
    hostname = fetch_hostname(cursor, dealer_id)
    inventory_url = build_inventory_url(hostname)
    count_result = post_webhook_request(dealer_id, "COUNT", inventory_url)
    dms_result = post_webhook_request(dealer_id, "DMS")
    count_value = extract_count(count_result, dealer_id, "COUNT")
    dms_value = extract_count(dms_result, dealer_id, "DMS")
    matched = count_value == dms_value
    comparison_color = f"{Colors.BOLD}{Colors.GREEN}" if matched else Colors.RED

    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_metric("Hostname", hostname, Colors.WHITE)
    print_metric("Inventory URL", inventory_url, Colors.GRAY)
    print_metric("COUNT action", count_value, comparison_color)
    print_metric("DMS action", dms_value, comparison_color)
    print_status(
        "MATCHED" if matched else "NOT MATCHED",
        "counts are equal" if matched else "counts are different",
    )

    if not matched:
        raise RuntimeError(
            f"Count mismatch for DealerId={dealer_id}: "
            f"COUNT={count_value}, DMS={dms_value}."
        )

    return count_value, dms_value


def validate_outputs(dealer_ids: list[int]) -> bool:
    """Validate every dealer, continuing after dealer-specific failures."""
    connection = None
    cursor = None
    passed_dealer_ids = []
    failed_dealers = []

    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for dealer_id in dealer_ids:
            try:
                validate_dealer(cursor, dealer_id)
            except Exception as error:
                failed_dealers.append((dealer_id, str(error)))
                print_status("FAILED", f"DealerId={dealer_id}: {error}")
                continue

            passed_dealer_ids.append(dealer_id)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print_log("  Database connection closed.", Colors.GRAY)

    print_banner(
        "VALIDATION COMPLETE",
        (
            f"{len(passed_dealer_ids)} passed | "
            f"{len(failed_dealers)} failed | {len(dealer_ids)} total"
        ),
    )

    if failed_dealers:
        print_log("Failed DealerIds:", f"{Colors.BOLD}{Colors.RED}")
        for dealer_id, error_message in failed_dealers:
            print_log(f"  {dealer_id}: {error_message}", Colors.RED)
        return False

    return True


def parse_arguments():
    """Parse the optional DealerId command-line argument."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare COUNT and DMS webhook counts. "
            "Without a DealerId, one DealerId per line is read from input.txt; "
            "legacy pipe-delimited lines are also accepted."
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
        all_dealers_passed = validate_outputs(dealer_ids)
        if not all_dealers_passed:
            return 1
    except Exception as error:
        print_log(f"Validation error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1

    return 0


if __name__ == "__main__":
    # File mode (one DealerId per line): python 7_ValidateDealerQaCount.py
    # Single-dealer mode: python 7_ValidateDealerQaCount.py 7781
    sys.exit(main())
