# --- START OF FILE plugins/audio_device_tester/audio_tester.py ---

import os
import sys
import json
import numpy as np
import sounddevice as sd
import soundfile as sf
import tempfile
import time
import subprocess
from collections import deque # 用于音量计平滑
import queue # 用于线程间音量数据传输

# PyQt5 核心模块导入
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QListWidget, QListWidgetItem, QProgressBar, QLabel,
                             QMessageBox, QInputDialog, QMenu, QLineEdit, QApplication)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QRunnable, QThreadPool, QObject
from PyQt5.QtGui import QIcon

# 动态导入插件系统基类
try:
    from plugin_system import BasePlugin
except ImportError:
    # 回退路径，以防插件在非主程序环境中被独立测试
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 后台测试线程
# 负责并行监听多个音频设备，实时报告音量，并捕获设备打开错误。
# ==============================================================================
class TestWorker(QThread):
    volume_update = pyqtSignal(int, np.ndarray) # 信号传递 numpy 数组
    device_error = pyqtSignal(int, str)    # 设备打开错误信号: device_index, error_message
    device_disable_request = pyqtSignal(int) # 请求禁用设备的信号: device_index
    test_finished = pyqtSignal(dict)       # 所有设备测试完成信号: results_dict

    def __init__(self, devices_to_test, duration=None):
        """
        初始化 TestWorker。
        :param devices_to_test: 要测试的设备信息列表。
        :param duration: (可选) 测试的持续时间（秒）。如果为None，则持续运行直到stop()被调用。
        """
        super().__init__()
        self.devices_to_test = devices_to_test
        self.duration = duration # 用于区分“全部测试”和“单独测试”
        self._is_running = False 
        self.streams = [] # 存储所有已成功打开的音频流
        self.detected_sound = set() # 记录哪些设备检测到了有效声音
        self.opened_streams_info = {} # 存储成功打开设备的原始信息
        self.SILENCE_THRESHOLD_RMS = 0.002 # 使用与 SampleAnalysisWorker 一致的、更灵敏的阈值

    def run(self):
        """线程主入口点，执行音频设备的并行监听。"""
        self._is_running = True
        try:
            for dev in self.devices_to_test:
                try:
                    stream = sd.InputStream(
                        device=dev['index'],
                        channels=1,
                        samplerate=dev['default_samplerate'],
                        callback=lambda indata, f, t, s, index=dev['index']: self.audio_callback(indata, index)
                    )
                    stream.start()
                    self.streams.append(stream)
                    self.opened_streams_info[dev['index']] = dev
                except Exception as e:
                    error_str = str(e)
                    print(f"警告: 无法打开设备 {dev['index']} ({dev['name']}): {error_str}")
                    self.device_error.emit(dev['index'], error_str)
                    if "Invalid device" in error_str:
                        self.device_disable_request.emit(dev['index'])
                    continue

            if not self.streams and len(self.devices_to_test) > 0:
                self.test_finished.emit({})
                return

            # 根据是否有 duration 参数，决定运行模式
            if self.duration:
                # 定时模式 (用于“全部测试”)
                start_time = time.time()
                while self._is_running and time.time() - start_time < self.duration:
                    self.msleep(100)
            else:
                # 持续模式 (用于“单独测试”)
                while self._is_running:
                    self.msleep(100)

        except Exception as e:
            self.error.emit(f"音频测试线程发生严重错误: {e}")
        finally:
            self._is_running = False 
            for stream in self.streams:
                try: stream.stop(); stream.close()
                except Exception: pass
            self.streams = []

            results = {}
            for dev_id in self.opened_streams_info.keys():
                results[dev_id] = "has_signal" if dev_id in self.detected_sound else "no_signal"
            self.test_finished.emit(results)

    def audio_callback(self, indata, device_index):
        if not self._is_running: return
        self.volume_update.emit(device_index, indata.copy())
        
        # 使用 RMS 值进行声音检测，与样本分析保持一致
        rms = np.sqrt(np.mean(np.square(indata)))
        if rms > self.SILENCE_THRESHOLD_RMS:
            self.detected_sound.add(device_index)

    def stop(self):
        self._is_running = False

# ==============================================================================
# 后台录音线程
# 负责为单个设备录制短样本，并将结果通过信号返回。
# ==============================================================================
class RecordingWorker(QThread):
    recording_finished = pyqtSignal(bool, str) # 录制完成信号: success, filepath_or_error
    volume_update = pyqtSignal(int, np.ndarray) # 信号传递 numpy 数组

    def __init__(self, device_info, filepath):
        """
        初始化 RecordingWorker。
        :param device_info: 要录制样本的设备信息。
        :param filepath: 录制文件保存的临时路径。
        """
        super().__init__()
        self.device_info = device_info
        self.filepath = filepath
        self._is_running = False
        self.audio_data_queue = queue.Queue() # 使用队列进行线程间数据传输

    def run(self):
        self._is_running = True
        try:
            samplerate = int(self.device_info['default_samplerate'])
            
            def callback(indata, frames, time, status):
                if self._is_running: # 只有在线程被标记为运行时才收集数据
                    self.audio_data_queue.put(indata.copy())
                    self.volume_update.emit(self.device_info['index'], indata.copy())

            with sd.InputStream(device=self.device_info['index'], channels=1, samplerate=samplerate, callback=callback):
                while self._is_running: # 持续录制，直到外部调用 stop()
                    self.msleep(100) # 短暂休眠，避免CPU空转，等待 stop() 信号
            
            # 停止后，从队列中收集所有数据
            recorded_chunks = []
            while not self.audio_data_queue.empty():
                recorded_chunks.append(self.audio_data_queue.get_nowait())

            if not recorded_chunks:
                raise RuntimeError("录音数据为空，未采集到任何信号。请检查麦克风是否工作。")

            recording = np.concatenate(recorded_chunks, axis=0)
            sf.write(self.filepath, recording, samplerate)
            self.recording_finished.emit(True, self.filepath)

        except Exception as e:
            self.recording_finished.emit(False, str(e))

    def stop(self):
        self._is_running = False

# ==============================================================================
# 后台回放线程
# 负责在后台播放音频文件，避免UI卡顿。
# ==============================================================================
class PlaybackWorker(QThread):
    playback_finished = pyqtSignal() # 回放完成信号
    error = pyqtSignal(str)          # 回放错误信号

    def __init__(self, filepath):
        """
        初始化 PlaybackWorker。
        :param filepath: 要回放的音频文件路径。
        """
        super().__init__()
        self.filepath = filepath

    def run(self):
        """线程主入口点，执行音频回放。"""
        try:
            if not os.path.exists(self.filepath):
                raise FileNotFoundError("找不到要回放的样本文件。")
            
            # 读取并播放音频数据
            data, sr = sf.read(self.filepath)
            sd.play(data, sr)
            sd.wait() # 等待播放完成
            self.playback_finished.emit() # 发射完成信号
        except Exception as e:
            # 捕获播放错误
            self.error.emit(str(e)) # 发射错误信号

# ==============================================================================
# 后台样本分析工作器
# 负责在后台分析录制样本的音量，判断是否包含有效信号。
# ==============================================================================
class SampleAnalysisSignals(QObject):
    analysis_finished = pyqtSignal(int, str) # 分析完成信号: device_id, new_status

class SampleAnalysisWorker(QRunnable):
    """
    一个可以在 QThreadPool 中运行的分析任务，用于检查录制样本是否包含有效声音。
    """
    def __init__(self, device_id, filepath):
        """
        初始化 SampleAnalysisWorker。
        :param device_id: 设备的索引。
        :param filepath: 录制样本文件的路径。
        """
        super().__init__()
        self.device_id = device_id
        self.filepath = filepath
        self.signals = SampleAnalysisSignals()
        # 定义一个非常低的 RMS 阈值，低于此值视为静音
        self.SILENCE_THRESHOLD_RMS = 0.002 

    def run(self):
        """任务主入口点，执行样本分析。"""
        try:
            if not os.path.exists(self.filepath):
                raise FileNotFoundError("样本文件未找到。")
                
            data, sr = sf.read(self.filepath, dtype='float32')
            
            # 检查音频是否为空
            if data.size == 0:
                self.signals.analysis_finished.emit(self.device_id, "no_signal")
                return

            # 计算 RMS 值 (均方根)，衡量音频能量
            rms = np.sqrt(np.mean(np.square(data)))
            
            if rms < self.SILENCE_THRESHOLD_RMS:
                # 低于阈值，视为无信号（静音）
                self.signals.analysis_finished.emit(self.device_id, "no_signal")
            else:
                # 高于阈值，视为成功验证（有信号）
                self.signals.analysis_finished.emit(self.device_id, "has_signal") # 更改为 has_signal，而不是 verified

        except Exception as e:
            # 捕获分析过程中的错误
            print(f"样本分析失败: {e}")
            self.signals.analysis_finished.emit(self.device_id, "error")

# ==============================================================================
# 插件主类： AudioDeviceTesterPlugin
# 这是插件的入口点，负责其生命周期管理和与主程序的集成。
# ==============================================================================
class AudioDeviceTesterPlugin(BasePlugin):
    """
    录音设备测试器插件主类。
    负责初始化插件对话框，并对设置模块进行猴子补丁，以扩展设备列表功能。
    """
    def __init__(self, main_window, plugin_manager):
        """
        初始化插件实例。
        :param main_window: 主程序 MainWindow 实例。
        :param plugin_manager: PluginManager 实例。
        """
        super().__init__(main_window, plugin_manager)
        self.dialog = None # 存储对话框实例
        
        # [核心修正] 在这里初始化 self.plugin_dir
        self.plugin_dir = os.path.dirname(__file__) 

        # 配置文件路径现在可以安全地使用 self.plugin_dir
        self.config_path = os.path.join(self.plugin_dir, 'config.json') 
        self.device_config = self._load_device_config() # 加载设备配置
        self.original_populate_method = None # 用于存储被猴子补丁的原始方法

    def _load_device_config(self):
        """从插件的配置文件中加载设备配置（自定义名称、禁用状态等）。"""
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def setup(self):
        """
        插件启用时调用。
        主要任务是对设置模块的 populate_input_devices 方法进行猴子补丁，
        使其能显示自定义的设备信息。
        """
        settings_page = getattr(self.main_window, 'settings_page', None)
        if not settings_page:
            print("[Audio Tester] 警告: 未找到 'settings_page' 模块，无法应用设备列表补丁。")
            return True # 插件本身仍然可用，但联动功能失效

        if hasattr(settings_page, 'populate_input_devices'):
            self.original_populate_method = settings_page.populate_input_devices
            # 应用补丁：将原始方法替换为我们的补丁方法
            settings_page.populate_input_devices = self._patched_populate_input_devices
            print("[Audio Tester] 已成功对设置模块应用设备列表补丁。")
        
        return True

    def teardown(self):
        """
        插件禁用时调用。
        主要任务是恢复设置模块被猴子补丁的方法，并停止所有正在进行的测试。
        """
        if self.dialog:
            self.dialog.stop_all_tests() # 停止所有测试线程
            self.dialog.close() # 关闭对话框
        
        settings_page = getattr(self.main_window, 'settings_page', None)
        # 恢复原始方法，这是猴子补丁的关键一步，确保插件禁用后主程序行为恢复正常
        if settings_page and self.original_populate_method:
            settings_page.populate_input_devices = self.original_populate_method
            print("[Audio Tester] 已从设置模块移除设备列表补丁。")
        
        self.original_populate_method = None # 清除对原始方法的引用

    def execute(self, **kwargs):
        """
        执行插件。当用户从插件菜单点击时调用。
        采用单例模式，如果对话框已存在则显示，否则创建新的。
        """
        if self.dialog is None:
            # 将插件实例自身传递给对话框，以便对话框可以访问插件的配置和方法
            self.dialog = TesterDialog(self)
            self.dialog.finished.connect(self._on_dialog_finished) # 清除引用
        
        # 显示对话框并将其置于顶层，确保用户能看到它
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _on_dialog_finished(self):
        """当对话框关闭时，清除对话框实例的引用，以便下次可以重新创建。"""
        self.dialog = None
    
    def _patched_populate_input_devices(self):
        """
        这是替换 settings_module 中原始 populate_input_devices 方法的补丁版本。
        它在列出设备前，会读取本插件的配置文件，根据自定义名称和禁用状态过滤设备。
        """
        settings_page = self.main_window.settings_page
        settings_page.input_device_combo.clear() # 清空设备下拉列表
        is_simple_mode = settings_page.simple_mode_switch.isChecked()

        if is_simple_mode:
            # 简易模式不受影响，直接调用原始行为
            settings_page.input_device_combo.setToolTip("选择一个简化的录音设备类型。")
            settings_page.input_device_combo.addItem("智能选择 (推荐)", "smart")
            settings_page.input_device_combo.addItem("系统默认", "default")
            settings_page.input_device_combo.addItem("内置麦克风", "internal")
            settings_page.input_device_combo.addItem("外置设备 (USB/蓝牙等)", "external")
            settings_page.input_device_combo.addItem("电脑内部声音", "loopback")
        else: # 专家模式
            settings_page.input_device_combo.setToolTip("选择用于录制音频的物理麦克风设备。")
            try:
                devices = sd.query_devices() # 查询所有可用设备
                # 获取系统默认输入设备的索引
                default_input_idx = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else -1
                
                settings_page.input_device_combo.addItem("系统默认", None) # 添加“系统默认”选项
                
                for i, device in enumerate(devices):
                    if device['max_input_channels'] > 0: # 筛选出有输入通道的设备
                        device_id = str(i) # 设备索引作为唯一ID
                        
                        # 检查设备是否被禁用
                        if self.device_config.get(device_id, {}).get("disabled", False):
                            continue # 如果禁用，则跳过，不添加到设置列表
                        
                        # 检查是否有自定义名称
                        custom_name = self.device_config.get(device_id, {}).get("name")
                        if custom_name:
                            display_name = f"{custom_name}" # 显示自定义名称
                        else:
                            display_name = device['name'] # 否则显示原始名称
                        
                        # 判断是否为系统默认设备
                        is_default_system = (i == default_input_idx)
                        
                        # 在专家模式下，不显示原始名称的括号，避免冗余
                        settings_page.input_device_combo.addItem(f"{display_name}" + (" (系统推荐)" if is_default_system else ""), i)
            except Exception as e:
                print(f"获取录音设备失败 (补丁版): {e}")
                settings_page.input_device_combo.addItem("无法获取设备列表", -1)

# ==============================================================================
# 测试器主对话框：TesterDialog
# 提供设备列表、实时音量显示、样本录制/回放、设备管理和默认设置功能。
# ==============================================================================
class TesterDialog(QDialog):
    """
    录音设备测试器的主界面对话框。
    提供设备列表、实时音量显示、样本录制/回放、设备管理和默认设置功能。
    """
    def __init__(self, plugin_instance):
        """
        初始化 TesterDialog。
        :param plugin_instance: AudioDeviceTesterPlugin 实例。
        """
        super().__init__(plugin_instance.main_window)
        self.plugin = plugin_instance             # 插件实例
        self.main_window = plugin_instance.main_window # 主窗口实例
        self.icon_manager = self.main_window.icon_manager # 图标管理器
        
        self.devices = []          # 存储所有扫描到的设备信息
        self.device_widgets = {}   # 存储设备列表项对应的UI控件（QLabel, QProgressBar）
        self.active_test_workers = {} # 存储当前活动的 TestWorker 线程
        
        self.is_recording_sample = False # 录制状态标志
        self.recording_worker = None     # 存储当前录音 worker
        self.recorded_sample = None      # 存储录制的样本音频数据 (文件路径)
        self.sample_filepath = os.path.join(tempfile.gettempdir(), "phonacq_test_sample.wav") # 临时文件路径

        # 音量计平滑处理所需的状态变量
        self.volume_meter_queues = {}  # {device_id: queue}
        self.volume_histories = {}     # {device_id: deque}
        self.volume_update_timer = QTimer(self)
        self.volume_update_timer.timeout.connect(self.update_all_volume_meters)

        self._load_status_icons() # 加载状态图标（错误、警告、成功、已验证）
        self.analysis_thread_pool = QThreadPool() # 用于样本分析的线程池

        self.setWindowTitle("录音设备测试器")
        self.setMinimumSize(600, 700) # 调整窗口大小以容纳更多内容
        self._init_ui() # 初始化UI
        self._populate_device_list() # 填充设备列表

    def _load_status_icons(self):
        """
        [v1.1 健壮版] 从插件的 icons 目录和主程序的 icon_manager 加载各种状态图标。
        确保即使在图标被禁用或文件缺失时也不会导致程序崩溃。
        """
        self.status_icons = {}
        icon_dir = os.path.join(self.plugin.plugin_dir, 'icons')
        
        icons_to_load = ["error", "warn", "success", "checked", "record", "stop", "lock", "unlock"]
        
        for name in icons_to_load:
            final_icon = None # 初始化为空

            # 1. 优先尝试从主程序的 icon_manager 获取
            icon_from_manager = self.icon_manager.get_icon(name)
            if icon_from_manager and not icon_from_manager.isNull():
                final_icon = icon_from_manager

            # 2. 如果从 manager 获取失败，则尝试从插件本地文件加载
            if not final_icon:
                for ext in ['.png', '.svg']:
                    path = os.path.join(icon_dir, f"{name}{ext}")
                    if os.path.exists(path):
                        final_icon = QIcon(path)
                        break
            
            # --- [核心修正] ---
            # 3. 无论加载成功与否，都确保向字典中存入一个有效的 QIcon 对象。
            #    如果 final_icon 仍然是 None，就存入一个空的 QIcon()。
            #    这可以防止 setIcon() 接收到 NoneType 参数。
            self.status_icons[name] = final_icon if final_icon else QIcon()


    def _init_ui(self):
        """构建对话框的用户界面布局。"""
        layout = QVBoxLayout(self)
        
        # --- 顶部按钮栏：全局测试控制 ---
        top_layout = QHBoxLayout()
        self.test_all_btn = QPushButton("开始测试")
        self.test_all_btn.setIcon(self.icon_manager.get_icon("play_audio"))
        self.test_all_btn.setToolTip("并行监听所有<b>已启用</b>的设备5秒，快速识别哪个麦克风正在工作。<br>音量条会实时显示信号强度，测试结束后会标记出<b>有信号</b>和<b>无信号</b>的设备。")
        self.stop_all_btn = QPushButton("停止测试")
        self.stop_all_btn.setIcon(self.icon_manager.get_icon("stop"))
        self.stop_all_btn.setEnabled(False) # 初始禁用
        self.stop_all_btn.setToolTip("提前停止对所有设备的并行监听。")
        top_layout.addWidget(self.test_all_btn)
        top_layout.addWidget(self.stop_all_btn)
        top_layout.addStretch() # 填充空白
        layout.addLayout(top_layout)
        
        # --- 设备列表 (QListWidget) ---
        self.device_list_widget = QListWidget()
        self.device_list_widget.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单
        layout.addWidget(self.device_list_widget)
        
        # --- 底部操作栏：样本录制与默认设置 ---
        bottom_layout = QHBoxLayout()
        # 录制按钮初始文本和图标
        self.record_btn = QPushButton("开始录制样本") 
        self.record_btn.setIcon(self.status_icons.get("record")) # 使用 record 图标
        self.record_btn.setToolTip("开始为当前选中的设备录制一段音频，点击再次点击按钮停止录制。")
        
        self.playback_btn = QPushButton("回放样本")
        self.playback_btn.setIcon(self.icon_manager.get_icon("play_audio"))
        self.playback_btn.setToolTip("播放刚刚录制的音频样本。")
        
        # 初始禁用底部按钮
        self.record_btn.setEnabled(False)
        self.playback_btn.setEnabled(False)
        
        bottom_layout.addWidget(self.record_btn)
        bottom_layout.addWidget(self.playback_btn)
        bottom_layout.addStretch() # 填充空白
        layout.addLayout(bottom_layout)
        
        # --- 连接信号与槽 ---
        self.test_all_btn.clicked.connect(self.start_all_tests)
        self.stop_all_btn.clicked.connect(self.stop_all_tests)
        self.device_list_widget.currentItemChanged.connect(self.on_device_selected)
        self.device_list_widget.customContextMenuRequested.connect(self.show_context_menu) # 右键菜单
        self.device_list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.record_btn.clicked.connect(self.record_sample)
        self.playback_btn.clicked.connect(self.playback_sample)

    def _populate_device_list(self):
        current_row = self.device_list_widget.currentRow()
        scrollbar_pos = self.device_list_widget.verticalScrollBar().value()
        
        self.device_list_widget.clear(); self.devices = []; self.device_widgets = {}
        try:
            device_list = sd.query_devices()
            for i, dev in enumerate(device_list):
                if dev['max_input_channels'] > 0:
                    dev['index'] = i; self.devices.append(dev)
                    item = QListWidgetItem(); item_widget = QWidget(); item_layout = QHBoxLayout(item_widget)
                    item_layout.setContentsMargins(5, 5, 5, 5); item_layout.setSpacing(10)
                    
                    device_id = str(i); config = self.plugin.device_config.get(device_id, {})
                    custom_name = config.get("name")
                    status = config.get("status", "untested")
                    display_name = f"{custom_name}" if custom_name else dev['name']

                    status_icon_label = QLabel(); status_icon_label.setFixedSize(24, 24)
                    
                    # 构建包含更丰富信息的HTML字符串，并设置最小高度
                    info_html = (
                        f"<b>{display_name}</b><br>"
                        f"<small style='color: #666;'>{dev['default_samplerate']:.0f} Hz | {dev['max_input_channels']} 通道 | 索引: {i}</small>"
                    )
                    # 如果有自定义名称，再额外显示原始名称
                    if custom_name:
                        info_html += f"<br><small style='color: #888;'>原始名称: {dev['name']}</small>"

                    name_label = QLabel(info_html)
                    name_label.setWordWrap(True)
                    # 确保足够的最小高度，以容纳三行文本
                    name_label.setMinimumHeight(60) 

                    volume_bar = QProgressBar(); volume_bar.setRange(0, 100); volume_bar.setValue(0); volume_bar.setTextVisible(False)
                    
                    item_layout.addWidget(status_icon_label); item_layout.addWidget(name_label, 1); item_layout.addWidget(volume_bar, 1)
                    
                    self.device_list_widget.addItem(item); self.device_list_widget.setItemWidget(item, item_widget)
                    # 关键一步：让 item 的尺寸提示根据 widget 的实际大小来决定
                    item.setSizeHint(item_widget.sizeHint())
                    self.device_widgets[i] = {'item': item, 'bar': volume_bar, 'label': name_label, 'status_icon': status_icon_label}
                    self.update_status_icon(i, status, config.get("error_msg"))
        except Exception as e: QMessageBox.critical(self, "错误", f"无法获取音频设备列表:\n{e}")

        if 0 <= current_row < self.device_list_widget.count(): self.device_list_widget.setCurrentRow(current_row)
        self.device_list_widget.verticalScrollBar().setValue(scrollbar_pos)

    def update_status_icon(self, device_id, status, error_msg=None):
        if device_id not in self.device_widgets: return
        info = self.device_widgets[device_id]
        icon_label = info['status_icon']; icon = None; tooltip = ""
        
        # 首先获取设备的禁用状态
        config = self.plugin.device_config.setdefault(str(device_id), {})
        is_disabled = config.get("disabled", False)

        # 默认清除样式
        info['label'].setStyleSheet("")
        
        # 统一的状态机，确保 error 状态的最高优先级
        if status == "error":
            icon = self.status_icons.get("error")
            tooltip = f"<b>设备错误</b><br>{error_msg}"
            info['label'].setStyleSheet("color: grey;")
        elif is_disabled:
            # 如果设备被禁用（且没有错误），显示灰色文字，但不显示状态图标
            icon_label.clear() 
            tooltip = "此设备已被禁用。"
            info['label'].setStyleSheet("color: grey;")
        elif status == "no_signal":
            icon = self.status_icons.get("warn")
            tooltip = "<b>警告：未检测到信号</b><br>请检查设备是否已连接或静音。"
        elif status == "has_signal":
            icon = self.status_icons.get("success"); tooltip = "<b>检测到信号</b><br>设备工作正常。"
        elif status == "verified":
            icon = self.status_icons.get("checked"); tooltip = "<b>已验证</b><br>您已通过录制和回放确认此设备可用。"
        
        if icon: icon_label.setPixmap(icon.pixmap(24, 24)); icon_label.setToolTip(tooltip)
        else: icon_label.clear(); icon_label.setToolTip(tooltip or "此设备尚未测试。")
        
        config['status'] = status
        if error_msg: config['error_msg'] = error_msg
        else: config.pop('error_msg', None)
        
        self._save_config_only()

    def _save_config_only(self):
        """只保存插件的设备配置到文件，不刷新任何UI。"""
        try:
            with open(self.plugin.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.plugin.device_config, f, indent=4)
        except Exception as e:
            # 在这种静默保存中，我们只在控制台打印错误
            print(f"错误: 无法静默保存设备配置: {e}")
            
    def _reset_volume_bar(self, device_id):
        """将指定设备的音量条安全地重置为零。"""
        if device_id in self.device_widgets:
            self.device_widgets[device_id]['bar'].setValue(0)

    def on_device_selected(self, current, previous):
        """
        当用户在设备列表中选择一个设备时触发。
        控制底部操作按钮的启用状态。
        """
        # 如果没有选中任何设备，禁用所有相关按钮
        if not current:
            self.record_btn.setEnabled(False)
            self.playback_btn.setEnabled(False)
            return

        row = self.device_list_widget.row(current)
        if 0 <= row < len(self.devices):
            device_info = self.devices[row]
            device_id = str(device_info['index'])
            # 检查设备在配置文件中是否被标记为禁用
            is_disabled = self.plugin.device_config.get(device_id, {}).get("disabled", False)
            
            # 录制按钮的启用状态取决于是否禁用和是否正在录制
            self.record_btn.setEnabled(not is_disabled)
            # 如果是当前选中的设备，且正在录制，则按钮文本设为停止
            if self.is_recording_sample and self.recording_worker and self.recording_worker.device_info['index'] == device_info['index']:
                self.record_btn.setText("停止录制")
                self.record_btn.setIcon(self.status_icons.get("stop"))
            else:
                self.record_btn.setText("开始录制样本")
                self.record_btn.setIcon(self.status_icons.get("record"))

            # 回放按钮的状态取决于是否有录制样本 (filepath的存在)
            if self.recorded_sample and os.path.exists(self.sample_filepath):
                self.playback_btn.setEnabled(True)
            else:
                self.playback_btn.setEnabled(False)

    def on_item_double_clicked(self, item):
        """
        当用户双击设备项时，在Windows上打开系统的声音录制设备设置。
        """
        if sys.platform != "win32":
            return # 目前只支持Windows

        try:
            # 使用 os.system() 将整个命令作为一个字符串传递给系统命令解释器。
            # 'start' 命令会直接打开声音设置的“录制”标签页
            command = 'start control.exe mmsys.cpl,,1' 
            os.system(command)
            
        except Exception as e:
            QMessageBox.warning(self, "操作失败", f"无法打开系统声音设置:\n{e}")

    def start_all_tests(self):
        """
        开始对所有已启用设备的并行实时监听测试。
        """
        self.stop_all_tests() # 确保之前的所有测试已停止
        
        # 清除所有设备的状态图标和样式，准备重新测试
        for dev_id, info in self.device_widgets.items():
            info['label'].setStyleSheet("") # 清除变灰样式
            info['status_icon'].clear() # 清除图标
            # 确保将 config 中的 status 重置为 "untested"
            self.plugin.device_config.setdefault(str(dev_id), {})['status'] = "untested"
            # 刷新列表项的 Tooltip，因为 update_status_icon 在没有 status 时会设为“未测试”
            self.update_status_icon(dev_id, "untested") 

        # 筛选出未被禁用的设备进行测试
        devices_to_test = [dev for dev in self.devices if not self.plugin.device_config.get(str(dev['index']), {}).get("disabled", False)]
        
        if not devices_to_test:
            QMessageBox.information(self, "无可用设备", "没有可供测试的已启用录音设备。\n请检查设备是否被禁用。")
            return
        
        # 创建并启动 TestWorker 线程
        worker = TestWorker(devices_to_test, duration=5) # 传入 duration=5 参数
        self.active_test_workers['all'] = worker # 存储工作线程引用
        
        worker.volume_update.connect(self.process_volume_data) 
        worker.device_error.connect(self.mark_device_as_error) 
        worker.device_disable_request.connect(lambda dev_id: self.toggle_disable_device(str(dev_id), True)) 
        worker.test_finished.connect(self.on_test_finished) 
        
        # 启动音量计UI更新定时器
        if not self.volume_update_timer.isActive():
            self.volume_update_timer.start(50) 

        worker.start() # 启动线程

        # 更新UI按钮状态
        self.test_all_btn.setEnabled(False)
        self.stop_all_btn.setEnabled(True)

    def on_test_finished(self, results):
        """
        所有设备并行测试完成后调用的槽函数。
        根据测试结果更新每个设备的最终状态图标。
        """
        # 遍历测试结果，更新每个设备的图标和状态
        for dev_id, status in results.items():
            self.update_status_icon(dev_id, status)
        
        self.stop_all_tests() # 确保按钮状态被重置（因为测试已经自然结束）

    def stop_all_tests(self):
        """停止所有正在进行的并行监听测试。"""
        if 'all' in self.active_test_workers:
            worker = self.active_test_workers.pop('all')
            worker.stop() # 请求停止线程
            worker.quit() # 发送退出请求
            worker.wait() # 等待线程结束

        # 重置所有设备的音量条到零
        for dev_id in self.device_widgets.keys():
            self._reset_volume_bar(dev_id)
        
        # 恢复UI按钮状态
        self.test_all_btn.setEnabled(True)
        self.stop_all_btn.setEnabled(False)
        # 停止音量计UI更新定时器
        self.volume_update_timer.stop()

    def process_volume_data(self, device_id, data_chunk):
        """
        接收来自后台线程的原始音频数据，并将其放入对应设备的队列中。
        """
        if device_id not in self.volume_meter_queues:
            self.volume_meter_queues[device_id] = queue.Queue(maxsize=5) # 队列大小5
        
        try:
            # 使用非阻塞方式放入，如果队列已满则丢弃旧数据（保证实时性）
            if self.volume_meter_queues[device_id].full():
                self.volume_meter_queues[device_id].get_nowait() # 丢弃最旧的
            self.volume_meter_queues[device_id].put_nowait(data_chunk)
        except queue.Full:
            pass # 队列满时不做额外处理

    def update_all_volume_meters(self):
        """
        由QTimer触发，遍历所有设备，计算并平滑更新它们的音量条。
        """
        for device_id, widgets in self.device_widgets.items():
            q = self.volume_meter_queues.get(device_id)
            history = self.volume_histories.setdefault(device_id, deque(maxlen=5)) # 历史记录 deque 大小5
            
            raw_target_value = 0
            if q and not q.empty():
                try:
                    data_chunk = q.get_nowait()
                    if data_chunk is not None and data_chunk.any(): # 确保数据块不为空
                        rms = np.linalg.norm(data_chunk) / np.sqrt(len(data_chunk)) # 计算RMS
                        dbfs = 20 * np.log10(rms + 1e-9) # 转换为dBFS，加小量避免log(0)
                        # 将 -60dBFS 到 0dBFS 的范围映射到 0-100 的进度条值
                        raw_target_value = max(0, min(100, (dbfs + 60) * (100 / 60)))
                except queue.Empty:
                    raw_target_value = 0 # 队列空，音量为0
                except Exception as e:
                    print(f"Error calculating volume for device {device_id}: {e}")
                    raw_target_value = 0
            
            # 平滑处理：将当前原始目标值添加到历史记录，并计算平均值
            history.append(raw_target_value)
            smoothed_target_value = sum(history) / len(history)

            current_value = widgets['bar'].value()
            smoothing_factor = 0.4 # UI插值平滑因子
            new_value = int(current_value * (1 - smoothing_factor) + smoothed_target_value * smoothing_factor)
            
            # 如果当前值与平滑目标值非常接近，直接设为平滑目标值，避免微小抖动
            if abs(new_value - smoothed_target_value) < 2:
                new_value = int(smoothed_target_value)
            
            widgets['bar'].setValue(new_value)

    def mark_device_as_error(self, device_id, error_msg):
        """
        当设备在测试过程中打开失败时，更新其状态为“错误”并显示错误图标。
        """
        self.update_status_icon(device_id, "error", error_msg)
        self._reset_volume_bar(device_id) # 错误发生时立即将音量条归零

    def record_sample(self):
        """
        控制样本录制的开始和停止。
        """
        # 如果当前正在录制，则停止它
        if self.is_recording_sample:
            if self.recording_worker and self.recording_worker.isRunning():
                self.recording_worker.stop() # 请求停止线程
            # UI状态的恢复将在 on_sample_recorded 中处理
            return

        # 如果当前未在录制，则开始录制
        current_item = self.device_list_widget.currentItem()
        if not current_item: return
        
        # 在开始录制样本前，必须先停止所有并行的监听流，以释放设备
        self.stop_all_tests()
        
        row = self.device_list_widget.row(current_item)
        device_info = self.devices[row] # 获取选中设备的详细信息
        
        # 更新UI，进入录制状态
        self.is_recording_sample = True
        self.record_btn.setText("停止录制")
        self.record_btn.setIcon(self.status_icons.get("stop")) # 使用停止图标
        self.playback_btn.setEnabled(False) # 录制过程中禁用回放
        self.test_all_btn.setEnabled(False) # 录制时禁用全局测试
        
        self.recording_worker = RecordingWorker(device_info, self.sample_filepath)
        self.recording_worker.recording_finished.connect(self.on_sample_recorded) # 连接录制完成信号
        self.recording_worker.volume_update.connect(self.process_volume_data) 
        
        # 启动音量计UI更新定时器
        if not self.volume_update_timer.isActive():
            self.volume_update_timer.start(50)
            
        self.recording_worker.start()

    def on_sample_recorded(self, success, result_or_path):
        """
        样本录制完成后（无论是成功还是失败）调用的槽函数。
        """
        # 恢复UI状态
        self.is_recording_sample = False
        self.record_btn.setText("开始录制样本") # 恢复录制按钮文本
        self.record_btn.setIcon(self.status_icons.get("record")) # 恢复录制图标
        self.test_all_btn.setEnabled(True) # 恢复全局测试按钮状态
        # 只有在有选中项且未被禁用时才启用录制按钮
        current_item = self.device_list_widget.currentItem()
        if current_item:
            row = self.device_list_widget.row(current_item)
            device_id_str = str(self.devices[row]['index'])
            is_disabled = self.plugin.device_config.get(device_id_str, {}).get("disabled", False)
            self.record_btn.setEnabled(not is_disabled)
        else:
            self.record_btn.setEnabled(False)
        
        # 停止音量计UI更新定时器
        self.volume_update_timer.stop()

        row = self.device_list_widget.currentRow()
        if row == -1: return # 避免在无选中行时出错
        device_id = self.devices[row]['index']

        self._reset_volume_bar(device_id) # 无论成功与否，录制结束后都将对应音量条清零

        if success:
            self.playback_btn.setEnabled(True) # 成功录制后启用回放按钮
            
            # 录制成功，现在启动后台分析任务来验证录音内容
            self.recorded_sample = result_or_path # 记录录制样本的路径
            analysis_worker = SampleAnalysisWorker(device_id, self.recorded_sample)
            analysis_worker.signals.analysis_finished.connect(self.on_sample_analysis_finished) # 连接分析完成信号
            self.analysis_thread_pool.start(analysis_worker) # 启动分析线程
        else:
            # 录制过程本身就失败了
            self.playback_btn.setEnabled(False)
            self.recorded_sample = None # 清空已录制样本的引用
            error_msg = result_or_path
            QMessageBox.critical(self, "录制失败", f"无法录制样本:\n{error_msg}")
            self.update_status_icon(device_id, "error", error_msg) # 更新状态为错误

    def on_sample_analysis_finished(self, device_id, new_status):
        """
        样本分析完成后调用的槽函数。
        根据分析结果更新设备的最终状态图标和工具提示。
        """
        # 如果分析成功（has_signal），不直接设为verified，而是等待用户确认
        if new_status == "no_signal":
            self.update_status_icon(device_id, "no_signal")
            QMessageBox.warning(self, "验证失败", "录制的样本中未检测到有效声音信号。请检查麦克风或输入音量。")
        elif new_status == "error":
            self.update_status_icon(device_id, "error", "样本文件分析失败。")
        elif new_status == "has_signal": # 分析出信号，但还未用户确认
            self.update_status_icon(device_id, "has_signal") # 暂时标记为检测到信号
            # 此时不做其他动作，等待用户点击回放后进行确认

    def playback_sample(self):
        """
        播放最近录制的音频样本。
        """
        if not self.recorded_sample or not os.path.exists(self.sample_filepath):
            QMessageBox.warning(self, "无样本", "没有可供回放的录音样本。")
            return
            
        self.playback_btn.setText("播放中...")
        self.playback_btn.setEnabled(False) # 播放期间禁用按钮
        
        # 启动后台播放线程
        self.playback_worker = PlaybackWorker(self.sample_filepath)
        self.playback_worker.playback_finished.connect(self.on_playback_finished) # 连接播放完成信号
        self.playback_worker.error.connect(self.on_playback_error) # 连接播放错误信号
        self.playback_worker.start()

    def on_playback_finished(self):
        """播放完成后恢复回放按钮状态，并弹出确认对话框。"""
        self.playback_btn.setText("回放样本")
        self.playback_btn.setEnabled(True)

        # 弹出确认对话框，询问用户是否听到清晰声音
        current_item = self.device_list_widget.currentItem()
        if not current_item: return # 确保有选中设备
        row = self.device_list_widget.row(current_item)
        device_id = self.devices[row]['index']

        reply = QMessageBox.question(
            self,
            "确认设备可用性",
            "您是否能清晰地听到刚才录制的声音？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes # 默认选中“是”
        )

        if reply == QMessageBox.Yes:
            self.update_status_icon(device_id, "verified") # 用户确认，标记为“已验证”
            QMessageBox.information(self, "已确认", "设备已标记为‘已验证’。")
        else:
            self.update_status_icon(device_id, "no_signal") # 用户确认有问题，标记为“无信号”
            QMessageBox.warning(self, "请检查", "设备已标记为‘无信号’。请检查麦克风或音量。")

    def on_playback_error(self, error_msg):
        """播放出错时显示错误信息并恢复回放按钮。"""
        QMessageBox.critical(self, "回放失败", f"无法回放样本:\n{error_msg}")
        self.on_playback_finished() # 调用通用恢复逻辑 (会弹出确认框)
    
    def set_as_default(self):
        """
        将当前选中的设备设为程序默认的录音设备，并更新主程序的配置文件。
        同时会切换“程序设置”中的录音设备模式为“专家模式”。
        """
        current_item = self.device_list_widget.currentItem()
        if not current_item: return
        
        row = self.device_list_widget.row(current_item)
        device_info = self.devices[row]
        device_index = device_info['index']
        
        # 确认对话框，使用富文本格式显示设备名称
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Question)
        msg_box.setWindowTitle("确认操作")
        msg_box.setTextFormat(Qt.RichText) # 启用HTML渲染
        msg_box.setText(f"您确定要将设备:<br><br><b>{device_info['name']}</b><br><br>设为整个程序的默认录音设备吗？")
        msg_box.setInformativeText("(这会将设置中的模式更改为'专家模式')")
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.Yes)
        
        reply = msg_box.exec_()
        if reply != QMessageBox.Yes: return

        try:
            # 修改主程序的配置
            config = self.main_window.config
            audio_settings = config.setdefault("audio_settings", {})
            audio_settings["input_device_mode"] = "manual" # 切换到专家模式
            audio_settings["input_device_index"] = device_index # 设置选中的设备索引
            
            # 保存主程序配置
            with open(self.main_window.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            
            QMessageBox.information(self, "成功", f"设备 '{device_info['name']}' 已成功设为默认！")
        except Exception as e:
            QMessageBox.critical(self, "设置失败", f"无法保存设置:\n{e}")

    def show_context_menu(self, position):
        """
        显示设备列表的右键上下文菜单，提供设备管理选项。
        """
        item = self.device_list_widget.itemAt(position)
        if not item: return # 如果未点击到有效项，则返回
        
        row = self.device_list_widget.row(item)
        device_info = self.devices[row]
        device_id = str(device_info['index'])
        # 检查设备当前是否在插件配置中被禁用
        is_disabled = self.plugin.device_config.get(device_id, {}).get("disabled", False)
        
        menu = QMenu(); menu.setStyleSheet(self.main_window.styleSheet()) # 应用主窗口的QSS样式
        
        # “开始/停止单独测试”选项
        if device_id in self.active_test_workers:
            menu.addAction(self.icon_manager.get_icon("stop"), "停止单独测试", lambda: self.stop_single_test(device_id))
        else:
            menu.addAction(self.icon_manager.get_icon("play_audio"), "开始单独测试", lambda: self.start_single_test(device_info))
        
        menu.addSeparator() # 分隔线

        # “重命名”选项
        menu.addAction(self.icon_manager.get_icon("rename"), "重命名...", lambda: self.rename_device(device_id, device_info))
        
        # “设为默认”选项
        set_default_action = menu.addAction(self.icon_manager.get_icon("check"), "设为程序默认设备")
        set_default_action.setEnabled(not is_disabled) # 只有启用的设备才能设为默认
        set_default_action.triggered.connect(self.set_as_default) # 连接到现有的 set_as_default 方法
        
        menu.addSeparator() # 在禁用/启用前加分隔线

        # “禁用/启用”选项
        if is_disabled:
            menu.addAction(self.status_icons.get("unlock"), "启用此设备", lambda: self.toggle_disable_device(device_id, False))
        else:
            menu.addAction(self.status_icons.get("lock"), "禁用此设备", lambda: self.toggle_disable_device(device_id, True))
        
        menu.exec_(self.device_list_widget.mapToGlobal(position)) # 在鼠标位置显示菜单

    def start_single_test(self, device_info):
        """
        开始对单个设备的实时监听测试。
        """
        device_id_str = str(device_info['index'])
        self.stop_single_test(device_id_str) # 确保之前的已停止
        
        worker = TestWorker([device_info]) # 不传入 duration 参数，使其无限期运行
        self.active_test_workers[device_id_str] = worker
        
        worker.volume_update.connect(self.process_volume_data) 
        worker.device_error.connect(self.mark_device_as_error)
        worker.test_finished.connect(self.on_test_finished) # 确保为单独测试也连接 test_finished 信号
        
        # 启动音量计UI更新定时器
        if not self.volume_update_timer.isActive():
            self.volume_update_timer.start(50)

        worker.start()

    def stop_single_test(self, device_id):
        """
        停止对单个设备的实时监听测试。
        """
        if device_id in self.active_test_workers:
            worker = self.active_test_workers.pop(device_id)
            worker.stop() # 请求停止线程
            worker.quit() # 发送退出请求
            worker.wait() # 等待线程结束
            self._reset_volume_bar(int(device_id)) # 将对应设备的音量条归零
    
    def rename_device(self, device_id, device_info):
        """
        重命名指定设备，并保存到插件配置。
        """
        config = self.plugin.device_config.get(device_id, {})
        current_name = config.get("name", "") # 获取当前自定义名称
        
        # 弹出输入对话框
        new_name, ok = QInputDialog.getText(self, "重命名设备", f"为 '{device_info['name']}' 输入新名称:", QLineEdit.Normal, current_name)
        
        if ok: # 如果用户点击了确定
            config["name"] = new_name.strip() # 保存新名称（去除空白）
            self.plugin.device_config[device_id] = config # 更新插件配置
            self._save_and_refresh() # 保存配置并刷新UI

    def toggle_disable_device(self, device_id, disable):
        """
        启用或禁用指定设备，并保存到插件配置。
        """
        config = self.plugin.device_config.setdefault(device_id, {})
        config["disabled"] = disable # 设置禁用状态
        self._save_and_refresh() # 保存配置并刷新UI

    def _save_and_refresh(self, refresh_ui=True):
        """保存配置，并根据需要刷新UI。"""
        self._save_config_only() # 先调用只保存的方法
        
        if refresh_ui:
            # 只有在需要时才刷新整个UI
            self._populate_device_list()
            settings_page = getattr(self.main_window, 'settings_page', None)
            if settings_page:
                # 无论是否可见，都直接调用 populate，这比 load_settings 更轻量且安全
                settings_page.populate_input_devices()

    def closeEvent(self, event):
        """
        对话框关闭事件处理。确保停止所有测试并清理临时文件。
        """
        self.stop_all_tests() # 停止所有并行测试
        # 停止所有可能仍在运行的单个设备测试线程
        # 确保遍历的是拷贝，因为 pop 会修改字典
        for worker_id in list(self.active_test_workers.keys()):
            worker = self.active_test_workers.pop(worker_id) # 从活跃列表中移除
            worker.stop(); worker.quit(); worker.wait() # 停止线程并等待结束
        
        # 清理临时录音文件
        if os.path.exists(self.sample_filepath):
            try: os.remove(self.sample_filepath)
            except OSError: pass
        
        super().closeEvent(event) # 调用父类的 closeEvent