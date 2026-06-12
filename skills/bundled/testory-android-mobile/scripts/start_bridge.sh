#!/usr/bin/env bash
# Start Testory Android bridge daemon (Appium session pre-warm)
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON="$SCRIPT_DIR/bridge_daemon.py"

echo "[*] Starting Testory Appium bridge..."

pkill -f bridge_daemon 2>/dev/null || true
rm -f /tmp/bridge_cmd /tmp/bridge_resp /tmp/bridge.lock
sleep 1

if ! adb devices | grep -q "device$"; then
    echo "[!] No ADB device connected."
    exit 1
fi
echo "  [OK] ADB device found"

if ! curl -s http://127.0.0.1:4723/status 2>/dev/null | grep -q '"ready":true'; then
    echo "[*] Starting Appium..."
    if [ -z "$ANDROID_HOME" ]; then
        for d in "$HOME/android-sdk" "$HOME/Library/Android/sdk" "/usr/lib/android-sdk"; do
            if [ -d "$d" ]; then export ANDROID_HOME="$d"; break; fi
        done
    fi
    export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
    nohup appium --allow-insecure all --relaxed-security --log /tmp/appium.log > /dev/null 2>&1 &
    sleep 4
fi

rm -f /tmp/bridge_cmd /tmp/bridge_resp
python3 "$DAEMON" --daemon &
sleep 6

RESP=$(python3 "$DAEMON" dump 2>/dev/null || true)
if echo "$RESP" | grep -q '"ok":true'; then
    echo "  [OK] Bridge daemon ready"
else
    echo "  [!] Daemon test failed: $RESP"
    exit 1
fi

echo "[OK] Bridge ready at $DAEMON"
