from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'shugramm-' + str(random.randint(10000,99999))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

users = {}
posts = []
groups = {'general': {'id': 'general', 'name': '💬 Общий чат', 'members': set(), 'messages': []}}
private_chats = {}
pending = {}
colors = ['#FFD700', '#FFA500', '#FF8C00', '#FFB800', '#FFC800', '#E6C200']

@app.route('/')
def index():
    return render_template_string(HTML)

@socketio.on('rc')
def rc(data):
    phone = data.get('p', '')
    if len(phone) < 10:
        emit('er', {'m': 'Введите номер (минимум 10 цифр)'})
        return
    code = str(random.randint(100000, 999999))
    pending[phone] = code
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def vc(data):
    if data.get('d') not in pending or data.get('c') != pending[data.get('d')]:
        emit('er', {'m': 'Неверный код'})
        return
    del pending[data['d']]
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
    users[name] = {'s': request.sid, 'a': color, 'au': None, 'st': 'онлайн'}
    groups['general']['members'].add(name)
    join_room('general')
    emit('rok', {'n': name, 'a': color})

@socketio.on('sm')
def sm(data):
    name = data.get('n', '')
    chat = data.get('ch', 'general')
    msg = {'i': f"m{time.time()}", 'n': name, 't': data.get('t', 'text'), 'ts': datetime.now().strftime("%H:%M"), 'a': users[name].get('au') or users[name]['a']}
    if msg['t'] == 'text':
        msg['c'] = data.get('c', '')[:1000]
    else:
        msg['c'] = data.get('c', '')[:50000]
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
    emit('ch', {'ms': msgs})

@socketio.on('gu')
def gu(data):
    au = [{'n': n, 'a': d.get('au') or d['a'], 'st': d['st']} for n, d in users.items() if n != data.get('n')]
    emit('ul', {'u': au})

@socketio.on('sp')
def sp(data):
    u1, u2 = data.get('n'), data.get('t')
    cid = f"p_{min(u1, u2)}_{max(u1, u2)}"
    if cid not in private_chats:
        private_chats[cid] = {'users': [u1, u2], 'messages': []}
    join_room(cid)
    emit('pok', {'ch': cid, 't': u2, 'a': users[u2].get('au') or users[u2]['a'], 'ms': private_chats[cid]['messages']})

@socketio.on('ua')
def ua(data):
    users[data.get('n')]['au'] = data.get('a', '')

@socketio.on('cp')
def cp(data):
    name = data.get('n')
    post = {'id': f"p{len(posts)}_{time.time()}", 'n': name, 'a': users[name].get('au') or users[name]['a'], 'm': data.get('m', '')[:50000], 'mt': data.get('mt', 'image'), 'c': data.get('c', '')[:300], 'l': [], 'cm': [], 'ts': datetime.now().strftime("%d.%m.%Y %H:%M")}
    posts.insert(0, post)
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
            p['cm'].append({'n': data.get('n'), 'a': users[data.get('n')].get('au') or users[data.get('n')]['a'], 'c': data.get('c', '')[:200], 'ts': datetime.now().strftime("%H:%M")})
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
.h{background:var(--b2);padding:10px 14px;display:flex;align-items:center;border-bottom:1px solid var(--br)}
.h span{color:var(--y);font-weight:800;font-size:17px;flex:1}
.btn{background:none;border:none;color:var(--w);font-size:18px;padding:5px 10px;cursor:pointer}
.nav{background:var(--b2);display:flex;border-top:1px solid var(--br);padding:8px 0}
.ni{flex:1;text-align:center;color:var(--g);font-size:10px;cursor:pointer;padding:5px}
.ni.ac{color:var(--y)}
.ct{flex:1;overflow-y:auto;padding:10px;display:none}
.ct.ac{display:block}
.ci{display:flex;align-items:center;padding:10px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.05)}
.av{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:#000;background:var(--y);flex-shrink:0;overflow:hidden}
.av img{width:100%;height:100%;object-fit:cover}
.mr{display:flex;gap:5px;margin-bottom:5px}
.mr.mi{flex-direction:row-reverse}
.bb{max-width:75%;padding:8px 10px;border-radius:12px;font-size:14px;background:var(--b3)}
.mr.mi .bb{background:#333;border:1px solid rgba(255,215,0,.2)}
.bb img{max-width:180px;border-radius:8px;cursor:pointer;display:block}
.mt{font-size:9px;color:var(--g);text-align:right;margin-top:2px}
.ib{display:flex;padding:8px;background:var(--b2);border-top:1px solid var(--br);gap:5px}
.ib input{flex:1;padding:8px 12px;background:#333;border:1px solid #444;border-radius:18px;color:var(--w);font-size:14px;outline:none}
.sb{width:34px;height:34px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:16px;cursor:pointer}
.ls{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--b);display:flex;align-items:center;justify-content:center;z-index:100}
.lb{text-align:center;padding:20px;width:90%;max-width:320px}
.ll{width:70px;height:70px;background:var(--y);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:32px;color:#000;font-weight:900}
.lb h1{font-size:22px;margin-bottom:4px}
.li{width:100%;padding:12px;background:var(--b2);border:1px solid #444;border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center}
.lbtn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;margin-top:4px}
.hd{display:none!important}
.cs{background:var(--b3);padding:10px;border-radius:8px;font-size:24px;letter-spacing:8px;font-weight:700;color:var(--y);margin:10px 0}
.post{background:var(--b2);margin-bottom:20px}
.post-h{display:flex;align-items:center;padding:10px;gap:8px}
.post-av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#000;overflow:hidden}
.post-av img{width:100%;height:100%;object-fit:cover}
.post-u{font-weight:600;font-size:13px}
.post-t{font-size:10px;color:var(--g)}
.post-m{width:100%;max-height:350px;object-fit:cover;cursor:pointer}
.post-a{display:flex;padding:8px 10px;gap:16px}
.post-btn{background:none;border:none;color:var(--w);font-size:20px;cursor:pointer}
.post-btn span{font-size:12px;margin-left:3px}
.post-c{padding:0 10px 8px;font-size:13px}
.post-cm{padding:0 10px 8px;max-height:120px;overflow-y:auto}
.cm{display:flex;gap:5px;margin-bottom:3px;font-size:12px}
.cm-av{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:700;color:#000;flex-shrink:0;overflow:hidden}
.cm-av img{width:100%;height:100%;object-fit:cover}
.ac{display:flex;padding:8px 10px;border-top:1px solid var(--br);gap:5px}
.ac input{flex:1;background:none;border:none;color:var(--w);font-size:12px;outline:none}
.ac button{background:none;border:none;color:var(--y);font-weight:600;cursor:pointer}
.pf-av{width:80px;height:80px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:700;color:#000;margin:0 auto 10px;cursor:pointer;overflow:hidden}
.pf-av img{width:100%;height:100%;object-fit:cover}
.pf-name{font-size:20px;font-weight:700;text-align:center}
.mv{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.95);z-index:300;display:none;align-items:center;justify-content:center}
.mv.sh{display:flex}
.mv img,.mv video{max-width:100%;max-height:100vh}
.mc{position:absolute;top:16px;right:16px;width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);border:none;color:#fff;font-size:18px;cursor:pointer}
.cpb{position:fixed;bottom:70px;right:15px;width:48px;height:48px;border-radius:50%;background:var(--y);color:#000;border:none;font-size:24px;cursor:pointer;z-index:10;display:flex;align-items:center;justify-content:center}
</style>
</head>
<body>
<div class="app">
<div class="h"><span>⚡ Shugramm</span><button class="btn" onclick="share()">🔗</button></div>
<div class="ct ac" id="cc"></div>
<div class="ct" id="uc"></div>
<div class="ct" id="pc"></div>
<div class="ct" id="prc"></div>
<div id="cw" class="hd" style="flex:1;display:none;flex-direction:column">
<div class="h"><button class="btn" onclick="cl()">←</button><span id="cti">Чат</span></div>
<div id="mc" style="flex:1;overflow-y:auto;padding:10px"></div>
<div class="ib"><button class="btn" onclick="document.getElementById('fi').click()">📎</button><input type="text" id="mi" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sm()"><button class="sb" onclick="sm()">➤</button></div>
</div>
<button class="cpb hd" id="cpb" onclick="cp()">+</button>
<div class="nav" id="nv" style="display:none">
<div class="ni ac" onclick="st('c')">💬 Чаты</div>
<div class="ni" onclick="st('u')">👥 Люди</div>
<div class="ni" onclick="st('p')">📸 Посты</div>
<div class="ni" onclick="st('pr')">👤 Профиль</div>
</div>
<div class="mv" id="mv" onclick="clm()"><button class="mc">✕</button><img id="mvi" style="display:none"><video id="mvv" controls style="display:none"></video></div>
<div class="ls" id="ls">
<div class="lb">
<div id="s1"><div class="ll">⚡</div><h1>Shugramm</h1><p style="color:var(--g);margin-bottom:15px">Введите номер телефона</p><input type="tel" class="li" id="pi" placeholder="+79991234567"><button class="lbtn" onclick="rc()">Получить код</button></div>
<div id="s2" class="hd"><div class="ll">🔐</div><h1>Подтверждение</h1><p style="color:var(--g);margin-bottom:10px">Код отправлен</p><div class="cs" id="cs"></div><input type="text" class="li" id="ci" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:5px"><button class="lbtn" onclick="vc()">Подтвердить</button><button class="lbtn" onclick="bp()" style="margin-top:6px;background:var(--b3);color:var(--w)">← Назад</button></div>
<div id="s3" class="hd"><div class="ll">✏️</div><h1>Профиль</h1><p style="color:var(--g);margin-bottom:15px">Придумайте имя</p><input type="text" class="li" id="ni" placeholder="Имя"><button class="lbtn" onclick="fr()">Войти в Shugramm</button></div>
</div>
</div>
</div>
<input type="file" id="fi" accept="image/*,video/*" style="display:none" onchange="hf(event)">
<input type="file" id="ai" accept="image/*" style="display:none" onchange="ha(event)">
<input type="file" id="pi2" accept="image/*,video/*" style="display:none" onchange="hp(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const s=io();let u=null,ua=null,ch='general',pd='';
function rc(){const p=document.getElementById('pi').value.trim();if(p.length<10){alert('Введите номер');return}s.emit('rc',{p:p})}
function vc(){const c=document.getElementById('ci').value.trim();if(c.length!==6){alert('Введите 6 цифр');return}s.emit('vc',{d:pd,c:c})}
function fr(){const n=document.getElementById('ni').value.trim();if(n.length<2){alert('Минимум 2 символа');return}s.emit('reg',{n:n})}
function bp(){document.getElementById('s2').classList.add('hd');document.getElementById('s1').classList.remove('hd')}
s.on('cs',d=>{pd=d.d;document.getElementById('s1').classList.add('hd');document.getElementById('s2').classList.remove('hd');document.getElementById('cs').textContent=d.c})
s.on('ok',()=>{document.getElementById('s2').classList.add('hd');document.getElementById('s3').classList.remove('hd')})
s.on('rok',d=>{u=d.n;ua=d.a;document.getElementById('ls').classList.add('hd');document.getElementById('nv').style.display='flex';document.getElementById('cpb').classList.remove('hd');lc()})
s.on('er',d=>{alert('❌ '+d.m)})
function lc(){document.getElementById('cc').innerHTML='<div class="ci" onclick="oc(\'general\',\'💬 Общий чат\')"><div class="av">💬</div><div><b>Общий чат</b></div></div>'}
function st(t){document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));document.querySelectorAll('.ni').forEach(n=>n.classList.remove('ac'));
if(t==='c'){document.getElementById('cc').classList.add('ac');document.querySelector('.ni:nth-child(1)').classList.add('ac')}
else if(t==='u'){document.getElementById('uc').classList.add('ac');document.querySelector('.ni:nth-child(2)').classList.add('ac');s.emit('gu',{n:u})}
else if(t==='p'){document.getElementById('pc').classList.add('ac');document.querySelector('.ni:nth-child(3)').classList.add('ac');s.emit('gp')}
else{document.getElementById('prc').classList.add('ac');document.querySelector('.ni:nth-child(4)').classList.add('ac');lp()}}
function lp(){document.getElementById('prc').innerHTML='<div style="text-align:center;padding:30px"><div class="pf-av" style="background:'+(ua||'#FFD700')+'" onclick="document.getElementById(\'ai\').click()">'+(ua?'<img src="'+ua+'">':u?u[0]:'?')+'</div><div class="pf-name">'+u+'</div><p style="color:var(--g);margin-top:10px">Нажмите на аватар чтобы изменить</p></div>'}
function oc(id,nm){ch=id;document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));document.getElementById('cw').classList.remove('hd');document.getElementById('cw').style.display='flex';document.getElementById('cti').textContent=nm;document.getElementById('mc').innerHTML='';s.emit('jc',{ch:id})}
function cl(){document.getElementById('cw').classList.add('hd');document.getElementById('cw').style.display='none';document.getElementById('cc').classList.add('ac')}
function sm(){const i=document.getElementById('mi');const t=i.value.trim();if(!t)return;s.emit('sm',{n:u,ch:ch,t:'text',c:t});i.value=''}
function hf(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f)}
function ha(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{ua=ev.target.result;s.emit('ua',{n:u,a:ev.target.result});lp()};r.readAsDataURL(f)}
function hp(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{const c=prompt('Описание:','');s.emit('cp',{n:u,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''})};r.readAsDataURL(f)}
function cp(){document.getElementById('pi2').click()}
s.on('ch',d=>{document.getElementById('mc').innerHTML='';d.ms.forEach(m=>am(m));sb()})
s.on('nm',d=>{if(d.ch===ch){am(d.m);sb()}})
function am(m){const c=document.getElementById('mc');const im=m.n===u;const d=document.createElement('div');d.className='mr '+(im?'mi':'');let ct=m.t==='img'?`<img src="${m.c}" onclick="vm('${m.c}','img')">`:m.t==='vid'?`<video src="${m.c}" controls></video>`:m.c.replace(/</g,'&lt;');let av=m.a&&m.a.startsWith('data:')?`<img src="${m.a}">`:m.n[0];d.innerHTML=`<div class="av" style="width:28px;height:28px;font-size:10px;background:${m.a&&m.a.startsWith('data:')?'transparent':m.a}">${av}</div><div style="max-width:75%"><div class="bb">${ct}</div><div class="mt">${m.ts}</div></div>`;c.appendChild(d)}
function sb(){const c=document.getElementById('mc');setTimeout(()=>{c.scrollTop=c.scrollHeight},50)}
s.on('ul',d=>{let h='';d.u.forEach(u=>{h+=`<div class="ci" onclick="sp('${u.n}')"><div class="av" style="background:${u.a&&u.a.startsWith('data:')?'transparent':u.a}">${u.a&&u.a.startsWith('data:')?`<img src="${u.a}">`:u.n[0]}</div><div><b>${u.n}</b><br><span style="color:${u.st==='онлайн'?'#4CAF50':'var(--g)'};font-size:11px">${u.st}</span></div></div>`});document.getElementById('uc').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет пользователей</div>'})
function sp(t){s.emit('sp',{n:u,t:t})}
s.on('pok',d=>{ch=d.ch;document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));document.getElementById('cw').classList.remove('hd');document.getElementById('cw').style.display='flex';document.getElementById('cti').textContent=d.t;document.getElementById('mc').innerHTML='';d.ms.forEach(m=>am(m));sb()})
s.on('pl',d=>{let h='';d.p.forEach(p=>{h+=bp(p)});document.getElementById('pc').innerHTML=h||'<div style="text-align:center;padding:40px;color:var(--g)">Нет постов</div>'})
s.on('np',d=>{const el=document.getElementById('pc');if(el.classList.contains('ac')){el.insertAdjacentHTML('afterbegin',bp(d.p))}})
s.on('pu',d=>{const el=document.getElementById(d.p.id);if(el)el.outerHTML=bp(d.p)})
function bp(p){return `<div class="post" id="${p.id}"><div class="post-h"><div class="post-av" style="background:${p.a&&p.a.startsWith('data:')?'transparent':p.a}">${p.a&&p.a.startsWith('data:')?`<img src="${p.a}">`:p.n[0]}</div><div><div class="post-u">${p.n}</div><div class="post-t">${p.ts}</div></div></div>${p.mt==='image'?`<img class="post-m" src="${p.m}" onclick="vm('${p.m}','img')">`:`<video class="post-m" src="${p.m}" controls></video>`}<div class="post-a"><button class="post-btn" onclick="lik('${p.id}')">❤️<span>${p.l.length}</span></button><button class="post-btn">💬<span>${p.cm.length}</span></button></div><div class="post-c"><b>${p.n}</b> ${p.c}</div><div class="post-cm">${p.cm.map(c=>`<div class="cm"><div class="cm-av" style="background:${c.a&&c.a.startsWith('data:')?'transparent':c.a}">${c.a&&c.a.startsWith('data:')?`<img src="${c.a}">`:c.n[0]}</div><div><b>${c.n}</b> ${c.c}</div></div>`).join('')}</div><div class="ac"><input id="ci_${p.id}" placeholder="Комментарий..."><button onclick="acm('${p.id}')">➤</button></div></div>`}
function lik(pid){s.emit('lp',{pid:pid,n:u})}
function acm(pid){const i=document.getElementById('ci_'+pid);const t=i.value.trim();if(!t)return;s.emit('cmp',{pid:pid,n:u,c:t});i.value=''}
function share(){s.emit('sh')}
s.on('sl',d=>{const l='https://'+d.l;if(navigator.clipboard){navigator.clipboard.writeText(l).then(()=>alert('✅ Ссылка скопирована!'))}else{prompt('Ссылка:',l)}})
function vm(src,tp){document.getElementById('mvi').style.display=tp==='img'?'block':'none';document.getElementById('mvv').style.display=tp==='img'?'none':'block';if(tp==='img')document.getElementById('mvi').src=src;else document.getElementById('mvv').src=src;document.getElementById('mv').classList.add('sh')}
function clm(){document.getElementById('mv').classList.remove('sh')}
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'🚀 Shugramm запущен на порту {port}')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
