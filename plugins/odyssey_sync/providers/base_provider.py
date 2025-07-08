# --- START OF FILE plugins/odyssey_sync/providers/base_provider.py ---
from abc import ABC, abstractmethod

class BaseSyncProvider(ABC):
    """同步提供者的抽象基类，定义了所有后端必须实现的方法。"""
    
    @abstractmethod
    def test_connection(self):
        """测试连接是否成功。成功返回True，失败返回错误信息字符串。"""
        pass

    @abstractmethod
    def list_files(self, remote_path):
        """列出远程路径下的所有文件及其元数据（修改时间）。"""
        pass

    @abstractmethod
    def download_file(self, remote_path, local_path):
        """从远程下载一个文件到本地。"""
        pass

    @abstractmethod
    def upload_file(self, local_path, remote_path):
        """从本地上传一个文件到远程。"""
        pass

    @abstractmethod
    def ensure_dir(self, remote_path):
        """确保远程目录存在，如果不存在则创建。"""
        pass

    @abstractmethod
    def delete(self, remote_path):
        """删除远程的一个文件或目录（递归）。"""
        pass