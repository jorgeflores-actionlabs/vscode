import pyodbc


SERVER = "sqlag_pdxsql.external.pie.pdx.dealerspike.com"
DATABASE = "DMS_Imports"

CONNECTION_STRING = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server={SERVER};"
    f"Database={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

DUMMY_QUERY = """
SELECT *
FROM [dbo].[SQS_Dealer_Staged]
WHERE stageDateTime >= DATEADD(DAY, -1, CONVERT(date, GETDATE()))
  AND stageDateTime <  DATEADD(DAY,  1, CONVERT(date, GETDATE()))
ORDER BY stageDateTime DESC;
"""


def print_rows(cursor) -> None:
    columns = [column[0] for column in cursor.description]
    rows = cursor.fetchall()

    print(f"Rows returned: {len(rows)}")
    if not rows:
        return

    print(" | ".join(columns))
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def main() -> int:
    print(f"Connecting to {SERVER}/{DATABASE}...")
    print("Executing dummy read-only query...")

    with pyodbc.connect(CONNECTION_STRING) as connection:
        cursor = connection.cursor()
        cursor.execute(DUMMY_QUERY)
        print_rows(cursor)

    print("Dummy script completed without data changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
