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
ROOM_ID = 27063248

# Bilibili 登录 Cookie 的 SESSDATA（替换为有效值）
SESSDATA = ''

# OpenLive 模式配置
ACCESS_KEY_ID = 'example_access_key_id'  # 替换为你的 Access Key ID
ACCESS_KEY_SECRET = 'example_access_key_secret'  # 替换为你的 Access Key Secret
APP_ID = 1757756104085  # 在开放平台创建的项目ID
ROOM_OWNER_AUTH_CODE = 'example_room_owner_auth_code'  # 主播身份码

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

# 最新指令缓存机制（无政府模式）
latest_command = None  # 最新的指令
latest_command_lock = threading.Lock()  # 最新指令锁
executing_command = False  # 是否正在执行指令
execution_lock = threading.Lock()  # 执行锁

# 模式切换系统
current_mode = "自由"  # 当前模式："自由" 或 "秩序"
mode_lock = threading.Lock()  # 模式锁
vote_queue = deque(maxlen=100)  # 投票队列，最大100票
vote_lock = threading.Lock()  # 投票锁
freedom_votes = 0  # 自由模式票数
order_votes = 0  # 秩序模式票数

# 秩序模式相关
order_commands = {}  # 秩序模式指令统计 {command: count}
order_start_time = None  # 秩序模式统计开始时间
order_lock = threading.Lock()  # 秩序模式锁
ORDER_INTERVAL = 10  # 秩序模式统计间隔（秒）

# 自动输入机制
last_command_time = time.time()  # 最后一次接收指令的时间
auto_input_lock = threading.Lock()  # 自动输入锁
AUTO_INPUT_TIMEOUT = 120  # 120秒无操作自动输入
AUTO_INPUT_INTERVAL = 10  # 无人值守状态每10秒输入一次
auto_mode = False  # 是否处于自动模式

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

def add_vote(vote_type):
    """添加投票到队列并更新票数统计"""
    global freedom_votes, order_votes
    
    with vote_lock:
        # 如果队列满了，移除最旧的投票
        if len(vote_queue) >= 100:
            old_vote = vote_queue.popleft()
            if old_vote == "自由":
                freedom_votes = max(0, freedom_votes - 1)
            elif old_vote == "秩序":
                order_votes = max(0, order_votes - 1)
        
        # 添加新投票
        vote_queue.append(vote_type)
        if vote_type == "自由":
            freedom_votes += 1
        elif vote_type == "秩序":
            order_votes += 1
        
        logger.info(f"Added vote: {vote_type}. Current votes - 自由: {freedom_votes}, 秩序: {order_votes}")

def check_mode_switch():
    """检查是否需要切换模式（基于百分比）"""
    global current_mode
    
    with mode_lock:
        total_votes = freedom_votes + order_votes
        if total_votes == 0:
            return False
            
        freedom_percentage = (freedom_votes / total_votes) * 100
        order_percentage = (order_votes / total_votes) * 100
        
        if current_mode == "秩序":
            # 秩序模式时，自由票需要超过50%才能切换到自由模式
            if freedom_percentage > 50:
                current_mode = "自由"
                logger.info(f"Mode switched to 自由 (freedom: {freedom_percentage:.1f}%, order: {order_percentage:.1f}%)")
                # 重置秩序模式统计
                with order_lock:
                    global order_commands, order_start_time
                    order_commands.clear()
                    order_start_time = None
                return True
        elif current_mode == "自由":
            # 自由模式时，秩序票需要超过75%才能切换到秩序模式
            if order_percentage > 75:
                current_mode = "秩序"
                logger.info(f"Mode switched to 秩序 (freedom: {freedom_percentage:.1f}%, order: {order_percentage:.1f}%)")
                # 初始化秩序模式统计
                with order_lock:
                    order_commands.clear()
                    order_start_time = time.time()
                return True
    return False

def add_order_command(display_command, original_command):
    """在秩序模式下添加指令到统计"""
    global order_start_time
    
    with order_lock:
        if order_start_time is None:
            order_start_time = time.time()
        
        # 添加指令到统计，存储格式：{display_command: [count, original_command]}
        if display_command in order_commands:
            order_commands[display_command][0] += 1
        else:
            order_commands[display_command] = [1, original_command]
        
        logger.info(f"Order command added: {display_command}. Current stats: {order_commands}")

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
                            winning_display_command = sorted_commands[0][0]
                            winning_command = sorted_commands[0][1][1]  # 获取原始指令
                            winning_votes = sorted_commands[0][1][0]
                            should_execute = True
                            logger.info(f"Order execution timer: Winner is {winning_display_command} with {winning_votes} votes")
                        else:
                            logger.info("Order execution timer: No commands to execute")
                        
                        # 重置统计
                        order_commands.clear()
                        order_start_time = current_time
        
        # 在锁外执行指令，避免死锁
        if should_execute and winning_command:
            logger.info(f"Executing order winner: {winning_display_command}")
            # 直接调用control_mgba，它内部会处理execution_lock
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
                                'username': 'Ninot-Quyi',
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
                                time.sleep(0.15)  # 按键间隔0.5秒
                        time.sleep(0.5)  # 指令之间的间隔
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

def process_danmaku_command(username: str, command: str, room_id: str = None):
    """处理弹幕指令的通用函数，支持组合指令如 a3+b3+i2 或 a3 b3 i2，以及模式投票"""
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
        add_vote(vote_type)
        
        # 检查是否需要切换模式
        mode_switched = check_mode_switch()
        
        # 保存投票到CSV
        try:
            danmaku_saver.save_danmaku(current_time, username, original_command, 1 if mode_switched else 0)
        except Exception as e:
            logger.error(f"Failed to save vote to CSV: {e}")
        
        # 创建投票显示数据
        vote_display = f"投票: {vote_type}"
        # if mode_switched:
        #     vote_display = f" -> 切换到{current_mode}模式!"
        
        danmaku_data = {
            'username': username,
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
                    'freedom_votes': freedom_votes,
                    'order_votes': order_votes,
                    'total_votes': len(vote_queue)
                },
                'mode_switched': mode_switched
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
                add_order_command(display_command, command)
                executed = 1  # 标记为已处理（加入投票）
                logger.info(f"Order mode - Added command to voting: {display_command}")
        
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
        danmaku_saver.save_danmaku(current_time, username, original_command, executed)
    except Exception as e:
        logger.error(f"Failed to save danmaku to CSV: {e}")

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
    
    # 获取模式和投票信息
    with mode_lock:
        mode_info = {
            'current_mode': current_mode,
            'freedom_votes': freedom_votes,
            'order_votes': order_votes,
            'total_votes': len(vote_queue)
        }
    
    # 获取秩序模式统计信息
    democracy_info = {}
    if current_mode == "秩序":
        with order_lock:
            if order_commands:
                # 按票数排序
                sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                # 转换为前端需要的格式：[display_command, vote_count]
                formatted_commands = [(cmd, votes[0]) for cmd, votes in sorted_commands]
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
                            'freedom_votes': freedom_votes,
                            'order_votes': order_votes,
                            'total_votes': len(vote_queue)
                        }
                    
                    democracy_info = {}
                    if current_mode == "秩序":
                        with order_lock:
                            if order_commands:
                                sorted_commands = sorted(order_commands.items(), key=lambda x: x[1][0], reverse=True)
                                # 转换为前端需要的格式：[display_command, vote_count]
                                formatted_commands = [(cmd, votes[0]) for cmd, votes in sorted_commands]
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
