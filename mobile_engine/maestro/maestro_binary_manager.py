# -*- coding: utf-8 -*-
"""
Maestro CLI 二进制管理。

功能:
- 从 GitHub Releases 下载指定版本 Maestro CLI (jar)
- SHA256 校验完整性
- 安装到项目本地 .maestro/ 目录
- 平台自适应 (Windows/macOS/Linux)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MaestroBinaryManager:
    """Maestro CLI 二进制下载与管理"""

    # Maestro GitHub 仓库信息
    GITHUB_REPO = "mobile-dev-inc/maestro"
    GITHUB_API = "https://api.github.com/repos/{repo}/releases"
    # Maestro 使用 JAR 分发, 不同平台文件名略有差异
    DEFAULT_VERSION = "2.0.11"

    # 已安装版本信息缓存文件
    _INSTALL_STATE_FILE = ".maestro/install_state.json"

    def __init__(self, install_root: Optional[str] = None):
        """
        Args:
            install_root: 安装根目录，默认为项目根目录
        """
        if install_root:
            self._root = Path(install_root)
        else:
            self._root = Path(__file__).resolve().parent.parent
        self._install_dir = self._root / ".maestro"
        self._install_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def ensure_installed(self, version: Optional[str] = None) -> str:
        """
        确保指定版本已安装，返回 maestro 可执行路径。
        若未安装则自动下载。

        Returns:
            maestro 命令路径 (jar 路径)
        """
        ver = version or self.DEFAULT_VERSION
        jar_path = self._maestro_jar_path(ver)

        if jar_path.exists():
            if self._verify_sha256(jar_path, ver):
                uat_logger.info("Maestro %s 已安装: %s", ver, jar_path)
                return str(jar_path)
            else:
                uat_logger.warning("Maestro %s 校验失败，重新下载", ver)
                jar_path.unlink(missing_ok=True)

        return self.download_version(ver)

    def download_version(self, version: str) -> str:
        """
        下载指定版本 Maestro JAR。

        Returns:
            本地 jar 路径
        """
        jar_path = self._maestro_jar_path(version)
        jar_path.parent.mkdir(parents=True, exist_ok=True)

        url = self._build_download_url(version)
        uat_logger.info("下载 Maestro %s: %s", version, url)

        # 使用 requests 或 urllib 下载
        if requests:
            resp = requests.get(url, stream=True, timeout=300)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(jar_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = min(100, int(downloaded / total * 100))
                        if downloaded % (total // 20 + 1) < 8192:
                            uat_logger.debug("下载进度: %d%%", pct)
        else:
            import urllib.request

            urllib.request.urlretrieve(url, str(jar_path))

        # 校验 SHA256
        if not self._verify_sha256(jar_path, version):
            jar_path.unlink(missing_ok=True)
            raise RuntimeError(f"Maestro {version} SHA256 校验失败，文件可能损坏")

        # 保存安装状态
        self._save_install_state(version)
        uat_logger.info("Maestro %s 下载完成: %s", version, jar_path)
        return str(jar_path)

    def list_local_versions(self) -> List[str]:
        """列出本地已安装的 Maestro 版本"""
        versions = []
        if not self._install_dir.exists():
            return versions
        for d in self._install_dir.iterdir():
            if d.is_dir() and (d / "maestro.jar").exists():
                versions.append(d.name)
        versions.sort(key=lambda v: [int(x) for x in v.split(".")], reverse=True)
        return versions

    def get_available_versions(self, limit: int = 10) -> List[Dict[str, str]]:
        """
        从 GitHub API 获取可用的 Maestro 版本列表。

        Returns:
            [{"tag_name": "v2.0.11", "name": "v2.0.11", "published_at": "..."}, ...]
        """
        try:
            api_url = self.GITHUB_API.format(repo=self.GITHUB_REPO)
            if requests:
                resp = requests.get(api_url, timeout=30)
                resp.raise_for_status()
                releases = resp.json()
            else:
                import urllib.request
                import urllib.error

                req = urllib.request.Request(api_url, headers={"User-Agent": "Testory"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    releases = json.loads(resp.read().decode())

            result = []
            for rel in releases[:limit]:
                result.append({
                    "tag_name": rel.get("tag_name", ""),
                    "name": rel.get("name", rel.get("tag_name", "")),
                    "published_at": rel.get("published_at", ""),
                    "prerelease": rel.get("prerelease", False),
                })
            return result
        except Exception as exc:
            uat_logger.warning("获取 Maestro 版本列表失败: %s", exc)
            return []

    def remove_version(self, version: str) -> bool:
        """删除本地指定版本"""
        ver_dir = self._install_dir / version
        if ver_dir.exists():
            shutil.rmtree(str(ver_dir), ignore_errors=True)
            self._save_install_state()
            return True
        return False

    def get_java_command(self) -> str:
        """获取 Java 命令路径"""
        # 先检查 JAVA_HOME
        java_home = os.environ.get("JAVA_HOME", "")
        if java_home:
            java_exe = "java.exe" if sys.platform == "win32" else "java"
            java_path = Path(java_home) / "bin" / java_exe
            if java_path.exists():
                return str(java_path)

        # 回退到系统 PATH
        return "java"

    def check_java_runtime(self) -> Tuple[bool, str]:
        """
        检查 Java 运行时是否可用 (版本 >= 11)。

        Returns:
            (ok, message)
        """
        java = self.get_java_command()
        try:
            proc = subprocess.run(
                [java, "-version"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            output = proc.stderr or proc.stdout or ""
            # 提取版本号: java version "17.0.1" 或 openjdk version "11.0.2"
            import re

            m = re.search(r'version\s+"(\d+)', output)
            if m:
                major = int(m.group(1))
                if major >= 11:
                    return True, f"Java {major} 可用"
                else:
                    return False, f"Java 版本过低 ({major})，需要 >= 11"
            return False, f"无法解析 Java 版本: {output[:200]}"
        except FileNotFoundError:
            return False, "未找到 Java，请安装 JDK 11+ 并设置 JAVA_HOME"
        except Exception as exc:
            return False, f"检查 Java 失败: {exc}"

    def build_maestro_cmd(self, version: Optional[str] = None,
                          *args: str) -> List[str]:
        """
        构建 maestro 命令行。

        Returns:
            ["java", "-jar", "<jar_path>", *args]
        """
        java = self.get_java_command()
        jar = self.ensure_installed(version)
        return [java, "-jar", jar, *args]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _maestro_jar_path(self, version: str) -> Path:
        """获取指定版本的 jar 文件路径"""
        return self._install_dir / version / "maestro.jar"

    def _build_download_url(self, version: str) -> str:
        """
        构建 Maestro 下载 URL。

        Maestro releases 中的 JAR 文件命名规则:
        https://github.com/mobile-dev-inc/maestro/releases/download/cli-{version}/maestro.jar
        """
        return (
            f"https://github.com/{self.GITHUB_REPO}"
            f"/releases/download/cli-{version}/maestro.jar"
        )

    def _verify_sha256(self, jar_path: Path, version: str) -> bool:
        """校验 JAR SHA256（如果存在校验文件）"""
        checksum_path = jar_path.with_suffix(jar_path.suffix + ".sha256")
        if not checksum_path.exists():
            # 没有校验文件则不强制校验
            return True

        try:
            expected = checksum_path.read_text().strip().split()[0]
            actual = hashlib.sha256(jar_path.read_bytes()).hexdigest()
            return actual.lower() == expected.lower()
        except Exception:
            return False

    def _save_install_state(self, current_version: Optional[str] = None) -> None:
        """保存安装状态到 JSON 文件"""
        state_path = self._root / self._INSTALL_STATE_FILE
        state: Dict[str, Any] = {
            "current_version": current_version,
            "installed_versions": self.list_local_versions(),
            "platform": platform.system(),
            "arch": platform.machine(),
        }
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception as exc:
            uat_logger.warning("保存安装状态失败: %s", exc)

    def load_install_state(self) -> Dict[str, Any]:
        """读取安装状态"""
        state_path = self._root / self._INSTALL_STATE_FILE
        if state_path.exists():
            try:
                return json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}
