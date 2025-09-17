# --- START OF FILE plugins/quick_record/quick_record.py (v2.0 - Redesigned with Volume Meter, Folder Save & Context Menu) ---

import os
import sys
import tempfile
import shutil
import time
import numpy as np
from datetime import datetime
from collections import deque
import queue

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QListWidget, QListWidgetItem, QGroupBox, QSpacerItem,
                             QSizePolicy, QInputDialog, QLineEdit, QFileDialog, QMessageBox,
                             QProgressBar, QShortcut, QMenu, QWidget, QApplication) # [新增] QProgressBar, QShortcut, QMenu
from PyQt5.QtCore import Qt, QSize, QUrl, QTimer # [新增] QTimer
from PyQt5.QtGui import QFont, QIcon # [新增] QIcon (为了设置 AnimatedListWidget 的图标)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# --- 插件依赖导入 ---
# 确保插件能找到 sounddevice 和 soundfile
try:
    import sounddevice as sd
    import soundfile as sf
except ImportError as e:
    # 这是一个优雅的回退，如果核心依赖缺失，插件将无法加载
    # 但主程序不会崩溃。错误信息会在控制台打印。
    raise ImportError(f"随录插件无法加载，缺少核心依赖: {e}")

# 导入插件API基类 和 自定义控件模块中的 AnimatedListWidget
try:
    # 假设在标准插件目录结构中，可以直接导入
    from plugin_system import BasePlugin
    from custom_widgets_module import AnimatedListWidget # [核心修改] 导入 AnimatedListWidget
except ImportError:
    # 如果直接运行此文件进行测试，则从上层目录导入
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin
    from custom_widgets_module import AnimatedListWidget # [核心修改] 导入 AnimatedListWidget

# ==============================================================================
# 插件主类 (Plugin Entry Class)
# 负责管理 QuickRecordDialog 的生命周期，遵循 BasePlugin 契约。
# ==============================================================================
class QuickRecordPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None

    def setup(self):
        """
        当插件被启用时调用。
        对于独立窗口插件，通常只需返回True，表示已准备就绪。
        """
        return True

    def teardown(self):
        """
        当插件被禁用或程序退出时调用。
        确保对话框被正确关闭和清理，释放所有资源。
        """
        if self.dialog_instance:
            # 安全地关闭对话框，这将触发其 closeEvent，进而清理临时文件
            self.dialog_instance.close()
        self.dialog_instance = None

    def execute(self, **kwargs):
        """
        当用户通过UI（菜单或快捷按钮）执行此插件时调用。
        它创建并显示录音对话框。
        """
        try:
            # 采用单例模式，避免打开多个相同的对话框实例
            if self.dialog_instance is None:
                self.dialog_instance = QuickRecordDialog(self.main_window)
                # 连接对话框的 finished 信号，以便在它关闭时清理实例引用
                self.dialog_instance.finished.connect(self.on_dialog_finished)
            
            # 显示对话框并将其置于顶层
            self.dialog_instance.show()
            self.dialog_instance.raise_()
            self.dialog_instance.activateWindow()
        except Exception as e:
            QMessageBox.critical(self.main_window, "插件错误", f"无法打开'随录'插件:\n{e}")

    def on_dialog_finished(self):
        """当对话框关闭时，重置实例变量，允许下次重新创建。"""
        self.dialog_instance = None

# ==============================================================================
# 录音对话框 (QuickRecordDialog)
# 这是插件的核心UI和逻辑实现，包含了录音、管理、播放及文件操作。
# ==============================================================================
class QuickRecordDialog(QDialog):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        
        self.main_window = parent_window
        self.icon_manager = parent_window.icon_manager
        self.resolve_device_func = self.main_window.settings_page.resolve_device_func
        
        # --- 状态变量 ---
        self.is_recording = False
        # recorded_clips 存储每个录制片段的信息: {'path': temp_path, 'name': display_name}
        self.recorded_clips = []
        self.stream = None # sounddevice 音频流
        
        # 核心：创建一个唯一的临时目录来存放本次会话的录音。
        # 这确保了文件的隔离性，并在对话框关闭时可以轻松进行批量清理。
        self.temp_dir = tempfile.mkdtemp(prefix="phonacq_quick_record_")
        
        # --- 音量计相关状态 (新增功能) ---
        self.audio_queue = queue.Queue() # sounddevice 回调函数将原始音频数据放入此队列
        self.volume_meter_queue = queue.Queue(maxsize=2) # 用于UI音量计更新的队列
        self.volume_history = deque(maxlen=5) # 用于平滑音量显示的历史数据

        # --- UI组件 ---
        self.player = QMediaPlayer() # 用于播放录制片段
        self.update_timer = QTimer() # 定时器，用于周期性更新音量计UI

        self._init_ui()
        self._connect_signals()
        self._update_button_states() # 初始化按钮启用状态

    def _init_ui(self):
        """
        初始化对话框的用户界面布局和控件。
        采用左右两栏布局：左侧为录音列表，右侧为录制控制和操作按钮。
        """
        self.setWindowTitle("随录 (Quick Record)")
        self.setMinimumSize(600, 500)
        self.resize(700, 600)

        # 主布局：左右两栏
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)

        # --- 1. 左侧面板 (列表区) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        list_group = QGroupBox("已录制片段")
        list_group.setToolTip("所有在本窗口中录制的音频片段都会显示在这里。")
        list_layout = QVBoxLayout(list_group)
        
        self.clip_list = AnimatedListWidget(icon_manager=self.icon_manager)
        self.clip_list.setToolTip("<b>录音片段列表:</b>\n"
                                  " - <b>双击</b> 或按 <b>回车</b> 播放片段\n"
                                  " - <b>右键单击</b> 可进行重命名、播放或移除操作\n"
                                  " - 支持使用 <b>Ctrl</b> 或 <b>Shift</b> 进行多选")
        self.clip_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.clip_list.setContextMenuPolicy(Qt.CustomContextMenu)
        
        list_layout.addWidget(self.clip_list)
        
        playback_layout = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.setToolTip("播放当前选中的单个音频片段。")
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setToolTip("从列表中永久删除选中的一个或多个片段。")
        
        self.play_btn.setIcon(self.icon_manager.get_icon("play"))
        self.delete_btn.setIcon(self.icon_manager.get_icon("clear_contents"))

        playback_layout.addStretch()
        playback_layout.addWidget(self.play_btn)
        playback_layout.addWidget(self.delete_btn)
        list_layout.addLayout(playback_layout)

        left_layout.addWidget(list_group)
        main_layout.addWidget(left_panel, 1)

        # --- 2. 右侧面板 (控制区) ---
        right_panel = QWidget()
        right_panel.setFixedWidth(280)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        record_group = QGroupBox("录制控制")
        record_layout = QVBoxLayout(record_group)
        record_layout.setSpacing(8)
        
        self.record_btn = QPushButton("开始录制")
        self.record_btn.setToolTip("点击开始录音，再次点击则停止。")
        self.record_btn.setIconSize(QSize(24, 24))
        self.record_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.record_btn.setMinimumHeight(40)

        self.status_label = QLabel("准备就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        # [新增] 用于显示当前录音设备的标签
        self.device_label = QLabel("设备: 未指定")
        self.device_label.setAlignment(Qt.AlignCenter)
        self.device_label.setObjectName("SubtleStatusLabel") # 使用一个不显眼的样式
        self.device_label.setWordWrap(True) # <-- [新增] 允许标签文本换行
        self.device_label.setToolTip("当前用于录音的音频设备。\n该设置可在'系统与帮助 -> 程序设置'中更改。")
        self.volume_meter = QProgressBar()
        self.volume_meter.setRange(0, 100)
        self.volume_meter.setValue(0)
        self.volume_meter.setTextVisible(False)
        self.volume_meter.setToolTip("实时录音音量计。\n请确保说话时音量条大部分时间处于绿色或黄色区域。")

        record_layout.addWidget(self.record_btn)
        record_layout.addWidget(self.status_label)
        record_layout.addWidget(self.device_label) # [新增] 将设备标签添加到布局中
        record_layout.addWidget(self.volume_meter)
        right_layout.addWidget(record_group)

        action_group = QGroupBox("后续操作")
        action_layout = QVBoxLayout(action_group)
        action_layout.setSpacing(8)
        
        # [修改] 重新排序按钮，使其逻辑更顺畅
        self.save_default_btn = QPushButton("保存到默认目录")
        self.save_default_btn.setToolTip("<b>推荐操作：</b>\n"
                                       "将选中的片段保存到音频管理器的'语音包录制'目录下。\n"
                                       "系统会提示您为这次录音创建一个新的子文件夹。")
        self.save_as_btn = QPushButton("另存为...")
        self.save_as_btn.setToolTip("将选中的片段保存到您计算机上的任意位置。")
        self.send_analysis_btn = QPushButton("发送到音频分析")
        self.send_analysis_btn.setToolTip("<b>工作流操作：</b>\n"
                                        "将选中的片段直接发送到'音频分析'模块进行深入研究。\n"
                                        "这不会在本地保存文件副本。")
        self.send_analysis_btn.setObjectName("AccentButton")
        
        self.save_default_btn.setIcon(self.icon_manager.get_icon("save"))
        self.save_as_btn.setIcon(self.icon_manager.get_icon("save_as"))
        self.send_analysis_btn.setIcon(self.icon_manager.get_icon("analyze"))

        # [修改] 按照新的顺序添加
        action_layout.addWidget(self.save_default_btn)
        action_layout.addWidget(self.save_as_btn)
        action_layout.addWidget(self.send_analysis_btn)
        right_layout.addWidget(action_group)

        right_layout.addStretch()

        self.close_btn = QPushButton("关闭窗口")
        self.close_btn.setToolTip("关闭此窗口。所有未保存的录音片段将被丢弃。")
        right_layout.addWidget(self.close_btn)
        
        main_layout.addWidget(right_panel)

        self._update_icons()

    def _connect_signals(self):
        """
        连接所有UI控件的信号到相应的槽函数。
        包括新的 AnimatedListWidget 信号和快捷键。
        """
        self.record_btn.clicked.connect(self.toggle_recording)
        
        # [修改] 使用 itemSelectionChanged 更新按钮状态
        self.clip_list.itemSelectionChanged.connect(self._update_button_states)
        # [修改] item_activated 信号用于播放 (双击或回车触发)
        self.clip_list.item_activated.connect(self.play_clip_from_item)
        # [新增] 右键菜单现在直接连接到 clip_list
        self.clip_list.customContextMenuRequested.connect(self._show_clip_context_menu)
        
        # [新增] 回车快捷键，因为 AnimatedListWidget 已经捕获了，所以可以简化
        # self.play_shortcut_enter = QShortcut(Qt.Key_Return, self.clip_list)
        # self.play_shortcut_enter.activated.connect(self.play_selected_clip) # 或 play_clip_from_item
        # self.play_shortcut_enter_2 = QShortcut(Qt.Key_Enter, self.clip_list)
        # self.play_shortcut_enter_2.activated.connect(self.play_selected_clip) # 或 play_clip_from_item
        
        self.play_btn.clicked.connect(self.play_selected_clip)
        self.player.stateChanged.connect(self._on_player_state_changed)
        
        self.delete_btn.clicked.connect(self.delete_selected_clips)
        
        self.save_default_btn.clicked.connect(self.save_to_default)
        self.save_as_btn.clicked.connect(self.save_as)
        self.send_analysis_btn.clicked.connect(self.send_to_analysis)
        
        self.close_btn.clicked.connect(self.accept)
        
        # [新增功能] 连接音量计定时器
        self.update_timer.timeout.connect(self.update_volume_meter)

    def _get_device_name(self, device_index):
        """根据设备索引号查询并返回设备名称。"""
        try:
            if device_index is None:
                # 如果索引是None，表示使用系统默认输入设备
                default_device_info = sd.query_devices(kind='input')
                return default_device_info.get('name', "系统默认")
            
            devices = sd.query_devices()
            if 0 <= device_index < len(devices):
                return devices[device_index]['name']
            else:
                return f"无效索引 ({device_index})"
        except Exception as e:
            # 在某些系统或没有声卡驱动时，查询可能失败
            print(f"查询设备名称失败: {e}", file=sys.stderr)
            return "查询失败"

    def _update_icons(self):
        """
        设置或更新所有按钮的图标。
        录制按钮的图标会根据录制状态动态变化。
        """
        self.record_btn.setIcon(self.icon_manager.get_icon("record")) # 初始设置为录制图标
        # 其他按钮图标在 _init_ui 中已经设置，这里只用于确保一致性或在需要时刷新

    def _update_button_states(self):
        """
        根据当前录音列表是否有选中项，启用或禁用相关操作按钮。
        """
        has_selection = len(self.clip_list.selectedItems()) > 0
        
        self.play_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
        self.save_default_btn.setEnabled(has_selection)
        self.save_as_btn.setEnabled(has_selection)
        self.send_analysis_btn.setEnabled(has_selection)

    # --- 核心录音控制逻辑 ---
    def toggle_recording(self):
        """切换录制状态：如果正在录制则停止，否则开始。"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        """
        开始音频录制。
        从主程序配置中获取采样率和通道数，并启动 sounddevice 输入流。
        """
        try:
            # [核心修改] 使用从主程序获取的函数来解析最终的录音设备
            device_index = self.resolve_device_func(self.main_window.config)
            device_name = self._get_device_name(device_index)
            self.device_label.setText(f"设备: {device_name}")

            # 从主程序配置中获取音频设置
            sr = self.main_window.config.get("audio_settings", {}).get("sample_rate", 44100)
            channels = self.main_window.config.get("audio_settings", {}).get("channels", 1)
            
            # 清空上次录制的数据队列
            while not self.audio_queue.empty(): self.audio_queue.get_nowait()
            while not self.volume_meter_queue.empty(): self.volume_meter_queue.get_nowait()
            self.volume_history.clear()

            # [核心修改] 在创建流时传入解析出的设备索引
            self.stream = sd.InputStream(
                device=device_index,
                samplerate=sr,
                channels=channels,
                callback=self._audio_callback
            )
            self.stream.start()
            
            self.is_recording = True
            self.status_label.setText("录制中...")
            self.record_btn.setText("停止录制")
            self.record_btn.setIcon(self.icon_manager.get_icon("stop"))
            
            self.update_timer.start(30)
        
        except Exception as e:
            QMessageBox.critical(self, "录制错误", f"无法开始录制:\n{e}")
            self.is_recording = False
            self.device_label.setText("设备: 初始化失败")

    def stop_recording(self):
        """
        停止音频录制。
        将录制的数据从队列中取出并保存为临时WAV文件，然后更新UI列表。
        """
        if not self.stream: return # 如果流未启动，则直接返回
            
        self.stream.stop()  # 停止 sounddevice 流
        self.stream.close() # 关闭流，释放资源
        self.stream = None
        self.is_recording = False
        
        # [新增功能] 停止音量计定时器并重置UI
        self.update_timer.stop()
        self.volume_meter.setValue(0) # 重置音量计显示

        # 更新UI状态，提示用户正在处理
        self.status_label.setText("处理中...")
        QApplication.processEvents() # 强制UI立即更新文本

        # 将录制的数据块从队列中取出并合并为一个Numpy数组
        data_chunks = []
        while not self.audio_queue.empty():
            try: data_chunks.append(self.audio_queue.get_nowait())
            except queue.Empty: break # 队列为空时退出循环
        
        # 如果没有录到任何数据，则丢弃本次录音
        if not data_chunks:
            self.status_label.setText("录音为空，已丢弃。")
            self.record_btn.setText("开始录制")
            self.record_btn.setIcon(self.icon_manager.get_icon("record"))
            return
            
        recording = np.concatenate(data_chunks, axis=0) # 合并所有录制片段
        
        # 生成一个唯一的文件名和临时文件路径
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3] # 精确到毫秒
        clip_name = f"QuickRecord_{timestamp}"
        temp_filepath = os.path.join(self.temp_dir, f"{clip_name}.wav")
        
        # 获取采样率并保存录音到临时文件
        sr = int(self.main_window.config.get("audio_settings", {}).get("sample_rate", 44100))
        sf.write(temp_filepath, recording, sr) # 使用 soundfile 保存WAV

        # 更新内部数据模型 (recorded_clips)
        self.recorded_clips.append({'path': temp_filepath, 'name': clip_name})
        
        # [修改] 使用 AnimatedListWidget 的 API 添加新项到UI列表
        new_item_data = {
            'type': 'item', # 类型为“item”，表示普通条目
            'text': clip_name, # 显示在列表中的文本
            'icon': self.icon_manager.get_icon("music_record"), # 给它一个音频图标
            'data': {'path': temp_filepath, 'name': clip_name} # 存储核心数据
        }
        self.clip_list.appendItemWithAnimation(new_item_data)
        
        # 选中新添加的项，并更新按钮状态
        last_item = self.clip_list.item(self.clip_list.count() - 1)
        if last_item: self.clip_list.setCurrentItem(last_item)
        
        # 恢复UI状态
        self.status_label.setText("准备就绪")
        self.device_label.setText("设备: 未指定") # [新增] 重置设备标签
        self.record_btn.setText("开始录制")
        self.record_btn.setIcon(self.icon_manager.get_icon("record"))

    # --- 音量计与声卡回调函数 ---
    def _audio_callback(self, indata, frames, time, status):
        """
        sounddevice 的回调函数，在后台音频线程中运行。
        它负责将录音数据放入队列，并为音量计提供采样。
        """
        if status: print(status, file=sys.stderr) # 打印任何状态警告

        # 1. 原始数据：放入 `audio_queue` 供后续文件保存
        self.audio_queue.put(indata.copy())
        
        # 2. 增益处理：根据配置的录音增益调整数据，用于音量计显示
        gain = self.main_window.config.get('audio_settings', {}).get('recording_gain', 1.0)
        processed_for_meter = indata
        if gain != 1.0: processed_for_meter = np.clip(indata * gain, -1.0, 1.0) # 剪裁防止超限

        # 3. 音量计数据：放入 `volume_meter_queue` 供UI线程读取
        try: self.volume_meter_queue.put_nowait(processed_for_meter.copy())
        except queue.Full: pass # 如果UI线程处理不过来，可以丢弃一些帧，避免阻塞回调

    def update_volume_meter(self):
        """
        从队列中获取数据并更新音量计UI。
        此方法在主UI线程中运行，通过 QTimer 周期性触发。
        """
        raw_target_value = 0
        try:
            data_chunk = self.volume_meter_queue.get_nowait()
            # 计算 RMS (Root Mean Square) 值作为音量指标
            rms = np.linalg.norm(data_chunk) / np.sqrt(len(data_chunk)) if data_chunk.any() else 0
            # 转换为 dBFS (decibels relative to full scale)
            dbfs = 20 * np.log10(rms + 1e-7) # 加1e-7防止log(0)
            # 将 dBFS 映射到 0-100 的进度条范围 (-60dBFS 映射到 0, 0dBFS 映射到 100)
            raw_target_value = max(0, min(100, (dbfs + 60) * (100 / 60)))
        except queue.Empty:
            # 如果队列为空，表示没有新的音频数据，则让音量计缓慢衰减
            raw_target_value = self.volume_meter.value() * 0.8
        except Exception as e:
            # 捕获其他可能的错误，如数据格式问题
            print(f"音量计更新错误: {e}")
            raw_target_value = 0 # 发生错误时重置音量计

        # 应用平滑：将当前音量加入历史，并计算平均值
        self.volume_history.append(raw_target_value)
        smoothed_target_value = sum(self.volume_history) / len(self.volume_history)

        # 动画式更新：让音量计平滑地过渡到目标值，避免跳动
        current_value = self.volume_meter.value()
        smoothing_factor = 0.4 # 越大越平滑，响应越慢
        new_value = int(current_value * (1 - smoothing_factor) + smoothed_target_value * smoothing_factor)
        
        # 如果新值与目标值非常接近，则直接跳到目标值，避免无限接近
        if abs(new_value - smoothed_target_value) < 2: new_value = int(smoothed_target_value)
            
        self.volume_meter.setValue(new_value)

    # --- 列表项操作 (重命名、播放、删除) ---
    def rename_clip(self, item):
        """
        重命名已录制片段的列表项。
        此方法由右键菜单触发，接收一个 QListWidgetItem。
        """
        if not item: return
        
        # 从 AnimatedListWidget 的 HIERARCHY_DATA_ROLE 中获取原始数据
        item_data = item.data(AnimatedListWidget.HIERARCHY_DATA_ROLE)
        old_name = item_data.get('text', '') # 显示的旧名称
        
        # 弹出输入对话框让用户输入新名称
        new_name, ok = QInputDialog.getText(self, "重命名片段", "请输入新名称:", QLineEdit.Normal, old_name)
        
        # 如果用户取消或输入为空，则不进行任何操作
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        
        new_name_stripped = new_name.strip()
        
        # 1. 更新 QListWidgetItem 的显示文本
        item.setText(new_name_stripped)
        
        # 2. 更新 AnimatedListWidget 内部存储的 item_data (HIERARCHY_DATA_ROLE)
        item_data['text'] = new_name_stripped
        item.setData(AnimatedListWidget.HIERARCHY_DATA_ROLE, item_data)

        # 3. 更新插件内部的 recorded_clips 数据模型
        for clip in self.recorded_clips:
            if clip['name'] == old_name:
                clip['name'] = new_name_stripped
                break

    def play_clip_from_item(self, item):
        """
        播放指定的单个 QListWidgetItem 对应的音频。
        这是所有播放操作的统一入口，由 AnimatedListWidget 的 item_activated 信号触发。
        """
        if not item: return
        
        # 从 AnimatedListWidget 的 HIERARCHY_DATA_ROLE 中获取文件路径
        item_data = item.data(AnimatedListWidget.HIERARCHY_DATA_ROLE)
        filepath = item_data.get('data', {}).get('path')

        if filepath:
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(filepath)))
            self.player.play()
            
    def play_selected_clip(self):
        """
        当点击“播放”按钮时，播放当前选中的项。
        此方法会调用 `play_clip_from_item` 来处理实际的播放逻辑。
        """
        current_item = self.clip_list.currentItem()
        if current_item: self.play_clip_from_item(current_item)
            
    def _on_player_state_changed(self, state):
        """
        当播放器状态改变时（播放/暂停/停止）调用，更新播放按钮的图标。
        """
        if state == QMediaPlayer.PlayingState:
            self.play_btn.setIcon(self.icon_manager.get_icon("pause"))
        else:
            self.play_btn.setIcon(self.icon_manager.get_icon("play"))

    def delete_clip(self, item):
        """
        删除指定的单个 QListWidgetItem 及其关联的临时文件。
        此方法通常由 `delete_selected_clips` 调用。
        """
        if not item: return
        
        # 从 AnimatedListWidget 的 HIERARCHY_DATA_ROLE 中获取片段名称
        item_data = item.data(AnimatedListWidget.HIERARCHY_DATA_ROLE)
        clip_name = item_data.get('text')
        
        # 从内部数据模型 (self.recorded_clips) 中找到并移除对应的片段信息
        clip_to_remove = next((c for c in self.recorded_clips if c['name'] == clip_name), None)
        if clip_to_remove:
            self.recorded_clips.remove(clip_to_remove)
            # 尝试从磁盘删除临时文件
            try: os.remove(clip_to_remove['path'])
            except OSError as e: print(f"删除临时文件失败: {e}", file=sys.stderr)
        
        # 从UI列表 (AnimatedListWidget) 中移除该项
        self.clip_list.takeItem(self.clip_list.row(item))

    def delete_selected_clips(self):
        """
        删除所有在列表中选中的片段。
        此方法会弹出确认对话框，并循环调用 `delete_clip` 删除每个选中项。
        """
        selected_items = self.clip_list.selectedItems()
        if not selected_items: return
        
        # 弹出确认对话框，提示用户将删除的片段数量
        reply = QMessageBox.question(self, "确认移除", f"您确定要移除选中的 {len(selected_items)} 个片段吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            # 必须复制 selected_items 列表，因为 `takeItem` 会在循环中修改原列表
            for item in list(selected_items):
                self.delete_clip(item)

    # --- 核心文件操作 (保存与发送) ---
    def save_to_default(self):
        """
        将选中的录音片段保存到音频管理器 (audio_record) 的默认路径下。
        用户将被提示输入一个子文件夹名称，录音将保存在该子文件夹中。
        """
        selected_clips = self._get_selected_clips_info()
        if not selected_clips: return # 如果没有选中片段，则退出

        # [新增] 1. 生成一个默认的文件夹名称 (例如: record_20231027_1530)
        default_folder_name = "record_" + datetime.now().strftime("%Y%m%d_%H%M")

        # [新增] 2. 弹出输入对话框让用户命名新的子文件夹
        folder_name, ok = QInputDialog.getText(self, "创建录音批次", 
                                                 "请输入要保存到的文件夹名称:", 
                                                 QLineEdit.Normal, 
                                                 default_folder_name)
        
        # 如果用户取消操作或输入了空名称，则中止保存
        if not ok or not folder_name.strip(): return
        
        folder_name = folder_name.strip() # 去除首尾空格

        # [修改] 3. 构建最终的目标文件夹的完整路径
        # self.main_window.audio_record_dir 是在 Canary.py 中注入的 MainWindow 属性
        base_dir = self.main_window.audio_record_dir
        target_dir = os.path.join(base_dir, folder_name)

        # 检查目标文件夹是否已经存在，并询问用户是否覆盖
        if os.path.exists(target_dir):
            reply = QMessageBox.question(self, "文件夹已存在", 
                                         f"文件夹 '{folder_name}' 已存在。\n是否要将文件保存到该文件夹中？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.No: return # 用户选择不覆盖，则中止
        
        # 确保目标文件夹存在，如果不存在则创建它
        os.makedirs(target_dir, exist_ok=True)
        
        # 4. 复制选中的录音片段到目标文件夹
        saved_count = self._copy_clips(selected_clips, target_dir)
        
        # 弹出消息框通知用户保存结果
        QMessageBox.information(self, "保存成功", f"成功将 {saved_count} 个文件保存到文件夹:\n{target_dir}")
        
        # 刷新音频管理器页面，让新保存的文件立即显示在列表中
        if hasattr(self.main_window, 'audio_manager_page'):
            self.main_window.audio_manager_page.load_and_refresh()

    def save_as(self):
        """
        将选中的录音片段另存为用户指定位置的文件。
        如果选中多个片段，则会提示选择一个目标文件夹。
        """
        selected_clips = self._get_selected_clips_info()
        if not selected_clips: return

        if len(selected_clips) > 1:
            # 如果选中了多个片段，提示用户选择一个目标文件夹
            target_dir = QFileDialog.getExistingDirectory(self, "选择保存所有片段的文件夹")
            if not target_dir: return # 用户取消选择文件夹
            
            saved_count = self._copy_clips(selected_clips, target_dir)
            QMessageBox.information(self, "保存成功", f"成功将 {saved_count} 个文件保存到指定目录。")
        else:
            # 如果只选中一个片段，则进行单个文件的另存为操作
            clip = selected_clips[0]
            # 默认保存路径为用户主目录，文件名沿用片段名称
            default_path = os.path.join(os.path.expanduser("~"), f"{clip['name']}.wav")
            save_path, _ = QFileDialog.getSaveFileName(self, "另存为", default_path, "WAV 音频 (*.wav)")
            
            if save_path: # 用户选择了保存路径
                try:
                    shutil.copy(clip['path'], save_path) # 复制文件
                    QMessageBox.information(self, "保存成功", f"文件已保存到:\n{save_path}")
                except Exception as e:
                    QMessageBox.critical(self, "保存失败", f"无法保存文件: {e}")

    def send_to_analysis(self):
        """
        将选中的录音片段发送到音频分析模块。
        单个文件发送到单文件模式，多个文件发送到批量分析模式。
        """
        selected_clips = self._get_selected_clips_info()
        if not selected_clips: return

        # 检查主窗口是否加载了音频分析模块
        if not hasattr(self.main_window, 'audio_analysis_page'):
            QMessageBox.warning(self, "功能缺失", "音频分析模块未加载，无法发送。")
            return
            
        audio_analysis_page = self.main_window.audio_analysis_page
        
        # 导航到音频分析模块的标签页
        target_page = self.main_window._navigate_to_tab("资源管理", "音频分析")
        if not target_page:
            QMessageBox.warning(self, "导航失败", "无法切换到音频分析模块。")
            return
        
        # 提取选中片段的文件路径
        clip_paths = [clip['path'] for clip in selected_clips]
        
        if len(clip_paths) == 1:
            # 如果只有一个文件，切换到音频分析的单文件模式并加载文件
            audio_analysis_page.mode_toggle.setChecked(False) # 确保是单文件模式
            QApplication.processEvents() # 强制UI更新以完成模式切换
            audio_analysis_page.load_audio_file(clip_paths[0])
        else:
            # 如果有多个文件，切换到音频分析的批量模式并加载文件列表
            audio_analysis_page.mode_toggle.setChecked(True) # 确保是批量模式
            QApplication.processEvents() # 强制UI更新以完成模式切换
            
            # 调用批量分析面板的外部加载API
            if hasattr(audio_analysis_page, 'batch_analysis_panel'):
                audio_analysis_page.batch_analysis_panel.load_files_from_external(clip_paths)
        
        self.accept() # 任务完成后关闭“随录”对话框

    # --- 辅助方法 ---
    def _get_selected_clips_info(self):
        """
        获取 QListWidget 中所有选中项对应的内部数据 (recorded_clips)。
        """
        selected_items = self.clip_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "未选择", "请先在列表中选择一个或多个片段。")
            return []
            
        # 从 AnimatedListWidget 的 HIERARCHY_DATA_ROLE 中获取选中项的文本 (名称)
        selected_names = [item.data(AnimatedListWidget.HIERARCHY_DATA_ROLE).get('text') for item in selected_items]
        
        # 返回与这些名称匹配的内部 recorded_clips 数据
        return [clip for clip in self.recorded_clips if clip['name'] in selected_names]

    def _copy_clips(self, clips_info, target_dir):
        """
        将指定的临时文件复制到目标目录，使用其当前名称。
        """
        saved_count = 0
        for clip in clips_info:
            target_path = os.path.join(target_dir, f"{clip['name']}.wav")
            try:
                shutil.copy(clip['path'], target_path)
                saved_count += 1
            except Exception as e:
                print(f"复制文件 {clip['name']} 失败: {e}", file=sys.stderr)
        return saved_count
        
    def _show_clip_context_menu(self, position):
        """
        当在列表上右键单击时，创建并显示上下文菜单。
        菜单选项根据选中项的数量而动态变化。
        """
        selected_items = self.clip_list.selectedItems()
        if not selected_items: return # 如果没有选中项，则不显示菜单

        menu = QMenu(self)
        
        # 播放操作：只在单选时启用
        play_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "播放")
        play_action.setEnabled(len(selected_items) == 1)

        # 重命名操作：只在单选时启用
        rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名")
        rename_action.setEnabled(len(selected_items) == 1)

        menu.addSeparator() # 分隔符

        # 移除操作：显示移除的片段数量
        remove_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), f"移除选中的 {len(selected_items)} 项")

        # 在鼠标位置执行菜单，并获取用户选择的动作
        action = menu.exec_(self.clip_list.mapToGlobal(position))

        # 根据用户的选择执行相应的操作
        if action == play_action:
            # 如果是播放，且只有单选，则播放当前点击或选中的项
            if len(selected_items) == 1:
                # 优先使用实际右键点击的项，否则使用当前选中项
                target_item = self.clip_list.itemAt(position) or selected_items[0]
                self.play_clip_from_item(target_item)
        elif action == rename_action:
            # 如果是重命名，且只有单选，则重命名当前点击或选中的项
            if len(selected_items) == 1:
                target_item = self.clip_list.itemAt(position) or selected_items[0]
                self.rename_clip(target_item)
        elif action == remove_action:
            # 如果是移除，则调用批量删除方法
            self.delete_selected_clips()

    # --- 对话框生命周期管理 ---
    def closeEvent(self, event):
        """
        对话框关闭时（包括用户点击X按钮、Esc键或 accept/reject 调用），
        确保所有临时资源被清理。
        """
        try:
            # 停止所有可能的播放和录制流
            self.player.stop()
            if self.stream:
                self.stream.stop()
                self.stream.close()
            self.update_timer.stop() # 停止音量计定时器

            # [核心清理] 递归删除整个临时文件夹及其所有内容
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                print(f"临时目录 '{self.temp_dir}' 已清理。")
        except Exception as e:
            # 打印错误但不要阻止对话框关闭，以防清理失败
            print(f"清理临时目录时发生错误: {e}", file=sys.stderr)
        
        super().closeEvent(event) # 调用父类的 closeEvent 方法完成关闭