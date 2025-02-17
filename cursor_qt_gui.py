import sys
import json
import sqlite3
import os
from pathlib import Path
import requests
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QLabel, QLineEdit, 
                            QFrame, QProgressBar, QMessageBox, QCheckBox)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont
import uuid
import subprocess
from carzy_cursor import TokenManager, CursorManager, FilePathManager, Utils, CursorPatcher, UpdateManager
from dotenv import load_dotenv
from update_cursor_token_main import (TokenData, TokenManager, CursorManager, 
                                    CursorAuthManager, FilePathManager, FilePermissionManager)

# 获取当前文件所在目录的父目录（项目根目录）
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

# 从项目中导入所需模块
from cursor_pro_keep_alive import (
    sign_up_account,
    get_cursor_session_token,
    check_cursor_version,
    EmailGenerator,
    EmailVerificationHandler,
    ExitCursor,
    reset_machine_id
)
from browser_utils import BrowserManager
from update_cursor_token_main import TokenData, TokenManager, CursorManager, CursorAuthManager
from carzy_cursor import UsageManager
from cursor_auth_manager import CursorAuthManager
from reset_machine import MachineIDResetter

def update_cursor_auth(email, access_token, refresh_token):
    auth_manager = CursorAuthManager()
    auth_manager.update_auth(email, access_token, refresh_token)

# 添加 reset_machine_id 函数
def reset_machine_id(greater_than_0_45=True):
    """重置机器码"""
    try:
        resetter = MachineIDResetter()
        # 生成新的机器码
        new_machine_id = str(uuid.uuid4())
        # 更新机器码
        resetter.update_machine_id(new_machine_id)
        return True
    except Exception as e:
        print(f"重置机器码失败: {str(e)}")
        return False

class TokenUpdateWorker(QThread):
    """Token更新工作线程"""
    finished = pyqtSignal(bool, str)
    
    def __init__(self, access_code: str):
        super().__init__()
        self.access_code = access_code
        self.config = {
            "API_URL": "https://cursor.ccopilot.org/api/get_next_token.php",
            "SCRIPT_VERSION": "2025020801"
        }

    def run(self):
        try:
            # 获取当前版本
            pkg_path = Path(os.getenv("LOCALAPPDATA")) / "Programs" / "Cursor" / "resources" / "app" / "package.json"
            cursor_version = json.loads(pkg_path.read_text(encoding="utf-8"))["version"]
            
            # 获取新Token
            params = {
                "accessCode": self.access_code,
                "cursorVersion": cursor_version,
                "scriptVersion": self.config["SCRIPT_VERSION"]
            }
            response = requests.get(self.config["API_URL"], params=params)
            data = response.json()
            
            if data.get("code") != 0:
                self.finished.emit(False, f"获取Token失败: {data.get('message', '未知错误')}")
                return
            
            token_data = data.get("data")
            if not token_data:
                self.finished.emit(False, "获取Token数据为空")
                return
            
            # 退出Cursor进程
            if not CursorManager.exit_cursor():
                self.finished.emit(False, "无法关闭Cursor进程")
                return
            
            # 更新Token
            if TokenManager.update_token(TokenData.from_dict(token_data)):
                self.finished.emit(True, "Token更换成功！")
            else:
                self.finished.emit(False, "Token更换失败")
                
        except Exception as e:
            self.finished.emit(False, f"操作失败: {str(e)}")

class UsageDataWorker(QThread):
    """使用情况数据获取线程"""
    finished = pyqtSignal(dict)
    
    def run(self):
        try:
            # 获取当前token
            auth_manager = CursorAuthManager()
            try:
                with sqlite3.connect(auth_manager.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT value FROM itemTable WHERE key = ?", ('cursorAuth/accessToken',))
                    result = cursor.fetchone()
                    token = result[0] if result else None
            except Exception as e:
                token = None

            if not token:
                self.finished.emit({"error": "未找到有效的token"})
                return

            # 获取订阅信息
            profile = UsageManager.get_stripe_profile(token)
            if not profile:
                self.finished.emit({"error": "获取订阅信息失败"})
                return

            # 获取使用量信息
            usage = UsageManager.get_usage(token)
            if not usage:
                self.finished.emit({"error": "获取使用量信息失败"})
                return

            # 整合数据，确保数值类型字段有默认值
            usage_data = {
                "email": profile.get('email', 'Unknown'),
                "days_left": profile.get('daysRemainingOnTrial', 0),
                "premium_used": usage.get('premium_usage', 0),
                "premium_total": usage.get('max_premium_usage', 150),
                "basic_used": usage.get('basic_usage', 0),
                "basic_total": usage.get('max_basic_usage', None),  # 可以为None
                "account_type": profile.get('membershipType', 'Unknown')
            }
            
            self.finished.emit(usage_data)
            
        except Exception as e:
            self.finished.emit({"error": str(e)})

class RegisterAccountWorker(QThread):
    """账号注册工作线程"""
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)
    
    def run(self):
        try:
            self.progress.emit("正在初始化...")
            
            # 检查版本
            greater_than_0_45 = check_cursor_version()
            
            # 初始化浏览器
            self.progress.emit("正在初始化浏览器...")
            browser_manager = BrowserManager()
            browser = browser_manager.init_browser()
            
            # 初始化邮箱验证模块
            self.progress.emit("正在初始化邮箱验证模块...")
            email_handler = EmailVerificationHandler()
            
            # 生成随机账号信息
            self.progress.emit("正在生成随机账号信息...")
            email_generator = EmailGenerator()
            account_info = email_generator.get_account_info()
            
            self.progress.emit(f"生成的邮箱账号: {account_info['email']}")
            
            tab = browser.latest_tab
            
            # 开始注册流程
            self.progress.emit("开始注册流程...")
            if sign_up_account(browser, tab):
                self.progress.emit("正在获取会话令牌...")
                token = get_cursor_session_token(tab)
                if token:
                    self.progress.emit("更新认证信息...")
                    update_cursor_auth(
                        email=account_info['email'],
                        access_token=token,
                        refresh_token=token
                    )
                    
                    self.progress.emit("重置机器码...")
                    reset_machine_id(greater_than_0_45)
                    
                    success_msg = f"注册成功!\n\n账号信息:\n邮箱: {account_info['email']}\n密码: {account_info['password']}"
                    self.finished.emit(True, success_msg)
                else:
                    self.finished.emit(False, "获取会话令牌失败")
            else:
                self.finished.emit(False, "注册流程失败")
                
        except Exception as e:
            self.finished.emit(False, f"注册过程出错: {str(e)}")
        finally:
            if 'browser_manager' in locals():
                browser_manager.quit()

class CursorQtGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cursor管理工具")
        self.setFixedSize(400, 600)
        self.auth_manager = CursorAuthManager()
        # 加载.env文件
        load_dotenv()
        self.setup_ui()
        self.refresh_usage()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 修改窗口和主布局的背景色为白色
        self.setStyleSheet("""
            QMainWindow {
                background: #FFFFFF;
            }
            QWidget {
                background: #FFFFFF;
            }
        """)

        # 使用情况标题和刷新按钮
        header_layout = QHBoxLayout()
        usage_title = QLabel("本月模型使用情况")
        usage_title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        usage_title.setStyleSheet("QLabel { color: #000000; }")
        header_layout.addWidget(usage_title)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_usage)
        self.style_button(refresh_btn, False)
        refresh_btn.setFixedWidth(80)
        header_layout.addWidget(refresh_btn)
        main_layout.addLayout(header_layout)

        # 用户信息
        self.user_label = QLabel()
        self.user_label.setFont(QFont("Arial", 9))
        self.user_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        main_layout.addWidget(self.user_label)

        # 剩余天数
        self.days_left_label = QLabel()
        self.days_left_label.setFont(QFont("Arial", 9))
        self.days_left_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        main_layout.addWidget(self.days_left_label)

        # Premium使用情况
        self.premium_frame = self.setup_model_usage("Premium", 0, 150, "#3B82F6")
        
        # Basic使用情况
        self.basic_frame = self.setup_model_usage("Basic", 0, 150, "#3B82F6", "无限制")

        # 在Basic使用情况和注册按钮之间添加禁用自动更新复选框
        self.auto_update_checkbox = QCheckBox("禁用自动更新")
        self.auto_update_checkbox.setStyleSheet("""
            QCheckBox {
                color: #374151;
                padding: 5px 0;
            }
        """)
        self.auto_update_checkbox.stateChanged.connect(self.toggle_auto_update)
        # 修改检查逻辑，只在文件存在时才检查是否为空
        is_disabled = False
        if UpdateManager.check_auto_upload_file_exist():
            is_disabled = UpdateManager.check_auto_upload_file_empty()
        self.auto_update_checkbox.setChecked(is_disabled)
        main_layout.addWidget(self.auto_update_checkbox)

        # 注册按钮
        register_btn = QPushButton("注册新号")
        register_btn.clicked.connect(self.register_new_account)
        self.style_button(register_btn, True)
        main_layout.addWidget(register_btn)
        
        # 添加切换账号按钮
        switch_account_btn = QPushButton("切换账号")
        switch_account_btn.clicked.connect(self.switch_account)
        self.style_button(switch_account_btn, True)  # 使用主要样式
        main_layout.addWidget(switch_account_btn)
        
        # 添加弹性空间
        main_layout.addStretch()

    def setup_model_usage(self, model_name: str, used: int, total: int, color: str, total_text: str = None):
        """设置模型使用情况显示"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame { 
                background: #F3F4F6;  /* 改为浅灰色背景 */
                border-radius: 5px; 
                padding: 10px;
                margin: 5px 0px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(5)
        
        # 模型名称
        name_label = QLabel(model_name)
        name_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        name_label.setFont(QFont("Arial", 9))
        layout.addWidget(name_label)
        
        # 使用量
        usage_label = QLabel()
        usage_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        usage_label.setFont(QFont("Arial", 9))
        layout.addWidget(usage_label)
        
        # 进度条
        if total > 0:
            progress = QProgressBar()
            progress.setMaximum(total)
            progress.setValue(used)
            progress.setTextVisible(False)
            progress.setFixedHeight(4)
            progress.setStyleSheet(f"""
                QProgressBar {{
                    background: #E5E7EB;  /* 改为更浅的灰色背景 */
                    border: none;
                    border-radius: 2px;
                }}
                QProgressBar::chunk {{
                    background: {color};
                    border-radius: 2px;
                }}
            """)
            layout.addWidget(progress)
            setattr(frame, 'progress', progress)
        
        # 保存使用量标签引用
        setattr(frame, 'usage_label', usage_label)
        
        self.centralWidget().layout().addWidget(frame)
        return frame

    def style_button(self, button: QPushButton, is_primary: bool = False):
        """设置按钮样式"""
        if is_primary:
            button.setStyleSheet("""
                QPushButton {
                    background-color: #3B82F6;
                    color: white;
                    border: none;
                    padding: 10px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #2563EB;
                }
                QPushButton:pressed {
                    background-color: #1D4ED8;
                }
            """)
        else:
            button.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #374151;
                    border: 1px solid #D1D5DB;
                    padding: 10px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #F3F4F6;
                }
                QPushButton:pressed {
                    background-color: #E5E7EB;
                }
            """)

    def update_usage_display(self, data: dict):
        """更新使用情况显示"""
        if "error" in data:
            QMessageBox.warning(self, "错误", f"获取使用情况失败: {data['error']}")
            return
            
        # 更新用户信息 - 使用本地存储的邮箱
        try:
            with sqlite3.connect(self.auth_manager.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM itemTable WHERE key = ?", ('cursorAuth/cachedEmail',))
                result = cursor.fetchone()
                email = result[0] if result else "未知用户"
        except Exception as e:
            email = "未知用户"
            
        self.user_label.setText(f"当前账号: {email}")
        self.user_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        
        # 更新剩余天数
        self.days_left_label.setText(f"剩余 {data['days_left']} 天")
        self.days_left_label.setStyleSheet("QLabel { color: #000000; }")  # 改为黑色文字
        
        # 更新Premium使用情况
        premium_usage_label = self.premium_frame.usage_label
        premium_usage_label.setText(f"{data['premium_used']} / {data['premium_total']} 请求")
        if hasattr(self.premium_frame, 'progress'):
            self.premium_frame.progress.setValue(data['premium_used'])
            self.premium_frame.progress.setMaximum(data['premium_total'])
        
        # 更新Basic使用情况
        basic_usage_label = self.basic_frame.usage_label
        basic_total = data.get('basic_total')
        if basic_total:
            basic_usage_label.setText(f"{data['basic_used']} / {basic_total} 请求")
        else:
            basic_usage_label.setText(f"{data['basic_used']} 请求 (无限制)")

    def refresh_usage(self):
        """刷新使用情况"""
        self.worker = UsageDataWorker()
        self.worker.finished.connect(self.update_usage_display)
        self.worker.start()

    def update_token(self):
        """更新Token"""
        access_code = self.access_code_input.text().strip()
        if not access_code:
            # 显示错误提示
            return
        
        self.change_account_btn.setEnabled(False)
        self.worker = TokenUpdateWorker(access_code)
        self.worker.finished.connect(self.on_token_update_finished)
        self.worker.start()

    def on_token_update_finished(self, success: bool, message: str):
        """Token更新完成回调"""
        self.change_account_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, "成功", message)
            self.refresh_usage()
        else:
            QMessageBox.warning(self, "错误", message)

    def register_new_account(self):
        """注册新账号"""
        reply = QMessageBox.question(
            self,
            "确认",
            "注册过程将会关闭当前 Cursor，请确保已保存所有工作。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 获取cursor_pro_keep_alive.py的完整路径
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cursor_pro_keep_alive.py')
                
                # 构建命令 - 使用引号正确处理路径
                python_executable = sys.executable.replace('pythonw.exe', 'python.exe')  # 确保使用python.exe
                
                # 使用双引号包裹路径，并转义现有的双引号
                cmd = f'start cmd /k ""{python_executable}" "{script_path}""'
                
                # 执行命令，并设置正确的工作目录
                work_dir = os.path.dirname(script_path)
                subprocess.run(cmd, shell=True, cwd=work_dir)
                
                # 等待一段时间后刷新使用情况
                QThread.sleep(2)
                self.refresh_usage()
                
            except Exception as e:
                QMessageBox.warning(self, "错误", f"注册过程出错: {str(e)}")

    def on_register_finished(self, success: bool, message: str):
        """注册完成回调"""
        if success:
            QMessageBox.information(self, "成功", message)
            self.refresh_usage()  # 刷新使用情况
        else:
            QMessageBox.warning(self, "错误", message)

    def switch_account(self):
        """切换账号"""
        reply = QMessageBox.question(
            self,
            "确认",
            "切换账号将会关闭当前 Cursor，请确保已保存所有工作。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 从.env获取授权码
                access_code = os.getenv('ACCESS_CODE')
                if not access_code:
                    QMessageBox.warning(self, "错误", "未在.env文件中找到ACCESS_CODE")
                    return

                # 获取Cursor版本
                pkg_path, main_path = FilePathManager.get_cursor_app_paths()
                if not Utils.check_files_exist(pkg_path, main_path):
                    QMessageBox.warning(self, "错误", "请检查是否正确安装 Cursor")
                    return

                try:
                    cursor_version = json.loads(pkg_path.read_text(encoding="utf-8"))["version"]
                    need_patch = CursorPatcher.check_version(cursor_version)
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"读取版本信息失败: {str(e)}")
                    return

                # 获取token数据
                token_data = TokenManager.fetch_token_data(access_code, cursor_version)
                if not token_data:
                    QMessageBox.warning(self, "错误", "获取Token数据失败")
                    return

                # 退出Cursor
                if not CursorManager.exit_cursor():
                    QMessageBox.warning(self, "错误", "无法关闭Cursor进程")
                    return

                # 如果需要，执行patch
                if need_patch and not CursorPatcher.patch_main_js(main_path):
                    QMessageBox.warning(self, "错误", "Patch失败")
                    return

                # 更新token
                if not TokenManager.update_token(token_data):
                    QMessageBox.warning(self, "错误", "更新Token失败")
                    return

                # 更新成功
                QMessageBox.information(
                    self,
                    "成功",
                    f"账号切换成功!\n新账号邮箱: {token_data.email}"
                )

                # 刷新使用情况显示
                self.refresh_usage()

                # 提示禁用自动更新
                if need_patch:
                    QMessageBox.information(
                        self,
                        "提示",
                        "请注意：建议禁用Cursor自动更新!\n从0.45.xx开始每次更新都需要重新执行此操作"
                    )
                    UpdateManager.disable_auto_update_main()

            except Exception as e:
                QMessageBox.warning(self, "错误", f"切换账号过程出错: {str(e)}")

    def toggle_auto_update(self, state):
        """切换自动更新状态"""
        try:
            if state == Qt.CheckState.Checked.value:
                # 禁用自动更新
                if UpdateManager.disable_auto_update():
                    QMessageBox.information(self, "成功", "已禁用自动更新")
                else:
                    QMessageBox.warning(self, "错误", "禁用自动更新失败")
                    # 如果失败，取消选中状态
                    self.auto_update_checkbox.setChecked(False)
            else:
                # 启用自动更新（恢复备份文件）
                update_path = FilePathManager.get_update_config_path()
                backup_path = update_path.with_suffix('.bak')
                if backup_path.exists():
                    import shutil
                    try:
                        FilePermissionManager.make_file_writable(update_path)
                        shutil.copy2(backup_path, update_path)
                        FilePermissionManager.make_file_readonly(update_path)
                        QMessageBox.information(self, "成功", "已恢复自动更新")
                    except Exception as e:
                        QMessageBox.warning(self, "错误", f"恢复自动更新失败: {str(e)}")
                        # 如果失败，恢复选中状态
                        self.auto_update_checkbox.setChecked(True)
                else:
                    QMessageBox.warning(self, "错误", "未找到备份文件，无法恢复自动更新")
                    # 如果没有备份文件，恢复选中状态
                    self.auto_update_checkbox.setChecked(True)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"操作失败: {str(e)}")

def main():
    app = QApplication(sys.argv)
    window = CursorQtGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main() 