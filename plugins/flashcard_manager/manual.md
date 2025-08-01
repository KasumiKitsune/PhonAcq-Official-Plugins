# 速记卡管理器 - 使用手册

欢迎使用“速记卡管理器”插件！本插件为您提供了一个集中管理、预览和导入“速记卡”模块学习材料的强大工具。

## 我能用这个插件做什么？

*   **集中查看**: 在一个界面中，清晰地看到所有可用于“速记卡”模块的学习词表。
*   **快速预览**: 在开始学习前，快速预览标准词表或图文词表的内容，确保加载了正确的材料。
*   **一键导入**: 将您在主程序中创建的标准词表（位于 `word_lists/` 目录）或图文词表（位于 `dialect_visual_wordlists/` 目录）轻松复制到速记卡模块中，无需手动操作文件。
*   **安全管理**: 通过图形界面安全地复制、删除词表，避免误操作。

---

## 快速开始

### 1. 打开管理器

从主程序顶部的插件菜单中，选择 **“速记卡管理器”** 即可打开本插件的独立窗口。

[图片：展示主程序插件菜单，并高亮“速记卡管理器”选项]

### 2. 认识主界面

管理器界面分为左右两个部分：

*   **左侧：词表列表**
    这里会列出所有已经位于速记卡模块目录下的词表文件。文件名前的 `[标准]` 或 `[图文]` 标签可以帮助您快速区分它们的类型。

*   **右侧：内容预览区**
    当您在左侧列表中选择一个词表时，这里会显示该词表的详细内容。

[图片：展示速记卡管理器完整界面，并用箭头标注左侧列表和右侧预览区]

---

## 核心功能详解

### 导入新词表用于学习

这是本插件最核心的功能。当您在“词表编辑器”或“图文词表编辑器”中创建好一份新材料后，可以通过以下步骤将其导入速记卡模块：

1.  在管理器窗口的左下角，点击 **“导入词表...”** 按钮。
2.  程序会弹出一个文件选择对话框，默认打开主程序的 `word_lists/` 目录。您也可以导航到其他任何位置，例如 `dialect_visual_wordlists/` 目录。
3.  选择您想要导入的 `.json` 词表文件，然后点击“打开”。
4.  插件会自动识别该词表的格式（标准或图文），并将其**复制**到速记卡模块对应的文件夹中。
    *   **重要提示**: 如果您导入的是一个图文词表，插件还会自动寻找并复制与该词表同名的图片文件夹。请确保您的图片文件夹与 `.json` 文件位于同一目录下。
5.  导入成功后，您会在左侧列表中看到新加入的词表。

### 预览词表内容

在开始学习前，确认词表内容是否正确非常重要。

*   **对于标准词表**: 点击左侧列表中的 `[标准]` 词表，右侧会以表格形式清晰地展示其包含的组别、单词、备注和语言信息。
*   **对于图文词表**: 点击左侧列表中的 `[图文]` 词表，右侧会显示一个带导航的预览界面。
    *   您可以清晰地看到每个条目的 **ID、提示文字和备注**。
    *   上方会显示对应的 **图片**。
    *   使用 **“上一个”** 和 **“下一个”** 按钮（或键盘的 `←` `→` 键）来浏览词表中的所有条目。

[图片：展示图文词表预览界面，高亮导航按钮和信息区域]

### 管理现有词表 (右键菜单)

在左侧的词表列表上单击鼠标右键，可以打开一个功能菜单，进行更高级的管理操作：

*   **在文件浏览器中显示**: 快速定位到该词表文件在您电脑上的实际位置。
*   **创建副本**: 复制当前选中的词表，并以 `_copy` 后缀命名。如果复制的是图文词表，其关联的图片文件夹也会被一并复制。
*   **删除**: 永久删除选中的词表。这是一个**不可撤销**的操作，程序会弹窗要求您二次确认。删除图文词表会连同图片文件夹一起删除。
*   **刷新列表**: 如果您在程序外部对文件夹进行了修改，点击此项可以重新加载列表。

---

## 专家提示

*   **批量导入**: 虽然文件对话框一次只能选择一个文件，但您可以在操作系统的文件浏览器中，一次性将多个词表文件和图片文件夹拖拽到 `flashcards/` 下对应的子目录中，然后回到管理器点击“刷新列表”即可。
*   **备份**: 在进行大量删除操作前，建议先通过“创建副本”功能对重要的词表进行备份。
*   **命名规范**: 为了让图文词表的图片能被正确关联，请确保图片文件夹的名称与 `.json` 文件的主文件名（不含扩展名）完全一致。例如：`MyVisualList.json` 对应的图片文件夹应为 `MyVisualList/`。