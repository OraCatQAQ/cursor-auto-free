import os
import sys
from pathlib import Path

def setup_environment():
    """设置运行环境"""
    # 获取exe所在目录
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    
    # 设置工作目录
    os.chdir(application_path)
    
    # 确保.env文件存在
    env_path = os.path.join(application_path, '.env')
    if not os.path.exists(env_path):
        # 如果.env不存在，创建一个空的.env文件
        Path(env_path).touch()

if __name__ == "__main__":
    setup_environment()
    
    # 导入并运行主程序
    from cursor_qt_gui import main
    main() 