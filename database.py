from models import db, Admin


def init_database(app):
    """Инициализация базы данных"""
    with app.app_context():
        db.create_all()

        if Admin.query.count() == 0:
            admin = Admin(username='admin', must_change_password=False)
            admin.set_password('PressKit2026!')
            db.session.add(admin)
            db.session.commit()
            print("Администратор создан: admin / PressKit2026!")
        else:
            admin = Admin.query.first()
            print(f"База данных готова. Админ: {admin.username}")
