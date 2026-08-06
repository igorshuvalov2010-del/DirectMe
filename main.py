from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'shugramm-secret')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

users = {}
groups = {'general': {'id': 'general', 'name': '💬 Общий чат Shugramm', 'members': set(), 'messages': []}}
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
    print('✅ Пользователь подключился')

@socketio.on('rc')
def rc(data):
    phone = ''.join(filter(str.isdigit, data.get('p', '')))
    if len(phone) < 10:
        emit('er', {'m': 'Введите номер телефона (минимум 10 цифр)'})
        return
    code = gen_code()
    pending[phone] = code
    print(f'📱 Код для {phone}: {code}')
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def vc(data):
    phone = data.get('d', '')
    code = data.get('c', '')
    if phone not in pending:
        emit('er', {'m': 'Сначала запросите код'})
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
        emit('er', {'m': 'Имя должно содержать минимум 2 символа'})
        return
    if name in users:
        emit('er', {'m': 'Это имя уже занято'})
        return
    
    color = colors[len(users) % len(colors)]
    users[name] = {'s': request.sid, 'a': color, 'st': 'онлайн'}
    groups['general']['members'].add(name)
    join_room('general')
    
    print(f'✅ Зарегистрирован: {name}')
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
        'a': users[name]['a']
    }
    
    if msg_type == 'text':
        msg['c'] = data.get('c', '').strip()[:1000]
        if not msg['c']:
            return
    elif msg_type in ['img', 'vid']:
        msg['c'] = data.get('c', '')[:80000]
    
    if chat in groups:
        groups[chat]['messages'].append(msg)
    
    emit('nm', {'ch': chat, 'm': msg}, room=chat)

@socketio.on('jc')
def jc(data):
    chat = data.get('ch', 'general')
    join_room(chat)
    msgs = groups[chat]['messages'][-50:] if chat in groups else []
    emit('ch', {'ch': chat, 'ms': msgs})

@socketio.on('gu')
def gu(data):
    name = data.get('n', '')
    all_users = []
    for n, d in users.items():
        if n != name:
            all_users.append({'n': n, 'a': d['a'], 'st': d['st']})
    emit('ul', {'u': all_users})

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
        :root{--bg:#0d0d0d;--bg2:#1a1a1a;--bg3:#2a2a2a;--y:#FFD700;--y2:#E6C200;--g:#888;--w:#fff;--b:#3a3a3a;--r:#f44;--gr:#4CAF50}
        *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w);user-select:none;overflow:hidden}
        .app{width:100%;max-width:480px;height:100vh;background:var(--bg);display:flex;flex-direction:column}
        
        .header{background:var(--bg2);padding:10px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--b);min-height:46px}
        .header-title{font-weight:800;font-size:17px;display:flex;align-items:center;gap:5px;flex:1}
        .header-title span{color:var(--y)}
        .btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center}
        .btn:active{background:var(--bg3)}
        
        .nav{background:var(--bg2);display:flex;border-top:1px solid var(--b);padding:8px 0;padding-bottom:max(8px,env(safe-area-inset-bottom))}
        .nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;color:var(--g);font-size:10px;padding:4px}
        .nav-item.active{color:var(--y)}
        .nav-icon{font-size:22px}
        
        .content{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;display:none}
        .content.active{display:block}
        
        .chat-item{display:flex;align-items:center;padding:10px 12px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.03)}
        .chat-item:active{background:var(--bg3)}
        .av{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:#000;flex-shrink:0;position:relative}
        .online-dot{position:absolute;bottom:1px;right:1px;width:12px;height:12px;background:var(--gr);border-radius:50%;border:2px solid var(--bg)}
        .chat-info{flex:1;min-width:0}
        .chat-name{font-weight:600;font-size:14px}
        .chat-last{font-size:11px;color:var(--g);margin-top:1px}
        
        .msgs{padding:8px}
        .msg-row{display:flex;gap:4px;margin-bottom:3px;animation:msgIn .2s}
        @keyframes msgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
        .msg-row.mine{flex-direction:row-reverse}
        .msg-av{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#000;flex-shrink:0;margin-top:auto}
        .bubble{max-width:78%;padding:7px 9px;border-radius:10px;font-size:13px;line-height:1.35;word-wrap:break-word;background:var(--bg3)}
        .msg-row.mine .bubble{background:#333;border:1px solid rgba(255,215,0,.15)}
        .bubble img{max-width:180px;max-height:180px;border-radius:6px;cursor:pointer;display:block}
        .bubble video{max-width:180px;max-height:180px;border-radius:6px}
        .msg-time{font-size:9px;color:var(--g);text-align:right;margin-top:1px}
        
        .input-bar{display:flex;padding:6px 8px;background:var(--bg2);border-top:1px solid var(--b);gap:6px;align-items:center}
        .input-bar input{flex:1;padding:8px 12px;background:#333;border:1px solid var(--b);border-radius:18px;color:var(--w);font-size:13px;outline:none}
        .input-bar input:focus{border-color:var(--y)}
        .send-btn{width:34px;height:34px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:16px;cursor:pointer;flex-shrink:0}
        .send-btn:active{background:var(--y2)}
        
        .user-item{display:flex;align-items:center;padding:10px 12px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.03)}
        .user-item:active{background:var(--bg3)}
        .user-status{font-size:11px}
        .user-status.online{color:var(--gr)}
        
        .login-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
        .login-box{text-align:center;padding:20px;width:90%;max-width:320px}
        .login-logo{width:70px;height:70px;background:var(--y);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:32px;color:#000;font-weight:900}
        .login-box h1{font-size:22px;margin-bottom:4px}
        .login-box .sub{color:var(--g);font-size:12px;margin-bottom:16px}
        .login-input{width:100%;padding:12px;background:var(--bg2);border:1px solid var(--b);border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center}
        .login-input:focus{border-color:var(--y)}
        .login-btn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;margin-top:4px}
        .login-btn:active{background:var(--y2)}
        .hidden{display:none!important}
        .code-show{background:var(--bg3);padding:10px;border-radius:8px;font-size:22px;letter-spacing:6px;font-weight:700;color:var(--y);margin:10px 0}
        
        .media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.95);z-index:300;display:none;align-items:center;justify-content:center}
        .media-viewer.show{display:flex}
        .media-viewer img,.media-viewer video{max-width:100%;max-height:100vh;object-fit:contain}
        .media-close{position:absolute;top:16px;right:16px;width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);border:none;color:#fff;font-size:18px;cursor:pointer;z-index:301;display:flex;align-items:center;justify-content:center}
        
        .empty-state{text-align:center;padding:40px;color:var(--g)}
        .empty-state .icon{font-size:50px;margin-bottom:10px}
    </style>
</head>
<body>
    <div class="app">
        <div class="header">
            <div class="header-title"><span>⚡</span> Shugramm</div>
            <button class="btn" onclick="share()">🔗</button>
        </div>
        
        <div class="content active" id="chatsContent"></div>
        <div class="content" id="usersContent"></div>
        
        <div id="chatWindow" class="hidden" style="flex:1;display:none;flex-direction:column">
            <div class="header">
                <button class="btn" onclick="closeChat()">←</button>
                <span style="font-weight:700;flex:1" id="chatTitle">Чат</span>
            </div>
            <div id="messages" style="flex:1;overflow-y:auto;padding:8px"></div>
            <div class="input-bar">
                <button class="btn" onclick="document.getElementById('fileInput').click()">📎</button>
                <input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMsg()">
                <button class="send-btn" onclick="sendMsg()">➤</button>
            </div>
        </div>
        
        <div class="nav" id="nav" style="display:none">
            <div class="nav-item active" onclick="switchTab('chats')">
                <span class="nav-icon">💬</span>Чаты
            </div>
            <div class="nav-item" onclick="switchTab('users')">
                <span class="nav-icon">👥</span>Люди
            </div>
        </div>
        
        <div class="media-viewer" id="mediaViewer" onclick="closeMedia()">
            <button class="media-close">✕</button>
            <img id="mediaImg" style="display:none">
            <video id="mediaVid" controls style="display:none"></video>
        </div>
        
        <div class="login-screen" id="loginScreen">
            <div class="login-box">
                <div id="step1">
                    <div class="login-logo">⚡</div>
                    <h1>Shugramm</h1>
                    <p class="sub">Введите номер телефона для регистрации</p>
                    <input type="tel" class="login-input" id="phoneInput" placeholder="+7 (999) 123-45-67">
                    <button class="login-btn" onclick="requestCode()">Получить код подтверждения</button>
                </div>
                <div id="step2" class="hidden">
                    <div class="login-logo">🔐</div>
                    <h1>Подтверждение номера</h1>
                    <p class="sub">Мы отправили код на номер</p>
                    <div class="code-show" id="codeDisplay"></div>
                    <p style="color:var(--g);font-size:10px;margin-bottom:8px">⚠️ Введите код показанный выше</p>
                    <input type="text" class="login-input" id="codeInput" placeholder="••••••" maxlength="6" inputmode="numeric" style="font-size:20px;letter-spacing:5px">
                    <button class="login-btn" onclick="verifyCode()">Подтвердить код</button>
                    <button class="login-btn" onclick="backToPhone()" style="margin-top:6px;background:var(--bg3);color:var(--w)">← Изменить номер</button>
                </div>
                <div id="step3" class="hidden">
                    <div class="login-logo">✏️</div>
                    <h1>Создание профиля</h1>
                    <p class="sub">Придумайте имя пользователя</p>
                    <input type="text" class="login-input" id="nameInput" placeholder="Имя пользователя">
                    <button class="login-btn" onclick="finishReg()">Войти в Shugramm</button>
                </div>
            </div>
        </div>
    </div>
    
    <input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="handleFile(event)">
    
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script>
        const socket = io();
        let user = null;
        let currentChat = 'general';
        let phoneDigits = '';
        
        function requestCode() {
            const phone = document.getElementById('phoneInput').value.trim();
            if (phone.length < 10) { alert('Введите номер телефона (минимум 10 цифр)'); return; }
            socket.emit('rc', { p: phone });
        }
        
        function verifyCode() {
            const code = document.getElementById('codeInput').value.trim();
            if (code.length !== 6) { alert('Введите 6 цифр кода'); return; }
            socket.emit('vc', { d: phoneDigits, c: code });
        }
        
        function finishReg() {
            const name = document.getElementById('nameInput').value.trim();
            if (name.length < 2) { alert('Имя должно содержать минимум 2 символа'); return; }
            socket.emit('reg', { n: name });
        }
        
        function backToPhone() {
            document.getElementById('step2').classList.add('hidden');
            document.getElementById('step1').classList.remove('hidden');
        }
        
        socket.on('cs', function(d) {
            phoneDigits = d.d;
            document.getElementById('step1').classList.add('hidden');
            document.getElementById('step2').classList.remove('hidden');
            document.getElementById('codeDisplay').textContent = d.c;
        });
        
        socket.on('ok', function() {
            document.getElementById('step2').classList.add('hidden');
            document.getElementById('step3').classList.remove('hidden');
            document.getElementById('nameInput').focus();
        });
        
        socket.on('rok', function(d) {
            user = d.n;
            document.getElementById('loginScreen').classList.add('hidden');
            document.getElementById('nav').style.display = 'flex';
            loadChats();
        });
        
        socket.on('er', function(d) {
            alert('❌ ' + d.m);
        });
        
        function loadChats() {
            document.getElementById('chatsContent').innerHTML = '<div class="chat-item" onclick="openChat(\'general\',\'💬 Общий чат Shugramm\')"><div class="av" style="background:#FFD700">💬</div><div class="chat-info"><div class="chat-name">Общий чат Shugramm</div><div class="chat-last">Нажмите чтобы открыть</div></div></div>';
        }
        
        function switchTab(tab) {
            document.querySelectorAll('.content').forEach(function(c) { c.classList.remove('active'); });
            document.querySelectorAll('.nav-item').forEach(function(n) { n.classList.remove('active'); });
            if (tab === 'chats') {
                document.getElementById('chatsContent').classList.add('active');
                document.querySelector('.nav-item:first-child').classList.add('active');
            } else {
                document.getElementById('usersContent').classList.add('active');
                document.querySelector('.nav-item:last-child').classList.add('active');
                socket.emit('gu', { n: user });
            }
        }
        
        function openChat(id, name) {
            currentChat = id;
            document.getElementById('chatsContent').classList.remove('active');
            document.getElementById('usersContent').classList.remove('active');
            document.getElementById('chatWindow').classList.remove('hidden');
            document.getElementById('chatWindow').style.display = 'flex';
            document.getElementById('chatTitle').textContent = name;
            document.getElementById('messages').innerHTML = '';
            socket.emit('jc', { n: user, ch: id });
        }
        
        function closeChat() {
            document.getElementById('chatWindow').classList.add('hidden');
            document.getElementById('chatWindow').style.display = 'none';
            document.getElementById('chatsContent').classList.add('active');
        }
        
        function sendMsg() {
            var input = document.getElementById('msgInput');
            var text = input.value.trim();
            if (!text) return;
            socket.emit('sm', { n: user, ch: currentChat, t: 'text', c: text });
            input.value = '';
        }
        
        function handleFile(e) {
            var file = e.target.files[0];
            if (!file) return;
            var reader = new FileReader();
            reader.onload = function(ev) {
                socket.emit('sm', { n: user, ch: currentChat, t: file.type.startsWith('video') ? 'vid' : 'img', c: ev.target.result });
            };
            reader.readAsDataURL(file);
        }
        
        socket.on('ch', function(d) {
            document.getElementById('messages').innerHTML = '';
            d.ms.forEach(function(m) { addMsg(m); });
            scrollBottom();
        });
        
        socket.on('nm', function(d) {
            if (d.ch === currentChat) {
                addMsg(d.m);
                scrollBottom();
            }
        });
        
        function addMsg(m) {
            var container = document.getElementById('messages');
            var isMine = m.n === user;
            var div = document.createElement('div');
            div.className = 'msg-row ' + (isMine ? 'mine' : '');
            
            var content = '';
            if (m.t === 'img') {
                content = '<img src="' + m.c + '" onclick="viewMedia(\'' + m.c + '\',\'image\')">';
            } else if (m.t === 'vid') {
                content = '<video src="' + m.c + '" controls></video>';
            } else {
                content = m.c.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            }
            
            div.innerHTML = '<div class="msg-av" style="background:' + m.a + '">' + m.n[0] + '</div><div style="max-width:78%"><div class="bubble">' + content + '</div><div class="msg-time">' + m.ts + '</div></div>';
            container.appendChild(div);
        }
        
        function scrollBottom() {
            var c = document.getElementById('messages');
            setTimeout(function() { c.scrollTop = c.scrollHeight; }, 50);
        }
        
        socket.on('ul', function(d) {
            var html = '';
            if (d.u.length === 0) {
                html = '<div class="empty-state"><div class="icon">👥</div><p>Пока нет других пользователей</p><p style="font-size:12px;margin-top:8px">Пригласите друзей по ссылке!</p></div>';
            } else {
                d.u.forEach(function(u) {
                    html += '<div class="user-item"><div class="av" style="background:' + u.a + '">' + u.n[0] + '</div><div class="chat-info"><div class="chat-name">' + u.n + '</div><div class="user-status ' + (u.st === 'онлайн' ? 'online' : '') + '">' + u.st + '</div></div></div>';
                });
            }
            document.getElementById('usersContent').innerHTML = html;
        });
        
        function share() {
            socket.emit('sh');
        }
        
        socket.on('sl', function(d) {
            var link = 'https://' + d.l;
            if (navigator.clipboard) {
                navigator.clipboard.writeText(link).then(function() {
                    alert('✅ Ссылка скопирована! Отправьте её друзьям.');
                });
            } else {
                prompt('Ссылка для приглашения (скопируйте):', link);
            }
        });
        
        function viewMedia(src, type) {
            var viewer = document.getElementById('mediaViewer');
            viewer.classList.add('show');
            if (type === 'image') {
                document.getElementById('mediaImg').src = src;
                document.getElementById('mediaImg').style.display = 'block';
                document.getElementById('mediaVid').style.display = 'none';
            } else {
                document.getElementById('mediaVid').src = src;
                document.getElementById('mediaVid').style.display = 'block';
                document.getElementById('mediaImg').style.display = 'none';
            }
        }
        
        function closeMedia() {
            document.getElementById('mediaViewer').classList.remove('show');
        }
    </script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'🚀 Shugramm запущен на порту {port}')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
