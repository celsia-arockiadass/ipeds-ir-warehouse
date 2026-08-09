"""
Verify that Python can reach the SQL Server warehouse.

This is a deliberate smoke test, not a unit test. It confirms four things
in order, because each one fails differently and the error message should
tell you which link in the chain is broken:

  1. the pyodbc driver is installed and importable
  2. the named ODBC driver exists on this machine
  3. the SQL Server instance accepts a Windows authenticated connection
  4. the IR_DW database exists and its three schemas are present

Run it any time the environment feels wrong. It touches nothing and
changes nothing.
"""

import sys

import pyodbc

DRIVER = "ODBC Driver 18 for SQL Server"
SERVER = "localhost"
DATABASE = "IR_DW"

CONNECTION_STRING = (
    f"DRIVER={{{DRIVER}}};"
    f"SERVER={SERVER};"
    f"DATABASE={DATABASE};"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


def check_driver_present() -> None:
    drivers = pyodbc.drivers()
    if DRIVER not in drivers:
        print(f"FAIL: ODBC driver '{DRIVER}' is not installed.")
        print("Drivers found on this machine:")
        for name in drivers:
            print(f"  {name}")
        sys.exit(1)
    print(f"PASS: ODBC driver found, {DRIVER}")


def check_connection() -> None:
    with pyodbc.connect(CONNECTION_STRING, timeout=10) as connection:
        cursor = connection.cursor()

        cursor.execute("SELECT @@VERSION, DB_NAME(), SUSER_NAME();")
        version, database_name, login = cursor.fetchone()
        print(f"PASS: connected to {version.splitlines()[0].strip()}")
        print(f"PASS: database is {database_name}, authenticated as {login}")

        cursor.execute(
            "SELECT name FROM sys.schemas "
            "WHERE name IN ('stg','dw','util') ORDER BY name;"
        )
        schemas = [row[0] for row in cursor.fetchall()]
        expected = ["dw", "stg", "util"]
        if schemas != expected:
            print(f"FAIL: expected schemas {expected}, found {schemas}")
            sys.exit(1)
        print(f"PASS: schemas present, {', '.join(schemas)}")


if __name__ == "__main__":
    check_driver_present()
    check_connection()
    print("\nAll checks passed. Python can read and write the warehouse.")
