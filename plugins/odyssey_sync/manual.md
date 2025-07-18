# Odyssey 同步 - 插件使用手册

## 插件用途

**Odyssey 同步** 是一个强大的数据备份和恢复工具。它能帮助您将 PhonAcq Assistant 中的重要数据（如词表、录音成果）备份到一个安全的本地文件夹，从而防止数据丢失，并方便在不同设备间迁移工作环境。

## 核心功能

- **双向同步**: 插件采用双向同步逻辑。它会比较本地项目文件夹和您指定的目标备份文件夹，然后进行增量更新。
- **灵活的冲突策略**: 您可以决定当两边文件都发生变化时如何处理：
    - **保留较新**: 自动保留修改时间最新的文件（默认）。
    - **以本地为准**: 强制用程序内的文件覆盖备份（单向备份）。
    - **以备份为准**: 强制用备份文件覆盖程序内的文件（数据恢复）。
- **自定义备份项目**: 除了默认的项目（词表、录音等），您还可以手动添加程序根目录下的任何其他文件夹作为自定义备份目标。
- **状态管理**: 通过清晰的图标和右键菜单，您可以轻松地启用、暂停或立即备份任意项目。

## 如何使用

1.  **配置目标文件夹**:
    - 在左侧“备份目标”区域，点击“...”按钮，选择一个**空文件夹**作为您的备份仓库。
    - 点击“测试目标文件夹”以确保程序有权读写该位置。
2.  **选择冲突策略**: 在“通用设置”中，根据您的需求选择合适的冲突解决策略。
3.  **管理备份项目**:
    - 在右侧列表中，您可以看到所有可备份的项目。
    - **右键单击**任意项目，可以“启用/暂停备份”、“立即备份此项”或“清空目标备份”。
    - 使用“添加项目”按钮可以添加自定义的文件夹。
4.  **执行备份**:
    - 点击右下角的 **“立即备份所有启用项”** 按钮，开始对所有已启用的项目进行一次全面备份。
    - 观察底部状态栏的实时进度。

## 如何实现云同步？

本插件可以通过与任何第三方云同步工具（如坚果云、Dropbox、百度网盘等）结合，实现全自动的云端同步。**方法非常简单**：

1.  在您的电脑上安装云同步客户端。
2.  将本插件的“备份根目录”**直接设置为云同步工具的本地同步文件夹**。
3.  完成！本插件会将数据备份到该文件夹，云工具会自动将其上传。