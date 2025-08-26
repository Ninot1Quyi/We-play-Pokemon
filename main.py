import pyautogui
import win32gui
import win32con
import time

# 设置 pyautogui 的暂停时间，防止操作过快
pyautogui.PAUSE = 0.1

def activate_mgba_window(search_text="mGBA - POKEMON"):
    """查找并激活标题中包含指定文本的 mGBA 窗口"""
    def enum_windows(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if search_text in title:
                results.append(hwnd)
    
    hwnd_list = []
    win32gui.EnumWindows(enum_windows, hwnd_list)
    
    if hwnd_list:
        hwnd = hwnd_list[0]  # 取第一个匹配的窗口
        win32gui.SetForegroundWindow(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # 确保窗口未最小化
        print(f"Activated window: {win32gui.GetWindowText(hwnd)}")
    else:
        raise Exception(f"No window found with '{search_text}' in title!")
    time.sleep(0.5)  # 等待窗口激活

def press_key(key, duration=0.1):
    """模拟按下并释放一个按键"""
    pyautogui.keyDown(key)
    time.sleep(duration)
    pyautogui.keyUp(key)

def main():
    try:
        # 激活 mGBA 窗口
        activate_mgba_window()

        # 示例：模拟按下方向键“右”并保持 0.2 秒
        print("Moving right...")
        press_key("right", 0.2)

        # 模拟按下 A 键（Z 键）
        print("Pressing A button...")
        press_key("z", 0.1)

        # 模拟按下 B 键（X 键）
        print("Pressing B button...")
        press_key("x", 0.1)

        # 模拟按下 Start 键（Enter）
        print("Pressing Start button...")
        press_key("enter", 0.1)

        # 模拟按下 Select 键（Backspace）
        print("Pressing Select button...")
        press_key("backspace", 0.1)

        # 模拟连续按键（例如快速连按 A 键）
        print("Rapidly pressing A button...")
        for _ in range(5):
            press_key("z", 0.05)
            time.sleep(0.05)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()