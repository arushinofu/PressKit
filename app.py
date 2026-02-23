from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response, abort
from flask_login import login_user, logout_user, login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler
import os
import re
from datetime import datetime, timedelta
import random
import io
import csv

from config import Config
from models import db, Admin, User, Guest, Equipment, Category, Pack, PackEquipment, Log
from database import init_database
from auth import login_manager
from qr_generator import generate_equipment_qr, generate_pack_qr
from backup import create_backup

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'show_admin_login_page'
init_database(app)

RETURN_DATE_FORMAT = '%d.%m.%y %H:%M'
RETURN_DATE_REGEX = re.compile(r'^\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}$')
RETURN_DEADLINE_CHECK_MINUTES = 1


@app.before_request
def configure_session():
    session.permanent = True


# Вспомогательные функции
def add_log(action, details, user_id=None, guest_id=None, equipment_id=None, pack_id=None):
    """Создаёт запись в журнале."""
    log = Log(
        user_id=user_id,
        guest_id=guest_id,
        equipment_id=equipment_id,
        pack_id=pack_id,
        action=action,
        details=details
    )
    db.session.add(log)
    db.session.commit()


def format_return_date(value):
    """Преобразует дату и время в формат для интерфейса."""
    if not value:
        return None
    return value.strftime(RETURN_DATE_FORMAT)


def parse_return_date(value):
    """Разбирает и проверяет дату возврата в формате ДД.ММ.ГГ ЧЧ:ММ."""
    raw_value = (value or '').strip()
    if not raw_value:
        raise ValueError('Укажите дату возврата в формате ДД.ММ.ГГ ЧЧ:ММ')

    if not RETURN_DATE_REGEX.match(raw_value):
        raise ValueError('Неверный формат даты. Используйте ДД.ММ.ГГ ЧЧ:ММ')

    try:
        parsed_date = datetime.strptime(raw_value, RETURN_DATE_FORMAT)
    except ValueError as exc:
        raise ValueError('Неверная дата возврата. Проверьте введённые дату и время') from exc

    if parsed_date <= datetime.now():
        raise ValueError('Дата возврата должна быть в будущем')

    return parsed_date


def is_equipment_overdue(equipment, reference_time=None):
    """Возвращает истину, если у занятого оборудования просрочен дедлайн возврата."""
    now = reference_time or datetime.now()
    return bool(
        equipment.status == 'occupied'
        and equipment.return_date
        and equipment.return_date < now
    )


def get_equipment_holder_name(equipment):
    if equipment.current_user:
        return equipment.current_user.full_name
    if equipment.current_guest:
        return f"{equipment.current_guest.full_name} (гость)"
    return 'неизвестный пользователь'


def get_log_user_name(log_item):
    """Возвращает подпись пользователя для логов."""
    if log_item.user:
        return log_item.user.full_name
    if log_item.guest:
        return f"{log_item.guest.full_name} (гость)"
    return 'admin'


def get_guest_log_identity(guest):
    """Формирует подпись гостя для деталей лога с контактами."""
    phone = (guest.phone or '').strip() or '-'
    telegram = (guest.telegram or '').strip() or '-'
    return f"{guest.full_name} (гость) [{phone}/{telegram}]"


def normalize_phone(phone_value):
    """Нормализует номер телефона к формату 7 (000) 000-00-00."""
    digits = re.sub(r'\D', '', phone_value or '')
    if len(digits) != 11 or digits[0] not in {'7', '8'}:
        raise ValueError('Неверный формат телефона. Используйте 7 (XXX) XXX-XX-XX')

    digits = '7' + digits[1:]
    return f"7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def parse_guest_data(data):
    full_name = (data.get('guest_full_name') or '').strip()
    raw_phone = (data.get('guest_phone') or '').strip()
    telegram = (data.get('guest_telegram') or '').strip()

    if not full_name:
        raise ValueError('Укажите ФИО гостя')
    if not raw_phone:
        raise ValueError('Укажите номер телефона гостя')
    phone = normalize_phone(raw_phone)
    if not telegram:
        raise ValueError('Укажите Telegram гостя')

    return full_name, phone, telegram


def get_or_create_guest(full_name, phone, telegram):
    guest = Guest.query.filter_by(phone=phone).first()
    if guest:
        guest.full_name = full_name
        guest.telegram = telegram
        guest.is_active = True
        db.session.flush()
        return guest

    guest = Guest(
        full_name=full_name,
        phone=phone,
        telegram=telegram,
        is_active=True
    )
    db.session.add(guest)
    db.session.flush()
    return guest


def resolve_guest_for_take(data):
    """Возвращает гостя по идентификатору или создаёт и обновляет по переданным полям."""
    guest_id_raw = data.get('guest_id')
    if guest_id_raw is not None and str(guest_id_raw).strip():
        try:
            guest_id = int(guest_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError('Неверный ID гостя') from exc

        guest = Guest.query.get(guest_id)
        if not guest:
            raise ValueError('Гость не найден')

        guest.is_active = True
        db.session.flush()
        return guest

    full_name, phone, telegram = parse_guest_data(data)
    return get_or_create_guest(full_name, phone, telegram)


def deactivate_guest_if_no_equipment(guest_id):
    if not guest_id:
        return

    active_items = Equipment.query.filter_by(
        current_guest_id=guest_id,
        status='occupied'
    ).count()

    if active_items == 0:
        guest = Guest.query.get(guest_id)
        if guest and guest.is_active:
            guest.is_active = False
            db.session.commit()


def detect_pack_actor(pack_equipment):
    """Определяет, кто сейчас держит пак: пользователь или гость."""
    if not pack_equipment:
        return None, None

    if not all(item.status == 'occupied' for item in pack_equipment):
        return None, None

    actor_tokens = set()
    for item in pack_equipment:
        if item.current_user_id:
            actor_tokens.add(('user', item.current_user_id))
        elif item.current_guest_id:
            actor_tokens.add(('guest', item.current_guest_id))
        else:
            return None, None

    if len(actor_tokens) != 1:
        return None, None

    actor_type, actor_id = actor_tokens.pop()
    return actor_type, actor_id


def equipment_is_guest_occupied(equipment):
    return equipment.status == 'occupied' and equipment.current_guest_id is not None


def equipment_is_user_occupied(equipment):
    return equipment.status == 'occupied' and equipment.current_user_id is not None


def get_pack_equipment(pack_id):
    """Возвращает список оборудования в паке."""
    return db.session.query(Equipment).join(
        PackEquipment, Equipment.id == PackEquipment.equipment_id
    ).filter(PackEquipment.pack_id == pack_id).all()


def has_deadline_missed_log_since_last_take(equipment_id):
    """Предотвращает дублирование логов просрочки в одном цикле выдачи."""
    last_take_log = Log.query.filter_by(
        equipment_id=equipment_id,
        action='take'
    ).order_by(Log.timestamp.desc()).first()
    missed_deadline_query = Log.query.filter_by(
        equipment_id=equipment_id,
        action='дедлайн потерян'
    )

    if last_take_log:
        missed_deadline_query = missed_deadline_query.filter(Log.timestamp >= last_take_log.timestamp)

    return missed_deadline_query.first() is not None


def check_overdue_equipment_deadlines():
    """Фоновая задача проверяет дедлайны возврата и пишет логи о просрочке."""
    with app.app_context():
        now = datetime.now()
        overdue_equipment = Equipment.query.filter(
            Equipment.status == 'occupied',
            Equipment.return_date.isnot(None),
            Equipment.return_date < now
        ).all()

        new_logs = []
        for equipment in overdue_equipment:
            if has_deadline_missed_log_since_last_take(equipment.id):
                continue

            user_name = get_equipment_holder_name(equipment)
            deadline_text = format_return_date(equipment.return_date)
            new_logs.append(Log(
                user_id=equipment.current_user_id,
                guest_id=equipment.current_guest_id,
                equipment_id=equipment.id,
                action='дедлайн потерян',
                details=(
                    f"Просрочен возврат {equipment.name} №{equipment.number}. "
                    f"Пользователь: {user_name}. Дедлайн: {deadline_text}"
                )
            ))

        if new_logs:
            db.session.add_all(new_logs)
            db.session.commit()


# Настройка автоматических задач
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=create_backup,
    trigger='interval',
    hours=Config.BACKUP_INTERVAL_HOURS,
    next_run_time=datetime.now()
)
scheduler.add_job(
    func=check_overdue_equipment_deadlines,
    trigger='interval',
    minutes=RETURN_DEADLINE_CHECK_MINUTES,
    next_run_time=datetime.now() + timedelta(minutes=RETURN_DEADLINE_CHECK_MINUTES)
)
scheduler.start()
print(f"Планировщик бэкапов запущен (каждые {Config.BACKUP_INTERVAL_HOURS} часов)")
print(f"Планировщик дедлайнов запущен (каждые {RETURN_DEADLINE_CHECK_MINUTES} мин)")
create_backup()
check_overdue_equipment_deadlines()


# Административные маршруты

@app.route('/')
def index():
    return redirect(url_for('show_admin_login_page'))


@app.route('/admin')
@app.route('/admin/login')
def show_admin_login_page():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/login.html')


@app.route('/admin/auth', methods=['POST'])
def authenticate_admin():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': 'Введите логин и пароль'}), 400

    admin = Admin.query.filter_by(username=username).first()
    if not admin or not admin.check_password(password):
        return jsonify({'success': False, 'message': 'Неверный логин или пароль'}), 401

    login_user(admin, remember=True, duration=timedelta(days=30))
    return jsonify({
        'success': True,
        'redirect': url_for('admin_dashboard'),
        'message': 'Вход выполнен успешно'
    })


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    return render_template('admin/dashboard.html')


@app.route('/admin/logout')
@login_required
def logout_admin():
    logout_user()
    return redirect(url_for('show_admin_login_page'))


# API пользователей
@app.route('/api/users', methods=['GET'])
@login_required
def list_all_users():
    search = request.args.get('search', '').strip()

    query = User.query
    if search:
        query = query.filter(
            db.or_(
                User.full_name.ilike(f'%{search}%'),
                User.telegram.ilike(f'%{search}%')
            )
        )

    users = query.order_by(User.full_name).all()
    return jsonify([{
        'id': u.id,
        'full_name': u.full_name,
        'telegram': u.telegram,
        'pin_code': u.pin_code,
        'created_at': u.created_at.isoformat(),
        'current_equipment': [{
            'id': e.id,
            'number': e.number,
            'name': e.name,
            'description': e.description,
            'return_date': format_return_date(e.return_date),
            'is_overdue': is_equipment_overdue(e)
        } for e in Equipment.query.filter_by(current_user_id=u.id).order_by(Equipment.number).all()]
    } for u in users])


@app.route('/api/guests', methods=['GET'])
@login_required
def list_all_guests():
    guests = Guest.query.filter_by(is_active=True).order_by(Guest.full_name).all()
    return jsonify([{
        'id': g.id,
        'full_name': g.full_name,
        'phone': g.phone,
        'telegram': g.telegram,
        'created_at': g.created_at.isoformat(),
        'current_equipment': [{
            'id': e.id,
            'number': e.number,
            'name': e.name,
            'description': e.description,
            'return_date': format_return_date(e.return_date),
            'is_overdue': is_equipment_overdue(e)
        } for e in Equipment.query.filter_by(current_guest_id=g.id).order_by(Equipment.number).all()]
    } for g in guests])


@app.route('/api/users', methods=['POST'])
@login_required
def register_new_user():
    data = request.get_json()
    pin_code = data.get('pin_code') or str(random.randint(1000, 9999))

    user = User(
        full_name=data['full_name'],
        telegram=data['telegram'],
        pin_code=pin_code
    )
    db.session.add(user)
    db.session.commit()

    add_log('create', f"Пользователь {user.full_name} создан, PIN: {pin_code}")

    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'full_name': user.full_name,
            'telegram': user.telegram,
            'pin_code': user.pin_code
        }
    })


@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
def edit_user_profile(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json()

    user.full_name = data.get('full_name', user.full_name)
    user.telegram = data.get('telegram', user.telegram)
    if data.get('pin_code'):
        user.pin_code = data['pin_code']

    db.session.commit()
    add_log('edit', f"Пользователь {user.full_name} отредактирован")

    return jsonify({'success': True})


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def remove_user(user_id):
    user = User.query.get_or_404(user_id)
    user_name = user.full_name
    
    db.session.delete(user)
    db.session.commit()
    add_log('delete', f"Пользователь {user_name} удалён")

    return jsonify({'success': True})


@app.route('/api/users/<int:user_id>/history', methods=['GET'])
@login_required
def get_user_activity_history(user_id):
    user = User.query.get_or_404(user_id)

    current_equipment = Equipment.query.filter_by(current_user_id=user_id).order_by(Equipment.number).all()
    user_logs = Log.query.filter_by(user_id=user_id).order_by(Log.timestamp.desc()).limit(50).all()

    return jsonify({
        'user': {
            'id': user.id,
            'full_name': user.full_name,
            'telegram': user.telegram,
            'pin_code': user.pin_code
        },
        'current_equipment': [{
            'id': e.id,
            'number': e.number,
            'name': e.name,
            'description': e.description,
            'return_date': format_return_date(e.return_date),
            'is_overdue': is_equipment_overdue(e)
        } for e in current_equipment],
        'logs': [{
            'id': l.id,
            'action': l.action,
            'details': l.details,
            'timestamp': l.timestamp.isoformat()
        } for l in user_logs]
    })


# API оборудования
@app.route('/api/equipment', methods=['GET'])
@login_required
def list_all_equipment():
    search = request.args.get('search', '').lower()
    category_filter = request.args.get('category', '')
    status_filter = request.args.get('status', '')

    query = Equipment.query

    if search:
        query = query.outerjoin(Category).filter(
            db.or_(
                Equipment.name.ilike(f'%{search}%'),
                Equipment.description.ilike(f'%{search}%'),
                Equipment.number.ilike(f'%{search}%'),
                Category.name.ilike(f'%{search}%')
            )
        )

    if category_filter:
        query = query.join(Category).filter(Category.name == category_filter)

    if status_filter:
        query = query.filter(Equipment.status == status_filter)

    now = datetime.now()
    equipment = query.order_by(Equipment.number).all()
    return jsonify([{
        'id': e.id,
        'number': e.number,
        'category': e.category.name if e.category else None,
        'name': e.name,
        'description': e.description,
        'general_name': e.name,  # Backward-compatible response keys
        'specific_name': e.description,  # Backward-compatible response keys
        'status': e.status,
        'current_user_id': e.current_user_id,
        'current_user_name': e.current_user.full_name if e.current_user else None,
        'current_guest_id': e.current_guest_id,
        'current_guest_name': f"{e.current_guest.full_name} (гость)" if e.current_guest else None,
        'occupied_by': 'guest' if e.current_guest_id else ('user' if e.current_user_id else None),
        'return_date': format_return_date(e.return_date),
        'is_overdue': is_equipment_overdue(e, now),
        'qr_code_path': e.qr_code_path,
        'created_at': e.created_at.isoformat()
    } for e in equipment])


@app.route('/api/equipment', methods=['POST'])
@login_required
def add_equipment_item():
    data = request.get_json()

    category_name = (data.get('category') or '').strip()
    equipment_name = (data.get('name') or data.get('general_name') or '').strip()
    equipment_description = (data.get('description') or data.get('specific_name') or '').strip()

    if not category_name:
        return jsonify({'success': False, 'message': 'Категория обязательна'}), 400
    if not equipment_name or not equipment_description:
        return jsonify({'success': False, 'message': 'Заполните название и описание'}), 400

    # Получаем или создаем категорию
    category = Category.query.filter_by(name=category_name).first()
    if not category:
        category = Category(name=category_name)
        db.session.add(category)
        db.session.flush()

    equipment = Equipment(
        number=data['number'],
        category_id=category.id,
        name=equipment_name,
        description=equipment_description,
        status=data.get('status', 'available')
    )
    db.session.add(equipment)
    db.session.flush()

    base_url = request.url_root.rstrip('/')
    equipment.qr_code_path = generate_equipment_qr(equipment.id, equipment.number, base_url)
    db.session.commit()

    add_log('create', f"Оборудование №{equipment.number} создано", equipment_id=equipment.id)

    return jsonify({
        'success': True,
        'equipment': {
            'id': equipment.id,
            'number': equipment.number,
            'qr_code': f"QR-{equipment.number}",
            'status': equipment.status
        }
    })


@app.route('/api/equipment/<int:equipment_id>', methods=['PUT'])
@login_required
def edit_equipment_item(equipment_id):
    equipment = Equipment.query.get_or_404(equipment_id)
    data = request.get_json()
    old_category_id = equipment.category_id
    old_number = equipment.number
    old_guest_id = equipment.current_guest_id

    # Обновление категории если нужно
    if 'category' in data:
        category_name = (data.get('category') or '').strip()
        if category_name:
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(name=category_name)
                db.session.add(category)
                db.session.flush()
            equipment.category_id = category.id

    equipment.number = data.get('number', equipment.number)
    equipment.name = (data.get('name') or data.get('general_name') or equipment.name)
    equipment.description = (data.get('description') or data.get('specific_name') or equipment.description)
    equipment.status = data.get('status', equipment.status)

    if equipment.status == 'available':
        equipment.current_user_id = None
        equipment.current_guest_id = None
        equipment.return_date = None
    elif equipment.status != 'occupied':
        equipment.current_user_id = None
        equipment.current_guest_id = None
        equipment.return_date = None

    if equipment.number != old_number or not equipment.qr_code_path or not equipment.qr_code_path.endswith('.svg'):
        base_url = request.url_root.rstrip('/')
        equipment.qr_code_path = generate_equipment_qr(equipment.id, equipment.number, base_url)

    db.session.commit()

    # Удаляем пустую категорию если она больше не используется
    if old_category_id and old_category_id != equipment.category_id:
        if Equipment.query.filter_by(category_id=old_category_id).count() == 0:
            old_category = Category.query.get(old_category_id)
            if old_category:
                db.session.delete(old_category)
                db.session.commit()

    add_log('edit', f"Оборудование №{equipment.number} отредактировано", equipment_id=equipment.id)
    if old_guest_id and old_guest_id != equipment.current_guest_id:
        deactivate_guest_if_no_equipment(old_guest_id)

    return jsonify({'success': True, 'equipment': {
        'id': equipment.id,
        'status': equipment.status
    }})


@app.route('/api/equipment/<int:equipment_id>', methods=['DELETE'])
@login_required
def remove_equipment_item(equipment_id):
    equipment = Equipment.query.get_or_404(equipment_id)
    equipment_number = equipment.number
    category_id = equipment.category_id
    guest_id = equipment.current_guest_id

    # Удаляем связи с паками
    PackEquipment.query.filter_by(equipment_id=equipment_id).delete()

    # Удаляем QR-код если есть
    if equipment.qr_code_path and os.path.exists(equipment.qr_code_path):
        try:
            os.remove(equipment.qr_code_path)
        except:
            pass

    db.session.delete(equipment)
    db.session.commit()

    # Удаляем пустую категорию
    if category_id and Equipment.query.filter_by(category_id=category_id).count() == 0:
        category = Category.query.get(category_id)
        if category:
            db.session.delete(category)
            db.session.commit()

    add_log('delete', f"Оборудование №{equipment_number} удалено")
    deactivate_guest_if_no_equipment(guest_id)

    return jsonify({'success': True})


@app.route('/api/categories', methods=['GET'])
@login_required
def list_all_categories():
    categories = Category.query.all()
    return jsonify([{'id': c.id, 'name': c.name} for c in categories])


@app.route('/api/categories/<int:category_id>', methods=['DELETE'])
@login_required
def remove_category(category_id):
    category = Category.query.get_or_404(category_id)

    equipment_count = Equipment.query.filter_by(category_id=category_id).count()
    if equipment_count > 0:
        return jsonify({'success': False, 'message': f'В категории {equipment_count} единиц оборудования'}), 400

    category_name = category.name
    db.session.delete(category)
    db.session.commit()

    return jsonify({'success': True})


# API логов
@app.route('/api/logs', methods=['GET'])
@login_required
def get_activity_logs():
    logs = Log.query.order_by(Log.timestamp.desc()).limit(1000).all()
    return jsonify([{
        'id': l.id,
        'user_id': l.user_id,
        'guest_id': l.guest_id,
        'user_name': get_log_user_name(l),
        'equipment_id': l.equipment_id,
        'equipment_name': f"{l.equipment.name} №{l.equipment.number}" if l.equipment else None,
        'action': l.action,
        'details': l.details,
        'timestamp': l.timestamp.isoformat()
    } for l in logs])


@app.route('/api/logs/export', methods=['GET'])
@login_required
def export_activity_logs_to_csv():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writerow(['Время', 'Пользователь', 'Действие', 'Оборудование', 'Детали'])

    for log in Log.query.order_by(Log.timestamp.desc()).all():
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            get_log_user_name(log),
            log.action,
            f"{log.equipment.name} №{log.equipment.number}" if log.equipment else '-',
            log.details or '-'
        ])

    return create_csv_response(output.getvalue(), 'presskit_logs')


# Экспорт/импорт данных
def create_csv_response(csv_data, filename):
    """Формирует ответ с файлом табличных данных."""
    return Response(
        ('\ufeff' + csv_data).encode('utf-8'),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename={filename}_{datetime.now().strftime("%Y%m%d")}.csv',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )


@app.route('/api/users/export', methods=['GET'])
@login_required
def export_users_to_csv():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writerow(['ФИ', 'Телеграм', 'PIN-код'])

    for user in User.query.order_by(User.full_name).all():
        writer.writerow([user.full_name, user.telegram, user.pin_code])

    return create_csv_response(output.getvalue(), 'presskit_users')


@app.route('/api/users/import', methods=['POST'])
@login_required
def import_users_from_csv():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Файл не загружен'}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'message': 'Разрешены только CSV файлы'}), 400

    try:
        content = file.read().decode('utf-8-sig')
        csv_reader = csv.reader(io.StringIO(content), delimiter=';')
        next(csv_reader)  # Пропускаем заголовок

        imported = 0
        errors = []

        for row_num, row in enumerate(csv_reader, start=2):
            if len(row) < 3:
                errors.append(f"Строка {row_num}: недостаточно данных")
                continue

            full_name = row[0].strip()
            telegram = row[1].strip()
            pin_code = row[2].strip()

            if not full_name or not telegram:
                errors.append(f"Строка {row_num}: пустое ФИ или Telegram")
                continue

            if not pin_code or len(pin_code) != 4 or not pin_code.isdigit():
                errors.append(f"Строка {row_num}: неверный PIN-код (должен быть 4 цифры)")
                continue

            if User.query.filter_by(telegram=telegram).first():
                errors.append(f"Строка {row_num}: пользователь с Telegram {telegram} уже существует")
                continue

            db.session.add(User(full_name=full_name, telegram=telegram, pin_code=pin_code))
            imported += 1

        db.session.commit()
        add_log('import', f"Импортировано пользователей: {imported}, ошибок: {len(errors)}")

        return jsonify({
            'success': True,
            'imported': imported,
            'errors': errors,
            'message': f'Импортировано: {imported} пользователей'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Ошибка импорта: {str(e)}'}), 500


@app.route('/api/equipment/export', methods=['GET'])
@login_required
def export_equipment_to_csv():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writerow(['Категория', 'Номер', 'Название', 'Описание', 'Статус'])

    for eq in Equipment.query.order_by(Equipment.category_id, Equipment.number).all():
        writer.writerow([
            eq.category.name if eq.category else '',
            eq.number,
            eq.name,
            eq.description,
            eq.status
        ])

    return create_csv_response(output.getvalue(), 'presskit_equipment')


@app.route('/api/equipment/import', methods=['POST'])
@login_required
def import_equipment_from_csv():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Файл не загружен'}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'message': 'Разрешены только CSV файлы'}), 400

    try:
        content = file.read().decode('utf-8-sig')
        csv_reader = csv.reader(io.StringIO(content), delimiter=';')
        next(csv_reader)

        imported = 0
        errors = []
        base_url = request.url_root.rstrip('/')

        for row_num, row in enumerate(csv_reader, start=2):
            if len(row) < 5:
                errors.append(f"Строка {row_num}: недостаточно данных")
                continue

            category_name = row[0].strip()
            number = row[1].strip()
            equipment_name = row[2].strip()
            equipment_description = row[3].strip()
            status = row[4].strip().lower()

            if not number or not equipment_name or not equipment_description:
                errors.append(f"Строка {row_num}: пустые обязательные поля")
                continue

            if status not in ['available', 'occupied', 'broken']:
                errors.append(f"Строка {row_num}: неверный статус '{status}'")
                continue

            if Equipment.query.filter_by(number=number).first():
                errors.append(f"Строка {row_num}: оборудование с номером {number} уже существует")
                continue

            # Получаем или создаем категорию
            category = None
            if category_name:
                category = Category.query.filter_by(name=category_name).first()
                if not category:
                    category = Category(name=category_name)
                    db.session.add(category)
                    db.session.flush()

            equipment = Equipment(
                number=number,
                category_id=category.id if category else None,
                name=equipment_name,
                description=equipment_description,
                status=status
            )
            db.session.add(equipment)
            db.session.flush()

            equipment.qr_code_path = generate_equipment_qr(equipment.id, equipment.number, base_url)
            imported += 1

        db.session.commit()
        add_log('import', f"Импортировано оборудования: {imported}, ошибок: {len(errors)}")

        return jsonify({
            'success': True,
            'imported': imported,
            'errors': errors,
            'message': f'Импортировано: {imported} единиц оборудования'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Ошибка импорта: {str(e)}'}), 500


@app.route('/api/packs/export', methods=['GET'])
@login_required
def export_packs_to_csv():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writerow(['Название пака', 'Номера оборудования (через запятую)'])

    for pack in Pack.query.all():
        pack_equipment = get_pack_equipment(pack.id)
        equipment_numbers = ','.join([eq.number for eq in pack_equipment])
        writer.writerow([pack.name, equipment_numbers])

    return create_csv_response(output.getvalue(), 'presskit_packs')


@app.route('/api/packs/import', methods=['POST'])
@login_required
def import_packs_from_csv():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Файл не загружен'}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'message': 'Разрешены только CSV файлы'}), 400

    try:
        content = file.read().decode('utf-8-sig')
        csv_reader = csv.reader(io.StringIO(content), delimiter=';')
        next(csv_reader)

        imported = 0
        errors = []
        base_url = request.url_root.rstrip('/')

        for row_num, row in enumerate(csv_reader, start=2):
            if len(row) < 2:
                errors.append(f"Строка {row_num}: недостаточно данных")
                continue

            pack_name = row[0].strip()
            equipment_numbers_str = row[1].strip()

            if not pack_name or not equipment_numbers_str:
                errors.append(f"Строка {row_num}: пустые поля")
                continue

            equipment_numbers = [num.strip() for num in equipment_numbers_str.split(',')]
            if len(equipment_numbers) < 2:
                errors.append(f"Строка {row_num}: минимум 2 предмета для пака")
                continue

            # Проверяем наличие оборудования
            equipment_ids = []
            missing_numbers = []
            for number in equipment_numbers:
                eq = Equipment.query.filter_by(number=number).first()
                if eq:
                    equipment_ids.append(eq.id)
                else:
                    missing_numbers.append(number)

            if missing_numbers:
                errors.append(f"Строка {row_num}: не найдено оборудование: {', '.join(missing_numbers)}")
                continue

            if Pack.query.filter_by(name=pack_name).first():
                errors.append(f"Строка {row_num}: пак '{pack_name}' уже существует")
                continue

            # Создаем пак
            pack = Pack(name=pack_name)
            db.session.add(pack)
            db.session.flush()

            for eq_id in equipment_ids:
                db.session.add(PackEquipment(pack_id=pack.id, equipment_id=eq_id))

            pack.qr_code_path = generate_pack_qr(pack.id, equipment_numbers, base_url)
            imported += 1

        db.session.commit()
        add_log('import', f"Импортировано паков: {imported}, ошибок: {len(errors)}")

        return jsonify({
            'success': True,
            'imported': imported,
            'errors': errors,
            'message': f'Импортировано: {imported} паков'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Ошибка импорта: {str(e)}'}), 500


# Пользовательские маршруты (сканирование QR)
@app.route('/scan/<scan_token>')
def show_equipment_scan_page(scan_token):
    equipment = Equipment.query.filter_by(number=scan_token).first()

    # Backward compatibility for old QR links: /scan/<equipment_id>
    if not equipment and scan_token.isdigit():
        legacy_equipment = Equipment.query.get(int(scan_token))
        if legacy_equipment:
            return redirect(url_for('show_equipment_scan_page', scan_token=legacy_equipment.number), code=302)

    if not equipment:
        abort(404)

    users = User.query.order_by(User.full_name).all()
    guests = Guest.query.filter_by(is_active=True).order_by(Guest.full_name).all()
    return render_template('user/scan.html', equipment=equipment, users=users, guests=guests, now=datetime.now())


@app.route('/api/scan/take', methods=['POST'])
def borrow_equipment():
    data = request.get_json() or {}
    equipment = Equipment.query.get_or_404(data['equipment_id'])
    actor_type = data.get('actor_type', 'user')
    try:
        return_date = parse_return_date(data.get('return_date'))
    except ValueError as error:
        return jsonify({'success': False, 'message': str(error)}), 400

    if equipment.status != 'available':
        return jsonify({'success': False, 'message': 'Оборудование недоступно'}), 400

    user = None
    guest = None
    if actor_type == 'guest':
        try:
            guest = resolve_guest_for_take(data)
        except ValueError as error:
            return jsonify({'success': False, 'message': str(error)}), 400
    else:
        user = User.query.get_or_404(data['user_id'])
        if user.pin_code != data.get('pin_code'):
            return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    equipment.status = 'occupied'
    equipment.current_user_id = user.id if user else None
    equipment.current_guest_id = guest.id if guest else None
    equipment.return_date = return_date
    db.session.commit()

    return_date_text = format_return_date(return_date)
    actor_name = user.full_name if user else get_guest_log_identity(guest)
    add_log(
        'take',
        f"{actor_name} взял {equipment.name} №{equipment.number}. Дедлайн возврата: {return_date_text}",
        user_id=user.id if user else None,
        guest_id=guest.id if guest else None,
        equipment_id=equipment.id
    )

    return jsonify({
        'success': True,
        'message': f'Оборудование взято: {equipment.name}. Вернуть до {return_date_text}',
        'actor_type': actor_type
    })


@app.route('/api/scan/return', methods=['POST'])
def return_equipment_item():
    data = request.get_json() or {}
    equipment = Equipment.query.get_or_404(data['equipment_id'])

    if equipment.status != 'occupied':
        return jsonify({'success': False, 'message': 'Оборудование не занято'}), 400

    user = None
    guest = None
    if equipment_is_guest_occupied(equipment):
        guest = Guest.query.get_or_404(data.get('guest_id'))
        if equipment.current_guest_id != guest.id:
            return jsonify({'success': False, 'message': 'Это оборудование взято другим гостем'}), 400
    elif equipment_is_user_occupied(equipment):
        user = User.query.get_or_404(data.get('user_id'))
        if user.pin_code != data.get('pin_code'):
            return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401
        if equipment.current_user_id != user.id:
            return jsonify({'success': False, 'message': 'Это оборудование взято другим пользователем'}), 400
    else:
        return jsonify({'success': False, 'message': 'Не удалось определить владельца оборудования'}), 400

    previous_deadline = format_return_date(equipment.return_date)
    equipment.status = 'available'
    equipment.current_user_id = None
    equipment.current_guest_id = None
    equipment.return_date = None
    db.session.commit()

    actor_name = user.full_name if user else f"{guest.full_name} (гость)"
    details = f"{actor_name} вернул {equipment.name} №{equipment.number}"
    if previous_deadline:
        details += f". Дедлайн был: {previous_deadline}"

    add_log(
        'return',
        details,
        user_id=user.id if user else None,
        guest_id=guest.id if guest else None,
        equipment_id=equipment.id
    )

    if guest:
        deactivate_guest_if_no_equipment(guest.id)

    return jsonify({'success': True, 'message': f'Оборудование возвращено: {equipment.name}'})


# Паки
@app.route('/scan/pack/<int:pack_id>')
def show_pack_scan_page(pack_id):
    pack = Pack.query.get_or_404(pack_id)
    pack_equipment = get_pack_equipment(pack_id)
    users = User.query.order_by(User.full_name).all()
    guests = Guest.query.filter_by(is_active=True).order_by(Guest.full_name).all()

    all_available = all(e.status == 'available' for e in pack_equipment)
    occupied_actor_type, occupied_actor_id = detect_pack_actor(pack_equipment)
    all_occupied_same_actor = occupied_actor_type is not None

    return render_template('user/pack.html',
                          pack=pack,
                          pack_items=pack_equipment,
                          pack_items_ids=[e.id for e in pack_equipment],
                          users=users,
                          guests=guests,
                          all_available=all_available,
                          all_occupied_same_actor=all_occupied_same_actor,
                          occupied_actor_type=occupied_actor_type,
                          occupied_actor_id=occupied_actor_id,
                          now=datetime.now())


@app.route('/api/scan/take-pack', methods=['POST'])
def borrow_equipment_pack():
    data = request.get_json() or {}
    pack_id = data['pack_id']
    actor_type = data.get('actor_type', 'user')
    try:
        return_date = parse_return_date(data.get('return_date'))
    except ValueError as error:
        return jsonify({'success': False, 'message': str(error)}), 400

    pack_equipment = get_pack_equipment(pack_id)
    unavailable = [e for e in pack_equipment if e.status != 'available']

    if unavailable:
        return jsonify({'success': False, 'message': 'Некоторые предметы недоступны'}), 400

    user = None
    guest = None
    if actor_type == 'guest':
        try:
            guest = resolve_guest_for_take(data)
        except ValueError as error:
            return jsonify({'success': False, 'message': str(error)}), 400
    else:
        user = User.query.get_or_404(data['user_id'])
        if user.pin_code != data.get('pin_code'):
            return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    return_date_text = format_return_date(return_date)
    actor_name = user.full_name if user else get_guest_log_identity(guest)
    for equipment in pack_equipment:
        equipment.status = 'occupied'
        equipment.current_user_id = user.id if user else None
        equipment.current_guest_id = guest.id if guest else None
        equipment.return_date = return_date
        db.session.add(Log(
            user_id=user.id if user else None,
            guest_id=guest.id if guest else None,
            equipment_id=equipment.id,
            pack_id=pack_id,
            action='take',
            details=(
                f"{actor_name} взял {equipment.name} №{equipment.number} (из пака). "
                f"Дедлайн возврата: {return_date_text}"
            )
        ))

    db.session.commit()
    return jsonify({
        'success': True,
        'message': f'Взято {len(pack_equipment)} предметов из пака. Вернуть до {return_date_text}'
    })


@app.route('/api/scan/return-pack', methods=['POST'])
def return_equipment_pack():
    data = request.get_json() or {}
    pack_id = data['pack_id']

    pack_equipment = get_pack_equipment(pack_id)
    actor_type, actor_id = detect_pack_actor(pack_equipment)
    if not actor_type:
        return jsonify({'success': False, 'message': 'Пак нельзя вернуть целиком: разные владельцы или статусы'}), 400

    user = None
    guest = None
    if actor_type == 'guest':
        guest = Guest.query.get_or_404(data.get('guest_id'))
        if guest.id != actor_id:
            return jsonify({'success': False, 'message': 'Пак взят другим гостем'}), 400
    else:
        user = User.query.get_or_404(data.get('user_id'))
        if user.pin_code != data.get('pin_code'):
            return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401
        if user.id != actor_id:
            return jsonify({'success': False, 'message': 'Пак взят другим пользователем'}), 400

    for equipment in pack_equipment:
        previous_deadline = format_return_date(equipment.return_date)
        equipment.status = 'available'
        equipment.current_user_id = None
        equipment.current_guest_id = None
        equipment.return_date = None

        actor_name = user.full_name if user else f"{guest.full_name} (гость)"
        details = f"{actor_name} вернул {equipment.name} №{equipment.number} (из пака)"
        if previous_deadline:
            details += f". Дедлайн был: {previous_deadline}"

        db.session.add(Log(
            user_id=user.id if user else None,
            guest_id=guest.id if guest else None,
            equipment_id=equipment.id,
            pack_id=pack_id,
            action='return',
            details=details
        ))

    db.session.commit()
    if guest:
        deactivate_guest_if_no_equipment(guest.id)
    return jsonify({'success': True, 'message': f'Возвращено {len(pack_equipment)} предметов из пака'})


# API паков (для админа)
@app.route('/api/packs', methods=['GET'])
@login_required
def list_all_packs():
    packs = Pack.query.all()
    result = []

    for pack in packs:
        pack_equipment = get_pack_equipment(pack.id)
        result.append({
            'id': pack.id,
            'name': pack.name,
            'qr_code_path': pack.qr_code_path,
            'items_count': len(pack_equipment),
            'equipment': [{
                'id': e.id,
                'number': e.number,
                'name': e.name,
                'description': e.description,
                'general_name': e.name,  # Backward-compatible response key
                'status': e.status,
                'current_user_id': e.current_user_id,
                'current_user_name': e.current_user.full_name if e.current_user else None,
                'current_guest_id': e.current_guest_id,
                'current_guest_name': f"{e.current_guest.full_name} (гость)" if e.current_guest else None,
                'occupied_by': 'guest' if e.current_guest_id else ('user' if e.current_user_id else None),
                'return_date': format_return_date(e.return_date),
                'is_overdue': is_equipment_overdue(e)
            } for e in pack_equipment],
            'created_at': pack.created_at.isoformat()
        })

    return jsonify(result)


@app.route('/api/packs', methods=['POST'])
@login_required
def create_equipment_pack():
    data = request.get_json()
    equipment_ids = data['equipment_ids']
    pack_name = data.get('pack_name', '')
    equipment_items = Equipment.query.filter(Equipment.id.in_(equipment_ids)).all()
    equipment_by_id = {item.id: item for item in equipment_items}
    ordered_numbers = [equipment_by_id[item_id].number for item_id in equipment_ids if item_id in equipment_by_id]

    # Генерируем имя пака из номеров оборудования если не указано
    if not pack_name:
        pack_name = '_'.join(ordered_numbers)

    pack = Pack(name=pack_name)
    db.session.add(pack)
    db.session.flush()

    for eq_id in equipment_ids:
        db.session.add(PackEquipment(pack_id=pack.id, equipment_id=eq_id))

    base_url = request.url_root.rstrip('/')
    pack.qr_code_path = generate_pack_qr(pack.id, ordered_numbers, base_url)
    db.session.commit()

    add_log('create', f"Создан пак '{pack_name}' из {len(equipment_ids)} предметов", pack_id=pack.id)

    return jsonify({
        'success': True,
        'pack': {
            'id': pack.id,
            'name': pack_name,
            'qr_code': f"QR-PACK-{pack_name}"
        }
    })


@app.route('/api/packs/<int:pack_id>', methods=['DELETE'])
@login_required
def remove_equipment_pack(pack_id):
    pack = Pack.query.get_or_404(pack_id)
    pack_name = pack.name

    PackEquipment.query.filter_by(pack_id=pack_id).delete()

    # Удаляем QR-код если есть
    if pack.qr_code_path and os.path.exists(pack.qr_code_path):
        try:
            os.remove(pack.qr_code_path)
        except:
            pass

    db.session.delete(pack)
    db.session.commit()
    add_log('delete', f"Пак '{pack_name}' удалён")

    return jsonify({'success': True})


# Утилиты
@app.route('/api/backup/create', methods=['POST'])
@login_required
def trigger_backup_now():
    success = create_backup()
    if success:
        return jsonify({'success': True, 'message': 'Резервная копия создана'})
    return jsonify({'success': False, 'message': 'Ошибка создания резервной копии'}), 500


@app.route('/api/stats', methods=['GET'])
@login_required
def get_system_statistics():
    return jsonify({
        'users': User.query.count(),
        'equipment': {
            'total': Equipment.query.count(),
            'available': Equipment.query.filter_by(status='available').count(),
            'occupied': Equipment.query.filter_by(status='occupied').count(),
            'broken': Equipment.query.filter_by(status='broken').count()
        },
        'logs': Log.query.count()
    })


# Обработчики ошибок
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


# Запуск приложения
if __name__ == '__main__':
    print("=" * 50)
    print("PressKit запущен!")
    print(f"Адрес: http://localhost:{Config.PORT}/admin")
    print(f"Локальная сеть: http://<IP-адрес>:{Config.PORT}/admin")
    print(f"Логин: admin")
    print(f"Пароль: admin123")
    print("=" * 50)

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
