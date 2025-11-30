#!/usr/bin/python3
"""
Script to find and embed any missed document, audio, and video attachments into existing PDFs.
This script:
1. Scans PDFs for PDF/DOC/DOCX attachments that need to be converted to images (deletes those PDFs for re-conversion)
2. Scans markdown files, finds doc/docx/txt/audio/video references, and adds them to PDFs if missing.
"""

import os
import sys
import re
from datetime import datetime
from pathlib import Path

# Try to import pikepdf for PDF attachment embedding (most reliable)
# Fallback to pypdf/PyPDF2 if pikepdf not available
PIKEPDF_AVAILABLE = False
PYPDF_AVAILABLE = False
try:
    from pikepdf import Pdf, AttachedFileSpec
    PIKEPDF_AVAILABLE = True
except ImportError:
    try:
        from pypdf import PdfWriter, PdfReader
        PYPDF_AVAILABLE = True
    except ImportError:
        try:
            from PyPDF2 import PdfWriter, PdfReader
            PYPDF_AVAILABLE = True
        except ImportError:
            PYPDF_AVAILABLE = False

# Define the input and output directories
input_dir = r'c:\users\mwtorq\enexmd_update'
output_dir = r'c:\users\mwtorq\enexpdf'
log_file = r'c:\users\mwtorq\doc_attachment_log.txt'

# Files to skip (files that cause hangs or errors) - matches convert_md_to_pdf.py
SKIP_FILES = [
    'OID_admin_10-1-4-0-1_b15996.pdf',  # Known problematic file
    'Oracle_Database_Standards-Export-Import.md',  # Skip this markdown file entirely
    'Oracle_Database_Standards-Export-Import',  # Also match without extension
]

# Folders to skip entirely (any file in these folders will be skipped) - matches convert_md_to_pdf.py
SKIP_FOLDERS = [
    'Campbell_s_Soup_Design_Group_Meeting_Follow_Up-22_February_2016',  # Skip entire folder
    'CAD_ePDM_SQL_DB_Disaster_Recovery_Mtg-4_December_2012',  # Skip entire folder
    'CBI_Reporting_Issues-3_September_2013',  # Skip entire folder
]

# PDF attachments to skip (files that cause hangs when embedding)
SKIP_ATTACHMENTS = [
    'OID_admin_10-1-4-0-1_b15996.pdf',  # Known problematic attachment
]

# Function to log and print
def log_print(message):
    print(message)
    sys.stdout.flush()
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        f.flush()

# Counters for statistics
total_checked = 0
attachments_found = 0
attachments_added = 0
attachments_skipped = 0
errors = 0
pdfs_marked_for_reconversion = 0

def find_doc_attachments_in_markdown(md_path):
    """Find all document, audio, and video file references in a markdown file."""
    doc_attachments = []
    
    try:
        with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
            md_content = f.read()
        
        md_dir = os.path.dirname(md_path)
        
        # Find file links: [text](path/to/file.ext)
        def resolve_file_path(match):
            nonlocal doc_attachments
            full_match = match.group(0)
            link_text = match.group(1) if match.group(1) else ''
            file_path = match.group(2)
            
            # Skip if it's already a URL, email link, anchor link, or other non-file link
            if (file_path.startswith('http://') or 
                file_path.startswith('https://') or 
                file_path.startswith('file://') or
                file_path.startswith('mailto:') or
                file_path.startswith('#') or
                '://' in file_path):
                return full_match
            
            # Skip if it doesn't look like a file path (no extension and no common file patterns)
            # This avoids checking things like "mailto:email@domain.com" as files
            if not ('.' in file_path or 
                   os.path.sep in file_path or 
                   os.path.altsep in file_path if os.path.altsep else False or
                   '/' in file_path):  # Also check for forward slashes (common in markdown)
                return full_match
            
            # Only check file existence if it looks like a real file path
            # Skip the check for very short paths or paths that look like URLs/emails
            if len(file_path) < 3 or '@' in file_path:
                return full_match
            
            # Normalize path separators (markdown often uses forward slashes, even on Windows)
            # Convert forward slashes to OS-specific separators for path joining
            normalized_file_path = file_path.replace('/', os.path.sep) if os.path.sep != '/' else file_path
            
            # Resolve path relative to markdown file's directory
            # Handle both relative and absolute paths
            try:
                if os.path.isabs(normalized_file_path):
                    # Already absolute path
                    resolved_path = os.path.normpath(normalized_file_path)
                else:
                    # Relative path - resolve from markdown file's directory
                    resolved_path = os.path.normpath(os.path.join(md_dir, normalized_file_path))
                
                # Convert to absolute path for consistency
                resolved_path = os.path.abspath(resolved_path)
            except (OSError, ValueError) as e:
                # If path resolution fails (e.g., invalid characters, too long path), skip it
                return full_match
            
            # Audio formats
            audio_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus']
            # Video formats
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpg', '.mpeg']
            # Document formats
            doc_extensions = ['.doc', '.docx', '.txt']
            # All supported attachment formats
            file_ext = os.path.splitext(resolved_path)[1].lower()
            if file_ext in doc_extensions + audio_extensions + video_extensions:
                if os.path.exists(resolved_path):
                    filename = os.path.basename(resolved_path)
                    if filename not in SKIP_ATTACHMENTS and not any(skip in resolved_path for skip in SKIP_ATTACHMENTS):
                        doc_attachments.append((resolved_path, link_text))
                # Note: We don't log missing files here to avoid spam, but we could add verbose logging if needed
            
            return full_match
        
        # Find file link references (not images)
        re.sub(
            r'(?<!\!)\[([^\]]*)\]\(([^)]+)\)',
            resolve_file_path,
            md_content
        )
        
    except Exception as e:
        log_print(f"  Error reading markdown file {md_path}: {str(e)}")
    
    return doc_attachments

def get_existing_attachments(pdf_path):
    """Get list of existing attachment filenames in a PDF."""
    existing_attachments = []
    
    if not os.path.exists(pdf_path):
        return existing_attachments
    
    try:
        if PIKEPDF_AVAILABLE:
            pdf = Pdf.open(pdf_path)
            existing_attachments = list(pdf.attachments.keys())
            pdf.close()
        elif PYPDF_AVAILABLE:
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
            reader = PdfReader(pdf_path)
            if hasattr(reader, 'attachments'):
                for page in reader.pages:
                    if '/Annots' in page:
                        for annot in page['/Annots']:
                            obj = annot.get_object()
                            if obj.get('/Subtype') == '/FileAttachment':
                                file_spec = obj['/FS']
                                if '/F' in file_spec:
                                    existing_attachments.append(file_spec['/F'])
    except Exception as e:
        log_print(f"  Warning: Could not read existing attachments from {pdf_path}: {str(e)}")
    
    return existing_attachments

def has_pdf_doc_attachments(pdf_path):
    """Check if a PDF has PDF, DOC, or DOCX attachments that need to be converted to images."""
    attachments = get_existing_attachments(pdf_path)
    pdf_doc_extensions = ['.pdf', '.doc', '.docx']
    
    for attachment_name in attachments:
        file_ext = os.path.splitext(attachment_name)[1].lower()
        if file_ext in pdf_doc_extensions:
            return True
    return False

def find_corresponding_markdown(pdf_path):
    """Find the corresponding markdown file for a PDF file.
    Handles the complex PDF naming logic from convert_md_to_pdf.py:
    1. If folder only has markdown files, PDF is saved one level up using folder name
    2. If at root level, PDF uses file_base.pdf
    3. Otherwise, PDF uses directory structure with file_base.pdf or dir_name.pdf for README.md
    """
    # Get relative path from output_dir
    rel_path = os.path.relpath(pdf_path, output_dir)
    
    # Get directory and filename
    pdf_dir = os.path.dirname(rel_path)
    pdf_filename = os.path.basename(pdf_path)
    file_base = os.path.splitext(pdf_filename)[0]
    
    # Strategy 1: PDF at root level -> markdown at root level
    if pdf_dir == '.':
        md_path = os.path.join(input_dir, f"{file_base}.md")
        if os.path.exists(md_path):
            return md_path
    
    # Strategy 2: PDF in subdirectory -> markdown in same subdirectory
    if pdf_dir != '.':
        md_subdir = os.path.join(input_dir, pdf_dir)
        # Try README.md first (common case, especially for single README.md files)
        md_path = os.path.join(md_subdir, "README.md")
        if os.path.exists(md_path):
            return md_path
        # Try with filename
        md_path = os.path.join(md_subdir, f"{file_base}.md")
        if os.path.exists(md_path):
            return md_path
    
    # Strategy 3: PDF might be one level up (if folder only had markdown files)
    # PDF name is folder name, so look for folder with that name containing markdown files
    if pdf_dir != '.':
        # Check if there's a folder with PDF name that contains markdown files
        potential_folder = os.path.join(input_dir, pdf_dir, file_base)
        if os.path.isdir(potential_folder):
            # Check for README.md in that folder
            md_path = os.path.join(potential_folder, "README.md")
            if os.path.exists(md_path):
                return md_path
            # Check for any .md file in that folder
            for f in os.listdir(potential_folder):
                if f.lower().endswith('.md'):
                    return os.path.join(potential_folder, f)
    
    # Strategy 4: PDF name matches directory name (for README.md cases)
    if pdf_dir != '.':
        dir_name = os.path.basename(pdf_dir)
        if file_base == dir_name:
            # This might be a README.md that was converted
            md_path = os.path.join(input_dir, pdf_dir, "README.md")
            if os.path.exists(md_path):
                return md_path
    
    # Strategy 5: Try parent directory (if PDF was saved one level up)
    if pdf_dir != '.':
        parent_dir = os.path.dirname(pdf_dir)
        if parent_dir != '.':
            # PDF might be in parent, markdown in child folder named after PDF
            potential_folder = os.path.join(input_dir, pdf_dir, file_base)
            if os.path.isdir(potential_folder):
                md_path = os.path.join(potential_folder, "README.md")
                if os.path.exists(md_path):
                    return md_path
    
    return None

def rerun_conversion_for_file(md_path):
    """Re-run convert_md_to_pdf.py for a specific markdown file."""
    global errors
    
    if not os.path.exists(md_path):
        log_print(f"  ERROR: Markdown file not found: {md_path}")
        errors += 1
        return False
    
    try:
        # Import and run the conversion logic from convert_md_to_pdf.py
        # We'll use subprocess to run the conversion script with just this file
        converter_script = r'c:\users\mwtorq\convert_md_to_pdf.py'
        
        if not os.path.exists(converter_script):
            log_print(f"  ERROR: convert_md_to_pdf.py not found at {converter_script}")
            errors += 1
            return False
        
        # We can't easily run just one file through the existing script
        # Instead, we'll delete the PDF and let the user re-run convert_md_to_pdf.py
        # Or we could extract the conversion logic, but that's complex
        # For now, let's just mark it for re-conversion by deleting the PDF
        pdf_path = find_corresponding_pdf(md_path)
        if os.path.exists(pdf_path):
            log_print(f"  → Marking for re-conversion: {os.path.basename(pdf_path)}")
            # Delete the PDF so it will be recreated with image conversions
            try:
                os.remove(pdf_path)
                log_print(f"  ✓ Deleted PDF - will be recreated with image conversions on next run of convert_md_to_pdf.py")
                return True
            except Exception as e:
                log_print(f"  ERROR: Could not delete PDF: {str(e)}")
                errors += 1
                return False
        else:
            log_print(f"  Warning: PDF not found at {pdf_path}")
            return False
            
    except Exception as e:
        log_print(f"  ERROR: Could not process file for re-conversion: {str(e)}")
        import traceback
        log_print(f"  Error details: {traceback.format_exc()}")
        errors += 1
        return False

def add_attachment_to_pdf(pdf_path, attachment_path):
    """Add a document, audio, or video attachment to an existing PDF."""
    global attachments_added, errors
    
    if not os.path.exists(pdf_path):
        log_print(f"  ERROR: PDF not found: {pdf_path}")
        errors += 1
        return False
    
    if not os.path.exists(attachment_path):
        log_print(f"  ERROR: Attachment file not found: {attachment_path}")
        errors += 1
        return False
    
    filename = os.path.basename(attachment_path)
    
    try:
        if PIKEPDF_AVAILABLE:
            # Check file size before opening
            pdf_size = os.path.getsize(pdf_path)
            if pdf_size > 100 * 1024 * 1024:  # 100MB limit
                log_print(f"  Warning: PDF is very large ({pdf_size / 1024 / 1024:.1f}MB), skipping attachment")
                return False
            
            attach_size = os.path.getsize(attachment_path)
            if attach_size > 50 * 1024 * 1024:  # 50MB limit per attachment
                log_print(f"  Warning: Skipping large attachment {filename} ({attach_size / 1024 / 1024:.1f}MB)")
                return False
            
            # Open the PDF
            pdf = Pdf.open(pdf_path)
            
            # Ensure unique filename if duplicates exist
            original_filename = filename
            counter = 1
            while filename in pdf.attachments:
                name, ext = os.path.splitext(original_filename)
                filename = f"{name}_{counter}{ext}"
                counter += 1
            
            # Create attachment from file path
            attachment = AttachedFileSpec.from_filepath(pdf, Path(attachment_path))
            attachment.filename = filename
            
            # Add attachment to the PDF
            pdf.attachments[filename] = attachment
            
            # Save to a temporary file first, then replace the original
            temp_path = pdf_path + '.tmp'
            try:
                # Try saving without PDF/A first
                try:
                    pdf.save(temp_path, preserve_pdfa=False)
                except Exception:
                    pdf.save(temp_path, preserve_pdfa=True)
                
                # Verify attachment was saved
                verify_pdf = Pdf.open(temp_path)
                saved_attachment_count = len(verify_pdf.attachments)
                verify_pdf.close()
                pdf.close()
                
                if saved_attachment_count > 0:
                    # Replace original with the new file
                    os.replace(temp_path, pdf_path)
                    log_print(f"  ✓ Added attachment: {filename} ({attach_size / 1024:.1f}KB)")
                    attachments_added += 1
                    return True
                else:
                    os.remove(temp_path)
                    log_print(f"  ERROR: Attachment was not saved to PDF")
                    errors += 1
                    return False
            except Exception as e:
                pdf.close()
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                log_print(f"  ERROR: Could not save PDF with attachment: {str(e)}")
                errors += 1
                return False
                
        elif PYPDF_AVAILABLE:
            try:
                from pypdf import PdfWriter, PdfReader
            except ImportError:
                from PyPDF2 import PdfWriter, PdfReader
            
            # Open the PDF
            writer = PdfWriter()
            reader = PdfReader(pdf_path)
            
            # Copy all pages from the existing PDF
            for page in reader.pages:
                writer.add_page(page)
            
            # Read the attachment file
            with open(attachment_path, 'rb') as attach_file:
                attachment_data = attach_file.read()
            
            attach_size = len(attachment_data)
            
            # Add attachment
            try:
                writer.add_attachment(filename, attachment_data)
            except TypeError:
                try:
                    writer.add_attachment(filename=filename, data=attachment_data)
                except TypeError:
                    writer.add_attachment(attachment_path)
            
            # Write the PDF with attachment
            temp_path = pdf_path + '.tmp'
            with open(temp_path, 'wb') as output_file:
                writer.write(output_file)
            
            # Replace original
            os.replace(temp_path, pdf_path)
            log_print(f"  ✓ Added attachment: {filename} ({attach_size / 1024:.1f}KB)")
            attachments_added += 1
            return True
            
    except Exception as e:
        log_print(f"  ERROR: Could not add attachment {filename}: {str(e)}")
        import traceback
        log_print(f"  Error details: {traceback.format_exc()}")
        errors += 1
        return False

def search_pdf_in_output_dir(pdf_name, preferred_rel_path=None):
    """Search for a PDF file in the output directory tree by name (case-insensitive).
    If preferred_rel_path is provided, prefers PDFs in that relative path.
    Returns the full path if found, None otherwise.
    """
    if not os.path.isdir(output_dir):
        return None
    
    found_paths = []
    preferred_path = None
    pdf_name_lower = pdf_name.lower()
    
    for root, dirs, files in os.walk(output_dir):
        # Case-insensitive search
        for file in files:
            if file.lower() == pdf_name_lower:
                found_path = os.path.join(root, file)
                found_paths.append(found_path)
                
                # If we have a preferred path, check if this matches it
                if preferred_rel_path:
                    rel_path = os.path.relpath(root, output_dir)
                    # Normalize paths for comparison
                    rel_path_norm = rel_path.replace('\\', '/')
                    preferred_norm = preferred_rel_path.replace('\\', '/')
                    if rel_path_norm == preferred_norm:
                        preferred_path = found_path
                        break  # Found preferred path, use it
                if preferred_path:
                    break
        if preferred_path:
            break
    
    # Return preferred path if found, otherwise return first match
    if preferred_path:
        return preferred_path
    elif found_paths:
        return found_paths[0]  # Return first match if no preferred path
    
    return None

def find_corresponding_pdf(md_path):
    """Find the corresponding PDF file for a markdown file.
    This matches the exact PDF naming logic from convert_md_to_pdf.py.
    """
    # Get relative path from input_dir (this is the directory containing the markdown file)
    # This matches: rel_path = os.path.relpath(root, input_dir) in convert_md_to_pdf.py
    rel_path = os.path.relpath(os.path.dirname(md_path), input_dir)
    
    # Get the directory name (parent folder of the markdown file)
    # This matches: dir_name = os.path.basename(root) in convert_md_to_pdf.py
    dir_name = os.path.basename(os.path.dirname(md_path))
    
    # Get the base filename without extension
    md_filename = os.path.basename(md_path)
    file_base = os.path.splitext(md_filename)[0]
    
    # Check if this directory only contains markdown files (likely a "note folder")
    # This matches the logic in convert_md_to_pdf.py
    md_file_dir = os.path.dirname(md_path)
    if os.path.isdir(md_file_dir):
        try:
            files_in_dir = os.listdir(md_file_dir)
            all_files_are_md = all(f.lower().endswith('.md') for f in files_in_dir)
            md_files_in_dir = [f for f in files_in_dir if f.lower().endswith('.md')]
            is_single_readme = len(md_files_in_dir) == 1 and md_filename.lower() == 'readme.md'
        except (OSError, PermissionError):
            all_files_are_md = False
            is_single_readme = False
    else:
        all_files_are_md = False
        is_single_readme = False
    
    # If folder only has markdown files, save PDF one level up using folder name
    if rel_path != '.' and all_files_are_md:
        # File is in a folder that only contains markdown - save PDF one level up
        parent_rel_path = os.path.dirname(rel_path) if os.path.dirname(rel_path) != '.' else ''
        if parent_rel_path:
            output_subdir = os.path.join(output_dir, parent_rel_path)
        else:
            output_subdir = output_dir
        # Use folder name as PDF name
        pdf_filename = f"{dir_name}.pdf"
    elif rel_path == '.':
        # At root level, save directly to output directory
        output_subdir = output_dir
        pdf_filename = f"{file_base}.pdf"
    else:
        # Create subdirectory structure in output matching input structure
        output_subdir = os.path.join(output_dir, rel_path)
        # Use just the filename (directory structure provides uniqueness)
        if is_single_readme:
            pdf_filename = f"{dir_name}.pdf"
        else:
            pdf_filename = f"{file_base}.pdf"
    
    # Construct the output PDF path
    pdf_path = os.path.join(output_subdir, pdf_filename)
    
    return pdf_path

if __name__ == '__main__':
    log_print("=" * 60)
    log_print("Finding and embedding missed document, audio, and video attachments")
    log_print("=" * 60)
    log_print(f"Input directory: {input_dir}")
    log_print(f"Output directory: {output_dir}")
    
    if not PIKEPDF_AVAILABLE and not PYPDF_AVAILABLE:
        log_print("ERROR: No PDF library available for attachments.")
        log_print("Install with: pip install pikepdf (recommended) or pip install pypdf")
        sys.exit(1)
    
    log_print(f"Using: {'pikepdf' if PIKEPDF_AVAILABLE else 'pypdf'}")
    log_print("")
    
    # STEP 1: Scan PDFs for PDF/DOC/DOCX attachments that need to be converted to images
    log_print("=" * 60)
    log_print("STEP 1: Checking PDFs for PDF/DOC/DOCX attachments that need image conversion")
    log_print("=" * 60)
    
    pdfs_to_reconvert = []
    for root, dirs, files in os.walk(output_dir):
        for pdf_file in files:
            if pdf_file.lower().endswith('.pdf'):
                pdf_path = os.path.join(root, pdf_file)
                
                # Check if this PDF has PDF/DOC/DOCX attachments
                if has_pdf_doc_attachments(pdf_path):
                    # Find corresponding markdown file
                    md_path = find_corresponding_markdown(pdf_path)
                    if md_path:
                        pdfs_to_reconvert.append((pdf_path, md_path))
                        log_print(f"  → Found PDF with PDF/DOC/DOCX attachments: {os.path.relpath(pdf_path, output_dir)}")
                        log_print(f"    Markdown file: {os.path.relpath(md_path, input_dir)}")
                    else:
                        log_print(f"  Warning: Could not find markdown file for {os.path.relpath(pdf_path, output_dir)}")
    
    # Delete PDFs that need re-conversion
    if pdfs_to_reconvert:
        log_print("")
        log_print(f"Found {len(pdfs_to_reconvert)} PDF(s) with PDF/DOC/DOCX attachments that need image conversion")
        log_print("Deleting these PDFs so they can be recreated with image conversions...")
        log_print("")
        
        for pdf_path, md_path in pdfs_to_reconvert:
            try:
                os.remove(pdf_path)
                pdfs_marked_for_reconversion += 1
                log_print(f"  ✓ Deleted: {os.path.relpath(pdf_path, output_dir)}")
            except Exception as e:
                log_print(f"  ERROR: Could not delete {pdf_path}: {str(e)}")
                errors += 1
        
        log_print("")
        log_print(f"Deleted {pdfs_marked_for_reconversion} PDF(s). These will be recreated with image conversions.")
        log_print("Run convert_md_to_pdf.py to recreate these PDFs with proper image conversions.")
        log_print("")
    else:
        log_print("No PDFs found with PDF/DOC/DOCX attachments that need conversion.")
        log_print("")
    
    # STEP 2: Continue with existing logic for other attachments
    log_print("=" * 60)
    log_print("STEP 2: Finding and embedding missed document, audio, and video attachments")
    log_print("=" * 60)
    log_print("")
    
    # Count total markdown files first for progress reporting
    total_md_files = 0
    for root, dirs, files in os.walk(input_dir):
        total_md_files += len([f for f in files if f.lower().endswith('.md')])
    
    log_print(f"Scanning {total_md_files} markdown file(s) for attachments...")
    log_print("")
    
    # Walk through the directory tree
    for root, dirs, files in os.walk(input_dir):
        # Filter for Markdown files
        md_files = [f for f in files if f.lower().endswith('.md')]
        
        for md_file in md_files:
            total_checked += 1
            md_path = os.path.join(root, md_file)
            
            # Check if this file should be skipped (matches convert_md_to_pdf.py logic)
            if md_file in SKIP_FILES or any(skip_file in md_path for skip_file in SKIP_FILES):
                # File is in skip list - PDF was never created, so skip it
                continue  # Skip this file entirely
            
            # Check if file is in a skipped folder
            if any(skip_folder in md_path for skip_folder in SKIP_FOLDERS):
                # File is in a skipped folder - PDF was never created, so skip it
                continue  # Skip this file entirely
            
            # Find document/audio/video attachments in markdown
            doc_attachments = find_doc_attachments_in_markdown(md_path)
            
            if not doc_attachments:
                # Only log first few files without attachments to avoid spam
                if total_checked <= 10 or total_checked % 100 == 0:
                    log_print(f"[{total_checked}] {os.path.relpath(md_path, input_dir)} - No attachments found")
                continue
            
            attachments_found += len(doc_attachments)
            log_print(f"[{total_checked}] {os.path.relpath(md_path, input_dir)}")
            log_print(f"  Found {len(doc_attachments)} attachment(s) (doc/audio/video)")
            # Log attachment details for first few files
            if total_checked <= 10:
                for attach_path, link_text in doc_attachments:
                    log_print(f"    - {os.path.basename(attach_path)}")
            
            # Find corresponding PDF
            pdf_path = find_corresponding_pdf(md_path)
            
            # Try alternative PDF paths if the primary path doesn't exist
            if not os.path.exists(pdf_path):
                rel_path = os.path.relpath(os.path.dirname(md_path), input_dir)
                dir_name = os.path.basename(os.path.dirname(md_path))
                md_filename = os.path.basename(md_path)
                file_base = os.path.splitext(md_filename)[0]
                found_alternative = False  # Initialize flag
                
                # FIRST: Check if there's any PDF in the expected directory (even if name doesn't match exactly)
                expected_output_dir = os.path.join(output_dir, rel_path) if rel_path != '.' else output_dir
                if os.path.isdir(expected_output_dir):
                    try:
                        pdfs_in_dir = [f for f in os.listdir(expected_output_dir) if f.lower().endswith('.pdf')]
                        if pdfs_in_dir:
                            # Prefer PDFs that match the expected name (case-insensitive)
                            preferred_pdf = None
                            for pdf_file in pdfs_in_dir:
                                pdf_lower = pdf_file.lower()
                                if pdf_lower == f"{dir_name.lower()}.pdf" or pdf_lower == f"{file_base.lower()}.pdf":
                                    preferred_pdf = pdf_file
                                    break
                            
                            # Use preferred PDF if found, otherwise use first PDF in directory
                            pdf_to_use = preferred_pdf if preferred_pdf else pdfs_in_dir[0]
                            alt_pdf = os.path.join(expected_output_dir, pdf_to_use)
                            if os.path.exists(alt_pdf):
                                pdf_path = alt_pdf
                                found_alternative = True
                                if preferred_pdf:
                                    log_print(f"  Found PDF in expected directory: {os.path.relpath(pdf_path, output_dir)}")
                                else:
                                    log_print(f"  Found PDF in expected directory (name doesn't match exactly): {os.path.relpath(pdf_path, output_dir)}")
                                    log_print(f"    Expected: {dir_name}.pdf or {file_base}.pdf, Found: {pdf_to_use}")
                    except (OSError, PermissionError) as e:
                        pass  # Silently skip permission errors
                
                # SECOND: Check one level up (for folders that only contain markdown files)
                if not found_alternative and rel_path != '.':
                    parent_rel_path = os.path.dirname(rel_path) if os.path.dirname(rel_path) != '.' else ''
                    if parent_rel_path:
                        parent_output_dir = os.path.join(output_dir, parent_rel_path)
                    else:
                        parent_output_dir = output_dir
                    
                    if os.path.isdir(parent_output_dir):
                        try:
                            pdfs_in_parent = [f for f in os.listdir(parent_output_dir) if f.lower().endswith('.pdf')]
                            # Look for PDF with directory name
                            for pdf_file in pdfs_in_parent:
                                if pdf_file.lower() == f"{dir_name.lower()}.pdf":
                                    alt_pdf = os.path.join(parent_output_dir, pdf_file)
                                    if os.path.exists(alt_pdf):
                                        pdf_path = alt_pdf
                                        found_alternative = True
                                        log_print(f"  Found PDF one level up: {os.path.relpath(pdf_path, output_dir)}")
                                        break
                        except (OSError, PermissionError):
                            pass
                
                # THIRD: Do a comprehensive directory tree search for PDFs with matching names
                # This is the most reliable way to find PDFs
                if not found_alternative:
                    search_names = [f"{dir_name}.pdf", f"{file_base}.pdf"]
                    
                    for search_name in search_names:
                        # Prefer PDFs in the same relative path as the markdown file
                        found_pdf = search_pdf_in_output_dir(search_name, preferred_rel_path=rel_path)
                        if found_pdf and os.path.exists(found_pdf):
                            pdf_path = found_pdf
                            found_alternative = True
                            log_print(f"  Found PDF via directory search: {os.path.relpath(pdf_path, output_dir)}")
                            break
                    
                    # If still not found, try partial name matching (PDFs that contain the directory name)
                    if not found_alternative and os.path.isdir(output_dir):
                        dir_name_lower = dir_name.lower()
                        for root, dirs, files in os.walk(output_dir):
                            for file in files:
                                if file.lower().endswith('.pdf') and dir_name_lower in file.lower():
                                    # Check if this PDF is in a related directory
                                    file_rel_path = os.path.relpath(root, output_dir)
                                    if rel_path in file_rel_path or file_rel_path in rel_path:
                                        found_pdf = os.path.join(root, file)
                                        if os.path.exists(found_pdf):
                                            pdf_path = found_pdf
                                            found_alternative = True
                                            log_print(f"  Found PDF via partial name match: {os.path.relpath(pdf_path, output_dir)}")
                                            break
                            if found_alternative:
                                break
                
                # SECOND: If directory search didn't find it, try calculated alternative paths
                if not found_alternative:
                    alternative_paths = []
                    
                    # Alternative 1: Try in the same directory as markdown with file_base name
                    if rel_path != '.':
                        alt_path1 = os.path.join(output_dir, rel_path, f"{file_base}.pdf")
                        if alt_path1 != pdf_path:
                            alternative_paths.append(alt_path1)
                    
                    # Alternative 2: Try with directory name as filename (for README.md cases)
                    if rel_path != '.':
                        alt_path2 = os.path.join(output_dir, rel_path, f"{dir_name}.pdf")
                        if alt_path2 != pdf_path and alt_path2 not in alternative_paths:
                            alternative_paths.append(alt_path2)
                    
                    # Alternative 3: Try one level up with directory name (for folders that only contain markdown)
                    if rel_path != '.':
                        parent_rel_path = os.path.dirname(rel_path) if os.path.dirname(rel_path) != '.' else ''
                        if parent_rel_path:
                            alt_path3 = os.path.join(output_dir, parent_rel_path, f"{dir_name}.pdf")
                        else:
                            alt_path3 = os.path.join(output_dir, f"{dir_name}.pdf")
                        if alt_path3 != pdf_path and alt_path3 not in alternative_paths:
                            alternative_paths.append(alt_path3)
                    
                    # Alternative 4: Try one level up with file_base name
                    if rel_path != '.':
                        parent_rel_path = os.path.dirname(rel_path) if os.path.dirname(rel_path) != '.' else ''
                        if parent_rel_path:
                            alt_path4 = os.path.join(output_dir, parent_rel_path, f"{file_base}.pdf")
                        else:
                            alt_path4 = os.path.join(output_dir, f"{file_base}.pdf")
                        if alt_path4 != pdf_path and alt_path4 not in alternative_paths:
                            alternative_paths.append(alt_path4)
                    
                    # Try alternative paths
                    for alt_path in alternative_paths:
                        if os.path.exists(alt_path):
                            pdf_path = alt_path
                            found_alternative = True
                            log_print(f"  Found PDF at alternative location: {os.path.relpath(pdf_path, output_dir)}")
                            break
                
                if not found_alternative:
                    # Final attempt: Search for any PDF in nearby directories
                    nearby_dirs = []
                    if rel_path != '.':
                        nearby_dirs.append(rel_path)
                        parent = os.path.dirname(rel_path)
                        if parent != '.':
                            nearby_dirs.append(parent)
                        # Also check sibling directories
                        if parent != '.':
                            parent_dir = os.path.join(output_dir, parent)
                            if os.path.isdir(parent_dir):
                                try:
                                    siblings = [d for d in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, d))]
                                    for sibling in siblings:
                                        sibling_path = os.path.join(parent, sibling)
                                        if sibling_path not in nearby_dirs:
                                            nearby_dirs.append(sibling_path)
                                except:
                                    pass
                    
                    # Search in nearby directories
                    for nearby_dir in nearby_dirs:
                        nearby_output_dir = os.path.join(output_dir, nearby_dir)
                        if os.path.isdir(nearby_output_dir):
                            try:
                                pdfs_nearby = [f for f in os.listdir(nearby_output_dir) if f.lower().endswith('.pdf')]
                                # Look for PDFs that might match (contain directory name or file base)
                                for pdf_file in pdfs_nearby:
                                    pdf_lower = pdf_file.lower()
                                    if (dir_name.lower() in pdf_lower or 
                                        file_base.lower() in pdf_lower or
                                        pdf_lower.replace('_', '-') == dir_name.lower().replace('_', '-') + '.pdf'):
                                        alt_pdf = os.path.join(nearby_output_dir, pdf_file)
                                        if os.path.exists(alt_pdf):
                                            pdf_path = alt_pdf
                                            found_alternative = True
                                            log_print(f"  Found PDF in nearby directory: {os.path.relpath(pdf_path, output_dir)}")
                                            break
                                if found_alternative:
                                    break
                            except (OSError, PermissionError):
                                pass
                    
                    if not found_alternative:
                        log_print(f"  WARNING: PDF not found at {os.path.relpath(pdf_path, output_dir)}")
                        log_print(f"    Markdown file: {os.path.relpath(md_path, input_dir)}")
                        log_print(f"    Searched for: {dir_name}.pdf, {file_base}.pdf")
                        log_print(f"    This PDF may not have been created yet, or the file may be in the skip list.")
                        log_print(f"    Skipping {len(doc_attachments)} attachment(s) for this file.")
                        attachments_skipped += len(doc_attachments)
                        continue
            
            # Get existing attachments
            existing_attachments = get_existing_attachments(pdf_path)
            log_print(f"  PDF has {len(existing_attachments)} existing attachment(s)")
            
            # Check each attachment
            for attachment_path, link_text in doc_attachments:
                filename = os.path.basename(attachment_path)
                
                # Check if already attached
                if filename in existing_attachments:
                    log_print(f"  ⊘ Already attached: {filename}")
                    attachments_skipped += 1
                    continue
                
                # Add the attachment
                log_print(f"  → Adding: {filename}")
                add_attachment_to_pdf(pdf_path, attachment_path)
    
    log_print("")
    log_print("=" * 60)
    log_print("Summary")
    log_print("=" * 60)
    log_print(f"PDFs marked for re-conversion (PDF/DOC/DOCX attachments): {pdfs_marked_for_reconversion}")
    log_print(f"Markdown files checked: {total_checked}")
    log_print(f"Attachments found in markdown (doc/audio/video): {attachments_found}")
    log_print(f"Attachments successfully added to PDFs: {attachments_added}")
    log_print(f"Attachments skipped (already present in PDF): {attachments_skipped}")
    log_print(f"Errors encountered: {errors}")
    log_print("=" * 60)
    
    if attachments_found == 0:
        log_print("")
        log_print("NOTE: No attachments were found in any markdown files.")
        log_print("      This could mean:")
        log_print("      - All attachments are already embedded in the PDFs")
        log_print("      - Markdown files don't contain doc/docx/txt/audio/video file links")
        log_print("      - Attachment files don't exist at the referenced paths")
    elif attachments_added == 0 and attachments_found > 0:
        log_print("")
        log_print("NOTE: Attachments were found but none were added.")
        log_print("      This could mean:")
        log_print("      - All attachments are already present in the PDFs")
        log_print("      - PDFs for the markdown files were not found")
        log_print("      - Errors occurred while adding attachments")
    
    if pdfs_marked_for_reconversion > 0:
        log_print("")
        log_print("NOTE: Run convert_md_to_pdf.py to recreate the deleted PDFs with proper image conversions.")
        log_print("=" * 60)

