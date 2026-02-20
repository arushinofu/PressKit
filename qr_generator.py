import qrcode
import os
from config import Config


def generate_qr_code(data, filename):
    """Генерация QR-кода"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    filepath = os.path.join(Config.QR_CODES_FOLDER, f"{filename}.png")
    img.save(filepath)

    return filepath


def generate_equipment_qr(equipment_id, equipment_number, base_url):
    """Генерация QR-кода для оборудования"""
    url = f"{base_url}/scan/{equipment_id}"
    filename = f"equipment_{equipment_number}"
    return generate_qr_code(url, filename)


def generate_pack_qr(pack_id, pack_name, base_url):
    """Генерация QR-кода для пака"""
    url = f"{base_url}/scan/pack/{pack_id}"
    filename = f"pack_{pack_name}"
    return generate_qr_code(url, filename)
