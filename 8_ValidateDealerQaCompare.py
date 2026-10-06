"""Validate dealer inventory using the QA webhook's COMPARE action."""

import argparse
import json
import sys
import textwrap
from itertools import zip_longest
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pyodbc

try:
    from console_colors import Colors, print_banner, print_log, print_metric, print_status
except ModuleNotFoundError as error:
    if error.name != "console_colors":
        raise

    class Colors:
        """Fallback colors when the shared helper is not beside this script."""

        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        GRAY = "\033[90m"
        WHITE = "\033[97m"
        BOLD = "\033[1m"

    def print_log(message: str, color: str = Colors.RESET):
        print(f"{color}{message}{Colors.RESET}")

    def print_banner(title: str, subtitle: str = ""):
        line = "=" * 72
        print_log(line, Colors.CYAN)
        print_log(f"  {title}", f"{Colors.BOLD}{Colors.CYAN}")
        if subtitle:
            print_log(f"  {subtitle}", Colors.GRAY)
        print_log(line, Colors.CYAN)

    def print_metric(label: str, value, color: str = Colors.RESET):
        print_log(f"  {label:<28} {value}", color)

    def print_status(status: str, details: str = ""):
        normalized_status = status.upper()
        color = Colors.GREEN if normalized_status in {"MATCHED", "SUCCESS", "PASSED"} else Colors.RED
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
    """Read the first field, skipping missing DealerIds and null placeholders."""
    dealer_ids = []
    with input_file.open(encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            dealer_id_value = raw_line.split("|", maxsplit=1)[0].strip()
            if dealer_id_value.casefold() in {"", "empty", "null", "none"}:
                continue
            dealer_ids.append(validate_dealer_id(
                dealer_id_value,
                source=f"Line {line_number}: DealerId",
            ))
    if not dealer_ids:
        raise ValueError(f"The file {input_file} contains no valid DealerIds.")
    return dealer_ids


def fetch_hostname(cursor, dealer_id: int) -> str:
    """Resolve the dealer hostname internally from Site_Config."""
    cursor.execute(SITE_CONFIG_QUERY, (dealer_id,))
    row = cursor.fetchone()
    if row is None or row[0] is None or not str(row[0]).strip():
        raise ValueError(f"No hostname was found in Site_Config for DealerId={dealer_id}.")
    return str(row[0]).strip()


def build_inventory_url(hostname: str) -> str:
    """Build the same inventory URL used by COUNT."""
    hostname = hostname.strip().rstrip("/")
    if not hostname.startswith(("http://", "https://")):
        hostname = f"https://{hostname}"
    return f"{hostname}{INVENTORY_PATH}"


def post_webhook_request(dealer_id: int, url: str) -> dict:
    """Send COUNT's payload fields with action COMPARE."""
    request = Request(
        WEBHOOK_URL,
        data=json.dumps({
            "action": "COMPARE", "dealer_id": str(dealer_id), "url": url,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Webhook returned HTTP {error.code} for COMPARE, DealerId={dealer_id}: "
            f"{body or error.reason}"
        ) from None
    except TimeoutError:
        raise RuntimeError(
            f"Webhook timed out for COMPARE, DealerId={dealer_id} "
            f"(timeout={REQUEST_TIMEOUT_SECONDS}s)."
        ) from None
    except URLError as error:
        raise RuntimeError(
            f"Webhook request failed for COMPARE, DealerId={dealer_id}: {error.reason}"
        ) from None
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Webhook returned invalid JSON for COMPARE, DealerId={dealer_id}: {error.msg}"
        ) from None
    if not isinstance(result, dict):
        raise RuntimeError("COMPARE webhook response must be a JSON object.")
    return result


def is_empty_zero_odometer_match(difference) -> bool:
    """Accept only an explicit mileage value mismatch between empty and zero."""
    if not isinstance(difference, dict):
        return False
    if difference.get("reason", "value_mismatch") != "value_mismatch":
        return False
    if str(difference.get("field", "")).casefold() not in {"miles", "odometer"}:
        return False
    dms = difference.get("dms")
    if not isinstance(dms, dict) or len(dms) != 1 or "client" not in difference:
        return False
    field, dms_value = next(iter(dms.items()))
    if field.casefold() != "odometer":
        return False
    client_value = difference["client"]

    def is_empty(value):
        return value is None or (isinstance(value, str) and not value.strip())

    def is_zero(value):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return False
        try:
            return Decimal(str(value).strip()) == 0
        except InvalidOperation:
            return False

    return (is_empty(dms_value) and is_zero(client_value)) or (
        is_zero(dms_value) and is_empty(client_value)
    )


def is_case_insensitive_make_match(difference) -> bool:
    """Ignore case, whitespace and trademark symbols when comparing Make."""
    if not isinstance(difference, dict):
        return False
    if difference.get("reason", "value_mismatch") != "value_mismatch":
        return False
    if str(difference.get("field", "")).casefold() != "make":
        return False
    dms = difference.get("dms")
    if not isinstance(dms, dict) or len(dms) != 1:
        return False
    field, dms_value = next(iter(dms.items()))
    client_value = difference.get("client")

    def normalize_make(value: str) -> str:
        value = value.replace("\u00ae", "").replace("\u2122", "")
        return " ".join(value.split()).casefold()

    return (
        field.casefold() == "make"
        and isinstance(dms_value, str)
        and isinstance(client_value, str)
        and bool(normalize_make(dms_value))
        and normalize_make(dms_value) == normalize_make(client_value)
    )


def is_model_containment_match(difference) -> bool:
    """Match nonempty Model text contained in either platform's Model value."""
    if not isinstance(difference, dict):
        return False
    if difference.get("reason", "value_mismatch") != "value_mismatch":
        return False
    if str(difference.get("field", "")).casefold() != "model":
        return False
    dms = difference.get("dms")
    client_value = difference.get("client")
    if not isinstance(dms, dict) or not isinstance(client_value, str):
        return False
    dms_value = next((value for key, value in dms.items() if key.casefold() == "model"), None)
    if not isinstance(dms_value, str):
        return False
    dms_model = dms_value.strip().casefold()
    client_model = client_value.strip().casefold()
    return bool(dms_model and client_model) and (
        dms_model in client_model or client_model in dms_model
    )


def normalize_comparison(result: dict) -> dict:
    """Apply local matching rules without changing the webhook response."""
    differences = result.get("differences") or []
    remaining = [
        item for item in differences
        if not (
            is_empty_zero_odometer_match(item)
            or is_case_insensitive_make_match(item)
            or is_model_containment_match(item)
        )
    ]
    normalized = dict(result, differences=remaining)
    if (
        differences and not remaining
        and str(result.get("status", "")).strip().casefold() == "failed"
        and not any(result.get(key) for key in ("errors", "error", "message"))
    ):
        normalized["status"] = "success"
    return normalized


def extract_errors(result: dict) -> list[str]:
    """Expect status=success and no errors; preserve structured error details."""
    result = normalize_comparison(result)
    errors = []
    for difference in result.get("differences") or []:
        if isinstance(difference, dict):
            errors.append(
                f"VIN {difference.get('vin', '?')} | {difference.get('field', '?')}: "
                f"DMS={json.dumps(difference.get('dms'), ensure_ascii=False)} | "
                f"CLIENT_PAGE={json.dumps(difference.get('client'), ensure_ascii=False)} "
                f"({difference.get('reason', 'difference')})"
            )
        else:
            errors.append(str(difference))
    for field in ("errors", "error"):
        value = result.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            errors.append(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
    if str(result.get("status", "")).strip().casefold() != "success" and not errors:
        detail = result.get("message")
        if detail:
            errors.append(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        else:
            errors.append(f"Webhook status was not success: {result.get('status', 'missing')}")
    return errors


def format_value(value) -> str:
    """Keep empty, missing and zero values visibly distinct."""
    if value is None:
        return "(null)"
    if value == "":
        return "(empty)"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_comparison_table(differences: list):
    """Group differences by VIN with DMS first and CLIENT_PAGE second."""
    groups = {}
    for difference in differences:
        if isinstance(difference, dict):
            groups.setdefault(str(difference.get("vin", "?")), []).append(difference)
        else:
            print_log(f"  - {difference}", Colors.RED)
    width = 48
    border = f"+{'-' * (width + 2)}+{'-' * (width + 2)}+"
    for vin, items in groups.items():
        print_log(f"\nVIN: {vin}", f"{Colors.BOLD}{Colors.WHITE}")
        print_log(border, Colors.GRAY)
        print_log(
            f"| {Colors.BOLD}{Colors.CYAN}{'DMS':<{width}}{Colors.RESET} | "
            f"{Colors.BOLD}{Colors.BLUE}{'CLIENT_PAGE':<{width}}{Colors.RESET} |"
        )
        print_log(border, Colors.GRAY)
        for item in items:
            dms = item.get("dms")
            if isinstance(dms, dict):
                left = "\n".join(f"{key}: {format_value(value)}" for key, value in dms.items())
            else:
                left = f"{item.get('field', '?')}: {format_value(dms)}"
            right = f"{item.get('field', '?')}: {format_value(item.get('client'))}"
            left_lines = [part for line in left.splitlines() for part in (textwrap.wrap(line, width) or [""])]
            right_lines = [part for line in right.splitlines() for part in (textwrap.wrap(line, width) or [""])]
            for left_line, right_line in zip_longest(left_lines, right_lines, fillvalue=""):
                print_log(f"| {left_line:<{width}} | {right_line:<{width}} |")
            reason = item.get("reason")
            if reason and reason != "value_mismatch":
                print_log(f"  Reason: {reason}", Colors.RED)
            print_log(border, Colors.GRAY)


def validate_dealer(cursor, dealer_id: int) -> list[str]:
    """Return comparison errors, or an empty list on success."""
    hostname = fetch_hostname(cursor, dealer_id)
    url = build_inventory_url(hostname)
    print_log(f"\nDealer {dealer_id}", f"{Colors.BOLD}{Colors.BLUE}")
    print_log(hostname, Colors.GRAY)
    result = normalize_comparison(post_webhook_request(dealer_id, url))
    errors = extract_errors(result)
    if errors:
        differences = result.get("differences") or []
        if differences:
            print_log(
                f"Compared: {result.get('compared', '?')} | Differences: {len(differences)}",
                Colors.RED,
            )
            print_comparison_table(differences)
            for error in extract_errors({key: value for key, value in result.items()
                                         if key in {"errors", "error", "message"}} | {"status": "success"}):
                print_log(f"  - {error}", Colors.RED)
        else:
            for error in errors:
                print_log(f"  - {error}", Colors.RED)
    return errors


def validate_outputs(dealer_ids: list[int]) -> list[dict]:
    """Return failures by dealer and continue after individual errors."""
    connection = None
    cursor = None
    failed_dealers = []
    try:
        print_log(f"  Connecting to {SERVER}/{DATABASE}...", Colors.CYAN)
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()
        for dealer_id in dealer_ids:
            try:
                errors = validate_dealer(cursor, dealer_id)
            except Exception as error:
                errors = [str(error)]
                print_log(f"  - {error}", Colors.RED)
            if errors:
                failed_dealers.append({"dealer_id": dealer_id, "errors": errors})
                print_status("FAILED", f"DealerId={dealer_id}")
            else:
                print_status("PASSED", f"DealerId={dealer_id}: COMPARE success")
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()
    print_log(
        f"{len(dealer_ids) - len(failed_dealers)} passed | "
        f"{len(failed_dealers)} failed | {len(dealer_ids)} total",
    )
    if failed_dealers:
        print_log("Failed DealerIds: " + ", ".join(str(item["dealer_id"]) for item in failed_dealers), Colors.RED)
    return failed_dealers


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Run COMPARE for one DealerId or the first field of each input.txt row."
    )
    parser.add_argument("dealer_id", nargs="?", help="Optional DealerId; skips input.txt.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_arguments()
        dealer_ids = (
            read_dealer_ids(INPUT_FILE) if args.dealer_id is None
            else [validate_dealer_id(args.dealer_id)]
        )
        print_banner("DEALER QA COMPARE VALIDATION", f"{len(dealer_ids)} dealer(s)")
        return 1 if validate_outputs(dealer_ids) else 0
    except Exception as error:
        print_log(f"Validation error: {error}", f"{Colors.BOLD}{Colors.RED}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
