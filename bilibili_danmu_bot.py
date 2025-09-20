#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bilibili直播间弹幕机器人 - 核心功能整合版
基于神奇弹幕项目的核心功能提取和Python实现

主要功能：
1. 二维码扫码登录获取Cookie
2. 根据房间号连接直播间WebSocket
3. 接收和解析弹幕消息
4. 发送弹幕到直播间

作者：基于Bilibili-MagicalDanmaku项目分析整合
"""

import asyncio
import websockets
import json
import struct
import zlib
import time
import logging
import requests
import qrcode
from io import BytesIO
import base64
import hashlib
import re
from typing import Dict, Any, Optional, Callable, List
from collections import deque
import random
import os

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BilibiliDanmuBot:
    """Bilibili直播间弹幕机器人"""
    
    def __init__(self, config_path: str = "config.json"):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # 登录相关
        self.qrcode_key = ""
        self.cookies = ""
        self.csrf_token = ""
        self.uid = ""
        self.buvid3 = ""
        self.buvid4 = ""
        
        # WBI 签名相关
        self.wbi_mixin_key = ""
        
        # 房间相关
        self.room_id = ""
        self.websocket = None
        self.is_connected = False
        
        # 回调函数
        self.on_danmu_callback: Optional[Callable] = None
        self.on_gift_callback: Optional[Callable] = None
        self.on_enter_callback: Optional[Callable] = None
        
        # AI自动回复功能
        self.ai_mode = False  # 是否开启AI模式
        self.ai_api_key = ""  # AI API密钥
        self.ai_api_url = ""  # AI API地址
        self.ai_model = "qwen-plus"  # AI模型
        self.recent_danmus = deque(maxlen=15)  # 最近的弹幕消息（改为15条）
        self.last_ai_response_time = 0  # 上次AI回复时间
        self.ai_response_interval = 30  # AI回复间隔（改为30秒）
        self.min_danmu_for_response = 3  # 最少弹幕数量才回复
        
        # 配置文件
        self.config_path = config_path
        self.config = self.load_config()
        
    def generate_qr_login(self) -> str:
        """
        生成二维码登录链接
        返回二维码的base64编码图片数据
        """
        try:
            # 1. 获取登录URL和qrcode_key
            url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            response = self.session.get(url)
            data = response.json()
            
            if data['code'] != 0:
                raise Exception(f"获取二维码失败: {data['message']}")
            
            qr_url = data['data']['url']
            self.qrcode_key = data['data']['qrcode_key']
            
            # 2. 生成二维码并在命令行中显示
            qr = qrcode.QRCode(version=1, box_size=1, border=2)
            qr.add_data(qr_url)
            qr.make(fit=True)
            
            # 在命令行中打印二维码
            print("\n" + "=" * 50)
            print("哔哩哔哩登录二维码")
            print("=" * 50)
            qr.print_ascii(invert=True)
            print("=" * 50)
            print("请使用哔哩哔哩APP扫描上方二维码登录")
            print("或者打开下方链接在手机上登录:")
            print(qr_url)
            print("=" * 50 + "\n")
            
            # 同时保存为文件作为备用
            try:
                import os
                img = qr.make_image(fill_color="black", back_color="white")
                qr_filename = os.path.join(os.getcwd(), "bilibili_login_qr.png")
                img.save(qr_filename)
                logger.info(f"二维码也已保存为文件: {qr_filename}")
            except Exception as save_error:
                logger.warning(f"保存二维码文件失败: {save_error}")
            
            return qr_url
            
        except Exception as e:
            logger.error(f"生成二维码失败: {e}")
            return ""
    
    def check_qr_login(self) -> bool:
        """
        检查二维码登录状态
        返回是否登录成功
        """
        try:
            url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={self.qrcode_key}"
            response = self.session.get(url)
            data = response.json()
            
            if data['code'] != 0:
                logger.error(f"检查登录状态失败: {data['message']}")
                return False
            
            code = data['data']['code']
            
            if code == 0:  # 登录成功
                logger.info("扫码登录成功！")
                
                # 获取cookies
                cookies = []
                for cookie in response.cookies:
                    cookies.append(f"{cookie.name}={cookie.value}")
                
                # 获取buvid
                self._get_buvid()
                
                # 添加buvid到cookies
                if self.buvid3:
                    cookies.append(f"buvid3={self.buvid3}")
                if self.buvid4:
                    cookies.append(f"buvid4={self.buvid4}")
                
                # 添加刷新token
                refresh_token = data['data'].get('refresh_token', '')
                if refresh_token:
                    cookies.append(f"ac_time_value={refresh_token}")
                
                self.cookies = "; ".join(cookies)
                self.session.headers.update({'Cookie': self.cookies})
                
                # 提取csrf_token和uid
                self._extract_user_info()
                
                return True
                
            elif code == 86038:  # 二维码已失效
                logger.warning("二维码已失效")
                return False
            elif code == 86090:  # 已扫描但未确认
                logger.info("等待确认...")
                return False
            elif code == 86101:  # 未扫描
                logger.info("等待扫描...")
                return False
            else:
                logger.warning(f"未知状态码: {code}")
                return False
                
        except Exception as e:
            logger.error(f"检查登录状态异常: {e}")
            return False
    
    def _get_buvid(self):
        """获取buvid"""
        try:
            url = "https://api.bilibili.com/x/frontend/finger/spi"
            response = self.session.get(url)
            data = response.json()
            
            if data['code'] == 0:
                self.buvid3 = data['data'].get('b_3', '')
                self.buvid4 = data['data'].get('b_4', '')
                logger.info(f"获取到BUVID: {self.buvid3}, {self.buvid4}")
                
        except Exception as e:
            logger.error(f"获取buvid失败: {e}")
    
    def _extract_user_info(self):
        """从cookies中提取用户信息"""
        try:
            cookie_dict = {}
            for item in self.cookies.split('; '):
                if '=' in item:
                    key, value = item.split('=', 1)
                    cookie_dict[key] = value
            
            self.csrf_token = cookie_dict.get('bili_jct', '')
            self.uid = cookie_dict.get('DedeUserID', '')
            
            logger.info(f"用户信息: UID={self.uid}, CSRF={self.csrf_token}")
            
            # 获取WBI签名密钥
            self._get_wbi_key()
            
        except Exception as e:
            logger.error(f"提取用户信息失败: {e}")
    
    def _get_wbi_key(self):
        """获取WBI签名密钥"""
        try:
            url = "https://api.bilibili.com/x/web-interface/nav"
            response = self.session.get(url)
            data = response.json()
            
            if data['code'] != 0:
                logger.warning("获取WBI密钥失败，使用默认配置")
                return
                
            wbi_img = data['data']['wbi_img']
            img_url = wbi_img['img_url']
            sub_url = wbi_img['sub_url']
            
            # 提取32位字符串
            img_key = re.search(r'/(\w{32})\.png', img_url).group(1)
            sub_key = re.search(r'/(\w{32})\.png', sub_url).group(1)
            wbi_key = img_key + sub_key
            
            # 重排序生成mixin key
            mixin_key_enc_tab = [
                46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
                33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
                61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
                36, 20, 34, 44, 52
            ]
            
            self.wbi_mixin_key = ''.join([wbi_key[i] for i in mixin_key_enc_tab])[:32]
            logger.info(f"WBI密钥获取成功: {self.wbi_mixin_key}")
            
        except Exception as e:
            logger.error(f"获取WBI密钥失败: {e}")
    
    def _wbi_sign(self, params: str) -> str:
        """WBI签名"""
        if not self.wbi_mixin_key:
            return params
            
        # 添加时间戳
        if 'wts=' not in params:
            params += f"&wts={int(time.time())}"
        
        # 按键名排序
        param_list = params.split('&')
        param_list.sort()
        sorted_params = '&'.join(param_list)
        
        # 计算MD5
        md5_hash = hashlib.md5((sorted_params + self.wbi_mixin_key).encode()).hexdigest()
        return params + f"&w_rid={md5_hash}"
    
    def wait_for_login(self, timeout: int = 300) -> bool:
        """
        等待用户扫码登录
        timeout: 超时时间（秒）
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.check_qr_login():
                return True
            time.sleep(3)
        
        logger.error("登录超时")
        return False
    
    def get_room_info(self, room_id: str) -> Dict[str, Any]:
        """获取房间信息"""
        try:
            url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
            response = self.session.get(url)
            data = response.json()
            
            if data['code'] != 0:
                raise Exception(f"获取房间信息失败: {data['message']}")
            
            room_info = data['data']
            self.room_id = str(room_info['room_id'])  # 真实房间号
            
            # 安全获取主播名称，有些房间可能没有uname字段
            title = room_info.get('title', '未知标题')
            uname = room_info.get('uname') or room_info.get('anchor_info', {}).get('base_info', {}).get('uname', '未知主播')
            
            logger.info(f"房间信息: {title} - 主播: {uname}")
            return room_info
            
        except Exception as e:
            logger.error(f"获取房间信息失败: {e}")
            return {}
    
    def get_danmu_info(self) -> Dict[str, Any]:
        """获取弹幕服务器信息"""
        try:
            # 错误码-352通常表示需要登录，添加必要的headers和cookies
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': f'https://live.bilibili.com/{self.room_id}',
                'Origin': 'https://live.bilibili.com'
            }
            
            # 使用WBI签名
            params = f"id={self.room_id}&type=0"
            if self.wbi_mixin_key:
                params = self._wbi_sign(params)
            
            url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?{params}"
            
            # 如果有cookies，添加到请求中
            if self.cookies:
                headers['Cookie'] = self.cookies
                
            response = self.session.get(url, headers=headers)
            data = response.json()
            
            if data['code'] != 0:
                # 如果还是失败，尝试使用备用方法
                logger.warning(f"获取弹幕服务器信息失败 (code: {data['code']}): {data.get('message', '未知错误')}")
                logger.info("尝试使用默认弹幕服务器配置...")
                
                # 返回默认配置
                return {
                    'token': '',
                    'host_list': [
                        {'host': 'broadcastlv.chat.bilibili.com', 'port': 2243, 'wss_port': 443, 'ws_port': 2244}
                    ]
                }
            
            return data['data']
            
        except Exception as e:
            logger.error(f"获取弹幕服务器信息失败: {e}")
            logger.info("使用默认弹幕服务器配置...")
            # 返回默认配置作为兜底
            return {
                'token': '',
                'host_list': [
                    {'host': 'broadcastlv.chat.bilibili.com', 'port': 2243, 'wss_port': 443, 'ws_port': 2244}
                ]
            }
    
    def _make_packet(self, data: bytes, operation: int) -> bytes:
        """构造数据包"""
        body = data
        header = struct.pack('>IHHII', 
                           16 + len(body),  # 总长度
                           16,              # 头部长度
                           1,               # 协议版本
                           operation,       # 操作码
                           1)               # sequence
        return header + body
    
    def _send_auth_packet(self):
        """发送认证包"""
        auth_data = {
            "uid": int(self.uid) if self.uid else 0,
            "roomid": int(self.room_id),
            "protover": 2,
            "platform": "web",
            "type": 2,
            "key": self.danmu_token,
            "buvid": self.buvid3  # 添加buvid字段
        }
        
        packet = self._make_packet(json.dumps(auth_data).encode(), 7)  # OP_AUTH
        return packet
    
    def _send_heartbeat_packet(self):
        """发送心跳包"""
        packet = self._make_packet(b'[object Object]', 2)  # OP_HEARTBEAT
        return packet
    
    def _parse_packet(self, data: bytes):
        """解析数据包"""
        offset = 0
        while offset < len(data):
            if offset + 16 > len(data):
                break
                
            # 解析包头
            header = struct.unpack('>IHHII', data[offset:offset+16])
            pack_len, header_len, proto_ver, operation, sequence = header
            
            # 获取包体
            body = data[offset + header_len:offset + pack_len]
            
            if operation == 8:  # 认证回复
                result = json.loads(body.decode())
                if result.get('code') == 0:
                    logger.info("WebSocket认证成功")
                else:
                    logger.error(f"WebSocket认证失败: {result}")
                    
            elif operation == 3:  # 心跳回复
                popularity = struct.unpack('>I', body)[0]
                logger.debug(f"当前人气值: {popularity}")
                
            elif operation == 5:  # 普通消息
                if proto_ver == 2:  # zlib压缩
                    try:
                        body = zlib.decompress(body)
                        self._parse_packet(body)
                    except:
                        logger.error("zlib解压失败")
                elif proto_ver == 0:  # 未压缩
                    try:
                        msg = json.loads(body.decode())
                        self._handle_message(msg)
                    except:
                        logger.error(f"解析消息失败: {body}")
            
            offset += pack_len
    
    def _handle_message(self, msg: Dict[str, Any]):
        """处理消息"""
        cmd = msg.get('cmd', '')
        
        if cmd == 'DANMU_MSG':  # 弹幕消息
            info = msg['info']
            content = info[1]  # 弹幕内容
            user_info = info[2]  # 用户信息
            username = user_info[1]  # 用户名
            uid = user_info[0]  # 用户ID
            
            logger.info(f"[弹幕] {username}: {content}")
            
            # 收集弹幕用于AI分析
            if self.ai_mode and str(uid) != self.uid:  # 不收集自己的弹幕
                # 检查是否是机器人自己发送的弹幕（通过用户名判断）
                if not self._is_bot_message(username, content):
                    self.recent_danmus.append({
                        'username': username,
                        'content': content,
                        'timestamp': time.time()
                    })
                    logger.debug(f"收集弹幕[{len(self.recent_danmus)}/{self.recent_danmus.maxlen}]: {username}: {content}")
                else:
                    logger.debug(f"跳过机器人自己的弹幕: {username}: {content}")
            
            if self.on_danmu_callback:
                self.on_danmu_callback({
                    'username': username,
                    'uid': uid,
                    'content': content,
                    'timestamp': time.time()
                })
                
        elif cmd == 'SEND_GIFT':  # 礼物消息
            data = msg['data']
            username = data['uname']
            gift_name = data['giftName']
            num = data['num']
            
            logger.info(f"[礼物] {username} 送出 {gift_name} x{num}")
            
            if self.on_gift_callback:
                self.on_gift_callback({
                    'username': username,
                    'gift_name': gift_name,
                    'num': num,
                    'timestamp': time.time()
                })
                
        elif cmd == 'INTERACT_WORD':  # 进入直播间
            data = msg['data']
            username = data['uname']
            
            logger.info(f"[进入] {username} 进入直播间")
            
            if self.on_enter_callback:
                self.on_enter_callback({
                    'username': username,
                    'timestamp': time.time()
                })
    
    async def connect_websocket(self, room_id: str):
        """连接WebSocket"""
        try:
            # 获取房间信息
            room_info = self.get_room_info(room_id)
            if not room_info:
                return False
            
            # 获取弹幕服务器信息
            danmu_info = self.get_danmu_info()
            if not danmu_info:
                return False
            
            self.danmu_token = danmu_info['token']
            host_list = danmu_info['host_list']
            
            # 选择服务器
            if host_list:
                host = host_list[0]['host']
                port = host_list[0]['wss_port']
                ws_url = f"wss://{host}:{port}/sub"
            else:
                ws_url = "wss://broadcastlv.chat.bilibili.com:443/sub"
            
            logger.info(f"连接弹幕服务器: {ws_url}")
            
            # 连接WebSocket
            self.websocket = await websockets.connect(ws_url)
            self.is_connected = True
            
            # 发送认证包
            auth_packet = self._send_auth_packet()
            await self.websocket.send(auth_packet)
            
            # 启动心跳
            asyncio.create_task(self._heartbeat_loop())
            
            # 启动AI自动回复循环
            if self.ai_mode:
                logger.info(f"AI模式已启动，使用模型: {self.ai_model}")
                asyncio.create_task(self._ai_response_loop())
            
            # 接收消息
            async for message in self.websocket:
                self._parse_packet(message)
                
        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")
            self.is_connected = False
    
    async def _heartbeat_loop(self):
        """心跳循环"""
        while self.is_connected and self.websocket:
            try:
                heartbeat_packet = self._send_heartbeat_packet()
                await self.websocket.send(heartbeat_packet)
                await asyncio.sleep(30)  # 30秒心跳
            except:
                break
    
    def send_danmu(self, message: str) -> bool:
        """发送弹幕"""
        try:
            if not self.cookies or not self.csrf_token:
                logger.error("未登录，无法发送弹幕")
                return False
            
            url = "https://api.live.bilibili.com/msg/send"
            
            # 设置完整的请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': f'https://live.bilibili.com/{self.room_id}',
                'Origin': 'https://live.bilibili.com',
                'Cookie': self.cookies,
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
            }
            
            # 使用原项目的完整参数格式
            data = {
                'color': '4546550',      # 弹幕颜色
                'fontsize': '25',        # 字体大小
                'mode': '4',             # 弹幕模式
                'msg': message,          # 弹幕内容
                'rnd': str(int(time.time())),  # 时间戳
                'roomid': self.room_id,  # 房间ID
                'bubble': '5',           # 气泡样式
                'csrf_token': self.csrf_token,  # CSRF token
                'csrf': self.csrf_token  # CSRF token (重复)
            }
            
            response = self.session.post(url, data=data, headers=headers)
            result = response.json()
            
            if result.get('code') == 0:
                logger.info(f"弹幕发送成功: {message}")
                return True
            else:
                error_msg = result.get('message', '未知错误')
                # 处理常见错误码
                if error_msg == 'f':
                    error_msg = "包含屏蔽词"
                elif error_msg == 'k':
                    error_msg = "包含直播间屏蔽词"
                
                logger.error(f"弹幕发送失败: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"发送弹幕异常: {e}")
            return False
    
    def set_danmu_callback(self, callback: Callable):
        """设置弹幕回调函数"""
        self.on_danmu_callback = callback
    
    def set_gift_callback(self, callback: Callable):
        """设置礼物回调函数"""
        self.on_gift_callback = callback
    
    def set_enter_callback(self, callback: Callable):
        """设置进入回调函数"""
        self.on_enter_callback = callback
    
    async def _call_ai_api(self, messages: List[Dict[str, str]]) -> str:
        """调用通义千问AI API"""
        try:
            url = self.ai_api_url or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.ai_api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.ai_model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 20
            }
            
            logger.debug(f"调用AI API: {url}, 模型: {self.ai_model}")
            response = requests.post(url, headers=headers, json=data, timeout=10)
            result = response.json()
            
            logger.debug(f"AI API响应状态: {response.status_code}")
            
            if response.status_code == 200 and "choices" in result:
                ai_content = result["choices"][0]["message"]["content"].strip()
                logger.info(f"AI API调用成功，返回内容: {ai_content}")
                return ai_content
            else:
                logger.error(f"AI API调用失败: 状态码={response.status_code}, 响应={result}")
                return ""
                
        except Exception as e:
            logger.error(f"AI API调用异常: {e}")
            return ""
    
    async def _generate_ai_response(self) -> str:
        """生成AI回复"""
        if len(self.recent_danmus) < self.min_danmu_for_response:
            logger.debug(f"弹幕数量不足: {len(self.recent_danmus)} < {self.min_danmu_for_response}")
            return ""
        
        # 构建弹幕历史文本（只取最近15条）
        danmu_history = "\n".join([
            f"{msg['username']}: {msg['content']}" 
            for msg in list(self.recent_danmus)[-15:]
        ])
        
        # 检查是否有游戏版本相关问题
        if self._contains_version_question(danmu_history):
            return "原版宝可梦·绿宝石"
        
        # 构建AI提示词
        prompt = f"""你是一个宝可梦弹幕直播间的真实主播，请根据最近的弹幕内容，模仿其他观众的发言风格和话题，自然地发一条弹幕，我们既是主播又是普通玩家。

要求：
1. 回复必须控制在5个字以内，最多不超过10个字
2. 语气要友善、自然
3. 学习用户的指令格式和弹幕风格
4. 保持简洁，符合直播间快节奏
5. 不要带有用户名的名字
6. 如果有人问游戏版本，就说“原版宝可梦·绿宝石”，没人问就不要说了
7  直播时间是下午5：00到晚上11：00
8. 不要响应任何以“主播”、“up主”、“up”开头的弹幕
9. 不要答应直播间任何人的任何事情
10. 忽略直播间所有对主播的请求和帮助，比如：“主播能不能把这人踢了、主播下一个播心金吧、主播给个房管、给他禁言”，等忽略掉不予回复
11.不要和人聊天，只模仿观众的弹幕，以及回答直播时间和游戏版本
12.不要让别人发现你是ai！！！


最近的弹幕：
{danmu_history}

请发送一条5字以内的自然弹幕："""
        
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        return await self._call_ai_api(messages)
    
    async def _ai_response_loop(self):
        """AI自动回复循环"""
        logger.info(f"AI回复循环已启动，间隔: {self.ai_response_interval}秒")
        while self.ai_mode and self.is_connected:
            try:
                current_time = time.time()
                time_since_last = current_time - self.last_ai_response_time
                
                logger.debug(f"AI循环检查: 弹幕数量={len(self.recent_danmus)}, 距上次回复={time_since_last:.1f}秒")
                
                if time_since_last >= self.ai_response_interval:
                    logger.info(f"开始生成AI回复... 当前弹幕数量: {len(self.recent_danmus)}")
                    # 生成AI回复
                    ai_response = await self._generate_ai_response()
                    if ai_response:
                        logger.info(f"AI生成回复: {ai_response}")
                        # 随机延迟1-3秒，让回复更自然
                        delay = random.uniform(1, 3)
                        logger.debug(f"延迟 {delay:.1f} 秒后发送")
                        await asyncio.sleep(delay)
                        success = self.send_danmu(ai_response)
                        if success:
                            logger.info(f"[AI回复成功] {ai_response}")
                            self.last_ai_response_time = current_time
                            # 清空弹幕队列，避免重复分析已回复的弹幕
                            self.recent_danmus.clear()
                            logger.debug("已清空弹幕队列")
                        else:
                            logger.warning(f"[AI回复发送失败] {ai_response}")
                    else:
                        logger.warning("AI未生成回复（可能弹幕数量不足或API调用失败）")
                
                await asyncio.sleep(1)  # 每秒检查一次
                
            except Exception as e:
                logger.error(f"AI回复循环异常: {e}")
                await asyncio.sleep(5)
    
    def enable_ai_mode(self, api_key: str, interval: int = 10):
        """开启AI模式"""
        self.ai_mode = True
        self.ai_api_key = api_key
        self.ai_response_interval = interval
        logger.info(f"AI模式已开启，回复间隔: {interval}秒")
    
    def load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info(f"配置文件加载成功: {self.config_path}")
                return config
            else:
                logger.warning(f"配置文件不存在: {self.config_path}，使用默认配置")
                return {}
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def get_config_value(self, key: str, default=None):
        """获取配置值"""
        keys = key.split('.')
        value = self.config
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def init_from_config(self):
        """从配置文件初始化设置"""
        # 初始化AI配置
        ai_config = self.get_config_value('ai_danmu_bot', {})
        if ai_config.get('enabled', False):
            self.ai_api_key = ai_config.get('api_key', '')
            self.ai_api_url = ai_config.get('api_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
            self.ai_model = ai_config.get('model', 'qwen-plus')
            self.ai_response_interval = ai_config.get('response_interval', 30)
            self.min_danmu_for_response = ai_config.get('min_danmu_for_response', 3)
            
            # 更新弹幕历史队列大小
            max_history = ai_config.get('max_danmu_history', 15)
            self.recent_danmus = deque(maxlen=max_history)
            
            if self.ai_api_key:
                self.ai_mode = True
                logger.info(f"从配置文件启用AI模式: 模型={self.ai_model}, 间隔={self.ai_response_interval}秒")
            else:
                logger.warning("AI API密钥未配置，AI模式未启用")
        else:
            logger.info("AI模式在配置文件中被禁用")
    
    def get_room_id_from_config(self) -> Optional[str]:
        """从配置文件获取房间号"""
        room_id = self.get_config_value('room_id')
        if room_id and room_id != "example_room_id":
            return str(room_id)
        return None
    
    def disable_ai_mode(self):
        """关闭AI模式"""
        self.ai_mode = False
        logger.info("AI模式已关闭")
    
    def is_ai_mode_enabled(self) -> bool:
        """检查AI模式是否开启"""
        return self.ai_mode
    
    def _is_bot_message(self, username: str, content: str) -> bool:
        """判断是否是机器人自己发送的消息"""
        # 获取当前登录账号的用户名进行比较
        # 如果能获取到当前账号信息，可以通过用户名过滤
        # 这里可以添加更多判断逻辑，比如检查是否是自己的UID
        return False  # 暂时返回False，可以根据实际情况调整
    
    def _contains_version_question(self, danmu_history: str) -> bool:
        """检查弹幕历史中是否包含游戏版本相关问题"""
        version_keywords = ['版本', '什么版本', '哪个版本', '游戏版本', '宝可梦版本', '口袋妖怪版本']
        return any(keyword in danmu_history for keyword in version_keywords)

    async def disconnect(self):
        """断开连接"""
        self.is_connected = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None


# 使用示例
async def main():
    """使用示例"""
    bot = BilibiliDanmuBot()
    
    # 从配置文件初始化设置
    bot.init_from_config()
    
    # 1. 生成二维码登录
    qr_data = bot.generate_qr_login()
    if qr_data:
        print("请扫描二维码登录（二维码已生成）")
        
        # 等待登录
        if bot.wait_for_login():
            print("登录成功！")
        else:
            print("登录失败或超时")
            return
    
    # 2. 设置回调函数（可选，用于自定义处理）
    def on_danmu(data):
        print(f"收到弹幕: {data['username']} 说: {data['content']}")
        # AI会自动分析弹幕并生成回复，无需手动处理
    
    def on_gift(data):
        print(f"收到礼物: {data['username']} 送出 {data['gift_name']} x{data['num']}")
        # 可以选择对礼物进行感谢回复
        # bot.send_danmu(f"感谢 {data['username']} 的 {data['gift_name']}！")
    
    def on_enter(data):
        print(f"用户进入: {data['username']} 进入直播间")
    
    bot.set_danmu_callback(on_danmu)
    bot.set_gift_callback(on_gift)
    bot.set_enter_callback(on_enter)
    
    # 3. 获取房间号（从配置文件或用户输入）
    room_id = bot.get_room_id_from_config()
    if not room_id:
        room_id = input("请输入房间号: ")
    else:
        print(f"从配置文件读取到房间号: {room_id}")
    
    # 4. 连接直播间
    if bot.is_ai_mode_enabled():
        print(f"正在连接房间 {room_id}，AI自动回复模式已开启...")
        print(f"AI将每{bot.ai_response_interval}秒分析最近的弹幕并自动发送回复")
    else:
        print(f"正在连接房间 {room_id}，AI模式未开启")
    
    await bot.connect_websocket(room_id)


if __name__ == "__main__":
    # 安装依赖: pip install websockets requests qrcode[pil] pillow
    print("=== Bilibili弹幕机器人 - AI自动回复版 ===")
    print("功能说明：")
    print("1. 扫码登录哔哩哔哩账号")
    print("2. 从 config.json 读取房间号和AI配置")
    print("3. AI自动分析最近的弹幕")
    print("4. 模仿观众风格发送自然回复")
    print("5. 使用通义千问AI，让回复更自然")
    print("6. 请确保 config.json 文件存在并配置正确")
    print("="*50)
    
    asyncio.run(main())
