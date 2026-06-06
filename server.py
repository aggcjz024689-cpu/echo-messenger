import os
import json
import secrets
import re
import asyncpg
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import shutil

app = FastAPI(title="ЭХО Мессенджер", version="6.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL")

# Папка для аватарок
AVATAR_DIR = "avatars"
os.makedirs(AVATAR_DIR, exist_ok=True)

# Подключаем статику
app.mount("/avatars", StaticFiles(directory="avatars"), name="avatars")

# ========== ФИЛЬТР МАТА ==========
BAD_WORDS = ['хуй', 'пизд', 'бля', 'еба', 'залуп', 'мудак', 'гандон', 'пидор', 'сука', 'шлюха', 
             'хуе', 'хуи', 'ебан', 'ебот', 'сволоч', 'тварь', 'ублюд', 'дроч', 'хер', 'пох', 'нах']
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
calls_history = []

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
            avatar TEXT DEFAULT '',
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
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS hidden_messages (
            message_id TEXT,
            user_id TEXT,
            PRIMARY KEY (message_id, user_id)
        )
    ''')
    
    await conn.execute('''
        DO $$ 
        BEGIN 
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                           WHERE table_name='users' AND column_name='avatar') THEN
                ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT '';
            END IF;
        END $$;
    ''')
    
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

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ========== АВАТАРКИ ==========
@app.post("/api/users/{user_id}/avatar")
async def upload_avatar(user_id: str, file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Только изображения")
    
    ext = file.filename.split(".")[-1]
    filename = f"{user_id}.{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE users SET avatar = $1 WHERE id = $2", f"/avatars/{filename}", user_id)
    await conn.close()
    
    return {"avatar_url": f"/avatars/{filename}"}

@app.delete("/api/users/{user_id}/avatar")
async def delete_avatar(user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    user = await conn.fetchrow("SELECT avatar FROM users WHERE id = $1", user_id)
    if user and user['avatar']:
        filepath = user['avatar'].lstrip("/")
        if os.path.exists(filepath):
            os.remove(filepath)
        await conn.execute("UPDATE users SET avatar = '' WHERE id = $1", user_id)
    await conn.close()
    return {"success": True}

# ========== ПОЛЬЗОВАТЕЛИ ==========
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
        "avatar": db_user['avatar'] or '',
        "is_admin": db_user['is_admin'],
        "phone": db_user['phone']
    }

@app.get("/api/users")
async def get_users():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT id, username, display_name, avatar, is_admin, is_banned FROM users")
    await conn.close()
    return [dict(r) for r in rows]

@app.get("/api/users/search")
async def search_users(q: str):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT id, username, display_name, avatar, is_admin, is_banned FROM users WHERE username ILIKE $1 OR display_name ILIKE $1", f"%{q}%")
    await conn.close()
    return [dict(r) for r in rows]

@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    await conn.close()
    if not user:
        raise HTTPException(404, "User not found")
    return dict(user)

@app.put("/api/users/{user_id}/profile")
async def update_profile(user_id: str, profile: UpdateProfile):
    if has_profanity(profile.display_name):
        raise HTTPException(400, "Недопустимые символы")
    
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE users SET display_name = $1, bio = $2 WHERE id = $3", profile.display_name, profile.bio, user_id)
    await conn.close()
    return {"success": True}

# ========== БАН ==========
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

# ========== ДИАЛОГИ ==========
@app.get("/api/chats/{user_id}")
async def get_chats(user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch('''
        SELECT DISTINCT 
            CASE WHEN from_user_id = $1 THEN to_user_id ELSE from_user_id END as other_user_id
        FROM messages 
        WHERE from_user_id = $1 OR to_user_id = $1
    ''', user_id)
    
    chats = []
    for r in rows:
        if r['other_user_id']:
            other = await conn.fetchrow("SELECT id, username, display_name, avatar, is_admin, is_banned FROM users WHERE id = $1", r['other_user_id'])
            if other:
                chats.append({
                    'id': other['id'],
                    'username': other['username'],
                    'display_name': other['display_name'],
                    'avatar': other['avatar'] or '',
                    'is_online': online_users.get(other['id'], False),
                    'is_admin': other['is_admin'],
                    'is_banned': other['is_banned']
                })
    await conn.close()
    return chats

@app.get("/api/calls/{user_id}")
async def get_calls_history(user_id: str):
    user_calls = [c for c in calls_history if c.get('from_user_id') == user_id or c.get('to_user_id') == user_id]
    return sorted(user_calls, key=lambda x: x.get('timestamp', ''), reverse=True)[:50]

# ========== СООБЩЕНИЯ ==========
@app.get("/api/messages/{other_user_id}")
async def get_messages(other_user_id: str, user_id: str = None):
    if not user_id:
        return []
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch('''
        SELECT * FROM messages 
        WHERE ((from_user_id = $1 AND to_user_id = $2) OR (from_user_id = $2 AND to_user_id = $1))
        AND id NOT IN (SELECT message_id FROM hidden_messages WHERE user_id = $1)
        ORDER BY created_at ASC LIMIT 100
    ''', user_id, other_user_id)
    await conn.close()
    return [dict(r) for r in rows]

@app.delete("/api/messages/{message_id}/for-me")
async def delete_message_for_me(message_id: str, user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("INSERT INTO hidden_messages (message_id, user_id) VALUES ($1, $2)", message_id, user_id)
    await conn.close()
    return {"success": True}

@app.delete("/api/messages/{message_id}/for-everyone")
async def delete_message_for_everyone(message_id: str, user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    msg = await conn.fetchrow("SELECT from_user_id FROM messages WHERE id = $1", message_id)
    if msg and msg['from_user_id'] == user_id:
        await conn.execute("DELETE FROM messages WHERE id = $1", message_id)
    await conn.close()
    return {"success": True}

@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
        DELETE FROM messages 
        WHERE (from_user_id = $1 AND to_user_id = $2) OR (from_user_id = $2 AND to_user_id = $1)
    """, user_id, chat_id)
    await conn.close()
    return {"success": True}

# ========== ГРУППЫ ==========
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
async def get_group_messages(group_id: str, limit: int = 100):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT * FROM messages WHERE group_id = $1 ORDER BY created_at ASC LIMIT $2", group_id, limit)
    await conn.close()
    return [dict(r) for r in rows]

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
                        "from_avatar": user['avatar'],
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
                
                members = await conn_msg.fetch("SELECT user_id FROM group_members WHERE group_id = $1", message['group_id'])
                await conn_msg.close()
                
                for member in members:
                    if member['user_id'] in active_connections and member['user_id'] != user_id:
                        await active_connections[member['user_id']].send_text(json.dumps({
                            "type": "group_message",
                            "id": msg_id,
                            "group_id": message['group_id'],
                            "from": user_id,
                            "from_username": user['display_name'],
                            "from_avatar": user['avatar'],
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
            
            elif msg_type == 'call_offer':
                target = message.get('to_user_id') or message.get('group_id')
                target_type = 'user' if 'to_user_id' in message else 'group'
                
                if target_type == 'user' and target in active_connections:
                    await active_connections[target].send_text(json.dumps({
                        "type": "call_offer",
                        "from": user_id,
                        "from_username": user['display_name'],
                        "offer": message['offer'],
                        "is_video": message.get('is_video', False)
                    }))
                elif target_type == 'group' and target in groups_db:
                    for member_id in groups_db[target]['members']:
                        if member_id in active_connections and member_id != user_id:
                            await active_connections[member_id].send_text(json.dumps({
                                "type": "call_offer",
                                "from": user_id,
                                "from_username": user['display_name'],
                                "group_id": target,
                                "offer": message['offer'],
                                "is_video": message.get('is_video', False)
                            }))
            
            elif msg_type == 'call_answer':
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "call_answer",
                        "from": user_id,
                        "answer": message['answer']
                    }))
            
            elif msg_type == 'ice_candidate':
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "ice_candidate",
                        "from": user_id,
                        "candidate": message['candidate']
                    }))
            
            elif msg_type == 'call_end':
                target = message.get('to_user_id') or message.get('group_id')
                target_type = 'user' if 'to_user_id' in message else 'group'
                
                if target_type == 'user' and target in active_connections:
                    await active_connections[target].send_text(json.dumps({
                        "type": "call_end",
                        "from": user_id
                    }))
                elif target_type == 'group' and target in groups_db:
                    for member_id in groups_db[target]['members']:
                        if member_id in active_connections and member_id != user_id:
                            await active_connections[member_id].send_text(json.dumps({
                                "type": "call_end",
                                "from": user_id
                            }))
            
            elif msg_type == 'call_reject':
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "call_reject",
                        "from": user_id
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
    rows = await conn.fetch("SELECT id, username, display_name, avatar, is_admin, is_banned FROM users")
    await conn.close()
    
    users_list = []
    for r in rows:
        users_list.append({
            'id': r['id'],
            'username': r['username'],
            'display_name': r['display_name'],
            'avatar': r['avatar'],
            'is_online': online_users.get(r['id'], False),
            'is_admin': r['is_admin'],
            'is_banned': r['is_banned']
        })
    
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
@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str, user_id: str):
    conn = await asyncpg.connect(DATABASE_URL)
    
    # Проверяем, существует ли группа
    group = await conn.fetchrow("SELECT * FROM groups WHERE id = $1", group_id)
    if not group:
        await conn.close()
        raise HTTPException(404, "Группа не найдена")
    
    # Проверяем, является ли пользователь создателем группы
    if group['created_by'] != user_id:
        await conn.close()
        raise HTTPException(403, "Только создатель группы может удалить её")
    
    # Удаляем всех участников группы
    await conn.execute("DELETE FROM group_members WHERE group_id = $1", group_id)
    
    # Удаляем все сообщения группы
    await conn.execute("DELETE FROM messages WHERE group_id = $1", group_id)
    
    # Удаляем саму группу
    await conn.execute("DELETE FROM groups WHERE id = $1", group_id)
    
    await conn.close()
    
    # Уведомляем всех участников об удалении группы
    for member_id in group['members']:
        if member_id in active_connections:
            await active_connections[member_id].send_text(json.dumps({
                "type": "group_deleted",
                "group_id": group_id
            }))
    
    return {"success": True}
@app.get("/ping")
async def ping():
    return "ok"

@app.get("/landing")
async def landing():
    with open("landing.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

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