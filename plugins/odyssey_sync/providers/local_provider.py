# --- START OF FILE plugins/odyssey_sync/providers/local_provider.py ---
import os
import time
import shutil
from .base_provider import BaseSyncProvider

class LocalFolderProvider(BaseSyncProvider):
    """将“同步”实现为到另一个本地文件夹的复制。"""
    def __init__(self, target_root_path):
        self.root = target_root_path

    def test_connection(self):
        if not os.path.isdir(self.root):
            try:
                os.makedirs(self.root)
            except Exception as e:
                 return f"无法创建或访问目标文件夹，请检查权限: {e}"
        try:
            # [核心修复] 使用 time.time() 而不是 shutil.time.time()
            test_file = os.path.join(self.root, f".phonacq_test_{int(time.time())}")
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            return True
        except Exception as e:
            return f"无法写入目标文件夹，请检查权限: {e}"

    def list_files(self, remote_path):
        full_path = os.path.join(self.root, remote_path)
        if not os.path.isdir(full_path): return {}
        file_map = {}
        for root, _, files in os.walk(full_path):
            for name in files:
                local_file_path = os.path.join(root, name)
                relative_path = os.path.relpath(local_file_path, full_path).replace('\\', '/')
                file_map[relative_path] = os.path.getmtime(local_file_path)
        return file_map

    def download_file(self, remote_path, local_path):
        full_remote_path = os.path.join(self.root, remote_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        shutil.copy2(full_remote_path, local_path)

    def upload_file(self, local_path, remote_path):
        full_remote_path = os.path.join(self.root, remote_path)
        os.makedirs(os.path.dirname(full_remote_path), exist_ok=True)
        shutil.copy2(local_path, full_remote_path)

    def ensure_dir(self, remote_path):
        os.makedirs(os.path.join(self.root, remote_path), exist_ok=True)
        
    def delete(self, remote_path):
        full_path = os.path.join(self.root, remote_path)
        if os.path.isfile(full_path):
            os.remove(full_path)
        elif os.path.isdir(full_path):
            shutil.rmtree(full_path)