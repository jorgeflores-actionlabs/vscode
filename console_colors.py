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


def print_log(message: str, color: str = Colors.RESET):
    """Print a colored log message and reset the terminal color afterward."""
    print(f"{color}{message}{Colors.RESET}")
