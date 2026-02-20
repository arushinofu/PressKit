from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_login import login_user, logout_user, login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler
import os
from datetime import datetime, timedelta
import random
import io
import csv

from config import Config
from models import db, Admin, User, Equipment, Category, Pack, PackEquipment, Log
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


@app.before_request
def configure_session():
    session.permanent = True


# Настройка автоматических бэкапов
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=create_backup,
    trigger="interval",
    hours=Config.BACKUP_INTERVAL_HOURS,
    next_run_time=datetime.now()
)
scheduler.start()
print(f"Планировщик бэкапов запущен (каждые {Config.BACKUP_INTERVAL_HOURS} часов)")
create_backup()


# Вспомогательные функции
def add_log(action, details, user_id=None, equipment_id=None, pack_id=None):
    """Создает запись в логе"""
    log = Log(
        user_id=user_id,
        equipment_id=equipment_id,
        pack_id=pack_id,
        action=action,
        details=details
    )
    db.session.add(log)
    db.session.commit()


def get_pack_equipment(pack_id):
    """Получает список оборудования в паке"""
    return db.session.query(Equipment).join(
        PackEquipment, Equipment.id == PackEquipment.equipment_id
    ).filter(PackEquipment.pack_id == pack_id).all()


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
    
    users = query.all()
    return jsonify([{
        'id': u.id,
        'full_name': u.full_name,
        'telegram': u.telegram,
        'pin_code': u.pin_code,
        'created_at': u.created_at.isoformat()
    } for u in users])


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

    current_equipment = Equipment.query.filter_by(current_user_id=user_id).all()
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
            'general_name': e.general_name,
            'specific_name': e.specific_name
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
                Equipment.general_name.ilike(f'%{search}%'),
                Equipment.specific_name.ilike(f'%{search}%'),
                Equipment.number.ilike(f'%{search}%'),
                Category.name.ilike(f'%{search}%')
            )
        )

    if category_filter:
        query = query.join(Category).filter(Category.name == category_filter)

    if status_filter:
        query = query.filter(Equipment.status == status_filter)

    equipment = query.all()
    return jsonify([{
        'id': e.id,
        'number': e.number,
        'category': e.category.name if e.category else None,
        'general_name': e.general_name,
        'specific_name': e.specific_name,
        'status': e.status,
        'current_user_id': e.current_user_id,
        'current_user_name': e.current_user.full_name if e.current_user else None,
        'qr_code_path': e.qr_code_path,
        'created_at': e.created_at.isoformat()
    } for e in equipment])


@app.route('/api/equipment', methods=['POST'])
@login_required
def add_equipment_item():
    data = request.get_json()
    
    # Получаем или создаем категорию
    category_name = data['category'].strip()
    category = Category.query.filter_by(name=category_name).first()
    if not category:
        category = Category(name=category_name)
        db.session.add(category)
        db.session.flush()

    equipment = Equipment(
        number=data['number'],
        category_id=category.id,
        general_name=data['general_name'],
        specific_name=data['specific_name'],
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

    # Обновление категории если нужно
    if 'category' in data:
        category_name = data['category'].strip()
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()
        equipment.category_id = category.id

    equipment.number = data.get('number', equipment.number)
    equipment.general_name = data.get('general_name', equipment.general_name)
    equipment.specific_name = data.get('specific_name', equipment.specific_name)
    equipment.status = data.get('status', equipment.status)

    if equipment.status == 'available':
        equipment.current_user_id = None

    db.session.commit()

    # Удаляем пустую категорию если она больше не используется
    if old_category_id and old_category_id != equipment.category_id:
        if Equipment.query.filter_by(category_id=old_category_id).count() == 0:
            old_category = Category.query.get(old_category_id)
            if old_category:
                db.session.delete(old_category)
                db.session.commit()

    add_log('edit', f"Оборудование №{equipment.number} отредактировано", equipment_id=equipment.id)

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
        'user_name': l.user.full_name if l.user else None,
        'equipment_id': l.equipment_id,
        'equipment_name': f"{l.equipment.general_name} №{l.equipment.number}" if l.equipment else None,
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
            log.user.full_name if log.user else '-',
            log.action,
            f"{log.equipment.general_name} №{log.equipment.number}" if log.equipment else '-',
            log.details or '-'
        ])

    return create_csv_response(output.getvalue(), 'presskit_logs')


# Экспорт/импорт данных
def create_csv_response(csv_data, filename):
    """Создает HTTP ответ с CSV файлом"""
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
    writer.writerow(['Категория', 'Номер', 'Общее название', 'Конкретное название', 'Статус'])

    for eq in Equipment.query.order_by(Equipment.category_id, Equipment.number).all():
        writer.writerow([
            eq.category.name if eq.category else '',
            eq.number,
            eq.general_name,
            eq.specific_name,
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
            general_name = row[2].strip()
            specific_name = row[3].strip()
            status = row[4].strip().lower()

            if not number or not general_name or not specific_name:
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
                general_name=general_name,
                specific_name=specific_name,
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

            pack.qr_code_path = generate_pack_qr(pack.id, pack_name, base_url)
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
@app.route('/scan/<int:equipment_id>')
def show_equipment_scan_page(equipment_id):
    equipment = Equipment.query.get_or_404(equipment_id)
    users = User.query.order_by(User.full_name).all()
    return render_template('user/scan.html', equipment=equipment, users=users)


@app.route('/api/scan/take', methods=['POST'])
def borrow_equipment():
    data = request.get_json()
    user = User.query.get_or_404(data['user_id'])
    equipment = Equipment.query.get_or_404(data['equipment_id'])

    if user.pin_code != data['pin_code']:
        return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    if equipment.status != 'available':
        return jsonify({'success': False, 'message': 'Оборудование недоступно'}), 400

    equipment.status = 'occupied'
    equipment.current_user_id = user.id
    db.session.commit()

    add_log('take', f"{user.full_name} взял {equipment.general_name} №{equipment.number}",
            user_id=user.id, equipment_id=equipment.id)

    return jsonify({'success': True, 'message': f'Оборудование взято: {equipment.general_name}'})


@app.route('/api/scan/return', methods=['POST'])
def return_equipment_item():
    data = request.get_json()
    user = User.query.get_or_404(data['user_id'])
    equipment = Equipment.query.get_or_404(data['equipment_id'])

    if user.pin_code != data['pin_code']:
        return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    if equipment.status != 'occupied':
        return jsonify({'success': False, 'message': 'Оборудование не занято'}), 400

    if equipment.current_user_id != user.id:
        return jsonify({'success': False, 'message': 'Это оборудование взято другим пользователем'}), 400

    equipment.status = 'available'
    equipment.current_user_id = None
    db.session.commit()

    add_log('return', f"{user.full_name} вернул {equipment.general_name} №{equipment.number}",
            user_id=user.id, equipment_id=equipment.id)

    return jsonify({'success': True, 'message': f'Оборудование возвращено: {equipment.general_name}'})


# Паки
@app.route('/scan/pack/<int:pack_id>')
def show_pack_scan_page(pack_id):
    pack = Pack.query.get_or_404(pack_id)
    pack_equipment = get_pack_equipment(pack_id)
    users = User.query.order_by(User.full_name).all()

    all_available = all(e.status == 'available' for e in pack_equipment)
    
    # Проверяем, все ли предметы заняты одним пользователем
    any_occupied_by_current = False
    if pack_equipment and pack_equipment[0].current_user_id:
        first_user_id = pack_equipment[0].current_user_id
        any_occupied_by_current = all(e.current_user_id == first_user_id for e in pack_equipment)

    return render_template('user/pack.html',
                          pack=pack,
                          pack_items=pack_equipment,
                          pack_items_ids=[e.id for e in pack_equipment],
                          users=users,
                          all_available=all_available,
                          any_occupied_by_current=any_occupied_by_current)


@app.route('/api/scan/take-pack', methods=['POST'])
def borrow_equipment_pack():
    data = request.get_json()
    user = User.query.get_or_404(data['user_id'])
    pack_id = data['pack_id']

    if user.pin_code != data['pin_code']:
        return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    pack_equipment = get_pack_equipment(pack_id)
    unavailable = [e for e in pack_equipment if e.status != 'available']
    
    if unavailable:
        return jsonify({'success': False, 'message': 'Некоторые предметы недоступны'}), 400

    for equipment in pack_equipment:
        equipment.status = 'occupied'
        equipment.current_user_id = user.id
        add_log('take', f"{user.full_name} взял {equipment.general_name} №{equipment.number} (из пака)",
                user_id=user.id, equipment_id=equipment.id, pack_id=pack_id)

    db.session.commit()
    return jsonify({'success': True, 'message': f'Взято {len(pack_equipment)} предметов из пака'})


@app.route('/api/scan/return-pack', methods=['POST'])
def return_equipment_pack():
    data = request.get_json()
    user = User.query.get_or_404(data['user_id'])
    pack_id = data['pack_id']

    if user.pin_code != data['pin_code']:
        return jsonify({'success': False, 'message': 'Неверный PIN-код'}), 401

    pack_equipment = get_pack_equipment(pack_id)
    not_by_user = [e for e in pack_equipment if e.current_user_id != user.id]
    
    if not_by_user:
        return jsonify({'success': False, 'message': 'Не все предметы взяты вами'}), 400

    for equipment in pack_equipment:
        equipment.status = 'available'
        equipment.current_user_id = None
        add_log('return', f"{user.full_name} вернул {equipment.general_name} №{equipment.number} (из пака)",
                user_id=user.id, equipment_id=equipment.id, pack_id=pack_id)

    db.session.commit()
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
                'general_name': e.general_name,
                'status': e.status
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

    # Генерируем имя пака из номеров оборудования если не указано
    if not pack_name:
        equipment_items = Equipment.query.filter(Equipment.id.in_(equipment_ids)).all()
        pack_name = '_'.join([e.number for e in equipment_items])

    pack = Pack(name=pack_name)
    db.session.add(pack)
    db.session.flush()

    for eq_id in equipment_ids:
        db.session.add(PackEquipment(pack_id=pack.id, equipment_id=eq_id))

    base_url = request.url_root.rstrip('/')
    pack.qr_code_path = generate_pack_qr(pack.id, pack_name, base_url)
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
