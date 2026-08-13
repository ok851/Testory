with open('playwright_automation.py', 'r', encoding='utf-8') as f:
    c = f.read()

checks = [
    ('_timing_ok sentinel', '_timing_ok = False' in c),
    ('_timing_ok set True', '_timing_ok = True' in c),
    ('selector_resolve_ms after wait', c.find('_selector_resolve_ms') > c.find('wait_for(state="attached"'),
    ('sentinel in except', '_timing_ok' in c.split('except Exception as e:')[1] if 'except Exception as e:' in c else False),
    ('no dir() check', "'_extract_t0' in dir()" not in c),
]
for name, ok in checks:
    print(f"  pw: {name}: {'OK' if ok else 'FAIL'}")

with open('app.py', 'r', encoding='utf-8') as f:
    c2 = f.read()

checks2 = [
    ('_last_step_detail reset per step', 'automation._last_step_detail = {}  # 重置上一步残留数据' in c2),
    ('screenshot relative path', "_os.path.join('screenshots', _ss_name)" in c2),
    ('screenshot guard web only', 'automation.page is not None' in c2 and 'should_capture_before' in c2),
    ('no page_title URL', 'page_title = getattr(automation.page' not in c2),
]
for name, ok in checks2:
    print(f"  app: {name}: {'OK' if ok else 'FAIL'}")

with open('templates/run_history.html', 'r', encoding='utf-8') as f:
    c3 = f.read()

checks3 = [
    ('bar scale overflow', 'var scale = sumMs > total' in c3),
]
for name, ok in checks3:
    print(f"  html: {name}: {'OK' if ok else 'FAIL'}")
