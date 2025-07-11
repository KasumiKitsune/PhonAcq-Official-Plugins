# --- START OF FILE plugins/batch_processor/processor.py (v1.2 UX Enhanced) ---

import os
import sys
import shutil
import tempfile
import traceback
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QApplication, QMessageBox, QGroupBox, QListWidget, QProgressBar,
                             QFileDialog, QTextBrowser, QCheckBox, QComboBox, QRadioButton,
                             QListWidgetItem, QSplitter, QWidget)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence

try:
    import numpy as np
    import soundfile as sf
    AUDIO_LIBS_AVAILABLE = True
except ImportError:
    AUDIO_LIBS_AVAILABLE = False

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 1. 后台音频处理工作器 (Backend Worker)
# ==============================================================================
class AudioProcessorWorker(QObject):
    progress_updated = pyqtSignal(int)
    log_message = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, filepaths, options):
        super().__init__()
        self.filepaths = filepaths
        self.options = options
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        total_files = len(self.filepaths)
        
        for i, filepath in enumerate(self.filepaths):
            if not self._is_running:
                self.log_message.emit("处理被用户取消。")
                break

            filename = os.path.basename(filepath)
            self.log_message.emit(f"--- 开始处理: {filename} ---")
            
            try:
                data, sr = sf.read(filepath)
                original_sr = sr
                
                # [新增]声道转换
                if self.options.get('convert_channels_enabled'):
                    target_channels = self.options.get('target_channels', 1)
                    if data.ndim > 1 and data.shape[1] > 1 and target_channels == 1:
                        self.log_message.emit("  > 正在转换为单声道...")
                        data = np.mean(data, axis=1) # 混合为单声道
                    # (单转多声道比较复杂，暂时只实现多转单)
                
                # 重采样
                target_sr = self.options.get('target_sr')
                if self.options['resample_enabled'] and sr != target_sr:
                    self.log_message.emit(f"  > 正在重采样至 {target_sr} Hz...")
                    num_samples = int(len(data) * target_sr / sr)
                    data = np.interp(np.linspace(0, len(data), num_samples), np.arange(len(data)), data)
                    sr = target_sr
                
                # 音量标准化
                if self.options['normalize_enabled']:
                    norm_type = self.options.get('normalize_type', 'rms')
                    self.log_message.emit(f"  > 正在进行音量标准化 ({norm_type.upper()})...")
                    if norm_type == 'peak':
                        max_val = np.max(np.abs(data))
                        if max_val > 0: data = data / max_val
                    else: # RMS
                        current_rms = np.sqrt(np.mean(data**2))
                        if current_rms > 1e-6:
                            gain = self.options['target_rms'] / current_rms
                            data = np.clip(data * gain, -1.0, 1.0)
                
                # 输出
                output_dir = self.options.get('output_dir') or os.path.dirname(filepath)
                os.makedirs(output_dir, exist_ok=True)
                
                template = self.options.get('name_template', '{original_name}_processed')
                original_name = os.path.splitext(filename)[0]
                replacements = {"{original_name}": original_name, "{samplerate}": str(sr), "{sr}": str(sr), "{original_samplerate}": str(original_sr), "{original_sr}": str(original_sr)}
                new_base_name = template
                for placeholder, value in replacements.items():
                    new_base_name = new_base_name.replace(placeholder, value)
                
                output_ext = self.options.get('output_format', '.wav')
                output_filename = f"{new_base_name}{output_ext}"
                output_path = os.path.join(output_dir, output_filename)

                if os.path.exists(output_path):
                    conflict_policy = self.options.get('conflict_policy', 'skip')
                    if conflict_policy == 'skip':
                        self.log_message.emit(f"  > <font color='orange'>跳过: 文件 '{output_filename}' 已存在。</font>")
                        self.progress_updated.emit(int((i + 1) / total_files * 100))
                        continue
                    elif conflict_policy == 'rename':
                        n = 1
                        while os.path.exists(output_path):
                            output_filename = f"{new_base_name}_{n}{output_ext}"
                            output_path = os.path.join(output_dir, output_filename)
                            n += 1
                        self.log_message.emit(f"  > 文件已存在，重命名为: {output_filename}")
                
                self.log_message.emit(f"  > 正在保存至: {output_filename}")
                sf.write(output_path, data, sr)
                self.log_message.emit(f"  > <font color='green'>成功!</font>")

            except Exception as e:
                self.log_message.emit(f"  > <font color='red'>错误: {e}</font>")
            
            self.progress_updated.emit(int((i + 1) / total_files * 100))
        
        self.finished.emit()

# ==============================================================================
# 2. UI 对话框 (Frontend UI Dialog - v1.2 UX Enhanced)
# ==============================================================================
class BatchProcessorDialog(QDialog):
    def __init__(self, initial_filepaths=None, parent=None, icon_manager=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.icon_manager = icon_manager
        
        self.setWindowTitle("批量音频处理器")
        self.resize(1000, 800)
        self.setMinimumSize(1000, 750)
        
        self._init_ui()
        self._connect_signals()
        
        if initial_filepaths:
            self.add_files_to_list(initial_filepaths)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([380, 620])
        main_layout.addWidget(splitter)

    def _create_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # --- 处理选项区 (带Tooltips) ---
        options_group = QGroupBox("1. 处理选项")
        options_layout = QVBoxLayout(options_group)
        
        # Resampling
        self.resample_check = QCheckBox("重采样到:")
        self.resample_check.setToolTip("启用后，所有音频将被统一重采样到右侧选择的采样率。")
        self.sr_combo = QComboBox()
        self.sr_combo.addItems(["44100", "48000", "22050", "16000"])
        self.sr_combo.setToolTip("选择目标采样率(Hz)。\n- 44100Hz: CD音质，通用标准。\n- 48000Hz: 录音室/视频常用。\n- 16000Hz: 语音识别常用。")
        h1 = QHBoxLayout(); h1.addWidget(self.resample_check); h1.addWidget(self.sr_combo); h1.addStretch()
        
        # Channel Conversion
        self.convert_channels_check = QCheckBox("转换为单声道 (如果为立体声)")
        self.convert_channels_check.setToolTip("启用后，所有立体声音频将被混合为单声道。\n这对于统一后续分析的格式非常有用。")
        
        # Normalization
        self.normalize_check = QCheckBox("音量标准化:")
        self.normalize_check.setToolTip("启用后，将调整所有音频的音量至一个统一的水平。")
        self.normalize_type_combo = QComboBox()
        self.normalize_type_combo.addItems(["RMS (响度)", "Peak (峰值)"])
        self.normalize_type_combo.setToolTip("选择标准化算法：\n- RMS: 基于平均响度进行调整，能更好地平衡人耳听感。\n- Peak: 将音频的最大峰值调整到100%，防止削波，但可能无法统一听感。")
        h_norm = QHBoxLayout(); h_norm.addWidget(self.normalize_check); h_norm.addWidget(self.normalize_type_combo); h_norm.addStretch()
        
        # Output Format
        h3 = QHBoxLayout(); h3.addWidget(QLabel("输出格式:"))
        self.format_combo = QComboBox(); self.format_combo.addItems([".wav", ".flac"])
        self.format_combo.setToolTip("选择输出文件的格式。\n- .wav: 无损格式，兼容性最好。\n- .flac: 无损压缩格式，文件体积更小。")
        h3.addWidget(self.format_combo); h3.addStretch()
        
        options_layout.addLayout(h1); options_layout.addWidget(self.convert_channels_check); options_layout.addLayout(h_norm); options_layout.addLayout(h3)

        # --- 输出设置区 (带Tooltips) ---
        output_group = QGroupBox("2. 输出设置")
        output_layout = QVBoxLayout(output_group)
        
        # Output Directory
        output_dir_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("默认为源文件目录...")
        self.output_dir_edit.setToolTip("指定所有处理后文件的保存位置。\n如果留空，每个文件将被保存在其原始文件夹中。")
        self.browse_output_btn = QPushButton("浏览...")
        output_dir_layout.addWidget(self.output_dir_edit); output_dir_layout.addWidget(self.browse_output_btn)
        
        # Naming Template
        self.name_template_edit = QLineEdit("{original_name}_processed")
        self.name_template_edit.setToolTip(
            "设置输出文件名模板，可使用以下占位符：\n"
            "{original_name} - 原始文件名(不含扩展名)\n"
            "{samplerate} 或 {sr} - 处理后的采样率\n"
            "{original_samplerate} 或 {original_sr} - 原始采样率"
        )
        
        # Conflict Policy
        conflict_box = QGroupBox("如果文件名已存在:")
        conflict_layout = QHBoxLayout(conflict_box)
        self.overwrite_radio = QRadioButton("覆盖")
        self.overwrite_radio.setToolTip("用新生成的文件直接覆盖同名旧文件。")
        self.skip_radio = QRadioButton("跳过")
        self.skip_radio.setToolTip("不处理该文件，保留原始文件不变。")
        self.rename_radio = QRadioButton("重命名(加后缀)")
        self.rename_radio.setToolTip("在文件名后添加 '_1', '_2' 等后缀来保存新文件。")
        self.skip_radio.setChecked(True)
        conflict_layout.addWidget(self.overwrite_radio); conflict_layout.addWidget(self.skip_radio); conflict_layout.addWidget(self.rename_radio)
        
        output_layout.addWidget(QLabel("输出文件夹:"))
        output_layout.addLayout(output_dir_layout)
        output_layout.addWidget(QLabel("文件名模板:"))
        output_layout.addWidget(self.name_template_edit)
        output_layout.addWidget(conflict_box)
        
        layout.addWidget(options_group)
        layout.addWidget(output_group)
        layout.addStretch()
        return panel

    def _create_right_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        input_group = QGroupBox("待处理文件")
        input_layout = QVBoxLayout(input_group)
        self.file_list = QListWidget(); self.file_list.setToolTip("将文件/文件夹拖拽到此，或按 Delete/Backspace 键移除选中项。")
        input_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("添加文件")
        self.add_folder_btn = QPushButton("添加目录")
        self.clear_list_btn = QPushButton("清空")
        # [新增] 设置图标
        if self.icon_manager:
            self.add_files_btn.setIcon(self.icon_manager.get_icon("add_row"))
            self.add_folder_btn.setIcon(self.icon_manager.get_icon("open_folder"))
            self.clear_list_btn.setIcon(self.icon_manager.get_icon("clear_contents"))
        input_btn_layout.addWidget(self.add_files_btn); input_btn_layout.addWidget(self.add_folder_btn); input_btn_layout.addStretch(); input_btn_layout.addWidget(self.clear_list_btn)
        input_layout.addWidget(self.file_list, 1); input_layout.addLayout(input_btn_layout)
        run_group = QGroupBox("执行与日志")
        run_layout = QVBoxLayout(run_group)
        self.start_btn = QPushButton("开始处理"); self.start_btn.setObjectName("AccentButton")
        self.progress_bar = QProgressBar(); self.progress_bar.setValue(0)
        self.log_browser = QTextBrowser(); self.log_browser.setReadOnly(True)
        run_layout.addWidget(self.start_btn); run_layout.addWidget(self.progress_bar); run_layout.addWidget(self.log_browser, 1)
        layout.addWidget(input_group, 1); layout.addWidget(run_group, 1)
        return panel

    def _connect_signals(self):
        self.add_files_btn.clicked.connect(self._add_files)
        self.add_folder_btn.clicked.connect(self._add_folder)
        self.clear_list_btn.clicked.connect(self.file_list.clear)
        self.browse_output_btn.clicked.connect(self._browse_output_dir)
        self.start_btn.clicked.connect(self._start_processing)
        # [新增] 连接 keyPressEvent 以处理按键删除
        self.file_list.keyPressEvent = self.on_file_list_key_press

    # [新增] 按键事件处理器
    def on_file_list_key_press(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            selected_items = self.file_list.selectedItems()
            for item in selected_items:
                self.file_list.takeItem(self.file_list.row(item))
        else:
            # 调用原始的 keyPressEvent 来处理其他按键（如上下箭头）
            super(QListWidget, self.file_list).keyPressEvent(event)

    # [新增] 统一的添加文件方法，处理路径缩写和ToolTip
    def add_files_to_list(self, filepaths):
        for path in filepaths:
            # 缩写路径
            parts = path.split(os.sep)
            if len(parts) > 2:
                display_text = f"...{os.sep}{parts[-2]}{os.sep}{parts[-1]}"
            else:
                display_text = path
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, path) # 将完整路径存储在后台
            item.setToolTip(path) # 鼠标悬停显示完整路径
            self.file_list.addItem(item)
    
    def _add_files(self):
        filepaths, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", "音频文件 (*.wav *.mp3 *.flac *.ogg)")
        if filepaths:
            self.add_files_to_list(filepaths)
    
    def _add_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "选择包含音频的文件夹")
        if directory:
            filepaths_to_add = []
            supported_exts = ('.wav', '.mp3', '.flac', '.ogg')
            for root, _, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(supported_exts):
                        filepaths_to_add.append(os.path.join(root, name))
            self.add_files_to_list(filepaths_to_add)
    
    # ... 其他方法保持不变，但 get_options 需要更新 ...
    def _get_options(self):
        """[更新] 从UI收集所有处理选项。"""
        return {
            'resample_enabled': self.resample_check.isChecked(),
            'target_sr': int(self.sr_combo.currentText()),
            'convert_channels_enabled': self.convert_channels_check.isChecked(),
            'target_channels': 1, # 目前只支持转单声道
            'normalize_enabled': self.normalize_check.isChecked(),
            'normalize_type': self.normalize_type_combo.currentText().lower(),
            'target_rms': 0.1,
            'output_format': self.format_combo.currentText(),
            'output_dir': self.output_dir_edit.text().strip(),
            'name_template': self.name_template_edit.text().strip(),
            'conflict_policy': 'overwrite' if self.overwrite_radio.isChecked() else 'rename' if self.rename_radio.isChecked() else 'skip'
        }

    def _start_processing(self):
        if not AUDIO_LIBS_AVAILABLE: QMessageBox.critical(self, "依赖缺失", "无法开始处理，'numpy' 或 'soundfile' 库未安装。"); return
        
        # [修改] 从 item 的 UserRole 中获取完整路径
        filepaths = [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]
        if not filepaths: QMessageBox.warning(self, "无文件", "请先添加要处理的音频文件。"); return
        
        options = self._get_options()
        if not options['name_template']: QMessageBox.warning(self, "模板无效", "文件名模板不能为空。"); return
        
        self.log_browser.clear(); self.progress_bar.setValue(0); self.set_ui_enabled(False)
        
        self.thread = QThread()
        self.worker = AudioProcessorWorker(filepaths, options)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self.set_ui_enabled(True))
        self.worker.progress_updated.connect(self.progress_bar.setValue)
        self.worker.log_message.connect(self.log_browser.append)
        self.thread.start()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event):
        filepaths_to_add = []
        supported_exts = ('.wav', '.mp3', '.flac', '.ogg')
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for name in files:
                        if name.lower().endswith(supported_exts): filepaths_to_add.append(os.path.join(root, name))
            elif os.path.isfile(path) and path.lower().endswith(supported_exts): filepaths_to_add.append(path)
        self.add_files_to_list(filepaths_to_add)
    def _browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if directory: self.output_dir_edit.setText(directory)
    def set_ui_enabled(self, enabled):
        # ... (此方法逻辑不变)
        for w in [self.add_files_btn, self.add_folder_btn, self.clear_list_btn, self.resample_check, self.sr_combo, self.normalize_check, self.convert_channels_check, self.normalize_type_combo, self.format_combo, self.output_dir_edit, self.browse_output_btn, self.name_template_edit, self.overwrite_radio, self.skip_radio, self.rename_radio]:
            w.setEnabled(enabled)
        self.start_btn.setEnabled(enabled)
        self.start_btn.setText("开始处理" if enabled else "处理中...")

# ==============================================================================
# 3. 插件主入口 (Plugin Entry Point)
# ==============================================================================
class BatchProcessorPlugin(BasePlugin):
    # ... (这个类完全不变，但我们会修改 execute 方法) ...
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
    def setup(self):
        """当插件启用时，向音频管理器注册自己。"""
        self.audio_manager_page = getattr(self.main_window, 'audio_manager_page', None)
        if not self.audio_manager_page:
            print("[Batch Processor] 错误: 未找到音频管理器模块。")
            return False
        
        # 设置钩子，值为插件实例本身
        setattr(self.audio_manager_page, 'batch_processor_plugin_active', self)
        
        print("[Batch Processor] 已向音频管理器注册。")
        return True
    def teardown(self):
        """当插件禁用时，移除钩子。"""
        if hasattr(self, 'audio_manager_page') and hasattr(self.audio_manager_page, 'batch_processor_plugin_active'):
            delattr(self.audio_manager_page, 'batch_processor_plugin_active')
            print("[Batch Processor] 已从音频管理器注销。")
    def execute(self, **kwargs):
        initial_filepaths = kwargs.get('filepaths', None)
        # [修改] 传递 icon_manager
        if self.dialog_instance is None:
            self.dialog_instance = BatchProcessorDialog(initial_filepaths, self.main_window, self.main_window.icon_manager)
            self.dialog_instance.finished.connect(self.on_dialog_finished)
        else:
            if initial_filepaths: self.dialog_instance.add_files_to_list(initial_filepaths)
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()
    def on_dialog_finished(self):
        self.dialog_instance = None