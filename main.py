from flask import Flask, render_template_string, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
import random, time, os, hashlib, json, logging, re, base64
from functools import wraps
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Set, Any
from markupsafe import escape
import bcrypt
from threading import Lock

# ========== КОНФИГУРАЦИЯ ==========
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'shugramm-' + str(random.randint(10000, 99999)))
    MAX_BUFFER_SIZE = 100 * 1024 * 1024
    MAX_MESSAGES = 200
    MAX_POSTS = 50
    MAX_MESSAGE_LENGTH = 2000
    MAX_POST_LENGTH = 500000
    MAX_COMMENT_LENGTH = 300
    MAX_BIO_LENGTH = 200
    MAX_USERNAME_LENGTH = 20
    MIN_USERNAME_LENGTH = 2
    MIN_PASSWORD_LENGTH = 4
    DATA_FILE = 'shugramm_data.json'
    PORT = int(os.environ.get('PORT', 5000))
    DEBUG = False
    SESSION_TIMEOUT = 3600  # 1 час
    MAX_ONLINE_USERS = 100
    RATE_LIMIT = 10  # сообщений в минуту

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('shugramm.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== ПРИЛОЖЕНИЕ ==========
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=Config.SESSION_TIMEOUT)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    max_http_buffer_size=Config.MAX_BUFFER_SIZE,
    ping_timeout=60,
    ping_interval=25,
    logger=logger,
    engineio_logger=logger
)

# ========== МОДЕЛИ ДАННЫХ (Python) ==========
@dataclass
class User:
    name: str
    sid: str = ''
    avatar: Optional[str] = None
    status: str = 'онлайн'
    phone: str = ''
    password_hash: str = ''
    lang: str = 'ru'
    bio: str = ''
    token: str = ''
    last_seen: float = field(default_factory=time.time)
    messages_count: int = 0
    last_message_time: float = 0
    
    def to_dict(self) -> Dict:
        return {
            'n': self.name,
            'a': self.avatar,
            'st': self.status,
            'lang': self.lang,
            'bio': self.bio,
            'last_seen': datetime.fromtimestamp(self.last_seen).strftime("%H:%M"),
            'online': self.status == 'онлайн'
        }
    
    def to_public_dict(self) -> Dict:
        return {
            'name': self.name,
            'avatar': self.avatar,
            'status': self.status,
            'bio': self.bio
        }

@dataclass
class Message:
    id: str
    sender: str
    type: str  # text, img, vid, audio, file
    content: str
    timestamp: float
    avatar: Optional[str] = None
    edited: bool = False
    reply_to: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            'i': self.id,
            'n': self.sender,
            't': self.type,
            'c': self.content[:200] if self.type == 'text' else self.content,
            'ts': datetime.fromtimestamp(self.timestamp).strftime("%H:%M"),
            'a': self.avatar,
            'edited': self.edited,
            'reply': self.reply_to
        }

@dataclass
class Post:
    id: str
    author: str
    avatar: Optional[str]
    content: str
    media_type: str  # image, video, text
    media_url: str
    caption: str
    likes: List[str] = field(default_factory=list)
    comments: List[Dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    views: int = 0
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'n': self.author,
            'a': self.avatar,
            'm': self.media_url,
            'mt': self.media_type,
            'c': escape(self.caption[:500]),
            'l': self.likes,
            'cm': self.comments[-10:],
            'ts': datetime.fromtimestamp(self.timestamp).strftime("%d.%m.%Y %H:%M"),
            'views': self.views,
            'likes_count': len(self.likes),
            'comments_count': len(self.comments)
        }

@dataclass
class Chat:
    id: str
    name: str
    type: str  # group, private
    members: Set[str] = field(default_factory=set)
    messages: List[Dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    avatar: Optional[str] = None
    description: str = ''
    admins: Set[str] = field(default_factory=set)
    muted: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'members': list(self.members),
            'messages_count': len(self.messages),
            'created': datetime.fromtimestamp(self.created_at).strftime("%d.%m.%Y"),
            'avatar': self.avatar,
            'description': self.description,
            'admins': list(self.admins)
        }

# ========== ХРАНИЛИЩЕ ДАННЫХ (Python) ==========
class DataStore:
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.users: Dict[str, User] = {}
            self.posts: List[Post] = []
            self.chats: Dict[str, Chat] = {}
            self.pending_codes: Dict[str, Dict] = {}
            self.unread: Dict[str, Dict[str, int]] = {}
            self.notifications: Dict[str, List[Dict]] = {}
            self.banned_users: Set[str] = set()
            self.message_limits: Dict[str, List[float]] = {}
            self.initialized = True
            self._load_data()
            self._init_defaults()
    
    def _init_defaults(self):
        """Создание стандартных чатов"""
        if 'general' not in self.chats:
            self.chats['general'] = Chat(
                id='general',
                name='Общий чат',
                type='group',
                members=set(),
                description='Главный чат для всех пользователей'
            )
    
    def _load_data(self):
        """Загрузка данных из JSON"""
        try:
            if os.path.exists(Config.DATA_FILE):
                with open(Config.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Загрузка пользователей
                    for name, user_data in data.get('users', {}).items():
                        user = User(
                            name=name,
                            avatar=user_data.get('avatar'),
                            phone=user_data.get('phone', ''),
                            password_hash=user_data.get('password_hash', ''),
                            lang=user_data.get('lang', 'ru'),
                            bio=user_data.get('bio', ''),
                            token=user_data.get('token', ''),
                            last_seen=user_data.get('last_seen', time.time()),
                            messages_count=user_data.get('messages_count', 0)
                        )
                        self.users[name] = user
                    
                    # Загрузка постов
                    for post_data in data.get('posts', []):
                        post = Post(
                            id=post_data['id'],
                            author=post_data['author'],
                            avatar=post_data.get('avatar'),
                            content=post_data.get('content', ''),
                            media_type=post_data.get('media_type', 'text'),
                            media_url=post_data.get('media_url', ''),
                            caption=post_data.get('caption', ''),
                            likes=post_data.get('likes', []),
                            comments=post_data.get('comments', []),
                            timestamp=post_data.get('timestamp', time.time()),
                            views=post_data.get('views', 0)
                        )
                        self.posts.append(post)
                    
                    # Загрузка чатов
                    for chat_id, chat_data in data.get('chats', {}).items():
                        chat = Chat(
                            id=chat_id,
                            name=chat_data.get('name', chat_id),
                            type=chat_data.get('type', 'group'),
                            members=set(chat_data.get('members', [])),
                            messages=chat_data.get('messages', []),
                            created_at=chat_data.get('created_at', time.time()),
                            avatar=chat_data.get('avatar'),
                            description=chat_data.get('description', ''),
                            admins=set(chat_data.get('admins', [])),
                            muted=set(chat_data.get('muted', []))
                        )
                        self.chats[chat_id] = chat
                    
                    # Загрузка непрочитанных
                    self.unread = data.get('unread', {})
                    
                    logger.info(f"Загружено: {len(self.users)} пользователей, {len(self.posts)} постов, {len(self.chats)} чатов")
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
            self._init_defaults()
    
    def _save_data(self):
        """Сохранение данных в JSON"""
        try:
            data = {
                'users': {
                    name: {
                        'avatar': user.avatar,
                        'phone': user.phone,
                        'password_hash': user.password_hash,
                        'lang': user.lang,
                        'bio': user.bio,
                        'token': user.token,
                        'last_seen': user.last_seen,
                        'messages_count': user.messages_count
                    }
                    for name, user in self.users.items()
                },
                'posts': [
                    {
                        'id': p.id,
                        'author': p.author,
                        'avatar': p.avatar,
                        'content': p.content,
                        'media_type': p.media_type,
                        'media_url': p.media_url,
                        'caption': p.caption,
                        'likes': p.likes,
                        'comments': p.comments,
                        'timestamp': p.timestamp,
                        'views': p.views
                    }
                    for p in self.posts
                ],
                'chats': {
                    chat_id: {
                        'name': chat.name,
                        'type': chat.type,
                        'members': list(chat.members),
                        'messages': chat.messages[-Config.MAX_MESSAGES:],
                        'created_at': chat.created_at,
                        'avatar': chat.avatar,
                        'description': chat.description,
                        'admins': list(chat.admins),
                        'muted': list(chat.muted)
                    }
                    for chat_id, chat in self.chats.items()
                },
                'unread': self.unread
            }
            
            with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("Данные сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    # ===== МЕТОДЫ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ =====
    
    def create_user(self, name: str, phone: str, password: str, sid: str) -> User:
        """Создание нового пользователя"""
        if name in self.users:
            raise ValueError("Пользователь уже существует")
        
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        token = self._generate_token()
        
        user = User(
            name=name,
            sid=sid,
            phone=phone,
            password_hash=password_hash,
            token=token,
            last_seen=time.time()
        )
        self.users[name] = user
        self.unread[name] = {}
        self.notifications[name] = []
        self.chats['general'].members.add(name)
        self._save_data()
        logger.info(f"👤 Новый пользователь: {name}")
        return user
    
    def verify_user(self, name: str, password: str) -> Optional[User]:
        """Проверка пароля"""
        user = self.users.get(name)
        if not user:
            return None
        if user.name in self.banned_users:
            return None
        try:
            if bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
                user.token = self._generate_token()
                user.last_seen = time.time()
                self._save_data()
                return user
        except Exception as e:
            logger.error(f"Ошибка проверки пароля: {e}")
        return None
    
    def get_user_by_token(self, token: str) -> Optional[User]:
        """Поиск пользователя по токену"""
        for user in self.users.values():
            if user.token == token:
                return user
        return None
    
    def get_user_by_sid(self, sid: str) -> Optional[User]:
        """Поиск пользователя по SID"""
        for user in self.users.values():
            if user.sid == sid:
                return user
        return None
    
    def update_user_status(self, name: str, status: str):
        """Обновление статуса пользователя"""
        user = self.users.get(name)
        if user:
            user.status = status
            user.last_seen = time.time()
            self._save_data()
    
    def is_online(self, name: str) -> bool:
        """Проверка, онлайн ли пользователь"""
        user = self.users.get(name)
        return user and user.status == 'онлайн' and user.sid
    
    def get_online_users(self) -> List[Dict]:
        """Получение списка онлайн пользователей"""
        return [
            {'n': u.name, 'a': u.avatar}
            for u in self.users.values()
            if u.status == 'онлайн' and u.sid
        ]
    
    # ===== МЕТОДЫ РАБОТЫ С СООБЩЕНИЯМИ =====
    
    def add_message(self, chat_id: str, message: Message) -> bool:
        """Добавление сообщения в чат"""
        chat = self.chats.get(chat_id)
        if not chat:
            return False
        
        # Проверка, не забанен ли пользователь
        if message.sender in self.banned_users:
            return False
        
        # Проверка, не замучен ли чат для пользователя
        if message.sender in chat.muted:
            return False
        
        # Rate limiting
        if not self._check_rate_limit(message.sender):
            return False
        
        msg_dict = message.to_dict()
        chat.messages.append(msg_dict)
        
        # Ограничение количества сообщений
        if len(chat.messages) > Config.MAX_MESSAGES:
            chat.messages = chat.messages[-Config.MAX_MESSAGES:]
        
        # Обновление счетчика сообщений пользователя
        user = self.users.get(message.sender)
        if user:
            user.messages_count += 1
            user.last_message_time = time.time()
        
        self._save_data()
        return True
    
    def delete_message(self, chat_id: str, msg_id: str, username: str) -> bool:
        """Удаление сообщения"""
        chat = self.chats.get(chat_id)
        if not chat:
            return False
        
        for i, msg in enumerate(chat.messages):
            if msg.get('i') == msg_id and msg.get('n') == username:
                chat.messages.pop(i)
                self._save_data()
                return True
        return False
    
    def edit_message(self, chat_id: str, msg_id: str, username: str, new_content: str) -> bool:
        """Редактирование сообщения"""
        chat = self.chats.get(chat_id)
        if not chat:
            return False
        
        for msg in chat.messages:
            if msg.get('i') == msg_id and msg.get('n') == username:
                msg['c'] = escape(new_content[:Config.MAX_MESSAGE_LENGTH])
                msg['edited'] = True
                self._save_data()
                return True
        return False
    
    def get_chat_messages(self, chat_id: str, limit: int = Config.MAX_MESSAGES) -> List[Dict]:
        """Получение сообщений чата"""
        chat = self.chats.get(chat_id)
        if chat:
            return chat.messages[-limit:]
        return []
    
    def search_messages(self, chat_id: str, query: str) -> List[Dict]:
        """Поиск по сообщениям"""
        chat = self.chats.get(chat_id)
        if not chat:
            return []
        
        query = query.lower()
        results = []
        for msg in chat.messages:
            if query in msg.get('c', '').lower():
                results.append(msg)
        return results[:20]
    
    # ===== МЕТОДЫ РАБОТЫ С ПОСТАМИ =====
    
    def add_post(self, post: Post) -> bool:
        """Добавление поста"""
        if post.author in self.banned_users:
            return False
        
        self.posts.insert(0, post)
        if len(self.posts) > Config.MAX_POSTS:
            self.posts.pop()
        self._save_data()
        logger.info(f"📝 Новый пост от {post.author}")
        return True
    
    def delete_post(self, post_id: str, username: str) -> bool:
        """Удаление поста"""
        for i, post in enumerate(self.posts):
            if post.id == post_id and (post.author == username or username in self.get_admins()):
                self.posts.pop(i)
                self._save_data()
                return True
        return False
    
    def like_post(self, post_id: str, username: str) -> bool:
        """Лайк/дизлайк поста"""
        for post in self.posts:
            if post.id == post_id:
                if username in post.likes:
                    post.likes.remove(username)
                else:
                    post.likes.append(username)
                self._save_data()
                return True
        return False
    
    def add_comment(self, post_id: str, username: str, comment: str, avatar: Optional[str] = None) -> bool:
        """Добавление комментария"""
        if username in self.banned_users:
            return False
        
        for post in self.posts:
            if post.id == post_id:
                post.comments.append({
                    'n': username,
                    'a': avatar,
                    'c': escape(comment[:Config.MAX_COMMENT_LENGTH]),
                    'ts': datetime.now().strftime("%H:%M"),
                    'id': self._generate_id()
                })
                self._save_data()
                return True
        return False
    
    def delete_comment(self, post_id: str, comment_id: str, username: str) -> bool:
        """Удаление комментария"""
        for post in self.posts:
            if post.id == post_id:
                for i, comment in enumerate(post.comments):
                    if comment.get('id') == comment_id and comment.get('n') == username:
                        post.comments.pop(i)
                        self._save_data()
                        return True
        return False
    
    # ===== МЕТОДЫ РАБОТЫ С ЧАТАМИ =====
    
    def create_chat(self, name: str, chat_type: str = 'group', creator: str = '') -> Chat:
        """Создание нового чата"""
        chat_id = self._generate_id()
        chat = Chat(
            id=chat_id,
            name=escape(name),
            type=chat_type,
            members=set(),
            admins={creator} if creator else set()
        )
        self.chats[chat_id] = chat
        self._save_data()
        return chat
    
    def get_private_chat_id(self, user1: str, user2: str) -> str:
        """Получение ID приватного чата"""
        return f"p_{min(user1, user2)}_{max(user1, user2)}"
    
    def create_private_chat(self, user1: str, user2: str) -> str:
        """Создание приватного чата"""
        chat_id = self.get_private_chat_id(user1, user2)
        if chat_id not in self.chats:
            chat = Chat(
                id=chat_id,
                name=user2,
                type='private',
                members={user1, user2}
            )
            self.chats[chat_id] = chat
            self._save_data()
        return chat_id
    
    def add_user_to_chat(self, chat_id: str, username: str) -> bool:
        """Добавление пользователя в чат"""
        chat = self.chats.get(chat_id)
        if not chat:
            return False
        chat.members.add(username)
        self._save_data()
        return True
    
    def remove_user_from_chat(self, chat_id: str, username: str) -> bool:
        """Удаление пользователя из чата"""
        chat = self.chats.get(chat_id)
        if not chat:
            return False
        if username in chat.members:
            chat.members.remove(username)
            self._save_data()
            return True
        return False
    
    # ===== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =====
    
    def _generate_token(self) -> str:
        """Генерация токена"""
        return hashlib.sha256(f"{random.random()}{time.time()}".encode()).hexdigest()[:32]
    
    def _generate_id(self) -> str:
        """Генерация ID"""
        return f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    
    def _check_rate_limit(self, username: str) -> bool:
        """Проверка лимита сообщений"""
        current_time = time.time()
        if username not in self.message_limits:
            self.message_limits[username] = []
        
        # Очистка старых сообщений
        self.message_limits[username] = [
            t for t in self.message_limits[username]
            if current_time - t < 60
        ]
        
        if len(self.message_limits[username]) >= Config.RATE_LIMIT:
            return False
        
        self.message_limits[username].append(current_time)
        return True
    
    def get_admins(self) -> Set[str]:
        """Получение списка администраторов"""
        admins = set()
        for chat in self.chats.values():
            admins.update(chat.admins)
        return admins
    
    def get_stats(self) -> Dict:
        """Получение статистики"""
        online_count = sum(1 for u in self.users.values() if u.status == 'онлайн')
        messages_count = sum(len(chat.messages) for chat in self.chats.values())
        return {
            'total_users': len(self.users),
            'online_users': online_count,
            'total_posts': len(self.posts),
            'total_chats': len(self.chats),
            'total_messages': messages_count,
            'banned_users': len(self.banned_users)
        }

# ========== ИНИЦИАЛИЗАЦИЯ ==========
store = DataStore()

# ========== ДЕКОРАТОРЫ ==========
def require_auth(f):
    """Декоратор для проверки авторизации"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get('token') or request.headers.get('X-Auth-Token') or request.cookies.get('token')
        if not token:
            return jsonify({'error': 'Требуется авторизация'}), 401
        user = store.get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Недействительный токен'}), 401
        if user.name in store.banned_users:
            return jsonify({'error': 'Пользователь забанен'}), 403
        return f(user=user, *args, **kwargs)
    return decorated

def validate_user(f):
    """Декоратор для валидации пользователя в SocketIO"""
    @wraps(f)
    def decorated(data):
        username = data.get('n', '')
        if username not in store.users:
            emit('er', {'m': 'Пользователь не найден'})
            return
        if username in store.banned_users:
            emit('er', {'m': 'Пользователь забанен'})
            return
        return f(data)
    return decorated

# ========== HTTP РОУТЫ ==========
@app.route('/')
def index():
    """Главная страница"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/stats')
@require_auth
def get_stats(user):
    """Получение статистики"""
    return jsonify(store.get_stats())

@app.route('/api/users')
@require_auth
def get_users(user):
    """Получение списка пользователей"""
    users_list = [
        u.to_dict()
        for u in store.users.values()
        if u.name != user.name
    ]
    return jsonify({'users': users_list})

@app.route('/api/users/<username>')
@require_auth
def get_user_profile(user, username):
    """Получение профиля пользователя"""
    target = store.users.get(username)
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404
    return jsonify(target.to_dict())

@app.route('/api/posts')
@require_auth
def get_posts(user):
    """Получение постов"""
    posts = [p.to_dict() for p in store.posts[:Config.MAX_POSTS]]
    return jsonify({'posts': posts})

@app.route('/api/search')
@require_auth
def search(user):
    """Поиск пользователей и постов"""
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'users': [], 'posts': []})
    
    # Поиск пользователей
    users = [
        {'name': u.name, 'avatar': u.avatar}
        for u in store.users.values()
        if query in u.name.lower() and u.name != user.name
    ][:5]
    
    # Поиск постов
    posts = [
        p.to_dict()
        for p in store.posts
        if query in p.caption.lower() or query in p.content.lower()
    ][:5]
    
    return jsonify({'users': users, 'posts': posts})

@app.route('/api/chat/<chat_id>')
@require_auth
def get_chat_messages(user, chat_id):
    """Получение сообщений чата"""
    if chat_id not in store.chats:
        return jsonify({'error': 'Чат не найден'}), 404
    
    chat = store.chats[chat_id]
    if user.name not in chat.members and chat.type == 'private':
        return jsonify({'error': 'Нет доступа'}), 403
    
    messages = store.get_chat_messages(chat_id)
    return jsonify({'messages': messages})

@app.route('/api/chat', methods=['POST'])
@require_auth
def create_chat(user):
    """Создание нового чата"""
    data = request.get_json()
    name = data.get('name', '').strip()
    chat_type = data.get('type', 'group')
    members = data.get('members', [])
    
    if not name:
        return jsonify({'error': 'Название чата обязательно'}), 400
    
    if chat_type == 'private' and len(members) != 1:
        return jsonify({'error': 'Для приватного чата нужен 1 участник'}), 400
    
    chat = store.create_chat(name, chat_type, user.name)
    for member in members:
        store.add_user_to_chat(chat.id, member)
    store.add_user_to_chat(chat.id, user.name)
    
    return jsonify(chat.to_dict())

@app.route('/delete_post', methods=['POST'])
@require_auth
def delete_post(user):
    """Удаление поста"""
    data = request.get_json()
    post_id = data.get('pid', '')
    if not post_id:
        return jsonify({'error': 'ID поста не указан'}), 400
    
    if store.delete_post(post_id, user.name):
        return jsonify({'ok': True})
    return jsonify({'error': 'Пост не найден'}), 404

# ========== SOCKET.IO СОБЫТИЯ ==========
@socketio.on('connect')
def handle_connect():
    """Подключение клиента"""
    logger.info(f"🔌 Клиент подключен: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """Отключение клиента"""
    user = store.get_user_by_sid(request.sid)
    if user:
        user.status = 'оффлайн'
        user.last_seen = time.time()
        user.sid = ''
        store._save_data()
        emit('nu_user', {
            'n': user.name,
            'a': user.avatar,
            'st': 'оффлайн'
        }, broadcast=True)
        logger.info(f"🔴 Пользователь отключен: {user.name}")

@socketio.on('rc')
def request_code(data):
    """Запрос кода подтверждения"""
    phone = ''.join(filter(str.isdigit, data.get('p', '')))
    if len(phone) < 10:
        emit('er', {'m': 'Введите корректный номер телефона (минимум 10 цифр)'})
        return
    
    # Проверка, не забанен ли номер
    for user in store.users.values():
        if user.phone == phone and user.name in store.banned_users:
            emit('er', {'m': 'Номер заблокирован'})
            return
    
    code = str(random.randint(100000, 999999))
    store.pending_codes[phone] = {
        'code': code,
        'timestamp': time.time(),
        'attempts': 0
    }
    logger.info(f"📱 Код для {phone}: {code}")
    emit('cs', {'d': phone, 'c': code})

@socketio.on('vc')
def verify_code(data):
    """Проверка кода"""
    phone = data.get('d', '')
    code = data.get('c', '')
    
    if phone not in store.pending_codes:
        emit('er', {'m': 'Сессия истекла, запросите код заново'})
        return
    
    pending = store.pending_codes[phone]
    pending['attempts'] += 1
    
    # Проверка количества попыток
    if pending['attempts'] > 5:
        del store.pending_codes[phone]
        emit('er', {'m': 'Превышено количество попыток'})
        return
    
    # Проверка времени (5 минут)
    if time.time() - pending['timestamp'] > 300:
        del store.pending_codes[phone]
        emit('er', {'m': 'Код истек, запросите новый'})
        return
    
    if code != pending['code']:
        emit('er', {'m': f'Неверный код (осталось {5 - pending["attempts"]} попыток)'})
        return
    
    del store.pending_codes[phone]
    
    # Проверка существующего пользователя
    for user in store.users.values():
        if user.phone == phone:
            emit('ue', {'n': user.name})
            return
    
    emit('nu', {'d': phone})

@socketio.on('sp')
def set_password(data):
    """Установка пароля и регистрация"""
    phone = data.get('d', '')
    password = data.get('p', '').strip()
    name = data.get('n', '').strip()
    
    # Валидация имени
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_]{2,20}$', name):
        emit('er', {'m': 'Имя: 2-20 символов (буквы, цифры, _)'})
        return
    
    if name in store.users:
        emit('er', {'m': 'Пользователь с таким именем уже существует'})
        return
    
    if len(password) < Config.MIN_PASSWORD_LENGTH:
        emit('er', {'m': f'Пароль минимум {Config.MIN_PASSWORD_LENGTH} символов'})
        return
    
    # Создание пользователя
    user = store.create_user(name, phone, password, request.sid)
    join_room('general')
    
    emit('ro', {
        'n': user.name,
        'a': user.avatar,
        'token': user.token
    })
    emit('nu_user', {
        'n': user.name,
        'a': user.avatar,
        'st': 'онлайн'
    }, broadcast=True)
    logger.info(f"✅ Регистрация: {name}")

@socketio.on('li')
def login(data):
    """Вход в систему"""
    name = data.get('n', '').strip()
    password = data.get('p', '').strip()
    
    user = store.verify_user(name, password)
    if not user:
        emit('er', {'m': 'Неверное имя или пароль'})
        return
    
    if name in store.banned_users:
        emit('er', {'m': 'Пользователь забанен'})
        return
    
    # Обновление сессии
    old_user = store.get_user_by_sid(request.sid)
    if old_user and old_user.name != name:
        old_user.status = 'оффлайн'
        old_user.sid = ''
    
    user.sid = request.sid
    user.status = 'онлайн'
    user.last_seen = time.time()
    join_room('general')
    store._save_data()
    
    emit('lo', {
        'n': user.name,
        'a': user.avatar,
        'token': user.token
    })
    emit('nu_user', {
        'n': user.name,
        'a': user.avatar,
        'st': 'онлайн'
    }, broadcast=True)
    logger.info(f"✅ Вход: {name}")

@socketio.on('auto_login')
def auto_login(data):
    """Автоматический вход по токену"""
    token = data.get('token', '')
    user = store.get_user_by_token(token)
    if not user:
        return
    
    if user.name in store.banned_users:
        return
    
    old_user = store.get_user_by_sid(request.sid)
    if old_user and old_user.name != user.name:
        old_user.status = 'оффлайн'
        old_user.sid = ''
    
    user.sid = request.sid
    user.status = 'онлайн'
    user.last_seen = time.time()
    join_room('general')
    store._save_data()
    
    emit('lo', {
        'n': user.name,
        'a': user.avatar,
        'token': user.token
    })
    emit('nu_user', {
        'n': user.name,
        'a': user.avatar,
        'st': 'онлайн'
    }, broadcast=True)
    logger.info(f"✅ Автовход: {user.name}")

@socketio.on('sm')
@validate_user
def send_message(data):
    """Отправка сообщения"""
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_type = data.get('t', 'text')
    content = data.get('c', '')
    
    user = store.users.get(username)
    if not user:
        return
    
    chat = store.chats.get(chat_id)
    if not chat:
        emit('er', {'m': 'Чат не найден'})
        return
    
    # Проверка прав
    if username not in chat.members and chat.type == 'private':
        emit('er', {'m': 'Нет доступа к чату'})
        return
    
    # Валидация контента
    if msg_type == 'text':
        content = escape(content.strip())
        if not content:
            return
        if len(content) > Config.MAX_MESSAGE_LENGTH:
            content = content[:Config.MAX_MESSAGE_LENGTH]
    elif msg_type in ['img', 'vid', 'audio', 'file']:
        if len(content) > Config.MAX_BUFFER_SIZE:
            emit('er', {'m': 'Файл слишком большой'})
            return
    
    message = Message(
        id=f"m_{int(time.time() * 1000)}",
        sender=username,
        type=msg_type,
        content=content,
        timestamp=time.time(),
        avatar=user.avatar
    )
    
    if store.add_message(chat_id, message):
        msg_dict = message.to_dict()
        emit('nm', {'ch': chat_id, 'm': msg_dict}, room=chat_id)
        
        # Уведомления
        for member in chat.members:
            if member != username:
                store.unread.setdefault(member, {})
                store.unread[member][chat_id] = store.unread[member].get(chat_id, 0) + 1
                
                target = store.users.get(member)
                if target and target.sid:
                    emit('notify', {
                        'ch': chat_id,
                        'n': username,
                        'c': content[:30] + ('...' if len(content) > 30 else ''),
                        'type': msg_type
                    }, room=target.sid)

@socketio.on('jc')
def join_chat(data):
    """Подключение к чату"""
    chat_id = data.get('ch', 'general')
    username = data.get('n', '')
    
    if username not in store.users:
        return
    if chat_id not in store.chats:
        return
    
    chat = store.chats[chat_id]
    if username not in chat.members and chat.type == 'private':
        return
    
    join_room(chat_id)
    if username in store.unread:
        store.unread[username][chat_id] = 0
    
    messages = store.get_chat_messages(chat_id)
    emit('ch', {'ms': messages, 'chat': chat.to_dict()})

@socketio.on('leave_chat')
def leave_chat(data):
    """Выход из чата"""
    chat_id = data.get('ch', '')
    username = data.get('n', '')
    
    if chat_id in store.chats:
        leave_room(chat_id)
        if chat_id != 'general':
            store.remove_user_from_chat(chat_id, username)
            emit('user_left', {'n': username, 'ch': chat_id}, room=chat_id)

@socketio.on('delete_message')
@validate_user
def delete_message(data):
    """Удаление сообщения"""
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_id = data.get('mid', '')
    
    if store.delete_message(chat_id, msg_id, username):
        emit('message_deleted', {'ch': chat_id, 'mid': msg_id}, room=chat_id)

@socketio.on('edit_message')
@validate_user
def edit_message(data):
    """Редактирование сообщения"""
    username = data.get('n', '')
    chat_id = data.get('ch', 'general')
    msg_id = data.get('mid', '')
    new_content = data.get('c', '')
    
    if store.edit_message(chat_id, msg_id, username, new_content):
        # Отправляем обновленное сообщение
        chat = store.chats.get(chat_id)
        if chat:
            for msg in chat.messages:
                if msg.get('i') == msg_id:
                    emit('message_edited', {'ch': chat_id, 'm': msg}, room=chat_id)
                    break

@socketio.on('search_messages')
def search_messages(data):
    """Поиск по сообщениям"""
    chat_id = data.get('ch', 'general')
    query = data.get('q', '')
    
    results = store.search_messages(chat_id, query)
    emit('search_results', {'results': results})

@socketio.on('gu')
def get_users(data):
    """Получение списка пользователей"""
    username = data.get('n', '')
    users_list = []
    
    for name, user in store.users.items():
        if name != username:
            users_list.append({
                'n': name,
                'a': user.avatar,
                'st': user.status,
                'bio': user.bio,
                'online': user.status == 'онлайн'
            })
    
    # Сортировка: онлайн сверху
    users_list.sort(key=lambda x: (not x['online'], x['n']))
    emit('ul', {'u': users_list})

@socketio.on('sp2')
def start_private_chat(data):
    """Начало приватного чата"""
    user1 = data.get('n', '')
    user2 = data.get('t', '')
    
    if user1 not in store.users or user2 not in store.users:
        return
    
    chat_id = store.create_private_chat(user1, user2)
    join_room(chat_id)
    
    if user1 in store.unread:
        store.unread[user1][chat_id] = 0
    
    target = store.users[user2]
    messages = store.get_chat_messages(chat_id)
    chat = store.chats[chat_id]
    
    emit('po', {
        'ch': chat_id,
        't': user2,
        'a': target.avatar,
        'ms': messages,
        'chat': chat.to_dict()
    })

@socketio.on('ua')
def update_avatar(data):
    """Обновление аватара"""
    username = data.get('n', '')
    avatar = data.get('a', '')
    
    user = store.users.get(username)
    if user:
        user.avatar = avatar
        store._save_data()
        emit('avatar_updated', {'n': username, 'a': avatar}, broadcast=True)

@socketio.on('ub')
def update_bio(data):
    """Обновление биографии"""
    username = data.get('n', '')
    bio = data.get('b', '')[:Config.MAX_BIO_LENGTH]
    
    user = store.users.get(username)
    if user:
        user.bio = escape(bio)
        store._save_data()
        emit('bio_updated', {'n': username, 'b': user.bio})

@socketio.on('ul2')
def update_language(data):
    """Обновление языка"""
    username = data.get('n', '')
    lang = data.get('l', 'ru')
    
    user = store.users.get(username)
    if user:
        user.lang = lang
        store._save_data()

@socketio.on('cp')
def create_post(data):
    """Создание поста"""
    username = data.get('n', '')
    content = data.get('m', '')
    media_type = data.get('mt', 'image')
    caption = data.get('c', '')[:500]
    
    user = store.users.get(username)
    if not user:
        return
    
    if username in store.banned_users:
        emit('er', {'m': 'Вы забанены'})
        return
    
    if len(content) > Config.MAX_POST_LENGTH:
        content = content[:Config.MAX_POST_LENGTH]
    
    post = Post(
        id=f"p_{int(time.time() * 1000)}_{random.randint(1000, 9999)}",
        author=username,
        avatar=user.avatar,
        content=content,
        media_type=media_type,
        media_url=content,
        caption=escape(caption)
    )
    
    if store.add_post(post):
        emit('np', {'p': post.to_dict()}, broadcast=True)

@socketio.on('gp')
def get_posts():
    """Получение постов"""
    posts = [p.to_dict() for p in store.posts[:Config.MAX_POSTS]]
    emit('pl', {'p': posts})

@socketio.on('lp')
def like_post(data):
    """Лайк поста"""
    post_id = data.get('pid', '')
    username = data.get('n', '')
    
    if store.like_post(post_id, username):
        for post in store.posts:
            if post.id == post_id:
                emit('pu', {'p': post.to_dict()}, broadcast=True)
                break

@socketio.on('cmp')
def comment_post(data):
    """Добавление комментария"""
    post_id = data.get('pid', '')
    username = data.get('n', '')
    comment = data.get('c', '')[:Config.MAX_COMMENT_LENGTH]
    
    user = store.users.get(username)
    if not user:
        return
    
    if store.add_comment(post_id, username, comment, user.avatar):
        for post in store.posts:
            if post.id == post_id:
                emit('pu', {'p': post.to_dict()}, broadcast=True)
                break

@socketio.on('delete_comment')
def delete_comment(data):
    """Удаление комментария"""
    post_id = data.get('pid', '')
    comment_id = data.get('cid', '')
    username = data.get('n', '')
    
    if store.delete_comment(post_id, comment_id, username):
        for post in store.posts:
            if post.id == post_id:
                emit('pu', {'p': post.to_dict()}, broadcast=True)
                break

@socketio.on('sh')
def share_link():
    """Поделиться ссылкой"""
    host = request.host
    emit('sl', {'l': host})

@socketio.on('logout')
def logout(data):
    """Выход из системы"""
    token = data.get('token', '')
    user = store.get_user_by_token(token)
    if user:
        user.token = ''
        user.status = 'оффлайн'
        user.sid = ''
        store._save_data()
        emit('nu_user', {
            'n': user.name,
            'a': user.avatar,
            'st': 'оффлайн'
        }, broadcast=True)
        logger.info(f"🚪 Выход: {user.name}")

@socketio.on('typing')
def typing(data):
    """Индикатор набора текста"""
    chat_id = data.get('ch', 'general')
    username = data.get('n', '')
    is_typing = data.get('typing', False)
    
    emit('typing_status', {
        'n': username,
        'typing': is_typing
    }, room=chat_id, include_self=False)

@socketio.on('get_online_users')
def get_online_users():
    """Получение списка онлайн пользователей"""
    online = store.get_online_users()
    emit('online_users', {'users': online})

@socketio.on('report_user')
def report_user(data):
    """Жалоба на пользователя"""
    username = data.get('n', '')
    reported = data.get('target', '')
    reason = data.get('reason', '')
    
    # Здесь можно реализовать систему жалоб
    logger.warning(f"⚠️ Жалоба от {username} на {reported}: {reason}")
    emit('report_sent', {'ok': True})

@socketio.on('block_user')
def block_user(data):
    """Блокировка пользователя (только админ)"""
    username = data.get('n', '')
    target = data.get('target', '')
    
    # Проверка прав админа
    if username not in store.get_admins():
        return
    
    if target in store.users:
        store.banned_users.add(target)
        store.users[target].status = 'забанен'
        store._save_data()
        emit('user_blocked', {'n': target}, broadcast=True)
        logger.info(f"🚫 Пользователь {target} забанен админом {username}")

# ========== HTML ШАБЛОН (Python) ==========
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0d0d0d">
<title>Shugramm</title>
<style>
:root{--bg:#0d0d0d;--bg2:#1a1a1a;--bg3:#2a2a2a;--y:#FFD700;--g:#888;--w:#fff;--b:#3a3a3a;--gr:#4CAF50;--r:#f44;--p:#9b59b6;--o:#e67e22}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#000;height:100vh;height:100dvh;display:flex;justify-content:center;align-items:center;color:var(--w);user-select:none;overflow:hidden}
.app{width:100%;max-width:480px;height:100vh;height:100dvh;background:var(--bg);display:flex;flex-direction:column;position:relative}
.notification{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:400;background:var(--bg2);color:var(--w);padding:12px 20px;border-radius:0 0 12px 12px;font-size:14px;max-width:90%;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,.6);display:none;border-bottom:3px solid var(--y);font-weight:500}
.notification.show{display:block;animation:slideDown .3s ease}
@keyframes slideDown{from{transform:translateX(-50%) translateY(-100%)}to{transform:translateX(-50%) translateY(0)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.header{background:var(--bg2);padding:8px 16px;display:flex;align-items:center;border-bottom:1px solid var(--b);min-height:44px;flex-shrink:0}
.header-title{font-weight:600;font-size:17px;flex:1;display:flex;align-items:center;gap:8px}
.header-title .logo{color:var(--y);font-weight:800;font-size:20px}
.header-title .online-count{font-size:11px;color:var(--g);font-weight:400;margin-left:4px}
.btn{background:none;border:none;color:var(--w);font-size:18px;cursor:pointer;padding:6px;border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .2s}
.btn:active{background:var(--bg3)}
.nav{background:var(--bg2);display:flex;border-top:1px solid var(--b);padding:4px 0;padding-bottom:max(4px,env(safe-area-inset-bottom));flex-shrink:0}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:1px;cursor:pointer;color:var(--g);font-size:10px;padding:6px 4px;transition:color .2s;position:relative}
.nav-item.active{color:var(--y)}
.nav-item svg{width:22px;height:22px}
.nav-item .badge{position:absolute;top:2px;right:50%;transform:translateX(150%);background:var(--r);color:#fff;font-size:10px;min-width:18px;height:18px;border-radius:9px;display:flex;align-items:center;justify-content:center;padding:0 5px;font-weight:700}
.content{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;display:none;animation:fadeIn .3s ease}
.content.active{display:block}
.list-item{display:flex;align-items:center;padding:10px 16px;gap:10px;cursor:pointer;transition:background .15s}
.list-item:active{background:var(--bg3)}
.avatar{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:19px;color:#000;flex-shrink:0;overflow:hidden;background:var(--y);position:relative}
.avatar img{width:100%;height:100%;object-fit:cover}
.avatar .online-dot{position:absolute;bottom:0;right:0;width:12px;height:12px;border-radius:50%;border:2px solid var(--bg);background:var(--gr)}
.list-info{flex:1;min-width:0;border-bottom:1px solid rgba(255,255,255,.05);padding-bottom:10px}
.list-name{font-weight:500;font-size:15px;display:flex;align-items:center;gap:6px}
.list-preview{font-size:13px;color:var(--g);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-time{font-size:10px;color:var(--g);flex-shrink:0;margin-left:8px}
.unread-badge{background:var(--y);color:#000;font-size:11px;font-weight:700;min-width:20px;height:20px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;padding:0 6px;margin-left:8px}
.search-box{padding:8px 16px;background:var(--bg2);border-bottom:1px solid var(--b);position:sticky;top:0;z-index:5}
.search-box input{width:100%;padding:8px 14px;background:var(--bg3);border:1px solid var(--b);border-radius:18px;color:var(--w);font-size:13px;outline:none;transition:border .3s}
.search-box input:focus{border-color:var(--y)}
.msg-row{display:flex;gap:4px;margin-bottom:2px;padding:2px 14px;animation:fadeIn .2s ease}
.msg-row.mine{flex-direction:row-reverse}
.msg-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#000;flex-shrink:0;margin-top:auto;overflow:hidden;background:var(--y);cursor:pointer}
.msg-avatar img{width:100%;height:100%;object-fit:cover}
.msg-content{max-width:75%;display:flex;flex-direction:column}
.msg-bubble{max-width:100%;padding:7px 10px;border-radius:14px;font-size:14px;line-height:1.4;word-wrap:break-word;overflow-wrap:break-word;white-space:pre-wrap;background:var(--bg3);word-break:break-word}
.msg-row.mine .msg-bubble{background:var(--y);color:#000}
.msg-bubble img{max-width:220px;max-height:280px;border-radius:8px;cursor:pointer;display:block;object-fit:cover}
.msg-bubble video{max-width:220px;max-height:280px;border-radius:8px;display:block}
.msg-bubble audio{width:180px}
.msg-bubble .reply{font-size:11px;color:var(--g);border-left:2px solid var(--y);padding-left:6px;margin-bottom:4px;opacity:.7}
.msg-time{font-size:10px;color:var(--g);text-align:right;margin-top:1px;padding:0 3px;display:flex;gap:4px;justify-content:flex-end;align-items:center}
.msg-row.mine .msg-time{color:rgba(0,0,0,.5)}
.msg-actions{display:flex;gap:4px;margin-top:2px;justify-content:flex-end}
.msg-actions button{background:none;border:none;color:var(--g);font-size:10px;cursor:pointer;padding:2px 4px;border-radius:4px}
.msg-actions button:hover{background:var(--bg3)}
.input-bar{display:flex;padding:6px 10px;background:var(--bg2);border-top:1px solid var(--b);gap:6px;align-items:center;flex-shrink:0}
.input-bar input{flex:1;padding:9px 14px;background:var(--bg3);border:1px solid var(--b);border-radius:18px;color:var(--w);font-size:14px;outline:none;transition:border .3s}
.input-bar input:focus{border-color:var(--y)}
.send-btn{width:34px;height:34px;border-radius:50%;background:var(--y);border:none;color:#000;font-size:16px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;transition:transform .2s}
.send-btn:active{transform:scale(.9)}
.file-preview{display:flex;align-items:center;gap:6px;padding:4px 10px;background:var(--bg3);border-radius:12px;font-size:11px;color:var(--g);flex-shrink:0}
.file-preview img{width:24px;height:24px;border-radius:4px;object-fit:cover}
.post-card{background:var(--bg2);margin-bottom:12px;animation:fadeIn .3s ease}
.post-header{display:flex;align-items:center;padding:10px 14px;gap:8px}
.post-avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:14px;color:#000;overflow:hidden;background:var(--y);cursor:pointer}
.post-avatar img{width:100%;height:100%;object-fit:cover}
.post-user{font-weight:500;font-size:14px;cursor:pointer}
.post-user:hover{color:var(--y)}
.post-date{font-size:11px;color:var(--g)}
.post-media{width:100%;max-height:400px;object-fit:cover;cursor:pointer;display:block}
.post-actions{display:flex;padding:8px 14px;gap:20px;border-top:1px solid var(--b)}
.post-action{background:none;border:none;color:var(--w);cursor:pointer;display:flex;align-items:center;gap:5px;font-size:13px;padding:0;transition:color .2s}
.post-action:hover{color:var(--y)}
.post-action.liked{color:var(--r)}
.post-caption{padding:0 14px 8px;font-size:13px;line-height:1.4}
.post-caption b{cursor:pointer}
.post-caption b:hover{color:var(--y)}
.post-comments{padding:0 14px 8px}
.comment-row{display:flex;gap:6px;margin-bottom:3px;font-size:12px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.05)}
.comment-avatar{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:#000;flex-shrink:0;overflow:hidden;background:var(--y)}
.comment-avatar img{width:100%;height:100%;object-fit:cover}
.comment-body{flex:1;line-height:1.3}
.comment-body b{cursor:pointer}
.comment-body b:hover{color:var(--y)}
.comment-input{display:flex;padding:8px 14px;border-top:1px solid var(--b);gap:8px;background:var(--bg)}
.comment-input input{flex:1;background:none;border:none;color:var(--w);font-size:13px;outline:none;padding:4px 0}
.comment-input input::placeholder{color:var(--g)}
.comment-input button{background:none;border:none;color:var(--y);font-weight:600;cursor:pointer;font-size:13px}
.profile-section{text-align:center;padding:24px;background:var(--bg2);margin:8px;border-radius:12px}
.profile-avatar{width:80px;height:80px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:600;color:#000;margin:0 auto 10px;cursor:pointer;overflow:hidden;background:var(--y);border:3px solid var(--y);transition:transform .3s}
.profile-avatar:active{transform:scale(.95)}
.profile-avatar img{width:100%;height:100%;object-fit:cover}
.profile-name{font-size:18px;font-weight:600}
.profile-bio{color:var(--g);font-size:13px;margin-top:4px}
.profile-status{font-size:12px;margin-top:2px;padding:2px 10px;border-radius:10px;display:inline-block}
.profile-status.online{color:var(--gr)}
.profile-status.offline{color:var(--g)}
.settings-group{padding:8px}
.setting-item{display:flex;justify-content:space-between;align-items:center;padding:14px;background:var(--bg2);margin-bottom:6px;border-radius:10px;cursor:pointer;transition:background .2s}
.setting-item:active{background:var(--bg3)}
.setting-label{font-size:14px}
.setting-value{color:var(--g);font-size:13px}
.login-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
.login-card{text-align:center;padding:28px 20px;width:90%;max-width:340px;animation:fadeIn .3s ease}
.login-logo{width:72px;height:72px;background:var(--y);border-radius:18px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:30px;color:#000;font-weight:800}
.login-card h1{font-size:24px;font-weight:700;margin-bottom:2px}
.login-card p{color:var(--g);font-size:13px;margin-bottom:18px}
.form-input{width:100%;padding:12px 14px;background:var(--bg2);border:1px solid var(--b);border-radius:10px;color:var(--w);font-size:14px;margin-bottom:8px;outline:none;text-align:center;transition:border .3s}
.form-input:focus{border-color:var(--y)}
.form-input.error{border-color:var(--r)}
.form-btn{width:100%;padding:12px;background:var(--y);color:#000;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;margin-top:4px;transition:opacity .2s}
.form-btn:active{opacity:.8}
.form-link{background:none;border:none;color:var(--y);font-size:13px;cursor:pointer;margin-top:10px;text-decoration:underline}
.code-box{background:var(--bg3);padding:12px;border-radius:8px;font-size:26px;letter-spacing:8px;font-weight:600;color:var(--y);margin:10px 0;font-family:monospace}
.hidden{display:none!important}
.media-viewer{position:fixed;top:0;left:0;right:0;bottom:0;background:#000;z-index:300;display:none;align-items:center;justify-content:center}
.media-viewer.show{display:flex}
.media-viewer img{max-width:100%;max-height:100vh;object-fit:contain}
.media-viewer video{max-width:100%;max-height:100vh}
.media-close{position:absolute;top:14px;right:14px;width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.15);border:none;color:#fff;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;z-index:301}
.fab{position:fixed;bottom:76px;right:14px;width:48px;height:48px;border-radius:14px;background:var(--y);color:#000;border:none;font-size:22px;cursor:pointer;z-index:10;display:none;align-items:center;justify-content:center;box-shadow:0 2px 12px rgba(255,215,0,.3);transition:transform .2s}
.fab:active{transform:scale(.9)}
.fab.show{display:flex}
.typing-indicator{font-size:12px;color:var(--g);padding:4px 14px;font-style:italic;min-height:20px;transition:opacity .3s}
.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:500;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-content{background:var(--bg2);border-radius:16px;padding:24px;max-width:340px;width:90%;max-height:80vh;overflow-y:auto;animation:fadeIn .3s ease}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}
.stats-item{background:var(--bg3);padding:12px;border-radius:8px;text-align:center}
.stats-item .value{font-size:24px;font-weight:700;color:var(--y)}
.stats-item .label{font-size:11px;color:var(--g);margin-top:2px}
.emoji-picker{display:grid;grid-template-columns:repeat(6,1fr);gap:4px;padding:8px;background:var(--bg2);border-radius:12px;margin-bottom:8px}
.emoji-picker span{font-size:24px;cursor:pointer;text-align:center;padding:4px;border-radius:4px;transition:background .2s}
.emoji-picker span:hover{background:var(--bg3)}
.empty-state{text-align:center;padding:40px;color:var(--g)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.empty-state h3{color:var(--w);margin-bottom:4px}
.loading-spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--bg3);border-top:2px solid var(--y);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
.toast-container{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:600;display:flex;flex-direction:column;gap:6px;max-width:90%;pointer-events:none}
.toast{padding:10px 16px;background:var(--bg2);border-radius:10px;color:var(--w);font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.5);animation:fadeIn .3s ease;border-left:3px solid var(--y);pointer-events:auto}
.toast.error{border-left-color:var(--r)}
.toast.success{border-left-color:var(--gr)}
.toast.info{border-left-color:var(--y)}
</style>
</head>
<body>
<div class="app">
<div class="notification" id="notification"></div>
<div class="toast-container" id="toastContainer"></div>
<div class="header">
<div class="header-title">
<span class="logo">⚡</span>
Shugramm
<span class="online-count" id="onlineCount"></span>
</div>
<button class="btn" onclick="share()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
<path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8"/>
<polyline points="16 6 12 2 8 6"/>
<line x1="12" y1="2" x2="12" y2="15"/>
</svg>
</button>
</div>
<div class="content active" id="chatsContent"></div>
<div class="content" id="usersContent"></div>
<div class="content" id="postsContent"></div>
<div class="content" id="settingsContent"></div>
<div id="chatWindow" class="hidden" style="flex:1;display:none;flex-direction:column;min-height:0">
<div class="header">
<button class="btn" onclick="closeChat()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><polyline points="15 18 9 12 15 6"/></svg>
</button>
<span style="font-weight:500;flex:1" id="chatTitle"></span>
<button class="btn" onclick="searchInChat()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
</button>
</div>
<div id="messages" style="flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:6px 0;min-height:0"></div>
<div class="typing-indicator" id="typingIndicator"></div>
<div class="input-bar">
<button class="btn" onclick="toggleEmojiPicker()">😊</button>
<button class="btn" onclick="document.getElementById('fileInput').click()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
</button>
<button class="btn" onclick="startRecording()" id="recordBtn">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
</button>
<div id="emojiPicker" class="emoji-picker" style="display:none;position:absolute;bottom:60px;background:var(--bg2);border-radius:12px;padding:8px;z-index:10;max-width:280px"></div>
<input type="text" id="msgInput" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')sendMsg()" oninput="handleTyping()">
<button class="send-btn" onclick="sendMsg()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
</button>
</div>
</div>
<button class="fab" id="fab" onclick="createPost()">+</button>
<div class="nav" id="nav" style="display:none">
<div class="nav-item active" onclick="switchTab('chats')">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
Чаты
<span class="badge" id="chatBadge" style="display:none">0</span>
</div>
<div class="nav-item" onclick="switchTab('users')">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4-4v2"/><circle cx="9" cy="7" r="4"/></svg>
Контакты
</div>
<div class="nav-item" onclick="switchTab('posts')">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
Посты
</div>
<div class="nav-item" onclick="switchTab('settings')">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
Ещё
</div>
</div>
<div class="media-viewer" id="mediaViewer">
<button class="media-close" onclick="closeMedia()">✕</button>
<img id="mediaImg" style="display:none">
<video id="mediaVid" controls style="display:none"></video>
</div>
<div class="modal" id="statsModal">
<div class="modal-content">
<h2 style="text-align:center;margin-bottom:12px">📊 Статистика</h2>
<div id="statsContent"></div>
<button onclick="closeStats()" style="width:100%;padding:10px;background:var(--bg3);border:none;color:var(--w);border-radius:10px;margin-top:16px;cursor:pointer">Закрыть</button>
</div>
</div>
<div class="modal" id="searchModal">
<div class="modal-content">
<h2 style="margin-bottom:8px">🔍 Поиск</h2>
<input type="text" id="searchChatInput" placeholder="Поиск в чате..." oninput="searchMessages()" style="width:100%;padding:8px 14px;background:var(--bg3);border:1px solid var(--b);border-radius:10px;color:var(--w);font-size:14px;outline:none;margin-bottom:12px">
<div id="searchResults"></div>
<button onclick="closeSearch()" style="width:100%;padding:10px;background:var(--bg3);border:none;color:var(--w);border-radius:10px;margin-top:8px;cursor:pointer">Закрыть</button>
</div>
</div>
<div class="login-screen" id="loginScreen">
<div class="login-card">
<div id="step1">
<div class="login-logo">⚡</div>
<h1>Shugramm</h1>
<p>Введите номер телефона для входа</p>
<input type="tel" class="form-input" id="phoneInput" placeholder="+7 999 123-45-67">
<button class="form-btn" onclick="requestCode()">Получить код</button>
</div>
<div id="step2" class="hidden">
<div class="login-logo">⚡</div>
<h1>Код</h1>
<p>Отправлен на <span id="phoneDisplay" style="color:var(--y);font-weight:600"></span></p>
<div class="code-box" id="codeDisplay"></div>
<input type="text" class="form-input" id="codeInput" placeholder="••••••" maxlength="6" style="font-size:20px;letter-spacing:6px">
<button class="form-btn" onclick="verifyCode()">Подтвердить</button>
<button class="form-link" onclick="backToPhone()">Изменить номер</button>
</div>
<div id="step3" class="hidden">
<div class="login-logo">⚡</div>
<h1>Регистрация</h1>
<input type="password" class="form-input" id="passwordInput" placeholder="Придумайте пароль (мин. 4 символа)">
<input type="text" class="form-input" id="nameInput" placeholder="Имя пользователя (2-20 символов)">
<button class="form-btn" onclick="setPassword()">Зарегистрироваться</button>
</div>
<div id="step4" class="hidden">
<div class="login-logo">⚡</div>
<h1>Вход</h1>
<p style="color:var(--y);font-weight:600" id="loginUsername"></p>
<input type="password" class="form-input" id="loginPassword" placeholder="Введите пароль">
<button class="form-btn" onclick="loginUser()">Войти</button>
<button class="form-link" onclick="backToStart()">Назад</button>
</div>
</div>
</div>
</div>
<input type="file" id="fileInput" accept="image/*,video/*,audio/*" style="display:none" onchange="handleFile(event)">
<input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="handleAvatar(event)">
<input type="file" id="postInput" accept="image/*,video/*" style="display:none" onchange="handlePost(event)">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
// ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
var s=io();
var u=null, ua=null, ch='general', pd='', lang='ru', token='';
var typingTimeout=null, mediaRecorder=null, audioChunks=[];
var unreadData={}, searchTimeout=null;
var savedToken=localStorage.getItem('shugramm_token')||'';

// ========== ИНИЦИАЛИЗАЦИЯ ==========
if(savedToken){ s.emit('auto_login',{token:savedToken}) }

// ========== УТИЛИТЫ ==========
function notify(msg, type='info'){
var n=document.getElementById('notification');
n.textContent=msg; n.className='notification '+type;
n.classList.add('show');
setTimeout(function(){n.classList.remove('show')}, 3000);
}

function toast(msg, type='info'){
var container=document.getElementById('toastContainer');
var t=document.createElement('div');
t.className='toast '+type;
t.textContent=msg;
container.appendChild(t);
setTimeout(function(){t.remove()}, 3000);
}

function getDate(ts){ return new Date(ts).toLocaleString() }

// ========== АВТОРИЗАЦИЯ ==========
function requestCode(){
var p=document.getElementById('phoneInput').value.trim();
if(p.length<10){ notify('Введите номер (минимум 10 цифр)','error'); return; }
s.emit('rc',{p:p});
}

function verifyCode(){
var c=document.getElementById('codeInput').value.trim();
if(c.length!==6){ notify('Введите 6 цифр','error'); return; }
s.emit('vc',{d:pd,c:c});
}

function setPassword(){
var p=document.getElementById('passwordInput').value.trim();
var n=document.getElementById('nameInput').value.trim();
if(!p||p.length<4){ notify('Пароль минимум 4 символа','error'); return; }
if(!n||n.length<2){ notify('Имя минимум 2 символа','error'); return; }
if(!/^[a-zA-Zа-яА-Я0-9_]{2,20}$/.test(n)){ notify('Имя: 2-20 символов (буквы, цифры, _)','error'); return; }
s.emit('sp',{d:pd,p:p,n:n});
}

function loginUser(){
var p=document.getElementById('loginPassword').value.trim();
if(!p){ notify('Введите пароль','error'); return; }
s.emit('li',{n:document.getElementById('loginUsername').textContent,p:p});
}

function backToPhone(){
document.getElementById('step2').classList.add('hidden');
document.getElementById('step1').classList.remove('hidden');
}

function backToStart(){
document.getElementById('step4').classList.add('hidden');
document.getElementById('step1').classList.remove('hidden');
}

// ========== SOCKET EVENTS ==========
s.on('cs',function(d){
pd=d.d;
document.getElementById('step1').classList.add('hidden');
document.getElementById('step2').classList.remove('hidden');
document.getElementById('phoneDisplay').textContent='+'+d.d;
document.getElementById('codeDisplay').textContent=d.c;
});

s.on('ue',function(d){
document.getElementById('step2').classList.add('hidden');
document.getElementById('step4').classList.remove('hidden');
document.getElementById('loginUsername').textContent=d.n;
});

s.on('nu',function(d){
pd=d.d;
document.getElementById('step2').classList.add('hidden');
document.getElementById('step3').classList.remove('hidden');
});

s.on('ro',function(d){ u=d.n; ua=d.a; token=d.token; localStorage.setItem('shugramm_token',token); enterApp(); });
s.on('lo',function(d){ u=d.n; ua=d.a; token=d.token; localStorage.setItem('shugramm_token',token); enterApp(); });
s.on('er',function(d){ notify(d.m,'error'); });
s.on('notify',function(d){ toast(d.n+': '+d.c,'info'); loadChats(); updateBadge(); });
s.on('avatar_updated',function(d){ if(u&&d.n!==u){ loadChats(); if(document.getElementById('usersContent').classList.contains('active')){ s.emit('gu',{n:u}); } }});
s.on('bio_updated',function(d){ if(d.n===u){ userBio=d.b; loadSettings(); }});
s.on('typing_status',function(d){ if(d.n!==u){ var el=document.getElementById('typingIndicator'); el.textContent=d.typing?d.n+' печатает...':''; }});
s.on('online_users',function(d){ document.getElementById('onlineCount').textContent='● '+d.users.length; });
s.on('user_blocked',function(d){ if(d.n===u){ notify('Вы были забанены','error'); logout(); }});
s.on('nu_user',function(d){ loadChats(); updateBadge(); if(document.getElementById('usersContent').classList.contains('active')){ s.emit('gu',{n:u}); } });
s.on('message_deleted',function(d){ if(d.ch===ch){ var msgs=document.getElementById('messages').children; for(var i=0;i<msgs.length;i++){ if(msgs[i].dataset.mid===d.mid){ msgs[i].remove(); break; } } }});
s.on('message_edited',function(d){ if(d.ch===ch){ var msgs=document.getElementById('messages').children; for(var i=0;i<msgs.length;i++){ if(msgs[i].dataset.mid===d.m.id){ msgs[i].querySelector('.msg-bubble').innerHTML=d.m.c; msgs[i].querySelector('.msg-time').innerHTML+=' ✎'; break; } } }});
s.on('search_results',function(d){ var el=document.getElementById('searchResults'); if(d.results.length===0){ el.innerHTML='<div style="color:var(--g);padding:8px">Ничего не найдено</div>'; return; } el.innerHTML=d.results.map(function(m){ return '<div style="padding:6px;border-bottom:1px solid var(--b);font-size:13px"><b>'+m.n+'</b>: '+m.c+' <span style="color:var(--g);font-size:10px">'+m.ts+'</span></div>'; }).join(''); });

// ========== ОСНОВНЫЕ ФУНКЦИИ ==========
function enterApp(){
document.getElementById('loginScreen').classList.add('hidden');
document.getElementById('nav').style.display='flex';
loadChats();
s.emit('get_online_users');
setInterval(function(){ s.emit('get_online_users'); }, 30000);
}

function loadChats(){
var h='<div class="list-item" onclick="openChat(\'general\',\'Общий чат\')">'+
'<div class="avatar">#</div>'+
'<div class="list-info"><div class="list-name">Общий чат</div>'+
'<div class="list-preview">Нажмите чтобы открыть</div></div></div>';
var chats=JSON.parse(localStorage.getItem('private_chats')||'[]');
for(var i=0;i<chats.length;i++){
var c=chats[i];
var ur=unreadData[c.id]||0;
h+='<div class="list-item" onclick="openChat(\''+c.id+'\',\''+c.name+'\')">'+
'<div class="avatar">'+c.avatar+'</div>'+
'<div class="list-info"><div class="list-name">'+c.name+'</div>'+
'<div class="list-preview">'+c.lastMsg+'</div></div>'+
(ur>0?'<div class="unread-badge">'+ur+'</div>':'')+'</div>';
}
document.getElementById('chatsContent').innerHTML=h;
updateBadge();
}

function updateBadge(){
var total=0; for(var k in unreadData){ total+=unreadData[k]; }
var badge=document.getElementById('chatBadge');
if(total>0){ badge.textContent=total; badge.style.display='flex'; }
else{ badge.style.display='none'; }
}

function switchTab(t){
document.querySelectorAll('.content').forEach(function(c){ c.classList.remove('active'); });
document.querySelectorAll('.nav-item').forEach(function(n){ n.classList.remove('active'); });
document.getElementById('fab').classList.remove('show');
document.getElementById('chatWindow').classList.add('hidden');
document.getElementById('chatWindow').style.display='none';
if(t==='chats'){ document.getElementById('chatsContent').classList.add('active'); document.querySelector('.nav-item:nth-child(1)').classList.add('active'); loadChats(); }
else if(t==='users'){ document.getElementById('usersContent').classList.add('active'); document.querySelector('.nav-item:nth-child(2)').classList.add('active'); s.emit('gu',{n:u}); }
else if(t==='posts'){ document.getElementById('postsContent').classList.add('active'); document.querySelector('.nav-item:nth-child(3)').classList.add('active'); document.getElementById('fab').classList.add('show'); s.emit('gp'); }
else{ document.getElementById('settingsContent').classList.add('active'); document.querySelector('.nav-item:nth-child(4)').classList.add('active'); loadSettings(); }
}

function loadSettings(){
var h='<div class="profile-section">'+
'<div class="profile-avatar" onclick="document.getElementById(\'avatarInput\').click()">'+(ua?'<img src="'+ua+'">':u[0])+'</div>'+
'<div class="profile-name">'+u+'</div>'+
'<div class="profile-bio">'+(userBio||'Нажмите чтобы добавить описание')+'</div></div>';
h+='<div class="settings-group">'+
'<div class="setting-item" onclick="editBio()"><span class="setting-label">✏️ Описание</span></div>'+
'<div class="setting-item" onclick="changeLang()"><span class="setting-label">🌐 Язык</span><span class="setting-value">'+lang+'</span></div>'+
'<div class="setting-item" onclick="toggleTheme()"><span class="setting-label">🌓 Тема</span><span class="setting-value">'+(localStorage.getItem('theme')==='light'?'Светлая':'Темная')+'</span></div>'+
'<div class="setting-item" onclick="showStats()"><span class="setting-label">📊 Статистика</span></div>'+
'<div class="setting-item" onclick="share()"><span class="setting-label">🔗 Поделиться</span></div>'+
'<div class="setting-item" onclick="doLogout()"><span class="setting-label" style="color:var(--r)">🚪 Выйти</span></div></div>';
document.getElementById('settingsContent').innerHTML=h;
}

var userBio='';
function editBio(){ var b=prompt('Описание:',userBio||''); if(b!==null){ userBio=b; s.emit('ub',{n:u,b:b}); loadSettings(); } }
function changeLang(){ lang=lang==='ru'?'en':'ru'; s.emit('ul2',{n:u,l:lang}); loadSettings(); }
function toggleTheme(){ var root=document.documentElement; var dark=root.style.getPropertyValue('--bg').trim()==='#0d0d0d'; if(dark){ root.style.setProperty('--bg','#f5f5f5'); root.style.setProperty('--bg2','#ffffff'); root.style.setProperty('--bg3','#e8e8e8'); root.style.setProperty('--w','#000000'); root.style.setProperty('--b','#ddd'); localStorage.setItem('theme','light'); } else { root.style.setProperty('--bg','#0d0d0d'); root.style.setProperty('--bg2','#1a1a1a'); root.style.setProperty('--bg3','#2a2a2a'); root.style.setProperty('--w','#ffffff'); root.style.setProperty('--b','#3a3a3a'); localStorage.setItem('theme','dark'); } loadSettings(); }
function doLogout(){ localStorage.removeItem('shugramm_token'); u=null; ua=null; location.reload(); }

// ========== ЧАТ ==========
function openChat(id,nm){
ch=id;
document.querySelectorAll('.content').forEach(function(c){ c.classList.remove('active'); });
document.getElementById('chatWindow').classList.remove('hidden');
document.getElementById('chatWindow').style.display='flex';
document.getElementById('chatTitle').textContent=nm;
document.getElementById('messages').innerHTML='';
document.getElementById('typingIndicator').textContent='';
s.emit('jc',{ch:id,n:u});
}

function closeChat(){
document.getElementById('chatWindow').classList.add('hidden');
document.getElementById('chatWindow').style.display='none';
document.getElementById('chatsContent').classList.add('active');
loadChats();
}

function sendMsg(){
var i=document.getElementById('msgInput');
var t=i.value.trim();
if(!t)return;
s.emit('sm',{n:u,ch:ch,t:'text',c:t});
i.value='';
s.emit('typing',{ch:ch,n:u,typing:false});
var preview=document.querySelector('.file-preview');
if(preview)preview.remove();
}

function handleTyping(){
if(typingTimeout)clearTimeout(typingTimeout);
s.emit('typing',{ch:ch,n:u,typing:true});
typingTimeout=setTimeout(function(){ s.emit('typing',{ch:ch,n:u,typing:false}); },1500);
}

function handleFile(e){
var f=e.target.files[0]; if(!f)return;
if(f.size>100*1024*1024){ notify('Файл слишком большой (макс. 100MB)','error'); return; }
// Превью
if(f.type.startsWith('image/')){
var reader=new FileReader();
reader.onload=function(ev){ showFilePreview(ev.target.result,f.name); };
reader.readAsDataURL(f);
}
var r=new FileReader();
r.onload=function(ev){ s.emit('sm',{n:u,ch:ch,t:f.type.startsWith('video')?'vid':f.type.startsWith('audio')?'audio':'file',c:ev.target.result}); };
r.readAsDataURL(f);
}

function showFilePreview(src,name){
var preview=document.createElement('div');
preview.className='file-preview';
preview.innerHTML='<img src="'+src+'" style="width:24px;height:24px;border-radius:4px;object-fit:cover"> <span>'+name+'</span> <span onclick="this.parentElement.remove()" style="cursor:pointer">✕</span>';
var inputBar=document.querySelector('.input-bar');
var existing=inputBar.querySelector('.file-preview');
if(existing)existing.remove();
inputBar.insertBefore(preview, inputBar.querySelector('#msgInput'));
}

function handleAvatar(e){
var f=e.target.files[0]; if(!f)return;
var r=new FileReader();
r.onload=function(ev){ ua=ev.target.result; s.emit('ua',{n:u,a:ev.target.result}); loadSettings(); };
r.readAsDataURL(f);
}

function handlePost(e){
var f=e.target.files[0]; if(!f)return;
var r=new FileReader();
r.onload=function(ev){ var c=prompt('Описание:',''); s.emit('cp',{n:u,m:ev.target.result,mt:f.type.startsWith('video')?'video':'image',c:c||''}); };
r.readAsDataURL(f);
}

function createPost(){ document.getElementById('postInput').click(); }

s.on('ch',function(d){
document.getElementById('messages').innerHTML='';
if(d.ms){ d.ms.forEach(function(m){ addMsg(m); }); }
var mc=document.getElementById('messages');
setTimeout(function(){ mc.scrollTop=mc.scrollHeight; },100);
});

s.on('nm',function(d){
if(d.ch===ch){ addMsg(d.m); var mc=document.getElementById('messages'); var isAtBottom=mc.scrollHeight-mc.scrollTop-mc.clientHeight<50; if(isAtBottom){ setTimeout(function(){ mc.scrollTop=mc.scrollHeight; },100); } }
});

function addMsg(m){
var c=document.getElementById('messages');
var im=m.n===u;
var d=document.createElement('div');
d.className='msg-row '+(im?'mine':'');
d.dataset.mid=m.i;
var txt=m.c?m.c.replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';
var ct='';
if(m.t==='img'){ ct='<img src="'+m.c+'" onclick="viewMedia(\''+m.c+'\',\'img\')" loading="lazy">'; }
else if(m.t==='vid'){ ct='<video src="'+m.c+'" controls preload="none"></video>'; }
else if(m.t==='audio'){ ct='<audio src="'+m.c+'" controls preload="none"></audio>'; }
else { ct=txt; }
var av=m.a&&m.a.startsWith('data:')?'<img src="'+m.a+'">':m.n[0];
var actions='';
if(im){ actions='<div class="msg-actions"><button onclick="editMessage(\''+m.i+'\')">✎</button><button onclick="deleteMessage(\''+m.i+'\')">✕</button></div>'; }
d.innerHTML='<div class="msg-avatar" onclick="openProfile(\''+m.n+'\')">'+av+'</div>'+
'<div class="msg-content"><div class="msg-bubble">'+ct+'</div>'+
'<div class="msg-time">'+m.ts+(m.edited?' ✎':'')+'</div>'+actions+'</div>';
c.appendChild(d);
}

function editMessage(mid){ var newText=prompt('Редактировать сообщение:'); if(newText&&newText.trim()){ s.emit('edit_message',{n:u,ch:ch,mid:mid,c:newText.trim()}); } }
function deleteMessage(mid){ if(confirm('Удалить сообщение?')){ s.emit('delete_message',{n:u,ch:ch,mid:mid}); } }

// ========== ПОЛЬЗОВАТЕЛИ ==========
s.on('ul',function(d){
var h='';
d.u.forEach(function(u2){
var av=u2.a&&u2.a.startsWith('data:')?'<img src="'+u2.a+'">':u2.n[0];
var statusClass=u2.online?'online':'offline';
h+='<div class="list-item" onclick="startPrivate(\''+u2.n+'\')">'+
'<div class="avatar">'+av+'</div>'+
'<div class="list-info"><div class="list-name">'+u2.n+'</div>'+
'<div class="list-preview"><span class="profile-status '+statusClass+'">'+(u2.online?'🟢 В сети':'⚫ Был недавно')+'</span></div></div></div>';
});
document.getElementById('usersContent').innerHTML=h||'<div class="empty-state"><div class="icon">👤</div><h3>Нет контактов</h3><p style="color:var(--g)">Пригласите друзей в Shugramm!</p></div>';
});

function startPrivate(t){ s.emit('sp2',{n:u,t:t}); }

s.on('po',function(d){
ch=d.ch;
document.querySelectorAll('.content').forEach(function(c){ c.classList.remove('active'); });
document.getElementById('chatWindow').classList.remove('hidden');
document.getElementById('chatWindow').style.display='flex';
document.getElementById('chatTitle').textContent=d.t;
document.getElementById('messages').innerHTML='';
if(d.ms){ d.ms.forEach(function(m){ addMsg(m); }); }
var mc=document.getElementById('messages');
setTimeout(function(){ mc.scrollTop=mc.scrollHeight; },100);
var chats=JSON.parse(localStorage.getItem('private_chats')||'[]');
var found=false;
for(var i=0;i<chats.length;i++){ if(chats[i].id===d.ch){ found=true; break; } }
if(!found){ var av=d.a&&d.a.startsWith('data:')?'<img src="'+d.a+'">':d.t[0]; chats.push({id:d.ch,name:d.t,avatar:av,lastMsg:''}); }
localStorage.setItem('private_chats',JSON.stringify(chats));
});

// ========== ПОСТЫ ==========
s.on('pl',function(d){
var h='';
if(d.p.length===0){ h='<div class="empty-state"><div class="icon">📸</div><h3>Нет постов</h3><p style="color:var(--g)">Создайте свой первый пост!</p></div>'; }
else { d.p.forEach(function(p){ h+=buildPost(p); }); }
document.getElementById('postsContent').innerHTML=h;
});

s.on('np',function(d){ var el=document.getElementById('postsContent'); if(el.classList.contains('active')){ el.insertAdjacentHTML('afterbegin',buildPost(d.p)); } });
s.on('pu',function(d){ var el=document.getElementById(d.p.id); if(el){ el.outerHTML=buildPost(d.p); } });

function buildPost(p){
var delBtn=(p.n===u)?'<button class="post-action" onclick="deletePost(\''+p.id+'\')" style="margin-left:auto;color:var(--r);font-size:16px">✕</button>':'';
var av=p.a&&p.a.startsWith('data:')?'<img src="'+p.a+'">':p.n[0];
var likeClass=p.l&&p.l.includes(u)?'liked':'';
return '<div class="post-card" id="'+p.id+'">'+
'<div class="post-header"><div class="post-avatar" onclick="openProfile(\''+p.n+'\')">'+av+'</div>'+
'<div><div class="post-user" onclick="openProfile(\''+p.n+'\')">'+p.n+'</div><div class="post-date">'+p.ts+'</div></div>'+delBtn+'</div>'+
(p.mt==='image'?'<img class="post-media" src="'+p.m+'" onclick="viewMedia(\''+p.m+'\',\'img\')" loading="lazy">':p.mt==='video'?'<video class="post-media" src="'+p.m+'" controls preload="none"></video>':'')+
'<div class="post-actions"><button class="post-action '+likeClass+'" onclick="likePost(\''+p.id+'\')">❤️ '+p.likes_count+'</button>'+
'<button class="post-action">💬 '+p.comments_count+'</button></div>'+
'<div class="post-caption"><b onclick="openProfile(\''+p.n+'\')">'+p.n+'</b> '+p.c+'</div>'+
'<div class="post-comments">'+p.cm.map(function(c){ var ca=c.a&&c.a.startsWith('data:')?'<img src="'+c.a+'">':c.n[0]; return '<div class="comment-row"><div class="comment-avatar">'+ca+'</div><div class="comment-body"><b onclick="openProfile(\''+c.n+'\')">'+c.n+'</b> '+c.c+'</div></div>'; }).join('')+'</div>'+
'<div class="comment-input"><input id="ci_'+p.id+'" placeholder="Комментарий..." onkeypress="if(event.key===\'Enter\')addComment(\''+p.id+'\')"><button onclick="addComment(\''+p.id+'\')">Отправить</button></div></div>';
}

function deletePost(pid){
if(confirm('Удалить пост?')){
var xhr=new XMLHttpRequest();
xhr.open('POST','/delete_post',true);
xhr.setRequestHeader('Content-Type','application/json');
xhr.send(JSON.stringify({pid:pid}));
setTimeout(function(){ s.emit('gp'); },500);
}
}

function likePost(pid){ s.emit('lp',{pid:pid,n:u}); }
function addComment(pid){ var i=document.getElementById('ci_'+pid); var t=i.value.trim(); if(!t)return; s.emit('cmp',{pid:pid,n:u,c:t}); i.value=''; }

// ========== ЭМОДЗИ ==========
function toggleEmojiPicker(){
var picker=document.getElementById('emojiPicker');
if(picker.style.display==='block'){ picker.style.display='none'; return; }
var emojis=['😊','😂','❤️','🔥','👍','👏','🎉','✨','💪','🙏','😍','🤣','💯','🎊','🌟','💎','👀','💔','😢','🤗','🤔','😎','🥰','💕','🌸','🌺','⭐','🌈','⚡','🎵'];
picker.innerHTML=emojis.map(function(e){ return '<span onclick="insertEmoji(\''+e+'\')">'+e+'</span>'; }).join('');
picker.style.display='block';
}

function insertEmoji(emoji){
var input=document.getElementById('msgInput');
input.value+=emoji;
input.focus();
document.getElementById('emojiPicker').style.display='none';
}

// ========== ГОЛОСОВЫЕ СООБЩЕНИЯ ==========
function startRecording(){
if(mediaRecorder&&mediaRecorder.state==='recording'){
mediaRecorder.stop();
document.getElementById('recordBtn').style.color='var(--w)';
return;
}
navigator.mediaDevices.getUserMedia({audio:true})
.then(function(stream){
mediaRecorder=new MediaRecorder(stream);
audioChunks=[];
mediaRecorder.ondataavailable=function(e){ audioChunks.push(e.data); };
mediaRecorder.onstop=function(){
var blob=new Blob(audioChunks,{type:'audio/webm'});
var reader=new FileReader();
reader.onload=function(e){ s.emit('sm',{n:u,ch:ch,t:'audio',c:e.target.result}); };
reader.readAsDataURL(blob);
stream.getTracks().forEach(function(t){ t.stop(); });
};
mediaRecorder.start();
document.getElementById('recordBtn').style.color='var(--r)';
toast('🎤 Запись... Нажмите еще раз для остановки','info');
})
.catch(function(){ toast('❌ Нет доступа к микрофону','error'); });
}

// ========== ПОИСК ==========
function searchInChat(){
document.getElementById('searchModal').classList.add('show');
document.getElementById('searchResults').innerHTML='';
document.getElementById('searchChatInput').value='';
}

function searchMessages(){
clearTimeout(searchTimeout);
var q=document.getElementById('searchChatInput').value.trim();
if(!q){ document.getElementById('searchResults').innerHTML=''; return; }
searchTimeout=setTimeout(function(){ s.emit('search_messages',{ch:ch,q:q}); },300);
}

function closeSearch(){ document.getElementById('searchModal').classList.remove('show'); }

// ========== СТАТИСТИКА ==========
function showStats(){
var modal=document.getElementById('statsModal');
modal.classList.add('show');
fetch('/api/stats')
.then(function(r){ return r.json(); })
.then(function(data){
document.getElementById('statsContent').innerHTML=
'<div class="stats-grid">'+
'<div class="stats-item"><div class="value">'+data.total_users+'</div><div class="label">Всего пользователей</div></div>'+
'<div class="stats-item"><div class="value">'+data.online_users+'</div><div class="label">Сейчас онлайн</div></div>'+
'<div class="stats-item"><div class="value">'+data.total_posts+'</div><div class="label">Всего постов</div></div>'+
'<div class="stats-item"><div class="value">'+data.total_chats+'</div><div class="label">Всего чатов</div></div>'+
'<div class="stats-item"><div class="value">'+data.total_messages+'</div><div class="label">Всего сообщений</div></div>'+
'</div>';
});
}

function closeStats(){ document.getElementById('statsModal').classList.remove('show'); }

// ========== МЕДИА ==========
function viewMedia(src,tp){
var mv=document.getElementById('mediaViewer'); mv.classList.add('show');
if(tp==='img'){ document.getElementById('mediaImg').src=src; document.getElementById('mediaImg').style.display='block'; document.getElementById('mediaVid').style.display='none'; }
else { document.getElementById('mediaVid').src=src; document.getElementById('mediaVid').style.display='block'; document.getElementById('mediaImg').style.display='none'; }
}

function closeMedia(){ document.getElementById('mediaViewer').classList.remove('show'); }

// ========== ПРОФИЛЬ ==========
function openProfile(name){ toast('👤 Профиль пользователя: '+name,'info'); }

// ========== ШАРИНГ ==========
function share(){ s.emit('sh'); }
s.on('sl',function(d){ var l='https://'+d.l; if(navigator.clipboard){ navigator.clipboard.writeText(l).then(function(){ toast('Ссылка скопирована!','success'); }); } else { prompt('Ссылка:',l); } });

// ========== ГОРЯЧИЕ КЛАВИШИ ==========
document.addEventListener('keydown',function(e){
if(e.ctrlKey&&e.key==='Enter'){ sendMsg(); e.preventDefault(); }
if(e.key==='Escape'){ closeMedia(); closeStats(); closeSearch(); }
});

// ========== ЗАГРУЗКА ТЕМЫ ==========
var savedTheme=localStorage.getItem('theme')||'dark';
if(savedTheme==='light'){ var root=document.documentElement; root.style.setProperty('--bg','#f5f5f5'); root.style.setProperty('--bg2','#ffffff'); root.style.setProperty('--bg3','#e8e8e8'); root.style.setProperty('--w','#000000'); root.style.setProperty('--b','#ddd'); }

// ========== ЗАПРОС УВЕДОМЛЕНИЙ ==========
if('Notification' in window && Notification.permission==='default'){ Notification.requestPermission(); }

console.log('⚡ Shugramm v2.0 - Добро пожаловать!');
</script>
</body>
</html>
'''

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    logger.info(f"🚀 Запуск Shugramm сервера на порту {Config.PORT}")
    logger.info(f"📊 Режим: {'DEBUG' if Config.DEBUG else 'PRODUCTION'}")
    logger.info(f"💾 Файл данных: {Config.DATA_FILE}")
    
    # Создание файла данных при первом запуске
    if not os.path.exists(Config.DATA_FILE):
        store._save_data()
        logger.info("✅ Создан новый файл данных")
    
    socketio.run(
        app,
        host='0.0.0.0',
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
        use_reloader=False
    )
