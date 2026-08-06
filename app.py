from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import random, time, os, hashlib, json, re

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'shugramm-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=100*1024*1024)

# ========== ДАННЫЕ ==========
пользователи = {}
посты = []
группы = {'общий': {'id': 'общий', 'название': 'Общий чат', 'участники': set(), 'сообщения': []}}
приватные_чаты = {}
ожидание = {}
непрочитанные = {}
печатают = {}

def хеш_пароля(пароль):
    соль = os.urandom(32).hex()
    return соль + ':' + hashlib.sha256((соль + пароль).encode()).hexdigest()

def проверить_пароль(пароль, хеш):
    соль, значение = хеш.split(':')
    return значение == hashlib.sha256((соль + пароль).encode()).hexdigest()

def сгенерировать_токен():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]

def сохранить_данные():
    данные = {
        'пользователи': пользователи,
        'посты': посты,
        'группы': {k: {**v, 'участники': list(v['участники'])} for k, v in группы.items()},
        'приватные_чаты': приватные_чаты,
        'непрочитанные': непрочитанные
    }
    with open('shugramm_data.json', 'w') as f:
        json.dump(данные, f, ensure_ascii=False, indent=2)

def загрузить_данные():
    global пользователи, посты, группы, приватные_чаты, непрочитанные
    try:
        with open('shugramm_data.json', 'r') as f:
            данные = json.load(f)
            пользователи = данные.get('пользователи', {})
            посты = данные.get('посты', [])
            группы = {k: {**v, 'участники': set(v.get('участники', []))} for k, v in данные.get('группы', {}).items()}
            приватные_чаты = данные.get('приватные_чаты', {})
            непрочитанные = данные.get('непрочитанные', {})
    except:
        pass

загрузить_данные()

# ========== HTTP МАРШРУТЫ ==========
@app.route('/')
def главная():
    return render_template_string(HTML)

@app.route('/api/пользователь/<имя>')
def получить_профиль(имя):
    if имя not in пользователи:
        return jsonify({'ошибка': 'Пользователь не найден'}), 404
    пользователь = пользователи[имя]
    посты_пользователя = [p for p in посты if p['автор'] == имя]
    return jsonify({
        'имя': имя,
        'аватар': пользователь.get('аватар'),
        'описание': пользователь.get('описание', ''),
        'статус': пользователь.get('статус', 'оффлайн'),
        'посты': посты_пользователя[:20]
    })

@app.route('/удалить_пост', methods=['POST'])
def удалить_пост():
    данные = request.get_json()
    pid = данные.get('pid', '')
    имя = данные.get('n', '')
    global посты
    for i, p in enumerate(посты):
        if p['id'] == pid and p['автор'] == имя:
            посты.pop(i)
            сохранить_данные()
            break
    return {'ok': True}

# ========== SOCKET.IO ==========
@socketio.on('connect')
def подключение():
    print(f"✅ Клиент: {request.sid}")

@socketio.on('disconnect')
def отключение():
    for имя, пользователь in пользователи.items():
        if пользователь.get('sid') == request.sid:
            пользователь['статус'] = 'оффлайн'
            пользователь['sid'] = ''
            emit('статус_пользователя', {'имя': имя, 'статус': 'оффлайн'}, broadcast=True)
            сохранить_данные()
            break

@socketio.on('регистрация')
def регистрация(данные):
    телефон = ''.join(filter(str.isdigit, данные.get('телефон', '')))
    if len(телефон) < 10:
        emit('ошибка', {'сообщение': 'Введите корректный номер'})
        return
    код = str(random.randint(100000, 999999))
    ожидание[телефон] = {'код': код, 'время': time.time()}
    print(f"📱 Код {телефон}: {код}")
    emit('код_отправлен', {'телефон': телефон, 'код': код})

@socketio.on('проверить_код')
def проверить_код(данные):
    телефон = данные.get('телефон', '')
    код = данные.get('код', '')
    if телефон not in ожидание:
        emit('ошибка', {'сообщение': 'Сессия истекла'})
        return
    if time.time() - ожидание[телефон]['время'] > 300:
        del ожидание[телефон]
        emit('ошибка', {'сообщение': 'Код истек'})
        return
    if код != ожидание[телефон]['код']:
        emit('ошибка', {'сообщение': 'Неверный код'})
        return
    del ожидание[телефон]
    for имя, пользователь in пользователи.items():
        if пользователь.get('телефон') == телефон:
            emit('пользователь_существует', {'имя': имя})
            return
    emit('новый_пользователь', {'телефон': телефон})

@socketio.on('создать_пользователя')
def создать_пользователя(данные):
    телефон = данные.get('телефон', '')
    имя = данные.get('имя', '').strip()
    пароль = данные.get('пароль', '')
    
    if not имя or len(имя) < 2 or len(имя) > 20:
        emit('ошибка', {'сообщение': 'Имя 2-20 символов'})
        return
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_]+$', имя):
        emit('ошибка', {'сообщение': 'Недопустимые символы'})
        return
    if имя in пользователи:
        emit('ошибка', {'сообщение': 'Пользователь уже существует'})
        return
    if len(пароль) < 4:
        emit('ошибка', {'сообщение': 'Пароль минимум 4 символа'})
        return
    
    токен = сгенерировать_токен()
    пользователи[имя] = {
        'sid': request.sid,
        'телефон': телефон,
        'пароль': хеш_пароля(пароль),
        'аватар': None,
        'статус': 'онлайн',
        'описание': '',
        'токен': токен,
        'последний_визит': time.time()
    }
    группы['общий']['участники'].add(имя)
    непрочитанные[имя] = {}
    сохранить_данные()
    join_room('общий')
    emit('вход_успешен', {'имя': имя, 'токен': токен, 'аватар': None})
    emit('пользователь_вошел', {'имя': имя, 'аватар': None, 'статус': 'онлайн'}, broadcast=True)

@socketio.on('вход')
def вход(данные):
    имя = данные.get('имя', '').strip()
    пароль = данные.get('пароль', '')
    if имя not in пользователи:
        emit('ошибка', {'сообщение': 'Пользователь не найден'})
        return
    if not проверить_пароль(пароль, пользователи[имя]['пароль']):
        emit('ошибка', {'сообщение': 'Неверный пароль'})
        return
    токен = сгенерировать_токен()
    пользователи[имя]['sid'] = request.sid
    пользователи[имя]['статус'] = 'онлайн'
    пользователи[имя]['токен'] = токен
    пользователи[имя]['последний_визит'] = time.time()
    сохранить_данные()
    join_room('общий')
    emit('вход_успешен', {'имя': имя, 'токен': токен, 'аватар': пользователи[имя].get('аватар')})
    emit('пользователь_вошел', {'имя': имя, 'аватар': пользователи[имя].get('аватар'), 'статус': 'онлайн'}, broadcast=True)

@socketio.on('авто_вход')
def авто_вход(данные):
    токен = данные.get('токен', '')
    for имя, пользователь in пользователи.items():
        if пользователь.get('токен') == токен:
            пользователь['sid'] = request.sid
            пользователь['статус'] = 'онлайн'
            пользователь['последний_визит'] = time.time()
            сохранить_данные()
            join_room('общий')
            emit('вход_успешен', {'имя': имя, 'токен': токен, 'аватар': пользователь.get('аватар')})
            emit('пользователь_вошел', {'имя': имя, 'аватар': пользователь.get('аватар'), 'статус': 'онлайн'}, broadcast=True)
            return

@socketio.on('отправить_сообщение')
def отправить_сообщение(данные):
    имя = данные.get('имя', '')
    чат = данные.get('чат', 'общий')
    тип = данные.get('тип', 'текст')
    содержимое = данные.get('содержимое', '')
    
    if имя not in пользователи:
        return
    
    if тип == 'текст':
        содержимое = содержимое[:2000]
    elif тип in ['изображение', 'видео']:
        содержимое = содержимое[:150000]
    
    сообщение = {
        'id': f"m{int(time.time()*1000)}",
        'имя': имя,
        'тип': тип,
        'содержимое': содержимое,
        'время': datetime.now().strftime("%H:%M"),
        'аватар': пользователи[имя].get('аватар'),
        'время_отправки': time.time()
    }
    
    if чат in группы:
        группы[чат]['сообщения'].append(сообщение)
        if len(группы[чат]['сообщения']) > 200:
            группы[чат]['сообщения'] = группы[чат]['сообщения'][-100:]
    elif чат in приватные_чаты:
        приватные_чаты[чат]['сообщения'].append(сообщение)
        if len(приватные_чаты[чат]['сообщения']) > 200:
            приватные_чаты[чат]['сообщения'] = приватные_чаты[чат]['сообщения'][-100:]
    
    сохранить_данные()
    emit('новое_сообщение', {'чат': чат, 'сообщение': сообщение}, room=чат)
    
    if чат in приватные_чаты:
        for участник in приватные_чаты[чат]['участники']:
            if участник != имя:
                непрочитанные.setdefault(участник, {})
                непрочитанные[участник][чат] = непрочитанные[участник].get(чат, 0) + 1
                if пользователи.get(участник, {}).get('sid'):
                    emit('обновить_непрочитанные', {'чат': чат, 'количество': непрочитанные[участник][чат]}, room=пользователи[участник]['sid'])
    else:
        for участник in группы[чат]['участники']:
            if участник != имя:
                непрочитанные.setdefault(участник, {})
                непрочитанные[участник][чат] = непрочитанные[участник].get(чат, 0) + 1
                if пользователи.get(участник, {}).get('sid'):
                    emit('обновить_непрочитанные', {'чат': чат, 'количество': непрочитанные[участник][чат]}, room=пользователи[участник]['sid'])

@socketio.on('присоединиться_к_чату')
def присоединиться_к_чату(данные):
    чат = данные.get('чат', 'общий')
    имя = данные.get('имя', '')
    if имя not in пользователи:
        return
    join_room(чат)
    if имя in непрочитанные:
        непрочитанные[имя][чат] = 0
    сообщения = группы.get(чат, {}).get('сообщения', [])[-100:] if чат in группы else приватные_чаты.get(чат, {}).get('сообщения', [])[-100:]
    emit('история_чата', {'сообщения': сообщения, 'чат': чат})

@socketio.on('печатает')
def печатает(данные):
    чат = данные.get('чат', 'общий')
    имя = данные.get('имя', '')
    печатает = данные.get('печатает', False)
    печатают[чат] = печатают.get(чат, {})
    if печатает:
        печатают[чат][имя] = time.time()
    else:
        печатают[чат].pop(имя, None)
    emit('статус_печатает', {'имя': имя, 'печатает': печатает}, room=чат, include_self=False)

@socketio.on('получить_пользователей')
def получить_пользователей(данные):
    имя = данные.get('имя', '')
    список = []
    for n, u in пользователи.items():
        if n != имя:
            список.append({
                'имя': n,
                'аватар': u.get('аватар'),
                'статус': u.get('статус', 'оффлайн'),
                'описание': u.get('описание', '')
            })
    emit('список_пользователей', {'пользователи': список})

@socketio.on('начать_приватный_чат')
def начать_приватный_чат(данные):
    пользователь1 = данные.get('пользователь1', '')
    пользователь2 = данные.get('пользователь2', '')
    if пользователь1 not in пользователи or пользователь2 not in пользователи:
        return
    id_чата = f"p_{min(пользователь1, пользователь2)}_{max(пользователь1, пользователь2)}"
    if id_чата not in приватные_чаты:
        приватные_чаты[id_чата] = {'участники': [пользователь1, пользователь2], 'сообщения': []}
        сохранить_данные()
    join_room(id_чата)
    if пользователь1 in непрочитанные:
        непрочитанные[пользователь1][id_чата] = 0
    сообщения = приватные_чаты[id_чата]['сообщения'][-100:]
    emit('приватный_чат', {
        'id_чата': id_чата,
        'пользователь': пользователь2,
        'аватар': пользователи[пользователь2].get('аватар'),
        'сообщения': сообщения
    })

@socketio.on('обновить_аватар')
def обновить_аватар(данные):
    имя = данные.get('имя', '')
    аватар = данные.get('аватар', '')
    if имя in пользователи:
        пользователи[имя]['аватар'] = аватар
        сохранить_данные()
        emit('аватар_обновлен', {'имя': имя, 'аватар': аватар}, broadcast=True)

@socketio.on('обновить_описание')
def обновить_описание(данные):
    имя = данные.get('имя', '')
    описание = данные.get('описание', '')[:200]
    if имя in пользователи:
        пользователи[имя]['описание'] = описание
        сохранить_данные()
        emit('описание_обновлено', {'имя': имя, 'описание': описание})

@socketio.on('создать_пост')
def создать_пост(данные):
    имя = данные.get('имя', '')
    содержимое = данные.get('содержимое', '')
    тип_медиа = данные.get('тип_медиа', 'изображение')
    описание = данные.get('описание', '')[:500]
    
    if имя not in пользователи:
        return
    if len(содержимое) > 500000:
        содержимое = содержимое[:500000]
    
    пост = {
        'id': f"p{len(посты)}_{int(time.time()*1000)}",
        'автор': имя,
        'аватар': пользователи[имя].get('аватар'),
        'содержимое': содержимое,
        'тип_медиа': тип_медиа,
        'описание': описание,
        'лайки': [],
        'комментарии': [],
        'время': datetime.now().strftime("%d.%m.%Y %H:%M"),
        'время_отправки': time.time()
    }
    посты.insert(0, пост)
    if len(посты) > 50:
        посты.pop()
    сохранить_данные()
    emit('новый_пост', {'пост': пост}, broadcast=True)

@socketio.on('получить_посты')
def получить_посты():
    emit('список_постов', {'посты': посты[:30]})

@socketio.on('лайкнуть_пост')
def лайкнуть_пост(данные):
    id_поста = данные.get('id_поста', '')
    имя = данные.get('имя', '')
    for p in посты:
        if p['id'] == id_поста:
            if имя in p['лайки']:
                p['лайки'].remove(имя)
            else:
                p['лайки'].append(имя)
            сохранить_данные()
            emit('пост_обновлен', {'пост': p}, broadcast=True)
            break

@socketio.on('комментировать_пост')
def комментировать_пост(данные):
    id_поста = данные.get('id_поста', '')
    имя = данные.get('имя', '')
    комментарий = данные.get('комментарий', '')[:300]
    for p in посты:
        if p['id'] == id_поста:
            p['комментарии'].append({
                'имя': имя,
                'аватар': пользователи.get(имя, {}).get('аватар'),
                'комментарий': комментарий,
                'время': datetime.now().strftime("%H:%M")
            })
            сохранить_данные()
            emit('пост_обновлен', {'пост': p}, broadcast=True)
            break

@socketio.on('выход')
def выход(данные):
    токен = данные.get('токен', '')
    for имя, пользователь in пользователи.items():
        if пользователь.get('токен') == токен:
            пользователь['токен'] = ''
            пользователь['статус'] = 'оффлайн'
            пользователь['sid'] = ''
            сохранить_данные()
            emit('статус_пользователя', {'имя': имя, 'статус': 'оффлайн'}, broadcast=True)
            break

@socketio.on('удалить_сообщение')
def удалить_сообщение(данные):
    чат = данные.get('чат', '')
    id_сообщения = данные.get('id_сообщения', '')
    имя = данные.get('имя', '')
    
    if чат in группы:
        сообщения = группы[чат]['сообщения']
        for i, m in enumerate(сообщения):
            if m['id'] == id_сообщения and m['имя'] == имя:
                сообщения.pop(i)
                сохранить_данные()
                emit('сообщение_удалено', {'чат': чат, 'id_сообщения': id_сообщения}, room=чат)
                break
    elif чат in приватные_чаты:
        сообщения = приватные_чаты[чат]['сообщения']
        for i, m in enumerate(сообщения):
            if m['id'] == id_сообщения and m['имя'] == имя:
                сообщения.pop(i)
                сохранить_данные()
                emit('сообщение_удалено', {'чат': чат, 'id_сообщения': id_сообщения}, room=чат)
                break

@socketio.on('редактировать_сообщение')
def редактировать_сообщение(данные):
    чат = данные.get('чат', '')
    id_сообщения = данные.get('id_сообщения', '')
    имя = данные.get('имя', '')
    новое_содержимое = данные.get('содержимое', '')[:2000]
    
    if чат in группы:
        for m in группы[чат]['сообщения']:
            if m['id'] == id_сообщения and m['имя'] == имя:
                m['содержимое'] = новое_содержимое
                m['отредактировано'] = True
                сохранить_данные()
                emit('сообщение_отредактировано', {'чат': чат, 'сообщение': m}, room=чат)
                break
    elif чат in приватные_чаты:
        for m in приватные_чаты[чат]['сообщения']:
            if m['id'] == id_сообщения and m['имя'] == имя:
                m['содержимое'] = новое_содержимое
                m['отредактировано'] = True
                сохранить_данные()
                emit('сообщение_отредактировано', {'чат': чат, 'сообщение': m}, room=чат)
                break

@socketio.on('поделиться_ссылкой')
def поделиться_ссылкой():
    emit('поделиться_ссылкой', {'url': request.host})

# ========== HTML ==========
HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Shugramm</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#0a0a0a;--bg2:#141414;--bg3:#1e1e1e;--bg4:#2a2a2a;--gold:#FFD700;--text:#fff;--text-secondary:#888;--border:#2a2a2a}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden;display:flex;justify-content:center;align-items:center}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--gold);border-radius:4px}
#app{width:100%;max-width:480px;height:100vh;background:var(--bg);display:flex;flex-direction:column;position:relative;overflow:hidden}
.header{background:var(--bg2);padding:8px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);flex-shrink:0;min-height:48px;z-index:10}
.header-title{font-size:17px;font-weight:600;color:var(--gold)}
.btn{background:none;border:none;color:var(--text-secondary);padding:6px;border-radius:50%;cursor:pointer;width:34px;height:34px;display:flex;align-items:center;justify-content:center;transition:all .2s}
.btn:active{background:var(--bg3);transform:scale(.9)}
.nav{background:var(--bg2);display:flex;border-top:1px solid var(--border);flex-shrink:0;padding-bottom:env(safe-area-inset-bottom)}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 0 8px;cursor:pointer;color:var(--text-secondary);font-size:9px;transition:color .2s;position:relative;background:none;border:none}
.nav-item.active{color:var(--gold)}
.nav-item .icon{font-size:20px}
.nav-item .badge{position:absolute;top:2px;right:50%;transform:translateX(200%);background:#ff4444;color:#fff;font-size:9px;font-weight:700;min-width:16px;height:16px;border-radius:8px;display:flex;align-items:center;justify-content:center;padding:0 4px}
.page{flex:1;overflow-y:auto;display:none;-webkit-overflow-scrolling:touch;padding-bottom:4px}
.page.active{display:block}
.fab{position:fixed;bottom:80px;right:16px;width:48px;height:48px;border-radius:50%;background:var(--gold);color:#000;border:none;font-size:24px;cursor:pointer;z-index:20;display:none;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(255,215,0,.3);transition:transform .2s}
.fab.show{display:flex}
.fab:active{transform:scale(.9)}
.chat-item{display:flex;align-items:center;padding:10px 16px;gap:12px;cursor:pointer;transition:background .15s;border-bottom:1px solid rgba(255,255,255,.03)}
.chat-item:active{background:var(--bg3)}
.chat-avatar{width:48px;height:48px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:600;color:#000;background:var(--gold);overflow:hidden;position:relative}
.chat-avatar img{width:100%;height:100%;object-fit:cover}
.chat-avatar .online-dot{position:absolute;bottom:1px;right:1px;width:10px;height:10px;border-radius:50%;border:2px solid var(--bg2);background:#4CAF50}
.chat-info{flex:1;min-width:0}
.chat-name{font-size:15px;font-weight:500;display:flex;align-items:center;gap:6px}
.chat-name .unread-badge{display:inline-block;background:var(--gold);color:#000;font-size:10px;font-weight:700;min-width:18px;height:18px;border-radius:9px;text-align:center;line-height:18px;padding:0 5px}
.chat-last{font-size:13px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-unread{background:var(--gold);color:#000;font-size:11px;font-weight:700;min-width:20px;height:20px;border-radius:10px;display:flex;align-items:center;justify-content:center;padding:0 6px}
#chatWindow{display:none;flex:1;flex-direction:column;min-height:0}
#chatWindow.open{display:flex}
.messages-container{flex:1;overflow-y:auto;padding:8px 12px;-webkit-overflow-scrolling:touch;display:flex;flex-direction:column}
.msg{display:flex;gap:6px;margin-bottom:4px;max-width:85%;animation:fadeIn .2s ease}
.msg.self{align-self:flex-end;flex-direction:row-reverse}
.msg-avatar{width:28px;height:28px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#000;background:var(--gold);overflow:hidden;margin-top:auto;cursor:pointer}
.msg-avatar img{width:100%;height:100%;object-fit:cover}
.msg-avatar:hover{opacity:.8}
.msg-bubble{padding:7px 12px;border-radius:14px;font-size:14px;line-height:1.4;word-wrap:break-word;background:var(--bg3);max-width:100%}
.msg.self .msg-bubble{background:var(--gold);color:#000}
.msg-bubble img{max-width:200px;border-radius:8px;display:block;cursor:pointer}
.msg-bubble video{max-width:200px;border-radius:8px;display:block}
.msg-bubble .edited{font-size:9px;color:var(--text-secondary);opacity:.6;margin-left:4px}
.msg-time{font-size:9px;color:var(--text-secondary);text-align:right;margin-top:2px}
.msg.self .msg-time{color:rgba(0,0,0,.5)}
.msg-actions{display:flex;gap:2px;margin-top:2px;justify-content:flex-end}
.msg-actions button{background:none;border:none;color:var(--text-secondary);font-size:10px;cursor:pointer;padding:2px 4px;border-radius:4px}
.msg-actions button:active{background:var(--bg4)}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.typing-indicator{font-size:12px;color:var(--text-secondary);padding:2px 14px 6px;font-style:italic;min-height:22px;opacity:0;transition:opacity .3s}
.typing-indicator.show{opacity:1}
.input-bar{display:flex;padding:6px 10px;background:var(--bg2);border-top:1px solid var(--border);gap:6px;align-items:center;flex-shrink:0}
.input-bar input{flex:1;padding:8px 14px;background:var(--bg3);border:1px solid var(--border);border-radius:18px;color:var(--text);font-size:14px;outline:none;transition:border .3s}
.input-bar input:focus{border-color:var(--gold)}
.input-bar input::placeholder{color:var(--text-secondary)}
.send-btn{background:var(--gold);color:#000;border:none;width:34px;height:34px;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .2s;flex-shrink:0}
.send-btn:active{transform:scale(.9)}
.post-card{background:var(--bg2);margin:8px 12px;border-radius:12px;overflow:hidden;border:1px solid var(--border)}
.post-header{display:flex;align-items:center;padding:10px 14px;gap:10px}
.post-avatar{width:36px;height:36px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;color:#000;background:var(--gold);overflow:hidden;cursor:pointer}
.post-avatar img{width:100%;height:100%;object-fit:cover}
.post-author{font-weight:500;font-size:14px;cursor:pointer}
.post-author:hover{color:var(--gold)}
.post-time{font-size:11px;color:var(--text-secondary)}
.post-media{width:100%;max-height:400px;object-fit:cover;cursor:pointer}
.post-caption{padding:8px 14px;font-size:13px;line-height:1.4}
.post-actions{display:flex;padding:6px 14px 10px;gap:16px;border-top:1px solid var(--border)}
.post-action{background:none;border:none;color:var(--text-secondary);cursor:pointer;display:flex;align-items:center;gap:4px;font-size:13px;padding:2px 6px;border-radius:6px;transition:all .2s}
.post-action:active{transform:scale(.9)}
.post-action.liked{color:#ff4444}
.post-action .count{font-size:12px}
.post-comments{padding:0 14px 8px}
.post-comment{display:flex;gap:6px;margin-bottom:4px;font-size:12px}
.post-comment-avatar{width:20px;height:20px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:600;color:#000;background:var(--gold);overflow:hidden}
.post-comment-avatar img{width:100%;height:100%;object-fit:cover}
.post-comment-text{line-height:1.3}
.post-comment-text b{margin-right:4px}
.comment-input{display:flex;padding:6px 14px 10px;gap:8px;border-top:1px solid var(--border)}
.comment-input input{flex:1;background:var(--bg3);border:none;border-radius:12px;padding:6px 12px;color:var(--text);font-size:12px;outline:none}
.comment-input input::placeholder{color:var(--text-secondary)}
.comment-input button{background:var(--gold);color:#000;border:none;padding:4px 14px;border-radius:12px;font-weight:600;cursor:pointer;font-size:12px}
.profile-section{text-align:center;padding:24px;background:var(--bg2);margin:12px;border-radius:12px}
.profile-avatar{width:80px;height:80px;border-radius:50%;margin:0 auto 10px;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:600;color:#000;background:var(--gold);cursor:pointer;overflow:hidden;border:3px solid var(--gold);transition:transform .2s}
.profile-avatar:active{transform:scale(.95)}
.profile-avatar img{width:100%;height:100%;object-fit:cover}
.profile-name{font-size:20px;font-weight:600}
.profile-bio{color:var(--text-secondary);font-size:13px;margin-top:4px}
.profile-status{font-size:12px;margin-top:2px;color:#4CAF50}
.settings-group{padding:0 12px 12px}
.setting-item{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;background:var(--bg2);margin-bottom:6px;border-radius:10px;cursor:pointer;transition:background .15s}
.setting-item:active{background:var(--bg3)}
.setting-label{font-size:14px}
.setting-value{color:var(--text-secondary);font-size:13px}
.profile-modal{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.85);z-index:100;display:none;align-items:center;justify-content:center;padding:20px}
.profile-modal.open{display:flex}
.profile-modal-content{background:var(--bg2);border-radius:16px;max-width:400px;width:100%;max-height:90vh;overflow-y:auto;padding:24px;animation:fadeIn .3s ease}
.profile-modal-close{float:right;background:none;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;padding:4px 8px}
.profile-modal-avatar{width:80px;height:80px;border-radius:50%;margin:0 auto 12px;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:600;color:#000;background:var(--gold);overflow:hidden}
.profile-modal-avatar img{width:100%;height:100%;object-fit:cover}
.profile-modal-name{text-align:center;font-size:20px;font-weight:600}
.profile-modal-bio{text-align:center;color:var(--text-secondary);font-size:13px;margin-top:4px}
.profile-modal-status{text-align:center;font-size:12px;margin-top:2px}
.profile-modal-status.online{color:#4CAF50}
.profile-modal-status.offline{color:var(--text-secondary)}
.profile-modal-posts{margin-top:16px;border-top:1px solid var(--border);padding-top:12px}
.profile-modal-posts-title{font-size:13px;color:var(--text-secondary);margin-bottom:8px}
.profile-modal-post{background:var(--bg3);border-radius:8px;padding:8px 12px;margin-bottom:6px;font-size:12px;color:var(--text-secondary);cursor:pointer}
.profile-modal-post:hover{background:var(--bg4)}
.profile-modal-post .p-time{color:var(--text-secondary);font-size:10px;float:right}
.profile-modal-post .p-caption{color:var(--text);display:block;margin-top:2px}
.profile-modal-btn{width:100%;padding:10px;background:var(--gold);color:#000;border:none;border-radius:10px;font-weight:600;cursor:pointer;margin-top:12px;transition:opacity .2s}
.profile-modal-btn:active{opacity:.8}
.login-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:200}
.login-card{text-align:center;padding:32px 24px;width:90%;max-width:340px}
.login-logo{font-size:48px;margin-bottom:12px}
.login-card h1{font-size:24px;font-weight:700;color:var(--gold)}
.login-card p{color:var(--text-secondary);font-size:13px;margin:4px 0 20px}
.form-input{width:100%;padding:12px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;margin-bottom:8px;outline:none;text-align:center;transition:border .3s}
.form-input:focus{border-color:var(--gold)}
.form-btn{width:100%;padding:12px;background:var(--gold);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .2s}
.form-btn:active{opacity:.8}
.form-link{background:none;border:none;color:var(--gold);font-size:13px;cursor:pointer;margin-top:10px}
.code-box{background:var(--bg3);padding:12px;border-radius:8px;font-size:26px;letter-spacing:8px;font-weight:600;color:var(--gold);margin:10px 0;font-family:monospace}
.hidden{display:none!important}
.media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.95);z-index:300;display:none;align-items:center;justify-content:center;padding:20px}
.media-viewer.open{display:flex}
.media-viewer img{max-width:100%;max-height:80vh;object-fit:contain}
.media-viewer video{max-width:100%;max-height:80vh}
.media-close{position:absolute;top:16px;right:16px;width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.1);border:none;color:#fff;font-size:20px;cursor:pointer}
.notification{position:fixed;top:0;left:50%;transform:translateX(-50%);background:var(--bg2);color:var(--text);padding:10px 20px;border-radius:0 0 12px 12px;font-size:13px;max-width:90%;text-align:center;z-index:50;display:none;border-bottom:3px solid var(--gold);box-shadow:0 4px 20px rgba(0,0,0,.6)}
.notification.show{display:block;animation:slideDown .3s ease}
@keyframes slideDown{from{transform:translateX(-50%) translateY(-100%)}to{transform:translateX(-50%) translateY(0)}}
.empty-state{text-align:center;padding:40px 20px;color:var(--text-secondary)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.empty-state h3{color:var(--text);margin-bottom:4px}
</style>
</head>
<body>
<div class="notification" id="notification"></div>
<div id="app">
<div class="header">
<div class="header-title">⚡ Shugramm</div>
<button class="btn" onclick="поделиться()">📤</button>
</div>
<div class="page active" id="pageChats"><div id="chatList"></div></div>
<div class="page" id="pageUsers"><div style="padding:8px 12px;position:sticky;top:0;background:var(--bg);z-index:5"><input class="form-input" id="searchUsers" placeholder="🔍 Поиск..." oninput="поискПользователей()" style="text-align:left"></div><div id="usersList"></div></div>
<div class="page" id="pagePosts"><div id="postsList"></div></div>
<div class="page" id="pageSettings"><div id="settingsContent"></div></div>
<div id="chatWindow">
<div class="header" style="border-bottom:1px solid var(--border);flex-shrink:0">
<button class="btn" onclick="закрытьЧат()">←</button>
<span style="font-weight:500;flex:1;font-size:15px" id="chatTitle">Чат</span>
<button class="btn" onclick="удалитьЧат()">🗑</button>
</div>
<div class="messages-container" id="messagesContainer"></div>
<div class="typing-indicator" id="typingIndicator"></div>
<div class="input-bar">
<button class="btn" onclick="document.getElementById('fileInput').click()">📎</button>
<input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')отправитьСообщение()" oninput="печатает()">
<button class="send-btn" onclick="отправитьСообщение()">➤</button>
</div>
</div>
<div class="nav" id="nav" style="display:none">
<div class="nav-item active" onclick="переключитьВкладку('чаты')"><span class="icon">💬</span><span class="label">Чаты</span><span class="badge" id="totalBadge" style="display:none">0</span></div>
<div class="nav-item" onclick="переключитьВкладку('люди')"><span class="icon">👤</span><span class="label">Люди</span></div>
<div class="nav-item" onclick="переключитьВкладку('посты')"><span class="icon">📸</span><span class="label">Посты</span></div>
<div class="nav-item" onclick="переключитьВкладку('настройки')"><span class="icon">⚙️</span><span class="label">Настройки</span></div>
</div>
<button class="fab" id="fab" onclick="создатьПост()">+</button>
</div>
<div class="profile-modal" id="profileModal"><div class="profile-modal-content"><button class="profile-modal-close" onclick="закрытьПрофиль()">✕</button><div id="profileContent"></div></div></div>
<div class="media-viewer" id="mediaViewer"><button class="media-close" onclick="закрытьМедиа()">✕</button><img id="mediaImg" style="display:none"><video id="mediaVideo" controls style="display:none"></video></div>
<input type="file" id="fileInput" accept="image/*,video/*" style="display:none" onchange="обработатьФайл(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="обработатьАватар(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="обработатьПост(event)">
<div class="login-screen" id="loginScreen">
<div class="login-card">
<div id="loginStep1"><div class="login-logo">⚡</div><h1>Shugramm</h1><p>Введите номер телефона</p><input class="form-input" id="phoneInput" placeholder="+7 999 123-45-67" type="tel"><button class="form-btn" onclick="запроситьКод()">Получить код</button></div>
<div id="loginStep2" class="hidden"><div class="login-logo">⚡</div><h1>Код</h1><p>Отправлен на <span id="phoneDisplay" style="color:var(--gold)"></span></p><div class="code-box" id="codeDisplay">000000</div><input class="form-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:6px"><button class="form-btn" onclick="проверитьКод()">Подтвердить</button><button class="form-link" onclick="назадКТелефону()">Изменить номер</button></div>
<div id="loginStep3" class="hidden"><div class="login-logo">⚡</div><h1>Регистрация</h1><input class="form-input" id="regPassword" placeholder="Пароль (мин. 4)" type="password"><input class="form-input" id="regName" placeholder="Имя (2-20 символов)"><button class="form-btn" onclick="зарегистрироваться()">Зарегистрироваться</button></div>
<div id="loginStep4" class="hidden"><div class="login-logo">⚡</div><h1>Вход</h1><p id="loginName" style="color:var(--gold);font-weight:600"></p><input class="form-input" id="loginPassword" placeholder="Пароль" type="password"><button class="form-btn" onclick="войти()">Войти</button><button class="form-link" onclick="назадКСтарту()">Назад</button></div>
</div>
</div>
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
const socket=io();
let текущийПользователь=null,текущийТокен=null,текущийЧат='общий',текущееНазваниеЧата='Общий чат';
let текущийАватар=null,текущееОписание='',таймаутПечатания=null,чатОткрыт=false;
let непрочитанныеДанные={},приватныеЧаты=JSON.parse(localStorage.getItem('приватные_чаты')||'[]');
const $=id=>document.getElementById(id);
const уведомление=$('notification');
function показатьУведомление(msg){уведомление.textContent=msg;уведомление.classList.add('show');clearTimeout(уведомление._таймаут);уведомление._таймаут=setTimeout(()=>уведомление.classList.remove('show'),3000);}
function показатьТост(msg){const el=document.createElement('div');el.style.cssText='position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--bg2);padding:10px 20px;border-radius:10px;font-size:13px;z-index:60;border-left:3px solid var(--gold);box-shadow:0 4px 20px rgba(0,0,0,.6);animation:fadeIn .3s ease;max-width:90%';el.textContent=msg;document.body.appendChild(el);setTimeout(()=>el.remove(),2500);}
function запроситьКод(){const p=$('phoneInput').value.trim();if(p.length<10){показатьУведомление('Введите корректный номер');return}socket.emit('регистрация',{телефон:p});}
function проверитьКод(){const c=$('codeInput').value.trim();if(c.length!==6){показатьУведомление('Введите 6 цифр');return}socket.emit('проверить_код',{телефон:текущийТелефон,код:c});}
let текущийТелефон='';
function зарегистрироваться(){const имя=$('regName').value.trim(),пароль=$('regPassword').value.trim();if(!имя||имя.length<2||имя.length>20){показатьУведомление('Имя 2-20 символов');return}if(!/^[a-zA-Zа-яА-Я0-9_]+$/.test(имя)){показатьУведомление('Недопустимые символы');return}if(пароль.length<4){показатьУведомление('Пароль минимум 4 символа');return}socket.emit('создать_пользователя',{телефон:текущийТелефон,имя,пароль});}
function войти(){const p=$('loginPassword').value.trim();if(!p){показатьУведомление('Введите пароль');return}socket.emit('вход',{имя:текущееИмяВхода,пароль:p});}
let текущееИмяВхода='';
function назадКТелефону(){$('loginStep2').classList.add('hidden');$('loginStep1').classList.remove('hidden');}
function назадКСтарту(){$('loginStep4').classList.add('hidden');$('loginStep1').classList.remove('hidden');}
socket.on('код_отправлен',(d)=>{текущийТелефон=d.телефон;$('loginStep1').classList.add('hidden');$('loginStep2').classList.remove('hidden');$('phoneDisplay').textContent='+'+d.телефон;$('codeDisplay').textContent=d.код;});
socket.on('пользователь_существует',(d)=>{текущееИмяВхода=d.имя;$('loginStep2').classList.add('hidden');$('loginStep4').classList.remove('hidden');$('loginName').textContent=d.имя;});
socket.on('новый_пользователь',(d)=>{текущийТелефон=d.телефон;$('loginStep2').classList.add('hidden');$('loginStep3').classList.remove('hidden');});
socket.on('вход_успешен',(d)=>{текущийПользователь=d.имя;текущийТокен=d.токен;текущийАватар=d.аватар;localStorage.setItem('shugramm_токен',d.токен);localStorage.setItem('shugramm_пользователь',d.имя);войтиВПриложение();});
socket.on('ошибка',(d)=>{показатьУведомление(d.сообщение);});
socket.on('пользователь_вошел',()=>{отобразитьЧаты();отобразитьПользователей();});
socket.on('статус_пользователя',()=>{отобразитьЧаты();отобразитьПользователей();});
socket.on('новое_сообщение',(d)=>{if(d.чат===текущийЧат&&чатОткрыт){отобразитьСообщение(d.сообщение);скроллВниз();}if(d.чат!==текущийЧат||!чатОткрыт){непрочитанныеДанные[d.чат]=(непрочитанныеДанные[d.чат]||0)+1;обновитьБейдж();}отобразитьЧаты();});
socket.on('история_чата',(d)=>{$('messagesContainer').innerHTML='';if(d.сообщения)d.сообщения.forEach(m=>отобразитьСообщение(m));скроллВниз();});
socket.on('статус_печатает',(d)=>{if(d.печатает){$('typingIndicator').textContent=d.имя+' печатает...';$('typingIndicator').classList.add('show');}else{$('typingIndicator').classList.remove('show');}});
socket.on('список_пользователей',(d)=>{отобразитьСписокПользователей(d.пользователи);});
socket.on('приватный_чат',(d)=>{открытьПриватныйЧат(d.id_чата,d.пользователь,d.аватар,d.сообщения);});
socket.on('аватар_обновлен',(d)=>{if(d.имя===текущийПользователь)текущийАватар=d.аватар;отобразитьЧаты();отобразитьПользователей();});
socket.on('описание_обновлено',(d)=>{if(d.имя===текущийПользователь){текущееОписание=d.описание;отобразитьНастройки();}});
socket.on('новый_пост',(d)=>{if($('pagePosts').classList.contains('active')){$('postsList').insertAdjacentHTML('afterbegin',отобразитьПост(d.пост));}});
socket.on('список_постов',(d)=>{$('postsList').innerHTML=d.посты.length?d.посты.map(p=>отобразитьПост(p)).join(''):'<div class="empty-state"><div class="icon">📸</div><h3>Нет постов</h3><p>Создайте свой первый пост!</p></div>';});
socket.on('пост_обновлен',(d)=>{const el=document.getElementById('post-'+d.пост.id);if(el)el.outerHTML=отобразитьПост(d.пост);});
socket.on('сообщение_удалено',(d)=>{if(d.чат===текущийЧат){const el=document.querySelector('[data-msg-id="'+d.id_сообщения+'"]');if(el)el.remove();}});
socket.on('сообщение_отредактировано',(d)=>{if(d.чат===текущийЧат){const el=document.querySelector('[data-msg-id="'+d.сообщение.id+'"]');if(el){const b=el.querySelector('.msg-bubble');if(b)b.innerHTML=d.сообщение.содержимое+'<span class="edited">✎</span>';}}});
socket.on('поделиться_ссылкой',(d)=>{const url='https://'+d.url;if(navigator.clipboard){navigator.clipboard.writeText(url).then(()=>показатьТост('Ссылка скопирована!'));}else{prompt('Ссылка:',url);}});
socket.on('обновить_непрочитанные',(d)=>{непрочитанныеДанные[d.чат]=d.количество;отобразитьЧаты();обновитьБейдж();});
const сохраненныйТокен=localStorage.getItem('shugramm_токен');
if(сохраненныйТокен)socket.emit('авто_вход',{токен:сохраненныйТокен});
function войтиВПриложение(){$('loginScreen').classList.add('hidden');$('nav').style.display='flex';загрузитьДанные();отобразитьЧаты();отобразитьПользователей();отобразитьНастройки();socket.emit('получить_посты');}
function загрузитьДанные(){приватныеЧаты=JSON.parse(localStorage.getItem('приватные_чаты')||'[]');}
function переключитьВкладку(страница){
document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
$('fab').classList.remove('show');
if(страница==='чаты'){$('pageChats').classList.add('active');document.querySelector('.nav-item:nth-child(1)').classList.add('active');отобразитьЧаты();}
else if(страница==='люди'){$('pageUsers').classList.add('active');document.querySelector('.nav-item:nth-child(2)').classList.add('active');socket.emit('получить_пользователей',{имя:текущийПользователь});}
else if(страница==='посты'){$('pagePosts').classList.add('active');document.querySelector('.nav-item:nth-child(3)').classList.add('active');$('fab').classList.add('show');socket.emit('получить_посты');}
else{$('pageSettings').classList.add('active');document.querySelector('.nav-item:nth-child(4)').classList.add('active');отобразитьНастройки();}
if(чатОткрыт){$('chatWindow').classList.remove('open');$('chatWindow').style.display='none';чатОткрыт=false;}
}
function отобразитьЧаты(){
let html='<div class="chat-item" onclick="открытьЧат(\'общий\',\'Общий чат\')"><div class="chat-avatar">#</div><div class="chat-info"><div class="chat-name">Общий чат</div><div class="chat-last">'+получитьПоследнееСообщение('общий')+'</div></div>'+(непрочитанныеДанные['общий']?'<div class="chat-unread">'+непрочитанныеДанные['общий']+'</div>':'')+'</div>';
приватныеЧаты.forEach(c=>{const ur=непрочитанныеДанные[c.id]||0;html+='<div class="chat-item" onclick="открытьПриватныйЧат(\''+c.id+'\',\''+c.имя+'\')"><div class="chat-avatar">'+(c.аватар||c.имя[0])+'</div><div class="chat-info"><div class="chat-name">'+c.имя+'</div><div class="chat-last">'+получитьПоследнееСообщение(c.id)+'</div></div>'+(ur?'<div class="chat-unread">'+ur+'</div>':'')+'</div>';});
$('chatList').innerHTML=html;обновитьБейдж();
}
function получитьПоследнееСообщение(idЧата){
let сообщения=[];if(idЧата==='общий'){сообщения=window._кешСообщений?.общий||[];}else{const чат=приватныеЧаты.find(c=>c.id===idЧата);if(чат)сообщения=чат.сообщения||[];}
if(!сообщения||сообщения.length===0)return'Начните общение';
const последнее=сообщения[сообщения.length-1];let текст='';
if(последнее.имя===текущийПользователь)текст='Вы: ';
if(последнее.тип==='текст')текст+=последнее.содержимое;
else if(последнее.тип==='изображение')текст+='📎 Фото';
else if(последнее.тип==='видео')текст+='📎 Видео';
else текст+='📎 Медиа';
return текст;
}
function отобразитьПользователей(){socket.emit('получить_пользователей',{имя:текущийПользователь});}
function отобразитьСписокПользователей(пользователи){
if(!пользователи||!пользователи.length){$('usersList').innerHTML='<div class="empty-state"><div class="icon">👤</div><h3>Нет пользователей</h3></div>';return;}
$('usersList').innerHTML=пользователи.map(u=>'<div class="chat-item" onclick="начатьПриватныйЧат(\''+u.имя+'\')"><div class="chat-avatar">'+(u.аватар?'<img src="'+u.аватар+'">':u.имя[0])+(u.статус==='онлайн'?'<span class="online-dot"></span>':'')+'</div><div class="chat-info"><div class="chat-name">'+u.имя+'</div><div class="chat-last">'+(u.описание||'Привет!')+'</div></div></div>').join('');
}
function поискПользователей(){const q=$('searchUsers').value.toLowerCase();document.querySelectorAll('#usersList .chat-item').forEach(el=>{const имя=el.querySelector('.chat-name').textContent.toLowerCase();el.style.display=имя.includes(q)?'flex':'none';});}
function открытьЧат(чат,название){текущийЧат=чат;текущееНазваниеЧата=название;чатОткрыт=true;document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));$('chatWindow').classList.add('open');$('chatWindow').style.display='flex';$('chatTitle').textContent=название;$('messagesContainer').innerHTML='';if(непрочитанныеДанные[чат]){непрочитанныеДанные[чат]=0;обновитьБейдж();}socket.emit('присоединиться_к_чату',{чат,имя:текущийПользователь});$('msgInput').focus();}
function открытьПриватныйЧат(idЧата,имя){текущийЧат=idЧата;текущееНазваниеЧата=имя;чатОткрыт=true;document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));$('chatWindow').classList.add('open');$('chatWindow').style.display='flex';$('chatTitle').textContent=имя;$('messagesContainer').innerHTML='';if(непрочитанныеДанные[idЧата]){непрочитанныеДанные[idЧата]=0;обновитьБейдж();}socket.emit('присоединиться_к_чату',{чат:idЧата,имя:текущийПользователь});$('msgInput').focus();}
function начатьПриватныйЧат(имя){if(имя===текущийПользователь)return;socket.emit('начать_приватный_чат',{пользователь1:текущийПользователь,пользователь2:имя});}
function открытьПриватныйЧатДанные(idЧата,пользователь,аватар,сообщения){текущийЧат=idЧата;текущееНазваниеЧата=пользователь;чатОткрыт=true;document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));$('chatWindow').classList.add('open');$('chatWindow').style.display='flex';$('chatTitle').textContent=пользователь;$('messagesContainer').innerHTML='';if(сообщения){сообщения.forEach(m=>отобразитьСообщение(m));скроллВниз();}const существует=приватныеЧаты.some(c=>c.id===idЧата);if(!существует){приватныеЧаты.push({id:idЧата,имя:пользователь,аватар:аватар||пользователь[0],сообщения:сообщения||[]});localStorage.setItem('приватные_чаты',JSON.stringify(приватныеЧаты));}if(непрочитанныеДанные[idЧата]){непрочитанныеДанные[idЧата]=0;обновитьБейдж();}$('msgInput').focus();}
function закрытьЧат(){$('chatWindow').classList.remove('open');$('chatWindow').style.display='none';чатОткрыт=false;$('pageChats').classList.add('active');document.querySelector('.nav-item:nth-child(1)').classList.add('active');отобразитьЧаты();}
function удалитьЧат(){if(!confirm('Удалить чат из списка?'))return;приватныеЧаты=приватныеЧаты.filter(c=>c.id!==текущийЧат);localStorage.setItem('приватные_чаты',JSON.stringify(приватныеЧаты));закрытьЧат();}
function отправитьСообщение(){const текст=$('msgInput').value.trim();if(!текст)return;socket.emit('отправить_сообщение',{имя:текущийПользователь,чат:текущийЧат,тип:'текст',содержимое:текст});$('msgInput').value='';socket.emit('печатает',{чат:текущийЧат,имя:текущийПользователь,печатает:false});}
function печатает(){if(таймаутПечатания)clearTimeout(таймаутПечатания);socket.emit('печатает',{чат:текущийЧат,имя:текущийПользователь,печатает:true});таймаутПечатания=setTimeout(()=>{socket.emit('печатает',{чат:текущийЧат,имя:текущийПользователь,печатает:false});},1500);}
function обработатьФайл(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=(ev)=>{socket.emit('отправить_сообщение',{имя:текущийПользователь,чат:текущийЧат,тип:f.type.startsWith('video')?'видео':'изображение',содержимое:ev.target.result});};r.readAsDataURL(f);e.target.value='';}
function отобразитьСообщение(msg){const isSelf=msg.имя===текущийПользователь;const div=document.createElement('div');div.className='msg'+(isSelf?' self':'');div.dataset.msgId=msg.id;let содержимое=msg.содержимое;if(msg.тип==='изображение'){содержимое='<img src="'+msg.содержимое+'" onclick="открытьМедиа(\''+msg.содержимое+'\',\'image\')">';}else if(msg.тип==='видео'){содержимое='<video src="'+msg.содержимое+'" controls></video>';}else{содержимое=msg.содержимое.replace(/</g,'&lt;').replace(/>/g,'&gt;');}const аватар=msg.аватар?'<img src="'+msg.аватар+'">':msg.имя[0];const действия=isSelf?'<div class="msg-actions"><button onclick="редактироватьСообщение(\''+msg.id+'\')">✎</button><button onclick="удалитьСообщение(\''+msg.id+'\')">✕</button></div>':'';div.innerHTML='<div class="msg-avatar" onclick="открытьПрофиль(\''+msg.имя+'\')">'+аватар+'</div><div><div class="msg-bubble">'+содержимое+(msg.отредактировано?'<span class="edited">✎</span>':'')+'</div><div class="msg-time">'+msg.время+'</div>'+действия+'</div>';$('messagesContainer').appendChild(div);}
function удалитьСообщение(id){if(!confirm('Удалить сообщение?'))return;socket.emit('удалить_сообщение',{чат:текущийЧат,id_сообщения:id,имя:текущийПользователь});}
function редактироватьСообщение(id){const t=prompt('Редактировать:');if(t&&t.trim()){socket.emit('редактировать_сообщение',{чат:текущийЧат,id_сообщения:id,имя:текущийПользователь,содержимое:t.trim()});}}
function скроллВниз(){setTimeout(()=>{$('messagesContainer').scrollTop=$('messagesContainer').scrollHeight;},50);}
function создатьПост(){document.getElementById('postInput').click();}
function обработатьПост(e){const f=e.target.files[0];if(!f)return;const описание=prompt('Описание:')||'';const r=new FileReader();r.onload=(ev)=>{socket.emit('создать_пост',{имя:текущийПользователь,содержимое:ev.target.result,тип_медиа:f.type.startsWith('video')?'видео':'изображение',описание});показатьУведомление('Пост опубликован!');};r.readAsDataURL(f);e.target.value='';}
function отобразитьПост(p){const лайкнут=p.лайки&&p.лайки.includes(текущийПользователь);const автор=p.автор===текущийПользователь;const аватар=p.аватар?'<img src="'+p.аватар+'">':p.автор[0];return'<div class="post-card" id="post-'+p.id+'"><div class="post-header"><div class="post-avatar" onclick="открытьПрофиль(\''+p.автор+'\')">'+аватар+'</div><div><div class="post-author" onclick="открытьПрофиль(\''+p.автор+'\')">'+p.автор+'</div><div class="post-time">'+p.время+'</div></div>'+(автор?'<button class="btn" onclick="удалитьПост(\''+p.id+'\')" style="margin-left:auto;color:#ff4444">✕</button>':'')+'</div>'+(p.тип_медиа==='изображение'?'<img class="post-media" src="'+p.содержимое+'" onclick="открытьМедиа(\''+p.содержимое+'\',\'image\')">':p.тип_медиа==='видео'?'<video class="post-media" src="'+p.содержимое+'" controls></video>':'')+'<div class="post-caption">'+(p.описание||'')+'</div><div class="post-actions"><button class="post-action '+(лайкнут?'liked':'')+'" onclick="лайкнутьПост(\''+p.id+'\')">❤️ <span class="count">'+(p.лайки||[]).length+'</span></button><button class="post-action" onclick="переключитьКомментарии(\''+p.id+'\')">💬 <span class="count">'+(p.комментарии||[]).length+'</span></button></div><div class="post-comments" id="comments-'+p.id+'" style="'+(p.комментарии||[]).length?'':'display:none'+'">'+(p.комментарии||[]).map(c=>'<div class="post-comment"><div class="post-comment-avatar">'+(c.аватар?'<img src="'+c.аватар+'">':c.имя[0])+'</div><div class="post-comment-text"><b>'+c.имя+'</b> '+c.комментарий+'</div></div>').join('')+'</div><div class="comment-input"><input id="comment-'+p.id+'" placeholder="Комментарий..." onkeypress="if(event.key===\'Enter\')отправитьКомментарий(\''+p.id+'\')"><button onclick="отправитьКомментарий(\''+p.id+'\')">Отправить</button></div></div>';}
function лайкнутьПост(id){socket.emit('лайкнуть_пост',{id_поста:id,имя:текущийПользователь});}
function отправитьКомментарий(id){const input=document.getElementById('comment-'+id);const текст=input.value.trim();if(!текст)return;socket.emit('комментировать_пост',{id_поста:id,имя:текущийПользователь,комментарий:текст});input.value='';}
function переключитьКомментарии(id){const el=document.getElementById('comments-'+id);if(el)el.style.display=el.style.display==='none'?'block':'none';}
function удалитьПост(id){if(!confirm('Удалить пост?'))return;const xhr=new XMLHttpRequest();xhr.open('POST','/удалить_пост',true);xhr.setRequestHeader('Content-Type','application/json');xhr.send(JSON.stringify({pid:id,n:текущийПользователь}));setTimeout(()=>socket.emit('получить_посты'),500);}
function отобразитьНастройки(){const аватар=текущийАватар?'<img src="'+текущийАватар+'">':текущийПользователь?текущийПользователь[0]:'?';$('settingsContent').innerHTML='<div class="profile-section"><div class="profile-avatar" onclick="document.getElementById(\'avatarInput\').click()">'+аватар+'</div><div class="profile-name">'+(текущийПользователь||'Гость')+'</div><div class="profile-bio">'+(текущееОписание||'Нажмите чтобы добавить описание')+'</div><div class="profile-status">🟢 Онлайн</div></div><div class="settings-group"><div class="setting-item" onclick="редактироватьОписание()"><span class="setting-label">✏️ Редактировать описание</span></div><div class="setting-item" onclick="поделиться()"><span class="setting-label">🔗 Поделиться</span></div><div class="setting-item" onclick="выйти()" style="border-left:3px solid #ff4444"><span class="setting-label" style="color:#ff4444">🚪 Выйти</span></div></div>';}
function редактироватьОписание(){const описание=prompt('Введите описание:',текущееОписание||'');if(описание!==null){текущееОписание=описание;socket.emit('обновить_описание',{имя:текущийПользователь,описание});отобразитьНастройки();}}
function обработатьАватар(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=(ev)=>{текущийАватар=ev.target.result;socket.emit('обновить_аватар',{имя:текущийПользователь,аватар:ev.target.result});отобразитьНастройки();показатьУведомление('Аватар обновлен!');};r.readAsDataURL(f);e.target.value='';}
function выйти(){if(!confirm('Выйти?'))return;socket.emit('выход',{токен:текущийТокен});localStorage.removeItem('shugramm_токен');localStorage.removeItem('shugramm_пользователь');текущийПользователь=null;location.reload();}
function поделиться(){socket.emit('поделиться_ссылкой');}
function открытьПрофиль(имя){if(имя===текущийПользователь)return;fetch('/api/пользователь/'+имя).then(r=>r.json()).then(d=>{if(d.ошибка){показатьУведомление(d.ошибка);return;}const аватар=d.аватар?'<img src="'+d.аватар+'">':d.имя[0];$('profileContent').innerHTML='<div class="profile-modal-avatar">'+аватар+'</div><div class="profile-modal-name">'+d.имя+'</div><div class="profile-modal-bio">'+(d.описание||'Нет описания')+'</div><div class="profile-modal-status '+(d.статус==='онлайн'?'online':'offline')+'">'+(d.статус==='онлайн'?'🟢 Онлайн':'⚫ Не в сети')+'</div><div class="profile-modal-posts"><div class="profile-modal-posts-title">📸 Посты ('+d.посты.length+')</div>'+(d.посты.length?d.посты.map(p=>'<div class="profile-modal-post"><span class="p-time">'+p.время+'</span><span class="p-caption">'+(p.описание||'Без описания')+'</span></div>').join(''):'<div style="color:var(--text-secondary);font-size:12px">Нет постов</div>')+'</div><button class="profile-modal-btn" onclick="начатьПриватныйЧат(\''+d.имя+'\')">💬 Написать</button>';$('profileModal').classList.add('open');});}
function закрытьПрофиль(){$('profileModal').classList.remove('open');}
function открытьМедиа(src,type){const viewer=$('mediaViewer');viewer.classList.add('open');if(type==='image'){$('mediaImg').src=src;$('mediaImg').style.display='block';$('mediaVideo').style.display='none';}else{$('mediaVideo').src=src;$('mediaVideo').style.display='block';$('mediaImg').style.display='none';$('mediaVideo').play();}}
function закрытьМедиа(){$('mediaViewer').classList.remove('open');$('mediaVideo').pause();}
function обновитьБейдж(){let всего=0;for(const k in непрочитанныеДанные)всего+=непрочитанныеДанные[k]||0;const бейдж=$('totalBadge');if(всего>0){бейдж.textContent=всего;бейдж.style.display='flex';}else{бейдж.style.display='none';}}
document.addEventListener('keydown',(e)=>{if(e.key==='Escape'){if($('mediaViewer').classList.contains('open'))закрытьМедиа();else if(чатОткрыт)закрытьЧат();}});
window._кешСообщений={};
socket.on('история_чата',(d)=>{window._кешСообщений[d.чат]=d.сообщения;});
socket.on('новое_сообщение',(d)=>{if(!window._кешСообщений[d.чат])window._кешСообщений[d.чат]=[];window._кешСообщений[d.чат].push(d.сообщение);});
console.log('⚡ Shugramm загружен!');
</script>
</body>
</html>
'''

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    порт = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=порт, allow_unsafe_werkzeug=True)
