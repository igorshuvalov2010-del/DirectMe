from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
import random, time, os, hashlib, json, re, base64, io
from functools import wraps
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'directme-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=500*1024*1024)

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

def hash_pass(password):
    salt = os.urandom(32).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_pass(password, hashed):
    salt, hash_value = hashed.split(':')
    return hash_value == hashlib.sha256((salt + password).encode()).hexdigest()

def generate_token():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]

def get_user_by_username(username):
    for name, user in users.items():
        if user.get('username') == username:
            return name, user
    return None, None

def is_blocked(user1, user2):
    return user2 in blocked_users.get(user1, []) or user1 in blocked_users.get(user2, [])

def save_data():
    data = {
        'users': users, 'posts': posts, 'stories': stories,
        'private_chats': private_chats, 'group_chats': group_chats,
        'unread': unread, 'blocked_users': blocked_users,
        'saved_posts': saved_posts, 'reposts': reposts,
        'pinned_messages': pinned_messages
    }
    with open('directme_data.json', 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_data():
    global users, posts, stories, private_chats, group_chats, unread, blocked_users, saved_posts, reposts, pinned_messages
    try:
        with open('directme_data.json', 'r') as f:
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
    return jsonify({'posts': list(posts.values())[:30]})

@app.route('/api/users')
@auth_required
def get_users_api(user, name):
    user_list = []
    for n, u in users.items():
        if n != name and n not in blocked_users.get(name, []):
            user_list.append({'name': n, 'username': u.get('username', n), 'avatar': u.get('avatar'), 'status': u.get('status', 'offline'), 'bio': u.get('bio', '')})
    return jsonify({'users': user_list})

@app.route('/delete_post', methods=['POST'])
def delete_post():
    data = request.get_json()
    pid = data.get('pid', '')
    name = data.get('n', '')
    if pid in posts and posts[pid]['author'] == name:
        del posts[pid]
        save_data()
        return {'ok': True}
    return {'ok': False}, 403

# ============================================================
#  WEBSOCKET: АУТЕНТИФИКАЦИЯ
# ============================================================
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    for name, user in users.items():
        if user.get('sid') == request.sid:
            user['status'] = 'offline'
            user['sid'] = ''
            user_status_history[name] = {'status': 'offline', 'time': time.time()}
            emit('user_status', {'name': name, 'status': 'offline', 'last_seen': time.time()}, broadcast=True)
            save_data()
            break

@socketio.on('register')
def register(data):
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    
    if not username or len(username) < 3 or len(username) > 20:
        emit('error', {'message': 'Юзернейм 3-20 символов'})
        return
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        emit('error', {'message': 'Юзернейм: латиница, цифры, _'})
        return
    if username in users:
        emit('error', {'message': 'Пользователь уже существует'})
        return
    if len(password) < 4:
        emit('error', {'message': 'Пароль минимум 4 символа'})
        return
    
    token = generate_token()
    users[username] = {
        'sid': request.sid,
        'username': username,
        'password': hash_pass(password),
        'avatar': None,
        'status': 'online',
        'bio': '',
        'token': token,
        'last_seen': time.time(),
        'created_at': time.time(),
        'is_banned': False
    }
    unread[username] = {}
    user_status_history[username] = {'status': 'online', 'time': time.time()}
    save_data()
    emit('login_success', {'name': username, 'username': username, 'token': token, 'avatar': None, 'bio': ''})
    emit('user_joined', {'name': username, 'username': username, 'avatar': None, 'status': 'online'}, broadcast=True)

@socketio.on('login')
def login(data):
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    
    if username not in users:
        emit('error', {'message': 'Пользователь не найден'})
        return
    if users[username].get('is_banned', False):
        emit('error', {'message': 'Аккаунт заблокирован'})
        return
    if not verify_pass(password, users[username]['password']):
        emit('error', {'message': 'Неверный пароль'})
        return
    
    token = generate_token()
    users[username]['sid'] = request.sid
    users[username]['status'] = 'online'
    users[username]['token'] = token
    users[username]['last_seen'] = time.time()
    user_status_history[username] = {'status': 'online', 'time': time.time()}
    save_data()
    emit('login_success', {
        'name': username,
        'username': username,
        'token': token,
        'avatar': users[username].get('avatar'),
        'bio': users[username].get('bio', '')
    })
    emit('user_joined', {
        'name': username,
        'username': username,
        'avatar': users[username].get('avatar'),
        'status': 'online'
    }, broadcast=True)

@socketio.on('auto_login')
def auto_login(data):
    token = data.get('token', '')
    for username, user in users.items():
        if user.get('token') == token:
            if user.get('is_banned', False):
                emit('error', {'message': 'Аккаунт заблокирован'})
                return
            user['sid'] = request.sid
            user['status'] = 'online'
            user['last_seen'] = time.time()
            user_status_history[username] = {'status': 'online', 'time': time.time()}
            save_data()
            emit('login_success', {
                'name': username,
                'username': username,
                'token': token,
                'avatar': user.get('avatar'),
                'bio': user.get('bio', '')
            })
            emit('user_joined', {
                'name': username,
                'username': username,
                'avatar': user.get('avatar'),
                'status': 'online'
            }, broadcast=True)
            return

# ============================================================
#  WEBSOCKET: СООБЩЕНИЯ, РЕАКЦИИ, РЕПЛАИ, ПЕРЕСЫЛКА
# ============================================================
@socketio.on('send_message')
def send_message(data):
    name = data.get('name', '')
    chat = data.get('chat', '')
    msg_type = data.get('type', 'text')
    content = data.get('content', '')
    reply_to = data.get('reply_to', None)
    forwarded_from = data.get('forwarded_from', None)
    
    if name not in users:
        return
    if chat in private_chats:
        for member in private_chats[chat]['users']:
            if member != name and is_blocked(name, member):
                emit('error', {'message': 'Вы заблокированы'})
                return
    
    if msg_type == 'text':
        content = content[:5000]
    elif msg_type in ['image', 'video']:
        content = content[:500000]
    elif msg_type == 'voice':
        content = content[:200000]
    
    msg = {
        'id': f"m{int(time.time()*1000)}_{random.randint(1000, 9999)}",
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
    
    if chat in private_chats:
        private_chats[chat]['messages'].append(msg)
        if len(private_chats[chat]['messages']) > 500:
            private_chats[chat]['messages'] = private_chats[chat]['messages'][-300:]
        save_data()
        emit('new_message', {'chat': chat, 'message': msg}, room=chat)
        for member in private_chats[chat]['users']:
            if member != name:
                unread.setdefault(member, {})
                unread[member][chat] = unread[member].get(chat, 0) + 1
                if users.get(member, {}).get('sid'):
                    emit('push_notification', {'from': name, 'content': content[:100] + ('...' if len(content) > 100 else ''), 'chat_id': chat, 'msg_id': msg['id']}, room=users[member]['sid'])
    
    elif chat in group_chats:
        if name not in group_chats[chat]['members']:
            return
        group_chats[chat]['messages'].append(msg)
        if len(group_chats[chat]['messages']) > 500:
            group_chats[chat]['messages'] = group_chats[chat]['messages'][-300:]
        save_data()
        emit('new_message', {'chat': chat, 'message': msg}, room=chat)
        for member in group_chats[chat]['members']:
            if member != name:
                unread.setdefault(member, {})
                unread[member][chat] = unread[member].get(chat, 0) + 1
                if users.get(member, {}).get('sid'):
                    emit('push_notification', {'from': name, 'content': content[:100] + ('...' if len(content) > 100 else ''), 'chat_id': chat, 'msg_id': msg['id']}, room=users[member]['sid'])

@socketio.on('join_chat')
def join_chat(data):
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
    if name in unread:
        unread[name][chat] = 0
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages'][-200:]
    elif chat in group_chats:
        msgs = group_chats[chat]['messages'][-200:]
    for msg in msgs:
        if msg['name'] != name and name not in msg.get('read_by', []):
            msg['read_by'] = msg.get('read_by', []) + [name]
    save_data()
    emit('chat_history', {'messages': msgs, 'chat': chat})

@socketio.on('typing')
def typing(data):
    chat = data.get('chat', '')
    name = data.get('name', '')
    is_typing = data.get('typing', False)
    if chat in private_chats and is_blocked(name, [u for u in private_chats[chat]['users'] if u != name][0]):
        return
    typing_users[chat] = typing_users.get(chat, {})
    if is_typing:
        typing_users[chat][name] = time.time()
    else:
        typing_users[chat].pop(name, None)
    emit('typing_status', {'name': name, 'typing': is_typing}, room=chat, include_self=False)

@socketio.on('message_reaction')
def message_reaction(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    reaction = data.get('reaction', '')
    if name not in users or reaction not in ['❤️', '🔥', '👍', '👎', '😂', '😮', '😡', '🥰', '😱', '💯', '👏', '🙌', '🎉']:
        return
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    for msg in msgs:
        if msg['id'] == msg_id:
            if name in msg['reactions'] and msg['reactions'][name] == reaction:
                del msg['reactions'][name]
            else:
                msg['reactions'][name] = reaction
            save_data()
            emit('reaction_updated', {'chat': chat, 'msg_id': msg_id, 'reactions': msg['reactions']}, room=chat)
            break

@socketio.on('reply_message')
def reply_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    reply_text = data.get('reply', '')[:500]
    if name not in users:
        return
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    original = None
    for msg in msgs:
        if msg['id'] == msg_id:
            original = msg
            break
    if not original:
        return
    reply_msg = {
        'id': f"m{int(time.time()*1000)}_{random.randint(1000, 9999)}",
        'name': name,
        'type': 'text',
        'content': reply_text,
        'reply_to': {'id': original['id'], 'name': original['name'], 'content': original['content'][:100] + ('...' if len(original['content']) > 100 else '')},
        'time': datetime.now().strftime("%H:%M"),
        'timestamp': time.time(),
        'avatar': users[name].get('avatar'),
        'edited': False,
        'reactions': {},
        'read_by': [name]
    }
    if chat in private_chats:
        private_chats[chat]['messages'].append(reply_msg)
        save_data()
        emit('new_message', {'chat': chat, 'message': reply_msg}, room=chat)
    elif chat in group_chats:
        group_chats[chat]['messages'].append(reply_msg)
        save_data()
        emit('new_message', {'chat': chat, 'message': reply_msg}, room=chat)

@socketio.on('forward_message')
def forward_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    target_user = data.get('to', '')
    if name not in users or target_user not in users:
        return
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    original = None
    for msg in msgs:
        if msg['id'] == msg_id:
            original = msg
            break
    if not original:
        return
    chat_id = f"p_{min(name, target_user)}_{max(name, target_user)}"
    if chat_id not in private_chats:
        private_chats[chat_id] = {'users': [name, target_user], 'messages': []}
        save_data()
    forward_msg = {
        'id': f"m{int(time.time()*1000)}_{random.randint(1000, 9999)}",
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
    private_chats[chat_id]['messages'].append(forward_msg)
    save_data()
    join_room(chat_id)
    emit('new_message', {'chat': chat_id, 'message': forward_msg}, room=chat_id)

@socketio.on('pin_message')
def pin_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    if chat in group_chats and group_chats[chat]['admin'] != name:
        return
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    for m in msgs:
        if m['id'] == msg_id:
            if msg_id in pinned_messages.get(chat, []):
                pinned_messages[chat].remove(msg_id)
                m['is_pinned'] = False
            else:
                pinned_messages.setdefault(chat, []).append(msg_id)
                m['is_pinned'] = True
            save_data()
            emit('message_pinned', {'chat': chat, 'msg_id': msg_id, 'pinned': m['is_pinned']}, room=chat)
            break

@socketio.on('delete_message')
def delete_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    delete_for_all = data.get('delete_for_all', False)
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    for i, m in enumerate(msgs):
        if m['id'] == msg_id:
            if m['name'] == name or (chat in group_chats and group_chats[chat]['admin'] == name):
                if delete_for_all:
                    del msgs[i]
                else:
                    m['content'] = 'Сообщение удалено'
                    m['deleted'] = True
                save_data()
                emit('message_deleted', {'chat': chat, 'msg_id': msg_id, 'delete_for_all': delete_for_all}, room=chat)
                break

@socketio.on('edit_message')
def edit_message(data):
    chat = data.get('chat', '')
    msg_id = data.get('msg_id', '')
    name = data.get('name', '')
    new_content = data.get('content', '')[:5000]
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    for m in msgs:
        if m['id'] == msg_id and m['name'] == name:
            m['content'] = new_content
            m['edited'] = True
            save_data()
            emit('message_edited', {'chat': chat, 'message': m}, room=chat)
            break

# ============================================================
#  WEBSOCKET: ГРУППЫ, СТОРИС, ПОСТЫ
# ============================================================
@socketio.on('create_group')
def create_group(data):
    name = data.get('name', '')
    group_name = data.get('group_name', 'Новая группа')
    members = data.get('members', [])
    if name not in users or len(members) < 2:
        emit('error', {'message': 'Нужно минимум 2 участника'})
        return
    chat_id = f"g_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    group_chats[chat_id] = {'name': group_name, 'admin': name, 'members': [name] + members, 'messages': [], 'created_at': time.time(), 'avatar': None}
    save_data()
    join_room(chat_id)
    for member in [name] + members:
        if users.get(member, {}).get('sid'):
            emit('group_created', {'chat_id': chat_id, 'name': group_name, 'members': [name] + members}, room=users[member]['sid'])
    emit('private_chat', {'chat_id': chat_id, 'user': group_name, 'avatar': None, 'messages': [], 'is_group': True})

@socketio.on('add_group_member')
def add_group_member(data):
    chat = data.get('chat', '')
    name = data.get('name', '')
    new_member = data.get('member', '')
    if chat not in group_chats or group_chats[chat]['admin'] != name or new_member not in users:
        return
    if new_member not in group_chats[chat]['members']:
        group_chats[chat]['members'].append(new_member)
        save_data()
        if users.get(new_member, {}).get('sid'):
            emit('group_updated', {'chat': chat, 'members': group_chats[chat]['members']}, room=users[new_member]['sid'])
        emit('group_updated', {'chat': chat, 'members': group_chats[chat]['members']}, room=chat)

@socketio.on('remove_group_member')
def remove_group_member(data):
    chat = data.get('chat', '')
    name = data.get('name', '')
    remove_user = data.get('user', '')
    if chat not in group_chats or group_chats[chat]['admin'] != name or remove_user not in group_chats[chat]['members'] or remove_user == group_chats[chat]['admin']:
        return
    group_chats[chat]['members'].remove(remove_user)
    save_data()
    emit('group_updated', {'chat': chat, 'members': group_chats[chat]['members']}, room=chat)

@socketio.on('create_story')
def create_story(data):
    name = data.get('name', '')
    content = data.get('content', '')
    media_type = data.get('type', 'image')
    if name not in users:
        return
    story = {'id': f"s{int(time.time()*1000)}_{random.randint(1000, 9999)}", 'name': name, 'content': content, 'type': media_type, 'timestamp': time.time(), 'views': []}
    stories.setdefault(name, []).append(story)
    if len(stories[name]) > 10:
        stories[name] = stories[name][-10:]
    save_data()
    emit('new_story', {'name': name, 'story': story}, broadcast=True)
    threading.Timer(86400, lambda: delete_story_after_time(name, story['id'])).start()

def delete_story_after_time(name, story_id):
    if name in stories:
        stories[name] = [s for s in stories[name] if s['id'] != story_id]
        save_data()
        emit('story_deleted', {'name': name, 'story_id': story_id}, broadcast=True)

@socketio.on('view_story')
def view_story(data):
    name = data.get('name', '')
    story_id = data.get('story_id', '')
    viewer = data.get('viewer', '')
    if name in stories:
        for story in stories[name]:
            if story['id'] == story_id and viewer not in story['views']:
                story['views'].append(viewer)
                save_data()
                emit('story_viewed', {'name': name, 'story_id': story_id, 'views': story['views']}, broadcast=True)
                break

@socketio.on('create_post')
def create_post(data):
    name = data.get('name', '')
    content = data.get('content', '')
    media_type = data.get('media_type', 'image')
    caption = data.get('caption', '')[:500]
    hashtags = re.findall(r'#(\w+)', caption)
    if name not in users or len(content) > 500000:
        return
    post_id = f"p{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    posts[post_id] = {'id': post_id, 'author': name, 'avatar': users[name].get('avatar'), 'content': content, 'media_type': media_type, 'caption': caption, 'hashtags': hashtags, 'likes': [], 'comments': [], 'saved_by': [], 'reposts': [], 'time': datetime.now().strftime("%d.%m.%Y %H:%M"), 'timestamp': time.time()}
    save_data()
    emit('new_post', {'post': posts[post_id]}, broadcast=True)

@socketio.on('get_posts')
def get_posts():
    emit('posts_list', {'posts': list(posts.values())[:50]})

@socketio.on('like_post')
def like_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    if post_id in posts:
        if name in posts[post_id]['likes']:
            posts[post_id]['likes'].remove(name)
        else:
            posts[post_id]['likes'].append(name)
        save_data()
        emit('post_updated', {'post': posts[post_id]}, broadcast=True)

@socketio.on('comment_post')
def comment_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    comment = data.get('comment', '')[:300]
    if post_id in posts:
        posts[post_id]['comments'].append({'id': f"c{int(time.time()*1000)}_{random.randint(1000, 9999)}", 'name': name, 'avatar': users.get(name, {}).get('avatar'), 'comment': comment, 'time': datetime.now().strftime("%H:%M"), 'timestamp': time.time(), 'likes': []})
        save_data()
        emit('post_updated', {'post': posts[post_id]}, broadcast=True)

@socketio.on('save_post')
def save_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    if post_id in posts:
        if name in posts[post_id]['saved_by']:
            posts[post_id]['saved_by'].remove(name)
        else:
            posts[post_id]['saved_by'].append(name)
        save_data()
        emit('post_updated', {'post': posts[post_id]}, broadcast=True)

@socketio.on('repost_post')
def repost_post(data):
    post_id = data.get('post_id', '')
    name = data.get('name', '')
    if post_id in posts and name not in posts[post_id]['reposts']:
        posts[post_id]['reposts'].append(name)
        save_data()
        emit('post_updated', {'post': posts[post_id]}, broadcast=True)

# ============================================================
#  WEBSOCKET: ПРОФИЛЬ, БЛОКИРОВКА, ПОИСК, ЧАТЫ
# ============================================================
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

@socketio.on('update_profile')
def update_profile(data):
    name = data.get('name', '')
    new_name = data.get('new_name', '').strip()
    new_username = data.get('new_username', '').strip().lower()
    if name not in users:
        return
    if new_name and len(new_name) >= 2 and len(new_name) <= 20 and re.match(r'^[a-zA-Zа-яА-Я0-9_]+$', new_name) and new_name not in users:
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
        emit('profile_updated', {'old_name': name, 'new_name': new_name}, broadcast=True)
        return
    if new_username and len(new_username) >= 3 and len(new_username) <= 20 and re.match(r'^[a-zA-Z0-9_]+$', new_username):
        for n, u in users.items():
            if u.get('username') == new_username and n != name:
                emit('error', {'message': 'Юзернейм занят'})
                return
        users[name]['username'] = new_username
        save_data()
        emit('username_updated', {'name': name, 'username': new_username}, broadcast=True)

@socketio.on('block_user')
def block_user(data):
    name = data.get('name', '')
    block_name = data.get('block_name', '')
    if name not in users or block_name not in users:
        return
    if block_name not in blocked_users.get(name, []):
        blocked_users.setdefault(name, []).append(block_name)
        save_data()
        emit('user_blocked', {'by': name, 'blocked': block_name}, room=users[block_name].get('sid') if users[block_name].get('sid') else '')

@socketio.on('unblock_user')
def unblock_user(data):
    name = data.get('name', '')
    unblock_name = data.get('unblock_name', '')
    if name in blocked_users and unblock_name in blocked_users[name]:
        blocked_users[name].remove(unblock_name)
        save_data()
        emit('user_unblocked', {'by': name, 'unblocked': unblock_name}, room=users[unblock_name].get('sid') if users[unblock_name].get('sid') else '')

@socketio.on('search_users')
def search_users(data):
    query = data.get('query', '').lower()
    name = data.get('name', '')
    results = []
    for n, u in users.items():
        if n != name and n not in blocked_users.get(name, []):
            if query in n.lower() or query in u.get('username', '').lower():
                results.append({'name': n, 'username': u.get('username', n), 'avatar': u.get('avatar'), 'status': u.get('status', 'offline')})
    emit('search_results', {'results': results[:20]})

@socketio.on('search_hashtag')
def search_hashtag(data):
    tag = data.get('tag', '').lower()
    results = []
    for post in list(posts.values()):
        if tag in [h.lower() for h in post.get('hashtags', [])]:
            results.append(post)
    emit('search_results', {'posts': results[:30]})

@socketio.on('search_messages')
def search_messages(data):
    chat = data.get('chat', '')
    query = data.get('query', '').lower()
    name = data.get('name', '')
    if name not in users:
        return
    msgs = []
    if chat in private_chats:
        msgs = private_chats[chat]['messages']
    elif chat in group_chats:
        msgs = group_chats[chat]['messages']
    else:
        return
    results = [m for m in msgs if query in m['content'].lower()]
    emit('search_results', {'messages': results[:50]})

@socketio.on('logout')
def logout(data):
    token = data.get('token', '')
    for name, user in users.items():
        if user.get('token') == token:
            user['token'] = ''
            user['status'] = 'offline'
            user['sid'] = ''
            user_status_history[name] = {'status': 'offline', 'time': time.time()}
            save_data()
            emit('user_status', {'name': name, 'status': 'offline', 'last_seen': time.time()}, broadcast=True)
            break

@socketio.on('get_users')
def get_users(data):
    name = data.get('name', '')
    user_list = []
    for n, u in users.items():
        if n != name and n not in blocked_users.get(name, []):
            user_list.append({'name': n, 'username': u.get('username', n), 'avatar': u.get('avatar'), 'status': u.get('status', 'offline'), 'bio': u.get('bio', ''), 'last_seen': u.get('last_seen', 0)})
    emit('users_list', {'users': user_list})

@socketio.on('start_private_chat')
def start_private_chat(data):
    user1 = data.get('user1', '')
    user2 = data.get('user2', '')
    if user1 not in users or user2 not in users or is_blocked(user1, user2):
        emit('error', {'message': 'Вы заблокированы'})
        return
    chat_id = f"p_{min(user1, user2)}_{max(user1, user2)}"
    if chat_id not in private_chats:
        private_chats[chat_id] = {'users': [user1, user2], 'messages': []}
        save_data()
    join_room(chat_id)
    if user1 in unread:
        unread[user1][chat_id] = 0
    msgs = private_chats[chat_id]['messages'][-200:]
    emit('private_chat', {'chat_id': chat_id, 'user': user2, 'avatar': users[user2].get('avatar'), 'messages': msgs, 'is_group': False})

@socketio.on('share_link')
def share_link():
    emit('share_link', {'url': request.host})


# ============================================================
#  HTML (ВЕСЬ КОД СТРАНИЦЫ — CSS, HTML, JAVASCRIPT)
# ============================================================
HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>DirectMe</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
:root {
    --bg: #0a0a0a; --bg-secondary: #141414; --bg-card: #1a1a1a; --bg-input: #242424; --bg-hover: #2a2a2a;
    --primary: #FFD700; --primary-dark: #B8960F; --primary-light: #FFE44D;
    --primary-gradient: linear-gradient(135deg, #FFD700, #FFA500);
    --text: #ffffff; --text-secondary: #8e8e93; --text-muted: #636366;
    --border: #2c2c2e; --shadow: rgba(255,215,0,0.15);
    --bubble-self: #FFD700; --bubble-other: #1c1c1e; --radius: 16px; --radius-sm: 10px;
}
body.light {
    --bg: #f2f2f7; --bg-secondary: #ffffff; --bg-card: #e5e5ea; --bg-input: #e5e5ea;
    --bg-hover: #d1d1d6; --text: #000000; --text-secondary: #3a3a3c;
    --text-muted: #8e8e93; --border: #c6c6c8; --bubble-other: #e5e5ea;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, system-ui, sans-serif; background: var(--bg); color: var(--text); height: 100vh; height: 100dvh; overflow: hidden; display: flex; justify-content: center; align-items: center; user-select: none; }
::-webkit-scrollbar { width: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--text-muted); border-radius: 3px; }
#app { width: 100%; max-width: 480px; height: 100vh; height: 100dvh; background: var(--bg); display: flex; flex-direction: column; position: relative; overflow: hidden; }
.header { background: var(--bg-secondary); padding: 8px 12px; display: flex; align-items: center; justify-content: space-between; border-bottom: 0.5px solid var(--border); flex-shrink: 0; min-height: 48px; z-index: 10; }
.header-left { display: flex; align-items: center; gap: 8px; }
.header-left .logo { font-size: 18px; font-weight: 700; background: var(--primary-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.header-left .logo-icon { width: 22px; height: 22px; fill: none; stroke: var(--primary); stroke-width: 2; }
.header-title { font-size: 16px; font-weight: 600; }
.header-right { display: flex; gap: 2px; align-items: center; }
.btn-icon { background: none; border: none; color: var(--text-secondary); padding: 4px; border-radius: 50%; cursor: pointer; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
.btn-icon:hover { background: var(--bg-hover); }
.btn-icon:active { transform: scale(0.92); }
.btn-icon svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.nav { background: var(--bg-secondary); display: flex; border-top: 0.5px solid var(--border); flex-shrink: 0; padding-bottom: env(safe-area-inset-bottom); }
.nav-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 0 4px; cursor: pointer; color: var(--text-secondary); font-size: 10px; transition: color 0.2s; position: relative; }
.nav-item.active { color: var(--primary); }
.nav-item svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.nav-item .label { font-size: 9px; font-weight: 500; }
.nav-item .badge { position: absolute; top: 2px; right: 50%; transform: translateX(180%); background: #ff3b30; color: #fff; font-size: 9px; font-weight: 700; min-width: 16px; height: 16px; border-radius: 8px; display: flex; align-items: center; justify-content: center; padding: 0 4px; border: 1.5px solid var(--bg-secondary); }
.page { flex: 1; overflow-y: auto; overflow-x: hidden; display: none; -webkit-overflow-scrolling: touch; padding-bottom: 4px; }
.page.active { display: block; }
.chat-item { display: flex; align-items: center; padding: 8px 12px; gap: 10px; cursor: pointer; transition: background 0.15s; border-bottom: 0.5px solid rgba(255,255,255,0.03); }
.chat-item:active { background: var(--bg-hover); }
.chat-avatar { width: 44px; height: 44px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; position: relative; }
.chat-avatar img { width: 100%; height: 100%; object-fit: cover; }
.chat-avatar .online-dot { position: absolute; bottom: 2px; right: 2px; width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--bg-secondary); background: #30d158; }
.chat-avatar .online-dot.offline { background: var(--text-muted); }
.chat-info { flex: 1; min-width: 0; }
.chat-name { font-size: 14px; font-weight: 500; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.chat-username { font-size: 11px; color: var(--text-secondary); }
.chat-last { font-size: 12px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chat-unread { background: var(--primary); color: #000; font-size: 10px; font-weight: 600; min-width: 18px; height: 18px; border-radius: 9px; display: flex; align-items: center; justify-content: center; padding: 0 5px; margin-left: auto; }
#chatWindow { display: none; flex: 1; flex-direction: column; min-height: 0; background: var(--bg); }
#chatWindow.open { display: flex; }
.messages-container { flex: 1; overflow-y: auto; padding: 8px 12px; -webkit-overflow-scrolling: touch; display: flex; flex-direction: column; gap: 2px; }
.msg { display: flex; gap: 6px; margin-bottom: 2px; max-width: 88%; animation: msgIn 0.2s ease; }
.msg.self { align-self: flex-end; flex-direction: row-reverse; }
@keyframes msgIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
.msg-avatar { width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; margin-top: auto; }
.msg-avatar img { width: 100%; height: 100%; object-fit: cover; }
.msg-bubble { padding: 6px 12px; border-radius: 14px; font-size: 13px; line-height: 1.4; word-wrap: break-word; background: var(--bubble-other); max-width: 100%; box-shadow: 0 1px 2px rgba(0,0,0,0.2); color: var(--text); }
.msg.self .msg-bubble { background: var(--bubble-self); color: #000; }
.msg-bubble img { max-width: 180px; border-radius: 8px; display: block; cursor: pointer; }
.msg-bubble video { max-width: 180px; border-radius: 8px; display: block; max-height: 300px; }
.msg-bubble audio { width: 150px; height: 30px; }
.msg-bubble .edited { font-size: 9px; color: var(--text-secondary); opacity: 0.6; margin-left: 4px; }
.msg.self .msg-bubble .edited { color: rgba(0,0,0,0.5); }
.msg-time { font-size: 9px; color: var(--text-secondary); text-align: right; margin-top: 2px; padding-right: 2px; }
.msg.self .msg-time { color: rgba(0,0,0,0.5); }
.msg-actions { display: flex; gap: 2px; margin-top: 2px; justify-content: flex-end; flex-wrap: wrap; }
.msg-actions button { background: none; border: none; color: var(--text-secondary); font-size: 10px; cursor: pointer; padding: 2px 6px; border-radius: 4px; transition: background 0.2s; }
.msg-actions button:hover { background: var(--bg-hover); }
.msg-reactions { display: flex; gap: 3px; flex-wrap: wrap; margin-top: 3px; }
.msg-reaction { cursor: pointer; background: var(--bg-input); padding: 1px 6px; border-radius: 10px; font-size: 12px; transition: 0.2s; }
.msg-reaction:hover { background: var(--bg-hover); transform: scale(1.1); }
.msg-reply-indicator { font-size: 11px; color: var(--text-secondary); padding: 2px 0 2px 12px; border-left: 2px solid var(--primary); margin-bottom: 2px; }
.typing-indicator { font-size: 11px; color: var(--text-secondary); padding: 2px 16px 6px; font-style: italic; min-height: 20px; opacity: 0; transition: opacity 0.3s; }
.typing-indicator.show { opacity: 1; }
.input-bar { display: flex; padding: 6px 10px 8px; background: var(--bg-secondary); border-top: 0.5px solid var(--border); gap: 6px; align-items: center; flex-shrink: 0; }
.input-bar input { flex: 1; padding: 6px 14px; background: var(--bg-input); border: none; border-radius: 18px; color: var(--text); font-size: 13px; outline: none; transition: all 0.2s; }
.input-bar input:focus { background: var(--bg-hover); }
.input-bar input::placeholder { color: var(--text-secondary); }
.input-bar .btn-icon { width: 30px; height: 30px; }
.send-btn { background: var(--primary-gradient); color: #000; border: none; width: 32px; height: 32px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s; flex-shrink: 0; }
.send-btn:active { transform: scale(0.9); opacity: 0.8; }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.send-btn svg { width: 18px; height: 18px; fill: none; stroke: #000; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.record-btn.recording { color: #ff3b30 !important; animation: pulse 0.8s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.post-card { background: var(--bg-secondary); margin: 6px 10px; border-radius: var(--radius); overflow: hidden; border: 0.5px solid var(--border); }
.post-header { display: flex; align-items: center; padding: 8px 12px; gap: 8px; }
.post-avatar { width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; cursor: pointer; }
.post-avatar img { width: 100%; height: 100%; object-fit: cover; }
.post-author { font-weight: 500; font-size: 13px; cursor: pointer; }
.post-author:hover { text-decoration: underline; }
.post-username { font-size: 10px; color: var(--text-secondary); margin-left: 4px; }
.post-time { font-size: 10px; color: var(--text-secondary); margin-left: auto; }
.post-media { width: 100%; max-height: 350px; object-fit: cover; cursor: pointer; background: var(--bg-input); }
.post-caption { padding: 6px 12px; font-size: 12px; line-height: 1.4; }
.post-actions { display: flex; padding: 4px 12px 8px; gap: 12px; border-top: 0.5px solid var(--border); flex-wrap: wrap; }
.post-action { background: none; border: none; color: var(--text-secondary); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 12px; padding: 2px 6px; border-radius: 6px; transition: all 0.2s; }
.post-action:hover { color: var(--text); }
.post-action:active { transform: scale(0.92); }
.post-action.liked { color: #ff3b30; }
.post-action.liked svg { fill: #ff3b30; stroke: #ff3b30; }
.post-action .count { font-size: 11px; }
.post-action svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.post-comments-wrap { max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }
.post-comments-wrap.open { max-height: 300px; }
.post-comments { padding: 0 12px 8px; max-height: 260px; overflow-y: auto; }
.post-comment { display: flex; gap: 6px; padding: 3px 0; font-size: 11px; border-bottom: 0.5px solid rgba(255,255,255,0.04); }
.post-comment:last-child { border-bottom: none; }
.post-comment-avatar { width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 8px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; }
.post-comment-avatar img { width: 100%; height: 100%; object-fit: cover; }
.post-comment-text { line-height: 1.3; }
.post-comment-text b { margin-right: 4px; }
.comment-input { display: flex; padding: 4px 12px 8px; gap: 6px; border-top: 0.5px solid var(--border); }
.comment-input input { flex: 1; background: var(--bg-input); border: none; border-radius: 10px; padding: 4px 10px; color: var(--text); font-size: 11px; outline: none; }
.comment-input input::placeholder { color: var(--text-secondary); }
.comment-input button { background: var(--primary-gradient); color: #000; border: none; padding: 4px 12px; border-radius: 10px; font-weight: 600; cursor: pointer; font-size: 11px; transition: opacity 0.2s; }
.comment-input button:active { opacity: 0.7; }
.profile-section { text-align: center; padding: 20px 16px; background: var(--bg-secondary); margin: 8px 10px; border-radius: var(--radius); }
.profile-avatar { width: 72px; height: 72px; border-radius: 50%; margin: 0 auto 8px; display: flex; align-items: center; justify-content: center; font-size: 28px; font-weight: 600; color: #fff; background: var(--primary-gradient); cursor: pointer; overflow: hidden; border: 3px solid var(--primary); transition: transform 0.2s; }
.profile-avatar:active { transform: scale(0.95); }
.profile-avatar img { width: 100%; height: 100%; object-fit: cover; }
.profile-name { font-size: 18px; font-weight: 600; }
.profile-username { font-size: 13px; color: var(--text-secondary); }
.profile-bio { color: var(--text-secondary); font-size: 12px; margin-top: 4px; }
.profile-status { font-size: 11px; margin-top: 2px; color: #30d158; }
.settings-group { padding: 0 10px 10px; }
.setting-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; background: var(--bg-secondary); margin-bottom: 4px; border-radius: var(--radius-sm); cursor: pointer; transition: background 0.15s; }
.setting-item:active { background: var(--bg-hover); }
.setting-label { font-size: 13px; }
#loginScreen { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: var(--bg); display: flex; align-items: center; justify-content: center; z-index: 100; }
.login-card { text-align: center; padding: 28px 20px; width: 90%; max-width: 340px; }
.login-logo { width: 48px; height: 48px; margin: 0 auto 10px; fill: none; stroke: var(--primary); stroke-width: 2; }
.login-card h1 { font-size: 24px; font-weight: 700; background: var(--primary-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.login-card p { color: var(--text-secondary); font-size: 12px; margin: 4px 0 16px; }
.form-input { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1.5px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-size: 13px; margin-bottom: 8px; outline: none; text-align: center; transition: border 0.3s; }
.form-input:focus { border-color: var(--primary); }
.form-btn { width: 100%; padding: 10px; background: var(--primary-gradient); color: #000; border: none; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
.form-btn:active { opacity: 0.8; }
.form-link { background: none; border: none; color: var(--primary); font-size: 12px; cursor: pointer; margin-top: 8px; }
.code-box { background: var(--bg-input); padding: 10px; border-radius: var(--radius-sm); font-size: 24px; letter-spacing: 8px; font-weight: 600; color: var(--primary); margin: 8px 0; font-family: monospace; }
.hidden { display: none !important; }
.media-viewer { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.95); z-index: 200; display: none; align-items: center; justify-content: center; padding: 20px; }
.media-viewer.open { display: flex; }
.media-viewer img { max-width: 100%; max-height: 80vh; object-fit: contain; }
.media-viewer video { max-width: 100%; max-height: 80vh; }
.media-close { position: absolute; top: 16px; right: 16px; width: 40px; height: 40px; border-radius: 50%; background: rgba(255,255,255,0.1); border: none; color: #fff; font-size: 20px; cursor: pointer; }
.push-notification { position: fixed; top: 12px; left: 50%; transform: translateX(-50%) translateY(-20px); background: var(--bg-secondary); padding: 12px 16px; border-radius: var(--radius); border-left: 4px solid var(--primary); box-shadow: 0 8px 32px rgba(0,0,0,0.6); z-index: 100; max-width: 92%; min-width: 260px; opacity: 0; pointer-events: none; transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1); }
.push-notification.show { opacity: 1; transform: translateX(-50%) translateY(0); pointer-events: auto; }
.push-notification .pn-header { display: flex; align-items: center; gap: 8px; }
.push-notification .pn-avatar { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: #fff; background: var(--primary-gradient); overflow: hidden; flex-shrink: 0; }
.push-notification .pn-avatar img { width: 100%; height: 100%; object-fit: cover; }
.push-notification .pn-name { font-weight: 600; font-size: 13px; }
.push-notification .pn-text { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
.push-notification .pn-close { background: none; border: none; color: var(--text-secondary); font-size: 16px; cursor: pointer; margin-left: auto; padding: 0 4px; }
.empty-state { text-align: center; padding: 40px 16px; color: var(--text-secondary); }
.empty-state .icon { width: 48px; height: 48px; margin: 0 auto 8px; fill: none; stroke: var(--text-muted); stroke-width: 1.5; }
.empty-state h3 { color: var(--text); margin-bottom: 4px; font-size: 15px; }
.empty-state p { font-size: 12px; }
.toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: var(--bg-secondary); padding: 8px 16px; border-radius: var(--radius-sm); font-size: 12px; z-index: 60; border-left: 3px solid var(--primary); box-shadow: 0 4px 20px rgba(0,0,0,0.6); animation: toastIn 0.3s ease; max-width: 90%; }
@keyframes toastIn { from { opacity: 0; transform: translateX(-50%) translateY(16px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
.stories-row { display: flex; gap: 10px; padding: 8px 12px; overflow-x: auto; scrollbar-width: none; }
.stories-row::-webkit-scrollbar { display: none; }
.story-circle { width: 56px; height: 56px; border-radius: 50%; flex-shrink: 0; padding: 2px; background: var(--primary-gradient); cursor: pointer; }
.story-circle-inner { width: 100%; height: 100%; border-radius: 50%; overflow: hidden; border: 2px solid var(--bg); }
.story-circle-inner img { width: 100%; height: 100%; object-fit: cover; }
.story-name { font-size: 10px; color: var(--text-secondary); text-align: center; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 56px; }
@media (max-width: 480px) { .msg { max-width: 92%; } .msg-bubble img, .msg-bubble video { max-width: 150px; } .push-notification { min-width: unset; width: 92%; } }
</style>
</head>
<body>
<div class="push-notification" id="pushNotification" onclick="openChatFromPush()">
    <div class="pn-header">
        <div class="pn-avatar" id="pnAvatar"></div>
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
            <svg class="logo-icon" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <span class="logo" id="headerTitle">DirectMe</span>
        </div>
        <div class="header-right">
            <button class="btn-icon" onclick="createPost()" id="headerFab">
                <svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
            <button class="btn-icon" onclick="shareApp()">
                <svg viewBox="0 0 24 24"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>
            </button>
        </div>
    </div>
    <div id="storiesRow" class="stories-row" style="display:none;"></div>
    <div class="page active" id="pageChats"><div id="chatList"></div></div>
    <div class="page" id="pageUsers">
        <div style="padding:6px 10px;position:sticky;top:0;background:var(--bg);z-index:5">
            <input class="form-input" id="searchUsers" placeholder="Поиск по имени или юзернейму..." oninput="searchUsers()" style="text-align:left;padding:8px 12px;font-size:12px;">
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
            <span style="font-weight:500;flex:1;font-size:15px;text-align:center" id="chatTitle">Чат</span>
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
            <button class="btn-icon record-btn" id="recordBtn" onclick="toggleRecording()" title="Голосовое сообщение">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
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
            <span class="label" id="navChats">Чаты</span>
            <span class="badge" id="totalBadge" style="display:none">0</span>
        </div>
        <div class="nav-item" onclick="switchPage('users')">
            <svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
            <span class="label" id="navUsers">Люди</span>
        </div>
        <div class="nav-item" onclick="switchPage('posts')">
            <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            <span class="label" id="navPosts">Посты</span>
        </div>
        <div class="nav-item" onclick="switchPage('settings')">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v4"/><path d="M12 19v4"/><path d="M4.22 4.22l2.83 2.83"/><path d="M16.95 16.95l2.83 2.83"/><path d="M1 12h4"/><path d="M19 12h4"/><path d="M4.22 19.78l2.83-2.83"/><path d="M16.95 7.05l2.83-2.83"/></svg>
            <span class="label" id="navSettings">Настройки</span>
        </div>
    </div>
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
            <svg class="login-logo" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <h1>DirectMe</h1>
            <p id="loginDesc">Введите юзернейм и пароль</p>
            <input class="form-input" id="regUsername" placeholder="Юзернейм (латиница, 3-20 символов)">
            <input class="form-input" id="regPassword" placeholder="Пароль (мин. 4)" type="password">
            <button class="form-btn" id="loginBtn" type="button">Войти / Зарегистрироваться</button>
        </div>
    </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.4/socket.io.min.js"></script>
<script>
// ============================================================
//  ПОЛНЫЙ JAVASCRIPT + ПЕРЕКЛЮЧАТЕЛЬ ЯЗЫКА
// ============================================================
const socket = io();
let currentUser = null, currentToken = null, currentChat = null, currentChatName = '';
let currentAvatar = null, currentBio = '', currentUsername = '', typingTimeout = null;
let isChatOpen = false, isRecording = false, mediaRecorder = null, audioChunks = [];
let unreadData = {}, privateChats = JSON.parse(localStorage.getItem('private_chats') || '[]');
let pushData = null, pushTimeout = null;

const $ = id => document.getElementById(id);
const chatList = $('chatList'), usersList = $('usersList'), postsList = $('postsList');
const settingsContent = $('settingsContent'), totalBadge = $('totalBadge');
const chatWindow = $('chatWindow'), messagesContainer = $('messagesContainer');
const chatTitle = $('chatTitle'), msgInput = $('msgInput'), typingIndicator = $('typingIndicator');
const storiesRow = $('storiesRow');

// ============================================================
//  ПЕРЕКЛЮЧАТЕЛЬ ЯЗЫКА
// ============================================================
let currentLang = localStorage.getItem('directme_lang') || 'ru';

const translations = {
    ru: {
        appName: 'DirectMe',
        chats: 'Чаты',
        users: 'Люди',
        posts: 'Посты',
        settings: 'Настройки',
        loginDesc: 'Введите юзернейм и пароль',
        loginBtn: 'Войти / Зарегистрироваться',
        search: 'Поиск по имени или юзернейму...',
        noChats: 'Нет чатов',
        noChatsDesc: 'Найдите людей в разделе "Люди"',
        noUsers: 'Нет пользователей',
        noPosts: 'Нет постов',
        noPostsDesc: 'Создайте первый пост!',
        profile: 'Профиль',
        editBio: 'Редактировать описание',
        editProfile: 'Редактировать профиль',
        share: 'Поделиться',
        logout: 'Выйти',
        theme: 'Тема',
        dark: 'Темная',
        light: 'Светлая',
        language: 'Язык',
        online: 'В сети',
        offline: 'Не в сети',
        noDescription: 'Нет описания',
        writeFirst: 'Напишите первым...',
        message: 'Сообщение...',
        reply: 'Ответить',
        forward: 'Переслать',
        delete: 'Удалить',
        edit: 'Редактировать',
        pin: 'Закрепить',
        pinned: 'Закреплено',
        voice: 'Голосовое сообщение',
        recording: 'Запись...',
        send: 'Отправить',
        addPost: 'Добавить пост',
        caption: 'Описание:',
        postPublished: 'Пост опубликован!',
        comment: 'Написать комментарий...',
        save: 'Сохранить',
        repost: 'Репостнуть',
        block: 'Заблокировать',
        unblock: 'Разблокировать',
        report: 'Пожаловаться',
        copyLink: 'Ссылка скопирована!',
        error: 'Ошибка',
        success: 'Успех',
        username: 'Юзернейм',
        password: 'Пароль',
        usernamePlaceholder: 'Юзернейм (латиница, 3-20 символов)',
        passwordPlaceholder: 'Пароль (мин. 4)',
    },
    en: {
        appName: 'DirectMe',
        chats: 'Chats',
        users: 'People',
        posts: 'Posts',
        settings: 'Settings',
        loginDesc: 'Enter username and password',
        loginBtn: 'Login / Register',
        search: 'Search by name or username...',
        noChats: 'No chats',
        noChatsDesc: 'Find people in the "People" section',
        noUsers: 'No users',
        noPosts: 'No posts',
        noPostsDesc: 'Create your first post!',
        profile: 'Profile',
        editBio: 'Edit bio',
        editProfile: 'Edit profile',
        share: 'Share',
        logout: 'Logout',
        theme: 'Theme',
        dark: 'Dark',
        light: 'Light',
        language: 'Language',
        online: 'Online',
        offline: 'Offline',
        noDescription: 'No description',
        writeFirst: 'Write first...',
        message: 'Message...',
        reply: 'Reply',
        forward: 'Forward',
        delete: 'Delete',
        edit: 'Edit',
        pin: 'Pin',
        pinned: 'Pinned',
        voice: 'Voice message',
        recording: 'Recording...',
        send: 'Send',
        addPost: 'Add post',
        caption: 'Caption:',
        postPublished: 'Post published!',
        comment: 'Write a comment...',
        save: 'Save',
        repost: 'Repost',
        block: 'Block',
        unblock: 'Unblock',
        report: 'Report',
        copyLink: 'Link copied!',
        error: 'Error',
        success: 'Success',
        username: 'Username',
        password: 'Password',
        usernamePlaceholder: 'Username (latin, 3-20 chars)',
        passwordPlaceholder: 'Password (min 4)',
    }
};

function t(key) {
    return translations[currentLang][key] || key;
}

function setLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('directme_lang', lang);
    updateLanguageUI();
}

function updateLanguageUI() {
    document.getElementById('navChats').textContent = t('chats');
    document.getElementById('navUsers').textContent = t('users');
    document.getElementById('navPosts').textContent = t('posts');
    document.getElementById('navSettings').textContent = t('settings');
    document.getElementById('loginDesc').textContent = t('loginDesc');
    document.getElementById('loginBtn').textContent = t('loginBtn');
    document.getElementById('searchUsers').placeholder = t('search');
    document.getElementById('msgInput').placeholder = t('message');
    document.getElementById('headerTitle').textContent = t('appName');
    
    // Если есть открытый чат
    if (isChatOpen && currentChatName) {
        chatTitle.textContent = currentChatName;
    }
    
    // Обновляем настройки, если они открыты
    if (document.getElementById('pageSettings').classList.contains('active')) {
        renderSettings();
    }
    
    // Обновляем пустые состояния
    updateEmptyStates();
}

function updateEmptyStates() {
    if (chatList && chatList.innerHTML.includes('Нет чатов') || chatList.innerHTML.includes('No chats')) {
        chatList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><h3>${t('noChats')}</h3><p>${t('noChatsDesc')}</p></div>`;
    }
    if (usersList && usersList.innerHTML.includes('Нет пользователей') || usersList.innerHTML.includes('No users')) {
        usersList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg><h3>${t('noUsers')}</h3></div>`;
    }
    if (postsList && postsList.innerHTML.includes('Нет постов') || postsList.innerHTML.includes('No posts')) {
        postsList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg><h3>${t('noPosts')}</h3><p>${t('noPostsDesc')}</p></div>`;
    }
}

// ============================================================
//  ОСТАЛЬНЫЕ ФУНКЦИИ
// ============================================================
function toggleTheme() {
    document.body.classList.toggle('light');
    const isLight = document.body.classList.contains('light');
    const themeLabel = document.getElementById('themeLabel');
    if (themeLabel) themeLabel.textContent = isLight ? t('light') : t('dark');
    localStorage.setItem('directme_theme', isLight ? 'light' : 'dark');
}

function loadTheme() {
    if (localStorage.getItem('directme_theme') === 'light') {
        document.body.classList.add('light');
        const themeLabel = document.getElementById('themeLabel');
        if (themeLabel) themeLabel.textContent = t('light');
    }
}

function showPush(from, content, chatId) {
    const el = $('pushNotification'), avatar = $('pnAvatar'), name = $('pnName'), text = $('pnText');
    const user = Object.values(users).find(u => u.name === from);
    avatar.innerHTML = (user && user.avatar) ? `<img src="${user.avatar}">` : from[0];
    name.textContent = from;
    text.textContent = content;
    pushData = { chatId, from };
    el.classList.add('show');
    clearTimeout(pushTimeout);
    pushTimeout = setTimeout(closePush, 5000);
}

function closePush() { const el = $('pushNotification'); if (el) el.classList.remove('show'); pushData = null; }
function openChatFromPush() { if (pushData) { closePush(); openPrivateChat(pushData.chatId, pushData.from); } }

// ===== ВХОД / РЕГИСТРАЦИЯ (ГАРАНТИРОВАННО РАБОТАЕТ) =====
function doLogin() {
    console.log('🔵 Кнопка нажата!');
    var username = document.getElementById('regUsername').value.trim().toLowerCase();
    var password = document.getElementById('regPassword').value.trim();

    if (!username || username.length < 3 || username.length > 20) {
        showToast(t('username') + ' 3-20 ' + (currentLang === 'ru' ? 'символов' : 'chars'));
        return;
    }
    if (!/^[a-zA-Z0-9_]+$/.test(username)) {
        showToast(t('username') + ': ' + (currentLang === 'ru' ? 'только латиница, цифры, _' : 'latin, numbers, _'));
        return;
    }
    if (password.length < 4) {
        showToast(t('password') + ' ' + (currentLang === 'ru' ? 'минимум 4 символа' : 'min 4 chars'));
        return;
    }

    socket.emit('login', { username: username, password: password });
    
    var answered = false;
    socket.once('login_success', function(data) {
        answered = true;
        console.log('✅ Вход выполнен!');
        currentUser = data.name;
        currentToken = data.token;
        currentAvatar = data.avatar;
        currentUsername = data.username || data.name;
        currentBio = data.bio || '';
        localStorage.setItem('directme_token', data.token);
        localStorage.setItem('directme_user', data.name);
        enterApp();
    });
    
    socket.once('error', function(data) {
        answered = true;
        console.log('❌ Ошибка:', data.message);
        if (data.message === 'Пользователь не найден' || data.message === 'User not found') {
            if (confirm(currentLang === 'ru' ? 'Пользователь не найден. Создать нового?' : 'User not found. Create new?')) {
                socket.emit('register', { username: username, password: password });
                socket.once('login_success', function(data2) {
                    console.log('✅ Аккаунт создан!');
                    currentUser = data2.name;
                    currentToken = data2.token;
                    currentAvatar = data2.avatar;
                    currentUsername = data2.username || data2.name;
                    currentBio = data2.bio || '';
                    localStorage.setItem('directme_token', data2.token);
                    localStorage.setItem('directme_user', data2.name);
                    enterApp();
                });
            }
        } else {
            showToast(t('error') + ': ' + data.message);
        }
    });
    
    setTimeout(function() {
        if (!answered) {
            showToast('⏰ ' + (currentLang === 'ru' ? 'Сервер не отвечает. Попробуйте позже.' : 'Server not responding. Try later.'));
        }
    }, 3000);
}

// ===== ЗАГРУЗКА КНОПКИ =====
document.addEventListener('DOMContentLoaded', function() {
    console.log('📄 Страница загружена!');
    
    // Загружаем язык
    currentLang = localStorage.getItem('directme_lang') || 'ru';
    updateLanguageUI();
    loadTheme();
    
    var btn = document.getElementById('loginBtn');
    if (btn) {
        console.log('✅ Кнопка найдена!');
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            doLogin();
        });
        console.log('✅ Кнопка привязана!');
    } else {
        console.log('❌ Кнопка НЕ найдена!');
    }
});

function showToast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
}

// ===== SOCKET EVENTS =====
socket.on('login_success', function(data) {
    console.log('✅ Вход выполнен (socket)!');
    currentUser = data.name;
    currentToken = data.token;
    currentAvatar = data.avatar;
    currentUsername = data.username || data.name;
    currentBio = data.bio || '';
    localStorage.setItem('directme_token', data.token);
    localStorage.setItem('directme_user', data.name);
    enterApp();
});

socket.on('error', function(data) {
    showToast(t('error') + ': ' + data.message);
});

socket.on('push_notification', (data) => {
    showPush(data.from, data.content, data.chat_id);
    if ($('pageUsers').classList.contains('active')) switchPage('chats');
});

socket.on('new_message', (data) => {
    if (data.chat === currentChat && isChatOpen) { renderMessage(data.message); scrollToBottom(); }
    if (data.chat !== currentChat || !isChatOpen) { unreadData[data.chat] = (unreadData[data.chat] || 0) + 1; updateBadge(); }
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
socket.on('private_chat', (data) => { openPrivateChat(data.chat_id, data.user, data.avatar, data.messages); });

socket.on('avatar_updated', (data) => {
    if (data.name === currentUser) currentAvatar = data.avatar;
    renderChats(); renderUsers();
});

socket.on('bio_updated', (data) => {
    if (data.name === currentUser) { currentBio = data.bio; renderSettings(); }
});

socket.on('new_post', (data) => {
    if ($('pagePosts').classList.contains('active')) {
        postsList.insertAdjacentHTML('afterbegin', renderPost(data.post));
    }
});

socket.on('posts_list', (data) => {
    if (!data.posts || !data.posts.length) {
        postsList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg><h3>${t('noPosts')}</h3><p>${t('noPostsDesc')}</p></div>`;
        return;
    }
    postsList.innerHTML = data.posts.map(p => renderPost(p)).join('');
});

socket.on('post_updated', (data) => {
    const el = document.getElementById('post-' + data.post.id);
    if (el) el.outerHTML = renderPost(data.post);
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
            if (bubble) bubble.innerHTML = data.message.content + '<span class="edited">✎</span>';
        }
    }
});

socket.on('share_link', (data) => {
    const url = 'https://' + data.url;
    if (navigator.clipboard) { navigator.clipboard.writeText(url).then(() => showToast(t('copyLink'))); }
    else { prompt('Link:', url); }
});

socket.on('reaction_updated', (data) => {
    if (data.chat === currentChat) {
        const el = document.querySelector(`[data-msg-id="${data.msg_id}"]`);
        if (el) {
            let reactionsHtml = '';
            if (data.reactions && Object.keys(data.reactions).length > 0) {
                const counts = {};
                for (const [user, emoji] of Object.entries(data.reactions)) {
                    counts[emoji] = (counts[emoji] || 0) + 1;
                }
                reactionsHtml = '<div class="msg-reactions">';
                for (const [emoji, count] of Object.entries(counts)) {
                    reactionsHtml += `<span class="msg-reaction" onclick="toggleReaction('${data.msg_id}','${emoji}')">${emoji} ${count}</span>`;
                }
                reactionsHtml += '</div>';
            }
            const existing = el.querySelector('.msg-reactions');
            if (existing) existing.remove();
            if (reactionsHtml) {
                const bubble = el.querySelector('.msg-bubble');
                if (bubble) bubble.insertAdjacentHTML('afterend', reactionsHtml);
            }
        }
    }
});

// ============================================================
//  ВХОД В ПРИЛОЖЕНИЕ
// ============================================================
function enterApp() {
    $('loginScreen').classList.add('hidden');
    $('nav').style.display = 'flex';
    loadTheme();
    renderChats();
    renderUsers();
    renderSettings();
    socket.emit('get_posts');
    setInterval(() => socket.emit('get_users', { name: currentUser }), 30000);
    setInterval(() => socket.emit('get_posts'), 60000);
}

function switchPage(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (page === 'chats') {
        $('pageChats').classList.add('active');
        document.querySelector('.nav-item:nth-child(1)').classList.add('active');
        renderChats();
        $('headerFab').style.display = 'none';
        if (storiesRow) storiesRow.style.display = 'flex';
    } else if (page === 'users') {
        $('pageUsers').classList.add('active');
        document.querySelector('.nav-item:nth-child(2)').classList.add('active');
        socket.emit('get_users', { name: currentUser });
        $('headerFab').style.display = 'none';
        if (storiesRow) storiesRow.style.display = 'none';
    } else if (page === 'posts') {
        $('pagePosts').classList.add('active');
        document.querySelector('.nav-item:nth-child(3)').classList.add('active');
        socket.emit('get_posts');
        $('headerFab').style.display = 'flex';
        if (storiesRow) storiesRow.style.display = 'none';
    } else {
        $('pageSettings').classList.add('active');
        document.querySelector('.nav-item:nth-child(4)').classList.add('active');
        renderSettings();
        $('headerFab').style.display = 'none';
        if (storiesRow) storiesRow.style.display = 'none';
    }
    if (isChatOpen) { chatWindow.classList.remove('open'); chatWindow.style.display = 'none'; isChatOpen = false; }
}

function renderChats() {
    if (!privateChats.length) {
        chatList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><h3>${t('noChats')}</h3><p>${t('noChatsDesc')}</p></div>`;
        return;
    }
    let html = '';
    privateChats.forEach(c => {
        const ur = unreadData[c.id] || 0;
        const lastMsg = c.lastMsg || t('writeFirst');
        const username = users[c.name]?.username || c.name;
        html += `
            <div class="chat-item" onclick="openPrivateChat('${c.id}', '${c.name}')">
                <div class="chat-avatar">
                    ${c.avatar ? `<img src="${c.avatar}">` : c.name[0]}
                    <span class="online-dot ${c.status === 'online' ? '' : 'offline'}"></span>
                </div>
                <div class="chat-info">
                    <div class="chat-name">${c.name} <span class="chat-username">@${username}</span></div>
                    <div class="chat-last">${lastMsg}</div>
                </div>
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
        usersList.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg><h3>${t('noUsers')}</h3></div>`;
        return;
    }
    usersList.innerHTML = users.map(u => `
        <div class="chat-item" onclick="viewProfile('${u.name}')">
            <div class="chat-avatar">
                ${u.avatar ? `<img src="${u.avatar}">` : u.name[0]}
                <span class="online-dot ${u.status === 'online' ? '' : 'offline'}"></span>
            </div>
            <div class="chat-info">
                <div class="chat-name">${u.name} <span class="chat-username">@${u.username}</span></div>
                <div class="chat-last">${u.bio || t('noDescription')}</div>
            </div>
            <button onclick="event.stopPropagation();startPrivateChat('${u.name}')" class="btn-icon" style="color:var(--primary)">
                <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            </button>
        </div>
    `).join('');
}

function searchUsers() {
    const query = $('searchUsers').value.toLowerCase().trim();
    document.querySelectorAll('#usersList .chat-item').forEach(el => {
        const name = el.querySelector('.chat-name')?.textContent?.toLowerCase() || '';
        const username = el.querySelector('.chat-username')?.textContent?.toLowerCase() || '';
        const bio = el.querySelector('.chat-last')?.textContent?.toLowerCase() || '';
        const match = name.includes(query) || username.includes(query) || bio.includes(query);
        el.style.display = match || !query ? 'flex' : 'none';
    });
}

function viewProfile(name) {
    if (name === currentUser) { switchPage('settings'); return; }
    const user = Object.values(users).find(u => u.name === name);
    if (!user) return;
    socket.emit('get_posts');
    const userPosts = Object.values(posts).filter(p => p.author === name);
    const html = `
        <div style="padding:12px;">
            <div class="profile-section">
                <div class="profile-avatar" style="cursor:default">${user.avatar ? `<img src="${user.avatar}">` : name[0]}</div>
                <div class="profile-name">${name}</div>
                <div class="profile-username">@${user.username || name}</div>
                <div class="profile-bio">${user.bio || t('noDescription')}</div>
                <div class="profile-status">${user.status === 'online' ? '🟢 ' + t('online') : '⚪ ' + t('offline')}</div>
                <button class="form-btn" style="margin-top:12px;width:auto;padding:8px 24px;" onclick="startPrivateChat('${name}')">${t('message')}</button>
                <button class="form-link" onclick="closeProfile()">← ${t('settings')}</button>
            </div>
            <div style="margin-top:8px;">
                <h3 style="font-size:14px;margin-bottom:4px;">📸 ${t('posts')} (${userPosts.length})</h3>
                ${userPosts.length ? userPosts.map(p => renderPost(p)).join('') : '<div style="color:var(--text-secondary);font-size:12px;">' + t('noPosts') + '</div>'}
            </div>
        </div>
    `;
    $('pageChats').innerHTML = html;
    $('pageChats').classList.add('active');
    document.querySelectorAll('.page').forEach(p => { if(p.id !== 'pageChats') p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector('.nav-item:nth-child(1)').classList.add('active');
    $('headerFab').style.display = 'none';
    if (storiesRow) storiesRow.style.display = 'none';
}

function closeProfile() {
    $('pageChats').innerHTML = '<div id="chatList"></div>';
    renderChats();
    $('pageChats').classList.add('active');
    if (storiesRow) storiesRow.style.display = 'flex';
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
    if (storiesRow) storiesRow.style.display = 'none';
}

function closeChat() {
    chatWindow.classList.remove('open');
    chatWindow.style.display = 'none';
    isChatOpen = false;
    $('pageChats').classList.add('active');
    document.querySelector('.nav-item:nth-child(1)').classList.add('active');
    renderChats();
    if (storiesRow) storiesRow.style.display = 'flex';
}

function deleteChat() {
    if (!confirm(t('delete') + '?')) return;
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
    typingTimeout = setTimeout(() => { socket.emit('typing', { chat: currentChat, name: currentUser, typing: false }); }, 1500);
}

function renderMessage(msg) {
    const isSelf = msg.name === currentUser;
    const div = document.createElement('div');
    div.className = 'msg' + (isSelf ? ' self' : '');
    div.dataset.msgId = msg.id;
    let content = msg.content;
    if (msg.type === 'image') content = `<img src="${msg.content}" onclick="openMedia('${msg.content}','image')">`;
    else if (msg.type === 'video') content = `<video src="${msg.content}" controls></video>`;
    else if (msg.type === 'voice') content = `<audio src="${msg.content}" controls></audio>`;
    else {
        content = msg.content.replace(/</g,'&lt;').replace(/>/g,'&gt;');
        content = content.replace(/#(\w+)/g, '<span style="color:var(--primary-light);cursor:pointer;" onclick="searchHashtag(\'$1\')">#$1</span>');
        content = content.replace(/@(\w+)/g, '<span style="color:var(--primary);cursor:pointer;" onclick="viewProfile(\'$1\')">@$1</span>');
    }
    const avatar = msg.avatar ? `<img src="${msg.avatar}">` : msg.name[0];
    const actions = isSelf ? `
        <div class="msg-actions">
            <button onclick="editMessage('${msg.id}')">✎</button>
            <button onclick="deleteMessage('${msg.id}')">✕</button>
            <button onclick="pinMessage('${msg.id}')">📌</button>
        </div>
    ` : `
        <div class="msg-actions">
            <button onclick="replyToMessage('${msg.id}','${msg.name}','${msg.content.replace(/'/g, "\\'")}')">↩</button>
            <button onclick="forwardMessage('${msg.id}')">➡</button>
        </div>
    `;
    let replyHtml = '';
    if (msg.reply_to) {
        replyHtml = `<div class="msg-reply-indicator">↳ ${msg.reply_to.name}: ${msg.reply_to.content}</div>`;
    }
    let reactionsHtml = '';
    if (msg.reactions && Object.keys(msg.reactions).length > 0) {
        const counts = {};
        for (const [user, emoji] of Object.entries(msg.reactions)) {
            counts[emoji] = (counts[emoji] || 0) + 1;
        }
        reactionsHtml = '<div class="msg-reactions">';
        for (const [emoji, count] of Object.entries(counts)) {
            reactionsHtml += `<span class="msg-reaction" onclick="toggleReaction('${msg.id}','${emoji}')">${emoji} ${count}</span>`;
        }
        reactionsHtml += '</div>';
    }
    const reactionBtns = ['❤️', '🔥', '👍', '😂', '😮'].map(e =>
        `<span onclick="addReaction('${msg.id}','${e}')" style="cursor:pointer;padding:0 3px;font-size:13px;">${e}</span>`
    ).join('');
    div.innerHTML = `
        <div class="msg-avatar">${avatar}</div>
        <div>
            ${replyHtml}
            <div class="msg-bubble">${content}${msg.edited ? '<span class="edited">✎</span>' : ''}</div>
            <div style="display:flex;gap:4px;margin-top:2px;flex-wrap:wrap;">${reactionBtns}</div>
            ${reactionsHtml}
            <div class="msg-time">${msg.time}</div>
            ${actions}
        </div>
    `;
    messagesContainer.appendChild(div);
}

function deleteMessage(msgId) {
    if (!confirm(t('delete') + '?')) return;
    socket.emit('delete_message', { chat: currentChat, msg_id: msgId, name: currentUser });
}

function editMessage(msgId) {
    const newText = prompt(t('edit') + ':');
    if (newText?.trim()) {
        socket.emit('edit_message', { chat: currentChat, msg_id: msgId, name: currentUser, content: newText.trim() });
    }
}

function pinMessage(msgId) {
    socket.emit('pin_message', { chat: currentChat, msg_id: msgId, name: currentUser });
}

function replyToMessage(msgId, name, content) {
    const replyText = prompt(t('reply') + ' ' + name + ': ' + content);
    if (replyText?.trim()) {
        socket.emit('reply_message', { chat: currentChat, msg_id: msgId, name: currentUser, reply: replyText.trim() });
    }
}

function forwardMessage(msgId) {
    const target = prompt(t('forward') + ':');
    if (target && target.trim() && target !== currentUser) {
        socket.emit('forward_message', { chat: currentChat, msg_id: msgId, from: currentUser, to: target.trim() });
    }
}

function addReaction(msgId, reaction) {
    socket.emit('message_reaction', { chat: currentChat, msg_id: msgId, name: currentUser, reaction });
}

function toggleReaction(msgId, reaction) {
    socket.emit('message_reaction', { chat: currentChat, msg_id: msgId, name: currentUser, reaction });
}

function scrollToBottom() { setTimeout(() => messagesContainer.scrollTop = messagesContainer.scrollHeight, 50); }

function updateBadge() {
    const total = Object.values(unreadData).reduce((a,b) => a + b, 0);
    if (total > 0) { totalBadge.textContent = total; totalBadge.style.display = 'flex'; }
    else { totalBadge.style.display = 'none'; }
}

function toggleRecording() {
    if (isRecording) {
        if (mediaRecorder) mediaRecorder.stop();
        isRecording = false;
        $('recordBtn').classList.remove('recording');
        return;
    }
    if (!currentChat) { showToast(t('error')); return; }
    navigator.mediaDevices.getUserMedia({ audio: true })
        .then(stream => {
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
            mediaRecorder.onstop = () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                const reader = new FileReader();
                reader.onload = () => {
                    socket.emit('send_message', { name: currentUser, chat: currentChat, type: 'voice', content: reader.result });
                };
                reader.readAsDataURL(audioBlob);
                audioChunks = [];
                stream.getTracks().forEach(t => t.stop());
            };
            mediaRecorder.start();
            isRecording = true;
            $('recordBtn').classList.add('recording');
            showToast('⏺ ' + t('recording') + ' 30 ' + (currentLang === 'ru' ? 'сек' : 'sec'));
            setTimeout(() => {
                if (isRecording && mediaRecorder) {
                    mediaRecorder.stop();
                    isRecording = false;
                    $('recordBtn').classList.remove('recording');
                }
            }, 30000);
        })
        .catch(() => showToast(t('error')));
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

function createPost() { $('postInput').click(); }

function handlePost(e) {
    const file = e.target.files[0];
    if (!file) return;
    const caption = prompt(t('caption')) || '';
    const reader = new FileReader();
    reader.onload = (ev) => {
        socket.emit('create_post', {
            name: currentUser,
            content: ev.target.result,
            media_type: file.type.startsWith('video') ? 'video' : 'image',
            caption: caption
        });
        showToast(t('postPublished'));
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function renderPost(p) {
    const isLiked = p.likes?.includes(currentUser);
    const isSaved = p.saved_by?.includes(currentUser);
    const isReposted = p.reposts?.includes(currentUser);
    const isAuthor = p.author === currentUser;
    const avatar = p.avatar ? `<img src="${p.avatar}">` : p.author[0];
    const comments = p.comments || [];
    const hasComments = comments.length > 0;
    const username = users[p.author]?.username || p.author;
    return `
        <div class="post-card" id="post-${p.id}">
            <div class="post-header">
                <div class="post-avatar" onclick="viewProfile('${p.author}')">${avatar}</div>
                <div>
                    <div class="post-author" onclick="viewProfile('${p.author}')">${p.author} <span class="post-username">@${username}</span></div>
                </div>
                <div class="post-time">${p.time}</div>
                ${isAuthor ? `<button class="btn-icon" onclick="deletePost('${p.id}')" style="margin-left:auto;color:#ff3b30"><svg viewBox="0 0 24 24" width="16" height="16"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>` : ''}
            </div>
            ${p.media_type === 'image' ? `<img class="post-media" src="${p.content}" onclick="openMedia('${p.content}','image')">` : ''}
            ${p.media_type === 'video' ? `<video class="post-media" src="${p.content}" controls></video>` : ''}
            <div class="post-caption">${p.caption ? p.caption.replace(/#(\w+)/g, '<span style="color:var(--primary-light);cursor:pointer;" onclick="searchHashtag(\'$1\')">#$1</span>') : ''}</div>
            <div class="post-actions">
                <button class="post-action ${isLiked ? 'liked' : ''}" onclick="likePost('${p.id}')">
                    <svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                    <span class="count">${(p.likes || []).length}</span>
                </button>
                <button class="post-action" onclick="toggleComments('${p.id}')">
                    <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <span class="count">${comments.length}</span>
                </button>
                <button class="post-action ${isSaved ? 'liked' : ''}" onclick="savePost('${p.id}')" style="color:${isSaved ? 'var(--primary)' : ''}">
                    <svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
                </button>
                <button class="post-action ${isReposted ? 'liked' : ''}" onclick="repostPost('${p.id}')" style="color:${isReposted ? 'var(--primary)' : ''}">
                    <svg viewBox="0 0 24 24"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
                    <span class="count">${(p.reposts || []).length}</span>
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
                <input id="comment-${p.id}" placeholder="${t('comment')}" onkeypress="if(event.key==='Enter')sendComment('${p.id}')">
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
function savePost(postId) { socket.emit('save_post', { post_id: postId, name: currentUser }); }
function repostPost(postId) { socket.emit('repost_post', { post_id: postId, name: currentUser }); }

function sendComment(postId) {
    const input = document.getElementById('comment-' + postId);
    const text = input.value.trim();
    if (!text) return;
    socket.emit('comment_post', { post_id: postId, name: currentUser, comment: text });
    input.value = '';
}

function deletePost(postId) {
    if (!confirm(t('delete') + '?')) return;
    fetch('/delete_post', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid: postId, n: currentUser }) });
    setTimeout(() => socket.emit('get_posts'), 500);
}

function searchHashtag(tag) {
    socket.emit('search_hashtag', { tag });
    switchPage('posts');
    socket.once('search_results', (data) => {
        if (data.posts && data.posts.length) {
            postsList.innerHTML = data.posts.map(p => renderPost(p)).join('');
        } else {
            postsList.innerHTML = `<div class="empty-state"><h3>#${tag}</h3><p>${t('noPosts')}</p></div>`;
        }
    });
}

function renderSettings() {
    const avatar = currentAvatar ? `<img src="${currentAvatar}">` : (currentUser ? currentUser[0] : '?');
    settingsContent.innerHTML = `
        <div class="profile-section">
            <div class="profile-avatar" onclick="$('avatarInput').click()">${avatar}</div>
            <div class="profile-name">${currentUser || 'Гость'}</div>
            <div class="profile-username">@${currentUsername || currentUser}</div>
            <div class="profile-bio">${currentBio || t('noDescription')}</div>
            <div class="profile-status">🟢 ${t('online')}</div>
        </div>
        <div class="settings-group">
            <div class="setting-item" onclick="toggleTheme()"><span class="setting-label">🌓 ${t('theme')}: <span id="themeLabel">${document.body.classList.contains('light') ? t('light') : t('dark')}</span></span></div>
            <div class="setting-item" onclick="toggleLanguage()"><span class="setting-label">🌐 ${t('language')}: <span id="langLabel">${currentLang === 'ru' ? 'Русский' : 'English'}</span></span></div>
            <div class="setting-item" onclick="editBio()"><span class="setting-label">✏️ ${t('editBio')}</span></div>
            <div class="setting-item" onclick="editProfile()"><span class="setting-label">✏️ ${t('editProfile')}</span></div>
            <div class="setting-item" onclick="shareApp()"><span class="setting-label">🔗 ${t('share')}</span></div>
            <div class="setting-item" onclick="logout()" style="border-left:3px solid #ff3b30"><span class="setting-label" style="color:#ff3b30">🚪 ${t('logout')}</span></div>
        </div>
    `;
}

function toggleLanguage() {
    const newLang = currentLang === 'ru' ? 'en' : 'ru';
    setLanguage(newLang);
    document.getElementById('langLabel').textContent = newLang === 'ru' ? 'Русский' : 'English';
    renderSettings();
}

function editBio() {
    const bio = prompt(t('editBio') + ':', currentBio || '');
    if (bio !== null) { currentBio = bio; socket.emit('update_bio', { name: currentUser, bio }); renderSettings(); }
}

function editProfile() {
    const newName = prompt(t('editProfile') + ' (' + t('username') + '):', currentUser);
    if (newName && newName.trim() && newName !== currentUser) {
        socket.emit('update_profile', { name: currentUser, new_name: newName.trim() });
    }
    const newUsername = prompt(t('editProfile') + ' (@' + t('username') + '):', currentUsername);
    if (newUsername && newUsername.trim() && newUsername !== currentUsername) {
        socket.emit('update_profile', { name: currentUser, new_username: newUsername.trim().toLowerCase() });
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
        showToast(t('success'));
    };
    reader.readAsDataURL(file);
    e.target.value = '';
}

function logout() {
    if (!confirm(t('logout') + '?')) return;
    socket.emit('logout', { token: currentToken });
    localStorage.removeItem('directme_token');
    localStorage.removeItem('directme_user');
    location.reload();
}

function shareApp() { socket.emit('share_link'); }

function openMedia(src, type) {
    const viewer = $('mediaViewer');
    viewer.classList.add('open');
    if (type === 'image') {
        $('mediaImg').src = src;
        $('mediaImg').style.display = 'block';
        $('mediaVideo').style.display = 'none';
        $('mediaVideo').pause();
    } else {
        $('mediaVideo').src = src;
        $('mediaVideo').style.display = 'block';
        $('mediaImg').style.display = 'none';
        $('mediaVideo').play();
    }
}

function closeMedia() {
    $('mediaViewer').classList.remove('open');
    $('mediaVideo').pause();
}

const savedToken = localStorage.getItem('directme_token');
const savedUser = localStorage.getItem('directme_user');
if (savedToken && savedUser) { socket.emit('auto_login', { token: savedToken }); }

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if ($('mediaViewer').classList.contains('open')) closeMedia();
        else if (isChatOpen) closeChat();
    }
});

console.log('💬 DirectMe загружен!');
</script>
</body>
</html>
'''


# ============================================================
#  ЗАПУСК
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
