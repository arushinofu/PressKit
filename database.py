from sqlalchemy import text

from models import db, Admin


def _get_column_names(table_name):
    columns = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {column[1] for column in columns}


def _migrate_equipment_columns():
    """Добавляет отсутствующие колонки в устаревшую таблицу оборудования."""
    column_names = _get_column_names('equipment')

    if 'return_date' not in column_names:
        db.session.execute(text("ALTER TABLE equipment ADD COLUMN return_date DATETIME"))
        db.session.commit()
        print("Миграция: добавлено поле equipment.return_date")

    if 'current_guest_id' not in column_names:
        db.session.execute(text("ALTER TABLE equipment ADD COLUMN current_guest_id INTEGER"))
        db.session.commit()
        print("Миграция: добавлено поле equipment.current_guest_id")


def _migrate_logs_columns():
    """Добавляет отсутствующие колонки в устаревшую таблицу логов."""
    column_names = _get_column_names('logs')
    if 'guest_id' not in column_names:
        db.session.execute(text("ALTER TABLE logs ADD COLUMN guest_id INTEGER"))
        db.session.commit()
        print("Миграция: добавлено поле logs.guest_id")


def init_database(app):
    """Инициализирует базу данных."""
    with app.app_context():
        db.create_all()
        _migrate_equipment_columns()
        _migrate_logs_columns()

        if Admin.query.count() == 0:
            admin = Admin(username='admin', must_change_password=False)
            admin.set_password('PressKit2026!')
            db.session.add(admin)
            db.session.commit()
            print("Администратор создан: admin / PressKit2026!")
        else:
            admin = Admin.query.first()
            print(f"База данных готова. Админ: {admin.username}")
