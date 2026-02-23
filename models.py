from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Admin(db.Model, UserMixin):
    __tablename__ = 'admins'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    telegram = db.Column(db.String(100), nullable=False)
    pin_code = db.Column(db.String(4), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Guest(db.Model):
    __tablename__ = 'guests'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(30), nullable=False, unique=True)
    telegram = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    equipment = db.relationship('Equipment', backref='category', lazy=True)


class Equipment(db.Model):
    __tablename__ = 'equipment'

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(50), unique=True, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    name = db.Column('general_name', db.String(200), nullable=False)
    description = db.Column('specific_name', db.String(500), nullable=False)
    status = db.Column(db.String(20), default='available')  # available, occupied, broken
    current_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    current_guest_id = db.Column(db.Integer, db.ForeignKey('guests.id'), nullable=True)
    return_date = db.Column(db.DateTime, nullable=True)
    qr_code_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    current_user = db.relationship('User', foreign_keys=[current_user_id], backref='equipment')
    current_guest = db.relationship('Guest', foreign_keys=[current_guest_id], backref='equipment')
    pack_memberships = db.relationship('PackEquipment', backref='equipment', lazy=True)

    @property
    def general_name(self):
        """Обратносовместимый псевдоним для старых шаблонов и интерфейсов."""
        return self.name

    @general_name.setter
    def general_name(self, value):
        self.name = value

    @property
    def specific_name(self):
        """Обратносовместимый псевдоним для старых шаблонов и интерфейсов."""
        return self.description

    @specific_name.setter
    def specific_name(self, value):
        self.description = value


class Pack(db.Model):
    __tablename__ = 'packs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    qr_code_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    equipment_items = db.relationship('PackEquipment', backref='pack', lazy=True)


class PackEquipment(db.Model):
    __tablename__ = 'pack_equipment'

    pack_id = db.Column(db.Integer, db.ForeignKey('packs.id'), primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), primary_key=True)


class Log(db.Model):
    __tablename__ = 'logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    guest_id = db.Column(db.Integer, db.ForeignKey('guests.id'), nullable=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=True)
    pack_id = db.Column(db.Integer, db.ForeignKey('packs.id'), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', foreign_keys=[user_id], backref='logs')
    guest = db.relationship('Guest', foreign_keys=[guest_id], backref='logs')
    equipment = db.relationship('Equipment', foreign_keys=[equipment_id], backref='logs')
    pack = db.relationship('Pack', foreign_keys=[pack_id], backref='logs')
