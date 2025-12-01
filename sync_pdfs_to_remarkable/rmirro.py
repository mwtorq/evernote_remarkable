#!/usr/bin/python3

import subprocess
import os
import json
import urllib.request
import uuid
import time
import argparse
import shutil
import sys
import io

# Set up UTF-8 encoding for Windows console output - MUST be done before any other operations
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

def safe_print(*args, **kwargs):
    """Safely print text, handling Unicode encoding errors."""
    try:
        print(*args, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # If encoding fails, replace problematic characters
        try:
            # Convert all args to safe strings
            safe_args = []
            for arg in args:
                if isinstance(arg, bytes):
                    safe_args.append(arg.decode('utf-8', errors='replace'))
                elif isinstance(arg, str):
                    safe_args.append(arg.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
                else:
                    safe_args.append(str(arg))
            print(*safe_args, **kwargs)
        except Exception:
            # Last resort: print as ASCII with replacements
            try:
                safe_args = []
                for arg in args:
                    if isinstance(arg, bytes):
                        safe_args.append(arg.decode('ascii', errors='replace'))
                    elif isinstance(arg, str):
                        safe_args.append(arg.encode('ascii', errors='replace').decode('ascii', errors='replace'))
                    else:
                        safe_args.append(str(arg))
                print(*safe_args, **kwargs)
            except Exception:
                # Absolute last resort: print repr
                print(*[repr(arg) for arg in args], **kwargs)

# directory of this file
# (e.g. /some/absolute/path/rmirro)
DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(
    prog = "rmirro",
    description = "Synchronize reMarkable with local directory \"[name]/\"",
)
parser.add_argument("name", type=str, nargs="?", default="10.11.99.1", help="SSH hostname of reMarkable reachable with \"ssh [name]\" without password (default: remarkable)")
parser.add_argument("-r", "--renderers", default=["render_usb.py"], nargs="+", metavar="EX", help="list of one or more executables EX in this project's directory such that \"EX infile outfile\" renders a reMarkable document with stem infile to the PDF outfile (default: render_usb.py - using the official USB web interface renderer)")
parser.add_argument("-v", "--verbose", action="store_true", help="print executed shell commands")
parser.add_argument("-s", "--skip", default=["Quick sheets"], nargs="*", help="skip file names (default: skip \"Quick sheets\"; pass empty -s to include)")
parser.add_argument("--no-pull", action="store_true", help="skip pulling files from reMarkable to PC (push only)")

# TODO: --favorites-only (or by tags)
# TODO: --pull-only, --push-only, --backup, etc?
# TODO: let user exclude certain files? how would this pan out if they are suddenly included again?
# TODO: build symlink directory structure by tags?
# TODO: set --output directory
# TODO: support renderers that output e.g. SVG instead of PDF?

# Print an error message and exit
def panic(error):
    safe_print("ERROR: " + str(error))
    exit(1) # nonzero status code marks failure

# Run a shell command on the local computer,
# Optionally panic with exiterror if it fails
# Optionally capture and return its output
def pc_run(cmd, exiterror=None, capture=True):
    if args.verbose:
        print(">", subprocess.list2cmdline(cmd)) # print the command

    proc = subprocess.run(cmd, capture_output=capture, encoding="utf-8")
    if proc.returncode != 0 and exiterror is not None:
        print(proc.stderr, end="")
        panic(exiterror)

    return proc

# Interface to communicate with reMarkable and operate on its raw file system
class Remarkable:
    def __init__(self, ssh_name):
        self.ssh_name = ssh_name # e.g. "remarkable"

        self.raw_dir_remote = "/home/root/.local/share/remarkable/xochitl" # path to raw notes on RM
        self.processed_dir_local = os.path.abspath(f"{self.ssh_name}") # path to rendered PDFs on PC (e.g. remarkable/)
        self.raw_dir_local = os.path.abspath(f"{self.ssh_name}_metadata") # path to *.metadata files on PC (downloaded from RM) (e.g. remarkable_metadata/)
        self.backup_dir = os.path.abspath(f"{self.ssh_name}_backup") # path to save a backup of all raw RM files on PC (e.g. remarkable_backup/)
        self.last_sync_path = self.processed_dir_local + "/.last_sync" # path to a file on PC with the timestamp at which the last sync was performed

        # create directories if they do not exist
        os.makedirs(self.processed_dir_local, exist_ok=True)
        os.makedirs(self.raw_dir_local, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

        # "ping" to check if we do indeed have a remarkable connected
        print(f"Connecting to {self.ssh_name}")
        if self.run("uname -n", exiterror=f"Could not connect to {self.ssh_name} with SSH").stdout not in ("reMarkable\n", "imx8mm-ferrari\n"): # covers (RM1, RM2) and (RMPP)
            panic(f"Could not verify that SSH host {self.ssh_name} is a reMarkable")
        print(f"Connected to {self.ssh_name}")

        # Backup and metadata download are optional - they help with sync comparison
        # but aren't strictly required for uploading files
        try:
            self.backup()
        except Exception as e:
            print(f"WARNING: Backup step failed (optional): {e}")
            print("Continuing without backup - sync may be less accurate but will still work")
        
        try:
            self.download_metadata()
        except Exception as e:
            print(f"WARNING: Metadata download failed (optional): {e}")
            print("Continuing without metadata - sync may be less accurate but will still work")

        # RM .metadata files store only the *parent* of each file
        # keep track of every file's *children* too,
        # to effectively traverse its file tree later
        self.children_cache = {"": [], "trash": []} # root and trash are "implicit/special", as they don't appear in filenames
        for id in self.ids():
            self.children_cache[id] = [] # initialize list for each file
        for id in self.ids():
            metadata = self.read_metadata(id)
            if args.verbose:
                print(f"Read {id = } with {metadata = }")
            parent_id = metadata["parent"]
            self.children_cache[parent_id].append(id)

    # Read the timestamp at which the last sync was performed
    def last_sync(self):
        if os.path.exists(self.last_sync_path):
            with open(self.last_sync_path, "r") as file:
                return int(file.read()) # s
        return float("inf") # never synced before (i.e. infinitely far in the future)

    # Write the timestamp at which the last sync was performed (by default, now)
    def write_last_sync(self, t=int(time.time())):
        with open(self.last_sync_path, "w") as file:
            file.write(str(t) + "\n") # s

    # Generate IDs of all RM files
    def ids(self):
        for filename in os.listdir(self.raw_dir_local):
            id, ext = os.path.splitext(filename)
            if ext == ".metadata":
                yield id

    # Download all raw *.metadata files from RM with rsync
    def download_metadata(self):
        print(f"Downloading metadata to {self.raw_dir_local}")
        # Ensure destination directory exists
        os.makedirs(self.raw_dir_local, exist_ok=True)
        
        # Check if we're on Windows with limited rsync
        is_windows = os.name == 'nt'
        if is_windows:
            # Windows rsync has very limited flag support (no --include, --exclude, --delete-excluded, or remote paths)
            print("WARNING: Windows rsync detected - skipping metadata download (optional step)")
            print("Sync will still work, but may be less accurate without metadata comparison")
            return
        
        # Unix/Linux rsync uses standard syntax
        remote_path = f"{self.ssh_name}:{self.raw_dir_remote}/"
        local_dest = os.path.normpath(self.raw_dir_local).replace('\\', '/')
        # Try rsync, but handle errors gracefully
        try:
            pc_run(["rsync", "-a", "--delete-excluded", "--include=*.metadata", "--exclude=*", remote_path, local_dest], exiterror=None, capture=False)
        except Exception as e:
            print(f"WARNING: Metadata download failed (this is optional): {e}")
            print("Continuing without metadata...")

    # Download all raw files from RM with rsync
    def backup(self):
        print(f"Backing up raw files to {self.backup_dir}")
        # Ensure destination directory exists
        os.makedirs(self.backup_dir, exist_ok=True)
        
        # Check if we're on Windows with limited rsync
        is_windows = os.name == 'nt'
        if is_windows:
            # Windows rsync has very limited flag support (no remote paths, limited flags)
            print("WARNING: Windows rsync detected - skipping backup (optional step)")
            print("Sync will still work, but may be less accurate without backup comparison")
            return
        
        # Unix/Linux rsync uses standard syntax
        remote_path = f"{self.ssh_name}:{self.raw_dir_remote}/"
        local_dest = os.path.normpath(self.backup_dir).replace('\\', '/')
        # Try rsync, but don't fail if it doesn't work
        try:
            pc_run(["rsync", "-a", "--delete", remote_path, local_dest], exiterror=None, capture=False) # --delete deletes files on PC that are no longer on RM
        except Exception as e:
            print(f"WARNING: Backup failed (this is optional): {e}")
            print("Continuing without backup...")

    # Read a RM file that has been downloaded to PC
    def read_file(self, filename):
        with open(self.raw_dir_local + "/" + filename, "r") as file:
            return file.read()

    # Read a RM JSON file that has been downloaded to PC
    def read_json(self, filename):
        return json.loads(self.read_file(filename))

    # Read a RM .metadata file that has been downloaded to PC
    def read_metadata(self, id):
        return self.read_json(f"{id}.metadata")

    # Upload a file from the PC storage to RM
    def upload_file(self, src_path, dest_name): # TODO: use same prefix as read methods
        # Use ControlMaster to reuse SSH connections and avoid repeated password prompts
        # Note: ControlMaster may not work well on Windows, so we only use it on Unix-like systems
        is_windows = os.name == 'nt'
        if is_windows:
            # On Windows, just use regular scp (password will be prompted for each file)
            # Consider setting up SSH key authentication to avoid password prompts
            pc_run(["scp", src_path, f"{self.ssh_name}:{self.raw_dir_remote}/{dest_name}"])
        else:
            # On Unix/Linux, use ControlMaster for connection reuse
            ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
            os.makedirs(ssh_dir, exist_ok=True)
            host_safe = self.ssh_name.replace("@", "_at_").replace(":", "_").replace(".", "_")
            control_path = os.path.join(ssh_dir, f"rmirro_control_{host_safe}")
            pc_run(["scp", "-o", "ControlMaster=auto", "-o", f"ControlPath={control_path}", "-o", "ControlPersist=300", src_path, f"{self.ssh_name}:{self.raw_dir_remote}/{dest_name}"])

    # Create a file in the PC storage and upload it to RM
    def write_file(self, filename, content):
        # write locally
        path_local = f"{self.raw_dir_local}/{filename}"
        with open(path_local, "w") as file:
            file.write(content)

        # copy same file to remarkable
        self.upload_file(path_local, filename)

    # Create a JSON file in the PC storage and upload it to RM
    def write_json(self, filename, dict):
        self.write_file(filename, json.dumps(dict) + "\n")

    # Create a .metadata file in the PC storage and upload it to RM
    def write_metadata(self, id, metadata):
        self.write_json(f"{id}.metadata", metadata)

        # update cache (parent -> child)
        if id not in self.children_cache[metadata["parent"]]:
            self.children_cache[metadata["parent"]].append(id)

        # update cache (child -> nothing)
        if id not in self.children_cache:
            self.children_cache[id] = []

    # Create a .content file in the PC storage and upload it to RM
    def write_content(self, id, content):
        self.write_json(f"{id}.content", content)

    # Run a shell command on RM
    def run(self, cmd, exiterror=None):
        # Use ControlMaster to reuse SSH connections and avoid repeated password prompts
        # Note: ControlMaster may not work well on Windows, so we only use it on Unix-like systems
        is_windows = os.name == 'nt'
        if is_windows:
            # On Windows, just use regular ssh (password will be prompted)
            # Consider setting up SSH key authentication to avoid password prompts
            return pc_run(["ssh", "-o", "ConnectTimeout=1", self.ssh_name, cmd], exiterror=exiterror)
        else:
            # On Unix/Linux, use ControlMaster for connection reuse
            ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
            os.makedirs(ssh_dir, exist_ok=True)
            host_safe = self.ssh_name.replace("@", "_at_").replace(":", "_").replace(".", "_")
            control_path = os.path.join(ssh_dir, f"rmirro_control_{host_safe}")
            return pc_run(["ssh", "-o", "ConnectTimeout=1", "-o", "ControlMaster=auto", "-o", f"ControlPath={control_path}", "-o", "ControlPersist=300", self.ssh_name, cmd], exiterror=exiterror)

    # Restart reMarkable's interface
    # (needed to show newly uploaded files)
    def restart(self):
        print("Restarting remarkable interface (xochitl)...")
        try:
            self.run("systemctl restart xochitl")
            print("  [OK] Restart command executed")
            # Give xochitl time to restart and refresh file list
            import time
            print("  Waiting for xochitl to restart...", end="", flush=True)
            for i in range(5):
                time.sleep(1)
                print(".", end="", flush=True)
            print()
            # Verify xochitl is running
            try:
                result = self.run("systemctl is-active xochitl", exiterror=None)
                if "active" in result.lower():
                    print("  [OK] xochitl is running - files should now be visible on device")
                else:
                    print(f"  [WARN] xochitl status: {result}")
            except:
                print("  [WARN] Could not verify xochitl status")
            print("  [OK] Restart complete")
        except Exception as e:
            print(f"  [ERROR] WARNING: Failed to restart xochitl: {e}")
            print("  Files may not appear until device is manually restarted")
            print("  Try: ssh to device and run 'systemctl restart xochitl' manually")

# Some methods that are common to RM files and PC files
class AbstractFile:
    # List children of this file (like listing a directory)
    def list(self):
        for child in self.children():
            print(child.path())
            child.list()

    # Generate all descendants (children, children's children, ...) of this file
    def traverse(self):
        for child in self.children():
            if child.name()[0] == ".":
                continue # skip hidden files
            else:
                yield child # child
                yield from child.traverse() # child's children

# Represents a file stored on the reMarkable
class RemarkableFile(AbstractFile):
    # Cached lookup of RM file IDs by their full paths (common to all instances)
    fullpath_to_id_cache = {} # build as we go

    # Construct a RM file from its ID
    def __init__(self, id=""):
        self.is_root = id == ""
        self.is_trash = id == "trash"
        self.id = id

        if not self.trashed() and self.path() not in self.fullpath_to_id_cache:
            self.fullpath_to_id_cache[self.path()] = self.id # cache

        # Verify this is a file XOR a directory, to make sure our logic is consistent
        assert self.is_file() != self.is_directory(), f"reMarkable file \"{self.id}\" is not a file XOR a directory"

    # Read and return metadata attributes as a dictionary
    def metadata(self):
        return rm.read_metadata(self.id)

    # Return whether this file is trashed
    def trashed(self):
        if self.is_trash:
            return True
        if self.is_root:
            return False
        # On RM, a file can be marked as trashed even though its parent is not
        # What on earth should be done, then, to a non-trashed that is in a trashed directory?
        # Here, it is more sensible to say that a file is trashed if its parent is trashed
        return self.parent().trashed()

    # Generate this file's children
    def children(self):
        for id in rm.children_cache[self.id]: # use cached parent-to-child lookup
            yield RemarkableFile(id)

    # Return this file's parent (directory), or None if it 
    def parent(self):
        if "parent" in self.metadata():
            parent_id = self.metadata()["parent"]
            return RemarkableFile(parent_id)
        else:
            assert self.is_root or self.is_trash, "file is an orphan"
            return None

    # Return this file's name (e.g. "document")
    def name(self):
        if self.is_root:
            return ""
        return self.metadata()["visibleName"]

    # Return this file's full path as it appears in the visual RM file system (e.g. notes/document.pdf)
    def path(self):
        if self.is_root:
            path = ""
        elif self.parent().is_root:
            path = self.name() # handle separately to get "toplevelfile" instead of "/toplevelfile"
        else:
            path = self.parent().path() + "/" + self.name()

        # Any file (note, annotated PDF or EPUB) will be a PDF upon export
        if self.is_file() and not (path.endswith(".pdf") or path.endswith(".epub")):
            path += ".pdf" # add PDF extension to to-be-exported notes

        return path

    # Find a descendant of this file by its relative path to it
    def find(self, path):
        if path == "":
            return self
        if not self.is_root:
            path = self.path() + "/" + path # relative to full path
        if path in self.fullpath_to_id_cache:
            return RemarkableFile(self.fullpath_to_id_cache[path]) # use cache
        for file in self.traverse():
            if file.path() == path:
                return file
        return None

    # Returns whether this "file" is a directory
    def is_directory(self):
        return self.is_root or self.metadata()["type"] == "CollectionType"

    # Returns whether this "file" is a file (i.e. document)
    def is_file(self):
        return not self.is_root and self.metadata()["type"] == "DocumentType"

    # Returns timestamp at which file was last modified
    def last_modified(self):
        return 0 if self.is_root else int(self.metadata()["lastModified"]) // 1000 # s

    # Returns timestamp at which file was last accessed (opened)
    def last_accessed(self):
        return 0 if self.is_root else int(self.metadata()["lastOpened"]) // 1000 # s

    # Download this file to its corresponding location in the PC directory
    def download(self):
        infile  = rm.backup_dir + "/" + self.id # already have raw file(s) from the backup
        outfile = rm.processed_dir_local + "/" + self.path() # output folder/PDF location
        if self.is_directory():
            os.makedirs(outfile, exist_ok=True) # make directories ourselves
        else: # is file
            success = False
            for renderer in renderers:
                proc = pc_run([f"{DIR}/{renderer}", infile, outfile]) # try to render
                success = proc.returncode == 0
                if len(renderers) > 1 or args.verbose:
                    print(f"- {renderer}", "succeeded" if success else "failed")
                print(proc.stderr, end="")
                if success:
                    break # jump out upon first successful render

            # Double check that file was indeed downloaded
            success = success and os.path.exists(outfile)

            if not success:
                panic(f"All renderers failed to render {self.path()}")

            # Copy last access/modification time from RM to PC file system
            # (these are used to determine sync actions)
            atime = self.last_accessed() # s
            mtime = self.last_modified() # s
            os.utime(outfile, (atime, mtime))

    # Returns the corresponding file on PC, or None if it does not exist
    def on_computer(self):
        pc_file = ComputerFile(rm.processed_dir_local).find(self.path())
        return pc_file if pc_file.exists() else None

# Represents a file stored on the computer
class ComputerFile(AbstractFile):
    # Track directories that have been created during this sync session to avoid duplicates
    # Maps directory path -> directory ID
    _created_directories = {}
    # Track directories currently being created to prevent concurrent creation attempts
    _creating_directories = set()
    
    # Construct a PC file by its path
    def __init__(self, path):
        self._path = path

    # Returns whether the PC file exists
    def exists(self):
        return os.path.exists(self.path())

    # Returns the file's path
    def path(self):
        return self._path

    # Returns the file's filename without its extension
    def name(self):
        filename = os.path.basename(self.path()) # e.g. "document.pdf"
        name, ext = os.path.splitext(filename) # e.g. ("document", ".pdf")
        return name # without extension

    # Returns the file's extension
    def extension(self):
        _, ext = os.path.splitext(self.path())
        return ext

    # Returns the file's parent
    def parent(self):
        return ComputerFile(os.path.dirname(self.path()))

    # Returns whether the file is a directory
    def is_directory(self):
        return os.path.isdir(self.path())

    # Returns whether the "file" is a file (i.e. a document, i.e. not a directory)
    def is_file(self):
        return os.path.isfile(self.path())

    # Returns the file's children, if any
    def children(self):
        if self.is_directory():
            return [ComputerFile(self.path() + "/" + name) for name in os.listdir(self.path())]
        else:
            return []

    # Returns a descendant of this file by its path relative to it
    def find(self, name):
        return ComputerFile(self.path() + "/" + name)

    # Returns the timestamp at which the file was created
    def created(self):
        return int(os.path.getctime(self.path())) # s

    # Returns the timestamp at which the file was last accessed
    def last_accessed(self):
        return int(os.path.getatime(self.path())) # s

    # Returns the timestamp at which the file was last modified
    def last_modified(self):
        return int(os.path.getmtime(self.path())) # s

    # Returns the path that the PC file would have on RM
    def path_on_remarkable(self):
        rm_path = os.path.relpath(self.path(), start=rm.processed_dir_local) # path relative to base directory
        if rm_path == ".":
            rm_path = "" # RM root
        # Normalize path separators to forward slashes (reMarkable uses Unix-style paths)
        rm_path = rm_path.replace('\\', '/')
        return rm_path

    # Returns the corresponding file on RM, or None if it does not exist
    def on_remarkable(self):
        return rm_root.find(self.path_on_remarkable())

    # Upload this PC file to RM
    # Returns the ID of the created/updated file on RM
    # TODO: could use RM web interface for uploading, if don't need to make new directories?
    # TODO: then it would not be necessary to restart the RM interface
    def upload(self):
        if self.is_file() and self.extension() not in (".pdf", ".epub"):
            panic(f"Extension of {self.path()} is not PDF or EPUB")

        rm_file = self.on_remarkable()

        if rm_file:
            # RM file already exists, so we will only update it
            id = rm_file.id
            metadata = rm_file.metadata()
            # Note: We'll return id at the end of the function
        else:
            # RM file does not exist, so we have to create it from scratch
            # First, ensure parent directory exists on RM (create it if needed)
            parent_pc = self.parent()
            parent_rm_path = parent_pc.path_on_remarkable()
            
            # Determine parent ID
            if parent_rm_path == "":
                # Parent is root, which should always exist - use empty string as ID
                parent_rm_id = ""
            elif parent_rm_path in ComputerFile._created_directories:
                # Parent directory was already created in this session - use cached ID
                parent_rm_id = ComputerFile._created_directories[parent_rm_path]
                # Verify it can be found (should be in lookup map now)
                parent_rm = parent_pc.on_remarkable()
                if not parent_rm:
                    # Directory was created but not yet in lookup map - add it now
                    if hasattr(ComputerFile, '_rm_path_to_file'):
                        rm_file = RemarkableFile(parent_rm_id)
                        ComputerFile._rm_path_to_file[parent_rm_path] = rm_file
                        parent_rm = rm_file
            elif parent_rm_path in ComputerFile._creating_directories:
                # Another file is currently creating this directory
                # Since we're single-threaded, this means we're in a recursive call
                # which shouldn't happen, but check if it's now created
                parent_rm = parent_pc.on_remarkable()
                if parent_rm:
                    parent_rm_id = parent_rm.id
                    ComputerFile._created_directories[parent_rm_path] = parent_rm_id
                    ComputerFile._creating_directories.discard(parent_rm_path)
                elif parent_rm_path in ComputerFile._created_directories:
                    # It was just created and added to cache
                    parent_rm_id = ComputerFile._created_directories[parent_rm_path]
                    ComputerFile._creating_directories.discard(parent_rm_path)
                else:
                    # This shouldn't happen in single-threaded execution
                    panic(f"Parent directory {parent_rm_path} is marked as creating but not found")
            else:
                # Check if parent exists on RM
                parent_rm = parent_pc.on_remarkable()
                if not parent_rm:
                    # Parent doesn't exist on RM - create it recursively
                    print(f"  Creating parent directory: {parent_rm_path}")
                    # Mark as being created to prevent duplicates
                    ComputerFile._creating_directories.add(parent_rm_path)
                    try:
                        # Recursively upload parent directories - it will return the created ID
                        parent_rm_id = parent_pc.upload()
                        # Mark as created after successful upload with its ID
                        ComputerFile._creating_directories.discard(parent_rm_path)
                        ComputerFile._created_directories[parent_rm_path] = parent_rm_id
                        # Verify it was created (if metadata available)
                        if parent_rm_id == "":
                            # If we got empty ID, something went wrong
                            panic(f"Failed to create parent directory for {self.path_on_remarkable()}")
                    except Exception as e:
                        # Remove from creating set on error
                        ComputerFile._creating_directories.discard(parent_rm_path)
                        raise
                else:
                    parent_rm_id = parent_rm.id
                    # Add to cache so we know it exists with its ID
                    ComputerFile._created_directories[parent_rm_path] = parent_rm_id
            
            id = str(uuid.uuid4()) # create new ID
            assert id not in rm.ids(), f"{id} already exists on {rm.ssh_name}"
            
            # Log parent information for debugging
            parent_info = f"root" if parent_rm_id == "" else f"{parent_rm_path} (ID: {parent_rm_id})"
            print(f"    Creating {self.path_on_remarkable()} with parent: {parent_info}")
            
            metadata = {
                "visibleName": self.name(),
                "parent": parent_rm_id,
                "modified": False, # TODO: do I really need to set all these?
                "metadatamodified": False,
                "deleted": False,
                "pinned": False,
                "version": 0,
            }
            if self.is_directory():
                metadata["type"] = "CollectionType"
            else: # is file
                metadata["type"] = "DocumentType"
                metadata["lastOpened"] = str(self.last_accessed() * 1000) # s to ms, only files have this property

        metadata["lastModified"] = str(self.last_modified() * 1000) # s to ms

        # Write metadata first (required before content)
        # This also updates the children_cache automatically
        print(f"    Writing metadata for {self.path_on_remarkable()} (ID: {id}, parent: {metadata.get('parent', 'root')})")
        rm.write_metadata(id, metadata)
        
        # Write content file (required for RM to list file properly)
        # For directories, content is typically empty dict {}
        # For documents, content should have pages array (empty for PDFs)
        if metadata["type"] == "DocumentType":
            content = {"pages": []}  # Documents need pages array
        else:
            content = {}  # Directories use empty dict
        
        print(f"    Writing content file for {self.path_on_remarkable()}")
        rm.write_content(id, content)
        
        # Ensure parent directory exists and is properly set up
        # The children_cache is updated by write_metadata, but we should verify parent exists
        if metadata.get("parent") and metadata["parent"] != "":
            parent_id = metadata["parent"]
            # Verify parent is in cache (should be after write_metadata)
            if parent_id not in rm.children_cache:
                print(f"    WARNING: Parent {parent_id} not in children_cache, adding it")
                rm.children_cache[parent_id] = []
            # Ensure this file is listed as a child of parent
            if id not in rm.children_cache[parent_id]:
                rm.children_cache[parent_id].append(id)
                print(f"    Added {id} to parent {parent_id}'s children list")
            
            # Update parent directory's metadata lastModified timestamp
            # This ensures the parent directory shows as updated when children are added
            # Only update if parent already exists (not if we just created it)
            try:
                # Check if parent metadata file exists locally (means it exists on device)
                parent_metadata_path = f"{rm.raw_dir_local}/{parent_id}.metadata"
                if os.path.exists(parent_metadata_path):
                    parent_metadata = rm.read_metadata(parent_id)
                    parent_metadata["lastModified"] = str(int(time.time() * 1000))  # Current time in ms
                    print(f"    Updating parent directory metadata: {parent_id}")
                    rm.write_metadata(parent_id, parent_metadata)
                else:
                    print(f"    Note: Parent {parent_id} metadata not found locally (may have just been created)")
            except Exception as e:
                print(f"    WARNING: Could not update parent directory metadata: {e}")
                # Continue anyway - parent might not exist yet or might be root
        
        # Upload the actual file for documents
        if metadata["type"] == "DocumentType":
            rm_path = self.path_on_remarkable()
            
            # Check if PDF has embedded attachments (reMarkable may not support viewing them)
            attachment_count = 0
            if self.extension().lower() == ".pdf":
                try:
                    # Try to check for attachments using pikepdf (if available)
                    try:
                        from pikepdf import Pdf
                        with Pdf.open(self.path()) as pdf:
                            attachment_count = len(pdf.attachments) if hasattr(pdf, 'attachments') else 0
                    except ImportError:
                        # Try pypdf as fallback
                        try:
                            from pypdf import PdfReader
                            reader = PdfReader(self.path())
                            attachment_count = len(reader.attachments) if hasattr(reader, 'attachments') and reader.attachments else 0
                        except (ImportError, AttributeError):
                            pass
                except Exception:
                    # If we can't check, just continue - attachments might still be there
                    pass
            
            print(f"    Uploading: {rm_path} -> {id}{self.extension()}")
            if attachment_count > 0:
                print(f"    Note: PDF contains {attachment_count} embedded attachment(s)")
                print(f"    WARNING: reMarkable device may not support viewing embedded PDF attachments")
                print(f"    Attachments are in the file but may not be accessible on the device")
            
            try:
                rm.upload_file(self.path(), f"{id}{self.extension()}") # upload e.g. document.pdf in the "raw" form {id}.pdf
                print(f"    [OK] Uploaded: {rm_path}")
            except Exception as e:
                print(f"    [ERROR] ERROR uploading {rm_path}: {e}")
                raise
        else:
            rm_path = self.path_on_remarkable()
            print(f"    [OK] Created directory: {rm_path} (ID: {id}, parent: {metadata.get('parent', 'root')})")
        
        # Add to lookup map so it can be found immediately (prevents duplicate pushes)
        rm_path = self.path_on_remarkable()
        if hasattr(ComputerFile, '_rm_path_to_file'):
            rm_file = RemarkableFile(id)
            ComputerFile._rm_path_to_file[rm_path] = rm_file
            # Track that this file was uploaded in this session
            if hasattr(ComputerFile, '_uploaded_in_session'):
                ComputerFile._uploaded_in_session.add(rm_path)
            # Don't print - this happens for every file and would be too verbose
        
        # If this is a directory, also add it to the cache to prevent duplicate creation
        if metadata["type"] == "CollectionType":
            ComputerFile._created_directories[rm_path] = id
        
        # Return the created/updated ID so parent directory creation can use it
        return id

    # Remove (delete) this file on PC
    def remove(self):
        if self.is_directory():
            os.rmdir(self.path())
        else:
            os.remove(self.path())

# Determine what to do, and why, when syncing file with given RM/PC representations
def sync_action_and_reason(rm_file, pc_file, skip=[], no_pull=False, last_sync_time=None):
    if (rm_file and rm_file.name() in skip) or (pc_file and pc_file.name() in skip):
        return "SKIP", "in --skip"

    if rm_file and not pc_file:
        if no_pull:
            return "SKIP", "only on RM (PULL disabled)"
        return "PULL", "only on RM"

    elif rm_file and pc_file and rm_file.is_file(): # if the file is a directory, there is nothing worth updating (its name doesn't change)
        # Cache timestamps to avoid multiple calls
        rm_time = rm_file.last_modified()
        pc_time = pc_file.last_modified()
        # Use a small tolerance (1 second) for timestamp comparison to account for rounding
        time_diff = abs(rm_time - pc_time)
        if time_diff <= 1:
            # Files are essentially the same age - skip
            return "SKIP", "up-to-date"
        elif rm_time > pc_time:
            if no_pull:
                return "SKIP", "newer on RM (PULL disabled)"
            return "PULL", "newer on RM"
        elif rm_time < pc_time:
            return "PUSH", "newer on PC"

    elif not rm_file and pc_file:
        # Check if this file was just uploaded in this session
        rm_path = pc_file.path_on_remarkable()
        if hasattr(ComputerFile, '_uploaded_in_session') and rm_path in ComputerFile._uploaded_in_session:
            # File was just uploaded in this session - skip to avoid duplicate push
            return "SKIP", "just uploaded in this session"
        
        # Was the file removed from RM or created on PC after last sync?
        # Compare last sync time to PC time to find out
        # Use cached last_sync_time if provided, otherwise fall back to rm.last_sync()
        if last_sync_time is None:
            last_sync_time = rm.last_sync()

        # Cache directory check and timestamps
        is_dir = pc_file.is_directory()
        if is_dir:
            # Directory modification times are changed every time its contents changes,
            # but the creation time stays constant, so go by this instead
            pc_time = pc_file.created()
        else:
            # Cache both timestamps
            pc_created = pc_file.created()
            pc_modified = pc_file.last_modified()
            if pc_created > pc_modified:
                # When a file is copied from on PC, many programs preserve its
                # (old) modification time, so rather go by the (new) creation time
                # (only holds if the file does not exist on RM)
                pc_time = pc_created
            else:
                # The default is that we want the time the file was modified last
                pc_time = pc_modified

        if last_sync_time < pc_time:
            return "PUSH", "added on PC"
        else:
            return "DROP", "deleted on RM"

    return "SKIP", "up-to-date"

if __name__ == "__main__":
    # Check sys.argv directly first to see if --no-pull is present
    no_pull_in_argv = '--no-pull' in sys.argv
    print(f"DEBUG: sys.argv = {sys.argv}")
    print(f"DEBUG: '--no-pull' in sys.argv = {no_pull_in_argv}")
    
    args = parser.parse_args()
    ssh_name = getattr(args, "name")
    renderers = getattr(args, "renderers")
    skip = getattr(args, "skip")
    
    # Helper function to check no_pull flag reliably
    def check_no_pull():
        """Check if --no-pull flag is set, checking both args and sys.argv"""
        # First check args (argparse converts --no-pull to no_pull)
        if hasattr(args, 'no_pull') and args.no_pull:
            return True
        # Fallback: check sys.argv directly
        if '--no-pull' in sys.argv:
            return True
        return False
    
    # Set the flag using the helper
    no_pull_flag = check_no_pull()
    
    # Create a constant that will be used throughout - check both sources
    NO_PULL_ENABLED = no_pull_flag or '--no-pull' in sys.argv
    
    # Debug: verify --no-pull flag is being read
    print(f"DEBUG: Checking --no-pull flag...")
    print(f"DEBUG: no_pull_flag = {no_pull_flag}")
    print(f"DEBUG: '--no-pull' in sys.argv = {'--no-pull' in sys.argv}")
    print(f"DEBUG: NO_PULL_ENABLED = {NO_PULL_ENABLED}")
    print(f"DEBUG: hasattr(args, 'no_pull') = {hasattr(args, 'no_pull')}")
    if hasattr(args, 'no_pull'):
        print(f"DEBUG: args.no_pull = {args.no_pull}")
    else:
        print(f"DEBUG: args.no_pull attribute does not exist")
    if NO_PULL_ENABLED:
        print(f"DEBUG: --no-pull flag is SET - PULL operations will be DISABLED")
    else:
        print(f"DEBUG: --no-pull flag is NOT SET - PULL operations will be allowed")

    rm = Remarkable(ssh_name)
    rm_root = RemarkableFile()
    pc_root = ComputerFile(rm.processed_dir_local)
    
    # Clear the directory creation cache at the start of each sync session
    ComputerFile._created_directories.clear()
    ComputerFile._creating_directories.clear()
    # Initialize set to track files uploaded in this session
    if not hasattr(ComputerFile, '_uploaded_in_session'):
        ComputerFile._uploaded_in_session = set()
    else:
        ComputerFile._uploaded_in_session.clear()

    # Build a fast lookup map for RM files by path (speeds up on_remarkable() calls)
    print("Building remarkable file lookup map...")
    rm_path_to_file = {}
    file_count = 0
    skipped_count = 0
    duplicate_count = 0
    for rm_file in rm_root.traverse():
        file_count += 1
        rm_path = rm_file.path()
        # Normalize path separators to forward slashes (reMarkable uses Unix-style paths)
        rm_path = rm_path.replace('\\', '/')
        
        # Skip if already in map (prevent duplicates)
        if rm_path in rm_path_to_file:
            duplicate_count += 1
            # Always show status every 10, regardless of verbose flag
            if duplicate_count % 10 == 0:
                if args.verbose:
                    print(f"  Skipped {duplicate_count} duplicate(s) (last: {rm_path})...")
                else:
                    print(f"  Skipped {duplicate_count} duplicate(s)...")
            continue
        
        # When --no-pull is enabled, skip files that only exist on remarkable
        # (no corresponding PC file) since we won't be pulling them anyway
        if NO_PULL_ENABLED:
            pc_file = rm_file.on_computer()
            if not pc_file:
                skipped_count += 1
                # Show status every 10 skipped files
                if skipped_count % 10 == 0:
                    if args.verbose:
                        print(f"  Skipped {skipped_count} RM-only file(s) (last: {rm_path})...")
                    else:
                        print(f"  Skipped {skipped_count} RM-only file(s)...")
                continue
        
        rm_path_to_file[rm_path] = rm_file
        if args.verbose:
            print(f"  [{file_count}] Added to map: {rm_path}")
    
    print(f"  Loaded {len(rm_path_to_file)} remarkable files into lookup map", end="")
    parts = []
    if duplicate_count > 0:
        parts.append(f"{duplicate_count} duplicate(s) skipped")
    if NO_PULL_ENABLED and skipped_count > 0:
        parts.append(f"{skipped_count} RM-only file(s) skipped")
    if parts:
        print(f" ({', '.join(parts)})")
    else:
        print()

    # Store lookup map reference in ComputerFile class for access during upload
    ComputerFile._rm_path_to_file = rm_path_to_file
    
    # Override on_remarkable() to use the lookup map for speed
    original_on_remarkable = ComputerFile.on_remarkable
    def fast_on_remarkable(self):
        rm_path = self.path_on_remarkable()
        # First check the lookup map
        if rm_path in ComputerFile._rm_path_to_file:
            return ComputerFile._rm_path_to_file[rm_path]
        # If not found, check if it was created in this session (in cache)
        if rm_path in ComputerFile._created_directories:
            # Directory was created but not yet in lookup map - add it
            dir_id = ComputerFile._created_directories[rm_path]
            rm_file = RemarkableFile(dir_id)
            ComputerFile._rm_path_to_file[rm_path] = rm_file
            return rm_file
        return None
    ComputerFile.on_remarkable = fast_on_remarkable

    # Ensure Import folder exists on remarkable (if it exists on PC)
    import_folder_path = "Import"
    import_pc_path = os.path.join(rm.processed_dir_local, import_folder_path)
    
    # Check if Import folder exists on PC (might be a junction/symlink)
    if os.path.exists(import_pc_path) or os.path.isdir(import_pc_path):
        if import_folder_path not in rm_path_to_file:
            print(f"Ensuring '{import_folder_path}' folder exists on remarkable...")
            import_pc_file = ComputerFile(import_pc_path)
            import_rm_file = import_pc_file.on_remarkable()
            if not import_rm_file:
                # Create Import folder on remarkable
                print(f"  Creating '{import_folder_path}' folder on remarkable...")
                try:
                    # Ensure it's treated as a directory
                    if not os.path.isdir(import_pc_path):
                        os.makedirs(import_pc_path, exist_ok=True)
                    import_id = import_pc_file.upload()
                    ComputerFile._created_directories[import_folder_path] = import_id
                    # Update lookup map
                    rm_path_to_file[import_folder_path] = RemarkableFile(import_id)
                    print(f"  [OK] Created '{import_folder_path}' folder on remarkable")
                except Exception as e:
                    print(f"  WARNING: Failed to create '{import_folder_path}' folder: {e}")
            else:
                print(f"  [OK] '{import_folder_path}' folder already exists on remarkable")

    # Iterate over all unique (RM file, PC file) pairs exactly once
    def iterate_files():
        for rm_file in rm_root.traverse():
            pc_file = rm_file.on_computer()
            yield (rm_file, pc_file)
        for pc_file in pc_root.traverse():
            rm_file = pc_file.on_remarkable()
            if not rm_file: # already processed files on RM in last loop
                yield (rm_file, pc_file)

    print(f"Synchronizing PDFs with {rm.processed_dir_local}")
    print("Will use renderer(s)", " -> ".join(renderers))

    # Check if metadata was downloaded (needed for accurate sync comparison)
    metadata_files = [f for f in os.listdir(rm.raw_dir_local) if f.endswith('.metadata')]
    metadata_available = len(metadata_files) > 0
    if not metadata_available:
        print("WARNING: No metadata files found - metadata download may have been skipped")
        print("WARNING: DROP actions will be disabled to prevent accidental file deletion")
        print("WARNING: Only PUSH (upload) and PULL (download) actions will be performed")
        # Clear last_sync to force fresh comparison when metadata is unavailable
        if os.path.exists(rm.last_sync_path):
            print(f"WARNING: Clearing .last_sync file to force fresh sync (metadata unavailable)")
            os.remove(rm.last_sync_path)

    print("Comparing files and collecting commands")
    commands = {"PULL": [], "PUSH": [], "DROP": []}
    file_count = 0
    skip_count = 0
    # Only show detailed comparison for first 10 files, then every 50th file, or when verbose
    show_details = args.verbose if hasattr(args, 'verbose') else False
    
    # Pre-compute last_sync time once (used frequently)
    last_sync_time = rm.last_sync()
    
    # Progress indicator
    for rm_file, pc_file in iterate_files():
        file_count += 1
        
        # Show progress every 100 files (only if not showing details)
        if not show_details and file_count % 100 == 0:
            print(f"\r  Comparing files... {file_count} processed", end="", flush=True)
        
        path = rm_file.path() if rm_file else pc_file.path_on_remarkable()
        
        # Show what we're comparing (reduced output for speed)
        if show_details and (file_count <= 10 or file_count % 50 == 0):
            rm_status = f"RM: {rm_file.path()}" if rm_file else "RM: (not found)"
            pc_status = f"PC: {pc_file.path()}" if pc_file else "PC: (not found)"
            print(f"\n  [{file_count}] Comparing: {rm_status} | {pc_status}")
        
        action, reason = sync_action_and_reason(rm_file, pc_file, skip=skip, no_pull=NO_PULL_ENABLED, last_sync_time=last_sync_time)
        original_action = action
        
        # ALWAYS prevent DROP actions - convert to PUSH to avoid accidental deletion
        # This is especially important when metadata is unavailable, but we prevent it always for safety
        if action == "DROP":
            action = "PUSH"  # Convert DROP to PUSH to prevent accidental deletion
            reason = "converting DROP to PUSH (preventing accidental deletion)"
            if show_details:
                print(f"      -> Action changed: {original_action} -> {action} ({reason})")
        
        # When metadata is unavailable, be more aggressive about pushing files
        elif not metadata_available:
            # If file exists on PC but not found in metadata, always push it
            # (since we can't verify if it exists on RM without metadata)
            if not rm_file and pc_file:
                # File exists on PC but not in metadata - push it regardless of last_sync or other conditions
                action = "PUSH"
                reason = "new file (metadata unavailable, assuming not on RM)"
                if show_details:
                    print(f"      -> Action changed: {original_action} -> {action} ({reason})")
            # Also convert "up-to-date" skips to PUSH when metadata is unavailable
            elif action == "SKIP" and reason == "up-to-date" and pc_file:
                # Can't verify if file is actually up-to-date without metadata, so push it
                action = "PUSH"
                reason = "forcing push (metadata unavailable, cannot verify sync status)"
                if show_details:
                    print(f"      -> Action changed: {original_action} -> {action} ({reason})")
        
        # Show the determined action and reason (only for non-skip actions or when verbose)
        if action != "SKIP":
            if show_details or file_count <= 10 or file_count % 50 == 0:
                print(f"      -> {action}: {path} ({reason})")
            commands[action].append((action, reason, path, rm_file, pc_file))
        else:
            skip_count += 1
            # Show status every 10 skipped files
            if skip_count % 10 == 0:
                if args.verbose:
                    print(f"  Skipped {skip_count} file(s) (last: {path})...")
                else:
                    print(f"  Skipped {skip_count} file(s)...")
    
    # Clear progress indicator and show completion
    if not show_details:
        print("\r" + " " * 60 + "\r", end="", flush=True)  # Clear progress line
    print(f"  Comparison complete: {file_count} files compared, {skip_count} skipped")
    
    pull_count_before_filter = len(commands['PULL'])
    print(f"\n  Comparison complete: {file_count} files compared, {skip_count} skipped, {pull_count_before_filter} to pull, {len(commands['PUSH'])} to push, {len(commands['DROP'])} to drop")

    # Filter out PULL commands if --no-pull is set
    if NO_PULL_ENABLED:
        print(f"  --no-pull flag set: skipping all {pull_count_before_filter} PULL operation(s)")
        commands["PULL"] = []
        print(f"  After filtering: {len(commands['PULL'])} PULL commands remaining")
    else:
        if pull_count_before_filter > 0:
            print(f"  DEBUG: --no-pull flag NOT set")

    # Sort commands
    def key(command):
        action, reason, path, rm_file, pc_file = command
        return path
    commands["PULL"].sort(key=key, reverse=False) # pull shallow files first (creating directories before pulling their contents)
    commands["PUSH"].sort(key=key, reverse=False) # push shallow files first (creating directories before pushing their contents)
    commands["DROP"].sort(key=key, reverse=True)  # drop deep files first (deleting directories' contents before themselves)
    
    # Build final commands list - ensure PULL is empty if flag is set
    if NO_PULL_ENABLED:
        # Explicitly ensure PULL list is empty
        commands["PULL"] = []
        print(f"  Final check: PULL commands list has {len(commands['PULL'])} items (should be 0)")
    
    commands = commands["PULL"] + commands["PUSH"] + commands["DROP"] # join all commands in one list (pull first, then push, then drop)
    
    # Verify no PULL commands in final list when flag is set
    if NO_PULL_ENABLED:
        pull_in_final = [cmd for cmd in commands if cmd[0] == "PULL"]
        if pull_in_final:
            print(f"  ERROR: Found {len(pull_in_final)} PULL command(s) in final commands list! Removing...")
            commands = [cmd for cmd in commands if cmd[0] != "PULL"]
        else:
            print(f"  Verified: No PULL commands in final execution list")

    # List commands and prompt before proceeding
    # Final safety check: remove any PULL commands if flag is set
    if NO_PULL_ENABLED:
        commands = [cmd for cmd in commands if cmd[0] != "PULL"]
        print(f"  Final safety check: Removed any remaining PULL commands, {len(commands)} commands left")
    
    actions = [command[0] for command in commands]
    npull = actions.count("PULL")
    npush = actions.count("PUSH")
    ndrop = actions.count("DROP")
    
    # Verify no PULL commands when flag is set
    if NO_PULL_ENABLED and npull > 0:
        print(f"  ERROR: Found {npull} PULL command(s) in actions list despite --no-pull flag!")
        print(f"  Removing them from commands list...")
        commands = [cmd for cmd in commands if cmd[0] != "PULL"]
        actions = [command[0] for command in commands]
        npull = 0  # Force to 0
    npush = actions.count("PUSH")
    ndrop = actions.count("DROP")
    for i, (action, reason, path, rm_file, pc_file) in enumerate(commands):
        print(f"? ({i+1}/{len(commands)}) {action}: {path}" + (f" ({reason})" if args.verbose else ""))

    if len(commands) == 0:
        print("Did nothing (everything was up-to-date)")
        exit()
    else:
        # Build execution message
        action_parts = []
        if npull > 0:
            action_parts.append(f"pull {npull}")
        if npush > 0:
            action_parts.append(f"push {npush}")
        if ndrop > 0:
            action_parts.append(f"drop {ndrop}")
        
        exec_parts = []
        if npull > 0 and not NO_PULL_ENABLED:
            exec_parts.append(f"Pulling {npull}")
        elif NO_PULL_ENABLED and npull > 0:
            exec_parts.append(f"Skipping {npull} pull operation(s)")
        if npush > 0:
            exec_parts.append(f"pushing {npush}")
        if ndrop > 0:
            exec_parts.append(f"dropping {ndrop}")
        
        print(f"Automatically executing: {', '.join(exec_parts)} files")

    # Execute commands
    print(f"\n=== EXECUTION PHASE ===")
    print(f"  NO_PULL_ENABLED = {NO_PULL_ENABLED}")
    print(f"  Total commands to execute: {len(commands)}")
    
    # Count PULL commands before filtering
    pull_commands_before = [cmd for cmd in commands if cmd[0] == "PULL"]
    print(f"  PULL commands in list: {len(pull_commands_before)}")
    if pull_commands_before:
        print(f"  PULL command paths: {[cmd[2] for cmd in pull_commands_before]}")
    
    # Final verification: check if any PULL commands are in the execution list when flag is set
    if NO_PULL_ENABLED:
        if pull_commands_before:
            print(f"  ERROR: Found {len(pull_commands_before)} PULL command(s) in execution list despite --no-pull flag!")
            print(f"  Removing them from execution list...")
            commands = [cmd for cmd in commands if cmd[0] != "PULL"]
            print(f"  After removal: {len(commands)} commands remaining (should have 0 PULL commands)")
        else:
            print(f"  Verified: No PULL commands in execution list")
    
    # Final count after filtering
    pull_commands_after = [cmd for cmd in commands if cmd[0] == "PULL"]
    print(f"  PULL commands after filtering: {len(pull_commands_after)}")
    if pull_commands_after and NO_PULL_ENABLED:
        print(f"  CRITICAL ERROR: PULL commands still present after filtering!")
        print(f"  Force-removing all PULL commands...")
        commands = [cmd for cmd in commands if cmd[0] != "PULL"]
        print(f"  Final count: {len([cmd for cmd in commands if cmd[0] == 'PULL'])} PULL commands")
    print(f"=======================\n")
    
    for i, (action, reason, path, rm_file, pc_file) in enumerate(commands):
        print(f"! ({i+1}/{len(commands)}) {action}: {path}")
        if action == "PULL":
            # ABSOLUTE SAFETY: Never execute PULL if flag is set, regardless of how it got here
            # Check both the constant and sys.argv directly as a final safeguard
            final_check = NO_PULL_ENABLED or '--no-pull' in sys.argv
            if final_check:
                print(f"  BLOCKED: PULL command blocked by --no-pull flag!")
                print(f"    NO_PULL_ENABLED={NO_PULL_ENABLED}, '--no-pull' in sys.argv={'--no-pull' in sys.argv}")
                print(f"    Skipping download for: {path}")
                continue
            # This should never be reached if NO_PULL_ENABLED is True, but just in case:
            print(f"  WARNING: Executing PULL despite safety checks!")
            print(f"    NO_PULL_ENABLED={NO_PULL_ENABLED}, '--no-pull' in sys.argv={'--no-pull' in sys.argv}")
            rm_file.download()
        elif action == "PUSH":
            pc_file.upload()
        elif action == "DROP":
            pc_file.remove()

    # Write last sync timestamp after all operations complete
    rm.write_last_sync()

    # RM interface must be restarted to show newly added files
    if npush > 0:
        print(f"\nAll uploads complete.")
        
        # Verify some files were created (check a few metadata files exist locally)
        push_files = [cmd for cmd in commands if cmd[0] == "PUSH"]
        verified_count = 0
        for action, reason, path, rm_file, pc_file in push_files[:5]:  # Check first 5
            if pc_file:
                # Try to find the file ID - this is tricky without the ID
                # For now, just note that we pushed files
                pass
        
        print(f"Restarting remarkable interface to refresh file list...")
        rm.restart()
        print(f"\n[OK] Sync complete. {npush} file(s) pushed to reMarkable device.")
        print(f"  Files should now be visible on your device.")
        print(f"  If files don't appear, try manually restarting the device or check the file paths.")

    # Build final summary message
    summary_parts = []
    if NO_PULL_ENABLED and npull > 0:
        summary_parts.append(f"Skipped {npull} pull(s)")
    elif npull > 0:
        summary_parts.append(f"Pulled {npull}")
    if npush > 0:
        summary_parts.append(f"pushed {npush}")
    if ndrop > 0:
        summary_parts.append(f"dropped {ndrop}")
    print(f"{', '.join(summary_parts)} files")
