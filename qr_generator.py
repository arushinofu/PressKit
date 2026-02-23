import qrcode
import os
import re
from html import escape
from config import Config


def _normalize_filename(filename):
    """Делает имя файла кода безопасным для файловой системы."""
    safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', filename)
    return safe_name.strip('_') or 'qr_code'


def _build_pack_filename(equipment_numbers):
    if isinstance(equipment_numbers, (list, tuple)):
        parts = [str(value).strip() for value in equipment_numbers if str(value).strip()]
        return '_'.join(parts) or 'items'
    return str(equipment_numbers).strip() or 'items'


def generate_qr_code_svg(data, filename, label_text=None):
    """Генерирует чёрно-белый печатный код векторного формата с минималистичным обрамлением."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    module_size = 10
    border_thickness = 16
    qr_padding = 24
    qr_side = len(matrix) * module_size
    label_block_height = 64 if label_text else 0

    inner_width = qr_side + (qr_padding * 2)
    inner_height = qr_side + (qr_padding * 2) + label_block_height
    width = inner_width + (border_thickness * 2)
    height = inner_height + (border_thickness * 2)
    qr_origin_x = border_thickness + qr_padding
    qr_origin_y = border_thickness + qr_padding

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="black"/>',
        (
            f'<rect x="{border_thickness}" y="{border_thickness}" '
            f'width="{inner_width}" height="{inner_height}" fill="white"/>'
        ),
    ]

    for row_index, row in enumerate(matrix):
        for column_index, is_filled in enumerate(row):
            if not is_filled:
                continue
            x = qr_origin_x + column_index * module_size
            y = qr_origin_y + row_index * module_size
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{module_size}" height="{module_size}" fill="black"/>'
            )

    if label_text:
        separator_y = border_thickness + (qr_padding * 2) + qr_side
        label_box_width = min(inner_width - 32, max(170, len(label_text) * 16))
        label_box_height = 34
        label_box_x = (width - label_box_width) / 2
        label_box_y = separator_y + 14

        svg_parts.append(
            f'<rect x="{label_box_x}" y="{label_box_y}" '
            f'width="{label_box_width}" height="{label_box_height}" '
            'fill="white" stroke="black" stroke-width="3" rx="4" ry="4"/>'
        )
        svg_parts.append(
            f'<text x="{width / 2}" y="{label_box_y + 23}" text-anchor="middle" '
            'font-size="20" font-family="Arial, sans-serif" font-weight="700" letter-spacing="1" fill="black">'
            f'{escape(label_text)}'
            '</text>'
        )

    svg_parts.append('</svg>')

    safe_filename = _normalize_filename(filename)
    filepath = os.path.join(Config.QR_CODES_FOLDER, f"{safe_filename}.svg")
    with open(filepath, 'w', encoding='utf-8') as svg_file:
        svg_file.write('\n'.join(svg_parts))

    return filepath


def generate_equipment_qr(equipment_id, equipment_number, base_url):
    """Генерирует код для одной единицы оборудования."""
    url = f"{base_url}/scan/{equipment_number}"
    filename = f"equipment_{equipment_number}"
    return generate_qr_code_svg(url, filename, label_text=equipment_number)


def generate_pack_qr(pack_id, equipment_numbers, base_url):
    """Генерирует код для пака оборудования."""
    url = f"{base_url}/scan/pack/{pack_id}"
    filename = _build_pack_filename(equipment_numbers)
    return generate_qr_code_svg(url, filename)
