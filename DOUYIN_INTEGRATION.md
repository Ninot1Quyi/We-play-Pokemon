# 抖音弹幕集成使用说明

## 概述

现在 bilibili_mgba_controller.py 已经支持同时监听哔哩哔哩和抖音的弹幕，实现双平台弹幕控制游戏功能。

## 功能特性

- ✅ 同时监听哔哩哔哩和抖音直播间弹幕
- ✅ 统一的弹幕指令处理系统
- ✅ 支持自由模式和秩序模式
- ✅ 抖音弹幕用户名会带有 `[抖音]` 标识
- ✅ 所有弹幕都会保存到CSV文件中

## 配置说明

### 1. 安装依赖

首先需要安装 websockets 依赖包：

```bash
pip install websockets~=12.0
```

或者使用 requirements.txt：

```bash
pip install -r requirements.txt
```

### 2. 配置文件设置

在 `config.json` 中添加抖音配置：

```json
{
    "room_id": 你的哔哩哔哩房间号,
    "sessdata": "你的哔哩哔哩SESSDATA",
    "openlive": {
        "access_key_id": "你的access_key_id",
        "access_key_secret": "你的access_key_secret", 
        "app_id": 你的app_id,
        "room_owner_auth_code": "你的room_owner_auth_code"
    },
    "danmaku_mode": "openlive",
    "douyin": {
        "enabled": true,
        "websocket_port": 8765
    },
    "ORDER_INTERVAL": 20
}
```

**配置说明：**
- `douyin.enabled`: 是否启用抖音弹幕监听（true/false）
- `douyin.websocket_port`: WebSocket服务器端口，默认8765

## 使用步骤

### 1. 启动游戏控制程序

```bash
python bilibili_mgba_controller.py
```

启动后会看到类似日志：
```
[INFO] Douyin Enabled: True
[INFO] Douyin WebSocket Port: 8765
[INFO] Douyin WebSocket server started on ws://localhost:8765
[INFO] You can now connect dycast-main to this WebSocket server to forward Douyin danmaku
```

### 2. 启动 dycast-main

1. 进入 dycast-main 目录
2. 启动前端应用：
   ```bash
   npm run dev
   ```
3. 在浏览器中打开应用
4. 输入抖音直播间房间号并连接
5. 在转发地址框中填入：`ws://localhost:8765`
6. 点击"转发"按钮建立连接

### 3. 验证连接

成功连接后，你会在游戏控制程序的日志中看到：
```
[INFO] Douyin WebSocket client connected. Total clients: 1
```

当有抖音弹幕时，会显示：
```
[INFO] [抖音] 用户名: 弹幕内容
```

## 弹幕指令格式

两个平台的弹幕指令格式完全相同：

### 基础指令
- `上` / `i` / `up` - 上方向键
- `下` / `k` / `down` - 下方向键  
- `左` / `j` / `left` - 左方向键
- `右` / `l` / `right` - 右方向键
- `a` - A键
- `b` - B键
- `开始` / `start` - Start键
- `选择` / `select` - Select键

### 组合指令
- `a3+b2+i1` - A键按3次，B键按2次，上键按1次
- `i j k l` - 上左下右各按一次

### 奔跑指令
- `r i3 j2` - 奔跑模式：按住B键的同时，上键3次，左键2次
- `run i+j+k` - 奔跑模式：按住B键的同时，上左下各一次

### 模式投票
- `自由` / `自由模式` / `anarchy` / `freedom` - 投票自由模式
- `秩序` / `秩序模式` / `democracy` / `order` - 投票秩序模式

## 工作原理

1. **哔哩哔哩弹幕**：通过 blivedm 库直接连接哔哩哔哩直播间获取弹幕
2. **抖音弹幕**：通过 WebSocket 服务器接收 dycast-main 转发的弹幕数据
3. **统一处理**：两个平台的弹幕都通过 `process_danmaku_command()` 函数统一处理
4. **平台标识**：抖音弹幕用户名会自动添加 `[抖音]` 前缀以区分平台

## 数据格式

### dycast-main 发送的数据格式

```json
{
    "id": "消息ID",
    "method": "WebcastChatMessage",
    "user": {
        "name": "用户名",
        "id": "用户ID",
        "avatar": "头像URL"
    },
    "content": "弹幕内容",
    "rtfContent": [
        {
            "type": 1,
            "text": "文本内容"
        }
    ]
}
```

## 故障排除

### 1. WebSocket连接失败
- 检查端口是否被占用
- 确认防火墙设置
- 检查配置文件中的端口号

### 2. 抖音弹幕不显示
- 确认 dycast-main 已成功连接抖音直播间
- 检查转发地址是否正确：`ws://localhost:8765`
- 查看控制台日志确认连接状态

### 3. 弹幕指令不执行
- 确认指令格式正确
- 检查mGBA窗口是否处于前台
- 查看日志确认指令是否被识别

## 日志说明

- `[抖音] 用户名: 内容` - 抖音弹幕
- `[房间号] 用户名: 内容` - 哔哩哔哩弹幕
- `Douyin WebSocket client connected` - 抖音客户端连接
- `Douyin WebSocket client disconnected` - 抖音客户端断开

## 注意事项

1. 确保 mGBA 模拟器窗口标题包含 "mGBA - POKEMON"
2. 两个平台的弹幕会统一参与模式投票和指令执行
3. 所有弹幕都会保存到 `danmaku/` 目录下的CSV文件中
4. 抖音弹幕的处理延迟可能略高于哔哩哔哩弹幕

## 技术细节

- WebSocket服务器使用 `websockets` 库实现
- 支持多个客户端同时连接（虽然通常只需要一个）
- 异步处理，不会阻塞主程序运行
- 自动重连机制（由 dycast-main 处理）
