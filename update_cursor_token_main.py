import json
import logging
import os
import platform
import sqlite3
import subprocess
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import psutil
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# 常量配置
class Config:
    """配置常量类"""
    API_URL = "https://cursor.ccopilot.org/api/get_next_token.php"
    ACCESS_CODE = ""
    PROCESS_TIMEOUT = 5
    CURSOR_PROCESS_NAMES = ['cursor.exe', 'cursor']
    DB_KEYS = {
        'email': 'cursorAuth/cachedEmail',
        'access_token': 'cursorAuth/accessToken',
        'refresh_token': 'cursorAuth/refreshToken'
    }
    MIN_PATCH_VERSION = "0.45.0"  # 需要 patch 的版本
    VERSION_PATTERN = r"^\d+\.\d+\.\d+$"  # 版本号格式
    SCRIPT_VERSION = "2025020801"  # 脚本版本号


@dataclass
class TokenData:
    """Token数据类"""
    mac_machine_id: str
    machine_id: str
    dev_device_id: str
    email: str
    token: str

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'TokenData':
        """从字典创建TokenData实例"""
        return cls(
            mac_machine_id=data['mac_machine_id'],
            machine_id=data['machine_id'],
            dev_device_id=data['dev_device_id'],
            email=data['email'],
            token=data['token']
        )


class FilePathManager:
    """文件路径管理器"""

    @staticmethod
    def get_storage_path() -> Path:
        """获取storage.json文件路径"""
        system = platform.system()
        if system == "Windows":
            return Path(os.getenv('APPDATA')) / 'Cursor' / 'User' / 'globalStorage' / 'storage.json'
        elif system == "Darwin":
            return Path.home() / 'Library' / 'Application Support' / 'Cursor' / 'User' / 'globalStorage' / 'storage.json'
        elif system == "Linux":
            return Path.home() / '.config' / 'Cursor' / 'User' / 'globalStorage' / 'storage.json'
        raise OSError(f"不支持的操作系统: {system}")

    @staticmethod
    def get_db_path() -> Path:
        """获取数据库文件路径"""
        system = platform.system()
        if system == "Windows":
            return Path(os.getenv('APPDATA')) / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'
        elif system == "Darwin":
            return Path.home() / 'Library' / 'Application Support' / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'
        elif system == "Linux":
            return Path.home() / '.config' / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'
        raise OSError(f"不支持的操作系统: {system}")

    @staticmethod
    def get_cursor_app_paths() -> Tuple[Path, Path]:
        """获取Cursor应用相关路径"""
        system = platform.system()

        if system == "Windows":
            base_path = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Cursor" / "resources" / "app"
        elif system == "Darwin":
            base_path = Path("/Applications/Cursor.app/Contents/Resources/app")
        elif system == "Linux":
            # 检查可能的Linux安装路径
            possible_paths = [
                Path("/opt/Cursor/resources/app"),
                Path("/usr/share/cursor/resources/app")
            ]
            base_path = next((p for p in possible_paths if p.exists()), None)
            if not base_path:
                raise OSError("在Linux系统上未找到Cursor安装路径")
        else:
            raise OSError(f"不支持的操作系统: {system}")

        return base_path / "package.json", base_path / "out" / "main.js"

    @staticmethod
    def get_update_config_path() -> Optional[Path]:
        """获取更新配置文件路径"""
        system = platform.system()
        if system == "Windows":
            return Path(os.getenv('LOCALAPPDATA')) / 'Programs' / 'Cursor' / 'resources' / 'app-update.yml'
        elif system == "Darwin":
            return Path('/Applications/Cursor.app/Contents/Resources/app-update.yml')
        return None


class FilePermissionManager:
    """文件权限管理器"""

    @staticmethod
    def make_file_writable(file_path: Union[str, Path]) -> None:
        """修改文件权限为可写"""
        file_path = Path(file_path)
        if platform.system() == "Windows":
            subprocess.run(['attrib', '-R', str(file_path)], check=True)
        else:
            os.chmod(file_path, 0o666)

    @staticmethod
    def make_file_readonly(file_path: Union[str, Path]) -> None:
        """修改文件权限为只读"""
        file_path = Path(file_path)
        if platform.system() == "Windows":
            subprocess.run(['attrib', '+R', str(file_path)], check=True)
        else:
            os.chmod(file_path, 0o444)


class CursorAuthManager:
    """Cursor认证信息管理器"""

    def __init__(self):
        self.db_path = FilePathManager.get_db_path()

    def update_auth(self, email: Optional[str] = None,
                    access_token: Optional[str] = None,
                    refresh_token: Optional[str] = None) -> bool:
        """更新或插入Cursor的认证信息"""
        updates: List[Tuple[str, str]] = []
        if email is not None:
            updates.append((Config.DB_KEYS['email'], email))
        if access_token is not None:
            updates.append((Config.DB_KEYS['access_token'], access_token))
        if refresh_token is not None:
            updates.append((Config.DB_KEYS['refresh_token'], refresh_token))

        if not updates:
            logger.info("没有提供任何要更新的值")
            return False

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                for key, value in updates:
                    cursor.execute("SELECT 1 FROM itemTable WHERE key = ?", (key,))
                    exists = cursor.fetchone() is not None

                    if exists:
                        cursor.execute("UPDATE itemTable SET value = ? WHERE key = ?", (value, key))
                    else:
                        cursor.execute("INSERT INTO itemTable (key, value) VALUES (?, ?)", (key, value))
                    logger.info(f"成功{'更新' if exists else '插入'} {key.split('/')[-1]}")
                return True
        except sqlite3.Error as e:
            logger.error(f"数据库错误: {e}")
            return False
        except Exception as e:
            logger.error(f"发生错误: {e}")
            return False


class CursorManager:
    """Cursor管理器"""

    @staticmethod
    def reset_cursor_id(token_data: TokenData) -> bool:
        """重置Cursor ID"""
        storage_path = FilePathManager.get_storage_path()
        if not storage_path.exists():
            logger.warning(f"未找到文件: {storage_path}")
            return False

        try:
            FilePermissionManager.make_file_writable(storage_path)
            data = json.loads(storage_path.read_text(encoding='utf-8'))

            data.update({
                "telemetry.macMachineId": token_data.mac_machine_id,
                "telemetry.machineId": token_data.machine_id,
                "telemetry.devDeviceId": token_data.dev_device_id
            })

            storage_path.write_text(json.dumps(data, indent=4))
            FilePermissionManager.make_file_readonly(storage_path)
            logger.info("Cursor 机器码已成功修改")
            return True
        except Exception as e:
            logger.error(f"重置 Cursor 机器码时发生错误: {e}")
            return False

    @staticmethod
    def exit_cursor() -> bool:
        """安全退出Cursor进程"""
        try:
            logger.info("开始退出 Cursor...")
            cursor_processes = [
                proc for proc in psutil.process_iter(['pid', 'name'])
                if proc.info['name'].lower() in Config.CURSOR_PROCESS_NAMES
            ]

            if not cursor_processes:
                logger.info("未发现运行中的 Cursor 进程")
                return True

            # 温和地请求进程终止
            for proc in cursor_processes:
                try:
                    if proc.is_running():
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # 等待进程终止
            start_time = time.time()
            while time.time() - start_time < Config.PROCESS_TIMEOUT:
                still_running = [p for p in cursor_processes if p.is_running()]
                if not still_running:
                    logger.info("所有 Cursor 进程已正常关闭")
                    return True
                time.sleep(0.5)

            if still_running := [p for p in cursor_processes if p.is_running()]:
                process_list = ", ".join(str(p.pid) for p in still_running)
                logger.warning(f"以下进程未能在规定时间内关闭: {process_list}")
                return False

            return True
        except Exception as e:
            logger.error(f"关闭 Cursor 进程时发生错误: {e}")
            return False


class TokenManager:
    """Token管理器"""

    @staticmethod
    def fetch_token_data(access_code: str, cursor_version: str) -> Optional[TokenData]:
        """获取Token数据"""
        logger.info("正在获取 Token 数据...")
        try:
            params = {
                "accessCode": access_code,
                "cursorVersion": cursor_version,
                "scriptVersion": Config.SCRIPT_VERSION
            }
            response = requests.get(Config.API_URL, params=params)
            data = response.json()

            if data.get("code") == 0 and (token_data := data.get("data")):
                logger.info("成功获取 Token 数据")
                return TokenData.from_dict(token_data)

            logger.warning(f"获取 Token 失败: {data.get('message', '未知错误')}")
            return None
        except Exception as e:
            logger.error(f"获取 Token 数据时发生错误: {e}")
            return None

    @staticmethod
    def update_token(token_data: TokenData) -> bool:
        """更新Cursor的token信息"""
        try:
            # 更新机器ID
            if not CursorManager.reset_cursor_id(token_data):
                return False

            # 更新认证信息
            auth_manager = CursorAuthManager()
            if not auth_manager.update_auth(email=token_data.email, access_token=token_data.token,
                                            refresh_token=token_data.token):
                logger.error("更新 Token 时发生错误")
                return False

            logger.info(f"成功更新 Cursor 认证信息! 邮箱: {token_data.email}")
            return True
        except Exception as e:
            logger.error(f"更新 Token 时发生错误: {e}")
            return False


class Utils:
    """工具类"""

    @staticmethod
    def version_check(version: str, min_version: str = "", max_version: str = "") -> bool:
        """
        版本号检查

        Args:
            version: 当前版本号
            min_version: 最小版本号要求
            max_version: 最大版本号要求

        Returns:
            bool: 版本号是否符合要求
        """
        try:
            if not re.match(Config.VERSION_PATTERN, version):
                logger.error(f"无效的版本号格式: {version}")
                return False

            def parse_version(ver: str) -> Tuple[int, ...]:
                return tuple(map(int, ver.split(".")))

            current = parse_version(version)

            if min_version and current < parse_version(min_version):
                return False

            if max_version and current > parse_version(max_version):
                return False

            return True

        except Exception as e:
            logger.error(f"版本检查失败: {str(e)}")
            return False

    @staticmethod
    def check_files_exist(pkg_path: Path, main_path: Path) -> bool:
        """
        检查文件是否存在

        Args:
            pkg_path: package.json 文件路径
            main_path: main.js 文件路径

        Returns:
            bool: 检查是否通过
        """
        for file_path in [pkg_path, main_path]:
            if not file_path.exists():
                logger.error(f"文件不存在: {file_path}")
                return False
        return True


class CursorPatcher:
    """Cursor补丁管理器"""

    @staticmethod
    def check_version(version: str) -> bool:
        return Utils.version_check(version, min_version=Config.MIN_PATCH_VERSION)

    @staticmethod
    def patch_main_js(main_path: Path) -> bool:
        """
        修改main.js文件以移除机器码检查

        Args:
            main_path: main.js文件路径

        Returns:
            bool: 修改是否成功
        """
        try:
            # 读取文件内容
            content = main_path.read_text(encoding="utf-8")

            # 定义需要替换的模式
            patterns = {
                r"async getMachineId\(\)\{return [^??]+\?\?([^}]+)\}": r"async getMachineId(){return \1}",
                r"async getMacMachineId\(\)\{return [^??]+\?\?([^}]+)\}": r"async getMacMachineId(){return \1}"
            }

            # 检查是否存在需要修复的代码
            found_patterns = False
            for pattern in patterns.keys():
                if re.search(pattern, content):
                    found_patterns = True
                    break

            if not found_patterns:
                logger.info("未发现需要修复的代码，可能已经修复或不支持当前版本")
                return True

            # 执行替换
            for pattern, replacement in patterns.items():
                content = re.sub(pattern, replacement, content)

            # 写入修改后的内容
            FilePermissionManager.make_file_writable(main_path)
            main_path.write_text(content, encoding="utf-8")
            FilePermissionManager.make_file_readonly(main_path)
            logger.info("成功 Patch Cursor 机器码")
            return True

        except Exception as e:
            logger.error(f"Patch Cursor 机器码时发生错误: {e}")
            return False


class UpdateManager:
    """更新管理器"""

    @staticmethod
    def disable_auto_update_main():
        """禁用自动更新"""
        if not UpdateManager.check_auto_upload_file_exist():
            logger.info("暂不支持自动禁用更新，请手动操作，参考：https://linux.do/t/topic/297886")
            return

        if UpdateManager.check_auto_upload_file_empty():
            return

        logger.info("（建议禁用自动更新）是否要禁用 Cursor 自动更新？(y/n)")
        if input().strip().lower() == 'y':
            UpdateManager.disable_auto_update()

    @staticmethod
    def check_auto_upload_file_exist() -> bool:
        """检查更新配置文件是否存在"""
        update_path = FilePathManager.get_update_config_path()
        return update_path and update_path.exists()

    @staticmethod
    def check_auto_upload_file_empty() -> bool:
        """检查更新配置文件是否为空"""
        update_path = FilePathManager.get_update_config_path()
        return update_path and update_path.stat().st_size == 0

    @staticmethod
    def disable_auto_update() -> bool:
        """禁用自动更新"""
        update_path = FilePathManager.get_update_config_path()
        if not update_path:
            logger.error("无法获取更新配置文件路径")
            return False

        if UpdateManager.check_auto_upload_file_empty():
            logger.info("更新配置文件已经为空，无需重复操作")
            return True

        try:
            # 创建备份
            backup_path = update_path.with_suffix('.bak')
            if not backup_path.exists():
                import shutil
                shutil.copy2(update_path, backup_path)
                logger.info(f"已创建配置文件备份: {backup_path}")

            # 清空文件内容
            FilePermissionManager.make_file_writable(update_path)
            update_path.write_text("")
            FilePermissionManager.make_file_readonly(update_path)

            logger.info("已成功禁用自动更新")
            return True

        except Exception as e:
            logger.error(f"禁用自动更新时发生错误: {e}")
            return False


def main() -> None:
    """主函数"""
    try:
        logger.info("提示：本脚本请不要再 Cursor 中执行")
        # 获取Cursor路径
        pkg_path, main_path = FilePathManager.get_cursor_app_paths()

        if not Utils.check_files_exist(pkg_path, main_path):
            logger.warning("请检查是否正确安装 Cursor")
            return

        cursor_version = ""
        # 检查版本
        try:
            cursor_version = json.loads(pkg_path.read_text(encoding="utf-8"))["version"]
            logger.info(f"当前 Cursor 版本: {cursor_version}")
            need_patch = CursorPatcher.check_version(cursor_version)
            if not need_patch:
                logger.info("当前版本无需 Patch，继续执行 Token 更新...")
        except Exception as e:
            logger.error(f"读取版本信息失败: {e}")
            return
        time.sleep(0.1)

        # 获取授权码
        access_code = input("请输入授权码（获取地址：https://cursor.ccopilot.org）: ") or Config.ACCESS_CODE

        # 获取token数据
        token_data = TokenManager.fetch_token_data(access_code, cursor_version)
        if not token_data:
            return

        logger.info("即将退出 Cursor 并修改配置，请确保所有工作已保存。")
        input("按回车键继续...")

        # 退出Cursor
        if not CursorManager.exit_cursor():
            return

        if need_patch and not CursorPatcher.patch_main_js(main_path):
            logger.error("Patch 失败，程序退出")
            return

        if not TokenManager.update_token(token_data):
            return

        logger.info("所有操作已完成，现在可以重新打开Cursor体验了\n")
        logger.info("请注意：建议禁用 Cursor 自动更新!!! ")
        logger.info("从 0.45.xx 开始每次更新都需要重新执行此脚本\n\n")

        UpdateManager.disable_auto_update_main()

    except Exception as e:
        logger.error(f"程序执行过程中发生错误: {e}")


if __name__ == "__main__":
    main()
