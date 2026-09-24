import argparse
from pathlib import Path

import pyodbc


SERVER = "sqlag_pdxsql.external.pie.pdx.dealerspike.com"
DATABASE = "DMS_Imports"
INPUT_FILE = Path(__file__).with_name("input.txt")

# input.txt contains four required columns separated by "|":
# DealerId | S3ProfileFolder | FTPProfileFolder | DealerFolderName
# A fifth value may be present for display/context and is ignored here.
# The stored procedure requires a fifth parameter that is not included in the file.
# Use 0 for @S3GuidLookup, as in the original example.
DEFAULT_S3_GUID_LOOKUP = 0

CONNECTION_STRING = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server={SERVER};"
    f"Database={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

SP_CALL = """
EXEC [dbo].[SQS_SetStagedDealerForProcessing]
    @DealerId = ?,
    @FTPProfileFolder = ?,
    @S3ProfileFolder = ?,
    @DealerFolderName = ?,
    @S3GuidLookup = ?
"""


def validate_dealer_id(value: str, source: str = "DealerId") -> int:
    try:
        dealer_id = int(value)
    except ValueError as error:
        raise ValueError(f"{source} must be an integer.") from error

    if dealer_id <= 0:
        raise ValueError(f"{source} must be a positive integer.")

    return dealer_id


def build_record(
    dealer_id: int,
    s3_profile: str,
    ftp_profile: str,
    dealer_folder: str,
    s3_guid_lookup: int = DEFAULT_S3_GUID_LOOKUP,
):
    if not all([s3_profile, ftp_profile, dealer_folder]):
        raise ValueError("S3ProfileFolder, FTPProfileFolder, and DealerFolderName are required.")

    return (
        dealer_id,
        ftp_profile,
        s3_profile,
        dealer_folder,
        s3_guid_lookup,
    )


def read_input(input_file: Path):
    """Read and validate the records that will be sent to the stored procedure."""
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

            dealer_id_text, s3_profile, ftp_profile, dealer_folder = fields[:4]
            if not all(fields[:4]):
                raise ValueError(f"Line {line_number}: one or more fields are empty.")

            records.append(
                build_record(
                    validate_dealer_id(dealer_id_text, f"Line {line_number} DealerId"),
                    s3_profile,
                    ftp_profile,
                    dealer_folder,
                )
            )

    if not records:
        raise ValueError(f"The file {input_file} contains no records.")

    return records


def filter_records_by_dealer_id(records, dealer_id: int):
    matching_records = [record for record in records if record[0] == dealer_id]
    if not matching_records:
        raise ValueError(f"DealerId {dealer_id} was not found in {INPUT_FILE}.")
    return matching_records


def consume_results(cursor):
    """Consume all result sets so the cursor is ready for the next procedure."""
    while True:
        if cursor.description:
            for row in cursor.fetchall():
                print(row)

        if not cursor.nextset():
            break


def execute_stored_procedures(records):
    """Execute the stored procedure once for each validated record."""
    connection = None
    cursor = None

    try:
        print(f"Connecting to {SERVER}/{DATABASE}...")
        connection = pyodbc.connect(CONNECTION_STRING)
        cursor = connection.cursor()

        for index, arguments in enumerate(records, start=1):
            dealer_id = arguments[0]
            print(f"[{index}/{len(records)}] Executing SP for DealerId={dealer_id}...")
            cursor.execute(SP_CALL, arguments)
            consume_results(cursor)

        # Commit all records together. If one fails, the except block rolls everything back.
        connection.commit()
        print(f"Completed: {len(records)} stored procedures executed successfully.")

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
    parser = argparse.ArgumentParser(
        description=(
            "Execute SQS_SetStagedDealerForProcessing. Without parameters, records "
            "are read from input.txt."
        )
    )
    parser.add_argument(
        "dealer_id",
        nargs="?",
        help="Optional DealerId. When provided, the matching record is read from input.txt.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    if args.dealer_id is None:
        records = read_input(INPUT_FILE)
        print(f"File mode: loaded {len(records)} record(s) from {INPUT_FILE}.")
    else:
        dealer_id = validate_dealer_id(args.dealer_id)
        records = filter_records_by_dealer_id(read_input(INPUT_FILE), dealer_id)
        print(f"Single-dealer mode: DealerId={records[0][0]}.")

    execute_stored_procedures(records)


if __name__ == "__main__":
    # File mode:
    #   python 1_SQS_SetStagedDealerForProcessing.py
    # Single-dealer mode:
    #   python 1_SQS_SetStagedDealerForProcessing.py 5221
    main()
