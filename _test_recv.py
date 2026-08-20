import subprocess, time, socket, threading
adb = r"C:\Users\zxcyb\AppData\Local\Testory\extensions\android\sdk\platform-tools\adb.exe"
serial = "3B163L00CF800000"

# Server should still be running from previous test - check
r = subprocess.run([adb, "-s", serial, "shell", "pidof app_process"], capture_output=True, text=True, timeout=5)
print("Server PIDs:", r.stdout.strip())

# Connect and read byte by byte
port = 27670
print("Connecting...")
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.settimeout(30)
    print("Connected, reading...")
    
    total_read = b""
    deadline = time.time() + 20
    while time.time() < deadline and len(total_read) < 100:
        try:
            chunk = sock.recv(64)
            if chunk:
                total_read += chunk
                print(f"Read {len(chunk)} bytes (total {len(total_read)}): {chunk[:20].hex()}")
                if len(total_read) >= 65:
                    break
            else:
                print("Connection closed by server")
                break
        except socket.timeout:
            print(f"Timeout after reading {len(total_read)} bytes")
            break
    
    print(f"Total read: {len(total_read)} bytes")
    if len(total_read) >= 1:
        dummy = total_read[0:1]
        print(f"Dummy byte: {dummy.hex()}")
    if len(total_read) >= 65:
        name = total_read[1:65].split(b"\x00")[0]
        print(f"Device name: {name.decode('utf-8', errors='replace')}")
    elif len(total_read) >= 64:
        name = total_read[0:64].split(b"\x00")[0]
        print(f"Device name (no dummy): {name.decode('utf-8', errors='replace')}")
    
    sock.close()
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
