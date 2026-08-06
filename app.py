from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import random, time, os, hashlib, json, re
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'shugramm-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=100*1024*1024)

# ========== ДАННЫЕ ==========
users = {}
posts = []
groups = {'general': {'id': 'general', 'name': 'Общий чат', 'members': set(), 'messages': []}}
private_chats = {}
pending = {}
unread = {}
typing_users = {}

def hash_pass(password):
    salt = os.urandom(32).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_pass(password, hashed):
    salt, hash_value = hashed.split(':')
    return hash_value == hashlib.sha256((salt + password).encode()).hexdigest()

def generate_token():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]

def save_data():
    data = {
        'users': users,
        'posts': posts,
        'groups': {k: {**v, 'members': list(v['members'])} for k, v in groups.items()},
        'private_chats': private_chats,
        'unread': unread
    }
    with open('shugramm_data.json', 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_data():
    global users, posts, groups, private_chats, unread
    try:
        with open('shugramm_data.json', 'r') as f:
            data = json.load(f)
            users = data.get('users', {})
            posts = data.get('posts', [])
            groups = {k: {**v, 'members': set(v.get('members', []))} for k, v in data.get('groups', {}).items()}
            private_chats = data.get('private_chats', {})
            unread = data.get('unread', {})
    except:
        pass

load_data()

# ========== ДЕКОРАТОРЫ ==========
def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Auth-Token') or request.args.get('token')
        if not token:
            return jsonify({'error': 'Требуется авторизация'}), 401
        for name, user in users.items():
            if user.get('token') == token:
                return f(user=user, name=name, *args, **kwargs)
        return jsonify({'error': 'Недействительный токен'}), 401
    return decorated

# ========== HTTP РОУТЫ ==========
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/posts')
@auth_required
def get_posts_api(user, name):
    return jsonify({'posts': posts[:30]})

@app.route('/api/users')
@auth_required
def get_users_api(user, name):
    user_list = []
    for n, u in users.items():
        if n != name:
            user_list.append({
                'name': n,
                'avatar': u.get('avatar'),
                'status': u.get('status', 'offline'),
                'bio': u.get('bio', '')
            })
    return jsonify({'users': user_list})

@app.route('/delete_post', methods=['POST'])
def delete_post():
    data = request.get_json()
    pid = data.get('pid', '')
    name = data.get('n', '')
    global posts
    for i, p in enumerate(posts):
        if p['id'] == pid and p['author'] == name:
            posts.pop(i)
            save_data()
            break
    return {'ok': True}

# ========== SOCKET.IO СОБЫТИЯ ==========
@socketio.on('connect')
def handle_connect():
    print(f"✅ Клиент подключен: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    for name, user in users.items():
        if user.get('sid') == request.sid:
            user['status'] = 'offline'
            user['sid'] = ''
            emit('user_status', {'name': name, 'status': 'offline'}, broadcast=True)
            save_data()
            break
    print(f"❌ Клиент отключен: {request.sid}")

@socketio.on('register')
def register(data):
    phone = ''.join(filter(str.isdigit, data.get('phone', '')))
    if len(phone) < 10:
        emit('error', {'message': 'Введите корректный номер телефона'})
        return
    code = str(random.randint(100000, 999999))
    pending[phone] = {'code': code, 'time': time.time()}
    print(f"📱 Код для {phone}: {code}")
    emit('code_sent', {'phone': phone, 'code': code})

@socketio.on('verify_code')
def verify_code(data):
    phone = data.get('phone', '')
    code = data.get('code', '')
    if phone not in pending:
        emit('error', {'message': 'Сессия истекла'})
        return
    if time.time() - pending[phone]['time'] > 300:
        del pending[phone]
        emit('error', {'message': 'Код истек'})
        return
    if code != pending[phone]['code']:
        emit('error', {'message': 'Неверный код'})
        return
    del pending[phone]
    for name, user in users.items():
        if user.get('phone') == phone:
            emit('user_exists', {'name': name})
            return
    emit('new_user', {'phone': phone})

@socketio.on('create_user')
def create_user(data):
    phone = data.get('phone', '')
    name = data.get('name', '').strip()
    password = data.get('password', '')
    
    if not name or len(name) < 2 or len(name) > 20:
        emit('error', {'message': 'Имя должно быть от 2 до 20 символов'})
        return
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_]+$', name):
        emit('error', {'message': 'Имя содержит недопустимые символы'})
        return
    if name in users:
        emit('error', {'message': 'Пользователь уже существует'})
        return
    if len(password) < 4:
        emit('error', {'message': 'Пароль минимум 4 символа'})
        return
    
    token = generate_token()
    users[name] = {
        'sid': request.sid,
        'phone': phone,
        'password': hash_pass(password),
        'avatar': None,
        'status': 'online',
        'bio': '',
        'token': token,
        'last_seen': time.time()
    }
    groups['general']['members'].add(name)
    unread[name] = {}
    save_data()
    join_room('general')
    emit('login_success', {'name': name, 'token': token, 'avatar': None})
    emit('user_joined', {'name': name, 'avatar': None, 'status': 'online'}, broadcast=True)

@socketio.on('login')
def login(data):
    name = data.get('name', '').strip()
    password = data.get('password', '')
    if name not in users:
        emit('error', {'message': 'Пользователь не найден'})
        return
    if not verify_pass(password, users[name]['password']):
        emit('error', {'message': 'Неверный пароль'})
        return
    token = generate_token()
    users[name]['sid'] = request.sid
    users[name]['status'] = 'online'
    users[name]['token'] = token
    users[name]['last_seen'] = time.time()
    save_data()
    join_room('general')
    emit('login_success', {'name': name, 'token': token, 'avatar': users[name].get('avatar')})
    emit('user_joined', {'name': name, 'avatar': users[name].get('avatar'), 'status': 'online'}, broadcast=True)

@socketio.on('auto_login')
def auto_login(data):
    token = data.get('token', '')
    for name, user in users.items():
        if user.get('token') == token:
            user['sid'] = request.sid
            user['status'] = 'online'
            user['last_seen'] = time.time()
            save_data()
            join_room('general')
            emit('login_success', {'name': name, 'token': token, 'avatar': user.get('avatar')})
            emit('user_joined', {'name': name, 'avatar': user.get('avatar'), 'status': 'online'}, broadcast=True)
            return

@socketio.on('send_message')
def send_message(data):
    name = data.get('name', '')
    chat = data.get('chat', 'general')
    msg_type = data.get('type', 'text')
    content = data.get('content', '')
    
    if name not in users:
        return
    
    if msg_type == 'text':
        content = content[:2000]
    elif msg_type in ['image', 'video']:
        content = content[:150000]
    
    msg = {
        'id': f"m{int(time.time()*1000)}",
        'name': name,
        'type': msg_type,
        'content': content,
        'time': datetime.now().strftime("%H:%M"),
        'avatar': users[name].get('avatar'),
        'timestamp': time.time()
    }
    
    if chat in groups:
        groups[chat]['messages'].append(msg)
        if len(groups[chat]['messages']) > 200:
            groups[chat]['messages'] = groups[chat]['messages'][-100:]
    elif chat in private_chats:
        private_chats[chat]['messages'].append(msg)
        if len(private_chats[chat]['messages']) > 200:
            private_chats[chat]['messages'] = private_chats[chat]['messages'][-100:]
    
    save_data()
    emit('new_message', {'chat': chat, 'message': msg}, room=chat)
    
    # Уведомления о непрочитанных
    if chat in private_chats:
        for member in private_chats[chat]['users']:
            if member != name:
                unread.setdefault(member, {})
                unread[member][chat] = unread[member].get(chat, 0) + 1
                if users.get(member, {}).get('sid'):
                    emit('unread_update', {'chat': chat, 'count': unread[member][chat]}, room=users[member]['sid'])

@socketio.on('join_chat')
def join_chat(data):
    chat = data.get('chat', 'general')
    name = data.get('name', '')
    if name not in users:
        return
    join_room(chat)
    if name in unread:
        unread[name][chat] = 0
    msgs = groups.get(chat, {}).get('messages', [])[-100:] if chat in groups else private_chats.get(chat, {}).get('messages', [])[-100:]
    emit('chat_history', {'messages': msgs, 'chat': chat})

@socketio.on('typing')
def typing(data):
    chat = data.get('chat', 'general')
    name = data.get('name', '')
    is_typing = data.get('typing', False)
    typing_users[chat] = typing_users.get(chat, {})
    if is_typing:
        typing_users[chat][name] = time.time()
    else:
        typing_users[chat].pop(name, None)
    emit('typing_status', {'name': name, 'typing': is_typing}, room=chat, include_self=False)

@socketio.on('get_users')
def get_users(data):
    name = data.get('name', '')
    user_list = []
    for n, u in users.items():
        if n != name:
            user_list.append({
                'name': n,
                'avatar': u.get('avatar'),
                'status': u.get('status', 'offline'),
                'bio': u.get('bio', '')
            })
    emit('users_list', {'users': user_list})

@socketio.on('start_private_chat')
def start_private_chat(data):
    user1 = data.get('user1', '')
    user2 = data.get('user2', '')
    if user1 not in users or user2 not in users:
        return
    chat_id = f"p_{min(user1, user2)}_{max(user1, user2)}"
    if chat_id not in private_chats:
        private_chats[chat_id] = {'users': [user1, user2], 'messages': []}
        save_data()
    join_room(chat_id)
    if user1 in unread:
        unread[user1][chat_id] = 0
    msgs = private_chats[chat_id]['messages'][-100:]
    emit('private_chat', {
        'chat_id': chat_id,
        'user': user2,
        'avatar': users[user2].get('avatar'),
        'messages': msgs
    })

@socketio.on('update_avatar')
def update_avatar(data):
    name = data.get('name', '')
    avatar = data.get('avatar', '')
    if name in users:
        users[name]['avatar'] = avatar
        save_data()
        emit('avatar_updated', {'name': name, 'avatar': avatar}, broadcast=True)

@socketio.on('update_bio')
def update_bio(data):
    name = data.get('name', '')
    bio = data.get('bio', '')[:200]
    if name in users:
        users[name]['bio'] = bio
        save_data()
        emit('bio_updated', {'name': name, 'bio': bio})

@socketio.on('create_post')
def create_post(data):
    name = data.get('name', '')
    content = data.get('content', '')
    media_type = data.get('media_type', 'image')
    caption = data.get('caption', '')[:500]
    
    if name not in users:
        return
    if len(content) > 500000:
        content = content[:500000]
    
    post = {
        'id': f"p{len(posts)}_{int(time.time()*1000)}",
        'author': name,
        'avatar': users[name].get('avatar'),
        'content': content,
        'media_type': media_type,
        'caption': caption,
        'likes': [],
        'comments': [],
        'time': datetime.now().strftime("%d.%m.%Y %H:%M"),
        'timestamp': time.time()
    }
    posts.insert(0, post)
    if len(posts) > 50:
        posts.pop()
    save_data()
    emit('new_post', {'post': post}, broadcast=True)

@socketio.on('get_posts')
def get_posts():
    emit('posts_list', {'posts': posts[:30]})

@socketio.on('like_post')
def like_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    for p in posts:
        if p['id'] == post_id:
            if name in p['likes']:
                p['likes'].remove(name)
            else:
                p['likes'].append(name)
            save_data()
            emit('post_updated', {'post': p}, broadcast=True)
            break

@socketio.on('comment_post')
def comment_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    comment = data.get('comment', '')[:300]
    for p in posts:
        if p['id'] == post_id:
            p['comments'].append({
                'name': name,
                'avatar': users.get(name, {}).get('avatar'),
                'comment': comment,
                'time': datetime.now().strftime("%H:%M")
            })
            save_data()
            emit('post_updated', {'post': p}, broadcast=True)
            break

@socketio.on('logout')
def logout(data):
    token = data.get('token', '')
    for name, user in users.items():
        if user.get('token') == token:
            user['token'] = ''
            user['status'] = 'offline'
            user['sid'] = ''
            save_data()
            emit('user_status', {'name': name, 'status': 'offline'}, broadcast=True)
            break

@socketio.on('delete_message')
def delete_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    
    if chat in groups:
        msgs = groups[chat]['messages']
        for i, m in enumerate(msgs):
            if m['id'] == msg_id and m['name'] == name:
                msgs.pop(i)
                save_data()
                emit('message_deleted', {'chat': chat, 'msg_id': msg_id}, room=chat)
                break
    elif chat in private_chats:
        msgs = private_chats[chat]['messages']
        for i, m in enumerate(msgs):
            if m['id'] == msg_id and m['name'] == name:
                msgs.pop(i)
                save_data()
                emit('message_deleted', {'chat': chat, 'msg_id': msg_id}, room=chat)
                break

@socketio.on('edit_message')
def edit_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    new_content = data.get('content', '')[:2000]
    
    if chat in groups:
        for m in groups[chat]['messages']:
            if m['id'] == msg_id and m['name'] == name:
                m['content'] = new_content
                m['edited'] = True
                save_data()
                emit('message_edited', {'chat': chat, 'message': m}, room=chat)
                break
    elif chat in private_chats:
        for m in private_chats[chat]['messages']:
            if m['id'] == msg_id and m['name'] == name:
                m['content'] = new_content
                m['edited'] = True
                save_data()
                emit('message_edited', {'chat': chat, 'message': m}, room=chat)
                break

@socketio.on('share_link')
def share_link():
    emit('share_link', {'url': request.host})

# ========== HTML ==========
HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Shugramm</title>
<style>
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

:root {
    --bg: #0a0a0a;
    --bg2: #141414;
    --bg3: #1e1e1e;
    --bg4: #2a2a2a;
    --gold: #FFD700;
    --gold-dark: #B8960F;
    --gold-light: #FFE44D;
    --text: #ffffff;
    --text-secondary: #888888;
    --border: #2a2a2a;
    --shadow: rgba(255, 215, 0, 0.1);
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    height: 100dvh;
    overflow: hidden;
    display: flex;
    justify-content: center;
    align-items: center;
}

/* ===== SCROLLBAR ===== */
::-webkit-scrollbar {
    width: 4px;
}
::-webkit-scrollbar-track {
    background: var(--bg2);
}
::-webkit-scrollbar-thumb {
    background: var(--gold);
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
    background: var(--gold-dark);
}

/* ===== APP ===== */
#app {
    width: 100%;
    max-width: 480px;
    height: 100vh;
    height: 100dvh;
    background: var(--bg);
    display: flex;
    flex-direction: column;
    position: relative;
    overflow: hidden;
}

/* ===== HEADER ===== */
.header {
    background: var(--bg2);
    padding: 10px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    min-height: 50px;
    z-index: 10;
}

.header-left {
    display: flex;
    align-items: center;
    gap: 8px;
}

.logo {
    color: var(--gold);
    font-size: 20px;
    font-weight: 800;
}

.logo-icon {
    font-size: 24px;
}

.header-title {
    font-size: 17px;
    font-weight: 600;
}

.header-right {
    display: flex;
    gap: 4px;
}

.btn {
    background: none;
    border: none;
    color: var(--text-secondary);
    padding: 6px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 18px;
    width: 34px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
}
.btn:active {
    background: var(--bg3);
    transform: scale(0.9);
}
.btn-gold {
    color: var(--gold);
}
.btn-gold:active {
    background: rgba(255, 215, 0, 0.1);
}

/* ===== NAV ===== */
.nav {
    background: var(--bg2);
    display: flex;
    border-top: 1px solid var(--border);
    flex-shrink: 0;
    padding-bottom: env(safe-area-inset-bottom);
}

.nav-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 6px 0;
    cursor: pointer;
    color: var(--text-secondary);
    font-size: 10px;
    transition: color 0.2s;
    position: relative;
}

.nav-item.active {
    color: var(--gold);
}

.nav-item .icon {
    font-size: 20px;
}
.nav-item .label {
    font-size: 9px;
    font-weight: 500;
}

.nav-item .badge {
    position: absolute;
    top: 2px;
    right: 50%;
    transform: translateX(200%);
    background: #ff4444;
    color: #fff;
    font-size: 9px;
    font-weight: 700;
    min-width: 16px;
    height: 16px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 4px;
}

/* ===== PAGES ===== */
.page {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    display: none;
    -webkit-overflow-scrolling: touch;
    padding-bottom: 4px;
}
.page.active {
    display: block;
}

/* ===== CHAT LIST ===== */
.chat-item {
    display: flex;
    align-items: center;
    padding: 10px 16px;
    gap: 12px;
    cursor: pointer;
    transition: background 0.15s;
    border-bottom: 1px solid rgba(255,255,255,0.03);
}
.chat-item:active {
    background: var(--bg3);
}

.chat-avatar {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    font-weight: 600;
    color: #000;
    background: var(--gold);
    overflow: hidden;
    position: relative;
}
.chat-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.chat-avatar .online-dot {
    position: absolute;
    bottom: 1px;
    right: 1px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 2px solid var(--bg2);
    background: #4CAF50;
}

.chat-info {
    flex: 1;
    min-width: 0;
}
.chat-name {
    font-size: 15px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 6px;
}
.chat-last {
    font-size: 13px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.chat-time {
    font-size: 10px;
    color: var(--text-secondary);
    flex-shrink: 0;
    margin-left: 8px;
}

.chat-unread {
    background: var(--gold);
    color: #000;
    font-size: 10px;
    font-weight: 700;
    min-width: 18px;
    height: 18px;
    border-radius: 9px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 5px;
    margin-left: auto;
}

/* ===== MESSAGES ===== */
#chatWindow {
    display: none;
    flex: 1;
    flex-direction: column;
    min-height: 0;
}
#chatWindow.open {
    display: flex;
}

.messages-container {
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
    -webkit-overflow-scrolling: touch;
    display: flex;
    flex-direction: column;
}

.msg {
    display: flex;
    gap: 6px;
    margin-bottom: 4px;
    max-width: 85%;
    animation: fadeIn 0.2s ease;
}
.msg.self {
    align-self: flex-end;
    flex-direction: row-reverse;
}

.msg-avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: #000;
    background: var(--gold);
    overflow: hidden;
    margin-top: auto;
}
.msg-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.msg-bubble {
    padding: 7px 12px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.4;
    word-wrap: break-word;
    background: var(--bg3);
    max-width: 100%;
}
.msg.self .msg-bubble {
    background: var(--gold);
    color: #000;
}
.msg-bubble img {
    max-width: 200px;
    border-radius: 8px;
    display: block;
    cursor: pointer;
}
.msg-bubble video {
    max-width: 200px;
    border-radius: 8px;
    display: block;
}
.msg-bubble .edited {
    font-size: 9px;
    color: var(--text-secondary);
    opacity: 0.6;
    margin-left: 4px;
}
.msg-time {
    font-size: 9px;
    color: var(--text-secondary);
    text-align: right;
    margin-top: 2px;
}
.msg.self .msg-time {
    color: rgba(0,0,0,0.5);
}

.msg-actions {
    display: flex;
    gap: 2px;
    margin-top: 2px;
    justify-content: flex-end;
}
.msg-actions button {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 10px;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 4px;
}
.msg-actions button:active {
    background: var(--bg4);
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}

/* ===== TYPING ===== */
.typing-indicator {
    font-size: 12px;
    color: var(--text-secondary);
    padding: 2px 14px 6px;
    font-style: italic;
    min-height: 22px;
    opacity: 0;
    transition: opacity 0.3s;
}
.typing-indicator.show {
    opacity: 1;
}

/* ===== INPUT BAR ===== */
.input-bar {
    display: flex;
    padding: 6px 10px;
    background: var(--bg2);
    border-top: 1px solid var(--border);
    gap: 6px;
    align-items: center;
    flex-shrink: 0;
}
.input-bar input {
    flex: 1;
    padding: 8px 14px;
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: 18px;
    color: var(--text);
    font-size: 14px;
    outline: none;
    transition: border 0.3s;
}
.input-bar input:focus {
    border-color: var(--gold);
}
.input-bar input::placeholder {
    color: var(--text-secondary);
}
.input-bar .btn {
    font-size: 16px;
}
.send-btn {
    background: var(--gold);
    color: #000;
    border: none;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.2s;
    flex-shrink: 0;
}
.send-btn:active {
    transform: scale(0.9);
}
.send-btn:disabled {
    opacity: 0.5;
}

/* ===== POSTS ===== */
.post-card {
    background: var(--bg2);
    margin: 8px 12px;
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--border);
}
.post-header {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    gap: 10px;
}
.post-avatar {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 600;
    color: #000;
    background: var(--gold);
    overflow: hidden;
}
.post-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.post-author {
    font-weight: 500;
    font-size: 14px;
}
.post-time {
    font-size: 11px;
    color: var(--text-secondary);
}
.post-media {
    width: 100%;
    max-height: 400px;
    object-fit: cover;
    cursor: pointer;
}
.post-caption {
    padding: 8px 14px;
    font-size: 13px;
    line-height: 1.4;
}
.post-actions {
    display: flex;
    padding: 6px 14px 10px;
    gap: 16px;
    border-top: 1px solid var(--border);
}
.post-action {
    background: none;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 13px;
    padding: 2px 6px;
    border-radius: 6px;
    transition: all 0.2s;
}
.post-action:active {
    transform: scale(0.9);
}
.post-action.liked {
    color: #ff4444;
}
.post-action .count {
    font-size: 12px;
}

.post-comments {
    padding: 0 14px 8px;
}
.post-comment {
    display: flex;
    gap: 6px;
    margin-bottom: 4px;
    font-size: 12px;
}
.post-comment-avatar {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 8px;
    font-weight: 600;
    color: #000;
    background: var(--gold);
    overflow: hidden;
}
.post-comment-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.post-comment-text {
    line-height: 1.3;
}
.post-comment-text b {
    margin-right: 4px;
}

.comment-input {
    display: flex;
    padding: 6px 14px 10px;
    gap: 8px;
    border-top: 1px solid var(--border);
}
.comment-input input {
    flex: 1;
    background: var(--bg3);
    border: none;
    border-radius: 12px;
    padding: 6px 12px;
    color: var(--text);
    font-size: 12px;
    outline: none;
}
.comment-input input::placeholder {
    color: var(--text-secondary);
}
.comment-input button {
    background: var(--gold);
    color: #000;
    border: none;
    padding: 4px 14px;
    border-radius: 12px;
    font-weight: 600;
    cursor: pointer;
    font-size: 12px;
}

/* ===== PROFILE ===== */
.profile-section {
    text-align: center;
    padding: 24px;
    background: var(--bg2);
    margin: 12px;
    border-radius: 12px;
}
.profile-avatar {
    width: 80px;
    height: 80px;
    border-radius: 50%;
    margin: 0 auto 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 32px;
    font-weight: 600;
    color: #000;
    background: var(--gold);
    cursor: pointer;
    overflow: hidden;
    border: 3px solid var(--gold);
    transition: transform 0.2s;
}
.profile-avatar:active {
    transform: scale(0.95);
}
.profile-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.profile-name {
    font-size: 20px;
    font-weight: 600;
}
.profile-bio {
    color: var(--text-secondary);
    font-size: 13px;
    margin-top: 4px;
}
.profile-status {
    font-size: 12px;
    margin-top: 2px;
    color: #4CAF50;
}

.settings-group {
    padding: 0 12px 12px;
}
.setting-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 16px;
    background: var(--bg2);
    margin-bottom: 6px;
    border-radius: 10px;
    cursor: pointer;
    transition: background 0.15s;
}
.setting-item:active {
    background: var(--bg3);
}
.setting-label {
    font-size: 14px;
}
.setting-value {
    color: var(--text-secondary);
    font-size: 13px;
}

/* ===== LOGIN ===== */
#loginScreen {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: var(--bg);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
}
.login-card {
    text-align: center;
    padding: 32px 24px;
    width: 90%;
    max-width: 340px;
}
.login-logo {
    font-size: 48px;
    margin-bottom: 12px;
}
.login-card h1 {
    font-size: 24px;
    font-weight: 700;
    color: var(--gold);
}
.login-card p {
    color: var(--text-secondary);
    font-size: 13px;
    margin: 4px 0 20px;
}
.form-input {
    width: 100%;
    padding: 12px 14px;
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 14px;
    margin-bottom: 8px;
    outline: none;
    text-align: center;
    transition: border 0.3s;
}
.form-input:focus {
    border-color: var(--gold);
}
.form-input.error {
    border-color: #ff4444;
}
.form-btn {
    width: 100%;
    padding: 12px;
    background: var(--gold);
    color: #000;
    border: none;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
}
.form-btn:active {
    opacity: 0.8;
}
.form-link {
    background: none;
    border: none;
    color: var(--gold);
    font-size: 13px;
    cursor: pointer;
    margin-top: 10px;
}
.code-box {
    background: var(--bg3);
    padding: 12px;
    border-radius: 8px;
    font-size: 26px;
    letter-spacing: 8px;
    font-weight: 600;
    color: var(--gold);
    margin: 10px 0;
    font-family: monospace;
}
.hidden {
    display: none !important;
}

/* ===== MEDIA VIEWER ===== */
.media-viewer {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.95);
    z-index: 200;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 20px;
}
.media-viewer.open {
    display: flex;
}
.media-viewer img {
    max-width: 100%;
    max-height: 80vh;
    object-fit: contain;
}
.media-viewer video {
    max-width: 100%;
    max-height: 80vh;
}
.media-close {
    position: absolute;
    top: 16px;
    right: 16px;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: rgba(255,255,255,0.1);
    border: none;
    color: #fff;
    font-size: 20px;
    cursor: pointer;
}

/* ===== NOTIFICATION ===== */
.notification {
    position: fixed;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    background: var(--bg2);
    color: var(--text);
    padding: 10px 20px;
    border-radius: 0 0 12px 12px;
    font-size: 13px;
    max-width: 90%;
    text-align: center;
    z-index: 50;
    display: none;
    border-bottom: 3px solid var(--gold);
    box-shadow: 0 4px 20px rgba(0,0,0,0.6);
    font-weight: 500;
}
.notification.show {
    display: block;
    animation: slideDown 0.3s ease;
}
@keyframes slideDown {
    from { transform: translateX(-50%) translateY(-100%); }
    to { transform: translateX(-50%) translateY(0); }
}

/* ===== FAB ===== */
.fab {
    position: fixed;
    bottom: 80px;
    right: 16px;
    width: 48px;
    height: 48px;
    border-radius: 14px;
    background: var(--gold);
    color: #000;
    border: none;
    font-size: 24px;
    cursor: pointer;
    z-index: 20;
    display: none;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 16px rgba(255,215,0,0.3);
    transition: transform 0.2s;
}
.fab.show {
    display: flex;
}
.fab:active {
    transform: scale(0.9);
}

/* ===== EMPTY STATE ===== */
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: var(--text-secondary);
}
.empty-state .icon {
    font-size: 48px;
    margin-bottom: 12px;
}
.empty-state h3 {
    color: var(--text);
    margin-bottom: 4px;
}
.empty-state p {
    font-size: 13px;
}

/* ===== TOAST ===== */
.toast {
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--bg2);
    padding: 10px 20px;
    border-radius: 10px;
    font-size: 13px;
    z-index: 60;
    border-left: 3px solid var(--gold);
    box-shadow: 0 4px 20px rgba(0,0,0,0.6);
    animation: fadeIn 0.3s ease;
    max-width: 90%;
}

/* ===== RESPONSIVE ===== */
@media (max-width: 480px) {
    .msg { max-width: 90%; }
    .msg-bubble img, .msg-bubble video { max-width: 160px; }
}
</style>
</head>
<body>

<!-- NOTIFICATION -->
<div class="notification" id="notification"></div>

<!-- APP -->
<div id="app">

    <!-- HEADER -->
    <div class="header">
        <div class="header-left">
            <span class="logo-icon">⚡</span>
            <span class="logo" id="headerTitle">Shugramm</span>
        </div>
        <div class="header-right">
            <button class="btn" onclick="shareApp()">📤</button>
        </div>
    </div>

    <!-- PAGES -->
    <div class="page active" id="pageChats">
        <div id="chatList"></div>
    </div>

    <div class="page" id="pageUsers">
        <div style="padding:8px 12px;position:sticky;top:0;background:var(--bg);z-index:5">
            <input class="form-input" id="searchUsers" placeholder="🔍 Поиск пользователей..." oninput="searchUsers()" style="text-align:left">
        </div>
        <div id="usersList"></div>
    </div>

    <div class="page" id="pagePosts">
        <div id="postsList"></div>
    </div>

    <div class="page" id="pageSettings">
        <div id="settingsContent"></div>
    </div>

    <!-- CHAT WINDOW -->
    <div id="chatWindow">
        <div class="header" style="border-bottom:1px solid var(--border);flex-shrink:0">
            <button class="btn" onclick="closeChat()">←</button>
            <span style="font-weight:500;flex:1;font-size:15px" id="chatTitle">Чат</span>
            <button class="btn" onclick="deleteChat()">🗑</button>
        </div>
        <div class="messages-container" id="messagesContainer"></div>
        <div class="typing-indicator" id="typingIndicator"></div>
        <div class="input-bar">
            <button class="btn" onclick="document.getElementById('fileInput').click()">📎</button>
            <input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMessage()" oninput="handleTyping()">
            <button class="send-btn" onclick="sendMessage()">➤</button>
        </div>
    </div>

    <!-- NAV -->
    <div class="nav" id="nav" style="display:none">
        <div class="nav-item active" onclick="switchPage('chats')">
            <span class="icon">💬</span>
            <span class="label">Чаты</span>
            <span class="badge" id="totalBadge" style="display:none">0</span>
        </div>
        <div class="nav-item" onclick="switchPage('users')">
            <span class="icon">👤</span>
            <span class="label">Люди</span>
        </div>
        <div class="nav-item" onclick="switchPage('posts')">
            <span class="icon">📸</span>
            <span class="label">Посты</span>
        </div>
        <div class="nav-item" onclick="switchPage('settings')">
            <span class="icon">⚙️</span>
            <span class="label">Настройки</span>
        </div>
    </div>

    <!-- FAB -->
    <button class="fab" id="fab" onclick="createPost()">+</button>
</div>

<!-- MEDIA VIEWER -->
<div class="media-viewer" id="mediaViewer">
    <button class="media-close" onclick="closeMedia()">✕</button>
    <img id="mediaImg" style="display:none">
    <video id="mediaVideo" controls style="display:none"></video>
</div>

<!-- HIDDEN INPUTS -->
<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">

<!-- LOGIN SCREEN -->
<div id="loginScreen">
    <div class="login-card">
        <div id="loginStep1">
            <div class="login-logo">⚡</div>
            <h1>Shugramm</h1>
            <p>Введите номер телефона для входа</p>
            <input class="form-input" id="phoneInput" placeholder="+7 999 123-45-67" type="tel">
            <button class="form-btn" onclick="requestCode()">Получить код</button>
        </div>
        <div id="loginStep2" class="hidden">
            <div class="login-logo">⚡</div>
            <h1>Код</h1>
            <p>Отправлен на <span id="phoneDisplay" style="color:var(--gold)"></span></p>
            <div class="code-box" id="codeDisplay">000000</div>
            <input class="form-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:6px">
            <button class="form-btn" onclick="verifyCode()">Подтвердить</button>
            <button class="form-link" onclick="backToPhone()">Изменить номер</button>
        </div>
        <div id="loginStep3" class="hidden">
            <div class="login-logo">⚡</div>
            <h1>Регистрация</h1>
            <input class="form-input" id="regPassword" placeholder="Пароль (мин. 4 символа)" type="password">
            <input class="form-input" id="regName" placeholder="Имя пользователя (2-20 символов)">
            <button class="form-btn" onclick="registerUser()">Зарегистрироваться</button>
        </div>
        <div id="loginStep4" class="hidden">
            <div class="login-logo">⚡</div>
            <h1>Вход</h1>
            <p id="loginName" style="color:var(--gold);font-weight:600"></p>
            <input class="form-input" id="loginPassword" placeholder="Пароль" type="password">
            <button class="form-btn" onclick="loginUser()">Войти</button>
            <button class="form-link" onclick="backToStart()">Назад</button>
        </div>
    </div>
</div>

<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
// ========== CONFIG ==========
const socket = io();
let currentUser = null;
let currentToken = null;
let currentChat = 'general';
let currentChatName = 'Общий чат';
let currentAvatar = null;
let currentBio = '';
let typingTimeout = null;
let unreadData = {};
let privateChats = JSON.parse(localStorage.getItem('private_chats') || '[]');
let isChatOpen = false;

// ========== DOM REFS ==========
const $ = id => document.getElementById(id);
const notification = $('notification');
const loginScreen = $('loginScreen');
const nav = $('nav');
const pageChats = $('pageChats');
const pageUsers = $('pageUsers');
const pagePosts = $('pagePosts');
const pageSettings = $('pageSettings');
const chatWindow = $('chatWindow');
const messagesContainer = $('messagesContainer');
const chatTitle = $('chatTitle');
const msgInput = $('msgInput');
const typingIndicator = $('typingIndicator');
const chatList = $('chatList');
const usersList = $('usersList');
const postsList = $('postsList');
const settingsContent = $('settingsContent');
const fab = $('fab');
const totalBadge = $('totalBadge');

// ========== NOTIFICATIONS ==========
function showNotification(msg) {
    notification.textContent = msg;
    notification.classList.add('show');
    clearTimeout(notification._timeout);
    notification._timeout = setTimeout(() => notification.classList.remove('show'), 3000);
}

function showToast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
}

// ========== LOGIN ==========
function requestCode() {
    const phone = $('phoneInput').value.trim();
    if (phone.length < 10) {
        showNotification('Введите корректный номер');
        return;
    }
    socket.emit('register', { phone });
}

function verifyCode() {
    const code = $('codeInput').value.trim();
    if (code.length !== 6) {
        showNotification('Введите 6 цифр');
        return;
    }
    socket.emit('verify_code', { phone: currentPhone, code });
}

let currentPhone = '';

function registerUser() {
    const name = $('regName').value.trim();
    const password = $('regPassword').value.trim();
    if (!name || name.length < 2 || name.length > 20) {
        showNotification('Имя должно быть 2-20 символов');
        return;
    }
    if (!/^[a-zA-Zа-яА-Я0-9_]+$/.test(name)) {
        showNotification('Недопустимые символы в имени');
        return;
    }
    if (password.length < 4) {
        showNotification('Пароль минимум 4 символа');
        return;
    }
    socket.emit('create_user', { phone: currentPhone, name, password });
}

function loginUser() {
    const password = $('loginPassword').value.trim();
    if (!password) {
        showNotification('Введите пароль');
        return;
    }
    socket.emit('login', { name: currentLoginName, password });
}

let currentLoginName = '';

function backToPhone() {
    $('loginStep2').classList.add('hidden');
    $('loginStep1').classList.remove('hidden');
}

function backToStart() {
    $('loginStep4').classList.add('hidden');
    $('loginStep1').classList.remove('hidden');
}

// ========== SOCKET EVENTS ==========
socket.on('code_sent', (data) => {
    currentPhone = data.phone;
    $('loginStep1').classList.add('hidden');
    $('loginStep2').classList.remove('hidden');
    $('phoneDisplay').textContent = '+' + data.phone;
    $('codeDisplay').textContent = data.code;
});

socket.on('user_exists', (data) => {
    currentLoginName = data.name;
    $('loginStep2').classList.add('hidden');
    $('loginStep4').classList.remove('hidden');
    $('loginName').textContent = data.name;
});

socket.on('new_user', (data) => {
    currentPhone = data.phone;
    $('loginStep2').classList.add('hidden');
    $('loginStep3').classList.remove('hidden');
});

socket.on('login_success', (data) => {
    currentUser = data.name;
    currentToken = data.token;
    currentAvatar = data.avatar;
    localStorage.setItem('shugramm_token', data.token);
    localStorage.setItem('shugramm_user', data.name);
    enterApp();
});

socket.on('error', (data) => {
    showNotification(data.message);
});

socket.on('user_joined', (data) => {
    renderUsers();
    renderChats();
});

socket.on('user_status', (data) => {
    renderUsers();
    renderChats();
});

socket.on('new_message', (data) => {
    if (data.chat === currentChat && isChatOpen) {
        renderMessage(data.message);
        scrollToBottom();
    }
    if (data.chat !== currentChat || !isChatOpen) {
        unreadData[data.chat] = (unreadData[data.chat] || 0) + 1;
        updateBadge();
    }
    renderChats();
});

socket.on('chat_history', (data) => {
    messagesContainer.innerHTML = '';
    if (data.messages) {
        data.messages.forEach(m => renderMessage(m));
        scrollToBottom();
    }
});

socket.on('typing_status', (data) => {
    if (data.typing) {
        typingIndicator.textContent = data.name + ' печатает...';
        typingIndicator.classList.add('show');
    } else {
        typingIndicator.classList.remove('show');
    }
});

socket.on('users_list', (data) => {
    renderUsersList(data.users);
});

socket.on('private_chat', (data) => {
    openPrivateChat(data.chat_id, data.user, data.avatar, data.messages);
});

socket.on('avatar_updated', (data) => {
    if (data.name === currentUser) {
        currentAvatar = data.avatar;
    }
    renderChats();
    renderUsers();
});

socket.on('bio_updated', (data) => {
    if (data.name === currentUser) {
        currentBio = data.bio;
        renderSettings();
    }
});

socket.on('new_post', (data) => {
    if (document.getElementById('pagePosts').classList.contains('active')) {
        postsList.insertAdjacentHTML('afterbegin', renderPost(data.post));
    }
});

socket.on('posts_list', (data) => {
    postsList.innerHTML = data.posts.length ? data.posts.map(p => renderPost(p)).join('') : 
        '<div class="empty-state"><div class="icon">📸</div><h3>Нет постов</h3><p>Создайте свой первый пост!</p></div>';
});

socket.on('post_updated', (data) => {
    const el = document.getElementById('post-' + data.post.id);
    if (el) {
        el.outerHTML = renderPost(data.post);
    }
});

socket.on('message_deleted', (data) => {
    if (data.chat === currentChat) {
        const el = document.querySelector(`[data-msg-id="${data.msg_id}"]`);
        if (el) el.remove();
    }
});

socket.on('message_edited', (data) => {
    if (data.chat === currentChat) {
        const el = document.querySelector(`[data-msg-id="${data.message.id}"]`);
        if (el) {
            const bubble = el.querySelector('.msg-bubble');
            if (bubble) {
                bubble.innerHTML = data.message.content + '<span class="edited">✎</span>';
            }
        }
    }
});

socket.on('share_link', (data) => {
    const url = 'https://' + data.url;
    if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(() => showToast('Ссылка скопирована!'));
    } else {
        prompt('Ссылка:', url);
    }
});

// ========== AUTO LOGIN ==========
const savedToken = localStorage.getItem('shugramm_token');
const savedUser = localStorage.getItem('shugramm_user');
if (savedToken && savedUser) {
    socket.emit('auto_login', { token: savedToken });
}

// ========== APP ==========
function enterApp() {
    loginScreen.classList.add('hidden');
    nav.style.display = 'flex';
    fab.classList.add('show');
    loadData();
    renderChats();
    renderUsers();
    renderSettings();
    socket.emit('get_posts');
    setInterval(() => socket.emit('get_users', { name: currentUser }), 30000);
}

function loadData() {
    // Load private chats from localStorage
    privateChats = JSON.parse(localStorage.getItem('private_chats') || '[]');
}

function switchPage(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    
    if (page === 'chats') {
        pageChats.classList.add('active');
        document.querySelector('.nav-item:nth-child(1)').classList.add('active');
        renderChats();
        fab.classList.add('show');
    } else if (page === 'users') {
        pageUsers.classList.add('active');
        document.querySelector('.nav-item:nth-child(2)').classList.add('active');
        socket.emit('get_users', { name: currentUser });
        fab.classList.remove('show');
    } else if (page === 'posts') {
        pagePosts.classList.add('active');
        document.querySelector('.nav-item:nth-child(3)').classList.add('active');
        socket.emit('get_posts');
        fab.classList.add('show');
    } else {
        pageSettings.classList.add('active');
        document.querySelector('.nav-item:nth-child(4)').classList.add('active');
        renderSettings();
        fab.classList.remove('show');
    }
    
    if (isChatOpen) {
        chatWindow.classList.remove('open');
        chatWindow.style.display = 'none';
        isChatOpen = false;
    }
}

function renderChats() {
    let html = `
        <div class="chat-item" onclick="openChat('general', 'Общий чат')">
            <div class="chat-avatar">#</div>
            <div class="chat-info">
                <div class="chat-name">Общий чат</div>
                <div class="chat-last">Нажмите чтобы открыть</div>
            </div>
            ${unreadData['general'] ? `<div class="chat-unread">${unreadData['general']}</div>` : ''}
        </div>
    `;
    
    privateChats.forEach(c => {
        const ur = unreadData[c.id] || 0;
        html += `
            <div class="chat-item" onclick="openPrivateChat('${c.id}', '${c.name}')">
                <div class="chat-avatar">${c.avatar || c.name[0]}</div>
                <div class="chat-info">
                    <div class="chat-name">${c.name}</div>
                    <div class="chat-last">${c.lastMsg || 'Нажмите чтобы открыть'}</div>
                </div>
                ${ur ? `<div class="chat-unread">${ur}</div>` : ''}
            </div>
        `;
    });
    
    chatList.innerHTML = html;
    updateBadge();
}

function renderUsers() {
    socket.emit('get_users', { name: currentUser });
}

function renderUsersList(users) {
    if (!users || !users.length) {
        usersList.innerHTML = '<div class="empty-state"><div class="icon">👤</div><h3>Нет пользователей</h3></div>';
        return;
    }
    usersList.innerHTML = users.map(u => `
        <div class="chat-item" onclick="startPrivateChat('${u.name}')">
            <div class="chat-avatar">
                ${u.avatar ? `<img src="${u.avatar}">` : u.name[0]}
                ${u.status === 'online' ? '<span class="online-dot"></span>' : ''}
            </div>
            <div class="chat-info">
                <div class="chat-name">${u.name}</div>
                <div class="chat-last">${u.bio || 'Привет!'}</div>
            </div>
        </div>
    `).join('');
}

function searchUsers() {
    const query = document.getElementById('searchUsers').value.toLowerCase();
    const items = usersList.querySelectorAll('.chat-item');
    items.forEach(el => {
        const name = el.querySelector('.chat-name').textContent.toLowerCase();
        el.style.display = name.includes(query) ? 'flex' : 'none';
    });
}

// ========== CHAT ==========
function openChat(chat, name) {
    currentChat = chat;
    currentChatName = name;
    isChatOpen = true;
    
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    chatWindow.classList.add('open');
    chatWindow.style.display = 'flex';
    chatTitle.textContent = name;
    messagesContainer.innerHTML = '';
    
    if (unreadData[chat]) {
        unreadData[chat] = 0;
        updateBadge();
    }
    
    socket.emit('join_chat', { chat, name: currentUser });
    msgInput.focus();
}

function openPrivateChat(chatId, name) {
    currentChat = chatId;
    currentChatName = name;
    isChatOpen = true;
    
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    chatWindow.classList.add('open');
    chatWindow.style.display = 'flex';
    chatTitle.textContent = name;
    messagesContainer.innerHTML = '';
    
    if (unreadData[chatId]) {
        unreadData[chatId] = 0;
        updateBadge();
    }
    
    socket.emit('join_chat', { chat: chatId, name: currentUser });
    msgInput.focus();
}

function startPrivateChat(name) {
    if (name === currentUser) return;
    socket.emit('start_private_chat', { user1: currentUser, user2: name });
}

function openPrivateChatData(chatId, user, avatar, messages) {
    currentChat = chatId;
    currentChatName = user;
    isChatOpen = true;
    
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    chatWindow.classList.add('open');
    chatWindow.style.display = 'flex';
    chatTitle.textContent = user;
    messagesContainer.innerHTML = '';
    
    if (messages) {
        messages.forEach(m => renderMessage(m));
        scrollToBottom();
    }
    
    // Save to private chats
    const exists = privateChats.some(c => c.id === chatId);
    if (!exists) {
        privateChats.push({ id: chatId, name: user, avatar: avatar || user[0], lastMsg: '' });
        localStorage.setItem('private_chats', JSON.stringify(privateChats));
    }
    
    if (unreadData[chatId]) {
        unreadData[chatId] = 0;
        updateBadge();
    }
    
    msgInput.focus();
}

function closeChat() {
    chatWindow.classList.remove('open');
    chatWindow.style.display = 'none';
    isChatOpen = false;
    document.getElementById('pageChats').classList.add('active');
    document.querySelector('.nav-item:nth-child(1)').classList.add('active');
    renderChats();
}

function deleteChat() {
    if (!confirm('Удалить чат из списка?')) return;
    privateChats = privateChats.filter(c => c.id !== currentChat);
    localStorage.setItem('private_chats', JSON.stringify(privateChats));
    closeChat();
}

function sendMessage() {
    const text = msgInput.value.trim();
    if (!text) return;
    socket.emit('send_message', {
        name: currentUser,
        chat: currentChat,
        type: 'text',
        content: text
    });
    msgInput.value = '';
    socket.emit('typing', { chat: currentChat, name: currentUser, typing: false });
}

function handleTyping() {
    if (typingTimeout) clearTimeout(typingTimeout);
    socket.emit('typing', { chat: currentChat, name: currentUser, typing: true });
    typingTimeout = setTimeout(() => {
        socket.emit('typing', { chat: currentChat, name: currentUser, typing: false });
    }, 1500);
}

function handleFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
        socket.emit('send_message', {
            name: currentUser,
            chat: currentChat,
            type: file.type.startsWith('video') ? 'video' : 'image',
            content: ev.target.result
        });
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function renderMessage(msg) {
    const isSelf = msg.name === currentUser;
    const div = document.createElement('div');
    div.className = 'msg' + (isSelf ? ' self' : '');
    div.dataset.msgId = msg.id;
    
    let content = msg.content;
    if (msg.type === 'image') {
        content = `<img src="${msg.content}" onclick="openMedia('${msg.content}', 'image')">`;
    } else if (msg.type === 'video') {
        content = `<video src="${msg.content}" controls></video>`;
    } else {
        content = msg.content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    
    const avatar = msg.avatar ? `<img src="${msg.avatar}">` : msg.name[0];
    const actions = isSelf ? `
        <div class="msg-actions">
            <button onclick="editMessage('${msg.id}')">✎</button>
            <button onclick="deleteMessage('${msg.id}')">✕</button>
        </div>
    ` : '';
    
    div.innerHTML = `
        <div class="msg-avatar">${avatar}</div>
        <div>
            <div class="msg-bubble">${content}${msg.edited ? '<span class="edited">✎</span>' : ''}</div>
            <div class="msg-time">${msg.time}</div>
            ${actions}
        </div>
    `;
    messagesContainer.appendChild(div);
}

function deleteMessage(msgId) {
    if (!confirm('Удалить сообщение?')) return;
    socket.emit('delete_message', { chat: currentChat, msg_id: msgId, name: currentUser });
}

function editMessage(msgId) {
    const newText = prompt('Редактировать сообщение:');
    if (newText && newText.trim()) {
        socket.emit('edit_message', { 
            chat: currentChat, 
            msg_id: msgId, 
            name: currentUser, 
            content: newText.trim() 
        });
    }
}

function scrollToBottom() {
    setTimeout(() => {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }, 50);
}

// ========== POSTS ==========
function createPost() {
    document.getElementById('postInput').click();
}

function handlePost(e) {
    const file = e.target.files[0];
    if (!file) return;
    const caption = prompt('Описание:') || '';
    const reader = new FileReader();
    reader.onload = (ev) => {
        socket.emit('create_post', {
            name: currentUser,
            content: ev.target.result,
            media_type: file.type.startsWith('video') ? 'video' : 'image',
            caption: caption
        });
        showNotification('Пост опубликован!');
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function renderPost(p) {
    const isLiked = p.likes && p.likes.includes(currentUser);
    const isAuthor = p.author === currentUser;
    const avatar = p.avatar ? `<img src="${p.avatar}">` : p.author[0];
    
    return `
        <div class="post-card" id="post-${p.id}">
            <div class="post-header">
                <div class="post-avatar">${avatar}</div>
                <div>
                    <div class="post-author">${p.author}</div>
                    <div class="post-time">${p.time}</div>
                </div>
                ${isAuthor ? `<button class="btn" onclick="deletePost('${p.id}')" style="margin-left:auto;color:#ff4444">✕</button>` : ''}
            </div>
            ${p.media_type === 'image' ? `<img class="post-media" src="${p.content}" onclick="openMedia('${p.content}', 'image')">` : ''}
            ${p.media_type === 'video' ? `<video class="post-media" src="${p.content}" controls></video>` : ''}
            <div class="post-caption">${p.caption ? p.caption : ''}</div>
            <div class="post-actions">
                <button class="post-action ${isLiked ? 'liked' : ''}" onclick="likePost('${p.id}')">
                    ${isLiked ? '❤️' : '🤍'} <span class="count">${(p.likes || []).length}</span>
                </button>
                <button class="post-action" onclick="toggleComments('${p.id}')">
                    💬 <span class="count">${(p.comments || []).length}</span>
                </button>
            </div>
            <div class="post-comments" id="comments-${p.id}" style="${(p.comments || []).length ? '' : 'display:none'}">
                ${(p.comments || []).map(c => `
                    <div class="post-comment">
                        <div class="post-comment-avatar">${c.avatar ? `<img src="${c.avatar}">` : c.name[0]}</div>
                        <div class="post-comment-text"><b>${c.name}</b> ${c.comment}</div>
                    </div>
                `).join('')}
            </div>
            <div class="comment-input">
                <input id="comment-${p.id}" placeholder="Написать комментарий..." onkeypress="if(event.key==='Enter')sendComment('${p.id}')">
                <button onclick="sendComment('${p.id}')">Отправить</button>
            </div>
        </div>
    `;
}

function likePost(postId) {
    socket.emit('like_post', { post_id: postId, name: currentUser });
}

function sendComment(postId) {
    const input = document.getElementById('comment-' + postId);
    const text = input.value.trim();
    if (!text) return;
    socket.emit('comment_post', { post_id: postId, name: currentUser, comment: text });
    input.value = '';
}

function toggleComments(postId) {
    const el = document.getElementById('comments-' + postId);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}

function deletePost(postId) {
    if (!confirm('Удалить пост?')) return;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/delete_post', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.send(JSON.stringify({ pid: postId, n: currentUser }));
    setTimeout(() => socket.emit('get_posts'), 500);
}

// ========== SETTINGS ==========
function renderSettings() {
    const avatar = currentAvatar ? `<img src="${currentAvatar}">` : (currentUser ? currentUser[0] : '?');
    settingsContent.innerHTML = `
        <div class="profile-section">
            <div class="profile-avatar" onclick="document.getElementById('avatarInput').click()">
                ${avatar}
            </div>
            <div class="profile-name">${currentUser || 'Гость'}</div>
            <div class="profile-bio">${currentBio || 'Нажмите чтобы добавить описание'}</div>
            <div class="profile-status">🟢 Онлайн</div>
        </div>
        <div class="settings-group">
            <div class="setting-item" onclick="editBio()">
                <span class="setting-label">✏️ Редактировать описание</span>
            </div>
            <div class="setting-item" onclick="shareApp()">
                <span class="setting-label">🔗 Поделиться приложением</span>
            </div>
            <div class="setting-item" onclick="logout()" style="border-left:3px solid #ff4444">
                <span class="setting-label" style="color:#ff4444">🚪 Выйти</span>
            </div>
        </div>
    `;
}

function editBio() {
    const bio = prompt('Введите описание:', currentBio || '');
    if (bio !== null) {
        currentBio = bio;
        socket.emit('update_bio', { name: currentUser, bio });
        renderSettings();
    }
}

function handleAvatar(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
        currentAvatar = ev.target.result;
        socket.emit('update_avatar', { name: currentUser, avatar: ev.target.result });
        renderSettings();
        showNotification('Аватар обновлен!');
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function logout() {
    if (!confirm('Выйти из аккаунта?')) return;
    socket.emit('logout', { token: currentToken });
    localStorage.removeItem('shugramm_token');
    localStorage.removeItem('shugramm_user');
    currentUser = null;
    currentToken = null;
    location.reload();
}

function shareApp() {
    socket.emit('share_link');
}

// ========== MEDIA ==========
function openMedia(src, type) {
    const viewer = document.getElementById('mediaViewer');
    viewer.classList.add('open');
    if (type === 'image') {
        document.getElementById('mediaImg').src = src;
        document.getElementById('mediaImg').style.display = 'block';
        document.getElementById('mediaVideo').style.display = 'none';
    } else {
        document.getElementById('mediaVideo').src = src;
        document.getElementById('mediaVideo').style.display = 'block';
        document.getElementById('mediaImg').style.display = 'none';
        document.getElementById('mediaVideo').play();
    }
}

function closeMedia() {
    document.getElementById('mediaViewer').classList.remove('open');
    document.getElementById('mediaVideo').pause();
}

// ========== BADGE ==========
function updateBadge() {
    let total = 0;
    for (const key in unreadData) {
        total += unreadData[key] || 0;
    }
    if (total > 0) {
        totalBadge.textContent = total;
        totalBadge.style.display = 'flex';
    } else {
        totalBadge.style.display = 'none';
    }
}

// ========== KEYBOARD SHORTCUTS ==========
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (document.getElementById('mediaViewer').classList.contains('open')) {
            closeMedia();
        } else if (isChatOpen) {
            closeChat();
        }
    }
});

// ========== INIT ==========
console.log('⚡ Shugramm загружен!');
</script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
