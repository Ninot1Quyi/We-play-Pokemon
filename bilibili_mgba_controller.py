# -*- coding: utf-8 -*-
We-play-Pokemon  Copyright (C) 2025 Ninot1Quyi <quyimail@foxmail.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
import http.cookies
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
import json
from collections import deque
import csv

import aiohttp
import blivedm
import blivedm.models.web as web_models
import blivedm.models.open_live as open_models
import pyautogui
import win32api
import win32con
import win32gui
import win32process
from flask import Flask, render_template, jsonify, request, Response

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 直播间 ID
ROOM_ID = 27063247

# Bilibili 登录 Cookie 的 SESSDATA（替换为有效值）
SESSDATA = ''

# OpenLive 模式配置
ACCESS_KEY_ID = '2oZxfhKZNUv2SDBN4Wiuszo6'
ACCESS_KEY_SECRET = 'gRBXleqvAJLIhi0gFUvp6Dncq76aWY'
APP_ID = 1757756104085  # 在开放平台创建的项目ID
ROOM_OWNER_AUTH_CODE = 'CCHWE6NUD43K7'  # 主播身份码

# 弹幕获取模式：'wss' 或 'openlive'
DANMAKU_MODE = 'openlive'

# 时间文件
TIME_FILE = "start_time.txt"

# mGBA 窗口标题关键字
MGBA_WINDOW_TITLE = "mGBA - POKEMON"

# 弹幕到按键的映射（大小写不敏感）
COMMAND_TO_KEY = {
    '上': 'up',
    'i': 'up',
    '下': 'down',
    'k': 'down',
    '左': 'left',
    'j': 'left',
    '右': 'right',
    'l': 'right',
    'a': 'x',
    'b': 'z',
    '开始': 'enter',
    'start': 'enter',
    '选择': 'backspace',
    'select': 'backspace'
}

# 按键到显示文本的映射
KEY_TO_DISPLAY = {
    'up': '↑',
    'down': '↓',
    'left': '←',
    'right': '→',
    'x': 'A',
    'z': 'B',
    'enter': 'Start',
    'backspace': 'Select'
}

# 设置 pyautogui 的暂停时间
pyautogui.PAUSE = 0.1

class DanmakuSaver:
    """弹幕保存管理类"""
    def __init__(self, base_dir="danmaku"):
        self.base_dir = base_dir
        self.current_file = None
        self.current_count = 0
        self.max_count = 1000
        self.file_counter = 1
        self.csv_writer = None
        self.csv_file_handle = None
        
        # 确保目录存在
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
            logger.info(f"Created directory: {self.base_dir}")
    
    def _get_filename(self):
        """生成文件名，格式：danmaku/20250826-1045-23.csv"""
        now = datetime.now()
        base_name = now.strftime("%Y%m%d-%H%M-%S")
        return os.path.join(self.base_dir, f"{base_name}.csv")
    
    def _create_new_file(self):
        """创建新的CSV文件"""
        if self.csv_file_handle:
            self.csv_file_handle.close()
        
        self.current_file = self._get_filename()
        self.csv_file_handle = open(self.current_file, 'w', newline='', encoding='utf-8')
        self.csv_writer = csv.writer(self.csv_file_handle)
        self.current_count = 0
        logger.info(f"Created new CSV file: {self.current_file}")
    
    def save_danmaku(self, timestamp, username, command, executed):
        """保存弹幕数据到CSV文件"""
        # 如果需要创建新文件
        if self.current_count >= self.max_count or self.csv_writer is None:
            self._create_new_file()
        
        # 写入数据：时间戳，用户名，指令内容，是否执行
        row = [timestamp, username, command, executed]
        self.csv_writer.writerow(row)
        self.csv_file_handle.flush()  # 立即写入磁盘
        self.current_count += 1
        
        logger.debug(f"Saved danmaku to CSV: {row}")
    
    def close(self):
        """关闭文件句柄"""
        if self.csv_file_handle:
            self.csv_file_handle.close()
            logger.info(f"Closed CSV file: {self.current_file}")

session: Optional[aiohttp.ClientSession] = None
start_time: datetime = None
danmaku_display_queue = deque(maxlen=13)  # 固定长度15的队列用于HTML显示
danmaku_lock = threading.Lock()  # 弹幕数据锁
sse_clients = []  # SSE客户端列表
sse_lock = threading.Lock()  # SSE客户端锁
danmaku_saver = DanmakuSaver()  # 弹幕保存器实例

# 最新指令缓存机制
latest_command = None  # 最新的指令
latest_command_lock = threading.Lock()  # 最新指令锁
executing_command = False  # 是否正在执行指令
execution_lock = threading.Lock()  # 执行锁

app = Flask(__name__)
window_lock = threading.Lock()  # 线程锁

def init_session():
    """初始化 HTTP 会话并设置 Cookie"""
    cookies = http.cookies.SimpleCookie()
    if SESSDATA:
        cookies['SESSDATA'] = SESSDATA
        cookies['SESSDATA']['domain'] = 'bilibili.com'
    global session
    session = aiohttp.ClientSession()
    session.cookie_jar.update_cookies(cookies)
    logger.info("HTTP session initialized")

def load_or_set_start_time():
    """加载或设置启动时间"""
    global start_time
    if os.path.exists(TIME_FILE):
        with open(TIME_FILE, 'r', encoding='utf-8') as f:
            start_time_str = f.read().strip()
            start_time = datetime.fromisoformat(start_time_str)
    else:
        start_time = datetime.now()
        with open(TIME_FILE, 'w', encoding='utf-8') as f:
            f.write(start_time.isoformat())
    logger.info(f"Start time: {start_time}")

def get_runtime():
    """获取运行时间"""
    if start_time:
        runtime = datetime.now() - start_time
        days = runtime.days
        hours, remainder = divmod(runtime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"
    return "0d 00h 00m 00s"

def broadcast_danmaku(danmaku_data):
    """向所有SSE客户端广播弹幕数据"""
    with sse_lock:
        disconnected_clients = []
        for client_queue in sse_clients:
            try:
                client_queue.put(danmaku_data)
            except:
                disconnected_clients.append(client_queue)
        
        # 移除断开连接的客户端
        for client in disconnected_clients:
            sse_clients.remove(client)
        
        logger.info(f"Broadcasted danmaku to {len(sse_clients)} clients")

def activate_mgba_window(search_text: str = MGBA_WINDOW_TITLE):
    """查找并激活 mGBA 窗口（改进版本）"""
    def enum_windows(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if search_text in title:
                results.append(hwnd)
    
    hwnd_list = []
    win32gui.EnumWindows(enum_windows, hwnd_list)
    
    if not hwnd_list:
        logger.error(f"No window found with '{search_text}' in title")
        return False
    
    hwnd = hwnd_list[0]
    with window_lock:  # 线程安全
        try:
            # 检查窗口是否已经是前台窗口
            current_foreground = win32gui.GetForegroundWindow()
            if current_foreground == hwnd:
                logger.info(f"mGBA window is already active: {win32gui.GetWindowText(hwnd)}")
                return True
            
            # 强制显示窗口（处理最小化和被覆盖的情况）
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            time.sleep(0.05)
            
            # 尝试多种方法激活窗口
            success = False
            
            # 方法1: 直接设置前台窗口
            try:
                if win32gui.SetForegroundWindow(hwnd):
                    time.sleep(0.05)  # 短暂等待窗口响应
                    if win32gui.GetForegroundWindow() == hwnd:
                        success = True
                        logger.info(f"Method 1 success: Activated mGBA window: {win32gui.GetWindowText(hwnd)}")
            except Exception as e:
                logger.debug(f"Method 1 failed: {e}")
            
            # 方法2: 强制置顶 + 线程输入附加
            if not success:
                try:
                    # 先强制置顶
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, 
                                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                    time.sleep(0.05)
                    
                    current_thread = win32process.GetCurrentThreadId()
                    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
                    
                    if current_thread != target_thread:
                        win32process.AttachThreadInput(current_thread, target_thread, True)
                        try:
                            win32gui.SetForegroundWindow(hwnd)
                            win32gui.BringWindowToTop(hwnd)
                            time.sleep(0.05)
                            if win32gui.GetForegroundWindow() == hwnd:
                                success = True
                                logger.info(f"Method 2 success: Activated mGBA window: {win32gui.GetWindowText(hwnd)}")
                        finally:
                            win32process.AttachThreadInput(current_thread, target_thread, False)
                    else:
                        win32gui.SetForegroundWindow(hwnd)
                        win32gui.BringWindowToTop(hwnd)
                        time.sleep(0.05)
                        if win32gui.GetForegroundWindow() == hwnd:
                            success = True
                            logger.info(f"Method 2 (same thread) success: Activated mGBA window: {win32gui.GetWindowText(hwnd)}")
                    
                    # 取消置顶状态，让窗口正常显示
                    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, 
                                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                except Exception as e:
                    logger.debug(f"Method 2 failed: {e}")
            
            # 方法3: 温和激活（不影响其他窗口）
            if not success:
                try:
                    # 只激活目标窗口，不影响其他窗口
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.05)
                    
                    # 检查是否激活成功
                    if win32gui.GetForegroundWindow() == hwnd:
                        success = True
                        logger.info(f"Method 3 success: Activated mGBA window: {win32gui.GetWindowText(hwnd)}")
                except Exception as e:
                    logger.debug(f"Method 3 failed: {e}")
            
            # 方法4: 强制激活（最后手段）
            if not success:
                try:
                    # 发送 Alt 键来解除系统的前台锁定
                    pyautogui.keyDown('alt')
                    time.sleep(0.05)
                    pyautogui.keyUp('alt')
                    
                    # 再次尝试激活
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0, 
                                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.05)
                    
                    # 检查是否激活成功
                    if win32gui.GetForegroundWindow() == hwnd:
                        success = True
                        logger.info(f"Method 4 success: Activated mGBA window: {win32gui.GetWindowText(hwnd)}")
                except Exception as e:
                    logger.debug(f"Method 4 failed: {e}")
            
            # 验证激活是否成功
            if success:
                time.sleep(0.1)  # 等待窗口响应
                current_foreground = win32gui.GetForegroundWindow()
                if current_foreground == hwnd:
                    return True
                else:
                    logger.warning(f"Window activation may have failed - current foreground: {win32gui.GetWindowText(current_foreground) if current_foreground else 'None'}")
                    return False
            else:
                logger.warning(f"All activation methods failed for: {win32gui.GetWindowText(hwnd)}")
                return False
                
        except Exception as e:
            logger.error(f"Error activating mGBA window: {e}")
            return False

def press_key(key: str, duration: float = 0.1):
    """模拟按下并释放按键"""
    try:
        pyautogui.keyDown(key)
        pyautogui.sleep(duration)
        pyautogui.keyUp(key)
        logger.info(f"Pressed key: {key} (duration: {duration}s)")
    except Exception as e:
        logger.error(f"Failed to press key {key}: {e}")

def control_mgba(command: str):
    """根据弹幕命令控制 mGBA（大小写不敏感，支持按键+数字）"""
    global executing_command
    
    command = command.strip().lower()
    
    # 检查是否是按键+数字格式
    repeat_count = 1
    base_command = command
    
    # 如果命令以数字结尾，提取数字
    if command and command[-1].isdigit():
        digit = command[-1]
        base_command = command[:-1]
        repeat_count = int(digit)
        if repeat_count < 1 or repeat_count > 9:
            repeat_count = 1
    
    key = COMMAND_TO_KEY.get(base_command)
    if key:
        # 获取执行锁，防止指令执行被打断
        with execution_lock:
            executing_command = True
            try:
                if not activate_mgba_window():
                    logger.warning("mGBA window not found or activation failed, skipping key press")
                else:
                    for i in range(repeat_count):
                        press_key(key)
                        if i < repeat_count - 1:  # 不是最后一次按键
                            time.sleep(0.5)  # 按键间隔0.5秒
                    logger.info(f"Executed command '{base_command}' {repeat_count} times")
            finally:
                executing_command = False
    else:
        logger.warning(f"Unknown command: {command}")

async def run_bilibili_wss_client():
    """运行 Bilibili WSS 模式弹幕监听"""
    client = blivedm.BLiveClient(ROOM_ID, session=session)
    handler = DanmakuHandler()
    client.set_handler(handler)
    
    client.start()
    logger.info(f"Started Bilibili WSS client for room {ROOM_ID}")
    
    try:
        await client.join()
    except Exception as e:
        logger.error(f"Bilibili WSS client error: {e}")
    finally:
        await client.stop_and_close()
        logger.info("Bilibili WSS client stopped")

async def run_bilibili_openlive_client():
    """运行 Bilibili OpenLive 模式弹幕监听"""
    client = blivedm.OpenLiveClient(
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET,
        app_id=APP_ID,
        room_owner_auth_code=ROOM_OWNER_AUTH_CODE,
    )
    handler = OpenLiveHandler()
    client.set_handler(handler)
    
    client.start()
    logger.info(f"Started Bilibili OpenLive client")
    
    try:
        await client.join()
    except Exception as e:
        logger.error(f"Bilibili OpenLive client error: {e}")
    finally:
        await client.stop_and_close()
        logger.info("Bilibili OpenLive client stopped")

async def run_bilibili_client():
    """根据配置运行对应的弹幕监听客户端"""
    if DANMAKU_MODE.lower() == 'openlive':
        await run_bilibili_openlive_client()
    else:
        await run_bilibili_wss_client()

def execute_latest_command():
    """执行最新的指令（在单独线程中运行）"""
    global latest_command, executing_command
    
    while True:
        command_to_execute = None
        
        # 获取最新指令
        with latest_command_lock:
            if latest_command and not executing_command:
                command_to_execute = latest_command
                latest_command = None  # 清空最新指令
        
        if command_to_execute:
            logger.info(f"Executing latest command: {command_to_execute}")
            control_mgba(command_to_execute)
        
        time.sleep(0.1)  # 检查间隔

def process_danmaku_command(username: str, command: str, room_id: str = None):
    """处理弹幕指令的通用函数"""
    global latest_command
    
    command = command.strip()
    original_command = command  # 保存原始指令用于CSV记录
    
    # 获取当前时间戳（精确到秒）
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 检查是否是合法指令
    base_command = command.lower()
    repeat_count = 1
    executed = 0  # 默认未执行
    
    # 处理按键+数字格式
    if command and command[-1].isdigit():
        digit = command[-1]
        base_command = command[:-1].lower()
        repeat_count = int(digit)
        if repeat_count < 1 or repeat_count > 9:
            repeat_count = 1
    
    # 检查基础命令是否合法
    if base_command in COMMAND_TO_KEY:
        key = COMMAND_TO_KEY[base_command]
        display_command = KEY_TO_DISPLAY.get(key, base_command)
        if repeat_count > 1:
            display_command += str(repeat_count)
        
        # 创建结构化的弹幕数据
        danmaku_data = {
            'username': username,
            'command': display_command,
            'timestamp': time.time()
        }
        
        # 添加到固定长度的显示队列
        with danmaku_lock:
            danmaku_display_queue.append(danmaku_data)
            logger.info(f"Added danmaku to queue: {danmaku_data}")
        
        # 实时推送给所有SSE客户端
        broadcast_danmaku(danmaku_data)
        
        # 更新最新指令（覆盖之前的指令）
        with latest_command_lock:
            if not executing_command:  # 只有在不执行时才更新
                latest_command = command
                executed = 1  # 标记为将要执行
                logger.info(f"Updated latest command: {command}")
            else:
                logger.info(f"Command ignored (executing): {command}")
        
        # 只保存合法指令到CSV文件
        try:
            danmaku_saver.save_danmaku(current_time, username, original_command, executed)
        except Exception as e:
            logger.error(f"Failed to save danmaku to CSV: {e}")
    else:
        logger.info(f"Unknown command ignored: {command}")

class DanmakuHandler(blivedm.BaseHandler):
    """处理 Bilibili WSS 模式直播间消息"""
    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        logger.debug(f"[{client.room_id}] Heartbeat")

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        logger.info(f"[{client.room_id}] {message.uname}: {message.msg}")
        process_danmaku_command(message.uname, message.msg, str(client.room_id))

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        logger.info(f"[{client.room_id}] {message.uname} 赠送 {message.gift_name}x{message.num}")

    def _on_user_toast_v2(self, client: blivedm.BLiveClient, message: web_models.UserToastV2Message):
        logger.info(f"[{client.room_id}] {message.username} 上舰，guard_level={message.guard_level}")

    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        logger.info(f"[{client.room_id}] 醒目留言 ¥{message.price} {message.uname}: {message.message}")

    def _on_log_in_notice(self, client: blivedm.BLiveClient, message: dict):
        logger.info(f"[{client.room_id}] Login notice: {message['data']['notice_msg']}")

class OpenLiveHandler(blivedm.BaseHandler):
    """处理 Bilibili OpenLive 模式直播间消息"""
    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        logger.debug(f"[OpenLive] Heartbeat")

    def _on_open_live_danmaku(self, client: blivedm.OpenLiveClient, message: open_models.DanmakuMessage):
        logger.info(f"[{message.room_id}] {message.uname}: {message.msg}")
        process_danmaku_command(message.uname, message.msg, str(message.room_id))

    def _on_open_live_gift(self, client: blivedm.OpenLiveClient, message: open_models.GiftMessage):
        coin_type = '金瓜子' if message.paid else '银瓜子'
        total_coin = message.price * message.gift_num
        logger.info(f"[{message.room_id}] {message.uname} 赠送{message.gift_name}x{message.gift_num}"
                   f" （{coin_type}x{total_coin}）")

    def _on_open_live_buy_guard(self, client: blivedm.OpenLiveClient, message: open_models.GuardBuyMessage):
        logger.info(f"[{message.room_id}] {message.user_info.uname} 购买 大航海等级={message.guard_level}")

    def _on_open_live_super_chat(self, client: blivedm.OpenLiveClient, message: open_models.SuperChatMessage):
        logger.info(f"[{message.room_id}] 醒目留言 ¥{message.rmb} {message.uname}: {message.message}")

    def _on_open_live_super_chat_delete(self, client: blivedm.OpenLiveClient, message: open_models.SuperChatDeleteMessage):
        logger.info(f"[{message.room_id}] 删除醒目留言 message_ids={message.message_ids}")

    def _on_open_live_like(self, client: blivedm.OpenLiveClient, message: open_models.LikeMessage):
        logger.info(f"[{message.room_id}] {message.uname} 点赞")

    def _on_open_live_enter_room(self, client: blivedm.OpenLiveClient, message: open_models.RoomEnterMessage):
        logger.info(f"[{message.room_id}] {message.uname} 进入房间")

    def _on_open_live_start_live(self, client: blivedm.OpenLiveClient, message: open_models.LiveStartMessage):
        logger.info(f"[{message.room_id}] 开始直播")

    def _on_open_live_end_live(self, client: blivedm.OpenLiveClient, message: open_models.LiveEndMessage):
        logger.info(f"[{message.room_id}] 结束直播")

@app.route('/')
def index():
    """渲染 HTML 页面"""
    runtime = get_runtime()
    with danmaku_lock:
        danmaku_list = list(danmaku_display_queue)  # 获取队列中的所有数据
    return render_template('index.html', runtime=runtime, danmaku_list=danmaku_list)

@app.route('/api/danmaku/stream')
def danmaku_stream():
    """SSE端点：实时推送弹幕数据"""
    def event_stream():
        client_queue = queue.Queue()
        
        # 添加客户端到列表
        with sse_lock:
            sse_clients.append(client_queue)
            logger.info(f"New SSE client connected. Total clients: {len(sse_clients)}")
        
        try:
            # 首先发送当前队列中的所有弹幕
            with danmaku_lock:
                for danmaku in danmaku_display_queue:
                    yield f"data: {json.dumps(danmaku)}\n\n"
            
            # 持续监听新的弹幕
            while True:
                try:
                    danmaku = client_queue.get(timeout=30)  # 30秒超时
                    yield f"data: {json.dumps(danmaku)}\n\n"
                except queue.Empty:
                    # 发送心跳包保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat', 'runtime': get_runtime()})}\n\n"
        except GeneratorExit:
            pass
        finally:
            # 移除客户端
            with sse_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
                    logger.info(f"SSE client disconnected. Remaining clients: {len(sse_clients)}")
    
    return Response(event_stream(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache',
                           'Connection': 'keep-alive',
                           'Access-Control-Allow-Origin': '*'})

def run_web_server():
    """运行 Flask Web 服务器"""
    import logging
    # 禁用Flask的访问日志
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    logger.info("=" * 50)
    logger.info("Flask Web Server Starting...")
    logger.info("Frontend URL: http://localhost:5000")
    logger.info("SSE Stream: http://localhost:5000/api/danmaku/stream")
    logger.info("=" * 50)
    
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

async def main():
    """主函数"""
    init_session()
    load_or_set_start_time()
    
    # 启动Web服务器线程
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # 启动指令执行线程
    command_thread = threading.Thread(target=execute_latest_command, daemon=True)
    command_thread.start()
    logger.info("Started command execution thread")
    
    try:
        await run_bilibili_client()
    finally:
        if session:
            await session.close()
            logger.info("HTTP session closed")
        # 关闭弹幕保存器
        danmaku_saver.close()

if __name__ == "__main__":
    asyncio.run(main())
