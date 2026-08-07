from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import random, time, os, hashlib, json, re, logging
from functools import wraps
import threading

# ============================================================
#  НАСТРОЙКА
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'directme-secret-key')

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    max_http_buffer_size=500*1024*1024,
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)

# ============================================================
#  КОНСТАНТЫ
# ============================================================
MAX_USERNAME_LENGTH = 20
MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 4
MAX_MESSAGE_LENGTH = 5000
MAX_FILE_SIZE = 500000
MAX_VOICE_SIZE = 200000
MAX_POST_CAPTION = 500
MAX_BIO_LENGTH = 200
MAX_COMMENT_LENGTH = 300
MAX_POSTS_TO_KEEP = 500
MAX_MESSAGES_TO_KEEP = 300

# ============================================================
#  БАЗА ДАННЫХ
# ============================================================
users = {}
posts = {}
stories = {}
private_chats = {}
group_chats = {}
unread = {}
typing_users = {}
pinned_messages = {}
blocked_users = {}
saved_posts = {}
reposts = {}
user_status_history = {}
login_attempts = {}

# ============================================================
#  ФУНКЦИИ БАЗЫ ДАННЫХ
# ============================================================
def hash_password(password):
    salt = os.urandom(32).hex()
    return f"{salt}:{hashlib.sha256((salt + password).encode()).hexdigest()}"

def verify_password(password, hashed):
    try:
        salt, hash_value = hashed.split(':')
        return hash_value == hashlib.sha256((salt + password).encode()).hexdigest()
    except:
        return False

def generate_token():
    return hashlib.sha256(f"{random.random()}{time.time()}".encode()).hexdigest()[:32]

def is_blocked(user1, user2):
    if not user1 or not user2:
        return False
    return user2 in blocked_users.get(user1, []) or user1 in blocked_users.get(user2, [])

def save_data():
    try:
        data = {
            'users': users,
            'posts': posts,
            'stories': stories,
            'private_chats': private_chats,
            'group_chats': group_chats,
            'unread': unread,
            'blocked_users': blocked_users,
            'saved_posts': saved_posts,
            'reposts': reposts,
            'pinned_messages': pinned_messages,
            'user_status_history': user_status_history
        }
        with open('directme_data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Данные сохранены")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def load_data():
    global users, posts, stories, private_chats, group_chats, unread, blocked_users, saved_posts, reposts, pinned_messages, user_status_history
    try:
        with open('directme_data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            users = data.get('users', {})
            posts = data.get('posts', {})
            stories = data.get('stories', {})
            private_chats = data.get('private_chats', {})
            group_chats = data.get('group_chats', {})
            unread = data.get('unread', {})
            blocked_users = data.get('blocked_users', {})
            saved_posts = data.get('saved_posts', {})
            reposts = data.get('reposts', {})
            pinned_messages = data.get('pinned_messages', {})
            user_status_history = data.get('user_status_history', {})
        logger.info("Данные загружены")
    except FileNotFoundError:
        logger.info("Новый файл данных")
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

def validate_username(username):
    if not username:
        return False, "Имя обязательно"
    if len(username) < MIN_USERNAME_LENGTH or len(username) > MAX_USERNAME_LENGTH:
        return False, f"От {MIN_USERNAME_LENGTH} до {MAX_USERNAME_LENGTH} символов"
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Только латиница, цифры, _"
    return True, ""

# ============================================================
#  ДЕКОРАТОРЫ
# ============================================================
def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Auth-Token') or request.args.get('token')
        if not token:
            return jsonify({'error': 'Требуется авторизация'}), 401
        for name, user in users.items():
            if user.get('token') == token:
                if user.get('is_banned', False):
                    return jsonify({'error': 'Аккаунт заблокирован'}), 403
                return f(user=user, name=name, *args, **kwargs)
        return jsonify({'error': 'Недействительный токен'}), 401
    return decorated

# ============================================================
#  HTTP МАРШРУТЫ
# ============================================================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/posts')
@auth_required
def get_posts_api(user, name):
    limit = request.args.get('limit', 30, type=int)
    posts_list = list(posts.values())
    posts_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
    return jsonify({'posts': posts_list[:limit]})

@app.route('/api/users')
@auth_required
def get_users_api(user, name):
    user_list = []
    for n, u in users.items():
        if n != name and n not in blocked_users.get(name, []):
            user_list.append({
                'name': n,
                'username': u.get('username', n),
                'avatar': u.get('avatar'),
                'status': u.get('status', 'offline'),
                'bio': u.get('bio', ''),
                'last_seen': u.get('last_seen', 0)
            })
    return jsonify({'users': user_list})

@app.route('/api/delete_post', methods=['POST'])
@auth_required
def delete_post_api(user, name):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    pid = data.get('pid', '')
    if not pid or pid not in posts:
        return jsonify({'error': 'Пост не найден'}), 404
    if posts[pid]['author'] != name:
        return jsonify({'error': 'Нет прав'}), 403
    del posts[pid]
    save_data()
    return jsonify({'ok': True})

# ============================================================
#  WEBSOCKET: АУТЕНТИФИКАЦИЯ
# ============================================================
def get_user_by_sid(sid):
    for name, user in users.items():
        if user.get('sid') == sid:
            return name, user
    return None, None

def safe_emit(event, data, room=None, broadcast=False, include_self=True):
    try:
        if broadcast:
            emit(event, data, broadcast=True)
        elif room:
            emit(event, data, room=room, include_self=include_self)
        else:
            emit(event, data)
    except Exception as e:
        logger.error(f"Ошибка отправки {event}: {e}")

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    try:
        name, user = get_user_by_sid(request.sid)
        if name and user:
            user['status'] = 'offline'
            user['sid'] = ''
            user['last_seen'] = time.time()
            safe_emit('user_status', {
                'name': name,
                'status': 'offline',
                'last_seen': time.time()
            }, broadcast=True)
            save_data()
    except Exception as e:
        logger.error(f"Disconnect error: {e}")

@socketio.on('register')
def register(data):
    try:
        username = data.get('username', '').strip().lower()
        password = data.get('password', '')
        
        valid, msg = validate_username(username)
        if not valid:
            emit('error', {'message': msg})
            return
        if len(password) < MIN_PASSWORD_LENGTH:
            emit('error', {'message': f'Пароль минимум {MIN_PASSWORD_LENGTH} символов'})
            return
        if username in users:
            emit('error', {'message': 'Пользователь уже существует'})
            return
        
        token = generate_token()
        users[username] = {
            'sid': request.sid,
            'username': username,
            'password': hash_password(password),
            'avatar': None,
            'status': 'online',
            'bio': '',
            'token': token,
            'last_seen': time.time(),
            'created_at': time.time(),
            'is_banned': False
        }
        unread[username] = {}
        save_data()
        
        emit('login_success', {
            'name': username,
            'username': username,
            'token': token,
            'avatar': None,
            'bio': ''
        })
        safe_emit('user_joined', {
            'name': username,
            'username': username,
            'avatar': None,
            'status': 'online'
        }, broadcast=True)
        logger.info(f"User registered: {username}")
    except Exception as e:
        logger.error(f"Registration error: {e}")
        emit('error', {'message': 'Ошибка регистрации'})

@socketio.on('login')
def login(data):
    try:
        username = data.get('username', '').strip().lower()
        password = data.get('password', '')
        
        if username not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        
        user = users[username]
        if user.get('is_banned', False):
            emit('error', {'message': 'Аккаунт заблокирован'})
            return
        if not verify_password(password, user['password']):
            emit('error', {'message': 'Неверный пароль'})
            return
        
        token = generate_token()
        user['sid'] = request.sid
        user['status'] = 'online'
        user['token'] = token
        user['last_seen'] = time.time()
        save_data()
        
        emit('login_success', {
            'name': username,
            'username': username,
            'token': token,
            'avatar': user.get('avatar'),
            'bio': user.get('bio', '')
        })
        safe_emit('user_joined', {
            'name': username,
            'username': username,
            'avatar': user.get('avatar'),
            'status': 'online'
        }, broadcast=True)
        logger.info(f"User logged in: {username}")
    except Exception as e:
        logger.error(f"Login error: {e}")
        emit('error', {'message': 'Ошибка входа'})

@socketio.on('auto_login')
def auto_login(data):
    try:
        token = data.get('token', '')
        if not token:
            emit('error', {'message': 'Токен не предоставлен'})
            return
        for username, user in users.items():
            if user.get('token') == token:
                if user.get('is_banned', False):
                    emit('error', {'message': 'Аккаунт заблокирован'})
                    return
                user['sid'] = request.sid
                user['status'] = 'online'
                user['last_seen'] = time.time()
                save_data()
                emit('login_success', {
                    'name': username,
                    'username': username,
                    'token': token,
                    'avatar': user.get('avatar'),
                    'bio': user.get('bio', '')
                })
                safe_emit('user_joined', {
                    'name': username,
                    'username': username,
                    'avatar': user.get('avatar'),
                    'status': 'online'
                }, broadcast=True)
                logger.info(f"Auto-login: {username}")
                return
        emit('error', {'message': 'Недействительный токен'})
    except Exception as e:
        logger.error(f"Auto-login error: {e}")
        emit('error', {'message': 'Ошибка автовхода'})

# ============================================================
#  WEBSOCKET: СООБЩЕНИЯ
# ============================================================
def create_message_id():
    return f"m{int(time.time()*1000)}_{random.randint(1000, 9999)}"

def add_message_to_chat(chat_id, message):
    if chat_id in private_chats:
        private_chats[chat_id]['messages'].append(message)
        if len(private_chats[chat_id]['messages']) > MAX_MESSAGES_TO_KEEP:
            private_chats[chat_id]['messages'] = private_chats[chat_id]['messages'][-MAX_MESSAGES_TO_KEEP:]
        return True
    elif chat_id in group_chats:
        group_chats[chat_id]['messages'].append(message)
        if len(group_chats[chat_id]['messages']) > MAX_MESSAGES_TO_KEEP:
            group_chats[chat_id]['messages'] = group_chats[chat_id]['messages'][-MAX_MESSAGES_TO_KEEP:]
        return True
    return False

def get_chat_messages(chat_id, limit=200):
    if chat_id in private_chats:
        return private_chats[chat_id]['messages'][-limit:]
    elif chat_id in group_chats:
        return group_chats[chat_id]['messages'][-limit:]
    return []

def send_push_notification(to_user, from_name, content, chat_id, msg_id):
    try:
        if to_user in users and users[to_user].get('sid'):
            safe_emit('push_notification', {
                'from': from_name,
                'content': content[:100] + ('...' if len(content) > 100 else ''),
                'chat_id': chat_id,
                'msg_id': msg_id
            }, room=users[to_user]['sid'])
    except Exception as e:
        logger.error(f"Push error: {e}")

@socketio.on('send_message')
def send_message(data):
    try:
        name = data.get('name', '')
        chat = data.get('chat', '')
        msg_type = data.get('type', 'text')
        content = data.get('content', '')
        reply_to = data.get('reply_to', None)
        forwarded_from = data.get('forwarded_from', None)
        
        if name not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        
        if chat in private_chats:
            for member in private_chats[chat]['users']:
                if member != name and is_blocked(name, member):
                    emit('error', {'message': 'Вы заблокированы'})
                    return
        
        if msg_type == 'text':
            content = content[:MAX_MESSAGE_LENGTH]
        elif msg_type in ['image', 'video']:
            content = content[:MAX_FILE_SIZE]
        elif msg_type == 'voice':
            content = content[:MAX_VOICE_SIZE]
        
        msg = {
            'id': create_message_id(),
            'name': name,
            'type': msg_type,
            'content': content,
            'time': datetime.now().strftime("%H:%M"),
            'timestamp': time.time(),
            'avatar': users[name].get('avatar'),
            'edited': False,
            'reactions': {},
            'reply_to': reply_to,
            'forwarded_from': forwarded_from,
            'read_by': [name],
            'is_pinned': False
        }
        
        if add_message_to_chat(chat, msg):
            save_data()
            safe_emit('new_message', {'chat': chat, 'message': msg}, room=chat)
            
            if chat in private_chats:
                for member in private_chats[chat]['users']:
                    if member != name:
                        unread.setdefault(member, {})
                        unread[member][chat] = unread[member].get(chat, 0) + 1
                        send_push_notification(member, name, content, chat, msg['id'])
            elif chat in group_chats:
                for member in group_chats[chat]['members']:
                    if member != name:
                        unread.setdefault(member, {})
                        unread[member][chat] = unread[member].get(chat, 0) + 1
                        send_push_notification(member, name, content, chat, msg['id'])
        else:
            emit('error', {'message': 'Чат не найден'})
    except Exception as e:
        logger.error(f"Send message error: {e}")
        emit('error', {'message': 'Ошибка отправки'})

@socketio.on('join_chat')
def join_chat(data):
    try:
        chat = data.get('chat', '')
        name = data.get('name', '')
        if name not in users:
            return
        if chat in private_chats:
            if name not in private_chats[chat]['users']:
                return
        elif chat in group_chats:
            if name not in group_chats[chat]['members']:
                return
        else:
            return
        
        join_room(chat)
        if name in unread and chat in unread[name]:
            unread[name][chat] = 0
        
        msgs = get_chat_messages(chat)
        for msg in msgs:
            if msg['name'] != name and name not in msg.get('read_by', []):
                msg['read_by'] = msg.get('read_by', []) + [name]
        save_data()
        emit('chat_history', {'messages': msgs, 'chat': chat})
    except Exception as e:
        logger.error(f"Join chat error: {e}")

@socketio.on('typing')
def typing(data):
    try:
        chat = data.get('chat', '')
        name = data.get('name', '')
        is_typing = data.get('typing', False)
        if name not in users:
            return
        if chat in private_chats:
            other = [u for u in private_chats[chat]['users'] if u != name]
            if other and is_blocked(name, other[0]):
                return
        typing_users.setdefault(chat, {})
        if is_typing:
            typing_users[chat][name] = time.time()
        else:
            typing_users[chat].pop(name, None)
        safe_emit('typing_status', {'name': name, 'typing': is_typing}, room=chat, include_self=False)
    except Exception as e:
        logger.error(f"Typing error: {e}")

@socketio.on('message_reaction')
def message_reaction(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        reaction = data.get('reaction', '')
        valid_reactions = ['❤️', '🔥', '👍', '👎', '😂', '😮', '😡', '🥰', '😱', '💯', '👏', '🙌', '🎉']
        if name not in users or reaction not in valid_reactions:
            return
        
        msgs = get_chat_messages(chat, limit=1000)
        for msg in msgs:
            if msg['id'] == msg_id:
                if name in msg['reactions'] and msg['reactions'][name] == reaction:
                    del msg['reactions'][name]
                else:
                    msg['reactions'][name] = reaction
                save_data()
                safe_emit('reaction_updated', {
                    'chat': chat,
                    'msg_id': msg_id,
                    'reactions': msg['reactions']
                }, room=chat)
                break
    except Exception as e:
        logger.error(f"Reaction error: {e}")

@socketio.on('reply_message')
def reply_message(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        reply_text = data.get('reply', '')[:MAX_MESSAGE_LENGTH]
        if name not in users:
            return
        
        msgs = get_chat_messages(chat, limit=1000)
        original = None
        for msg in msgs:
            if msg['id'] == msg_id:
                original = msg
                break
        if not original:
            emit('error', {'message': 'Сообщение не найдено'})
            return
        
        reply_msg = {
            'id': create_message_id(),
            'name': name,
            'type': 'text',
            'content': reply_text,
            'reply_to': {
                'id': original['id'],
                'name': original['name'],
                'content': original['content'][:100] + ('...' if len(original['content']) > 100 else '')
            },
            'time': datetime.now().strftime("%H:%M"),
            'timestamp': time.time(),
            'avatar': users[name].get('avatar'),
            'edited': False,
            'reactions': {},
            'read_by': [name]
        }
        
        if add_message_to_chat(chat, reply_msg):
            save_data()
            safe_emit('new_message', {'chat': chat, 'message': reply_msg}, room=chat)
            if chat in private_chats:
                for member in private_chats[chat]['users']:
                    if member != name:
                        unread.setdefault(member, {})
                        unread[member][chat] = unread[member].get(chat, 0) + 1
                        send_push_notification(member, name, reply_text, chat, reply_msg['id'])
    except Exception as e:
        logger.error(f"Reply error: {e}")

@socketio.on('delete_message')
def delete_message(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        delete_for_all = data.get('delete_for_all', False)
        
        msgs = get_chat_messages(chat, limit=1000)
        for i, msg in enumerate(msgs):
            if msg['id'] == msg_id:
                if msg['name'] == name or (chat in group_chats and group_chats[chat]['admin'] == name):
                    if delete_for_all:
                        del msgs[i]
                    else:
                        msg['content'] = 'Сообщение удалено'
                        msg['deleted'] = True
                    save_data()
                    safe_emit('message_deleted', {
                        'chat': chat,
                        'msg_id': msg_id,
                        'delete_for_all': delete_for_all
                    }, room=chat)
                else:
                    emit('error', {'message': 'Нет прав'})
                break
    except Exception as e:
        logger.error(f"Delete error: {e}")

@socketio.on('edit_message')
def edit_message(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        new_content = data.get('content', '')[:MAX_MESSAGE_LENGTH]
        
        msgs = get_chat_messages(chat, limit=1000)
        for msg in msgs:
            if msg['id'] == msg_id and msg['name'] == name:
                msg['content'] = new_content
                msg['edited'] = True
                save_data()
                safe_emit('message_edited', {'chat': chat, 'message': msg}, room=chat)
                break
    except Exception as e:
        logger.error(f"Edit error: {e}")

@socketio.on('pin_message')
def pin_message(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        
        if chat in group_chats and group_chats[chat]['admin'] != name:
            emit('error', {'message': 'Только админ'})
            return
        
        msgs = get_chat_messages(chat, limit=1000)
        for msg in msgs:
            if msg['id'] == msg_id:
                pinned_messages.setdefault(chat, [])
                if msg_id in pinned_messages[chat]:
                    pinned_messages[chat].remove(msg_id)
                    msg['is_pinned'] = False
                else:
                    pinned_messages[chat].append(msg_id)
                    msg['is_pinned'] = True
                save_data()
                safe_emit('message_pinned', {
                    'chat': chat,
                    'msg_id': msg_id,
                    'pinned': msg['is_pinned']
                }, room=chat)
                break
    except Exception as e:
        logger.error(f"Pin error: {e}")

@socketio.on('forward_message')
def forward_message(data):
    try:
        chat = data.get('chat', '')
        msg_id = data.get('msg_id', '')
        name = data.get('name', '')
        target_user = data.get('to', '')
        
        if name not in users or target_user not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        if is_blocked(name, target_user):
            emit('error', {'message': 'Заблокирован'})
            return
        
        msgs = get_chat_messages(chat, limit=1000)
        original = None
        for msg in msgs:
            if msg['id'] == msg_id:
                original = msg
                break
        if not original:
            emit('error', {'message': 'Сообщение не найдено'})
            return
        
        chat_id = f"p_{min(name, target_user)}_{max(name, target_user)}"
        if chat_id not in private_chats:
            private_chats[chat_id] = {'users': [name, target_user], 'messages': []}
            save_data()
        
        forward_msg = {
            'id': create_message_id(),
            'name': name,
            'type': original['type'],
            'content': original['content'],
            'time': datetime.now().strftime("%H:%M"),
            'timestamp': time.time(),
            'avatar': users[name].get('avatar'),
            'edited': False,
            'reactions': {},
            'forwarded_from': original['name'],
            'read_by': [name]
        }
        
        if add_message_to_chat(chat_id, forward_msg):
            save_data()
            join_room(chat_id)
            safe_emit('new_message', {'chat': chat_id, 'message': forward_msg}, room=chat_id)
            if target_user != name:
                unread.setdefault(target_user, {})
                unread[target_user][chat_id] = unread[target_user].get(chat_id, 0) + 1
                send_push_notification(target_user, name, 'Пересланное сообщение', chat_id, forward_msg['id'])
    except Exception as e:
        logger.error(f"Forward error: {e}")

# ============================================================
#  WEBSOCKET: ГРУППЫ
# ============================================================
@socketio.on('create_group')
def create_group(data):
    try:
        name = data.get('name', '')
        group_name = data.get('group_name', 'Новая группа')
        members = data.get('members', [])
        
        if name not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        if len(members) < 2:
            emit('error', {'message': 'Нужно минимум 2 участника'})
            return
        for member in members:
            if member not in users:
                emit('error', {'message': f'Участник {member} не найден'})
                return
        
        chat_id = f"g_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        group_chats[chat_id] = {
            'name': group_name,
            'admin': name,
            'members': [name] + members,
            'messages': [],
            'created_at': time.time(),
            'avatar': None
        }
        save_data()
        join_room(chat_id)
        
        for member in [name] + members:
            if users.get(member, {}).get('sid'):
                safe_emit('group_created', {
                    'chat_id': chat_id,
                    'name': group_name,
                    'members': [name] + members
                }, room=users[member]['sid'])
        
        logger.info(f"Group created: {chat_id}")
    except Exception as e:
        logger.error(f"Create group error: {e}")

# ============================================================
#  WEBSOCKET: СТОРИС
# ============================================================
@socketio.on('create_story')
def create_story(data):
    try:
        name = data.get('name', '')
        content = data.get('content', '')
        media_type = data.get('type', 'image')
        
        if name not in users:
            return
        
        story = {
            'id': f"s{int(time.time()*1000)}_{random.randint(1000, 9999)}",
            'name': name,
            'content': content,
            'type': media_type,
            'timestamp': time.time(),
            'views': []
        }
        
        stories.setdefault(name, []).append(story)
        if len(stories[name]) > 10:
            stories[name] = stories[name][-10:]
        
        save_data()
        safe_emit('new_story', {'name': name, 'story': story}, broadcast=True)
        threading.Timer(86400, lambda: delete_story_after_time(name, story['id'])).start()
        logger.info(f"Story created by {name}")
    except Exception as e:
        logger.error(f"Create story error: {e}")

def delete_story_after_time(name, story_id):
    try:
        if name in stories:
            stories[name] = [s for s in stories[name] if s['id'] != story_id]
            if not stories[name]:
                del stories[name]
            save_data()
            safe_emit('story_deleted', {'name': name, 'story_id': story_id}, broadcast=True)
    except Exception as e:
        logger.error(f"Delete story error: {e}")

@socketio.on('view_story')
def view_story(data):
    try:
        name = data.get('name', '')
        story_id = data.get('story_id', '')
        viewer = data.get('viewer', '')
        
        if name not in stories:
            return
        for story in stories[name]:
            if story['id'] == story_id and viewer not in story['views']:
                story['views'].append(viewer)
                save_data()
                safe_emit('story_viewed', {
                    'name': name,
                    'story_id': story_id,
                    'views': story['views']
                }, broadcast=True)
                break
    except Exception as e:
        logger.error(f"View story error: {e}")

# ============================================================
#  WEBSOCKET: ПОСТЫ
# ============================================================
@socketio.on('create_post')
def create_post(data):
    try:
        name = data.get('name', '')
        content = data.get('content', '')
        media_type = data.get('media_type', 'image')
        caption = data.get('caption', '')[:MAX_POST_CAPTION]
        
        if name not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        if len(content) > MAX_FILE_SIZE:
            emit('error', {'message': 'Файл слишком большой'})
            return
        
        hashtags = re.findall(r'#(\w+)', caption)
        post_id = f"p{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        
        posts[post_id] = {
            'id': post_id,
            'author': name,
            'avatar': users[name].get('avatar'),
            'content': content,
            'media_type': media_type,
            'caption': caption,
            'hashtags': hashtags,
            'likes': [],
            'comments': [],
            'saved_by': [],
            'reposts': [],
            'time': datetime.now().strftime("%d.%m.%Y %H:%M"),
            'timestamp': time.time()
        }
        
        if len(posts) > MAX_POSTS_TO_KEEP:
            oldest_posts = sorted(posts.keys(), key=lambda x: posts[x]['timestamp'])[:len(posts) - MAX_POSTS_TO_KEEP]
            for pid in oldest_posts:
                del posts[pid]
        
        save_data()
        safe_emit('new_post', {'post': posts[post_id]}, broadcast=True)
        logger.info(f"Post created by {name}")
    except Exception as e:
        logger.error(f"Create post error: {e}")

@socketio.on('get_posts')
def get_posts():
    try:
        posts_list = list(posts.values())
        posts_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        safe_emit('posts_list', {'posts': posts_list[:50]})
    except Exception as e:
        logger.error(f"Get posts error: {e}")

@socketio.on('like_post')
def like_post(data):
    try:
        post_id = data.get('post_id', '')
        name = data.get('name', '')
        if post_id not in posts:
            return
        if name in posts[post_id]['likes']:
            posts[post_id]['likes'].remove(name)
        else:
            posts[post_id]['likes'].append(name)
        save_data()
        safe_emit('post_updated', {'post': posts[post_id]}, broadcast=True)
    except Exception as e:
        logger.error(f"Like error: {e}")

@socketio.on('comment_post')
def comment_post(data):
    try:
        post_id = data.get('post_id', '')
        name = data.get('name', '')
        comment = data.get('comment', '')[:MAX_COMMENT_LENGTH]
        
        if post_id not in posts or name not in users or not comment.strip():
            return
        
        posts[post_id]['comments'].append({
            'id': f"c{int(time.time()*1000)}_{random.randint(1000, 9999)}",
            'name': name,
            'avatar': users.get(name, {}).get('avatar'),
            'comment': comment,
            'time': datetime.now().strftime("%H:%M"),
            'timestamp': time.time(),
            'likes': []
        })
        save_data()
        safe_emit('post_updated', {'post': posts[post_id]}, broadcast=True)
    except Exception as e:
        logger.error(f"Comment error: {e}")

@socketio.on('save_post')
def save_post(data):
    try:
        post_id = data.get('post_id', '')
        name = data.get('name', '')
        if post_id not in posts:
            return
        if name in posts[post_id]['saved_by']:
            posts[post_id]['saved_by'].remove(name)
        else:
            posts[post_id]['saved_by'].append(name)
        save_data()
        safe_emit('post_updated', {'post': posts[post_id]}, broadcast=True)
    except Exception as e:
        logger.error(f"Save error: {e}")

@socketio.on('repost_post')
def repost_post(data):
    try:
        post_id = data.get('post_id', '')
        name = data.get('name', '')
        if post_id not in posts:
            return
        if name not in posts[post_id]['reposts']:
            posts[post_id]['reposts'].append(name)
            save_data()
            safe_emit('post_updated', {'post': posts[post_id]}, broadcast=True)
    except Exception as e:
        logger.error(f"Repost error: {e}")

# ============================================================
#  WEBSOCKET: ПРОФИЛЬ
# ============================================================
@socketio.on('update_avatar')
def update_avatar(data):
    try:
        name = data.get('name', '')
        avatar = data.get('avatar', '')
        if name in users:
            users[name]['avatar'] = avatar
            save_data()
            safe_emit('avatar_updated', {'name': name, 'avatar': avatar}, broadcast=True)
    except Exception as e:
        logger.error(f"Avatar error: {e}")

@socketio.on('update_bio')
def update_bio(data):
    try:
        name = data.get('name', '')
        bio = data.get('bio', '')[:MAX_BIO_LENGTH]
        if name in users:
            users[name]['bio'] = bio
            save_data()
            safe_emit('bio_updated', {'name': name, 'bio': bio})
    except Exception as e:
        logger.error(f"Bio error: {e}")

@socketio.on('update_profile')
def update_profile(data):
    try:
        name = data.get('name', '')
        new_name = data.get('new_name', '').strip()
        new_username = data.get('new_username', '').strip().lower()
        
        if name not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        
        if new_name and new_name != name:
            valid, msg = validate_username(new_name)
            if not valid:
                emit('error', {'message': msg})
                return
            if new_name in users:
                emit('error', {'message': 'Имя занято'})
                return
            
            user_data = users.pop(name)
            users[new_name] = user_data
            for chat_id, chat in private_chats.items():
                if name in chat['users']:
                    chat['users'] = [new_name if u == name else u for u in chat['users']]
            for chat_id, chat in group_chats.items():
                if name in chat['members']:
                    chat['members'] = [new_name if u == name else u for u in chat['members']]
                if chat['admin'] == name:
                    chat['admin'] = new_name
            save_data()
            safe_emit('profile_updated', {'old_name': name, 'new_name': new_name}, broadcast=True)
            return
        
        if new_username and new_username != users[name].get('username'):
            valid, msg = validate_username(new_username)
            if not valid:
                emit('error', {'message': msg})
                return
            for n, u in users.items():
                if u.get('username') == new_username and n != name:
                    emit('error', {'message': 'Юзернейм занят'})
                    return
            users[name]['username'] = new_username
            save_data()
            safe_emit('username_updated', {'name': name, 'username': new_username}, broadcast=True)
    except Exception as e:
        logger.error(f"Profile update error: {e}")

@socketio.on('block_user')
def block_user(data):
    try:
        name = data.get('name', '')
        block_name = data.get('block_name', '')
        if name not in users or block_name not in users or name == block_name:
            return
        if block_name not in blocked_users.get(name, []):
            blocked_users.setdefault(name, []).append(block_name)
            save_data()
    except Exception as e:
        logger.error(f"Block error: {e}")

@socketio.on('unblock_user')
def unblock_user(data):
    try:
        name = data.get('name', '')
        unblock_name = data.get('unblock_name', '')
        if name in blocked_users and unblock_name in blocked_users[name]:
            blocked_users[name].remove(unblock_name)
            save_data()
    except Exception as e:
        logger.error(f"Unblock error: {e}")

@socketio.on('start_private_chat')
def start_private_chat(data):
    try:
        user1 = data.get('user1', '')
        user2 = data.get('user2', '')
        if user1 not in users or user2 not in users:
            emit('error', {'message': 'Пользователь не найден'})
            return
        if is_blocked(user1, user2):
            emit('error', {'message': 'Заблокированы'})
            return
        
        chat_id = f"p_{min(user1, user2)}_{max(user1, user2)}"
        if chat_id not in private_chats:
            private_chats[chat_id] = {'users': [user1, user2], 'messages': []}
            save_data()
        
        join_room(chat_id)
        msgs = get_chat_messages(chat_id)
        safe_emit('private_chat', {
            'chat_id': chat_id,
            'user': user2,
            'avatar': users[user2].get('avatar'),
            'messages': msgs,
            'is_group': False
        })
    except Exception as e:
        logger.error(f"Start chat error: {e}")

@socketio.on('logout')
def logout(data):
    try:
        token = data.get('token', '')
        for name, user in users.items():
            if user.get('token') == token:
                user['token'] = ''
                user['status'] = 'offline'
                user['sid'] = ''
                user['last_seen'] = time.time()
                save_data()
                safe_emit('user_status', {
                    'name': name,
                    'status': 'offline',
                    'last_seen': time.time()
                }, broadcast=True)
                break
    except Exception as e:
        logger.error(f"Logout error: {e}")

# ============================================================
#  ЗАПУСК
# ============================================================
load_data()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
