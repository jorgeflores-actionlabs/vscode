import pyodbc

server = 'sqlag_pdxsql.external.pie.pdx.dealerspike.com' 
database = 'DMS_Imports' 


connection_string = (
    f"Driver={{ODBC Driver 18 for SQL Server}};"
    f"Server={server};"
    f"Database={database};"
    f"Trusted_Connection=yes;"
    f"TrustServerCertificate=yes;" 
)

try:
    print("Trying to connect to the DB...")
    connection = pyodbc.connect(connection_string)
    print("Success!")
    cursor = connection.cursor()
    ###########################################################
    sp_call = """
    EXEC [dbo].[SQS_SetStagedDealerForProcessing]
        @DealerId = ?,
        @FTPProfileFolder = ?,
        @S3ProfileFolder = ?,
        @DealerFolderName = ?,
        @S3GuidLookup = ?
    """

    arguments = [
        123,
        "folder1",
        "folder2",
        "dealer123",
        0
    ]

    cursor.execute(sp_call, arguments)
    print("Executing stored procedure...")
    cursor.execute(sp_call, arguments)
    ###########################################################
    if cursor.description:  # Checks if the execution returned a dataset
        rows = cursor.fetchall()
        for row in rows:
            # Access columns by index (e.g., row[0]) or loop through them
            print(row)
    else:
        print("Stored procedure executed successfully with no returned rows.")
except pyodbc.Error as e:
    print(f"An error occurred while executing the database operation: {e}")
    if connection:
        connection.rollback()  # Roll back transaction on failure
finally:
    # 6. Ensure connection is safely closed
    if connection:
        cursor.close()
        connection.close()
        print("Database connection closed.")