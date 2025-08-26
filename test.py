import asyncio
import websockets
import json
import time
import struct
import zlib

async def get_danmaku(room_id):
    """
    获取指定B站直播间弹幕

    Args:
        room_id: 直播间房间号
    """
    # B站弹幕服务器地址
    uri = "wss://broadcastlv.chat.bilibili.com:2245/sub"
    
    async with websockets.connect(uri) as websocket:
        # 构造认证包
        auth_message = {
            "uid": 0,  # 非必须，游客可以为0
            "roomid": room_id,
            "protover": 3,  # 使用协议版本3（支持zlib压缩）
            "platform": "web",
            "type": 2,
            "key": ""  # 非必须，游客可为空
        }
        # 封装认证包
        auth_packet = pack_packet(auth_message, 7)  # 操作码7表示认证
        await websocket.send(auth_packet)
        print("已发送认证信息")

        # 发送心跳包以保持连接
        async def send_heartbeat():
            while True:
                try:
                    heartbeat_packet = pack_packet({}, 2)  # 操作码2表示心跳
                    await websocket.send(heartbeat_packet)
                    await asyncio.sleep(30)  # 每30秒发送一次心跳
                except Exception as e:
                    print(f"心跳包发送失败: {e}")
                    break

        # 启动心跳包任务
        asyncio.create_task(send_heartbeat())

        # 接收弹幕数据
        while True:
            try:
                message = await websocket.recv()
                # 解析数据包
                for data in parse_packet(message):
                    if isinstance(data, dict):
                        if data.get("cmd") == "DANMU_MSG":
                            danmaku_content = data["info"][1]
                            username = data["info"][2][1]
                            print(f"[{username}]: {danmaku_content}")
                        elif data.get("cmd") == "SEND_GIFT":
                            print(f"收到礼物: {data}")
                        elif data.get("cmd") == "INTERACT_WORD":
                            print(f"用户进入: {data['data']['uname']}")
                        else:
                            print(f"其他消息: {data.get('cmd')}")

            except websockets.exceptions.ConnectionClosed as e:
                print(f"连接已关闭: {e}")
                break
            except Exception as e:
                print(f"发生错误: {e}")

def pack_packet(data, operation):
    """
    打包数据包，符合B站弹幕协议
    """
    body = json.dumps(data).encode('utf-8') if data else b''
    packet_len = len(body) + 16
    header = struct.pack('>IHHII', packet_len, 16, 1, operation, 1)
    return header + body

def parse_packet(data):
    """
    解析B站弹幕数据包
    """
    offset = 0
    packets = []
    
    while offset < len(data):
        try:
            # 解析头部
            header = struct.unpack('>IHHII', data[offset:offset+16])
            packet_len, header_len, ver, op, seq = header
            body = data[offset+16:offset+packet_len]
            
            if ver == 2:  # zlib压缩
                decompressed = zlib.decompress(body)
                packets.extend(parse_packet(decompressed))
            elif ver == 1 or ver == 0:  # 普通JSON
                if body:
                    try:
                        packets.append(json.loads(body.decode('utf-8')))
                    except json.JSONDecodeError:
                        pass
            
            offset += packet_len
        except Exception as e:
            print(f"解析数据包错误: {e}")
            break
    
    return packets

async def main():
    room_id = 27063247  # 替换为你想获取弹幕的直播间房间号
    await get_danmaku(room_id)

if __name__ == "__main__":
    asyncio.run(main())