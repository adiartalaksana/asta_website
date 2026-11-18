from __future__ import annotations

import csv
import json
from pathlib import Path
from decimal import Decimal
from io import BytesIO, StringIO

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from xml.sax.saxutils import escape
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image as PdfImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


HEADERS = ("Tanggal", "Referensi", "Keterangan", "Dana Masuk", "Dana Keluar", "Saldo", "Akun")


def _rows(statement):
    return [
        (
            timezone.localtime(line.transaction_at).strftime("%d/%m/%Y %H:%M"),
            line.reference,
            line.description,
            f"{line.debit:.4f}" if line.debit else "",
            f"{line.credit:.4f}" if line.credit else "",
            f"{line.balance:.4f}",
            line.suggested_account.name if line.suggested_account else "",
        )
        for line in statement.lines.select_related("suggested_account").all()
    ]


def _response(content, content_type, filename):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _csv_export(rows, filename):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return _response(output.getvalue(), "text/csv; charset=utf-8", filename)


def _xlsx_export(rows, filename):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Mutasi"
    sheet.append(HEADERS)
    for row in rows:
        # Store amounts as numbers so they can be summed in Excel.
        sheet.append([Decimal(value) if index in (3, 4, 5) and value else value for index, value in enumerate(row)])
    for column, width in {"A": 20, "B": 18, "C": 52, "D": 18, "E": 18, "F": 18, "G": 28}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    stream = BytesIO()
    workbook.save(stream)
    return _response(
        stream.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename,
    )


LOGO_DIR = Path(settings.BASE_DIR) / "static" / "img"
LOGO_DARK = LOGO_DIR / "asta-asa-abadi-logo-dark.png"
LOGO_WHITE = LOGO_DIR / "asta-asa-abadi-logo-white.png"


def _pdf_header():
    title = Paragraph("<b>REKENING KORAN</b>", ParagraphStyle("title", fontName="Helvetica", fontSize=11, leading=14, textColor=colors.HexColor("#1D1D1F")))
    if not LOGO_DARK.exists():
        return [title, Spacer(1, 6 * mm)]
    logo = PdfImage(str(LOGO_DARK), width=24 * mm, height=24 * mm * 836 / 1108)
    header = Table([[logo, title]], colWidths=[30 * mm, None])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return [header, Spacer(1, 6 * mm)]


def _pdf_export(rows, filename):
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    cell_style = ParagraphStyle("cell", fontName="Helvetica", fontSize=7, leading=9)
    # Wrap free-text columns so long descriptions stay inside their cells.
    wrapped_rows = [
        [Paragraph(escape(str(value)), cell_style) if index in (2, 6) else value for index, value in enumerate(row)]
        for row in rows
    ]
    table = Table([HEADERS, *wrapped_rows], repeatRows=1, colWidths=[26 * mm, 26 * mm, 72 * mm, 30 * mm, 30 * mm, 30 * mm, 42 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D1D1F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DCE4EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
    ]))
    document.build([*_pdf_header(), table])
    return _response(stream.getvalue(), "application/pdf", filename)


def _png_export(rows, filename):
    visible_rows = rows[:80]
    width, header_height, row_height = 1800, 90, 34
    image = Image.new("RGB", (width, header_height + row_height * (len(visible_rows) + 1)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    bold_font = ImageFont.load_default()
    column_x = (20, 230, 420, 1080, 1230, 1380, 1530)
    draw.rectangle((0, 0, width, header_height), fill="#1D1D1F")
    if LOGO_WHITE.exists():
        with Image.open(LOGO_WHITE) as logo:
            logo = logo.convert("RGBA")
            logo.thumbnail((60, 44))
            image.paste(logo, (20, 6), logo)
        draw.text((96, 18), "REKENING KORAN", fill="white", font=bold_font)
    else:
        draw.text((20, 18), "ASTA ASA ABADI — REKENING KORAN", fill="white", font=bold_font)
    for index, header in enumerate(HEADERS):
        draw.text((column_x[index], 58), header, fill="white", font=font)
    for row_index, row in enumerate(visible_rows, start=1):
        top = header_height + (row_index - 1) * row_height
        if row_index % 2 == 0:
            draw.rectangle((0, top, width, top + row_height), fill="#F7F9FC")
        for column_index, value in enumerate(row):
            text = str(value)
            if len(text) > 40:
                text = f"{text[:37]}..."
            draw.text((column_x[column_index], top + 10), text, fill="#1D1D1F", font=font)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return _response(stream.getvalue(), "image/png", filename)


def _json_export(statement, rows, filename):
    payload = {
        "bank": statement.bank_name,
        "account_number": statement.account_number,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "opening_balance": str(statement.opening_balance),
        "closing_balance": str(statement.closing_balance),
        "transactions": [dict(zip(HEADERS, row)) for row in rows],
    }
    return _response(json.dumps(payload, ensure_ascii=False, indent=2), "application/json", filename)


EXPORT_FORMATS = {"csv", "xlsx", "pdf", "png", "json"}


def build_statement_export(statement, export_format):
    if export_format not in EXPORT_FORMATS:
        raise ValueError("Format ekspor tidak didukung.")
    rows = _rows(statement)
    filename = f"rekening-koran-{statement.id}.{export_format}"
    if export_format == "csv":
        return _csv_export(rows, filename)
    if export_format == "xlsx":
        return _xlsx_export(rows, filename)
    if export_format == "pdf":
        return _pdf_export(rows, filename)
    if export_format == "png":
        return _png_export(rows, filename)
    if export_format == "json":
        return _json_export(statement, rows, filename)
    raise ValueError("Format ekspor tidak didukung.")
