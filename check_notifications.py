import sqlite3
import json

def check_notifications():
    conn = sqlite3.connect('test_cases.db')
    cursor = conn.cursor()
    
    # 检查表是否存在
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notification_configs'")
    if not cursor.fetchone():
        print("通知配置表不存在")
        conn.close()
        return
    
    # 查询所有通知配置
    cursor.execute("SELECT * FROM notification_configs ORDER BY created_at DESC")
    rows = cursor.fetchall()
    
    print(f"找到 {len(rows)} 条通知配置:")
    print("-" * 50)
    
    for row in rows:
        print(f"ID: {row[0]}")
        print(f"名称: {row[1]}")
        print(f"类型: {row[2]}")
        print(f"配置: {row[3]}")
        print(f"事件: {row[4]}")
        print(f"是否激活: {row[5]}")
        print(f"创建时间: {row[6]}")
        print("-" * 50)
    
    conn.close()

if __name__ == '__main__':
    check_notifications()
