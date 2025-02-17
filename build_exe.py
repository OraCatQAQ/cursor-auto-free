import PyInstaller.__main__
import os
import sys

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 定义图标文件路径（如果有的话）
# icon_path = os.path.join(current_dir, 'icon.ico')

# 定义需要打包的Python文件
main_file = os.path.join(current_dir, 'launcher.py')

# 定义需要包含的其他Python文件
additional_files = [
    'carzy_cursor.py',
    'cursor_pro_keep_alive.py',
    'update_cursor_token_main.py',
    'browser_utils.py',
    'cursor_auth_manager.py',
    'reset_machine.py'
]

# 构建完整的文件路径
hidden_imports = [
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'requests',
    'sqlite3',
    'psutil',
    'dotenv'
]

PyInstaller.__main__.run([
    main_file,
    '--name=Cursor管理工具',
    '--onefile',  # 打包成单个exe文件
    '--noconsole',  # 不显示控制台窗口
    '--clean',  # 清理临时文件
    # f'--icon={icon_path}',  # 如果有图标的话
    '--add-data=.env;.',  # 添加.env文件
] + 
[f'--hidden-import={imp}' for imp in hidden_imports] +  # 添加隐式导入
[f'--add-data={f};.' for f in additional_files]  # 添加其他Python文件
) 