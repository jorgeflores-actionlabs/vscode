"""Reusable colored console output helpers for the SQL Server scripts."""


class Colors:
    """ANSI colors supported by modern PowerShell and Windows Terminal."""

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
    """Print a colored log message and reset the terminal color afterward."""
    print(f"{color}{message}{Colors.RESET}")


def print_banner(title: str, subtitle: str = ""):
    """Print a consistent section banner for command-line scripts."""
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
