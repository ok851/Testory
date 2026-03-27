import requests

# 先登录获取 session
login_data = {
    'username': 'admin',
    'password': 'admin123'
}

session = requests.Session()

# 尝试登录
try:
    r = session.post('http://127.0.0.1:5000/api/auth/login', json=login_data)
    print(f"Login status: {r.status_code}")
    print(f"Login response: {r.text[:500]}")
    
    if r.status_code == 200:
        data = r.json()
        print(f"\nLogin success: {data.get('success')}")
        print(f"User role: {data.get('user', {}).get('role')}")
except Exception as e:
    print(f"Login error: {e}")

# 获取通知配置
try:
    r = session.get('http://127.0.0.1:5000/api/notifications/configs')
    print(f"\nConfigs status: {r.status_code}")
    print(f"Configs response: {r.text[:500]}")
except Exception as e:
    print(f"Configs error: {e}")
