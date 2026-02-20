from flask_login import LoginManager
from models import Admin

login_manager = LoginManager()
login_manager.session_protection = 'strong'
login_manager.login_view = 'admin_login'
login_manager.login_message = 'Требуется авторизация'


@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))
