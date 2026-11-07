"""Converts one PDF to Markdown with Docling and prints it to stdout.

Run by ai_api._docling_pdf_to_text with the Python of a SEPARATE virtualenv
(enable_ai_assistant.docling_python). Docling pulls in PyTorch, newer pydantic,
numpy and pillow, so it is kept out of the bench environment on purpose.
Does not import frappe.

    <docling-env>/bin/python docling_convert.py /path/to/file.pdf
"""
import sys


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: docling_convert.py <file.pdf>\n")
        return 2

    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(sys.argv[1])
    markdown = result.document.export_to_markdown()
    sys.stdout.buffer.write(markdown.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
