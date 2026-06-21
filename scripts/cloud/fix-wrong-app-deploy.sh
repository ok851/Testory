#!/usr/bin/env bash
# 修复误将 website/ 兼容壳部署到 /opt/testory-website 导致的 502
# 在服务器上以 root 运行: bash scripts/cloud/fix-wrong-app-deploy.sh
#
# 前提: monorepo 已 clone 到 /opt/testory-repo（或 /opt 即为仓库根且含 projects/）

set -euo pipefail

REPO=""
for candidate in /opt/testory-repo /opt; do
  if [[ -f "$candidate/projects/testory-website/app.py" ]]; then
    REPO="$candidate"
    break
  fi
done

if [[ -z "$REPO" ]]; then
  echo "ERROR: 未找到 projects/testory-website/app.py"
  echo "请先 git clone  monorepo 到 /opt/testory-repo，或从本机 export 后 scp 完整 projects/testory-website"
  exit 1
fi

echo "使用 monorepo: $REPO"

fix_one() {
  local name="$1"
  local src="$REPO/projects/$name"
  local dest="/opt/$name"

  echo ""
  echo "=== 修复 $dest ==="
  mkdir -p "$dest/data"
  [[ -f "$dest/.env" ]] && cp -a "$dest/.env" "/tmp/${name}.env.bak"

  rsync -a --delete \
    --exclude '.venv' \
    --exclude 'data/' \
    --exclude '.env' \
    "$src/" "$dest/"

  rsync -a "$REPO/packages/testory_common/" "$dest/testory_common/"

  if [[ -f "/tmp/${name}.env.bak" ]]; then
    cp -a "/tmp/${name}.env.bak" "$dest/.env"
  elif [[ ! -f "$dest/.env" && -f "$dest/.env.example" ]]; then
    cp "$dest/.env.example" "$dest/.env"
    echo "  已从 .env.example 创建 .env，请 nano $dest/.env"
  fi

  if [[ ! -x "$dest/.venv/bin/waitress-serve" ]]; then
    echo "  创建 venv ..."
    python3 -m venv "$dest/.venv"
    "$dest/.venv/bin/pip" install -U pip -q
    "$dest/.venv/bin/pip" install -r "$dest/requirements.txt" waitress -q
  fi

  echo "  app.py 前几行:"
  head -3 "$dest/app.py"
}

fix_one "testory-website"
fix_one "testory-platform-admin"

mkdir -p /opt/testory-platform-admin/data/release_files

cat > /etc/systemd/system/testory-website.service << 'EOF'
[Unit]
Description=Testory Website
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/testory-website
EnvironmentFile=/opt/testory-website/.env
ExecStart=/opt/testory-website/.venv/bin/waitress-serve --listen=127.0.0.1:5200 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/testory-admin.service << 'EOF'
[Unit]
Description=Testory Platform Admin
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/testory-platform-admin
EnvironmentFile=/opt/testory-platform-admin/.env
ExecStart=/opt/testory-platform-admin/.venv/bin/waitress-serve --listen=127.0.0.1:5100 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart testory-website testory-admin
sleep 2

echo ""
echo "=== 自检 ==="
curl -s -o /dev/null -w "5200: %{http_code}\n" http://127.0.0.1:5200/ || true
curl -s -o /dev/null -w "5100: %{http_code}\n" http://127.0.0.1:5100/login || true
systemctl is-active testory-website testory-admin || true
