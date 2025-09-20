# -*- coding: utf-8 -*-
import asyncio
import json
import time
import aiohttp
import blivedm
import blivedm.models.web as web_models
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit
import threading
from threading import Lock

# 全局变量用于跨线程共享数据
current_online_count = 0
data_lock = Lock()
socketio_instance = None

class CleanOnlineHandler(blivedm.BaseHandler):
    """简洁的在线人数监控处理器"""
    
    def __init__(self, socketio=None):
        super().__init__()
        self.online_count = 0
        self.last_update = time.time()
        self.socketio = socketio
    
    # 复制基础回调字典并添加自定义处理
    _CMD_CALLBACK_DICT = blivedm.BaseHandler._CMD_CALLBACK_DICT.copy()
    
    def __online_rank_count_callback(self, client: blivedm.BLiveClient, command: dict):
        """处理在线人数"""
        global current_online_count, data_lock
        try:
            data = command.get('data', {})
            online_count = data.get('online_count', 0)
            
            if online_count != self.online_count:
                self.online_count = online_count
                self.last_update = time.time()
                
                # 更新全局变量
                with data_lock:
                    current_online_count = online_count
                
                current_time = time.strftime('%H:%M:%S', time.localtime())
                print(f'[{current_time}] 房间 {client.room_id} 在线人数: {online_count}')
            
        except Exception as e:
            pass
    
    # 注册回调
    _CMD_CALLBACK_DICT['ONLINE_RANK_COUNT'] = __online_rank_count_callback
    
    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        """心跳处理"""
        pass

async def monitor_clean_online(room_id: int, socketio=None):
    """简洁监控房间在线人数"""
    session = aiohttp.ClientSession()
    
    try:
        # 创建客户端
        client = blivedm.BLiveClient(room_id, session=session)
        handler = CleanOnlineHandler(socketio)
        client.set_handler(handler)
        
        
        # 启动客户端
        client.start()
        
        # 等待连接
        await client.join()
        
    except KeyboardInterrupt:
        pass
    except Exception as e:
        pass
    finally:
        if client:
            await client.stop_and_close()
        await session.close()

def load_room_id_from_config(config_file='config.json'):
    """从配置文件加载房间ID"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        room_id = config.get('room_id')
        if not room_id:
            return None
            
        # 转换为整数
        if isinstance(room_id, str):
            room_id = int(room_id)
            
        return room_id
        
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, ValueError) as e:
        return None
    except Exception as e:
        return None

# Flask 应用和 SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

def broadcast_online_count():
    """定时广播在线人数"""
    global current_online_count, data_lock, socketio_instance
    if socketio_instance:
        with data_lock:
            count = current_online_count
        socketio_instance.emit('online_count_update', {'count': count})

def start_broadcast_timer():
    """启动定时广播"""
    broadcast_online_count()
    # 每秒广播一次
    threading.Timer(1.0, start_broadcast_timer).start()

# 简单的 HTML 模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>在线人数监控</title>
    <style>
        @font-face {
            font-family: 'FusionPixel';
            src: url('/static/fusion-pixel-10px-monospaced-ko.ttf.woff') format('woff');
            font-display: swap;
        }
        
        body {
            background-color: black;
            color: white;
            font-family: 'FusionPixel', 'Courier New', monospace;
            margin: 0;
            padding: 20px;
            font-size: 24px;
            image-rendering: pixelated;
            -webkit-font-smoothing: none;
            font-smooth: never;
            text-rendering: optimizeSpeed;
            text-align: right;
        }
    </style>
</head>
<body>
    在线 <span id="online-count"></span> 人
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        const socket = io();
        
        socket.on('online_count_update', function(data) {
            const countElement = document.getElementById('online-count');
            // 直接显示数字，不补空格
            const formattedCount = data.count.toString();
            countElement.textContent = formattedCount;
        });
        
        socket.on('connect', function() {
            console.log('Connected to server');
        });
        
        socket.on('disconnect', function() {
            console.log('Disconnected from server');
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('connect')
def handle_connect():
    global current_online_count, data_lock
    print('Client connected')
    # 立即发送当前在线人数
    with data_lock:
        count = current_online_count
    emit('online_count_update', {'count': count})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

def run_flask_app():
    """在单独线程中运行 Flask 应用"""
    global socketio_instance
    socketio_instance = socketio
    # 启动定时广播
    start_broadcast_timer()
    socketio.run(app, host='0.0.0.0', port=5001, debug=False)

async def main():
    """主函数"""
    # 从配置文件加载房间ID
    room_id = load_room_id_from_config()
    
    if room_id is None:
        print("无法从配置文件加载房间ID")
        return
    
    # 在单独线程中启动 Flask 应用
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    print("Flask 服务器已启动在 http://localhost:5001")
    
    # 等待一下让 Flask 启动
    await asyncio.sleep(2)
    
    # 开始监控
    await monitor_clean_online(room_id, socketio)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
