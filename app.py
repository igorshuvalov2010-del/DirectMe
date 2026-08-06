from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os, hashlib, json, logging, re
from functools import wraps
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from markupsafe import escape
from threading import Lock

# ========== КОНФИГУРАЦИЯ ==========
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'shugramm-' + str(random.randint(10000, 99999)))
    MAX_BUFFER_SIZE = 100 * 1024 * 1024
    MAX_MESSAGES = 200
    MAX_POSTS = 50
    MAX_MESSAGE_LENGTH = 2000
    MAX_POST_LENGTH = 500000
    MAX_COMMENT_LENGTH = 300
    MAX_BIO_LENGTH = 200
    MAX_USERNAME_LENGTH = 20
    MIN_USERNAME_LENGTH = 2
    MIN_PASSWORD_LENGTH = 4
    DATA_FILE = 'shugramm_data.json'
    PORT = int(os.environ.get('PORT', 5000))
    DEBUG = False

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ПРИЛОЖЕНИЕ ==========
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    max_http_buffer_size=Config.MAX_BUFFER_SIZE,
    ping_timeout=60,
    ping_interval=25
)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def hash_password(password):
    """Хеширование пароля с солью"""
    salt = os.urandom(32).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(password, hashed):
    """Проверка пароля"""
    salt, hash_value = hashed.split(':')
    return hash_value == hashlib.sha256((salt + password).encode()).hexdigest()

def generate_token():
    """Генерация токена"""
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]

def generate_id():
    """Генерация ID"""
    return f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

def sanitize(text):
    """Очистка HTML"""
    return escape(str(text))

# ========== ХРАНИЛИЩЕ ДАННЫХ ==========
class DataStore:
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.users = {}
            self.posts = []
            self.chats = {'general': {'id': 'general', 'name': 'Общий чат', 'members': set(), 'messages': [], 'admins': set()}}
            self.pending_codes = {}
            self.unread = {}
            self.banned_users = set()
            self.initialized = True
            self._load_data()
    
    def _load_data(self):
        try:
            if os.path.exists(Config.DATA_FILE):
                with open(Config.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.users = data.get('users', {})
                    self.posts = data.get('posts', [])
                    self.chats = data.get('chats', {'general': {'id': 'general', 'name': 'Общий чат', 'members': set(), 'messages': [], 'admins': set()}})
                    self.unread = data.get('unread', {})
                    self.banned_users = set(data.get('banned_users', []))
                    # Восстановление set для members
                    for chat_id in self.chats:
                        self.chats[chat_id]['members'] = set(self.chats[chat_id].get('members', []))
                        self.chats[chat_id]['admins'] = set(self.chats[chat_id].get('admins', []))
                    logger.info(f"Загружено: {len(self.users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
    
    def _save_data(self):
        try:
            data = {
                'users': self.users,
                'posts': self.posts,
                'chats': {k: {**v, 'members': list(v['members']), 'admins': list(v.get('admins', set()))} for k, v in self.chats.items()},
                'unread': self.unread,
                'banned_users': list(self.banned_users)
            }
            with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def create_user(self, name, phone, password, sid):
        if name in self.users:
            return None
        self.users[name] = {
            'sid': sid,
            'phone': phone,
            'password': hash_password(password),
            'avatar': None,
            'status': 'онлайн',
            'bio': '',
            'lang': 'ru',
            'token': generate_token(),
            'last_seen': time.time(),
            'messages_count': 0
        }
        self.unread[name] = {}
        self.chats['general']['members'].add(name)
        self._save_data()
        return self.users[name]
    
    def verify_user(self, name, password):
        user = self.users.get(name)
        if not user:
            return None
        if name in self.banned_users:
            return None
        if verify_password(password, user['password']):
            user['token'] = generate_token()
            user['last_seen'] = time.time()
            self._save_data()
            return user
        return None
    
    def get_user_by_token(self, token):
        for name, user in self.users.items():
            if user.get('token') == token:
                return user
        return None
    
    def get_user_by_sid(self, sid):
        for name, user in self.users.items():
            if user.get('sid') == sid:
                return user
        return None
    
    def add_message(self, chat_id, message):
        if chat_id not in self.chats:
            return False
        self.chats[chat_id]['messages'].append(message)
        if len(self.chats[chat_id]['messages']) > Config.MAX_MESSAGES:
            self.chats[chat_id]['messages'] = self.chats[chat_id]['messages'][-Config.MAX_MESSAGES:]
        self._save_data()
        return True
    
    def get_chat_messages(self, chat_id):
        return self.chats.get(chat_id, {}).get('messages', [])[-Config.MAX_MESSAGES:]
    
    def add_post(self, post):
        self.posts.insert(0, post)
        if len(self.posts) > Config.MAX_POSTS:
            self.posts.pop()
        self._save_data()
    
    def delete_post(self, post_id, username):
        for i, p in enumerate(self.posts):
            if p['id'] == post_id and (p['author'] == username or username in self.chats['general'].get('admins', set())):
                self.posts.pop(i)
                self._save_data()
                return True
        return False
    
    def like_post(self, post_id, username):
        for p in self.posts:
            if p['id'] == post_id:
                if username in p.get('likes', []):
                    p['likes'].remove(username)
                else:
                    p['likes'].append(username)
                self._save_data()
                return True
        return False
    
    def add_comment(self, post_id, username, comment, avatar):
        for p in self.posts:
            if p['id'] == post_id:
                p.setdefault('comments', []).append({
                    'n': username,
                    'a': avatar,
                    'c': sanitize(comment[:Config.MAX_COMMENT_LENGTH]),
                    'ts': datetime.now().strftime("%H:%M"),
                    'id': generate_id()
                })
                self._save_data()
                return True
        return False
    
    def get_online_users(self):
        return [{'n': n, 'a': u.get('avatar')} for n, u in self.users.items() if u.get('status') == 'онлайн' and u.get('sid')]
    
    def get_stats(self):
        return {
            'total_users': len(self.users),
            'online_users': len([u for u in self.users.values() if u.get('status') == 'онлайн']),
            'total_posts': len(self.posts),
            'total_chats': len(self.chats),
            'total_messages': sum(len(c.get('messages', [])) for c in self.chats.values()),
            'banned_users': len(self.banned_users)
        }

# ========== ИНИЦИАЛИЗАЦИЯ ==========
store = DataStore()

# ========== ДЕКОРАТОРЫ ==========
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get('token') or request.headers.get('X-Auth-Token') or request.cookies.get('token')
        if not token:
            return jsonify({'error': 'Требуется авторизация'}), 401
        user = store.get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Недействительный токен'}), 401
        return f(user=user, *args, **kwargs)
    return decorated

# ========== HTTP РОУТЫ ==========
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/stats')
@require_auth
def get_stats(user):
    return jsonify(store.get_stats())

@app.route('/api/users')
@require_auth
def get_users(user):
    users_list = []
    for name, u in store.users.items():
        if name != user.get('name'):
            users_list.append({
                'name': name,
                'avatar': u.get('avatar'),
                'status': u.get('status', 'оффлайн'),
                'bio': u.get('bio', '')
            })
    return jsonify({'users': users_list})

@app.route('/delete_post', methods=['POST'])
@require_auth
def delete_post(user):
    data = request.get_json()
    post_id = data.get('pid', '')
    if store.delete_post(post_id, user.get('name')):
        return jsonify({'ok': True})
    return jsonify({'error': 'Пост не найден'}), 404

# ========== SOCKET.IO СОБЫТИЯ ==========
@socketio.on('connect')
def handle_connect():
    logger.info(f"Клиент подключен: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    user = store.get_user_by_sid(request.sid)
    if user:
        user['status'] = 'оффлайн'
        user['sid'] = ''
        store._save_data()
        emit('nu_user', {
            'n': user.get('name'),
            'a': user.get('avatar'),
            'st': 'оффлайн'
        }, broadcast=True)

@socketio.on('rc')
def request_code(data):
    phone = ''.join(filter(str.isdigit, data.get('p', '')))
    if len(phone) < 10:
        emit('er', {'m': 'Введите корректный номер'})
        return
    code = str(random.randint(100000, 999999))
    store.pending_codes[phone] = {'code': code, 'timestamp': time.time()}
    logger.info(f"Код для {phone}: {code}")
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def verify_code(data):
    phone = data.get('d', '')
    code = data.get('c', '')
    if phone not in store.pending_codes:
        emit('er', {'m': 'Сессия истекла'})
        return
    pending = store.pending_codes[phone]
    if time.time() - pending['timestamp'] > 300:
        del store.pending_codes[phone]
        emit('er', {'m': 'Код истек'})
        return
    if code != pending['code']:
        emit('er', {'m': 'Неверный код'})
        return
    del store.pending_codes[phone]
    for name, user in store.users.items():
        if user.get('phone') == phone:
            emit('ue', {'n': name})
            return
    emit('nu', {'d': phone})

@socketio.on('sp')
def set_password(data):
    phone = data.get('d', '')
    password = data.get('p', '').strip()
    name = data.get('n', '').strip()
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_]{2,20}$', name):
        emit('er', {'m': 'Имя: 2-20 символов (буквы, цифры, _)'})
        return
    if name in store.users:
        emit('er', {'m': 'Пользователь уже существует'})
        return
    if len(password) < Config.MIN_PASSWORD_LENGTH:
        emit('er', {'m': f'Пароль минимум {Config.MIN_PASSWORD_LENGTH} символов'})
        return
    user = store.create_user(name, phone, password, request.sid)
    if not user:
        emit('er', {'m': 'Ошибка регистрации'})
        return
    user['name'] = name
    join_room('general')
    emit('ro', {'n': name, 'a': None, 'token': user['token']})
    emit('nu_user', {'n': name, 'a': None, 'st': 'онлайн'}, broadcast=True)

@socketio.on('li')
def login(data):
    name = data.get('n', '').strip()
    password = data.get('p', '').strip()
    user = store.verify_user(name, password)
    if not user:
        emit('er', {'m': 'Неверное имя или пароль'})
        return
    user['sid'] = request.sid
    user['status'] = 'онлайн'
    user['name'] = name
    join_room('general')
    store._save_data()
    emit('lo', {'n': name, 'a': user.get('avatar'), 'token': user['token']})
    emit('nu_user', {'n': name, 'a': user.get('avatar'), 'st': 'онлайн'}, broadcast=True)

@socketio.on('auto_login')
def auto_login(data):
    token = data.get('token', '')
    user = store.get_user_by_token(token)
    if not user:
        return
    user['sid'] = request.sid
    user['status'] = 'онлайн'
    user['name'] = [n for n, u in store.users.items() if u.get('token') == token][0]
    join_room('general')
    store._save_data()
    emit('lo', {'n': user['name'], 'a': user.get('avatar'), 'token': token})
    emit('nu_user', {'n': user['name'], 'a': user.get('avatar'), 'st': 'онлайн'}, broadcast=True)

@socketio.on('sm')
def send_message(data):
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_type = data.get('t', 'text')
    content = data.get('c', '')
    user = store.users.get(username)
    if not user:
        return
    if chat_id not in store.chats:
        return
    if username not in store.chats[chat_id]['members'] and chat_id != 'general':
        emit('er', {'m': 'Нет доступа'})
        return
    if msg_type == 'text':
        content = sanitize(content.strip())
        if not content:
            return
        if len(content) > Config.MAX_MESSAGE_LENGTH:
            content = content[:Config.MAX_MESSAGE_LENGTH]
    msg = {
        'i': f"m{int(time.time()*1000)}",
        'n': username,
        't': msg_type,
        'c': content,
        'ts': datetime.now().strftime("%H:%M"),
        'a': user.get('avatar'),
        'edited': False
    }
    if store.add_message(chat_id, msg):
        emit('nm', {'ch': chat_id, 'm': msg}, room=chat_id)
        for member in store.chats[chat_id]['members']:
            if member != username:
                store.unread.setdefault(member, {})
                store.unread[member][chat_id] = store.unread[member].get(chat_id, 0) + 1
                target = store.users.get(member)
                if target and target.get('sid'):
                    emit('notify', {
                        'ch': chat_id,
                        'n': username,
                        'c': content[:30] + ('...' if len(content) > 30 else '')
                    }, room=target['sid'])

@socketio.on('jc')
def join_chat(data):
    chat_id = data.get('ch', 'general')
    username = data.get('n', '')
    if username not in store.users:
        return
    join_room(chat_id)
    if username in store.unread:
        store.unread[username][chat_id] = 0
    messages = store.get_chat_messages(chat_id)
    emit('ch', {'ms': messages})

@socketio.on('gu')
def get_users(data):
    username = data.get('n', '')
    users_list = []
    for name, user in store.users.items():
        if name != username:
            users_list.append({
                'n': name,
                'a': user.get('avatar'),
                'st': user.get('status', 'оффлайн'),
                'bio': user.get('bio', '')
            })
    users_list.sort(key=lambda x: (x['st'] != 'онлайн', x['n']))
    emit('ul', {'u': users_list})

@socketio.on('sp2')
def start_private_chat(data):
    user1 = data.get('n', '')
    user2 = data.get('t', '')
    if user1 not in store.users or user2 not in store.users:
        return
    chat_id = f"p_{min(user1, user2)}_{max(user1, user2)}"
    if chat_id not in store.chats:
        store.chats[chat_id] = {
            'id': chat_id,
            'name': user2,
            'members': {user1, user2},
            'messages': [],
            'admins': set()
        }
        store._save_data()
    join_room(chat_id)
    if user1 in store.unread:
        store.unread[user1][chat_id] = 0
    target = store.users[user2]
    messages = store.get_chat_messages(chat_id)
    emit('po', {
        'ch': chat_id,
        't': user2,
        'a': target.get('avatar'),
        'ms': messages
    })

@socketio.on('ua')
def update_avatar(data):
    username = data.get('n', '')
    avatar = data.get('a', '')
    if username in store.users:
        store.users[username]['avatar'] = avatar
        store._save_data()
        emit('avatar_updated', {'n': username, 'a': avatar}, broadcast=True)

@socketio.on('ub')
def update_bio(data):
    username = data.get('n', '')
    bio = data.get('b', '')[:Config.MAX_BIO_LENGTH]
    if username in store.users:
        store.users[username]['bio'] = sanitize(bio)
        store._save_data()
        emit('bio_updated', {'n': username, 'b': store.users[username]['bio']})

@socketio.on('ul2')
def update_language(data):
    username = data.get('n', '')
    lang = data.get('l', 'ru')
    if username in store.users:
        store.users[username]['lang'] = lang
        store._save_data()

@socketio.on('cp')
def create_post(data):
    username = data.get('n', '')
    content = data.get('m', '')
    media_type = data.get('mt', 'image')
    caption = data.get('c', '')[:500]
    user = store.users.get(username)
    if not user:
        return
    if len(content) > Config.MAX_POST_LENGTH:
        content = content[:Config.MAX_POST_LENGTH]
    post = {
        'id': f"p_{int(time.time()*1000)}_{random.randint(1000,9999)}",
        'author': username,
        'avatar': user.get('avatar'),
        'media_url': content,
        'media_type': media_type,
        'caption': sanitize(caption),
        'likes': [],
        'comments': [],
        'timestamp': time.time(),
        'ts': datetime.now().strftime("%d.%m.%Y %H:%M")
    }
    store.add_post(post)
    post['likes_count'] = 0
    post['comments_count'] = 0
    emit('np', {'p': post}, broadcast=True)

@socketio.on('gp')
def get_posts():
    posts = []
    for p in store.posts[:Config.MAX_POSTS]:
        posts.append({
            **p,
            'likes_count': len(p.get('likes', [])),
            'comments_count': len(p.get('comments', []))
        })
    emit('pl', {'p': posts})

@socketio.on('lp')
def like_post(data):
    post_id = data.get('pid', '')
    username = data.get('n', '')
    if store.like_post(post_id, username):
        for p in store.posts:
            if p['id'] == post_id:
                emit('pu', {'p': {**p, 'likes_count': len(p.get('likes', [])), 'comments_count': len(p.get('comments', []))}}, broadcast=True)
                break

@socketio.on('cmp')
def comment_post(data):
    post_id = data.get('pid', '')
    username = data.get('n', '')
    comment = data.get('c', '')[:Config.MAX_COMMENT_LENGTH]
    user = store.users.get(username)
    if not user:
        return
    if store.add_comment(post_id, username, comment, user.get('avatar')):
        for p in store.posts:
            if p['id'] == post_id:
                emit('pu', {'p': {**p, 'likes_count': len(p.get('likes', [])), 'comments_count': len(p.get('comments', []))}}, broadcast=True)
                break

@socketio.on('sh')
def share_link():
    emit('sl', {'l': request.host})

@socketio.on('logout')
def logout(data):
    token = data.get('token', '')
    user = store.get_user_by_token(token)
    if user:
        user['token'] = ''
        user['status'] = 'оффлайн'
        user['sid'] = ''
        store._save_data()
        emit('nu_user', {'n': user.get('name'), 'a': user.get('avatar'), 'st': 'оффлайн'}, broadcast=True)

@socketio.on('typing')
def typing(data):
    chat_id = data.get('ch', 'general')
    username = data.get('n', '')
    is_typing = data.get('typing', False)
    emit('typing_status', {'n': username, 'typing': is_typing}, room=chat_id, include_self=False)

@socketio.on('get_online_users')
def get_online_users():
    online = store.get_online_users()
    emit('online_users', {'users': online})

@socketio.on('delete_message')
def delete_message(data):
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_id = data.get('mid', '')
    if chat_id in store.chats:
        messages = store.chats[chat_id]['messages']
        for i, msg in enumerate(messages):
            if msg.get('i') == msg_id and msg.get('n') == username:
                messages.pop(i)
                store._save_data()
                emit('message_deleted', {'ch': chat_id, 'mid': msg_id}, room=chat_id)
                break

@socketio.on('edit_message')
def edit_message(data):
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_id = data.get('mid', '')
    new_content = data.get('c', '')
    if chat_id in store.chats:
        for msg in store.chats[chat_id]['messages']:
            if msg.get('i') == msg_id and msg.get('n') == username:
                msg['c'] = sanitize(new_content[:Config.MAX_MESSAGE_LENGTH])
                msg['edited'] = True
                store._save_data()
                emit('message_edited', {'ch': chat_id, 'm': msg}, room=chat_id)
                break

# ========== HTML ==========
HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Shugramm</title>
<style>
:root{--bg:#0d0d0d;--bg2:#1a1a1a;--bg3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff;--b:#3a3a3a;--gr:#4CAF50;--r:#f44}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w);overflow:hidden}
.app{width:100%;max-width:480px;height:100vh;background:var(--bg);display:flex;flex-direction:column;position:relative}
.notification{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:400;background:var(--bg2);padding:12px 20px;border-radius:0 0 12px 12px;font-size:14px;max-width:90%;display:none;border-bottom:3px solid var(--y)}
.notification.show{display:block;animation:slideDown .3s}
@keyframes slideDown{from{transform:translateX(-50%) translateY(-100%)}to{transform:translateX(-50%) translateY(0)}}
.header{background:var(--bg2);padding:8px 16px;display:flex;align-items:center;border-bottom:1px solid var(--b);min-height:44px;flex-shrink:0}
.header-title{font-weight:600;font-size:17px;flex:1}
.header-title .logo{color:var(--y);font-weight:800}
.btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;padding:6px;border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center}
.btn:active{background:var(--bg3)}
.nav{background:var(--bg2);display:flex;border-top:1px solid var(--b);padding:4px 0;flex-shrink:0}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:1px;cursor:pointer;color:var(--g);font-size:10px;padding:6px 4px}
.nav-item.active{color:var(--y)}
.nav-item svg{width:22px;height:22px}
.content{flex:1;overflow-y:auto;display:none}
.content.active{display:block}
.list-item{display:flex;align-items:center;padding:10px 16px;gap:10px;cursor:pointer}
.list-item:active{background:var(--bg3)}
.avatar{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:19px;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.avatar img{width:100%;height:100%;object-fit:cover}
.list-info{flex:1;min-width:0;border-bottom:1px solid rgba(255,255,255,.05);padding-bottom:10px}
.list-name{font-weight:500;font-size:15px}
.list-preview{font-size:13px;color:var(--g);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.unread-badge{background:var(--y);color:#000;font-size:11px;font-weight:700;min-width:20px;height:20px;border-radius:10px;display:flex;align-items:center;justify-content:center;padding:0 6px}
.msg-row{display:flex;gap:4px;margin-bottom:2px;padding:2px 14px}
.msg-row.mine{flex-direction:row-reverse}
.msg-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.msg-avatar img{width:100%;height:100%;object-fit:cover}
.msg-bubble{max-width:75%;padding:7px 10px;border-radius:14px;font-size:14px;line-height:1.4;word-wrap:break-word;background:var(--bg3)}
.msg-row.mine .msg-bubble{background:var(--y);color:#000}
.msg-bubble img{max-width:200px;border-radius:8px;cursor:pointer;display:block}
.msg-bubble video{max-width:200px;border-radius:8px;display:block}
.msg-time{font-size:10px;color:var(--g);text-align:right;margin-top:1px}
.msg-row.mine .msg-time{color:rgba(0,0,0,.5)}
.input-bar{display:flex;padding:6px 10px;background:var(--bg2);border-top:1px solid var(--b);gap:6px;align-items:center;flex-shrink:0}
.input-bar input{flex:1;padding:9px 14px;background:var(--bg3);border:1px solid var(--b);border-radius:18px;color:var(--w);font-size:14px;outline:none}
.input-bar input:focus{border-color:var(--y)}
.send-btn{width:34px;height:34px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.post-card{background:var(--bg2);margin-bottom:12px}
.post-header{display:flex;align-items:center;padding:10px 14px;gap:8px}
.post-avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:14px;color:#000;overflow:hidden;background:var(--y)}
.post-avatar img{width:100%;height:100%;object-fit:cover}
.post-user{font-weight:500;font-size:14px}
.post-date{font-size:11px;color:var(--g)}
.post-media{width:100%;max-height:400px;object-fit:cover;cursor:pointer;display:block}
.post-actions{display:flex;padding:8px 14px;gap:20px}
.post-action{background:none;border:none;color:var(--w);cursor:pointer;display:flex;align-items:center;gap:5px;font-size:13px;padding:0}
.post-action.liked{color:var(--r)}
.post-caption{padding:0 14px 8px;font-size:13px}
.post-comments{padding:0 14px 8px}
.comment-row{display:flex;gap:6px;margin-bottom:3px;font-size:12px}
.comment-avatar{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:#000;overflow:hidden;background:var(--y)}
.comment-avatar img{width:100%;height:100%;object-fit:cover}
.comment-body{flex:1}
.comment-input{display:flex;padding:8px 14px;border-top:1px solid var(--b);gap:8px}
.comment-input input{flex:1;background:none;border:none;color:var(--w);font-size:13px;outline:none}
.comment-input button{background:none;border:none;color:var(--y);font-weight:600;cursor:pointer;font-size:13px}
.profile-section{text-align:center;padding:24px;background:var(--bg2);margin:8px;border-radius:12px}
.profile-avatar{width:80px;height:80px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:600;color:#000;margin:0 auto 10px;cursor:pointer;overflow:hidden;background:var(--y)}
.profile-avatar img{width:100%;height:100%;object-fit:cover}
.profile-name{font-size:18px;font-weight:600}
.profile-bio{color:var(--g);font-size:13px;margin-top:4px}
.settings-group{padding:8px}
.setting-item{display:flex;justify-content:space-between;align-items:center;padding:14px;background:var(--bg2);margin-bottom:6px;border-radius:10px;cursor:pointer}
.setting-item:active{background:var(--bg3)}
.setting-label{font-size:14px}
.setting-value{color:var(--g);font-size:13px}
.login-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
.login-card{text-align:center;padding:28px 20px;width:90%;max-width:340px}
.login-logo{width:72px;height:72px;background:var(--y);border-radius:18px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:30px;color:#000;font-weight:800}
.login-card h1{font-size:24px;font-weight:700}
.login-card p{color:var(--g);font-size:13px;margin:4px 0 18px}
.form-input{width:100%;padding:12px 14px;background:var(--bg2);border:1px solid var(--b);border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center}
.form-input:focus{border-color:var(--y)}
.form-btn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}
.form-link{background:none;border:none;color:var(--y);font-size:13px;cursor:pointer;margin-top:10px}
.code-box{background:var(--bg3);padding:12px;border-radius:8px;font-size:26px;letter-spacing:8px;font-weight:600;color:var(--y);margin:10px 0}
.hidden{display:none!important}
.media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:#000;z-index:300;display:none;align-items:center;justify-content:center}
.media-viewer.show{display:flex}
.media-viewer img{max-width:100%;max-height:100vh;object-fit:contain}
.media-close{position:absolute;top:14px;right:14px;width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.15);border:none;color:#fff;font-size:18px;cursor:pointer}
.fab{position:fixed;bottom:76px;right:14px;width:48px;height:48px;border-radius:14px;background:var(--y);color:#000;border:none;font-size:22px;cursor:pointer;z-index:10;display:none;align-items:center;justify-content:center;box-shadow:0 2px 12px rgba(255,215,0,.3)}
.fab.show{display:flex}
.typing-indicator{font-size:12px;color:var(--g);padding:4px 14px;font-style:italic;min-height:20px}
.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:500;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-content{background:var(--bg2);border-radius:16px;padding:24px;max-width:340px;width:90%;max-height:80vh;overflow-y:auto}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}
.stats-item{background:var(--bg3);padding:12px;border-radius:8px;text-align:center}
.stats-item .value{font-size:24px;font-weight:700;color:var(--y)}
.stats-item .label{font-size:11px;color:var(--g);margin-top:2px}
.empty-state{text-align:center;padding:40px;color:var(--g)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.toast-container{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:600;display:flex;flex-direction:column;gap:6px;max-width:90%}
.toast{padding:10px 16px;background:var(--bg2);border-radius:10px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.5);animation:fadeIn .3s;border-left:3px solid var(--y)}
.toast.error{border-left-color:var(--r)}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="app">
<div class="notification" id="notification"></div>
<div class="toast-container" id="toastContainer"></div>
<div class="header">
<div class="header-title"><span class="logo">⚡</span> Shugramm <span id="onlineCount" style="font-size:11px;color:var(--g);font-weight:400"></span></div>
<button class="btn" onclick="share()">📤</button>
</div>
<div class="content active" id="chatsContent"></div>
<div class="content" id="usersContent"></div>
<div class="content" id="postsContent"></div>
<div class="content" id="settingsContent"></div>
<div id="chatWindow" class="hidden" style="flex:1;display:none;flex-direction:column;min-height:0">
<div class="header">
<button class="btn" onclick="closeChat()">←</button>
<span style="font-weight:500;flex:1" id="chatTitle"></span>
<button class="btn" onclick="searchInChat()">🔍</button>
</div>
<div id="messages" style="flex:1;overflow-y:auto;padding:6px 0"></div>
<div class="typing-indicator" id="typingIndicator"></div>
<div class="input-bar">
<button class="btn" onclick="document.getElementById('fileInput').click()">📎</button>
<input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMsg()" oninput="handleTyping()">
<button class="send-btn" onclick="sendMsg()">➤</button>
</div>
</div>
<button class="fab" id="fab" onclick="createPost()">+</button>
<div class="nav" id="nav" style="display:none">
<div class="nav-item active" onclick="switchTab('chats')">💬 Чаты</div>
<div class="nav-item" onclick="switchTab('users')">👤 Контакты</div>
<div class="nav-item" onclick="switchTab('posts')">📸 Посты</div>
<div class="nav-item" onclick="switchTab('settings')">⚙️ Ещё</div>
</div>
<div class="media-viewer" id="mediaViewer">
<button class="media-close" onclick="closeMedia()">✕</button>
<img id="mediaImg" style="display:none">
<video id="mediaVid" controls style="display:none"></video>
</div>
<div class="modal" id="statsModal">
<div class="modal-content">
<h2 style="text-align:center">📊 Статистика</h2>
<div id="statsContent"></div>
<button onclick="closeStats()" style="width:100%;padding:10px;background:var(--bg3);border:none;color:var(--w);border-radius:10px;margin-top:12px;cursor:pointer">Закрыть</button>
</div>
</div>
<div class="modal" id="searchModal">
<div class="modal-content">
<h2>🔍 Поиск</h2>
<input type="text" id="searchChatInput" placeholder="Поиск в чате..." oninput="searchMessages()" style="width:100%;padding:8px;background:var(--bg3);border:1px solid var(--b);border-radius:8px;color:var(--w);margin:8px 0;outline:none">
<div id="searchResults"></div>
<button onclick="closeSearch()" style="width:100%;padding:10px;background:var(--bg3);border:none;color:var(--w);border-radius:10px;cursor:pointer">Закрыть</button>
</div>
</div>
<div class="login-screen" id="loginScreen">
<div class="login-card">
<div id="step1"><div class="login-logo">⚡</div><h1>Shugramm</h1><p>Введите номер телефона</p><input type="tel" class="form-input" id="phoneInput" placeholder="+7 999 123-45-67"><button class="form-btn" onclick="requestCode()">Получить код</button></div>
<div id="step2" class="hidden"><div class="login-logo">⚡</div><h1>Код</h1><p>Отправлен на <span id="phoneDisplay" style="color:var(--y)"></span></p><div class="code-box" id="codeDisplay"></div><input type="text" class="form-input" id="codeInput" placeholder="••••••" maxlength="6"><button class="form-btn" onclick="verifyCode()">Подтвердить</button><button class="form-link" onclick="backToPhone()">Изменить номер</button></div>
<div id="step3" class="hidden"><div class="login-logo">⚡</div><h1>Регистрация</h1><input type="password" class="form-input" id="passwordInput" placeholder="Пароль (мин. 4)"><input type="text" class="form-input" id="nameInput" placeholder="Имя (2-20 символов)"><button class="form-btn" onclick="setPassword()">Зарегистрироваться</button></div>
<div id="step4" class="hidden"><div class="login-logo">⚡</div><h1>Вход</h1><p id="loginUsername" style="color:var(--y)"></p><input type="password" class="form-input" id="loginPassword" placeholder="Пароль"><button class="form-btn" onclick="loginUser()">Войти</button><button class="form-link" onclick="backToStart()">Назад</button></div>
</div>
</div>
</div>
<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
var s=io(), u=null, ua=null, ch='general', pd='', token='', userBio='', unreadData={};
var typingTimeout=null, searchTimeout=null;
var savedToken=localStorage.getItem('shugramm_token')||'';
if(savedToken)s.emit('auto_login',{token:savedToken});

function notify(m){var n=document.getElementById('notification');n.textContent=m;n.classList.add('show');setTimeout(function(){n.classList.remove('show')},3000);}
function toast(m,t){var c=document.getElementById('toastContainer');var d=document.createElement('div');d.className='toast'+(t==='error'?' error':'');d.textContent=m;c.appendChild(d);setTimeout(function(){d.remove()},3000);}

function requestCode(){var p=document.getElementById('phoneInput').value.trim();if(p.length<10){notify('Введите номер');return}s.emit('rc',{p:p});}
function verifyCode(){var c=document.getElementById('codeInput').value.trim();if(c.length!==6){notify('Введите 6 цифр');return}s.emit('vc',{d:pd,c:c});}
function setPassword(){var p=document.getElementById('passwordInput').value.trim(),n=document.getElementById('nameInput').value.trim();if(!p||p.length<4){notify('Пароль минимум 4 символа');return}if(!n||n.length<2){notify('Имя минимум 2 символа');return}if(!/^[a-zA-Zа-яА-Я0-9_]{2,20}$/.test(n)){notify('Недопустимые символы в имени');return}s.emit('sp',{d:pd,p:p,n:n});}
function loginUser(){var p=document.getElementById('loginPassword').value.trim();if(!p)return;s.emit('li',{n:document.getElementById('loginUsername').textContent,p:p});}
function backToPhone(){document.getElementById('step2').classList.add('hidden');document.getElementById('step1').classList.remove('hidden');}
function backToStart(){document.getElementById('step4').classList.add('hidden');document.getElementById('step1').classList.remove('hidden');}

s.on('cs',function(d){pd=d.d;document.getElementById('step1').classList.add('hidden');document.getElementById('step2').classList.remove('hidden');document.getElementById('phoneDisplay').textContent='+'+d.d;document.getElementById('codeDisplay').textContent=d.c;});
s.on('ue',function(d){document.getElementById('step2').classList.add('hidden');document.getElementById('step4').classList.remove('hidden');document.getElementById('loginUsername').textContent=d.n;});
s.on('nu',function(d){pd=d.d;document.getElementById('step2').classList.add('hidden');document.getElementById('step3').classList.remove('hidden');});
s.on('ro',function(d){u=d.n;ua=d.a;token=d.token;localStorage.setItem('shugramm_token',token);enterApp();});
s.on('lo',function(d){u=d.n;ua=d.a;token=d.token;localStorage.setItem('shugramm_token',token);enterApp();});
s.on('er',function(d){notify(d.m);});
s.on('notify',function(d){toast(d.n+': '+d.c);loadChats();});
s.on('avatar_updated',function(d){if(u&&d.n!==u){loadChats();if(document.getElementById('usersContent').classList.contains('active')){s.emit('gu',{n:u});}}});
s.on('bio_updated',function(d){if(d.n===u){userBio=d.b;loadSettings();}});
s.on('typing_status',function(d){if(d.n!==u){document.getElementById('typingIndicator').textContent=d.typing?d.n+' печатает...':'';}});
s.on('online_users',function(d){document.getElementById('onlineCount').textContent='● '+d.users.length;});
s.on('nu_user',function(d){loadChats();if(document.getElementById('usersContent').classList.contains('active')){s.emit('gu',{n:u});}});
s.on('message_deleted',function(d){if(d.ch===ch){var msgs=document.getElementById('messages').children;for(var i=0;i<msgs.length;i++){if(msgs[i].dataset.mid===d.mid){msgs[i].remove();}}}});
s.on('message_edited',function(d){if(d.ch===ch){var msgs=document.getElementById('messages').children;for(var i=0;i<msgs.length;i++){if(msgs[i].dataset.mid===d.m.i){msgs[i].querySelector('.msg-bubble').innerHTML=d.m.c;}}}});
s.on('search_results',function(d){var el=document.getElementById('searchResults');if(!d.results||d.results.length===0){el.innerHTML='<div style="color:var(--g);padding:8px">Ничего не найдено</div>';return;}el.innerHTML=d.results.map(function(m){return '<div style="padding:6px;border-bottom:1px solid var(--b);font-size:13px"><b>'+m.n+'</b>: '+m.c+' <span style="color:var(--g);font-size:10px">'+m.ts+'</span></div>';}).join('');});

function enterApp(){document.getElementById('loginScreen').classList.add('hidden');document.getElementById('nav').style.display='flex';loadChats();s.emit('get_online_users');setInterval(function(){s.emit('get_online_users')},30000);}

function loadChats(){
var h='<div class="list-item" onclick="openChat(\'general\',\'Общий чат\')"><div class="avatar">#</div><div class="list-info"><div class="list-name">Общий чат</div><div class="list-preview">Нажмите чтобы открыть</div></div></div>';
var chats=JSON.parse(localStorage.getItem('private_chats')||'[]');
for(var i=0;i<chats.length;i++){var c=chats[i];var ur=unreadData[c.id]||0;h+='<div class="list-item" onclick="openChat(\''+c.id+'\',\''+c.name+'\')"><div class="avatar">'+c.avatar+'</div><div class="list-info"><div class="list-name">'+c.name+'</div><div class="list-preview">'+c.lastMsg+'</div></div>'+(ur>0?'<div class="unread-badge">'+ur+'</div>':'')+'</div>';}
document.getElementById('chatsContent').innerHTML=h;
}

function switchTab(t){
document.querySelectorAll('.content').forEach(function(c){c.classList.remove('active');});
document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active');});
document.getElementById('fab').classList.remove('show');
document.getElementById('chatWindow').classList.add('hidden');
document.getElementById('chatWindow').style.display='none';
if(t==='chats'){document.getElementById('chatsContent').classList.add('active');document.querySelector('.nav-item:nth-child(1)').classList.add('active');loadChats();}
else if(t==='users'){document.getElementById('usersContent').classList.add('active');document.querySelector('.nav-item:nth-child(2)').classList.add('active');s.emit('gu',{n:u});}
else if(t==='posts'){document.getElementById('postsContent').classList.add('active');document.querySelector('.nav-item:nth-child(3)').classList.add('active');document.getElementById('fab').classList.add('show');s.emit('gp');}
else{document.getElementById('settingsContent').classList.add('active');document.querySelector('.nav-item:nth-child(4)').classList.add('active');loadSettings();}
}

function loadSettings(){
var h='<div class="profile-section"><div class="profile-avatar" onclick="document.getElementById(\'avatarInput\').click()">'+(ua?'<img src="'+ua+'">':u[0])+'</div><div class="profile-name">'+u+'</div><div class="profile-bio">'+(userBio||'Нажмите чтобы добавить описание')+'</div></div>';
h+='<div class="settings-group"><div class="setting-item" onclick="editBio()"><span class="setting-label">✏️ Описание</span></div>';
h+='<div class="setting-item" onclick="toggleTheme()"><span class="setting-label">🌓 Тема</span><span class="setting-value">'+(localStorage.getItem('theme')==='light'?'Светлая':'Темная')+'</span></div>';
h+='<div class="setting-item" onclick="showStats()"><span class="setting-label">📊 Статистика</span></div>';
h+='<div class="setting-item" onclick="share()"><span class="setting-label">🔗 Поделиться</span></div>';
h+='<div class="setting-item" onclick="doLogout()"><span class="setting-label" style="color:var(--r)">🚪 Выйти</span></div></div>';
document.getElementById('settingsContent').innerHTML=h;
}

function editBio(){var b=prompt('Описание:',userBio||'');if(b!==null){userBio=b;s.emit('ub',{n:u,b:b});loadSettings();}}
function toggleTheme(){var root=document.documentElement;var dark=root.style.getPropertyValue('--bg').trim()==='#0d0d0d';if(dark){root.style.setProperty('--bg','#f5f5f5');root.style.setProperty('--bg2','#ffffff');root.style.setProperty('--bg3','#e8e8e8');root.style.setProperty('--w','#000000');root.style.setProperty('--b','#ddd');localStorage.setItem('theme','light');}else{root.style.setProperty('--bg','#0d0d0d');root.style.setProperty('--bg2','#1a1a1a');root.style.setProperty('--bg3','#2a2a2a');root.style.setProperty('--w','#ffffff');root.style.setProperty('--b','#3a3a3a');localStorage.setItem('theme','dark');}loadSettings();}
function doLogout(){localStorage.removeItem('shugramm_token');u=null;ua=null;location.reload();}

function openChat(id,nm){ch=id;document.querySelectorAll('.content').forEach(function(c){c.classList.remove('active');});document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='flex';document.getElementById('chatTitle').textContent=nm;document.getElementById('messages').innerHTML='';document.getElementById('typingIndicator').textContent='';s.emit('jc',{ch:id,n:u});}
function closeChat(){document.getElementById('chatWindow').classList.add('hidden');document.getElementById('chatWindow').style.display='none';document.getElementById('chatsContent').classList.add('active');loadChats();}
function sendMsg(){var i=document.getElementById('msgInput');var t=i.value.trim();if(!t)return;s.emit('sm',{n:u,ch:ch,t:'text',c:t});i.value='';s.emit('typing',{ch:ch,n:u,typing:false});}
function handleTyping(){if(typingTimeout)clearTimeout(typingTimeout);s.emit('typing',{ch:ch,n:u,typing:true});typingTimeout=setTimeout(function(){s.emit('typing',{ch:ch,n:u,typing:false})},1500);}
function handleFile(e){var f=e.target.files[0];if(!f)return;var r=new FileReader();r.onload=function(ev){s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f);}
function handleAvatar(e){var f=e.target.files[0];if(!f)return;var r=new FileReader();r.onload=function(ev){ua=ev.target.result;s.emit('ua',{n:u,a:ev.target.result});loadSettings()};r.readAsDataURL(f);}
function handlePost(e){var f=e.target.files[0];if(!f)return;var r=new FileReader();r.onload=function(ev){var c=prompt('Описание:','');s.emit('cp',{n:u,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''})};r.readAsDataURL(f);}
function createPost(){document.getElementById('postInput').click();}

s.on('ch',function(d){document.getElementById('messages').innerHTML='';if(d.ms)d.ms.forEach(function(m){addMsg(m)});var mc=document.getElementById('messages');setTimeout(function(){mc.scrollTop=mc.scrollHeight},100);});
s.on('nm',function(d){if(d.ch===ch){addMsg(d.m);var mc=document.getElementById('messages');var atBottom=mc.scrollHeight-mc.scrollTop-mc.clientHeight<50;if(atBottom)setTimeout(function(){mc.scrollTop=mc.scrollHeight},100);}});

function addMsg(m){
var c=document.getElementById('messages');var im=m.n===u;var d=document.createElement('div');d.className='msg-row '+(im?'mine':'');d.dataset.mid=m.i;
var txt=m.c?m.c.replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';
var ct=m.t==='img'?'<img src="'+m.c+'" onclick="viewMedia(\''+m.c+'\',\'img\')" loading="lazy">':m.t==='vid'?'<video src="'+m.c+'" controls preload="none"></video>':txt;
var av=m.a&&m.a.startsWith('data:')?'<img src="'+m.a+'">':m.n[0];
var actions=im?'<div style="display:flex;gap:4px;margin-top:2px;justify-content:flex-end"><button onclick="editMessage(\''+m.i+'\')" style="background:none;border:none;color:var(--g);font-size:10px;cursor:pointer">✎</button><button onclick="deleteMessage(\''+m.i+'\')" style="background:none;border:none;color:var(--r);font-size:10px;cursor:pointer">✕</button></div>':'';
d.innerHTML='<div class="msg-avatar">'+av+'</div><div style="max-width:75%"><div class="msg-bubble">'+ct+'</div><div class="msg-time">'+m.ts+(m.edited?' ✎':'')+'</div>'+actions+'</div>';
c.appendChild(d);
}

function editMessage(mid){var t=prompt('Редактировать:');if(t&&t.trim()){s.emit('edit_message',{n:u,ch:ch,mid:mid,c:t.trim()});}}
function deleteMessage(mid){if(confirm('Удалить?')){s.emit('delete_message',{n:u,ch:ch,mid:mid});}}

s.on('ul',function(d){
var h='';d.u.forEach(function(u2){var av=u2.a&&u2.a.startsWith('data:')?'<img src="'+u2.a+'">':u2.n[0];h+='<div class="list-item" onclick="startPrivate(\''+u2.n+'\')"><div class="avatar">'+av+'</div><div class="list-info"><div class="list-name">'+u2.n+'</div><div class="list-preview">'+(u2.st==='онлайн'?'🟢 В сети':'⚫ Был недавно')+'</div></div></div>';});
document.getElementById('usersContent').innerHTML=h||'<div class="empty-state"><div class="icon">👤</div><h3>Нет контактов</h3></div>';
});

function startPrivate(t){s.emit('sp2',{n:u,t:t});}
s.on('po',function(d){ch=d.ch;document.querySelectorAll('.content').forEach(function(c){c.classList.remove('active');});document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='flex';document.getElementById('chatTitle').textContent=d.t;document.getElementById('messages').innerHTML='';if(d.ms)d.ms.forEach(function(m){addMsg(m)});var mc=document.getElementById('messages');setTimeout(function(){mc.scrollTop=mc.scrollHeight},100);var chats=JSON.parse(localStorage.getItem('private_chats')||'[]');var found=false;for(var i=0;i<chats.length;i++){if(chats[i].id===d.ch){found=true;break}}if(!found){var av=d.a&&d.a.startsWith('data:')?'<img src="'+d.a+'">':d.t[0];chats.push({id:d.ch,name:d.t,avatar:av,lastMsg:''})}localStorage.setItem('private_chats',JSON.stringify(chats));});

s.on('pl',function(d){var h='';if(d.p.length===0){h='<div class="empty-state"><div class="icon">📸</div><h3>Нет постов</h3></div>';}else{d.p.forEach(function(p){h+=buildPost(p);});}document.getElementById('postsContent').innerHTML=h;});
s.on('np',function(d){var el=document.getElementById('postsContent');if(el.classList.contains('active'))el.insertAdjacentHTML('afterbegin',buildPost(d.p));});
s.on('pu',function(d){var el=document.getElementById(d.p.id);if(el)el.outerHTML=buildPost(d.p);});

function buildPost(p){
var delBtn=(p.author===u)?'<button class="post-action" onclick="deletePost(\''+p.id+'\')" style="margin-left:auto;color:var(--r)">✕</button>':'';
var av=p.avatar&&p.avatar.startsWith('data:')?'<img src="'+p.avatar+'">':p.author[0];
var likeClass=p.likes&&p.likes.includes(u)?'liked':'';
return '<div class="post-card" id="'+p.id+'"><div class="post-header"><div class="post-avatar">'+av+'</div><div><div class="post-user">'+p.author+'</div><div class="post-date">'+p.ts+'</div></div>'+delBtn+'</div>'+(p.media_type==='image'?'<img class="post-media" src="'+p.media_url+'" onclick="viewMedia(\''+p.media_url+'\',\'img\')" loading="lazy">':p.media_type==='video'?'<video class="post-media" src="'+p.media_url+'" controls preload="none"></video>':'')+'<div class="post-actions"><button class="post-action '+likeClass+'" onclick="likePost(\''+p.id+'\')">❤️ '+p.likes_count+'</button><button class="post-action">💬 '+p.comments_count+'</button></div><div class="post-caption"><b>'+p.author+'</b> '+p.caption+'</div><div class="post-comments">'+p.comments.map(function(c){var ca=c.a&&c.a.startsWith('data:')?'<img src="'+c.a+'">':c.n[0];return '<div class="comment-row"><div class="comment-avatar">'+ca+'</div><div class="comment-body"><b>'+c.n+'</b> '+c.c+'</div></div>';}).join('')+'</div><div class="comment-input"><input id="ci_'+p.id+'" placeholder="Комментарий..." onkeypress="if(event.key===\'Enter\')addComment(\''+p.id+'\')"><button onclick="addComment(\''+p.id+'\')">Отправить</button></div></div>';
}

function deletePost(pid){if(confirm('Удалить пост?')){var xhr=new XMLHttpRequest();xhr.open('POST','/delete_post',true);xhr.setRequestHeader('Content-Type','application/json');xhr.send(JSON.stringify({pid:pid}));setTimeout(function(){s.emit('gp')},500);}}
function likePost(pid){s.emit('lp',{pid:pid,n:u});}
function addComment(pid){var i=document.getElementById('ci_'+pid);var t=i.value.trim();if(!t)return;s.emit('cmp',{pid:pid,n:u,c:t});i.value='';}

function searchInChat(){document.getElementById('searchModal').classList.add('show');document.getElementById('searchResults').innerHTML='';document.getElementById('searchChatInput').value='';}
function searchMessages(){clearTimeout(searchTimeout);var q=document.getElementById('searchChatInput').value.trim();if(!q){document.getElementById('searchResults').innerHTML='';return;}searchTimeout=setTimeout(function(){s.emit('search_messages',{ch:ch,q:q});},300);}
function closeSearch(){document.getElementById('searchModal').classList.remove('show');}

function showStats(){var modal=document.getElementById('statsModal');modal.classList.add('show');fetch('/api/stats').then(function(r){return r.json();}).then(function(data){document.getElementById('statsContent').innerHTML='<div class="stats-grid"><div class="stats-item"><div class="value">'+data.total_users+'</div><div class="label">Всего пользователей</div></div><div class="stats-item"><div class="value">'+data.online_users+'</div><div class="label">Сейчас онлайн</div></div><div class="stats-item"><div class="value">'+data.total_posts+'</div><div class="label">Всего постов</div></div><div class="stats-item"><div class="value">'+data.total_chats+'</div><div class="label">Всего чатов</div></div></div>';});}
function closeStats(){document.getElementById('statsModal').classList.remove('show');}

function viewMedia(src,tp){var mv=document.getElementById('mediaViewer');mv.classList.add('show');if(tp==='img'){document.getElementById('mediaImg').src=src;document.getElementById('mediaImg').style.display='block';document.getElementById('mediaVid').style.display='none';}else{document.getElementById('mediaVid').src=src;document.getElementById('mediaVid').style.display='block';document.getElementById('mediaImg').style.display='none';}}
function closeMedia(){document.getElementById('mediaViewer').classList.remove('show');}
function share(){s.emit('sh');}
s.on('sl',function(d){var l='https://'+d.l;if(navigator.clipboard){navigator.clipboard.writeText(l).then(function(){toast('Ссылка скопирована!','success');});}else{prompt('Ссылка:',l);}});

document.addEventListener('keydown',function(e){if(e.ctrlKey&&e.key==='Enter'){sendMsg();e.preventDefault();}if(e.key==='Escape'){closeMedia();closeStats();closeSearch();}});

var savedTheme=localStorage.getItem('theme')||'dark';if(savedTheme==='light'){var root=document.documentElement;root.style.setProperty('--bg','#f5f5f5');root.style.setProperty('--bg2','#ffffff');root.style.setProperty('--bg3','#e8e8e8');root.style.setProperty('--w','#000000');root.style.setProperty('--b','#ddd');}
console.log('⚡ Shugramm готов!');
</script>
</body>
</html>
'''

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("🚀 Запуск Shugramm...")
    print(f"📱 Откройте http://localhost:{Config.PORT}")
    socketio.run(app, host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG, allow_unsafe_werkzeug=True)
