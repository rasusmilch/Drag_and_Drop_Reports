#!/usr/bin/env python3
"""Convert text files into paginated PDFs and offer printing via Ghostscript.

Features:
- Command-line mode:
  - Accepts one or more text files as arguments.
  - Converts each to a PDF (same directory).
  - Optionally invokes Ghostscript to show a print dialog.
- GUI mode (default when no text files are provided):
  - Drag-and-drop "drop box" window.
  - Drag .txt files from Windows Explorer into the window.
  - If more than one file is dropped, a confirmation dialog appears.
  - Each file is converted to PDF and optionally printed.

Dependencies:
- reportlab       (pip install reportlab)
- tkinterdnd2     (pip install tkinterdnd2)
- Ghostscript     (must be installed and on PATH for printing)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


FORMFEED_MARKER = "FORMFEED"
POINTS_PER_INCH = 72.0


@dataclass
class PageMargins:
    """Simple container for page margins in inches."""

    top_inch: float
    bottom_inch: float
    left_inch: float
    right_inch: float


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convert text files into paginated PDFs and optionally invoke "
            "Ghostscript to show a print dialog. If no files are specified, "
            "a drag-and-drop GUI is launched."
        )
    )
    parser.add_argument(
        "text_files",
        type=Path,
        nargs="*",
        help="Path(s) to input .txt file(s). If omitted, GUI mode is used.",
    )
    parser.add_argument(
        "--no-print",
        action="store_true",
        help="Do not invoke Ghostscript to show a print dialog.",
    )
    parser.add_argument(
        "--gs-binary",
        type=str,
        default=None,
        help=(
            "Ghostscript executable name or full path "
            "(default: auto-detect, e.g., gswin64c/gswin32c/gs)."
        ),
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Force GUI drag-and-drop mode even if files are specified.",
    )
    # Margin controls (in inches).
    parser.add_argument(
        "--margin-top",
        type=float,
        default=0.5,
        help="Top margin in inches (default: 0.5).",
    )
    parser.add_argument(
        "--margin-bottom",
        type=float,
        default=0.75,
        help="Bottom margin in inches (default: 0.75).",
    )
    parser.add_argument(
        "--margin-left",
        type=float,
        default=0.5,
        help="Left margin in inches (default: 0.5).",
    )
    parser.add_argument(
        "--margin-right",
        type=float,
        default=0.5,
        help="Right margin in inches (default: 0.5).",
    )
    return parser.parse_args()


def read_text_file(text_path: Path) -> List[str]:
    """Read the text file and return lines.

    Args:
        text_path: Path to the text file.

    Returns:
        List of lines from the file, without trailing newline characters.

    Raises:
        FileNotFoundError: If the input file does not exist.
    """
    if not text_path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {text_path}")

    return text_path.read_text(encoding="utf-8", errors="replace").splitlines()


def preprocess_lines(lines: List[str]) -> List[str]:
    """Remove formfeed markers and following footer lines.

    Behavior:
    - Any line containing FORMFEED_MARKER is removed.
    - Any line containing PAGE and OF is removed (Page X of Y)
    - Remaining lines are returned as a flat list, preserving order.

    Args:
        lines: All lines from the input text file.

    Returns:
        Cleaned list of lines with markers and footer lines removed.
    """
    cleaned_lines: List[str] = []
    skip_next_footer_line = False

    for line in lines:
        if FORMFEED_MARKER in line:
            continue

        if "PAGE" and "OF" in line.upper():
            continue

        cleaned_lines.append(line)

    return cleaned_lines


def render_pdf_from_pages(
    pages: List[List[str]],
    pdf_path: Path,
    margins: PageMargins,
) -> None:
    """Render the given logical pages into a PDF file.

    Logical pages are split into physical pages as needed
    to respect the configured margins and line height.

    In this usage, we pass a single logical page containing
    the entire document, so pagination is purely by page overflow.

    Args:
        pages: Logical pages to render; each page is a list of text lines.
        pdf_path: Output path for the PDF file.
        margins: Margins (in inches) for the page layout.
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    page_width, page_height = letter

    top_margin_pts = margins.top_inch * POINTS_PER_INCH
    bottom_margin_pts = margins.bottom_inch * POINTS_PER_INCH
    left_margin_pts = margins.left_inch * POINTS_PER_INCH
    right_margin_pts = margins.right_inch * POINTS_PER_INCH

    usable_height = page_height - top_margin_pts - bottom_margin_pts
    if usable_height <= 0:
        raise ValueError("Margins are too large; no vertical space remains for text.")

    font_name = "Courier"
    font_size = 10.0
    line_height = font_size * 1.2  # Simple leading factor.

    max_lines_per_page = int(usable_height // line_height)
    if max_lines_per_page <= 0:
        raise ValueError("Margins are too large; not enough room for even one line.")

    # Expand logical pages into physical pages to respect max_lines_per_page.
    physical_pages: list[list[str]] = []

    for logical_page_lines in pages:
        if not logical_page_lines:
            physical_pages.append([])
            continue

        start_index = 0
        while start_index < len(logical_page_lines):
            end_index = start_index + max_lines_per_page
            physical_pages.append(logical_page_lines[start_index:end_index])
            start_index = end_index

    if not physical_pages:
        physical_pages.append([])

    total_pages = len(physical_pages)

    pdf_canvas = canvas.Canvas(str(pdf_path), pagesize=letter)

    for page_index, page_lines in enumerate(physical_pages, start=1):
        text_object = pdf_canvas.beginText()
        text_object.setTextOrigin(left_margin_pts, page_height - top_margin_pts)
        text_object.setFont(font_name, font_size)
        text_object.setLeading(line_height)

        for line in page_lines:
            text_object.textLine(line)

        pdf_canvas.drawText(text_object)

        # Footer with centered "Page X of Y".
        footer_font_size = 10.0
        pdf_canvas.setFont("Helvetica", footer_font_size)

        # Place footer within the bottom margin; bias to middle of margin.
        footer_y = max(bottom_margin_pts / 2.0, footer_font_size * 1.5)

        footer_text = f"Page {page_index} of {total_pages}"
        pdf_canvas.drawCentredString(page_width / 2.0, footer_y, footer_text)

        pdf_canvas.showPage()

    pdf_canvas.save()


def detect_ghostscript_binary(custom_binary: Optional[str] = None) -> Optional[str]:
    """Detect a Ghostscript executable on the system.

    Args:
        custom_binary: Optional explicit Ghostscript executable name or path.

    Returns:
        The detected Ghostscript executable name or path, or None if not found.
    """
    if custom_binary:
        if shutil.which(custom_binary) is not None:
            return custom_binary
        return None

    candidate_binaries = [
        "gswin64c",
        "gswin32c",
        "gs",
        "ghostscript",
    ]

    for candidate in candidate_binaries:
        if shutil.which(candidate) is not None:
            return candidate

    return None


def open_print_dialog_with_ghostscript(pdf_path: Path, gs_binary: str) -> None:
    """Invoke Ghostscript to open a print dialog for the given PDF.

    On Windows, this uses the `mswinpr2` device. If no printer is specified via
    OutputFile, Ghostscript will open the standard Windows print dialog.

    Args:
        pdf_path: Path to the PDF file to print.
        gs_binary: Ghostscript executable name or path.
    """
    command: list[str] = [gs_binary, "-dBATCH", "-dNOPAUSE"]

    if sys.platform.startswith("win"):
        command.extend(["-sDEVICE=mswinpr2"])

    command.append(str(pdf_path))

    try:
        subprocess.run(command, check=False)
    except Exception as error:  # pylint: disable=broad-except
        print(
            f"Warning: Failed to invoke Ghostscript print dialog: {error}",
            file=sys.stderr,
        )


def process_text_file(
    text_path: Path,
    no_print: bool,
    gs_binary: Optional[str],
    margins: PageMargins,
) -> None:
    """Convert a single text file to PDF and optionally invoke Ghostscript.

    Args:
        text_path: Path to the input text file.
        no_print: If True, skip invoking Ghostscript.
        gs_binary: Optional explicit Ghostscript binary name or path.
        margins: Margins (in inches) for the page layout.
    """
    try:
        lines = read_text_file(text_path)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return

    cleaned_lines = preprocess_lines(lines)

    # Treat whole document as one logical page; pagination is by overflow only.
    pages: list[list[str]] = [cleaned_lines]

    pdf_path = text_path.with_suffix(".pdf")

    print(f"Rendering PDF: {pdf_path}")
    render_pdf_from_pages(pages, pdf_path, margins)
    print("PDF rendering complete.")

    if no_print:
        return

    effective_gs_binary = detect_ghostscript_binary(gs_binary)
    if effective_gs_binary is None:
        print(
            "Warning: Ghostscript not found on PATH. "
            f"Skipping print dialog for {pdf_path}.",
            file=sys.stderr,
        )
        return

    print(f"Invoking Ghostscript print dialog with: {effective_gs_binary}")
    open_print_dialog_with_ghostscript(pdf_path, effective_gs_binary)


def run_gui(
    default_no_print: bool,
    default_gs_binary: Optional[str],
    margins: PageMargins,
) -> None:
    """Run the drag-and-drop GUI for converting text files to PDF.

    Args:
        default_no_print: Default setting for skipping Ghostscript printing.
        default_gs_binary: Optional Ghostscript binary name or path.
        margins: Margins (in inches) for the page layout.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError as error:  # pylint: disable=broad-except
        print(
            "Error: GUI mode requires tkinter and tkinterdnd2.\n"
            "Install with: pip install tkinterdnd2\n"
            f"Details: {error}",
            file=sys.stderr,
        )
        sys.exit(1)

    root = TkinterDnD.Tk()
    root.title("Text to PDF Converter - Drop Box")
    root.geometry("600x300")

    instruction_text = (
        "Drag and drop .txt files here.\n\n"
        "Each file will be converted to a PDF alongside the original.\n"
        "If printing is enabled, a Ghostscript print dialog will be shown."
    )

    drop_label = tk.Label(
        root,
        text=instruction_text,
        relief="groove",
        bd=2,
        padx=20,
        pady=20,
        justify="center",
        anchor="center",
        wraplength=550,
    )
    drop_label.pack(expand=True, fill="both", padx=20, pady=20)

    drop_label.drop_target_register(DND_FILES)

    def handle_drop(event: object) -> None:
        """Handle files dropped onto the drop area."""
        data = event.data  # type: ignore[attr-defined]
        paths_str_list = drop_label.tk.splitlist(data)

        text_paths: list[Path] = []
        for item in paths_str_list:
            candidate = Path(item)
            if candidate.is_file() and candidate.suffix.lower() == ".txt":
                text_paths.append(candidate)

        if not text_paths:
            messagebox.showinfo(
                "No text files",
                "No .txt files were dropped.",
                parent=root,
            )
            return

        if len(text_paths) > 1:
            confirm = messagebox.askyesno(
                "Confirm multiple files",
                (
                    f"You dropped {len(text_paths)} files.\n"
                    "Convert and (optionally) print all of them?"
                ),
                parent=root,
            )
            if not confirm:
                return

        for text_path in text_paths:
            process_text_file(
                text_path=text_path,
                no_print=default_no_print,
                gs_binary=default_gs_binary,
                margins=margins,
            )

        messagebox.showinfo(
            "Done",
            "Conversion complete.",
            parent=root,
        )

    drop_label.dnd_bind("<<Drop>>", handle_drop)

    root.mainloop()


def main() -> None:
    """Main entry point."""
    arguments = parse_arguments()

    margins = PageMargins(
        top_inch=arguments.margin_top,
        bottom_inch=arguments.margin_bottom,
        left_inch=arguments.margin_left,
        right_inch=arguments.margin_right,
    )

    if arguments.gui or not arguments.text_files:
        run_gui(
            default_no_print=arguments.no_print,
            default_gs_binary=arguments.gs_binary,
            margins=margins,
        )
        return

    for text_path in arguments.text_files:
        process_text_file(
            text_path=text_path,
            no_print=arguments.no_print,
            gs_binary=arguments.gs_binary,
            margins=margins,
        )


if __name__ == "__main__":
    main()
