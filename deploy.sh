#!/bin/bash
# UAT Platform 私有化部署脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印信息
echo_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

echo_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

COMPOSE_CMD=()

# 兼容 docker-compose 与 docker compose（某些服务器只有 docker compose 插件）
init_compose_cmd() {
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD=(docker-compose)
    elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
        COMPOSE_CMD=(docker compose)
    else
        echo_error "Docker Compose 未安装，请先安装 docker-compose 或 Docker Compose 插件"
        exit 1
    fi
}

# 检查依赖
check_dependencies() {
    echo_info "检查依赖..."
    
    if ! command -v docker &> /dev/null; then
        echo_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    init_compose_cmd
    
    echo_info "依赖检查通过"
}

# 创建目录结构
create_directories() {
    echo_info "创建目录结构..."
    
    mkdir -p data
    mkdir -p logs
    mkdir -p screenshots
    mkdir -p videos
    mkdir -p exports
    mkdir -p ssl
    
    echo_info "目录创建完成"
}

# 生成配置文件
generate_config() {
    echo_info "生成配置文件..."
    
    # 生成随机密钥
    if command -v openssl &> /dev/null; then
        SECRET_KEY=$(openssl rand -hex 32)
        DB_PASSWORD=$(openssl rand -base64 32)
    else
        # 部分精简系统可能没有 openssl，这里用 python 兜底生成
        SECRET_KEY=$(python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
        DB_PASSWORD=$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)
    fi
    
    # 创建环境变量文件
    cat > .env << EOF
# UAT Platform 环境配置
SECRET_KEY=${SECRET_KEY}
DB_PASSWORD=${DB_PASSWORD}
FLASK_ENV=production
EOF
    
    echo_info "配置文件生成完成"
}

# 构建镜像
build_image() {
    echo_info "构建 Docker 镜像..."
    
    "${COMPOSE_CMD[@]}" build
    
    echo_info "镜像构建完成"
}

# 启动服务
start_services() {
    echo_info "启动服务..."
    
    "${COMPOSE_CMD[@]}" up -d
    
    echo_info "服务启动完成"
    echo_info "等待服务初始化..."
    sleep 5
    
    # 检查健康状态
    if command -v curl &> /dev/null; then
        if curl -f http://localhost:5000/api/health/ready &> /dev/null; then
            echo_info "服务运行正常"
        else
            echo_warn "服务可能还在启动中，请稍后检查"
        fi
    elif command -v wget &> /dev/null; then
        if wget -qO- http://localhost:5000/api/health/ready &> /dev/null; then
            echo_info "服务运行正常"
        else
            echo_warn "服务可能还在启动中，请稍后检查"
        fi
    else
        echo_warn "宿主机没有 curl/wget，跳过健康检查"
    fi
}

# 显示信息
show_info() {
    echo ""
    echo "========================================"
    echo "  UAT Platform 部署完成"
    echo "========================================"
    echo ""
    echo "访问地址:"
    echo "  - 本地访问: http://localhost:5000"
    echo ""
    echo "管理员账号:"
    echo "  - 用户名: admin"
    echo "  - 初始密码: 请在 .env 中设置 ADMIN_INITIAL_PASSWORD（≥8 位），"
    echo "    或首次启动后查看容器日志中的随机密码；"
    echo "    仅内网调试可设 ALLOW_INSECURE_DEFAULT_ADMIN=true 使用 admin/admin123"
    echo ""
    echo "数据目录:"
    echo "  - 数据库: ./data/"
    echo "  - 日志: ./logs/"
    echo "  - 截图: ./screenshots/"
    echo "  - 视频: ./videos/"
    echo ""
    echo "常用命令:"
    echo "  - 查看日志: docker-compose logs -f"
    echo "  - 停止服务: docker-compose down"
    echo "  - 重启服务: docker-compose restart"
    echo ""
    echo "========================================"
}

# 主函数
main() {
    echo "========================================"
    echo "  UAT Platform 私有化部署脚本"
    echo "========================================"
    echo ""
    
    create_directories
    generate_config
    build_image
    start_services
    show_info
}

# 处理命令行参数
check_dependencies
case "${1:-}" in
    "stop")
        echo_info "停止服务..."
        "${COMPOSE_CMD[@]}" down
        ;;
    "restart")
        echo_info "重启服务..."
        "${COMPOSE_CMD[@]}" restart
        ;;
    "logs")
        "${COMPOSE_CMD[@]}" logs -f
        ;;
    "update")
        echo_info "更新服务..."
        "${COMPOSE_CMD[@]}" pull
        "${COMPOSE_CMD[@]}" up -d
        ;;
    *)
        main
        ;;
esac
