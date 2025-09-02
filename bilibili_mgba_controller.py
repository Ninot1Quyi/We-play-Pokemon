# -*- coding: utf-8 -*-
'''
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
'''
import asyncio
import http.cookies
import logging
import os
import queue
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
import json
from collections import deque
import csv
import websockets

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

# 配置文件路径
CONFIG_FILE = "config.json"

# 全局配置变量（将从config.json加载）
ROOM_ID = None
SESSDATA = None
ACCESS_KEY_ID = None
ACCESS_KEY_SECRET = None
APP_ID = None
ROOM_OWNER_AUTH_CODE = None
DANMAKU_MODE = None
DOUYIN_WEBSOCKET_PORT = None
DOUYIN_ENABLED = None
BLOCKED_WORDS = []

# 时间文件
TIME_FILE = "start_time.txt"

# mGBA 窗口标题关键字
MGBA_WINDOW_TITLE = "mGBA - POKEMON"

# 弹幕到按键的映射（大小写不敏感）
COMMAND_TO_KEY = {
    '上': 'up',
    'i': 'up',
    'up': 'up',
    '下': 'down',
    'k': 'down',
    'down': 'down',
    '左': 'left',
    'j': 'left',
    'left': 'left',
    '右': 'right',
    'l': 'right',
    'right': 'right',
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
        # 写入CSV头部
        self.csv_writer.writerow(['时间戳', '用户名', '指令内容', '是否执行', '平台'])
        self.current_count = 0
        logger.info(f"Created new CSV file: {self.current_file}")
    
    def save_danmaku(self, timestamp, username, command, executed, platform="哔哩哔哩"):
        """保存弹幕数据到CSV文件"""
        # 如果需要创建新文件
        if self.current_count >= self.max_count or self.csv_writer is None:
            self._create_new_file()
        
        # 写入数据：时间戳，用户名，指令内容，是否执行，平台
        row = [timestamp, username, command, executed, platform]
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

# 最新指令缓存机制（无政府模式）
latest_command = None  # 最新的指令
latest_command_lock = threading.Lock()  # 最新指令锁
executing_command = False  # 是否正在执行指令
execution_lock = threading.Lock()  # 执行锁

# 模式切换系统
current_mode = "自由"  # 当前模式："自由" 或 "秩序"
mode_lock = threading.Lock()  # 模式锁
vote_lock = threading.Lock()  # 投票锁
freedom_support = 50  # 自由模式支持率，初始50%

# 秩序模式相关
order_commands = {}  # 秩序模式指令统计 {command: count}
order_start_time = None  # 秩序模式统计开始时间
order_lock = threading.Lock()  # 秩序模式锁
ORDER_INTERVAL = 20  # 秩序模式统计间隔（秒）

# 自动输入机制
last_command_time = time.time()  # 最后一次接收指令的时间
auto_input_lock = threading.Lock()  # 自动输入锁
AUTO_INPUT_TIMEOUT = 120  # 120秒无操作自动输入
AUTO_INPUT_INTERVAL = 10  # 无人值守状态每10秒输入一次
auto_mode = False  # 是否处于自动模式

app = Flask(__name__)
window_lock = threading.Lock()  # 线程锁

def load_config():
    """加载配置文件"""
    global ROOM_ID, SESSDATA, ACCESS_KEY_ID, ACCESS_KEY_SECRET, APP_ID, ROOM_OWNER_AUTH_CODE, DANMAKU_MODE, ORDER_INTERVAL, DOUYIN_WEBSOCKET_PORT, DOUYIN_ENABLED
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        ROOM_ID = config.get('room_id', 27063248)
        SESSDATA = config.get('sessdata', '')
        
        openlive_config = config.get('openlive', {})
        ACCESS_KEY_ID = openlive_config.get('access_key_id', '')
        ACCESS_KEY_SECRET = openlive_config.get('access_key_secret', '')
        APP_ID = openlive_config.get('app_id', 0)
        ROOM_OWNER_AUTH_CODE = openlive_config.get('room_owner_auth_code', '')
        
        DANMAKU_MODE = config.get('danmaku_mode', 'openlive')
        ORDER_INTERVAL = config.get('ORDER_INTERVAL', 20)
        
        # 抖音弹幕配置
        douyin_config = config.get('douyin', {})
        DOUYIN_WEBSOCKET_PORT = douyin_config.get('websocket_port', 8765)
        DOUYIN_ENABLED = douyin_config.get('enabled', False)
        
        # 屏蔽词配置
        global BLOCKED_WORDS
        BLOCKED_WORDS = config.get('blocked_words', [])
        
        logger.info(f"Configuration loaded from {CONFIG_FILE}")
        logger.info(f"Room ID: {ROOM_ID}")
        logger.info(f"Danmaku Mode: {DANMAKU_MODE}")
        logger.info(f"Order Interval: {ORDER_INTERVAL} seconds")
        logger.info(f"Douyin Enabled: {DOUYIN_ENABLED}")
        if DOUYIN_ENABLED:
            logger.info(f"Douyin WebSocket Port: {DOUYIN_WEBSOCKET_PORT}")
        
    except FileNotFoundError:
        logger.error(f"Configuration file {CONFIG_FILE} not found. Using default values.")
        # 设置默认值
        ROOM_ID = 27063248
        SESSDATA = ''
        ACCESS_KEY_ID = '2oZxfhKZNUv2SDBN4Wiuszo6'
        ACCESS_KEY_SECRET = 'gRBXleqvAJLIhi0gFUvp6Dncq76aWY'
        APP_ID = 1757756104085
        ROOM_OWNER_AUTH_CODE = 'CCHWE6NUD43K7'
        DANMAKU_MODE = 'openlive'
        DOUYIN_WEBSOCKET_PORT = 8765
        DOUYIN_ENABLED = False
        BLOCKED_WORDS = []
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing configuration file {CONFIG_FILE}: {e}")
        logger.error("Using default values.")
        # 设置默认值
        ROOM_ID = 27063248
        SESSDATA = ''
        ACCESS_KEY_ID = '2oZxfhKZNUv2SDBN4Wiuszo6'
        ACCESS_KEY_SECRET = 'gRBXleqvAJLIhi0gFUvp6Dncq76aWY'
        APP_ID = 1757756104085
        ROOM_OWNER_AUTH_CODE = 'CCHWE6NUD43K7'
        DANMAKU_MODE = 'openlive'
        DOUYIN_WEBSOCKET_PORT = 8765
        DOUYIN_ENABLED = False
        BLOCKED_WORDS = []
    except Exception as e:
        logger.error(f"Unexpected error loading configuration: {e}")
        logger.error("Using default values.")
        # 设置默认值
        ROOM_ID = 27063248
        SESSDATA = ''
        ACCESS_KEY_ID = '2oZxfhKZNUv2SDBN4Wiuszo6'
        ACCESS_KEY_SECRET = 'gRBXleqvAJLIhi0gFUvp6Dncq76aWY'
        APP_ID = 1757756104085
        ROOM_OWNER_AUTH_CODE = 'CCHWE6NUD43K7'
        DANMAKU_MODE = 'openlive'
        DOUYIN_WEBSOCKET_PORT = 8765
        DOUYIN_ENABLED = False
        BLOCKED_WORDS = []

def filter_username(username: str) -> str:
    """
    过滤用户名中的屏蔽词，将屏蔽词替换为对应数量的*
    例如："bilibili_1234" -> "********_1234"
    """
    if not BLOCKED_WORDS or not username:
        return username
    
    filtered_username = username
    for blocked_word in BLOCKED_WORDS:
        if blocked_word.lower() in filtered_username.lower():
            # 找到屏蔽词的位置（不区分大小写）
            start_pos = filtered_username.lower().find(blocked_word.lower())
            while start_pos != -1:
                # 获取原始大小写的屏蔽词
                original_word = filtered_username[start_pos:start_pos + len(blocked_word)]
                # 替换为对应数量的*
                asterisks = '*' * len(original_word)
                filtered_username = filtered_username[:start_pos] + asterisks + filtered_username[start_pos + len(blocked_word):]
                # 继续查找下一个匹配
                start_pos = filtered_username.lower().find(blocked_word.lower(), start_pos + len(asterisks))
    
    return filtered_username

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

def press_key(key: str, duration: float = 0.02):
    """模拟按下并释放按键"""
    try:
        pyautogui.keyDown(key)
        pyautogui.sleep(duration)
        pyautogui.keyUp(key)
        logger.info(f"Pressed key: {key} (duration: {duration}s)")
    except Exception as e:
        logger.error(f"Failed to press key {key}: {e}")

def control_mgba_run(command: str):
    """奔跑模式控制 mGBA（长按B键的同时执行移动指令）"""
    global executing_command
    
    command = command.strip().lower()
    
    # 移除开头的奔跑关键词，支持r/R/run/跑
    if command.startswith('r'):
        if len(command) > 1 and command[1] == '+':
            command = command[2:].strip()  # 移除r+
        else:
            command = command[1:].strip()  # 移除r
    elif command.startswith('run'):
        if len(command) > 3 and command[3] == '+':
            command = command[4:].strip()  # 移除run+
        else:
            command = command[3:].strip()  # 移除run
    elif command.startswith('跑'):
        if len(command) > 1 and command[1] == '+':
            command = command[2:].strip()  # 移除跑+
        else:
            command = command[1:].strip()  # 移除跑
    
    # 支持两种分隔符：'+' 和空格，优先使用 '+' 分割
    if '+' in command:
        # 按 '+' 分割组合指令，最多3个子指令
        sub_commands = command.split('+', 2)
    else:
        # 按空格分割组合指令，最多3个子指令
        sub_commands = command.split(' ', 2)
    
    valid_sub_commands = []
    
    # 解析每个子指令，只允许ijkl和数字的组合
    for sub_cmd in sub_commands:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        
        # 检查是否是按键+数字格式
        repeat_count = 1
        base_command = sub_cmd
        
        if sub_cmd and sub_cmd[-1].isdigit():
            digit = sub_cmd[-1]
            base_command = sub_cmd[:-1]
            repeat_count = int(digit)
            if repeat_count < 1 or repeat_count > 9:
                repeat_count = 1
        
        # 只允许移动指令 i(上), j(左), k(下), l(右)
        if base_command in ['i', 'j', 'k', 'l']:
            valid_sub_commands.append((base_command, repeat_count))
        else:
            logger.warning(f"Run command only supports i,j,k,l movement keys, ignored: {base_command}")
    
    # 如果有合法子指令，执行奔跑模式
    if valid_sub_commands:
        with execution_lock:
            executing_command = True
            try:
                if not activate_mgba_window():
                    logger.warning("mGBA window not found or activation failed, skipping run command")
                else:
                    # 开始长按B键
                    pyautogui.keyDown('z')  # B键对应z
                    logger.info("Started running mode (B key held down)")
                    
                    try:
                        for base_command, repeat_count in valid_sub_commands:
                            key = COMMAND_TO_KEY[base_command]
                            for i in range(repeat_count):
                                press_key(key)  # 按键持续时间保持0.1秒
                                # if i < repeat_count - 1:  # 不是最后一次按键
                                    # time.sleep(0.01)  # 奔跑模式按键间隔0.01秒
                            # time.sleep(0.01)  # 指令之间的间隔也是0.01秒
                    finally:
                        # 释放B键
                        pyautogui.keyUp('z')
                        logger.info("Stopped running mode (B key released)")
                    
                    logger.info(f"Executed run command: r {command}")
            finally:
                executing_command = False
    else:
        logger.warning(f"No valid movement commands in run command: r {command}")

def add_vote(vote_type):
    """添加投票并实时更新支持率"""
    global freedom_support
    
    with vote_lock:
        previous_support = freedom_support
        should_shake = False
        
        if vote_type == "自由":
            # 检测是否需要触发抖动：当前已经是最高值且还要继续增加
            if freedom_support >= 99.0:
                should_shake = True
                logger.info(f"Vote bar shake triggered: freedom_support at maximum ({freedom_support:.1f}%) and freedom vote received")
            freedom_support += 1.0
        elif vote_type == "秩序":
            # 检测是否需要触发抖动：当前已经是最小值且还要继续减少
            if freedom_support <= 1.0:
                should_shake = True
                logger.info(f"Vote bar shake triggered: freedom_support at minimum ({freedom_support:.1f}%) and order vote received")
            freedom_support -= 1.0
        
        # 确保支持率在合理范围内（1-99%）
        freedom_support = max(1.0, min(99.0, freedom_support))
        
        order_support = 100.0 - freedom_support
        logger.info(f"Added vote: {vote_type}. Current support - 自由: {freedom_support:.1f}%, 秩序: {order_support:.1f}%")
        
        return should_shake

def check_mode_switch():
    """检查是否需要切换模式（基于支持率）"""
    global current_mode
    
    with mode_lock:
        order_support = 100.0 - freedom_support
        
        if current_mode == "秩序":
            # 秩序模式时，自由支持率需要超过50%才能切换到自由模式
            if freedom_support > 50.0:
                current_mode = "自由"
                logger.info(f"Mode switched to 自由 (freedom support: {freedom_support:.1f}%, order support: {order_support:.1f}%)")
                # 重置秩序模式统计
                with order_lock:
                    global order_commands, order_start_time
                    order_commands.clear()
                    order_start_time = None
                return True
        elif current_mode == "自由":
            # 自由模式时，自由支持率需要低于25%（即秩序支持率超过75%）才能切换到秩序模式
            if freedom_support < 25.0:
                current_mode = "秩序"
                logger.info(f"Mode switched to 秩序 (freedom support: {freedom_support:.1f}%, order support: {order_support:.1f}%)")
                # 初始化秩序模式统计
                with order_lock:
                    order_commands.clear()
                    order_start_time = time.time()
                return True
    return False

def add_order_command(display_command, original_command):
    """在秩序模式下添加指令到统计，奔跑指令和普通指令分开统计"""
    global order_start_time
    
    with order_lock:
        if order_start_time is None:
            order_start_time = time.time()
        
        # 判断是否为奔跑指令，如果是则在显示名称前加上标识
        is_run_command = original_command.startswith('run:')
        
        # 为奔跑指令和普通指令创建不同的统计key
        if is_run_command:
            # 奔跑指令：使用原始显示名称，但在统计中保持独立
            stat_key = f"[RUN] {display_command}"
        else:
            # 普通指令：直接使用显示名称
            stat_key = display_command
        
        # 添加指令到统计，存储格式：{stat_key: [count, original_command, display_command]}
        if stat_key in order_commands:
            order_commands[stat_key][0] += 1
        else:
            order_commands[stat_key] = [1, original_command, display_command]
        
        logger.info(f"Order command added: {stat_key}. Current stats: {order_commands}")

def execute_order_command():
    """执行秩序模式下票数最高的指令"""
    if not order_commands:
        return
    
    # 找到票数最高的指令
    sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
    winning_display_command = sorted_commands[0][0]
    winning_votes = sorted_commands[0][1][0]
    winning_original_command = sorted_commands[0][1][1]
    
    logger.info(f"Executing order winner: {winning_display_command} with {winning_votes} votes")
    
    # 执行指令
    with execution_lock:
        global executing_command
        executing_command = True
        try:
            control_mgba(winning_original_command)
        finally:
            executing_command = False

def config_hot_reload_thread():
    """配置热更新线程，每120秒从配置文件中读取最新的ORDER_INTERVAL和屏蔽词"""
    global ORDER_INTERVAL, BLOCKED_WORDS
    logger.info("Config hot reload thread started")
    
    while True:
        try:
            time.sleep(120)  # 等待120秒
            
            # 读取配置文件中的ORDER_INTERVAL和屏蔽词
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                new_order_interval = config.get('ORDER_INTERVAL', 20)
                new_blocked_words = config.get('blocked_words', [])
                
                # 如果ORDER_INTERVAL值发生变化，更新并记录日志
                if new_order_interval != ORDER_INTERVAL:
                    old_value = ORDER_INTERVAL
                    ORDER_INTERVAL = new_order_interval
                    logger.info(f"ORDER_INTERVAL hot reloaded: {old_value} -> {ORDER_INTERVAL} seconds")
                else:
                    logger.debug(f"ORDER_INTERVAL unchanged: {ORDER_INTERVAL} seconds")
                
                # 如果屏蔽词发生变化，更新并记录日志
                if new_blocked_words != BLOCKED_WORDS:
                    old_blocked_words = BLOCKED_WORDS.copy()
                    BLOCKED_WORDS = new_blocked_words
                    logger.info(f"Blocked words hot reloaded: {old_blocked_words} -> {BLOCKED_WORDS}")
                else:
                    logger.debug(f"Blocked words unchanged: {BLOCKED_WORDS}")
                    
            except FileNotFoundError:
                logger.warning(f"Configuration file {CONFIG_FILE} not found during hot reload")
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error during hot reload: {e}")
            except Exception as e:
                logger.error(f"Error during config hot reload: {e}")
                
        except Exception as e:
            logger.error(f"Config hot reload thread error: {e}")
            time.sleep(10)  # 出错时短暂等待后继续

def order_execution_thread():
    """秩序模式执行线程"""
    global order_start_time
    logger.info("Order execution thread started")
    while True:
        should_execute = False
        winning_command = None
        winning_display_command = None
        
        with mode_lock:
            if current_mode == "秩序":
                with order_lock:
                    # 确保秩序模式下有计时器
                    if order_start_time is None:
                        order_start_time = time.time()
                        logger.info("Order mode timer started")
                    
                    current_time = time.time()
                    time_elapsed = current_time - order_start_time
                    
                    if current_time - order_start_time >= ORDER_INTERVAL:
                        # 准备执行票数最高的指令
                        if order_commands:
                            sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                            winning_stat_key = sorted_commands[0][0]
                            winning_command = sorted_commands[0][1][1]  # 获取原始指令
                            winning_display_command = sorted_commands[0][1][2] if len(sorted_commands[0][1]) > 2 else winning_stat_key  # 获取显示指令
                            winning_votes = sorted_commands[0][1][0]
                            should_execute = True
                            logger.info(f"Order execution timer: Winner is {winning_stat_key} with {winning_votes} votes")
                        else:
                            logger.info("Order execution timer: No commands to execute")
                        
                        # 重置统计
                        order_commands.clear()
                        order_start_time = current_time
        
        # 在锁外执行指令，避免死锁
        if should_execute and winning_command:
            logger.info(f"Executing order winner: {winning_display_command}")
            # 检查是否是奔跑指令
            if winning_command.startswith('run:'):
                # 奔跑指令：移除run:前缀并调用奔跑控制函数
                actual_command = winning_command[4:]  # 移除"run:"前缀
                control_mgba_run(actual_command)
            else:
                # 普通指令：直接调用control_mgba
                control_mgba(winning_command)
            
            # 执行完毕后，发送清空的democracy_info更新到前端
            democracy_update = {
                'type': 'democracy_update',
                'democracy_info': {
                    'commands': [],  # 清空指令列表
                    'time_left': ORDER_INTERVAL  # 重置时间
                }
            }
            
            # 发送到所有SSE客户端
            with sse_lock:
                disconnected_clients = []
                for client_queue in sse_clients:
                    try:
                        client_queue.put(democracy_update)
                    except:
                        disconnected_clients.append(client_queue)
                
                # 清理断开的客户端
                for client in disconnected_clients:
                    sse_clients.remove(client)
            
            logger.info("Sent democracy clear update to frontend after execution")
        
        time.sleep(0.5)  # 每0.5秒检查一次，提高响应性

def generate_random_command():
    """生成随机指令，从l0-9+i0-9+j0-9+i0-9中随机选择1-3个"""
    # 定义可用的基础指令
    base_commands = ['l', 'i', 'j', 'i']  # 右、上、左、上
    
    # 随机选择1-3个指令
    selected_commands = []
    for _ in range(random.randint(1, 3)):
        # 随机选择基础指令
        base_cmd = random.choice(base_commands)
        # 随机选择重复次数 0-9
        repeat_count = random.randint(0, 9)
        # 组合指令
        if repeat_count == 0:
            selected_commands.append(base_cmd)
        else:
            selected_commands.append(f"{base_cmd}{repeat_count}")
    
    # 用+连接指令
    command = '+'.join(selected_commands)
    logger.info(f"Generated random command: {command}")
    return command

def auto_save_daemon():
    """自动存档守护线程，每10秒循环发送Shift+F1~Shift+F9"""
    save_slot = 1  # 当前存档位，从F1开始
    logger.info("Auto-save daemon thread started")
    
    while True:
        try:
            # 等待120秒
            time.sleep(120)
            
            # 激活mGBA窗口
            if activate_mgba_window():
                # 发送Shift+F键组合
                key_combination = f"shift+f{save_slot}"
                logger.info(f"Auto-save: Sending {key_combination}")
                
                # 按下Shift+F键
                pyautogui.keyDown('shift')
                time.sleep(0.05)
                pyautogui.press(f'f{save_slot}')
                time.sleep(0.05)
                pyautogui.keyUp('shift')
                
                logger.info(f"Auto-save: Executed {key_combination}")
                
                # 循环到下一个存档位 (F1~F9)
                save_slot = save_slot % 9 + 1
            else:
                logger.warning("Auto-save: mGBA window not found, skipping save")
                
        except Exception as e:
            logger.error(f"Auto-save daemon error: {e}")
            time.sleep(1)  # 出错时短暂等待

def auto_input_thread():
    """自动输入线程函数"""
    global last_command_time, auto_mode, latest_command
    
    while True:
        current_time = time.time()
        
        with auto_input_lock:
            time_since_last = current_time - last_command_time
            
            # 检查是否需要进入自动模式
            if not auto_mode and time_since_last >= AUTO_INPUT_TIMEOUT:
                auto_mode = True
                logger.info("Entering auto mode - no commands received for 120 seconds")
            
            # 如果处于自动模式，每10秒输入一次随机指令
            if auto_mode and time_since_last >= AUTO_INPUT_TIMEOUT:
                # 检查是否到了下一次自动输入的时间
                time_in_auto_mode = time_since_last - AUTO_INPUT_TIMEOUT
                if time_in_auto_mode % AUTO_INPUT_INTERVAL < 1:  # 允许1秒的误差
                    # 生成随机指令
                    random_command = generate_random_command()
                    
                    # 设置为最新指令并执行
                    with latest_command_lock:
                        if not executing_command:
                            latest_command = random_command
                            logger.info(f"Auto-generated command: {random_command}")
                            
                            # 创建自动生成的弹幕数据用于显示
                            danmaku_data = {
                                'username': filter_username('Ninot-Quyi'),
                                'command': random_command.replace('+', ' + '),  # 添加空格显示
                                'timestamp': time.time()
                            }
                            
                            # 添加到显示队列
                            with danmaku_lock:
                                danmaku_display_queue.append(danmaku_data)
                            
                            # 广播给前端
                            broadcast_danmaku(danmaku_data)
        
        time.sleep(1)  # 每秒检查一次


def control_mgba(command: str):
    """根据弹幕命令控制 mGBA（支持组合指令如 a3+b3+i2 或 a3 b3 i2）"""
    global executing_command
    
    command = command.strip().lower()
    
    # 支持两种分隔符：'+' 和空格，优先使用 '+' 分割
    if '+' in command:
        # 按 '+' 分割组合指令，最多3个子指令
        sub_commands = command.split('+', 2)
    else:
        # 按空格分割组合指令，最多3个子指令
        sub_commands = command.split(' ', 2)
    
    valid_sub_commands = []
    
    # 解析每个子指令
    for sub_cmd in sub_commands:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        
        # 检查是否是按键+数字格式
        repeat_count = 1
        base_command = sub_cmd
        
        if sub_cmd and sub_cmd[-1].isdigit():
            digit = sub_cmd[-1]
            base_command = sub_cmd[:-1]
            repeat_count = int(digit)
            if repeat_count < 1 or repeat_count > 9:
                repeat_count = 1
        
        if base_command in COMMAND_TO_KEY:
            valid_sub_commands.append((base_command, repeat_count))
    
    # 如果有合法子指令，依次执行
    if valid_sub_commands:
        with execution_lock:
            executing_command = True
            try:
                if not activate_mgba_window():
                    logger.warning("mGBA window not found or activation failed, skipping key press")
                else:
                    for base_command, repeat_count in valid_sub_commands:
                        key = COMMAND_TO_KEY[base_command]
                        for i in range(repeat_count):
                            press_key(key)
                            if i < repeat_count - 1:  # 不是最后一次按键
                                time.sleep(0.2)  # 按键间隔0.5秒
                        time.sleep(0.2)  # 指令之间的间隔
                    logger.info(f"Executed combined command: {command}")
            finally:
                executing_command = False
    else:
        logger.warning(f"No valid commands in: {command}")


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

# 抖音弹幕WebSocket服务器
douyin_websocket_server = None
douyin_clients = set()  # 存储连接的抖音客户端

async def handle_douyin_websocket(websocket):
    """处理抖音弹幕WebSocket连接"""
    douyin_clients.add(websocket)
    logger.info(f"Douyin WebSocket client connected. Total clients: {len(douyin_clients)}")
    
    try:
        async for message in websocket:
            try:
                # 解析抖音弹幕数据
                data = json.loads(message)
                logger.debug(f"Received Douyin WebSocket message: {message}")
                logger.debug(f"Parsed Douyin data: {data}")
                
                # dycast-main发送的是消息数组，需要遍历处理
                if isinstance(data, list):
                    for msg in data:
                        if isinstance(msg, dict):
                            # 检查弹幕类型，只处理聊天弹幕
                            method = msg.get('method')
                            if method == 'WebcastChatMessage':
                                # 提取用户信息
                                username = "抖音用户"
                                if 'user' in msg and msg['user']:
                                    username = msg['user'].get('name', '抖音用户')
                                
                                # 提取弹幕内容 - 优先使用content字段
                                content = ""
                                if 'content' in msg and msg['content']:
                                    content = msg['content']
                                elif 'rtfContent' in msg and msg['rtfContent']:
                                    # 处理富文本内容，提取纯文本
                                    content_parts = []
                                    for rtf in msg['rtfContent']:
                                        if rtf.get('type') == 1 and rtf.get('text'):  # 普通文本
                                            content_parts.append(rtf['text'])
                                    content = ''.join(content_parts)
                                
                                # 如果有有效的弹幕内容，处理它
                                if content.strip():
                                    logger.info(f"[抖音] {username}: {content}")
                                    # 使用现有的弹幕处理函数，不添加前缀，但传递平台信息
                                    process_danmaku_command(username, content, "douyin", "抖音")
                                else:
                                    logger.debug(f"Empty content in Douyin message: {msg}")
                            else:
                                logger.debug(f"Non-chat message type: {method}")
                elif isinstance(data, dict):
                    # 兼容单个消息对象的情况
                    method = data.get('method')
                    if method == 'WebcastChatMessage':
                        username = "抖音用户"
                        if 'user' in data and data['user']:
                            username = data['user'].get('name', '抖音用户')
                        
                        content = ""
                        if 'content' in data and data['content']:
                            content = data['content']
                        elif 'rtfContent' in data and data['rtfContent']:
                            content_parts = []
                            for rtf in data['rtfContent']:
                                if rtf.get('type') == 1 and rtf.get('text'):
                                    content_parts.append(rtf['text'])
                            content = ''.join(content_parts)
                        
                        if content.strip():
                            logger.info(f"[抖音] {username}: {content}")
                            process_danmaku_command(username, content, "douyin", "抖音")
                else:
                    logger.warning(f"Unexpected data format from Douyin: {type(data)}")
                
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received from Douyin WebSocket: {message}")
            except Exception as e:
                logger.error(f"Error processing Douyin message: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        logger.info("Douyin WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Douyin WebSocket error: {e}")
    finally:
        douyin_clients.discard(websocket)
        logger.info(f"Douyin WebSocket client removed. Remaining clients: {len(douyin_clients)}")

async def start_douyin_websocket_server():
    """启动抖音弹幕WebSocket服务器"""
    global douyin_websocket_server
    
    if not DOUYIN_ENABLED:
        logger.info("Douyin WebSocket server disabled in config")
        return
    
    try:
        douyin_websocket_server = await websockets.serve(
            handle_douyin_websocket, 
            "localhost", 
            DOUYIN_WEBSOCKET_PORT
        )
        logger.info(f"Douyin WebSocket server started on ws://localhost:{DOUYIN_WEBSOCKET_PORT}")
        logger.info("You can now connect dycast-main to this WebSocket server to forward Douyin danmaku")
    except Exception as e:
        logger.error(f"Failed to start Douyin WebSocket server: {e}")

def process_danmaku_command(username: str, command: str, room_id: str = None, platform: str = "哔哩哔哩"):
    """处理弹幕指令的通用函数，支持组合指令如 a3+b3+i2 或 a3 b3 i2，奔跑指令如 r i3 j2，以及模式投票"""
    global latest_command, last_command_time, auto_mode
    
    # 更新最后指令时间并退出自动模式
    with auto_input_lock:
        last_command_time = time.time()
        if auto_mode:
            auto_mode = False
            logger.info("Exiting auto mode - received new danmaku command")
    
    command = command.strip()
    original_command = command  # 保存原始指令用于CSV记录
    command_lower = command.lower()
    
    # 检查是否是奔跑指令 (r/R/run/跑 开头)
    is_run_command = False
    run_part = ""
    
    if command_lower.startswith('r') and len(command_lower) > 1 and (command_lower[1] == ' ' or command_lower[1] == '+' or command_lower[1] in 'ijkl0123456789'):
        # r或R开头的奔跑指令
        is_run_command = True
        if command_lower[1] == '+':
            run_part = command_lower[2:].strip()  # 移除r+
        else:
            run_part = command_lower[1:].strip()  # 移除r
    elif command_lower.startswith('run') and len(command_lower) > 3 and (command_lower[3] == ' ' or command_lower[3] == '+'):
        # run开头的奔跑指令
        is_run_command = True
        if command_lower[3] == '+':
            run_part = command_lower[4:].strip()  # 移除run+
        else:
            run_part = command_lower[3:].strip()  # 移除run
    elif command_lower.startswith('跑') and len(command_lower) > 1 and (command_lower[1] == ' ' or command_lower[1] == '+'):
        # 跑开头的奔跑指令
        is_run_command = True
        if command_lower[1] == '+':
            run_part = command_lower[2:].strip()  # 移除跑+
        else:
            run_part = command_lower[1:].strip()  # 移除跑
    
    if is_run_command:
        
        # 检查奔跑指令的合法性
        valid_run_command = True
        if '+' in run_part:
            sub_commands = run_part.split('+')
        else:
            sub_commands = run_part.split(' ')
        
        for sub_cmd in sub_commands:
            sub_cmd = sub_cmd.strip()
            if not sub_cmd:
                continue
            
            # 检查每个子指令是否只包含ijkl和数字
            base_cmd = sub_cmd.rstrip('0123456789')
            if base_cmd not in ['i', 'j', 'k', 'l']:
                valid_run_command = False
                break
        
        if valid_run_command:
            # 获取当前时间戳
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 生成显示用的指令
            display_commands = []
            for sub_cmd in sub_commands:
                sub_cmd = sub_cmd.strip()
                if not sub_cmd:
                    continue
                
                repeat_count = 1
                base_command = sub_cmd.rstrip('0123456789')
                if sub_cmd != base_command:
                    digit_part = sub_cmd[len(base_command):]
                    if digit_part.isdigit():
                        repeat_count = int(digit_part)
                        if repeat_count < 1 or repeat_count > 9:
                            repeat_count = 1
                
                key = COMMAND_TO_KEY.get(base_command, base_command)
                display_cmd = KEY_TO_DISPLAY.get(key, base_command)
                if repeat_count > 1:
                    display_cmd += str(repeat_count)
                display_commands.append(display_cmd)
            
            # 根据原始指令格式决定显示格式
            if '+' in original_command or original_command.lower().startswith(('r+', 'run+', '跑+')):
                display_command = 'R + ' + ' + '.join(display_commands)  # 使用R + 连接，+号前后加空格
            else:
                display_command = 'R + ' + ' + '.join(display_commands)  # 统一使用R + 格式，+号前后加空格
            
            # 根据当前模式处理奔跑指令
            executed = 0
            with mode_lock:
                if current_mode == "自由":
                    # 自由模式：直接执行奔跑指令
                    with latest_command_lock:
                        if not executing_command:
                            # 直接执行奔跑指令，不通过latest_command队列
                            threading.Thread(target=control_mgba_run, args=(command,), daemon=True).start()
                            executed = 1
                            logger.info(f"Freedom mode - Executing run command: {command}")
                        else:
                            executed = 0
                            logger.info(f"Freedom mode - Run command ignored (executing): {command}")
                elif current_mode == "秩序":
                    # 秩序模式：添加到投票统计，加上run:前缀标识奔跑指令
                    add_order_command(display_command, f"run:{command}")
                    executed = 1
                    logger.info(f"Order mode - Added run command to voting: {display_command}")
            
            # 创建结构化的弹幕数据
            danmaku_data = {
                'username': filter_username(username),
                'command': display_command,
                'timestamp': time.time()
            }
            
            # 添加到显示队列
            with danmaku_lock:
                danmaku_display_queue.append(danmaku_data)
            
            # 广播给前端
            broadcast_danmaku(danmaku_data)
            
            # 保存到CSV
            try:
                danmaku_saver.save_danmaku(current_time, username, original_command, executed, platform)
            except Exception as e:
                logger.error(f"Failed to save run command to CSV: {e}")
            
            # 如果是秩序模式，发送democracy更新
            if current_mode == "秩序":
                with order_lock:
                    if order_commands:
                        sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                        # 使用显示名称而不是统计key
                        formatted_commands = [(votes[2] if len(votes) > 2 else cmd, votes[0]) for cmd, votes in sorted_commands]
                        democracy_update = {
                            'type': 'democracy_update',
                            'democracy_info': {
                                'commands': formatted_commands[:5],
                                'time_left': max(0, ORDER_INTERVAL - (time.time() - order_start_time)) if order_start_time else ORDER_INTERVAL
                            }
                        }
                        
                        with sse_lock:
                            disconnected_clients = []
                            for client_queue in sse_clients:
                                try:
                                    client_queue.put(democracy_update)
                                except:
                                    disconnected_clients.append(client_queue)
                            
                            for client in disconnected_clients:
                                sse_clients.remove(client)
            
            return  # 奔跑指令处理完毕，直接返回
        else:
            logger.warning(f"Invalid run command format: {command}. Run commands can only contain i,j,k,l and numbers.")
            return  # 无效的奔跑指令，直接返回，不继续处理为普通指令
    
    # 获取当前时间戳（精确到秒）
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 检查是否是投票指令
    vote_commands = {
        '自由模式': '自由',
        '自由': '自由', 
        'anarchy': '自由',
        'freedom': '自由',
        '秩序模式': '秩序',
        '秩序': '秩序',
        'democracy': '秩序',
        'order': '秩序'
    }
    
    if command_lower in vote_commands:
        vote_type = vote_commands[command_lower]
        should_shake = add_vote(vote_type)
        
        # 检查是否需要切换模式
        mode_switched = check_mode_switch()
        
        # 保存投票到CSV
        try:
            danmaku_saver.save_danmaku(current_time, username, original_command, 1 if mode_switched else 0, platform)
        except Exception as e:
            logger.error(f"Failed to save vote to CSV: {e}")
        
        # 创建投票显示数据
        vote_display = f" {vote_type}"
        # if mode_switched:
        #     vote_display = f" -> 切换到{current_mode}模式!"
        
        danmaku_data = {
            'username': filter_username(username),
            'command': vote_display,
            'timestamp': time.time()
        }
        
        # 添加到显示队列
        with danmaku_lock:
            danmaku_display_queue.append(danmaku_data)
        
        # 广播给前端
        broadcast_danmaku(danmaku_data)
        
        # 立即发送投票更新信息
        with mode_lock:
            vote_update = {
                'type': 'vote_update',
                'mode_info': {
                    'current_mode': current_mode,
                    'freedom_support': round(freedom_support, 1),
                    'order_support': round(100.0 - freedom_support, 1)
                },
                'mode_switched': mode_switched,
                'should_shake': should_shake if 'should_shake' in locals() else False
            }
        
        # 发送投票更新到所有SSE客户端
        disconnected_clients = []
        for client_queue in sse_clients:
            try:
                client_queue.put(vote_update)
            except:
                disconnected_clients.append(client_queue)
        
        # 清理断开的客户端
        for client in disconnected_clients:
            sse_clients.remove(client)
        
        return  # 投票指令处理完毕，直接返回
    
    # 支持两种分隔符：'+' 和空格，优先使用 '+' 分割
    if '+' in command:
        # 按 '+' 分割组合指令，最多允许3个子指令
        sub_commands = command_lower.split('+', 2)
    else:
        # 按空格分割组合指令，最多允许3个子指令
        sub_commands = command_lower.split(' ', 2)
    
    valid_sub_commands = []
    display_commands = []
    
    # 处理每个子指令
    for sub_cmd in sub_commands:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        
        # 检查是否是按键+数字格式
        repeat_count = 1
        base_command = sub_cmd
        
        if sub_cmd and sub_cmd[-1].isdigit():
            digit = sub_cmd[-1]
            base_command = sub_cmd[:-1]
            repeat_count = int(digit)
            if repeat_count < 1 or repeat_count > 9:
                repeat_count = 1
        
        # 验证基础命令是否合法
        if base_command in COMMAND_TO_KEY:
            valid_sub_commands.append((base_command, repeat_count))
            key = COMMAND_TO_KEY[base_command]
            display_cmd = KEY_TO_DISPLAY.get(key, base_command)
            if repeat_count > 1:
                display_cmd += str(repeat_count)
            display_commands.append(display_cmd)
    
    # 初始化executed变量
    executed = 0  # 默认为未执行
    
    # 如果有合法子指令
    if valid_sub_commands:
        # 生成显示用的组合指令，在+号左右添加空格
        display_command = ' + '.join(display_commands)
        
        # 根据当前模式处理指令
        with mode_lock:
            if current_mode == "自由":
                # 自由模式：直接更新最新指令
                with latest_command_lock:
                    if not executing_command:  # 只有在不执行时才更新
                        latest_command = command
                        executed = 1  # 标记为将要执行
                        logger.info(f"Freedom mode - Updated latest command: {command}")
                    else:
                        executed = 0  # 标记为被忽略
                        logger.info(f"Freedom mode - Command ignored (executing): {command}")
            elif current_mode == "秩序":
                # 秩序模式：添加到投票统计
                # 普通指令直接添加到统计
                add_order_command(display_command, command)
                executed = 1  # 标记为已处理（加入投票）
                logger.info(f"Order mode - Added command to voting: {display_command}")
        
        # 创建结构化的弹幕数据
        danmaku_data = {
            'username': filter_username(username),
            'command': display_command,
            'timestamp': time.time()
        }
        
        # 添加到固定长度的显示队列
        with danmaku_lock:
            danmaku_display_queue.append(danmaku_data)
            logger.info(f"Added danmaku to queue: {danmaku_data}")
        
        # 实时推送给所有SSE客户端
        broadcast_danmaku(danmaku_data)
        
    # 如果是秩序模式，立即发送democracy_info更新（在mode_lock外执行）
    if current_mode == "秩序":
        with order_lock:
            democracy_update = {}
            if order_commands:
                sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                formatted_commands = [(cmd, votes[0]) for cmd, votes in sorted_commands]
                democracy_update = {
                    'type': 'democracy_update',
                    'democracy_info': {
                        'commands': formatted_commands[:5],
                        'time_left': max(0, ORDER_INTERVAL - (time.time() - order_start_time)) if order_start_time else ORDER_INTERVAL
                    }
                }
                
                # 发送到所有SSE客户端
                with sse_lock:
                    disconnected_clients = []
                    for client_queue in sse_clients:
                        try:
                            client_queue.put(democracy_update)
                        except:
                            disconnected_clients.append(client_queue)
                    
                    # 清理断开的客户端
                    for client in disconnected_clients:
                        sse_clients.remove(client)
    else:
        logger.info(f"Unknown command ignored: {command}")
        executed = 0  # 标记为未执行
    
    # 保存原始指令到CSV文件
    try:
        danmaku_saver.save_danmaku(current_time, username, original_command, executed, platform)
    except Exception as e:
        logger.error(f"Failed to save danmaku to CSV: {e}")

class DanmakuHandler(blivedm.BaseHandler):
    """处理 Bilibili WSS 模式直播间消息"""
    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        logger.debug(f"[{client.room_id}] Heartbeat")

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        logger.info(f"[哔哩哔哩] {message.uname}: {message.msg}")
        process_danmaku_command(message.uname, message.msg, str(client.room_id))

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        logger.info(f"[哔哩哔哩] {message.uname} 赠送 {message.gift_name}x{message.num}")

    def _on_user_toast_v2(self, client: blivedm.BLiveClient, message: web_models.UserToastV2Message):
        logger.info(f"[哔哩哔哩] {message.username} 上舰，guard_level={message.guard_level}")

    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        logger.info(f"[哔哩哔哩] 醒目留言 ¥{message.price} {message.uname}: {message.message}")

    def _on_log_in_notice(self, client: blivedm.BLiveClient, message: dict):
        logger.info(f"[哔哩哔哩] Login notice: {message['data']['notice_msg']}")

class OpenLiveHandler(blivedm.BaseHandler):
    """处理 Bilibili OpenLive 模式直播间消息"""
    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        logger.debug(f"[OpenLive] Heartbeat")

    def _on_open_live_danmaku(self, client: blivedm.OpenLiveClient, message: open_models.DanmakuMessage):
        logger.info(f"[哔哩哔哩] {message.uname}: {message.msg}")
        process_danmaku_command(message.uname, message.msg, str(message.room_id))

    def _on_open_live_gift(self, client: blivedm.OpenLiveClient, message: open_models.GiftMessage):
        coin_type = '金瓜子' if message.paid else '银瓜子'
        total_coin = message.price * message.gift_num
        logger.info(f"[哔哩哔哩] {message.uname} 赠送{message.gift_name}x{message.gift_num}"
                   f" （{coin_type}x{total_coin}）")

    def _on_open_live_buy_guard(self, client: blivedm.OpenLiveClient, message: open_models.GuardBuyMessage):
        logger.info(f"[哔哩哔哩] {message.user_info.uname} 购买 大航海等级={message.guard_level}")

    def _on_open_live_super_chat(self, client: blivedm.OpenLiveClient, message: open_models.SuperChatMessage):
        logger.info(f"[哔哩哔哩] 醒目留言 ¥{message.rmb} {message.uname}: {message.message}")

    def _on_open_live_super_chat_delete(self, client: blivedm.OpenLiveClient, message: open_models.SuperChatDeleteMessage):
        logger.info(f"[哔哩哔哩] 删除醒目留言 message_ids={message.message_ids}")

    def _on_open_live_like(self, client: blivedm.OpenLiveClient, message: open_models.LikeMessage):
        logger.info(f"[哔哩哔哩] {message.uname} 点赞")

    def _on_open_live_enter_room(self, client: blivedm.OpenLiveClient, message: open_models.RoomEnterMessage):
        logger.info(f"[哔哩哔哩] {message.uname} 进入房间")

    def _on_open_live_start_live(self, client: blivedm.OpenLiveClient, message: open_models.LiveStartMessage):
        logger.info(f"[哔哩哔哩] 开始直播")

    def _on_open_live_end_live(self, client: blivedm.OpenLiveClient, message: open_models.LiveEndMessage):
        logger.info(f"[哔哩哔哩] 结束直播")

@app.route('/')
def index():
    """渲染 HTML 页面"""
    runtime = get_runtime()
    with danmaku_lock:
        danmaku_list = list(danmaku_display_queue)  # 获取队列中的所有数据
    
    # 获取模式和投票信息
    with mode_lock:
        mode_info = {
            'current_mode': current_mode,
            'freedom_support': round(freedom_support, 1),
            'order_support': round(100.0 - freedom_support, 1)
        }
    
    # 获取秩序模式统计信息
    democracy_info = {}
    if current_mode == "秩序":
        with order_lock:
            if order_commands:
                # 按票数排序
                sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                # 转换为前端需要的格式：[display_command, vote_count]
                formatted_commands = [(votes[2] if len(votes) > 2 else cmd, votes[0]) for cmd, votes in sorted_commands]
                democracy_info = {
                    'commands': formatted_commands[:5],  # 只显示前5名
                    'time_left': max(0, ORDER_INTERVAL - (time.time() - order_start_time)) if order_start_time else ORDER_INTERVAL
                }
    
    return render_template('index.html', 
                         runtime=runtime, 
                         danmaku_list=danmaku_list,
                         mode_info=mode_info,
                         democracy_info=democracy_info)

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
                    # 发送心跳包保持连接，包含模式信息
                    with mode_lock:
                        mode_info = {
                            'current_mode': current_mode,
                            'freedom_support': round(freedom_support, 1),
                            'order_support': round(100.0 - freedom_support, 1)
                        }
                    
                    democracy_info = {}
                    if current_mode == "秩序":
                        with order_lock:
                            if order_commands:
                                sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                                # 转换为前端需要的格式：[display_command, vote_count]
                                formatted_commands = [(votes[2] if len(votes) > 2 else cmd, votes[0]) for cmd, votes in sorted_commands]
                                democracy_info = {
                                    'commands': formatted_commands[:5],
                                    'time_left': max(0, ORDER_INTERVAL - (time.time() - order_start_time)) if order_start_time else ORDER_INTERVAL
                                }
                    
                    heartbeat_data = {
                        'type': 'heartbeat', 
                        'runtime': get_runtime(),
                        'mode_info': mode_info,
                        'democracy_info': democracy_info
                    }
                    yield f"data: {json.dumps(heartbeat_data)}\n\n"
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
    load_config()  # 首先加载配置
    init_session()
    load_or_set_start_time()
    
    # 启动Web服务器线程
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # 启动指令执行线程（无政府模式）
    command_thread = threading.Thread(target=execute_latest_command, daemon=True)
    command_thread.start()
    logger.info("Started command execution thread (anarchy mode)")
    
    # 启动秩序模式执行线程
    order_thread = threading.Thread(target=order_execution_thread, daemon=True)
    order_thread.start()
    logger.info("Started order execution thread")
    
    # 启动自动输入线程
    auto_thread = threading.Thread(target=auto_input_thread, daemon=True)
    auto_thread.start()
    logger.info("Started auto input thread")
    
    # 启动自动存档守护线程
    auto_save_thread = threading.Thread(target=auto_save_daemon, daemon=True)
    auto_save_thread.start()
    logger.info("Started auto-save daemon thread")
    
    # 启动配置热更新线程
    config_reload_thread = threading.Thread(target=config_hot_reload_thread, daemon=True)
    config_reload_thread.start()
    logger.info("Started config hot reload thread")
    
    # 启动抖音WebSocket服务器
    if DOUYIN_ENABLED:
        douyin_task = asyncio.create_task(start_douyin_websocket_server())
        logger.info("Started Douyin WebSocket server task")
    
    try:
        # 同时运行哔哩哔哩客户端和抖音WebSocket服务器
        if DOUYIN_ENABLED:
            await asyncio.gather(
                run_bilibili_client(),
                douyin_task
            )
        else:
            await run_bilibili_client()
    finally:
        if session:
            await session.close()
            logger.info("HTTP session closed")
        # 关闭弹幕保存器
        danmaku_saver.close()
        # 关闭抖音WebSocket服务器
        if douyin_websocket_server:
            douyin_websocket_server.close()
            await douyin_websocket_server.wait_closed()
            logger.info("Douyin WebSocket server closed")

if __name__ == "__main__":
    asyncio.run(main())
