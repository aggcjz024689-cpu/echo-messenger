import os
import json
import secrets
import re
import asyncpg
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="ЭХО Мессенджер", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ========== ФИЛЬТР МАТА ==========
BAD_WORDS = ['хуй', 'пизд', 'бля', 'еба', 'залуп', 'мудак', 'гандон', 'пидор', 'сука', 'шлюха', 
             'хуе', 'хуи', 'ебан', 'ебот', 'сволоч', 'тварь', 'ублюд', 'дроч', 'хер', 'пох', 'нах', 
             'редиск', 'даун', 'лох']
BAD_PATTERN = re.compile('|'.join(re.escape(w) for w in BAD_WORDS), re.IGNORECASE)

def has_profanity(text: str) -> bool:
    return bool(BAD_PATTERN.search(text))

# ========== МОДЕЛИ ==========
class UserRegister(BaseModel):
    username: str
    display_name: str
    password: str
    phone: str

class UserLogin(BaseModel):
    username: str
    password: str

class BanUser(BaseModel):
    target_username: str
    admin_id: str

class UpdateProfile(BaseModel):
    display_name: str
    bio: str

class CreateGroup(BaseModel):
    name: str
    members: list

# ========== ГЛОБАЛЬНЫЕ ХРАНИЛИЩА ==========
active_connections = {}
online_users = {}

def get_avatar_color(name: str) -> str:
    colors = ['#9147ff', '#ff6b6b', '#4ade80', '#fbbf24', '#60a5fa', '#f472b6', '#34d399', '#a78bfa']
    index = ord(name[0]) % len(colors) if name else 0
    return colors[index]

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password TEXT NOT NULL,
            phone TEXT UNIQUE,
            bio TEXT DEFAULT '',
            avatar_color TEXT DEFAULT '#9147ff',
            is_admin BOOLEAN DEFAULT FALSE,
            is_banned BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_user_id TEXT,
            to_user_id TEXT,
            group_id TEXT,
            content TEXT,
            type TEXT DEFAULT 'text',
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_color TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT,
            user_id TEXT,
            joined_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (group_id, user_id)
        )
    ''')
    
    # Создаём админа
    admin = await conn.fetchrow("SELECT * FROM users WHERE username = '@admin'")
    if not admin:
        admin_id = secrets.token_urlsafe(16)
        await conn.execute('''
            INSERT INTO users (id, username, display_name, password, phone, bio, avatar_color, is_admin, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ''', admin_id, '@admin', 'Администратор', 'Admin2024Secure', '+79990000000', 
           'Главный администратор', '#ff4444', True, datetime.now())
        print("✅ Админ создан: @admin / Admin2024Secure")
    
    await conn.close()
    print("✅ База данных готова")

@app.on_event("startup")
async def startup():
    await init_db()

# ========== API ==========
@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/register")
async def register(user: UserRegister):
    if has_profanity(user.username) or has_profanity(user.display_name):
        raise HTTPException(400, "Недопустимые символы")
    
    clean_username = user.username if user.username.startswith('@') else f"@{user.username}"
    
    conn = await asyncpg.connect(DATABASE_URL)
    existing = await conn.fetchrow("SELECT * FROM users WHERE username = $1 OR phone = $2", clean_username, user.phone)
    if existing:
        await conn.close()
        raise HTTPException(400, "Username или телефон уже занят")
    
    user_id = secrets.token_urlsafe(16)
    await conn.execute('''
        INSERT INTO users (id, username, display_name, password, phone, avatar_color, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    ''', user_id, clean_username, user.display_name, user.password, user.phone, get_avatar_color(clean_username), datetime.now())
    
    await conn.close()
    return {"user_id": user_id, "username": clean_username, "display_name": user.display_name}

@app.post("/api/login")
async def login(user: UserLogin):
    conn = await asyncpg.connect(DATABASE_URL)
    db_user = await conn.fetchrow("SELECT * FROM users WHERE username = $1", user.username)
    await conn.close()
    
    if not db_user or db_user['password'] != user.password:
        raise HTTPException(401, "Неверные данные")
    if db_user['is_banned']:
        raise HTTPException(403, "Вы забанены")
    
    return {
        "user_id": db_user['id'],
        "username": db_user['username'],
        "display_name": db_user['display_name'],
        "is_admin": db_user['is_admin'],
        "phone": db_user['phone']
    }

@app.post("/api/ban")
async def ban_user(ban: BanUser):
    conn = await asyncpg.connect(DATABASE_URL)
    admin = await conn.fetchrow("SELECT * FROM users WHERE id = $1", ban.admin_id)
    if not admin or not admin['is_admin']:
        await conn.close()
        raise HTTPException(403, "Нет прав")
    
    target = await conn.fetchrow("SELECT * FROM users WHERE username = $1", ban.target_username)
    if not target:
        await conn.close()
        raise HTTPException(404, "Пользователь не найден")
    
    if target['is_admin']:
        await conn.close()
        raise HTTPException(403, "Нельзя забанить админа")
    
    await conn.execute("UPDATE users SET is_banned = TRUE WHERE id = $1", target['id'])
    await conn.close()
    
    if target['id'] in active_connections:
        await active_connections[target['id']].close(code=1008, reason="Вы забанены")
    
    return {"success": True}

@app.get("/api/users")
async def get_users():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT id, username, display_name, phone, is_admin, is_banned FROM users")
    await conn.close()
    return [dict(r) for r in rows]

@app.get("/api/users/search")
async def search_users(q: str):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT id, username, display_name, phone, is_admin, is_banned FROM users WHERE username ILIKE $1 OR display_name ILIKE $1", f"%{q}%")
    await conn.close()
    return [dict(r) for r in rows]

@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    await conn.close()
    if not user:
        raise HTTPException(404, "User not found")
    return {
        'id': user['id'],
        'username': user['username'],
        'display_name': user['display_name'],
        'phone': user['phone'],
        'bio': user['bio'],
        'is_admin': user['is_admin'],
        'is_banned': user['is_banned'],
        'is_online': online_users.get(user_id, False),
        'avatar_color': user['avatar_color']
    }

@app.put("/api/users/{user_id}/profile")
async def update_profile(user_id: str, profile: UpdateProfile):
    if has_profanity(profile.display_name):
        raise HTTPException(400, "Недопустимые символы")
    
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE users SET display_name = $1, bio = $2 WHERE id = $3", profile.display_name, profile.bio, user_id)
    await conn.close()
    return {"success": True}

@app.post("/api/groups")
async def create_group(group: CreateGroup):
    group_id = secrets.token_urlsafe(16)
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("INSERT INTO groups (id, name, avatar_color, created_by) VALUES ($1, $2, $3, $4)", 
                       group_id, group.name, get_avatar_color(group.name), group.members[0])
    for member in group.members:
        await conn.execute("INSERT INTO group_members (group_id, user_id) VALUES ($1, $2)", group_id, member)
    await conn.close()
    return {"group_id": group_id, "name": group.name}

@app.get("/api/groups")
async def get_groups():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT g.*, COUNT(gm.user_id) as members_count FROM groups g LEFT JOIN group_members gm ON g.id = gm.group_id GROUP BY g.id")
    await conn.close()
    return [dict(r) for r in rows]

@app.get("/api/groups/{group_id}/messages")
async def get_group_messages(group_id: str, limit: int = 50):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT * FROM messages WHERE group_id = $1 ORDER BY created_at DESC LIMIT $2", group_id, limit)
    await conn.close()
    return [dict(r) for r in reversed(rows)]

@app.get("/api/messages/{other_user_id}")
async def get_messages(other_user_id: str, user_id: str = None):
    if not user_id:
        return []
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch('''
        SELECT * FROM messages 
        WHERE (from_user_id = $1 AND to_user_id = $2) OR (from_user_id = $2 AND to_user_id = $1)
        ORDER BY created_at DESC LIMIT 50
    ''', user_id, other_user_id)
    await conn.close()
    return [dict(r) for r in reversed(rows)]

# ========== WEBSOCKET ==========
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = await websocket.receive_text()
    
    conn = await asyncpg.connect(DATABASE_URL)
    user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    await conn.close()
    
    if not user or user['is_banned']:
        await websocket.close(code=1008, reason="Access denied")
        return
    
    active_connections[user_id] = websocket
    online_users[user_id] = True
    await broadcast_users_list()
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get('type', 'text')
            
            if msg_type in ['text', 'image', 'video', 'audio'] and 'to_user_id' in message:
                msg_id = secrets.token_urlsafe(16)
                conn_msg = await asyncpg.connect(DATABASE_URL)
                await conn_msg.execute('''
                    INSERT INTO messages (id, from_user_id, to_user_id, content, type, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', msg_id, user_id, message['to_user_id'], message['content'], msg_type, datetime.now())
                await conn_msg.close()
                
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "message",
                        "id": msg_id,
                        "from": user_id,
                        "from_username": user['display_name'],
                        "content": message['content'],
                        "type2": msg_type,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            elif msg_type in ['text', 'image', 'video', 'audio'] and 'group_id' in message:
                msg_id = secrets.token_urlsafe(16)
                conn_msg = await asyncpg.connect(DATABASE_URL)
                await conn_msg.execute('''
                    INSERT INTO messages (id, from_user_id, group_id, content, type, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', msg_id, user_id, message['group_id'], message['content'], msg_type, datetime.now())
                await conn_msg.close()
                
                members = await conn_msg.fetch("SELECT user_id FROM group_members WHERE group_id = $1", message['group_id'])
                for member in members:
                    if member['user_id'] in active_connections and member['user_id'] != user_id:
                        await active_connections[member['user_id']].send_text(json.dumps({
                            "type": "group_message",
                            "id": msg_id,
                            "group_id": message['group_id'],
                            "from": user_id,
                            "from_username": user['display_name'],
                            "content": message['content'],
                            "type2": msg_type,
                            "timestamp": datetime.now().isoformat()
                        }))
            
            elif msg_type == 'typing' and 'to_user_id' in message:
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "typing",
                        "from": user_id
                    }))
            
            elif msg_type in ['call_offer', 'call_answer', 'ice_candidate', 'call_end', 'call_reject']:
                if 'to_user_id' in message and message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": msg_type,
                        "from": user_id,
                        "from_username": user['display_name'],
                        **{k: v for k, v in message.items() if k not in ['type', 'to_user_id']}
                    }))
                    
    except WebSocketDisconnect:
        pass
    finally:
        if user_id in active_connections:
            del active_connections[user_id]
        online_users[user_id] = False
        await broadcast_users_list()

async def broadcast_users_list():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT id, username, display_name, is_admin, is_banned FROM users")
    await conn.close()
    
    users_list = []
    for r in rows:
        users_list.append({
            'id': r['id'],
            'username': r['username'],
            'display_name': r['display_name'],
            'is_online': online_users.get(r['id'], False),
            'is_admin': r['is_admin'],
            'is_banned': r['is_banned']
        })
    
    # Группы для отображения
    conn2 = await asyncpg.connect(DATABASE_URL)
    groups_rows = await conn2.fetch("SELECT id, name FROM groups")
    await conn2.close()
    
    groups_list = [dict(r) for r in groups_rows]
    
    status_msg = json.dumps({"type": "users_list", "users": users_list, "groups": groups_list})
    for conn_ws in active_connections.values():
        try:
            await conn_ws.send_text(status_msg)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("=" * 50)
    print("🚀 ЭХО МЕССЕНДЖЕР ЗАПУЩЕН!")
    print(f"📡 http://localhost:{port}")
    print("=" * 50)
    print("👑 Админ: @admin / Admin2024Secure")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=port)