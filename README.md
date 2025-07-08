# PhonAcq Assistant - 官方插件仓库 (Monorepo)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

欢迎来到 PhonAcq Assistant 的官方插件仓库！这里集中管理了由核心团队开发和维护的所有官方插件。

## 🎯 仓库目标

这个仓库旨在：

-   为所有官方插件提供一个统一的开发和版本管理平台。
-   作为插件开发的最佳实践范例，供社区开发者参考。
-   通过 GitHub Releases 为 PhonAcq Assistant 主程序提供插件的下载和更新源。

## 🔌 插件列表

| 插件名称 | 分类 | 描述 | 状态 |
| :--- | :--- | :--- | :--- |
| **欢迎与开始** | 教学与演示 | 显示欢迎信息和快速导航。 | ✅ 已发布 |
| **IPA 符号键盘** | 效率/辅助工具 | 提供可搜索、可筛选的IPA虚拟键盘。 | ✅ 已发布 |
| **拼音转IPA** | 效率/辅助工具 | 将汉字实时转换为国际音标。 | ✅ 已发布 |
| **批量音频处理器** | 功能扩展 | 对音频文件进行批量格式转换、重采样等。 | ✅ 已发布 |
| **Praat TextGrid 导出器**| 功能扩展 | 将音频分析模块的选区导出为 `.TextGrid`。 | ✅ 已发布 |
| **Praat 启动器** | 系统与集成 | 从数据管理器一键用 Praat 打开音频文件。 | ✅ 已发布 |
| **Odyssey 同步** | 系统与集成 | 提供本地文件夹备份/同步功能。 | ✅ 已发布 |

---

## 🧑‍💻 对于开发者：如何贡献？

我们欢迎对现有插件的改进或提交新的官方插件提案。

### 插件结构规范

每个插件都必须是一个独立的文件夹，位于 `plugins/` 目录下，并包含一个 `plugin.json` 清单文件。详细的插件开发指南请参考 [PhonAcq Assistant 主程序文档](https://github.com/KasumiKitsune/PhonAcq-Official-Plugins)
### 提交流程

1.  **Fork** 本仓库。
2.  创建一个新的分支 (`git checkout -b feature/your-new-plugin`)。
3.  进行修改和开发。
4.  提交您的更改 (`git commit -m 'Add some feature'`)。
5.  将您的分支推送到GitHub (`git push origin feature/your-new-plugin`)。
6.  提交一个 **Pull Request**，并详细描述您的改动。

## 📥 对于用户：如何安装这些插件？

这些插件应该通过 **PhonAcq Assistant** 主程序内的“插件管理 -> 获取插件”功能来自动下载和安装。不建议手动下载和安装，因为这可能导致版本不匹配。

---

感谢您对 PhonAcq Assistant 生态系统的关注和贡献！
