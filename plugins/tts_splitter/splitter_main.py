# --- START OF FILE plugins/tts_splitter/splitter_main.py ---

import os
import sys
import json
import re
import numpy as np
from datetime import datetime
import tempfile
import zipfile

# PyQt5 核心 UI 模块
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QSplitter, QMessageBox, QFileDialog, QTableWidget,
                             QTableWidgetItem, QHeaderView, QGroupBox, QFormLayout,
                             QSlider, QLineEdit, QComboBox, QPlainTextEdit,
                             QProgressBar, QListWidget, QListWidgetItem, QApplication,
                             QWidget, QMenu, QAbstractItemView, QRadioButton, QProgressDialog,
                             QShortcut, QSpinBox, QDialogButtonBox) # 新增导入 QSpinBox, QDialogButtonBox

# PyQt5 核心功能模块
from PyQt5.QtCore import Qt, QUrl, QEvent, pyqtSignal, QRect, QSize, QTimer, pyqtProperty
from PyQt5.QtGui import QIntValidator, QColor, QKeySequence, QPainter, QPen, QBrush, QPalette # 新增导入 QPainter, QPen, QBrush, QPalette
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# 动态导入项目特有依赖和自定义控件
try:
    from plugin_system import BasePlugin
    import librosa
    import soundfile as sf
    # 尝试导入自定义的UI组件
    from modules.custom_widgets_module import AnimatedIconButton, AnimatedSlider
    CUSTOM_WIDGETS_AVAILABLE = True
    DEPENDENCIES_MISSING = False
except ImportError as e:
    DEPENDENCIES_MISSING = True
    MISSING_ERROR_MESSAGE = str(e)
    # 如果核心依赖或自定义控件缺失，提供回退类以防止崩溃
    class BasePlugin:
        def __init__(self, *args, **kwargs): pass
        def setup(self): return False
        def teardown(self): pass
        def execute(self, **kwargs): pass
    # 如果自定义控件缺失，使用标准 Qt 控件作为回退
    class AnimatedIconButton(QPushButton):
        def __init__(self, icon_manager, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.icon_manager = icon_manager
        def setIcons(self, icon_name_on, icon_name_off=None):
            # 正确：使用 self.icon_manager，它是实例化时传入的
            # 并且调用 get_icon 方法，这是 IconManager 的标准用法
            if self.icon_manager:
                self.setIcon(self.icon_manager.get_icon(icon_name_on))
    class AnimatedSlider(QSlider): # 仅提供最基本的 QSlider 功能
        pass
    CUSTOM_WIDGETS_AVAILABLE = False


# ==============================================================================
# [重构] 波形显示控件 (WaveformWidget)
# ------------------------------------------------------------------------------
# 这是一个独立的波形显示组件，支持两种标记模式和交互。
# 解决了之前 `QProperty` 缺失的问题。
# ==============================================================================
class WaveformWidget(QWidget):
    # 定义 QProperty，以便在 QSS 中定制颜色
    position_clicked = pyqtSignal(float)
    @pyqtProperty(QColor)
    def waveformColor(self): return self._waveform_color
    @waveformColor.setter
    def waveformColor(self, color): self._waveform_color = color; self.update()

    @pyqtProperty(QColor)
    def cursorColor(self): return self._cursor_color
    @cursorColor.setter
    def cursorColor(self, color): self._cursor_color = color; self.update()
    
    @pyqtProperty(QColor)
    def selectionColor(self): return self._selection_color
    @selectionColor.setter
    def selectionColor(self, color): self._selection_color = color; self.update()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setMaximumHeight(150)
        
        # --- 核心数据模型 ---
        self.waveform_data = None       # 缩略波形数据 (numpy array)
        self.playback_ratio = 0.0       # 当前播放位置 (0.0 to 1.0)
        
        # [修改] 两种标记模式的数据结构
        self.split_markers = []      # 'split' 模式下的垂直分割线位置列表 (0.0 to 1.0)
        self.selections = []         # 'select' 模式下的选区列表 [(start_ratio, end_ratio), ...]
        self.pending_selection_start = None # 'select' 模式下，记录第一个点击的起点

        # --- 状态与颜色 ---
        self.mode = 'split' # 默认模式：'split' (分割点模式) 或 'select' (精确选择模式)
        self._waveform_color = self.palette().color(QPalette.Highlight) # 波形颜色
        self._cursor_color = QColor("#E91E63") # 播放头颜色
        self._selection_color = QColor(0, 100, 255, 60) # 精确选择模式下选区高亮颜色 (带透明度)
        self.marker_color = QColor("#FFC107") # 分割点标记线颜色
        self.pending_marker_color = QColor("#FF9800") # 精确选择模式下待定起点标记颜色
        
        self.update_tooltip() # 根据当前模式更新工具提示

    # --- 公共API ---
    def set_mode(self, mode):
        """设置波形图的交互模式：'split' (分割点) 或 'select' (精确选择)。"""
        if mode in ['split', 'select']:
            self.mode = mode
            self.pending_selection_start = None # 切换模式时清除任何待定标记
            self.update_tooltip()
            self.update() # 强制重绘以反映模式变化

    def update_tooltip(self):
        """根据当前模式更新控件的工具提示。"""
        if self.mode == 'split':
            self.setToolTip("分割点模式\n- 右键单击: 在点击位置添加一个分割点（垂直线）\n- 中键单击: 移除距离指针最近的分割点")
        else: # select mode
            self.setToolTip("精确选择模式\n- 左键单击: 标记一个新片段的起点\n- 再次左键单击: 标记该片段的终点，完成一个选择区域\n- 中键单击: 移除距离指针最近的标记点或选区")
            
    def set_waveform(self, audio_data, sr):
        """加载音频数据以绘制波形。"""
        self.clear() # 清除所有旧数据和标记
        if audio_data is None: 
            self.update() # 强制重绘为空状态
            return

        num_samples = len(audio_data)
        # 将波形数据下采样到适合UI显示的点数 (例如，每像素2个点)
        target_points = self.width() * 2 
        
        # 如果音频太短或控件宽度为0，则直接使用原始数据（或等待 resizeEvent）
        if num_samples <= target_points or target_points <= 0: 
            self.waveform_data = audio_data
        else:
            step = num_samples // target_points
            # 简化波形：取每个窗口的最大绝对值作为显示点
            peak_data = [np.max(np.abs(audio_data[i:i+step])) for i in range(0, num_samples, step)]
            self.waveform_data = np.array(peak_data)
        self.update() # 强制重绘波形

    def update_playback_position(self, ratio):
        """更新播放头位置（0.0到1.0的比例）。"""
        self.playback_ratio = ratio
        self.update() # 强制重绘以更新播放头

    def add_split_marker(self, ratio):
        """在'split'模式下添加一个分割点。"""
        self.split_markers.append(ratio)
        self.split_markers.sort() # 保持排序
        self.update()

    def clear(self):
        """清除所有波形数据、播放位置和标记点。"""
        self.waveform_data = None
        self.playback_ratio = 0.0
        self.split_markers = []
        self.selections = []
        self.pending_selection_start = None
        self.update()

    # --- 交互事件处理 ---
    def mousePressEvent(self, event):
        """处理鼠标按下事件，用于标记和寻轨。"""
        ratio = event.x() / self.width() # 计算点击位置相对于宽度的比例
        if not (0 <= ratio <= 1): return # 确保点击在有效区域内

        # 模式1: 分割点模式 (右键单击添加分割点)
        if event.button() == Qt.RightButton and self.mode == 'split':
            if ratio not in self.split_markers: # 避免重复添加
                self.split_markers.append(ratio)
                self.split_markers.sort()
                self.update()
                self.parent().update_split_preview() # 通知父级更新预览表格

        # 模式2: 精确选择模式 (左键单击标记选区)
        elif event.button() == Qt.LeftButton and self.mode == 'select':
            if self.pending_selection_start is None:
                # 第一次点击：标记起点
                self.pending_selection_start = ratio
            else:
                # 第二次点击：标记终点，完成一个选区
                start = min(self.pending_selection_start, ratio)
                end = max(self.pending_selection_start, ratio)
                self.selections.append((start, end))
                self.selections.sort() # 保持排序
                self.pending_selection_start = None # 清除待定起点
            self.update()
            self.parent().update_split_preview() # 通知父级更新预览表格

        # 模式3: 移除标记点 (中键单击)
        elif event.button() == Qt.MiddleButton:
            self._find_and_remove_closest_marker(ratio)
        
        # 将鼠标按下事件继续传递给父级，以便它进行播放寻轨
        self.position_clicked.emit(ratio)
    
    def _find_and_remove_closest_marker(self, click_ratio):
        """查找并移除距离点击位置最近的标记点或选区。"""
        closest_info = {'dist': float('inf'), 'type': None, 'index': -1, 'sub_index': -1} # sub_index for start/end of selection

        # 检查分割点模式下的垂直分割线
        for i, marker_ratio in enumerate(self.split_markers):
            dist = abs(marker_ratio - click_ratio)
            if dist < closest_info['dist']:
                closest_info = {'dist': dist, 'type': 'split', 'index': i}
        
        # 检查精确选择模式下的选区边界 (起点和终点)
        for i, (start, end) in enumerate(self.selections):
            dist_start = abs(start - click_ratio)
            if dist_start < closest_info['dist']:
                closest_info = {'dist': dist_start, 'type': 'selection', 'index': i, 'sub_index': 0}
            dist_end = abs(end - click_ratio)
            if dist_end < closest_info['dist']:
                closest_info = {'dist': dist_end, 'type': 'selection', 'index': i, 'sub_index': 1}
        
        # 如果找到了足够近的标记点 (容忍度可调，这里隐式为像素级的距离)
        if closest_info['type'] is not None and closest_info['dist'] * self.width() < 10: # 10像素容忍度
            if closest_info['type'] == 'split':
                del self.split_markers[closest_info['index']]
            elif closest_info['type'] == 'selection':
                del self.selections[closest_info['index']]
            
            self.update() # 强制重绘以移除标记
            self.parent().update_split_preview() # 通知父级更新预览表格

    # --- 绘图逻辑 ---
    def paintEvent(self, event):
        """绘制波形、标记点和播放头。"""
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制背景
        painter.fillRect(self.rect(), self.palette().color(QPalette.Base))
        
        # 如果没有波形数据，显示提示文本
        if self.waveform_data is None or len(self.waveform_data) == 0: 
            painter.setPen(self.palette().color(QPalette.Mid))
            painter.drawText(self.rect(), Qt.AlignCenter, "加载波形中...")
            return

        w, h = self.width(), self.height()
        half_h = h / 2
        
        # 归一化波形数据以适应高度
        max_val = np.max(self.waveform_data) if len(self.waveform_data) > 0 else 1.0
        if max_val == 0: max_val = 1.0

        # 绘制波形本身
        painter.setPen(QPen(self._waveform_color, 1))
        for i, val in enumerate(self.waveform_data):
            x = int(i * w / len(self.waveform_data))
            y_offset = (val / max_val) * half_h
            painter.drawLine(x, int(half_h - y_offset), x, int(half_h + y_offset))

        # 根据当前模式绘制标记点或选区
        if self.mode == 'split':
            # 绘制垂直分割线
            painter.setPen(QPen(self.marker_color, 2, Qt.DashLine))
            for marker_ratio in self.split_markers:
                x = int(marker_ratio * w)
                painter.drawLine(x, 0, x, h)
        else: # 'select' mode (精确选择模式)
            # 绘制已完成的选区高亮
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self._selection_color))
            for start_ratio, end_ratio in self.selections:
                x1 = int(start_ratio * w)
                x2 = int(end_ratio * w)
                painter.drawRect(QRect(x1, 0, x2 - x1, h))
            
            # 绘制待定的起点标记
            if self.pending_selection_start is not None:
                painter.setPen(QPen(self.pending_marker_color, 2, Qt.DashLine))
                x = int(self.pending_selection_start * w)
                painter.drawLine(x, 0, x, h)

        # 绘制播放头（红色垂直线）
        painter.setPen(QPen(self._cursor_color, 2))
        pos_x = int(self.playback_ratio * w)
        painter.drawLine(pos_x, 0, pos_x, h)

    def resizeEvent(self, event):
        """窗口大小改变时，重新渲染波形以适应新尺寸。"""
        super().resizeEvent(event)
        # 只有当有音频数据时才需要重新渲染波形，以适应新宽度
        if self.waveform_data is not None:
            # 简单地重新调用 set_waveform 即可，它会根据新宽度重新采样
            # 为了避免循环，直接访问父级的 audio_data 和 sr
            if hasattr(self.parent(), 'audio_data') and hasattr(self.parent(), 'sr'):
                self.set_waveform(self.parent().audio_data, self.parent().sr)


# ==============================================================================
# [重构] 手动分割对话框 (ManualSplitDialog)
# ------------------------------------------------------------------------------
# 这是一个独立的对话框，用于手动精细调整音频分割点。
# 具备完善的播放控制和模式切换功能。
# ==============================================================================
class ManualSplitDialog(QDialog):
    """
    一个用于手动精细分割音频片段的对话框。
    提供两种交互模式：分割点模式（右键添加）和精确选择模式（左键选择），
    并拥有与主程序音频管理器一致的流畅播放体验。
    """
    def __init__(self, audio_data, sr, original_words, icon_manager, parent=None):
        super().__init__(parent)
        self.audio_data = audio_data          # 传入的完整音频数据 (numpy array)
        self.sr = sr                          # 采样率
        self.original_words = original_words  # 对应此片段的原始单词列表
        self.icon_manager = icon_manager      # 图标管理器实例
        self._initial_layout_done = False
        self.temp_audio_file = None           # 用于 QMediaPlayer 加载的临时文件路径
        self.player = QMediaPlayer()
        # [解决问题1] 提高播放器位置更新频率，使得进度条更流畅
        self.player.setNotifyInterval(16) # 约 60Hz 刷新率
        
        self.segment_stop_timer = QTimer(self) # 用于精确试听片段的定时器
        self.segment_stop_timer.setSingleShot(True) # 定时器只触发一次

        self.setWindowTitle("手动精细分割"); self.setMinimumSize(800, 600)
        
        self._init_ui()
        self._connect_signals()
        self._setup_shortcuts()
        
        self._load_audio_into_player() # 在UI初始化后立即加载音频到播放器
        self.update_split_preview()    # 初始更新预览表格

    def _init_ui(self):
        """构建对话框的用户界面。"""
        layout = QVBoxLayout(self)

        # --- 1. 操作模式选择 (GroupBox + RadioButtons) ---
        mode_group = QGroupBox("选择操作模式"); mode_layout = QHBoxLayout(mode_group)
        self.split_mode_radio = QRadioButton("分割点模式 (右键添加)"); self.split_mode_radio.setChecked(True) # 默认选中
        self.select_mode_radio = QRadioButton("精确选择模式 (左键选择)")
        mode_layout.addWidget(self.split_mode_radio); mode_layout.addWidget(self.select_mode_radio)
        
        # --- 2. 波形图和播放控制栏 (使用自定义控件) ---
        self.waveform_widget = WaveformWidget(self) # 传入 self 作为父级，方便 WaveformWidget 访问数据
        
        playback_layout = QHBoxLayout(); playback_layout.setSpacing(10) # 增加间距
        # 播放/暂停按钮 (AnimatedIconButton)
        self.play_btn = AnimatedIconButton(self.icon_manager, self)
        self.play_btn.setIcons("play_audio", "pause") # 设置播放和暂停图标
        self.play_btn.setToolTip("播放/暂停 (空格键)"); self.play_btn.setMinimumSize(QSize(40, 40)) # 固定大小
        
        # 进度条滑块 (AnimatedSlider)
        self.progress_slider = AnimatedSlider(Qt.Horizontal)
        self.progress_slider.setObjectName("PlaybackSlider"); self.progress_slider.setToolTip("显示当前播放进度，可拖动或点击以跳转。")
        
        # 时长显示标签
        self.duration_label = QLabel("00:00.00 / 00:00.00")
        
        playback_layout.addWidget(self.play_btn); playback_layout.addWidget(self.progress_slider, 1); playback_layout.addWidget(self.duration_label)

        # --- 3. 预览表格 (QTableWidget) ---
        self.preview_table = QTableWidget(); self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["#", "对应单词 (可编辑)", "时长 (s)"]); self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.preview_table.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单

        # --- 4. 底部确认/取消按钮 (QDialogButtonBox) ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        
        # --- 组装主布局 ---
        layout.addWidget(mode_group)
        layout.addWidget(QLabel("提示：拖动进度条或点击波形图可寻轨。"))
        layout.addWidget(self.waveform_widget)
        layout.addLayout(playback_layout)
        layout.addWidget(self.preview_table, 1) # 让表格占据主要垂直空间
        layout.addWidget(self.button_box)

    def _connect_signals(self):
        """连接所有UI控件的信号与槽。"""
        # 模式选择信号
        self.split_mode_radio.toggled.connect(self._on_mode_changed)

        # 波形图交互信号
        # 右键点击事件由 WaveformWidget 内部处理并直接修改 split_markers
        # 左键点击事件由 WaveformWidget 内部处理并直接修改 selections
        self.waveform_widget.position_clicked.connect(self.seek_by_ratio) # 波形图点击寻轨
        
        # 预览表格信号
        self.preview_table.customContextMenuRequested.connect(self.show_table_context_menu)
        
        # 播放器控制信号
        self.play_btn.toggled.connect(self.toggle_playback) # 播放/暂停按钮
        self.progress_slider.sliderMoved.connect(self.player.setPosition) # 进度条拖动寻轨
        
        # 播放器状态更新 UI 信号
        self.player.positionChanged.connect(self.update_progress) # 位置变化更新进度条和时间标签
        self.player.durationChanged.connect(self.update_duration) # 总时长变化更新进度条范围和时间标签
        self.player.stateChanged.connect(self.on_player_state_changed) # 播放状态变化更新播放按钮图标
        
        # 精确试听片段的定时器
        self.segment_stop_timer.timeout.connect(self.stop_playback)

        # 确认/取消按钮
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

    def _setup_shortcuts(self):
        """设置对话框内的快捷键。"""
        # 空格键：播放/暂停
        QShortcut(QKeySequence(Qt.Key_Space), self, self.play_btn.toggle)

    def _on_mode_changed(self, checked):
        """当模式切换时，更新 WaveformWidget 的模式并刷新预览。"""
        if self.split_mode_radio.isChecked():
            self.waveform_widget.set_mode('split')
        else: # self.select_mode_radio.isChecked()
            self.waveform_widget.set_mode('select')
        
        self.update_split_preview() # 模式切换后，强制更新预览表格

    # --- 音频加载与播放核心逻辑 ---
    def _load_audio_into_player(self):
        """
        将整个音频片段（此对话框所处理的）加载到 QMediaPlayer 中。
        使用临时文件以兼容所有 QMediaPlayer 支持的格式。
        """
        try:
            # 清理旧的临时文件（如果存在）并释放播放器句柄
            if self.temp_audio_file and os.path.exists(self.temp_audio_file):
                self.player.setMedia(QMediaContent()) # 释放文件句柄
                os.remove(self.temp_audio_file)

            # 创建新的临时文件并写入音频数据
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            sf.write(path, self.audio_data, self.sr)
            self.temp_audio_file = path
            
            # 将临时文件设置为播放器的媒体内容
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self.temp_audio_file)))
            # 将音频数据传递给波形图组件以进行绘制
            self.waveform_widget.set_waveform(self.audio_data, self.sr)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载音频到播放器失败: {e}")
            # 禁用播放相关UI，防止进一步错误
            self.play_btn.setEnabled(False)
            self.progress_slider.setEnabled(False)

    def toggle_playback(self, checked):
        """主播放按钮的槽函数，控制播放和暂停。"""
        # 如果正在进行精确片段试听，则先停止它
        if self.segment_stop_timer.isActive():
            self.stop_playback() 
        
        if checked:
            self.player.play()
        else:
            self.player.pause()

    def stop_playback(self):
        """一个统一的停止播放方法，停止播放器和定时器。"""
        self.segment_stop_timer.stop() # 停止精确试听定时器
        self.player.stop() # 停止播放器 (会触发 stateChanged -> StoppedState)

    def on_player_state_changed(self, state):
        """根据播放器状态更新UI（如播放按钮图标）。"""
        # 阻塞信号以避免递归触发
        self.play_btn.blockSignals(True)
        if state == QMediaPlayer.PlayingState:
            self.play_btn.setChecked(True)
        else: # PausedState or StoppedState
            self.play_btn.setChecked(False)
            # 如果是自然停止（非暂停），将播放头重置到0
            if state == QMediaPlayer.StoppedState and not self.segment_stop_timer.isActive():
                self.player.setPosition(0) # 播放到自然结束，回到开头
        self.play_btn.blockSignals(False)

    def update_progress(self, position):
        """播放器位置变化时，更新进度条和时间标签。"""
        # 如果用户正在手动拖动滑块，则不响应播放器位置更新
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        
        # 更新时间标签 (当前位置 / 总时长)
        total_duration = self.player.duration()
        self.duration_label.setText(f"{self.format_time(position)} / {self.format_time(total_duration)}")
        
        # 更新波形图上的播放头位置
        if total_duration > 0:
            self.waveform_widget.update_playback_position(position / total_duration)

    def update_duration(self, duration):
        """播放器总时长变化时，更新滑块范围和时间标签。"""
        self.progress_slider.setRange(0, duration)
        # 立即更新时间显示，确保总时长正确
        self.update_progress(self.player.position()) 

    def seek_by_ratio(self, ratio):
        """响应波形图点击，跳转播放位置。"""
        if self.player.duration() > 0:
            target_position = int(ratio * self.player.duration())
            self.player.setPosition(target_position)
            # 如果播放器是暂停状态，跳转后保持暂停
            if not self.play_btn.isChecked():
                self.player.pause()

    def play_segment(self, start_sample, end_sample):
        """
        【核心改进】精确试听表格中一个片段。
        直接设置播放器的起止点和定时器，避免创建临时文件，实现即时预览。
        """
        if self.player.duration() <= 0:
            QMessageBox.warning(self, "播放错误", "音频尚未加载完成或无效。")
            return

        # 将样本点转换为毫秒
        start_ms = int(start_sample / self.sr * 1000)
        end_ms = int(end_sample / self.sr * 1000)
        duration_ms = end_ms - start_ms

        if duration_ms <= 0: return
        
        # 标记正在播放片段，防止 on_player_state_changed 意外重置播放头
        self.is_playing_segment = True
        
        # 设置播放位置和播放
        self.player.setPosition(start_ms)
        self.player.play()
        
        # 启动定时器，在片段结束时自动停止播放
        self.segment_stop_timer.start(duration_ms)

    # --- 预览表格和结果处理 ---
    def update_split_preview(self):
        """
        根据 WaveformWidget 的当前模式（分割点或精确选择）和标记，
        更新预览表格中的片段列表。
        """
        self.preview_table.setRowCount(0) # 清空表格
        
        segments_data = [] # 存储 (start_ratio, end_ratio) 的列表
        
        if self.waveform_widget.mode == 'split':
            # 分割点模式：基于垂直线生成片段
            all_points = sorted([0.0] + self.waveform_widget.split_markers + [1.0])
            for i in range(len(all_points) - 1):
                segments_data.append((all_points[i], all_points[i+1]))
        else: # 'select' mode (精确选择模式)
            # 精确选择模式：直接使用已标记的选区
            segments_data = self.waveform_widget.selections
        
        self.preview_table.setRowCount(len(segments_data))

        for i, (start_ratio, end_ratio) in enumerate(segments_data):
            # 将比例转换为样本点
            start_sample = int(start_ratio * len(self.audio_data))
            end_sample = int(end_ratio * len(self.audio_data))
            duration_s = (end_sample - start_sample) / self.sr # 计算时长（秒）

            # 填充表格行
            item_num = QTableWidgetItem(str(i + 1))
            item_num.setFlags(item_num.flags() & ~Qt.ItemIsEditable) # 序号不可编辑
            
            # 尝试用原始单词填充，如果数量不匹配则用占位符
            word = self.original_words[i] if i < len(self.original_words) else f"片段_{i+1}"
            item_word = QTableWidgetItem(word) # 单词可编辑

            item_duration = QTableWidgetItem(f"{duration_s:.3f}")
            item_duration.setFlags(item_duration.flags() & ~Qt.ItemIsEditable) # 时长不可编辑

            self.preview_table.setItem(i, 0, item_num)
            self.preview_table.setItem(i, 1, item_word)
            self.preview_table.setItem(i, 2, item_duration)

    def show_table_context_menu(self, pos):
        """表格行的右键上下文菜单（目前只提供试听）。"""
        row = self.preview_table.rowAt(pos.y())
        if row == -1: return

        menu = QMenu(self)
        play_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "试听此片段")
        
        action = menu.exec_(self.preview_table.mapToGlobal(pos))
        
        if action == play_action:
            # 根据当前模式获取片段的起止样本点
            start_sample, end_sample = 0, 0
            if self.waveform_widget.mode == 'split':
                all_points = sorted([0.0] + self.waveform_widget.split_markers + [1.0])
                start_ratio, end_ratio = all_points[row], all_points[row+1]
                start_sample = int(start_ratio * len(self.audio_data))
                end_sample = int(end_ratio * len(self.audio_data))
            else: # 'select' mode
                start_ratio, end_ratio = self.waveform_widget.selections[row]
                start_sample = int(start_ratio * len(self.audio_data))
                end_sample = int(end_ratio * len(self.audio_data))
            
            self.play_segment(start_sample, end_sample)
    def showEvent(self, event):
        """
        重写 showEvent。此方法在窗口即将显示时被调用。
        这是执行一次性布局调整的最佳时机。
        """
        # 首先，必须调用父类的实现
        super().showEvent(event)
        
        # --- [核心修复-步骤2] ---
        # 仅在第一次显示对话框时执行以下逻辑
        if not self._initial_layout_done:
            # 1. 让所有非拉伸列自动调整到内容的最佳宽度
            self.preview_table.resizeColumnsToContents()
            
            # 2. 强制声明中间列应该占据所有剩余空间
            self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            
            # 3. 设置标志位，防止后续重复执行
            self._initial_layout_done = True
    def get_results(self):
        """
        当用户点击“确定”时，返回最终的分割结果。
        结果包括：是否成功、新的相对分割点（样本点），和新的单词列表。
        """
        new_relative_splits = []
        if self.waveform_widget.mode == 'split':
            # 分割点模式：基于分割点比例计算实际样本点
            all_points = sorted([0.0] + self.waveform_widget.split_markers + [1.0])
            for i in range(len(all_points) - 1):
                start_sample = int(all_points[i] * len(self.audio_data))
                end_sample = int(all_points[i+1] * len(self.audio_data))
                new_relative_splits.append((start_sample, end_sample))
        else: # 'select' mode
            # 精确选择模式：直接使用选区比例计算实际样本点
            for start_ratio, end_ratio in self.waveform_widget.selections:
                start_sample = int(start_ratio * len(self.audio_data))
                end_sample = int(end_ratio * len(self.audio_data))
                new_relative_splits.append((start_sample, end_sample))

        num_segments = len(new_relative_splits)
        # 确保表格内容与实际分割结果一致（用户可能未点击刷新）
        if self.preview_table.rowCount() != num_segments: 
            self.update_split_preview() # 强制更新以同步表格
            
        new_words = [self.preview_table.item(i, 1).text() for i in range(num_segments)]
        
        # 结果验证
        if not all(new_words):
            QMessageBox.warning(self, "输入无效", "所有片段都必须有对应的单词/文件名。"); return False, None, None
        if len(new_words) != len(new_relative_splits):
            QMessageBox.warning(self, "数量不匹配", "最终生成的片段数与单词数不匹配，请检查。"); return False, None, None

        return True, new_relative_splits, new_words

    # --- 辅助方法 ---
    def format_time(self, ms):
        """将毫秒数格式化为 MM:SS.ms (例如 00:05.123) 的字符串。"""
        if ms is None or ms < 0: return "00:00.00"
        s_float = ms / 1000.0
        m = int(s_float // 60)
        s = s_float % 60
        return f"{m:02d}:{s:05.2f}" # 格式化为两位分钟，小数点后两位的秒

    def closeEvent(self, event):
        """对话框关闭时，停止播放器并清理临时文件。"""
        self.stop_playback() # 停止播放
        # 清理临时文件
        if self.temp_audio_file and os.path.exists(self.temp_audio_file):
            try:
                self.player.setMedia(QMediaContent()) # 释放文件句柄
                os.remove(self.temp_audio_file)
            except Exception as e:
                print(f"清理临时文件失败: {e}", file=sys.stderr)
        super().closeEvent(event)


# ==============================================================================
# 插件主类 (TtsSplitterPlugin)
# ------------------------------------------------------------------------------
# 插件的入口点，负责注册到主程序和管理 SplitterDialog 的生命周期。
# ==============================================================================
class TtsSplitterPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.splitter_dialog = None

    def setup(self):
        """插件启用时调用，检查依赖并注册自身到词表编辑器。"""
        if DEPENDENCIES_MISSING:
            print(f"[错误][TTS 分割器]: 缺少核心依赖: {MISSING_ERROR_MESSAGE}", file=sys.stderr)
            return False
        
        # 将自身注册为词表编辑器的“钩子”，以便从词表编辑器中启动
        self.wordlist_editor_page = getattr(self.main_window, 'wordlist_editor_page', None)
        if self.wordlist_editor_page:
            setattr(self.wordlist_editor_page, 'tts_splitter_plugin_active', self)
            print("TTS 批量分割器已向词表编辑器注册。")
        
        return True

    def teardown(self):
        """插件禁用时调用，清理注册和关闭对话框。"""
        # 从词表编辑器中注销自身
        if hasattr(self, 'wordlist_editor_page') and hasattr(self.wordlist_editor_page, 'tts_splitter_plugin_active'):
            delattr(self.wordlist_editor_page, 'tts_splitter_plugin_active')
            print("TTS 批量分割器已从词表编辑器注销。")

        # 关闭可能打开的对话框
        if self.splitter_dialog:
            self.splitter_dialog.close()
        print("TTS 批量分割器插件已卸载。")

    def execute(self, **kwargs):
        """当插件被执行时调用，显示 TTS 分割器主对话框。"""
        if DEPENDENCIES_MISSING:
            QMessageBox.critical(self.main_window, "依赖缺失", f"无法启动 TTS 批量分割器，缺少以下核心依赖：\n\n{MISSING_ERROR_MESSAGE}\n\n请通过 pip 安装它们。")
            return
            
        # 确保对话框是单例模式
        if self.splitter_dialog is None:
            self.splitter_dialog = SplitterDialog(self.main_window)
            # 对话框关闭时，将实例引用设为 None
            self.splitter_dialog.finished.connect(lambda: setattr(self, 'splitter_dialog', None))
        
        # 接收并加载传入的词表路径 (如果从词表编辑器启动)
        wordlist_path = kwargs.get('wordlist_path')
        if wordlist_path:
            self.splitter_dialog.load_wordlist_from_path(wordlist_path)

        # 显示对话框并使其置顶
        self.splitter_dialog.show()
        self.splitter_dialog.raise_()
        self.splitter_dialog.activateWindow()


# ==============================================================================
# 分割器主对话框 (SplitterDialog)
# ------------------------------------------------------------------------------
# 负责自动分割、参数设置、预览和调用手动分割。
# ==============================================================================
class SplitterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager
        
        self.audio_filepath, self.audio_data, self.audio_sr = None, None, None
        self.wordlist_path, self.word_list = None, []
        self.split_points = [] # 存储自动分割出的所有片段的起止样本点 (绝对值)
        
        self.player = QMediaPlayer() # 用于试听自动分割片段的播放器
        self.temp_audio_file = None  # 用于片段试听的临时文件

        self.setWindowTitle("TTS 分割器")
        self.setMinimumSize(1000, 700)
        self._init_ui()
        self._connect_signals()
        self._add_tooltips()

    def _init_ui(self):
        """
        [v1.6 - 三栏布局重构版]
        构建一个左、中、右三栏布局的UI，以优化操作流程和视觉层次。
        - 左栏: 文件加载与输出设置 (输入)
        - 中栏: 实时预览与验证 (核心交互区)
        - 右栏: 分割参数、处理与日志 (控制与反馈)
        """
        main_layout = QVBoxLayout(self)
        
        # --- 1. 创建主水平分割器 (QSplitter) ---
        # 这个分割器将包含三个主要的垂直面板。
        main_splitter = QSplitter(Qt.Horizontal)

        # ======================================================================
        #  左侧面板 (输入区)
        # ======================================================================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setMinimumWidth(300) # 为左侧栏设置一个合理的最小宽度

        # --- GroupBox: 1. 加载文件 ---
        load_group = QGroupBox("1. 加载文件")
        load_form = QFormLayout(load_group)
        self.audio_select_btn = QPushButton("选择长音频文件...")
        self.audio_label = QLabel("未选择")
        self.wordlist_select_btn = QPushButton("选择对应的词表...")
        self.wordlist_label = QLabel("未选择")
        load_form.addRow(self.audio_select_btn)
        load_form.addRow(self.audio_label)
        load_form.addRow(self.wordlist_select_btn)
        load_form.addRow(self.wordlist_label)
        self.copy_for_tts_btn = QPushButton("复制为TTS格式")
        self.copy_for_tts_btn.setIcon(self.icon_manager.get_icon("copy"))
        self.copy_for_tts_btn.setEnabled(False)
        load_form.addRow(self.copy_for_tts_btn)

        # --- GroupBox: 2. 输出设置 ---
        output_group = QGroupBox("2. 输出设置")
        output_layout = QVBoxLayout(output_group)
        output_layout.addWidget(QLabel("输出文件夹:"))
        self.output_dir_input = QLineEdit()
        self.output_dir_btn = QPushButton("浏览...")
        output_dir_layout = QHBoxLayout()
        output_dir_layout.addWidget(self.output_dir_input)
        output_dir_layout.addWidget(self.output_dir_btn)
        output_layout.addLayout(output_dir_layout)
        output_layout.addWidget(QLabel("输出格式:"))
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["WAV (推荐)", "MP3"])
        output_layout.addWidget(self.output_format_combo)

        # 组装左侧面板
        left_layout.addWidget(load_group)
        left_layout.addWidget(output_group)
        left_layout.addStretch() # 添加一个弹簧，将内容推到顶部

        # ======================================================================
        #  中间面板 (核心预览区)
        # ======================================================================
        center_panel = QWidget()
        center_panel.setMinimumWidth(400)
        center_layout = QVBoxLayout(center_panel)
        
        # --- GroupBox: 实时预览与验证 ---
        preview_group = QGroupBox("实时预览与验证")
        preview_layout = QVBoxLayout(preview_group)
        self.match_status_label = QLabel("请加载文件并设置参数")
        self.match_status_label.setObjectName("StatusLabel")
        self.match_status_label.setAlignment(Qt.AlignCenter)
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["片段 #", "对应单词 (可编辑)", "时长 (秒)"])
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        preview_layout.addWidget(self.match_status_label)
        preview_layout.addWidget(self.preview_table)
        
        # 组装中间面板
        center_layout.addWidget(preview_group)

        # ======================================================================
        #  右侧面板 (控制与反馈区)
        # ======================================================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_panel.setMinimumWidth(300) # 为右侧栏设置一个合理的最小宽度

        # --- GroupBox: 3. 分割参数 (从旧左侧栏移来) ---
        params_group = QGroupBox("3. 分割参数 (实时预览)")
        params_layout = QVBoxLayout(params_group)
        mode_layout = QHBoxLayout()
        self.precise_mode_radio = QRadioButton("精确模式")
        self.full_mode_radio = QRadioButton("完整模式")
        self.precise_mode_radio.setChecked(True)
        mode_layout.addWidget(self.precise_mode_radio)
        mode_layout.addWidget(self.full_mode_radio)
        params_layout.addLayout(mode_layout)
        
        params_form = QFormLayout()
        params_form.setContentsMargins(0, 5, 0, 0)
        params_form.addRow(QLabel("静音阈值:"))
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(-60, -20)
        self.threshold_slider.setValue(-40)
        self.threshold_label = QLabel(f"{self.threshold_slider.value()} dB")
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(self.threshold_slider)
        threshold_layout.addWidget(self.threshold_label)
        params_form.addRow(threshold_layout)
        
        self.min_silence_input = QLineEdit("300")
        self.min_silence_input.setValidator(QIntValidator(50, 5000))
        self.offset_input = QLineEdit("50")
        self.offset_input.setValidator(QIntValidator(0, 1000))
        params_form.addRow("最小静音时长 (ms):", self.min_silence_input)
        params_form.addRow("分割点偏移 (ms):", self.offset_input)
        params_layout.addLayout(params_form)
        
        self.recommend_btn = QPushButton("智能推荐参数")
        self.recommend_btn.setIcon(self.icon_manager.get_icon("auto_detect"))
        self.recommend_btn.setEnabled(False)
        params_layout.addWidget(self.recommend_btn, 0, Qt.AlignRight)

        # --- GroupBox: 4. 处理与日志 (从旧右侧栏移来) ---
        process_group = QGroupBox("4. 处理与日志")
        process_layout = QVBoxLayout(process_group)
        self.process_btn = QPushButton("开始处理")
        self.process_btn.setObjectName("AccentButton")
        self.process_btn.setIcon(self.icon_manager.get_icon("start_session"))
        self.process_btn.setEnabled(False)
        self.log_display = QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.progress_bar = QProgressBar()
        
        process_layout.addWidget(self.process_btn)
        process_layout.addWidget(QLabel("日志:"))
        process_layout.addWidget(self.log_display, 1) # 让日志区域占据更多垂直空间
        process_layout.addWidget(self.progress_bar)
        
        # 组装右侧面板
        right_layout.addWidget(params_group)
        right_layout.addWidget(process_group)
        
        # ======================================================================
        #  最终组装
        # ======================================================================
        # 将三个面板添加到主分割器中
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)

        # --- 2. 设置分割器的初始尺寸比例 ---
        # 这是一个经验法则，可以根据需要调整。
        # 这里我们将 左:中:右 的比例设置为 1:2:1
        # 但由于 Stretch Factor 是相对的，我们可以用更大的数字以便微调
        main_splitter.setStretchFactor(0, 2) # 左侧栏
        main_splitter.setStretchFactor(1, 5) # 中间栏 (核心区域，给更多空间)
        main_splitter.setStretchFactor(2, 2) # 右侧栏

        # 将主分割器添加到对话框的主布局中
        main_layout.addWidget(main_splitter)
    
    def _connect_signals(self):
        """连接所有UI控件的信号与槽。"""
        # 文件加载与输出
        self.audio_select_btn.clicked.connect(self.select_audio_file)
        self.wordlist_select_btn.clicked.connect(self.select_wordlist_file)
        self.output_dir_btn.clicked.connect(self.select_output_dir)
        self.process_btn.clicked.connect(self.run_processing)
        
        # 分割参数实时预览
        self.threshold_slider.valueChanged.connect(self.on_param_changed)
        self.min_silence_input.textChanged.connect(self.on_param_changed)
        self.offset_input.textChanged.connect(self.on_param_changed)
        self.precise_mode_radio.toggled.connect(self.on_param_changed)
        self.full_mode_radio.toggled.connect(self.on_param_changed)
        
        # 预览表格交互
        self.preview_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_table.customContextMenuRequested.connect(self.show_preview_context_menu)
        self.preview_table.installEventFilter(self) # 用于处理键盘事件 (如回车播放)

        # 智能推荐与复制按钮
        self.recommend_btn.clicked.connect(self.apply_smart_params)
        self.copy_for_tts_btn.clicked.connect(self.copy_list_for_tts)

    def _add_tooltips(self):
        """为所有主要UI元素添加工具提示。"""
        self.audio_select_btn.setToolTip("选择一个包含多个词语连续录音的音频文件。")
        self.wordlist_select_btn.setToolTip("选择一个与音频内容顺序对应的标准JSON词表（支持 .json 或 .fdeck 格式）。")
        self.precise_mode_radio.setToolTip("尝试精确切分出发音部分，可能会切掉词尾的弱辅音，适用于噪音较大的环境。")
        self.full_mode_radio.setToolTip("以静音段的中心为分割点，确保每个单词的完整性，推荐用于标准TTS录音。")
        self.threshold_slider.setToolTip("定义多大的声音才不被当做静音。\n数值越小(靠左)，判断越严格，可能将弱音也视为静音。\n数值越大(靠右)，判断越宽松。")
        self.min_silence_input.setToolTip("只有当一段静音的持续时间超过此值(毫秒)，才被认为是一个有效的分割点。")
        self.offset_input.setToolTip("在每个分割点前后应用的微调(毫秒)。\n正值向外扩张，保留更多空白；负值向内收缩，切得更紧。")
        self.preview_table.setToolTip("预览分割结果。\n- 右键点击行可进行试听、删除、手动分割等操作。\n- 双击第二列可编辑对应的单词/文件名。\n- 选中行后按回车键可快速试听。")
        self.process_btn.setToolTip("当片段数与词表数完全匹配时，此按钮将启用。\n点击后将开始批量保存所有片段。")

    def eventFilter(self, source, event):
        """事件过滤器，用于捕获预览表格的回车键播放。"""
        if source is self.preview_table and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            selected_rows = self.preview_table.selectionModel().selectedRows()
            if selected_rows:
                self.play_segment(selected_rows[0].row())
                return True
        return super().eventFilter(source, event)
    
    def log(self, message):
        """向日志显示区添加时间戳信息。"""
        self.log_display.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}"); QApplication.processEvents()

    # --- 智能参数推荐 ---
    def _calculate_smart_params(self):
        """分析音频并返回推荐的静音阈值和最小静音时长。"""
        if self.audio_data is None or self.audio_sr is None: return None
        try:
            # 1. 估算本底噪声水平来推荐阈值
            rms = librosa.feature.rms(y=self.audio_data, frame_length=2048, hop_length=512)[0]
            sorted_rms = np.sort(rms)
            noise_floor_rms = np.mean(sorted_rms[:int(len(sorted_rms) * 0.05)])
            noise_floor_db = librosa.power_to_db([noise_floor_rms**2], ref=np.max)[0]
            recommended_threshold = max(-60, min(-20, int(noise_floor_db + 6))) # 限制在滑块范围内

            # 2. 估算平均静音时长
            intervals = librosa.effects.split(self.audio_data, top_db=40) # 用一个宽松的阈值初步分割
            if len(intervals) > 1:
                silence_durations_ms = [(intervals[i+1][0] - intervals[i][1]) / self.audio_sr * 1000 for i in range(len(intervals) - 1)]
                # 取静音时长的中位数，并调整到合理范围
                recommended_min_silence = int(max(100, min(500, np.median([d for d in silence_durations_ms if d > 50]) * 0.6)))
            else:
                recommended_min_silence = 200 # 如果只有一个片段，说明语速很快，用较小默认值
            return {'threshold': recommended_threshold, 'min_silence': recommended_min_silence}
        except Exception as e:
            self.log(f"智能参数推荐失败: {e}"); return None

    def apply_smart_params(self):
        """计算并应用智能推荐的参数到UI上。"""
        params = self._calculate_smart_params()
        if params:
            self.log(f"应用智能推荐参数: 阈值={params['threshold']}dB, 最小静音={params['min_silence']}ms")
            # 阻塞信号以防止在设置时触发不必要的多次分析
            self.threshold_slider.blockSignals(True); self.min_silence_input.blockSignals(True)
            self.threshold_slider.setValue(params['threshold']); self.min_silence_input.setText(str(params['min_silence']))
            self.threshold_slider.blockSignals(False); self.min_silence_input.blockSignals(False)
            self.on_param_changed() # 手动触发一次分析和UI更新

    # --- 文件加载与词表处理 ---
    def select_audio_file(self):
        """通过文件对话框选择长音频文件，并加载。"""
        filepath, _ = QFileDialog.getOpenFileName(self, "选择单个长音频文件", "", "音频文件 (*.wav *.mp3 *.flac)")
        if not filepath: self.recommend_btn.setEnabled(False); return
        
        # 使用不确定模式的进度对话框，因为音频加载可能耗时
        progress = QProgressDialog("正在加载音频，请稍候...", "取消", 0, 0, self)
        progress.setWindowModality(Qt.WindowModal); progress.setRange(0, 0); progress.show(); QApplication.processEvents()
        
        try:
            self.audio_data, self.audio_sr = librosa.load(filepath, sr=None) # 加载音频数据和采样率
            self.audio_filepath = filepath
            self.audio_label.setText(os.path.basename(filepath)) # 更新UI标签
            self.log("音频加载成功。"); self.recommend_btn.setEnabled(True) # 成功后启用推荐按钮
        except Exception as e:
            self.audio_data, self.audio_sr, self.audio_filepath = None, None, None # 清空数据
            self.audio_label.setText("加载失败"); self.recommend_btn.setEnabled(False)
            QMessageBox.critical(self, "音频错误", f"无法加载音频文件:\n{e}")
        finally:
            progress.close(); self.analyze_and_preview() # 关闭进度条并进行初步分析

    def _load_fdeck_for_splitting(self, filepath):
        """从 .fdeck 卡组包中加载单词列表以用于分割。"""
        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                if 'manifest.json' not in zf.namelist(): raise ValueError("卡组包内缺少 manifest.json 文件。")
                with zf.open('manifest.json') as manifest_file: manifest_data = json.load(manifest_file)
            
            self.word_list = [card.get("id") for card in manifest_data.get("cards", []) if card.get("id")]
            if not self.word_list: raise ValueError("卡组中没有找到任何有效的卡片ID。")

            self.wordlist_path = filepath
            filename = os.path.basename(filepath)
            deck_name = manifest_data.get("meta", {}).get("deck_name", filename)
            self.wordlist_label.setText(f"{deck_name} ({len(self.word_list)}个词)")
            self.log(f"已加载速记卡组 '{deck_name}'，包含 {len(self.word_list)} 个词条。")
            self.copy_for_tts_btn.setEnabled(True)
            
            # [关键修复] 自动设置默认输出目录，指向 flashcards/audio_tts/ 目录
            # ----------------------------------------------------------------------
            deck_name_for_dir = os.path.splitext(filename)[0]
            default_output_dir = os.path.join(
                self.parent_window.BASE_PATH, "flashcards", "audio_tts", deck_name_for_dir
            )
            self.output_dir_input.setText(default_output_dir.replace("\\", "/"))
            # ----------------------------------------------------------------------
            
            self.analyze_and_preview()

        except Exception as e:
            self.wordlist_path, self.word_list = None, []
            self.wordlist_label.setText("加载失败")
            self.copy_for_tts_btn.setEnabled(False)
            QMessageBox.critical(self, "卡组加载错误", f"无法加载或解析 .fdeck 卡组文件:\n{e}")

    def load_wordlist_from_path(self, filepath):
        """根据文件类型（.json 或 .fdeck）加载词表。"""
        if not filepath or not os.path.exists(filepath): return
        if filepath.endswith('.fdeck'): self._load_fdeck_for_splitting(filepath)
        elif filepath.endswith('.json'): self._load_standard_json_wordlist(filepath)
        else: QMessageBox.warning(self, "文件类型不支持", "请选择一个有效的 .json 词表或 .fdeck 速记卡组文件。")

    def _load_standard_json_wordlist(self, filepath):
        """处理旧的 standard_wordlist.json 格式词表。"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            if data.get("meta", {}).get("format") != "standard_wordlist": raise ValueError("不是有效的'standard_wordlist'格式。")
            self.word_list = [item.get("text", "") for group in data.get("groups", []) for item in group.get("items", []) if item.get("text")]
            if not self.word_list: raise ValueError("词表中没有找到任何有效的词条。")

            self.wordlist_path = filepath; filename = os.path.basename(filepath)
            self.wordlist_label.setText(f"{filename} ({len(self.word_list)}个词)")
            self.log(f"已加载词表 '{filename}'，包含 {len(self.word_list)} 个词。")
            self.copy_for_tts_btn.setEnabled(True)
            
            # 自动设置默认输出目录 (指向 audio_tts/ 目录)
            default_output_dir = os.path.join(self.parent_window.BASE_PATH, "audio_tts", os.path.splitext(filename)[0])
            self.output_dir_input.setText(default_output_dir.replace("\\", "/"))
            self.analyze_and_preview() # 重新分析和预览
        except Exception as e:
            self.wordlist_path, self.word_list = None, []; self.wordlist_label.setText("加载失败"); self.copy_for_tts_btn.setEnabled(False)
            QMessageBox.critical(self, "词表错误", f"无法加载或解析 .json 词表文件:\n{e}")

    def copy_list_for_tts(self):
        """将当前词表内容转换为逗号分隔的字符串并复制到剪贴板。"""
        if not self.word_list: self.log("错误：没有可复制的词表内容。"); return
        try:
            tts_string = ", ".join(self.word_list); QApplication.clipboard().setText(tts_string)
            self.log(f"成功！已将 {len(self.word_list)} 个单词以TTS格式复制到剪贴板。")
            QMessageBox.information(self, "复制成功", "词表内容已成功复制到剪贴板！", QMessageBox.Ok)
        except Exception as e:
            self.log(f"复制为TTS格式时出错: {e}"); QMessageBox.critical(self, "复制失败", f"无法将内容复制到剪贴板:\n{e}")

    def select_wordlist_file(self):
        """通过文件对话框选择词表文件。"""
        filepath, _ = QFileDialog.getOpenFileName(self, "选择词表或速记卡组", self.parent_window.BASE_PATH, "支持的文件 (*.json *.fdeck)")
        self.load_wordlist_from_path(filepath)

    def on_param_changed(self):
        """当分割参数改变时，更新UI并重新分析预览。"""
        self.threshold_label.setText(f"{self.threshold_slider.value()} dB"); self.analyze_and_preview()

    # --- 自动分割逻辑与预览 ---
    def analyze_and_preview(self):
        """
        根据当前设置的参数，使用 Librosa 进行音频分割，并更新预览表格。
        这是实时预览的核心方法。
        """
        if self.audio_data is None or self.audio_sr is None or not self.word_list: return
        try:
            top_db = -int(self.threshold_slider.value()) # 阈值转换为正值，因为 Librosa 使用 top_db (相对于峰值)
            min_silence_ms = int(self.min_silence_input.text())
            min_silence_samples = int(min_silence_ms / 1000 * self.audio_sr)
            
            # 使用 Librosa 的 voice activity detection (VAD) 进行初步分割
            intervals = librosa.effects.split(self.audio_data, top_db=top_db, frame_length=2048, hop_length=512)
            
            # 根据选择的模式（精确或完整）生成最终分割点
            if self.precise_mode_radio.isChecked():
                # 精确模式：只保留有效时长超过最小静音时长的语音片段
                self.split_points = [it for it in intervals if (it[1] - it[0]) > (min_silence_samples / 5)]
            else: # 完整模式 (Full Mode)
                # 完整模式：以静音段的中心作为分割点，确保每个单词的完整性
                if len(intervals) < 1: 
                    self.split_points = []
                else:
                    silence_midpoints = []
                    # 找到所有符合最小静音时长的静音段的中心
                    for i in range(len(intervals) - 1):
                        silence_duration_samples = intervals[i+1][0] - intervals[i][1]
                        if silence_duration_samples >= min_silence_samples:
                            mid_point = intervals[i][1] + silence_duration_samples // 2
                            silence_midpoints.append(mid_point)
                    
                    self.split_points = []
                    last_split_point = 0
                    for mid_point in silence_midpoints:
                        self.split_points.append((last_split_point, mid_point))
                        last_split_point = mid_point
                    # 添加最后一个片段 (从最后一个分割点到音频结束)
                    self.split_points.append((last_split_point, len(self.audio_data)))
            
            self.populate_preview_table() # 填充预览表格
        except Exception as e: 
            self.log(f"分析时出错: {e}")

    def populate_preview_table(self):
        """填充预览表格，显示自动分割的结果与词表的匹配情况。"""
        self.preview_table.setRowCount(0); # 清空表格
        num_segments = len(self.split_points) # 自动分割出的片段数量
        num_words = len(self.word_list)       # 词表中的单词数量

        # 更新匹配状态标签和处理按钮的启用状态
        if num_segments == num_words > 0:
            self.match_status_label.setText(f"✓ 匹配成功：检测到 {num_segments} 个片段。")
            self.match_status_label.setStyleSheet("color: #2E7D32; font-weight: bold;")
            self.process_btn.setEnabled(True)
        else:
            self.match_status_label.setText(f"✗ 不匹配：检测到 {num_segments} 个片段，但词表需要 {num_words} 个。请调整参数。")
            self.match_status_label.setStyleSheet("color: #C62828; font-weight: bold;")
            self.process_btn.setEnabled(False)
        
        # 填充表格行 (取片段数和单词数的最大值，以便同时显示两者)
        max_rows = max(num_segments, num_words)
        self.preview_table.setRowCount(max_rows)
        for i in range(max_rows):
            item_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            
            # 填充“片段 #”和“时长”列
            if i < num_segments:
                start, end = self.split_points[i]
                duration = (end - start) / self.audio_sr # 计算片段时长
                self.preview_table.setItem(i, 0, QTableWidgetItem(f"片段 {i+1}"))
                self.preview_table.setItem(i, 2, QTableWidgetItem(f"{duration:.2f}"))
                # 设置这些列为不可编辑
                self.preview_table.item(i, 0).setFlags(item_flags)
                self.preview_table.item(i, 2).setFlags(item_flags)
            else:
                self.preview_table.setItem(i, 0, QTableWidgetItem("-"))
                self.preview_table.setItem(i, 2, QTableWidgetItem("-"))
            
            # 填充“对应单词 (可编辑)”列
            if i < num_words:
                word_item = QTableWidgetItem(self.word_list[i])
                word_item.setFlags(item_flags | Qt.ItemIsEditable) # 单词列可编辑
                self.preview_table.setItem(i, 1, word_item)
            else:
                self.preview_table.setItem(i, 1, QTableWidgetItem("-"))
        self.preview_table.resizeColumnsToContents() # 调整列宽以适应内容

    # --- 预览表格的右键菜单 ---
    def show_preview_context_menu(self, position):
        """
        构建并显示预览表格的右键上下文菜单。
        菜单项会根据选中的行数动态变化。
        """
        selected_items = self.preview_table.selectionModel().selectedRows()
        if not selected_items: return

        menu = QMenu(self)
        count = len(selected_items) # 选中的行数

        # 试听选中片段 (只试听第一个选中的片段)
        play_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "试听选中片段")
        play_action.triggered.connect(lambda: self.play_segment(selected_items[0].row()))
        
        # --- 手动分割菜单项 ---
        menu.addSeparator()
        # 无论选中一个还是多个，都可以进行手动分割
        manual_split_action = menu.addAction(self.icon_manager.get_icon("cut"), f"手动分割选中的 {count} 个片段...")
        manual_split_action.triggered.connect(self.open_manual_splitter)
        
        # 导出选中片段
        menu.addSeparator()
        export_action = menu.addAction(self.icon_manager.get_icon("export"), f"导出选中的 {count} 个片段...")
        export_action.triggered.connect(self.export_selected_segments)
        
        # 删除选中行
        menu.addSeparator()
        delete_action = menu.addAction(self.icon_manager.get_icon("delete"), "从预览中删除选中行")
        delete_action.triggered.connect(self.delete_selected_segments)
        
        # 显示菜单
        action = menu.exec_(self.preview_table.mapToGlobal(position))
    
    # --- [核心新增] 手动分割处理逻辑 ---
    def open_manual_splitter(self):
        """
        为当前选中的连续片段打开 ManualSplitDialog 进行手动重分。
        如果选择所有片段，则相当于手动分割整个音频。
        """
        selected_rows = sorted([item.row() for item in self.preview_table.selectionModel().selectedRows()])
        if not selected_rows: return

        # 1. 验证选中行是否连续
        if len(selected_rows) > 1:
            for i in range(len(selected_rows) - 1):
                if selected_rows[i+1] - selected_rows[i] != 1:
                    QMessageBox.warning(self, "选择无效", "手动分割功能仅支持选择【连续】的片段。")
                    return
        
        # 获取选中片段的最小和最大行索引
        min_row, max_row = selected_rows[0], selected_rows[-1]

        # 2. 检查所选范围是否超出实际片段数据（词表可能比片段长）
        if max_row >= len(self.split_points):
            QMessageBox.information(self, "提示", "选择的行中包含无效片段（无对应音频），无法手动分割。")
            return

        # 3. 聚合数据：拼接音频数据和对应单词列表
        absolute_start_sample = self.split_points[min_row][0]  # 选中区域的起始样本点 (绝对位置)
        absolute_end_sample = self.split_points[max_row][1]    # 选中区域的结束样本点 (绝对位置)
        
        # 从原始音频数据中截取要手动处理的片段
        combined_audio_data = self.audio_data[absolute_start_sample:absolute_end_sample]
        
        # 提取选中范围对应的原始单词列表
        combined_words = [self.word_list[i] for i in selected_rows if i < len(self.word_list)]

        # 4. 实例化并显示 ManualSplitDialog
        dialog = ManualSplitDialog(
            audio_data=combined_audio_data, 
            sr=self.audio_sr, 
            original_words=combined_words, 
            icon_manager=self.icon_manager, 
            parent=self # 将当前对话框设为父级
        )
        
        # 5. 处理 ManualSplitDialog 返回的结果
        if dialog.exec_() == QDialog.Accepted:
            accepted, new_relative_splits, new_words = dialog.get_results()
            if not accepted: return # 如果对话框返回失败，则不做任何操作

            # --- 6. 原子化地更新主对话框的数据模型 ---
            # a. 首先，从 self.split_points 和 self.word_list 中移除旧的、粗糙的片段
            for i in reversed(selected_rows): # 从后往前删，避免索引错位
                del self.split_points[i]
                if i < len(self.word_list): # 确保不越界
                    del self.word_list[i]
            
            # b. 将 ManualSplitDialog 返回的“相对”分割点转换为“绝对”分割点
            # ManualSplitDialog 返回的分割点是相对于其传入的 combined_audio_data 的开头
            # 所以需要加上 combined_audio_data 在整个长音频中的绝对起始位置
            absolute_new_splits = [
                (absolute_start_sample + start, absolute_start_sample + end) 
                for start, end in new_relative_splits
            ]
            
            # c. 将新的、精细的分割点和单词插入到原来的位置 (min_row)
            for split_point in reversed(absolute_new_splits): # 从后往前插，保持顺序
                self.split_points.insert(min_row, split_point)
            
            for word in reversed(new_words): # 从后往前插，保持顺序
                self.word_list.insert(min_row, word)

            # 7. 刷新主对话框的预览表格，UI会自动更新
            self.log(f"片段 #{min_row + 1} 到 #{max_row + 1} 已被手动重分为 {len(absolute_new_splits)} 段。")
            self.populate_preview_table()
    
    # --- 片段试听、导出和删除 ---
    def play_segment(self, row):
        """试听指定行（片段）的音频。"""
        if not (0 <= row < len(self.split_points)) or self.audio_data is None:
            return

        # 应用分割点偏移量 (与最终保存逻辑一致)
        try:
            offset_ms = int(self.offset_input.text())
            offset_samples = int(offset_ms / 1000 * self.audio_sr)
        except (ValueError, TypeError):
            offset_samples = 0

        # 获取原始分割点并应用偏移量，确保不超出音频边界
        start_raw, end_raw = self.split_points[row]
        start = max(0, start_raw - offset_samples)
        end = min(len(self.audio_data), end_raw + offset_samples)
        
        segment_data = self.audio_data[start:end]

        try:
            # 停止并清理之前的临时文件和播放器
            if self.temp_audio_file and os.path.exists(self.temp_audio_file):
                self.player.setMedia(QMediaContent()) # 释放文件句柄
                os.remove(self.temp_audio_file)
            
            # 创建新的临时文件并播放
            fd, self.temp_audio_file = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            
            sf.write(self.temp_audio_file, segment_data, self.audio_sr)
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self.temp_audio_file)))
            self.player.play()
        except Exception as e: 
            self.log(f"试听失败: {e}")

    def delete_selected_segments(self):
        """从预览表格中删除所有选中的行（及其对应的片段和单词）。"""
        selected_rows = sorted([index.row() for index in self.preview_table.selectionModel().selectedRows()], reverse=True)
        if not selected_rows: return
        
        for row in selected_rows:
            if row < len(self.split_points): del self.split_points[row]
            if row < len(self.word_list): del self.word_list[row]
            
        self.log(f"已从预览中移除 {len(selected_rows)} 行。")
        self.populate_preview_table() # 刷新表格以反映更改

    def export_selected_segments(self):
        """将选中的音频片段导出到指定文件夹。"""
        selected_rows = sorted([index.row() for index in self.preview_table.selectionModel().selectedRows()])
        if not selected_rows: 
            QMessageBox.warning(self, "未选择", "请先在预览列表中选择要导出的片段。"); return
        
        output_dir = QFileDialog.getExistingDirectory(self, "选择导出文件夹");
        if not output_dir: return

        try:
            # 应用分割点偏移量
            offset_ms = int(self.offset_input.text())
            offset_samples = int(offset_ms / 1000 * self.audio_sr)
            
            for row in selected_rows:
                if row >= len(self.split_points): continue # 跳过无效行
                
                # 获取单词作为文件名
                word_item = self.preview_table.item(row, 1)
                word = word_item.text() if word_item else f"segment_{row+1}"
                
                # 截取音频片段
                start, end = self.split_points[row]
                start = max(0, start - offset_samples)
                end = min(len(self.audio_data), end + offset_samples)
                segment = self.audio_data[start:end]
                
                # 保存文件
                safe_filename = re.sub(r'[\\/*?:"<>|]', "", word) # 清理文件名中的非法字符
                out_path = os.path.join(output_dir, f"{safe_filename}.wav")
                sf.write(out_path, segment, self.audio_sr)
            
            self.log(f"成功导出 {len(selected_rows)} 个片段到: {output_dir}")
            QMessageBox.information(self, "导出成功", f"选中的 {len(selected_rows)} 个片段已成功保存。")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误:\n{e}")

    # --- 批量处理与输出 ---
    def select_output_dir(self):
        """通过对话框选择输出文件夹。"""
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹");
        if directory: self.output_dir_input.setText(directory)

    def run_processing(self):
        """执行批量分割和保存所有音频片段。"""
        output_dir = self.output_dir_input.text().strip()
        if not output_dir: QMessageBox.warning(self, "输出无效", "请选择一个有效的输出文件夹。"); return
        
        # 检查输出目录是否存在，不存在则创建
        if not os.path.exists(output_dir):
            reply = QMessageBox.question(self, "目录不存在", f"目录 '{output_dir}' 不存在，是否要创建它？", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes: os.makedirs(output_dir)
            else: return

        # 再次确认片段数与词表数匹配
        if not (len(self.split_points) == self.preview_table.rowCount() > 0): 
            QMessageBox.warning(self, "无法处理", "片段数与预览列表中的行数必须匹配才能批量处理。"); return

        self.log("开始批量处理..."); self.process_btn.setEnabled(False) # 禁用处理按钮
        self.progress_bar.setRange(0, len(self.split_points)); self.progress_bar.setValue(0) # 设置进度条范围

        try:
            offset_ms = int(self.offset_input.text())
            output_format = self.output_format_combo.currentText().split(' ')[0].lower() # 获取输出格式 (wav/mp3)
            offset_samples = int(offset_ms / 1000 * self.audio_sr)
            
            for i in range(len(self.split_points)):
                word_item = self.preview_table.item(i, 1)
                if not word_item: continue # 确保有对应的单词
                word = word_item.text()
                
                self.progress_bar.setValue(i + 1)
                self.log(f"正在保存片段 {i+1}/{len(self.split_points)}: {word}"); 
                QApplication.processEvents() # 强制UI更新进度和日志

                start, end = self.split_points[i]
                start = max(0, start - offset_samples); end = min(len(self.audio_data), end + offset_samples)
                segment = self.audio_data[start:end] # 截取音频片段
                
                safe_filename = re.sub(r'[\\/*?:"<>|]', "", word) # 清理文件名
                out_path = os.path.join(output_dir, f"{safe_filename}.{output_format}")
                sf.write(out_path, segment, self.audio_sr) # 保存音频文件

            self.log("所有文件处理完毕！"); QMessageBox.information(self, "处理完成", "所有音频片段已成功分割并保存。")
        except Exception as e:
            self.log(f"处理失败: {e}"); QMessageBox.critical(self, "处理失败", f"在处理过程中发生错误:\n{e}")
        finally:
            self.progress_bar.setValue(self.progress_bar.maximum()); self.process_btn.setEnabled(True) # 恢复按钮状态

    # --- 窗口关闭与清理 ---
    def closeEvent(self, event):
        """窗口关闭时，停止播放器并清理临时文件。"""
        if self.temp_audio_file and os.path.exists(self.temp_audio_file):
            try: 
                self.player.setMedia(QMediaContent()) # 释放文件句柄
                os.remove(self.temp_audio_file)
            except: 
                pass # 忽略删除失败
        super().closeEvent(event)