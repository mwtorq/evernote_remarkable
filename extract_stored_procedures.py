"""
Extract stored procedures from SQL Server database backup files.

This script can:
1. Connect to a restored SQL Server database and extract stored procedures
2. Restore a backup file and then extract stored procedures (requires server connection)

Requirements:
    pip install pyodbc

For Windows, you may need the SQL Server ODBC driver installed.
Download from: https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
"""

import pyodbc
import os
import sys
from pathlib import Path
from typing import List, Dict, Optional
import argparse


def get_sql_server_connection(server: str, database: str, username: Optional[str] = None, 
                              password: Optional[str] = None, use_windows_auth: bool = True) -> pyodbc.Connection:
    """
    Create a connection to SQL Server.
    
    Args:
        server: SQL Server instance (e.g., 'localhost' or 'localhost\\SQLEXPRESS')
        database: Database name
        username: SQL Server username (if not using Windows auth)
        password: SQL Server password (if not using Windows auth)
        use_windows_auth: Use Windows authentication (default: True)
    
    Returns:
        pyodbc.Connection object
    """
    if use_windows_auth:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
        )
    else:
        if not username or not password:
            raise ValueError("Username and password required when not using Windows authentication")
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
        )
    
    try:
        conn = pyodbc.connect(conn_str, timeout=30)
        print(f"✓ Connected to {server}\\{database}")
        return conn
    except pyodbc.Error as e:
        print(f"✗ Connection failed: {e}")
        print("\nTrying alternative driver names...")
        # Try alternative driver names
        drivers = [
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "SQL Server",
            "SQL Server Native Client 11.0"
        ]
        
        for driver in drivers:
            try:
                if use_windows_auth:
                    conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};Trusted_Connection=yes;"
                else:
                    conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={username};PWD={password};"
                conn = pyodbc.connect(conn_str, timeout=30)
                print(f"✓ Connected using driver: {driver}")
                return conn
            except pyodbc.Error:
                continue
        
        raise Exception(f"Could not connect with any available driver. Available drivers: {[d for d in pyodbc.drivers()]}")


def restore_database_backup(conn: pyodbc.Connection, backup_file: str, 
                           target_database: str, data_file_path: Optional[str] = None,
                           log_file_path: Optional[str] = None) -> bool:
    """
    Restore a database backup file.
    
    Args:
        conn: Connection to SQL Server (master database recommended)
        backup_file: Path to .bak file
        target_database: Name for the restored database
        data_file_path: Optional path for .mdf file
        log_file_path: Optional path for .ldf file
    
    Returns:
        True if successful
    """
    if not os.path.exists(backup_file):
        raise FileNotFoundError(f"Backup file not found: {backup_file}")
    
    # Get default data directory if not provided
    if not data_file_path or not log_file_path:
        cursor = conn.cursor()
        cursor.execute("SELECT SERVERPROPERTY('InstanceDefaultDataPath'), SERVERPROPERTY('InstanceDefaultLogPath')")
        row = cursor.fetchone()
        default_data_path = row[0] if row[0] else "C:\\Program Files\\Microsoft SQL Server\\MSSQL15.MSSQLSERVER\\MSSQL\\DATA\\"
        default_log_path = row[1] if row[1] else default_data_path
        
        if not data_file_path:
            data_file_path = os.path.join(default_data_path, f"{target_database}.mdf")
        if not log_file_path:
            log_file_path = os.path.join(default_log_path, f"{target_database}_Log.ldf")
    
    # Get file list from backup
    print(f"Reading backup file structure: {backup_file}")
    cursor = conn.cursor()
    
    # First, get the file list from the backup
    restore_filelistonly = f"""
    RESTORE FILELISTONLY
    FROM DISK = '{backup_file.replace(chr(92), chr(92)+chr(92))}'
    """
    
    try:
        cursor.execute(restore_filelistonly)
        files = cursor.fetchall()
        
        if not files:
            raise Exception("Could not read file list from backup. Check file path and permissions.")
        
        # Extract logical file names
        logical_data_name = files[0][0]  # LogicalName column
        logical_log_name = files[1][0] if len(files) > 1 else None
        
        print(f"Logical data file: {logical_data_name}")
        if logical_log_name:
            print(f"Logical log file: {logical_log_name}")
        
    except Exception as e:
        print(f"Warning: Could not read file list: {e}")
        print("Attempting restore with default file names...")
        logical_data_name = target_database
        logical_log_name = f"{target_database}_Log"
    
    # Restore the database
    restore_sql = f"""
    RESTORE DATABASE [{target_database}]
    FROM DISK = '{backup_file.replace(chr(92), chr(92)+chr(92))}'
    WITH 
        MOVE '{logical_data_name}' TO '{data_file_path.replace(chr(92), chr(92)+chr(92))}',
        MOVE '{logical_log_name}' TO '{log_file_path.replace(chr(92), chr(92)+chr(92))}',
        REPLACE,
        NOUNLOAD,
        STATS = 10
    """
    
    print(f"\nRestoring database '{target_database}' from backup...")
    print("This may take a while depending on backup size...")
    
    try:
        cursor.execute(restore_sql)
        conn.commit()
        print(f"✓ Successfully restored database '{target_database}'")
        return True
    except Exception as e:
        print(f"✗ Restore failed: {e}")
        conn.rollback()
        raise


def get_all_stored_procedures(conn: pyodbc.Connection, schema: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Get all stored procedures from the database.
    
    Args:
        conn: Database connection
        schema: Optional schema name filter (default: all schemas)
    
    Returns:
        List of dictionaries with 'schema', 'name', and 'definition' keys
    """
    cursor = conn.cursor()
    
    if schema:
        query = """
        SELECT 
            OBJECT_SCHEMA_NAME(p.object_id) AS schema_name,
            p.name AS procedure_name,
            OBJECT_DEFINITION(p.object_id) AS definition
        FROM sys.procedures p
        WHERE OBJECT_SCHEMA_NAME(p.object_id) = ?
        ORDER BY OBJECT_SCHEMA_NAME(p.object_id), p.name
        """
        cursor.execute(query, schema)
    else:
        query = """
        SELECT 
            OBJECT_SCHEMA_NAME(p.object_id) AS schema_name,
            p.name AS procedure_name,
            OBJECT_DEFINITION(p.object_id) AS definition
        FROM sys.procedures p
        ORDER BY OBJECT_SCHEMA_NAME(p.object_id), p.name
        """
        cursor.execute(query)
    
    procedures = []
    for row in cursor.fetchall():
        schema_name, proc_name, definition = row
        if definition:  # Only include procedures with definitions
            procedures.append({
                'schema': schema_name or 'dbo',
                'name': proc_name,
                'definition': definition
            })
    
    return procedures


def save_procedures_to_files(procedures: List[Dict[str, str]], output_dir: str, 
                             single_file: bool = False, filename: str = "all_procedures.sql"):
    """
    Save stored procedures to files.
    
    Args:
        procedures: List of procedure dictionaries
        output_dir: Directory to save files
        single_file: If True, save all to one file; if False, one file per procedure
        filename: Name for single output file (if single_file=True)
    """
    os.makedirs(output_dir, exist_ok=True)
    
    if single_file:
        # Save all procedures to a single file
        output_path = os.path.join(output_dir, filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"-- Extracted {len(procedures)} stored procedures\n")
            f.write(f"-- Generated by extract_stored_procedures.py\n\n")
            
            for proc in procedures:
                f.write(f"\n{'='*80}\n")
                f.write(f"-- Procedure: {proc['schema']}.{proc['name']}\n")
                f.write(f"{'='*80}\n\n")
                f.write(proc['definition'])
                f.write("\n\n")
                f.write("GO\n\n")
        
        print(f"✓ Saved {len(procedures)} procedures to: {output_path}")
    else:
        # Save each procedure to a separate file
        saved_count = 0
        for proc in procedures:
            # Sanitize filename
            safe_name = proc['name'].replace(' ', '_').replace('/', '_').replace('\\', '_')
            safe_schema = proc['schema'].replace(' ', '_')
            filename = f"{safe_schema}.{safe_name}.sql"
            output_path = os.path.join(output_dir, filename)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"-- Procedure: {proc['schema']}.{proc['name']}\n")
                f.write(f"-- Extracted by extract_stored_procedures.py\n\n")
                f.write(proc['definition'])
                f.write("\n\nGO\n")
            
            saved_count += 1
        
        print(f"✓ Saved {saved_count} procedures to individual files in: {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract stored procedures from SQL Server database backup files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract from already restored database
  python extract_stored_procedures.py --server localhost --database MyDatabase --output ./procedures

  # Restore backup and extract
  python extract_stored_procedures.py --server localhost --backup backup.bak --database MyRestoredDB --output ./procedures

  # Use SQL Server authentication
  python extract_stored_procedures.py --server localhost --database MyDatabase --username sa --password MyPassword --no-windows-auth --output ./procedures

  # Save to single file
  python extract_stored_procedures.py --server localhost --database MyDatabase --output ./procedures --single-file
        """
    )
    
    parser.add_argument('--server', required=True, help='SQL Server instance (e.g., localhost or localhost\\SQLEXPRESS)')
    parser.add_argument('--database', help='Database name (required if not using --backup)')
    parser.add_argument('--backup', help='Path to .bak backup file to restore')
    parser.add_argument('--output', default='./extracted_procedures', help='Output directory for extracted procedures')
    parser.add_argument('--schema', help='Filter by schema name (optional)')
    parser.add_argument('--single-file', action='store_true', help='Save all procedures to a single file')
    parser.add_argument('--username', help='SQL Server username (for SQL auth)')
    parser.add_argument('--password', help='SQL Server password (for SQL auth)')
    parser.add_argument('--no-windows-auth', action='store_true', help='Use SQL Server authentication instead of Windows auth')
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.backup and not args.database:
        parser.error("Either --database or --backup must be provided")
    
    if args.backup and not args.database:
        parser.error("--database is required when using --backup (specifies name for restored database)")
    
    use_windows_auth = not args.no_windows_auth
    
    try:
        # Connect to server (use master database for restore operations)
        print("Connecting to SQL Server...")
        master_conn = get_sql_server_connection(
            server=args.server,
            database='master',
            username=args.username,
            password=args.password,
            use_windows_auth=use_windows_auth
        )
        
        # Restore backup if provided
        if args.backup:
            restore_database_backup(master_conn, args.backup, args.database)
            master_conn.close()
            
            # Reconnect to the restored database
            print(f"\nConnecting to restored database '{args.database}'...")
            db_conn = get_sql_server_connection(
                server=args.server,
                database=args.database,
                username=args.username,
                password=args.password,
                use_windows_auth=use_windows_auth
            )
        else:
            # Connect directly to the specified database
            db_conn = get_sql_server_connection(
                server=args.server,
                database=args.database,
                username=args.username,
                password=args.password,
                use_windows_auth=use_windows_auth
            )
        
        # Extract stored procedures
        print(f"\nExtracting stored procedures from '{args.database}'...")
        if args.schema:
            print(f"Filtering by schema: {args.schema}")
        
        procedures = get_all_stored_procedures(db_conn, args.schema)
        
        if not procedures:
            print(f"\n⚠ No stored procedures found in database '{args.database}'")
            if args.schema:
                print(f"  (filtered by schema: {args.schema})")
            return
        
        print(f"✓ Found {len(procedures)} stored procedure(s)")
        
        # Save to files
        print(f"\nSaving procedures to: {args.output}")
        save_procedures_to_files(procedures, args.output, args.single_file)
        
        db_conn.close()
        print("\n✓ Extraction complete!")
        
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()




