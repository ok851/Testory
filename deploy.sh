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

# 检查依赖
check_dependencies() {
    echo_info "检查依赖..."
    
    if ! command -v docker &> /dev/null; then
        echo_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        echo_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi
    
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
    SECRET_KEY=$(openssl rand -hex 32)
    
    # 创建环境变量文件
    cat > .env << EOF
# UAT Platform 环境配置
SECRET_KEY=${SECRET_KEY}
DB_PASSWORD=$(openssl rand -base64 32)
FLASK_ENV=production
EOF
    
    echo_info "配置文件生成完成"
}

# 构建镜像
build_image() {
    echo_info "构建 Docker 镜像..."
    
    docker-compose build
    
    echo_info "镜像构建完成"
}

# 启动服务
start_services() {
    echo_info "启动服务..."
    
    docker-compose up -d
    
    echo_info "服务启动完成"
    echo_info "等待服务初始化..."
    sleep 5
    
    # 检查健康状态
    if curl -f http://localhost:5000/api/health &> /dev/null; then
        echo_info "服务运行正常"
    else
        echo_warn "服务可能还在启动中，请稍后检查"
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
    echo "默认账号:"
    echo "  - 用户名: admin"
    echo "  - 密码: admin123"
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
    
    check_dependencies
    create_directories
    generate_config
    build_image
    start_services
    show_info
}

# 处理命令行参数
case "${1:-}" in
    "stop")
        echo_info "停止服务..."
        docker-compose down
        ;;
    "restart")
        echo_info "重启服务..."
        docker-compose restart
        ;;
    "logs")
        docker-compose logs -f
        ;;
    "update")
        echo_info "更新服务..."
        docker-compose pull
        docker-compose up -d
        ;;
    *)
        main
        ;;
esac
