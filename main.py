from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'shugramm-pro')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

users = {}
posts = []
groups = {'general': {'id': 'general', 'name': '💬 Общий чат', 'members': set(), 'messages': []}}
private_chats = {}
pending = {}
colors = ['#FFD700', '#FFA500', '#FF8C00', '#FFB800', '#FFC800']

def gen_code():
    return str(random.randint(100000, 999999))

@app.route('/')
def index():
    return render_template_string(HTML)

@socketio.on('connect')
def connect():
    print('✅ Подключен')

@socketio.on('rc')
def rc(data):
    phone = ''.join(filter(str.isdigit, data.get('p', '')))
    if len(phone) < 10:
        emit('er', {'m': 'Введите номер (минимум 10 цифр)'})
        return
    code = gen_code()
    pending[phone] = code
    print(f'📱 Код: {code}')
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def vc(data):
    phone = data.get('d', '')
    code = data.get('c', '')
    if phone not in pending:
        emit('er', {'m': 'Запросите код'})
        return
    if code != pending[phone]:
        emit('er', {'m': 'Неверный код'})
        return
    del pending[phone]
    emit('ok', {})

@socketio.on('reg')
def reg(data):
    name = data.get('n', '').strip()
    if not name or len(name) < 2:
        emit('er', {'m': 'Минимум 2 символа'})
        return
    if name in users:
        emit('er', {'m': 'Имя занято'})
        return
    
    color = colors[len(users) % len(colors)]
    users[name] = {
        's': request.sid,
        'a': color,
        'au': None,
        'st': 'онлайн',
        'bio': '',
        'phone': data.get('ph', '')
    }
    groups['general']['members'].add(name)
    join_room('general')
    
    emit('rok', {'n': name, 'a': color})

@socketio.on('sm')
def sm(data):
    name = data.get('n', '')
    chat = data.get('ch', 'general')
    msg_type = data.get('t', 'text')
    
    if name not in users:
        return
    
    msg = {
        'i': f"m{time.time()}",
        'n': name,
        't': msg_type,
        'ts': datetime.now().strftime("%H:%M"),
        'a': users[name].get('au') or users[name]['a']
    }
    
    if msg_type == 'text':
        msg['c'] = data.get('c', '').strip()[:1000]
    elif msg_type in ['img', 'vid']:
        msg['c'] = data.get('c', '')[:100000]
    
    if chat in groups:
        groups[chat]['messages'].append(msg)
    elif chat in private_chats:
        private_chats[chat]['messages'].append(msg)
    
    emit('nm', {'ch': chat, 'm': msg}, room=chat)

@socketio.on('jc')
def jc(data):
    chat = data.get('ch', 'general')
    join_room(chat)
    msgs = groups[chat]['messages'][-50:] if chat in groups else private_chats.get(chat, {}).get('messages', [])[-50:]
    emit('ch', {'ch': chat, 'ms': msgs})

@socketio.on('gu')
def gu(data):
    name = data.get('n', '')
    au = [{'n': n, 'a': d.get('au') or d['a'], 'st': d['st']} for n, d in users.items() if n != name]
    emit('ul', {'u': au})

@socketio.on('sp')
def sp(data):
    name = data.get('n', '')
    target = data.get('t', '')
    chat_id = f"p_{min(name, target)}_{max(name, target)}"
    if chat_id not in private_chats:
        private_chats[chat_id] = {'users': [name, target], 'messages': []}
    join_room(chat_id)
    msgs = private_chats[chat_id]['messages'][-50:]
    emit('pok', {'ch': chat_id, 't': target, 'a': users[target].get('au') or users[target]['a'], 'ms': msgs})

@socketio.on('ua')
def ua(data):
    name = data.get('n', '')
    users[name]['au'] = data.get('a', '')

@socketio.on('cp')
def cp(data):
    name = data.get('n', '')
    post = {
        'id': f"p{len(posts)}_{time.time()}",
        'n': name,
        'a': users[name].get('au') or users[name]['a'],
        'm': data.get('m', '')[:100000],
        'mt': data.get('mt', 'image'),
        'c': data.get('c', '')[:500],
        'l': [],
        'cm': [],
        'ts': datetime.now().strftime("%d.%m.%Y %H:%M")
    }
    posts.insert(0, post)
    if len(posts) > 50:
        posts.pop()
    emit('np', {'p': post}, broadcast=True)

@socketio.on('gp')
def gp():
    emit('pl', {'p': posts[:30]})

@socketio.on('lp')
def lp(data):
    pid = data.get('pid', '')
    name = data.get('n', '')
    for p in posts:
        if p['id'] == pid:
            if name in p['l']:
                p['l'].remove(name)
            else:
                p['l'].append(name)
            emit('pu', {'p': p}, broadcast=True)
            break

@socketio.on('cmp')
def cmp(data):
    pid = data.get('pid', '')
    name = data.get('n', '')
    text = data.get('c', '').strip()[:300]
    if not text:
        return
    comment = {'n': name, 'a': users[name].get('au') or users[name]['a'], 'c': text, 'ts': datetime.now().strftime("%H:%M")}
    for p in posts:
        if p['id'] == pid:
            p['cm'].append(comment)
            emit('pu', {'p': p}, broadcast=True)
            break

@socketio.on('sh')
def sh():
    emit('sl', {'l': request.host})

HTML = r'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0d0d0d">
<title>Shugramm</title>
<style>
:root{--bg:#0d0d0d;--bg2:#1a1a1a;--bg3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff;--b:#3a3a3a;--r:#f44;--gr:#4CAF50}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w);user-select:none;overflow:hidden}
.app{width:100%;max-width:480px;height:100vh;background:var(--bg);display:flex;flex-direction:column}
.header{background:var(--bg2);padding:10px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--b);min-height:46px}
.header-title{font-weight:800;font-size:17px;display:flex;align-items:center;gap:5px;flex:1}
.header-title span{color:var(--y)}
.btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center}
.btn:active{background:var(--bg3)}
.nav{background:var(--bg2);display:flex;border-top:1px solid var(--b);padding:6px 0;padding-bottom:max(6px,env(safe-area-inset-bottom))}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:1px;cursor:pointer;color:var(--g);font-size:10px;padding:4px}
.nav-item.active{color:var(--y)}
.nav-icon{font-size:20px}
.content{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;display:none}
.content.active{display:block}
.chat-item{display:flex;align-items:center;padding:12px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.03)}
.chat-item:active{background:var(--bg3)}
.av{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:#000;flex-shrink:0;position:relative;overflow:hidden}
.av img{width:100%;height:100%;object-fit:cover}
.online-dot{position:absolute;bottom:1px;right:1px;width:12px;height:12px;background:var(--gr);border-radius:50%;border:2px solid var(--bg)}
.chat-info{flex:1;min-width:0}
.chat-name{font-weight:600;font-size:14px}
.chat-last{font-size:11px;color:var(--g);margin-top:2px}
.msgs{padding:10px}
.msg-row{display:flex;gap:5px;margin-bottom:4px;animation:msgIn .2s}
@keyframes msgIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.msg-row.mine{flex-direction:row-reverse}
.msg-av{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#000;flex-shrink:0;margin-top:auto;overflow:hidden}
.msg-av img{width:100%;height:100%;object-fit:cover}
.bubble{max-width:78%;padding:8px 10px;border-radius:12px;font-size:14px;line-height:1.4;word-wrap:break-word;background:var(--bg3)}
.msg-row.mine .bubble{background:#333;border:1px solid rgba(255,215,0,.2)}
.bubble img{max-width:200px;border-radius:8px;cursor:pointer;display:block}
.bubble video{max-width:200px;border-radius:8px}
.msg-time{font-size:10px;color:var(--g);text-align:right;margin-top:2px}
.input-bar{display:flex;padding:8px 10px;background:var(--bg2);border-top:1px solid var(--b);gap:6px;align-items:center}
.input-bar input{flex:1;padding:10px 14px;background:#333;border:1px solid var(--b);border-radius:20px;color:var(--w);font-size:14px;outline:none}
.input-bar input:focus{border-color:var(--y)}
.send-btn{width:38px;height:38px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:18px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}
.send-btn:active{background:#e6c200}
.post{background:var(--bg2);margin-bottom:20px}
.post-header{display:flex;align-items:center;padding:10px 12px;gap:8px}
.post-av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#000;overflow:hidden}
.post-av img{width:100%;height:100%;object-fit:cover}
.post-user{font-weight:600;font-size:13px}
.post-time{font-size:10px;color:var(--g)}
.post-media{width:100%;max-height:400px;object-fit:cover;display:block;cursor:pointer}
.post-actions{display:flex;padding:8px 12px;gap:20px}
.post-action{background:none;border:none;color:var(--w);font-size:22px;cursor:pointer;display:flex;align-items:center;gap:5px}
.post-action span{font-size:13px}
.post-caption{padding:0 12px 8px;font-size:13px}
.post-comments{padding:0 12px 8px;max-height:150px;overflow-y:auto}
.comment{display:flex;gap:6px;margin-bottom:4px;font-size:12px}
.comment-av{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:700;color:#000;flex-shrink:0;overflow:hidden}
.comment-av img{width:100%;height:100%;object-fit:cover}
.comment-text{flex:1}
.comment-user{font-weight:600}
.add-comment{display:flex;padding:8px 12px;border-top:1px solid var(--b);gap:8px}
.add-comment input{flex:1;background:none;border:none;color:var(--w);font-size:13px;outline:none}
.add-comment button{background:none;border:none;color:var(--y);font-weight:600;cursor:pointer}
.user-item{display:flex;align-items:center;padding:12px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.03)}
.user-item:active{background:var(--bg3)}
.user-status{font-size:11px;margin-top:2px}
.user-status.online{color:var(--gr)}
.profile-section{text-align:center;padding:30px 20px;background:var(--bg2)}
.profile-av{width:90px;height:90px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:36px;font-weight:700;color:#000;margin:0 auto 12px;cursor:pointer;overflow:hidden;position:relative}
.profile-av img{width:100%;height:100%;object-fit:cover}
.profile-name{font-size:22px;font-weight:700}
.profile-phone{color:var(--g);font-size:13px;margin-top:4px}
.login-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
.login-box{text-align:center;padding:30px 20px;width:90%;max-width:340px}
.login-logo{width:80px;height:80px;background:var(--y);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 18px;font-size:36px;color:#000;font-weight:900}
.login-box h1{font-size:24px;margin-bottom:4px;font-weight:800}
.login-box .sub{color:var(--g);font-size:13px;margin-bottom:18px}
.login-input{width:100%;padding:14px;background:var(--bg2);border:1px solid var(--b);border-radius:12px;color:var(--w);font-size:15px;margin-bottom:10px;outline:none;text-align:center}
.login-input:focus{border-color:var(--y)}
.login-btn{width:100%;padding:14px;background:var(--y);color:#000;border:none;border-radius:12px;font-size:15px;font-weight:700;cursor:pointer;margin-top:6px}
.login-btn:active{background:#e6c200}
.hidden{display:none!important}
.code-show{background:var(--bg3);padding:12px;border-radius:10px;font-size:26px;letter-spacing:8px;font-weight:700;color:var(--y);margin:12px 0}
.media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.96);z-index:300;display:none;align-items:center;justify-content:center}
.media-viewer.show{display:flex}
.media-viewer img,.media-viewer video{max-width:100%;max-height:100vh;object-fit:contain}
.media-close{position:absolute;top:16px;right:16px;width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.2);border:none;color:#fff;font-size:20px;cursor:pointer;z-index:301;display:flex;align-items:center;justify-content:center}
.create-post-btn{position:fixed;bottom:75px;right:15px;width:52px;height:52px;border-radius:50%;background:var(--y);color:#000;border:none;font-size:26px;cursor:pointer;z-index:10;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(255,215,0,.4)}
.create-post-btn:active{transform:scale(.9)}
.empty-state{text-align:center;padding:50px 20px;color:var(--g)}
.empty-state .icon{font-size:60px;margin-bottom:12px}
</style>
</head>
<body>
<div class="app">
<div class="header"><div class="header-title"><span>⚡</span> Shugramm</div><button class="btn" onclick="share()">🔗</button></div>
<div class="content active" id="chatsContent"></div>
<div class="content" id="usersContent"></div>
<div class="content" id="postsContent"></div>
<div class="content" id="profileContent"></div>
<div id="chatWindow" class="hidden" style="flex:1;display:none;flex-direction:column">
<div class="header"><button class="btn" onclick="closeChat()">←</button><span style="font-weight:700;flex:1" id="chatTitle">Чат</span></div>
<div id="messages" style="flex:1;overflow-y:auto;padding:10px"></div>
<div class="input-bar"><button class="btn" onclick="document.getElementById('fileInput').click()">📎</button><input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMsg()"><button class="send-btn" onclick="sendMsg()">➤</button></div>
</div>
<button class="create-post-btn hidden" id="createPostBtn" onclick="createPost()">+</button>
<div class="nav" id="nav" style="display:none">
<div class="nav-item active" onclick="switchTab('chats')"><span class="nav-icon">💬</span>Чаты</div>
<div class="nav-item" onclick="switchTab('users')"><span class="nav-icon">👥</span>Люди</div>
<div class="nav-item" onclick="switchTab('posts')"><span class="nav-icon">📸</span>Посты</div>
<div class="nav-item" onclick="switchTab('profile')"><span class="nav-icon">👤</span>Профиль</div>
</div>
<div class="media-viewer" id="mediaViewer" onclick="closeMedia()"><button class="media-close">✕</button><img id="mediaImg" style="display:none"><video id="mediaVid" controls style="display:none"></video></div>
<div class="login-screen" id="loginScreen">
<div class="login-box">
<div id="step1"><div class="login-logo">⚡</div><h1>Shugramm</h1><p class="sub">Введите номер телефона</p><input type="tel" class="login-input" id="phoneInput" placeholder="+79991234567"><button class="login-btn" onclick="requestCode()">Получить код</button></div>
<div id="step2" class="hidden"><div class="login-logo">🔐</div><h1>Подтверждение</h1><p class="sub">Код отправлен</p><div class="code-show" id="codeDisplay"></div><input type="text" class="login-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:22px;letter-spacing:6px"><button class="login-btn" onclick="verifyCode()">Подтвердить</button><button class="login-btn" onclick="backToPhone()" style="margin-top:8px;background:var(--bg3);color:var(--w)">← Назад</button></div>
<div id="step3" class="hidden"><div class="login-logo">✏️</div><h1>Профиль</h1><p class="sub">Придумайте имя</p><input type="text" class="login-input" id="nameInput" placeholder="Имя"><button class="login-btn" onclick="finishReg()">Войти в Shugramm</button></div>
</div>
</div>
</div>
<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const socket=io();let user=null,currentChat='general',pd='';
function requestCode(){const p=document.getElementById('phoneInput').value.trim();if(p.length<10){alert('Введите номер');return}socket.emit('rc',{p:p})}
function verifyCode(){const c=document.getElementById('codeInput').value.trim();if(c.length!==6){alert('Введите 6 цифр');return}socket.emit('vc',{d:pd,c:c})}
function finishReg(){const n=document.getElementById('nameInput').value.trim();if(n.length<2){alert('Минимум 2 символа');return}socket.emit('reg',{n:n})}
function backToPhone(){document.getElementById('step2').classList.add('hidden');document.getElementById('step1').classList.remove('hidden')}
socket.on('cs',d=>{pd=d.d;document.getElementById('step1').classList.add('hidden');document.getElementById('step2').classList.remove('hidden');document.getElementById('codeDisplay').textContent=d.c})
socket.on('ok',()=>{document.getElementById('step2').classList.add('hidden');document.getElementById('step3').classList.remove('hidden')})
socket.on('rok',d=>{user=d.n;document.getElementById('loginScreen').classList.add('hidden');document.getElementById('nav').style.display='flex';document.getElementById('createPostBtn').classList.remove('hidden');loadChats()})
socket.on('er',d=>{alert('❌ '+d.m)})
function loadChats(){document.getElementById('chatsContent').innerHTML='<div class="chat-item" onclick="openChat(\'general\',\'💬 Общий чат\')"><div class="av" style="background:#FFD700">💬</div><div class="chat-info"><div class="chat-name">Общий чат</div></div></div>'}
function switchTab(tab){
document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
if(tab==='chats'){document.getElementById('chatsContent').classList.add('active');document.querySelector('.nav-item:nth-child(1)').classList.add('active')}
else if(tab==='users'){document.getElementById('usersContent').classList.add('active');document.querySelector('.nav-item:nth-child(2)').classList.add('active');socket.emit('gu',{n:user})}
else if(tab==='posts'){document.getElementById('postsContent').classList.add('active');document.querySelector('.nav-item:nth-child(3)').classList.add('active');socket.emit('gp')}
else{document.getElementById('profileContent').classList.add('active');document.querySelector('.nav-item:nth-child(4)').classList.add('active');loadProfile()}
}
function loadProfile(){document.getElementById('profileContent').innerHTML='<div class="profile-section"><div class="profile-av" style="background:'+(userAvatar||'#FFD700')+'" onclick="document.getElementById(\'avatarInput\').click()">'+(userAvatar?'<img src="'+userAvatar+'">':user[0])+'</div><div class="profile-name">'+user+'</div></div>'}
let userAvatar=null;
socket.on('au',d=>{if(d.n===user){userAvatar=d.a;loadProfile()}})
function openChat(id,name){currentChat=id;document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='flex';document.getElementById('chatTitle').textContent=name;document.getElementById('messages').innerHTML='';socket.emit('jc',{n:user,ch:id})}
function closeChat(){document.getElementById('chatWindow').classList.add('hidden');document.getElementById('chatWindow').style.display='none';document.getElementById('chatsContent').classList.add('active')}
function sendMsg(){const i=document.getElementById('msgInput');const t=i.value.trim();if(!t)return;socket.emit('sm',{n:user,ch:currentChat,t:'text',c:t});i.value=''}
function handleFile(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{socket.emit('sm',{n:user,ch:currentChat,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f)}
function handleAvatar(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{userAvatar=ev.target.result;socket.emit('ua',{n:user,a:ev.target.result});loadProfile()};r.readAsDataURL(f)}
function handlePost(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{const c=prompt('Описание:','');socket.emit('cp',{n:user,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''})};r.readAsDataURL(f)}
function createPost(){document.getElementById('postInput').click()}
socket.on('ch',d=>{document.getElementById('messages').innerHTML='';d.ms.forEach(m=>addMsg(m));scrollB()})
socket.on('nm',d=>{if(d.ch===currentChat){addMsg(d.m);scrollB()}})
function addMsg(m){const c=document.getElementById('messages');const im=m.n===user;const d=document.createElement('div');d.className='msg-row '+(im?'mine':'');let cnt=m.t==='img'?`<img src="${m.c}" onclick="viewMedia('${m.c}','image')">`:m.t==='vid'?`<video src="${m.c}" controls></video>`:m.c.replace(/</g,'&lt;');const av=m.a&&m.a.startsWith('data:')?`<img src="${m.a}">`:m.n[0];d.innerHTML=`<div class="msg-av" style="background:${m.a&&m.a.startsWith('data:')?'transparent':m.a}">${av}</div><div style="max-width:78%"><div class="bubble">${cnt}</div><div class="msg-time">${m.ts}</div></div>`;c.appendChild(d)}
function scrollB(){const c=document.getElementById('messages');setTimeout(()=>{c.scrollTop=c.scrollHeight},50)}
socket.on('ul',d=>{let h='';d.u.forEach(u=>{h+=`<div class="user-item" onclick="startPrivate('${u.n}')"><div class="av" style="background:${u.a&&u.a.startsWith('data:')?'transparent':u.a}">${u.a&&u.a.startsWith('data:')?`<img src="${u.a}">`:u.n[0]}</div><div class="chat-info"><div class="chat-name">${u.n}</div><div class="user-status ${u.st==='онлайн'?'online':''}">${u.st}</div></div></div>`});document.getElementById('usersContent').innerHTML=h||'<div class="empty-state"><div class="icon">👥</div><p>Нет пользователей</p></div>'})
function startPrivate(t){socket.emit('sp',{n:user,t:t})}
socket.on('pok',d=>{currentChat=d.ch;document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));document.getElementById('chatWindow').classList.remove('hidden');document.getElementById('chatWindow').style.display='flex';document.getElementById('chatTitle').textContent=d.t;document.getElementById('messages').innerHTML='';d.ms.forEach(m=>addMsg(m));scrollB()})
socket.on('pl',d=>{let h='';d.p.forEach(p=>{h+=`<div class="post" id="${p.id}"><div class="post-header"><div class="post-av" style="background:${p.a&&p.a.startsWith('data:')?'transparent':p.a}">${p.a&&p.a.startsWith('data:')?`<img src="${p.a}">`:p.n[0]}</div><div><div class="post-user">${p.n}</div><div class="post-time">${p.ts}</div></div></div>${p.mt==='image'?`<img class="post-media" src="${p.m}" onclick="viewMedia('${p.m}','image')">`:`<video class="post-media" src="${p.m}" controls></video>`}<div class="post-actions"><button class="post-action" onclick="likePost('${p.id}')">❤️<span>${p.l.length}</span></button><button class="post-action">💬<span>${p.cm.length}</span></button></div><div class="post-caption"><b>${p.n}</b>${p.c}</div><div class="post-comments">${p.cm.map(c=>`<div class="comment"><div class="comment-av" style="background:${c.a&&c.a.startsWith('data:')?'transparent':c.a}">${c.a&&c.a.startsWith('data:')?`<img src="${c.a}">`:c.n[0]}</div><div class="comment-text"><span class="comment-user">${c.n}</span> ${c.c}</div></div>`).join('')}</div><div class="add-comment"><input id="ci_${p.id}" placeholder="Комментарий..."><button onclick="addComment('${p.id}')">➤</button></div></div>`});document.getElementById('postsContent').innerHTML=h||'<div class="empty-state"><div class="icon">📸</div><p>Нет постов</p></div>'})
socket.on('np',d=>{const el=document.getElementById('postsContent');if(el.classList.contains('active')){el.insertAdjacentHTML('afterbegin',buildPostHTML(d.p))}})
socket.on('pu',d=>{const el=document.getElementById(d.p.id);if(el){el.outerHTML=buildPostHTML(d.p)}})
function buildPostHTML(p){return`<div class="post" id="${p.id}"><div class="post-header"><div class="post-av" style="background:${p.a&&p.a.startsWith('data:')?'transparent':p.a}">${p.a&&p.a.startsWith('data:')?`<img src="${p.a}">`:p.n[0]}</div><div><div class="post-user">${p.n}</div><div class="post-time">${p.ts}</div></div></div>${p.mt==='image'?`<img class="post-media" src="${p.m}" onclick="viewMedia('${p.m}','image')">`:`<video class="post-media" src="${p.m}" controls></video>`}<div class="post-actions"><button class="post-action" onclick="likePost('${p.id}')">❤️<span>${p.l.length}</span></button><button class="post-action">💬<span>${p.cm.length}</span></button></div><div class="post-caption"><b>${p.n}</b>${p.c}</div><div class="post-comments">${p.cm.map(c=>`<div class="comment"><div class="comment-av" style="background:${c.a&&c.a.startsWith('data:')?'transparent':c.a}">${c.a&&c.a.startsWith('data:')?`<img src="${c.a}">`:c.n[0]}</div><div class="comment-text"><span class="comment-user">${c.n}</span> ${c.c}</div></div>`).join('')}</div><div class="add-comment"><input id="ci_${p.id}" placeholder="Комментарий..."><button onclick="addComment('${p.id}')">➤</button></div></div>`}
function likePost(pid){socket.emit('lp',{pid:pid,n:user})}
function addComment(pid){const i=document.getElementById('ci_'+
