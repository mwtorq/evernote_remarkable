#!/usr/bin/python3
"""
Sync PDF files from enexpdf directory to reMarkable device using rmirro.
Maintains directory structure on reMarkable.
"""

import os
import shutil
import subprocess
import sys
import argparse
from pathlib import Path
import io

# Set up UTF-8 encoding for Windows console output - MUST be done before any other imports/operations
if sys.platform == 'win32':
    # Set environment variables for UTF-8
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    
    # Try to set console to UTF-8 mode
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        # Python < 3.7 or reconfigure not available, wrap stdout/stderr
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            # If that fails, at least set the encoding attribute
            if hasattr(sys.stdout, 'buffer'):
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            if hasattr(sys.stderr, 'buffer'):
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Configuration
SOURCE_DIR = r'C:\Users\mwtorq\enexpdf'
# Use rmirro.py from the same directory as this script (repo version)
RMIRRO_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rmirro.py')
SSH_NAME = 'root@192.168.1.226'  # SSH hostname of reMarkable (username@IP address)
# rmirro uses a directory named after the SSH name in the current working directory
# os.path.abspath() will create a directory with the SSH name as-is
# On Windows, this might create issues with '@' in the name, but rmirro handles it
RMIRRO_BASE_DIR = os.path.abspath(SSH_NAME)  # Base directory that rmirro uses for syncing
RMIRRO_DIR = os.path.join(RMIRRO_BASE_DIR, 'Import')  # Import subfolder (will be junctioned to SOURCE_DIR or subfolder)

def safe_remove_path(path):
    """Safely remove a path, handling junctions, symlinks, and regular directories on Windows.
    
    On Windows, junctions appear as directories but cannot be removed with shutil.rmtree().
    This function tries rmdir first (safe for junctions), then falls back to other methods.
    """
    path = Path(path)
    if not path.exists():
        return True
    
    try:
        if os.name == 'nt':
            # On Windows, try plain rmdir first (works for junctions without deleting their targets)
            # This is safe for junctions - it removes the junction link, not the target
            try:
                result = subprocess.run(
                    ['cmd', '/c', 'rmdir', '/Q', str(path)],
                    check=True,
                    capture_output=True,
                    text=True
                )
                return True
            except subprocess.CalledProcessError:
                # If plain rmdir fails, it's likely a regular directory with contents
                # Use rmdir /S to remove it recursively, or fall back to shutil
                try:
                    result = subprocess.run(
                        ['cmd', '/c', 'rmdir', '/Q', '/S', str(path)],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    return True
                except subprocess.CalledProcessError:
                    # If both fail, fall through to standard methods
                    pass
        
        # Fall back to standard methods for non-Windows or if rmdir failed
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            try:
                shutil.rmtree(path)
            except OSError as e:
                # If rmtree fails with "Cannot call rmtree on a symbolic link" error,
                # it's likely a junction on Windows - try rmdir again
                if os.name == 'nt' and "symbolic link" in str(e).lower():
                    try:
                        subprocess.run(
                            ['cmd', '/c', 'rmdir', '/Q', str(path)],
                            check=True,
                            capture_output=True,
                            text=True
                        )
                        return True
                    except subprocess.CalledProcessError:
                        pass
                raise  # Re-raise if it's a different error
        else:
            path.unlink()
        return True
    except Exception:
        # If all methods fail, return False (will be logged by caller)
        return False

def setup_rmirro_directory(subfolder=None):
    """Create a symlink/junction from rmirro Import subfolder to source directory or subfolder.
    
    Note: All links are created in the rmirro directory structure only.
    The source directory is never modified - it is only referenced as the target of junctions/symlinks.
    """
    # Create base directory if it doesn't exist
    rmirro_base_path = Path(RMIRRO_BASE_DIR)
    rmirro_base_path.mkdir(parents=True, exist_ok=True)
    
    rmirro_import_path = Path(RMIRRO_DIR)
    
    if subfolder:
        # When subfolder is specified, Import should be a directory (not a junction)
        # Create Import directory if it doesn't exist
        rmirro_import_path.mkdir(parents=True, exist_ok=True)
        # When subfolder is specified, create Import/subfolder_name/ structure
        actual_source = os.path.join(SOURCE_DIR, subfolder)
        source_path = Path(actual_source)
        
        if not source_path.exists():
            print(f"ERROR: Source subfolder {actual_source} does not exist")
            return False
        
        # Create the subfolder path under Import
        rmirro_subfolder_path = rmirro_import_path / subfolder
        
        print(f"Setting up rmirro to sync subfolder: {subfolder}")
        print(f"Files will be synced to Import\\{subfolder}\\ on reMarkable")
        
        # Remove existing symlink/junction/directory if it exists
        if rmirro_subfolder_path.exists() or rmirro_subfolder_path.is_symlink():
            if not safe_remove_path(rmirro_subfolder_path):
                print(f"Warning: Could not remove existing {rmirro_subfolder_path}")
                print("Attempting to continue...")
        
        # Create symlink/junction from Import/subfolder to source subfolder
        try:
            if os.name == 'nt':
                # On Windows, use mklink to create a junction (directory symlink)
                result = subprocess.run(
                    ['cmd', '/c', 'mklink', '/J', str(rmirro_subfolder_path), str(source_path)],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print(f"Created junction: Import\\{subfolder} -> {actual_source}")
            else:
                # On Unix-like systems, use os.symlink
                os.symlink(source_path, rmirro_subfolder_path)
                print(f"Created symlink: Import/{subfolder} -> {actual_source}")
            
            # Verify the link was created
            if rmirro_subfolder_path.exists():
                print(f"[OK] rmirro will sync files from {subfolder} to Import\\{subfolder}\\ on reMarkable")
                return True
            else:
                print(f"ERROR: Failed to create link to {actual_source}")
                return False
                
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create junction/symlink: {e}")
            if e.stdout:
                print(f"Output: {e.stdout}")
            if e.stderr:
                print(f"Error: {e.stderr}")
            return False
        except Exception as e:
            print(f"ERROR: Failed to create link: {e}")
            import traceback
            traceback.print_exc()
            return False
    else:
        # No subfolder specified - sync entire source directory
        # Import should always be a directory, so create junctions inside Import for each top-level item
        actual_source = SOURCE_DIR
        source_path = Path(actual_source)
        
        if not source_path.exists():
            print(f"ERROR: Source directory {SOURCE_DIR} does not exist")
            return False
        
        # Ensure Import is a directory (not a junction)
        rmirro_import_path.mkdir(parents=True, exist_ok=True)
        
        print(f"Setting up rmirro Import directory to use source: {SOURCE_DIR}")
        print(f"Files will be synced to Import\\ folder on reMarkable")
        
        # Clear existing junctions/items in Import directory
        for item in rmirro_import_path.iterdir():
            if not safe_remove_path(item):
                print(f"Warning: Could not remove existing {item}")
        
        # Create junctions inside Import for each top-level item in source directory
        try:
            created_count = 0
            for item in source_path.iterdir():
                if item.is_dir() or item.is_file():
                    target_path = rmirro_import_path / item.name
                    
                    if os.name == 'nt':
                        # On Windows, use mklink to create a junction for directories or symlink for files
                        if item.is_dir():
                            result = subprocess.run(
                                ['cmd', '/c', 'mklink', '/J', str(target_path), str(item)],
                                check=True,
                                capture_output=True,
                                text=True
                            )
                        else:
                            result = subprocess.run(
                                ['cmd', '/c', 'mklink', str(target_path), str(item)],
                                check=True,
                                capture_output=True,
                                text=True
                            )
                    else:
                        # On Unix-like systems, use os.symlink
                        os.symlink(item, target_path)
                    
                    created_count += 1
            
            print(f"Created {created_count} junction(s)/symlink(s) in Import\\ directory")
            print(f"[OK] rmirro will sync all files from {SOURCE_DIR} to Import\\ on reMarkable")
            return True
                
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create junction/symlink: {e}")
            if e.stdout:
                print(f"Output: {e.stdout}")
            if e.stderr:
                print(f"Error: {e.stderr}")
            return False
        except Exception as e:
            print(f"ERROR: Failed to create links: {e}")
            import traceback
            traceback.print_exc()
            return False

def check_required_tools():
    """Check if required tools (rsync, ssh) are available."""
    tools = ['rsync', 'ssh', 'scp']
    missing = []
    
    for tool in tools:
        try:
            result = subprocess.run(
                ['where', tool] if os.name == 'nt' else ['which', tool],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                missing.append(tool)
        except:
            missing.append(tool)
    
    if missing:
        print(f"\nWARNING: Required tools not found: {', '.join(missing)}")
        print("rmirro requires rsync, ssh, and scp to work.")
        print("\nOptions to install these tools on Windows:")
        print("1. Install Git for Windows (includes ssh, scp, and optionally rsync)")
        print("2. Install WSL (Windows Subsystem for Linux) and use rmirro from WSL")
        print("3. Install rsync for Windows from: https://github.com/JoeNyland/rsync-for-windows")
        print("4. Use Cygwin")
        return False
    return True

def check_wsl():
    """Check if WSL is available."""
    try:
        result = subprocess.run(['wsl', '--list', '--quiet'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except:
        return False

def filter_duplicate_messages(output):
    """Filter out verbose individual duplicate skip messages from rmirro output.
    
    Keeps status updates (every 10 duplicates) but filters verbose individual messages.
    """
    if not output:
        return output
    
    lines = output.split('\n')
    filtered_lines = []
    for line in lines:
        # Filter only verbose individual duplicate messages (format: "  [X] Skipped duplicate: ...")
        # Keep status updates (format: "  Skipped X duplicate(s)...")
        # Keep summary messages (format: "...X duplicate(s) skipped")
        if '] Skipped duplicate:' in line:
            continue
        filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)

def safe_print(text, end='\n', flush=True):
    """Safely print text, handling Unicode encoding errors."""
    if text is None:
        text = ''
    
    # Convert to string if needed
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            text = repr(text)
    
    try:
        print(text, end=end, flush=flush)
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # If encoding fails, replace problematic characters
        try:
            # Try to encode/decode with UTF-8 and replace errors
            if isinstance(text, bytes):
                safe_text = text.decode('utf-8', errors='replace')
            else:
                safe_text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            print(safe_text, end=end, flush=flush)
        except Exception:
            # Last resort: print as ASCII with replacements
            try:
                if isinstance(text, bytes):
                    safe_text = text.decode('ascii', errors='replace')
                else:
                    safe_text = text.encode('ascii', errors='replace').decode('ascii', errors='replace')
                print(safe_text, end=end, flush=flush)
            except Exception:
                # Absolute last resort: print repr
                print(repr(text), end=end, flush=flush)

def process_output_lines(process):
    """Process output lines from a subprocess, filtering verbose duplicate messages but keeping status updates.
    
    Filters individual verbose "Skipped duplicate:" messages but allows status updates (every 10) to pass through.
    Returns the process return code.
    """
    import time
    
    # Use a more robust method to read output in real-time
    # This handles both Windows and Unix-like systems
    while True:
        # Check if process has finished
        if process.poll() is not None:
            # Process finished, read any remaining output
            try:
                remaining = process.stdout.read()
                if remaining:
                    for line in remaining.splitlines(True):
                        if '] Skipped duplicate:' not in line:
                            safe_print(line, end='')
            except Exception as e:
                # If reading fails, just continue
                pass
            break
        
        # Read available output
        try:
            line = process.stdout.readline()
            if line:
                # Filter only verbose individual duplicate messages (format: "  [X] Skipped duplicate: ...")
                # Keep status updates from rmirro.py (format: "  Skipped X duplicate(s)...")
                # Keep summary messages (format: "...X duplicate(s) skipped")
                if '] Skipped duplicate:' not in line:
                    # Print all other lines normally (including status updates every 10)
                    safe_print(line, end='')
        except Exception as e:
            # If reading fails, continue
            pass
        
        # No output available yet, small sleep to avoid busy-waiting
        time.sleep(0.01)
    
    return process.returncode

def run_rmirro(no_pull=False, verbose=False):
    """Run rmirro to sync files to reMarkable device."""
    if no_pull:
        print(f"\nRunning rmirro to sync to reMarkable device ({SSH_NAME}) - PULL disabled (push only)...")
    else:
        print(f"\nRunning rmirro to sync to reMarkable device ({SSH_NAME})...")
    
    if not os.path.exists(RMIRRO_SCRIPT):
        print(f"ERROR: rmirro.py not found at {RMIRRO_SCRIPT}")
        return False
    
    # Check for WSL first (recommended for Windows)
    use_wsl = check_wsl()
    if use_wsl:
        print("WSL detected - using WSL to run rmirro (recommended for Windows)")
        try:
            # Convert Windows paths to WSL paths
            wsl_script_path = subprocess.run(
                ['wsl', 'wslpath', '-a', os.path.abspath(RMIRRO_SCRIPT)],
                capture_output=True, text=True
            ).stdout.strip()
            wsl_rmirro_dir = subprocess.run(
                ['wsl', 'wslpath', '-a', os.path.abspath(RMIRRO_BASE_DIR)],
                capture_output=True, text=True
            ).stdout.strip()
            
            # Run rmirro through WSL
            flags = []
            if no_pull:
                flags.append("--no-pull")
                print(f"  Passing --no-pull flag to rmirro")
            if verbose:
                flags.append("--verbose")
                print(f"  Passing --verbose flag to rmirro")
            flags_str = " " + " ".join(flags) if flags else ""
            # Use -u flag for unbuffered Python output to ensure real-time display
            # Set UTF-8 encoding environment variables
            wsl_cmd = f"export PYTHONIOENCODING=utf-8 && export PYTHONUTF8=1 && cd {os.path.dirname(wsl_script_path)} && python3 -u {wsl_script_path} {SSH_NAME}{flags_str}"
            print(f"  WSL command: {wsl_cmd}")
            # Stream output in real-time while filtering duplicate messages
            # bufsize=1 enables line buffering for real-time output
            # Set encoding to UTF-8 with error handling
            # Pass UTF-8 environment variables to subprocess
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            process = subprocess.Popen(
                ['wsl', 'bash', '-c', wsl_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                universal_newlines=True,
                bufsize=1,
                env=env
            )
            # Process output line by line in real-time with duplicate filtering
            returncode = process_output_lines(process)
            return returncode == 0
        except Exception as e:
            print(f"WARNING: Failed to run through WSL: {e}")
            print("Falling back to native Windows execution...")
            use_wsl = False
    
    if not use_wsl:
        # Check for required tools
        if not check_required_tools():
            print("\nWARNING: Windows rsync has limitations and may not work properly.")
            print("Consider using WSL for better compatibility.")
            print("\nAttempting to run anyway...")
        
        # Run rmirro natively on Windows
        try:
            # Use absolute paths
            rmirro_script_path = os.path.abspath(RMIRRO_SCRIPT)
            python_exe = sys.executable
            
            if not os.path.exists(rmirro_script_path):
                print(f"ERROR: rmirro.py not found at {rmirro_script_path}")
                return False
            
            # Use -u flag for unbuffered Python output to ensure real-time display
            rmirro_args = [python_exe, '-u', rmirro_script_path, SSH_NAME]
            if no_pull:
                rmirro_args.append('--no-pull')
                print(f"  Passing --no-pull flag to rmirro")
            if verbose:
                rmirro_args.append('--verbose')
                print(f"  Passing --verbose flag to rmirro")
            print(f"Running: {' '.join(rmirro_args)}")
            # Stream output in real-time while filtering duplicate messages
            # bufsize=1 enables line buffering for real-time output
            # Set encoding to UTF-8 with error handling
            # Pass UTF-8 environment variables to subprocess
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            process = subprocess.Popen(
                rmirro_args,
                cwd=os.path.dirname(rmirro_script_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                universal_newlines=True,
                bufsize=1,
                env=env
            )
            # Process output line by line in real-time with duplicate filtering
            returncode = process_output_lines(process)
            return returncode == 0
        except FileNotFoundError as e:
            print(f"ERROR: File not found - {e}")
            print(f"Python executable: {sys.executable}")
            print(f"Rmirro script: {RMIRRO_SCRIPT}")
            return False
        except Exception as e:
            print(f"ERROR: Failed to run rmirro: {e}")
            import traceback
            traceback.print_exc()
            return False

def main():
    """Main function to sync PDFs to reMarkable."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Sync PDF files from enexpdf directory to reMarkable device using rmirro.'
    )
    parser.add_argument(
        '--subfolder',
        type=str,
        default=None,
        help='Only sync files from this subfolder (e.g., "2012 Charter")'
    )
    parser.add_argument(
        '--no-pull',
        action='store_true',
        help='Skip pulling files from reMarkable to PC (push only)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    args = parser.parse_args()
    
    print("=" * 60)
    print("Syncing PDFs to reMarkable")
    if args.subfolder:
        print(f"Subfolder filter: {args.subfolder}")
    if args.no_pull:
        print("Mode: Push only (no pull from reMarkable)")
    print("=" * 60)
    
    # Step 1: Set up symlink/junction from rmirro directory to source directory (or subfolder)
    if not setup_rmirro_directory(subfolder=args.subfolder):
        print("Failed to set up rmirro directory")
        sys.exit(1)
    
    # Step 2: Run rmirro to sync to device (will use files directly from source)
    if not run_rmirro(no_pull=args.no_pull, verbose=args.verbose):
        print("Failed to sync to reMarkable device")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("Sync complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()

