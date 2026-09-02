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

SP_CALL = "EXEC [dbo].[SQS_StagedDealerSwitchLive] @DealerId = ?"


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


def consume_results(cursor):
    """Consume all result sets so the cursor is ready for the next procedure."""
    while True:
        if cursor.description:
            for row in cursor.fetchall():
                print(row)

        if not cursor.nextset():
            break


def execute_stored_procedures(dealer_ids: list[int]):
    """Execute the stored procedure once for each DealerId."""
    connection = None
    cursor = None

    try:
        print(f"Connecting to {SERVER}/{DATABASE}...")
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for index, dealer_id in enumerate(dealer_ids, start=1):
            print(
                f"[{index}/{len(dealer_ids)}] Executing "
                f"SQS_StagedDealerSwitchLive for DealerId={dealer_id}..."
            )
            cursor.execute(SP_CALL, (dealer_id,))
            consume_results(cursor)

        # Commit all records together. If one fails, the except block rolls everything back.
        connection.commit()
        print(
            f"Completed: {len(dealer_ids)} stored procedures executed successfully."
        )

    except Exception:
        if connection is not None:
            connection.rollback()
        raise
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
            "Execute SQS_StagedDealerSwitchLive. Without a DealerId, values are "
            "read from input.txt."
        )
    )
    parser.add_argument(
        "dealer_id",
        nargs="?",
        help="Optional DealerId. When provided, input.txt is not read.",
    )
    return parser.parse_args()


def main():
    """Select file mode or single-dealer mode and execute the procedure."""
    args = parse_arguments()

    if args.dealer_id is None:
        dealer_ids = read_dealer_ids(INPUT_FILE)
        print(f"File mode: loaded {len(dealer_ids)} DealerId value(s) from {INPUT_FILE}.")
    else:
        dealer_ids = [validate_dealer_id(args.dealer_id)]
        print(f"Single-dealer mode: DealerId={dealer_ids[0]}.")

    execute_stored_procedures(dealer_ids)


if __name__ == "__main__":
    # File mode:          python 3_SQS_StagedDealerSwitchLive.py
    # Single-dealer mode: python 3_SQS_StagedDealerSwitchLive.py 1785
    main()
