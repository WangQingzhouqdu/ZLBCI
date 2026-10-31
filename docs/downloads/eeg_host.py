#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 eeg_host.py —— ESP32-S3 + ADS1299 八通道脑电 BLE 上位机
=============================================================================
 功能:
   * 设备扫描 / 连接 / 断开 , 连接状态指示框
   * 采集 启动 / 暂停 , 采样率 / 增益 / 输入模式 / 共模参考 / 偏置 / 导联脱落
   * 实时滤波: 高通 + 低通(带通) + 50/60 Hz 陷波(含谐波), 可调阶数
   * 8 通道实时波形显示, 时间窗与幅度量程可调, 通道可单独显示/隐藏
   * 数据保存为 CSV(可选保存原始/滤波/两者)
   * 数据传输状态指示框: 包速率 / 样本率 / 丢包 / 误码 / 已接收时长
   * 导联脱落逐通道指示
   * 无硬件时可用 --demo 模拟模式验证整个界面

 跨平台: Windows / macOS / Linux
   - macOS 首次运行会弹出蓝牙权限申请, 需要在
     [系统设置] -> [隐私与安全性] -> [蓝牙] 中允许 终端/Python
   - macOS 上 BLE 设备地址是系统分配的 UUID(不是 MAC), 属正常现象

 依赖:  pip install PyQt6 pyqtgraph numpy scipy bleak
 运行:  python eeg_host.py            # 真实设备
        python eeg_host.py --demo     # 模拟数据(无需硬件)
=============================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import queue
import struct
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

# ---- Qt 必须在 pyqtgraph 之前导入, 以便 pyqtgraph 正确选择 PyQt6 后端 ----
try:
    import PyQt6

    # Anaconda 的 qt.conf 可能把插件路径指向自己的 Qt，覆盖 pip wheel
    # 内置的 Qt6 路径。仅在该 wheel 目录存在时为当前进程补上路径。
    _pyqt6_dir = os.path.dirname(PyQt6.__file__ or "")
    _qt6_plugins = os.path.join(_pyqt6_dir, "Qt6", "plugins")
    _qt6_platform_plugins = os.path.join(_qt6_plugins, "platforms")
    if os.path.isdir(_qt6_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt6_plugins)
    if os.path.isdir(_qt6_platform_plugins):
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", _qt6_platform_plugins)

    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少 PyQt6, 请执行:  pip install PyQt6\n")
    raise

import pyqtgraph as pg

try:
    from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos
    SCIPY_OK = True
except ImportError:  # pragma: no cover
    SCIPY_OK = False

try:
    from bleak import BleakClient, BleakScanner
    BLEAK_OK = True
except ImportError:  # pragma: no cover
    BLEAK_OK = False


# =============================================================================
#  协议常量 (与固件 ESP32S3_ADS1299_EEG.ino 保持一致)
# =============================================================================
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # PC  -> ESP32 (write)
CHAR_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # ESP32 -> PC (notify, 数据)
CHAR_ST_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9e"   # ESP32 -> PC (notify, 状态)

VREF = 4.5                                    # ADS1299 内部基准 (V)
GAIN_TABLE = (1, 2, 4, 6, 8, 12, 24)
SR_TABLE = (250, 500, 1000, 2000, 4000, 8000, 16000)
N_CH = 8

PKT_HDR = struct.Struct("<2sHBBIBBBB")        # 14 字节包头
PKT_HDR_LEN = PKT_HDR.size

CH_COLORS = ["#d62728", "#ff7f0e", "#bcbd22", "#2ca02c",
             "#17becf", "#1f77b4", "#9467bd", "#8c564b"]

# CRC8 (多项式 0x07, 初值 0x00) 查表
_CRC8_TAB: List[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if (_c & 0x80) else ((_c << 1) & 0xFF)
    _CRC8_TAB.append(_c)


def crc8(buf: bytes) -> int:
    c = 0
    for b in buf:
        c = _CRC8_TAB[c ^ b]
    return c


def parse_packet(data: bytes) -> Optional[Dict[str, Any]]:
    """解析一个 BLE 数据包, 返回 dict; 校验失败返回 None。"""
    if len(data) < PKT_HDR_LEN + 1:
        return None
    magic, seq, nch, ns, sidx, loffp, loffn, flags, gcode = PKT_HDR.unpack_from(data, 0)
    if magic != b"\xa5\x5a" or nch == 0 or ns == 0:
        return None
    need = PKT_HDR_LEN + ns * nch * 3 + 1
    if len(data) < need:
        return None
    if crc8(data[:need - 1]) != data[need - 1]:
        return None

    raw = np.frombuffer(data, dtype=np.uint8, count=ns * nch * 3,
                        offset=PKT_HDR_LEN).astype(np.int32).reshape(-1, 3)
    v = (raw[:, 0] << 16) | (raw[:, 1] << 8) | raw[:, 2]     # 24bit 大端
    v = np.where(v & 0x800000, v - 0x1000000, v)             # 补码转有符号

    gi = gcode & 0x07
    gain = GAIN_TABLE[gi] if gi < len(GAIN_TABLE) else 24
    si = (flags >> 4) & 0x0F
    fs = SR_TABLE[si] if si < len(SR_TABLE) else 250

    scale = np.float32(VREF / (gain * 8388608.0) * 1e6)      # LSB -> 微伏
    uv = v.reshape(ns, nch).astype(np.float32) * scale

    return {
        "seq": seq, "nch": nch, "ns": ns, "sidx": sidx,
        "loffp": loffp, "loffn": loffn, "flags": flags,
        "gain": gain, "fs": fs, "data": uv,
        "ovf": bool(flags & 0x01), "streaming": bool(flags & 0x02),
        "nbytes": need,
    }


# =============================================================================
#  环形显示缓冲
# =============================================================================
class RingBuffer:
    def __init__(self, n: int, nch: int = N_CH):
        self.nch = nch
        self.reset(n)

    def reset(self, n: Optional[int] = None):
        if n is not None:
            self.n = max(2, int(n))
        self.buf = np.zeros((self.n, self.nch), dtype=np.float32)
        self.idx = 0
        self.filled = 0

    def push(self, x: np.ndarray):
        m = x.shape[0]
        if m == 0:
            return
        if m >= self.n:
            self.buf[:] = x[-self.n:]
            self.idx = 0
            self.filled = self.n
            return
        end = self.idx + m
        if end <= self.n:
            self.buf[self.idx:end] = x
        else:
            k = self.n - self.idx
            self.buf[self.idx:] = x[:k]
            self.buf[:end - self.n] = x[k:]
        self.idx = end % self.n
        self.filled = min(self.n, self.filled + m)

    def snapshot(self) -> np.ndarray:
        """返回 (n, nch)，最新样本在末尾；未填满部分为 NaN。"""
        if self.filled < self.n:
            out = np.full((self.n, self.nch), np.nan, dtype=np.float32)
            if self.filled:
                out[self.n - self.filled:] = self.buf[:self.filled]
            return out
        return np.concatenate((self.buf[self.idx:], self.buf[:self.idx]), axis=0)


# =============================================================================
#  实时滤波器组 (分段 IIR, 逐块保持状态)
# =============================================================================
class FilterBank:
    def __init__(self, nch: int = N_CH):
        self.nch = nch
        self.stages: List[np.ndarray] = []
        self._zi: List[Optional[np.ndarray]] = []
        self.desc = "关闭"

    def configure(self, fs: float, hp: Optional[float], lp: Optional[float],
                  order: int = 4, notch: Optional[float] = None,
                  notch_q: float = 30.0, harmonics: int = 3):
        self.stages = []
        self.desc = ""
        if not SCIPY_OK:
            self.desc = "scipy 未安装, 滤波不可用"
            self.reset()
            return

        nyq = fs / 2.0
        hp = None if (hp is None or hp <= 0) else float(min(hp, nyq * 0.9))
        lp = None if (lp is None or lp <= 0 or lp >= nyq * 0.98) else float(lp)

        parts = []
        try:
            if hp is not None and lp is not None and lp > hp:
                self.stages.append(butter(order, [hp, lp], btype="bandpass", fs=fs, output="sos"))
                parts.append(f"带通 {hp:g}-{lp:g} Hz")
            elif hp is not None:
                self.stages.append(butter(order, hp, btype="highpass", fs=fs, output="sos"))
                parts.append(f"高通 {hp:g} Hz")
            elif lp is not None:
                self.stages.append(butter(order, lp, btype="lowpass", fs=fs, output="sos"))
                parts.append(f"低通 {lp:g} Hz")
        except Exception as e:                                   # 参数非法时退化为不滤波
            self.desc = f"滤波器参数无效: {e}"

        if notch:
            hs = []
            for k in range(1, max(1, harmonics) + 1):
                f0 = notch * k
                if f0 < nyq * 0.95:
                    b, a = iirnotch(f0, notch_q, fs=fs)
                    self.stages.append(tf2sos(b, a))
                    hs.append(f"{f0:g}")
            if hs:
                parts.append("陷波 " + "/".join(hs) + " Hz")

        self.desc = " + ".join(parts) if parts else (self.desc or "直通")
        self.reset()

    def reset(self):
        self._zi = [None] * len(self.stages)

    def process(self, x: np.ndarray) -> np.ndarray:
        if not self.stages:
            return x
        y = x
        for i, sos in enumerate(self.stages):
            if self._zi[i] is None:
                # 用首样本做稳态初始化, 避免大直流偏置引起的长时间启动瞬态
                z = sosfilt_zi(sos)                      # (n_sections, 2)
                self._zi[i] = (z[:, :, None] * y[0][None, None, :]).astype(np.float64)
            y, self._zi[i] = sosfilt(sos, y.astype(np.float64), axis=0, zi=self._zi[i])
        return y.astype(np.float32)


# =============================================================================
#  数据记录线程
# =============================================================================
class Recorder(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="recorder")
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._f = None
        self._w = None
        self._mode = "raw"
        self._fs = 250.0
        self.rows_written = 0
        self.path = ""
        self.error = ""
        self._alive = True

    # --- 外部接口(线程安全) ---
    def open(self, path: str, meta: Dict[str, Any], mode: str):
        self.q.put(("open", path, meta, mode))

    def write(self, t0: float, sidx: int, raw: np.ndarray, filt: np.ndarray):
        self.q.put(("data", t0, sidx, raw.copy(), filt.copy()))

    def close(self):
        self.q.put(("close",))

    def quit(self):
        self._alive = False
        self.q.put(("quit",))

    # --- 线程主体 ---
    def run(self):
        while self._alive:
            try:
                item = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            op = item[0]
            try:
                if op == "open":
                    self._do_open(item[1], item[2], item[3])
                elif op == "data":
                    self._do_write(item[1], item[2], item[3], item[4])
                elif op == "close":
                    self._do_close()
                elif op == "quit":
                    self._do_close()
                    break
            except Exception as e:                               # 记录错误但不杀死线程
                self.error = str(e)
                self._do_close()

    def _do_open(self, path, meta, mode):
        self._do_close()
        self._mode = mode
        self._fs = float(meta.get("fs", 250))
        self._f = open(path, "w", newline="", encoding="utf-8")
        for k, v in meta.items():
            self._f.write(f"# {k}: {v}\n")
        self._f.write(f"# columns_unit: microvolt (uV)\n")
        self._w = csv.writer(self._f)
        head = ["time_s", "sample_index"]
        if mode in ("raw", "both"):
            head += [f"raw_ch{i+1}" for i in range(N_CH)]
        if mode in ("filt", "both"):
            head += [f"filt_ch{i+1}" for i in range(N_CH)]
        self._w.writerow(head)
        self.rows_written = 0
        self.path = path
        self.error = ""

    def _do_write(self, t0, sidx, raw, filt):
        if self._w is None:
            return
        n = raw.shape[0]
        dt = 1.0 / self._fs
        rows = []
        for i in range(n):
            row = [f"{t0 + i * dt:.6f}", sidx + i]
            if self._mode in ("raw", "both"):
                row.extend(f"{v:.4f}" for v in raw[i])
            if self._mode in ("filt", "both"):
                row.extend(f"{v:.4f}" for v in filt[i])
            rows.append(row)
        self._w.writerows(rows)
        self.rows_written += n

    def _do_close(self):
        if self._f is not None:
            try:
                self._f.flush()
                self._f.close()
            except Exception:
                pass
        self._f = None
        self._w = None


# =============================================================================
#  传输层
# =============================================================================
class Transport:
    name = "BASE"

    def __init__(self, data_q: "queue.Queue", evt_q: "queue.Queue"):
        self.data_q = data_q
        self.evt_q = evt_q

    def _evt(self, kind: str, **kw):
        kw["kind"] = kind
        self.evt_q.put(kw)

    # 子类实现
    def scan(self, timeout: float = 6.0, only_eeg: bool = True): ...
    def connect(self, key: str): ...
    def disconnect(self): ...
    def send_cmd(self, text: str): ...
    def shutdown(self): ...


class BleTransport(Transport):
    """bleak + 独立 asyncio 事件循环线程。Windows/macOS/Linux 通用。"""
    name = "BLE"

    def __init__(self, data_q, evt_q):
        super().__init__(data_q, evt_q)
        if not BLEAK_OK:
            raise RuntimeError("未安装 bleak, 请执行: pip install bleak")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None
        self._devices: Dict[str, Any] = {}
        self._ready = threading.Event()
        self._th = threading.Thread(target=self._run_loop, name="ble-loop", daemon=True)
        self._th.start()
        if not self._ready.wait(5.0):
            raise RuntimeError("BLE 事件循环启动失败")

    # --- 事件循环线程 ---
    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    def _submit(self, coro):
        if self._loop is None or self._loop.is_closed():
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # --- 扫描 ---
    def scan(self, timeout: float = 6.0, only_eeg: bool = True):
        self._submit(self._scan(timeout, only_eeg))

    async def _scan(self, timeout, only_eeg):
        self._evt("scan_started")
        found = []
        try:
            try:
                res = await BleakScanner.discover(timeout=timeout, return_adv=True)
                items = list(res.values())
            except TypeError:                       # 兼容较老版本 bleak
                devs = await BleakScanner.discover(timeout=timeout)
                items = [(d, None) for d in devs]

            self._devices.clear()
            for d, adv in items:
                uuids = []
                if adv is not None and getattr(adv, "service_uuids", None):
                    uuids = [str(u).lower() for u in adv.service_uuids]
                name = d.name or (getattr(adv, "local_name", None) if adv else None) or ""
                is_eeg = (SERVICE_UUID in uuids) or ("EEG" in name.upper())
                if only_eeg and not is_eeg:
                    continue
                key = str(d.address)
                self._devices[key] = d
                rssi = getattr(adv, "rssi", None) if adv is not None else None
                found.append({"key": key, "name": name or "(未命名)",
                              "rssi": rssi, "eeg": is_eeg})
            found.sort(key=lambda x: -(x["rssi"] if x["rssi"] is not None else -999))
        except Exception as e:
            self._evt("error", msg=f"扫描失败: {e}")
        self._evt("scan_done", devices=found)

    # --- 连接 ---
    def connect(self, key: str):
        self._submit(self._connect(key))

    async def _connect(self, key):
        self._evt("connecting")
        try:
            target = self._devices.get(key, key)
            cli = BleakClient(target, timeout=20.0,
                              disconnected_callback=self._on_disconnected)
            await cli.connect()
            self._client = cli
            await cli.start_notify(CHAR_TX_UUID, self._on_data)
            try:
                await cli.start_notify(CHAR_ST_UUID, self._on_status)
            except Exception:
                pass                                 # 状态通道可选
            mtu = int(getattr(cli, "mtu_size", 0) or 0)
            name = getattr(target, "name", None) or str(key)
            self._evt("connected", addr=str(key), name=name, mtu=mtu)
            await asyncio.sleep(0.3)
            await cli.write_gatt_char(CHAR_RX_UUID, b"INFO\n", response=True)
        except Exception as e:
            self._client = None
            self._evt("conn_failed", msg=str(e))

    def _on_disconnected(self, _client):
        self._client = None
        self._evt("disconnected")

    # --- 断开 ---
    def disconnect(self):
        self._submit(self._disconnect())

    async def _disconnect(self):
        cli, self._client = self._client, None
        if cli is not None:
            try:
                if cli.is_connected:
                    await cli.disconnect()
            except Exception:
                pass
        self._evt("disconnected")

    # --- 命令 ---
    def send_cmd(self, text: str):
        self._submit(self._send(text))

    async def _send(self, text):
        cli = self._client
        if cli is None or not cli.is_connected:
            self._evt("error", msg=f"未连接, 命令 [{text}] 未发送")
            return
        try:
            await cli.write_gatt_char(CHAR_RX_UUID, (text.strip() + "\n").encode(), response=True)
        except Exception as e:
            self._evt("error", msg=f"命令发送失败: {e}")

    # --- 通知回调 (在事件循环线程) ---
    def _on_data(self, _sender, data: bytearray):
        pkt = parse_packet(bytes(data))
        if pkt is None:
            self._evt("badpkt")
            return
        try:
            self.data_q.put_nowait(pkt)
        except queue.Full:
            pass

    def _on_status(self, _sender, data: bytearray):
        try:
            obj = json.loads(bytes(data).decode("utf-8", "ignore"))
        except Exception:
            return
        self._evt("devstatus", data=obj)

    def shutdown(self):
        try:
            fut = self._submit(self._disconnect())
            if fut is not None:
                fut.result(timeout=3.0)
        except Exception:
            pass
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)


class DemoTransport(Transport):
    """模拟设备: 生成合成脑电, 用于无硬件时验证界面与滤波/保存链路。"""
    name = "DEMO"

    def __init__(self, data_q, evt_q):
        super().__init__(data_q, evt_q)
        self.fs = 250
        self.gain = 24
        self.mode = "NORMAL"
        self._connected = False
        self._streaming = False
        self._stop = threading.Event()
        self._idx = 0
        self._seq = 0
        self._rng = np.random.default_rng(20260821)
        self._phase = self._rng.uniform(0, 2 * np.pi, N_CH)
        self._dc = self._rng.uniform(-150, 150, N_CH)
        self._blink_left = 0
        self._th = threading.Thread(target=self._loop, name="demo-gen", daemon=True)
        self._th.start()

    def scan(self, timeout: float = 6.0, only_eeg: bool = True):
        self._evt("scan_started")
        self._evt("scan_done", devices=[{"key": "demo", "name": "模拟设备 (无需硬件)",
                                         "rssi": -35, "eeg": True}])

    def connect(self, key: str):
        self._evt("connecting")
        self._connected = True
        self._evt("connected", addr="demo", name="模拟设备", mtu=185)
        self._push_status()

    def disconnect(self):
        self._connected = False
        self._streaming = False
        self._evt("disconnected")

    def send_cmd(self, text: str):
        t = text.strip().upper()
        if t == "START":
            self._idx = 0
            self._streaming = True
        elif t == "STOP":
            self._streaming = False
        elif t.startswith("SR:"):
            try:
                v = int(t[3:])
                if v in SR_TABLE:
                    self.fs = v
            except ValueError:
                pass
        elif t.startswith("GAIN:"):
            try:
                v = int(t[5:])
                if v in GAIN_TABLE:
                    self.gain = v
            except ValueError:
                pass
        elif t.startswith("MODE:"):
            self.mode = t[5:]
        self._push_status()

    def _push_status(self):
        self._evt("devstatus", data={
            "t": "status", "fw": "demo", "id": "0x3E",
            "stream": 1 if self._streaming else 0, "sr": self.fs,
            "gain": self.gain, "mode": self.mode, "srb1": 1, "bias": 1,
            "loff": 0, "mtu": 185, "spp": 6, "lost": 0, "idx": self._idx,
            "loffp": 0, "loffn": 0})

    def shutdown(self):
        self._stop.set()

    def _loop(self):
        chunk_dt = 0.04
        next_t = time.perf_counter()
        while not self._stop.is_set():
            if not (self._connected and self._streaming):
                time.sleep(0.05)
                next_t = time.perf_counter()
                continue
            n = max(1, int(round(self.fs * chunk_dt)))
            data = self._gen(n)
            pkt = {"seq": self._seq, "nch": N_CH, "ns": n, "sidx": self._idx,
                   "loffp": 0, "loffn": 0, "flags": 0x02, "gain": self.gain,
                   "fs": self.fs, "data": data, "ovf": False, "streaming": True,
                   "nbytes": PKT_HDR_LEN + n * N_CH * 3 + 1}
            try:
                self.data_q.put_nowait(pkt)
            except queue.Full:
                pass
            self._idx = (self._idx + n) & 0xFFFFFFFF
            self._seq = (self._seq + 1) & 0xFFFF

            next_t += chunk_dt
            dt = next_t - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            else:
                next_t = time.perf_counter()

    def _gen(self, n: int) -> np.ndarray:
        t = (self._idx + np.arange(n)) / float(self.fs)
        out = np.empty((n, N_CH), dtype=np.float32)

        if self.mode == "TEST":                     # ADS1299 内部方波 +-1.875 mV
            sq = 1875.0 * np.sign(np.sin(2 * np.pi * 0.9765625 * t))
            for c in range(N_CH):
                out[:, c] = sq + self._rng.normal(0, 2, n)
            return out
        if self.mode == "SHORT":                    # 输入短路 -> 仅本底噪声
            for c in range(N_CH):
                out[:, c] = self._rng.normal(0, 0.6, n) + self._dc[c] * 0.02
            return out

        for c in range(N_CH):
            alpha = (12 + 6 * np.sin(2 * np.pi * 0.15 * t + c)) * \
                    np.sin(2 * np.pi * (9.5 + 0.3 * c) * t + self._phase[c])
            theta = 6 * np.sin(2 * np.pi * (5.0 + 0.2 * c) * t + 1.3 * c)
            beta = 3 * np.sin(2 * np.pi * (20.0 + 0.7 * c) * t + 0.4 * c)
            hum = 5 * np.sin(2 * np.pi * 50.0 * t + 0.5 * c)
            drift = 25 * np.sin(2 * np.pi * 0.25 * t + 0.8 * c)
            noise = self._rng.normal(0, 4.0, n)
            out[:, c] = alpha + theta + beta + hum + drift + noise + self._dc[c]

        # 偶发眨眼伪迹(前额两通道)
        if self._blink_left <= 0 and self._rng.random() < 0.02:
            self._blink_left = int(0.35 * self.fs)
        if self._blink_left > 0:
            k = min(n, self._blink_left)
            env = np.hanning(2 * k)[:k] * 120.0
            out[:k, 0] += env
            out[:k, 1] += env * 0.8
            self._blink_left -= k
        return out


# =============================================================================
#  小控件
# =============================================================================
class StatusLight(QtWidgets.QFrame):
    """带彩色指示灯的状态框。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(8)
        self._dot = QtWidgets.QLabel("●")
        f = self._dot.font()
        f.setPointSize(f.pointSize() + 6)
        self._dot.setFont(f)
        self._label = QtWidgets.QLabel(text)
        self._label.setWordWrap(True)
        lay.addWidget(self._dot, 0)
        lay.addWidget(self._label, 1)
        self.set_state("#9e9e9e", text)

    def set_state(self, color: str, text: str):
        self._dot.setStyleSheet(f"color:{color};")
        self._label.setText(text)


class LeadOffBar(QtWidgets.QWidget):
    """8 通道导联脱落指示条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.cells = []
        for i in range(N_CH):
            lb = QtWidgets.QLabel(str(i + 1))
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setFixedHeight(20)
            lb.setMinimumWidth(24)
            lb.setStyleSheet("background:#e0e0e0;border:1px solid #b0b0b0;border-radius:3px;")
            lay.addWidget(lb)
            self.cells.append(lb)

    def update_state(self, loffp: int, loffn: int, enabled: bool):
        for i, lb in enumerate(self.cells):
            if not enabled:
                lb.setStyleSheet("background:#e0e0e0;border:1px solid #b0b0b0;"
                                 "border-radius:3px;color:#909090;")
                lb.setToolTip("导联脱落检测未启用")
                continue
            bad = bool((loffp >> i) & 1) or bool((loffn >> i) & 1)
            if bad:
                lb.setStyleSheet("background:#e53935;border:1px solid #b71c1c;"
                                 "border-radius:3px;color:white;font-weight:bold;")
                lb.setToolTip(f"CH{i+1} 电极脱落 / 阻抗过高")
            else:
                lb.setStyleSheet("background:#43a047;border:1px solid #2e7d32;"
                                 "border-radius:3px;color:white;")
                lb.setToolTip(f"CH{i+1} 接触正常")


# =============================================================================
#  主窗口
# =============================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, demo: bool = False, window_s: float = 5.0):
        super().__init__()
        self.setWindowTitle("ESP32-S3 + ADS1299  8 通道脑电采集上位机")
        self.resize(1400, 880)

        # ---- 数据通道 ----
        self.data_q: "queue.Queue" = queue.Queue(maxsize=4000)
        self.evt_q: "queue.Queue" = queue.Queue()
        self.transport: Optional[Transport] = None
        self.demo_default = demo

        # ---- 运行状态 ----
        self.fs = 250
        self.gain = 24
        self.connected = False
        self.streaming = False
        self.window_s = float(window_s)
        self.display_filtered = True
        self.loff_enabled = False

        self.ring = RingBuffer(int(self.fs * self.window_s))
        self.filters = FilterBank()
        self.recorder = Recorder()
        self.recorder.start()
        self.recording = False
        self.rec_t0 = 0.0
        self.rec_idx0 = 0

        # 统计
        self.last_idx: Optional[int] = None
        self.last_n = 0
        self.stat_samples = 0
        self.stat_packets = 0
        self.stat_bytes = 0
        self.stat_lost = 0
        self.stat_bad = 0
        self._win_samples = 0
        self._win_packets = 0
        self._win_bytes = 0
        self._win_t = time.perf_counter()
        self._pps = 0.0
        self._sps = 0.0
        self._kbps = 0.0
        self.last_pkt_t = 0.0
        self.t_connect = 0.0
        self.dev_status: Dict[str, Any] = {}

        # ---- 界面 ----
        self._build_ui()
        self._apply_filter_settings()
        self._update_conn_indicator()
        self._update_tx_indicator()

        # ---- 定时器 ----
        self.timer_plot = QtCore.QTimer(self)
        self.timer_plot.timeout.connect(self._on_tick)
        self.timer_plot.start(33)                       # ~30 FPS
        self.timer_stat = QtCore.QTimer(self)
        self.timer_stat.timeout.connect(self._on_stats)
        self.timer_stat.start(500)

        if not SCIPY_OK:
            self.statusBar().showMessage("警告: 未安装 scipy, 滤波功能不可用 (pip install scipy)")
        elif not BLEAK_OK and not demo:
            self.statusBar().showMessage("警告: 未安装 bleak, 只能使用模拟模式 (pip install bleak)")
        else:
            self.statusBar().showMessage("就绪。请先点击[扫描设备]。")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ============ 顶部常驻指示条 (不随左侧面板滚动而消失) ============
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self.ind_conn = StatusLight("未连接")           # 连接成功 / 失败 指示框
        self.ind_tx = StatusLight("空闲")               # 数据传输状态 指示框
        self.ind_rec = StatusLight("未记录")            # 数据记录状态 指示框
        for w_, stretch in ((self.ind_conn, 3), (self.ind_tx, 4), (self.ind_rec, 3)):
            w_.setMinimumHeight(38)
            bar.addWidget(w_, stretch)
        outer.addLayout(bar, 0)

        root = QtWidgets.QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        outer.addLayout(root, 1)

        # ============ 左侧控制面板 (置于滚动区, 兼容小屏与 macOS) ============
        panel = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(panel)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.setSpacing(8)

        pl.addWidget(self._grp_connection())
        pl.addWidget(self._grp_acquire())
        pl.addWidget(self._grp_filter())
        pl.addWidget(self._grp_display())
        pl.addWidget(self._grp_record())
        pl.addWidget(self._grp_transfer())
        pl.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(420)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ============ 右侧波形 ============
        pg.setConfigOptions(antialias=False, background="w", foreground="#303030")
        self.glw = pg.GraphicsLayoutWidget()
        self.plots: List[pg.PlotItem] = []
        self.curves: List[pg.PlotDataItem] = []
        self._build_plots()

        root.addWidget(scroll, 0)
        root.addWidget(self.glw, 1)

    def _grp_connection(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("1. 设备连接")
        lay = QtWidgets.QVBoxLayout(g)

        self.chk_demo = QtWidgets.QCheckBox("模拟模式 (无需硬件)")
        self.chk_demo.setChecked(self.demo_default)
        self.chk_demo.setToolTip("勾选后使用内置合成脑电信号, 用于验证界面/滤波/保存流程")
        lay.addWidget(self.chk_demo)

        self.chk_only_eeg = QtWidgets.QCheckBox("只显示 EEG 设备")
        self.chk_only_eeg.setChecked(True)
        lay.addWidget(self.chk_only_eeg)

        row = QtWidgets.QHBoxLayout()
        self.btn_scan = QtWidgets.QPushButton("扫描设备")
        self.btn_scan.clicked.connect(self._on_scan)
        row.addWidget(self.btn_scan)
        lay.addLayout(row)

        self.cmb_dev = QtWidgets.QComboBox()
        self.cmb_dev.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        lay.addWidget(self.cmb_dev)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_conn = QtWidgets.QPushButton("连接")
        self.btn_conn.clicked.connect(self._on_connect)
        self.btn_disc = QtWidgets.QPushButton("断开")
        self.btn_disc.clicked.connect(self._on_disconnect)
        self.btn_disc.setEnabled(False)
        row2.addWidget(self.btn_conn)
        row2.addWidget(self.btn_disc)
        lay.addLayout(row2)
        return g

    def _grp_acquire(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("2. 采集控制")
        lay = QtWidgets.QVBoxLayout(g)

        self.btn_run = QtWidgets.QPushButton("启动采集")
        self.btn_run.setMinimumHeight(38)
        f = self.btn_run.font(); f.setBold(True); self.btn_run.setFont(f)
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._on_run_pause)
        lay.addWidget(self.btn_run)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cmb_sr = QtWidgets.QComboBox()
        self.cmb_sr.addItems(["250", "500", "1000", "2000", "4000"])
        self.cmb_sr.setCurrentText("250")
        self.cmb_sr.currentTextChanged.connect(
            lambda t: self._send(f"SR:{t}"))
        form.addRow("采样率 (SPS)", self.cmb_sr)

        self.cmb_gain = QtWidgets.QComboBox()
        self.cmb_gain.addItems([str(x) for x in GAIN_TABLE])
        self.cmb_gain.setCurrentText("24")
        self.cmb_gain.currentTextChanged.connect(
            lambda t: self._send(f"GAIN:{t}"))
        form.addRow("PGA 增益", self.cmb_gain)

        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItem("正常电极输入", "NORMAL")
        self.cmb_mode.addItem("输入短路 (测本底噪声)", "SHORT")
        self.cmb_mode.addItem("内部方波测试信号", "TEST")
        self.cmb_mode.addItem("电源电压 MVDD", "MVDD")
        self.cmb_mode.addItem("芯片温度", "TEMP")
        self.cmb_mode.currentIndexChanged.connect(
            lambda i: self._send(f"MODE:{self.cmb_mode.itemData(i)}"))
        form.addRow("输入模式", self.cmb_mode)
        lay.addLayout(form)

        self.chk_srb1 = QtWidgets.QCheckBox("共模参考 SRB1 (8 通道共参考)")
        self.chk_srb1.setChecked(True)
        self.chk_srb1.setToolTip("置位 MISC1.SRB1, 把 SRB1 参考电极接到全部通道的负输入端")
        self.chk_srb1.toggled.connect(lambda b: self._send(f"SRB1:{1 if b else 0}"))
        lay.addWidget(self.chk_srb1)

        self.chk_bias = QtWidgets.QCheckBox("偏置(右腿)驱动 BIAS_OUT")
        self.chk_bias.setChecked(True)
        self.chk_bias.toggled.connect(lambda b: self._send(f"BIAS:{1 if b else 0}"))
        lay.addWidget(self.chk_bias)

        self.chk_loff = QtWidgets.QCheckBox("导联脱落检测 (6 nA 直流)")
        self.chk_loff.toggled.connect(self._on_loff_toggle)
        lay.addWidget(self.chk_loff)

        lay.addWidget(QtWidgets.QLabel("电极接触状态:"))
        self.leadoff = LeadOffBar()
        lay.addWidget(self.leadoff)
        return g

    def _grp_filter(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("3. 滤波范围")
        lay = QtWidgets.QVBoxLayout(g)

        self.chk_filter = QtWidgets.QCheckBox("启用实时滤波")
        self.chk_filter.setChecked(True)
        self.chk_filter.toggled.connect(self._apply_filter_settings)
        lay.addWidget(self.chk_filter)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.spn_hp = QtWidgets.QDoubleSpinBox()
        self.spn_hp.setRange(0.0, 200.0); self.spn_hp.setDecimals(2)
        self.spn_hp.setSingleStep(0.1); self.spn_hp.setValue(0.5)
        self.spn_hp.setSuffix(" Hz")
        self.spn_hp.setToolTip("高通截止频率, 设为 0 表示不做高通")
        form.addRow("高通下限", self.spn_hp)

        self.spn_lp = QtWidgets.QDoubleSpinBox()
        self.spn_lp.setRange(0.0, 2000.0); self.spn_lp.setDecimals(1)
        self.spn_lp.setSingleStep(1.0); self.spn_lp.setValue(45.0)
        self.spn_lp.setSuffix(" Hz")
        self.spn_lp.setToolTip("低通截止频率, 设为 0 或 >= 奈奎斯特频率表示不做低通")
        form.addRow("低通上限", self.spn_lp)

        self.cmb_order = QtWidgets.QComboBox()
        self.cmb_order.addItems(["2", "4", "6", "8"])
        self.cmb_order.setCurrentText("4")
        form.addRow("巴特沃斯阶数", self.cmb_order)

        self.cmb_notch = QtWidgets.QComboBox()
        self.cmb_notch.addItem("关闭", 0)
        self.cmb_notch.addItem("50 Hz (中国/欧洲)", 50)
        self.cmb_notch.addItem("60 Hz (北美/日本)", 60)
        self.cmb_notch.setCurrentIndex(1)
        form.addRow("工频陷波", self.cmb_notch)

        self.spn_harm = QtWidgets.QSpinBox()
        self.spn_harm.setRange(1, 5); self.spn_harm.setValue(2)
        self.spn_harm.setToolTip("同时陷除的谐波个数, 2 表示 50 与 100 Hz")
        form.addRow("陷波谐波数", self.spn_harm)
        lay.addLayout(form)

        row = QtWidgets.QHBoxLayout()
        btn_apply = QtWidgets.QPushButton("应用滤波")
        btn_apply.clicked.connect(self._apply_filter_settings)
        btn_reset = QtWidgets.QPushButton("重置状态")
        btn_reset.clicked.connect(lambda: (self.filters.reset(), self.ring.reset()))
        row.addWidget(btn_apply); row.addWidget(btn_reset)
        lay.addLayout(row)

        self.lbl_filter = QtWidgets.QLabel("—")
        self.lbl_filter.setWordWrap(True)
        self.lbl_filter.setStyleSheet("color:#555;")
        lay.addWidget(self.lbl_filter)
        return g

    def _grp_display(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("4. 显示设置")
        lay = QtWidgets.QVBoxLayout(g)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.spn_win = QtWidgets.QDoubleSpinBox()
        self.spn_win.setRange(1.0, 60.0); self.spn_win.setDecimals(1)
        self.spn_win.setValue(self.window_s); self.spn_win.setSuffix(" s")
        self.spn_win.valueChanged.connect(self._on_window_change)
        form.addRow("时间窗", self.spn_win)

        self.cmb_amp = QtWidgets.QComboBox()
        self.cmb_amp.addItem("自动", 0)
        for v in (10, 25, 50, 100, 200, 500, 1000, 2000, 5000):
            self.cmb_amp.addItem(f"±{v} µV", v)
        self.cmb_amp.setCurrentIndex(4)          # 默认 ±100 µV
        self.cmb_amp.currentIndexChanged.connect(self._apply_yrange)
        form.addRow("幅度量程", self.cmb_amp)

        self.cmb_src = QtWidgets.QComboBox()
        self.cmb_src.addItem("滤波后信号", True)
        self.cmb_src.addItem("原始信号", False)
        self.cmb_src.currentIndexChanged.connect(self._on_src_change)
        form.addRow("显示内容", self.cmb_src)
        lay.addLayout(form)

        lay.addWidget(QtWidgets.QLabel("显示通道:"))
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)
        self.chk_ch = []
        for i in range(N_CH):
            c = QtWidgets.QCheckBox(f"CH{i+1}")
            c.setChecked(True)
            c.setStyleSheet(f"color:{CH_COLORS[i]};font-weight:bold;")
            c.toggled.connect(self._build_plots)
            grid.addWidget(c, i // 4, i % 4)
            self.chk_ch.append(c)
        lay.addLayout(grid)

        btn_clear = QtWidgets.QPushButton("清空波形")
        btn_clear.clicked.connect(lambda: (self.ring.reset(), self.filters.reset()))
        lay.addWidget(btn_clear)
        return g

    def _grp_record(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("5. 数据保存")
        lay = QtWidgets.QVBoxLayout(g)

        self.cmb_recmode = QtWidgets.QComboBox()
        self.cmb_recmode.addItem("原始 + 滤波", "both")
        self.cmb_recmode.addItem("仅原始", "raw")
        self.cmb_recmode.addItem("仅滤波", "filt")
        lay.addWidget(self.cmb_recmode)

        row = QtWidgets.QHBoxLayout()
        self.edt_path = QtWidgets.QLineEdit(self._default_path())
        btn_browse = QtWidgets.QPushButton("浏览…")
        btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.edt_path, 1)
        row.addWidget(btn_browse, 0)
        lay.addLayout(row)

        self.btn_rec = QtWidgets.QPushButton("开始记录")
        self.btn_rec.setMinimumHeight(32)
        self.btn_rec.clicked.connect(self._on_record)
        lay.addWidget(self.btn_rec)
        return g

    def _grp_transfer(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("6. 传输统计")
        lay = QtWidgets.QVBoxLayout(g)

        self.lbl_tx = QtWidgets.QLabel("—")
        self.lbl_tx.setWordWrap(True)
        self.lbl_tx.setStyleSheet(
            "font-family:Menlo,Consolas,monospace;font-size:11px;color:#333;")
        lay.addWidget(self.lbl_tx)

        self.lbl_dev = QtWidgets.QLabel("设备信息: —")
        self.lbl_dev.setWordWrap(True)
        self.lbl_dev.setStyleSheet("color:#555;font-size:11px;")
        lay.addWidget(self.lbl_dev)
        return g

    # ------------------------------------------------------------- 波形区 --
    def _build_plots(self):
        vis = [i for i in range(N_CH) if self.chk_ch[i].isChecked()] if hasattr(self, "chk_ch") \
            else list(range(N_CH))
        if not vis:
            vis = [0]
            self.chk_ch[0].setChecked(True)
        self.visible = vis

        self.glw.clear()
        self.plots = []
        self.curves = []
        for r, ch in enumerate(vis):
            p = self.glw.addPlot(row=r, col=0)
            p.setMenuEnabled(False)
            p.showGrid(x=True, y=True, alpha=0.18)
            p.setLabel("left", f"CH{ch+1}", units=None)
            ax = p.getAxis("left")
            ax.setWidth(72)
            ax.enableAutoSIPrefix(False)
            p.getAxis("bottom").setStyle(showValues=(r == len(vis) - 1))
            if r == len(vis) - 1:
                p.setLabel("bottom", "时间", units="s")
            if r > 0:
                p.setXLink(self.plots[0])
            p.setXRange(-self.window_s, 0, padding=0)
            p.setMouseEnabled(x=False, y=True)
            c = p.plot(pen=pg.mkPen(CH_COLORS[ch], width=1))
            c.setDownsampling(auto=True, method="peak")
            c.setClipToView(True)
            self.plots.append(p)
            self.curves.append(c)
        self._apply_yrange()

    def _apply_yrange(self):
        amp = self.cmb_amp.currentData() if hasattr(self, "cmb_amp") else 100
        for p in self.plots:
            if amp:
                p.disableAutoRange(axis=pg.ViewBox.YAxis)
                p.setYRange(-amp, amp, padding=0)
            else:
                p.enableAutoRange(axis=pg.ViewBox.YAxis)

    # -------------------------------------------------------------- 事件 --
    def _default_path(self) -> str:
        base = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        return os.path.join(base, time.strftime("eeg_%Y%m%d_%H%M%S.csv"))

    def _ensure_transport(self):
        want_demo = self.chk_demo.isChecked()
        cur = self.transport
        if cur is not None:
            if (want_demo and cur.name == "DEMO") or (not want_demo and cur.name == "BLE"):
                return
            cur.shutdown()
            self.transport = None
        try:
            self.transport = DemoTransport(self.data_q, self.evt_q) if want_demo \
                else BleTransport(self.data_q, self.evt_q)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", str(e))
            self.transport = None

    def _send(self, cmd: str):
        if self.transport is not None and self.connected:
            self.transport.send_cmd(cmd)

    def _on_scan(self):
        self._ensure_transport()
        if self.transport is None:
            return
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("扫描中…")
        self.cmb_dev.clear()
        self.statusBar().showMessage("正在扫描 BLE 设备…")
        self.transport.scan(timeout=6.0, only_eeg=self.chk_only_eeg.isChecked())

    def _on_connect(self):
        if self.cmb_dev.count() == 0:
            QtWidgets.QMessageBox.information(self, "提示", "请先扫描并选择设备。")
            return
        self._ensure_transport()
        if self.transport is None:
            return
        key = self.cmb_dev.currentData()
        self.btn_conn.setEnabled(False)
        self.transport.connect(key)

    def _on_disconnect(self):
        if self.transport is not None:
            if self.streaming:
                self.transport.send_cmd("STOP")
            self.transport.disconnect()

    def _on_run_pause(self):
        if not self.connected:
            return
        if self.streaming:
            self._send("STOP")
            self.streaming = False
        else:
            self.filters.reset()
            self.last_idx = None
            self._send("START")
            self.streaming = True
        self._update_run_button()
        self._update_tx_indicator()

    def _update_run_button(self):
        self.btn_run.setText("暂停采集" if self.streaming else "启动采集")
        self.btn_run.setStyleSheet(
            "background:#ef6c00;color:white;" if self.streaming else
            "background:#2e7d32;color:white;")

    def _on_loff_toggle(self, b: bool):
        self.loff_enabled = b
        self._send(f"LOFF:{1 if b else 0}")
        self.leadoff.update_state(0, 0, b)

    def _on_window_change(self, v: float):
        self.window_s = float(v)
        self.ring.reset(int(self.fs * self.window_s))
        for p in self.plots:
            p.setXRange(-self.window_s, 0, padding=0)

    def _on_src_change(self, _i: int):
        self.display_filtered = bool(self.cmb_src.currentData())
        self.ring.reset()

    def _apply_filter_settings(self, *_):
        if not self.chk_filter.isChecked():
            self.filters.stages = []
            self.filters.reset()
            self.lbl_filter.setText("滤波已关闭 (显示原始信号)")
            self.ring.reset()
            return
        self.filters.configure(
            fs=float(self.fs),
            hp=self.spn_hp.value(),
            lp=self.spn_lp.value(),
            order=int(self.cmb_order.currentText()),
            notch=self.cmb_notch.currentData() or None,
            harmonics=self.spn_harm.value(),
        )
        self.lbl_filter.setText(f"当前: {self.filters.desc}   (fs = {self.fs} Hz)")
        self.ring.reset()

    def _on_browse(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "选择保存文件", self.edt_path.text(), "CSV 文件 (*.csv);;所有文件 (*)")
        if path:
            self.edt_path.setText(path)

    def _on_record(self):
        if self.recording:
            self.recorder.close()
            self.recording = False
            self.btn_rec.setText("开始记录")
            self.ind_rec.set_state("#9e9e9e",
                                   f"已停止 · 共 {self.recorder.rows_written} 个样本")
            self.edt_path.setText(self._default_path())
            self.statusBar().showMessage(f"数据已保存: {self.recorder.path}")
            return

        path = self.edt_path.text().strip()
        if not path:
            QtWidgets.QMessageBox.warning(self, "提示", "请先指定保存文件。")
            return
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "错误", f"无法创建目录: {e}")
                return
        meta = {
            "device": "ESP32-S3 + ADS1299",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fs": self.fs,
            "gain": self.gain,
            "n_channels": N_CH,
            "reference": "SRB1 common reference" if self.chk_srb1.isChecked() else "differential",
            "input_mode": self.cmb_mode.currentData(),
            "filter": self.filters.desc if self.chk_filter.isChecked() else "none",
            "host": f"{platform.system()} {platform.release()}",
        }
        self.recorder.open(path, meta, self.cmb_recmode.currentData())
        self.recording = True
        self.rec_t0 = time.time()
        self.rec_idx0 = self.last_idx if self.last_idx is not None else 0
        self.btn_rec.setText("停止记录")
        self.ind_rec.set_state("#e53935", f"记录中 · {os.path.basename(path)}")

    # --------------------------------------------------------- 主循环 --
    def _on_tick(self):
        # 1) 事件
        while True:
            try:
                ev = self.evt_q.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev)

        # 2) 数据
        got = False
        for _ in range(400):                     # 单帧最多处理 400 个包, 防止界面卡死
            try:
                pkt = self.data_q.get_nowait()
            except queue.Empty:
                break
            self._ingest(pkt)
            got = True
        if got:
            self.last_pkt_t = time.perf_counter()

        # 3) 刷新波形
        snap = self.ring.snapshot()
        n = snap.shape[0]
        t = np.linspace(-self.window_s, 0.0, n, dtype=np.float32)
        for k, ch in enumerate(self.visible):
            self.curves[k].setData(t, snap[:, ch], connect="finite")

    def _ingest(self, pkt: Dict[str, Any]):
        fs = int(pkt["fs"])
        if fs != self.fs:
            self.fs = fs
            self.ring.reset(int(self.fs * self.window_s))
            self._apply_filter_settings()
            if self.cmb_sr.currentText() != str(fs):
                self.cmb_sr.blockSignals(True)
                self.cmb_sr.setCurrentText(str(fs))
                self.cmb_sr.blockSignals(False)
        self.gain = int(pkt["gain"])

        # 丢包统计
        if self.last_idx is not None:
            exp = (self.last_idx + self.last_n) & 0xFFFFFFFF
            if pkt["sidx"] != exp:
                gap = (pkt["sidx"] - exp) & 0xFFFFFFFF
                if gap < (1 << 28):
                    self.stat_lost += gap
                self.filters.reset()             # 时间不连续 -> 复位滤波器状态
        self.last_idx = pkt["sidx"]
        self.last_n = pkt["ns"]

        raw = pkt["data"]
        filt = self.filters.process(raw) if self.chk_filter.isChecked() else raw
        self.ring.push(filt if self.display_filtered else raw)

        if self.recording:
            t0 = self.rec_t0 + (((pkt["sidx"] - self.rec_idx0) & 0xFFFFFFFF) / float(self.fs))
            self.recorder.write(t0, pkt["sidx"], raw, filt)

        self.stat_samples += pkt["ns"]
        self.stat_packets += 1
        self.stat_bytes += pkt["nbytes"]
        self._win_samples += pkt["ns"]
        self._win_packets += 1
        self._win_bytes += pkt["nbytes"]
        if pkt["ovf"]:
            self.stat_lost += 0                  # 具体丢样数由固件的序号跳变体现

        self.leadoff.update_state(pkt["loffp"], pkt["loffn"], self.loff_enabled)

    def _handle_event(self, ev: Dict[str, Any]):
        k = ev.get("kind")
        if k == "scan_started":
            self.ind_conn.set_state("#fbc02d", "正在扫描…")
        elif k == "scan_done":
            self.btn_scan.setEnabled(True)
            self.btn_scan.setText("扫描设备")
            devs = ev.get("devices", [])
            self.cmb_dev.clear()
            for d in devs:
                rssi = f"  {d['rssi']} dBm" if d.get("rssi") is not None else ""
                self.cmb_dev.addItem(f"{d['name']}{rssi}", d["key"])
            if devs:
                self.statusBar().showMessage(f"扫描完成, 找到 {len(devs)} 个设备。")
            else:
                hint = ""
                if platform.system() == "Darwin":
                    hint = "  macOS 请在 [系统设置>隐私与安全性>蓝牙] 中允许本程序访问蓝牙。"
                self.statusBar().showMessage(
                    "未找到设备。请确认设备已上电并在广播；可取消勾选[只显示 EEG 设备]。" + hint)
            self._update_conn_indicator()
        elif k == "connecting":
            self.ind_conn.set_state("#fbc02d", "正在连接…")
        elif k == "connected":
            self.connected = True
            self.streaming = False
            self.t_connect = time.time()
            self.last_idx = None
            self.stat_samples = self.stat_packets = self.stat_bytes = 0
            self.stat_lost = self.stat_bad = 0
            self.ring.reset()
            self.filters.reset()
            self._dev_name = ev.get("name", "")
            self._dev_mtu = ev.get("mtu", 0)
            self.btn_conn.setEnabled(False)
            self.btn_disc.setEnabled(True)
            self.btn_run.setEnabled(True)
            self._update_run_button()
            self._update_conn_indicator()
            self.statusBar().showMessage(f"已连接 {ev.get('name')} ({ev.get('addr')})")
            # 把界面上的当前配置下发给设备, 保证两边一致
            for c in (f"SR:{self.cmb_sr.currentText()}",
                      f"GAIN:{self.cmb_gain.currentText()}",
                      f"MODE:{self.cmb_mode.currentData()}",
                      f"SRB1:{1 if self.chk_srb1.isChecked() else 0}",
                      f"BIAS:{1 if self.chk_bias.isChecked() else 0}",
                      f"LOFF:{1 if self.chk_loff.isChecked() else 0}"):
                self._send(c)
        elif k == "conn_failed":
            self.connected = False
            self.btn_conn.setEnabled(True)
            self.btn_disc.setEnabled(False)
            self.btn_run.setEnabled(False)
            self.ind_conn.set_state("#e53935", f"连接失败: {ev.get('msg','')}")
            self.statusBar().showMessage(f"连接失败: {ev.get('msg','')}")
        elif k == "disconnected":
            was = self.connected
            self.connected = False
            self.streaming = False
            self.btn_conn.setEnabled(True)
            self.btn_disc.setEnabled(False)
            self.btn_run.setEnabled(False)
            self._update_run_button()
            self._update_conn_indicator()
            self._update_tx_indicator()
            if was:
                self.statusBar().showMessage("设备已断开。")
        elif k == "devstatus":
            self.dev_status = ev.get("data", {}) or {}
            self._update_dev_label()
        elif k == "badpkt":
            self.stat_bad += 1
        elif k == "error":
            self.statusBar().showMessage(str(ev.get("msg", "")))

    # --------------------------------------------------------- 指示框 --
    def _update_conn_indicator(self):
        if self.connected:
            mtu = getattr(self, "_dev_mtu", 0)
            name = getattr(self, "_dev_name", "设备")
            extra = f" · MTU {mtu}" if mtu else ""
            self.ind_conn.set_state("#43a047", f"已连接: {name}{extra}")
        else:
            self.ind_conn.set_state("#9e9e9e", "未连接")

    def _update_tx_indicator(self):
        if not self.connected:
            self.ind_tx.set_state("#9e9e9e", "未连接 · 无数据")
            return
        if not self.streaming:
            self.ind_tx.set_state("#1e88e5", "已连接 · 待机 (未启动采集)")
            return
        idle = time.perf_counter() - self.last_pkt_t if self.last_pkt_t else 99.0
        if idle > 1.5:
            self.ind_tx.set_state("#e53935", f"数据中断! 已 {idle:.1f} s 未收到数据包")
        elif self.stat_lost and self._loss_pct() > 0.5:
            self.ind_tx.set_state("#fb8c00",
                                  f"传输中(有丢包 {self._loss_pct():.2f}%) · "
                                  f"{self._pps:.0f} 包/s · {self._sps:.0f} 样本/s")
        else:
            self.ind_tx.set_state("#43a047",
                                  f"传输正常 · {self._pps:.0f} 包/s · "
                                  f"{self._sps:.0f} 样本/s · {self._kbps:.1f} KB/s")

    def _loss_pct(self) -> float:
        return 100.0 * self.stat_lost / max(1, self.stat_samples + self.stat_lost)

    def _update_dev_label(self):
        s = self.dev_status
        if not s:
            self.lbl_dev.setText("设备信息: —")
            return
        self.lbl_dev.setText(
            f"设备信息: 固件 {s.get('fw','?')} · ADS ID {s.get('id','?')} · "
            f"{s.get('sr','?')} SPS · 增益 x{s.get('gain','?')} · {s.get('mode','?')} · "
            f"MTU {s.get('mtu','?')} · 每包 {s.get('spp','?')} 样本 · 端内丢样 {s.get('lost',0)}")

    def _on_stats(self):
        now = time.perf_counter()
        dt = max(1e-6, now - self._win_t)
        pps = self._pps = self._win_packets / dt
        sps = self._sps = self._win_samples / dt
        kbps = self._kbps = self._win_bytes / dt / 1024.0
        self._win_packets = self._win_samples = self._win_bytes = 0
        self._win_t = now

        dur = (self.stat_samples / self.fs) if self.fs else 0.0
        loss_pct = self._loss_pct()
        self.lbl_tx.setText(
            f"包速率   : {pps:6.1f} 包/s\n"
            f"采样速率 : {sps:6.1f} 样本/s   (标称 {self.fs})\n"
            f"吞吐     : {kbps:6.2f} KB/s\n"
            f"累计样本 : {self.stat_samples}   ({dur:.1f} s)\n"
            f"丢失样本 : {self.stat_lost}   ({loss_pct:.3f} %)\n"
            f"校验失败 : {self.stat_bad} 包")
        self._update_tx_indicator()

        if self.recording:
            self.ind_rec.set_state(
                "#e53935",
                f"记录中 · {os.path.basename(self.recorder.path)} · "
                f"{self.recorder.rows_written} 样本")
            if self.recorder.error:
                self.ind_rec.set_state("#e53935", f"记录出错: {self.recorder.error}")

    # ---------------------------------------------------------- 关闭 --
    def closeEvent(self, e: QtGui.QCloseEvent):
        try:
            if self.recording:
                self.recorder.close()
            self.recorder.quit()
            if self.transport is not None:
                if self.connected and self.streaming:
                    self.transport.send_cmd("STOP")
                    time.sleep(0.1)
                self.transport.shutdown()
        except Exception:
            pass
        e.accept()


# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="ESP32-S3 + ADS1299 八通道脑电上位机")
    ap.add_argument("--demo", action="store_true", help="启动时默认使用模拟数据(无需硬件)")
    ap.add_argument("--window", type=float, default=5.0, help="波形时间窗长度(秒), 默认 5")
    args = ap.parse_args()

    QtWidgets.QApplication.setApplicationName("EEG Host")
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")                       # 三平台外观一致

    w = MainWindow(demo=args.demo, window_s=args.window)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
