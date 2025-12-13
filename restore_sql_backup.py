"""
Simple script to restore SQL Server database from a .bak backup file.

Requirements:
    pip install pyodbc
"""

import pyodbc
import os
import sys
import argparse
from pathlib import Path


def get_connection(server: str, username: str = None, password: str = None, use_windows_auth: bool = True) -> pyodbc.Connection:
    """Connect to SQL Server (master database)."""
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
                conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE=master;Trusted_Connection=yes;"
            else:
                conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE=master;UID={username};PWD={password};"
            conn = pyodbc.connect(conn_str, timeout=30)
            print(f"✓ Connected using driver: {driver}")
            return conn
        except pyodbc.Error:
            continue
    
    raise Exception(f"Could not connect. Available drivers: {[d for d in pyodbc.drivers()]}")


def get_backup_file_list(conn: pyodbc.Connection, backup_file: str):
    """Get file list from backup to determine logical file names."""
    print(f"\nReading backup file structure: {backup_file}")
    
    # Escape backslashes for SQL
    backup_path_escaped = backup_file.replace('\\', '\\\\')
    
    query = f"""
    RESTORE FILELISTONLY
    FROM DISK = '{backup_path_escaped}'
    """
    
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        files = cursor.fetchall()
        
        if not files:
            raise Exception("Could not read file list from backup")
        
        # Get column names
        columns = [column[0] for column in cursor.description]
        
        print("\nBackup file contents:")
        for row in files:
            row_dict = dict(zip(columns, row))
            print(f"  Logical Name: {row_dict.get('LogicalName', 'N/A')}")
            print(f"  Physical Name: {row_dict.get('PhysicalName', 'N/A')}")
            print(f"  Type: {row_dict.get('Type', 'N/A')}")
            print()
        
        return files, columns
    except Exception as e:
        print(f"Warning: Could not read file list: {e}")
        return None, None


def restore_database(conn: pyodbc.Connection, backup_file: str, target_database: str, 
                    data_path: str = None, log_path: str = None):
    """Restore database from backup file."""
    
    if not os.path.exists(backup_file):
        raise FileNotFoundError(f"Backup file not found: {backup_file}")
    
    print(f"\nPreparing to restore database '{target_database}' from:")
    print(f"  {backup_file}")
    
    # Get default paths if not provided
    if not data_path or not log_path:
        print("\nGetting SQL Server default data paths...")
        cursor = conn.cursor()
        cursor.execute("SELECT SERVERPROPERTY('InstanceDefaultDataPath'), SERVERPROPERTY('InstanceDefaultLogPath')")
        row = cursor.fetchone()
        default_data = row[0] if row[0] else "C:\\Program Files\\Microsoft SQL Server\\MSSQL15.MSSQLSERVER\\MSSQL\\DATA\\"
        default_log = row[1] if row[1] else default_data
        
        if not data_path:
            data_path = os.path.join(default_data, f"{target_database}.mdf")
        if not log_path:
            log_path = os.path.join(default_log, f"{target_database}_Log.ldf")
    
    print(f"\nData file location: {data_path}")
    print(f"Log file location: {log_path}")
    
    # Get file list from backup
    files, columns = get_backup_file_list(conn, backup_file)
    
    # Escape paths for SQL
    backup_path_escaped = backup_file.replace('\\', '\\\\')
    data_path_escaped = data_path.replace('\\', '\\\\')
    log_path_escaped = log_path.replace('\\', '\\\\')
    
    if files and columns:
        # Extract logical names
        logical_names = {}
        for row in files:
            row_dict = dict(zip(columns, row))
            logical_name = row_dict.get('LogicalName')
            file_type = row_dict.get('Type')
            
            if file_type == 'D':  # Data file
                logical_names['data'] = logical_name
            elif file_type == 'L':  # Log file
                logical_names['log'] = logical_name
        
        logical_data = logical_names.get('data', target_database)
        logical_log = logical_names.get('log', f"{target_database}_Log")
    else:
        # Fallback to default names
        logical_data = target_database
        logical_log = f"{target_database}_Log"
        print("Using default logical file names")
    
    print(f"\nLogical data file name: {logical_data}")
    print(f"Logical log file name: {logical_log}")
    
    # Build RESTORE command
    restore_sql = f"""
    RESTORE DATABASE [{target_database}]
    FROM DISK = '{backup_path_escaped}'
    WITH 
        MOVE '{logical_data}' TO '{data_path_escaped}',
        MOVE '{logical_log}' TO '{log_path_escaped}',
        REPLACE,
        NOUNLOAD,
        STATS = 10
    """
    
    print(f"\n{'='*60}")
    print("Restoring database...")
    print(f"{'='*60}")
    print("This may take a while depending on backup size...\n")
    
    cursor = conn.cursor()
    try:
        cursor.execute(restore_sql)
        
        # Show progress
        while cursor.nextset():
            if cursor.messages:
                for msg in cursor.messages:
                    print(msg[1])
        
        conn.commit()
        print(f"\n{'='*60}")
        print(f"✓ Successfully restored database '{target_database}'")
        print(f"{'='*60}")
        return True
    except Exception as e:
        print(f"\n✗ Restore failed: {e}")
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Restore SQL Server database from .bak backup file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Restore using Windows authentication
  python restore_sql_backup.py --server localhost --backup "C:\\backups\\mydb.bak" --database MyDatabase

  # Restore using SQL Server authentication
  python restore_sql_backup.py --server localhost --backup "C:\\backups\\mydb.bak" --database MyDatabase --username sa --password MyPassword

  # Specify custom file paths
  python restore_sql_backup.py --server localhost --backup "C:\\backups\\mydb.bak" --database MyDatabase --data-path "D:\\Data\\MyDatabase.mdf" --log-path "D:\\Logs\\MyDatabase_Log.ldf"
        """
    )
    
    parser.add_argument('--server', required=True, 
                       help='SQL Server instance (e.g., localhost or localhost\\SQLEXPRESS)')
    parser.add_argument('--backup', required=True, 
                       help='Path to .bak backup file')
    parser.add_argument('--database', required=True, 
                       help='Name for the restored database')
    parser.add_argument('--data-path', 
                       help='Path for .mdf data file (optional, uses default if not specified)')
    parser.add_argument('--log-path', 
                       help='Path for .ldf log file (optional, uses default if not specified)')
    parser.add_argument('--username', 
                       help='SQL Server username (for SQL auth)')
    parser.add_argument('--password', 
                       help='SQL Server password (for SQL auth)')
    parser.add_argument('--no-windows-auth', action='store_true', 
                       help='Use SQL Server authentication instead of Windows auth')
    
    args = parser.parse_args()
    
    use_windows_auth = not args.no_windows_auth
    
    if not use_windows_auth and (not args.username or not args.password):
        parser.error("Username and password required when using SQL Server authentication")
    
    try:
        print("Connecting to SQL Server...")
        conn = get_connection(
            server=args.server,
            username=args.username,
            password=args.password,
            use_windows_auth=use_windows_auth
        )
        
        restore_database(
            conn=conn,
            backup_file=args.backup,
            target_database=args.database,
            data_path=args.data_path,
            log_path=args.log_path
        )
        
        conn.close()
        print("\n✓ Restore complete!")
        
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()




