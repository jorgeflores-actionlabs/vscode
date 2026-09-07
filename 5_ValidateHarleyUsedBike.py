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

HARLEY_USEDBIKE_QUERY = """
SELECT DISTINCT harley_usedbike_id
FROM [Harley].[dbo].[harley_used_bikes] WITH (NOLOCK)
WHERE dealership_id = ?
  AND Automaintained = 1
  AND deleted = 0;
"""

UNIT_LINKING_QUERY = """
SELECT DISTINCT hubid
FROM [Harley].[dbo].[SQS_Unit_Linking] WITH (NOLOCK)
WHERE dealerId = ?
  AND hubid IN ({placeholders});
"""

# SQL Server accepts at most 2,100 parameters per statement. Leave room for
# the DealerId parameter used by the second query.
MAX_IDS_PER_QUERY = 2_000


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


def fetch_harley_usedbike_ids(cursor, dealer_id: int) -> set[int]:
    """Return active auto-maintained Harley used-bike IDs for a dealer."""
    cursor.execute(HARLEY_USEDBIKE_QUERY, (dealer_id,))
    return {int(row[0]) for row in cursor.fetchall() if row[0] is not None}


def fetch_linked_hubids(cursor, dealer_id: int, harley_usedbike_ids: set[int]) -> set[int]:
    """Return source IDs found as hubid in SQS_Unit_Linking."""
    matched_ids = set()
    ordered_ids = sorted(harley_usedbike_ids)

    for offset in range(0, len(ordered_ids), MAX_IDS_PER_QUERY):
        id_batch = ordered_ids[offset : offset + MAX_IDS_PER_QUERY]
        placeholders = ", ".join("?" for _ in id_batch)
        query = UNIT_LINKING_QUERY.format(placeholders=placeholders)
        cursor.execute(query, (dealer_id, *id_batch))
        matched_ids.update(int(row[0]) for row in cursor.fetchall() if row[0] is not None)

    return matched_ids


def validate_dealer(dealer_id: int):
    """Validate that every source Harley ID has a matching linked hubid."""
    connection = None
    cursor = None

    try:
        print(f"Connecting to {SERVER}/{DATABASE}...")
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        source_ids = fetch_harley_usedbike_ids(cursor, dealer_id)
        matched_ids = fetch_linked_hubids(cursor, dealer_id, source_ids)
        missing_ids = source_ids - matched_ids

        source_count = len(source_ids)
        matched_count = len(matched_ids)
        status = "MATCHED" if not missing_ids and source_count == matched_count else "MISMATCH"

        print(
            f"DealerId={dealer_id}: harley_used_bike records={source_count}, "
            f"matched hubid records={matched_count}, status={status}"
        )

        if missing_ids:
            print(f"Missing hubid values ({len(missing_ids)}): {sorted(missing_ids)}")

        if status != "MATCHED":
            raise RuntimeError(
                f"Validation failed for DealerId={dealer_id}: "
                f"{source_count} source records versus {matched_count} matched records."
            )

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
            print("Database connection closed.")


def parse_arguments():
    """Parse the optional DealerId command-line argument."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate Harley used-bike IDs against SQS_Unit_Linking. "
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
        print(f"File mode: loaded {len(dealer_ids)} DealerId value(s) from {INPUT_FILE}.")
    else:
        dealer_ids = [validate_dealer_id(args.dealer_id)]
        print(f"Single-dealer mode: DealerId={dealer_ids[0]}.")

    for dealer_id in dealer_ids:
        validate_dealer(dealer_id)

    print(f"Validation passed for {len(dealer_ids)} DealerId value(s).")


if __name__ == "__main__":
    # File mode: python 5_ValidateHarleyUsedBike.py
    # Single-dealer mode: python 5_ValidateHarleyUsedBike.py 2379
    main()
