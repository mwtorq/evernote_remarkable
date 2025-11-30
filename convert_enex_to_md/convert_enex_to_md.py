import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# Define the input and output directories
input_dir = r'c:\users\mwtorq\enex'
output_dir = r'c:\users\mwtorq\enexmd_update'
log_file = r'c:\users\mwtorq\enex_conversion_log.txt'
evernote2md_exe = r'c:\users\mwtorq\evernote2md.exe'

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)

# Function to log and print
def log_print(message):
    print(message)
    sys.stdout.flush()
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        f.flush()

# Counters for statistics
total_files = 0
converted_files = 0
error_files = 0

# Clear previous log and create initial entry
try:
    if os.path.exists(log_file):
        os.remove(log_file)
    # Create log file immediately
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Script started\n")
        f.write(f"Input dir exists: {os.path.exists(input_dir)}\n")
        f.write(f"Output dir: {output_dir}\n")
        f.write(f"Evernote2md exe exists: {os.path.exists(evernote2md_exe)}\n")
        f.flush()
except Exception as e:
    print(f"Warning: Could not create log file: {e}")

log_print(f"Starting conversion from {input_dir} to {output_dir}\n")
log_print(f"Input directory exists: {os.path.exists(input_dir)}")
log_print(f"Output directory: {output_dir}")
log_print(f"Evernote2md executable: {evernote2md_exe}")

# Verify evernote2md exists
if not os.path.exists(evernote2md_exe):
    log_print(f"ERROR: evernote2md.exe not found at {evernote2md_exe}")
    sys.exit(1)

# Walk through the directory tree
for root, dirs, files in os.walk(input_dir):
    # Filter for Evernote export files
    enex_files = [f for f in files if f.lower().endswith('.enex')]
    
    for enex_file in enex_files:
        total_files += 1
        # Construct the full path to the .enex file
        enex_path = os.path.join(root, enex_file)
        
        # Get the relative path from input_dir to preserve directory structure
        rel_path = os.path.relpath(root, input_dir)
        
        # Create corresponding output directory structure
        if rel_path == '.':
            output_subdir = output_dir
        else:
            output_subdir = os.path.join(output_dir, rel_path)
        
        # Ensure the output subdirectory exists
        os.makedirs(output_subdir, exist_ok=True)
        
        try:
            # Run evernote2md with --folders flag to include attachments
            # This puts each note in its own folder with attachments
            cmd = [
                evernote2md_exe,
                enex_path,
                output_subdir,
                '--folders'  # This ensures attachments are included in separate folders
            ]
            
            log_print(f"Processing [{total_files}]: {os.path.relpath(enex_path, input_dir)}")
            
            # Run the conversion
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout per file
            )
            
            if result.returncode == 0:
                converted_files += 1
                # Show progress - every file for first 100, then every 50 files
                if converted_files <= 100 or converted_files % 50 == 0:
                    log_print(f"✓ [{converted_files}] Converted: {os.path.relpath(enex_path, input_dir)}")
            else:
                error_files += 1
                error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
                log_print(f"✗ Error converting {enex_path}: {error_msg}")
                
        except subprocess.TimeoutExpired:
            error_files += 1
            log_print(f"✗ Timeout converting {enex_path} (exceeded 5 minutes)")
        except Exception as e:
            error_files += 1
            log_print(f"✗ Error converting {enex_path}: {str(e)}")

log_print(f"\n{'='*60}")
log_print(f"Conversion complete!")
log_print(f"Total .enex files found: {total_files}")
log_print(f"Successfully converted: {converted_files}")
log_print(f"Errors: {error_files}")
log_print(f"Markdown files saved to: {output_dir}")
log_print(f"{'='*60}")

