from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import random, time, os, hashlib, json, re
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'directme-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=100*1024*1024)

users = {}
posts = []
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
    data = {'users': users, 'posts': posts, 'private_chats': private_chats, 'unread': unread}
    with open('directme_data.json', 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_data():
    global users, posts, private_chats, unread
    try:
        with open('directme_data.json', 'r') as f:
            data = json.load(f)
            users = data.get('users', {})
            posts = data.get('posts', [])
            private_chats = data.get('private_chats', {})
            unread = data.get('unread', {})
    except:
        pass

load_data()

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
            user_list.append({'name': n, 'avatar': u.get('avatar'), 'status': u.get('status', 'offline'), 'bio': u.get('bio', '')})
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
    users[name] = {'sid': request.sid, 'phone': phone, 'password': hash_pass(password), 'avatar': None, 'status': 'online', 'bio': '', 'token': token, 'last_seen': time.time()}
    unread[name] = {}
    save_data()
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
            emit('login_success', {'name': name, 'token': token, 'avatar': user.get('avatar')})
            emit('user_joined', {'name': name, 'avatar': user.get('avatar'), 'status': 'online'}, broadcast=True)
            return

@socketio.on('send_message')
def send_message(data):
    name = data.get('name', '')
    chat = data.get('chat', '')
    msg_type = data.get('type', 'text')
    content = data.get('content', '')
    if name not in users or chat not in private_chats:
        return
    if msg_type == 'text':
        content = content[:2000]
    elif msg_type in ['image', 'video']:
        content = content[:150000]
    msg = {'id': f"m{int(time.time()*1000)}", 'name': name, 'type': msg_type, 'content': content, 'time': datetime.now().strftime("%H:%M"), 'avatar': users[name].get('avatar'), 'timestamp': time.time()}
    private_chats[chat]['messages'].append(msg)
    if len(private_chats[chat]['messages']) > 200:
        private_chats[chat]['messages'] = private_chats[chat]['messages'][-100:]
    save_data()
    emit('new_message', {'chat': chat, 'message': msg}, room=chat)
    for member in private_chats[chat]['users']:
        if member != name:
            unread.setdefault(member, {})
            unread[member][chat] = unread[member].get(chat, 0) + 1
            total_unread = sum(unread[member].values())
            if users.get(member, {}).get('sid'):
                emit('unread_update', {'chat': chat, 'count': unread[member][chat], 'total': total_unread}, room=users[member]['sid'])
                emit('push_notification', {'from': name, 'content': content[:100] + ('...' if len(content) > 100 else ''), 'chat_id': chat}, room=users[member]['sid'])

@socketio.on('join_chat')
def join_chat(data):
    chat = data.get('chat', '')
    name = data.get('name', '')
    if name not in users or chat not in private_chats:
        return
    join_room(chat)
    if name in unread:
        unread[name][chat] = 0
    msgs = private_chats.get(chat, {}).get('messages', [])[-100:]
    emit('chat_history', {'messages': msgs, 'chat': chat})

@socketio.on('typing')
def typing(data):
    chat = data.get('chat', '')
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
            user_list.append({'name': n, 'avatar': u.get('avatar'), 'status': u.get('status', 'offline'), 'bio': u.get('bio', '')})
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
    emit('private_chat', {'chat_id': chat_id, 'user': user2, 'avatar': users[user2].get('avatar'), 'messages': msgs})

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
    post = {'id': f"p{len(posts)}_{int(time.time()*1000)}", 'author': name, 'avatar': users[name].get('avatar'), 'content': content, 'media_type': media_type, 'caption': caption, 'likes': [], 'comments': [], 'time': datetime.now().strftime("%d.%m.%Y %H:%M"), 'timestamp': time.time()}
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
            p['comments'].append({'name': name, 'avatar': users.get(name, {}).get('avatar'), 'comment': comment, 'time': datetime.now().strftime("%H:%M")})
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
    if chat in private_chats:
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
    if chat in private_chats:
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

HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>DirectMe</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
:root {
    --bg: #0a0a0a;
    --bg-secondary: #141414;
    --bg-card: #1a1a1a;
    --bg-input: #242424;
    --bg-hover: #2a2a2a;
    --primary: #FFD700;
    --primary-dark: #B8960F;
    --primary-light: #FFE44D;
    --primary-gradient: linear-gradient(135deg, #FFD700, #FFA500);
    --text: #ffffff;
    --text-secondary: #8e8e93;
    --text-muted: #636366;
    --border: #2c2c2e;
    --shadow: rgba(255, 215, 0, 0.15);
    --bubble-self: #FFD700;
    --bubble-other: #1c1c1e;
    --radius: 16px;
    --radius-sm: 10px;
}
body.light {
    --bg: #f2f2f7;
    --bg-secondary: #ffffff;
    --bg-card: #e5e5ea;
    --bg-input: #e5e5ea;
    --bg-hover: #d1d1d6;
    --text: #000000;
    --text-secondary: #3a3a3c;
    --text-muted: #8e8e93;
    --border: #c6c6c8;
    --bubble-other: #e5e5ea;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, system-ui, sans-serif; background: var(--bg); color: var(--text); height: 100vh; height: 100dvh; overflow: hidden; display: flex; justify-content: center; align-items: center; user-select: none; }
::-webkit-scrollbar { width: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--text-muted); border-radius: 3px; }
#app { width: 100%; max-width: 480px; height: 100vh; height: 100dvh; background: var(--bg); display: flex; flex-direction: column; position: relative; overflow: hidden; }
.header { background: var(--bg-secondary); padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; border-bottom: 0.5px solid var(--border); flex-shrink: 0; min-height: 52px; z-index: 10; }
.header-left { display: flex; align-items: center; gap: 10px; }
.header-left .logo { font-size: 20px; font-weight: 700; background: var(--primary-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.header-left .logo-icon { font-size: 22px; }
.header-title { font-size: 17px; font-weight: 600; }
.header-right { display: flex; gap: 2px; }
.btn-icon { background: none; border: none; color: var(--text-secondary); padding: 6px; border-radius: 50%; cursor: pointer; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
.btn-icon:hover { background: var(--bg-hover); }
.btn-icon:active { transform: scale(0.92); }
.btn-icon svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.nav { background: var(--bg-secondary); display: flex; border-top: 0.5px solid var(--border); flex-shrink: 0; padding-bottom: env(safe-area-inset-bottom); }
.nav-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 0 4px; cursor: pointer; color: var(--text-secondary); font-size: 10px; transition: color 0.2s; position: relative; }
.nav-item.active { color: var(--primary); }
.nav-item svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.nav-item .label { font-size: 9px; font-weight: 500; }
.nav-item .badge { position: absolute; top: 2px; right: 50%; transform: translateX(180%); background: #ff3b30; color: #fff; font-size: 9px; font-weight: 700; min-width: 16px; height: 16px; border-radius: 8px; display: flex; align-items: center; justify-content: center; padding: 0 4px; border: 1.5px solid var(--bg-secondary); }
.page { flex: 1; overflow-y: auto; overflow-x: hidden; display: none; -webkit-overflow-scrolling: touch; padding-bottom: 4px; }
.page.active { display: block; }
.chat-item { display: flex; align-items: center; padding: 10px 16px; gap: 12px; cursor: pointer; transition: background 0.15s; border-bottom: 0.5px solid rgba(255,255,255,0.03); }
.chat-item:active { background: var(--bg-hover); }
.chat-avatar { width: 48px; height: 48px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; position: relative; }
.chat-avatar img { width: 100%; height: 100%; object-fit: cover; }
.chat-avatar .online-dot { position: absolute; bottom: 2px; right: 2px; width: 11px; height: 11px; border-radius: 50%; border: 2px solid var(--bg-secondary); background: #30d158; }
.chat-avatar .online-dot.offline { background: var(--text-muted); }
.chat-info { flex: 1; min-width: 0; }
.chat-name { font-size: 15px; font-weight: 500; display: flex; align-items: center; gap: 6px; }
.chat-last { font-size: 13px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chat-time { font-size: 10px; color: var(--text-secondary); flex-shrink: 0; margin-left: 8px; }
.chat-unread { background: var(--primary); color: #000; font-size: 10px; font-weight: 600; min-width: 18px; height: 18px; border-radius: 9px; display: flex; align-items: center; justify-content: center; padding: 0 5px; margin-left: auto; }
#chatWindow { display: none; flex: 1; flex-direction: column; min-height: 0; background: var(--bg); }
#chatWindow.open { display: flex; }
.messages-container { flex: 1; overflow-y: auto; padding: 8px 12px; -webkit-overflow-scrolling: touch; display: flex; flex-direction: column; gap: 2px; }
.msg { display: flex; gap: 6px; margin-bottom: 2px; max-width: 88%; animation: msgIn 0.25s ease; }
.msg.self { align-self: flex-end; flex-direction: row-reverse; }
@keyframes msgIn { from { opacity: 0; transform: translateY(8px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
.msg-avatar { width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; margin-top: auto; }
.msg-avatar img { width: 100%; height: 100%; object-fit: cover; }
.msg-bubble { padding: 8px 14px; border-radius: 16px; font-size: 14px; line-height: 1.45; word-wrap: break-word; background: var(--bubble-other); max-width: 100%; box-shadow: 0 1px 2px rgba(0,0,0,0.2); color: var(--text); }
.msg.self .msg-bubble { background: var(--bubble-self); color: #000; }
.msg-bubble img { max-width: 200px; border-radius: 10px; display: block; cursor: pointer; }
.msg-bubble video { max-width: 200px; border-radius: 10px; display: block; }
.msg-bubble .edited { font-size: 9px; color: var(--text-secondary); opacity: 0.6; margin-left: 4px; }
.msg.self .msg-bubble .edited { color: rgba(0,0,0,0.5); }
.msg-time { font-size: 9px; color: var(--text-secondary); text-align: right; margin-top: 2px; padding-right: 2px; }
.msg.self .msg-time { color: rgba(0,0,0,0.5); }
.msg-actions { display: flex; gap: 2px; margin-top: 2px; justify-content: flex-end; }
.msg-actions button { background: none; border: none; color: var(--text-secondary); font-size: 10px; cursor: pointer; padding: 2px 6px; border-radius: 4px; transition: background 0.2s; }
.msg-actions button:hover { background: var(--bg-hover); }
.typing-indicator { font-size: 12px; color: var(--text-secondary); padding: 2px 16px 6px; font-style: italic; min-height: 22px; opacity: 0; transition: opacity 0.3s; }
.typing-indicator.show { opacity: 1; }
.input-bar { display: flex; padding: 6px 12px 8px; background: var(--bg-secondary); border-top: 0.5px solid var(--border); gap: 6px; align-items: center; flex-shrink: 0; }
.input-bar input { flex: 1; padding: 8px 16px; background: var(--bg-input); border: none; border-radius: 20px; color: var(--text); font-size: 14px; outline: none; transition: all 0.2s; }
.input-bar input:focus { background: var(--bg-hover); }
.input-bar input::placeholder { color: var(--text-secondary); }
.input-bar .btn-icon { width: 34px; height: 34px; }
.send-btn { background: var(--primary-gradient); color: #000; border: none; width: 34px; height: 34px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s; flex-shrink: 0; }
.send-btn:active { transform: scale(0.9); opacity: 0.8; }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.send-btn svg { width: 20px; height: 20px; fill: none; stroke: #000; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.post-card { background: var(--bg-secondary); margin: 8px 12px; border-radius: var(--radius); overflow: hidden; border: 0.5px solid var(--border); }
.post-header { display: flex; align-items: center; padding: 10px 14px; gap: 10px; }
.post-avatar { width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; }
.post-avatar img { width: 100%; height: 100%; object-fit: cover; }
.post-author { font-weight: 500; font-size: 14px; }
.post-time { font-size: 11px; color: var(--text-secondary); }
.post-media { width: 100%; max-height: 400px; object-fit: cover; cursor: pointer; }
.post-caption { padding: 8px 14px; font-size: 13px; line-height: 1.4; }
.post-actions { display: flex; padding: 6px 14px 10px; gap: 20px; border-top: 0.5px solid var(--border); }
.post-action { background: none; border: none; color: var(--text-secondary); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 13px; padding: 2px 6px; border-radius: 6px; transition: all 0.2s; }
.post-action:hover { color: var(--text); }
.post-action:active { transform: scale(0.92); }
.post-action.liked { color: #ff3b30; }
.post-action.liked svg { fill: #ff3b30; stroke: #ff3b30; }
.post-action .count { font-size: 12px; }
.post-action svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.post-comments-wrap { max-height: 0; overflow: hidden; transition: max-height 0.35s ease; }
.post-comments-wrap.open { max-height: 300px; }
.post-comments { padding: 0 14px 8px; max-height: 260px; overflow-y: auto; }
.post-comment { display: flex; gap: 8px; padding: 4px 0; font-size: 12px; border-bottom: 0.5px solid rgba(255,255,255,0.04); }
.post-comment:last-child { border-bottom: none; }
.post-comment-avatar { width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; }
.post-comment-avatar img { width: 100%; height: 100%; object-fit: cover; }
.post-comment-text { line-height: 1.3; }
.post-comment-text b { margin-right: 4px; }
.comment-input { display: flex; padding: 6px 14px 10px; gap: 8px; border-top: 0.5px solid var(--border); }
.comment-input input { flex: 1; background: var(--bg-input); border: none; border-radius: 12px; padding: 6px 12px; color: var(--text); font-size: 12px; outline: none; }
.comment-input input::placeholder { color: var(--text-secondary); }
.comment-input button { background: var(--primary-gradient); color: #000; border: none; padding: 4px 14px; border-radius: 12px; font-weight: 600; cursor: pointer; font-size: 12px; transition: opacity 0.2s; }
.comment-input button:active { opacity: 0.7; }
.profile-section { text-align: center; padding: 28px 20px 20px; background: var(--bg-secondary); margin: 12px; border-radius: var(--radius); }
.profile-avatar { width: 80px; height: 80px; border-radius: 50%; margin: 0 auto 10px; display: flex; align-items: center; justify-content: center; font-size: 32px; font-weight: 600; color: #fff; background: var(--primary-gradient); cursor: pointer; overflow: hidden; border: 3px solid var(--primary); transition: transform 0.2s; }
.profile-avatar:active { transform: scale(0.95); }
.profile-avatar img { width: 100%; height: 100%; object-fit: cover; }
.profile-name { font-size: 20px; font-weight: 600; }
.profile-bio { color: var(--text-secondary); font-size: 13px; margin-top: 4px; }
.profile-status { font-size: 12px; margin-top: 2px; color: #30d158; }
.settings-group { padding: 0 12px 12px; }
.setting-item { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; background: var(--bg-secondary); margin-bottom: 6px; border-radius: var(--radius-sm); cursor: pointer; transition: background 0.15s; }
.setting-item:active { background: var(--bg-hover); }
.setting-label { font-size: 14px; }
.setting-value { color: var(--text-secondary); font-size: 13px; }
#loginScreen { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: var(--bg); display: flex; align-items: center; justify-content: center; z-index: 100; }
.login-card { text-align: center; padding: 32px 24px; width: 90%; max-width: 340px; }
.login-logo { font-size: 52px; margin-bottom: 12px; }
.login-card h1 { font-size: 26px; font-weight: 700; background: var(--primary-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.login-card p { color: var(--text-secondary); font-size: 13px; margin: 4px 0 20px; }
.form-input { width: 100%; padding: 12px 16px; background: var(--bg-secondary); border: 1.5px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-size: 14px; margin-bottom: 10px; outline: none; text-align: center; transition: border 0.3s; }
.form-input:focus { border-color: var(--primary); }
.form-input.error { border-color: #ff3b30; }
.form-btn { width: 100%; padding: 12px; background: var(--primary-gradient); color: #000; border: none; border-radius: var(--radius-sm); font-size: 14px; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
.form-btn:active { opacity: 0.8; }
.form-link { background: none; border: none; color: var(--primary); font-size: 13px; cursor: pointer; margin-top: 10px; }
.code-box { background: var(--bg-input); padding: 12px; border-radius: var(--radius-sm); font-size: 28px; letter-spacing: 10px; font-weight: 600; color: var(--primary); margin: 10px 0; font-family: monospace; }
.hidden { display: none !important; }
.media-viewer { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.95); z-index: 200; display: none; align-items: center; justify-content: center; padding: 20px; }
.media-viewer.open { display: flex; }
.media-viewer img { max-width: 100%; max-height: 80vh; object-fit: contain; }
.media-viewer video { max-width: 100%; max-height: 80vh; }
.media-close { position: absolute; top: 16px; right: 16px; width: 40px; height: 40px; border-radius: 50%; background: rgba(255,255,255,0.1); border: none; color: #fff; font-size: 20px; cursor: pointer; }
.push-notification { position: fixed; top: 60px; left: 50%; transform: translateX(-50%) translateY(-20px); background: var(--bg-secondary); padding: 14px 20px; border-radius: var(--radius); border-left: 4px solid var(--primary); box-shadow: 0 8px 32px rgba(0,0,0,0.6); z-index: 100; max-width: 90%; min-width: 280px; opacity: 0; pointer-events: none; transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1); }
.push-notification.show { opacity: 1; transform: translateX(-50%) translateY(0); pointer-events: auto; }
.push-notification .pn-header { display: flex; align-items: center; gap: 10px; }
.push-notification .pn-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; flex-shrink: 0; }
.push-notification .pn-avatar img { width: 100%; height: 100%; object-fit: cover; }
.push-notification .pn-name { font-weight: 600; font-size: 14px; }
.push-notification .pn-text { font-size: 13px; color: var(--text-secondary); margin-top: 2px; }
.push-notification .pn-close { background: none; border: none; color: var(--text-secondary); font-size: 18px; cursor: pointer; margin-left: auto; padding: 0 4px; }
.fab { position: fixed; bottom: 80px; right: 16px; width: 52px; height: 52px; border-radius: 16px; background: var(--primary-gradient); color: #fff; border: none; cursor: pointer; z-index: 20; display: none; align-items: center; justify-content: center; box-shadow: 0 4px 20px var(--shadow); transition: all 0.2s; }
.fab.show { display: flex; }
.fab:active { transform: scale(0.9); }
.fab svg { width: 28px; height: 28px; fill: none; stroke: #000; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
.empty-state .icon { font-size: 48px; margin-bottom: 12px; }
.empty-state h3 { color: var(--text); margin-bottom: 4px; }
.empty-state p { font-size: 13px; }
.toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: var(--bg-secondary); padding: 10px 20px; border-radius: var(--radius-sm); font-size: 13px; z-index: 60; border-left: 3px solid var(--primary); box-shadow: 0 4px 20px rgba(0,0,0,0.6); animation: toastIn 0.3s ease; max-width: 90%; }
@keyframes toastIn { from { opacity: 0; transform: translateX(-50%) translateY(20px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
@media (max-width: 480px) { .msg { max-width: 92%; } .msg-bubble img, .msg-bubble video { max-width: 160px; } .push-notification { min-width: unset; width: 92%; } }
</style>
</head>
<body>

<div class="push-notification" id="pushNotification" onclick="openChatFromPush()">
    <div class="pn-header">
        <div class="pn-avatar" id="pnAvatar">👤</div>
        <div style="flex:1;min-width:0">
            <div class="pn-name" id="pnName">Имя</div>
            <div class="pn-text" id="pnText">Сообщение</div>
        </div>
        <button class="pn-close" onclick="event.stopPropagation();closePush()">✕</button>
    </div>
</div>

<div id="app">
    <div class="header">
        <div class="header-left">
            <span class="logo-icon">💬</span>
            <span class="logo" id="headerTitle">DirectMe</span>
        </div>
        <div class="header-right">
            <button class="btn-icon" onclick="shareApp()">
                <svg viewBox="0 0 24 24"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>
            </button>
        </div>
    </div>

    <div class="page active" id="pageChats"><div id="chatList"></div></div>
    <div class="page" id="pageUsers">
        <div style="padding:8px 12px;position:sticky;top:0;background:var(--bg);z-index:5">
            <input class="form-input" id="searchUsers" placeholder="🔍 Поиск..." oninput="searchUsers()" style="text-align:left">
        </div>
        <div id="usersList"></div>
    </div>
    <div class="page" id="pagePosts"><div id="postsList"></div></div>
    <div class="page" id="pageSettings"><div id="settingsContent"></div></div>

    <div id="chatWindow">
        <div class="header" style="border-bottom:0.5px solid var(--border);flex-shrink:0">
            <button class="btn-icon" onclick="closeChat()">
                <svg viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"/></svg>
            </button>
            <span style="font-weight:500;flex:1;font-size:16px" id="chatTitle">Чат</span>
            <button class="btn-icon" onclick="deleteChat()">
                <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
            </button>
        </div>
        <div class="messages-container" id="messagesContainer"></div>
        <div class="typing-indicator" id="typingIndicator"></div>
        <div class="input-bar">
            <button class="btn-icon" onclick="document.getElementById('fileInput').click()">
                <svg viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="8" y1="2" x2="8" y2="22"/><line x1="16" y1="2" x2="16" y2="22"/><line x1="2" y1="8" x2="22" y2="8"/><line x1="2" y1="16" x2="22" y2="16"/></svg>
            </button>
            <input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMessage()" oninput="handleTyping()">
            <button class="send-btn" onclick="sendMessage()">
                <svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
            </button>
        </div>
    </div>

    <div class="nav" id="nav" style="display:none">
        <div class="nav-item active" onclick="switchPage('chats')">
            <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <span class="label">Чаты</span>
            <span class="badge" id="totalBadge" style="display:none">0</span>
        </div>
        <div class="nav-item" onclick="switchPage('users')">
            <svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
            <span class="label">Люди</span>
        </div>
        <div class="nav-item" onclick="switchPage('posts')">
            <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            <span class="label">Посты</span>
        </div>
        <div class="nav-item" onclick="switchPage('settings')">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v4"/><path d="M12 19v4"/><path d="M4.22 4.22l2.83 2.83"/><path d="M16.95 16.95l2.83 2.83"/><path d="M1 12h4"/><path d="M19 12h4"/><path d="M4.22 19.78l2.83-2.83"/><path d="M16.95 7.05l2.83-2.83"/></svg>
            <span class="label">Настройки</span>
        </div>
    </div>

    <button class="fab" id="fab" onclick="createPost()" style="display:none">
        <svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
    </button>
</div>

<div class="media-viewer" id="mediaViewer">
    <button class="media-close" onclick="closeMedia()">✕</button>
    <img id="mediaImg" style="display:none">
    <video id="mediaVideo" controls style="display:none"></video>
</div>

<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">

<div id="loginScreen">
    <div class="login-card">
        <div id="loginStep1">
            <div class="login-logo">💬</div>
            <h1>DirectMe</h1>
            <p>Введите номер телефона</p>
            <input class="form-input" id="phoneInput" placeholder="+7 999 123-45-67" type="tel">
            <button class="form-btn" onclick="requestCode()" type="button">Получить код</button>
        </div>
        <div id="loginStep2" class="hidden">
            <div class="login-logo">💬</div>
            <h1>Код</h1>
            <p>Отправлен на <span id="phoneDisplay" style="color:var(--primary)"></span></p>
            <div class="code-box" id="codeDisplay">000000</div>
            <input class="form-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:6px">
            <button class="form-btn" onclick="verifyCode()" type="button">Подтвердить</button>
            <button class="form-link" onclick="backToPhone()">Изменить номер</button>
        </div>
        <div id="loginStep3" class="hidden">
            <div class="login-logo">💬</div>
            <h1>Регистрация</h1>
            <input class="form-input" id="regPassword" placeholder="Пароль (мин. 4)" type="password">
            <input class="form-input" id="regName" placeholder="Имя (2-20 символов)">
            <button class="form-btn" onclick="registerUser()" type="button">Зарегистрироваться</button>
        </div>
        <div id="loginStep4" class="hidden">
            <div class="login-logo">💬</div>
            <h1>Вход</h1>
            <p id="loginName" style="color:var(--primary);font-weight:600"></p>
            <input class="form-input" id="loginPassword" placeholder="Пароль" type="password">
            <button class="form-btn" onclick="loginUser()" type="button">Войти</button>
            <button class="form-link" onclick="backToStart()">Назад</button>
        </div>
    </div>
</div>

<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const socket = io();
let currentUser = null, currentToken = null, currentChat = null, currentChatName = '';
let currentAvatar = null, currentBio = '', typingTimeout = null, isChatOpen = false;
let unreadData = {}, privateChats = JSON.parse(localStorage.getItem('private_chats') || '[]');
let pushData = null, pushTimeout = null;
let currentPhone = '', currentLoginName = '';

const $ = id => document.getElementById(id);
const chatList = $('chatList'), usersList = $('usersList'), postsList = $('postsList');
const settingsContent = $('settingsContent'), fab = $('fab'), totalBadge = $('totalBadge');
const chatWindow = $('chatWindow'), messagesContainer = $('messagesContainer');
const chatTitle = $('chatTitle'), msgInput = $('msgInput'), typingIndicator = $('typingIndicator');

// ===== THEME =====
function toggleTheme() {
    document.body.classList.toggle('light');
    const isLight = document.body.classList.contains('light');
    document.getElementById('themeLabel').textContent = isLight ? 'Светлая' : 'Темная';
    localStorage.setItem('directme_theme', isLight ? 'light' : 'dark');
}

function loadTheme() {
    const savedTheme = localStorage.getItem('directme_theme');
    if (savedTheme === 'light') {
        document.body.classList.add('light');
        document.getElementById('themeLabel').textContent = 'Светлая';
    }
}

// ===== PUSH =====
function showPush(from, content, chatId) {
    const el = $('pushNotification');
    const avatar = $('pnAvatar');
    const name = $('pnName');
    const text = $('pnText');
    const user = Object.values(users).find(u => u.name === from);
    avatar.innerHTML = (user && user.avatar) ? `<img src="${user.avatar}">` : from[0];
    name.textContent = from;
    text.textContent = content;
    pushData = { chatId, from };
    el.classList.add('show');
    clearTimeout(pushTimeout);
    pushTimeout = setTimeout(closePush, 5000);
}

function closePush() { $('pushNotification').classList.remove('show'); pushData = null; }

function openChatFromPush() {
    if (pushData) { closePush(); openPrivateChat(pushData.chatId, pushData.from); }
}

// ===== LOGIN =====
function requestCode() {
    console.log('📞 Запрос кода...');
    const phone = document.getElementById('phoneInput').value.trim();
    if (phone.length < 10) { showNotification('Введите корректный номер'); return; }
    socket.emit('register', { phone });
}

function verifyCode() {
    console.log('🔐 Проверка кода...');
    const code = document.getElementById('codeInput').value.trim();
    if (code.length !== 6) { showNotification('Введите 6 цифр'); return; }
    socket.emit('verify_code', { phone: currentPhone, code });
}

function registerUser() {
    console.log('📝 Регистрация...');
    const name = document.getElementById('regName').value.trim();
    const password = document.getElementById('regPassword').value.trim();
    if (!name || name.length < 2 || name.length > 20) { showNotification('Имя должно быть 2-20 символов'); return; }
    if (!/^[a-zA-Zа-яА-Я0-9_]+$/.test(name)) { showNotification('Недопустимые символы в имени'); return; }
    if (password.length < 4) { showNotification('Пароль минимум 4 символа'); return; }
    socket.emit('create_user', { phone: currentPhone, name, password });
}

function loginUser() {
    console.log('🔑 Вход...');
    const password = document.getElementById('loginPassword').value.trim();
    if (!password) { showNotification('Введите пароль'); return; }
    socket.emit('login', { name: currentLoginName, password });
}

function backToPhone() {
    document.getElementById('loginStep2').classList.add('hidden');
    document.getElementById('loginStep1').classList.remove('hidden');
}

function backToStart() {
    document.getElementById('loginStep4').classList.add('hidden');
    document.getElementById('loginStep1').classList.remove('hidden');
}

function showNotification(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
}

// ===== SOCKET EVENTS =====
socket.on('code_sent', (data) => {
    console.log('✅ Код отправлен:', data.code);
    currentPhone = data.phone;
    document.getElementById('loginStep1').classList.add('hidden');
    document.getElementById('loginStep2').classList.remove('hidden');
    document.getElementById('phoneDisplay').textContent = '+' + data.phone;
    document.getElementById('codeDisplay').textContent = data.code;
});

socket.on('user_exists', (data) => {
    console.log('👤 Пользователь найден:', data.name);
    currentLoginName = data.name;
    document.getElementById('loginStep2').classList.add('hidden');
    document.getElementById('loginStep4').classList.remove('hidden');
    document.getElementById('loginName').textContent = data.name;
});

socket.on('new_user', (data) => {
    console.log('🆕 Новый пользователь');
    currentPhone = data.phone;
    document.getElementById('loginStep2').classList.add('hidden');
    document.getElementById('loginStep3').classList.remove('hidden');
});

socket.on('login_success', (data) => {
    console.log('✅ Вход выполнен:', data.name);
    currentUser = data.name;
    currentToken = data.token;
    currentAvatar = data.avatar;
    localStorage.setItem('directme_token', data.token);
    localStorage.setItem('directme_user', data.name);
    enterApp();
});

socket.on('error', (data) => { showNotification(data.message); });

socket.on('push_notification', (data) => {
    showPush(data.from, data.content, data.chat_id);
    if (document.getElementById('pageUsers').classList.contains('active')) {
        switchPage('chats');
    }
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
    if (data.messages) { data.messages.forEach(m => renderMessage(m)); scrollToBottom(); }
});

socket.on('typing_status', (data) => {
    if (data.typing) { typingIndicator.textContent = data.name + ' печатает...'; typingIndicator.classList.add('show'); }
    else { typingIndicator.classList.remove('show'); }
});

socket.on('users_list', (data) => { renderUsersList(data.users); });

socket.on('private_chat', (data) => {
    openPrivateChat(data.chat_id, data.user, data.avatar, data.messages);
});

socket.on('avatar_updated', (data) => {
    if (data.name === currentUser) { currentAvatar = data.avatar; }
    renderChats(); renderUsers();
});

socket.on('bio_updated', (data) => {
    if (data.name === currentUser) { currentBio = data.bio; renderSettings(); }
});

socket.on('new_post', (data) => {
    if (document.getElementById('pagePosts').classList.contains('active')) {
        postsList.insertAdjacentHTML('afterbegin', renderPost(data.post));
    }
});

socket.on('posts_list', (data) => {
    postsList.innerHTML = data.posts.length ? data.posts.map(p => renderPost(p)).join('') : 
        '<div class="empty-state"><div class="icon">📸</div><h3>Нет постов</h3><p>Создайте первый пост!</p></div>';
});

socket.on('post_updated', (data) => {
    const el = document.getElementById('post-' + data.post.id);
    if (el) { el.outerHTML = renderPost(data.post); }
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
            if (bubble) { bubble.innerHTML = data.message.content + '<span class="edited">✎</span>'; }
        }
    }
});

socket.on('share_link', (data) => {
    const url = 'https://' + data.url;
    if (navigator.clipboard) { navigator.clipboard.writeText(url).then(() => showNotification('Ссылка скопирована!')); }
    else { prompt('Ссылка:', url); }
});

// ===== APP =====
function enterApp() {
    document.getElementById('loginScreen').classList.add('hidden');
    document.getElementById('nav').style.display = 'flex';
    loadTheme();
    renderChats();
    renderUsers();
    renderSettings();
    socket.emit('get_posts');
    setInterval(() => socket.emit('get_users', { name: currentUser }), 30000);
}

function switchPage(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (page === 'chats') {
        document.getElementById('pageChats').classList.add('active');
        document.querySelector('.nav-item:nth-child(1)').classList.add('active');
        renderChats();
        fab.style.display = 'none';
    } else if (page === 'users') {
        document.getElementById('pageUsers').classList.add('active');
        document.querySelector('.nav-item:nth-child(2)').classList.add('active');
        socket.emit('get_users', { name: currentUser });
        fab.style.display = 'none';
    } else if (page === 'posts') {
        document.getElementById('pagePosts').classList.add('active');
        document.querySelector('.nav-item:nth-child(3)').classList.add('active');
        socket.emit('get_posts');
        fab.style.display = 'flex';
    } else {
        document.getElementById('pageSettings').classList.add('active');
        document.querySelector('.nav-item:nth-child(4)').classList.add('active');
        renderSettings();
        fab.style.display = 'none';
    }
    if (isChatOpen) { chatWindow.classList.remove('open'); chatWindow.style.display = 'none'; isChatOpen = false; }
}

function renderChats() {
    if (!privateChats.length) {
        chatList.innerHTML = '<div class="empty-state"><div class="icon">💬</div><h3>Нет чатов</h3><p>Найдите людей в разделе "Люди"</p></div>';
        return;
    }
    let html = '';
    privateChats.forEach(c => {
        const ur = unreadData[c.id] || 0;
        const lastMsg = c.lastMsg || 'Напишите первым...';
        html += `
            <div class="chat-item" onclick="openPrivateChat('${c.id}', '${c.name}')">
                <div class="chat-avatar">${c.avatar ? `<img src="${c.avatar}">` : c.name[0]}<span class="online-dot ${c.status === 'online' ? '' : 'offline'}"></span></div>
                <div class="chat-info"><div class="chat-name">${c.name}</div><div class="chat-last">${lastMsg}</div></div>
                ${ur ? `<div class="chat-unread">${ur}</div>` : ''}
            </div>
        `;
    });
    chatList.innerHTML = html;
    updateBadge();
}

function renderUsers() { socket.emit('get_users', { name: currentUser }); }

function renderUsersList(users) {
    if (!users || !users.length) {
        usersList.innerHTML = '<div class="empty-state"><div class="icon">👤</div><h3>Нет пользователей</h3></div>';
        return;
    }
    usersList.innerHTML = users.map(u => `
        <div class="chat-item" onclick="startPrivateChat('${u.name}')">
            <div class="chat-avatar">${u.avatar ? `<img src="${u.avatar}">` : u.name[0]}<span class="online-dot ${u.status === 'online' ? '' : 'offline'}"></span></div>
            <div class="chat-info"><div class="chat-name">${u.name}</div><div class="chat-last">${u.bio || 'Привет!'}</div></div>
        </div>
    `).join('');
}

function searchUsers() {
    const query = document.getElementById('searchUsers').value.toLowerCase();
    document.querySelectorAll('#usersList .chat-item').forEach(el => {
        const name = el.querySelector('.chat-name').textContent.toLowerCase();
        el.style.display = name.includes(query) ? 'flex' : 'none';
    });
}

function startPrivateChat(name) {
    if (name === currentUser) return;
    socket.emit('start_private_chat', { user1: currentUser, user2: name });
}

function openPrivateChat(chatId, name, avatar, messages) {
    currentChat = chatId;
    currentChatName = name;
    isChatOpen = true;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    chatWindow.classList.add('open');
    chatWindow.style.display = 'flex';
    chatTitle.textContent = name;
    messagesContainer.innerHTML = '';
    if (messages) { messages.forEach(m => renderMessage(m)); scrollToBottom(); }
    const exists = privateChats.some(c => c.id === chatId);
    if (!exists) {
        privateChats.push({ id: chatId, name, avatar: avatar || name[0], lastMsg: '' });
        localStorage.setItem('private_chats', JSON.stringify(privateChats));
    }
    if (unreadData[chatId]) { unreadData[chatId] = 0; updateBadge(); }
    socket.emit('join_chat', { chat: chatId, name: currentUser });
    msgInput.focus();
    renderChats();
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
    if (!text || !currentChat) return;
    socket.emit('send_message', { name: currentUser, chat: currentChat, type: 'text', content: text });
    msgInput.value = '';
    socket.emit('typing', { chat: currentChat, name: currentUser, typing: false });
    const chat = privateChats.find(c => c.id === currentChat);
    if (chat) { chat.lastMsg = text; localStorage.setItem('private_chats', JSON.stringify(privateChats)); }
}

function handleTyping() {
    if (typingTimeout) clearTimeout(typingTimeout);
    socket.emit('typing', { chat: currentChat, name: currentUser, typing: true });
    typingTimeout = setTimeout(() => socket.emit('typing', { chat: currentChat, name: currentUser, typing: false }), 1500);
}

function renderMessage(msg) {
    const isSelf = msg.name === currentUser;
    const div = document.createElement('div');
    div.className = 'msg' + (isSelf ? ' self' : '');
    div.dataset.msgId = msg.id;
    let content = msg.content;
    if (msg.type === 'image') content = `<img src="${msg.content}" onclick="openMedia('${msg.content}','image')">`;
    else if (msg.type === 'video') content = `<video src="${msg.content}" controls></video>`;
    else content = msg.content.replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
    const newText = prompt('Редактировать:');
    if (newText?.trim()) socket.emit('edit_message', { chat: currentChat, msg_id: msgId, name: currentUser, content: newText.trim() });
}

function scrollToBottom() { setTimeout(() => messagesContainer.scrollTop = messagesContainer.scrollHeight, 50); }

function updateBadge() {
    const total = Object.values(unreadData).reduce((a,b) => a + b, 0);
    if (total > 0) { totalBadge.textContent = total; totalBadge.style.display = 'flex'; }
    else { totalBadge.style.display = 'none'; }
}

function handleFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
        socket.emit('send_message', { name: currentUser, chat: currentChat, type: file.type.startsWith('video') ? 'video' : 'image', content: ev.target.result });
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

// ===== POSTS =====
function createPost() { document.getElementById('postInput').click(); }

function handlePost(e) {
    const file = e.target.files[0];
    if (!file) return;
    const caption = prompt('Описание:') || '';
    const reader = new FileReader();
    reader.onload = (ev) => {
        socket.emit('create_post', { name: currentUser, content: ev.target.result, media_type: file.type.startsWith('video') ? 'video' : 'image', caption });
        showNotification('Пост опубликован!');
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function renderPost(p) {
    const isLiked = p.likes?.includes(currentUser);
    const isAuthor = p.author === currentUser;
    const avatar = p.avatar ? `<img src="${p.avatar}">` : p.author[0];
    const comments = p.comments || [];
    const hasComments = comments.length > 0;
    return `
        <div class="post-card" id="post-${p.id}">
            <div class="post-header">
                <div class="post-avatar">${avatar}</div>
                <div><div class="post-author">${p.author}</div><div class="post-time">${p.time}</div></div>
                ${isAuthor ? `<button class="btn-icon" onclick="deletePost('${p.id}')" style="margin-left:auto;color:#ff3b30"><svg viewBox="0 0 24 24" width="18" height="18"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>` : ''}
            </div>
            ${p.media_type === 'image' ? `<img class="post-media" src="${p.content}" onclick="openMedia('${p.content}','image')">` : ''}
            ${p.media_type === 'video' ? `<video class="post-media" src="${p.content}" controls></video>` : ''}
            <div class="post-caption">${p.caption || ''}</div>
            <div class="post-actions">
                <button class="post-action ${isLiked ? 'liked' : ''}" onclick="likePost('${p.id}')">
                    <svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                    <span class="count">${(p.likes || []).length}</span>
                </button>
                <button class="post-action" onclick="toggleComments('${p.id}')">
                    <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <span class="count">${comments.length}</span>
                </button>
            </div>
            <div class="post-comments-wrap ${hasComments ? 'open' : ''}" id="comments-wrap-${p.id}">
                <div class="post-comments" id="comments-${p.id}">
                    ${comments.map(c => `
                        <div class="post-comment">
                            <div class="post-comment-avatar">${c.avatar ? `<img src="${c.avatar}">` : c.name[0]}</div>
                            <div class="post-comment-text"><b>${c.name}</b> ${c.comment}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
            <div class="comment-input">
                <input id="comment-${p.id}" placeholder="Написать комментарий..." onkeypress="if(event.key==='Enter')sendComment('${p.id}')">
                <button onclick="sendComment('${p.id}')">→</button>
            </div>
        </div>
    `;
}

function toggleComments(postId) {
    const wrap = document.getElementById('comments-wrap-' + postId);
    if (wrap) wrap.classList.toggle('open');
}

function likePost(postId) { socket.emit('like_post', { post_id: postId, name: currentUser }); }

function sendComment(postId) {
    const input = document.getElementById('comment-' + postId);
    const text = input.value.trim();
    if (!text) return;
    socket.emit('comment_post', { post_id: postId, name: currentUser, comment: text });
    input.value = '';
}

function deletePost(postId) {
    if (!confirm('Удалить пост?')) return;
    fetch('/delete_post', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid: postId, n: currentUser }) });
    setTimeout(() => socket.emit('get_posts'), 500);
}

// ===== SETTINGS =====
function renderSettings() {
    const avatar = currentAvatar ? `<img src="${currentAvatar}">` : (currentUser ? currentUser[0] : '?');
    settingsContent.innerHTML = `
        <div class="profile-section">
            <div class="profile-avatar" onclick="document.getElementById('avatarInput').click()">${avatar}</div>
            <div class="profile-name">${currentUser || 'Гость'}</div>
            <div class="profile-bio">${currentBio || 'Нажмите чтобы добавить описание'}</div>
            <div class="profile-status">🟢 Онлайн</div>
        </div>
        <div class="settings-group">
            <div class="setting-item" onclick="toggleTheme()">
                <span class="setting-label">🌓 Тема: <span id="themeLabel">Темная</span></span>
            </div>
            <div class="setting-item" onclick="editBio()"><span class="setting-label">✏️ Редактировать описание</span></div>
            <div class="setting-item" onclick="shareApp()"><span class="setting-label">🔗 Поделиться</span></div>
            <div class="setting-item" onclick="logout()" style="border-left:3px solid #ff3b30"><span class="setting-label" style="color:#ff3b30">🚪 Выйти</span></div>
        </div>
    `;
}

function editBio() {
    const bio = prompt('Введите описание:', currentBio || '');
    if (bio !== null) { currentBio = bio; socket.emit('update_bio', { name: currentUser, bio }); renderSettings(); }
}

function handleAvatar(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => { currentAvatar = ev.target.result; socket.emit('update_avatar', { name: currentUser, avatar: ev.target.result }); renderSettings(); showNotification('Аватар обновлен!'); };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function logout() {
    if (!confirm('Выйти?')) return;
    socket.emit('logout', { token: currentToken });
    localStorage.removeItem('directme_token');
    localStorage.removeItem('directme_user');
    location.reload();
}

function shareApp() { socket.emit('share_link'); }

// ===== MEDIA =====
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

// ===== AUTO LOGIN =====
const savedToken = localStorage.getItem('directme_token');
const savedUser = localStorage.getItem('directme_user');
if (savedToken && savedUser) socket.emit('auto_login', { token: savedToken });

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (document.getElementById('mediaViewer').classList.contains('open')) closeMedia();
        else if (isChatOpen) closeChat();
    }
});

console.log('💬 DirectMe загружен!');
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
