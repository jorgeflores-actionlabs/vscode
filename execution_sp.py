import pyodbc

# 1. Define your connection string parameters
# Adjust these variables to match your environment
SERVER = 'your_server_name_or_ip'
DATABASE = 'your_database_name'
USERNAME = 'your_username'        # Keep blank if using Windows Authentication
PASSWORD = 'your_password'        # Keep blank if using Windows Authentication
USE_WINDOWS_AUTH = True           # Set to False if using SQL Server Authentication

def get_connection():
    """Establishes and returns a connection to the SQL Server database."""
    if USE_WINDOWS_AUTH:
        # Windows Authentication (Trusted Connection)
        conn_str = f"DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;"
    else:
        # SQL Server Authentication
        conn_str = f"DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};UID={USERNAME};PWD={PASSWORD};"
    
    return pyodbc.connect(conn_str)

def execute_stored_procedure():
    connection = None
    try:
        # 2. Open the database connection
        connection = get_connection()
        cursor = connection.cursor()

        # 3. Define the Stored Procedure call
        # Use the T-SQL EXEC syntax with '?' placeholders for parameters
        sp_call = "EXEC YourStoredProcedureName @Param1 = ?, @Param2 = ?"
        
        # Define the arguments you want to pass into the placeholders
        arguments = ('Value1', 123)

        print("Executing stored procedure...")
        cursor.execute(sp_call, arguments)

        # 4. Process the results (If the Stored Procedure returns a SELECT result)
        # Note: If your procedure performs INSERT/UPDATE/DELETE, skip to step 5.
        if cursor.description:  # Checks if the execution returned a dataset
            rows = cursor.fetchall()
            for row in rows:
                # Access columns by index (e.g., row[0]) or loop through them
                print(row)
        else:
            print("Stored procedure executed successfully with no returned rows.")

        # 5. Commit changes if your procedure modifies data (INSERT/UPDATE/DELETE)
        connection.commit()

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

if __name__ == "__main__":
    execute_stored_procedure()
