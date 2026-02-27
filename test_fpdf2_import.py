#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试fpdf2库的导入
"""

try:
    import fpdf2
    print("fpdf2库导入成功")
    print(f"fpdf2版本: {fpdf2.__version__}")
except Exception as e:
    print(f"fpdf2库导入失败: {str(e)}")

try:
    from fpdf2 import FPDF
    print("从fpdf2导入FPDF成功")
except Exception as e:
    print(f"从fpdf2导入FPDF失败: {str(e)}")