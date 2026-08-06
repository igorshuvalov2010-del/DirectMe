from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'shugramm'
socketio = SocketIO(app, cors_allowed_origins="*")

users = {}
groups = {'general': {'id': 'general', 'name': '💬 Общий чат Shugramm', 'members': set(), 'messages': []}}
pending = {}
colors = ['#FFD700', '#FFA500', '#FF8C00', '#FFB800', '#FFC800']

def gen():
    return ''.join([str(random.randint(0,9)) for _ in range(6)])

@app.route('/')
def index():
    return render_template_string(HTML)

@socketio.on('rc')
def rc(data):
    phone = ''.join(filter(str.isdigit, data['p']))
    if len(phone) < 10:
        emit('er', {'m': 'Введите корректный номер (минимум 10 цифр)'})
        return
    code = gen()
    pending[phone] = code
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def vc(data):
    if data['d'] not in pending or data['c'] != pending[data['d']]:
        emit('er', {'m': 'Неверный код'})
        return
    del pending[data['d']]
    emit('ok', {})

@socketio.on('reg')
def reg(data):
    n = data['n'].strip()
    if not n or len(n) < 2:
        emit('er', {'m': 'Имя должно содержать минимум 2 символа'})
        return
    if n in users:
        emit('er', {'m': 'Это имя уже занято'})
        return
    color = colors[len(users) % len(colors)]
    users[n] = {'s': request.sid, 'a': color, 'st': 'онлайн'}
    groups['general']['members'].add(n)
    join_room('general')
    emit('rok', {'n': n, 'a': color})

@socketio.on('sm')
def sm(data):
    m = {
        'i': f"m{time.time()}",
        'n': data['n'],
        't': data.get('t', 'text'),
        'ts': datetime.now().strftime("%H:%M"),
        'a': users[data['n']]['a']
    }
    if m['t'] == 'text':
        m['c'] = data['c'].strip()[:1000]
        if not m['c']: return
    elif m['t'] in ['img', 'vid']:
        m['c'] = data['c'][:80000]
    
    c = data.get('ch', 'general')
    if c in groups:
        groups[c]['messages'].append(m)
    emit('nm', {'ch': c, 'm': m}, room=c)

@socketio.on('jc')
def jc(data):
    join_room(data['ch'])
    ms = groups[data['ch']]['messages'][-50:] if data['ch'] in groups else []
    emit('ch', {'ch': data['ch'], 'ms': ms})

@socketio.on('gu')
def gu(data):
    au = [{'n': n, 'a': d['a'], 'st': d['st']} for n, d in users.items() if n != data['n']]
    emit('ul', {'u': au})

@socketio.on('sh')
def sh():
    emit('sl', {'l': f'https://{request.host}'})

HTML = r'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0d0d0d">
    <title>Shugramm</title>
    <style>
        :root{--b:#0d0d0d;--b2:#1a1a1a;--b3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff}
        *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
        body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#000;height:100vh;display:flex;justify-content:center;align-items:center;color:var(--w);user-select:none;overflow:hidden}
        .a{width:100%;max-width:480px;height:100vh;background:var(--b);display:flex;flex-direction:column}
        .h{background:var(--b2);padding:10px 14px;display:flex;align-items:center;border-bottom:1px solid #333;min-height:46px}
        .h span{color:var(--y);font-weight:800;font-size:17px;flex:1}
        .btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;padding:5px 10px}
        .nav{background:var(--b2);display:flex;border-top:1px solid #333;padding:8px 0;padding-bottom:max(8px,env(safe-area-inset-bottom))}
        .ni{flex:1;text-align:center;cursor:pointer;color:var(--g);font-size:11px;padding:5px}
        .ni.ac{color:var(--y)}
        .ct{flex:1;overflow-y:auto;padding:10px;display:none}
        .ct.ac{display:block}
        .ci{display:flex;align-items:center;padding:12px 10px;gap:10px;cursor:pointer;border-bottom:1px solid #222}
        .ci:active{background:var(--b3)}
        .av{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:#000;background:var(--y);flex-shrink:0}
        .mr{display:flex;gap:5px;margin-bottom:8px;animation:in .2s}
        @keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
        .mr.mi{flex-direction:row-reverse}
        .bb{max-width:75%;padding:8px 10px;border-radius:12px;font-size:14px;line-height:1.4;word-wrap:break-word;background:var(--b3)}
        .mr.mi .bb{background:#333;border:1px solid rgba(255,215,0,.2)}
        .bb img{max-width:200px;border-radius:8px;cursor:pointer;display:block}
        .mt{font-size:9px;color:var(--g);text-align:right;margin-top:2px}
        .ib{display:flex;padding:8px;background:var(--b2);border-top:1px solid #333;gap:6px;align-items:center}
        .ib input{flex:1;padding:10px 14px;background:#333;border:1px solid #444;border-radius:20px;color:var(--w);font-size:14px;outline:none}
        .ib input:focus{border-color:var(--y)}
        .sb{width:38px;height:38px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:18px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}
        .sb:active{background:#e6c200}
        .ls{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--b);display:flex;align-items:center;justify-content:center;z-index:100}
        .lb{text-align:center;padding:20px;width:90%;max-width:320px}
        .ll{width:70px;height:70px;background:var(--y);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:32px;color:#000;font-weight:900}
        .lb h1{font-size:22px;margin-bottom:4px}
        .lb .sub{color:var(--g);font-size:12px;margin-bottom:16px}
        .li{width:100%;padding:12px;background:var(--b2);border:1px solid #444;border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center}
        .li:focus{border-color:var(--y)}
        .lbtn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;margin-top:4px}
        .lbtn:active{background:#e6c200}
        .hd{display:none!important}
        .cs{background:var(--b3);padding:10px;border-radius:8px;font-size:22px;letter-spacing:6px;font-weight:700;color:var(--y);margin:10px 0}
        .mv{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.95);z-index:300;display:none;align-items:center;justify-content:center}
        .mv.sh{display:flex}
        .mv img{max-width:100%;max-height:100vh;object-fit:contain}
        .mc{position:absolute;top:16px;right:16px;width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);border:none;color:#fff;font-size:18px;cursor:pointer}
        .empty{text-align:center;padding:40px;color:var(--g)}
    </style>
</head>
<body>
    <div class="a">
        <div class="h"><span>⚡ Shugramm</span><button class="btn" onclick="sh()">🔗</button></div>
        <div class="ct ac" id="cc"></div>
        <div class="ct" id="uc"></div>
        <div id="cw" class="hd" style="flex:1;display:none;flex-direction:column">
            <div class="h"><button class="btn" onclick="cl()">←</button><span id="ct" style="font-weight:700">Чат</span></div>
            <div id="mc" style="flex:1;overflow-y:auto;padding:10px"></div>
            <div class="ib"><button class="btn" onclick="document.getElementById('fi').click()">📎</button><input type="text" id="mi" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sm()"><button class="sb" onclick="sm()">➤</button></div>
        </div>
        <div class="nav" id="nv" style="display:none">
            <div class="ni ac" onclick="st('c')">💬 Чаты</div>
            <div class="ni" onclick="st('u')">👥 Люди</div>
        </div>
        <div class="mv" id="mv" onclick="clm()"><button class="mc">✕</button><img id="mi2"></div>
        <div class="ls" id="ls">
            <div class="lb">
                <div id="s1"><div class="ll">⚡</div><h1>Shugramm</h1><p class="sub">Введите номер телефона</p><input type="tel" class="li" id="pi" placeholder="+79991234567"><button class="lbtn" onclick="rc()">Получить код</button></div>
                <div id="s2" class="hd"><div class="ll">🔐</div><h1>Подтверждение</h1><p class="sub">Код отправлен</p><div class="cs" id="cs"></div><input type="text" class="li" id="ci" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:5px" inputmode="numeric"><button class="lbtn" onclick="vc()">Подтвердить</button><button class="lbtn" onclick="bp()" style="margin-top:6px;background:var(--b3);color:var(--w)">← Назад</button></div>
                <div id="s3" class="hd"><div class="ll">✏️</div><h1>Профиль</h1><p class="sub">Придумайте имя</p><input type="text" class="li" id="ni" placeholder="Имя пользователя"><button class="lbtn" onclick="fr()">Войти в Shugramm</button></div>
            </div>
        </div>
    </div>
    <input type="file" id="fi" accept="image/*,video/*" style="display:none" onchange="hf(event)">
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script>
        const s=io();let u=null,ch='general',pd='';
        function rc(){const p=document.getElementById('pi').value.trim();if(p.length<10){alert('Введите номер телефона (минимум 10 цифр)');return}s.emit('rc',{p:p})}
        function vc(){const c=document.getElementById('ci').value.trim();if(c.length!==6){alert('Введите 6 цифр');return}s.emit('vc',{d:pd,c:c})}
        function fr(){const n=document.getElementById('ni').value.trim();if(n.length<2){alert('Минимум 2 символа');return}s.emit('reg',{n:n})}
        function bp(){document.getElementById('s2').classList.add('hd');document.getElementById('s1').classList.remove('hd')}
        s.on('cs',d=>{pd=d.d;document.getElementById('s1').classList.add('hd');document.getElementById('s2').classList.remove('hd');document.getElementById('cs').textContent=d.c})
        s.on('ok',()=>{document.getElementById('s2').classList.add('hd');document.getElementById('s3').classList.remove('hd')})
        s.on('rok',d=>{u=d.n;document.getElementById('ls').classList.add('hd');document.getElementById('nv').style.display='flex';lc()})
        s.on('er',d=>{alert('❌ '+d.m)})
        function lc(){document.getElementById('cc').innerHTML='<div class="ci" onclick="oc(\'general\',\'💬 Общий чат Shugramm\')"><div class="av">💬</div><div><b>Общий чат Shugramm</b><br><span style="color:var(--g);font-size:11px">Нажмите чтобы открыть</span></div></div>'}
        function st(t){document.querySelectorAll('.ct').forEach(c=>c.classList.remove('ac'));document.querySelectorAll('.ni').forEach(n=>n.classList.remove('ac'));if(t==='c'){document.getElementById('cc').classList.add('ac');document.querySelector('.ni:first-child').classList.add('ac')}else{document.getElementById('uc').classList.add('ac');document.querySelector('.ni:last-child').classList.add('ac');s.emit('gu',{n:u})}}
        function oc(id,nm){ch=id;document.getElementById('cc').classList.remove('ac');document.getElementById('cw').classList.remove('hd');document.getElementById('cw').style.display='flex';document.getElementById('ct').textContent=nm;document.getElementById('mc').innerHTML='';s.emit('jc',{n:u,ch:id})}
        function cl(){document.getElementById('cw').classList.add('hd');document.getElementById('cw').style.display='none';document.getElementById('cc').classList.add('ac')}
        function sm(){const i=document.getElementById('mi');const t=i.value.trim();if(!t)return;s.emit('sm',{n:u,ch:ch,t:'text',c:t});i.value=''}
        function hf(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':'img',c:ev.target.result})};r.readAsDataURL(f)}
        s.on('ch',d=>{document.getElementById('mc').innerHTML='';d.ms.forEach(m=>am(m));sB()})
        s.on('nm',d=>{if(d.ch===ch){am(d.m);sB()}})
        function am(m){const c=document.getElementById('mc');const im=m.n===u;const d=document.createElement('div');d.className='mr '+(im?'mi':'');let cnt=m.t==='img'?`<img src="${m.c}" onclick="vm('${m.c}')">`:m.t==='vid'?`<video src="${m.c}" controls style="max-width:200px;border-radius:8px"></video>`:m.c.replace(/</g,'&lt;');d.innerHTML=`<div class="av" style="width:28px;height:28px;font-size:10px;background:${m.a}">${m.n[0]}</div><div style="max-width:75%"><div class="bb">${cnt}</div><div class="mt">${m.ts}</div></div>`;c.appendChild(d)}
        function sB(){const c=document.getElementById('mc');setTimeout(()=>{c.scrollTop=c.scrollHeight},50)}
        s.on('ul',d=>{let h='';if(d.u.length===0){h='<div class="empty"><p style="font-size:40px">👥</p><p>Пока нет других пользователей</p><p style="font-size:12px;margin-top:8px">Пригласите друзей по ссылке!</p></div>'}else{d.u.forEach(u=>{h+=`<div class="ci"><div class="av" style="background:${u.a}">${u.n[0]}</div><div><b>${u.n}</b><br><span style="color:${u.st==='онлайн'?'#4CAF50':'var(--g)'};font-size:11px">${u.st}</span></div></div>`})}document.getElementById('uc').innerHTML=h})
        function sh(){s.emit('sh')}
        s.on('sl',d=>{const l=d.l;if(navigator.clipboard){navigator.clipboard.writeText(l).then(()=>alert('✅ Ссылка скопирована! Отправьте её друзьям.'))}else{prompt('Ссылка для приглашения (скопируйте):',l)}})
        function vm(src){document.getElementById('mi2').src=src;document.getElementById('mv').classList.add('sh')}
        function clm(){document.getElementById('mv').classList.remove('sh')}
    </script>
</body>
</html>'''

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
