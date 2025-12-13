# SQL Server Stored Procedure Extractor

This tool extracts stored procedures from SQL Server database backup files (`.bak`) or directly from restored databases.

## Prerequisites

1. **Python 3.7+**
2. **SQL Server ODBC Driver** (required for pyodbc)
   - Windows: Usually pre-installed, but you may need to download from [Microsoft](https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
   - The script will try multiple driver versions automatically
3. **SQL Server Instance** (to restore backups or access existing databases)

## Installation

```bash
pip install -r requirements_sql_extract.txt
```

## Usage

### Extract from an Already Restored Database

If you already have a restored database:

```bash
python extract_stored_procedures.py --server localhost --database MyDatabase --output ./procedures
```

### Restore Backup and Extract

To restore a backup file and then extract procedures:

```bash
python extract_stored_procedures.py --server localhost --backup "C:\path\to\backup.bak" --database MyRestoredDB --output ./procedures
```

### Using SQL Server Authentication

If you need to use SQL Server authentication instead of Windows authentication:

```bash
python extract_stored_procedures.py --server localhost --database MyDatabase --username sa --password YourPassword --no-windows-auth --output ./procedures
```

### Additional Options

- **Filter by Schema**: Extract only procedures from a specific schema
  ```bash
  python extract_stored_procedures.py --server localhost --database MyDatabase --schema dbo --output ./procedures
  ```

- **Save to Single File**: Combine all procedures into one file
  ```bash
  python extract_stored_procedures.py --server localhost --database MyDatabase --output ./procedures --single-file
  ```

## Output

By default, each stored procedure is saved to a separate `.sql` file in the format:
- `{schema}.{procedure_name}.sql`

Example: `dbo.usp_GetCustomerData.sql`

If using `--single-file`, all procedures are saved to `all_procedures.sql` with separators.

## Notes

- **Backup Restoration**: The script will restore the backup to a new database. If the database name already exists, it will be replaced (using `REPLACE` option).
- **File Paths**: For backup files on Windows, use forward slashes or escaped backslashes. The script handles path escaping automatically.
- **Permissions**: You need appropriate permissions to:
  - Connect to SQL Server
  - Restore databases (if using `--backup`)
  - Read from the target database

## Troubleshooting

### Connection Issues

If you see connection errors, try:
1. Verify SQL Server is running
2. Check the server name (use `localhost` for default instance, or `localhost\SQLEXPRESS` for Express)
3. Enable SQL Server authentication if needed
4. Check firewall settings

### Driver Not Found

If you get a driver error, the script will show available drivers. Install the appropriate ODBC driver from Microsoft if needed.

### Backup Restoration Fails

- Ensure the backup file path is accessible by SQL Server
- Check that you have sufficient disk space
- Verify you have `CREATE DATABASE` permissions
- The database name must be a valid SQL identifier




