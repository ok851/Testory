import os

# 检查Tesseract OCR目录中的DLL文件
tesseract_path = "C:\\Program Files\\Tesseract-OCR"

print(f"检查 {tesseract_path} 目录中的DLL文件:")
if os.path.exists(tesseract_path):
    files = os.listdir(tesseract_path)
    dll_files = [f for f in files if f.endswith('.dll')]
    print("DLL文件列表:")
    for dll in dll_files:
        print(f"  - {dll}")
    
    # 检查是否存在libtesseract-5.dll文件
    if "libtesseract-5.dll" in dll_files:
        print("\n✓ 找到 libtesseract-5.dll 文件")
    else:
        print("\n✗ 未找到 libtesseract-5.dll 文件")
else:
    print("目录不存在")