import argparse
from pathlib import Path

import pyodbc


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

DMS_LOADING_COUNT_QUERY = """
SELECT COUNT(*)
FROM [dbo].[DMS_Loading] WITH (NOLOCK)
WHERE DealerId = ?;
"""

DEALER_FEED_COUNT_QUERY = """
SELECT [v4FeedCount]
FROM [dbo].[SQS_Dealer_Staged]
WHERE DealerId = ?;
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


def fetch_single_value(cursor, query: str, parameters=None):
    """Execute a query and return its single scalar result."""
    if parameters is None:
        cursor.execute(query)
    else:
        cursor.execute(query, parameters)
    row = cursor.fetchone()

    if row is None:
        raise RuntimeError("The validation query returned no rows.")

    return row[0]


def validate_outputs(dealer_ids: list[int]):
    """Verify that DMS_Loading count equals the dealer's v4FeedCount value."""
    connection = None
    cursor = None
    mismatches = []

    try:
        print(f"Connecting to {SERVER}/{DATABASE}...")
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for index, dealer_id in enumerate(dealer_ids, start=1):
            loading_count = fetch_single_value(
                cursor, DMS_LOADING_COUNT_QUERY, (dealer_id,)
            )
            dealer_feed_count = fetch_single_value(
                cursor, DEALER_FEED_COUNT_QUERY, (dealer_id,)
            )

            print(
                f"[{index}/{len(dealer_ids)}] DealerId={dealer_id}: "
                f"DMS_Loading count={loading_count}, v4FeedCount={dealer_feed_count}"
            )

            if loading_count != dealer_feed_count:
                mismatches.append((dealer_id, loading_count, dealer_feed_count))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print("Database connection closed.")

    if mismatches:
        details = "; ".join(
            f"DealerId={dealer_id}: count={count}, v4FeedCount={feed_count}"
            for dealer_id, count, feed_count in mismatches
        )
        raise RuntimeError(f"Validation failed. Output mismatch: {details}")

    print(f"Validation passed for {len(dealer_ids)} DealerId value(s).")


def parse_arguments():
    """Parse the optional DealerId command-line argument."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare the DMS_Loading count with the dealer's v4FeedCount value. "
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
    """Select file mode or single-dealer mode and validate query outputs."""
    args = parse_arguments()

    if args.dealer_id is None:
        dealer_ids = read_dealer_ids(INPUT_FILE)
        print(f"File mode: loaded {len(dealer_ids)} DealerId value(s) from {INPUT_FILE}.")
    else:
        dealer_ids = [validate_dealer_id(args.dealer_id)]
        print(f"Single-dealer mode: DealerId={dealer_ids[0]}.")

    validate_outputs(dealer_ids)


if __name__ == "__main__":
    # File mode:          python 4_ValidateDealerOutput.py
    # Single-dealer mode: python 4_ValidateDealerOutput.py 1785
    main()
