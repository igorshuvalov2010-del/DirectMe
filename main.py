from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os, hashlib

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'shugramm-' + str(random.randint(10000,99999)))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

users = {}
posts = []
groups = {'general': {'id': 'general', 'name': 'Общий чат', 'members': set(), 'messages': []}}
private_chats = {}
pending = {}
colors = ['#FFD700', '#FFA500', '#FF8C00', '#FFB800', '#FFC800', '#E6C200']

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]

@app.route('/')
def index():
    token = request.cookies.get('shugramm_token')
    if token:
        for uname, udata in users.items():
            if udata.get('token') == token:
                return render_template_string(HTML.replace('AUTO_LOGIN_USER', uname).replace('AUTO_LOGIN_AVATAR', udata.get('au') or udata['a']).replace('AUTO_LOGIN_TOKEN', token))
    return render_template_string(HTML.replace('AUTO_LOGIN_USER', '').replace('AUTO_LOGIN_AVATAR', '').replace('AUTO_LOGIN_TOKEN', ''))

@socketio.on('rc')
def rc(data):
    phone = ''.join(filter(str.isdigit, data.get('p', '')))
    if len(phone) < 10:
        emit('er', {'m': 'Enter valid number'})
        return
    code = str(random.randint(100000, 999999))
    pending[phone] = code
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def vc(data):
    phone = data.get('d', '')
    code = data.get('c', '')
    if phone not in pending or code != pending[phone]:
        emit('er', {'m': 'Wrong code'})
        return
    del pending[phone]
    for uname, udata in users.items():
        if udata.get('phone') == phone:
            emit('ue', {'n': uname})
            return
    emit('nu', {'d': phone})

@socketio.on('sp')
def sp(data):
    phone = data.get('d', '')
    password = data.get('p', '')
    name = data.get('n', '').strip()
    if not name or len(name) < 2:
        emit('er', {'m': 'Name too short'})
        return
    color = colors[len(users) % len(colors)]
    token = generate_token()
    users[name] = {
        's': request.sid, 'a': color, 'au': None, 'st': 'online',
        'phone': phone, 'pass': hash_pass(password), 'lang': 'ru', 'bio': '',
        'token': token
    }
    groups['general']['members'].add(name)
    join_room('general')
    emit('ro', {'n': name, 'a': color, 'token': token})

@socketio.on('li')
def li(data):
    name = data.get('n', '')
    password = data.get('p', '')
    if name not in users:
        emit('er', {'m': 'User not found'})
        return
    if users[name]['pass'] != hash_pass(password):
        emit('er', {'m': 'Wrong password'})
        return
    token = generate_token()
    users[name]['s'] = request.sid
    users[name]['st'] = 'online'
    users[name]['token'] = token
    join_room('general')
    emit('lo', {'n': name, 'a': users[name].get('au') or users[name]['a'], 'token': token})

@socketio.on('auto_login')
def auto_login(data):
    token = data.get('token', '')
    for uname, udata in users.items():
        if udata.get('token') == token:
            users[uname]['s'] = request.sid
            users[uname]['st'] = 'online'
            join_room('general')
            emit('lo', {'n': uname, 'a': udata.get('au') or udata['a'], 'token': token})
            return
    emit('er', {'m': 'Session expired'})

@socketio.on('sm')
def sm(data):
    name = data.get('n', '')
    chat = data.get('ch', 'general')
    msg_type = data.get('t', 'text')
    if name not in users: return
    msg = {
        'i': f"m{time.time()}", 'n': name, 't': msg_type,
        'ts': datetime.now().strftime("%H:%M"),
        'a': users[name].get('au') or users[name]['a']
    }
    if msg_type == 'text':
        msg['c'] = data.get('c', '')[:2000]
    elif msg_type in ['img', 'vid']:
        msg['c'] = data.get('c', '')[:150000]
    if chat in groups:
        groups[chat]['messages'].append(msg)
    elif chat in private_chats:
        private_chats[chat]['messages'].append(msg)
    emit('nm', {'ch': chat, 'm': msg}, room=chat)

@socketio.on('jc')
def jc(data):
    chat = data.get('ch', 'general')
    join_room(chat)
    msgs = groups[chat]['messages'][-100:] if chat in groups else private_chats.get(chat, {}).get('messages', [])[-100:]
    emit('ch', {'ms': msgs})

@socketio.on('gu')
def gu(data):
    au = [{'n': n, 'a': d.get('au') or d['a'], 'st': d['st']} for n, d in users.items() if n != data.get('n')]
    emit('ul', {'u': au})

@socketio.on('sp2')
def sp2(data):
    u1, u2 = data.get('n'), data.get('t')
    cid = f"p_{min(u1, u2)}_{max(u1, u2)}"
    if cid not in private_chats:
        private_chats[cid] = {'users': [u1, u2], 'messages': []}
    join_room(cid)
    emit('po', {'ch': cid, 't': u2, 'a': users[u2].get('au') or users[u2]['a'], 'ms': private_chats[cid]['messages']})

@socketio.on('ua')
def ua(data):
    users[data.get('n')]['au'] = data.get('a', '')

@socketio.on('ub')
def ub(data):
    users[data.get('n')]['bio'] = data.get('b', '')[:100]

@socketio.on('ul2')
def ul2(data):
    users[data.get('n')]['lang'] = data.get('l', 'ru')

@socketio.on('cp')
def cp(data):
    name = data.get('n')
    content = data.get('m', '')
    if len(content) > 200000:
        content = content[:200000]
    post = {
        'id': f"p{len(posts)}_{time.time()}", 'n': name,
        'a': users[name].get('au') or users[name]['a'],
        'm': content, 'mt': data.get('mt', 'image'),
        'c': data.get('c', '')[:500], 'l': [], 'cm': [],
        'ts': datetime.now().strftime("%d.%m.%Y %H:%M")
    }
    posts.insert(0, post)
    if len(posts) > 50: posts.pop()
    emit('np', {'p': post}, broadcast=True)

@socketio.on('gp')
def gp():
    emit('pl', {'p': posts[:30]})

@socketio.on('lp')
def lp(data):
    for p in posts:
        if p['id'] == data.get('pid'):
            u = data.get('n')
            if u in p['l']: p['l'].remove(u)
            else: p['l'].append(u)
            emit('pu', {'p': p}, broadcast=True)
            break

@socketio.on('cmp')
def cmp(data):
    for p in posts:
        if p['id'] == data.get('pid'):
            p['cm'].append({
                'n': data.get('n'),
                'a': users[data.get('n')].get('au') or users[data.get('n')]['a'],
                'c': data.get('c', '')[:300],
                'ts': datetime.now().strftime("%H:%M")
            })
            emit('pu', {'p': p}, broadcast=True)
            break

@socketio.on('sh')
def sh():
    emit('sl', {'l': request.host})

@socketio.on('logout')
def logout(data):
    token = data.get('token', '')
    for uname, udata in users.items():
        if udata.get('token') == token:
            udata['token'] = ''
            udata['st'] = 'offline'
            break

HTML = r'''
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0d0d0d">
<title>Shugramm</title>
<style>
:root{--bg:#0d0d0d;--bg2:#1a1a1a;--bg3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff;--b:#3a3a3a;--gr:#4CAF50;--r:#f44}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w);user-select:none;overflow:hidden}
.app{width:100%;max-width:480px;height:100vh;background:var(--bg);display:flex;flex-direction:column}
.header{background:var(--bg2);padding:8px 16px;display:flex;align-items:center;border-bottom:1px solid var(--b);min-height:44px}
.header-title{font-weight:600;font-size:17px;flex:1;display:flex;align-items:center;gap:8px}
.header-title .logo{color:var(--y);font-weight:800;font-size:20px}
.btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;padding:6px;border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center}
.btn:active{background:var(--bg3)}
.nav{background:var(--bg2);display:flex;border-top:1px solid var(--b);padding:4px 0;padding-bottom:max(4px,env(safe-area-inset-bottom))}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:1px;cursor:pointer;color:var(--g);font-size:10px;padding:6px 4px;transition:color .2s}
.nav-item.active{color:var(--y)}
.nav-item svg{width:22px;height:22px}
.content{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;display:none}
.content.active{display:block}
.list-item{display:flex;align-items:center;padding:10px 16px;gap:10px;cursor:pointer}
.list-item:active{background:var(--bg3)}
.avatar{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:19px;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.avatar img{width:100%;height:100%;object-fit:cover}
.list-info{flex:1;min-width:0;border-bottom:1px solid rgba(255,255,255,.05);padding-bottom:10px}
.list-name{font-weight:500;font-size:15px}
.list-preview{font-size:13px;color:var(--g);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.msg-row{display:flex;gap:4px;margin-bottom:2px;padding:0 14px}
.msg-row.mine{flex-direction:row-reverse}
.msg-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#000;flex-shrink:0;margin-top:auto;overflow:hidden;background:var(--y)}
.msg-avatar img{width:100%;height:100%;object-fit:cover}
.msg-bubble{max-width:75%;padding:7px 10px;border-radius:14px;font-size:14px;line-height:1.4;word-wrap:break-word;white-space:pre-wrap;background:var(--bg3)}
.msg-row.mine .msg-bubble{background:var(--y);color:#000}
.msg-bubble img{max-width:220px;max-height:280px;border-radius:8px;cursor:pointer;display:block;object-fit:cover}
.msg-bubble video{max-width:220px;max-height:280px;border-radius:8px;display:block}
.msg-time{font-size:10px;color:var(--g);text-align:right;margin-top:1px;padding:0 3px}
.msg-row.mine .msg-time{color:rgba(0,0,0,.5)}
.input-bar{display:flex;padding:6px 10px;background:var(--bg2);border-top:1px solid var(--b);gap:6px;align-items:center}
.input-bar input{flex:1;padding:9px 14px;background:var(--bg3);border:1px solid var(--b);border-radius:18px;color:var(--w);font-size:14px;outline:none}
.input-bar input:focus{border-color:var(--y)}
.send-btn{width:34px;height:34px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:16px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}
.post-card{background:var(--bg2);margin-bottom:12px}
.post-header{display:flex;align-items:center;padding:10px 14px;gap:8px}
.post-avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:14px;color:#000;overflow:hidden;background:var(--y)}
.post-avatar img{width:100%;height:100%;object-fit:cover}
.post-user{font-weight:500;font-size:14px}
.post-date{font-size:11px;color:var(--g)}
.post-media{width:100%;max-height:400px;object-fit:cover;cursor:pointer;display:block}
.post-actions{display:flex;padding:8px 14px;gap:20px}
.post-action{background:none;border:none;color:var(--w);cursor:pointer;display:flex;align-items:center;gap:5px;font-size:13px;padding:0}
.post-caption{padding:0 14px 8px;font-size:13px;line-height:1.4}
.post-comments{padding:0 14px 8px}
.comment-row{display:flex;gap:6px;margin-bottom:3px;font-size:12px}
.comment-avatar{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.comment-avatar img{width:100%;height:100%;object-fit:cover}
.comment-body{flex:1;line-height:1.3}
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
.login-card h1{font-size:24px;font-weight:700;margin-bottom:2px}
.login-card p{color:var(--g);font-size:13px;margin-bottom:18px}
.form-input{width:100%;padding:12px 14px;background:var(--bg2);border:1px solid var(--b);border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center}
.form-input:focus{border-color:var(--y)}
.form-btn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}
.form-link{background:none;border:none;color:var(--y);font-size:13px;cursor:pointer;margin-top:10px}
.code-box{background:var(--bg3);padding:12px;border-radius:8px;font-size:26px;letter-spacing:8px;font-weight:600;color:var(--y);margin:10px 0}
.hidden{display:none!important}
.media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:#000;z-index:300;display:none;align-items:center;justify-content:center}
.media-viewer.show{display:flex}
.media-viewer img{max-width:100%;max-height:100vh;object-fit:contain}
.media-viewer video{max-width:100%;max-height:100vh}
.media-close{position:absolute;top:14px;right:14px;width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.15);border:none;color:#fff;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;z-index:301}
.fab{position:fixed;bottom:76px;right:14px;width:48px;height:48px;border-radius:14px;background:var(--y);color:#000;border:none;font-size:22px;cursor:pointer;z-index:10;display:none;align-items:center;justify-content:center;box-shadow:0 2px 12px rgba(255,215,0,.3)}
.fab.show{display:flex}
</style>
</head>
<body>
<div class="app">
<div class="header"><div class="header-title"><span class="logo">⚡</span>Shugramm</div><button class="btn" onclick="share()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg></button></div>
<div class="content active" id="chatsContent"></div>
<div class="content" id="usersContent"></div>
<div class="content" id="postsContent"></div>
<div class="content" id="settingsContent"></div>
<div id="chatWindow" class="hidden" style="flex:1;display:none;flex-direction:column">
<div class="header"><button class="btn" onclick="closeChat()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><polyline points="15 18 9 12 15 6"/></svg></button><span style="font-weight:500;flex:1" id="chatTitle"></span></div>
<div id="messages" style="flex:1;overflow-y:auto;padding:6px 0"></div>
<div class="input-bar"><button class="btn" onclick="document.getElementById('fileInput').click()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg></button><input type="text" id="msgInput" placeholder="Сообщение" onkeypress="if(event.key==='Enter')sendMsg()"><button class="send-btn" onclick="sendMsg()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></button></div>
</div>
<button class="fab" id="fab" onclick="createPost()">+</button>
<div class="nav" id="nav" style="display:none">
<div class="nav-item active" onclick="switchTab('chats')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>Чаты</div>
<div class="nav-item" onclick="switchTab('users')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4-4v2"/><circle cx="9" cy="7" r="4"/></svg>Контакты</div>
<div class="nav-item" onclick="switchTab('posts')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>Посты</div>
<div class="nav-item" onclick="switchTab('settings')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>Ещё</div>
</div>
<div class="media-viewer" id="mediaViewer"><button class="media-close" onclick="closeMedia()">✕</button><img id="mediaImg" style="display:none"><video id="mediaVid" controls style="display:none"></video></div>
<div class="login-screen" id="loginScreen">
<div class="login-card">
<div id="step1"><div class="login-logo">⚡</div><h1>Shugramm</h1><p>Введите номер телефона</p><input type="tel" class="form-input" id="phoneInput" placeholder="+7 999 123-45-67"><button class="form-btn" onclick="requestCode()">Получить код</button></div>
<div id="step2" class="hidden"><div class="login-logo">⚡</div><h1>Код</h1><p>Отправлен на <span id="phoneDisplay" style="color:var(--y);font-weight:600"></span></p><div class="code-box" id="codeDisplay"></div><input type="text" class="form-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:6px"><button class="form-btn" onclick="verifyCode()">Подтвердить</button><button class="form-link" onclick="backToPhone()">Изменить номер</button></div>
<div id="step3" class="hidden"><div class="login-logo">⚡</div><h1>Регистрация</h1><input type="password" class="form-input" id="passwordInput" placeholder="Придумайте пароль"><input type="text" class="form-input" id="nameInput" placeholder="Имя пользователя"><button class="form-btn" onclick="setPassword()">Зарегистрироваться</button></div>
<div id="step4" class="hidden"><div class="login-logo">⚡</div><h1>Вход</h1><p style="color:var(--y);font-weight:600" id="loginUsername"></p><input type="password" class="form-input" id="loginPassword" placeholder="Введите пароль"><button class="form-btn" onclick="loginUser()">Войти</button><button class="form-link" onclick="backToStart()">Назад</button></div>
</div>
</div>
</div>
<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const s=io();let u=null,ua=null,ch='general',pd='',lang='ru',token='';
let savedToken=localStorage.getItem('shugramm_token')||'AUTO_LOGIN_TOKEN';
if(savedToken&&savedToken!=='AUTO_LOGIN_TOKEN'&&savedToken!==''){s.emit('auto_login',{token:savedToken})}
function requestCode(){const p=document.getElementById('phoneInput').value.trim();if(p.length<10){alert('Enter valid number');return}s.emit('rc',{p:p})}
function verifyCode(){const c=document.getElementById('codeInput').value.trim();if(c.length!==6){alert('Enter 6 digits');return}s.emit('vc',{d:pd,c:c})}
function setPassword(){const p=document.getElementById('passwordInput').value.trim();const n=document.getElementById('nameInput').value.trim();if(!p||p.length<4){alert('Password min 4 chars');return}if(!n||n.length<2){alert('Name min 2 chars');return}s.emit('sp',{d:pd,p:p,n:n})}
function loginUser(){const p=document.getElementById('loginPassword').value.trim();if(!p)return;s.emit('li',{n:document.getElementById('loginUsername').textContent,p:p})}
function backToPhone(){document.getElementById('step2').classList.add('hidden');document.getElementById('step1').classList.remove('hidden')}
function backToStart(){document.getElementById('step4').classList.add('hidden');document.getElementById('step1').classList.remove('hidden')}
s.on('cs',d=>{pd=d.d;document.getElementById('step1').classList.add('hidden');document.getElementById('step2').classList.remove('hidden');document.getElementById('phoneDisplay').textContent='+'+d.d;document.getElementById('codeDisplay').textContent=d.c})
s.on('ue',d=>{document.getElementById('step2').classList.add('hidden');document.getElementById('step4').classList.remove('hidden');document.getElementById('loginUsername').textContent=d.n})
s.on('nu',d=>{pd=d.d;document.getElementById('step2').classList.add('hidden');document.getElementById('step3').classList.remove('hidden')})
s.on('ro',d=>{u=d.n;ua=d.a;token=d.token;localStorage.setItem('shugramm_token',token);enterApp()})
s.on('lo',d=>{u=d.n;ua=d.a;token=d.token;localStorage.setItem('shugramm_token',token);enterApp()})
s.on('er',d=>{alert('❌ '+d.m)})
function enterApp(){document.getElementById('loginScreen').classList.add('hidden');document.getElementById('nav').style.display='flex';loadChats()}
function loadChats(){document.getElementById('chatsContent').innerHTML='<div class="list-item" onclick="openChat(\'general\',\'Общий чат\')"><div class="avatar">#</div><div class="list-info"><div class="list-name">Общий чат</div></div></div>'}
function switchTab(t){
document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
document.getElementById('fab').classList.remove('show');
if(t==='chats'){document.getElementById('chatsContent').classList.add('active');document.querySelector('.nav-item:nth-child(1)').classList.add('active')}
else if(t==='users'){document.getElementById('usersContent').classList.add('active');document.querySelector('.nav-item:nth-child(2)').classList.add('active');s.emit('gu',{n:u})}
else if(t==='posts'){document.getElementById('postsContent').classList.add('active');document.querySelector('.nav-item:nth-child(3)').classList.add('active');document.getElementById('fab').classList.add('show');s.emit('gp')}
else{document.getElementById('settingsContent').classList.add('active');document.querySelector('.nav-item:nth-child(4)').classList.add('active');loadSettings()}
}
function loadSettings(){
let h='<div class="profile-section"><div class="profile-avatar" onclick="document.getElementById(\'avatarInput\').click()">'+(ua?'<img src="'+ua+'">':u[0])+'</div><div class="profile-name">'+u+'</div><div class="profile-bio" id="bioText">'+(usersBio||'Нажмите чтобы добавить описание')+'</div></div>';
h+='<div class="settings-group"><div class="setting-item" onclick="editBio()"><span class="setting-label">Описание</span></div>';
h+='<div class="setting-item" onclick="changeLang()"><span class="setting-label">Язык</span><span class="setting-value">'+lang+'</span></div>';
h+='<div class="setting-item" onclick="share()"><span class="setting-label">Поделиться</span></div>';
h+='<div class="setting-item" onclick="doLogout()"><span class="setting-label" style="color:var(--r)">Выйти</span></div></div>';
document.getElementById('settingsContent').innerHTML=h
}
let usersBio='';
function editBio(){const b=prompt('Описание профиля:',usersBio||'');if(b!==null){usersBio=b;s.emit('ub',{n:u,b:b});loadSettings()}}
function changeLang(){lang=lang==='ru'?'en':'ru';s.emit('ul2',{n:u,l:lang});loadSettings()}
function doLogout(){localStorage.removeItem('shugramm_token');s.emit('logout',{token:token});u=null;ua=null;location.reload()}
function openChat(id,nm){ch=id;document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='='flex';document.getElementById('chatTitle').textContent=nm;document.getElementById('messages').innerHTML='';s.emit('jc',{ch:id})}
function closeChat(){document.getElementById('chatWindow').classList.add('hidden');document.getElementById('chatWindow').style.display='none';document.getElementById('chatsContent').classList.add('active')}
function sendMsg(){const i=document.getElementById('msgInput');const t=i.value.trim();if(!t)return;s.emit('sm',{n:u,ch:ch,t:'text',c:t});i.value=''}
function handleFile(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f)}
function handleAvatar(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{ua=ev.target.result;s.emit('ua',{n:u,a:ev.target.result});loadSettings()};r.readAsDataURL(f)}
function handlePost(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{const c=prompt('Описание:','');s.emit('cp',{n:u,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''})};r.readAsDataURL(f)}
function createPost(){document.getElementById('postInput').click()}
s.on('ch',d=>{document.getElementById('messages').innerHTML='';d.ms.forEach(m=>addMsg(m));scrollBottom()})
s.on('nm',d=>{if(d.ch===ch){addMsg(d.m);scrollBottom()}})
function addMsg(m){
const c=document.getElementById('messages');const im=m.n===u;const d=document.createElement('div');
d.className='msg-row '+(im?'mine':'');
let ct=m.t==='img'?`<img src="${m.c}" onclick="viewMedia('${m.c}','img')">`:m.t==='vid'?`<video src="${m.c}" controls></video>`:m.c.replace(/</g,'&lt;').replace(/\n/g,'<br>');
let av=m.a&&m.a.startsWith('data:')?`<img src="${m.a}">`:m.n[0];
d.innerHTML=`<div class="msg-avatar">${av}</div><div style="max-width:75%"><div class="msg-bubble">${ct}</div><div class="msg-time">${m.ts}</div></div>`;
c.appendChild(d)
}
function scrollBottom(){const c=document.getElementById('messages');setTimeout(()=>{c.scrollTop=c.scrollHeight},50)}
s.on('ul',d=>{
let h='';d.u.forEach(u2=>{h+=`<div class="list-item" onclick="startPrivate('${u2.n}')"><div class="avatar">${u2.a&&u2.a.startsWith('data:')?`<img src="${u2.a}">`:u2.n[0]}</div><div class="list-info"><div class="list-name">${u2.n}</div><div class="list-preview">${u2.st==='online'?'В сети':'Был недавно'}</div></div></div>`});
document.getElementById('usersContent').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет контактов</div>'
})
function startPrivate(t){s.emit('sp2',{n:u,t:t})}
s.on('po',d=>{
ch=d.ch;document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='flex';
document.getElementById('chatTitle').textContent=d.t;document.getElementById('messages').innerHTML='';
d.ms.forEach(m=>addMsg(m));scrollBottom()
})
s.on('pl',d=>{
let h='';d.p.forEach(p=>{h+=buildPost(p)});
document.getElementById('postsContent').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет постов</div>'
})
s.on('np',d=>{const el=document.getElementById('postsContent');if(el.classList.contains('active'))el.insertAdjacentHTML('afterbegin',buildPost(d.p))})
s.on('pu',d=>{const el=document.getElementById(d.p.id);if(el)el.outerHTML=buildPost(d.p)})
function buildPost(p){
return `<div class="post-card" id="${p.id}"><div class="post-header"><div class="post-avatar">${p.a&&p.a.startsWith('data:')?`<img src="${p.a}">`:p.n[0]}</div><div><div class="post-user">${p.n}</div><div class="post-date">${p.ts}</div></div></div>${p.mt==='image'?`<img class="post-media" src="${p.m}" onclick="viewMedia('${p.m}','img')">`:`<video class="post-media" src="${p.m}" controls></video>`}<div class="post-actions"><button class="post-action" onclick="likePost('${p.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg>${p.l.length}</button></div><div class="post-caption"><b>${p.n}</b> ${p.c}</div><div class="post-comments">${p.cm.map(c=>`<div class="comment-row"><div class="comment-avatar">${c.a&&c.a.startsWith('data:')?`<img src="${c.a}">`:c.n[0]}</div><div class="comment-body"><b>${c.n}</b> ${c.c}</div></div>`).join('')}</div><div class="comment-input"><input id="ci_${p.id}" placeholder="Комментарий..."><button onclick="addComment('${p.id}')">Отправить</button></div></div>`
}
function likePost(pid){s.emit('lp',{pid:pid,n:u})}
function addComment(pid){const i=document.getElementById('ci_'+pid);const t=i.value.trim();if(!t)return;s.emit('cmp',{pid:pid,n:u,c:t});i.value=''}
function share(){s.emit('sh')}
s.on('sl',d=>{const l='https://'+d.l;if(navigator.clipboard){navigator.clipboard.writeText(l).then(()=>alert('Ссылка скопирована!'))}else{prompt('Ссылка:',l)}})
function viewMedia(src,tp){
const mv=document.getElementById('mediaViewer');mv.classList.add('show');
if(tp==='img'){document.getElementById('mediaImg').src=src;document.getElementById('mediaImg').style.display='block';document.getElementById('mediaVid').style.display='none'}
else{document.getElementById('mediaVid').src=src;document.getElementById('mediaVid').style.display='block';document.getElementById('mediaImg').style.display='none'}
}
function closeMedia(){document.getElementById('mediaViewer').classList.remove('show')}
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
