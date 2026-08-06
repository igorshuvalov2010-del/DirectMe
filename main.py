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

@app.route('/')
def index():
    return render_template_string(HTML)

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
    users[name] = {
        's': request.sid, 'a': color, 'au': None, 'st': 'online',
        'phone': phone, 'pass': hash_pass(password), 'lang': 'ru', 'bio': ''
    }
    groups['general']['members'].add(name)
    join_room('general')
    emit('ro', {'n': name, 'a': color})

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
    users[name]['s'] = request.sid
    users[name]['st'] = 'online'
    join_room('general')
    emit('lo', {'n': name, 'a': users[name].get('au') or users[name]['a']})

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

@socketio.on('ul')
def ul(data):
    users[data.get('n')]['lang'] = data.get('l', 'ru')

@socketio.on('cp')
def cp(data):
    name = data.get('n')
    post = {
        'id': f"p{len(posts)}_{time.time()}", 'n': name,
        'a': users[name].get('au') or users[name]['a'],
        'm': data.get('m', '')[:100000], 'mt': data.get('mt', 'image'),
        'c': data.get('c', '')[:300], 'l': [], 'cm': [],
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
                'c': data.get('c', '')[:200],
                'ts': datetime.now().strftime("%H:%M")
            })
            emit('pu', {'p': p}, broadcast=True)
            break

@socketio.on('sh')
def sh():
    emit('sl', {'l': request.host})

HTML = r'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Shugramm</title>
<style>
:root{--b:#0d0d0d;--b2:#1a1a1a;--b3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff;--br:#3a3a3a}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w)}
.app{width:100%;max-width:480px;height:100vh;background:var(--b);display:flex;flex-direction:column}
.h{background:var(--b2);padding:12px 16px;display:flex;align-items:center;border-bottom:1px solid var(--br);min-height:48px}
.ht{font-weight:700;font-size:18px;flex:1}
.ht span{color:var(--y)}
.btn{background:none;border:none;color:var(--w);font-size:20px;cursor:pointer;padding:6px;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center}
.nv{background:var(--b2);display:flex;border-top:1px solid var(--br);padding:6px 0}
.ni{flex:1;text-align:center;color:var(--g);font-size:10px;cursor:pointer;padding:5px}
.ni.ac{color:var(--y)}
.ct{flex:1;overflow-y:auto;display:none}
.ct.ac{display:block}
.ci{display:flex;align-items:center;padding:12px 16px;gap:12px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.04)}
.av{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:18px;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.av img{width:100%;height:100%;object-fit:cover}
.cif{flex:1;min-width:0}
.cn{font-weight:600;font-size:15px}
.cp{font-size:12px;color:var(--g);margin-top:2px}
.mr{display:flex;gap:6px;margin-bottom:4px;padding:0 16px}
.mr.mi{flex-direction:row-reverse}
.ma{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;color:#000;flex-shrink:0;margin-top:auto;overflow:hidden;background:var(--y)}
.ma img{width:100%;height:100%;object-fit:cover}
.mb{max-width:75%;padding:8px 12px;border-radius:16px;font-size:14px;line-height:1.4;word-wrap:break-word;background:var(--b3)}
.mr.mi .mb{background:var(--y);color:#000}
.mb img{max-width:220px;border-radius:8px;cursor:pointer;display:block}
.mb video{max-width:220px;border-radius:8px;cursor:pointer;display:block}
.mt{font-size:10px;color:var(--g);text-align:right;margin-top:2px;padding:0 4px}
.mr.mi .mt{color:rgba(0,0,0,.6)}
.ib{display:flex;padding:8px 12px;background:var(--b2);border-top:1px solid var(--br);gap:8px;align-items:center}
.ib input{flex:1;padding:10px 16px;background:var(--b3);border:1px solid var(--br);border-radius:20px;color:var(--w);font-size:14px;outline:none}
.sb{width:38px;height:38px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:18px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}
.pc2{background:var(--b2);margin-bottom:16px}
.ph{display:flex;align-items:center;padding:12px;gap:10px}
.pa{width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:15px;color:#000;overflow:hidden;background:var(--y)}
.pa img{width:100%;height:100%;object-fit:cover}
.pu{font-weight:600;font-size:14px}
.pd{font-size:11px;color:var(--g)}
.pm{width:100%;max-height:400px;object-fit:cover;cursor:pointer;display:block}
.pac{display:flex;padding:10px 12px;gap:20px}
.pbtn{background:none;border:none;color:var(--w);cursor:pointer;display:flex;align-items:center;gap:4px;font-size:13px;padding:0}
.pcap{padding:0 12px 8px;font-size:13px}
.pcm{padding:0 12px 8px;max-height:150px;overflow-y:auto}
.cm{display:flex;gap:6px;margin-bottom:4px;font-size:12px}
.cma{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.cma img{width:100%;height:100%;object-fit:cover}
.cmb{flex:1;line-height:1.3}
.cin{display:flex;padding:8px 12px;border-top:1px solid var(--br);gap:8px}
.cin input{flex:1;background:none;border:none;color:var(--w);font-size:13px;outline:none}
.cin button{background:none;border:none;color:var(--y);font-weight:600;cursor:pointer;font-size:13px}
.pf{text-align:center;padding:30px 20px;background:var(--b2);margin:10px;border-radius:12px}
.pfa{width:90px;height:90px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:36px;font-weight:600;color:#000;margin:0 auto 12px;cursor:pointer;overflow:hidden;background:var(--y)}
.pfa img{width:100%;height:100%;object-fit:cover}
.pfn{font-size:20px;font-weight:700}
.sg{padding:10px}
.si{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;background:var(--b2);margin-bottom:1px;cursor:pointer}
.sl{font-size:14px}
.sv{color:var(--g);font-size:13px}
.ls{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--b);display:flex;align-items:center;justify-content:center;z-index:100}
.lb{text-align:center;padding:30px 24px;width:90%;max-width:360px}
.ll{width:80px;height:80px;background:var(--y);border-radius:20px;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:32px;color:#000;font-weight:900}
.lb h1{font-size:26px;font-weight:800;margin-bottom:4px}
.lb p{color:var(--g);font-size:13px;margin-bottom:20px}
.fi{width:100%;padding:14px 16px;background:var(--b2);border:1px solid var(--br);border-radius:12px;color:var(--w);font-size:15px;margin-bottom:10px;outline:none;text-align:center}
.fb{width:100%;padding:14px;background:var(--y);color:#000;border:none;border-radius:12px;font-size:15px;font-weight:700;cursor:pointer;margin-top:6px}
.fl{background:none;border:none;color:var(--y);font-size:13px;cursor:pointer;margin-top:12px}
.cd{background:var(--b3);padding:14px;border-radius:10px;font-size:28px;letter-spacing:10px;font-weight:700;color:var(--y);margin:12px 0}
.hd{display:none!important}
.mv{position:fixed;top:0;left:0;right:0;bottom:0;background:#000;z-index:300;display:none;align-items:center;justify-content:center}
.mv.sh{display:flex}
.mv img{max-width:100%;max-height:100vh;object-fit:contain}
.mv video{max-width:100%;max-height:100vh}
.mc{position:absolute;top:16px;right:16px;width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.2);border:none;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;z-index:301}
.fab{position:fixed;bottom:80px;right:16px;width:50px;height:50px;border-radius:50%;background:var(--y);color:#000;border:none;font-size:24px;cursor:pointer;z-index:10;display:flex;align-items:center;justify-content:center}
</style>
</head>
<body>
<div class="app">
<div class="h"><div class="ht"><span>⚡</span>Shugramm</div><button class="btn" onclick="share()">🔗</button></div>
<div class="ct ac" id="cc"></div>
<div class="ct" id="uc"></div>
<div class="ct" id="pc"></div>
<div class="ct" id="sc"></div>
<div id="cw" class="hd" style="flex:1;display:none;flex-direction:column">
<div class="h"><button class="btn" onclick="cl()">←</button><span style="font-weight:600;flex:1" id="cti"></span></div>
<div id="mc" style="flex:1;overflow-y:auto;padding:8px 0"></div>
<div class="ib"><button class="btn" onclick="document.getElementById('fi2').click()">📎</button><input type="text" id="mi" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sm()"><button class="sb" onclick="sm()">➤</button></div>
</div>
<button class="fab hd" id="fab" onclick="cp2()">+</button>
<div class="nv" id="nv" style="display:none">
<div class="ni ac" onclick="st('c')">💬 Чаты</div>
<div class="ni" onclick="st('u')">👥 Люди</div>
<div class="ni" onclick="st('p')">📸 Посты</div>
<div class="ni" onclick="st('s')">⚙️ Ещё</div>
</div>
<div class="mv" id="mv"><button class="mc" onclick="clm()">✕</button><img id="mvi" style="display:none"><video id="mvv" controls style="display:none"></video></div>
<div class="ls" id="ls">
<div class="lb">
<div id="s1"><div class="ll">⚡</div><h1>Shugramm</h1><p>Введите номер телефона</p><input type="tel" class="fi" id="pi" placeholder="+79991234567"><button class="fb" onclick="rc()">Получить код</button></div>
<div id="s2" class="hd"><div class="ll">🔐</div><h1>Подтверждение</h1><p>Код отправлен на <span id="pd2" style="color:var(--y);font-weight:600"></span></p><div class="cd" id="cd2"></div><input type="text" class="fi" id="ci" placeholder="••••••" maxlength="6" style="font-size:22px;letter-spacing:6px"><button class="fb" onclick="vc()">Подтвердить</button><button class="fl" onclick="bp()">← Изменить номер</button></div>
<div id="s3" class="hd"><div class="ll">🔑</div><h1>Придумайте пароль</h1><p style="color:var(--g);margin-bottom:16px">Для защиты аккаунта</p><input type="password" class="fi" id="pwi" placeholder="Пароль"><input type="text" class="fi" id="ni" placeholder="Имя пользователя"><button class="fb" onclick="sp3()">Зарегистрироваться</button></div>
<div id="s4" class="hd"><div class="ll">👤</div><h1>Вход</h1><p style="color:var(--y);font-weight:600" id="lu"></p><input type="password" class="fi" id="lpi" placeholder="Введите пароль"><button class="fb" onclick="li2()">Войти</button><button class="fl" onclick="bs()">← Назад</button></div>
</div>
</div>
</div>
<input type="file" id="fi2" accept="image/*,video/*" style="display:none" onchange="hf(event)">
<input type="file" id="ai" accept="image/*" style="display:none" onchange="ha(event)">
<input type="file" id="pi3" accept="image/*,video/*" style="display:none" onchange="hp(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const s=io();let u=null,ua=null,ch='general',pd='',ul2='ru';
function rc(){const p=document.getElementById('pi').value.trim();if(p.length<10){alert('Enter valid number');return}s.emit('rc',{p:p})}
function vc(){const c=document.getElementById('ci').value.trim();if(c.length!==6){alert('Enter 6 digits');return}s.emit('vc',{d:pd,c:c})}
function sp3(){const p=document.getElementById('pwi').value.trim();const n=document.getElementById('ni').value.trim();if(!p||p.length<4){alert('Password min 4 chars');return}if(!n||n.length<2){alert('Name min 2 chars');return}s.emit('sp',{d:pd,p:p,n:n})}
function li2(){const p=document.getElementById('lpi').value.trim();if(!p)return;s.emit('li',{n:document.getElementById('lu').textContent,p:p})}
function bp(){document.getElementById('s2').classList.add('hd');document.getElementById('s1').classList.remove('hd')}
function bs(){document.getElementById('s4').classList.add('hd');document.getElementById('s1').classList.remove('hd')}
s.on('cs',d=>{pd=d.d;document.getElementById('s1').classList.add('hd');document.getElementById('s2').classList.remove('hd');document.getElementById('pd2').textContent='+'+d.d;document.getElementById('cd2').textContent=d.c})
s.on('ue',d=>{document.getElementById('s2').classList.add('hd');document.getElementById('s4').classList.remove('hd');document.getElementById('lu').textContent=d.n})
s.on('nu',d=>{pd=d.d;document.getElementById('s2').classList.add('hd');document.getElementById('s3').classList.remove('hd')})
s.on('ro',d=>{u=d.n;ua=d.a;document.getElementById('ls').classList.add('hd');document.getElementById('nv').style.display='flex';document.getElementById('fab').classList.remove('hd');lc()})
s.on('lo',d=>{u=d.n;ua=d.a;document.getElementById('ls').classList.add('hd');document.getElementById('nv').style.display='flex';document.getElementById('fab').classList.remove('hd');lc()})
s.on('er',d=>{alert('❌ '+d.m)})
function lc(){document.getElementById('cc').innerHTML='<div class="ci" onclick="oc(\'general\',\'Общий чат\')"><div class="av">#</div><div class="cif"><div class="cn">Общий чат</div></div></div>'}
function st(t){
document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));
document.querySelectorAll('.ni').forEach(n=>n.classList.remove('ac'));
if(t==='c'){document.getElementById('cc').classList.add('ac');document.querySelector('.ni:nth-child(1)').classList.add('ac')}
else if(t==='u'){document.getElementById('uc').classList.add('ac');document.querySelector('.ni:nth-child(2)').classList.add('ac');s.emit('gu',{n:u})}
else if(t==='p'){document.getElementById('pc').classList.add('ac');document.querySelector('.ni:nth-child(3)').classList.add('ac');s.emit('gp')}
else{document.getElementById('sc').classList.add('ac');document.querySelector('.ni:nth-child(4)').classList.add('ac');ls3()}
}
function ls3(){
let h='<div class="pf"><div class="pfa" onclick="document.getElementById(\'ai\').click()">'+(ua?'<img src="'+ua+'">':(u?u[0]:'?'))+'</div><div class="pfn">'+u+'</div></div>';
h+='<div class="sg"><div class="si" onclick="chl2()"><span class="sl">Язык</span><span class="sv">'+ul2+'</span></div>';
h+='<div class="si" onclick="share()"><span class="sl">Поделиться</span></div>';
h+='<div class="si" onclick="lo2()"><span class="sl" style="color:#f44">Выйти</span></div></div>';
document.getElementById('sc').innerHTML=h
}
function chl2(){ul2=ul2==='ru'?'en':'ru';s.emit('ul',{n:u,l:ul2});ls3()}
function lo2(){u=null;ua=null;location.reload()}
function oc(id,nm){ch=id;document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));document.getElementById('cw').classList.remove('hd');document.getElementById('cw').style.display='flex';document.getElementById('cti').textContent=nm;document.getElementById('mc').innerHTML='';s.emit('jc',{ch:id})}
function cl(){document.getElementById('cw').classList.add('hd');document.getElementById('cw').style.display='none';document.getElementById('cc').classList.add('ac')}
function sm(){const i=document.getElementById('mi');const t=i.value.trim();if(!t)return;s.emit('sm',{n:u,ch:ch,t:'text',c:t});i.value=''}
function hf(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f)}
function ha(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{ua=ev.target.result;s.emit('ua',{n:u,a:ev.target.result});ls3()};r.readAsDataURL(f)}
function hp(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{const c=prompt('Описание:','');s.emit('cp',{n:u,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''})};r.readAsDataURL(f)}
function cp2(){document.getElementById('pi3').click()}
s.on('ch',d=>{document.getElementById('mc').innerHTML='';d.ms.forEach(m=>am(m));sb3()})
s.on('nm',d=>{if(d.ch===ch){am(d.m);sb3()}})
function am(m){
const c=document.getElementById('mc');const im=m.n===u;const d=document.createElement('div');
d.className='mr '+(im?'mi':'');
let ct=m.t==='img'?`<img src="${m.c}" onclick="vm('${m.c}','img')">`:m.t==='vid'?`<video src="${m.c}" controls></video>`:m.c.replace(/</g,'&lt;');
d.innerHTML=`<div class="ma">${m.a&&m.a.startsWith('data:')?`<img src="${m.a}">`:m.n[0]}</div><div style="max-width:75%"><div class="mb">${ct}</div><div class="mt">${m.ts}</div></div>`;
c.appendChild(d)
}
function sb3(){const c=document.getElementById('mc');setTimeout(()=>{c.scrollTop=c.scrollHeight},50)}
s.on('ul',d=>{
let h='';d.u.forEach(u2=>{h+=`<div class="ci" onclick="sp4('${u2.n}')"><div class="av">${u2.a&&u2.a.startsWith('data:')?`<img src="${u2.a}">`:u2.n[0]}</div><div class="cif"><div class="cn">${u2.n}</div></div></div>`});
document.getElementById('uc').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет пользователей</div>'
})
function sp4(t){s.emit('sp2',{n:u,t:t})}
s.on('po',d=>{
ch=d.ch;document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));
document.getElementById('cw').classList.remove('hd');document.getElementById('cw').style.display='flex';
document.getElementById('cti').textContent=d.t;document.getElementById('mc').innerHTML='';
d.ms.forEach(m=>am(m));sb3()
})
s.on('pl',d=>{
let h='';d.p.forEach(p=>{h+=bp2(p)});
document.getElementById('pc').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет постов</div>'
})
s.on('np',d=>{const el=document.getElementById('pc');if(el.classList.contains('ac'))el.insertAdjacentHTML('afterbegin',bp2(d.p))})
s.on('pu',d=>{const el=document.getElementById(d.p.id);if(el)el.outerHTML=bp2(d.p)})
function bp2(p){
return `<div class="pc2" id="${p.id}"><div class="ph"><div class="pa">${p.a&&p.a.startsWith('data:')?`<img src="${p.a}">`:p.n[0]}</div><div><div class="pu">${p.n}</div><div class="pd">${p.ts}</div></div></div>${p.mt==='image'?`<img class="pm" src="${p.m}" onclick="vm('${p.m}','img')">`:`<video class="pm" src="${p.m}" controls></video>`}<div class="pac"><button class="pbtn" onclick="lk('${p.id}')">❤️ ${p.l.length}</button></div><div class="pcap">${p.c}</div><div class="pcm">${p.cm.map(c=>`<div class="cm"><div class="cma">${c.a&&c.a.startsWith('data:')?`<img src="${c.a}">`:c.n[0]}</div><div class="cmb"><strong>${c.n}</strong> ${c.c}</div></div>`).join('')}</div><div class="cin"><input id="ci_${p.id}" placeholder="Комментарий..."><button onclick="ac2('${p.id}')">➤</button></div></div>`
}
function lk(pid){s.emit('lp',{pid:pid,n:u})}
function ac2(pid){const i=document.getElementById('ci_'+pid);const t=i.value.trim();if(!t)return;s.emit('cmp',{pid:pid,n:u,c:t});i.value=''}
function share(){s.emit('sh')}
s.on('sl',d=>{const l='https://'+d.l;if(navigator.clipboard){navigator.clipboard.writeText(l).then(()=>alert('✅ Ссылка скопирована!'))}else{prompt('Ссылка:',l)}})
function vm(src,tp){
const mv=document.getElementById('mv');mv.classList.add('sh');
if(tp==='img'){document.getElementById('mvi').src=src;document.getElementById('mvi').style.display='block';document.getElementById('mvv').style.display='none'}
else{document.getElementById('mvv').src=src;document.getElementById('mvv').style.display='block';document.getElementById('mvi').style.display='none'}
}
function clm(){document.getElementById('mv').classList.remove('sh')}
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
