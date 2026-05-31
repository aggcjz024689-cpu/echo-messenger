import asyncio
import json
import secrets
from datetime import datetime
from typing import Dict, List, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os

app = FastAPI(title="ЭХО Мессенджер", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== ХРАНИЛИЩА ==========
users_db: Dict[str, dict] = {}
messages_db: List[dict] = []
active_connections: Dict[str, WebSocket] = {}
online_users: Dict[str, bool] = {}

# Групповые чаты
groups_db: Dict[str, dict] = {}  # group_id -> {name, avatar_color, created_by, created_at, members: Set}
group_messages_db: List[dict] = []

# ========== МОДЕЛИ ==========
class UserRegister(BaseModel):
    username: str
    display_name: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class UpdateProfile(BaseModel):
    display_name: str
    bio: str

class CreateGroup(BaseModel):
    name: str
    members: List[str]  # user_id списки

class AddMembers(BaseModel):
    group_id: str
    members: List[str]

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_avatar_color(name: str) -> str:
    colors = ['#9147ff', '#ff6b6b', '#4ade80', '#fbbf24', '#60a5fa', '#f472b6', '#34d399', '#a78bfa']
    index = ord(name[0]) % len(colors) if name else 0
    return colors[index]

# ========== API ЭНДПОИНТЫ ==========
@app.get("/", response_class=HTMLResponse)
async def get_index():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

# ----- ПОЛЬЗОВАТЕЛИ -----
@app.post("/api/register")
async def register(user: UserRegister):
    for uid, data in users_db.items():
        if data['username'] == user.username:
            raise HTTPException(status_code=400, detail="Username already taken")
    
    clean_username = user.username if user.username.startswith('@') else f"@{user.username}"
    user_id = secrets.token_urlsafe(16)
    users_db[user_id] = {
        'id': user_id,
        'username': clean_username,
        'display_name': user.display_name,
        'password': user.password,
        'bio': '',
        'avatar_color': get_avatar_color(clean_username),
        'created_at': datetime.now().isoformat()
    }
    return {"user_id": user_id, "username": clean_username, "display_name": user.display_name}

@app.post("/api/login")
async def login(user: UserLogin):
    for uid, data in users_db.items():
        if data['username'] == user.username and data['password'] == user.password:
            online_users[uid] = True
            return {"user_id": uid, "username": data['username'], "display_name": data['display_name']}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/users")
async def get_users():
    result = []
    for uid, data in users_db.items():
        result.append({
            'id': uid,
            'username': data['username'],
            'display_name': data['display_name'],
            'is_online': online_users.get(uid, False),
            'avatar_color': data.get('avatar_color', '#9147ff'),
            'bio': data.get('bio', '')
        })
    return result

@app.get("/api/users/search")
async def search_users(q: str):
    results = []
    for uid, data in users_db.items():
        if q.lower() in data['username'].lower() or q.lower() in data['display_name'].lower():
            results.append({
                'id': uid,
                'username': data['username'],
                'display_name': data['display_name'],
                'is_online': online_users.get(uid, False),
                'avatar_color': data.get('avatar_color', '#9147ff')
            })
    return results[:20]

@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    data = users_db[user_id]
    return {
        'id': user_id,
        'username': data['username'],
        'display_name': data['display_name'],
        'bio': data.get('bio', ''),
        'is_online': online_users.get(user_id, False),
        'avatar_color': data.get('avatar_color', '#9147ff'),
        'created_at': data['created_at']
    }

@app.put("/api/users/{user_id}/profile")
async def update_profile(user_id: str, profile: UpdateProfile):
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    users_db[user_id]['display_name'] = profile.display_name
    users_db[user_id]['bio'] = profile.bio
    return {"success": True, "display_name": profile.display_name, "bio": profile.bio}

# ----- ГРУППОВЫЕ ЧАТЫ -----
@app.post("/api/groups")
async def create_group(group: CreateGroup):
    group_id = secrets.token_urlsafe(16)
    groups_db[group_id] = {
        'id': group_id,
        'name': group.name,
        'avatar_color': get_avatar_color(group.name),
        'created_by': group.members[0] if group.members else None,
        'members': set(group.members),
        'created_at': datetime.now().isoformat()
    }
    return {"group_id": group_id, "name": group.name}

@app.get("/api/groups")
async def get_groups():
    result = []
    for gid, data in groups_db.items():
        result.append({
            'id': gid,
            'name': data['name'],
            'avatar_color': data['avatar_color'],
            'members_count': len(data['members']),
            'created_at': data['created_at']
        })
    return result

@app.get("/api/groups/{group_id}")
async def get_group(group_id: str):
    if group_id not in groups_db:
        raise HTTPException(status_code=404, detail="Group not found")
    data = groups_db[group_id]
    # Получаем информацию об участниках
    members_info = []
    for uid in data['members']:
        if uid in users_db:
            members_info.append({
                'id': uid,
                'username': users_db[uid]['username'],
                'display_name': users_db[uid]['display_name'],
                'is_online': online_users.get(uid, False)
            })
    return {
        'id': group_id,
        'name': data['name'],
        'avatar_color': data['avatar_color'],
        'created_by': data['created_by'],
        'members': members_info,
        'created_at': data['created_at']
    }

@app.post("/api/groups/{group_id}/members")
async def add_members(group_id: str, members: AddMembers):
    if group_id not in groups_db:
        raise HTTPException(status_code=404, detail="Group not found")
    groups_db[group_id]['members'].update(members.members)
    return {"success": True}

@app.get("/api/groups/{group_id}/messages")
async def get_group_messages(group_id: str, limit: int = 50):
    history = [msg for msg in group_messages_db if msg['group_id'] == group_id]
    history.sort(key=lambda x: x.get('created_at', ''))
    return history[-limit:]

# ----- ЛИЧНЫЕ СООБЩЕНИЯ -----
@app.get("/api/messages/{other_user_id}")
async def get_messages(other_user_id: str, user_id: str = None):
    if not user_id:
        return []
    history = []
    for msg in messages_db:
        if (msg['from_user_id'] == user_id and msg['to_user_id'] == other_user_id) or \
           (msg['from_user_id'] == other_user_id and msg['to_user_id'] == user_id):
            history.append(msg)
    history.sort(key=lambda x: x.get('created_at', ''))
    return history[-50:]

# ========== WEB SOCKET ==========
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = await websocket.receive_text()
    
    if user_id not in users_db:
        await websocket.close(code=1008, reason="Invalid user")
        return
    
    username = users_db[user_id]['username']
    display_name = users_db[user_id]['display_name']
    active_connections[user_id] = websocket
    online_users[user_id] = True
    await broadcast_users_list()
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get('type', 'text')
            
            # Личные сообщения
            if msg_type in ['text', 'image', 'video'] and 'to_user_id' in message:
                msg_id = secrets.token_urlsafe(16)
                msg_data = {
                    'id': msg_id,
                    'from_user_id': user_id,
                    'from_username': display_name,
                    'to_user_id': message['to_user_id'],
                    'content': message['content'],
                    'type': msg_type,
                    'created_at': datetime.now().isoformat()
                }
                messages_db.append(msg_data)
                
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "message",
                        "id": msg_id,
                        "from": user_id,
                        "from_username": display_name,
                        "content": message['content'],
                        "type2": msg_type,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            # Групповые сообщения
            elif msg_type in ['text', 'image', 'video'] and 'group_id' in message:
                group_id = message['group_id']
                if group_id not in groups_db:
                    continue
                
                msg_id = secrets.token_urlsafe(16)
                msg_data = {
                    'id': msg_id,
                    'group_id': group_id,
                    'from_user_id': user_id,
                    'from_username': display_name,
                    'content': message['content'],
                    'type': msg_type,
                    'created_at': datetime.now().isoformat()
                }
                group_messages_db.append(msg_data)
                
                # Рассылаем всем участникам группы
                for member_id in groups_db[group_id]['members']:
                    if member_id in active_connections and member_id != user_id:
                        await active_connections[member_id].send_text(json.dumps({
                            "type": "group_message",
                            "id": msg_id,
                            "group_id": group_id,
                            "from": user_id,
                            "from_username": display_name,
                            "content": message['content'],
                            "type2": msg_type,
                            "timestamp": datetime.now().isoformat()
                        }))
            
            # Звонки (личные)
            elif msg_type in ['call_offer', 'call_answer', 'ice_candidate', 'call_end', 'call_reject']:
                if 'to_user_id' in message and message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": msg_type,
                        "from": user_id,
                        "from_username": display_name,
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
    users_list = []
    for uid, data in users_db.items():
        users_list.append({
            'id': uid,
            'username': data['username'],
            'display_name': data['display_name'],
            'is_online': online_users.get(uid, False),
            'avatar_color': data.get('avatar_color', '#9147ff'),
            'bio': data.get('bio', '')
        })
    
    groups_list = []
    for gid, data in groups_db.items():
        groups_list.append({
            'id': gid,
            'name': data['name'],
            'avatar_color': data['avatar_color'],
            'members_count': len(data['members'])
        })
    
    status_msg = json.dumps({
        "type": "users_list",
        "users": users_list,
        "groups": groups_list
    })
    for conn in active_connections.values():
        try:
            await conn.send_text(status_msg)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("="*50)
    print("🚀 ЭХО МЕССЕНДЖЕР С ГРУППАМИ ЗАПУЩЕН!")
    print(f"📡 http://localhost:{port}")
    print("="*50)
    uvicorn.run(app, host="0.0.0.0", port=port)