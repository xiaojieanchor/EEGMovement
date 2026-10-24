"""Native acquisition workbench. Display animation never edits raw samples."""
from __future__ import annotations
import html
import json
import math
import os
import queue
import threading
import time
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets
from .gui import AcquisitionController,SettingsStore,load_enrollment
from .client import CAPTURE_LABELS,CAPTURE_NAMES,capture_task_spec
from .display import EventLog,envelope_points
from . import __version__

TEAL='#147d73';INK='#193336';MUTED='#6d8082';BORDER='#e2eae8'


def ensure_fonts():
    # The offscreen Qt platform does not enumerate Windows fonts on every build.
    # Load from the target OS only; no Microsoft font is redistributed in the ZIP.
    if 'Microsoft YaHei UI' not in QtGui.QFontDatabase.families():
        base=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        for name in ['msyh.ttc','msyhbd.ttc','segoeui.ttf','consola.ttf']:
            if (base/name).exists():QtGui.QFontDatabase.addApplicationFont(str(base/name))


def label(text,name=None):
    widget=QtWidgets.QLabel(text)
    if name:widget.setObjectName(name)
    return widget


class MotionButton(QtWidgets.QPushButton):
    def __init__(self,text,primary=False,parent=None):
        super().__init__(text,parent);self.primary=primary;self.hover=0.;self.setCursor(QtCore.Qt.PointingHandCursor);self.setMinimumHeight(38)
        self.animation=QtCore.QVariantAnimation(self,duration=150);self.animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._value);self.setMinimumWidth(88)
    def _value(self,value):self.hover=float(value);self.update()
    def _animate(self,target):
        if getattr(self,'reduce_motion',False):self.animation.stop();self._value(target);return
        self.animation.stop();self.animation.setStartValue(self.hover);self.animation.setEndValue(target);self.animation.start()
    def enterEvent(self,event):self._animate(1.);super().enterEvent(event)
    def leaveEvent(self,event):self._animate(0.);super().leaveEvent(event)
    def paintEvent(self,event):
        painter=QtGui.QPainter(self);painter.setRenderHint(QtGui.QPainter.Antialiasing)
        a=QtGui.QColor(TEAL if self.primary else '#ffffff');b=QtGui.QColor('#09655c' if self.primary else '#edf6f2')
        color=QtGui.QColor.fromRgbF(*[a.getRgbF()[i]*(1-self.hover)+b.getRgbF()[i]*self.hover for i in range(4)])
        if not self.isEnabled():color=QtGui.QColor('#e9eeec')
        painter.setBrush(color);painter.setPen(QtGui.QPen(QtGui.QColor(TEAL if self.primary else BORDER),1))
        painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),9,9)
        painter.setPen(QtGui.QColor('#ffffff' if self.primary and self.isEnabled() else INK if self.isEnabled() else '#9aa9a6'))
        painter.setFont(self.font());painter.drawText(self.rect(),QtCore.Qt.AlignCenter,self.text())
        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor('#4a9f95'),1,QtCore.Qt.DotLine));painter.setBrush(QtCore.Qt.NoBrush);painter.drawRoundedRect(self.rect().adjusted(4,4,-4,-4),6,6)


class CaptureKey(QtWidgets.QPushButton):
    pressed_hold=QtCore.Signal(str)
    released_hold=QtCore.Signal(str)
    def __init__(self,action_label,text,diagonal=False,parent=None):
        super().__init__(text,parent);self.action_label=action_label;self.diagonal=diagonal;self.held=False;self.hover=False;self.pressing=False
        self.setCursor(QtCore.Qt.PointingHandCursor);self.setMinimumSize(86,58)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,QtWidgets.QSizePolicy.Expanding)
        font=QtGui.QFont('Microsoft YaHei UI',11 if diagonal else 13,QtGui.QFont.DemiBold);self.setFont(font)
        self.setAccessibleName('按住标注'+text)
    def set_held(self,held):
        if self.held!=bool(held):self.held=bool(held);self.update()
    def enterEvent(self,event):self.hover=True;self.update();super().enterEvent(event)
    def leaveEvent(self,event):self.hover=False;self.update();super().leaveEvent(event)
    def mousePressEvent(self,event):
        if event.button()==QtCore.Qt.LeftButton and self.isEnabled():
            self.pressing=True;self.pressed_hold.emit(self.action_label);event.accept();return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.pressing:
            pos=event.position().toPoint() if hasattr(event,'position') else event.pos()
            if not self.rect().contains(pos):
                self.pressing=False;self.released_hold.emit(self.action_label);event.accept();return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==QtCore.Qt.LeftButton and self.pressing:
            self.pressing=False;self.released_hold.emit(self.action_label);event.accept();return
        super().mouseReleaseEvent(event)
    def paintEvent(self,event):
        painter=QtGui.QPainter(self);painter.setRenderHint(QtGui.QPainter.Antialiasing)
        enabled=self.isEnabled()
        rect=self.rect().adjusted(3,3,-3,-3) if self.held else self.rect().adjusted(1,1,-1,-1)
        bg=QtGui.QColor('#d0e7ef' if self.held else '#e5f0f5' if (self.hover and enabled) else '#f2f6f8')
        border=QtGui.QColor('#62a5bc' if self.held else '#d9e3e8' if enabled else '#e4ecee')
        painter.setBrush(bg);painter.setPen(QtGui.QPen(border,2 if self.held else 1))
        painter.drawRoundedRect(rect,18,18)
        if self.held:
            painter.setBrush(QtCore.Qt.NoBrush);painter.setPen(QtGui.QPen(QtGui.QColor('#91c5d6'),2))
            painter.drawRoundedRect(rect.adjusted(2,2,-2,-2),16,16)
        painter.setPen(QtGui.QColor('#263e4b' if enabled else '#9aa9b2'))
        painter.setFont(self.font());painter.drawText(rect,QtCore.Qt.AlignCenter,self.text())


class Metric(QtWidgets.QFrame):
    def __init__(self,title,unit,note):
        super().__init__();self.setObjectName('metric');layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(18,12,18,12);layout.setSpacing(3)
        layout.addWidget(label(title,'eyebrow'));row=QtWidgets.QHBoxLayout();self.number=label('—','number');row.addWidget(self.number);row.addWidget(label(unit,'muted'));row.addStretch();layout.addLayout(row)
        self.note=label(note,'small');layout.addWidget(self.note)
    def set(self,value,note=None):
        self.number.setText(str(value))
        if note is not None:self.note.setText(note)


class Waveform(QtWidgets.QWidget):
    COLORS=['#1d8f7d','#318dc5','#7c79c9','#d29b50','#288b97','#679d59','#a877a2','#5e8dc4']
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumHeight(265);self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,QtWidgets.QSizePolicy.Expanding)
        self.preview=None;self.page=0;self.gain=100.;self.paths=[];self.frozen=False;self.reduced=False;self.last_read=None;self.trace_stale=True
        self.timer=QtCore.QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self._frame);self.timer.start()
    def _frame(self):
        stale=not self.last_read or not 0<=time.monotonic_ns()-self.last_read<500_000_000
        if self.isVisible() and self.preview is not None and not self.frozen and stale!=self.trace_stale:
            self.trace_stale=stale;self.update()
    def set_preview(self,preview):
        if self.frozen:return
        self.preview=preview;self.last_read=preview.get('read_monotonic_ns');self.trace_stale=False;self._prepare();self.update()
    def resizeEvent(self,event):self._prepare();super().resizeEvent(event)
    def _prepare(self):
        self.paths=[]
        if not self.preview or self.preview.get('descriptor') is None:return
        descriptor=self.preview['descriptor'];eeg=[i for i,c in enumerate(descriptor['channels']) if c['kind']=='EEG']
        selected=eeg[self.page*8:self.page*8+8];data=self.preview['samples'];width=max(1,self.width()-116);row=(self.height()-43)/max(1,len(selected))
        fs=descriptor['sampling_rate'];duration=self.preview.get('seconds',5)
        for ri,col in enumerate(selected):
            segments=envelope_points(data[:,col],max(50,int(width/2))) if len(data) else []
            paths=[];center=15+(ri+.5)*row
            for segment in segments:
                path=QtGui.QPainterPath()
                for j,(index,value) in enumerate(segment):
                    x=84+(duration-(len(data)-1-index)/fs)/duration*width;y=center-max(-2.,min(2.,value/self.gain))*row*.36
                    if j==0:path.moveTo(x,y)
                    else:path.lineTo(x,y)
                paths.append(path)
            clipped=bool(np.any(np.isfinite(data[:,col]) & (np.abs(data[:,col])>self.gain))) if len(data) else False
            self.paths.append((descriptor['channels'][col]['name'],center,paths,self.COLORS[ri%8],row,clipped))
    def paintEvent(self,event):
        p=QtGui.QPainter(self);p.setRenderHint(QtGui.QPainter.Antialiasing);p.fillRect(self.rect(),QtGui.QColor('#fcfefd'))
        width=max(1,self.width()-116);bottom=self.height()-28;duration=self.preview.get('seconds',5) if self.preview else 5
        p.setFont(QtGui.QFont('Consolas',9))
        for tick in range(duration+1):
            x=84+width*tick/duration;p.setPen(QtGui.QPen(QtGui.QColor('#e7eeeb'),1));p.drawLine(QtCore.QPointF(x,12),QtCore.QPointF(x,bottom))
            p.setPen(QtGui.QColor('#91a29d'));p.drawText(QtCore.QRectF(x-22,bottom+5,44,20),QtCore.Qt.AlignCenter,f'{tick-duration} s')
        if not self.paths:
            p.setPen(QtGui.QColor('#8b9e98'));p.setFont(QtGui.QFont('Microsoft YaHei UI',12));p.drawText(self.rect(),QtCore.Qt.AlignCenter,'等待原厂共享会话的数据\n接收后显示实际 EEG 波形，不生成演示信号');return
        elapsed=max(0,(time.monotonic_ns()-self.last_read)/1e9) if self.last_read else 0
        shift=0 if self.frozen or self.reduced else min(.25,elapsed)/duration*width
        for name,center,paths,color,row,clipped in self.paths:
            p.setPen(QtGui.QPen(QtGui.QColor('#e7eeeb'),1));p.drawLine(QtCore.QPointF(84,center),QtCore.QPointF(84+width,center))
            p.setPen(QtGui.QColor(INK));p.setFont(QtGui.QFont('Microsoft YaHei UI',9));p.drawText(QtCore.QRectF(10,center-15,66,18),QtCore.Qt.AlignVCenter,name)
            p.setFont(QtGui.QFont('Consolas',8));p.setPen(QtGui.QColor('#8c9e98'));p.drawText(QtCore.QRectF(10,center+2,70,16),QtCore.Qt.AlignVCenter,f'±{self.gain:g} µV')
            if clipped:
                p.setPen(QtGui.QColor('#b17b36'));p.drawText(QtCore.QRectF(84+width+3,center-8,28,16),QtCore.Qt.AlignVCenter,'限幅')
            p.save();p.setClipRect(QtCore.QRectF(84,center-row*.36,width,row*.72));p.translate(-shift,0)
            # Dense EEG traces use a one-pixel cosmetic stroke. Software
            # antialiasing otherwise holds the GUI/GIL across native raster work.
            p.setRenderHint(QtGui.QPainter.Antialiasing,False)
            pen=QtGui.QPen(QtGui.QColor(color if elapsed<.5 else '#a8b9b3'),1);pen.setCosmetic(True);p.setPen(pen)
            for path in paths:p.drawPath(path)
            p.restore()


STYLE='''
QMainWindow,QWidget#root {background:#f1f5f2;color:#193336;font-family:"Microsoft YaHei UI";font-size:12px;}
QFrame#sidebar {background:#ffffff;border-right:1px solid #e2eae8;}
QFrame#panel,QFrame#metric {background:#ffffff;border:1px solid #e1e9e5;border-radius:13px;}
QLabel#brand {font-size:20px;font-weight:700;letter-spacing:1px;color:#147d73;}
QLabel#title {font-size:25px;font-weight:700;color:#193336;}
QLabel#heading {font-size:15px;font-weight:600;color:#263e3b;}
QLabel#number {font-size:25px;font-family:"Segoe UI";font-weight:600;}
QLabel#eyebrow {font-size:11px;color:#748780;}
QLabel#muted {color:#788c85;font-size:11px;}
QLabel#small {color:#84958e;font-size:10px;}
QLabel#captureState {font-size:15px;font-weight:600;color:#254653;}
QLabel#captureHint {font-size:11px;color:#627780;}
QLabel#captureDetail {font-size:10px;color:#73848d;}
QLabel#pill {color:#147d73;background:#e8f5ee;border:1px solid #d3e9dc;border-radius:12px;padding:5px 12px;}
QLineEdit,QComboBox {background:#fafcfb;border:1px solid #dce6e0;border-radius:7px;padding:8px;color:#28423c;selection-background-color:#cce7dc;}
QLineEdit:focus,QComboBox:focus {border:1px solid #63ab95;background:white;}
QComboBox::drop-down {border:none;width:20px;}
QCheckBox {spacing:6px;color:#6d8082;font-size:11px;}
QCheckBox::indicator {width:14px;height:14px;border:1px solid #c5d6cc;border-radius:4px;background:white;}
QCheckBox::indicator:checked {background:#4c9b81;border-color:#4c9b81;}
QTextBrowser {background:#fbfdfb;border:none;border-radius:8px;font-size:11px;padding:5px;}
QScrollArea {border:none;background:transparent;}
QScrollBar:vertical {background:transparent;width:7px;margin:2px;}
QScrollBar::handle:vertical {background:#cad8cf;border-radius:3px;min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QToolTip {background:#193b32;color:white;border:none;padding:7px;}
'''


class Workbench(QtWidgets.QMainWindow):
    def __init__(self,controller=None,settings_path=None):
        super().__init__();ensure_fonts();self.setWindowTitle(f'EEGMovement · g.Nautilus 采集工作台 v{__version__}');self.setMinimumSize(1000,680)
        screen=QtGui.QGuiApplication.primaryScreen().availableGeometry();self.resize(min(1420,screen.width()-50),min(900,screen.height()-50))
        self.controller=controller or AcquisitionController();self.jobs=queue.Queue();self.busy=False;self.events=EventLog(600);self.last_collector_seq=0;self.last_ui_seq=0
        self.recovery=None;self.recovery_journal=None;self.last_preview=None;self.last_descriptor=None;self.closed_after_stop=False
        base=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'EEGMovement/gNautilus'
        self.settings=SettingsStore(settings_path or base/'settings.json')
        self.identity_path=self.settings.path.parent/'relay-identity.json'
        self.enrollment_jobs=queue.Queue();self.enrollment_thread=None;self.enrollment_stop=threading.Event()
        self.enrollment_state='idle';self.enrollment_message='';self.enrollment_generation=0;self.closing=False
        self.status_link=None
        try:profile=self.settings.load()
        except (OSError,ValueError,TypeError):profile={};self.events.add('校验','WARN','界面设置文件无法读取，已使用默认路径；原始记录不受影响')
        defaults=dict(sdk_dir='C:/Program Files/gtec/gNEEDaccess Client API',serial='',pairing_path='',storage_root=str(base/'recordings'),layout_path='',connection_mode='automatic',lan_host='');defaults.update(profile)
        # Connection identity is internal application state, never a user input.
        # Even explicitly saved legacy paths migrate to automatic public mode.
        defaults['pairing_path']=str(self.identity_path)
        root=QtWidgets.QWidget();root.setObjectName('root');self.setCentralWidget(root);layout=QtWidgets.QHBoxLayout(root);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        sidebar=QtWidgets.QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(274);side=QtWidgets.QVBoxLayout(sidebar);side.setContentsMargins(23,26,23,20);side.setSpacing(12)
        side.addWidget(label('EEGMovement','brand'));side.addWidget(label('g.Nautilus  /  ACQUISITION','small'));side.addSpacing(16)
        side.addWidget(label('连接与保存','heading'));side.addWidget(label('原厂负责设备配置与启停','muted'))
        self.fields={}
        for name,key,directory in [('Client API目录','sdk_dir',True),('设备序列号','serial',None),('本地记录目录','storage_root',True),('实际位置文件 · 可选','layout_path',False)]:
            side.addWidget(label(name,'eyebrow'));row=QtWidgets.QHBoxLayout();row.setSpacing(6)
            field=QtWidgets.QLineEdit(defaults[key]);field.setAccessibleName(name);field.setToolTip(defaults[key]);self.fields[key]=field;row.addWidget(field)
            if directory is not None:
                choose=MotionButton('…');choose.setFixedWidth(32);choose.setMinimumWidth(32);choose.clicked.connect(lambda checked=False,k=key,d=directory:self.browse(k,d));row.addWidget(choose)
            side.addLayout(row)
        side.addWidget(label('中枢传输方式','eyebrow'));self.connection_mode_box=QtWidgets.QComboBox()
        for title,value in [('公网中转连接（保留现有逻辑）','automatic'),('中枢热点／局域网 WebSocket 直连','lan_ws'),('热点直连优先 · 自动回退公网','auto_lan_first')]:self.connection_mode_box.addItem(title,value)
        self.connection_mode_box.setCurrentIndex(max(0,self.connection_mode_box.findData(defaults.get('connection_mode','automatic'))));side.addWidget(self.connection_mode_box)
        self.fields['lan_host']=QtWidgets.QLineEdit(defaults.get('lan_host',''));self.fields['lan_host'].setPlaceholderText('热点地址（留空自动发现 如192.168.137.1:8768）');self.fields['lan_host'].setAccessibleName('中枢热点或局域网地址');side.addWidget(self.fields['lan_host'])
        self.connection_mode_box.currentIndexChanged.connect(self._on_connection_mode_changed)
        self.fields['lan_host'].editingFinished.connect(self._on_connection_mode_changed)
        side.addWidget(label('公网连接路径','eyebrow'));self.proxy_mode=QtWidgets.QComboBox()
        for title,value in [('自动选择','auto'),('直接连接','direct'),('系统代理','system'),('手动HTTP代理','manual')]:self.proxy_mode.addItem(title,value)
        self.proxy_mode.setCurrentIndex(max(0,self.proxy_mode.findData(defaults.get('proxy_mode','auto'))));side.addWidget(self.proxy_mode)
        self.fields['proxy_url']=QtWidgets.QLineEdit(defaults.get('proxy_url',''));self.fields['proxy_url'].setPlaceholderText('http://127.0.0.1:7890');side.addWidget(self.fields['proxy_url'])
        self.proxy_mode.currentIndexChanged.connect(self.save_profile)
        self.discover_button=MotionButton('发现本机EEG设备');self.discover_button.setToolTip('分层检查SDK、GDS服务及所有可见设备；未建立原厂会话的设备也会显示');self.discover_button.clicked.connect(self.discover);side.addWidget(self.discover_button)
        self.recover_button=MotionButton('补传已有记录');self.recover_button.clicked.connect(self.recover);side.addWidget(self.recover_button)
        self.reduce=QtWidgets.QCheckBox('减少界面动效');side.addWidget(self.reduce);side.addStretch()
        hint=label('本地优先保存\n断网可保留原始记录\n电极接触质量需另行验证','muted');hint.setWordWrap(True);side.addWidget(hint)
        area=QtWidgets.QScrollArea();area.setWidgetResizable(True);main=QtWidgets.QWidget();main.setObjectName('root');area.setWidget(main)
        body=QtWidgets.QVBoxLayout(main);body.setContentsMargins(25,24,25,20);body.setSpacing(15)
        header=QtWidgets.QHBoxLayout();titles=QtWidgets.QVBoxLayout();titles.setSpacing(4);titles.addWidget(label('信号采集工作台','title'));self.subtitle=label('原始数据、连接状态与中枢回执，一处查看。','muted');titles.addWidget(self.subtitle);header.addLayout(titles);header.addStretch()
        self.badge=label('尚未开始','pill');header.addWidget(self.badge);body.addLayout(header)
        actions=QtWidgets.QHBoxLayout();self.probe_button=MotionButton('连接中枢');self.cancel_enrollment_button=MotionButton('取消连接');self.cancel_enrollment_button.hide();self.start_button=MotionButton('开始接收',True);self.stop_button=MotionButton('结束并保存');self.stop_button.setEnabled(False)
        for b in [self.probe_button,self.cancel_enrollment_button,self.start_button,self.stop_button]:actions.addWidget(b)
        self.cancel_enrollment_button.clicked.connect(self.cancel_enrollment)
        self.probe_button.clicked.connect(self.probe);self.start_button.clicked.connect(self.begin);self.stop_button.clicked.connect(self.stop);actions.addStretch()
        self.status_text=label('等待原厂共享会话','muted');self.status_text.setWordWrap(True);self.status_text.setFixedHeight(38);self.status_text.setMinimumWidth(175);actions.addWidget(self.status_text);body.addLayout(actions)
        self.device_diagnosis=None
        self.device_status=label('本机设备尚未检测 · 点击“发现本机EEG设备”检查SDK、GDS与原厂会话。','muted')
        self.device_status.setWordWrap(True);self.device_status.setAccessibleName('本机EEG设备诊断');body.addWidget(self.device_status)
        pairing_panel=QtWidgets.QFrame();pairing_panel.setObjectName('panel');pairing_box=QtWidgets.QVBoxLayout(pairing_panel);pairing_box.setContentsMargins(14,9,14,9);pairing_box.setSpacing(3)
        self.pairing_status=label('等待自动连接中枢','heading');self.pairing_status.setWordWrap(True);pairing_box.addWidget(self.pairing_status)
        self.pairing_help=label('设备类型：EEG采集端。启动后自动等待中枢并保存本机身份，无需配对文件或连接码。','muted');self.pairing_help.setWordWrap(True);pairing_box.addWidget(self.pairing_help);body.addWidget(pairing_panel)
        metrics=QtWidgets.QGridLayout();metrics.setSpacing(12);self.metrics_grid=metrics
        self.saved=Metric('本地已保存','行','原始 float32 · 持久化后计数');self.rate=Metric('设备采样率','Hz','读取实际配置，不设定采样率')
        self.backlog=Metric('等待中枢确认','块','未确认数据留在本地日志');self.confirmed=Metric('中枢持久化前缀','块','收到与落盘确认分别记录')
        self.metric_cards=[self.saved,self.rate,self.backlog,self.confirmed]
        for i,card in enumerate(self.metric_cards):metrics.addWidget(card,0,i)
        body.addLayout(metrics)
        panel=QtWidgets.QFrame();panel.setObjectName('panel');wave_layout=QtWidgets.QVBoxLayout(panel);wave_layout.setContentsMargins(14,14,14,10);wave_layout.setSpacing(5)
        wave_head=QtWidgets.QHBoxLayout();wave_head.addWidget(label('实时原始波形','heading'));wave_head.addWidget(label('EEG · µV · 最近5秒','muted'));wave_head.addStretch()
        self.channel_page=QtWidgets.QComboBox();self.channel_page.addItem('等待通道',0);self.channel_page.setMinimumWidth(110);wave_head.addWidget(self.channel_page)
        self.scale=QtWidgets.QComboBox()
        for gain in [50,100,200,500,1000]:self.scale.addItem(f'± {gain} µV',gain)
        self.scale.setCurrentIndex(1);wave_head.addWidget(self.scale);self.freeze=QtWidgets.QCheckBox('冻结显示');wave_head.addWidget(self.freeze);wave_layout.addLayout(wave_head)
        self.wave=Waveform();wave_layout.addWidget(self.wave,1);wave_layout.addWidget(label('显示不重复滤波；同屏使用统一量程；缺失值留断口。冻结仅影响显示，接收与存储继续。','small'))
        self.channel_page.currentIndexChanged.connect(self._wave_options);self.scale.currentIndexChanged.connect(self._wave_options)
        self.freeze.toggled.connect(self._freeze);self.reduce.toggled.connect(self._reduced);body.addWidget(panel,3)
        lower=QtWidgets.QHBoxLayout();lower.setSpacing(14)
        self.capture_panel=QtWidgets.QFrame();self.capture_panel.setObjectName('panel');self.capture_panel.setMinimumWidth(330)
        cap_box=QtWidgets.QVBoxLayout(self.capture_panel);cap_box.setContentsMargins(16,14,16,12);cap_box.setSpacing(8)
        cap_head=QtWidgets.QHBoxLayout();cap_titles=QtWidgets.QVBoxLayout();cap_titles.setSpacing(2)
        cap_titles.addWidget(label('自主采集打标','heading'));cap_titles.addWidget(label('按住持续记为一个标注期间，松开为未标注','small'))
        cap_head.addLayout(cap_titles);cap_head.addStretch();self.capture_tag=label('本地+中枢同步','pill');cap_head.addWidget(self.capture_tag);cap_box.addLayout(cap_head)
        cap_actions=QtWidgets.QHBoxLayout();cap_actions.setSpacing(8)
        self.capture_start_button=MotionButton('开始自主采集',True);self.capture_start_button.setObjectName('captureStart')
        self.capture_stop_button=MotionButton('停止并保存');self.capture_stop_button.setObjectName('captureStop');self.capture_stop_button.setEnabled(False)
        self.capture_combos=QtWidgets.QCheckBox('加入转向组合');self.capture_combos.setObjectName('captureCombos')
        self.capture_start_button.clicked.connect(self.start_capture);self.capture_stop_button.clicked.connect(self.stop_capture)
        self.capture_combos.toggled.connect(self.refresh_capture_ui)
        cap_actions.addWidget(self.capture_start_button);cap_actions.addWidget(self.capture_stop_button);cap_actions.addWidget(self.capture_combos);cap_actions.addStretch()
        cap_box.addLayout(cap_actions)
        self.capture_grid_widget=QtWidgets.QWidget();self.capture_grid_widget.setObjectName('capturePad')
        self.capture_grid=QtWidgets.QGridLayout(self.capture_grid_widget);self.capture_grid.setContentsMargins(6,4,6,4);self.capture_grid.setSpacing(10)
        positions={'FORWARD':(0,1),'LEFT':(1,0),'REST':(1,1),'RIGHT':(1,2),'BACKWARD':(2,1),'FORWARD_LEFT':(0,0),'FORWARD_RIGHT':(0,2),'BACKWARD_LEFT':(2,0),'BACKWARD_RIGHT':(2,2)}
        self.capture_buttons={}
        for act_label,(r_idx,c_idx) in positions.items():
            btn=CaptureKey(act_label,CAPTURE_NAMES[act_label],diagonal='_' in act_label)
            btn.pressed_hold.connect(lambda lbl=act_label:self.press_capture_hold(lbl,'desktop_pointer'))
            btn.released_hold.connect(lambda lbl=act_label:self.release_capture_hold(lbl,'desktop_pointer'))
            self.capture_grid.addWidget(btn,r_idx,c_idx);self.capture_buttons[act_label]=btn
        cap_box.addWidget(self.capture_grid_widget,1)
        self.capture_state=label('尚未开始','captureState');self.capture_state.setAlignment(QtCore.Qt.AlignCenter);self.capture_state.setWordWrap(True);cap_box.addWidget(self.capture_state)
        self.capture_hint=label('按住才标注，松开为未标注；需要静止数据请按中央“静止”。也可按方向键／空格，Esc 保存退出。','captureHint')
        self.capture_hint.setAlignment(QtCore.Qt.AlignCenter);self.capture_hint.setWordWrap(True);cap_box.addWidget(self.capture_hint)
        self.capture_detail=label('电脑和 VR 共用采集服务，同时只允许一个会话。按键操作本身可能产生动作伪迹。','captureDetail')
        self.capture_detail.setAlignment(QtCore.Qt.AlignCenter);self.capture_detail.setWordWrap(True);cap_box.addWidget(self.capture_detail)
        lower.addWidget(self.capture_panel,2)
        feedback=QtWidgets.QFrame();feedback.setObjectName('panel');feedback.setMinimumWidth(235);feedback.setMaximumWidth(350);feedback_box=QtWidgets.QVBoxLayout(feedback);feedback_box.setContentsMargins(17,16,17,16);feedback_box.addWidget(label('中枢回传','heading'));feedback_box.addWidget(label('来自实际连接的状态，不作本地推断','small'))
        self.feedback_labels={}
        for key,title in [('transport','传输连接'),('cloud','云端保存'),('durable','中枢保存'),('record','主动记录'),('label','当前标签'),('vr','VR回传'),('motion','模型执行')]:
            r=QtWidgets.QHBoxLayout();r.addWidget(label(title,'muted'));r.addStretch();v=label('等待回传');v.setWordWrap(True);self.feedback_labels[key]=v;r.addWidget(v);feedback_box.addLayout(r)
        lower.addWidget(feedback,1)
        logs=QtWidgets.QFrame();logs.setObjectName('panel');log_layout=QtWidgets.QVBoxLayout(logs);log_layout.setContentsMargins(15,13,15,10);line=QtWidgets.QHBoxLayout();line.addWidget(label('分项事件日志','heading'));line.addStretch()
        self.filter=QtWidgets.QComboBox();self.filter.addItems(['全部','采集','写盘','标签','热点直连','中枢','校验','异常']);self.filter.currentTextChanged.connect(self.render_logs);line.addWidget(self.filter)
        export=MotionButton('导出日志');export.setMinimumHeight(30);export.clicked.connect(self.export_logs);line.addWidget(export);log_layout.addLayout(line)
        self.log_view=QtWidgets.QTextBrowser();self.log_view.setOpenExternalLinks(False);self.log_view.setMinimumHeight(150);self.log_view.document().setMaximumBlockCount(600);log_layout.addWidget(self.log_view);lower.addWidget(logs,2);body.addLayout(lower,2)
        self.path_label=label('保存位置将在接收开始后显示','small');self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse);body.addWidget(self.path_label)
        side_area=QtWidgets.QScrollArea();side_area.setWidgetResizable(True);side_area.setFixedWidth(281);side_area.setWidget(sidebar)
        self.side_area=side_area;self.sidebar=sidebar
        layout.addWidget(side_area);layout.addWidget(area,1);self.setStyleSheet(STYLE)
        self.capture_key_held=None;self.last_capture_heartbeat=0.0
        self.timer=QtCore.QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.refresh);self.timer.start()
        self.preview_timer=QtCore.QTimer(self);self.preview_timer.setInterval(100);self.preview_timer.timeout.connect(self.refresh_preview);self.preview_timer.start()
        self.capture_timer=QtCore.QTimer(self);self.capture_timer.setInterval(100);self.capture_timer.timeout.connect(self._tick_capture);self.capture_timer.start()
        self.pairing_timer=QtCore.QTimer(self);self.pairing_timer.setSingleShot(True);self.pairing_timer.setInterval(250);self.pairing_timer.timeout.connect(self.update_pairing_status)
        self.events.add('校验','INFO',f'采集端 v{__version__} 已就绪。设备类型固定为EEG采集端，中枢自动归属；本机EEG发现单独查询GDS。');self.update_pairing_status();self.refresh()
        self.update_apply=None
        from .updater import UpdateMonitor
        self.updater=UpdateMonitor(self.update_idle,self.events.add,self.update_ready,idle_refresh=self.update_idle_refresh)
        if os.environ.get('QT_QPA_PLATFORM')!='offscreen':self.updater.start()
        self.update_button=MotionButton('检查软件更新');self.update_button.clicked.connect(self.updater.check);side.addWidget(self.update_button)
        self.update_label=label('更新：启动后自动检查','small');self.update_label.setWordWrap(True);side.addWidget(self.update_label)
        if os.environ.get('QT_QPA_PLATFORM')!='offscreen' and not self.controller.running:QtCore.QTimer.singleShot(0,self.probe)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,'side_area'):return
        compact=self.width()<1250;columns=2 if compact else 4
        self.side_area.setFixedWidth(243 if compact else 281);self.sidebar.setFixedWidth(236 if compact else 274)
        for i,card in enumerate(self.metric_cards):self.metrics_grid.addWidget(card,i//columns,i%columns)
    def profile(self):return dict({k:f.text().strip() for k,f in self.fields.items()},pairing_path=str(self.identity_path),proxy_mode=self.proxy_mode.currentData(),connection_mode=self.connection_mode_box.currentData() if hasattr(self,'connection_mode_box') else 'automatic')
    def _on_connection_mode_changed(self,*_):
        if self.status_link:
            try:self.status_link.stop()
            except Exception:pass
            self.status_link=None
        self.save_profile();self.update_pairing_status()
    def update_idle(self):
        hub=getattr(self,'update_hub',{})
        return not self.controller.running and self.recovery is None and not self.busy and time.monotonic()-getattr(self,'update_hub_at',0)<5 and not hub.get('recording_active',True) and hub.get('motion_state')=='STOP'
    def update_idle_refresh(self):
        # The updater reads the same live status cache; never opens a second boot.
        if self.controller.running or self.recovery is not None or self.busy:return
        network=self.status_link.snapshot() if self.status_link else {}
        if network.get('hub_state_fresh'):
            self.update_hub=network['hub'];self.update_hub_at=time.monotonic()
        else:self.update_hub_at=0
    def update_ready(self,path,manifest):self.update_apply=path,manifest
    def browse(self,key,directory):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择目录') if directory else QtWidgets.QFileDialog.getOpenFileName(self,'选择JSON文件','','JSON (*.json)')[0]
        if path:
            self.fields[key].setText(path);self.fields[key].setToolTip(path)
            self.save_profile()
    def save_profile(self):
        try:self.settings.save(self.profile())
        except OSError:self.events.add('校验','WARN','界面设置无法保存；本次所选路径仍可使用，请检查用户目录写入权限。')
    def automatic_connection(self):
        return True
    def _enrollment_status(self,state,message=''):
        self.enrollment_state=state;self.enrollment_message=message
        names={'idle':'等待自动连接中枢','connecting':'正在自动连接中枢','waiting_hub':'自动等待中枢上线与空闲',
               'cancelled':'自动连接已取消','role_occupied':'已有采集端在线，当前设备等待归属',
               'expired':'自动连接请求已过期','timeout':'自动连接等待超时','error':'自动连接需要检查','ready':'EEG采集身份已自动保存'}
        self.pairing_status.setText(names.get(state,'正在自动连接中枢'))
        self.pairing_help.setText(message or ('中枢在线、空闲且没有其他EEG采集端在线时自动连接。无需审批或输入连接码。' if state in ('connecting','waiting_hub') else
            '可点击“连接中枢”继续；本机身份由软件内部保存。' if state in ('cancelled','expired','timeout','error') else
            '每类只允许一个在线设备；已有连接不会被接管，请先结束原采集端连接。' if state=='role_occupied' else
            '设备类型：EEG采集端。启动后自动等待中枢并保存本机身份，无需配对文件或连接码。'))
    def start_enrollment(self,done=None):
        if self.closing:return
        if self.enrollment_thread and self.enrollment_thread.is_alive():
            if done:self.enrollment_done=done
            return
        try:
            from eeg_relay.enrollment_client import EnrollmentClient
            config=json.loads((Path(__file__).parent/'relay_public.json').read_text('utf-8'))
            config.update(proxy_mode=self.proxy_mode.currentData(),proxy_url=self.fields['proxy_url'].text().strip())
            client=EnrollmentClient(config,self.settings.path.parent)
            if self.identity_path.exists():
                try:client._validate_identity(client._read_json(self.identity_path))
                except (ValueError,TypeError,KeyError):
                    backup=self.identity_path.with_name('relay-identity.invalid-'+str(time.time_ns())+'.json')
                    self.identity_path.replace(backup)
                    self.events.add('中枢','WARN','内部连接信息已备份，正在自动重新连接；原始记录不受影响。')
        except (OSError,ValueError,TypeError) as error:
            self._enrollment_status('error','无法读取公网连接配置，请检查程序文件完整性。');self.events.add('中枢','ERROR',self.pairing_help.text());return
        self.enrollment_client=client;self.enrollment_stop=threading.Event();stop=self.enrollment_stop
        self.enrollment_generation+=1;generation=self.enrollment_generation;self.enrollment_done=done or self.probe
        self.last_probe={};self.probed_at=0;self._enrollment_status('connecting')
        self.events.add('中枢','INFO','正在后台自动连接；设备类型固定为EEG采集端，可随时取消。')
        jobs=self.enrollment_jobs
        def work():
            try:
                client.run(stop_event=stop,on_state=lambda state:jobs.put((generation,'state',state)))
                jobs.put((generation,'ready',str(client.identity_path)))
            except InterruptedError:jobs.put((generation,'cancelled',''))
            except TimeoutError as error:jobs.put((generation,'timeout',str(error)))
            except Exception as error:jobs.put((generation,getattr(error,'state','error'),str(error)))
        self.enrollment_thread=threading.Thread(target=work,daemon=True,name='automatic EEG enrollment');self.enrollment_thread.start()
    def cancel_enrollment(self,quiet=False):
        if self.enrollment_thread and self.enrollment_state in ('connecting','waiting_hub'):
            self.enrollment_stop.set()
            if not quiet:
                self._enrollment_status('cancelled');self.events.add('中枢','INFO','自动连接已取消；未启动设备或采集。')
    def _refresh_enrollment(self):
        while not self.enrollment_jobs.empty():
            generation,state,value=self.enrollment_jobs.get()
            if self.closing or generation!=self.enrollment_generation or not self.automatic_connection():continue
            if self.enrollment_stop.is_set():
                if state!='state':self._enrollment_status('cancelled')
                continue
            if state=='state':
                if value['state']!='ready':self._enrollment_status(value['state'])
            elif state=='ready':
                self.save_profile();self.update_pairing_status()
                self.events.add('中枢','INFO','EEG采集身份已自动保存，正在验证实际中枢回包。')
                self.enrollment_state='ready';done=self.enrollment_done;self.enrollment_done=None
                if done:done()
            else:
                self._enrollment_status(state,value);self.events.add('中枢','WARN' if state in ('role_occupied','cancelled') else 'ERROR',self.pairing_help.text())
    def update_pairing_status(self,report_error=False):
        self.pairing_timer.stop()
        mode=self.connection_mode_box.currentData() if hasattr(self,'connection_mode_box') else 'automatic'
        lan_hint=self.fields['lan_host'].text().strip() if 'lan_host' in self.fields else ''
        if mode in ('lan_ws','auto_lan_first'):
            from .lan_client import build_lan_enrollment,discover_lan_hub
            saved_agent=None
            if self.identity_path.is_file():
                try:saved_agent=json.loads(self.identity_path.read_text('utf-8')).get('agent_id')
                except Exception:pass
            if mode=='lan_ws':
                enrollment=build_lan_enrollment(lan_hint,agent_id=saved_agent)
                endpoint=f"{enrollment['host']}:{enrollment['port']}"
                self.pairing_status.setText(f"中枢热点／局域网目标  {endpoint}  · WebSocket 直连已就绪")
                self.pairing_help.setText('本机连接中枢热点或同一局域网后，通过 WebSocket 直连传输（不经过公网，稳定不断链）。')
                self.update_enrollment=dict(enrollment);return enrollment
            found=discover_lan_hub(lan_hint,timeout=0.35)
            if found:
                enrollment=build_lan_enrollment(f"{found['host']}:{found['port']}",agent_id=saved_agent)
                endpoint=f"{enrollment['host']}:{enrollment['port']}"
                self.pairing_status.setText(f"已自动发现中枢热点／局域网  {endpoint}  · WebSocket 直连")
                self.pairing_help.setText('已自动匹配中枢热点／局域网 WebSocket 通道；若热点断开将自动回退公网中转。')
                self.update_enrollment=dict(enrollment);return enrollment
        if self.automatic_connection() and not self.identity_path.is_file():
            self.update_enrollment=None;self.update_hub_at=0
            self._enrollment_status(self.enrollment_state,self.enrollment_message)
            return None
        try:
            enrollment=load_enrollment(self.identity_path)
            if enrollment.get('mode')!='relay':raise ValueError('Internal identity must use public relay')
            endpoint=f"{enrollment['host']}:{enrollment['port']}"
            self.pairing_status.setText('中枢目标  '+endpoint+('  · EEG身份已就绪' if self.automatic_connection() else '  · 局域网兼容身份有效'))
            if enrollment.get('mode')=='relay':
                enrollment.update(proxy_mode=self.proxy_mode.currentData(),proxy_url=self.fields['proxy_url'].text().strip())
                self.pairing_help.setText('公网转发 · 应用专用CA · 需要本机中枢回包才算测试成功（也可在左侧切换为中枢热点 WebSocket 直连）。')
            else:self.pairing_help.setText('证书指纹 '+enrollment['certificate_sha256'][:16]+'… · 点击“连接中枢”验证实际回包。')
            self.update_enrollment=dict(enrollment);return enrollment
        except ValueError as error:
            self.update_enrollment=None;self.update_hub_at=0
            self.pairing_status.setText('自动连接身份需要恢复')
            self.pairing_help.setText('本机内部连接信息无法读取。点击“连接中枢”会自动备份并重新连接。')
            if report_error:self.events.add('校验','ERROR','内部连接信息无法读取，等待自动恢复；原始记录不受影响。')
            return None
    def task(self,fn,done=None):
        if self.busy:return
        self.busy=True
        def run():
            try:self.jobs.put((done,fn(),None))
            except BaseException as error:self.jobs.put((None,None,str(error)))
        threading.Thread(target=run,daemon=True).start()
    def discover(self):
        from .diagnostics import diagnose_sdk
        if self.busy or self.controller.running:return
        sdk_dir=self.fields['sdk_dir'].text().strip()
        serial=self.fields['serial'].text().strip()
        self.device_status.setText('正在检查SDK、GDS服务和本机设备；尚未读取样本。')
        def done(result):
            self.device_diagnosis=result
            devices=result.get('devices',[])
            # Respect an explicitly selected serial, including a disconnected one.
            selected=result.get('selected_serial') or (devices[0]['serial'] if len(devices)==1 else None)
            if selected and not serial:self.fields['serial'].setText(selected)
            visible='；'.join(d['serial']+('：原厂会话已存在' if d['in_use'] else '：设备已发现，未建立原厂会话') for d in devices)
            self.device_status.setText((visible+'。 ' if visible else '')+result['message'])
            self.events.add('采集','INFO' if result.get('ready_for_participant') else 'WARN',self.device_status.text())
            self.save_profile()
        self.events.add('采集','INFO','正在独立进程中检测本机GDS；显示全部可见设备，设备发现不等于共享权限或真实样本已确认。')
        self.task(lambda:diagnose_sdk(sdk_dir,serial),done)
    def probe(self):
        collector=self.controller.collector
        if self.controller.running and collector:
            net=collector.snapshot().get('network') or {}
            confirmed=net.get('hub_online') if net.get('transport')=='public_relay' else net.get('connected')
            self.events.add('校验','INFO' if confirmed else 'WARN','使用当前实时连接检查：'+('中枢实时回包已确认 '+json.dumps(net.get('hub',{}),ensure_ascii=False) if confirmed else net.get('error') or '尚未收到新鲜的中枢确认；公网认证不等于中枢在线'))
            return
        from .client import probe_hub
        enrollment=self.update_pairing_status(report_error=not self.automatic_connection())
        if enrollment is None:
            if self.automatic_connection():self.start_enrollment()
            return
        self.last_probe={};self.probed_at=0;self.save_profile()
        from .status_link import StatusLink
        previous=self.status_link
        if previous is None or previous.config!=enrollment or previous.stop_event.is_set():self.status_link=StatusLink(enrollment,self.events.add)
        link=self.status_link
        self.events.add('校验','INFO',f"采集身份已就绪；中枢目标 {enrollment['host']}:{enrollment['port']}。开始测试 TCP → TLS证书 → 采集端身份 → 中枢状态回传")
        def test():
            if previous is not None and previous is not link:previous.stop()
            return probe_hub(enrollment,self.events.add,status_link=link)
        self.task(test,lambda r:self._probe_done(r))
    def _probe_done(self,result):
        self.last_probe=result;self.probed_at=time.monotonic();self.events.add('校验','INFO',f"中枢连接测试完成，耗时 {result['elapsed_ms']}ms；状态连接继续保持，未访问设备")
    def begin(self):
        if self.recovery:self.events.add('校验','WARN','正在补传历史记录，请等待完成后开始实时接收');return
        enrollment=self.update_pairing_status(report_error=not self.automatic_connection())
        if enrollment is None:
            if self.automatic_connection():self.start_enrollment(self.begin)
            return
        try:
            profile=self.profile();self.settings.save(profile);profile['enrollment']=dict(enrollment);self.last_collector_seq=0;self.last_preview=None
            def receive():
                if self.status_link:self.status_link.stop();self.status_link=None
                self.controller.begin(profile)
            self.task(receive)
        except BaseException as error:self.events.add('校验','ERROR',str(error))
    def stop(self):
        if self._capture_snapshot().get('active'):
            try:self.stop_capture()
            except BaseException:pass
        self.events.add('采集','INFO','正在排空尾块并结束自身接收…');self.task(self.controller.stop)
    def _capture_snapshot(self):
        collector=getattr(self.controller,'collector',None)
        if collector and hasattr(collector,'capture_snapshot'):return collector.capture_snapshot()
        s=self.controller.snapshot()
        return s.get('capture') or dict(active=False,held=None,events_saved=0,periods_completed=0,allow_combos=False,labels=list(CAPTURE_LABELS[:5]),instructions={l:capture_task_spec(l)['instruction'] for l in CAPTURE_LABELS})
    def start_capture(self):
        try:
            if hasattr(self.controller,'start_capture'):
                self.controller.start_capture(allow_combos=self.capture_combos.isChecked())
            elif hasattr(getattr(self.controller,'collector',None),'start_capture'):
                self.controller.collector.start_capture(allow_combos=self.capture_combos.isChecked())
            else:
                raise ValueError('请先点击“开始接收”并确认正在接收脑电数据后，再开始自主采集打标')
            self.last_capture_heartbeat=time.monotonic()
            self.setFocus()
            self.refresh()
        except BaseException as error:
            self.events.add('标签','ERROR',str(error))
            self.capture_state.setText(str(error))
    def stop_capture(self):
        try:
            self.capture_key_held=None
            if hasattr(self.controller,'stop_capture'):self.controller.stop_capture('completed')
            elif hasattr(getattr(self.controller,'collector',None),'stop_capture'):self.controller.collector.stop_capture('completed')
            self.refresh()
        except BaseException as error:
            self.events.add('标签','ERROR',str(error))
    def press_capture_hold(self,action_label,pointer_source='desktop_pointer'):
        cap=self._capture_snapshot()
        if not cap.get('active'):return False
        focused=bool(self.isActiveWindow() or os.environ.get('QT_QPA_PLATFORM')=='offscreen')
        try:
            if hasattr(self.controller,'hold_start'):self.controller.hold_start(action_label,pointer_source=pointer_source,page_active=focused)
            elif hasattr(getattr(self.controller,'collector',None),'hold_start'):self.controller.collector.hold_start(action_label,pointer_source=pointer_source,page_active=focused)
            self.last_capture_heartbeat=time.monotonic()
            self.refresh_capture_ui()
            return True
        except BaseException as error:
            self.events.add('标签','WARN',str(error));return False
    def release_capture_hold(self,action_label=None,pointer_source=None,*,page_active=True):
        cap=self._capture_snapshot()
        if not cap.get('active') or not cap.get('held'):return False
        try:
            if hasattr(self.controller,'hold_end'):self.controller.hold_end(action_label,pointer_source=pointer_source,page_active=page_active)
            elif hasattr(getattr(self.controller,'collector',None),'hold_end'):self.controller.collector.hold_end(action_label,pointer_source=pointer_source,page_active=page_active)
            self.last_capture_heartbeat=time.monotonic()
            self.refresh_capture_ui()
            return True
        except BaseException as error:
            self.events.add('标签','WARN',str(error));return False
    def _tick_capture(self):
        cap=self._capture_snapshot()
        if not cap.get('active'):return
        focused=bool(self.isActiveWindow() or os.environ.get('QT_QPA_PLATFORM')=='offscreen')
        if not focused and cap.get('held'):
            self.capture_key_held=None
            self.release_capture_hold(page_active=False)
            return
        now=time.monotonic()
        if now-self.last_capture_heartbeat>=.3:
            self.last_capture_heartbeat=now
            try:
                if hasattr(self.controller,'capture_heartbeat'):self.controller.capture_heartbeat(page_active=focused)
                elif hasattr(getattr(self.controller,'collector',None),'capture_heartbeat'):self.controller.collector.capture_heartbeat(page_active=focused)
            except BaseException:pass
        if cap.get('held'):
            self.refresh_capture_ui()
    def refresh_capture_ui(self,s=None,hub=None):
        if not isinstance(s,dict):s=self.controller.snapshot()
        if not isinstance(hub,dict):
            net=s.get('network') or {}
            hub=net.get('hub') or {}
        cap=s.get('capture') or self._capture_snapshot()
        receiving=bool(self.controller.running and s.get('state')=='receiving')
        active=bool(cap.get('active') and receiving)
        remote_active=bool(hub.get('recording_active') and hub.get('phase') not in (None,'idle','completed','aborted') and not active)
        show_combos=bool(self.capture_combos.isChecked() if not active else cap.get('allow_combos'))
        self.capture_start_button.setEnabled(receiving and not active and not self.busy and not remote_active)
        self.capture_stop_button.setEnabled(active and not self.busy)
        self.capture_combos.setEnabled(not active and not self.busy)
        allowed=set(cap.get('labels') or (CAPTURE_LABELS if show_combos else CAPTURE_LABELS[:5]))
        held=cap.get('held') if active else None
        instructions=cap.get('instructions') or {}
        for act_label,btn in self.capture_buttons.items():
            is_diag='_' in act_label
            visible=(not is_diag) or show_combos
            btn.setVisible(visible)
            btn.setEnabled(active and (act_label in allowed))
            btn.set_held(held==act_label)
            tip=instructions.get(act_label) or ('按住标注'+CAPTURE_NAMES.get(act_label,act_label))
            btn.setToolTip(tip)
        if cap.get('error'):
            self.capture_state.setText(str(cap['error']))
        elif active:
            if held:
                dur=cap.get('held_duration_s',0.0)
                state_txt=f"正在标注：{CAPTURE_NAMES.get(held,held)} · 持续 {dur:.1f}s · 已记录 {cap.get('events_saved',0)} 个事件"
            else:
                state_txt=f"松开 · 未标注 · 已完成 {cap.get('periods_completed',0)} 个期间／{cap.get('events_saved',0)} 个事件"
            if cap.get('hub_synced'):
                state_txt+=f"（中枢已确认 {cap.get('hub_ack_seq',0)}）"
            self.capture_state.setText(state_txt)
        elif remote_active:
            self.capture_state.setText('已有中枢／VR 会话正在采集，请先结束该会话')
        elif cap.get('last_recording'):
            self.capture_state.setText(f"已保存：{cap['last_recording']}（共 {cap.get('periods_completed',0)} 个期间／{cap.get('events_saved',0)} 个事件）")
        elif not receiving:
            self.capture_state.setText('请先开始接收脑电数据，随后即可开始自主采集打标')
        else:
            self.capture_state.setText('尚未开始')
        if active:
            inst=instructions.get(held) if held else '以第一人称想象自己行走或转向，运动想象为主；是否配合眼动以当前任务说明为准。可正常操作手柄。'
            self.capture_hint.setText(inst+' 按住才标注，松开为未标注。')
            sync_note=(f"中枢同步记录 {cap.get('hub_recording') or '进行中'}" if cap.get('hub_synced') else '本地 events.jsonl 实时持久化（待中枢连接或回执）')
            self.capture_detail.setText(f"会话 {str(cap.get('session_id',''))[:8]} · {sync_note} · 松开立即恢复 UNKNOWN（绝不归为静止）")
        else:
            self.capture_hint.setText('按住才标注，松开为未标注；明确无导航意图请按中央“静止”。方向键／空格长按标注，Esc 保存退出。')
            self.capture_detail.setText('采集端与中枢样式及事件格式完全统一，长按持续为一个期间，松开即结束该期间。')
    def keyPressEvent(self,event):
        keys={QtCore.Qt.Key_Up:'FORWARD',QtCore.Qt.Key_Down:'BACKWARD',QtCore.Qt.Key_Left:'LEFT',QtCore.Qt.Key_Right:'RIGHT',QtCore.Qt.Key_Space:'REST'}
        cap=self._capture_snapshot()
        focused_widget=QtWidgets.QApplication.focusWidget()
        in_input=isinstance(focused_widget,(QtWidgets.QLineEdit,QtWidgets.QComboBox))
        if cap.get('active') and not in_input:
            if event.key()==QtCore.Qt.Key_Escape and not event.isAutoRepeat():
                event.accept();self.stop_capture();return
            if event.key() in keys and not event.isAutoRepeat():
                event.accept()
                lbl=keys[event.key()]
                self.capture_key_held=lbl
                self.press_capture_hold(lbl,'desktop_keyboard')
                return
        super().keyPressEvent(event)
    def keyReleaseEvent(self,event):
        keys={QtCore.Qt.Key_Up:'FORWARD',QtCore.Qt.Key_Down:'BACKWARD',QtCore.Qt.Key_Left:'LEFT',QtCore.Qt.Key_Right:'RIGHT',QtCore.Qt.Key_Space:'REST'}
        if event.key() in keys and not event.isAutoRepeat():
            lbl=keys[event.key()]
            if self.capture_key_held==lbl or self._capture_snapshot().get('held')==lbl:
                self.capture_key_held=None
                event.accept()
                self.release_capture_hold(lbl,'desktop_keyboard')
                return
        super().keyReleaseEvent(event)
    def changeEvent(self,event):
        if event.type()==QtCore.QEvent.ActivationChange and not self.isActiveWindow() and os.environ.get('QT_QPA_PLATFORM')!='offscreen':
            self.capture_key_held=None
            self.release_capture_hold(page_active=False)
        super().changeEvent(event)
    def recover(self):
        if self.controller.running or self.recovery:self.events.add('校验','WARN','请先结束当前接收／补传');return
        enrollment=self.update_pairing_status(report_error=not self.automatic_connection())
        if enrollment is None:
            if self.automatic_connection():self.start_enrollment(self.recover)
            return
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择含descriptor.json的原始流目录')
        if not path:return
        from .client import Replicator
        from .storage import StreamJournal
        def work():
            if self.status_link:self.status_link.stop();self.status_link=None
            p=Path(path);j=StreamJournal(p,json.loads((p/'descriptor.json').read_text('utf-8')))
            self.recovery_journal=j;self.recovery=Replicator(j,enrollment,True,self.events.add).start()
            return '历史记录补传已开始；不会刷新实时状态或产生动作'
        self.task(work,lambda text:self.events.add('中枢','INFO',text))
    def _wave_options(self):
        self.wave.page=self.channel_page.currentData() or 0;self.wave.gain=self.scale.currentData() or 100;self.wave._prepare();self.wave.update()
    def _freeze(self,value):self.wave.frozen=value;self.events.add('校验','INFO','波形显示已冻结，后台继续记录' if value else '波形显示已恢复')
    def _reduced(self,value):
        self.wave.reduced=value;self.wave.timer.setInterval(100)
        for button in self.findChildren(MotionButton):button.reduce_motion=value
    def refresh_preview(self):
        collector=self.controller.collector
        if not collector or not hasattr(collector,'preview') or self.wave.frozen:return
        preview=collector.preview.snapshot()
        if preview['read_monotonic_ns']!=self.last_preview:self.last_preview=preview['read_monotonic_ns'];self.wave.set_preview(preview)
    def idle_network(self,state):
        saved=dict(state.get('network') or {})
        if not self.status_link:return saved
        current=self.status_link.snapshot()
        if not state.get('descriptor'):return current
        # A status-only reconnect must not hide a previous stream's unacked data.
        for key in ('connected','hub_online','hub_state_fresh','hub_age_ms','transport','path','hub','error'):
            if key in current:saved[key]=current[key]
        saved['status_only']=False
        return saved

    def refresh(self):
        self._refresh_enrollment()
        if getattr(self,'update_apply',None) and self.update_idle():
            import subprocess
            path,manifest=self.update_apply;self.update_apply=None;self.updater.stop()
            subprocess.Popen([str(self.updater.root/'EEGMovement-Launcher.exe'),'--apply',str(path),'--manifest',str(manifest),'--wait-pid',str(os.getpid())],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.close();return
        if hasattr(self,'update_label'):
            names={'waiting':'等待检查','current':'已是最新版本','found':'发现更新','waiting_idle':'采集／动作停止后继续','downloading':'后台下载中','verified':'已校验，等待安装','check_failed':'检查未完成，当前版本可继续使用'}
            self.update_label.setText('更新：'+names.get(self.updater.state,self.updater.state))
        while not self.jobs.empty():
            done,result,error=self.jobs.get();self.busy=False
            if error:self.events.add('校验','ERROR',error)
            elif done:done(result)
        s=self.controller.snapshot();network=s.get('network') or {};d=s.get('descriptor') or {}
        if not self.controller.running and not self.recovery:
            if self.status_link:network=self.idle_network(s)
            if not self.busy and not self.closing and self.update_enrollment and os.environ.get('QT_QPA_PLATFORM')!='offscreen' and (self.status_link is None or self.status_link.stop_event.is_set()):
                from .status_link import StatusLink
                self.status_link=StatusLink(self.update_enrollment,self.events.add).start()
        if self.status_link and hasattr(self.status_link,'update_health'):
            from .telemetry import build
            self.status_link.update_health(dict(sdk_state=s.get('sdk_state','not_started'),disk_state='not_recording',queue_blocks=s.get('queue_blocks',0),error=s.get('error'),
                client_status=build(self.profile(),s,network,getattr(getattr(self,'updater',None),'state','waiting'))))
        revision=network.get('update_revision')
        if revision and revision!=getattr(self,'update_revision',None):self.update_revision=revision;self.updater.check()
        self.start_button.setEnabled(not self.controller.running and not self.busy and self.recovery is None)
        self.stop_button.setEnabled(self.controller.running and not self.busy);self.discover_button.setEnabled(not self.controller.running and not self.busy)
        self.probe_button.setEnabled(not self.busy);self.recover_button.setEnabled(not self.controller.running and not self.busy and self.recovery is None)
        enrolling=bool(self.enrollment_thread and self.enrollment_thread.is_alive())
        self.cancel_enrollment_button.setVisible(enrolling and not self.enrollment_stop.is_set())
        self.probe_button.setText('重新连接' if self.update_enrollment else '连接中枢')
        for field in self.fields.values():field.setEnabled(not self.controller.running and not self.busy)
        states={'idle':'尚未开始','starting':'连接共享会话','receiving':'正在接收','stopped':'已保存并结束','closed':'已关闭','error':'需要检查'}
        self.badge.setText('处理中…' if self.busy else states.get(s.get('state'),str(s.get('state','未知'))))
        self.status_text.setText(('中枢在线 · EEG尚未开始接收' if network.get('status_only') else ('热点WebSocket直连 · 中枢在线' if network.get('transport')=='lan_ws' else '公网已认证 · 中枢在线')) if network.get('hub_online') else '公网已认证 · 中枢回包未确认' if network.get('transport')=='public_relay' and network.get('connected') else '本地优先保存 · 中枢已连接' if network.get('connected') else '本地记录不依赖网络' if self.controller.running else '等待原厂共享会话')
        if not self.controller.running and self.enrollment_state in ('connecting','waiting_hub','role_occupied'):self.status_text.setText(self.pairing_status.text())
        self.proxy_mode.setEnabled(not self.controller.running and not self.busy)
        if hasattr(self,'connection_mode_box'):self.connection_mode_box.setEnabled(not self.controller.running and not self.busy)
        self.saved.set(f"{s.get('samples',0):,}");self.rate.set(d.get('sampling_rate','—'),f"{len(d.get('channels',[]))}列 · 原厂当前配置" if d else '读取实际配置，不设定采样率')
        self.backlog.set(network.get('backlog',0));self.confirmed.set(network.get('durable_seq',0))
        if d and d.get('stream_id')!=self.last_descriptor:
            self.last_descriptor=d['stream_id'];self.subtitle.setText(f"{d['device_serial']}  /  {sum(c['kind']=='EEG' for c in d['channels'])}路EEG  /  配置 {d['configuration_sha256'][:12]} · 接触质量未验证")
            eeg=[c for c in d['channels'] if c['kind']=='EEG'];self.channel_page.blockSignals(True);self.channel_page.clear()
            for i in range(0,len(eeg),8):self.channel_page.addItem(f"第{i+1}–{min(i+8,len(eeg))}路",i//8)
            self.channel_page.blockSignals(False);self._wave_options()
        self.path_label.setText('保存位置  '+str(s.get('path') or self.fields['storage_root'].text()));self.path_label.setToolTip(str(s.get('path') or ''))
        collector=self.controller.collector
        if collector and hasattr(collector,'log'):
            for event in collector.log.snapshot():
                if event['seq']>self.last_collector_seq:self.events.add(event['stage'],event['level'],event['message']);self.last_collector_seq=event['seq']
        if self.controller.running and s.get('state') in ['error','stopped','closed'] and not s.get('supervisor_active',False):self.controller.running=False
        hub=network.get('hub') if network.get('connected') else getattr(self,'last_probe',{}).get('hub') if time.monotonic()-getattr(self,'probed_at',0)<5 else {}
        hub=hub or {}
        if network.get('transport')=='public_relay' and not network.get('hub_state_fresh'):hub={}
        self.feedback_labels['transport'].setText('热点WebSocket已确认' if network.get('transport')=='lan_ws' and network.get('connected') else ('公网与中枢已确认' if network.get('hub_online') else '公网已认证 · 中枢未确认') if network.get('transport')=='public_relay' and network.get('connected') else '已认证连接' if network.get('connected') else '上次测试通过 · 快照' if hub else '等待连接')
        self.feedback_labels['cloud'].setText('热点/局域网直连' if network.get('transport')=='lan_ws' and network.get('connected') else '尚未采集EEG' if network.get('status_only') else ('本连接确认 '+str(network.get('cloud_blocks',0))+' 块') if network.get('transport')=='public_relay' else '局域网直连' if network.get('connected') else '等待连接')
        self.feedback_labels['durable'].setText('尚未采集EEG' if network.get('status_only') else '连续前缀 '+str(network.get('durable_seq',0))+' 块' if network.get('connected') else '等待持久化确认')
        self.feedback_labels['record'].setText(('记录中 · '+str(hub.get('recording_samples',0))+'行') if hub.get('recording_active') else '未开始' if hub else '等待回传')
        names={'REST':'静止指令','FORWARD':'向前','BACKWARD':'向后','LEFT':'向左','RIGHT':'向右','FORWARD_LEFT':'前进左转','FORWARD_RIGHT':'前进右转','BACKWARD_LEFT':'后退左转','BACKWARD_RIGHT':'后退右转'}
        cap_state=s.get('capture') or self._capture_snapshot()
        active_label=hub.get('label') if hub else (cap_state.get('held') if cap_state.get('active') else None)
        self.feedback_labels['label'].setText(names.get(active_label,active_label or '未标注') if (hub or cap_state.get('active')) else '等待回传')
        self.feedback_labels['vr'].setText(str(hub.get('vr_online',0))+'个在线' if hub else '等待回传')
        self.feedback_labels['motion'].setText(str(hub.get('motion_state','未知'))+' · '+('模型未配置' if hub.get('model_state')=='not_configured' else str(hub.get('model_state','未知'))) if hub else '等待回传')
        self.refresh_capture_ui(s,hub)
        if self.recovery:
            recovery=self.recovery.snapshot();self.backlog.set(recovery['backlog'],'历史补传，不进入实时控制')
            if recovery['connected'] and recovery['backlog']==0:
                self.recovery.stop();self.recovery_journal.close();self.recovery=self.recovery_journal=None;self.events.add('中枢','INFO','历史记录补传完成，所有块取得持久化确认')
        self.render_logs()
        if self.closed_after_stop and not self.busy:self.close()
    def render_logs(self,*args):
        events=self.events.snapshot();selected=self.filter.currentText();filtered=[r for r in events if selected=='全部' or r['stage'].startswith(selected) or selected=='异常' and r['level'] in ['WARN','ERROR']]
        signature=(selected,events[-1]['seq'] if events else 0)
        if getattr(self,'log_signature',None)==signature:return
        self.log_signature=signature;bar=self.log_view.verticalScrollBar();follow=bar.value()>=bar.maximum()-5;old=bar.value()
        colors={'INFO':'#56746a','WARN':'#ad7c32','ERROR':'#ba4e4b'}
        content=[]
        for r in filtered:
            stamp=r['time'][11:19];message=html.escape(r['message']);stage=html.escape(r['stage']);color=colors.get(r['level'],MUTED)
            content.append(f'<div style="margin:3px 0;color:{color}"><span style="color:#9caaa4;font-family:Consolas">{stamp}</span> &nbsp; [{stage}] &nbsp; {message}</div>')
        self.log_view.setHtml('<div style="font-family:Microsoft YaHei UI;font-size:11px;line-height:1.7">'+''.join(content)+'</div>')
        bar.setValue(bar.maximum() if follow else old)
    def export_logs(self):
        path=QtWidgets.QFileDialog.getSaveFileName(self,'导出分项日志','gNautilus-events.json','JSON (*.json)')[0]
        if path:
            pairing=self.update_pairing_status()
            context=dict(mode='automatic_public',identity_ready=pairing is not None,
                target=(f"{pairing['host']}:{pairing['port']}" if pairing else None))
            Path(path).write_text(json.dumps(dict(schema='gnautilus.ui_log.v1',app_version=__version__,connection=context,events=self.events.snapshot()),ensure_ascii=False,indent=2),encoding='utf-8')
            self.events.add('校验','INFO','分项日志已导出：'+path)
    def runtime_snapshot(self):
        from .telemetry import build
        state=self.controller.snapshot();network=state.get('network') or {}
        if not self.controller.running and self.status_link:network=self.idle_network(state)
        allowed=('connected','transport','hub_online','hub_state_fresh','hub_age_ms','durable_seq','cloud_blocks','backlog','live_accepted','live_submitted','error')
        hub_fields=('relay_timing_policy','source_age_ms','last_source_loss','source_fresh','sample_index_end','source_epoch','recording_active','recording_samples','events_saved','label','phase','vr_online','model_state','motion_state')
        hub=network.get('hub') or {}
        return dict(schema='gnautilus.runtime.v1',pid=os.getpid(),updated_utc_ns=time.time_ns(),
                    client=build(self.profile(),state,network),
                    network={k:network[k] for k in allowed if k in network},
                    hub={k:hub[k] for k in hub_fields if k in hub})

    def closeEvent(self,event):
        self.closing=True;self.enrollment_stop.set()
        if self.controller.running or self.recovery:
            event.ignore();self.closed_after_stop=True
            def finish():
                self.controller.stop()
                if self.recovery:self.recovery.stop();self.recovery_journal.close();self.recovery=self.recovery_journal=None
            self.task(finish);return
        if hasattr(self,'updater'):self.updater.stop()
        if self.status_link:threading.Thread(target=self.status_link.stop,daemon=True).start()
        self.timer.stop();self.preview_timer.stop();self.capture_timer.stop();self.pairing_timer.stop();self.wave.timer.stop();event.accept()


def run(*,start_receiving=False,status_path=None):
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([]);app.setApplicationName('EEGMovement g.Nautilus');app.setFont(QtGui.QFont('Microsoft YaHei UI',10))
    window=Workbench();window.show()
    health=os.environ.pop('EEGMOVEMENT_STARTUP_HEALTH',None)
    if health:
        from .storage import atomic_json
        QtCore.QTimer.singleShot(1000,lambda:atomic_json(Path(health),dict(ready=True,pid=os.getpid(),version=__version__,vendor_started=False)))
    if status_path:
        from .storage import atomic_json
        def write_runtime():
            try:atomic_json(Path(status_path),window.runtime_snapshot())
            except (OSError,ValueError) as error:
                window.events.add('校验','WARN','运行诊断文件无法写入：'+str(error))
                window.runtime_timer.stop()
        window.runtime_timer=QtCore.QTimer(window);window.runtime_timer.timeout.connect(write_runtime);window.runtime_timer.start(1000)
    if start_receiving:
        def start_when_ready():
            if window.closing:return
            if window.busy:
                QtCore.QTimer.singleShot(250,start_when_ready)
            else:window.begin()
        QtCore.QTimer.singleShot(2000,start_when_ready)
    return app.exec()
