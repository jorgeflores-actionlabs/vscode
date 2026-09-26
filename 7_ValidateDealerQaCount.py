import argparse
import json
import os
import re
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
DMS_ADMIN_DASHBOARD_URL = "https://dms-admin-app.services.dealerspike.net/Dashboard"
CHROME_PROFILE_EMAIL = "jorge.flores@cloudata.pe"
CHROME_USER_DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
)
REQUEST_TIMEOUT_SECONDS = 30
PAGE_TIMEOUT_MILLISECONDS = 30_000

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


def find_chrome_profile_directory() -> str:
    """Find the local Chrome profile directory for the requested email."""
    local_state_path = CHROME_USER_DATA_DIR / "Local State"
    if not local_state_path.exists():
        raise RuntimeError(f"Chrome Local State was not found: {local_state_path}")

    try:
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to read Chrome profile information: {error}") from None

    profile_cache = local_state.get("profile", {}).get("info_cache", {})
    for directory, profile in profile_cache.items():
        email = str(profile.get("user_name", "")).strip().casefold()
        if email == CHROME_PROFILE_EMAIL.casefold():
            return directory

    raise RuntimeError(
        f"Chrome profile for {CHROME_PROFILE_EMAIL} was not found in {local_state_path}."
    )


def launch_dms_admin_browser():
    """Open DMS Admin using the Chrome profile authenticated for the account."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Install it with: python -m pip install playwright"
        ) from None

    profile_directory = find_chrome_profile_directory()
    playwright = sync_playwright().start()

    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_USER_DATA_DIR),
            channel="chrome",
            headless=False,
            timeout=PAGE_TIMEOUT_MILLISECONDS,
            args=[f"--profile-directory={profile_directory}"],
        )
    except Exception as error:
        playwright.stop()
        message = str(error)
        if "already running" in message.casefold() or "lock" in message.casefold():
            raise RuntimeError(
                f"Chrome profile {profile_directory} is already in use. "
                "Close Chrome windows using the DMS Admin profile and try again."
            ) from None
        raise RuntimeError(f"Unable to open Chrome profile {profile_directory}: {error}") from None

    return playwright, context, PlaywrightTimeoutError


def get_browser_page(context):
    """Return an existing page or create one in the persistent browser context."""
    if context.pages:
        return context.pages[0]
    return context.new_page()


def find_profile_row(page, dealer_id: int):
    """Find exactly one dashboard row containing the requested Customer Id(s)."""
    dealer_text = str(dealer_id)
    rows = page.locator("tbody tr").filter(has_text=re.compile(rf"\b{re.escape(dealer_text)}\b"))
    if rows.count() != 1:
        raise RuntimeError(
            f"Expected one DMS Admin profile for DealerId={dealer_id}, "
            f"but found {rows.count()}."
        )
    return rows.first


def fetch_dms_admin_count(page, dealer_id: int, timeout_error) -> int:
    """Search DMS Admin, open Report, and read TOTAL INCLUDED UNITS."""
    page.goto(DMS_ADMIN_DASHBOARD_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MILLISECONDS)

    search = page.locator("input[placeholder*='Search for profiles']").first
    search.wait_for(state="visible", timeout=PAGE_TIMEOUT_MILLISECONDS)
    search.fill(str(dealer_id))
    page.wait_for_timeout(750)

    row = find_profile_row(page, dealer_id)
    row.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MILLISECONDS)

    report_tab = page.get_by_text("Report", exact=True).first
    report_tab.wait_for(state="visible", timeout=PAGE_TIMEOUT_MILLISECONDS)
    report_tab.click()

    included_units_label = page.get_by_text("TOTAL INCLUDED UNITS", exact=True).first
    included_units_label.wait_for(state="visible", timeout=PAGE_TIMEOUT_MILLISECONDS)

    for level in range(1, 7):
        ancestor = included_units_label.locator("xpath=" + "/.." * level)
        text = ancestor.inner_text(timeout=PAGE_TIMEOUT_MILLISECONDS)
        numbers = re.findall(r"(?<![\w])\d+(?![\w])", text)
        if numbers:
            return int(numbers[0])

    raise RuntimeError(
        f"TOTAL INCLUDED UNITS did not contain a numeric value for DealerId={dealer_id}."
    )


def validate_dealer(cursor, page, dealer_id: int, timeout_error) -> int:
    """Compare the QA webhook count with the DMS Admin Report count."""
    hostname = fetch_hostname(cursor, dealer_id)
    inventory_url = build_inventory_url(hostname)
    result = post_count_request(dealer_id, inventory_url)
    webhook_count = validate_count_value(result, dealer_id)
    dms_admin_count = fetch_dms_admin_count(page, dealer_id, timeout_error)
    matched = webhook_count == dms_admin_count

    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_metric("Hostname", hostname, Colors.WHITE)
    print_metric("Inventory URL", inventory_url, Colors.GRAY)
    comparison_color = f"{Colors.BOLD}{Colors.GREEN}" if matched else Colors.RED
    print_metric("QA webhook count", webhook_count, comparison_color)
    print_metric("DMS Admin count", dms_admin_count, comparison_color)
    print_status(
        "MATCHED" if matched else "NOT MATCHED",
        "counts are equal" if matched else "counts are different",
    )

    if not matched:
        raise RuntimeError(
            f"Count mismatch for DealerId={dealer_id}: "
            f"QA webhook={webhook_count}, DMS Admin={dms_admin_count}."
        )

    return webhook_count


def validate_outputs(dealer_ids: list[int]):
    """Validate all requested dealers and display their numeric counts."""
    connection = None
    cursor = None
    playwright = None
    browser_context = None
    results = {}

    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()
        playwright, browser_context, timeout_error = launch_dms_admin_browser()
        page = get_browser_page(browser_context)

        for dealer_id in dealer_ids:
            results[dealer_id] = validate_dealer(cursor, page, dealer_id, timeout_error)
    finally:
        if browser_context is not None:
            browser_context.close()
        if playwright is not None:
            playwright.stop()
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
