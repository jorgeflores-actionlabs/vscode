from pathlib import Path

import pyodbc


SERVER = "sqlag_pdxsql.external.pie.pdx.dealerspike.com"
DATABASE = "DMS_Imports"
INPUT_FILE = Path(__file__).with_name("input.txt")

# input.txt contains four columns separated by "|":
# DealerId | S3ProfileFolder | FTPProfileFolder | DealerFolderName
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


def read_input(input_file: Path):
    """Read and validate the records that will be sent to the stored procedure."""
    records = []

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

            dealer_id_text, s3_profile, ftp_profile, dealer_folder = fields
            if not all(fields):
                raise ValueError(f"Line {line_number}: one or more fields are empty.")

            try:
                dealer_id = int(dealer_id_text)
            except ValueError as error:
                raise ValueError(
                    f"Line {line_number}: DealerId must be an integer."
                ) from error

            records.append(
                (
                    dealer_id,
                    ftp_profile,
                    s3_profile,
                    dealer_folder,
                    DEFAULT_S3_GUID_LOOKUP,
                )
            )

    if not records:
        raise ValueError(f"The file {input_file} contains no records.")

    return records


def consume_results(cursor):
    """Consume all result sets so the cursor is ready for the next procedure."""
    while True:
        if cursor.description:
            for row in cursor.fetchall():
                print(row)

        if not cursor.nextset():
            break


def execute_stored_procedures(input_file: Path = INPUT_FILE):
    """Execute the stored procedure once for each valid input.txt record."""
    records = read_input(input_file)
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


if __name__ == "__main__":
    # Run manually with: python sqlserver_connection.py
    execute_stored_procedures()
