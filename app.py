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

# ========== HTTP РОУТЫ ==========
@app.route('/')
def index():
    return render_template_string(HTML)

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
    print(f"✅ Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    for name, user in users.items():
        if user.get('sid') == request.sid:
            user['status'] = 'offline'
            user['sid'] = ''
            emit('user_status', {'name': name, 'status': 'offline'}, broadcast=True)
            save_data()
            break

@socketio.on('register')
def register(data):
    phone = ''.join(filter(str.isdigit, data.get('phone', '')))
    if len(phone) < 10:
        emit('error', {'message': 'Введите корректный номер'})
        return
    code = str(random.randint(100000, 999999))
    pending[phone] = {'code': code, 'time': time.time()}
    print(f"📱 Code for {phone}: {code}")
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
        emit('error', {'message': 'Имя 2-20 символов'})
        return
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_]+$', name):
        emit('error', {'message': 'Недопустимые символы'})
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
    
    # Уведомления и непрочитанные
    chat_name = chat
    if chat in private_chats:
        for member in private_chats[chat]['users']:
            if member != name:
                unread.setdefault(member, {})
                unread[member][chat] = unread[member].get(chat, 0) + 1
                if users.get(member, {}).get('sid'):
                    emit('unread_update', {'chat': chat, 'count': unread[member][chat], 'name': chat_name}, room=users[member]['sid'])
                    # Push уведомление
                    emit('push_notification', {
                        'title': name,
                        'body': content[:100] if msg_type == 'text' else '📎 Медиа',
                        'chat': chat,
                        'sender': name
                    }, room=users[member]['sid'])
    else:
        # Общий чат - уведомления для всех
        for member in groups[chat]['members']:
            if member != name:
                unread.setdefault(member, {})
                unread[member][chat] = unread[member].get(chat, 0) + 1
                if users.get(member, {}).get('sid'):
                    emit('unread_update', {'chat': chat, 'count': unread[member][chat], 'name': 'Общий чат'}, room=users[member]['sid'])
                    emit('push_notification', {
                        'title': name,
                        'body': content[:100] if msg_type == 'text' else '📎 Медиа',
                        'chat': chat,
                        'sender': name
                    }, room=users[member]['sid'])

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
    padding: 8px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    min-height: 48px;
    z-index: 10;
}

.header-left {
    display: flex;
    align-items: center;
    gap: 8px;
}

.header-title {
    font-size: 17px;
    font-weight: 600;
    color: var(--gold);
}

.header-right {
    display: flex;
    gap: 2px;
}

.btn {
    background: none;
    border: none;
    color: var(--text-secondary);
    padding: 6px;
    border-radius: 50%;
    cursor: pointer;
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

/* ===== ICONS ===== */
.icon {
    width: 20px;
    height: 20px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
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
    padding: 6px 0 8px;
    cursor: pointer;
    color: var(--text-secondary);
    font-size: 9px;
    transition: color 0.2s;
    position: relative;
    border: none;
    background: none;
}
.nav-item.active {
    color: var(--gold);
}
.nav-item .icon {
    width: 22px;
    height: 22px;
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
    position: relative;
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
}
.chat-name .unread-badge {
    display: inline-block;
    background: var(--gold);
    color: #000;
    font-size: 10px;
    font-weight: 700;
    min-width: 18px;
    height: 18px;
    border-radius: 9px;
    text-align: center;
    line-height: 18px;
    padding: 0 5px;
    margin-left: 6px;
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
    cursor: pointer;
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
.send-btn {
    background: var(--gold);
    color: #000;
    border: none;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.2s;
    flex-shrink: 0;
}
.send-btn:active {
    transform: scale(0.9);
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
    margin-top: 
