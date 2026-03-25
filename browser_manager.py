"""
浏览器管理模块 - 支持多浏览器和视频录制
"""
import os
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


class BrowserType(Enum):
    CHROMIUM = "chromium"
    CHROME = "chrome"
    FIREFOX = "firefox"
    WEBKIT = "webkit"  # Safari
    EDGE = "edge"


@dataclass
class BrowserConfig:
    """浏览器配置"""
    browser_type: str = "chromium"
    headless: bool = True
    window_width: int = 1920
    window_height: int = 1080
    device_scale_factor: float = 1.0
    user_agent: str = None
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    record_video: bool = False
    video_dir: str = "./videos"
    record_har: bool = False
    har_dir: str = "./har"
    slow_mo: int = 0  # 慢动作延迟（毫秒）
    timeout: int = 30000


class BrowserManager:
    """浏览器管理器"""

    # 浏览器类型映射
    BROWSER_MAP = {
        "chromium": "chromium",
        "chrome": "chromium",
        "firefox": "firefox",
        "webkit": "webkit",
        "safari": "webkit",
        "edge": "chromium"
    }

    # 设备预设
    DEVICE_PRESETS = {
        "desktop": {"width": 1920, "height": 1080},
        "laptop": {"width": 1366, "height": 768},
        "tablet": {"width": 768, "height": 1024},
        "mobile": {"width": 375, "height": 667},
        "mobile_large": {"width": 414, "height": 896}
    }

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.config: Optional[BrowserConfig] = None
        self._video_path: Optional[str] = None

    def start(self, config: BrowserConfig = None) -> Page:
        """启动浏览器"""
        self.config = config or BrowserConfig()

        # 启动 Playwright
        self.playwright = sync_playwright().start()

        # 选择浏览器类型
        browser_type = self.BROWSER_MAP.get(self.config.browser_type, "chromium")

        if browser_type == "chromium":
            browser_launcher = self.playwright.chromium
        elif browser_type == "firefox":
            browser_launcher = self.playwright.firefox
        else:  # webkit
            browser_launcher = self.playwright.webkit

        # 启动浏览器
        launch_options = {
            "headless": self.config.headless,
        }

        if self.config.slow_mo > 0:
            launch_options["slow_mo"] = self.config.slow_mo

        # Chrome/Edge 特定配置
        if self.config.browser_type in ["chrome", "edge"]:
            launch_options["channel"] = self.config.browser_type

        self.browser = browser_launcher.launch(**launch_options)

        # 创建上下文
        context_options = {
            "viewport": {
                "width": self.config.window_width,
                "height": self.config.window_height
            },
            "device_scale_factor": self.config.device_scale_factor,
            "locale": self.config.locale,
            "timezone_id": self.config.timezone,
        }

        if self.config.user_agent:
            context_options["user_agent"] = self.config.user_agent

        # 视频录制配置
        if self.config.record_video:
            os.makedirs(self.config.video_dir, exist_ok=True)
            context_options["record_video_dir"] = self.config.video_dir
            context_options["record_video_size"] = {
                "width": self.config.window_width,
                "height": self.config.window_height
            }

        # HAR录制配置
        if self.config.record_har:
            os.makedirs(self.config.har_dir, exist_ok=True)
            har_path = os.path.join(
                self.config.har_dir,
                f"recording_{int(time.time())}.har"
            )
            context_options["record_har_path"] = har_path

        self.context = self.browser.new_context(**context_options)

        # 设置默认超时
        self.context.set_default_timeout(self.config.timeout)

        # 创建页面
        self.page = self.context.new_page()

        return self.page

    def stop(self) -> Optional[str]:
        """停止浏览器，返回视频路径（如果有录制）"""
        video_path = None

        try:
            # 关闭页面，保存视频
            if self.page:
                if self.page.video:
                    video_path = self.page.video.path()
                self.page.close()
                self.page = None

            # 关闭上下文
            if self.context:
                self.context.close()
                self.context = None

            # 关闭浏览器
            if self.browser:
                self.browser.close()
                self.browser = None

            # 停止 Playwright
            if self.playwright:
                self.playwright.stop()
                self.playwright = None

        except Exception as e:
            print(f"关闭浏览器时出错: {e}")

        return video_path

    def get_page(self) -> Optional[Page]:
        """获取当前页面"""
        return self.page

    def take_screenshot(self, path: str = None, full_page: bool = True) -> str:
        """截图"""
        if not self.page:
            raise RuntimeError("浏览器未启动")

        if path is None:
            os.makedirs("./screenshots", exist_ok=True)
            path = f"./screenshots/screenshot_{int(time.time())}.png"

        self.page.screenshot(path=path, full_page=full_page)
        return path

    def get_video_path(self) -> Optional[str]:
        """获取视频路径"""
        if self.page and self.page.video:
            return self.page.video.path()
        return None

    @classmethod
    def get_available_browsers(cls) -> List[Dict[str, str]]:
        """获取可用的浏览器列表"""
        return [
            {"id": "chromium", "name": "Chromium", "type": "chromium"},
            {"id": "chrome", "name": "Google Chrome", "type": "chromium"},
            {"id": "firefox", "name": "Mozilla Firefox", "type": "firefox"},
            {"id": "webkit", "name": "WebKit (Safari)", "type": "webkit"},
            {"id": "edge", "name": "Microsoft Edge", "type": "chromium"}
        ]

    @classmethod
    def get_device_presets(cls) -> Dict[str, Dict[str, int]]:
        """获取设备预设"""
        return cls.DEVICE_PRESETS.copy()


# 全局浏览器管理器实例
_browser_manager: Optional[BrowserManager] = None


def get_browser_manager() -> BrowserManager:
    """获取全局浏览器管理器"""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = BrowserManager()
    return _browser_manager


def start_browser(config: BrowserConfig = None) -> Page:
    """启动浏览器（便捷函数）"""
    manager = get_browser_manager()
    return manager.start(config)


def stop_browser() -> Optional[str]:
    """停止浏览器（便捷函数）"""
    global _browser_manager
    if _browser_manager:
        video_path = _browser_manager.stop()
        _browser_manager = None
        return video_path
    return None


def get_page() -> Optional[Page]:
    """获取当前页面（便捷函数）"""
    manager = get_browser_manager()
    return manager.get_page()


if __name__ == '__main__':
    # 测试代码
    print("可用浏览器:")
    for browser in BrowserManager.get_available_browsers():
        print(f"  - {browser['name']} ({browser['id']})")

    print("\n设备预设:")
    for name, size in BrowserManager.get_device_presets().items():
        print(f"  - {name}: {size['width']}x{size['height']}")

    # 测试启动浏览器
    # config = BrowserConfig(
    #     browser_type="chromium",
    #     headless=False,
    #     record_video=True
    # )
    # page = start_browser(config)
    # page.goto("https://www.example.com")
    # time.sleep(3)
    # video_path = stop_browser()
    # print(f"视频保存路径: {video_path}")
