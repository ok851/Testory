import subprocess
import os

# 检查Tesseract OCR是否可用
try:
    result = subprocess.run(['tesseract', '--version'], capture_output=True, text=True)
    print("Tesseract OCR版本:")
    print(result.stdout)
    if result.stderr:
        print("错误:")
        print(result.stderr)
except Exception as e:
    print(f"执行tesseract命令时出错: {e}")

# 检查Tesseract OCR的安装路径
try:
    # 尝试获取tesseract的安装路径
    result = subprocess.run(['where', 'tesseract'], capture_output=True, text=True)
    print("\nTesseract OCR安装路径:")
    print(result.stdout)
    if result.stderr:
        print("错误:")
        print(result.stderr)
except Exception as e:
    print(f"执行where命令时出错: {e}")

# 检查可能的Tesseract OCR安装目录
possible_paths = [
    "C:\\Program Files\\Tesseract-OCR",
    "C:\\Program Files (x86)\\Tesseract-OCR",
    "D:\\Program Files\\Tesseract-OCR"
]

print("\n检查可能的Tesseract OCR安装目录:")
for path in possible_paths:
    if os.path.exists(path):
        print(f"找到目录: {path}")
        files = os.listdir(path)
        print(f"目录中的文件: {files[:10]}...")  # 只显示前10个文件
    else:
        print(f"目录不存在: {path}")