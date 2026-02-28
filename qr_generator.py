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
    """Генерирует чёрно-белый код в стиле макета с чёрным фоном и белой зоной кода."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    def _fit_font_size(text_value, max_width, min_size=10, max_size=16, glyph_ratio=0.6):
        """Подбирает размер шрифта так, чтобы строка помещалась по ширине."""
        if not text_value:
            return max_size

        estimated = int(max_width / max(len(text_value) * glyph_ratio, 1))
        return max(min_size, min(max_size, estimated))

    matrix = qr.get_matrix()
    module_size = 10
    qr_side = len(matrix) * module_size
    brand_text = (getattr(Config, 'QR_BRAND_TEXT', '') or '').strip()

    panel_padding = 8
    outer_margin_x = 20
    panel_side = qr_side + (panel_padding * 2)
    width = panel_side + (outer_margin_x * 2)

    brand_font_size = _fit_font_size(
        brand_text,
        panel_side - 16,
        min_size=12,
        max_size=24,
        glyph_ratio=0.56
    ) if brand_text else 0
    label_font_size = _fit_font_size(
        str(label_text),
        panel_side - 10,
        min_size=22,
        max_size=56,
        glyph_ratio=0.62
    ) if label_text else 0

    top_band_height = (brand_font_size + 14) if brand_text else 12
    bottom_band_height = (label_font_size + 16) if label_text else 12
    height = top_band_height + panel_side + bottom_band_height

    panel_x = outer_margin_x
    panel_y = top_band_height
    panel_center_x = panel_x + (panel_side / 2)
    qr_origin_x = panel_x + panel_padding
    qr_origin_y = panel_y + panel_padding

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="black"/>',
        f'<rect x="{panel_x}" y="{panel_y}" width="{panel_side}" height="{panel_side}" fill="white"/>',
    ]

    for row_index, row in enumerate(matrix):
        for column_index, is_filled in enumerate(row):
            if not is_filled:
                continue
            x = qr_origin_x + (column_index * module_size)
            y = qr_origin_y + (row_index * module_size)
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{module_size}" height="{module_size}" fill="black"/>'
            )

    if brand_text:
        brand_baseline_y = top_band_height - 7
        svg_parts.append(
            f'<text x="{panel_center_x}" y="{brand_baseline_y}" text-anchor="middle" '
            f'font-size="{brand_font_size}" '
            'font-family="Arial, sans-serif" font-weight="400" letter-spacing="0.2" fill="white">'
            f'{escape(brand_text)}'
            '</text>'
        )

    if label_text:
        label_baseline_y = panel_y + panel_side + label_font_size + 4
        svg_parts.append(
            f'<text x="{panel_center_x}" y="{label_baseline_y}" text-anchor="middle" '
            f'font-size="{label_font_size}" '
            'font-family="Arial, sans-serif" font-weight="700" letter-spacing="0.4" fill="white">'
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
