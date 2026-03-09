import sqlite3

# 连接到数据库
conn = sqlite3.connect('test_cases.db')
cursor = conn.cursor()

# 查看test_steps表中的数据
cursor.execute("SELECT id, action, input_value FROM test_steps")
rows = cursor.fetchall()

print("test_steps表中的数据:")
for row in rows:
    print(f"ID: {row[0]}, 操作: {row[1]}, 输入值: '{row[2]}', 类型: {type(row[2])}")

# 关闭连接
conn.close()
