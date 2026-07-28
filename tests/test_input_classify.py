# -*- coding: utf-8 -*-
"""上传内容分类与多平台入口语义。"""

from ai_modules.generate.input_classify import (
    classify_design_input,
    classify_upload_filename,
    entry_field_meta,
    looks_like_frontend_source,
    normalize_platform,
)


def test_normalize_platform_aliases():
    assert normalize_platform("mobile") == "android"
    assert normalize_platform("windows") == "desktop"
    assert normalize_platform("system") == "os"
    assert normalize_platform("web") == "web"


def test_classify_by_extension():
    assert classify_upload_filename("Login.tsx") == "frontend_source"
    assert classify_upload_filename("Order.vue") == "frontend_source"
    assert classify_upload_filename("req.pdf") == "requirements_doc"


def test_classify_pasted_source_in_txt():
    code = '''
export function Login() {
  return <button data-testid="login-submit" onClick={onLogin}>登录</button>;
}
'''
    assert looks_like_frontend_source(code)
    assert classify_design_input(filename="note.txt", text=code) == "frontend_source"
    assert classify_design_input(filename="req.md", text="测试登录成功与失败") == "requirements_doc"


def test_entry_field_not_url_for_desktop():
    m = entry_field_meta("desktop")
    assert "URL" not in m["label"]
    assert m["key"] == "app_entry"
    assert entry_field_meta("web")["key"] == "base_url"
