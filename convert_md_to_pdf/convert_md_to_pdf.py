import os
import sys
import re
import glob
from md2pdf.core import md2pdf
from datetime import datetime
from pathlib import Path
import signal
import time
import subprocess
import tempfile
import json

# Try to import pikepdf for PDF attachment embedding (most reliable)
# Fallback to pypdf/PyPDF2, if pikepdf not available
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

# Try to import pdf2image for converting PDF attachments to images
PDF2IMAGE_AVAILABLE = False
PDF2IMAGE_POPPLER_ERROR = False
POPPLER_PATH = None

# Try to find poppler installation
_poppler_base = os.path.join(os.path.expanduser("~"), "poppler-25.11.0")
if os.path.exists(_poppler_base):
    # Check for conda-style installation (Library/bin)
    _poppler_bin = os.path.join(_poppler_base, "Library", "bin")
    if os.path.exists(_poppler_bin):
        POPPLER_PATH = _poppler_bin
    else:
        # Check for direct bin installation
        _poppler_bin = os.path.join(_poppler_base, "bin")
        if os.path.exists(_poppler_bin):
            POPPLER_PATH = _poppler_bin

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
    # Try to import the exception so we can catch it
    try:
        from pdf2image.exceptions import PDFInfoNotInstalledError
    except ImportError:
        # Older versions might not have this exception
        PDFInfoNotInstalledError = Exception
except ImportError:
    try:
        from pdf2image import convert_from_bytes
        PDF2IMAGE_AVAILABLE = True
        try:
            from pdf2image.exceptions import PDFInfoNotInstalledError
        except ImportError:
            PDFInfoNotInstalledError = Exception
    except ImportError:
        PDF2IMAGE_AVAILABLE = False

# Try to import docx2pdf for converting DOCX to PDF
DOCX2PDF_AVAILABLE = False
try:
    from docx2pdf import convert
    DOCX2PDF_AVAILABLE = True
except ImportError:
    DOCX2PDF_AVAILABLE = False

# Note: python-docx import removed - not currently used
# If needed in the future, can be re-added with: from docx import Document

# DOC to PDF conversion tool detection (for older .doc files)
DOC_TO_PDF_AVAILABLE = False
LIBREOFFICE_PATH = None

def find_libreoffice():
    """Find LibreOffice installation on the system."""
    global DOC_TO_PDF_AVAILABLE, LIBREOFFICE_PATH
    
    # Common LibreOffice installation paths on Windows
    possible_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        os.path.join(os.path.expanduser("~"), r"AppData\Local\Programs\LibreOffice\program\soffice.exe"),
        r"C:\LibreOffice\program\soffice.exe",
        # Check if soffice is in PATH
        "soffice.exe",
        "soffice",  # Linux/Mac
    ]
    
    for path in possible_paths:
        try:
            # If it's not an absolute path, try to find it in PATH
            if not os.path.isabs(path) or os.path.exists(path):
                # Try to run it to verify it works
                result = subprocess.run(
                    [path, '--version'],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    LIBREOFFICE_PATH = path
                    DOC_TO_PDF_AVAILABLE = True
                    return path
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            continue
    
    return None

# Try to find LibreOffice for DOC conversion
find_libreoffice()

# Define the input and output directories
input_dir = r'c:\users\mwtorq\enexmd_update'
output_dir = r'c:\users\mwtorq\enexpdf'
log_file = r'c:\users\mwtorq\conversion_log.txt'
css_file = r'c:\users\mwtorq\pdf_style.css'

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)

# Worker script for subprocess conversion (can be killed on timeout)
CONVERTER_SCRIPT = """
import os
import sys
from md2pdf.core import md2pdf

# Read arguments from stdin
import json
args = json.load(sys.stdin)
pdf_path = args['pdf_path']
md_content = args['md_content']
css_file = args.get('css_file')
md_dir = args.get('md_dir', '.')

try:
    os.chdir(md_dir)
    if css_file and os.path.exists(css_file):
        md2pdf(pdf_path, md_content=md_content, css_file_path=css_file)
    else:
        md2pdf(pdf_path, md_content=md_content)
    sys.exit(0)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
"""

# Function to log and print
def log_print(message):
    print(message)
    sys.stdout.flush()
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        f.flush()

# Function to convert DOC/DOCX to images via PDF
def convert_doc_to_images(doc_path, temp_dir):
    """Convert DOC or DOCX file to images by first converting to PDF, then to images.
    Returns list of PIL Image objects, or None if conversion fails.
    """
    if not PDF2IMAGE_AVAILABLE:
        return None
    
    try:
        temp_pdf_path = None
        pdf_created_by_us = False
        
        # Convert DOC/DOCX to PDF
        file_ext = os.path.splitext(doc_path)[1].lower()
        if file_ext == '.docx':
            if DOCX2PDF_AVAILABLE:
                # Create a temporary PDF file
                temp_pdf = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False, dir=temp_dir)
                temp_pdf.close()
                temp_pdf_path = temp_pdf.name
                pdf_created_by_us = True
                # Use docx2pdf to convert DOCX to PDF
                # Note: docx2pdf on Windows requires Microsoft Word or LibreOffice
                try:
                    convert(doc_path, temp_pdf_path)
                except Exception as e:
                    log_print(f"  Warning: docx2pdf conversion failed: {str(e)}")
                    log_print(f"  Note: docx2pdf requires Microsoft Word or LibreOffice on Windows")
                    # Clean up temp file
                    try:
                        os.unlink(temp_pdf_path)
                    except:
                        pass
                    return None
            else:
                log_print(f"  Warning: docx2pdf not available, cannot convert DOCX to images")
                return None
        elif file_ext == '.doc':
            # For .doc files, use LibreOffice (older binary format)
            if DOC_TO_PDF_AVAILABLE and LIBREOFFICE_PATH:
                try:
                    # Convert DOC to PDF using LibreOffice
                    # Use absolute path for input file
                    abs_doc_path = os.path.abspath(doc_path)
                    abs_temp_dir = os.path.abspath(temp_dir)
                    
                    result = subprocess.run(
                        [LIBREOFFICE_PATH, '--headless', '--convert-to', 'pdf', '--outdir', abs_temp_dir, abs_doc_path],
                        capture_output=True,
                        timeout=60,
                        cwd=abs_temp_dir
                    )
                    
                    if result.returncode == 0:
                        # LibreOffice creates PDF with same name in output dir
                        expected_pdf = os.path.join(abs_temp_dir, os.path.splitext(os.path.basename(doc_path))[0] + '.pdf')
                        if os.path.exists(expected_pdf):
                            temp_pdf_path = expected_pdf
                            pdf_created_by_us = False  # LibreOffice created it
                        else:
                            # Sometimes LibreOffice uses a different name or path
                            # Search for any PDF files created recently in the temp dir
                            pdf_files = glob.glob(os.path.join(abs_temp_dir, '*.pdf'))
                            if pdf_files:
                                # Use the most recently created one
                                pdf_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                                temp_pdf_path = pdf_files[0]
                                pdf_created_by_us = False
                            else:
                                log_print(f"  Warning: LibreOffice conversion succeeded but PDF not found")
                                return None
                    else:
                        error_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else 'Unknown error'
                        log_print(f"  Warning: LibreOffice conversion failed: {error_msg}")
                        return None
                except subprocess.TimeoutExpired:
                    log_print(f"  Warning: DOC to PDF conversion timed out (60s limit)")
                    return None
                except Exception as e:
                    log_print(f"  Warning: Error converting DOC to PDF: {str(e)}")
                    return None
            else:
                log_print(f"  Warning: LibreOffice not found, cannot convert .doc files to images")
                log_print(f"  Install LibreOffice from: https://www.libreoffice.org/download/")
                log_print(f"  Or convert .doc files to .docx format first")
                return None
        else:
            return None
        
        # Check if PDF was created
        if not temp_pdf_path or not os.path.exists(temp_pdf_path):
            log_print(f"  Warning: PDF conversion failed, file not created")
            return None
        
        # Convert PDF to images using pdf2image
        try:
            convert_kwargs = {'dpi': 150, 'output_folder': temp_dir}
            if POPPLER_PATH:
                convert_kwargs['poppler_path'] = POPPLER_PATH
            images = convert_from_path(temp_pdf_path, **convert_kwargs)
        except (PDFInfoNotInstalledError, Exception) as e:
            # Check if it's a poppler error
            error_msg = str(e).lower()
            if 'poppler' in error_msg or 'pdfinfo' in error_msg or 'page count' in error_msg:
                log_print(f"  Warning: Poppler not installed. PDF-to-image conversion requires poppler.")
                log_print(f"    Install poppler: https://github.com/oschwartz10612/poppler-windows/releases/")
                log_print(f"    Or use: conda install -c conda-forge poppler")
                return None
            else:
                # Other error, re-raise or log
                log_print(f"  Warning: Error converting PDF to images: {str(e)}")
                return None
        
        # Clean up temporary PDF (only if we created it, not if LibreOffice created it)
        if pdf_created_by_us:
            try:
                os.unlink(temp_pdf_path)
            except:
                pass
        
        return images if images else None
        
    except Exception as e:
        log_print(f"  Warning: Error converting DOC/DOCX to images: {str(e)}")
        import traceback
        log_print(f"  Traceback: {traceback.format_exc()}")
        return None

# Counters for statistics
total_files = 0
converted_files = 0
error_files = 0
skipped_files = 0
timeout_files = 0

# Timeout for conversion (in seconds)
CONVERSION_TIMEOUT = 300  # 300 seconds (5 minutes)

# Files to skip (files that cause hangs or errors)
SKIP_FILES = [
    'OID_admin_10-1-4-0-1_b15996.pdf',  # Known problematic file
    'Oracle_Database_Standards-Export-Import.md',  # Skip this markdown file entirely
    'Oracle_Database_Standards-Export-Import',  # Also match without extension
]

# Folders to skip entirely (any file in these folders will be skipped)
SKIP_FOLDERS = [
    'Campbell_s_Soup_Design_Group_Meeting_Follow_Up-22_February_2016',  # Skip entire folder
    'CAD_ePDM_SQL_DB_Disaster_Recovery_Mtg-4_December_2012',  # Skip entire folder
    'CBI_Reporting_Issues-3_September_2013',  # Skip entire folder
]

# PDF attachments to skip (files that cause hangs when embedding)
SKIP_ATTACHMENTS = [
    'OID_admin_10-1-4-0-1_b15996.pdf',  # Known problematic attachment
]

# Clear previous log and create initial entry
try:
    if os.path.exists(log_file):
        os.remove(log_file)
    # Create log file immediately
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Script started\n")
        f.write(f"Input dir exists: {os.path.exists(input_dir)}\n")
        f.write(f"Output dir: {output_dir}\n")
        f.flush()
except Exception as e:
    print(f"Warning: Could not create log file: {e}")

if __name__ == '__main__':
    log_print(f"Starting conversion from {input_dir} to {output_dir}\n")
    log_print(f"Input directory exists: {os.path.exists(input_dir)}")
    log_print(f"Output directory: {output_dir}")
    if PDF2IMAGE_AVAILABLE:
        log_print(f"PDF-to-image conversion: Available (PDF attachments will be converted to images)")
        if POPPLER_PATH:
            log_print(f"  Poppler path found: {POPPLER_PATH}")
        else:
            log_print(f"  Note: Requires poppler to be installed and in PATH")
            log_print(f"    Windows: https://github.com/oschwartz10612/poppler-windows/releases/")
            log_print(f"    Or: conda install -c conda-forge poppler")
    else:
        log_print(f"PDF-to-image conversion: Not available (install with: pip install pdf2image)")
        log_print(f"  PDF attachments will be embedded as regular attachments instead")
    
    if DOCX2PDF_AVAILABLE:
        log_print(f"DOCX-to-image conversion: Available (DOCX attachments will be converted to images)")
    else:
        log_print(f"DOCX-to-image conversion: Not available (install with: pip install docx2pdf)")
        log_print(f"  DOCX attachments will be embedded as regular attachments instead")
    
    if DOC_TO_PDF_AVAILABLE:
        log_print(f"DOC-to-image conversion: Available (DOC attachments will be converted to images)")
        log_print(f"  LibreOffice found: {LIBREOFFICE_PATH}")
    else:
        log_print(f"DOC-to-image conversion: Not available (install LibreOffice: https://www.libreoffice.org/download/)")
        log_print(f"  DOC attachments will be embedded as regular attachments instead")

# Walk through the directory tree
for root, dirs, files in os.walk(input_dir):
    # Filter for Markdown files
    md_files = [f for f in files if f.lower().endswith('.md')]
    
    for md_file in md_files:
        total_files += 1
        # Construct the full path to the Markdown file
        md_path = os.path.join(root, md_file)
        
        # Check if this file should be skipped
        # Check for skipped files
        if md_file in SKIP_FILES or any(skip_file in md_path for skip_file in SKIP_FILES):
            skipped_files += 1
            log_print(f"⊘ [{skipped_files}/{total_files}] Skipping {md_path} (in skip list)")
            continue
        
        # Check if file is in a skipped folder
        if any(skip_folder in md_path for skip_folder in SKIP_FOLDERS):
            skipped_files += 1
            log_print(f"⊘ [{skipped_files}/{total_files}] Skipping {md_path} (in skipped folder)")
            continue
        
        # Get the relative path from input_dir to preserve directory structure
        rel_path = os.path.relpath(root, input_dir)
        
        # Get the directory name (parent folder of the markdown file)
        dir_name = os.path.basename(root)
        
        # Get the base filename without extension
        file_base = os.path.splitext(md_file)[0]
        
        # Check if this directory only contains markdown files (likely a "note folder")
        # If so, save PDF one level up using the folder name to avoid creating a subfolder for each PDF
        all_files_are_md = all(f.lower().endswith('.md') for f in files)
        is_single_readme = len(md_files) == 1 and md_file.lower() == 'readme.md'
        
        # If folder only has markdown files, save PDF one level up using folder name
        if rel_path != '.' and all_files_are_md:
            # File is in a folder that only contains markdown - save PDF one level up
            parent_rel_path = os.path.dirname(rel_path) if os.path.dirname(rel_path) != '.' else ''
            if parent_rel_path:
                output_subdir = os.path.join(output_dir, parent_rel_path)
                os.makedirs(output_subdir, exist_ok=True)
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
            os.makedirs(output_subdir, exist_ok=True)
            # Use just the filename (directory structure provides uniqueness)
            if is_single_readme:
                pdf_filename = f"{dir_name}.pdf"
            else:
                pdf_filename = f"{file_base}.pdf"
        
        # Construct the output PDF path
        pdf_path = os.path.join(output_subdir, pdf_filename)
        
        # Skip if PDF already exists
        if os.path.exists(pdf_path):
            skipped_files += 1
            if skipped_files <= 10 or skipped_files % 100 == 0:  # Log first 10 and then every 100
                log_print(f"⊘ [{skipped_files}/{total_files}] Skipping {md_path} (PDF already exists: {pdf_filename})")
            # Show progress summary every 25 files
            if total_files % 25 == 0:
                log_print(f"  Progress: {converted_files} converted, {skipped_files} skipped, {error_files} errors, {timeout_files} timeouts")
            continue
        
        # Initialize temp directory variable (for cleanup in finally block)
        temp_img_dir = None
        try:
            # Read the markdown file content
            with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
                md_content = f.read()
            
            # Track file attachments to embed later (non-PDF/DOC/DOCX attachments)
            file_attachments = []
            # Track images being embedded
            images_found = []
            # Create temporary directory for PDF/DOC/DOCX-to-image conversions
            if PDF2IMAGE_AVAILABLE or DOCX2PDF_AVAILABLE:
                temp_img_dir = tempfile.mkdtemp(prefix='doc_images_')
            
            # Verify and validate file paths for images and attachments
            # Since we change to the markdown file's directory, relative paths should work
            # But we need to verify files exist and handle cross-directory references
            
            # First, handle image references: ![alt](path/to/image.ext)
            def resolve_image_path(match):
                full_match = match.group(0)
                alt_text = match.group(1) if match.group(1) else ''
                img_path = match.group(2)
                
                # Skip if it's already a URL
                if (img_path.startswith('http://') or 
                    img_path.startswith('https://') or 
                    img_path.startswith('file://')):
                    return full_match
                
                # Resolve path relative to markdown file's directory
                md_dir = os.path.dirname(md_path)
                resolved_path = os.path.normpath(os.path.join(md_dir, img_path))
                
                # Verify the image file exists
                if not os.path.exists(resolved_path):
                    return full_match  # Return original if image doesn't exist
                
                # Track and log the image
                img_filename = os.path.basename(resolved_path)
                if resolved_path not in images_found:
                    images_found.append(resolved_path)
                    try:
                        img_size = os.path.getsize(resolved_path)
                        log_print(f"  → Image: {img_filename} ({img_size / 1024:.1f}KB)")
                    except:
                        log_print(f"  → Image: {img_filename}")
                
                # For weasyprint (used by md2pdf), we need file:// URLs for Windows paths
                # Convert to absolute path and then to file:// URL
                abs_img_path = os.path.abspath(resolved_path).replace('\\', '/')
                # Windows paths need file:/// (three slashes) format
                file_url = f'file:///{abs_img_path}'
                
                # Return the markdown with file:// URL
                return f'![{alt_text}]({file_url})'
            
            # Handle regular file links: [text](path/to/file.ext)
            def resolve_file_path(match):
                full_match = match.group(0)
                link_text = match.group(1) if match.group(1) else ''
                file_path = match.group(2)
                
                # Skip if it's already a URL, email link, anchor link, or other non-file link
                if (file_path.startswith('http://') or 
                    file_path.startswith('https://') or 
                    file_path.startswith('file://') or
                    file_path.startswith('mailto:') or
                    file_path.startswith('#') or
                    '://' in file_path):  # Catch any other protocol
                    return full_match
                
                # Skip if it doesn't look like a file path (no extension and no common file patterns)
                # This avoids checking things like "mailto:email@domain.com" as files
                if not ('.' in file_path or 
                       os.path.sep in file_path or 
                       os.path.altsep in file_path if os.path.altsep else False):
                    return full_match
                
                # Resolve path relative to markdown file's directory
                md_dir = os.path.dirname(md_path)
                resolved_path = os.path.normpath(os.path.join(md_dir, file_path))
                
                # Only check file existence if it looks like a real file path
                # Skip the check for very short paths or paths that look like URLs/emails
                if len(file_path) < 3 or '@' in file_path:
                    return full_match
                
                # Verify the file exists (skip logging to speed up - only check existence)
                if not os.path.exists(resolved_path):
                    return full_match  # Return original if file doesn't exist
                
                # Track file attachments for embedding (PDF, DOC, DOCX, TXT, audio, video, etc.)
                file_ext = os.path.splitext(resolved_path)[1].lower()
                filename = os.path.basename(resolved_path)
                
                # Special handling for PDF attachments - convert to images
                if file_ext == '.pdf':
                    # Check if this PDF should be skipped
                    if filename in SKIP_ATTACHMENTS or any(skip in resolved_path for skip in SKIP_ATTACHMENTS):
                        log_print(f"  Skipping problematic PDF attachment: {filename}")
                        return f'{link_text} (attachment skipped - known problematic file)'
                    
                    # Convert PDF to images if pdf2image is available
                    if PDF2IMAGE_AVAILABLE and temp_img_dir:
                        try:
                            log_print(f"  → Converting PDF attachment to images: {filename}")
                            # Convert PDF pages to images
                            convert_kwargs = {'dpi': 150, 'output_folder': temp_img_dir}
                            if POPPLER_PATH:
                                convert_kwargs['poppler_path'] = POPPLER_PATH
                            images = convert_from_path(resolved_path, **convert_kwargs)
                            
                            if images:
                                # Build markdown with embedded images
                                image_markdown_parts = []
                                if link_text:
                                    image_markdown_parts.append(f"**{link_text}** (converted from PDF):\n\n")
                                
                                for i, img in enumerate(images):
                                    # Save image to temp directory
                                    img_filename = f"{os.path.splitext(filename)[0]}_page_{i+1}.png"
                                    img_path = os.path.join(temp_img_dir, img_filename)
                                    img.save(img_path, 'PNG')
                                    
                                    # Convert to absolute path for file:// URL
                                    abs_img_path = os.path.abspath(img_path).replace('\\', '/')
                                    file_url = f'file:///{abs_img_path}'
                                    
                                    # Add image to markdown
                                    if len(images) > 1:
                                        image_markdown_parts.append(f"![Page {i+1} of {filename}]({file_url})\n\n")
                                    else:
                                        image_markdown_parts.append(f"![{filename}]({file_url})\n\n")
                                
                                log_print(f"  ✓ Converted PDF to {len(images)} image(s) and embedded in document")
                                return ''.join(image_markdown_parts)
                            else:
                                log_print(f"  Warning: Could not convert PDF {filename} to images, will embed as attachment")
                        except (PDFInfoNotInstalledError, Exception) as e:
                            # Check if it's a poppler error
                            error_msg = str(e).lower()
                            if 'poppler' in error_msg or 'pdfinfo' in error_msg or 'page count' in error_msg or isinstance(e, PDFInfoNotInstalledError):
                                log_print(f"  Warning: Poppler not installed. Cannot convert PDF {filename} to images.")
                                log_print(f"    Install poppler: https://github.com/oschwartz10612/poppler-windows/releases/")
                                log_print(f"    Or use: conda install -c conda-forge poppler")
                                log_print(f"    Will embed PDF as regular attachment instead")
                            else:
                                log_print(f"  Warning: Error converting PDF {filename} to images: {str(e)}")
                                log_print(f"  Will embed as regular attachment instead")
                    
                    # If conversion failed or not available, treat as regular attachment
                    file_attachments.append((resolved_path, link_text))
                    log_print(f"  → Attachment: {filename}")
                    return f'{link_text} (attachment will be embedded)'
                
                # Special handling for DOC/DOCX attachments - convert to images
                if file_ext in ['.doc', '.docx']:
                    # Check if this attachment should be skipped
                    if filename in SKIP_ATTACHMENTS or any(skip in resolved_path for skip in SKIP_ATTACHMENTS):
                        log_print(f"  Skipping problematic DOC/DOCX attachment: {filename}")
                        return f'{link_text} (attachment skipped - known problematic file)'
                    
                    # Convert DOC/DOCX to images if conversion is available
                    if temp_img_dir:
                        try:
                            log_print(f"  → Converting DOC/DOCX attachment to images: {filename}")
                            # Convert DOC/DOCX to images via PDF
                            images = convert_doc_to_images(resolved_path, temp_img_dir)
                            
                            if images:
                                # Build markdown with embedded images
                                image_markdown_parts = []
                                if link_text:
                                    image_markdown_parts.append(f"**{link_text}** (converted from {file_ext.upper()}):\n\n")
                                
                                for i, img in enumerate(images):
                                    # Save image to temp directory
                                    img_filename = f"{os.path.splitext(filename)[0]}_page_{i+1}.png"
                                    img_path = os.path.join(temp_img_dir, img_filename)
                                    img.save(img_path, 'PNG')
                                    
                                    # Convert to absolute path for file:// URL
                                    abs_img_path = os.path.abspath(img_path).replace('\\', '/')
                                    file_url = f'file:///{abs_img_path}'
                                    
                                    # Add image to markdown
                                    if len(images) > 1:
                                        image_markdown_parts.append(f"![Page {i+1} of {filename}]({file_url})\n\n")
                                    else:
                                        image_markdown_parts.append(f"![{filename}]({file_url})\n\n")
                                
                                log_print(f"  ✓ Converted {file_ext.upper()} to {len(images)} image(s) and embedded in document")
                                return ''.join(image_markdown_parts)
                            else:
                                log_print(f"  Warning: Could not convert {file_ext.upper()} {filename} to images, will embed as attachment")
                        except Exception as e:
                            log_print(f"  Warning: Error converting {file_ext.upper()} {filename} to images: {str(e)}")
                            log_print(f"  Will embed as regular attachment instead")
                    
                    # If conversion failed or not available, treat as regular attachment
                    file_attachments.append((resolved_path, link_text))
                    log_print(f"  → Attachment: {filename}")
                    return f'{link_text} (attachment will be embedded)'
                
                # Audio formats
                audio_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus']
                # Video formats
                video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpg', '.mpeg']
                # Document formats (excluding PDF and DOC/DOCX, already handled above)
                doc_extensions = ['.txt']
                # All supported attachment formats
                if file_ext in doc_extensions + audio_extensions + video_extensions:
                    # Check if this attachment should be skipped
                    if filename in SKIP_ATTACHMENTS or any(skip in resolved_path for skip in SKIP_ATTACHMENTS):
                        log_print(f"  Skipping problematic attachment: {filename}")
                        return f'{link_text} (attachment skipped - known problematic file)'
                    
                    file_attachments.append((resolved_path, link_text))
                    log_print(f"  → Attachment: {filename}")
                    # Remove the link from markdown since we're embedding it as an attachment
                    # Just show the link text without a link
                    return f'{link_text} (attachment will be embedded)'
                
                # For other file links, use absolute file:// URL so they work in PDF
                # Convert Windows path to file:// URL
                abs_file_path = os.path.abspath(resolved_path).replace('\\', '/')
                file_url = f'file:///{abs_file_path}'
                
                # Return the updated markdown with file:// URL
                return f'[{link_text}]({file_url})'
            
            # Replace image references with absolute paths
            # Matches: ![alt text](relative/path)
            md_content = re.sub(
                r'!\[([^\]]*)\]\(([^)]+)\)',
                resolve_image_path,
                md_content
            )
            
            # Replace file link references with absolute paths
            # Matches: [text](relative/path) - but not images (already handled above)
            # We need to be careful not to match image syntax again
            md_content = re.sub(
                r'(?<!\!)\[([^\]]*)\]\(([^)]+)\)',
                resolve_file_path,
                md_content
            )
            
            # Individual attachments will be logged as they're processed
            
            # Convert the Markdown file to PDF
            # Note: This will overwrite existing PDF files if they already exist
            # Use subprocess with timeout so we can actually kill stuck processes on Windows
            original_cwd = os.getcwd()
            md_dir = os.path.dirname(md_path)
            
            # Check if PDF already exists (for logging purposes)
            if os.path.exists(pdf_path):
                log_print(f"  Overwriting existing PDF: {pdf_filename}")
            
            # Create a temporary Python script for the conversion
            # This allows us to run it in a subprocess that can be killed
            temp_script = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8')
            temp_script.write(CONVERTER_SCRIPT)
            temp_script.close()
            
            try:
                # Prepare arguments as JSON
                args_data = {
                    'pdf_path': pdf_path,
                    'md_content': md_content,
                    'css_file': css_file if os.path.exists(css_file) else None,
                    'md_dir': md_dir
                }
                
                # Start conversion in a subprocess
                start_time = time.time()
                
                # Run the conversion script with timeout
                process = subprocess.Popen(
                    [sys.executable, temp_script.name],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=md_dir
                )
                
                # Send arguments via stdin
                json.dump(args_data, process.stdin)
                process.stdin.close()
                
                # Wait for process with timeout
                try:
                    process.wait(timeout=CONVERSION_TIMEOUT)
                    elapsed = time.time() - start_time
                except subprocess.TimeoutExpired:
                    # Process timed out - kill it
                    elapsed = time.time() - start_time
                    timeout_files += 1
                    skipped_files += 1
                    log_print(f"⊘ [{timeout_files}/{total_files}] TIMEOUT: Skipping {md_path} (waited {elapsed:.1f}s, limit: {CONVERSION_TIMEOUT}s)")
                    # Show progress summary
                    log_print(f"  Progress: {converted_files} converted, {skipped_files} skipped, {error_files} errors, {timeout_files} timeouts")
                    process.kill()
                    process.wait()  # Wait for process to be killed
                    # Clean up any partial PDF file
                    if os.path.exists(pdf_path):
                        try:
                            os.remove(pdf_path)
                        except:
                            pass
                    continue
                
                # Check if process succeeded
                if process.returncode != 0:
                    stderr_output = process.stderr.read() if process.stderr else "Unknown error"
                    raise Exception(f"Conversion failed: {stderr_output}")
                
                # Verify PDF was created
                if not os.path.exists(pdf_path):
                    raise Exception("PDF was not created after conversion")
                
                # Only log conversion time if it's slow (over 5 seconds) or close to timeout
                if elapsed > CONVERSION_TIMEOUT * 0.9:
                    log_print(f"  Warning: Conversion took {elapsed:.1f}s (close to {CONVERSION_TIMEOUT}s limit)")
                elif elapsed > 5.0:
                    log_print(f"  Conversion took {elapsed:.1f}s")
                    
            finally:
                # Clean up temporary script
                try:
                    os.unlink(temp_script.name)
                except:
                    pass
            
            # Embed file attachments using pikepdf (preferred) or pypdf
            if file_attachments and os.path.exists(pdf_path):
                if PIKEPDF_AVAILABLE:
                    try:
                        # Check file size before opening - skip very large files that might cause issues
                        pdf_size = os.path.getsize(pdf_path)
                        if pdf_size > 100 * 1024 * 1024:  # 100MB limit
                            log_print(f"  Warning: PDF is very large ({pdf_size / 1024 / 1024:.1f}MB), skipping attachment embedding to avoid hangs")
                        else:
                            # Open the PDF with pikepdf
                            pdf = Pdf.open(pdf_path)
                            
                            attached_count = 0
                            for file_attach_path, attach_name in file_attachments:
                                if os.path.exists(file_attach_path):
                                    try:
                                        # Check attachment file size - skip very large files
                                        attach_size = os.path.getsize(file_attach_path)
                                        if attach_size > 50 * 1024 * 1024:  # 50MB limit per attachment
                                            log_print(f"  Warning: Skipping large attachment {os.path.basename(file_attach_path)} ({attach_size / 1024 / 1024:.1f}MB)")
                                            continue
                                        
                                        # Always show attachment being embedded
                                        log_print(f"  → Embedding: {os.path.basename(file_attach_path)} ({attach_size / 1024:.1f}KB)")
                                        
                                        # Create attachment from file
                                        filename = os.path.basename(file_attach_path)
                                        
                                        # Ensure unique filename if duplicates exist
                                        original_filename = filename
                                        counter = 1
                                        while filename in pdf.attachments:
                                            name, ext = os.path.splitext(original_filename)
                                            filename = f"{name}_{counter}{ext}"
                                            counter += 1
                                        
                                        # Create attachment from file data
                                        # Read the file content first to ensure it's accessible
                                        with open(file_attach_path, 'rb') as f:
                                            attachment_data = f.read()
                                        
                                        # Create attachment from file path (pikepdf will handle the data)
                                        attachment = AttachedFileSpec.from_filepath(pdf, Path(file_attach_path))
                                        
                                        # Ensure the attachment has proper metadata
                                        attachment.filename = filename
                                        
                                        # Add attachment to the PDF using the filename as key
                                        # Use explicit assignment to ensure it's added to the dictionary
                                        pdf.attachments[filename] = attachment
                                        
                                        # Force a check that it was actually added
                                        if filename not in pdf.attachments:
                                            raise Exception(f"Failed to add attachment {filename} to PDF attachments dictionary")
                                        
                                        # Verify the attachment object is valid
                                        if pdf.attachments[filename] is None:
                                            raise Exception(f"Attachment {filename} was set to None")
                                        
                                        # Verify it was added
                                        if filename in pdf.attachments:
                                            attached_count += 1
                                            # Always show attachment success
                                            if filename != original_filename:
                                                log_print(f"  ✓ Embedded: {original_filename} as {filename}")
                                            else:
                                                log_print(f"  ✓ Embedded: {filename}")
                                        else:
                                            log_print(f"  Warning: Attachment {filename} was not added to attachments dictionary")
                                            log_print(f"  Current attachments: {list(pdf.attachments.keys())}")
                                    except Exception as e:
                                        log_print(f"  Warning: Could not attach file {os.path.basename(file_attach_path)}: {str(e)}")
                                        import traceback
                                        log_print(f"  Error details: {traceback.format_exc()}")
                                else:
                                    log_print(f"  Warning: Attachment file not found: {file_attach_path}")
                        
                            if attached_count > 0:
                                # Verify attachments are in dictionary before saving
                                actual_attachments_before_save = list(pdf.attachments.keys())
                                actual_count_before_save = len(pdf.attachments)
                                
                                if actual_count_before_save != attached_count:
                                    log_print(f"  WARNING: Mismatch! Expected {attached_count} attachments, but dictionary has {actual_count_before_save}")
                                
                                # Only log save details for first few files or if there are issues
                                if converted_files < 10:
                                    log_print(f"  Saving PDF with {attached_count} attachment(s)...")
                                    # Save to a temporary file first, then replace the original
                                    import tempfile
                                    temp_path = pdf_path + '.tmp'
                                    try:
                                        # Double-check attachments are still in dictionary right before save
                                        final_check = list(pdf.attachments.keys())
                                        if len(final_check) != actual_count_before_save:
                                            log_print(f"  WARNING: Attachment count changed before save! Had {actual_count_before_save}, now has {len(final_check)}")
                                        
                                        # Try saving without PDF/A first (more reliable for multiple attachments)
                                        # PDF/A preservation can sometimes cause issues with attachments
                                        try:
                                            pdf.save(temp_path, preserve_pdfa=False)
                                        except Exception as save_error:
                                            # If that fails, try with PDF/A
                                            log_print(f"  Warning: Save without PDF/A failed: {str(save_error)}")
                                            log_print(f"  Retrying save with PDF/A preservation...")
                                            pdf.save(temp_path, preserve_pdfa=True)
                                        
                                        # Verify attachments were saved before closing
                                        verify_pdf = Pdf.open(temp_path)
                                        saved_attachment_count = len(verify_pdf.attachments)
                                        saved_attachment_names = list(verify_pdf.attachments.keys())
                                        verify_pdf.close()
                                        
                                        # Only log verification details for first few files or if there are issues
                                        if converted_files < 10 or saved_attachment_count != attached_count:
                                            log_print(f"  Verified: {saved_attachment_count} attachment(s) saved: {saved_attachment_names}")
                                        
                                        pdf.close()
                                        
                                        if saved_attachment_count > 0:
                                            # Replace original with the new file
                                            os.replace(temp_path, pdf_path)
                                            # Only log success for first few files or if there were issues
                                            if converted_files < 10 or saved_attachment_count != attached_count:
                                                log_print(f"  ✓ Embedded {saved_attachment_count} file attachment(s)")
                                        else:
                                            # Attachments weren't saved, keep original
                                            os.remove(temp_path)
                                            log_print(f"  ERROR: Attachments were not saved to PDF!")
                                            log_print(f"  Expected: {attached_count} attachments")
                                            log_print(f"  Found in saved PDF: {saved_attachment_count} attachments")
                                            log_print(f"  Attachment dictionary before save had: {actual_attachments_before_save}")
                                            log_print(f"  This suggests attachments were lost during the save operation")
                                    except Exception as e:
                                        pdf.close()
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                        log_print(f"  Error saving PDF with attachments: {str(e)}")
                                        import traceback
                                        log_print(f"  Error details: {traceback.format_exc()}")
                                        log_print(f"  Continuing without attachments for this file...")
                            else:
                                pdf.close()
                                log_print(f"  No attachments were successfully added")
                    except Exception as e:
                        log_print(f"  Warning: Could not embed PDF attachments with pikepdf: {str(e)}")
                        import traceback
                        log_print(f"  Traceback: {traceback.format_exc()}")
                elif PYPDF_AVAILABLE:
                    try:
                        from pypdf import PdfWriter, PdfReader
                        # Open the generated PDF
                        writer = PdfWriter()
                        reader = PdfReader(pdf_path)
                        
                        # Copy all pages from the generated PDF
                        for page in reader.pages:
                            writer.add_page(page)
                        
                        # Attach files
                        attached_count = 0
                        for file_attach_path, attach_name in file_attachments:
                            if os.path.exists(file_attach_path):
                                try:
                                    # Read the attachment file
                                    with open(file_attach_path, 'rb') as attach_file:
                                        attachment_data = attach_file.read()
                                    
                                    filename = os.path.basename(file_attach_path)
                                    attach_size = len(attachment_data)
                                    # Always show attachment being embedded
                                    log_print(f"  → Embedding: {filename} ({attach_size / 1024:.1f}KB)")
                                    
                                    # Try different API signatures
                                    try:
                                        writer.add_attachment(filename, attachment_data)
                                    except TypeError:
                                        try:
                                            writer.add_attachment(filename=filename, data=attachment_data)
                                        except TypeError:
                                            writer.add_attachment(file_attach_path)
                                    
                                    attached_count += 1
                                    log_print(f"  ✓ Embedded: {filename}")
                                except Exception as e:
                                    log_print(f"  Warning: Could not attach file {file_attach_path}: {str(e)}")
                            else:
                                log_print(f"  Warning: Attachment file not found: {file_attach_path}")
                        
                        if attached_count > 0:
                            # Write the PDF with attachments
                            with open(pdf_path, 'wb') as output_file:
                                writer.write(output_file)
                    except Exception as e:
                        log_print(f"  Warning: Could not embed file attachments with pypdf: {str(e)}")
                        import traceback
                        log_print(f"  Traceback: {traceback.format_exc()}")
                else:
                    log_print(f"  Warning: No PDF library available for attachments. Install with: pip install pikepdf (recommended) or pip install pypdf")
            converted_files += 1
            # Always show filename being converted, with progress count and folder structure
            # Get relative path from output_dir to show folder structure
            pdf_rel_path = os.path.relpath(pdf_path, output_dir)
            log_print(f"✓ [{converted_files}/{total_files}] {pdf_rel_path}")
            # Show progress summary every 25 files
            if total_files % 25 == 0:
                log_print(f"  Progress: {converted_files} converted, {skipped_files} skipped, {error_files} errors, {timeout_files} timeouts")
        except Exception as e:
            error_files += 1
            log_print(f"✗ [{error_files}/{total_files}] Error converting {md_path}: {str(e)}")
            # Show progress summary every 25 files
            if total_files % 25 == 0:
                log_print(f"  Progress: {converted_files} converted, {skipped_files} skipped, {error_files} errors, {timeout_files} timeouts")
        finally:
            # Clean up temporary image directory
            if temp_img_dir and os.path.exists(temp_img_dir):
                try:
                    import shutil
                    shutil.rmtree(temp_img_dir)
                except Exception as e:
                    log_print(f"  Warning: Could not clean up temp image directory: {e}")

log_print(f"\n{'='*60}")
log_print(f"Conversion complete!")
log_print(f"Total files found: {total_files}")
log_print(f"Successfully converted: {converted_files}")
log_print(f"Skipped: {skipped_files}")
log_print(f"Timeouts (>300s): {timeout_files}")
log_print(f"Errors: {error_files}")
log_print(f"{'='*60}")

