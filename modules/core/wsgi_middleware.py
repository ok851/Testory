"""WSGI 部署中间件：访问日志 + hop-by-hop 响应头剥除。

背景（2026-08-28 排查「agent 一收到命令就执行失败: HTTP 500」）：
桌面端常驻 Flask 弃用 werkzeug 开发服务器后改跑 wsgiref / waitress。
二者严格执行 WSGI 规范——**禁止应用下发 hop-by-hop 响应头**
（Connection / Keep-Alive / Transfer-Encoding / Upgrade / TE / Trailers / Proxy-* 等），
连接语义由服务器自己管理。而 app.py 中多个 SSE 路由（如 /api/ai/task/execute）
显式设置了 `'Connection': 'keep-alive'`：werkzeug 静默容忍，wsgiref 则在
start_response 阶段直接断言崩溃（backend_startup.log 中为
`AssertionError: Hop-by-hop header, 'Connection: keep-alive', not allowed`），
前端表现为「执行失败: HTTP 500」，且发生在调用大模型之前——与模型额度无关。

本中间件在服务器层统一剥除这些头，一次修复所有路由，waitress / wsgiref 分支通用。
"""
from __future__ import annotations

from typing import Callable, List, Tuple

# WSGI 规范（PEP 3333）+ HTTP/1.1（RFC 2616 13.5.1）定义的 hop-by-hop 头
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def filter_hop_by_hop(response_headers) -> Tuple[list, int]:
    """剥除 hop-by-hop 响应头。返回 (过滤后头列表, 被剥除的数量)。"""
    items = list(response_headers or [])
    filtered = [(k, v) for (k, v) in items if str(k).lower() not in HOP_BY_HOP_HEADERS]
    return filtered, len(items) - len(filtered)


class AccessLogMiddleware:
    """请求一到达即记录访问日志（早于 Flask 处理），并剥除 hop-by-hop 响应头。

    访问日志的意义：即使后续处理抛错/崩溃（甚至如 werkzeug 曾在 socket
    读取阶段抛 MemoryError 静默丢连接），也能看到对端到底有没有把请求发过来。
    """

    def __init__(self, wsgi_app: Callable) -> None:
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        from datetime import datetime

        try:
            addr = environ.get("REMOTE_ADDR", "-")
            method = environ.get("REQUEST_METHOD", "-")
            path = environ.get("PATH_INFO", "-")
            ts = datetime.now().strftime("%d/%b/%Y %H:%M:%S")
            print(
                f"INFO:werkzeug:{addr} - - [{ts}] \"{method} {path} HTTP/1.1\" - -",
                flush=True,
            )
        except Exception:
            pass

        def _safe_start_response(status, response_headers, exc_info=None):
            filtered, dropped = filter_hop_by_hop(response_headers)
            if dropped:
                try:
                    print(
                        f"[Testory] 已剥除 {dropped} 个 hop-by-hop 响应头"
                        f"（{environ.get('REQUEST_METHOD')} {environ.get('PATH_INFO')}）",
                        flush=True,
                    )
                except Exception:
                    pass
            return start_response(status, filtered, exc_info)

        return self.wsgi_app(environ, _safe_start_response)
