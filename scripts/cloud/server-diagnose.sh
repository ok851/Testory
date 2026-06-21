#!/usr/bin/env bash
# 在服务器上运行：bash scripts/cloud/server-diagnose.sh
# 502 Bad Gateway = Nginx 正常，但 127.0.0.1:5200 / 5100 无响应

set -euo pipefail

echo "========== 1. Nginx =========="
sudo nginx -t 2>&1 || true
systemctl is-active nginx 2>/dev/null || true

echo ""
echo "========== 2. systemd 服务状态 =========="
for svc in testory-website testory-admin; do
  echo "--- $svc ---"
  systemctl is-active "$svc" 2>/dev/null || echo "inactive/missing"
  systemctl status "$svc" --no-pager -l 2>/dev/null | tail -15 || true
done

echo ""
echo "========== 3. 端口监听 (应见 127.0.0.1:5200 与 :5100) =========="
ss -tlnp 2>/dev/null | grep -E ':5200|:5100' || netstat -tlnp 2>/dev/null | grep -E ':5200|:5100' || echo "(无监听 — 这就是 502 的原因)"

echo ""
echo "========== 4. 本机回环探测 =========="
curl -s -o /dev/null -w "127.0.0.1:5200 -> HTTP %{http_code}\n" http://127.0.0.1:5200/ 2>/dev/null || echo "127.0.0.1:5200 连接失败"
curl -s -o /dev/null -w "127.0.0.1:5100/login -> HTTP %{http_code}\n" http://127.0.0.1:5100/login 2>/dev/null || echo "127.0.0.1:5100 连接失败"

echo ""
echo "========== 5. 常见部署路径是否存在 =========="
for d in \
  /opt/testory-website \
  /opt/testory-platform-admin \
  /opt/testory-repo/projects/testory-website \
  /opt/testory-repo/projects/testory-platform-admin
do
  if [[ -d "$d" ]]; then
    echo "OK  $d"
    [[ -f "$d/app.py" ]] && echo "    app.py yes" || echo "    app.py MISSING"
    [[ -x "$d/.venv/bin/waitress-serve" ]] && echo "    .venv yes" || echo "    .venv MISSING"
    [[ -f "$d/.env" ]] && echo "    .env yes" || echo "    .env MISSING"
  fi
done

echo ""
echo "========== 6. 最近错误日志 (各 20 行) =========="
for svc in testory-website testory-admin; do
  echo "--- journalctl $svc ---"
  journalctl -u "$svc" -n 20 --no-pager 2>/dev/null || true
done

echo ""
echo "========== 建议 =========="
echo "若端口无监听：sudo systemctl restart testory-website testory-admin"
echo "若 WorkingDirectory 与代码路径不一致：修改 /etc/systemd/system/testory-*.service 后 daemon-reload + restart"
echo "若 .venv 缺失：见 docs/DEPLOY_CLOUD.md 第 3 节重建 venv"
