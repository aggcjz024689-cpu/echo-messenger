import asyncio
import json
import secrets
from datetime import datetime
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os

app = FastAPI(title="ЭХО Мессенджер", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилища
users_db: Dict[str, dict] = {}
messages_db: List[dict] = []
active_connections: Dict[str, WebSocket] = {}
online_users: Dict[str, bool] = {}
call_requests: Dict[str, dict] = {}  # Исходящие звонки

class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

def get_avatar_color(name: str) -> str:
    colors = ['#9147ff', '#ff6b6b', '#4ade80', '#fbbf24', '#60a5fa', '#f472b6', '#34d399', '#a78bfa']
    index = ord(name[0]) % len(colors) if name else 0
    return colors[index]

@app.get("/", response_class=HTMLResponse)
async def get_index():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

@app.post("/api/register")
async def register(user: UserRegister):
    for uid, data in users_db.items():
        if data['username'] == user.username:
            raise HTTPException(status_code=400, detail="Username already exists")
    user_id = secrets.token_urlsafe(16)
    users_db[user_id] = {
        'id': user_id,
        'username': user.username,
        'password': user.password,
        'created_at': datetime.now().isoformat(),
        'avatar_color': get_avatar_color(user.username)
    }
    return {"user_id": user_id, "username": user.username}

@app.post("/api/login")
async def login(user: UserLogin):
    for uid, data in users_db.items():
        if data['username'] == user.username and data['password'] == user.password:
            online_users[uid] = True
            return {"user_id": uid, "username": data['username']}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/users")
async def get_users():
    result = []
    for uid, data in users_db.items():
        result.append({
            'id': uid,
            'username': data['username'],
            'is_online': online_users.get(uid, False),
            'avatar_color': data.get('avatar_color', '#9147ff')
        })
    return result

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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = await websocket.receive_text()
    
    if user_id not in users_db:
        await websocket.close(code=1008, reason="Invalid user")
        return
    
    username = users_db[user_id]['username']
    active_connections[user_id] = websocket
    online_users[user_id] = True
    await broadcast_users_list()
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get('type', 'text')
            
            if msg_type == 'text' or msg_type == 'image' or msg_type == 'video':
                msg_id = secrets.token_urlsafe(16)
                msg_data = {
                    'id': msg_id,
                    'from_user_id': user_id,
                    'from_username': username,
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
                        "from_username": username,
                        "content": message['content'],
                        "type2": msg_type,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            elif msg_type == 'call_offer':
                # Предложение звонка
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "call_offer",
                        "from": user_id,
                        "from_username": username,
                        "offer": message['offer']
                    }))
            
            elif msg_type == 'call_answer':
                # Ответ на звонок
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "call_answer",
                        "from": user_id,
                        "answer": message['answer']
                    }))
            
            elif msg_type == 'ice_candidate':
                # ICE кандидаты для WebRTC
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "ice_candidate",
                        "from": user_id,
                        "candidate": message['candidate']
                    }))
            
            elif msg_type == 'call_end':
                # Завершение звонка
                if message['to_user_id'] in active_connections:
                    await active_connections[message['to_user_id']].send_text(json.dumps({
                        "type": "call_end",
                        "from": user_id
                    }))
            
            elif msg_type == 'call_reject':
                # Отказ от звонка
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
    users_list = []
    for uid, data in users_db.items():
        users_list.append({
            'id': uid,
            'username': data['username'],
            'is_online': online_users.get(uid, False),
            'avatar_color': data.get('avatar_color', '#9147ff')
        })
    status_msg = json.dumps({"type": "users_list", "users": users_list})
    for conn in active_connections.values():
        try:
            await conn.send_text(status_msg)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)