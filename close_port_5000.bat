@echo off
setlocal enabledelayedexpansion

set process_name=bilibili_mgba_controller.py
echo ���ڲ��Ҳ��رս��̣�%process_name%...

set found=0
:: ���Ұ���ָ�����Ƶ�Python����
for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq python.exe" /fo csv /nh ^| findstr /i "%process_name%"') do (
    set pid=%%~a
    echo �ҵ�����ID��!pid!��������ֹ...
    taskkill /f /pid !pid! >nul 2>&1
    if !errorlevel! equ 0 (
        echo �ɹ���ֹ����ID��!pid!
        set found=1
    ) else (
        echo ��ֹ����ID��!pid! ʧ��
    )
)

:: ����Ƿ���Pythonw���̣��޿���̨���ڵ�Python���̣�
for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq pythonw.exe" /fo csv /nh ^| findstr /i "%process_name%"') do (
    set pid=%%~a
    echo �ҵ�����ID��!pid!��������ֹ...
    taskkill /f /pid !pid! >nul 2>&1
    if !errorlevel! equ 0 (
        echo �ɹ���ֹ����ID��!pid!
        set found=1
    ) else (
        echo ��ֹ����ID��!pid! ʧ��
    )
)

if !found! equ 0 (
    echo δ�ҵ����ư��� %process_name% �Ľ���
)

echo �������
pause
