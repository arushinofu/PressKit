import os


class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    # База данных
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "presskit.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Секретный ключ (ВАЖНО: должен быть постоянным)
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'presskit-secret-key-DO-NOT-CHANGE-IN-PRODUCTION-2026'

    # Настройки сессий
    SESSION_COOKIE_SECURE = False  # True для HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400 * 30  # 30 дней
    SESSION_REFRESH_EACH_REQUEST = True

    # Flask-Login
    REMEMBER_COOKIE_DURATION = 86400 * 30  # 30 дней

    # Папки
    QR_CODES_FOLDER = os.path.join(BASE_DIR, 'static', 'qr_codes')
    BACKUPS_FOLDER = os.path.join(BASE_DIR, 'backups')

    # Сервер
    HOST = '0.0.0.0'
    PORT = 5000
    DEBUG = False

    # Бэкапы
    BACKUP_INTERVAL_HOURS = 24

    # Подпись на кодах
    QR_BRAND_TEXT = 'Пресс-центр ИТТСУ'


# Создание необходимых директорий
os.makedirs(Config.QR_CODES_FOLDER, exist_ok=True)
os.makedirs(Config.BACKUPS_FOLDER, exist_ok=True)
