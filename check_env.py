import os

# 检查系统环境变量
print("系统环境变量:")
path_env = os.environ.get('PATH', '')
print("PATH 环境变量:")
print(path_env)

# 检查是否包含Tesseract OCR路径
if "Tesseract-OCR" in path_env:
    print("\n✓ PATH 环境变量中包含 Tesseract-OCR 路径")
else:
    print("\n✗ PATH 环境变量中不包含 Tesseract-OCR 路径")

# 检查是否有其他与Tesseract相关的环境变量
tesseract_vars = [k for k, v in os.environ.items() if "TESS" in k.upper() or "OCR" in k.upper()]
if tesseract_vars:
    print("\n与 Tesseract/OCR 相关的环境变量:")
    for var in tesseract_vars:
        print(f"  - {var}: {os.environ[var]}")
else:
    print("\n没有找到与 Tesseract/OCR 相关的环境变量")