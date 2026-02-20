import os
import shutil
from datetime import datetime
from config import Config


def create_backup():
    """Создание резервной копии базы данных"""
    try:
        db_path = os.path.join(Config.BASE_DIR, 'presskit.db')
        if not os.path.exists(db_path):
            print("База данных не найдена для резервного копирования")
            return False

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'presskit_backup_{timestamp}.db'
        backup_path = os.path.join(Config.BACKUPS_FOLDER, backup_filename)

        shutil.copy2(db_path, backup_path)
        cleanup_old_backups()

        print(f"Резервная копия создана: {backup_filename}")
        return True

    except Exception as e:
        print(f"Ошибка создания резервной копии: {e}")
        return False


def cleanup_old_backups(keep_count=10):
    """Удаление старых резервных копий"""
    try:
        backup_files = []
        for filename in os.listdir(Config.BACKUPS_FOLDER):
            if filename.startswith('presskit_backup_') and filename.endswith('.db'):
                filepath = os.path.join(Config.BACKUPS_FOLDER, filename)
                backup_files.append((filepath, os.path.getctime(filepath)))

        backup_files.sort(key=lambda x: x[1], reverse=True)

        for filepath, _ in backup_files[keep_count:]:
            os.remove(filepath)
            print(f"  Удален старый бэкап: {os.path.basename(filepath)}")

    except Exception as e:
        print(f"Ошибка очистки старых бэкапов: {e}")