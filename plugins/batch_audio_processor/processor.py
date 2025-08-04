# --- START OF FILE plugins/batch_processor/processor.py (v2.4 Final Polish) ---

import os
import sys
import shutil
import tempfile
import traceback
import threading
from functools import partial

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QApplication, QMessageBox, QGroupBox, QTableWidget, QProgressBar,
                             QFileDialog, QCheckBox, QComboBox, QRadioButton, QTableWidgetItem,
                             QSplitter, QWidget, QHeaderView, QMenu, QLineEdit, QSlider, QFormLayout)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, QEvent, pyqtProperty
from PyQt5.QtGui import QIcon, QPainter, QPen, QColor, QPalette

try:
    import numpy as np
    import soundfile as sf
    import sounddevice as sd
    AUDIO_LIBS_AVAILABLE = True
except ImportError:
    AUDIO_LIBS_AVAILABLE = False

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

class QuickNormalizeWorker(QObject):
    progress = pyqtSignal(int, str)
    finished_with_refresh_request = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, filepaths, parent_window):
        super().__init__()
        self.filepaths = filepaths
        self.parent_window = parent_window
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        # 固定的处理选项 (保持不变)
        TARGET_SR = 44100
        OPTIONS = {
            'convert_channels_enabled': True,
            'resample_enabled': True, 'target_sr': TARGET_SR,
            'normalize_enabled': True
        }

        total_files = len(self.filepaths)
        for i, filepath in enumerate(self.filepaths):
            if not self._is_running:
                break
            
            filename = os.path.basename(filepath)
            self.progress.emit(int((i / total_files) * 100), f"处理中 ({i+1}/{total_files}): {filename}")

            try:
                # =================== [核心修正开始] ===================

                # 1. 分离基本路径和旧扩展名
                base_path, old_ext = os.path.splitext(filepath)
                # 2. 创建新的目标文件路径，强制使用 .wav 扩展名
                target_filepath = base_path + ".wav"

                # 读取源文件
                data, sr = sf.read(filepath)
                
                # --- 标准化处理 (已移除静音裁切) ---
                if OPTIONS['convert_channels_enabled']:
                    if data.ndim > 1 and data.shape[1] > 1:
                        data = np.mean(data, axis=1)
                if OPTIONS['resample_enabled'] and sr != OPTIONS['target_sr']:
                    num_samples = int(len(data) * OPTIONS['target_sr'] / sr)
                    data = np.interp(np.linspace(0, len(data), num_samples), np.arange(len(data)), data)
                    sr = OPTIONS['target_sr']
                if OPTIONS['normalize_enabled']:
                    current_rms = np.sqrt(np.mean(data**2))
                    if current_rms > 1e-9:
                        gain = 0.1 / current_rms
                        data = np.clip(data * gain, -1.0, 1.0)
                
                # 3. 将处理后的数据写入新的 .wav 文件
                sf.write(target_filepath, data, sr, format='WAV')

                # 4. 如果源文件不是 WAV (意味着发生了格式转换)，则在写入成功后删除源文件
                if old_ext.lower() != ".wav" and os.path.exists(filepath):
                    os.remove(filepath)

                # =================== [核心修正结束] ===================

            except Exception as e:
                error_msg = f"处理文件 '{filename}' 时出错: {e}"
                print(error_msg, file=sys.stderr)
                # self.error.emit(error_msg)
                continue

        self.progress.emit(100, "处理完成！")
        self.finished_with_refresh_request.emit()

# ==============================================================================
# 0. 可样式化的波形预览控件 (从核心模块引入)
# ==============================================================================
class WaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(30)
        self._waveform_data = None
        
        # 定义所有颜色属性的默认值
        self._waveformColor = self.palette().color(QPalette.Highlight)
        self._waveformColorSelected = QColor("white")
        self._cursorColor = QColor("red") # 新增默认值
        self._selectionColor = QColor(0, 100, 255, 60) # 新增默认值

        self._is_selected = False

    # --- 定义所有 pyqtProperty，暴露给QSS ---
    @pyqtProperty(QColor)
    def waveformColor(self): return self._waveformColor
    @waveformColor.setter
    def waveformColor(self, color):
        if self._waveformColor != color: self._waveformColor = color; self.update()

    @pyqtProperty(QColor)
    def waveformColorSelected(self): return self._waveformColorSelected
    @waveformColorSelected.setter
    def waveformColorSelected(self, color):
        if self._waveformColorSelected != color: self._waveformColorSelected = color; self.update()

    # [核心修复] 新增 cursorColor 属性的 getter 和 setter
    @pyqtProperty(QColor)
    def cursorColor(self): return self._cursorColor
    @cursorColor.setter
    def cursorColor(self, color):
        if self._cursorColor != color: self._cursorColor = color; self.update()

    # [核心修复] 新增 selectionColor 属性的 getter 和 setter
    @pyqtProperty(QColor)
    def selectionColor(self): return self._selectionColor
    @selectionColor.setter
    def selectionColor(self, color):
        if self._selectionColor != color: self._selectionColor = color; self.update()

    def set_selected(self, selected):
        if self._is_selected != selected:
            self._is_selected = selected
            self.update()

    def set_waveform_data(self, audio_filepath):
        self._waveform_data = None
        if not (audio_filepath and os.path.exists(audio_filepath)): self.update(); return
        try:
            data, sr = sf.read(audio_filepath, dtype='float32')
            if data.ndim > 1: data = data.mean(axis=1)
            num_samples = len(data)
            target_points = self.width() * 2 if self.width() > 0 else 400
            if num_samples <= target_points: self._waveform_data = data
            else:
                step = num_samples // target_points
                peak_data = [np.max(np.abs(data[i:i+step])) for i in range(0, num_samples, step)]
                self._waveform_data = np.array(peak_data)
        except Exception as e: self._waveform_data = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().color(QPalette.Base))
        if self._waveform_data is None or len(self._waveform_data) == 0: return
        
        color_to_use = self._waveformColorSelected if self._is_selected else self._waveformColor
        painter.setPen(QPen(color_to_use, 1))
        
        h, w, num_points = self.height(), self.width(), len(self._waveform_data)
        half_h = h / 2; max_val = np.max(self._waveform_data)
        if max_val == 0: max_val = 1.0
        for i, val in enumerate(self._waveform_data):
            x = int(i * w / num_points); y_offset = (val / max_val) * half_h
            painter.drawLine(x, int(half_h - y_offset), x, int(half_h + y_offset))
# ==============================================================================
# 1. 后台音频处理工作器 (无变动)
# ==============================================================================
def trim_silence_numpy(audio, sr, threshold_db, padding_ms, fade_out_ms):
    threshold_amp = 10**(threshold_db / 20); padding_samples = int(sr * padding_ms / 1000)
    is_sound = np.abs(audio) > threshold_amp
    if not np.any(is_sound): return audio
    first_sound = np.argmax(is_sound); last_sound = len(audio) - 1 - np.argmax(is_sound[::-1])
    start_index = max(0, first_sound - padding_samples); end_index = min(len(audio), last_sound + padding_samples)
    trimmed_audio = audio[start_index:end_index]
    if fade_out_ms > 0 and len(trimmed_audio) > 0:
        fade_samples = min(int(sr * fade_out_ms / 1000), len(trimmed_audio))
        if fade_samples > 0: trimmed_audio[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)
    return trimmed_audio

class AudioProcessorWorker(QObject):
    file_processed = pyqtSignal(dict); finished = pyqtSignal()
    def __init__(self, file_data, options, temp_dir):
        super().__init__(); self.file_data = file_data; self.options = options; self.temp_dir = temp_dir; self._is_running = True
    def stop(self): self._is_running = False
    def run(self):
        for i, item in enumerate(self.file_data):
            if not self._is_running: break
            filepath = item['original_path']; filename = os.path.basename(filepath); log_messages = []
            try:
                data, sr = sf.read(filepath)
                if self.options.get('trim_silence_enabled'):
                    log_messages.append("  > 正在裁切静音..."); data = trim_silence_numpy(data, sr, self.options['trim_threshold_db'], self.options['trim_padding_ms'], self.options['trim_fade_out_ms'])
                if self.options.get('convert_channels_enabled'):
                    if data.ndim > 1 and data.shape[1] > 1: log_messages.append("  > 正在转换为单声道..."); data = np.mean(data, axis=1)
                if self.options['resample_enabled'] and sr != self.options['target_sr']:
                    log_messages.append(f"  > 正在重采样至 {self.options['target_sr']} Hz..."); num_samples = int(len(data) * self.options['target_sr'] / sr); data = np.interp(np.linspace(0, len(data), num_samples), np.arange(len(data)), data); sr = self.options['target_sr']
                if self.options['normalize_enabled']:
                    log_messages.append(f"  > 正在进行音量标准化 (RMS)..."); current_rms = np.sqrt(np.mean(data**2));
                    if current_rms > 1e-9: gain = 0.1 / current_rms; data = np.clip(data * gain, -1.0, 1.0)
                temp_filename = f"{os.path.splitext(filename)[0]}_temp.wav"; temp_path = os.path.join(self.temp_dir, temp_filename)
                sf.write(temp_path, data, sr); log_messages.append(f"  > <font color='green'>预览生成成功!</font>")
                result = {'row': i, 'status': 'processed', 'temp_path': temp_path, 'log': "<br>".join(log_messages)}
            except Exception as e:
                log_messages.append(f"  > <font color='red'>错误: {e}</font>"); result = {'row': i, 'status': 'error', 'temp_path': None, 'log': "<br>".join(log_messages)}
            self.file_processed.emit(result)
        self.finished.emit()

# ==============================================================================
# 2. UI 对话框 (核心修改)
# ==============================================================================
class BatchProcessorDialog(QDialog):
    def __init__(self, initial_filepaths=None, parent=None, icon_manager=None):
        super().__init__(parent); self.setAcceptDrops(True); self.icon_manager = icon_manager
        self.plugin_dir = os.path.dirname(__file__); self.temp_dir = os.path.join(self.plugin_dir, "temp")
        self._setup_temp_dir(); self.file_data = []
        self.setWindowTitle("批量音频处理器"); self.resize(1100, 800)
        self._init_ui(); self._load_icons(); self._connect_signals()
        if initial_filepaths: self.add_files_to_list(initial_filepaths)

    def _setup_temp_dir(self):
        if os.path.exists(self.temp_dir): shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir, exist_ok=True)
        
    def _load_icons(self):
        if self.icon_manager:
            self.icons = {'pending': self.icon_manager.get_icon("replace"), 'success': self.icon_manager.get_icon("success"), 'error': self.icon_manager.get_icon("error"), 'saved': self.icon_manager.get_icon("saved")}
        else:
            self.icons = {'pending': QIcon(), 'success': QIcon(), 'error': QIcon(), 'saved': QIcon()}

    def _init_ui(self):
        main_layout = QVBoxLayout(self); splitter = QSplitter(Qt.Horizontal); left_panel = self._create_left_panel(); right_panel = self._create_right_panel()
        splitter.addWidget(left_panel); splitter.addWidget(right_panel); splitter.setSizes([380, 720]); main_layout.addWidget(splitter)
        
    def _create_left_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        options_group = QGroupBox("处理选项"); options_layout = QVBoxLayout(options_group)

        def create_slider_row(label, value, min_val, max_val, suffix, tooltip):
            slider = QSlider(Qt.Horizontal); slider.setRange(min_val, max_val); slider.setValue(value); slider.setToolTip(tooltip)
            label_widget = QLabel(); label_widget.setMinimumWidth(55)
            def update_label(val): label_widget.setText(f"{val} {suffix}")
            slider.valueChanged.connect(update_label); update_label(slider.value())
            row_layout = QHBoxLayout(); row_layout.addWidget(slider); row_layout.addWidget(label_widget)
            return row_layout, slider

        self.trim_silence_check = QCheckBox("裁切首尾静音"); self.trim_silence_check.setToolTip("启用后，将自动移除音频文件开头和结尾的静音部分。")
        layout_trim_thresh, self.trim_threshold_slider = create_slider_row("阈值", -40, -60, -20, "dB", "低于此分贝的声音被视为静音。")
        layout_trim_pad, self.trim_padding_slider = create_slider_row("保留", 50, 0, 500, "ms", "在检测到的声音前后保留指定时长的静音，防止裁切过近。")
        layout_trim_fade, self.trim_fade_out_slider = create_slider_row("渐弱", 10, 0, 100, "ms", "在裁切后的音频末尾应用渐弱效果，使结束更自然。")
        
        trim_form = QFormLayout(); trim_form.addRow(self.trim_silence_check); trim_form.addRow("静音判定阈值:", layout_trim_thresh); trim_form.addRow("首尾保留时长:", layout_trim_pad); trim_form.addRow("结尾渐弱时长:", layout_trim_fade)
        options_layout.addLayout(trim_form); options_layout.addWidget(QLabel("<hr>"))

        other_options_form = QFormLayout()
        self.resample_check = QCheckBox("重采样到:"); self.resample_check.setToolTip("将所有音频的采样率统一转换为所选值。")
        self.sr_combo = QComboBox(); self.sr_combo.addItems(["44100", "48000", "16000"]); self.sr_combo.setToolTip("44.1kHz是CD标准, 48kHz是视频标准, 16kHz是语音识别常用。")
        h1 = QHBoxLayout(); h1.addWidget(self.resample_check); h1.addWidget(self.sr_combo); h1.addStretch()
        self.convert_channels_check = QCheckBox("转换为单声道 (如果为立体声)"); self.convert_channels_check.setToolTip("将所有立体声音频混合为单声道，便于统一处理。")
        self.normalize_check = QCheckBox("音量标准化 (RMS -20dBFS)"); self.normalize_check.setToolTip("将所有音频的平均响度调整到一个统一的标准，避免音量忽大忽小。")
        other_options_form.addRow(h1); other_options_form.addRow(self.convert_channels_check); other_options_form.addRow(self.normalize_check)
        options_layout.addLayout(other_options_form)
        
        output_group = QGroupBox("输出设置"); output_layout = QVBoxLayout(output_group)
        self.output_dir_edit = QLineEdit(); self.output_dir_edit.setPlaceholderText("默认为源文件目录..."); self.output_dir_edit.setToolTip("指定所有处理后文件的保存位置。\n如果留空，每个文件将被保存在其原始文件夹中。")
        self.browse_output_btn = QPushButton("浏览..."); self.browse_output_btn.setToolTip("选择一个文件夹作为输出目录。")
        output_dir_layout = QHBoxLayout(); output_dir_layout.addWidget(self.output_dir_edit); output_dir_layout.addWidget(self.browse_output_btn)
        
        self.name_template_edit = QLineEdit("{original_name}_processed"); self.name_template_edit.setToolTip("设置输出文件名模板，可使用以下占位符：\n{original_name} - 原始文件名(不含扩展名)\n{samplerate} 或 {sr} - 处理后的采样率")
        
        # [核心修改] 增加输出格式选项
        format_layout = QFormLayout()
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems([".wav", ".mp3", ".flac", ".ogg"])
        self.output_format_combo.setToolTip("选择输出文件的格式:\n- WAV: 无损，兼容性最好\n- MP3: 有损压缩，文件小\n- FLAC: 无损压缩，体积比WAV小\n- OGG: 有损压缩，开源格式")
        format_layout.addRow("输出格式:", self.output_format_combo)
        
        conflict_box = QGroupBox("如果文件名已存在:"); conflict_layout = QHBoxLayout(conflict_box)
        self.overwrite_radio = QRadioButton("覆盖"); self.skip_radio = QRadioButton("跳过"); self.rename_radio = QRadioButton("重命名"); self.skip_radio.setChecked(True)
        conflict_layout.addWidget(self.overwrite_radio); conflict_layout.addWidget(self.skip_radio); conflict_layout.addWidget(self.rename_radio)
        
        output_layout.addWidget(QLabel("输出文件夹:")); output_layout.addLayout(output_dir_layout); output_layout.addWidget(QLabel("文件名模板:")); output_layout.addWidget(self.name_template_edit); output_layout.addLayout(format_layout); output_layout.addWidget(conflict_box)
        
        layout.addWidget(options_group); layout.addWidget(output_group); layout.addStretch()
        return panel
        
    def _create_right_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        input_group = QGroupBox("待处理文件列表"); input_layout = QVBoxLayout(input_group)
        
        # [核心修改] 表格增加一列
        self.file_table = QTableWidget(); self.file_table.setColumnCount(2)
        self.file_table.setHorizontalHeaderLabels(["文件 (右键可操作)", "波形预览"])
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setSelectionBehavior(QTableWidget.SelectRows); self.file_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_table.setToolTip("文件列表。右键点击项目可进行操作，按回车键可快速试听。")

        input_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("添加文件"); self.add_folder_btn = QPushButton("添加目录"); self.clear_list_btn = QPushButton("清空")
        input_btn_layout.addWidget(self.add_files_btn); input_btn_layout.addWidget(self.add_folder_btn); input_btn_layout.addStretch(); input_btn_layout.addWidget(self.clear_list_btn)
        input_layout.addWidget(self.file_table, 1); input_layout.addLayout(input_btn_layout)
        
        run_group = QGroupBox("执行操作"); run_layout = QVBoxLayout(run_group)
        self.process_btn = QPushButton("1. 处理并生成预览"); self.process_btn.setObjectName("AccentButton"); self.process_btn.setToolTip("根据左侧的设置处理所有文件，并在临时文件夹中生成预览。")
        self.save_btn = QPushButton("2. 保存所有已处理文件"); self.save_btn.setEnabled(False); self.save_btn.setToolTip("将所有处理成功的音频保存到指定的输出文件夹。")
        self.progress_bar = QProgressBar(); self.progress_bar.setValue(0)
        run_layout.addWidget(self.process_btn); run_layout.addWidget(self.save_btn); run_layout.addWidget(self.progress_bar)
        
        layout.addWidget(input_group, 1); layout.addWidget(run_group)
        return panel

    def _on_selection_changed(self):
        """当表格选择项改变时，更新所有波形图的选中状态。"""
        selected_rows = {index.row() for index in self.file_table.selectedIndexes()}
    
        for row in range(self.file_table.rowCount()):
            widget = self.file_table.cellWidget(row, 1)
            if isinstance(widget, WaveformWidget):
                widget.set_selected(row in selected_rows)

    def resizeEvent(self, event):
        """[新增] 动态调整列宽"""
        super().resizeEvent(event)
        if self.file_table.width() > 0:
            name_width = int(self.file_table.width() * 0.65)
            waveform_width = self.file_table.width() - name_width
            self.file_table.setColumnWidth(0, name_width)
            self.file_table.setColumnWidth(1, waveform_width)

    def _connect_signals(self):
        self.add_files_btn.clicked.connect(self._add_files); self.add_folder_btn.clicked.connect(self._add_folder); self.clear_list_btn.clicked.connect(self.clear_all_files)
        self.browse_output_btn.clicked.connect(self._browse_output_dir); self.process_btn.clicked.connect(self._start_processing); self.save_btn.clicked.connect(self._start_saving)
        self.file_table.customContextMenuRequested.connect(self._show_context_menu); self.file_table.installEventFilter(self)
        self.file_table.itemSelectionChanged.connect(self._on_selection_changed)

    def eventFilter(self, source, event):
        if source is self.file_table and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            selected_items = self.file_table.selectedItems()
            if selected_items:
                row = selected_items[0].row()
                if self.file_data[row]['status'] in ['processed', 'saved']: self._play_processed_audio(row)
                else: self._play_original_audio(row)
                return True
        return super().eventFilter(source, event)

    def add_files_to_list(self, filepaths):
        for path in filepaths:
            if any(item['original_path'] == path for item in self.file_data): continue
            display_text = os.path.basename(path)
            new_item_data = {'original_path': path, 'status': 'pending', 'temp_path': None, 'log': '待处理', 'display_text': display_text}
            self.file_data.append(new_item_data)
            row = self.file_table.rowCount(); self.file_table.insertRow(row); self._update_table_row(row)

    def _update_table_row(self, row):
        if row >= len(self.file_data): return
        item_data = self.file_data[row]
        
        # 更新第一列：文件名和状态图标
        table_item = QTableWidgetItem(item_data['display_text'])
        status = item_data['status']; status_text = {'pending': '待处理', 'processed': '已处理, 未保存', 'error': '处理失败', 'saved': '已保存'}.get(status, '未知')
        icon = self.icons.get('success') if status == 'processed' else self.icons.get(status, self.icons['pending'])
        table_item.setIcon(icon)
        tooltip_parts = [f"<b>原始路径:</b> {item_data['original_path']}", "<hr>", f"<b>状态:</b> {status_text}"]
        if item_data.get('temp_path'): tooltip_parts.append(f"<b>预览文件:</b> {item_data['temp_path']}")
        tooltip_parts.append("<hr><b>处理日志:</b><br>" + item_data.get('log', '无'))
        table_item.setToolTip("<br>".join(tooltip_parts))
        self.file_table.setItem(row, 0, table_item)
        
        # [核心修改] 更新第二列：波形图
        waveform_widget = WaveformWidget(self)
        self.file_table.setCellWidget(row, 1, waveform_widget)
        if item_data['status'] in ['processed', 'saved'] and item_data.get('temp_path'):
            waveform_widget.set_waveform_data(item_data['temp_path'])
        self.file_table.resizeRowToContents(row)
        self._on_selection_changed()

    def _get_options(self):
        return {'trim_silence_enabled': self.trim_silence_check.isChecked(), 'trim_threshold_db': self.trim_threshold_slider.value(), 'trim_padding_ms': self.trim_padding_slider.value(), 'trim_fade_out_ms': self.trim_fade_out_slider.value(), 'resample_enabled': self.resample_check.isChecked(), 'target_sr': int(self.sr_combo.currentText()), 'convert_channels_enabled': self.convert_channels_check.isChecked(), 'normalize_enabled': self.normalize_check.isChecked()}

    def _start_processing(self):
        if not AUDIO_LIBS_AVAILABLE: QMessageBox.critical(self, "依赖缺失", "无法处理，'numpy'或'soundfile'库未安装。"); return
        if not self.file_data: QMessageBox.warning(self, "无文件", "请先添加要处理的音频文件。"); return
        for item in self.file_data: item['status'] = 'pending'; item['log'] = '正在排队...'
        for i in range(len(self.file_data)): self._update_table_row(i)
        self.progress_bar.setValue(0); self.set_ui_enabled(False)
        self.thread = QThread(); self.worker = AudioProcessorWorker(self.file_data, self._get_options(), self.temp_dir)
        self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit); self.worker.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self.set_ui_enabled(True, processing_done=True)); self.worker.file_processed.connect(self._on_file_processed)
        self.thread.start()

    def _on_file_processed(self, result):
        row = result['row'];
        if row < len(self.file_data): self.file_data[row].update(result); self._update_table_row(row); self.progress_bar.setValue(int((row + 1) / len(self.file_data) * 100))
        
    def _start_saving(self):
        output_format = self.output_format_combo.currentText()
        options = {'output_dir': self.output_dir_edit.text().strip(), 'name_template': self.name_template_edit.text().strip(), 'conflict_policy': 'overwrite' if self.overwrite_radio.isChecked() else 'rename' if self.rename_radio.isChecked() else 'skip'}
        if not options['name_template']: QMessageBox.warning(self, "模板无效", "文件名模板不能为空。"); return
        processed_files = [item for item in self.file_data if item['status'] == 'processed']
        if not processed_files: QMessageBox.information(self, "无文件可保存", "没有成功处理的文件可供保存。"); return
        saved_count = 0
        for item in processed_files:
            try:
                output_dir = options['output_dir'] or os.path.dirname(item['original_path']); os.makedirs(output_dir, exist_ok=True)
                sr = sf.info(item['temp_path']).samplerate; original_name = os.path.splitext(os.path.basename(item['original_path']))[0]
                replacements = {"{original_name}": original_name, "{samplerate}": str(sr), "{sr}": str(sr)}
                new_base_name = options['name_template'];
                for placeholder, value in replacements.items(): new_base_name = new_base_name.replace(placeholder, value)
                
                output_path = os.path.join(output_dir, f"{new_base_name}{output_format}")
                
                if os.path.exists(output_path):
                    if options['conflict_policy'] == 'skip': continue
                    elif options['conflict_policy'] == 'rename':
                        n = 1
                        while os.path.exists(output_path): output_path = os.path.join(output_dir, f"{new_base_name}_{n}{output_format}"); n += 1
                
                # [核心修改] 读取临时WAV并按指定格式保存
                data, sr = sf.read(item['temp_path'])
                sf.write(output_path, data, sr)
                
                item['status'] = 'saved'; item['log'] += f"<br>  > <font color='blue'>已保存至 {os.path.basename(output_path)}</font>"; saved_count += 1
            except Exception as e:
                item['status'] = 'error'; item['log'] += f"<br>  > <font color='red'>保存失败: {e}</font>"
            self._update_table_row(self.file_data.index(item)); QApplication.processEvents()
        QMessageBox.information(self, "保存完成", f"成功保存 {saved_count} 个文件。")

    def _show_context_menu(self, position):
        row = self.file_table.rowAt(position.y())
        if row < 0: return
        menu = QMenu(self); item_data = self.file_data[row]
        play_original_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "试听原文件"); play_original_action.triggered.connect(partial(self._play_original_audio, row))
        play_processed_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "试听处理后音频"); play_processed_action.setEnabled(item_data['status'] in ['processed', 'saved'] and item_data['temp_path'] is not None); play_processed_action.triggered.connect(partial(self._play_processed_audio, row))
        menu.addSeparator()
        open_folder_action = menu.addAction(self.icon_manager.get_icon("show_in_explorer"), "在文件浏览器中显示"); open_folder_action.triggered.connect(partial(self._open_in_explorer, row))
        menu.addSeparator()
        remove_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), "从列表中移除"); remove_action.triggered.connect(partial(self._remove_item, row))
        menu.exec_(self.file_table.viewport().mapToGlobal(position))

    def _play_original_audio(self, row):
        original_path = self.file_data[row]['original_path']
        if original_path and os.path.exists(original_path): self._robust_play_sound(original_path)
    def _play_processed_audio(self, row):
        temp_path = self.file_data[row]['temp_path']
        if temp_path and os.path.exists(temp_path): self._robust_play_sound(temp_path)
    def _robust_play_sound(self, path):
        playback_thread = threading.Thread(target=self._play_sound_task, args=(path,), daemon=False); playback_thread.start()
    def _play_sound_task(self, path):
        try: data, sr = sf.read(path); sd.play(data, sr); sd.wait()
        except Exception as e: print(f"播放失败: {e}")
    def _open_in_explorer(self, row):
        path = os.path.dirname(self.file_data[row]['original_path'])
        try:
            if sys.platform == 'win32': os.startfile(path)
            elif sys.platform == 'darwin': os.system(f'open "{path}"')
            else: os.system(f'xdg-open "{path}"')
        except Exception as e: QMessageBox.critical(self, "打开失败", f"无法打开文件夹: {e}")
    def _remove_item(self, row):
        item_data = self.file_data.pop(row)
        if item_data['temp_path'] and os.path.exists(item_data['temp_path']):
            try: os.remove(item_data['temp_path'])
            except OSError: pass
        self.file_table.removeRow(row)
    def clear_all_files(self):
        self._setup_temp_dir(); self.file_data.clear(); self.file_table.setRowCount(0); self.save_btn.setEnabled(False)
    def set_ui_enabled(self, enabled, processing_done=False):
        self.process_btn.setEnabled(enabled); self.process_btn.setText("1. 处理并生成预览" if enabled else "处理中...")
        self.save_btn.setEnabled(enabled and processing_done)
        for w in [self.add_files_btn, self.add_folder_btn, self.clear_list_btn, self.trim_silence_check, self.trim_threshold_slider, self.trim_padding_slider, self.trim_fade_out_slider, self.resample_check, self.sr_combo, self.normalize_check, self.convert_channels_check, self.output_dir_edit, self.browse_output_btn, self.name_template_edit, self.overwrite_radio, self.skip_radio, self.rename_radio, self.output_format_combo]:
            w.setEnabled(enabled)
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event):
        filepaths_to_add = []; supported_exts = ('.wav', '.mp3', '.flac', '.ogg')
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for name in files:
                        if name.lower().endswith(supported_exts): filepaths_to_add.append(os.path.join(root, name))
            elif os.path.isfile(path) and path.lower().endswith(supported_exts): filepaths_to_add.append(path)
        self.add_files_to_list(filepaths_to_add)
    def _add_files(self):
        filepaths, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", "音频文件 (*.wav *.mp3 *.flac *.ogg)");
        if filepaths: self.add_files_to_list(filepaths)
    def _add_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "选择包含音频的文件夹")
        if directory:
            filepaths_to_add = []; supported_exts = ('.wav', '.mp3', '.flac', '.ogg')
            for root, _, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(supported_exts): filepaths_to_add.append(os.path.join(root, name))
            self.add_files_to_list(filepaths_to_add)
    def _browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹");
        if directory: self.output_dir_edit.setText(directory)
    def closeEvent(self, event):
        self._cleanup_temp_folder(); super().closeEvent(event)
    def _cleanup_temp_folder(self):
        if os.path.exists(self.temp_dir): shutil.rmtree(self.temp_dir)

# ==============================================================================
# 3. 插件主入口 (无变动)
# ==============================================================================
class BatchProcessorPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
    def setup(self):
        self.audio_manager_page = getattr(self.main_window, 'audio_manager_page', None)
        if not self.audio_manager_page: print("[Batch Processor] 错误: 未找到音频管理器模块。"); return False
        setattr(self.audio_manager_page, 'batch_processor_plugin_active', self)
        print("[Batch Processor] 已向音频管理器注册。")
        return True
    def teardown(self):
        if hasattr(self, 'audio_manager_page') and hasattr(self.audio_manager_page, 'batch_processor_plugin_active'):
            delattr(self.audio_manager_page, 'batch_processor_plugin_active')
            print("[Batch Processor] 已从音频管理器注销。")
    def execute(self, **kwargs):
        initial_filepaths = kwargs.get('filepaths', None)
        if self.dialog_instance is None:
            self.dialog_instance = BatchProcessorDialog(initial_filepaths, self.main_window, self.main_window.icon_manager)
            self.dialog_instance.finished.connect(self.on_dialog_finished)
        else:
            if initial_filepaths: self.dialog_instance.add_files_to_list(initial_filepaths)
        self.dialog_instance.show(); self.dialog_instance.raise_(); self.dialog_instance.activateWindow()
    def on_dialog_finished(self):
        if self.dialog_instance: self.dialog_instance._cleanup_temp_folder()
        self.dialog_instance = None

    # [新增] 插件的新功能入口
    def execute_quick_normalize(self, filepaths):
        """
        执行一个无UI的、快速的、覆盖式的标准化流程。
        """
        if not filepaths:
            return

        # 步骤 1: 显示一个非常明确的警告信息
        count = len(filepaths)
        msg_box = QMessageBox(self.main_window)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("确认“一键标准化”操作")
        msg_box.setText(f"您确定要对 {count} 个音频文件执行“一键标准化”吗？")
        
        informative_text = (
            "此操作将执行以下处理并<b>直接覆盖原始文件</b>：<br>"
            "<ul>"
            "<li>重采样至 <b>44100 Hz</b></li>"
            "<li>转换为<b>单声道</b></li>"
            "<li>音量标准化 (RMS)</li>"
            "<li>保存为 <b>WAV</b> 格式</li>"
            "</ul>"
            "<font color='red'><b>警告：此操作不可撤销！建议先备份您的数据。</b></font>"
        )
        msg_box.setInformativeText(informative_text)
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)

        if msg_box.exec_() == QMessageBox.No:
            return

        # 步骤 2: 在执行前，重置音频管理器的播放器以释放文件句柄
        if self.audio_manager_page and hasattr(self.audio_manager_page, 'reset_player'):
            self.audio_manager_page.reset_player()
            QApplication.processEvents() # 确保事件循环处理完请求

        # 步骤 3: 设置并运行后台工作器
        from PyQt5.QtWidgets import QProgressDialog

        self.progress_dialog = QProgressDialog("正在准备处理...", "取消", 0, 100, self.main_window)
        self.progress_dialog.setWindowTitle("快速标准化")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.show()

        self.thread = QThread()
        self.worker = QuickNormalizeWorker(filepaths, self.main_window)
        self.worker.moveToThread(self.thread)

        self.progress_dialog.canceled.connect(self.worker.stop)
        self.worker.progress.connect(self.progress_dialog.setValue)
        self.worker.progress.connect(lambda val, msg: self.progress_dialog.setLabelText(msg))
        
        self.worker.finished_with_refresh_request.connect(self.on_quick_normalize_finished)

        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished_with_refresh_request.connect(self.worker.deleteLater)
        
        self.thread.start()

    # [新增] 快速标准化完成后的回调函数
    def on_quick_normalize_finished(self):
        self.thread.quit()
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        
        QMessageBox.information(self.main_window, "完成", "快速标准化处理已完成。")

        # 请求音频管理器刷新其文件列表
        if self.audio_manager_page and hasattr(self.audio_manager_page, 'populate_audio_table'):
            self.audio_manager_page.populate_audio_table()
    # [已修正] 用于自动修复单个文件的后台工作器
    def _run_auto_fix_worker(self, filepath, on_success_callback):
        """这是一个内部方法，负责启动后台线程来执行修复任务。"""
        
        # [已修正] 内部定义一个简单的工作器
        class AutoFixWorker(QObject):
            success = pyqtSignal(str) # 成功时发射新文件路径
            failure = pyqtSignal(str) # 失败时发射错误信息
            finished = pyqtSignal()   # [核心修正 2.1] 手动添加 'finished' 信号

            def __init__(self, path_to_fix):
                super().__init__()
                self.path_to_fix = path_to_fix

            def run(self):
                try:
                    # (处理逻辑保持不变)
                    backup_path = self.path_to_fix + ".bak"
                    shutil.copy2(self.path_to_fix, backup_path)
                    base_path, old_ext = os.path.splitext(self.path_to_fix)
                    target_filepath = base_path + ".wav"
                    data, sr = sf.read(self.path_to_fix)
                    if data.ndim > 1: data = np.mean(data, axis=1)
                    if sr != 44100:
                        num_samples = int(len(data) * 44100 / sr)
                        data = np.interp(np.linspace(0, len(data), num_samples), np.arange(len(data)), data)
                        sr = 44100
                    current_rms = np.sqrt(np.mean(data**2));
                    if current_rms > 1e-9: gain = 0.1 / current_rms; data = np.clip(data * gain, -1.0, 1.0)
                    sf.write(target_filepath, data, sr, format='WAV')
                    if os.path.exists(self.path_to_fix):
                        os.remove(self.path_to_fix)
                    self.success.emit(target_filepath)
                except Exception as e:
                    self.failure.emit(str(e))
                finally:
                    # [核心修正 2.2] 无论成功还是失败，最后都必须发射 'finished' 信号
                    self.finished.emit()

        # 创建并启动线程
        self.fix_thread = QThread()
        self.fix_worker = AutoFixWorker(filepath)
        self.fix_worker.moveToThread(self.fix_thread)
        
        self.fix_worker.success.connect(on_success_callback)
        self.fix_worker.failure.connect(lambda msg: print(f"自动修复失败: {msg}"))
        
        # 现在，因为 AutoFixWorker 有了 finished 信号，这些连接都可以正常工作了
        self.fix_worker.finished.connect(self.fix_thread.quit)
        self.fix_thread.finished.connect(self.fix_thread.deleteLater)
        self.fix_worker.finished.connect(self.fix_worker.deleteLater) # 这行现在是安全的
        
        self.fix_thread.started.connect(self.fix_worker.run)
        self.fix_thread.start()


    # [已修正] 插件新的公共API，供外部调用
    def execute_automatic_fix(self, filepath, on_success_callback):
        """
        当外部模块检测到无法播放的音频时，调用此方法尝试自动修复。
        它会处理用户确认和“不再询问”的逻辑。
        """
        plugin_settings = self.main_window.config.setdefault("plugin_settings", {}).setdefault("batch_processor", {})
        auto_fix_preference = plugin_settings.get("auto_fix_preference", "ask")

        if auto_fix_preference == "always_run":
            self._run_auto_fix_worker(filepath, on_success_callback)
            return

        msg_box = QMessageBox(self.main_window)
        msg_box.setIcon(QMessageBox.Question)
        msg_box.setWindowTitle("播放失败 - 自动修复？")

        # [核心修正 1.1] 将主文本设为纯文本
        msg_box.setText("无法播放文件")
        
        # [核心修正 1.2] 将包含HTML的内容放入 setInformativeText
        informative_text = (
            f"文件: <b>{os.path.basename(filepath)}</b><br><br>"
            "此文件可能格式不受支持或已损坏。<br><br>"
            "是否允许 <b>音频处理器插件</b> 尝试自动将其标准化为可播放的WAV格式？<br>"
            "<small>(此操作会创建备份文件 `.bak` 并替换原文件)</small>"
        )
        msg_box.setInformativeText(informative_text)

        dont_ask_again_cb = QCheckBox("记住我的选择，下次自动执行")
        msg_box.setCheckBox(dont_ask_again_cb)
        
        yes_btn = msg_box.addButton("是，请修复", QMessageBox.YesRole)
        no_btn = msg_box.addButton("否", QMessageBox.NoRole)
        msg_box.setDefaultButton(yes_btn)

        msg_box.exec_()

        if msg_box.clickedButton() == yes_btn:
            # 如果用户同意，检查复选框状态并保存设置
            if dont_ask_again_cb.isChecked():
                plugin_settings["auto_fix_preference"] = "always_run"
                
                # [核心修正] 直接调用主窗口的全局保存方法。
                # 因为 plugin_settings 已经是主配置的引用，
                # 我们只需要触发保存即可。
                if hasattr(self.main_window, 'save_config'):
                    self.main_window.save_config()

            # 开始后台修复工作
            self._run_auto_fix_worker(filepath, on_success_callback)