import os
import random as r
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageTk
from io import BytesIO
import json
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog
import subprocess
import sys
import time
import platform
import re
from queue import Queue
import queue
import zipfile
import shutil
import webbrowser
import hashlib
import uuid as uuid_lib
import base64 as _b64
import colorsys
import math
import importlib.util
from array import array

try:
    HAS_WGPU = importlib.util.find_spec("wgpu") is not None
except Exception:
    HAS_WGPU = False
wgpu = None

try:
    import pystray
    from PIL import ImageDraw
    HAS_TRAY = True
except Exception:
    pystray = None
    HAS_TRAY = False
                                                     
class LaunchHistory:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.file = self.base_dir / "launcher_history.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self):
        if self.file.exists():
            try:
                with open(self.file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"records": []}

    def _save(self):
        try:
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add(self, version, duration_sec, exit_code):
        rec = {
            "version": version,
            "start": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(time.time() - duration_sec)),
            "duration": int(duration_sec),
            "exit_code": exit_code,
        }
        self.data["records"].append(rec)
                    
        if len(self.data["records"]) > 500:
            self.data["records"] = self.data["records"][-500:]
        self._save()

    def stats(self):
        recs = self.data.get("records", [])
        total = sum(int(r.get("duration", 0)) for r in recs)
        today = time.strftime("%Y-%m-%d")
        today_sec = sum(int(r.get("duration", 0)) for r in recs
                        if str(r.get("start", "")).startswith(today))
        week_sec = 0
        now = time.time()
        for r in recs:
            try:
                t = time.mktime(time.strptime(r["start"], "%Y-%m-%d %H:%M:%S"))
                if now - t <= 7 * 86400:
                    week_sec += int(r.get("duration", 0))
            except Exception:
                pass
        crashes = sum(1 for r in recs if r.get("exit_code") not in (0, None))

               
        per_version = {}
        for r in recs:
            ver = r.get("version", "未知")
            per_version[ver] = per_version.get(ver, 0) + int(r.get("duration", 0))

        return {
            "total_sec": total,
            "today_sec": today_sec,
            "week_sec": week_sec,
            "total_count": len(recs),
            "crash_count": crashes,
            "per_version": per_version,
        }

    def show(self, parent):
        s = self.stats()
        d = tk.Toplevel(parent)
        d.title("📊 启动历史 & 计时统计")
        d.geometry("820x560")
        d.transient(parent)

        top = ttk.Frame(d, padding=10)
        top.pack(fill=tk.X)

        def fmt(sec):
            sec = max(0, int(sec))
            h, rem = divmod(sec, 3600)
            m, s_ = divmod(rem, 60)
            if h:
                return f"{h} 时 {m} 分"
            if m:
                return f"{m} 分 {s_} 秒"
            return f"{s_} 秒"

        cards = [
            ("📅 今日", fmt(s["today_sec"])),
            ("🗓️ 本周", fmt(s["week_sec"])),
            ("⏱️ 总时长", fmt(s["total_sec"])),
            ("🎮 启动次数", f"{s['total_count']} 次"),
            ("💥 崩溃次数", f"{s['crash_count']} 次"),
        ]
        for i, (label, value) in enumerate(cards):
            card = tk.Frame(top, bg="#f0f0f5", padx=12, pady=8)
            card.grid(row=0, column=i, padx=4, sticky="nsew")
            tk.Label(card, text=label, bg="#f0f0f5", fg="#666").pack()
            tk.Label(card, text=value, bg="#f0f0f5",
                     font=("Microsoft YaHei", 12, "bold")).pack()
            top.grid_columnconfigure(i, weight=1)

               
        lf = ttk.LabelFrame(d, text="按版本统计", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        cols = ("version", "time", "count")
        tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        tree.heading("version", text="版本")
        tree.heading("time", text="累计时长")
        tree.heading("count", text="启动次数")
        tree.column("version", width=280)
        tree.column("time", width=160, anchor="center")
        tree.column("count", width=100, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True)

                  
        count_map = {}
        for r in self.data.get("records", []):
            ver = r.get("version", "未知")
            count_map[ver] = count_map.get(ver, 0) + 1
        for ver, sec in sorted(s["per_version"].items(),
                                key=lambda x: x[1], reverse=True):
            tree.insert("", "end", values=(ver, fmt(sec), count_map.get(ver, 0)))

        ttk.Button(d, text="❌ 关闭", command=d.destroy).pack(pady=6)
                                                     
class ModConflictDetector:
    """扫描 mods 目录，检测：重复 modid、多版本并存、缺失前置、损坏 jar"""

    def __init__(self, log_callback=None):
        self.log = log_callback or print

                                    
    @staticmethod
    def _parse_jar(jar_path: Path):
        info = {
            "path": jar_path,
            "name": jar_path.name,
            "loader": None,                                 
            "modid": None,
            "version": None,
            "deps": [],                                                            
            "error": None,
        }
        try:
            with zipfile.ZipFile(jar_path) as zf:
                names = zf.namelist()

                                              
                if "fabric.mod.json" in names:
                    info["loader"] = "fabric"
                    data = json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace"))
                    info["modid"] = data.get("id")
                    info["version"] = data.get("version")
                    for dep_id, dep_ver in (data.get("depends") or {}).items():
                        if dep_id in ("minecraft", "java", "fabricloader"):
                            continue
                        info["deps"].append({
                            "modid": dep_id,
                            "version": str(dep_ver) if not isinstance(dep_ver, list) else ",".join(map(str, dep_ver)),
                            "mandatory": True,
                        })
                    return info

                                                        
                for toml_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                    if toml_name in names:
                        info["loader"] = "neoforge" if "neoforge" in toml_name else "forge"
                        text = zf.read(toml_name).decode("utf-8", "replace")
                        info.update(ModConflictDetector._parse_toml(text))
                        return info

                info["error"] = "没有找到 mod 描述文件（可能不是 mod，或已损坏）"
        except zipfile.BadZipFile:
            info["error"] = "不是有效的 zip/jar（文件可能损坏或没下载完）"
        except Exception as e:
            info["error"] = f"解析失败: {e}"
        return info

    @staticmethod
    def _parse_toml(text):
        """极简 TOML 解析，够读 modid/version/dependencies 就行"""
        result = {"modid": None, "version": None, "deps": []}
        try:
                        
            mods_blocks = re.findall(r"\[\[mods\]\](.*?)(?=\[\[|\Z)", text, re.S)
            if mods_blocks:
                block = mods_blocks[0]
                m = re.search(r'^\s*modId\s*=\s*"([^"]+)"', block, re.M)
                if m: result["modid"] = m.group(1)
                m = re.search(r'^\s*version\s*=\s*"([^"]+)"', block, re.M)
                if m: result["version"] = m.group(1)

                                        
            dep_blocks = re.findall(
                r"\[\[dependencies\.[^\]]+\]\](.*?)(?=\[\[|\Z)", text, re.S
            )
            for block in dep_blocks:
                dep = {"modid": None, "version": None, "mandatory": True}
                m = re.search(r'^\s*modId\s*=\s*"([^"]+)"', block, re.M)
                if m: dep["modid"] = m.group(1)
                m = re.search(r'^\s*versionRange\s*=\s*"([^"]+)"', block, re.M)
                if m: dep["version"] = m.group(1)
                m = re.search(r'^\s*mandatory\s*=\s*(true|false)', block, re.M)
                if m: dep["mandatory"] = (m.group(1) == "true")
                m = re.search(r'^\s*type\s*=\s*"([^"]+)"', block, re.M)
                if m and m.group(1) == "incompatible":
                    dep["incompatible"] = True
                if dep["modid"] and dep["modid"] not in ("minecraft", "forge", "neoforge", "java"):
                    result["deps"].append(dep)
        except Exception as e:
            result["parse_error"] = str(e)
        return result

                                  
    def scan(self, mods_dir: Path):
        mods_dir = Path(mods_dir)
        if not mods_dir.exists():
            return {"mods": [], "issues": [], "ok": 0}

        jars = sorted(mods_dir.glob("*.jar"))
        self.log(f"🔍 扫描 {len(jars)} 个 jar...")
        try:
            _workers = min(os.cpu_count() or 4, len(jars) or 1)
            with ProcessPoolExecutor(max_workers=_workers) as _executor:
                parsed = list(_executor.map(ModConflictDetector._parse_jar, jars))
        except Exception:
            with ThreadPoolExecutor(max_workers=min(8, (len(jars) or 1))) as _executor:
                parsed = list(_executor.map(ModConflictDetector._parse_jar, jars))

        issues = []
        by_id = {}                             
        for info in parsed:
            if info["modid"]:
                by_id.setdefault(info["modid"], []).append(info)

                        
        for modid, items in by_id.items():
            if len(items) > 1:
                names = "、".join(i["name"] for i in items)
                issues.append({
                    "level": "error",
                    "type": "多版本并存",
                    "title": f"Mod「{modid}」存在 {len(items)} 个版本",
                    "detail": f"文件：{names}\n只保留一个，否则游戏会崩溃或行为异常。",
                })

                   
        known_ids = set(by_id.keys()) | {"minecraft", "forge", "neoforge", "fabricloader", "java"}
        for info in parsed:
            for dep in info["deps"]:
                dep_id = dep["modid"]
                if dep_id and dep_id not in known_ids:
                    issues.append({
                        "level": "error" if dep.get("mandatory", True) else "warning",
                        "type": "缺前置",
                        "title": f"「{info['modid']}」缺少前置：{dep_id}",
                        "detail": f"文件：{info['name']}\n需要的版本范围：{dep['version'] or '任意'}\n"
                                  f"{'必须安装，否则该 Mod 会加载失败。' if dep.get('mandatory', True) else '可选依赖，缺失通常不影响启动。'}",
                    })

                          
        for info in parsed:
            if info["error"]:
                issues.append({
                    "level": "warning",
                    "type": "损坏文件",
                    "title": f"无法解析：{info['name']}",
                    "detail": info["error"],
                })

                                
        loaders = {i["loader"] for i in parsed if i["loader"]}
        if len(loaders) > 1:
            issues.append({
                "level": "warning",
                "type": "加载器混用",
                "title": f"同一个 mods 目录里混用了多种加载器：{', '.join(loaders)}",
                "detail": "Fabric 和 Forge/NeoForge 的 Mod 不能混装，请分实例。",
            })

        ok = sum(1 for i in parsed if not i["error"])
        return {"mods": parsed, "issues": issues, "ok": ok}

                                
    def show_report(self, parent, mods_dir):
        result = self.scan(mods_dir)
        d = tk.Toplevel(parent)
        d.title(f"🔍 Mod 冲突检测 - {mods_dir.parent.name}")
        d.geometry("900x600")
        d.transient(parent)

              
        top = ttk.Frame(d, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text=f"📁 {mods_dir}", foreground="gray").pack(anchor=tk.W)
        ttk.Label(
            top,
            text=f"共 {len(result['mods'])} 个文件 · ✅ 正常 {result['ok']} · "
                 f"⚠️ 问题 {len(result['issues'])}",
            font=("Microsoft YaHei", 10, "bold")
        ).pack(anchor=tk.W, pady=4)

              
        if result["issues"]:
            lf = ttk.LabelFrame(d, text="⚠️ 检测到的问题", padding=5)
            lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
            txt = scrolledtext.ScrolledText(lf, wrap=tk.WORD, font=("Microsoft YaHei", 9))
            txt.pack(fill=tk.BOTH, expand=True)
            txt.tag_config("error", foreground="#CC0000", font=("Microsoft YaHei", 10, "bold"))
            txt.tag_config("warning", foreground="#CC8800", font=("Microsoft YaHei", 10, "bold"))
            txt.tag_config("type", foreground="#0066CC")
            txt.tag_config("detail", foreground="#444444")
            for issue in result["issues"]:
                lvl = issue["level"]
                tag = "error" if lvl == "error" else "warning"
                icon = "❌" if lvl == "error" else "⚠️"
                txt.insert(tk.END, f"{icon} [{issue['type']}] {issue['title']}\n", tag)
                txt.insert(tk.END, f"   {issue['detail']}\n\n", "detail")
            txt.config(state=tk.DISABLED)
        else:
            ttk.Label(
                d, text="✅ 未发现冲突，一切正常",
                font=("Microsoft YaHei", 12, "bold"), foreground="#008800"
            ).pack(pady=30)

              
        bf = ttk.Frame(d)
        bf.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(bf, text="📂 打开 mods 目录",
                   command=lambda: self._open_dir(mods_dir)).pack(side=tk.LEFT)
        ttk.Button(bf, text="❌ 关闭", command=d.destroy).pack(side=tk.RIGHT)

    @staticmethod
    def _open_dir(path):
        try:
            if platform.system() == "Windows":
                os.startfile(str(path))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception:
            pass
class LauncherGPURenderer:
    BACKEND_MAP = {"OpenGL": "OpenGL", "Vulkan": "Vulkan", "DirectX 12": "D3D12"}
    SHADER = """
struct Params {
  left: vec4<f32>,
  right: vec4<f32>,
  phase: vec4<f32>,
};
@group(0) @binding(0) var<uniform> params: Params;

struct VSOut {
  @builtin(position) pos: vec4<f32>,
  @location(0) uv: vec2<f32>,
};

@vertex
fn vs(@builtin(vertex_index) i: u32) -> VSOut {
  var p = array<vec2<f32>, 3>(
    vec2<f32>(-1.0, -1.0),
    vec2<f32>(3.0, -1.0),
    vec2<f32>(-1.0, 3.0)
  );
  var o: VSOut;
  o.pos = vec4<f32>(p[i], 0.0, 1.0);
  o.uv = (p[i] + vec2<f32>(1.0)) * 0.5;
  return o;
}

@fragment
fn fs(in: VSOut) -> @location(0) vec4<f32> {
  let t = clamp(in.uv.x, 0.0, 1.0);
  let e = t * t * (3.0 - 2.0 * t);
  var c = mix(params.left.rgb, params.right.rgb, e);
  let wave = sin(params.phase.x + in.uv.x * 6.28318 + vec3<f32>(0.0, 2.0, 4.0)) * 0.12;
  c = clamp(c + wave, vec3<f32>(0.0), vec3<f32>(1.0));
  return vec4<f32>(c, 1.0);
}
"""
    _adapters = None
    _adapter_lock = threading.Lock()

    @classmethod
    def _adapter_map(cls):
        global wgpu
        if not HAS_WGPU:
            return {}
        if wgpu is None:
            try:
                import wgpu as _wgpu
                wgpu = _wgpu
            except Exception:
                return {}
        with cls._adapter_lock:
            if cls._adapters is not None:
                return cls._adapters
            result = {}
            try:
                adapters = wgpu.gpu.enumerate_adapters_sync()
                for mode, backend_name in cls.BACKEND_MAP.items():
                    matches = [
                        a for a in adapters
                        if str(a.info.get("backend_type", "")) == backend_name
                    ]
                    if not matches:
                        continue
                    def rank(a):
                        t = a.info.get("adapter_type", "")
                        if t == "DiscreteGPU":
                            return 0
                        if t == "IntegratedGPU":
                            return 1
                        if t == "CPU":
                            return 3
                        return 2
                    matches.sort(key=rank)
                    result[mode] = matches[0]
            except Exception:
                result = {}
            cls._adapters = result
            return result

    @classmethod
    def probe(cls):
        report = {}
        for mode, adapter in cls._adapter_map().items():
            info = adapter.info
            report[mode] = {
                "device": info.get("device") or mode,
                "type": info.get("adapter_type") or "",
                "backend": info.get("backend_type") or mode,
            }
        return report

    def __init__(self):
        self._device = None
        self._queue = None
        self._pipeline = None
        self._bind_group = None
        self._uniform = None
        self._texture = None
        self._view = None
        self._size = None
        self._backend = None
        self._lock = threading.Lock()

    def _release(self):
        try:
            if self._device is not None:
                self._device.destroy()
        except Exception:
            pass
        self._device = None
        self._queue = None
        self._pipeline = None
        self._bind_group = None
        self._uniform = None
        self._texture = None
        self._view = None
        self._size = None
        self._backend = None

    def _ensure(self, backend):
        if not HAS_WGPU:
            raise RuntimeError("wgpu 未安装")
        if self._device is not None and self._backend == backend:
            return
        adapter = self._adapter_map().get(backend)
        if adapter is None:
            raise RuntimeError(f"{backend} 适配器不可用")
        if wgpu is None:
            raise RuntimeError("wgpu 未安装")
        self._release()
        device = adapter.request_device_sync()
        shader = device.create_shader_module(code=self.SHADER)
        bind_group_layout = device.create_bind_group_layout(
            entries=[
                {
                    "binding": 0,
                    "visibility": wgpu.ShaderStage.FRAGMENT,
                    "buffer": {"type": wgpu.BufferBindingType.uniform},
                }
            ]
        )
        pipeline_layout = device.create_pipeline_layout(
            bind_group_layouts=[bind_group_layout]
        )
        pipeline = device.create_render_pipeline(
            layout=pipeline_layout,
            vertex={"module": shader, "entry_point": "vs"},
            fragment={
                "module": shader,
                "entry_point": "fs",
                "targets": [{"format": wgpu.TextureFormat.rgba8unorm}],
            },
            primitive={"topology": wgpu.PrimitiveTopology.triangle_list},
        )
        uniform = device.create_buffer(
            size=48,
            usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST,
        )
        bind_group = device.create_bind_group(
            layout=bind_group_layout,
            entries=[
                {
                    "binding": 0,
                    "resource": {"buffer": uniform, "offset": 0, "size": 48},
                }
            ],
        )
        self._device = device
        self._queue = device.queue
        self._pipeline = pipeline
        self._uniform = uniform
        self._bind_group = bind_group
        self._backend = backend

    def _ensure_texture(self, width, height):
        if self._texture is not None and self._size == (width, height):
            return
        self._texture = self._device.create_texture(
            size=(width, height, 1),
            format=wgpu.TextureFormat.rgba8unorm,
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
        )
        self._view = self._texture.create_view()
        self._size = (width, height)

    def render_frames(self, backend, width, height, color_left, color_right, frame_count):
        with self._lock:
            self._ensure(backend)
            self._ensure_texture(width, height)
            frames = []
            left = [c / 255.0 for c in color_left]
            right = [c / 255.0 for c in color_right]
            for i in range(max(frame_count, 1)):
                phase = 2.0 * math.pi * i / max(frame_count, 1)
                values = array(
                    "f",
                    [
                        left[0], left[1], left[2], 1.0,
                        right[0], right[1], right[2], 1.0,
                        phase, 0.0, 0.0, 0.0,
                    ],
                )
                self._queue.write_buffer(self._uniform, 0, values.tobytes())
                encoder = self._device.create_command_encoder()
                render_pass = encoder.begin_render_pass(
                    color_attachments=[
                        {
                            "view": self._view,
                            "clear_value": (0, 0, 0, 1),
                            "load_op": wgpu.LoadOp.clear,
                            "store_op": wgpu.StoreOp.store,
                        }
                    ]
                )
                render_pass.set_pipeline(self._pipeline)
                render_pass.set_bind_group(0, self._bind_group)
                render_pass.draw(3)
                render_pass.end()
                self._queue.submit([encoder.finish()])
                data = self._queue.read_texture(
                    {"texture": self._texture, "origin": (0, 0, 0)},
                    {"bytes_per_row": width * 4, "rows_per_image": height},
                    (width, height, 1),
                )
                image = Image.frombuffer(
                    "RGBA", (width, height), data, "raw", "RGBA", 0, 1
                ).convert("RGB")
                frames.append(image)
            return frames


class AnimatedGradientBanner(tk.Canvas):
    """
    动态渐变标题栏（PIL 预渲染版）
    - 启动时预生成 N 帧渐变图，之后只切换图片
    - CPU 占用极低
    """

    def __init__(self, parent, height=76,
                 color_left=(74, 158, 255),
                 color_right=(108, 92, 231),
                 title="⛏ Minecraft 启动器",
                 subtitle="",
                 speed=0.35,
                 fps=30,
                 frame_count=15,
                 render_backend="默认",
                 log_callback=None,
                 fallback_callback=None,
                 title_color="#ffffff",
                 subtitle_color="#dcdcff",
                 user_color="#ffffff",
                 **kwargs):
        super().__init__(parent, height=height, highlightthickness=0,
                         bg="#1e1e2e", **kwargs)

        self._fallback_callback = fallback_callback
        self.banner_height = height
        self.base_left = color_left
        self.base_right = color_right
        self.speed = speed
        self.interval = max(16, int(1000 / max(fps, 1)))
        self.frame_count = max(frame_count, 1)
        self.render_backend = render_backend
        self._log = log_callback or (lambda msg: None)
        self._gpu = LauncherGPURenderer() if HAS_WGPU else None
        self._gpu_token = 0
        self._gpu_frames = []
        self._resize_job = None
        self._active = True

        self._phase = 0.0
        self._last_width = -1

                      
        self.title_id = self.create_text(
            26, height / 2 - 11, anchor="w",
            text=title, fill=title_color,
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        self.subtitle_id = None
        if subtitle:
            self.subtitle_id = self.create_text(
                28, height / 2 + 17, anchor="w",
                text=subtitle, fill=subtitle_color,
                font=("Microsoft YaHei UI", 10),
            )
        self.user_text_id = self.create_text(
            0, height / 2, anchor="e",
            text="👤 离线模式",
            fill=user_color,
            font=("Microsoft YaHei UI", 11, "bold"),
        )

                                   
        self.bg_image_id = self.create_image(0, 0, anchor="nw")

                      
        self._frames = []
        self._frame_w = 0
        self._frame_idx = 0

        self.bind("<Configure>", self._on_resize)
        try:
            top = self.winfo_toplevel()
            top.bind("<FocusIn>", self._on_focus_in, add="+")
            top.bind("<FocusOut>", self._on_focus_out, add="+")
        except Exception:
            pass
        self.after(self.interval, self._tick)

                                                                  
             
                                                                  
    def set_user_text(self, text):
        self.itemconfigure(self.user_text_id, text=text)

    def set_title(self, title, subtitle=None):
        self.itemconfigure(self.title_id, text=title)
        if subtitle is not None and self.subtitle_id:
            self.itemconfigure(self.subtitle_id, text=subtitle)

    def set_colors(self, color_left, color_right):
        self.base_left = color_left
        self.base_right = color_right
        if self._last_width > 1:
            self._schedule_rebuild(self._last_width)

    def set_render_backend(self, mode):
        self.render_backend = mode if mode in ("默认", "OpenGL", "Vulkan", "DirectX 12") else "默认"
        self._gpu_token += 1
        self._gpu_frames = []
        if self._last_width < 2:
            return
        if self.render_backend == "默认" or self._gpu is None:
            self._build_frames(self._last_width)
            self._log("🖼️ 启动器画面使用默认 Tk 渲染")
            return
        self._spawn_gpu_frames(self._last_width, self.render_backend)

    def _on_focus_in(self, event=None):
        self._active = True

    def _on_focus_out(self, event=None):
        self._active = False

    def _schedule_rebuild(self, width):
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(60, lambda: self._rebuild_width(width))

    def _on_resize(self, event):
        w = event.width
        if w == self._last_width or w < 2:
            return
        self._last_width = w
        self.coords(self.user_text_id, w - 26, self.banner_height / 2)
        self._schedule_rebuild(w)

    def _rebuild_width(self, width):
        self._resize_job = None
        try:
            if not self.winfo_exists() or width < 2:
                return
        except tk.TclError:
            return
        if self.render_backend == "默认" or self._gpu is None or not self._frames:
            self._build_frames(width)
        if self.render_backend != "默认" and self._gpu is not None:
            self._spawn_gpu_frames(width, self.render_backend)

    def notify_gpu_failure(self, reason=""):
        if self.render_backend == "默认":
            return
        backend = self.render_backend
        self._gpu_token += 1
        self._gpu_failed(self._gpu_token, backend, reason or "渲染失败")

    def _spawn_gpu_frames(self, width, backend):
        token = self._gpu_token + 1
        self._gpu_token = token
        height = self.banner_height
        colors = (self.base_left, self.base_right)
        count = min(self.frame_count, 8)
        render_width = min(width, 1000)

        def work():
            try:
                frames = self._gpu.render_frames(
                    backend, render_width, height, colors[0], colors[1], count
                )
                if render_width != width:
                    frames = [
                        img.resize((width, height), Image.BILINEAR)
                        for img in frames
                    ]
            except Exception as exc:
                try:
                    self.after(0, lambda err=exc: self._gpu_failed(token, backend, err))
                except Exception:
                    pass
                return
            try:
                self.after(0, lambda: self._apply_gpu_frames(token, frames, backend))
            except Exception:
                pass

        threading.Thread(target=work, name="launcher-gpu-banner", daemon=True).start()

    def _apply_gpu_frames(self, token, frames, backend):
        if token != self._gpu_token:
            return
        try:
            photos = [ImageTk.PhotoImage(img) for img in frames]
        except Exception as exc:
            self._gpu_failed(token, backend, exc)
            return
        if not photos:
            self._gpu_failed(token, backend, RuntimeError("没有生成帧"))
            return
        self._gpu_frames = photos
        self._frames = photos
        self._frame_idx = 0
        self.itemconfig(self.bg_image_id, image=photos[0])
        self.tag_lower(self.bg_image_id)
        self._log(f"✅ 启动器画面使用 {backend} 渲染")

    def _gpu_failed(self, token, backend, exc):
        if token != self._gpu_token:
            return
        self.render_backend = "默认"
        self._gpu_token += 1
        self._gpu_frames = []
        detail = str(exc) if str(exc) else exc.__class__.__name__
        self._log(f"⚠️ {backend} 渲染不可用，已自动切换到默认渲染: {detail}")
        if self._last_width > 1:
            self._build_frames(self._last_width)
        if self._fallback_callback is not None:
            try:
                self._fallback_callback(backend, detail)
            except Exception:
                pass

    def _build_frames(self, width):
        """预渲染 frame_count 张渐变图，存为 PhotoImage 列表"""
        self._frames = []
        self._frame_w = width
        h = self.banner_height

                         
                                               
                                  
        for i in range(self.frame_count):
            phase = i * (360.0 / self.frame_count)

                          
            r1, g1, b1 = self._shift_hue(self.base_left, phase)
            r2, g2, b2 = self._shift_hue(self.base_right, phase * 1.3 + 40)

                                          
                                         
                                                   
            row = Image.new("RGB", (width, 1))
            rpx = row.load()
            for x in range(width):
                t = x / max(width - 1, 1)
                t_eased = t * t * (3 - 2 * t)
                r = int(r1 + (r2 - r1) * t_eased)
                g = int(g1 + (g2 - g1) * t_eased)
                b = int(b1 + (b2 - b1) * t_eased)
                rpx[x, 0] = (r, g, b)
            img = row.resize((width, h), Image.NEAREST)

            self._frames.append(ImageTk.PhotoImage(img))

                   
        self._frame_idx = 0
        self.itemconfig(self.bg_image_id, image=self._frames[0])
        self.tag_lower(self.bg_image_id)

                                                                  
                  
                                                                  
    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            active = self._active and self.winfo_toplevel().state() == "normal"
        except tk.TclError:
            return

        if active and self._frames:
            self._frame_idx = (self._frame_idx + 1) % len(self._frames)
            self.itemconfig(self.bg_image_id, image=self._frames[self._frame_idx])

        self.after(self.interval, self._tick)


    @staticmethod
    def _shift_hue(rgb, degree):
        r, g, b = [c / 255.0 for c in rgb]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        h = (h + degree / 360.0) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return int(r * 255), int(g * 255), int(b * 255)
                                                     
try:
    import socket
    import urllib3.util.connection as urllib3_cn

    def _allowed_gai_family():
        return socket.AF_INET

    urllib3_cn.allowed_gai_family = _allowed_gai_family
except Exception:
    pass
def enable_rounded_window(window, radius=16):
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        from ctypes import wintypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())

                                             
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2

        preference = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference)
        )
    except Exception:
        pass

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    if platform.system() == 'Windows':
        import sys
        import io
    
        if sys.stdout is not None:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if sys.stderr is not None:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

NEWS_URL = "https://launchercontent.mojang.com/news.json"
                                                               
PCL2_ACCESS_TOKEN = "eyJraFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFqRPLQ"

                                                          
class ClientIDManager:
    DEFAULT_CLIENT_ID = "f6a6eaca-1bc1-4273-ba4b-a1677efd383b"
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback or print
        self.config_file = Path.home() / ".minecraft_launcher_config.json"
        self.client_id = None
        self.load_client_id()
    
    def log(self, msg, level=None):
        if self.log_callback:
            self.log_callback(msg)
    
    def load_client_id(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    if config.get('client_id'):
                        self.client_id = config['client_id']
                        self.log(f"✅ 从配置文件加载 Client ID: {self.client_id[:8]}...")
                        return
            except:
                pass
        
        env_id = os.environ.get('MINECRAFT_CLIENT_ID')
        if env_id:
            self.client_id = env_id
            self.log(f"✅ 从环境变量加载 Client ID: {self.client_id[:8]}...")
            self.save_client_id(self.client_id)
            return
        
        self.client_id = self.DEFAULT_CLIENT_ID
        self.log(f"✅ 使用默认 Client ID: {self.client_id[:8]}...")
        self.save_client_id(self.client_id)
    
    def save_client_id(self, client_id):
        try:
            config = {}
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            config['client_id'] = client_id
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self.log(f"💾 Client ID 已保存到配置文件")
        except Exception as e:
            self.log(f"⚠️ 保存失败: {e}")
    
    def get_client_id(self):
        return self.client_id
    
    def manual_set(self, client_id):
        if client_id and client_id.strip():
            self.client_id = client_id.strip()
            self.save_client_id(self.client_id)
            self.log(f"✅ Client ID 已更新: {self.client_id[:8]}...")
            return True
        return False

                                                           
class MinecraftNewsManager:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback or print
        self.news_cache = []
        self.last_update = None
        self.translations = self._load_translations()

    def log(self, msg, level=None):
        if self.log_callback:
            self.log_callback(msg)

    def _load_translations(self):
        return {
            'Minecraft': 'Minecraft', 'Java': 'Java版', 'Bedrock': '基岩版',
            'Dungeons': '地下城', 'Update': '更新', 'Release': '发布',
            'Snapshot': '快照', 'Preview': '预览版', 'Beta': '测试版',
            'Launcher': '启动器', 'coming soon': '即将推出', 'available now': '现已推出',
            'patch notes': '更新日志', 'bug fixes': '修复漏洞', 'performance': '性能优化',
            'new features': '新特性', 'community': '社区', 'event': '活动',
            'Live': '直播', 'Minecraft Live': 'Minecraft 年度盛会',
            'Trails & Tales': '足迹与故事', 'Caves & Cliffs': '洞穴与山崖',
            'The Wild Update': '荒野更新', 'Nether Update': '下界更新',
            'Village & Pillage': '村庄与掠夺', 'Ocean Update': '水域更新',
            'Better Together': '基岩版跨平台', 'New': '全新', 'Announcement': '公告',
            'Experience': '体验', 'Adventure': '冒险', 'Creativity': '创造力',
        }

    def _translate_title(self, title):
        translated = title
        for en, zh in self.translations.items():
            if en in translated:
                translated = translated.replace(en, zh)
        if not any('\u4e00' <= c <= '\u9fff' for c in translated):
            if 'Snapshot' in title or 'snapshot' in title:
                translated = f"🔨 {translated}"
            elif 'Update' in title or 'update' in title:
                translated = f"📦 {translated}"
            elif 'Release' in title or 'release' in title:
                translated = f"✅ {translated}"
            elif 'Preview' in title or 'Beta' in title:
                translated = f"🧪 {translated}"
            elif 'Live' in title:
                translated = f"🎥 {translated}"
            else:
                translated = f"📰 {translated}"
        return translated

                                                     
    def fetch_news(self):
        """获取新闻，失败时回退到备用新闻"""
        try:
            news = self.fetch_news_from_launcher()
            if news:
                self.log(f"✅ 获取新闻成功: 共 {len(news)} 条")
                return news
            else:
                self.log("⚠️ 官方新闻为空，使用备用新闻")
        except Exception as e:
            self.log(f"⚠️ 获取官方新闻失败: {e}")
            self.log("⚠️ 使用备用新闻")
        return self._get_fallback_news()

                                                     
    def fetch_news_from_launcher(self):
        """从 launchercontent.mojang.com 拉取官方新闻（只取前 30 条）"""
        r = requests.get(NEWS_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()

        entries = data.get("entries", [])
        if not entries:
            raise Exception("news.json 里没有 entries")

        all_news = []
        for entry in entries[:30]:           
                        
            title = entry.get("title", "") or entry.get("name", "")

                                              
            desc = (entry.get("text", "")
                    or entry.get("description", "")
                    or entry.get("body", ""))

                                                    
            date_raw = (entry.get("date", "")
                        or entry.get("published", "")
                        or entry.get("lastModified", ""))
            date_str = str(date_raw)[:10]

                                                             
            image_url = ""
            img_obj = entry.get("newsPageImage") or entry.get("image") or {}
            if isinstance(img_obj, dict):
                image_url = img_obj.get("url", "")
            if not image_url:
                image_url = entry.get("imageURL", "") or entry.get("imageUrl", "")

                                              
            link = (entry.get("readMoreLink", "")
                    or entry.get("link", "")
                    or entry.get("url", ""))

            all_news.append({
                "title": self._translate_title(title),
                "title_en": title,
                "description": desc,
                "date": date_str,
                "category": self._categorize_news(title),
                "link": link,
                "image_url": image_url,
                "is_translated": self._translate_title(title) != title,
            })

        return all_news

                                                  
    def _categorize_news(self, title):
        title_lower = title.lower()
        if 'dungeon' in title_lower or '地下城' in title_lower:
            return 'Minecraft Dungeons'
        elif 'bedrock' in title_lower or '基岩' in title_lower or 'be ' in title_lower or 'bE ' in title_lower:
            return 'Minecraft Bedrock'
        elif 'java' in title_lower or 'je' in title_lower or 'java edition' in title_lower:
            return 'Minecraft Java'
        elif 'launcher' in title_lower or '启动器' in title_lower:
            return 'Minecraft Launcher'
        else:
            return 'Minecraft'

    def _get_fallback_news(self):
        return [
            {'title': '🎮 Minecraft 1.21 正式版已发布！', 'title_en': 'Minecraft 1.21 Release', 'link': '', 'description': '全新试炼密室、狼铠、犰狳等新内容现已上线！', 'date': '2024-06-13', 'category': 'Minecraft Java', 'is_translated': True, 'image_url': ''},
            {'title': '📦 Minecraft 1.21.1 更新发布', 'title_en': 'Minecraft 1.21.1 Update', 'link': '', 'description': '修复了多个崩溃问题和性能优化，提升游戏稳定性', 'date': '2024-08-01', 'category': 'Minecraft Java', 'is_translated': True, 'image_url': ''},
            {'title': '🔵 Minecraft Bedrock 1.21 已上线', 'title_en': 'Minecraft Bedrock 1.21', 'link': '', 'description': '跨平台同步更新，支持新特性，与 Java 版内容对齐', 'date': '2024-06-13', 'category': 'Minecraft Bedrock', 'is_translated': True, 'image_url': ''},
            {'title': '⚔️ Minecraft Dungeons 新 DLC 预告', 'title_en': 'Dungeons New DLC', 'link': '', 'description': '全新冒险内容即将到来，更多挑战和装备等你探索', 'date': '2024-07-15', 'category': 'Minecraft Dungeons', 'is_translated': True, 'image_url': ''},
            {'title': '📰 Minecraft 2024 年度总结', 'title_en': 'Minecraft 2024 Review', 'link': '', 'description': '回顾过去一年的重大更新和社区活动，展望未来', 'date': '2024-12-20', 'category': 'Minecraft', 'is_translated': True, 'image_url': ''},
        ]
                                                                
class AuthlibInjectorManager:
    def __init__(self, base_dir: Path, log_callback=None):
        self.base_dir = base_dir
        self.log_callback = log_callback or print
        self.authlib_dir = base_dir / "authlib"
        self.authlib_dir.mkdir(exist_ok=True)
        self.authlib_jar = self.authlib_dir / "authlib-injector.jar"
    
    def log(self, msg, level=None):
        if self.log_callback:
            self.log_callback(msg)
    
    def download_authlib(self) -> bool:
        if self.authlib_jar.exists() and self.authlib_jar.stat().st_size > 1024:
            self.log("✅ Authlib-Injector 已存在")
            return True
        self.log("📥 下载 Authlib-Injector...")
        urls = [
            "https://authlib-injector.yushi.moe/artifact/authlib-injector-latest.jar",
            "https://mirror.ghproxy.com/https://github.com/yushi-moe/authlib-injector/releases/download/latest/authlib-injector.jar",
        ]
        for url in urls:
            try:
                response = requests.get(url, stream=True, timeout=30)
                response.raise_for_status()
                with open(self.authlib_jar, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                self.log(f"✅ Authlib-Injector 下载成功")
                return True
            except Exception as e:
                self.log(f"⚠️ 下载失败: {e}")
                continue
        self.log("❌ 所有下载源均失败")
        return False
    
    def get_authlib_args(self, auth_server_url: str) -> list:
        if not self.authlib_jar.exists():
            self.log("❌ Authlib-Injector 文件不存在")
            return []
        return [f"-javaagent:{self.authlib_jar}={auth_server_url}"]

                                                                                 
# === Fabric 下载辅助（镜像回退 + 重试 + UA）===
_FABRIC_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MinecraftLauncher/1.0"}
_FABRIC_META_HOSTS = [
    "https://bmclapi2.bangbang93.com/fabric-meta",
    "https://meta.fabricmc.net",
    "https://fabricmc.net",
]


def _fabric_swap_host(url: str, new_base: str) -> str:
    """把 url 的协议+主机部分替换成 new_base，保留 path。"""
    m = re.match(r"^https?://[^/]+(.*)$", url)
    path = m.group(1) if m else url
    return new_base.rstrip("/") + path


def _fabric_get_json(url: str, timeout=15, retries_per_host=2):
    """依次尝试多个 Fabric 镜像，每个镜像重试 retries_per_host 次；全部失败抛最后一次异常。"""
    last_err = None
    for host in _FABRIC_META_HOSTS:
        u = _fabric_swap_host(url, host)
        for attempt in range(retries_per_host):
            try:
                r = requests.get(u, timeout=timeout, headers=_FABRIC_UA)
                r.raise_for_status()
                return r.json(), u
            except Exception as e:
                last_err = e
                time.sleep(0.6 * (attempt + 1))
    raise last_err if last_err else RuntimeError("fabric meta all hosts failed")


def _fabric_download(url: str, dest_path: Path, timeout=(10, 60), retries_per_host=2):
    """下载文件，先原地址再 BMCLAPI maven 镜像；流式写入。成功返回实际使用的 url。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [url]
    if "maven.fabricmc.net" in url:
        candidates.append(_fabric_swap_host(url, "https://bmclapi2.bangbang93.com/maven"))
    last_err = None
    for u in candidates:
        for attempt in range(retries_per_host):
            try:
                with requests.get(u, stream=True, timeout=timeout, headers=_FABRIC_UA) as r:
                    r.raise_for_status()
                    tmp = dest_path.with_suffix(dest_path.suffix + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                    os.replace(tmp, dest_path)
                    return u
            except Exception as e:
                last_err = e
                time.sleep(0.8 * (attempt + 1))
    raise last_err if last_err else RuntimeError("fabric file download all failed")

class ModLoaderManager:
    def __init__(self, base_dir: Path, log_callback=None):
        self.base_dir = base_dir
        self.log_callback = log_callback or print
        self.version_dir = base_dir / "versions"
        self.libraries_dir = base_dir / "libraries"
        self.assets_dir = base_dir / "assets"

    def log(self, msg, level=None):
        if self.log_callback:
            self.log_callback(msg)
    
                                                                  
                                    
                                                                  
    def install_fabric(self, mc_version: str, loader_version: str = "latest") -> bool:
        self.log(f"📥 安装 Fabric {mc_version}...")
        try:
            check_url = f"https://meta.fabricmc.net/v2/versions/game"
            games, _used = _fabric_get_json(check_url, timeout=15)
            self.log(f"METADATA_HOST: {_used}")
            valid_versions = [g['version'] for g in games]
            if mc_version not in valid_versions:
                self.log(f"❌ 版本 {mc_version} 不存在")
                return False
            if loader_version == "latest":
                loader_url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}"
                loaders, _used2 = _fabric_get_json(loader_url, timeout=15)
                if loaders:
                    loader_version = loaders[0]['loader']['version']
            version_name = f"{mc_version}-Fabric-{loader_version}"
            vdir = self.version_dir / version_name
            vdir.mkdir(parents=True, exist_ok=True)

            json_url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/profile/json"
            profile, _used3 = _fabric_get_json(json_url, timeout=15)
            with open(vdir / f"{version_name}.json", 'w', encoding='utf-8') as f:
                json.dump(profile, f, indent=2)
            self.log(f"✅ Fabric profile JSON 已下载")

            parent_version = profile.get('inheritsFrom', mc_version)
            parent_jar = self.version_dir / parent_version / f"{parent_version}.jar"
            target_jar = vdir / f"{version_name}.jar"

            if parent_jar.exists():
                shutil.copy2(parent_jar, target_jar)
                self.log(f"✅ 已复制原版 JAR: {parent_version}.jar → {version_name}.jar")
            else:
                self.log(f"⚠️ 原版 JAR 不存在: {parent_jar}")
                self.log(f"💡 请先下载原版 {parent_version}，然后重新安装 Fabric")
                return False

            self.log("📥 正在下载 Fabric Loader 库...")
            libraries = profile.get('libraries', [])
            for lib in libraries:
                name = lib.get('name', '')
                if not name:
                    continue
                parts = name.split(':')
                if len(parts) < 3:
                    continue
                group = parts[0].replace('.', '/')
                artifact = parts[1]
                version_id = parts[2]
                base_url = lib.get('url', 'https://maven.fabricmc.net/')
                if not base_url.endswith('/'):
                    base_url += '/'
                jar_url = f"{base_url}{group}/{artifact}/{version_id}/{artifact}-{version_id}.jar"
                jar_path = self.libraries_dir / group / artifact / version_id / f"{artifact}-{version_id}.jar"
                if jar_path.exists():
                    continue
                self.log(f"DOWNLOAD: {artifact}-{version_id}.jar")
                try:
                    _fabric_download(jar_url, jar_path)
                except Exception as e:
                    self.log(f"DOWNLOAD_FAIL: {jar_url} - {e}")
                    return False
            self.log(f"✅ Fabric Loader 库下载完成")
            mods_dir = self.base_dir / "mods"
            mods_dir.mkdir(exist_ok=True)
            self.log(f"📂 已确保 mods 文件夹存在: {mods_dir}")
            self.log(f"✅ Fabric {version_name} 安装成功!")
            return True
        except Exception as e:
            self.log(f"❌ Fabric 安装失败: {e}")
            return False

                                                                  
                                   
                                                                  
    def _get_forge_versions(self, mc_version: str):
        """从 Forge Maven 获取指定 MC 版本的所有 Forge 版本"""
        url = "https://files.minecraftforge.net/net/minecraftforge/forge/maven-metadata.json"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        versions = data.get(mc_version, [])
        return versions

    def install_forge(self, mc_version: str, forge_version: str = "latest") -> bool:
        self.log(f"📥 安装 Forge {mc_version}...")
        try:
                               
            all_versions = self._get_forge_versions(mc_version)
            if not all_versions:
                self.log(f"❌ 未找到 {mc_version} 对应的 Forge 版本")
                return False

                                                               
                                                                              
                                                          

            if forge_version == "latest":
                full_version = all_versions[-1]                  
                self.log(f"📌 自动选择最新 Forge: {full_version}")
            else:
                                                       
                if forge_version.startswith(f"{mc_version}-"):
                    full_version = forge_version
                else:
                    full_version = f"{mc_version}-{forge_version}"

                if full_version not in all_versions:
                    self.log(f"❌ Forge 版本 {full_version} 不存在")
                    self.log(f"💡 可用版本示例: {', '.join(all_versions[-5:])}")
                    return False

                                                                       
                                                                       
                                              
            forge_only_ver = full_version
            if full_version.startswith(f"{mc_version}-"):
                forge_only_ver = full_version[len(mc_version) + 1:]
            version_name = f"{mc_version}-forge-{forge_only_ver}"

                             
                                           
            installer_url = (
                f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
                f"{full_version}/forge-{full_version}-installer.jar"
            )
            installer_path = self.base_dir / "forge_installer" / f"forge-{full_version}-installer.jar"
            installer_path.parent.mkdir(parents=True, exist_ok=True)

            self.log(f"📥 下载 Forge Installer: {full_version}")
            self.log(f"    URL: {installer_url}")
            self._download_file(installer_url, installer_path)

                            
            parent_jar = self.version_dir / mc_version / f"{mc_version}.jar"
            if not parent_jar.exists():
                self.log(f"❌ 原版 {mc_version} 未安装，请先下载原版")
                return False

                                               
            java_path = self._find_java()
            self.log(f"🔧 运行 Forge Installer (Java: {java_path})...")
            # Forge 1.8.9 及更老版本不支持 --installClient 参数
            # 客户端安装只能通过图形界面手动完成
            text_ver = (mc_version or "").strip()
            _m = re.match(r"^1\.(\d+)", text_ver)
            _minor = int(_m.group(1)) if _m else 99
            if _minor <= 12:
                cmd = [java_path, "-jar", str(installer_path)]
                self.log("⚠️ Forge 1.8.9 需要图形界面手动安装")
                self.log("💡 请在弹出的 installer 窗口中选择「Install client」，路径保持默认，点击 OK")
            else:
                cmd = [
                    java_path, "-jar", str(installer_path),
                    "--installClient", str(self.base_dir)
                ]

            _is_old_installer = (_minor <= 12)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=0 if _is_old_installer else (
                    subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
                ),
                encoding='utf-8',
                errors='replace'
            )
            if not _is_old_installer:
                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        if line.strip():
                            self.log(f"  [Forge] {line.strip()}")
                if proc.stderr:
                    for line in proc.stderr.splitlines():
                        if line.strip():
                            self.log(f"  [Forge-ERR] {line.strip()}", level='warning')
            if _is_old_installer:
                self.log(f"✅ installer 已退出（返回码 {proc.returncode}），正在检查版本目录...")
            elif proc.returncode != 0:
                self.log(f"❌ Forge Installer 执行失败，返回码 {proc.returncode}")
                return False

                       
            expected_dir = self.version_dir / version_name
            if not expected_dir.exists():
                                                   
                candidates = [
                    d for d in self.version_dir.iterdir()
                    if d.is_dir()
                    and 'forge' in d.name.lower()
                    and forge_only_ver in d.name
                ]
                if candidates:
                    version_name = candidates[0].name
                    expected_dir = candidates[0]
                    self.log(f"✅ 检测到 Forge 版本目录: {version_name}")
                else:
                    self.log(f"❌ 未找到 Forge 版本目录（预期: {version_name}）")
                                            
                    existing = [d.name for d in self.version_dir.iterdir() if d.is_dir()]
                    self.log(f"💡 当前 versions 目录: {existing}")
                    return False
            else:
                self.log(f"✅ 已生成 Forge 版本目录: {version_name}")

                                                   
            target_jar = expected_dir / f"{version_name}.jar"
            if not target_jar.exists() and parent_jar.exists():
                try:
                    shutil.copy2(parent_jar, target_jar)
                    self.log(f"✅ 已复制原版 JAR 作为 Forge JAR")
                except Exception as e:
                    self.log(f"⚠️ 复制 JAR 失败: {e}")

                             
            try:
                installer_path.unlink()
            except:
                pass

            self.log(f"✅ Forge {version_name} 安装成功!")
            return True
        except Exception as e:
            self.log(f"❌ Forge 安装失败: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False

                                                                  
                                      
                                                                  
    def _get_neoforge_versions(self):
        """从 NeoForged Maven 获取所有 NeoForge 版本"""
        url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
                  
        import re as _re
        versions = _re.findall(r'<version>([^<]+)</version>', r.text)
        return versions

    def install_neoforge(self, mc_version: str, neoforge_version: str = "latest") -> bool:
        self.log(f"📥 安装 NeoForge (MC {mc_version})...")
        try:
                                                           
            parts = mc_version.split('.')
            if len(parts) < 2:
                self.log(f"❌ 无效的 MC 版本: {mc_version}")
                return False

                                              
            if len(parts) >= 3:
                prefix = f"{parts[1]}.{parts[2]}"
            else:
                prefix = f"{parts[1]}.0"

            all_versions = self._get_neoforge_versions()
                                        
            matched = [v for v in all_versions if v.startswith(prefix + ".")]

            if not matched:
                self.log(f"❌ 未找到 MC {mc_version} 对应的 NeoForge 版本 (前缀 {prefix})")
                self.log(f"💡 可用版本示例: {', '.join(all_versions[-5:])}")
                return False

            if neoforge_version == "latest":
                neoforge_version = matched[-1]
                self.log(f"📌 自动选择最新 NeoForge: {neoforge_version}")
            else:
                if neoforge_version not in all_versions:
                    self.log(f"❌ NeoForge 版本 {neoforge_version} 不存在")
                    return False

            version_name = f"{mc_version}-neoforge-{neoforge_version}"

                          
            installer_url = (
                f"https://maven.neoforged.net/releases/net/neoforged/neoforge/"
                f"{neoforge_version}/neoforge-{neoforge_version}-installer.jar"
            )
            installer_path = self.base_dir / "forge_installer" / f"neoforge-{neoforge_version}-installer.jar"
            installer_path.parent.mkdir(parents=True, exist_ok=True)

            self.log(f"📥 下载 NeoForge Installer: {neoforge_version}")
            self._download_file(installer_url, installer_path)

            parent_jar = self.version_dir / mc_version / f"{mc_version}.jar"
            if not parent_jar.exists():
                self.log(f"❌ 原版 {mc_version} 未安装，请先下载原版")
                return False

            java_path = self._find_java()
            self.log(f"🔧 运行 NeoForge Installer...")
            cmd = [
                java_path, "-jar", str(installer_path),
                "--installClient", str(self.base_dir)
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0,
                encoding='utf-8',
                errors='replace'
            )
            if proc.stdout:
                for line in proc.stdout.splitlines():
                    if line.strip():
                        self.log(f"  [NeoForge] {line.strip()}")
            if proc.returncode != 0:
                self.log(f"❌ NeoForge Installer 执行失败，返回码 {proc.returncode}")
                if proc.stderr:
                    self.log(f"  [stderr] {proc.stderr[:500]}")
                return False

                
            expected_dir = self.version_dir / version_name
            if not expected_dir.exists():
                candidates = [
                    d for d in self.version_dir.iterdir()
                    if d.is_dir() and 'neoforge' in d.name.lower() and mc_version in d.name
                ]
                if candidates:
                    version_name = candidates[0].name
                    expected_dir = candidates[0]
                    self.log(f"✅ 检测到 NeoForge 版本目录: {version_name}")
                else:
                    self.log(f"❌ 未找到 NeoForge 版本目录")
                    return False

            target_jar = expected_dir / f"{version_name}.jar"
            if not target_jar.exists() and parent_jar.exists():
                try:
                    shutil.copy2(parent_jar, target_jar)
                    self.log(f"✅ 已复制原版 JAR 作为 NeoForge JAR")
                except Exception as e:
                    self.log(f"⚠️ 复制 JAR 失败: {e}")

            try:
                installer_path.unlink()
            except:
                pass

            self.log(f"✅ NeoForge {version_name} 安装成功!")
            return True
        except Exception as e:
            self.log(f"❌ NeoForge 安装失败: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False

                                                                  
                                  
                                                                  
    def _download_file(self, url: str, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def _find_java(self):
        """查找一个可用的 java 可执行文件（按优先级）"""
        candidates = []

                                       
        runtime_paths = [
            self.base_dir / "runtime" / "java-runtime-epsilon" / "bin" / "java.exe",
            self.base_dir / "runtime" / "java-runtime-delta" / "bin" / "java.exe",
            self.base_dir / "runtime" / "java-runtime-gamma" / "bin" / "java.exe",
            self.base_dir / "runtime" / "java-runtime-beta" / "bin" / "java.exe",
            self.base_dir / "runtime" / "java-runtime-alpha" / "bin" / "java.exe",
            self.base_dir / "runtime" / "jre-legacy" / "bin" / "java.exe",
        ]
        for p in runtime_paths:
            if p.exists():
                candidates.append(str(p))

                             
        if self.version_dir.exists():
            for vdir in self.version_dir.iterdir():
                if vdir.is_dir():
                    for runtime in vdir.glob("runtime/*/bin/java.exe"):
                        if runtime.exists():
                            candidates.append(str(runtime))

                               
        if platform.system() == 'Windows':
            appdata = Path(os.environ.get('APPDATA', ''))
            localappdata = Path(os.environ.get('LOCALAPPDATA', ''))
            default_runtime_dirs = [
                appdata / ".minecraft" / "runtime",
                localappdata / "Packages" / "Microsoft.4297127D64EC6_8wekyb3d8bbwe"
                / "LocalCache" / "Roaming" / ".minecraft" / "runtime",
            ]
            for rt_root in default_runtime_dirs:
                if rt_root.exists():
                    for runtime in rt_root.glob("*/bin/java.exe"):
                        if runtime.exists():
                            candidates.append(str(runtime))

                            
        if platform.system() == 'Windows':
            for root in [Path("C:/Program Files/Java"), Path("C:/Program Files/Eclipse Adoptium"),
                         Path("C:/Program Files/Microsoft")]:
                if root.exists():
                    for java_exe in root.rglob("bin/java.exe"):
                        candidates.append(str(java_exe))

                                                  
        which_java = shutil.which("java")
        if which_java:
            candidates.append(which_java)

        if not candidates:
            return "java"                          

                                                 
        for c in candidates:
            if "java-runtime-epsilon" in c:
                return c
        for c in candidates:
            if "java-runtime-delta" in c:
                return c
                   
        return candidates[0]

                                                     
class _MSAuthCallbackHandler(BaseHTTPRequestHandler):
    """接收微软重定向，把 code / error 存到类变量"""
    auth_code = None
    error = None
    done_event = None          

    def do_GET(self):
        parsed = urlparse(self.path)

                           
        if parsed.path not in ("/", ""):
            self.send_response(204)
            self.end_headers()
            return

        params = parse_qs(parsed.query)

        if "code" in params:
            _MSAuthCallbackHandler.auth_code = params["code"][0]
            self._respond(200, "✅ 登录成功，请关闭此窗口")
            if _MSAuthCallbackHandler.done_event:
                _MSAuthCallbackHandler.done_event.set()
        elif "error" in params:
            _MSAuthCallbackHandler.error = params.get(
                "error_description", ["未知错误"]
            )[0]
            self._respond(400, f"❌ 登录失败：{_MSAuthCallbackHandler.error}")
            if _MSAuthCallbackHandler.done_event:
                _MSAuthCallbackHandler.done_event.set()
        else:
            self._respond(400, "🤔 未收到 code")

    def _respond(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"<html><head><meta charset='utf-8'></head>"
            f"<body style='font-family:Microsoft YaHei;text-align:center;"
            f"padding-top:100px'><h2>{msg}</h2></body></html>".encode("utf-8")
        )

    def log_message(self, *args):
        pass       
                                                 
class MinecraftAuth:
    CLIENT_ID = None
    
    def __init__(self, client_id, log_callback=None):
        self.log_callback = log_callback or print
        self.CLIENT_ID = client_id
        self.access_token = None
        self.refresh_token = None
        self.xbox_token = None
        self.user_hash = None
        self.minecraft_access_token = None
        self.uuid = None
        self.username = None
        self.ui_root = None
        self.profile = None
                    
        self.auth_file = Path.home() / ".minecraft_launcher_config.json"
        self.current_account = None
        self.load_auth()
    
    def log(self, msg, level=None):
        if self.log_callback:
            self.log_callback(msg)
    
    def load_auth(self):
        if not self.auth_file.exists():
            return False
        try:
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            current_name = config.get('current_account')
            accounts = config.get('accounts') or []
            selected = None
            if current_name and accounts:
                for a in accounts:
                    if a.get('username') == current_name:
                        selected = a
                        break
            if selected is None and accounts:
                selected = accounts[0]
            if selected is None:
                selected = config.get('auth') or {}
            if not selected:
                return False
            self.current_account = selected.get('username')
            self.access_token = selected.get('access_token')
            self.refresh_token = selected.get('refresh_token')
            self.uuid = selected.get('uuid')
            self.username = selected.get('username')
            self.minecraft_access_token = selected.get('minecraft_access_token')
            self.profile = selected.get('profile')
            self.expires_at = selected.get('expires_at', 0) or 0
            if self.minecraft_access_token:
                self.log(f"已加载认证: {self.username}")
                # 后台刷新，避免卡启动窗口
                def _bg_refresh():
                    try:
                        now_ts = time.time()
                        exp = getattr(self, 'expires_at', 0) or 0
                        if not self.refresh_token:
                            return
                        if exp and now_ts < exp - 3600:
                            return
                        self.log("后台: token 临近/已过期，静默刷新")
                        if self.refresh_auth():
                            self.log("后台: 静默刷新成功")
                        else:
                            self.log("后台: 静默刷新失败")
                    except Exception as _e:
                        self.log(f"后台刷新异常: {_e}")
                try:
                    import threading as _th
                    _th.Thread(target=_bg_refresh, daemon=True).start()
                except Exception:
                    pass
                return True
        except:
            pass
        return False
    
    def save_auth(self):
        config = {}
        if self.auth_file.exists():
            try:
                with open(self.auth_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except:
                config = {}

        account_data = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'uuid': self.uuid,
            'username': self.username,
            'minecraft_access_token': self.minecraft_access_token,
            'profile': self.profile,
            'expires_at': getattr(self, 'expires_at', 0) or 0,
        }
        accounts = config.get('accounts') or []
        accounts = [a for a in accounts if a.get('username') != self.username]
        accounts.append(account_data)
        config['accounts'] = accounts
        config['current_account'] = self.username
        config['auth'] = account_data
        try:
            _tmp = str(self.auth_file) + '.tmp'
            with open(_tmp, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            os.replace(_tmp, str(self.auth_file))
        except Exception:
            with open(self.auth_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
    
    def clear_auth(self):
        self.access_token = None
        self.refresh_token = None
        self.xbox_token = None
        self.user_hash = None
        self.minecraft_access_token = None
        self.uuid = None
        self.username = None
        self.profile = None

        if self.auth_file.exists():
            try:
                with open(self.auth_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                config.pop('auth', None)
                with open(self.auth_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except:
                pass
        self.log("🗑️ 已清除认证")

    def list_accounts(self):
        if not self.auth_file.exists():
            return []
        try:
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            accounts = config.get('accounts') or []
            if not accounts and config.get('auth'):
                accounts = [config.get('auth')]
            return [a.get('username') for a in accounts if a.get('username')]
        except:
            return []

    def switch_account(self, username):
        if not username:
            return False
        if not self.auth_file.exists():
            return False
        try:
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            accounts = config.get('accounts') or []
            if not accounts and config.get('auth'):
                accounts = [config.get('auth')]
            selected = None
            for a in accounts:
                if a.get('username') == username:
                    selected = a
                    break
            if not selected:
                return False
            self.current_account = username
            self.access_token = selected.get('access_token')
            self.refresh_token = selected.get('refresh_token')
            self.xbox_token = None
            self.user_hash = None
            self.minecraft_access_token = selected.get('minecraft_access_token')
            self.uuid = selected.get('uuid')
            self.username = selected.get('username')
            self.profile = selected.get('profile')
            config['current_account'] = username
            config['auth'] = selected
            with open(self.auth_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self.log(f"已切换账号: {username}")
            return self.is_authenticated()
        except Exception as e:
            self.log(f"切换账号失败: {e}")
            return False

    def remove_account(self, username):
        if not self.auth_file.exists():
            return False
        try:
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            accounts = config.get('accounts') or []
            accounts = [a for a in accounts if a.get('username') != username]
            config['accounts'] = accounts
            if config.get('current_account') == username:
                config['current_account'] = accounts[-1].get('username') if accounts else None
                if accounts:
                    last = accounts[-1]
                    config['auth'] = last
                    self.switch_account(last.get('username'))
                else:
                    config.pop('auth', None)
                    self.access_token = None
                    self.refresh_token = None
                    self.xbox_token = None
                    self.user_hash = None
                    self.minecraft_access_token = None
                    self.uuid = None
                    self.username = None
                    self.profile = None
                    self.current_account = None
            with open(self.auth_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self.log(f"已删除账号: {username}")
            return True
        except Exception as e:
            self.log(f"删除账号失败: {e}")
            return False
    
    def is_authenticated(self):
        return self.minecraft_access_token is not None
    
    def get_game_args(self):
        if not self.is_authenticated():
            return None
        return {
            '--username': self.username,
            '--uuid': self.uuid,
            '--accessToken': self.minecraft_access_token,
            '--userType': 'mojang'
        }
    def _get_microsoft_token_device_code(self):
        try:
            import msal

            AUTHORITY = "https://login.microsoftonline.com/consumers"
            SCOPES = ["XboxLive.signin"]

            self.log("📱 正在请求设备代码...")
            app = msal.PublicClientApplication(self.CLIENT_ID, authority=AUTHORITY)

            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                self.log(f"❌ 获取设备码失败: {flow.get('error_description', '未知错误')}")
                return False

            user_code = flow["user_code"]
            verification_uri = flow.get("verification_uri", "https://microsoft.com/devicelogin")

            self.log(f"🔗 请打开: {verification_uri}")
            self.log(f"📝 输入设备码: {user_code}")

            webbrowser.open(verification_uri)

            if self.ui_root is not None:
                def show_code_dialog(uc=user_code, vu=verification_uri):
                    try:
                        self.ui_root.clipboard_clear()
                        self.ui_root.clipboard_append(uc)
                        self.ui_root.update()
                        messagebox.showinfo(
                            "微软登录 - 设备码",
                            f"1. 在浏览器中打开：\n{vu}\n\n"
                            f"2. 输入设备码：\n\n{uc}\n\n"
                            f"✅ 设备码已复制到剪贴板"
                        )
                    except Exception as e:
                        print(f"弹窗失败: {e}")

                try:
                    self.ui_root.after(0, show_code_dialog)
                except Exception:
                    pass

            self.log("⏳ 等待授权...")
            result = app.acquire_token_by_device_flow(flow)

            if "access_token" not in result:
                self.log(f"❌ 设备代码流失败: {result.get('error_description', '未知错误')}")
                return False

            self.access_token = result["access_token"]
            self.refresh_token = result.get("refresh_token")
            self.log("✅ 获取 Microsoft 令牌成功（设备代码流）")
            return True
        except Exception as e:
            self.log(f"❌ 设备代码流异常: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False
    def authenticate_microsoft(self, method="auth_code"):
        self.log(f"🔑 开始 Microsoft 正版登录（{method}）...")
        self.log(f"📌 使用 Client ID: {self.CLIENT_ID[:8]}...")

        if method == "device_code":
            if not self._get_microsoft_token_device_code():
                return False
        else:
            if not self._get_microsoft_token_auth_code():
                return False

        self.log("🎮 获取 Xbox Live 令牌...")
        if not self._get_xbox_token():
            return False
        self.log("🔐 获取 XSTS 令牌...")
        if not self._get_xsts_token():
            return False
        self.log("⛏️ 获取 Minecraft 访问令牌...")
        if not self._get_minecraft_token():
            return False
        self.log("👤 获取玩家资料...")
        if not self._get_profile():
            return False
        self.save_auth()
        self.log(f"✅ 登录成功! 玩家: {self.username}")
        return True
    
    def _get_microsoft_token_auth_code(self):
        """
        用 pywebview 内嵌浏览器 + MSAL 授权码流完成 Microsoft 登录。
        重定向到 http://localhost:8400，本地 HTTP 服务器截获 code。
        """
        try:
            import msal

            REDIRECT_URI = "http://localhost:8400"
            AUTHORITY = "https://login.microsoftonline.com/consumers"
            SCOPES = ["XboxLive.signin"]

            self.log("🌐 生成授权 URL...")
            app = msal.PublicClientApplication(
                self.CLIENT_ID, authority=AUTHORITY
            )
            auth_url = app.get_authorization_request_url(
                SCOPES, redirect_uri=REDIRECT_URI
            )
            self.log(f"🔗 授权 URL: {auth_url[:80]}...")

                                           
            done_event = threading.Event()
            _MSAuthCallbackHandler.auth_code = None
            _MSAuthCallbackHandler.error = None
            _MSAuthCallbackHandler.done_event = done_event

            httpd_holder = {"httpd": None}

            def run_server():
                httpd = HTTPServer(("localhost", 8400), _MSAuthCallbackHandler)
                httpd_holder["httpd"] = httpd
                while not done_event.is_set():
                    httpd.handle_request()
                httpd.server_close()

            threading.Thread(target=run_server, daemon=True).start()
            time.sleep(0.3)               
            self.log("✅ 本地回调服务器已就绪 (localhost:8400)")
                                                 
            child_code = r'''
import sys, time, threading
import webview
import tkinter as _tk
import ctypes
import winsound

auth_url = sys.argv[1]
redirect_uri = "http://localhost:8400"

# ====== 1. 计算居中坐标 ======
_tk_root = _tk.Tk()
_tk_root.withdraw()
_sw = _tk_root.winfo_screenwidth()
_sh = _tk_root.winfo_screenheight()
_tk_root.destroy()

_w, _h = 520, 720
_x = (_sw - _w) // 2
_y = (_sh - _h) // 2

# ====== 2. 创建登录窗口 ======
window = webview.create_window(
    "Microsoft 登录", auth_url,
    width=_w, height=_h, x=_x, y=_y,
    resizable=False,        # 不可改变大小
    frameless=True,         # 无边框（禁止拖动标题栏）
    on_top=True,            # 置顶
)

# ====== 3. 置顶但不抢焦点（模拟官方登录窗口行为） ======
def set_topmost_no_activate():
    """等窗口创建后，用 Windows API 设为 topmost 但不激活"""
    user32 = ctypes.windll.user32
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010

    # 等窗口出现（最多等 5 秒）
    for _ in range(50):
        hwnd = user32.FindWindowW(None, "Microsoft 登录")
        if hwnd:
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
            return
        time.sleep(0.1)

threading.Thread(target=set_topmost_no_activate, daemon=True).start()

# ====== 4. 焦点监控：失去焦点时播放系统提示音 ======
def focus_watchdog():
    """窗口失去焦点时播放系统感叹号提示音"""
    user32 = ctypes.windll.user32

    # 等窗口出现
    hwnd = None
    for _ in range(50):
        hwnd = user32.FindWindowW(None, "Microsoft 登录")
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        return

    last_focused = True
    while True:
        # 窗口是否还活着
        try:
            window.get_current_url()
        except Exception:
            return

        try:
            current_focused = (user32.GetForegroundWindow() == hwnd)
        except Exception:
            return

        # 从「有焦点」变成「没焦点」→ 播放提示音
        if last_focused and not current_focused:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

        last_focused = current_focused
        time.sleep(0.2)

threading.Thread(target=focus_watchdog, daemon=True).start()

# ====== 5. URL 轮询：命中 redirect 就关窗 ======
def poll_url():
    while True:
        try:
            url = window.get_current_url()
        except Exception:
            return
        if url and url.startswith(redirect_uri):
            try:
                window.destroy()
            except Exception:
                pass
            return
        time.sleep(0.3)

threading.Thread(target=poll_url, daemon=True).start()

# ====== 6. 启动 pywebview（阻塞直到窗口关闭） ======
webview.start()
'''

            self.log("🪟 启动内嵌登录窗口（独立进程）...")
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code, auth_url],
                creationflags=subprocess.CREATE_NO_WINDOW
                    if platform.system() == "Windows" else 0,
            )

                               
            self.log("⏳ 等待授权回调...")
            if not done_event.wait(timeout=300):
                self.log("❌ 授权超时（5 分钟）")
                try:
                    proc.terminate()
                except Exception:
                    pass
                return False

            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            self.log("⏳ 等待授权回调...")
            if not done_event.wait(timeout=300):
                self.log("❌ 授权超时（5 分钟）")
                return False

            if _MSAuthCallbackHandler.error:
                self.log(f"❌ 授权失败: {_MSAuthCallbackHandler.error}")
                return False

            auth_code = _MSAuthCallbackHandler.auth_code
            if not auth_code:
                self.log("❌ 未获取到授权码")
                return False

            self.log(f"✅ 收到授权码: {auth_code[:20]}...")

                                          
            self.log("🔄 正在用授权码换令牌...")
            token_result = app.acquire_token_by_authorization_code(
                auth_code,
                scopes=SCOPES,
                redirect_uri=REDIRECT_URI,
            )

            if "access_token" not in token_result:
                err = token_result.get("error_description", "未知错误")
                self.log(f"❌ 换 token 失败: {err}")
                return False

            self.access_token = token_result["access_token"]
            self.refresh_token = token_result.get("refresh_token")
            self.log("✅ 获取 Microsoft 令牌成功")
            return True

        except Exception as e:
            self.log(f"❌ 获取 Microsoft 令牌异常: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def _get_xbox_token(self):
        try:
            url = "https://user.auth.xboxlive.com/user/authenticate"
            headers = {"Content-Type": "application/json"}
            data = {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={self.access_token}"
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT"
            }
            response = requests.post(url, headers=headers, json=data, timeout=30)
            if response.status_code != 200:
                self.log(f"❌ Xbox 令牌失败: {response.status_code}")
                return False
            result = response.json()
            self.xbox_token = result.get('Token')
            self.user_hash = result.get('DisplayClaims', {}).get('xui', [{}])[0].get('uhs', '')
            if not self.user_hash:
                self.log("❌ 未获取到 user_hash")
                return False
            self.log("✅ Xbox Live 令牌获取成功")
            return True
        except Exception as e:
            self.log(f"❌ Xbox 令牌异常: {e}")
            return False
    
    def _get_xsts_token(self):
        try:
            url = "https://xsts.auth.xboxlive.com/xsts/authorize"
            headers = {"Content-Type": "application/json"}
            data = {
                "Properties": {
                    "SandboxId": "RETAIL",
                    "UserTokens": [self.xbox_token]
                },
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT"
            }
            response = requests.post(url, headers=headers, json=data, timeout=30)
            if response.status_code != 200:
                self.log(f"❌ XSTS 令牌失败: {response.status_code}")
                if response.status_code == 401:
                    self.log("💡 提示: 请确保你的 Microsoft 账号已购买 Minecraft")
                return False
            result = response.json()
            self.xbox_token = result.get('Token')
            self.log("✅ XSTS 令牌获取成功")
            return True
        except Exception as e:
            self.log(f"❌ XSTS 令牌异常: {e}")
            return False
    
    def _get_minecraft_token(self):
        try:
            url = "https://api.minecraftservices.com/authentication/login_with_xbox"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "identityToken": f"XBL3.0 x={self.user_hash};{self.xbox_token}"
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code != 200:
                self.log(f"❌ 换取 Minecraft 令牌失败: {response.status_code}")
                return False
            data = response.json()
            self.minecraft_access_token = data.get('access_token')
            if not self.minecraft_access_token:
                self.log("❌ 未获取到 Minecraft 访问令牌")
                return False
            self.log("✅ Minecraft 访问令牌获取成功")
            return True
        except Exception as e:
            self.log(f"❌ Minecraft 令牌异常: {e}")
            return False
    
    def _get_profile(self):
        try:
            url = "https://api.minecraftservices.com/minecraft/profile"
            headers = {
                "Authorization": f"Bearer {self.minecraft_access_token}",
                "Accept": "application/json",
                "User-Agent": "MinecraftLauncher/1.0"
            }
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code != 200:
                self.log(f"❌ 获取资料失败: {response.status_code}")
                return False
            profile = response.json()
            self.uuid = profile.get('id')
            self.username = profile.get('name')
            self.profile = profile
            self.log(f"✅ 获取资料成功: {self.username}")
            return True
        except Exception as e:
            self.log(f"❌ 获取资料异常: {e}")
            return False
    
    def refresh_auth(self):
        if not self.refresh_token:
            self.log("❌ 没有 RefreshToken")
            return False
        try:
            import msal

            AUTHORITY = "https://login.microsoftonline.com/consumers"
            SCOPES = ["XboxLive.signin"]

            app = msal.PublicClientApplication(
                self.CLIENT_ID, authority=AUTHORITY
            )
            result = app.acquire_token_by_refresh_token(
                self.refresh_token, scopes=SCOPES
            )

            if "access_token" not in result:
                err = result.get("error_description", "未知错误")
                self.log(f"❌ 刷新失败: {err}")
                return False

            self.access_token = result["access_token"]
            _new_rt = result.get("refresh_token")
            if _new_rt:
                self.refresh_token = _new_rt
            _exp_in = result.get("expires_in")
            self.expires_at = (time.time() + int(_exp_in)) if _exp_in else (time.time() + 86400)
            self.log("刷新: 微软 access_token 已更新")
            self.log("刷新: 重建 Xbox/XSTS/MC 链...")
            self.xbox_token = None
            self.user_hash = None
            if not self._get_xbox_token():
                self.log("刷新失败: Xbox 令牌获取失败")
                return False
            if not self._get_xsts_token():
                self.log("刷新失败: XSTS 令牌获取失败")
                return False
            if not self._get_minecraft_token():
                self.log("刷新失败: MC 令牌获取失败")
                return False
            if not self._get_profile():
                self.log("刷新失败: 玩家资料获取失败")
                return False
            self.log("刷新: MC 访问令牌已重建")
            try:
                self.save_auth()
                self.log("保存: 刷新令牌已落盘")
            except Exception as _se:
                self.log(f"警告: 保存刷新令牌失败: {_se}")
            return True
        except Exception as e:
            self.log(f"❌ 刷新异常: {e}")
            return False
    
    def ensure_valid_auth(self):
        """启动前调用：有 refresh_token 就强制 refresh 拿新 MC token（不信 expires_at）。"""
        if not self.minecraft_access_token:
            self.log("无 MC accessToken，需要重新登录")
            return False
        if not self.refresh_token:
            self.log("无 refresh_token，需要重新登录")
            return False
        self.log("启动前刷新令牌（强制）...")
        ok = self.refresh_auth()
        if ok:
            self.log("启动前刷新成功")
        else:
            self.log("启动前刷新失败，清理旧 token")
            # 刷新失败代表 refresh_token 也废了，清掉内存避免下次又用过期 token 启动
            self.minecraft_access_token = None
            self.access_token = None
            self.refresh_token = None
        return ok

    def login_with_token(self, access_token, username=None, uuid=None):
        if not access_token:
            self.log("❌ AccessToken 为空")
            return False
        self.minecraft_access_token = access_token
        self.username = username or "Player"
        self.uuid = uuid or "6bb65c25bdc7444382b949551c171a54"
        self.save_auth()
        self.log(f"✅ 手动 Token 登录成功: {self.username}")
        return True
    
    def import_from_official(self):
        self.log("📂 正在从官方启动器导入...")
        appdata = Path(os.environ.get('APPDATA', ''))
        paths = [
            appdata / ".minecraft/launcher_accounts.json",
            Path(os.environ.get('LOCALAPPDATA', '')) / "Packages/Microsoft.4297127D64EC6_8wekyb3d8bbwe/LocalCache/Roaming/.minecraft/launcher_accounts.json",
            Path.home() / ".minecraft/launcher_accounts.json",
        ]
        for path in paths:
            if not path.exists():
                continue
            self.log(f"📂 找到认证文件: {path}")
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for acc in data.get('accounts', {}).values():
                    token = acc.get('accessToken')
                    if token:
                        self.minecraft_access_token = token
                        self.username = acc.get('displayName') or acc.get('username')
                        self.uuid = acc.get('minecraftProfile', {}).get('id')
                        self.save_auth()
                        self.log(f"✅ 导入成功: {self.username}")
                        return True
            except Exception as e:
                self.log(f"⚠️ 读取失败: {e}")
        self.log("❌ 未找到认证文件")
        return False

                                                 
class CrashAnalyzer:
    @staticmethod
    def analyze(return_code, log_lines):
        log_text = "\n".join(log_lines)
        results = []
        if "F3+C" in log_text or "debug crash" in log_text.lower():
            results.append({
                'type': 'f3c_crash', 'severity': 'info',
                'message': '🔍 检测到 F3+C 强制崩溃 (用户主动触发)',
                'detail': '玩家按下了 F3+C 组合键，这是 Minecraft 内置的调试功能，用于生成崩溃报告。\n这不是真正的游戏错误。',
                'solution': '✅ 无需修复。这是正常的功能。'
            })
        vulkan_patterns = ['vulkan', 'Vulkan', 'VK_ERROR', 'VK_', 'vkCreateDevice',
                           'vkQueueSubmit', 'vkWaitForFences', 'RADV', 'ANV', 'validation layer']
        vulkan_matches = [p for p in vulkan_patterns if p in log_text]
        if vulkan_matches:
            results.append({
                'type': 'vulkan_crash', 'severity': 'warning',
                'message': '⚠️ 检测到 Vulkan 渲染器问题',
                'detail': f'匹配到关键词: {", ".join(vulkan_matches[:5])}',
                'solution': '💡 建议: 更新显卡驱动 / 切换 OpenGL / 回退 AMD 驱动'
            })
        if 'f3c_crash' in [r['type'] for r in results] and 'vulkan_crash' in [r['type'] for r in results]:
            results.append({
                'type': 'f3c_vulkan_combined', 'severity': 'info',
                'message': '🔍 F3+C 强制崩溃触发了 Vulkan 错误',
                'detail': '你按下了 F3+C，同时日志中出现了 Vulkan 相关错误。',
                'solution': '📌 尝试使用 OpenGL 渲染器 / 更新显卡驱动'
            })
        if "OutOfMemoryError" in log_text or "unable to allocate" in log_text:
            results.append({
                'type': 'out_of_memory', 'severity': 'error',
                'message': '❌ 内存不足 (OutOfMemoryError)',
                'detail': '游戏运行时 Java 堆内存耗尽。',
                'solution': '💡 增加分配内存 / 关闭其他程序 / 检查模组'
            })
        if "GLFW" in log_text and "error" in log_text.lower():
            results.append({
                'type': 'glfw_error', 'severity': 'error',
                'message': '❌ 显卡/OpenGL 初始化失败',
                'detail': 'GLFW 窗口系统初始化失败。',
                'solution': '💡 更新显卡驱动 / 切换渲染器'
            })
        if return_code == -1073741819 or return_code == 3221225477:
            results.append({
                'type': 'access_violation', 'severity': 'error',
                'message': '❌ 内存访问违规 (Access Violation - 0xC0000005)',
                'detail': '程序试图访问无法访问的内存地址。',
                'solution': '💡 降级/更新显卡驱动 / 降低画质'
            })
        if "fabric" in log_text.lower() and ("crash" in log_text.lower() or "error" in log_text.lower()):
            results.append({
                'type': 'mod_conflict', 'severity': 'error',
                'message': '⚠️ Fabric 或模组加载失败',
                'detail': '模组加载过程中出现了错误。',
                'solution': '💡 检查模组兼容性 / 逐个排查'
            })
        if return_code == 1 and not results:
            results.append({
                'type': 'general_error', 'severity': 'warning',
                'message': '⚠️ 游戏异常退出 (退出码 1)',
                'detail': '游戏进程返回了通用错误代码。',
                'solution': '💡 查看上方日志中的具体错误信息'
            })
        return results
    
    @staticmethod
    def format_results(results):
        if not results:
            return "✅ 未检测到明显崩溃原因"
        output = ["=" * 60, "💥 游戏崩溃分析报告", "=" * 60]
        for r in results:
            output.append("")
            output.append(f"📌 {r['message']}")
            output.append(f"📋 {r['detail']}")
            output.append(f"🔧 {r['solution']}")
        output.append("")
        output.append("=" * 60)
        return "\n".join(output)

                                                 
class MinecraftLauncher:
                                                      
    AIKAR_FLAGS = (
        "-XX:+UseG1GC -XX:+ParallelRefProcEnabled "
        "-XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions "
        "-XX:+DisableExplicitGC -XX:G1NewSizePercent=30 "
        "-XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
        "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 "
        "-XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 "
        " "
        "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
        "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1"
    )

    AMD_SAFE_FLAGS = (
        "-Dorg.lwjgl.vulkan.allowValidation=false "
        "-Dorg.lwjgl.vulkan.libname=vulkan-1.dll"
    )

    JVM_PRESETS = {
        "默认": "-XX:+UseG1GC -Dfile.encoding=UTF-8",
        "Aikar's Flags (推荐)": AIKAR_FLAGS,
        "A 卡受害者模式": f"{AIKAR_FLAGS} {AMD_SAFE_FLAGS}",
        "极限性能 (4核+)": (
            f"{AIKAR_FLAGS} -XX:+UseStringDeduplication "
            "-XX:+OptimizeStringConcat -XX:+UseCompressedOops"
        ),
        "省电模式 (低配)": (
            "-XX:+UseSerialGC -Xss512k -XX:MaxGCPauseMillis=500 "
            "-XX:+UseCompressedOops -Dfile.encoding=UTF-8"
        ),
        "调试模式 (崩溃排查)": (
            "-XX:+UseG1GC -XX:-OmitStackTraceInFastThrow "
            "-XX:+HeapDumpOnOutOfMemoryError "
            "-Dfile.encoding=UTF-8 -Dorg.lwjgl.util.Debug=true"
        ),
    }

                                                    
    def _format_number(self, n):
        """数字格式化：1234 → 1.2K，1234567 → 1.2M"""
        try:
            n = int(n)
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            elif n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)
        except:
            return str(n)

    def _make_result_tree(self, parent, columns_config):
        """
        创建一个标准的结果 Treeview
        columns_config: [(列id, 表头, 宽度, 对齐), ...]
        返回 (tree, frame)
        """
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        col_ids = [c[0] for c in columns_config]
        tree = ttk.Treeview(
            tree_frame, columns=col_ids, show="tree headings",
            height=15, selectmode="browse"
        )
               
        tree.heading("#0", text="")
        tree.column("#0", width=40, anchor="center", stretch=False)

        for col_id, header, width, anchor in columns_config:
            tree.heading(col_id, text=header)
            tree.column(col_id, width=width, anchor=anchor)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        _p = getattr(self, 'theme_palette', None) or {}
        _row_bg = _p.get('surface_alt', '#f5f5f5')
        _row_bg2 = _p.get('surface', '#ffffff')
        _row_fg = _p.get('fg', '#000000')
        tree.tag_configure("oddrow", background=_row_bg, foreground=_row_fg)
        tree.tag_configure("evenrow", background=_row_bg2, foreground=_row_fg)
        if not hasattr(self, '_theme_trees'):
            self._theme_trees = []
        self._theme_trees.append(tree)

        return tree, tree_frame

    def _clear_tree(self, tree):
                        
        for row in tree.get_children():
            tree.delete(row)
                          
        if hasattr(tree, '_photo_refs'):
            tree._photo_refs.clear()

    def _fill_tree_with_hits(self, tree, hits, extra_getter=None):
        """
        填充通用 hits 到 Treeview（带缩略图）
        缩略图异步下载，下载完再插入
        """
        self._clear_tree(tree)
        if not hits:
            return

                      
        row_map = {}                       
        for i, h in enumerate(hits):
            title = h.get("title", "")
            author = h.get("author", "")
            downloads = h.get("downloads", 0)
            followers = h.get("followers", 0)
            desc = h.get("description", "")
            updated = (h.get("date_modified") or h.get("updated") or "")[:10]
            mc_versions = h.get("versions", []) or h.get("game_versions", [])
            if mc_versions:
                mcver_show = ", ".join(mc_versions[-3:][::-1])
            else:
                mcver_show = ""

            values = [
                title,
                author,
                self._format_number(downloads),
                self._format_number(followers),
                mcver_show,
                updated,
                desc[:50] + ("..." if len(desc) > 50 else ""),
            ]
            if extra_getter:
                extra = extra_getter(h)
                values = extra + values

            tag = "evenrow" if i % 2 == 0 else "oddrow"
            row_id = tree.insert("", "end", values=values, tags=(tag,))

                      
            icon_url = h.get("icon_url", "")
            if icon_url:
                row_map[row_id] = icon_url

                                        
        if row_map:
            self._load_tree_icons_async(tree, row_map)

    def _load_tree_icons_async(self, tree, row_map):
        """异步下载缩略图并插入到 Treeview 的 #0 列"""
                               
        if not hasattr(self, '_tree_icon_tokens'):
            self._tree_icon_tokens = {}
        token = self._tree_icon_tokens.get(tree, 0) + 1
        self._tree_icon_tokens[tree] = token

                                      
        if not hasattr(self, '_tree_images'):
            self._tree_images = {}

        if not hasattr(self, '_icon_cache'):
            self._icon_cache = {}
        if not hasattr(self, '_img_session'):
            self._img_session = requests.Session()
            self._img_session.headers.update({"User-Agent": "Mozilla/5.0 MinecraftLauncher/1.0"})

        def worker(row_id, icon_url):
            try:
                if icon_url in self._icon_cache:
                    cached = self._icon_cache[icon_url]
                    if cached is not None:
                        self.root.after(0, lambda c=cached, r=row_id: self._insert_tree_icon(tree, r, c.copy(), token))
                    return
                # 磁盘缓存 key
                cache_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MinecraftLauncher" / "icon_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha1(icon_url.encode("utf-8")).hexdigest()
                cache_path = cache_dir / (key + ".png")
                if cache_path.exists() and cache_path.stat().st_size > 0:
                    try:
                        img = Image.open(str(cache_path)).convert("RGBA")
                        img.thumbnail((24, 24), Image.LANCZOS)
                        self._icon_cache[icon_url] = img
                        self.root.after(0, lambda: self._insert_tree_icon(tree, row_id, img, token))
                        return
                    except Exception:
                        pass
                # 尝试多个 URL(镜像回退)
                urls = [icon_url]
                if "cdn.modrinth.com" in icon_url:
                    urls.append(icon_url.replace("cdn.modrinth.com", "cdn-raw.modrinth.com"))
                content = None
                for u in urls:
                    try:
                        resp = self._img_session.get(u, timeout=6)
                        resp.raise_for_status()
                        content = resp.content
                        break
                    except Exception:
                        continue
                if content is None:
                    self._icon_cache[icon_url] = None
                    return
                img = Image.open(BytesIO(content))
                img = img.convert("RGBA")
                img.thumbnail((24, 24), Image.LANCZOS)
                self._icon_cache[icon_url] = img
                try:
                    img.save(str(cache_path), "PNG")
                except Exception:
                    pass
                self.root.after(0, lambda: self._insert_tree_icon(tree, row_id, img, token))
            except Exception:
                self._icon_cache[icon_url] = None

        def run_tasks():
            with ThreadPoolExecutor(max_workers=32) as ex:
                for row_id, icon_url in row_map.items():
                    if icon_url:
                        ex.submit(worker, row_id, icon_url)

        threading.Thread(target=run_tasks, daemon=True).start()

    def _insert_tree_icon(self, tree, row_id, pil_img, token):
        """在主线程里把 PIL Image 插到 Treeview 指定行"""
        if self._tree_icon_tokens.get(tree, 0) != token:
            return           
        try:
            photo = ImageTk.PhotoImage(pil_img)
                                         
            if not hasattr(tree, '_photo_refs'):
                tree._photo_refs = {}
            tree._photo_refs[row_id] = photo
            tree.item(row_id, image=photo)
        except Exception:
            pass

                                                    
    def start_performance_monitor(self, process):
        """启动性能监控线程"""
        if not HAS_PSUTIL:
            self.log("⚠️ 未安装 psutil，性能监控不可用。请运行: pip install psutil", level='warning')
            self.monitor_status_var.set("psutil 未安装")
            return

        try:
            proc = psutil.Process(process.pid)
        except Exception as e:
            self.log(f"⚠️ 无法监控进程: {e}", level='warning')
            return

        self.monitor_status_var.set("🟢 运行中")
        start_time = time.time()

        def monitor_loop():
            while process.poll() is None:
                try:
                    cpu = proc.cpu_percent(interval=1.0)
                    mem_info = proc.memory_info()
                    mem_mb = mem_info.rss / 1024 / 1024
                    total_mem = psutil.virtual_memory().total / 1024 / 1024
                    mem_pct = (mem_mb / total_mem) * 100
                    elapsed = int(time.time() - start_time)
                    h, r = divmod(elapsed, 3600)
                    m, s = divmod(r, 60)

                    self.root.after(0, lambda c=cpu, mb=mem_mb, pct=mem_pct, hh=h, mm=m, ss=s:
                        self._update_monitor_ui(c, mb, pct, hh, mm, ss))
                except psutil.NoSuchProcess:
                    break
                except Exception:
                    break
                time.sleep(1)

            self.root.after(0, lambda: self.monitor_status_var.set("⚫ 已结束"))
            self.root.after(0, lambda: self.monitor_cpu_var.set("CPU: --"))
            self.root.after(0, lambda: self.monitor_mem_var.set("内存: --"))

        threading.Thread(target=monitor_loop, daemon=True).start()

    def _update_monitor_ui(self, cpu, mem_mb, mem_pct, h, m, s):
        """更新监控面板 UI"""
                                        
        cpu_count = psutil.cpu_count() or 1
        cpu_normalized = cpu / cpu_count

        self.monitor_cpu_var.set(f"CPU: {cpu_normalized:.1f}%")
        self.monitor_mem_var.set(f"内存: {mem_mb:.0f}MB ({mem_pct:.1f}%)")
        self.monitor_time_var.set(f"运行: {h:02d}:{m:02d}:{s:02d}")
        self.monitor_mem_bar['value'] = min(mem_pct, 100)

        if mem_pct > 85:
            self.monitor_mem_var.set(f"⚠️ 内存: {mem_mb:.0f}MB ({mem_pct:.1f}%)")

                                                        
    def detect_amd_gpu(self):
        """检测是否为 AMD 显卡"""
        try:
            if platform.system() == 'Windows':
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     'Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name'],
                    capture_output=True, text=True, timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                output = result.stdout.lower()
                return 'amd' in output or 'radeon' in output or 'vega' in output
        except Exception as e:
            self.log(f"⚠️ 显卡检测失败: {e}", level='warning')
        return False

    def apply_amd_victim_mode(self):
        """A 卡受害者模式：一键应用所有 A 卡优化参数"""
        if not messagebox.askyesno(
            "A 卡受害者模式",
            "将应用以下优化：\n\n"
            "✅ Aikar's Flags (G1GC 调优)\n"
            "✅ 禁用 Vulkan 验证\n"
            "✅ 强制指定 vulkan-1.dll\n"
            "✅ 关闭独占全屏（避免 Win 键冻结）\n\n"
            "确定应用吗？"
        ):
            return

        self.custom_jvm_args_var.set(f"{self.AIKAR_FLAGS} {self.AMD_SAFE_FLAGS}")
        self.log("🔥 已应用 Aikar's Flags + AMD 安全参数", level='info')

        self._disable_exclusive_fullscreen()

        is_amd = self.detect_amd_gpu()
        if is_amd:
            self.log("✅ 检测到 AMD 显卡，A 卡受害者模式已激活", level='info')
            messagebox.showinfo(
                "A 卡受害者模式",
                "✅ 已启用 A 卡优化\n\n"
                "建议：\n"
                "• 游戏内使用「无边框窗口」而非「独占全屏」\n"
                "• 如仍崩溃，尝试回退 AMD 驱动到 25.8.1\n"
                "• 内存分配不要超过物理内存的 60%"
            )
        else:
            self.log("ℹ️ 未检测到 AMD 显卡，但参数已应用（对 N 卡无害）", level='info')

    def _disable_exclusive_fullscreen(self):
        """关闭所有版本的独占全屏"""
        count = 0
        if not self.version_dir.exists():
            return
        for vdir in self.version_dir.iterdir():
            if not vdir.is_dir():
                continue
            options_file = vdir / "options.txt"
            if not options_file.exists():
                continue
            try:
                with open(options_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                changed = False
                new_lines = []
                for line in lines:
                    if line.strip().startswith('exclusiveFullscreen:'):
                        if line.strip() != 'exclusiveFullscreen:false':
                            new_lines.append('exclusiveFullscreen:false\n')
                            changed = True
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                if changed:
                    with open(options_file, 'w', encoding='utf-8') as f:
                        f.writelines(new_lines)
                    count += 1
            except Exception as e:
                self.log(f"⚠️ 修改 {vdir.name}/options.txt 失败: {e}", level='warning')
        if count:
            self.log(f"✅ 已关闭 {count} 个版本的独占全屏", level='info')
        else:
            self.log("ℹ️ 未发现需要修改的 options.txt", level='info')

    def on_jvm_preset_change(self, event=None):
        preset_name = self.jvm_preset_var.get()
        if preset_name in self.JVM_PRESETS:
            self.custom_jvm_args_var.set(self.JVM_PRESETS[preset_name])
            self.log(f"🎛️ 已应用 JVM 预设: {preset_name}", level='info')
        self.save_config()          

    def save_jvm_as_preset(self):
        """把当前 JVM 参数保存为自定义预设"""
        current = self.custom_jvm_args_var.get().strip()
        if not current:
            messagebox.showwarning("提示", "当前 JVM 参数为空")
            return

        d = tk.Toplevel(self.root)
        d.title("保存 JVM 预设")
        d.geometry("400x150")
        d.transient(self.root)
        d.grab_set()

        ttk.Label(d, text="预设名称:").pack(pady=10)
        name_entry = ttk.Entry(d, width=30)
        name_entry.pack(pady=5)

        def confirm():
            name = name_entry.get().strip()
            if not name:
                messagebox.showerror("错误", "请输入预设名称")
                return
            if name in self.JVM_PRESETS:
                if not messagebox.askyesno("覆盖", f"预设 '{name}' 已存在，覆盖？"):
                    return
            self.JVM_PRESETS[name] = current
            self.log(f"💾 已保存 JVM 预设: {name}（重启后生效）", level='info')
            messagebox.showinfo("成功", f"预设 '{name}' 已保存\n重启启动器后生效")
            d.destroy()

        ttk.Button(d, text="保存", command=confirm).pack(pady=10)

                                                                    
    def search_shaders(self):
        query = self.shader_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        mc_ver = self.shader_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.shader_sort_var.get(), "downloads")

        facets = [["project_type:shader"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"query": query, "limit": 30, "index": index,
                  "facets": json.dumps(facets)}
        self.log(f"🔍 搜索光影: {query} (MC={mc_ver})")
        self.shader_status_label.config(text="搜索中...", foreground="blue")
        threading.Thread(target=self._do_search_shaders, args=(params,), daemon=True).start()

    def _do_search_shaders(self, params):
        try:
            r = requests.get("https://api.modrinth.com/v2/search", params=params, timeout=114514)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            self.shader_search_results = hits
            self.root.after(0, lambda: self._fill_tree_with_hits(self.shader_tree, hits))
            self.log(f"✅ 找到 {len(hits)} 个光影")
            self.root.after(0, lambda: self.shader_status_label.config(
                text=f"找到 {len(hits)} 个结果", foreground="green"))
        except Exception as e:
            self.log(f"❌ 光影搜索失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("搜索失败", str(e)))
            self.root.after(0, lambda: self.shader_status_label.config(text="搜索失败", foreground="red"))

    def show_shader_versions(self):
        sel = self.shader_tree.selection()
        if not sel or not hasattr(self, 'shader_search_results'):
            messagebox.showwarning("提示", "请先搜索并选中一个光影")
            return
        idx = self.shader_tree.index(sel[0])
        mod = self.shader_search_results[idx]
        project_id = mod["project_id"]
        shader_title = mod.get("title", "")
        mc_ver = self.shader_mc_filter.get().strip()
        self.log(f"📋 获取 {shader_title} 的版本列表...")
        threading.Thread(target=self._fetch_shader_versions,
                         args=(project_id, mc_ver, shader_title),
                         daemon=True).start()

    def _fetch_shader_versions(self, project_id, mc_ver, shader_title):
        try:
            params = {}
            if mc_ver:
                params["game_versions"] = json.dumps([mc_ver])
            r = requests.get(
                f"https://api.modrinth.com/v2/project/{project_id}/version",
                params=params, timeout=15
            )
            r.raise_for_status()
            versions = r.json()
            if not versions:
                self.root.after(0, lambda: messagebox.showwarning(
                    "无匹配版本", f"{shader_title} 没有适配 MC {mc_ver} 的版本"))
                return
            self.root.after(0, lambda: self._show_shader_version_dialog(versions, shader_title))
        except Exception as e:
            self.log(f"❌ 获取光影版本失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("获取失败", str(e)))

    def _show_shader_version_dialog(self, versions, shader_title):
        d = tk.Toplevel(self.root)
        d.title(f"选择光影版本 - {shader_title}")
        d.geometry("700x400")
        d.transient(self.root)
        d.grab_set()

        ttk.Label(d, text=f"共 {len(versions)} 个版本，双击直接下载:",
                  font=("Microsoft YaHei", 10)).pack(pady=10)

        lb_frame = ttk.Frame(d)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        lb = tk.Listbox(lb_frame, font=("Consolas", 9))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        for v in versions:
            files = v.get("files", [])
            fn = files[0]["filename"] if files else "无文件"
            game_vers = ",".join(v.get("game_versions", [])[:3])
            lb.insert(tk.END, f"{v['version_number']}  |  MC {game_vers}  |  {fn}")

        def on_double_click(event=None):
            sel = lb.curselection()
            if not sel:
                return
            ver = versions[sel[0]]
            d.destroy()
            self._download_shader_version(ver, shader_title)

        lb.bind("<Double-Button-1>", on_double_click)
        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📥 下载此版本", command=on_double_click).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=d.destroy).pack(side=tk.LEFT, padx=5)

    def _download_shader_version(self, version, shader_title):
        target_version = self.get_selected_launch_version()
        if not target_version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return
        files = version.get("files", [])
        if not files:
            messagebox.showerror("错误", "此版本没有可用文件")
            return
        primary = next((f for f in files if f.get("primary")), files[0])
        url = primary["url"]
        filename = primary["filename"]

                                 
        shader_dir = self.version_dir / target_version / "shaderpacks"
        shader_dir.mkdir(parents=True, exist_ok=True)
        save_path = shader_dir / filename

        self.log(f"📥 下载光影: {filename}")
        self.log(f"   目标: {save_path}")
        threading.Thread(target=self._do_download_shader, args=(url, save_path, shader_title), daemon=True).start()
                                                                     
    def search_resourcepacks(self):
        query = self.rp_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        mc_ver = self.rp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.rp_sort_var.get(), "downloads")

        facets = [["project_type:resourcepack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"query": query, "limit": 30, "index": index,
                  "facets": json.dumps(facets)}
        self.log(f"🔍 搜索材质包: {query} (MC={mc_ver})")
        self.rp_status_label.config(text="搜索中...", foreground="blue")
        threading.Thread(target=self._do_search_resourcepacks, args=(params,), daemon=True).start()

    def _do_search_resourcepacks(self, params):
        try:
            r = requests.get("https://api.modrinth.com/v2/search", params=params, timeout=114514)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            self.rp_search_results = hits
            self.root.after(0, lambda: self._fill_tree_with_hits(self.rp_tree, hits))
            self.log(f"✅ 找到 {len(hits)} 个材质包")
            self.root.after(0, lambda: self.rp_status_label.config(
                text=f"找到 {len(hits)} 个结果", foreground="green"))
        except Exception as e:
            self.log(f"❌ 材质包搜索失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("搜索失败", str(e)))
            self.root.after(0, lambda: self.rp_status_label.config(text="搜索失败", foreground="red"))

    def show_resourcepack_versions(self):
        sel = self.rp_tree.selection()
        if not sel or not hasattr(self, 'rp_search_results'):
            messagebox.showwarning("提示", "请先搜索并选中一个材质包")
            return
        idx = self.rp_tree.index(sel[0])
        mod = self.rp_search_results[idx]
        project_id = mod["project_id"]
        rp_title = mod.get("title", "")
        mc_ver = self.rp_mc_filter.get().strip()
        self.log(f"📋 获取 {rp_title} 的版本列表...")
        threading.Thread(target=self._fetch_resourcepack_versions,
                         args=(project_id, mc_ver, rp_title),
                         daemon=True).start()

    def _fetch_resourcepack_versions(self, project_id, mc_ver, rp_title):
        try:
            params = {}
            if mc_ver:
                params["game_versions"] = json.dumps([mc_ver])
            r = requests.get(
                f"https://api.modrinth.com/v2/project/{project_id}/version",
                params=params, timeout=15
            )
            r.raise_for_status()
            versions = r.json()
            if not versions:
                self.root.after(0, lambda: messagebox.showwarning(
                    "无匹配版本", f"{rp_title} 没有适配 MC {mc_ver} 的版本"))
                return
            self.root.after(0, lambda: self._show_resourcepack_version_dialog(versions, rp_title))
        except Exception as e:
            self.log(f"❌ 获取材质包版本失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("获取失败", str(e)))

    def _show_resourcepack_version_dialog(self, versions, rp_title):
        d = tk.Toplevel(self.root)
        d.title(f"选择材质包版本 - {rp_title}")
        d.geometry("700x400")
        d.transient(self.root)
        d.grab_set()

        ttk.Label(d, text=f"共 {len(versions)} 个版本，双击直接下载:",
                  font=("Microsoft YaHei", 10)).pack(pady=10)

        lb_frame = ttk.Frame(d)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        lb = tk.Listbox(lb_frame, font=("Consolas", 9))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        for v in versions:
            files = v.get("files", [])
            fn = files[0]["filename"] if files else "无文件"
            game_vers = ",".join(v.get("game_versions", [])[:3])
            lb.insert(tk.END, f"{v['version_number']}  |  MC {game_vers}  |  {fn}")

        def on_double_click(event=None):
            sel = lb.curselection()
            if not sel:
                return
            ver = versions[sel[0]]
            d.destroy()
            self._download_resourcepack_version(ver, rp_title)

        lb.bind("<Double-Button-1>", on_double_click)
        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📥 下载此版本", command=on_double_click).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=d.destroy).pack(side=tk.LEFT, padx=5)

    def _download_resourcepack_version(self, version, rp_title):
        target_version = self.get_selected_launch_version()
        if not target_version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return
        files = version.get("files", [])
        if not files:
            messagebox.showerror("错误", "此版本没有可用文件")
            return
        primary = next((f for f in files if f.get("primary")), files[0])
        url = primary["url"]
        filename = primary["filename"]

                                           
        rp_dir = self.version_dir / target_version / "resourcepacks"
        rp_dir.mkdir(parents=True, exist_ok=True)
        save_path = rp_dir / filename

        self.log(f"📥 下载材质包: {filename}")
        self.log(f"   目标: {save_path}")
        threading.Thread(target=self._do_download_resourcepack, args=(url, save_path, rp_title), daemon=True).start()

    def _do_download_resourcepack(self, url, save_path, rp_title):
        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            downloaded = 0
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            size_mb = downloaded / 1024 / 1024
            self.log(f"✅ {rp_title} 下载完成: {save_path.name} ({size_mb:.2f} MB)")
            self.root.after(0, lambda: messagebox.showinfo("下载完成", f"{rp_title}\n已保存到:\n{save_path}"))
        except Exception as e:
            self.log(f"❌ 材质包下载失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("下载失败", str(e)))
                                                                     
    def search_datapacks(self):
        query = self.dp_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        mc_ver = self.dp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.dp_sort_var.get(), "downloads")

        facets = [["project_type:datapack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"query": query, "limit": 30, "index": index,
                  "facets": json.dumps(facets)}
        self.log(f"🔍 搜索数据包: {query} (MC={mc_ver})")
        self.dp_status_label.config(text="搜索中...", foreground="blue")
        threading.Thread(target=self._do_search_datapacks, args=(params,), daemon=True).start()

    def _do_search_datapacks(self, params):
        try:
            r = requests.get("https://api.modrinth.com/v2/search", params=params, timeout=114514)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            self.dp_search_results = hits
            self.root.after(0, lambda: self._fill_tree_with_hits(self.dp_tree, hits))
            self.log(f"✅ 找到 {len(hits)} 个数据包")
            self.root.after(0, lambda: self.dp_status_label.config(
                text=f"找到 {len(hits)} 个结果", foreground="green"))
        except Exception as e:
            self.log(f"❌ 数据包搜索失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("搜索失败", str(e)))
            self.root.after(0, lambda: self.dp_status_label.config(text="搜索失败", foreground="red"))

    def show_datapack_versions(self):
        sel = self.dp_tree.selection()
        if not sel or not hasattr(self, 'dp_search_results'):
            messagebox.showwarning("提示", "请先搜索并选中一个数据包")
            return
        idx = self.dp_tree.index(sel[0])
        mod = self.dp_search_results[idx]
        project_id = mod["project_id"]
        dp_title = mod.get("title", "")
        mc_ver = self.dp_mc_filter.get().strip()
        self.log(f"📋 获取 {dp_title} 的版本列表...")
        threading.Thread(target=self._fetch_datapack_versions,
                         args=(project_id, mc_ver, dp_title),
                         daemon=True).start()

    def _fetch_datapack_versions(self, project_id, mc_ver, dp_title):
        try:
            params = {}
            if mc_ver:
                params["game_versions"] = json.dumps([mc_ver])
            r = requests.get(
                f"https://api.modrinth.com/v2/project/{project_id}/version",
                params=params, timeout=15
            )
            r.raise_for_status()
            versions = r.json()
            if not versions:
                self.root.after(0, lambda: messagebox.showwarning(
                    "无匹配版本", f"{dp_title} 没有适配 MC {mc_ver} 的版本"))
                return
            self.root.after(0, lambda: self._show_datapack_version_dialog(versions, dp_title))
        except Exception as e:
            self.log(f"❌ 获取数据包版本失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("获取失败", str(e)))

    def _show_datapack_version_dialog(self, versions, dp_title):
        d = tk.Toplevel(self.root)
        d.title(f"选择数据包版本 - {dp_title}")
        d.geometry("700x400")
        d.transient(self.root)
        d.grab_set()

        ttk.Label(d, text=f"共 {len(versions)} 个版本，双击直接下载:",
                  font=("Microsoft YaHei", 10)).pack(pady=10)

        lb_frame = ttk.Frame(d)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        lb = tk.Listbox(lb_frame, font=("Consolas", 9))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        for v in versions:
            files = v.get("files", [])
            fn = files[0]["filename"] if files else "无文件"
            game_vers = ",".join(v.get("game_versions", [])[:3])
            lb.insert(tk.END, f"{v['version_number']}  |  MC {game_vers}  |  {fn}")

        def on_double_click(event=None):
            sel = lb.curselection()
            if not sel:
                return
            ver = versions[sel[0]]
            d.destroy()
            self._download_datapack_version(ver, dp_title)

        lb.bind("<Double-Button-1>", on_double_click)
        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📥 下载此版本", command=on_double_click).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=d.destroy).pack(side=tk.LEFT, padx=5)

    def _download_datapack_version(self, version, dp_title):
        target_version = self.get_selected_launch_version()
        if not target_version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return
        files = version.get("files", [])
        if not files:
            messagebox.showerror("错误", "此版本没有可用文件")
            return
        primary = next((f for f in files if f.get("primary")), files[0])
        url = primary["url"]
        filename = primary["filename"]

                                         
                                
                                                     
        dp_dir = self.version_dir / target_version / "datapacks"
        dp_dir.mkdir(parents=True, exist_ok=True)
        save_path = dp_dir / filename

        self.log(f"📥 下载数据包: {filename}")
        self.log(f"   目标: {save_path}")
        self.log(f"   💡 提示：数据包需要放到存档的 datapacks 文件夹才生效")
        threading.Thread(target=self._do_download_datapack, args=(url, save_path, dp_title), daemon=True).start()

    def _do_download_datapack(self, url, save_path, dp_title):
        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            downloaded = 0
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            size_mb = downloaded / 1024 / 1024
            self.log(f"✅ {dp_title} 下载完成: {save_path.name} ({size_mb:.2f} MB)")
            self.root.after(0, lambda: messagebox.showinfo(
                "下载完成",
                f"{dp_title}\n已保存到:\n{save_path}\n\n"
                f"⚠️ 数据包需要手动放入存档的 datapacks 文件夹才生效"
            ))
        except Exception as e:
            self.log(f"❌ 数据包下载失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("下载失败", str(e)))
                                                                         
    def search_modpacks(self):
        query = self.mp_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        mc_ver = self.mp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.mp_sort_var.get(), "downloads")

        facets = [["project_type:modpack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"query": query, "limit": 30, "index": index,
                  "facets": json.dumps(facets)}
        self.log(f"🔍 搜索整合包: {query} (MC={mc_ver})")
        self.mp_status_label.config(text="搜索中...", foreground="blue")
        threading.Thread(target=self._do_search_modpacks, args=(params,), daemon=True).start()

    def _do_search_modpacks(self, params):
        try:
            r = requests.get("https://api.modrinth.com/v2/search", params=params, timeout=114514)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            self.mp_search_results = hits
            self.root.after(0, lambda: self._fill_tree_with_hits(self.mp_tree, hits))
            self.log(f"✅ 找到 {len(hits)} 个整合包")
            self.root.after(0, lambda: self.mp_status_label.config(
                text=f"找到 {len(hits)} 个结果", foreground="green"))
        except Exception as e:
            self.log(f"❌ 整合包搜索失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("搜索失败", str(e)))
            self.root.after(0, lambda: self.mp_status_label.config(text="搜索失败", foreground="red"))

    def show_modpack_versions(self):
        sel = self.mp_tree.selection()
        if not sel or not hasattr(self, 'mp_search_results'):
            messagebox.showwarning("提示", "请先搜索并选中一个整合包")
            return
        idx = self.mp_tree.index(sel[0])
        mod = self.mp_search_results[idx]
        project_id = mod["project_id"]
        mp_title = mod.get("title", "")
        mc_ver = self.mp_mc_filter.get().strip()
        self.log(f"📋 获取 {mp_title} 的版本列表...")
        self.mp_status_label.config(text=f"获取 {mp_title} 版本...", foreground="blue")
        threading.Thread(target=self._fetch_modpack_versions,
                         args=(project_id, mc_ver, mp_title),
                         daemon=True).start()

    def _fetch_modpack_versions(self, project_id, mc_ver, mp_title):
        try:
            params = {}
            if mc_ver:
                params["game_versions"] = json.dumps([mc_ver])
            r = requests.get(
                f"https://api.modrinth.com/v2/project/{project_id}/version",
                params=params, timeout=15
            )
            r.raise_for_status()
            versions = r.json()
            if not versions:
                self.root.after(0, lambda: messagebox.showwarning(
                    "无匹配版本", f"{mp_title} 没有适配 MC {mc_ver} 的版本"))
                return
            self.root.after(0, lambda: self._show_modpack_version_dialog(versions, mp_title))
        except Exception as e:
            self.log(f"❌ 获取整合包版本失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("获取失败", str(e)))

    def _show_modpack_version_dialog(self, versions, mp_title):
        d = tk.Toplevel(self.root)
        d.title(f"选择整合包版本 - {mp_title}")
        d.geometry("750x450")
        d.transient(self.root)
        d.grab_set()

        ttk.Label(d, text=f"共 {len(versions)} 个版本，双击直接安装:",
                  font=("Microsoft YaHei", 10)).pack(pady=10)

        lb_frame = ttk.Frame(d)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        lb = tk.Listbox(lb_frame, font=("Consolas", 9))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        for v in versions:
            files = v.get("files", [])
            fn = files[0]["filename"] if files else "无文件"
            game_vers = ",".join(v.get("game_versions", [])[:3])
            loaders = ",".join(v.get("loaders", []))
            lb.insert(tk.END, f"{v['version_number']}  |  {loaders}  |  MC {game_vers}  |  {fn}")

        def on_double_click(event=None):
            sel = lb.curselection()
            if not sel:
                return
            ver = versions[sel[0]]
            d.destroy()
            self._install_modpack(ver, mp_title)

        lb.bind("<Double-Button-1>", on_double_click)
        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📥 安装此版本", command=on_double_click).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=d.destroy).pack(side=tk.LEFT, padx=5)

    def _install_modpack(self, version, mp_title):
        files = version.get("files", [])
        if not files:
            messagebox.showerror("错误", "此版本没有可用文件")
            return
        primary = next((f for f in files if f.get("primary")), files[0])
        url = primary["url"]
        filename = primary["filename"]
        version_number = version.get("version_number", "unknown")

               
        if not messagebox.askyesno(
            "确认安装整合包",
            f"即将安装整合包：\n\n"
            f"  名称: {mp_title}\n"
            f"  版本: {version_number}\n"
            f"  文件: {filename}\n\n"
            f"安装过程会自动：\n"
            f"  1. 下载 .mrpack 文件\n"
            f"  2. 安装对应加载器\n"
            f"  3. 下载所有 Mod\n"
            f"  4. 复制配置文件\n"
            f"  5. 删除临时 .mrpack\n\n"
            f"确定安装吗？"
        ):
            return

        self.log("=" * 60)
        self.log(f"📦 开始安装整合包: {mp_title} ({version_number})")
        self.open_console_window()
        threading.Thread(
            target=self._do_install_modpack,
            args=(url, filename, mp_title, version_number),
            daemon=True
        ).start()

    def _do_install_modpack(self, url, filename, mp_title, version_number):
        import tempfile
        import time as _time

        tmp_dir = None
        mrpack_path = None
        try:
                                               
            tmp_dir = Path(tempfile.mkdtemp(prefix="modpack_"))
            mrpack_path = tmp_dir / filename
            self.log(f"📥 下载整合包文件: {filename}")
            r = requests.get(url, stream=True, timeout=180)
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            with open(mrpack_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded / total * 100
                            self.root.after(0, lambda p=pct: self.file_progress_var.set(p))
            self.log(f"✅ 下载完成 ({downloaded // 1024 // 1024} MB)")

                                         
            extract_dir = tmp_dir / "extracted"
            extract_dir.mkdir()
            self.log("📦 解压整合包...")
            with zipfile.ZipFile(mrpack_path, 'r') as zf:
                zf.extractall(extract_dir)

                                                     
            index_file = extract_dir / "modrinth.index.json"
            if not index_file.exists():
                raise Exception("整合包里没有 modrinth.index.json，可能是损坏的文件")
            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = json.load(f)

            pack_name = index_data.get("name", mp_title)
            pack_version = index_data.get("versionId", version_number)
            dependencies = index_data.get("dependencies", {})
            files_list = index_data.get("files", [])

                                           
            mc_version = dependencies.get("minecraft", "")
            if not mc_version:
                raise Exception("整合包没有指定 Minecraft 版本")

            loader_type = None
            loader_version = None
            if "fabric-loader" in dependencies:
                loader_type = "fabric"
                loader_version = dependencies["fabric-loader"]
            elif "forge" in dependencies:
                loader_type = "forge"
                loader_version = dependencies["forge"]
            elif "neoforge" in dependencies:
                loader_type = "neoforge"
                loader_version = dependencies["neoforge"]
            else:
                self.log("⚠️ 整合包未指定 Mod 加载器，将作为原版整合包安装")

            self.log(f"📌 整合包: {pack_name} v{pack_version}")
            self.log(f"📌 Minecraft: {mc_version}")
            if loader_type:
                self.log(f"📌 加载器: {loader_type} {loader_version}")
            self.log(f"📌 需要下载 {len(files_list)} 个文件")

                                        
                             
                                         
            safe_name = re.sub(r'[<>:"/\\|?*\[\]()]', '', pack_name)            
            safe_name = safe_name.replace(' ', '_')                            
            safe_name = re.sub(r'_+', '_', safe_name)                           
            safe_name = safe_name.strip('_')                                    
            version_dir_name = f"{safe_name}-{pack_version}"
            target_dir = self.version_dir / version_dir_name

            if target_dir.exists():
                if not messagebox.askyesno(
                    "已存在",
                    f"版本目录 {version_dir_name} 已存在，是否覆盖？"
                ):
                    self.log("ℹ️ 已取消安装")
                    return
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True)

                                              
            vanilla_dir = self.version_dir / mc_version
            vanilla_json = vanilla_dir / f"{mc_version}.json"
            vanilla_jar = vanilla_dir / f"{mc_version}.jar"
            if not vanilla_json.exists() or not vanilla_jar.exists():
                self.log(f"📥 原版 {mc_version} 不存在，先下载原版...")
                self._download_vanilla_silent(mc_version)
            else:
                self.log(f"✅ 原版 {mc_version} 已存在")

                                    
            if loader_type == "fabric":
                self.log(f"🔧 安装 Fabric {mc_version} + {loader_version}...")
                if not self.mod_manager.install_fabric(mc_version, loader_version):
                    raise Exception("Fabric 安装失败")
                                                    
                installed_dir_name = f"{mc_version}-Fabric-{loader_version}"
                installed_dir = self.version_dir / installed_dir_name
                if not installed_dir.exists():
                    raise Exception(f"未找到 Fabric 版本目录: {installed_dir}")
                self._merge_version_dir(installed_dir, target_dir, version_dir_name)
                                 
                try:
                    shutil.rmtree(installed_dir)
                except Exception as e:
                    self.log(f"⚠️ 清理临时 Fabric 目录失败: {e}", level='warning')

            elif loader_type == "forge":
                self.log(f"🔧 安装 Forge {mc_version} + {loader_version}...")
                if not self.mod_manager.install_forge(mc_version, loader_version):
                    raise Exception("Forge 安装失败")
                            
                forge_dirs = [
                    d for d in self.version_dir.iterdir()
                    if d.is_dir() and 'forge' in d.name.lower() and mc_version in d.name
                ]
                if not forge_dirs:
                    raise Exception("未找到 Forge 版本目录")
                self._merge_version_dir(forge_dirs[0], target_dir, version_dir_name)
                try:
                    shutil.rmtree(forge_dirs[0])
                except Exception as e:
                    self.log(f"⚠️ 清理临时 Forge 目录失败: {e}", level='warning')

            elif loader_type == "neoforge":
                self.log(f"🔧 安装 NeoForge {mc_version} + {loader_version}...")
                if not self.mod_manager.install_neoforge(mc_version, loader_version):
                    raise Exception("NeoForge 安装失败")
                neoforge_dirs = [
                    d for d in self.version_dir.iterdir()
                    if d.is_dir() and 'neoforge' in d.name.lower() and mc_version in d.name
                ]
                if not neoforge_dirs:
                    raise Exception("未找到 NeoForge 版本目录")
                self._merge_version_dir(neoforge_dirs[0], target_dir, version_dir_name)
                try:
                    shutil.rmtree(neoforge_dirs[0])
                except Exception as e:
                    self.log(f"⚠️ 清理临时 NeoForge 目录失败: {e}", level='warning')

            else:
                             
                self.log("📋 复制原版文件...")
                for f in vanilla_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, target_dir / f.name)

                                        
            if files_list:
                self.log(f"📥 开始下载 {len(files_list)} 个整合包文件...")
                self._download_modpack_files(files_list, target_dir)

                                            
            overrides_dir = extract_dir / "overrides"
            if overrides_dir.exists():
                self.log("📁 复制 overrides/ 覆盖文件...")
                self._copy_overrides(overrides_dir, target_dir)
            else:
                self.log("ℹ️ 整合包没有 overrides/ 目录")

                                                           
            self._fix_modpack_version_json(target_dir, version_dir_name)

            self.log("=" * 60)
            self.log(f"✅ 整合包安装成功: {version_dir_name}")
            self.log(f"📁 位置: {target_dir}")
            self.log("=" * 60)

                                      
            self.root.after(0, self.refresh_versions)
            self.root.after(0, self.refresh_local_versions)

            self.root.after(0, lambda: messagebox.showinfo(
                "安装成功",
                f"整合包已安装：\n\n{pack_name} v{pack_version}\n\n"
                f"版本目录: {version_dir_name}\n\n"
                f"请到「🚀 启动」页选择该版本启动。"
            ))

        except Exception as e:
            self.log(f"❌ 整合包安装失败: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.root.after(0, lambda: messagebox.showerror(
                "安装失败",
                f"整合包安装失败：\n\n{e}\n\n"
                f"请查看控制台日志了解详情。"
            ))
        finally:
                                            
            try:
                if tmp_dir and tmp_dir.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    self.log("🧹 已清理临时文件")
            except:
                pass

    def _download_vanilla_silent(self, mc_version):
        """静默下载原版（不弹对话框）"""
        r = self.session.get("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=15)
        r.raise_for_status()
        manifest = r.json()
        vdata = next((v for v in manifest['versions'] if v['id'] == mc_version), None)
        if not vdata:
            raise Exception(f"未找到原版 {mc_version}")

        r = self.session.get(vdata['url'], timeout=15)
        r.raise_for_status()
        vjson = r.json()

        vdir = self.version_dir / mc_version
        vdir.mkdir(parents=True, exist_ok=True)
        with open(vdir / f"{mc_version}.json", 'w', encoding='utf-8') as f:
            json.dump(vjson, f, indent=2, ensure_ascii=False)

        if 'downloads' in vjson and 'client' in vjson['downloads']:
            self.download_file(vjson['downloads']['client']['url'], vdir / f"{mc_version}.jar")
        self.download_libraries(vjson)
        self.download_assets(vjson)

    def _merge_version_dir(self, src_dir, dst_dir, new_name):
        """把 src_dir 的所有内容移到 dst_dir，并重命名主 JSON/JAR"""
                                     
        src_name = src_dir.name
        for f in src_dir.iterdir():
            if f.name == f"{src_name}.json":
                         
                new_file = dst_dir / f"{new_name}.json"
                shutil.copy2(f, new_file)
                self.log(f"📝 已复制并重命名: {f.name} → {new_name}.json")
            elif f.name == f"{src_name}.jar":
                new_file = dst_dir / f"{new_name}.jar"
                shutil.copy2(f, new_file)
                self.log(f"📝 已复制并重命名: {f.name} → {new_name}.jar")
            elif f.is_dir():
                shutil.copytree(f, dst_dir / f.name, dirs_exist_ok=True)
            else:
                shutil.copy2(f, dst_dir / f.name)

    def _download_modpack_files(self, files_list, target_dir):
        """下载整合包里所有 files 指定的文件"""
        total = len(files_list)
        done = 0
        failed = []

        def download_one(file_info):
            try:
                path = file_info.get("path", "")
                downloads = file_info.get("downloads", [])
                if not path or not downloads:
                    return
                url = downloads[0]
                save_path = target_dir / path
                save_path.parent.mkdir(parents=True, exist_ok=True)

                r = requests.get(url, stream=True, timeout=120)
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
            except Exception as e:
                failed.append(f"{file_info.get('path', '?')}: {e}")

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(download_one, fi) for fi in files_list]
            for f in as_completed(futures):
                done += 1
                pct = done / total * 100
                self.root.after(0, lambda p=pct: self.file_progress_var.set(p))
                if done % 5 == 0 or done == total:
                    self.log(f"  ⬇️ 进度: {done}/{total}")

        if failed:
            self.log(f"⚠️ 有 {len(failed)} 个文件下载失败:")
            for x in failed[:5]:
                self.log(f"  {x}")
        else:
            self.log(f"✅ 所有 {total} 个文件下载完成")

    def _copy_overrides(self, src_dir, dst_dir):
        """把 overrides/ 里的所有内容复制到目标目录"""
        for item in src_dir.iterdir():
            target = dst_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    def _fix_modpack_version_json(self, target_dir, new_name):
        """修正整合包目录下的 JSON，让启动器能识别"""
                                   
        json_files = [f for f in target_dir.iterdir() if f.suffix == '.json']
        if not json_files:
            return

        json_file = json_files[0]

                                   
        target_json = target_dir / f"{new_name}.json"
        if json_file != target_json:
            shutil.move(str(json_file), str(target_json))
            self.log(f"📝 已重命名: {json_file.name} → {new_name}.json")

                           
        try:
            with open(target_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['id'] = new_name
                                                     
            with open(target_json, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.log(f"✅ 已修正版本 JSON 的 id: {new_name}")
        except Exception as e:
            self.log(f"⚠️ 修正 JSON 失败: {e}", level='warning')

                       
        old_jars = [f for f in target_dir.iterdir() if f.suffix == '.jar']
        for jar in old_jars:
            if jar.name == f"{new_name}.jar":
                continue
                                      
            target_jar = target_dir / f"{new_name}.jar"
            try:
                shutil.move(str(jar), str(target_jar))
                self.log(f"📝 已重命名: {jar.name} → {new_name}.jar")
                break
            except:
                pass
    def _do_download_shader(self, url, save_path, shader_title):
        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            downloaded = 0
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            size_mb = downloaded / 1024 / 1024
            self.log(f"✅ {shader_title} 下载完成: {save_path.name} ({size_mb:.2f} MB)")
            self.root.after(0, lambda: messagebox.showinfo("下载完成", f"{shader_title}\n已保存到:\n{save_path}"))
        except Exception as e:
            self.log(f"❌ 光影下载失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("下载失败", str(e)))
    def search_mods(self):
        query = self.mod_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        loader = self.mod_loader_filter.get()
        mc_ver = self.mod_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.mod_sort_var.get(), "downloads")

        facets = [["project_type:mod"]]
        if loader:
            facets.append([f"categories:{loader}"])
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"query": query, "limit": 30, "index": index,
                  "facets": json.dumps(facets)}
        self.log(f"🔍 搜索 Mod: {query} (加载器={loader}, MC={mc_ver})")
        self.mod_status_label.config(text="搜索中...", foreground="blue")
        threading.Thread(target=self._do_search_mods, args=(params,), daemon=True).start()

    def _do_search_mods(self, params):
        try:
            r = requests.get("https://api.modrinth.com/v2/search", params=params, timeout=114514)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            self.mod_search_results = hits
            self.root.after(0, lambda: self._fill_tree_with_hits(self.mod_tree, hits))
            self.log(f"✅ 找到 {len(hits)} 个 Mod")
            self.root.after(0, lambda: self.mod_status_label.config(
                text=f"找到 {len(hits)} 个结果", foreground="green"))
        except Exception as e:
            self.log(f"❌ 搜索失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("搜索失败", str(e)))
            self.root.after(0, lambda: self.mod_status_label.config(text="搜索失败", foreground="red"))

    def show_mod_versions(self):
        sel = self.mod_tree.selection()
        if not sel or not hasattr(self, 'mod_search_results'):
            messagebox.showwarning("提示", "请先搜索并选中一个 Mod")
            return
        idx = self.mod_tree.index(sel[0])
        mod = self.mod_search_results[idx]
        project_id = mod["project_id"]
        mod_title = mod.get("title", "")
        loader = self.mod_loader_filter.get()
        mc_ver = self.mod_mc_filter.get().strip()
        self.log(f"📋 获取 {mod_title} 的版本列表...")
        self.mod_status_label.config(text=f"获取 {mod_title} 版本...", foreground="blue")
        threading.Thread(target=self._fetch_mod_versions,
                         args=(project_id, loader, mc_ver, mod_title),
                         daemon=True).start()

    def _fetch_mod_versions(self, project_id, loader, mc_ver, mod_title):
        try:
            params = {}
            if loader:
                params["loaders"] = json.dumps([loader])
            if mc_ver:
                params["game_versions"] = json.dumps([mc_ver])

            r = requests.get(
                f"https://api.modrinth.com/v2/project/{project_id}/version",
                params=params,
                timeout=15
            )
            r.raise_for_status()
            versions = r.json()

            if not versions:
                self.log(f"⚠️ {mod_title} 没有匹配的版本")
                self.root.after(0, lambda: messagebox.showwarning(
                    "无匹配版本",
                    f"{mod_title} 没有适配 {loader} / MC {mc_ver} 的版本"
                ))
                return

            self.root.after(0, lambda: self._show_version_dialog(versions, mod_title))
            self.log(f"✅ 找到 {len(versions)} 个版本")
        except Exception as e:
            self.log(f"❌ 获取版本失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("获取失败", str(e)))

    def _show_version_dialog(self, versions, mod_title):
        _p = getattr(self, 'theme_palette', None) or {}
        _bg = _p.get('bg', '#f3f6fa')
        _fg = _p.get('fg', '#18222e')
        _entry = _p.get('entry', '#ffffff')
        _sel = _p.get('select', '#dce8f7')
        _sel_fg = _p.get('select_fg', '#18222e')

        d = tk.Toplevel(self.root)
        d.title(f"选择版本 - {mod_title}")
        d.geometry("700x450")
        d.transient(self.root)
        d.grab_set()
        try:
            d.configure(bg=_bg)
        except Exception:
            pass

        ttk.Label(d, text=f"共 {len(versions)} 个版本，双击直接下载:",
                  font=("Microsoft YaHei", 10)).pack(pady=10)

        lb_frame = ttk.Frame(d)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        lb = tk.Listbox(lb_frame, font=("Consolas", 9),
                        bg=_entry, fg=_fg,
                        selectbackground=_sel, selectforeground=_sel_fg,
                        highlightthickness=0, relief="flat")
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        for v in versions:
            files = v.get("files", [])
            fn = files[0]["filename"] if files else "无文件"
            loaders = ",".join(v.get("loaders", []))
            game_vers = ",".join(v.get("game_versions", [])[:3])
            line = f"{v['version_number']}  |  {loaders}  |  MC {game_vers}  |  {fn}"
            lb.insert(tk.END, line)

        def on_double_click(event=None):
            sel = lb.curselection()
            if not sel:
                return
            ver = versions[sel[0]]
            d.destroy()
            self._download_mod_version(ver, mod_title)

        lb.bind("<Double-Button-1>", on_double_click)

        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="📥 下载此版本",
                   command=on_double_click).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消",
                   command=d.destroy).pack(side=tk.LEFT, padx=5)

    def _download_mod_version(self, version, mod_title):
        target_version = self.get_selected_launch_version()
        if not target_version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return

        files = version.get("files", [])
        if not files:
            messagebox.showerror("错误", "此版本没有可用文件")
            return

        primary = next((f for f in files if f.get("primary")), files[0])
        url = primary["url"]
        filename = primary["filename"]

        mods_dir = self.version_dir / target_version / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        save_path = mods_dir / filename

        self.log(f"📥 下载 Mod: {filename}")
        self.log(f"   目标: {save_path}")
        threading.Thread(
            target=self._do_download_mod,
            args=(url, save_path, mod_title),
            daemon=True
        ).start()

    def _do_download_mod(self, url, save_path, mod_title):
        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0

            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            size_mb = downloaded / 1024 / 1024
            self.log(f"✅ {mod_title} 下载完成: {save_path.name} ({size_mb:.2f} MB)")
            self.root.after(0, lambda: messagebox.showinfo(
                "下载完成",
                f"{mod_title}\n已保存到:\n{save_path}"
            ))
        except Exception as e:
            self.log(f"❌ Mod 下载失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("下载失败", str(e)))

                                                      
    def _filter_local_versions(self, versions):
        """确保原版始终显示（加 [原版] 标签），mod 加载器版本正常显示"""
                                  
        loader_parents = set()
        for v in versions:
            v_lower = v.lower()
            parent = None
            if '-fabric-' in v_lower or '_fabric_' in v_lower:
                parent = re.split(r'[-_]fabric[-_]', v, flags=re.IGNORECASE)[0]
            elif '-neoforge-' in v_lower or '_neoforge_' in v_lower:
                parent = re.split(r'[-_]neoforge[-_]', v, flags=re.IGNORECASE)[0]
            elif '-forge-' in v_lower or '_forge_' in v_lower:
                parent = re.split(r'[-_]forge[-_]', v, flags=re.IGNORECASE)[0]
            if parent:
                loader_parents.add(parent)

                                      
        result = []
        seen = set()

                                   
        for v in versions:
            v_lower = v.lower()
            is_loader = (
                '-fabric-' in v_lower or '_fabric_' in v_lower or
                '-forge-' in v_lower or '_forge_' in v_lower or
                '-neoforge-' in v_lower or '_neoforge_' in v_lower
            )
            if not is_loader:
                if v in loader_parents:
                                             
                    display = f"{v} [原版]"
                    if display not in seen:
                        result.append(display)
                        seen.add(display)
                else:
                    if v not in seen:
                        result.append(v)
                        seen.add(v)

                                         
        existing_vanilla = {v.replace(" [原版]", "") for v in result if " [原版]" in v}
        for parent in loader_parents:
            if parent not in existing_vanilla:
                                       
                if parent not in versions:
                    display = f"{parent} [原版]"
                    if display not in seen:
                        result.append(display)
                        seen.add(display)

                            
        for v in versions:
            v_lower = v.lower()
            is_loader = (
                '-fabric-' in v_lower or '_fabric_' in v_lower or
                '-forge-' in v_lower or '_forge_' in v_lower or
                '-neoforge-' in v_lower or '_neoforge_' in v_lower
            )
            if is_loader and v not in seen:
                result.append(v)
                seen.add(v)

        return result

    def open_mods_dir(self):
        version = self.get_selected_launch_version()
        if not version or version == "--- 暂无本地版本 ---":
            messagebox.showwarning("提示", "请先在「🚀 启动」标签页选择一个版本")
            return
        vdir = self.version_dir / version
        mods_dir = vdir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"📂 打开 Mods 文件夹: {mods_dir}")
        if platform.system() == 'Windows':
            os.startfile(str(mods_dir))
        else:
            subprocess.run(['xdg-open', str(mods_dir)])

    def _get_saves_dir(self, version=None):
        version = version or self.get_selected_launch_version()
        if not version:
            return None
        saves_dir = self.version_dir / version / "saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        return saves_dir

    def _read_world_seed(self, world_path):
        """
        world_path 是存档文件夹路径（不是 level.dat 路径）
        返回种子，失败返回 None
        """
        world_path = Path(world_path)
        
                                                                        
        new_seed_file = world_path / "data" / "minecraft" / "world_gen_settings.dat"
        if new_seed_file.exists():
            try:
                import nbtlib as _nbt
                data = _nbt.load(str(new_seed_file))
                root = data.get("Data") or data.get("data") or data
                if "seed" in root:
                    seed = int(root["seed"])
                    self.log(f"✅ 从 world_gen_settings.dat 读到种子: {seed}")
                    return seed
                else:
                    self.log(f"⚠️ world_gen_settings.dat 里没有 seed 字段", level='warning')
            except Exception as e:
                self.log(f"⚠️ 读取 world_gen_settings.dat 失败: {e}", level='warning')
        
                                                   
        level_dat = world_path / "level.dat"
        if level_dat.exists():
            try:
                import nbtlib as _nbt
                data = _nbt.load(str(level_dat))
                root = data.get("Data", data)
                
                                   
                wgs = root.get("WorldGenSettings")
                if wgs and "seed" in wgs:
                    seed = int(wgs["seed"])
                    self.log(f"✅ 从 level.dat (WorldGenSettings) 读到种子: {seed}")
                    return seed
                
                      
                if "RandomSeed" in root:
                    seed = int(root["RandomSeed"])
                    self.log(f"✅ 从 level.dat (RandomSeed) 读到种子: {seed}")
                    return seed
            except Exception as e:
                self.log(f"⚠️ 读取 level.dat 失败: {e}", level='warning')
        
        self.log("❌ 没有找到种子（26.1+ 请检查 data/minecraft/world_gen_settings.dat）", level='error')
        return None

    def _get_world_meta(self, world_path):
        seed = None
        gamemode = "?"
        level_dat = world_path / "level.dat"
        if level_dat.exists():
            seed = self._read_world_seed(world_path)   # ← 改成 world_path
            try:
                import nbtlib as _nbt
                data = _nbt.load(str(level_dat))
                root = data.get("Data", data)
                gm = root.get("GameType")
                if gm is not None:
                    gamemode = {0: "生存", 1: "创造", 2: "冒险", 3: "旁观"}.get(int(gm), str(gm))
            except Exception:
                pass
        size = sum(f.stat().st_size for f in world_path.rglob("*") if f.is_file())
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(world_path.stat().st_mtime))
        return seed, gamemode, mtime, size / 1024 / 1024
    def _get_world_full_meta(self, world_path: Path):
        """返回详细元数据：种子、模式、难度、游戏时间、玩家坐标、作弊、数据包"""
        import nbtlib as _nbt
        info = {
            "seed": None, "gamemode": "?", "difficulty": "?",
            "hardcore": False, "cheats": False, "day": 0,
            "player_pos": None, "datapacks": [], "icon": None,
            "size_mb": 0.0, "mtime": "",
        }
        level_dat = world_path / "level.dat"
        if level_dat.exists():
            try:
                data = _nbt.load(str(level_dat))
                root = data.get("Data", data)

                wgs = root.get("WorldGenSettings")
                if wgs and "seed" in wgs:
                    info["seed"] = int(wgs["seed"])
                elif "RandomSeed" in root:
                    info["seed"] = int(root["RandomSeed"])

                gm = root.get("GameType")
                if gm is not None:
                    info["gamemode"] = {0: "生存", 1: "创造", 2: "冒险", 3: "旁观"}.get(int(gm), str(gm))

                diff = root.get("Difficulty")
                if diff is not None:
                    info["difficulty"] = {0: "和平", 1: "简单", 2: "普通", 3: "困难"}.get(int(diff), str(diff))

                info["hardcore"] = bool(root.get("hardcore", 0))
                info["cheats"] = bool(root.get("allowCommands", 0))

                day_time = root.get("DayTime")
                if day_time is not None:
                    info["day"] = int(day_time) // 24000

                player = root.get("Player")
                if player and "Pos" in player:
                    pos = player["Pos"]
                    info["player_pos"] = (float(pos[0]), float(pos[1]), float(pos[2]))

                try:
                    dp = root.get("DataPacks")
                    if dp is not None:
                        info["datapacks"] = [str(x) for x in dp.get("Enabled", [])]
                except Exception:
                    pass
            except Exception as e:
                self.log(f"⚠️ 读取 level.dat 失败: {e}", level="warning")

        icon = world_path / "icon.png"
        if icon.exists():
            info["icon"] = icon

        info["size_mb"] = sum(f.stat().st_size for f in world_path.rglob("*") if f.is_file()) / 1024 / 1024
        info["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(world_path.stat().st_mtime))
        return info


    def show_world_detail(self, parent, world_path: Path):
        info = self._get_world_full_meta(world_path)
        d = tk.Toplevel(parent)
        d.title(f"🌍 存档详情 - {world_path.name}")
        d.geometry("560x600")
        d.transient(parent)

              
        top = tk.Frame(d, bg="#2b2b3c", height=180)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        if info["icon"]:
            try:
                img = Image.open(info["icon"])
                img.thumbnail((160, 160))
                photo = ImageTk.PhotoImage(img)
                lbl = tk.Label(top, image=photo, bg="#2b2b3c")
                lbl.image = photo
                lbl.pack(pady=10)
            except Exception:
                pass
        else:
            tk.Label(top, text="🌍 无预览图", font=("Microsoft YaHei", 16),
                    fg="white", bg="#2b2b3c").pack(expand=True)

              
        body = ttk.Frame(d, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        rows = [
            ("📛 名称", world_path.name),
            ("🌱 种子", str(info["seed"]) if info["seed"] is not None else "—"),
            ("🎮 模式", info["gamemode"] + ("  🔥极限" if info["hardcore"] else "")),
            ("⚔️ 难度", info["difficulty"]),
            ("📅 游戏内天数", f"第 {info['day']} 天"),
            ("✅ 作弊", "已开启" if info["cheats"] else "关闭"),
            ("📍 玩家坐标", 
                f"X={info['player_pos'][0]:.1f}  Y={info['player_pos'][1]:.1f}  Z={info['player_pos'][2]:.1f}"
                if info["player_pos"] else "—"),
            ("📦 大小", f"{info['size_mb']:.1f} MB"),
            ("🕒 最后修改", info["mtime"]),
        ]
        for i, (k, v) in enumerate(rows):
            tk.Label(body, text=k, font=("Microsoft YaHei", 10),
                    fg="#666", anchor="w").grid(row=i, column=0, sticky="w", pady=3)
            tk.Label(body, text=v, font=("Microsoft YaHei", 10, "bold"),
                    anchor="w", wraplength=380).grid(row=i, column=1, sticky="w", pady=3, padx=10)
        body.grid_columnconfigure(1, weight=1)

              
        bf = ttk.Frame(d)
        bf.pack(fill=tk.X, padx=12, pady=8)
        ttk.Button(bf, text="🗺️ 打开种子地图",
                command=lambda: self._open_chunkbase_for_world(
                    world_path, world_path.parent.parent.name)).pack(side=tk.LEFT, padx=3)
        ttk.Button(bf, text="📂 打开目录",
                command=lambda: ModConflictDetector._open_dir(world_path)).pack(side=tk.LEFT, padx=3)
        ttk.Button(bf, text="❌ 关闭", command=d.destroy).pack(side=tk.RIGHT, padx=3)
    def open_saves_manager(self):
        version = self.get_selected_launch_version()
        if not version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return
        saves_dir = self._get_saves_dir(version)
        if saves_dir is None:
            return

        d = tk.Toplevel(self.root)
        d.title(f"💾 存档管理 - {version}")
        d.geometry("960x520")
        d.transient(self.root)

        cols = ("name", "seed", "gamemode", "size", "mtime")
        tree = ttk.Treeview(d, columns=cols, show="headings", selectmode="browse")
        headers = {
            "name": ("存档名", 220),
            "seed": ("种子", 160),
            "gamemode": ("模式", 70),
            "size": ("大小", 90),
            "mtime": ("最后修改", 140),
        }
        for c in cols:
            text, w = headers[c]
            tree.heading(c, text=text)
            tree.column(c, width=w, anchor="w" if c in ("name", "seed") else "center")
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))

        status_var = tk.StringVar(value="就绪")
        ttk.Label(d, textvariable=status_var, foreground="gray").pack(anchor=tk.W, padx=10)

        def refresh():
            tree.delete(*tree.get_children())
            worlds = [w for w in saves_dir.iterdir() if w.is_dir()]
            worlds.sort(key=lambda w: w.stat().st_mtime, reverse=True)
            for w in worlds:
                seed, gm, mtime, size_mb = self._get_world_meta(w)
                tree.insert("", "end", values=(
                    w.name,
                    str(seed) if seed is not None else "—",
                    gm,
                    f"{size_mb:.1f} MB",
                    mtime,
                ))
            status_var.set(f"共 {len(worlds)} 个存档")

        def get_selected_world():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("提示", "请先选中一个存档")
                return None
            return tree.item(sel[0])["values"][0]

        def backup_selected():
            name = get_selected_world()
            if not name:
                return
            src = saves_dir / name
            backup_dir = self.version_dir / version / "backups" / "saves"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            out = backup_dir / f"{name}_{ts}.zip"
            try:
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in src.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(src.parent))
                self.log(f"💾 已备份存档: {out}")
                status_var.set(f"✅ 已备份到 {out.name}")
            except Exception as e:
                self.log(f"❌ 备份失败: {e}", level='error')
                messagebox.showerror("备份失败", str(e))

        def restore_backup():
            name = get_selected_world()
            if not name:
                return
            backup_dir = self.version_dir / version / "backups" / "saves"
            if not backup_dir.exists():
                messagebox.showinfo("提示", "该版本还没有备份")
                return
            backups = sorted(backup_dir.glob(f"{name}_*.zip"), reverse=True)
            if not backups:
                messagebox.showinfo("提示", f"「{name}」没有备份")
                return

            rd = tk.Toplevel(d)
            rd.title(f"选择备份 - {name}")
            rd.geometry("520x320")
            rd.transient(d)
            lb = tk.Listbox(rd, font=("Consolas", 9))
            lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            for b in backups:
                size_kb = b.stat().st_size / 1024
                lb.insert(tk.END, f"{b.name}   ({size_kb:.0f} KB)")

            def do_restore():
                sel = lb.curselection()
                if not sel:
                    return
                chosen = backups[sel[0]]
                if not messagebox.askyesno("确认", f"用备份覆盖当前存档「{name}」？\n当前存档会先被自动备份。"):
                    return
                if (saves_dir / name).exists():
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    pre = backup_dir / f"{name}_pre-restore_{ts}.zip"
                    with zipfile.ZipFile(pre, "w", zipfile.ZIP_DEFLATED) as zf:
                        for f in (saves_dir / name).rglob("*"):
                            if f.is_file():
                                zf.write(f, f.relative_to(saves_dir / name))
                shutil.rmtree(saves_dir / name, ignore_errors=True)
                with zipfile.ZipFile(chosen, "r") as zf:
                    zf.extractall(saves_dir)
                self.log(f"♻️ 已还原存档: {name} ← {chosen.name}")
                rd.destroy()
                refresh()

            ttk.Button(rd, text="✅ 还原此备份", command=do_restore).pack(pady=5)

        def delete_selected():
            name = get_selected_world()
            if not name:
                return
            if not messagebox.askyesno("⚠️ 确认删除", f"永久删除存档「{name}」？\n此操作不可恢复！"):
                return
            try:
                shutil.rmtree(saves_dir / name)
                self.log(f"🗑️ 已删除存档: {name}")
                refresh()
            except Exception as e:
                messagebox.showerror("删除失败", str(e))

        def rename_selected():
            name = get_selected_world()
            if not name:
                return
            new_name = simpledialog.askstring("重命名", "新名字:", initialvalue=name, parent=d)
            if not new_name or new_name == name:
                return
            try:
                (saves_dir / name).rename(saves_dir / new_name)
                self.log(f"✏️ 存档重命名: {name} → {new_name}")
                refresh()
            except Exception as e:
                messagebox.showerror("重命名失败", str(e))

        def open_folder():
            name = get_selected_world()
            target = saves_dir / name if name else saves_dir
            if not target.exists():
                return
            if platform.system() == "Windows":
                os.startfile(str(target))
            else:
                subprocess.run(["xdg-open", str(target)])

        def open_seed_map_for_selected():
            name = get_selected_world()
            if not name:
                return
            self._open_chunkbase_for_world(saves_dir / name, version)

        def open_detail_for_selected():
            name = get_selected_world()
            if not name:
                return
            self.show_world_detail(d, saves_dir / name)

        def import_backup():
            path = filedialog.askopenfilename(
                title="选择存档备份 (.zip)",
                filetypes=[("ZIP", "*.zip"), ("所有文件", "*.*")]
            )
            if not path:
                return
            path = Path(path)
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    zf.extractall(saves_dir)
                self.log(f"📥 已导入存档: {path.name}")
                refresh()
            except Exception as e:
                messagebox.showerror("导入失败", str(e))

                                     
        btn = ttk.Frame(d)
        btn.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(btn, text="💾 备份", command=backup_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="♻️ 还原", command=restore_backup).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="📥 导入备份", command=import_backup).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="✏️ 重命名", command=rename_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="🗑️ 删除", command=delete_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="🌍 详情", command=open_detail_for_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="🗺️ 种子地图", command=open_seed_map_for_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="📂 打开目录", command=open_folder).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="🔄 刷新", command=refresh).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="❌ 关闭", command=d.destroy).pack(side=tk.RIGHT, padx=3)

        refresh()
                         
        tree.bind("<Double-Button-1>", lambda e: open_detail_for_selected())

                                                    

    def _open_chunkbase_for_world(self, world_path, mc_version):
        level_dat = world_path / "level.dat"
        seed = self._read_world_seed(world_path) if level_dat.exists() else None

        if seed is None:
            url = "https://www.chunkbase.com/apps/seed-map"
            self.log("⚠️ 未读取到种子，打开空白 Chunkbase 页面")
        else:
                                             
                                            
                                           
                                             
                                          
                             
            match = re.match(
                r'^(\d+\.\d+(?:\.\d+)?(?:-(?:pre|rc|snapshot|alpha|beta)\d*)?)',
                mc_version, re.IGNORECASE
            )
            if match:
                pure_version = match.group(1)
            else:
                pure_version = mc_version

                                            
            if re.match(r'^\d+\.\d+(?:\.\d+)?$', pure_version):
                ver_for_url = pure_version
            else:
                ver_for_url = ".".join(pure_version.split(".")[:2])

            url = (f"https://www.chunkbase.com/apps/seed-map"
                   f"#seed={seed}&platform=java&version={ver_for_url}")
            self.log(f"🗺️ 种子地图: {world_path.name} / 种子={seed} / 版本={ver_for_url}")

        try:
            self.log(f"🗺️ 最终URL: {url}")
            webbrowser.open(url)
        except Exception as e:
            self.log(f"❌ 打开浏览器失败: {e}", level='error')
            messagebox.showerror("打开失败", str(e))

    def open_seed_map(self):
        version = self.get_selected_launch_version()
        if not version:
            messagebox.showwarning("提示", "请先在「启动」页选择一个版本")
            return
        saves_dir = self._get_saves_dir(version)
        if saves_dir is None:
            return
        worlds = [w for w in saves_dir.iterdir() if w.is_dir()]
        if not worlds:
            messagebox.showinfo("提示", f"版本 {version} 还没有存档")
            return
        if len(worlds) == 1:
            self._open_chunkbase_for_world(worlds[0], version)
            return

        d = tk.Toplevel(self.root)
        d.title("选择存档")
        d.geometry("420x360")
        d.transient(self.root)
        d.grab_set()
        lb = tk.Listbox(d, font=("Microsoft YaHei", 10))
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        worlds_sorted = sorted(worlds, key=lambda w: w.stat().st_mtime, reverse=True)
        for w in worlds_sorted:
            lb.insert(tk.END, w.name)

        def go():
            sel = lb.curselection()
            if not sel:
                return
            chosen = worlds_sorted[sel[0]]
            d.destroy()
            self._open_chunkbase_for_world(chosen, version)

        ttk.Button(d, text="🗺️ 打开种子地图", command=go).pack(pady=5)
        lb.bind("<Double-Button-1>", lambda e: go())

                                                    

    def _auto_backup_worlds(self, version):
        try:
            saves_dir = self.version_dir / version / "saves"
            if not saves_dir.exists():
                return
            worlds = [w for w in saves_dir.iterdir() if w.is_dir()]
            if not worlds:
                return
            max_keep = 5
            backup_dir = self.version_dir / version / "backups" / "saves"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            for world in worlds:
                out = backup_dir / f"{world.name}_{ts}.zip"
                try:
                    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                        for f in world.rglob("*"):
                            if f.is_file():
                                zf.write(f, f.relative_to(saves_dir))
                except Exception as e:
                    self.log(f"⚠️ 自动备份「{world.name}」失败: {e}", level='warning')
                    continue
                old = sorted(backup_dir.glob(f"{world.name}_*.zip"), reverse=True)
                for f in old[max_keep:]:
                    try:
                        f.unlink()
                    except Exception:
                        pass
            self.log(f"💾 已自动备份 {len(worlds)} 个存档（保留最近 {max_keep} 个）")
        except Exception as e:
            self.log(f"⚠️ 自动备份失败: {e}", level='warning')
                                                       
    def open_console_window(self):
        """打开（或聚焦）独立控制台窗口"""
                 
        if self.console_window is not None and self.console_window.winfo_exists():
            self.console_window.deiconify()
            self.console_window.lift()
            self.console_window.focus_force()
            return

        self.console_window = tk.Toplevel(self.root)
        self.console_window.title("控制台输出 - Minecraft启动器")
        self.console_window.geometry("900x600")
        self.console_window.minsize(500, 300)
                            
        self.console_window.protocol("WM_DELETE_WINDOW", self._hide_console_window)

        frame = ttk.Frame(self.console_window, padding="5")
        frame.pack(fill=tk.BOTH, expand=True)

        self.console_text = tk.Text(frame, wrap=tk.WORD, font=('TkFixedFont', 10))
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.console_text.config(state=tk.DISABLED)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.console_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.console_text.config(yscrollcommand=scroll.set)
        btn_frame = ttk.Frame(self.console_window)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="🗑️ 清空", command=self.clear_console).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="📋 复制全部",
                   command=self._copy_console_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="⬇️ 滚动到底部",
                   command=lambda: self.console_text.see(tk.END)).pack(side=tk.LEFT, padx=5)

                            
        self.setup_log_tags()

    def _hide_console_window(self):
        """隐藏控制台窗口（不销毁）"""
        if self.console_window is not None and self.console_window.winfo_exists():
            self.console_window.withdraw()

    def _copy_console_all(self):
        """把控制台全部内容复制到剪贴板"""
        if self.console_text is None:
            return
        try:
            content = self.console_text.get("1.0", tk.END)
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.log("📋 控制台内容已复制到剪贴板")
        except Exception as e:
            self.log(f"⚠️ 复制失败: {e}")
    def setup_log_tags(self):
        if self.console_text is None:
            return
        try:
            for tag in self.console_text.tag_names():
                self.console_text.tag_delete(tag)

            self.console_text.tag_config('log_debug', foreground='#888888')
            self.console_text.tag_config('log_info', foreground="#020101")
            self.console_text.tag_config('log_warning', foreground="#FBFF00")
            self.console_text.tag_config('log_error', foreground="#FF0000")
            self.console_text.tag_config(
                'log_crash',
                foreground="#740000",
                underline=True,
                font=('TkFixedFont', 10, 'bold')
            )
            self.console_text.tag_config('log_normal', foreground='#000000')
        except Exception as e:
            print(f"配置日志标签失败: {e}")

    def get_offline_uuid(self, username):
        hash_bytes = hashlib.md5(f"OfflinePlayer:{username}".encode('utf-8')).digest()
        hash_array = bytearray(hash_bytes)
        hash_array[6] = (hash_array[6] & 0x0f) | 0x30
        hash_array[8] = (hash_array[8] & 0x3f) | 0x80
        return str(uuid_lib.UUID(bytes=bytes(hash_array)))
    def __init__(self):
        self.base_dir = Path.home() / ".minecraft"
        self.config_file = Path.home() / ".minecraft_launcher_config.json"
        
        self.java_path = "java"
        self.username = "Player"
        self.login_mode_value = "offline"
        self.demo_mode_value = False
        self.authlib_enabled_value = False
        self.auto_chinese_value = True
        self.custom_jvm_args_value = "-XX:+UseG1GC -Dfile.encoding=UTF-8"                
        
        self.load_config()
        self.ensure_directories()
        
        self.versions = []
        self.is_downloading = False
        self.is_launching = False
        self.console_window = None            
        self.console_text = None                      
        self.tray_icon = None               
        self.log_queue = Queue()
        self.update_queue = Queue()
        self._save_after_id = None
        self._lazy_pages = set()
        self.download_stats = None
        self.session = requests.Session()
        
        self.release_versions = []
        self.snapshot_versions = []
        self.local_versions = []
        self.april_fools_versions = []
        self.all_versions = []
        
        self.client_manager = ClientIDManager(log_callback=self.log)
        self.client_id = self.client_manager.get_client_id()
        
        self.auth = MinecraftAuth(self.client_id, log_callback=self.log)
        self.auth_status_value = "离线模式"
        
        self.authlib_manager = AuthlibInjectorManager(self.base_dir, log_callback=self.log)
        self.mod_manager = ModLoaderManager(self.base_dir, log_callback=self.log)
        self.news_manager = MinecraftNewsManager(log_callback=self.log)
        
        self.crash_analyzer = CrashAnalyzer()
        
                                        
        self.root = tk.Tk()
        self.root.title("Minecraft启动器")
                                                    
        if platform.system() == 'Windows':
            self.root.state('zoomed')
        else:
            self.root.attributes('-fullscreen', True)
        self.root.minsize(800, 750)
        self.root.option_add('*Text.font', 'TkFixedFont')
        self.auth.ui_root = self.root

                            
        self.theme_var = tk.StringVar(value=self.theme_value)
        self.apply_theme(self.theme_value, save=False)

        
                                                
        self.login_mode = tk.StringVar(value=self.login_mode_value)
        self.auth_status = tk.StringVar(value=self.auth_status_value)
        self.username_var = tk.StringVar(value=self.username)
        self.demo_mode = tk.BooleanVar(value=self.demo_mode_value)
        self.authlib_enabled = tk.BooleanVar(value=self.authlib_enabled_value)
        self.auto_chinese = tk.BooleanVar(value=self.auto_chinese_value)
        
                             
        self.setup_ui()
        self.setup_log_tags()
                             
        self._save_after_id = None
        for _attr in (
            "login_method_var", "login_mode", "auto_backup_var", "auto_chinese",
            "authlib_server_var", "java_path_var", "memory_var",
            "jvm_preset_var", "custom_jvm_args_var", "theme_mode_var",
            "use_all_cores_var", "gpu_acceleration_var", "render_backend_var",
            "launcher_render_backend_var", "username_var", "dir_var",
        ):
            _var = getattr(self, _attr, None)
            if _var is not None:
                try:
                    _var.trace_add("write", lambda *a: self.schedule_save_config())
                except Exception:
                    pass 
        self.start_queue_processor()
        if getattr(self, "launcher_render_backend_value", "默认") != "默认":
            try:
                self.launcher_render_status.config(
                    text=f"{self.launcher_render_backend_value} · 正在初始化..."
                )
                self.root.after(
                    1200,
                    lambda: threading.Thread(
                        target=self._probe_launcher_gpu,
                        args=(self.launcher_render_backend_value,),
                        daemon=True,
                    ).start(),
                )
            except Exception:
                pass
        
        self.demo_check.config(variable=self.demo_mode)
        self.authlib_check.config(variable=self.authlib_enabled)
        self.auth_status_label.config(textvariable=self.auth_status)
        self.username_entry.config(textvariable=self.username_var)
        self.offline_radio.config(variable=self.login_mode)
        self.microsoft_radio.config(variable=self.login_mode)
        
        self.demo_mode.trace('w', self.on_demo_mode_changed)
        self.authlib_enabled.trace('w', self.on_authlib_changed)
        
        if self.auth.is_authenticated():
            self.auth_status.set(f"已登录: {self.auth.username}")
            self.login_mode.set("microsoft")
            self.login_mode_value = "microsoft"
            self.username_var.set(self.auth.username)
        
        self.update_login_display()
        self.update_auth_info()

                               
        self.root.after(300, self.refresh_versions)

        self._history = LaunchHistory(Path.home() / ".minecraft_launcher")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def ensure_directories(self):
        self.version_dir = self.base_dir / "versions"
        self.assets_dir = self.base_dir / "assets"
        self.libraries_dir = self.base_dir / "libraries"
        for d in [self.base_dir, self.version_dir, self.assets_dir, self.libraries_dir]:
            d.mkdir(parents=True, exist_ok=True)
    
    def load_config(self):
             
        defaults = {
            'auto_backup': True,
            'minecraft_dir': str(Path.home() / ".minecraft"),
            'java_path': "java",
            'username': "Player",
            'login_mode': 'offline',
            'demo_mode': False,
            'authlib_enabled': False,
            'authlib_server': "",
            'auto_chinese': True,
            'custom_jvm_args': "-XX:+UseG1GC -Dfile.encoding=UTF-8",
            'memory_mb': "2048",
            'jvm_preset': "默认",
            'window_width': 854,
            'window_height': 480,
            'last_launch_version': "",
            'last_download_category': "正式版",
            'last_download_version': "",
            'news_filter': "all",
            'login_method': "auth_code",
            'use_all_cores': True,
            'gpu_acceleration': True,
            'render_backend': "OpenGL",
            'launcher_render_backend': "默认",
            'theme': 'light',
        }

        config = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except:
                config = {}

        def get(key):
            return config.get(key, defaults[key])

        self.base_dir = Path(get('minecraft_dir'))
        self.java_path = get('java_path')
        self.username = get('username')
        self.login_mode_value = get('login_mode')
        self.demo_mode_value = get('demo_mode')
        self.authlib_enabled_value = get('authlib_enabled')
        self.authlib_server_value = get('authlib_server')
        self.auto_chinese_value = get('auto_chinese')
        self.custom_jvm_args_value = get('custom_jvm_args')
        self.memory_value = get('memory_mb')
        self.jvm_preset_value = get('jvm_preset')
        try:
            self.window_width = max(320, min(7680, int(get('window_width'))))
            self.window_height = max(240, min(4320, int(get('window_height'))))
        except (TypeError, ValueError):
            self.window_width, self.window_height = 854, 480
        self.last_launch_version = get('last_launch_version')
        self.last_download_category = get('last_download_category')
        self.last_download_version = get('last_download_version')
        self.news_filter_value = get('news_filter')
        self.theme_value = get('theme')
        self.login_method_value = get('login_method')
        self.use_all_cores_value = get('use_all_cores')
        self.gpu_acceleration_value = get('gpu_acceleration')
        self.render_backend_value = get('render_backend')
        self.launcher_render_backend_value = get('launcher_render_backend')
        self.auto_backup_value = get('auto_backup')
        if self.theme_value not in ('light', 'dark'):
            self.theme_value = 'light'
        if self.launcher_render_backend_value not in ('默认', 'OpenGL', 'Vulkan', 'DirectX 12'):
            self.launcher_render_backend_value = '默认'
    
    def schedule_save_config(self):
        try:
            if self._save_after_id is not None:
                self.root.after_cancel(self._save_after_id)
        except Exception:
            pass
        try:
            self._save_after_id = self.root.after(600, self._flush_save_config)
        except Exception:
            self._save_after_id = None

    def _flush_save_config(self):
        self._save_after_id = None
        self.save_config()

    def save_config(self):
                              
        config = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except:
                config = {}

                         
        config.update({
            'auto_backup': self.auto_backup_var.get() if hasattr(self, 'auto_backup_var') else True,
            'render_backend': self.render_backend_var.get() if hasattr(self, 'render_backend_var') else getattr(self, 'render_backend_value', 'OpenGL'),
            'launcher_render_backend': self.launcher_render_backend_var.get() if hasattr(self, 'launcher_render_backend_var') else getattr(self, 'launcher_render_backend_value', '默认'),
            'minecraft_dir': str(self.base_dir),
            'java_path': self.java_path_var.get() if hasattr(self, 'java_path_var') else self.java_path,
            'username': self.username_var.get(),
            'login_mode': self.login_mode.get(),
            'demo_mode': self.demo_mode.get(),
            'authlib_enabled': self.authlib_enabled.get(),
            'authlib_server': self.authlib_server_var.get() if hasattr(self, 'authlib_server_var') else "",
            'login_method': getattr(self, 'login_method_var', tk.StringVar(value="auth_code")).get() if hasattr(self, 'login_method_var') else getattr(self, 'login_method_value', "auth_code"),
            'auto_chinese': self.auto_chinese.get() if hasattr(self, 'auto_chinese') else True,
            'custom_jvm_args': self.custom_jvm_args_var.get() if hasattr(self, 'custom_jvm_args_var') else "",
            'use_all_cores': getattr(self, 'use_all_cores_var', tk.BooleanVar(value=True)).get() if hasattr(self, 'use_all_cores_var') else getattr(self, 'use_all_cores_value', True),
            'gpu_acceleration': getattr(self, 'gpu_acceleration_var', tk.BooleanVar(value=True)).get() if hasattr(self, 'gpu_acceleration_var') else getattr(self, 'gpu_acceleration_value', True),
            'memory_mb': self.memory_var.get() if hasattr(self, 'memory_var') else "2048",
            'jvm_preset': self.jvm_preset_var.get() if hasattr(self, 'jvm_preset_var') else "默认",
            'window_width': getattr(self, 'window_width', 854),
            'window_height': getattr(self, 'window_height', 480),
            'last_launch_version': self.launch_version_var.get() if hasattr(self, 'launch_version_var') else "",
            'last_download_category': self._current_download_category(),
            'last_download_version': self.download_version_var.get() if hasattr(self, 'download_version_var') else "",
            'news_filter': self.news_type_var.get() if hasattr(self, 'news_type_var') else "all",
            'theme': self.theme_var.get() if hasattr(self, 'theme_var') else getattr(self, 'theme_value', 'light'),
        })
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    def _current_download_category(self):
        """安全读取当前下载分类（无控件或未选中时返回默认值）"""
        try:
            if hasattr(self, 'download_cat_list'):
                sel = self.download_cat_list.curselection()
                if sel:
                    return self.download_cat_list.get(sel[0])
        except Exception:
            pass
        return "正式版"

    def get_game_window_size(self):
        try:
            width = int(getattr(self, 'window_width', 854))
            height = int(getattr(self, 'window_height', 480))
        except (TypeError, ValueError):
            width, height = 854, 480
        width = max(320, min(7680, width))
        height = max(240, min(4320, height))
        return width, height

    def _on_window_size_change(self, *args):
        try:
            width = int(self.window_width_var.get())
            height = int(self.window_height_var.get())
        except (ValueError, AttributeError):
            return
        if 320 <= width <= 7680 and 240 <= height <= 4320:
            self.window_width = width
            self.window_height = height
            preset = "自定义"
            for name, size in getattr(self, '_window_presets', {}).items():
                if size == (width, height):
                    preset = name
                    break
            if hasattr(self, 'window_preset_var'):
                self.window_preset_var.set(preset)
            self.schedule_save_config()

    def _normalize_window_size(self, event=None):
        width, height = self.get_game_window_size()
        try:
            width = max(320, min(7680, int(self.window_width_var.get())))
            height = max(240, min(4320, int(self.window_height_var.get())))
        except (ValueError, AttributeError):
            pass
        self.window_width = width
        self.window_height = height
        self.window_width_var.set(str(width))
        self.window_height_var.set(str(height))
        self.schedule_save_config()

    def _apply_window_preset(self, event=None):
        size = getattr(self, '_window_presets', {}).get(self.window_preset_var.get())
        if not size:
            return
        self.window_width_var.set(str(size[0]))
        self.window_height_var.set(str(size[1]))
        self._normalize_window_size()


    def apply_theme(self, mode=None, save=True):
        """应用浅色/深色主题（ttk + Tk 默认颜色）"""
        if mode is not None:
            self.theme_var.set(mode)
        mode = self.theme_var.get() if hasattr(self, 'theme_var') else getattr(self, 'theme_value', 'light')
        # system 模式：实时读系统主题
        if str(mode).lower() in ('system', 'auto', 'follow'):
            try:
                mode = _detect_system_theme()
            except Exception:
                mode = 'light'
                                     
        if mode in ('black', 'dark'):
            mode = 'dark'
        elif mode in ('white', 'light'):
            mode = 'light'
        if mode not in ('light', 'dark'):
            mode = 'light'
        self.theme_value = mode
        if not hasattr(self, 'theme_var'):
            self.theme_var = tk.StringVar(value=mode)
        else:
            self.theme_var.set(mode)

                                                    
        if mode in ('dark', 'black'):
            mode = 'dark'
        elif mode in ('light', 'white'):
            mode = 'light'

        if mode not in ('light', 'dark'):
            mode = 'light'

        if mode == 'dark':
            palette = {
                'bg': '#0b0f14',
                'fg': '#e8eef6',
                'surface': '#141b23',
                'surface_alt': '#1e2936',
                'accent': '#f2b84b',
                'accent_hover': '#ffd166',
                'accent_fg': '#1a1408',
                'secondary': '#93a4b8',
                'entry': '#0f1620',
                'select': '#2b3d52',
                'select_fg': '#ffffff',
                'border': '#263241',
                'heading': '#f2c86b',
                'danger': '#f07171',
                'success': '#4fd1a1',
                'hover': '#223041',
            }
        else:
            palette = {
                'bg': '#f3f6fa',
                'fg': '#18222e',
                'surface': '#ffffff',
                'surface_alt': '#e8eef6',
                'accent': '#c98a1f',
                'accent_hover': '#b57a12',
                'accent_fg': '#1a1408',
                'secondary': '#64748b',
                'entry': '#ffffff',
                'select': '#dce8f7',
                'select_fg': '#18222e',
                'border': '#d6dfea',
                'heading': '#8a5a00',
                'danger': '#c2410c',
                'success': '#15803d',
                'hover': '#eef3f9',
            }
        self.theme_palette = palette
        p = palette
        font_ui = ("Microsoft YaHei UI", 10)
        font_bold = ("Microsoft YaHei UI", 10, "bold")
        font_title = ("Microsoft YaHei UI", 11, "bold")

        try:
            style = ttk.Style(self.root)
            style.theme_use('clam')
        except Exception:
            style = ttk.Style(self.root)

        style.configure('.', background=p['bg'], foreground=p['fg'],
                        fieldbackground=p['entry'], troughcolor=p['surface_alt'],
                        selectbackground=p['select'], selectforeground=p['select_fg'],
                        bordercolor=p['border'], darkcolor=p['border'],
                        lightcolor=p['border'], font=font_ui)
        style.configure('TFrame', background=p['bg'], borderwidth=0)
        style.configure('TLabelframe', background=p['bg'], foreground=p['fg'],
                        bordercolor=p['border'], relief='solid', borderwidth=1, padding=8)
        style.configure('TLabelframe.Label', background=p['bg'], foreground=p['heading'],
                        font=font_bold)
        style.configure('TLabel', background=p['bg'], foreground=p['fg'], font=font_ui)
        style.configure('Muted.TLabel', background=p['bg'], foreground=p['secondary'], font=font_ui)
        style.configure('Accent.TLabel', background=p['bg'], foreground=p['accent'], font=font_bold)
        style.configure('Success.TLabel', background=p['bg'], foreground=p['success'], font=font_ui)
        style.configure('Danger.TLabel', background=p['bg'], foreground=p['danger'], font=font_ui)
        style.configure('TButton', background=p['surface'], foreground=p['fg'],
                        bordercolor=p['border'], focuscolor=p['surface'], focusthickness=0,
                        relief='flat', borderwidth=1, padding=(12, 6), font=font_ui)
        style.map('TButton',
                  background=[('disabled', p['surface']), ('pressed', p['select']),
                              ('active', p['hover'])],
                  foreground=[('disabled', p['secondary']), ('active', p['fg'])],
                  bordercolor=[('active', p['accent'])])
        style.configure('Accent.TButton', background=p['accent'], foreground=p['accent_fg'],
                        bordercolor=p['accent'], focuscolor=p['accent'], focusthickness=0,
                        relief='flat', borderwidth=1, padding=(16, 7), font=font_bold)
        style.map('Accent.TButton',
                  background=[('disabled', p['surface_alt']), ('pressed', p['accent_hover']),
                              ('active', p['accent_hover'])],
                  foreground=[('disabled', p['secondary']), ('active', p['accent_fg'])],
                  bordercolor=[('active', p['accent_hover'])])
        style.configure('TEntry', fieldbackground=p['entry'], foreground=p['fg'],
                        bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'],
                        insertcolor=p['fg'], padding=(8, 6), relief='flat')
        style.map('TEntry', bordercolor=[('focus', p['accent'])],
                  lightcolor=[('focus', p['accent'])], darkcolor=[('focus', p['accent'])])
        style.configure('TCombobox', fieldbackground=p['entry'], foreground=p['fg'],
                        background=p['surface'], bordercolor=p['border'],
                        arrowcolor=p['fg'], padding=(8, 4), relief='flat')
        style.map('TCombobox', fieldbackground=[('readonly', p['entry'])],
                  selectbackground=[('readonly', p['select'])],
                  selectforeground=[('readonly', p['select_fg'])],
                  bordercolor=[('focus', p['accent']), ('active', p['border'])],
                  arrowcolor=[('active', p['accent'])])
        style.configure('TCheckbutton', background=p['bg'], foreground=p['fg'],
                        indicatorcolor=p['entry'], indicatorforeground=p['accent'],
                        indicatormargin=(0, 0, 8, 0), font=font_ui)
        style.map('TCheckbutton',
                  background=[('active', p['bg'])],
                  indicatorcolor=[('selected', p['accent']), ('pressed', p['accent'])],
                  indicatorforeground=[('selected', p['accent_fg'])])
        style.configure('TRadiobutton', background=p['bg'], foreground=p['fg'],
                        indicatorcolor=p['entry'], indicatorforeground=p['accent'],
                        indicatormargin=(0, 0, 8, 0), font=font_ui)
        style.map('TRadiobutton',
                  background=[('active', p['bg'])],
                  indicatorcolor=[('selected', p['accent']), ('pressed', p['accent'])],
                  indicatorforeground=[('selected', p['accent_fg'])])
        style.configure('TNotebook', background=p['bg'], borderwidth=0,
                        tabmargins=[2, 8, 2, 0])
        style.configure('TNotebook.Tab', background=p['surface'], foreground=p['secondary'],
                        padding=[22, 9], borderwidth=0, font=font_bold)
        style.map('TNotebook.Tab',
                  background=[('selected', p['accent']), ('active', p['surface_alt']),
                              ('disabled', p['surface'])],
                  foreground=[('selected', p['accent_fg']), ('active', p['fg']),
                              ('disabled', p['secondary'])])
        style.configure('Treeview', background=p['surface'], foreground=p['fg'],
                        fieldbackground=p['surface'], bordercolor=p['border'],
                        rowheight=28, borderwidth=0, relief='flat', font=font_ui)
        style.configure('Treeview.Heading', background=p['surface_alt'], foreground=p['fg'],
                        relief='flat', borderwidth=0, padding=[10, 8], font=font_bold)
        style.map('Treeview', background=[('selected', p['select'])],
                  foreground=[('selected', p['select_fg'])],
                  bordercolor=[('focus', p['accent'])])
        style.map('Treeview.Heading', background=[('active', p['hover'])])
        style.configure('TScrollbar', background=p['surface_alt'], troughcolor=p['bg'],
                        bordercolor=p['bg'], arrowcolor=p['secondary'],
                        arrowsize=12, width=12, relief='flat')
        style.map('TScrollbar', background=[('active', p['accent'])])
        style.configure('TProgressbar', background=p['accent'], troughcolor=p['surface_alt'],
                        bordercolor=p['surface_alt'], lightcolor=p['accent'],
                        darkcolor=p['accent'], thickness=12, borderwidth=0)
        style.configure('TSeparator', background=p['border'])
        style.configure('TScale', background=p['bg'], troughcolor=p['surface_alt'],
                        bordercolor=p['border'])

        try:
            self.root.configure(bg=p['bg'])
        except Exception:
            pass
        for _t in getattr(self, '_theme_trees', []):
            try:
                _t.tag_configure('oddrow', background=p['surface_alt'], foreground=p['fg'])
                _t.tag_configure('evenrow', background=p['surface'], foreground=p['fg'])
            except Exception:
                pass
        for key, val in (
            ('*Background', p['bg']),
            ('*Foreground', p['fg']),
            ('*Font', font_ui),
            ('*Text.background', p['surface']),
            ('*Text.foreground', p['fg']),
            ('*Text.insertBackground', p['fg']),
            ('*Text.selectBackground', p['select']),
            ('*Text.selectForeground', p['select_fg']),
            ('*Listbox.background', p['surface']),
            ('*Listbox.foreground', p['fg']),
            ('*Listbox.selectBackground', p['select']),
            ('*Listbox.selectForeground', p['select_fg']),
            ('*Listbox.highlightThickness', 1),
            ('*Listbox.highlightBackground', p['border']),
            ('*Canvas.background', p['bg']),
            ('*Entry.background', p['entry']),
            ('*Entry.foreground', p['fg']),
            ('*Menu.background', p['surface']),
            ('*Menu.foreground', p['fg']),
            ('*Menu.activeBackground', p['accent']),
            ('*Menu.activeForeground', p['accent_fg']),
            ('*TCombobox*Listbox.background', p['surface']),
            ('*TCombobox*Listbox.foreground', p['fg']),
            ('*TCombobox*Listbox.selectBackground', p['select']),
            ('*TCombobox*Listbox.selectForeground', p['select_fg']),
        ):
            try:
                self.root.option_add(key, val)
            except Exception:
                pass
        self._restyle_tk_widgets(p)

        if save:
            try:
                self.save_config()
            except Exception:
                pass

    def _restyle_tk_widgets(self, p):
        def walk(widget):
            if widget is not self.root and isinstance(widget, tk.Toplevel):
                return
            try:
                cls = widget.winfo_class()
                if cls == 'Listbox':
                    widget.configure(background=p['surface'], foreground=p['fg'],
                                     selectbackground=p['select'],
                                     selectforeground=p['select_fg'],
                                     highlightbackground=p['border'],
                                     highlightcolor=p['accent'],
                                     borderwidth=0, highlightthickness=1)
                elif cls == 'Text':
                    widget.configure(background=p['surface'], foreground=p['fg'],
                                     insertbackground=p['fg'],
                                     selectbackground=p['select'],
                                     selectforeground=p['select_fg'],
                                     highlightbackground=p['border'],
                                     highlightcolor=p['accent'],
                                     borderwidth=0, highlightthickness=1)
                elif cls == 'Canvas':
                    widget.configure(background=p['bg'], highlightthickness=0)
                elif cls in ('Frame', 'Labelframe'):
                    widget.configure(background=p['bg'])
                elif cls == 'Label':
                    widget.configure(background=p['bg'])
                    fg = str(widget.cget('foreground')).lower()
                    if fg in ('gray', 'grey', '#666', '#666666', '#555555'):
                        widget.configure(foreground=p['secondary'])
                    elif fg in ('green', '#15803d'):
                        widget.configure(foreground=p['success'])
                    elif fg in ('blue', '#1976d2'):
                        widget.configure(foreground=p['accent'])
                    elif fg in ('red', '#c2410c'):
                        widget.configure(foreground=p['danger'])
                    else:
                        widget.configure(foreground=p['fg'])
            except Exception:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(self.root)

    def on_theme_change(self, event=None):
        """设置页触发：切换主题（支持 follow/system）"""
        display = getattr(self, 'theme_mode_var', None)
        display = display.get() if display is not None else None
        mode = self._theme_display_map.get(display, self.theme_value) if hasattr(self, '_theme_display_map') else self.theme_value
        is_system = (str(mode).lower() in ('system', 'auto', 'follow'))
        if not is_system and mode not in ('light', 'dark'):
            mode = 'dark' if (display and 'black' in str(display).lower()) else 'light'
        self.theme_var.set(mode)
        self.theme_value = mode
        self.apply_theme(mode, save=True)
        try:
            if is_system:
                real = _detect_system_theme()
                self.log(f"已切换为跟随系统（当前: {'黑色' if real == 'dark' else '白色'}）")
            else:
                self.log(f"已切换为{'黑色' if mode == 'dark' else '白色'}主题")
        except Exception:
            pass

    def toggle_theme(self):
        """快速在黑/白主题间切换"""
        new_mode = 'dark' if getattr(self, 'theme_value', 'light') != 'dark' else 'light'
        if hasattr(self, 'theme_mode_var'):
            self.theme_mode_var.set("black (黑色)" if new_mode == 'dark' else "white (白色)")
        self.on_theme_change()

    def on_launcher_renderer_change(self, event=None):
        mode = self.launcher_render_backend_var.get()
        self.launcher_render_backend_value = mode
        self.save_config()
        if mode == "默认":
            self.launcher_render_status.config(text="默认使用 Tk 渲染（兼容性最好）")
        else:
            self.launcher_render_status.config(text=f"正在检测 {mode} ...")
        if hasattr(self, "banner"):
            self.banner.set_render_backend(mode)
        if mode != "默认":
            threading.Thread(
                target=self._probe_launcher_gpu, args=(mode,), daemon=True
            ).start()

    def _probe_launcher_gpu(self, mode):
        try:
            report = LauncherGPURenderer.probe()
        except Exception as exc:
            report = {}
            startup_error = str(exc)
        else:
            startup_error = ""
        info = report.get(mode)

        def apply():
            if not hasattr(self, "launcher_render_status"):
                return
            if info:
                self.launcher_render_status.config(
                    text=f"{mode} · {info['device']}"
                )
                return
            self._auto_fallback_renderer(
                mode, startup_error or "当前设备没有可用的适配器"
            )

        try:
            self.root.after(0, apply)
        except Exception:
            pass

    def _auto_fallback_renderer(self, mode, reason=""):
        self.launcher_render_backend_value = "默认"
        if hasattr(self, "launcher_render_backend_var"):
            self.launcher_render_backend_var.set("默认")
        if hasattr(self, "launcher_render_status"):
            self.launcher_render_status.config(
                text=f"{mode} 不兼容，已自动切换到默认渲染"
            )
        if hasattr(self, "banner"):
            self.banner.set_render_backend("默认")
        self.log(f"⚠️ {mode} 启动器渲染不可用，已自动切换到默认渲染" + (f"：{reason}" if reason else ""))
        try:
            self.save_config()
        except Exception:
            pass

    def _on_banner_gpu_fallback(self, backend, exc):
        reason = str(exc)
        try:
            self.root.after(0, lambda: self._auto_fallback_renderer(backend, reason))
        except Exception:
            pass

    def start_queue_processor(self):
        def process():
            while True:
                messages = []
                try:
                    for _ in range(300):
                        item = self.log_queue.get_nowait()
                        if isinstance(item, tuple):
                            msg, level = item
                        else:
                            msg, level = item, 'info'
                        if isinstance(msg, bytes):
                            msg = msg.decode('utf-8', errors='replace')
                        messages.append((str(msg), level))
                except queue.Empty:
                    pass

                if messages:
                    cw = getattr(self, 'console_window', None)
                    ct = getattr(self, 'console_text', None)
                    if cw is not None and ct is not None:
                        try:
                            if cw.winfo_exists():
                                tag_map = {
                                    'debug': 'log_debug',
                                    'info': 'log_info',
                                    'warning': 'log_warning',
                                    'error': 'log_error',
                                    'crash': 'log_crash',
                                }
                                ct.config(state=tk.NORMAL)
                                for msg, level in messages:
                                    ct.insert(tk.END, f"{msg}\n", tag_map.get(level, 'log_info'))
                                line_count = int(ct.index('end-1c').split('.')[0])
                                if line_count > 4000:
                                    ct.delete('1.0', f'{line_count - 4000}.0')
                                ct.see(tk.END)
                                ct.config(state=tk.DISABLED)
                        except Exception:
                            pass

                updates = {}
                try:
                    for _ in range(100):
                        updates.update(self.update_queue.get_nowait())
                except queue.Empty:
                    pass
                if updates:
                    try:
                        if 'total' in updates and hasattr(self, 'total_progress_var'):
                            self.total_progress_var.set(updates['total'])
                        if 'file' in updates and hasattr(self, 'file_progress_var'):
                            self.file_progress_var.set(updates['file'])
                        if 'file_name' in updates and hasattr(self, 'file_label'):
                            self.file_label.config(text=updates['file_name'])
                        if 'status' in updates and hasattr(self, 'progress_label'):
                            self.progress_label.config(text=updates['status'])
                    except Exception:
                        pass

                time.sleep(0.04 if messages else 0.15)
        threading.Thread(target=process, daemon=True).start()


    def log(self, msg, level=None):
        """
        写入日志到队列
        level: 'debug' / 'info' / 'warning' / 'error' / 'crash' / None
        如果不传 level，会根据内容自动识别
        """
        if isinstance(msg, bytes):
            try:
                msg = msg.decode('utf-8', errors='replace')
            except:
                msg = str(msg)

                              
        if level is None:
            msg_lower = msg.lower()
            if '💀' in msg or 'Minecraft出现问题' in msg or '崩溃' in msg:
                level = 'crash'
            elif '[error]' in msg_lower or '❌' in msg or 'error' in msg_lower:
                level = 'error'
            elif '[warn]' in msg_lower or '⚠️' in msg or 'warning' in msg_lower:
                level = 'warning'
            elif '[debug]' in msg_lower or 'debug' in msg_lower:
                level = 'debug'
            else:
                level = 'info'

                             
        self.log_queue.put((msg, level))
    
    def update_progress(self, total=None, file=None, file_name=None, status=None):
        data = {}
        if total is not None: data['total'] = total
        if file is not None: data['file'] = file
        if file_name is not None: data['file_name'] = file_name
        if status is not None: data['status'] = status
        self.update_queue.put(data)
    
    def setup_ui(self):
                                  
        self.banner = AnimatedGradientBanner(
            self.root,
            height=92,
            color_left=(247, 201, 72),
            color_right=(255, 224, 138),
            title="⛏ Minecraft 启动器",
            subtitle="多版本管理 · 多账号切换 · 一键启动",
            render_backend=getattr(self, "launcher_render_backend_value", "默认"),
            log_callback=self.log,
            fallback_callback=self._on_banner_gpu_fallback,
            title_color="#1f1a0d",
            subtitle_color="#5a4612",
            user_color="#1f1a0d",
            frame_count=1,
            fps=1,
        )
        self.banner.pack(fill=tk.X, side=tk.TOP)

                                              
        main = ttk.Frame(self.root, padding="16")
        main.pack(fill=tk.BOTH, expand=True)
        
        f1 = ttk.LabelFrame(main, text="游戏目录")
        f1.pack(fill=tk.X, pady=(4, 6))
        r1 = ttk.Frame(f1)
        r1.pack(fill=tk.X, padx=5, pady=5)
        self.dir_var = tk.StringVar(value=str(self.base_dir))
        ttk.Entry(r1, textvariable=self.dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
        ttk.Button(r1, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)
        ttk.Button(r1, text="打开", command=self.open_dir).pack(side=tk.LEFT, padx=2)
        
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 10))
        
        self.download_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.download_tab, text="📥 下载")
        self.setup_download_tab()
        
        self.launch_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.launch_tab, text="🚀 启动")
        self.setup_launch_tab()
        
        self.auth_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.auth_tab, text="👤 账号")
        self.setup_auth_tab()

        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="⚙️ 设置")
        self.setup_settings_tab()

                                           
        f6 = ttk.LabelFrame(main, text="控制台")
        f6.pack(fill=tk.X, pady=3)
        console_btn_frame = ttk.Frame(f6)
        console_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(console_btn_frame, text="🖥️ 打开控制台窗口",
                   command=self.open_console_window).pack(side=tk.LEFT)
        ttk.Button(console_btn_frame, text="🗑️ 清空控制台",
                   command=self.clear_console).pack(side=tk.LEFT, padx=5)
        ttk.Button(console_btn_frame, text="📊 版本分类信息",
                   command=self.show_version_info).pack(side=tk.LEFT, padx=5)
        ttk.Button(console_btn_frame, text="🎨 黑/白主题",
                   command=self.toggle_theme).pack(side=tk.LEFT, padx=5)
                                                         
    def setup_settings_tab(self):
        host = ttk.Frame(self.settings_tab)
        host.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(host, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=v_scroll.set)

        tab = ttk.Frame(canvas)
        tab_window = canvas.create_window((0, 0), window=tab, anchor="nw")

        def _on_settings_config(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        tab.bind("<Configure>", _on_settings_config)

        def _on_settings_width(event):
            canvas.itemconfigure(tab_window, width=event.width)
        canvas.bind("<Configure>", _on_settings_width)

        def _on_settings_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_settings_wheel(event=None):
            canvas.bind_all("<MouseWheel>", _on_settings_wheel)

        def _unbind_settings_wheel(event=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_settings_wheel)
        canvas.bind("<Leave>", _unbind_settings_wheel)

                            
        f_auth = ttk.LabelFrame(tab, text="👤 账号设置")
        f_auth.pack(fill=tk.X, pady=5, padx=5)

        r_method = ttk.Frame(f_auth)
        r_method.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_method, text="正版登录方式:").pack(side=tk.LEFT)
        self.login_method_var = tk.StringVar(value=getattr(self, 'login_method_value', "auth_code"))
        ttk.Radiobutton(
            r_method, text="授权代码流（内嵌窗口）",
            variable=self.login_method_var, value="auth_code"
        ).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(
            r_method, text="设备代码流（浏览器输码）",
            variable=self.login_method_var, value="device_code"
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(
            r_method, text="(在「账号」页点登录时生效)",
            foreground="gray"
        ).pack(side=tk.LEFT, padx=10)

        r_demo = ttk.Frame(f_auth)
        r_demo.pack(fill=tk.X, padx=5, pady=5)
        self.demo_check = ttk.Checkbutton(
            r_demo, text="启用试玩模式 (--demo)",
            command=self.on_demo_mode_change
        )
        self.demo_check.pack(side=tk.LEFT, padx=5)
        tk.Label(r_demo, text="(无需正版账号，限时游玩100分钟)", fg="gray").pack(side=tk.LEFT, padx=5)

        r_lang = ttk.Frame(f_auth)
        r_lang.pack(fill=tk.X, padx=5, pady=5)
        self.auto_chinese_check = ttk.Checkbutton(
            r_lang, text="启动时自动设置游戏语言为简体中文",
            variable=self.auto_chinese,
            command=self.on_auto_chinese_change
        )
        self.auto_chinese_check.pack(side=tk.LEFT, padx=5)
        tk.Label(r_lang, text="(修改 options.txt 中的 lang:zh_cn)", fg="gray").pack(side=tk.LEFT, padx=5)

        r_backup = ttk.Frame(f_auth)
        r_backup.pack(fill=tk.X, padx=5, pady=5)
        self.auto_backup_var = tk.BooleanVar(value=getattr(self, 'auto_backup_value', True))
        ttk.Checkbutton(
            r_backup, text="启动前/关闭后自动备份存档（保留最近 5 个）",
            variable=self.auto_backup_var,
            command=self.save_config
        ).pack(side=tk.LEFT, padx=5)

                            
        f_theme = ttk.LabelFrame(tab, text="🎨 外观主题（黑 / 白）")
        f_theme.pack(fill=tk.X, pady=5, padx=5)
        r_theme = ttk.Frame(f_theme)
        r_theme.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_theme, text="主题:").pack(side=tk.LEFT)
        self.theme_mode_var = tk.StringVar(value=self.theme_value)
        self.theme_combo = ttk.Combobox(
            r_theme, textvariable=self.theme_mode_var, state="readonly",
            values=["follow (跟随系统)", "white (白色)", "black (黑色)"], width=18
        )
        self.theme_combo.pack(side=tk.LEFT, padx=5)
                   
        self._theme_display_map = {
            "follow (跟随系统)": "system", "system": "system",
            "white (白色)": "light", "black (黑色)": "dark",
            "light": "light", "dark": "dark",
            "white": "light", "black": "dark",
        }
        self.theme_combo.bind("<<ComboboxSelected>>", lambda e: self.on_theme_change())
                            
        try:
            if getattr(self, 'theme_value', 'light') == 'system':
                self.theme_mode_var.set("follow (跟随系统)")
            else:
                self.theme_mode_var.set("white (白色)" if self.theme_value == "light" else "black (黑色)")
        except Exception:
            pass
        ttk.Button(r_theme, text="🔄 应用", command=self.on_theme_change).pack(side=tk.LEFT, padx=5)
        ttk.Button(r_theme, text="⟳ 快速切换", command=self.toggle_theme).pack(side=tk.LEFT, padx=2)
        ttk.Label(r_theme, text="(切换后立即生效并自动保存)", foreground="gray").pack(side=tk.LEFT, padx=10)

                            
        f_authlib = ttk.LabelFrame(tab, text="🔐 外置登录 (Authlib-Injector)")
        f_authlib.pack(fill=tk.X, pady=5, padx=5)
        r_authlib = ttk.Frame(f_authlib)
        r_authlib.pack(fill=tk.X, padx=5, pady=5)

        self.authlib_check = ttk.Checkbutton(
            r_authlib, text="启用外置登录",
            command=self.on_authlib_change
        )
        self.authlib_check.pack(side=tk.LEFT, padx=5)
        ttk.Label(r_authlib, text="认证服务器:").pack(side=tk.LEFT, padx=(10, 5))
        self.authlib_server_var = tk.StringVar(
            value=self.authlib_server_value or "https://littleskin.cn/api/yggdrasil"
        )
        ttk.Entry(
            r_authlib, textvariable=self.authlib_server_var, width=35
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        def set_authlib_server(url):
            self.authlib_server_var.set(url)

        server_menu = ttk.Menubutton(r_authlib, text="预设")
        server_menu.pack(side=tk.LEFT, padx=2)
        menu = tk.Menu(server_menu, tearoff=0)
        menu.add_command(label="LittleSkin",
                         command=lambda: set_authlib_server("https://littleskin.cn/api/yggdrasil"))
        menu.add_command(label="Blessing Skin",
                         command=lambda: set_authlib_server("https://skin.example.com/api/yggdrasil"))
        menu.add_separator()
        menu.add_command(label="下载 Authlib", command=self.download_authlib)
        server_menu.config(menu=menu)

        f_window = ttk.LabelFrame(tab, text="🖥️ 游戏窗口")
        f_window.pack(fill=tk.X, pady=5, padx=5)
        r_window = ttk.Frame(f_window)
        r_window.pack(fill=tk.X, padx=5, pady=(5, 3))
        ttk.Label(r_window, text="窗口宽度:").pack(side=tk.LEFT, padx=(5, 3))
        self.window_width_var = tk.StringVar(value=str(getattr(self, 'window_width', 854)))
        window_width_spin = ttk.Spinbox(
            r_window, from_=320, to=7680, increment=1, width=8,
            textvariable=self.window_width_var
        )
        window_width_spin.pack(side=tk.LEFT)
        ttk.Label(r_window, text="×").pack(side=tk.LEFT, padx=6)
        ttk.Label(r_window, text="高度:").pack(side=tk.LEFT, padx=(0, 3))
        self.window_height_var = tk.StringVar(value=str(getattr(self, 'window_height', 480)))
        window_height_spin = ttk.Spinbox(
            r_window, from_=240, to=4320, increment=1, width=8,
            textvariable=self.window_height_var
        )
        window_height_spin.pack(side=tk.LEFT)
        ttk.Label(r_window, text="像素").pack(side=tk.LEFT, padx=(5, 12))
        ttk.Label(r_window, text="常用尺寸:").pack(side=tk.LEFT)
        self._window_presets = {
            "854 × 480 (默认)": (854, 480),
            "1280 × 720": (1280, 720),
            "1600 × 900": (1600, 900),
            "1920 × 1080": (1920, 1080),
            "2560 × 1440": (2560, 1440),
        }
        current_window_size = self.get_game_window_size()
        initial_preset = next(
            (name for name, size in self._window_presets.items() if size == current_window_size),
            "自定义"
        )
        self.window_preset_var = tk.StringVar(value=initial_preset)
        window_preset_combo = ttk.Combobox(
            r_window, textvariable=self.window_preset_var,
            values=list(self._window_presets) + ["自定义"], state="readonly", width=17
        )
        window_preset_combo.pack(side=tk.LEFT, padx=5)
        window_preset_combo.bind("<<ComboboxSelected>>", self._apply_window_preset)
        r_window_hint = ttk.Frame(f_window)
        r_window_hint.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Label(
            r_window_hint,
            text="修改后下次启动游戏生效；窗口尺寸超过屏幕时游戏会自动缩小。",
            foreground="gray"
        ).pack(side=tk.LEFT, padx=5)
        self.window_width_var.trace_add("write", self._on_window_size_change)
        self.window_height_var.trace_add("write", self._on_window_size_change)
        for spin in (window_width_spin, window_height_spin):
            spin.bind("<Return>", self._normalize_window_size)
            spin.bind("<FocusOut>", self._normalize_window_size)

                               
        f_java = ttk.LabelFrame(tab, text="☕ Java 设置")
        f_java.pack(fill=tk.X, pady=5, padx=5)

        r_java = ttk.Frame(f_java)
        r_java.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_java, text="Java 路径:").pack(side=tk.LEFT)
        self.java_path_var = tk.StringVar(value=self.java_path)
        ttk.Entry(
            r_java, textvariable=self.java_path_var, width=45
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(r_java, text="浏览", command=self.browse_java).pack(side=tk.LEFT)
        ttk.Button(r_java, text="自动搜索Java", command=self._auto_search_java_button).pack(side=tk.LEFT, padx=2)

        r_java_list = ttk.Frame(f_java)
        r_java_list.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_java_list, text="已发现 Java:").pack(side=tk.LEFT)
        self.java_list_var = tk.StringVar()
        self._java_list_paths = []
        self.java_list_combo = ttk.Combobox(
            r_java_list, textvariable=self.java_list_var, state="readonly", width=45
        )
        self.java_list_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.java_list_combo.bind("<<ComboboxSelected>>", self._on_java_list_select)
        ttk.Button(r_java_list, text="刷新列表", command=self.refresh_java_list).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_java_list, text="使用选中", command=self.use_selected_java).pack(side=tk.LEFT, padx=2)
        r_java_actions = ttk.Frame(f_java)
        r_java_actions.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(r_java_actions, text="手动添加", command=self.manually_add_java).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_java_actions, text="删除选中", command=self.remove_selected_java).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_java_actions, text="恢复隐藏", command=self.restore_ignored_java).pack(side=tk.LEFT, padx=2)
        self.refresh_java_list()

        r_mem = ttk.Frame(f_java)
        r_mem.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_mem, text="内存分配 (MB):").pack(side=tk.LEFT)
        self.memory_var = tk.StringVar(value=self.memory_value)
        ttk.Entry(r_mem, textvariable=self.memory_var, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Label(r_mem, text="(建议: 1024-4096)", foreground="gray").pack(side=tk.LEFT, padx=5)

                              
        f_jvm = ttk.LabelFrame(tab, text="🎛️ JVM 参数")
        f_jvm.pack(fill=tk.X, pady=5, padx=5)

        r_preset = ttk.Frame(f_jvm)
        r_preset.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_preset, text="预设:").pack(side=tk.LEFT)
        self.jvm_preset_var = tk.StringVar(value=self.jvm_preset_value)
        jvm_preset_combo = ttk.Combobox(
            r_preset, textvariable=self.jvm_preset_var,
            values=list(self.JVM_PRESETS.keys()),
            width=20, state="readonly"
        )
        jvm_preset_combo.pack(side=tk.LEFT, padx=5)
        jvm_preset_combo.bind('<<ComboboxSelected>>', self.on_jvm_preset_change)
        ttk.Button(
            r_preset, text="💾 保存当前为预设",
            command=self.save_jvm_as_preset
        ).pack(side=tk.LEFT, padx=2)

        r_custom = ttk.Frame(f_jvm)
        r_custom.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(
            r_custom, text="自定义 JVM 参数 (警告: 瞎写可能导致游戏崩溃):"
        ).pack(side=tk.LEFT)
        self.custom_jvm_args_var = tk.StringVar(value=self.custom_jvm_args_value)
        ttk.Entry(
            r_custom, textvariable=self.custom_jvm_args_var, width=50
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        r_quick = ttk.Frame(f_jvm)
        r_quick.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(r_quick, text="快捷添加:").pack(side=tk.LEFT)

        def append_jvm_arg(arg):
            current = self.custom_jvm_args_var.get().strip()
            if current:
                if arg not in current.split():
                    self.custom_jvm_args_var.set(current + " " + arg)
            else:
                self.custom_jvm_args_var.set(arg)

        def reset_jvm_args():
            self.custom_jvm_args_var.set("-XX:+UseG1GC -Dfile.encoding=UTF-8")

        ttk.Button(r_quick, text="+ZGC", width=8,
                   command=lambda: append_jvm_arg("-XX:+UseZGC")).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_quick, text="恢复默认", width=10,
                   command=reset_jvm_args).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_quick, text="清空", width=8,
                   command=lambda: self.custom_jvm_args_var.set("")).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_quick, text="🔥 A卡受害者模式", width=16,
                   command=self.apply_amd_victim_mode).pack(side=tk.LEFT, padx=2)

                                          
        f_perf = ttk.LabelFrame(tab, text="性能优化（CPU / 显卡）")
        f_perf.pack(fill=tk.X, pady=5, padx=5)
        r_perf1 = ttk.Frame(f_perf)
        r_perf1.pack(fill=tk.X, padx=5, pady=3)
        self.use_all_cores_var = tk.BooleanVar(value=getattr(self, "use_all_cores_value", True))
        ttk.Checkbutton(
            r_perf1, text="使用全部 CPU 核心 (自动设置 -XX:ActiveProcessorCount)",
            variable=self.use_all_cores_var, command=self.save_config
        ).pack(side=tk.LEFT, padx=5)
        r_perf2 = ttk.Frame(f_perf)
        r_perf2.pack(fill=tk.X, padx=5, pady=3)
        self.gpu_acceleration_var = tk.BooleanVar(value=getattr(self, "gpu_acceleration_value", True))
        ttk.Checkbutton(
            r_perf2, text="启用显卡硬件加速 (OpenGL / D3D 渲染管线)",
            variable=self.gpu_acceleration_var, command=self.save_config
        ).pack(side=tk.LEFT, padx=5)
        r_perf3 = ttk.Frame(f_perf)
        r_perf3.pack(fill=tk.X, padx=5, pady=3)
        ttk.Label(r_perf3, text="游戏渲染后端:").pack(side=tk.LEFT, padx=5)
        self.render_backend_var = tk.StringVar(value=getattr(self, "render_backend_value", "OpenGL"))
        rb_combo = ttk.Combobox(
            r_perf3, textvariable=self.render_backend_var,
            values=("OpenGL", "Vulkan", "DirectX 12"),
            state="readonly", width=18
        )
        rb_combo.pack(side=tk.LEFT, padx=5)
        rb_combo.bind("<<ComboboxSelected>>", lambda e: self.save_config())
        r_perf4 = ttk.Frame(f_perf)
        r_perf4.pack(fill=tk.X, padx=5, pady=3)
        ttk.Label(r_perf4, text="启动器画面:").pack(side=tk.LEFT, padx=5)
        self.launcher_render_backend_var = tk.StringVar(
            value=getattr(self, "launcher_render_backend_value", "默认")
        )
        lb_combo = ttk.Combobox(
            r_perf4, textvariable=self.launcher_render_backend_var,
            values=("默认", "OpenGL", "Vulkan", "DirectX 12"),
            state="readonly", width=18
        )
        lb_combo.pack(side=tk.LEFT, padx=5)
        lb_combo.bind("<<ComboboxSelected>>", self.on_launcher_renderer_change)
        self.launcher_render_status = ttk.Label(
            r_perf4, text="默认使用 Tk 渲染（兼容性最好）", foreground="gray"
        )
        self.launcher_render_status.pack(side=tk.LEFT, padx=8)
                                                     
                                                     
    def setup_download_tab(self):
        tab = self.download_tab

                                             
        outer = ttk.Frame(tab)
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        v_scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=v_scroll.set)

        inner = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_config)

        def _on_canvas_config(event):
            canvas.itemconfigure(canvas_window, width=event.width)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(e=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

                                   
        self.download_notebook = ttk.Notebook(inner)
        self.download_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        self.dl_version_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_version_tab, text="📦 版本")
        self._setup_dl_version_tab(self.dl_version_tab)

        self.dl_mod_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_mod_tab, text="🔧 Mod")
        self._setup_dl_mod_tab(self.dl_mod_tab)

        self.dl_shader_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_shader_tab, text="✨ 光影")
        self._setup_dl_shader_tab(self.dl_shader_tab)

        self.dl_resourcepack_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_resourcepack_tab, text="🎨 材质包")
        self._setup_dl_resourcepack_tab(self.dl_resourcepack_tab)

        self.dl_datapack_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_datapack_tab, text="📦 数据包")
        self._setup_dl_datapack_tab(self.dl_datapack_tab)

        self.dl_modpack_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_modpack_tab, text="📦 整合包")
        self._setup_dl_modpack_tab(self.dl_modpack_tab)

        self.dl_news_tab = ttk.Frame(self.download_notebook)
        self.download_notebook.add(self.dl_news_tab, text="📰 新闻")
        self._setup_dl_news_tab(self.dl_news_tab)
        self.download_notebook.bind("<<NotebookTabChanged>>", self._on_download_tab_changed)

    def _on_download_tab_changed(self, event=None):
        try:
            idx = self.download_notebook.index(self.download_notebook.select())
        except Exception:
            return
        pages = {
            1: ("mods", self._load_initial_mods),
            2: ("shaders", self._load_initial_shaders),
            3: ("resourcepacks", self._load_initial_resourcepacks),
            4: ("datapacks", self._load_initial_datapacks),
            5: ("modpacks", self._load_initial_modpacks),
            6: ("news", self.load_news),
        }
        page = pages.get(idx)
        if page is None:
            return
        key, callback = page
        if key in self._lazy_pages:
            return
        self._lazy_pages.add(key)
        self.root.after(50, callback)

    def _setup_dl_version_tab(self, tab):
                            
        f1 = ttk.LabelFrame(tab, text="版本选择 (左边选分类，右边选版本，双击直接下载)")
        f1.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        r1 = ttk.Frame(f1)
        r1.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        cat_frame = ttk.Frame(r1)
        cat_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        ttk.Label(cat_frame, text="分类").pack(anchor=tk.W)
        self.download_cat_list = tk.Listbox(
            cat_frame, width=14, height=12,
            font=("Microsoft YaHei", 10),
            exportselection=False, activestyle="none"
        )
        self.download_cat_list.pack(side=tk.LEFT, fill=tk.Y)
        for _cat in ["正式版", "快照版", "愚人节版", "远古版本", "本地已安装"]:
            self.download_cat_list.insert(tk.END, _cat)

        ver_frame = ttk.Frame(r1)
        ver_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ver_top = ttk.Frame(ver_frame)
        ver_top.pack(fill=tk.X)
        ttk.Label(ver_top, text="版本").pack(side=tk.LEFT)
        ttk.Button(ver_top, text="🔄 刷新列表", command=self.refresh_versions).pack(side=tk.RIGHT, padx=2)

        ver_list_wrap = ttk.Frame(ver_frame)
        ver_list_wrap.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.download_version_list = tk.Listbox(
            ver_list_wrap, height=12,
            font=("Consolas", 10),
            exportselection=False, activestyle="none"
        )
        self.download_version_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ver_scroll = ttk.Scrollbar(ver_list_wrap, orient="vertical",
                                   command=self.download_version_list.yview)
        ver_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.download_version_list.config(yscrollcommand=ver_scroll.set)

        self.download_version_var = tk.StringVar()
        self.download_version_combo = self.download_version_list

        self.download_cat_list.bind("<<ListboxSelect>>", self._on_download_cat_select)
        self.download_version_list.bind("<<ListboxSelect>>", self._on_download_version_select)
        self.download_version_list.bind("<Double-Button-1>", lambda e: self.download_selected_version())

        self.download_cat_list.selection_set(0)
        self.download_cat_list.activate(0)

                            
        f2 = ttk.LabelFrame(tab, text="下载操作")
        f2.pack(fill=tk.X, pady=5, padx=5)
        r2 = ttk.Frame(f2)
        r2.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(r2, text="📥 下载正式版", command=self.download_release_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(r2, text="📥 下载快照版", command=self.download_snapshot_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(r2, text="📥 下载愚人节版", command=self.download_april_fools_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(r2, text="📥 下载选中版本", command=self.download_selected_version,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        self.download_cancel_btn = ttk.Button(r2, text="⛔ 取消下载", command=self.cancel_download, state="disabled")
        self.download_cancel_btn.pack(side=tk.LEFT, padx=5)

                                 
        f_mod = ttk.LabelFrame(tab, text="Mod 加载器安装")
        f_mod.pack(fill=tk.X, pady=5, padx=5)
        r_mod = ttk.Frame(f_mod)
        r_mod.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r_mod, text="MC版本:").pack(side=tk.LEFT)
        self.mod_mc_version_var = tk.StringVar(value="1.20.1")
        ttk.Entry(r_mod, textvariable=self.mod_mc_version_var, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Label(r_mod, text="加载器版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.mod_loader_version_var = tk.StringVar(value="latest")
        ttk.Entry(r_mod, textvariable=self.mod_loader_version_var, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(r_mod, text="📦 Fabric",
                   command=lambda: threading.Thread(target=self._install_fabric, daemon=True).start()
                   ).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_mod, text="🔥 Forge",
                   command=lambda: threading.Thread(target=self._install_forge, daemon=True).start()
                   ).pack(side=tk.LEFT, padx=2)
        ttk.Button(r_mod, text="🌟 NeoForge",
                   command=lambda: threading.Thread(target=self._install_neoforge, daemon=True).start()
                   ).pack(side=tk.LEFT, padx=2)
        ttk.Label(r_mod, text="(latest = 最新版)", foreground="gray").pack(side=tk.LEFT, padx=5)

                            
        f3 = ttk.LabelFrame(tab, text="下载进度")
        f3.pack(fill=tk.X, pady=5, padx=5)
        r3 = ttk.Frame(f3)
        r3.pack(fill=tk.X, padx=5, pady=5)
        self.total_progress_var = tk.DoubleVar()
        self.total_bar = ttk.Progressbar(r3, variable=self.total_progress_var, maximum=100)
        self.total_bar.pack(fill=tk.X, pady=2)
        self.progress_label = ttk.Label(r3, text="就绪")
        self.progress_label.pack(anchor=tk.W, pady=2)
        self.file_progress_var = tk.DoubleVar()
        self.file_bar = ttk.Progressbar(r3, variable=self.file_progress_var, maximum=100)
        self.file_bar.pack(fill=tk.X, pady=2)
        self.file_label = ttk.Label(r3, text="等待下载...")
        self.file_label.pack(anchor=tk.W, pady=2)

    def _load_initial_mods(self):
        """打开页面时自动加载热门 Mod"""
        loader = self.mod_loader_filter.get()
        mc_ver = self.mod_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.mod_sort_var.get(), "downloads")

        facets = [["project_type:mod"]]
        if loader:
            facets.append([f"categories:{loader}"])
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"limit": 30, "index": index, "facets": json.dumps(facets)}
        self.log(f"📋 加载热门 Mod (加载器={loader}, MC={mc_ver})")
        self.mod_status_label.config(text="加载热门中...", foreground="blue")
        threading.Thread(target=self._do_search_mods, args=(params,), daemon=True).start()
    def _setup_dl_mod_tab(self, tab):
        f_search = ttk.LabelFrame(tab, text="🔍 搜索 Mod (Modrinth)")
        f_search.pack(fill=tk.X, pady=5, padx=5)

        r = ttk.Frame(f_search)
        r.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r, text="关键词:").pack(side=tk.LEFT)
        self.mod_search_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.mod_search_var, width=22).pack(side=tk.LEFT, padx=5)
        ttk.Button(r, text="🔍 搜索", command=self.search_mods).pack(side=tk.LEFT)
        ttk.Label(r, text="加载器:").pack(side=tk.LEFT, padx=(10, 5))
        self.mod_loader_filter = ttk.Combobox(
            r, values=["fabric", "forge", "neoforge", "quilt"],
            width=10, state="readonly"
        )
        self.mod_loader_filter.set("fabric")
        self.mod_loader_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="MC版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.mod_mc_filter = ttk.Entry(r, width=10)
        self.mod_mc_filter.insert(0, "")           
        self.mod_mc_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="(空=任意)", foreground="gray").pack(side=tk.LEFT, padx=2)
        ttk.Label(r, text="排序:").pack(side=tk.LEFT, padx=(10, 5))
        self.mod_sort_var = tk.StringVar(value="下载量")
        ttk.Combobox(r, textvariable=self.mod_sort_var,
                     values=["下载量", "关注度", "最新发布", "最近更新"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        f_list = ttk.LabelFrame(tab, text="搜索结果（双击下载）")
        f_list.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        self.mod_tree, _ = self._make_result_tree(f_list, [
            ("name", "Mod 名称", 240, "w"),
            ("author", "作者", 100, "w"),
            ("downloads", "下载量", 80, "e"),
            ("followers", "关注", 70, "e"),
            ("mcver", "MC版本", 120, "w"),
            ("updated", "更新", 90, "center"),
            ("desc", "简介", 280, "w"),
        ])
        self.mod_tree.bind("<Double-Button-1>", lambda e: self.show_mod_versions())

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(bf, text="📥 下载选中 Mod", command=self.show_mod_versions).pack(side=tk.LEFT, padx=5)
        self.mod_status_label = ttk.Label(bf, text="就绪", foreground="gray")
        self.mod_status_label.pack(side=tk.LEFT, padx=15)
                         
    def _load_initial_shaders(self):
        """打开页面时自动加载热门光影"""
        mc_ver = self.shader_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.shader_sort_var.get(), "downloads")

        facets = [["project_type:shader"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"limit": 30, "index": index, "facets": json.dumps(facets)}
        self.log(f"📋 加载热门光影 (MC={mc_ver})")
        self.shader_status_label.config(text="加载热门中...", foreground="blue")
        threading.Thread(target=self._do_search_shaders, args=(params,), daemon=True).start()
    def _setup_dl_shader_tab(self, tab):
        f_search = ttk.LabelFrame(tab, text="✨ 搜索光影 (Modrinth)")
        f_search.pack(fill=tk.X, pady=5, padx=5)

        r = ttk.Frame(f_search)
        r.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r, text="关键词:").pack(side=tk.LEFT)
        self.shader_search_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.shader_search_var, width=22).pack(side=tk.LEFT, padx=5)
        ttk.Button(r, text="🔍 搜索", command=self.search_shaders).pack(side=tk.LEFT)
        ttk.Label(r, text="MC版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.shader_mc_filter = ttk.Entry(r, width=10)
        self.shader_mc_filter.insert(0, "")           
        self.shader_mc_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="(空=任意)", foreground="gray").pack(side=tk.LEFT, padx=2)
        ttk.Label(r, text="排序:").pack(side=tk.LEFT, padx=(10, 5))
        self.shader_sort_var = tk.StringVar(value="下载量")
        ttk.Combobox(r, textvariable=self.shader_sort_var,
                     values=["下载量", "关注度", "最新发布", "最近更新"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        f_list = ttk.LabelFrame(tab, text="搜索结果（双击下载）")
        f_list.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        self.shader_tree, _ = self._make_result_tree(f_list, [
            ("name", "光影名称", 240, "w"),
            ("author", "作者", 100, "w"),
            ("downloads", "下载量", 80, "e"),
            ("followers", "关注", 70, "e"),
            ("mcver", "MC版本", 120, "w"),
            ("updated", "更新", 90, "center"),
            ("desc", "简介", 280, "w"),
        ])
        self.shader_tree.bind("<Double-Button-1>", lambda e: self.show_shader_versions())

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(bf, text="📥 下载选中光影", command=self.show_shader_versions).pack(side=tk.LEFT, padx=5)
        self.shader_status_label = ttk.Label(bf, text="就绪", foreground="gray")
        self.shader_status_label.pack(side=tk.LEFT, padx=15)
                       
    def _load_initial_resourcepacks(self):
        """打开页面时自动加载热门材质包"""
        mc_ver = self.rp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.rp_sort_var.get(), "downloads")

        facets = [["project_type:resourcepack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"limit": 30, "index": index, "facets": json.dumps(facets)}
        self.log(f"📋 加载热门材质包 (MC={mc_ver})")
        self.rp_status_label.config(text="加载热门中...", foreground="blue")
        threading.Thread(target=self._do_search_resourcepacks, args=(params,), daemon=True).start()
    def _setup_dl_resourcepack_tab(self, tab):
        f_search = ttk.LabelFrame(tab, text="🎨 搜索材质包 (Modrinth)")
        f_search.pack(fill=tk.X, pady=5, padx=5)

        r = ttk.Frame(f_search)
        r.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r, text="关键词:").pack(side=tk.LEFT)
        self.rp_search_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.rp_search_var, width=22).pack(side=tk.LEFT, padx=5)
        ttk.Button(r, text="🔍 搜索", command=self.search_resourcepacks).pack(side=tk.LEFT)
        ttk.Label(r, text="MC版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.rp_mc_filter = ttk.Entry(r, width=10)
        self.rp_mc_filter.insert(0, "")           
        self.rp_mc_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="(空=任意)", foreground="gray").pack(side=tk.LEFT, padx=2)
        ttk.Label(r, text="排序:").pack(side=tk.LEFT, padx=(10, 5))
        self.rp_sort_var = tk.StringVar(value="下载量")
        ttk.Combobox(r, textvariable=self.rp_sort_var,
                     values=["下载量", "关注度", "最新发布", "最近更新"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        f_list = ttk.LabelFrame(tab, text="搜索结果（双击下载）")
        f_list.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        self.rp_tree, _ = self._make_result_tree(f_list, [
            ("name", "材质包名称", 240, "w"),
            ("author", "作者", 100, "w"),
            ("downloads", "下载量", 80, "e"),
            ("followers", "关注", 70, "e"),
            ("mcver", "MC版本", 120, "w"),
            ("updated", "更新", 90, "center"),
            ("desc", "简介", 280, "w"),
        ])
        self.rp_tree.bind("<Double-Button-1>", lambda e: self.show_resourcepack_versions())

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(bf, text="📥 下载选中材质包", command=self.show_resourcepack_versions).pack(side=tk.LEFT, padx=5)
        self.rp_status_label = ttk.Label(bf, text="就绪", foreground="gray")
        self.rp_status_label.pack(side=tk.LEFT, padx=15)
                        
    def _load_initial_datapacks(self):
        """打开页面时自动加载热门数据包"""
        mc_ver = self.dp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.dp_sort_var.get(), "downloads")

        facets = [["project_type:datapack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"limit": 30, "index": index, "facets": json.dumps(facets)}
        self.log(f"📋 加载热门数据包 (MC={mc_ver})")
        self.dp_status_label.config(text="加载热门中...", foreground="blue")
        threading.Thread(target=self._do_search_datapacks, args=(params,), daemon=True).start()
    def _setup_dl_datapack_tab(self, tab):
        f_search = ttk.LabelFrame(tab, text="📦 搜索数据包 (Modrinth)")
        f_search.pack(fill=tk.X, pady=5, padx=5)

        r = ttk.Frame(f_search)
        r.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r, text="关键词:").pack(side=tk.LEFT)
        self.dp_search_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.dp_search_var, width=22).pack(side=tk.LEFT, padx=5)
        ttk.Button(r, text="🔍 搜索", command=self.search_datapacks).pack(side=tk.LEFT)
        ttk.Label(r, text="MC版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.dp_mc_filter = ttk.Entry(r, width=10)
        self.dp_mc_filter.insert(0, "")           
        self.dp_mc_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="(空=任意)", foreground="gray").pack(side=tk.LEFT, padx=2)
        ttk.Label(r, text="排序:").pack(side=tk.LEFT, padx=(10, 5))
        self.dp_sort_var = tk.StringVar(value="下载量")
        ttk.Combobox(r, textvariable=self.dp_sort_var,
                     values=["下载量", "关注度", "最新发布", "最近更新"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        f_list = ttk.LabelFrame(tab, text="搜索结果（双击下载）")
        f_list.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        self.dp_tree, _ = self._make_result_tree(f_list, [
            ("name", "数据包名称", 240, "w"),
            ("author", "作者", 100, "w"),
            ("downloads", "下载量", 80, "e"),
            ("followers", "关注", 70, "e"),
            ("mcver", "MC版本", 120, "w"),
            ("updated", "更新", 90, "center"),
            ("desc", "简介", 280, "w"),
        ])
        self.dp_tree.bind("<Double-Button-1>", lambda e: self.show_datapack_versions())

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(bf, text="📥 下载选中数据包", command=self.show_datapack_versions).pack(side=tk.LEFT, padx=5)
        self.dp_status_label = ttk.Label(bf, text="就绪", foreground="gray")
        self.dp_status_label.pack(side=tk.LEFT, padx=15)
                        
    def _load_initial_modpacks(self):
        """打开页面时自动加载热门整合包"""
        mc_ver = self.mp_mc_filter.get().strip()
        sort_map = {"下载量": "downloads", "关注度": "follows",
                    "最新发布": "newest", "最近更新": "updated"}
        index = sort_map.get(self.mp_sort_var.get(), "downloads")

        facets = [["project_type:modpack"]]
        if mc_ver:
            facets.append([f"versions:{mc_ver}"])

        params = {"limit": 30, "index": index, "facets": json.dumps(facets)}
        self.log(f"📋 加载热门整合包 (MC={mc_ver})")
        self.mp_status_label.config(text="加载热门中...", foreground="blue")
        threading.Thread(target=self._do_search_modpacks, args=(params,), daemon=True).start()
    def _setup_dl_modpack_tab(self, tab):
        f_search = ttk.LabelFrame(tab, text="📦 搜索整合包 (Modrinth)")
        f_search.pack(fill=tk.X, pady=5, padx=5)

        r = ttk.Frame(f_search)
        r.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r, text="关键词:").pack(side=tk.LEFT)
        self.mp_search_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.mp_search_var, width=22).pack(side=tk.LEFT, padx=5)
        ttk.Button(r, text="🔍 搜索", command=self.search_modpacks).pack(side=tk.LEFT)
        ttk.Label(r, text="MC版本:").pack(side=tk.LEFT, padx=(10, 5))
        self.mp_mc_filter = ttk.Entry(r, width=10)
        self.mp_mc_filter.insert(0, "")           
        self.mp_mc_filter.pack(side=tk.LEFT)
        ttk.Label(r, text="(空=任意)", foreground="gray").pack(side=tk.LEFT, padx=2)
        ttk.Label(r, text="排序:").pack(side=tk.LEFT, padx=(10, 5))
        self.mp_sort_var = tk.StringVar(value="下载量")
        ttk.Combobox(r, textvariable=self.mp_sort_var,
                     values=["下载量", "关注度", "最新发布", "最近更新"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        f_list = ttk.LabelFrame(tab, text="搜索结果（双击安装）")
        f_list.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        self.mp_tree, _ = self._make_result_tree(f_list, [
            ("name", "整合包名称", 240, "w"),
            ("author", "作者", 100, "w"),
            ("downloads", "下载量", 80, "e"),
            ("followers", "关注", 70, "e"),
            ("mcver", "MC版本", 120, "w"),
            ("updated", "更新", 90, "center"),
            ("desc", "简介", 280, "w"),
        ])
        self.mp_tree.bind("<Double-Button-1>", lambda e: self.show_modpack_versions())

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(bf, text="📥 安装选中整合包", command=self.show_modpack_versions).pack(side=tk.LEFT, padx=5)
        self.mp_status_label = ttk.Label(bf, text="就绪", foreground="gray")
        self.mp_status_label.pack(side=tk.LEFT, padx=15)
                        
    def _setup_dl_news_tab(self, tab):
        f_news = ttk.LabelFrame(tab, text="📰 Minecraft 新闻")
        f_news.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        news_frame = ttk.Frame(f_news)
        news_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        news_type_frame = ttk.Frame(news_frame)
        news_type_frame.pack(fill=tk.X, pady=5)
        self.news_type_var = tk.StringVar(value=self.news_filter_value)
        ttk.Radiobutton(news_type_frame, text="📌 全部", variable=self.news_type_var,
                        value="all", command=self.load_news).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(news_type_frame, text="🟢 Java版", variable=self.news_type_var,
                        value="java", command=self.load_news).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(news_type_frame, text="🔵 基岩版", variable=self.news_type_var,
                        value="bedrock", command=self.load_news).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(news_type_frame, text="🟣 地下城", variable=self.news_type_var,
                        value="dungeons", command=self.load_news).pack(side=tk.LEFT, padx=5)
        ttk.Button(news_type_frame, text="🔄 刷新", command=self.load_news).pack(side=tk.RIGHT, padx=5)

        news_list_frame = ttk.Frame(news_frame)
        news_list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.news_text = tk.Text(
            news_list_frame, height=15, wrap=tk.WORD,
            font=("Microsoft YaHei", 9), state=tk.DISABLED
        )
        self.news_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        news_scroll = ttk.Scrollbar(news_list_frame, orient="vertical", command=self.news_text.yview)
        news_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.news_text.config(yscrollcommand=news_scroll.set)
    def setup_launch_tab(self):
        tab = self.launch_tab
        f1 = ttk.LabelFrame(tab, text="本地版本选择")
        f1.pack(fill=tk.X, pady=5, padx=5)
        r1 = ttk.Frame(f1)
        r1.pack(fill=tk.X, padx=5, pady=5)
        self.login_display_label = tk.Label(r1, text="👤 离线模式", fg="green")
        self.login_display_label.pack(side=tk.RIGHT, padx=5)
        ttk.Label(r1, text="已安装版本:").pack(side=tk.LEFT)
        self.launch_version_var = tk.StringVar()
        self.launch_version_combo = ttk.Combobox(r1, textvariable=self.launch_version_var, width=32)
        self.launch_version_combo.bind(
            '<<ComboboxSelected>>',
            self._on_launch_version_change
        )

        self.launch_version_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(r1, text="🔄 刷新", command=self.refresh_local_versions).pack(side=tk.LEFT, padx=2)
        self.local_count_label = ttk.Label(r1, text="(0 个版本)", foreground="gray")
        self.local_count_label.pack(side=tk.LEFT, padx=10)

                            
        f4 = ttk.LabelFrame(tab, text="启动操作")
        f4.pack(fill=tk.X, pady=5, padx=5)
        r5 = ttk.Frame(f4)
        r5.pack(fill=tk.X, padx=5, pady=(5, 3))
        r5_tools = ttk.Frame(f4)
        r5_tools.pack(fill=tk.X, padx=5, pady=(3, 3))
        r5_files = ttk.Frame(f4)
        r5_files.pack(fill=tk.X, padx=5, pady=(3, 5))
        ttk.Button(
        r5, text="🔍 Mod 冲突检测",
        command=lambda: ModConflictDetector(self.log).show_report(
            self.root,
            self.version_dir / self.get_selected_launch_version() / "mods"
        )
    ).pack(side=tk.LEFT, padx=5)
        self.launch_btn = ttk.Button(r5, text="🚀 启动游戏", command=self.launch_async,
                                     width=15, style="Accent.TButton")
        self.launch_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(r5, text="🧱 启动基岩版", command=self.launch_bedrock).pack(side=tk.LEFT, padx=5)
        self.bedrock_download_btn = ttk.Button(r5, text="⬇️ winget 安装 Minecraft", command=self.download_bedrock)
        self.bedrock_download_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_tools, text="📊 启动统计",
        command=lambda: LaunchHistory(Path.home() / ".minecraft_launcher").show(self.root)
    ).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_tools, text="📄 生成启动脚本", command=self.export_bat_script).pack(side=tk.LEFT, padx=5)
        def java_downloader(parent):
            import os
            import time
            import zipfile
            import tarfile
            import shutil
            import threading
            import subprocess
            import platform
            import tkinter as tk
            from tkinter import ttk, messagebox, filedialog
            from pathlib import Path
            from queue import Queue
            import queue
            import requests

                                                                    
            ADOPTIUM_API = "https://api.adoptium.net/v3"

                                                                  
            MIRROR_TUNA   = "https://mirrors.tuna.tsinghua.edu.cn/Adoptium"
            MIRROR_USTC   = "https://mirrors.ustc.edu.cn/adoptium"
            MIRROR_NJU    = "https://mirrors.nju.edu.cn/adoptium"
            MIRROR_NYIST  = "https://mirror.nyist.edu.cn/Adoptium"

                    
            BROWSER_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            HEADERS = {"User-Agent": BROWSER_UA}


            class JavaDownloader:
                def __init__(self, parent):
                    self.root = tk.Toplevel(parent)
                    self.root.title("Java 下载器 (JRE) - 多镜像源")
                    self.root.geometry("800x720")
                    self.root.minsize(740, 660)

                    self.log_queue = Queue()
                    self.session = requests.Session()
                    self.session.headers.update(HEADERS)
                    self.is_downloading = False
                    self.available_versions = []
                    self.download_dir = Path.home() / "Java"

                    self.setup_ui()
                    self.start_queue_processor()
                    self.load_versions_async()

                    self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

                                                              
                def log(self, msg, level=None):
                    if isinstance(msg, bytes):
                        try:
                            msg = msg.decode('utf-8', errors='replace')
                        except:
                            msg = str(msg)
                    if level is None:
                        msg_lower = msg.lower()
                        if '❌' in msg or 'error' in msg_lower:
                            level = 'error'
                        elif '⚠️' in msg or 'warn' in msg_lower:
                            level = 'warning'
                        elif '✅' in msg:
                            level = 'success'
                        else:
                            level = 'info'
                    self.log_queue.put((msg, level))

                def start_queue_processor(self):
                    def process():
                        while True:
                            try:
                                item = self.log_queue.get_nowait()
                                msg, level = item if isinstance(item, tuple) else (item, 'info')
                                tag_map = {
                                    'info':    'log_info',
                                    'success': 'log_success',
                                    'warning': 'log_warning',
                                    'error':   'log_error',
                                }
                                tag = tag_map.get(level, 'log_info')
                                self.console.config(state=tk.NORMAL)
                                self.console.insert(tk.END, f"{msg}\n", tag)
                                self.console.see(tk.END)
                                self.console.config(state=tk.DISABLED)
                            except queue.Empty:
                                pass
                            time.sleep(0.05)
                    threading.Thread(target=process, daemon=True).start()

                                                              
                def setup_ui(self):
                    main = ttk.Frame(self.root, padding="10")
                    main.pack(fill=tk.BOTH, expand=True)

                    f_intro = ttk.LabelFrame(main, text="📖 说明")
                    f_intro.pack(fill=tk.X, pady=5)
                    tk.Label(
                        f_intro,
                        text="本工具从 Adoptium 下载 JRE，依次尝试多个国内镜像源。\n"
                            "顺序：清华 → 中科大 → 南大 → 南阳理工 → 官方\n"
                            "玩 Minecraft 只需要 JRE，无需 JDK。",
                        justify=tk.LEFT, font=("Microsoft YaHei", 9)
                    ).pack(anchor=tk.W, padx=10, pady=8)

                                           
                    f_ver = ttk.LabelFrame(main, text="☕ 选择 Java 版本")
                    f_ver.pack(fill=tk.X, pady=5)

                    r1 = ttk.Frame(f_ver)
                    r1.pack(fill=tk.X, padx=10, pady=8)

                    ttk.Label(r1, text="版本:").pack(side=tk.LEFT)
                    self.version_var = tk.StringVar()
                    self.version_combo = ttk.Combobox(
                        r1, textvariable=self.version_var, width=15, state="readonly"
                    )
                    self.version_combo.pack(side=tk.LEFT, padx=5)
                    ttk.Label(r1, text="(LTS 更稳定)", foreground="gray").pack(side=tk.LEFT, padx=5)
                    ttk.Button(r1, text="🔄 刷新版本", command=self.load_versions_async).pack(side=tk.LEFT, padx=5)

                    r2 = ttk.Frame(f_ver)
                    r2.pack(fill=tk.X, padx=10, pady=5)

                    self.image_type_var = tk.StringVar(value="jre")
                    ttk.Label(r2, text="类型: JRE (仅运行)", foreground="gray").pack(side=tk.LEFT)

                    ttk.Label(r2, text="架构:").pack(side=tk.LEFT, padx=(15, 5))
                    self.arch_var = tk.StringVar(value=self.detect_arch())
                    ttk.Combobox(
                        r2, textvariable=self.arch_var,
                        values=["x64", "x86", "aarch64"],
                        width=10, state="readonly"
                    ).pack(side=tk.LEFT)

                                         
                    r3 = ttk.Frame(f_ver)
                    r3.pack(fill=tk.X, padx=10, pady=5)

                    ttk.Label(r3, text="下载源:").pack(side=tk.LEFT)
                    self.source_var = tk.StringVar(value="auto")
                    ttk.Radiobutton(
                        r3, text="自动（推荐）",
                        variable=self.source_var, value="auto"
                    ).pack(side=tk.LEFT, padx=5)
                    ttk.Radiobutton(
                        r3, text="清华",
                        variable=self.source_var, value="tuna"
                    ).pack(side=tk.LEFT, padx=5)
                    ttk.Radiobutton(
                        r3, text="中科大",
                        variable=self.source_var, value="ustc"
                    ).pack(side=tk.LEFT, padx=5)
                    ttk.Radiobutton(
                        r3, text="南大",
                        variable=self.source_var, value="nju"
                    ).pack(side=tk.LEFT, padx=5)
                    ttk.Radiobutton(
                        r3, text="南阳理工",
                        variable=self.source_var, value="nyist"
                    ).pack(side=tk.LEFT, padx=5)
                    ttk.Radiobutton(
                        r3, text="官方",
                        variable=self.source_var, value="official"
                    ).pack(side=tk.LEFT, padx=5)

                                        
                    f_dir = ttk.LabelFrame(main, text="📁 下载位置")
                    f_dir.pack(fill=tk.X, pady=5)

                    r4 = ttk.Frame(f_dir)
                    r4.pack(fill=tk.X, padx=10, pady=8)

                    self.dir_var = tk.StringVar(value=str(self.download_dir))
                    ttk.Entry(r4, textvariable=self.dir_var, width=60).pack(
                        side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
                    )
                    ttk.Button(r4, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)
                    ttk.Button(r4, text="打开", command=self.open_dir).pack(side=tk.LEFT, padx=2)

                                      
                    f_act = ttk.LabelFrame(main, text="⚙️ 操作")
                    f_act.pack(fill=tk.X, pady=5)

                    r5 = ttk.Frame(f_act)
                    r5.pack(fill=tk.X, padx=10, pady=8)

                    self.download_btn = ttk.Button(
                        r5, text="📥 开始下载", command=self.start_download, width=15
                    )
                    self.download_btn.pack(side=tk.LEFT, padx=5)
                    ttk.Button(r5, text="🗑️ 清空日志", command=self.clear_console).pack(side=tk.LEFT, padx=5)

                                      
                    f_prog = ttk.LabelFrame(main, text="📊 下载进度")
                    f_prog.pack(fill=tk.X, pady=5)

                    r6 = ttk.Frame(f_prog)
                    r6.pack(fill=tk.X, padx=10, pady=8)

                    self.progress_var = tk.DoubleVar()
                    self.progress_bar = ttk.Progressbar(
                        r6, variable=self.progress_var, maximum=100
                    )
                    self.progress_bar.pack(fill=tk.X, pady=2)
                    self.status_label = ttk.Label(r6, text="就绪")
                    self.status_label.pack(anchor=tk.W, pady=2)

                                       
                    f_log = ttk.LabelFrame(main, text="📜 日志")
                    f_log.pack(fill=tk.BOTH, expand=True, pady=5)

                    log_frame = ttk.Frame(f_log)
                    log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

                    self.console = tk.Text(
                        log_frame, height=12, wrap=tk.WORD,
                        font=("Consolas", 9), state=tk.DISABLED
                    )
                    self.console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

                    scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.console.yview)
                    scroll.pack(side=tk.RIGHT, fill=tk.Y)
                    self.console.config(yscrollcommand=scroll.set)

                    self.console.tag_config('log_info', foreground='#222222')
                    self.console.tag_config('log_success', foreground='#008800')
                    self.console.tag_config('log_warning', foreground='#CC8800')
                    self.console.tag_config('log_error', foreground='#CC0000')

                                                                
                def detect_arch(self):
                    machine = platform.machine().lower()
                    if machine in ('amd64', 'x86_64'):
                        return "x64"
                    elif machine in ('x86', 'i386', 'i686'):
                        return "x86"
                    elif machine in ('arm64', 'aarch64'):
                        return "aarch64"
                    return "x64"

                def detect_os(self):
                    system = platform.system().lower()
                    if system == 'windows':
                        return "windows"
                    elif system == 'darwin':
                        return "mac"
                    elif system == 'linux':
                        return "linux"
                    return "windows"

                def browse_dir(self):
                    d = filedialog.askdirectory(initialdir=self.dir_var.get())
                    if d:
                        self.dir_var.set(d)

                def open_dir(self):
                    d = Path(self.dir_var.get())
                    d.mkdir(parents=True, exist_ok=True)
                    if platform.system() == 'Windows':
                        os.startfile(str(d))
                    elif platform.system() == 'Darwin':
                        subprocess.run(['open', str(d)])
                    else:
                        subprocess.run(['xdg-open', str(d)])

                def clear_console(self):
                    self.console.config(state=tk.NORMAL)
                    self.console.delete(1.0, tk.END)
                    self.console.config(state=tk.DISABLED)

                                                                  
                def load_versions_async(self):
                    threading.Thread(target=self._load_versions, daemon=True).start()

                def _load_versions(self):
                    self.log("🔍 正在从 Adoptium 获取可用 Java 版本...")
                    try:
                        r = self.session.get(
                            f"{ADOPTIUM_API}/info/available_releases", timeout=15
                        )
                        r.raise_for_status()
                        data = r.json()

                        releases = data.get("available_releases", [])
                        if not releases:
                            raise Exception("未获取到版本列表")

                        releases = sorted(releases, reverse=True)
                        self.available_versions = [str(v) for v in releases]

                        self.root.after(0, lambda: self.version_combo.config(
                            values=self.available_versions
                        ))
                        if self.available_versions:
                            self.root.after(0, lambda: self.version_var.set(self.available_versions[0]))

                        self.log(f"✅ 可用版本: {', '.join(self.available_versions)}")
                        self.log(f"📌 最新 LTS: {data.get('most_recent_lts', '未知')}")
                        self.log(f"📌 最新版本: {data.get('most_recent_feature_release', '未知')}")

                    except Exception as e:
                        self.log(f"❌ 获取版本列表失败: {e}", level='error')
                        fallback = ["21", "17", "11", "8"]
                        self.available_versions = fallback
                        self.root.after(0, lambda: self.version_combo.config(values=fallback))
                        self.root.after(0, lambda: self.version_var.set("21"))
                        self.log(f"⚠️ 使用默认版本列表: {', '.join(fallback)}")

                                                                
                def start_download(self):
                    if self.is_downloading:
                        messagebox.showwarning("提示", "已有下载任务在进行中")
                        return

                    java_version = self.version_var.get().strip()
                    if not java_version:
                        messagebox.showerror("错误", "请选择 Java 版本")
                        return

                    download_dir = Path(self.dir_var.get().strip())
                    if not download_dir:
                        messagebox.showerror("错误", "请选择下载位置")
                        return

                    image_type = "jre"
                    arch = self.arch_var.get()
                    os_name = self.detect_os()
                    source = self.source_var.get()

                    source_names = {
                        "auto": "自动（依次尝试所有镜像）",
                        "tuna": "清华镜像",
                        "ustc": "中科大镜像",
                        "nju": "南大镜像",
                        "nyist": "南阳理工镜像",
                        "official": "Adoptium 官方"
                    }
                    source_name = source_names.get(source, source)

                    if not messagebox.askyesno(
                        "确认下载",
                        f"即将下载：\n\n"
                        f"  Java 版本: {java_version}\n"
                        f"  类型: JRE (仅运行)\n"
                        f"  架构: {arch}\n"
                        f"  系统: {os_name}\n"
                        f"  下载源: {source_name}\n"
                        f"  下载到: {download_dir}\n\n"
                        f"确认开始吗？"
                    ):
                        return

                    self.is_downloading = True
                    self.download_btn.config(state="disabled")
                    self.progress_var.set(0)
                    self.status_label.config(text="准备中...")

                    threading.Thread(
                        target=self._download_thread,
                        args=(java_version, image_type, arch, os_name, download_dir, source),
                        daemon=True
                    ).start()

                                                                 
                def _build_source_list(self, java_version, image_type, arch, os_name, file_name, official_url, source_pref):
                    """根据用户偏好构建下载源列表"""
                                        
                    all_mirrors = [
                        ("清华镜像",   MIRROR_TUNA),
                        ("中科大镜像", MIRROR_USTC),
                        ("南大镜像",   MIRROR_NJU),
                        ("南阳理工镜像", MIRROR_NYIST),
                    ]

                                   
                    mirror_sources = [
                        (name, f"{base}/{java_version}/{image_type}/{arch}/{os_name}/{file_name}")
                        for name, base in all_mirrors
                    ]

                    official_source = ("Adoptium 官方", official_url)

                                
                    if source_pref == "auto":
                                        
                        return mirror_sources + [official_source]
                    elif source_pref == "tuna":
                        return [mirror_sources[0], official_source]
                    elif source_pref == "ustc":
                        return [mirror_sources[1], official_source]
                    elif source_pref == "nju":
                        return [mirror_sources[2], official_source]
                    elif source_pref == "nyist":
                        return [mirror_sources[3], official_source]
                    else:
                        return [official_source]

                                                                 
                def _download_thread(self, java_version, image_type, arch, os_name, download_dir, source):
                    image_type = "jre"
                    try:
                                                 
                        self.log(f"🔍 正在查询 Java {java_version} (JRE, {arch}, {os_name})...")

                        api_url = (
                            f"{ADOPTIUM_API}/assets/latest/{java_version}/hotspot"
                            f"?architecture={arch}&image_type=jre"
                            f"&os={os_name}&vendor=eclipse"
                        )

                        r = self.session.get(api_url, timeout=30)
                        if r.status_code == 404:
                            self.log(f"❌ Java {java_version} 暂不可用", level='error')
                            self.root.after(0, lambda: messagebox.showwarning(
                                "不可用", f"Java {java_version} 的 JRE 暂未提供下载。"
                            ))
                            return
                        r.raise_for_status()

                        assets = r.json()
                        if not assets:
                            raise Exception(f"未找到 Java {java_version} 的下载包")

                        binary = assets[0]["binary"]
                        package = binary["package"]
                        official_url = package["link"]
                        file_name = package["name"]
                        file_size = package["size"]

                        self.log(f"📦 找到: {file_name}")
                        self.log(f"📊 大小: {file_size / 1024 / 1024:.1f} MB")

                                               
                        target_dir = download_dir / f"java-{java_version}"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        archive_path = target_dir / file_name

                                                
                        sources = self._build_source_list(
                            java_version, image_type, arch, os_name, file_name, official_url, source
                        )

                                                  
                        download_success = False
                        last_error = None

                        for source_name, download_url in sources:
                            self.log(f"🔄 尝试 {source_name}")
                            self.log(f"    {download_url}")

                                     
                            try:
                                test = self.session.head(
                                    download_url, timeout=15, allow_redirects=True,
                                    headers=HEADERS
                                )
                                if test.status_code != 200:
                                    self.log(f"  ⚠️ {source_name} 返回 {test.status_code}，跳过", level='warning')
                                    continue
                            except Exception as e:
                                self.log(f"  ⚠️ {source_name} 探测失败: {e}，跳过", level='warning')
                                continue

                                
                            self.log(f"  📥 开始下载...")
                            self.root.after(0, lambda n=source_name: self.status_label.config(text=f"下载中 ({n})..."))

                            try:
                                self._download_single(download_url, archive_path, file_size)
                                download_success = True
                                self.log(f"  ✅ {source_name} 下载成功")
                                break
                            except Exception as e:
                                last_error = e
                                self.log(f"  ❌ {source_name} 下载失败: {e}", level='warning')
                                if archive_path.exists():
                                    try:
                                        archive_path.unlink()
                                    except:
                                        pass
                                continue

                        if not download_success:
                            raise Exception(f"所有下载源均失败: {last_error}")

                        self.log(f"✅ 下载完成: {archive_path.name}")

                                             
                        self.root.after(0, lambda: self.progress_var.set(100))
                        self.root.after(0, lambda: self.status_label.config(text="解压中..."))
                        self.log("📦 正在解压...")

                        if file_name.endswith('.zip'):
                            with zipfile.ZipFile(archive_path, 'r') as zf:
                                zf.extractall(target_dir)
                        elif file_name.endswith('.tar.gz'):
                            with tarfile.open(archive_path, 'r:gz') as tf:
                                tf.extractall(target_dir)
                        else:
                            raise Exception(f"不支持的压缩格式: {file_name}")

                                               
                                                                
                                                     
                        subdirs = [d for d in target_dir.iterdir() if d.is_dir()]
                        if len(subdirs) == 1:
                            inner = subdirs[0]
                            inner_bin = inner / "bin"
                            if inner_bin.exists() and inner_bin.is_dir():
                                self.log(f"📁 检测到嵌套目录 {inner.name}/，正在整理...")
                                for item in inner.iterdir():
                                    target_item = target_dir / item.name
                                                             
                                    if target_item.exists():
                                        if target_item.is_dir():
                                            shutil.rmtree(target_item, ignore_errors=True)
                                        else:
                                            target_item.unlink()
                                    shutil.move(str(item), str(target_item))
                                try:
                                    inner.rmdir()
                                except:
                                    shutil.rmtree(inner, ignore_errors=True)
                                self.log("✅ 已整理目录结构")
                            else:
                                self.log(f"ℹ️ 子目录 {inner.name}/ 里没有 bin/，跳过整理", level='warning')
                        elif len(subdirs) > 1:
                            self.log(f"ℹ️ 检测到 {len(subdirs)} 个子目录，跳过整理", level='warning')

                                                
                        try:
                            archive_path.unlink()
                            self.log("🧹 已删除临时压缩包")
                        except:
                            pass

                                             
                        if os_name == "windows":
                            java_exe = target_dir / "bin" / "java.exe"
                        else:
                            java_exe = target_dir / "bin" / "java"

                        if not java_exe.exists():
                            raise Exception(f"解压后未找到 java 可执行文件: {java_exe}")

                        self.log(f"✅ Java {java_version} (JRE) 安装成功!")
                        self.log(f"📁 位置: {target_dir}")
                        self.log(f"🔧 可执行文件: {java_exe}")

                        self.root.after(0, lambda: self.progress_var.set(100))
                        self.root.after(0, lambda: self.status_label.config(text="✅ 安装完成"))

                        self.root.after(0, lambda: messagebox.showinfo(
                            "完成",
                            f"Java {java_version} (JRE) 已安装到:\n\n{target_dir}\n\n"
                            f"可执行文件:\n{java_exe}"
                        ))

                    except Exception as e:
                                          
                        err_msg = str(e)
                        self.log(f"❌ 下载失败: {err_msg}", level='error')
                        import traceback
                        self.log(traceback.format_exc(), level='error')

                                             
                        self.root.after(0, lambda: self.status_label.config(text="❌ 下载失败"))
                        self.root.after(0, lambda msg=err_msg, ver=java_version: messagebox.showerror(
                            "下载失败",
                            f"Java {ver} 下载失败:\n\n{msg}\n\n"
                            f"建议：\n"
                            f"1. 尝试「自动」模式\n"
                            f"2. 检查网络连接\n"
                            f"3. 所有镜像都失败，可手动下载"
                        ))
                    finally:
                        self.is_downloading = False
                        self.root.after(0, lambda: self.download_btn.config(state="normal"))

                                                                 
                def _download_single(self, url, target_path, expected_size=0):
                    """单线程下载（带浏览器 UA）"""
                    r = self.session.get(url, stream=True, timeout=60, headers=HEADERS)
                    r.raise_for_status()
                    total = int(r.headers.get('content-length', 0)) or expected_size
                    downloaded = 0
                    start_time = time.time()

                    with open(target_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total > 0:
                                    pct = (downloaded / total) * 100
                                    self.root.after(0, lambda p=pct: self.progress_var.set(p))

                                    if downloaded % (5 * 1024 * 1024) < 65536:
                                        elapsed = time.time() - start_time
                                        speed = (downloaded / 1024 / 1024) / max(elapsed, 0.1)
                                        self.log(
                                            f"  ⬇️ {downloaded // 1024 // 1024} MB / "
                                            f"{total // 1024 // 1024} MB  ({speed:.1f} MB/s)"
                                        )

                    if expected_size > 0:
                        actual = target_path.stat().st_size
                        if actual != expected_size:
                            raise Exception(f"文件大小不匹配: {actual} != {expected_size}")

                                                              
                def on_closing(self):
                    if self.is_downloading:
                        if not messagebox.askyesno("确认", "下载正在进行中，确定要退出吗？"):
                            return
                    self.root.destroy()

                                        
            app = JavaDownloader(parent)
                                              
        def Service():
            import tkinter as tk
            from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
            import threading
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            import os
            import sys
            import time
            import re
            import json
            import zipfile
            import subprocess
            import shutil
            import queue
            import datetime

                                                      
            MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
            FABRIC_META = "https://meta.fabricmc.net/v2/versions"
            PAPER_API = "https://api.papermc.io/v2/projects/paper"
            FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
            BMCLAPI = "https://bmclapi2.bangbang93.com"

            HEADERS = {"User-Agent": "MinecraftJarDownloader/1.0 (contact@example.com)"}

            CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".mc_server_tool.json")

            SERVER_JAR_PATTERNS = [
                re.compile(r"^server\.jar$", re.I),
                re.compile(r"^paper.*\.jar$", re.I),
                re.compile(r"^forge.*\.jar$", re.I),
                re.compile(r"^fabric.*\.jar$", re.I),
                re.compile(r"^spigot.*\.jar$", re.I),
                re.compile(r"^bukkit.*\.jar$", re.I),
                re.compile(r"^purpur.*\.jar$", re.I),
                re.compile(r"^velocity.*\.jar$", re.I),
                re.compile(r"^server.*\.jar$", re.I),
            ]

            MAX_SCAN_DEPTH = 3

            TIME_UNITS = {
                "分钟": 60,
                "小时": 3600,
                "天": 86400,
                "年": 365 * 86400,
            }

            BAN_CHECK_INTERVAL = 30                


            def _make_session():
                s = requests.Session()
                retry = Retry(
                    total=5,
                    backoff_factor=1.0,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "HEAD"],
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                s.headers.update(HEADERS)
                return s


            SESSION = _make_session()


            class MinecraftDownloader:
                def __init__(self, root):
                    self.root = root
                    self.root.title("Minecraft 服务端下载 & 启动器")

                    self.config = self._load_config()
                    geo = self.config.get("window_geometry", "980x980")
                    self.root.geometry(geo)
                    self.root.minsize(920, 900)

                    self.server_type = "Vanilla"
                    self.version_list = []
                    self.downloading = False

                    self.server_proc = None
                    self.log_queue = queue.Queue()
                    self.log_file = None

                    self.servers = self.config.get("servers", [])
                    self.current_server_index = -1

                    self.scan_results = []

                            
                    self.ban_monitor_stop = threading.Event()

                    self._build_ui()
                    self._apply_config()
                    self._refresh_server_listbox()
                    self._on_type_change()
                    self._auto_detect_java_if_needed()
                    self._poll_log_queue()
                    self._start_ban_monitor()

                    self.root.protocol("WM_DELETE_WINDOW", self._on_close)

                                                                                  
                    
                                                                                  
                def _load_config(self):
                    try:
                        if os.path.isfile(CONFIG_FILE):
                            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                                return json.load(f)
                    except Exception as e:
                        print(f"[配置] 读取失败：{e}")
                    return {}

                def _save_config(self):
                    self._update_current_server_from_ui()
                    data = {
                        "window_geometry": self.root.geometry(),
                        "server_type": self.type_var.get(),
                        "channel": self.channel_var.get(),
                        "version": self.version_var.get(),
                        "jar_path": self.path_var.get(),
                        "use_mirror": self.use_mirror_var.get(),
                        "java_path": self.java_var.get(),
                        "workdir": self.workdir_var.get(),
                        "logfile": self.logfile_var.get(),
                        "min_mem": self.min_mem_var.get(),
                        "max_mem": self.max_mem_var.get(),
                        "nogui": self.nogui_var.get(),
                        "auto_eula": self.auto_eula_var.get(),
                        "scan_dir": self.scan_dir_var.get(),
                        "servers": self.servers,
                        "current_server_index": self.current_server_index,
                        "ban_player": self.ban_player_var.get(),
                        "ban_amount": self.ban_amount_var.get(),
                        "ban_unit": self.ban_unit_var.get(),
                        "ban_reason": self.ban_reason_var.get(),
                        "local_bans": self.config.get("local_bans", {}),
                    }
                    try:
                        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"[配置] 保存失败：{e}")

                def _save_config_silent(self):
                    try:
                        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(self.config, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

                def _apply_config(self):
                    c = self.config

                    if c.get("server_type") in ("Vanilla", "Forge", "Fabric", "Paper"):
                        self.type_var.set(c["server_type"])
                    if c.get("channel") in ("正式版", "快照"):
                        self.channel_var.set(c["channel"])
                    if c.get("jar_path"):
                        self.path_var.set(c["jar_path"])
                    if "use_mirror" in c:
                        self.use_mirror_var.set(bool(c["use_mirror"]))
                    if c.get("java_path"):
                        self.java_var.set(c["java_path"])
                    if c.get("workdir"):
                        self.workdir_var.set(c["workdir"])
                    if c.get("logfile"):
                        self.logfile_var.set(c["logfile"])
                    if c.get("min_mem"):
                        self.min_mem_var.set(c["min_mem"])
                    if c.get("max_mem"):
                        self.max_mem_var.set(c["max_mem"])
                    if "nogui" in c:
                        self.nogui_var.set(bool(c["nogui"]))
                    if "auto_eula" in c:
                        self.auto_eula_var.set(bool(c["auto_eula"]))
                    if c.get("scan_dir"):
                        self.scan_dir_var.set(c["scan_dir"])
                    if c.get("ban_player"):
                        self.ban_player_var.set(c["ban_player"])
                    if c.get("ban_amount"):
                        self.ban_amount_var.set(c["ban_amount"])
                    if c.get("ban_unit") in TIME_UNITS or c.get("ban_unit") == "永久":
                        self.ban_unit_var.set(c["ban_unit"])
                    if c.get("ban_reason"):
                        self.ban_reason_var.set(c["ban_reason"])

                    self._pending_version = c.get("version", "")

                                                                                  
                    
                                                                                  
                def _build_ui(self):
                    pad = {"padx": 8, "pady": 5}

                    outer = tk.Frame(self.root)
                    outer.pack(fill="both", expand=True)

                    canvas = tk.Canvas(outer, highlightthickness=0)
                    vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
                    canvas.configure(yscrollcommand=vsb.set)
                    vsb.pack(side="right", fill="y")
                    canvas.pack(side="left", fill="both", expand=True)

                    main = tk.Frame(canvas)
                    canvas.create_window((0, 0), window=main, anchor="nw")

                    def _on_cfg(_):
                        canvas.configure(scrollregion=canvas.bbox("all"))
                    main.bind("<Configure>", _on_cfg)

                    def _on_mousewheel(e):
                        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
                    canvas.bind_all("<MouseWheel>", _on_mousewheel)

                                     
                    dl_frame = tk.LabelFrame(main, text="下载服务端")
                    dl_frame.pack(fill="x", padx=10, pady=(10, 5))

                    tk.Label(dl_frame, text="服务端类型：").grid(row=0, column=0, sticky="w", **pad)
                    self.type_var = tk.StringVar(value="Vanilla")
                    self.type_combo = ttk.Combobox(dl_frame, textvariable=self.type_var,
                                                values=["Vanilla", "Forge", "Fabric", "Paper"],
                                                state="readonly", width=12)
                    self.type_combo.grid(row=0, column=1, sticky="w", **pad)
                    self.type_combo.bind("<<ComboboxSelected>>", lambda e: self._on_type_change())

                    tk.Label(dl_frame, text="发布渠道：").grid(row=0, column=2, sticky="w", **pad)
                    self.channel_var = tk.StringVar(value="正式版")
                    self.channel_combo = ttk.Combobox(dl_frame, textvariable=self.channel_var,
                                                    values=["正式版", "快照"],
                                                    state="readonly", width=10)
                    self.channel_combo.grid(row=0, column=3, sticky="w", **pad)
                    self.channel_combo.bind("<<ComboboxSelected>>", lambda e: self._on_channel_change())

                    tk.Label(dl_frame, text="选择版本：").grid(row=1, column=0, sticky="w", **pad)
                    self.version_var = tk.StringVar()
                    self.version_combo = ttk.Combobox(dl_frame, textvariable=self.version_var,
                                                    state="readonly", width=40)
                    self.version_combo.grid(row=1, column=1, columnspan=3, sticky="w", **pad)

                    tk.Label(dl_frame, text="保存到：").grid(row=2, column=0, sticky="w", **pad)
                    self.path_var = tk.StringVar(value=os.path.abspath("server.jar"))
                    tk.Entry(dl_frame, textvariable=self.path_var, width=58).grid(
                        row=2, column=1, columnspan=2, sticky="we", **pad)
                    tk.Button(dl_frame, text="浏览...", command=self._choose_path).grid(
                        row=2, column=3, **pad)

                    self.use_mirror_var = tk.BooleanVar(value=True)
                    tk.Checkbutton(dl_frame, text="使用国内镜像加速（BMCLAPI，仅原版）",
                                variable=self.use_mirror_var).grid(
                        row=3, column=0, columnspan=2, sticky="w", **pad)

                    self.download_btn = tk.Button(dl_frame, text="开始下载", width=14,
                                                command=self._start_download, state="disabled")
                    self.download_btn.grid(row=4, column=0, columnspan=4, pady=8)

                    self.progress = ttk.Progressbar(dl_frame, orient="horizontal",
                                                    length=900, mode="determinate")
                    self.progress.grid(row=5, column=0, columnspan=4, padx=10, pady=(0, 6), sticky="we")

                    self.status_var = tk.StringVar(value="就绪")
                    tk.Label(dl_frame, textvariable=self.status_var, fg="#1976d2",
                            anchor="w").grid(row=6, column=0, columnspan=4, sticky="we", padx=10)

                    dl_frame.columnconfigure(1, weight=1)

                                       
                    srv_frame = tk.LabelFrame(main, text="服务端列表（保存在 JSON 中）")
                    srv_frame.pack(fill="x", padx=10, pady=5)

                    list_wrap = tk.Frame(srv_frame)
                    list_wrap.grid(row=0, column=0, columnspan=6, sticky="we", padx=6, pady=4)

                    self.server_listbox = tk.Listbox(list_wrap, height=5, activestyle="dotbox")
                    self.server_listbox.pack(side="left", fill="x", expand=True)
                    self.server_listbox.bind("<<ListboxSelect>>",
                                            lambda e: self._on_listbox_select())

                    sb = tk.Scrollbar(list_wrap, orient="vertical",
                                    command=self.server_listbox.yview)
                    sb.pack(side="right", fill="y")
                    self.server_listbox.config(yscrollcommand=sb.set)

                    btn1 = tk.Frame(srv_frame)
                    btn1.grid(row=1, column=0, columnspan=6, pady=4)
                    tk.Button(btn1, text="➕ 添加服务端文件", width=16,
                            command=self._add_server_by_file).pack(side="left", padx=4)
                    tk.Button(btn1, text="➖ 删除选中", width=12,
                            command=self._remove_selected_server).pack(side="left", padx=4)
                    tk.Button(btn1, text="✎ 重命名", width=10,
                            command=self._rename_selected_server).pack(side="left", padx=4)
                    tk.Button(btn1, text="▲ 上移", width=8,
                            command=lambda: self._move_server(-1)).pack(side="left", padx=4)
                    tk.Button(btn1, text="▼ 下移", width=8,
                            command=lambda: self._move_server(1)).pack(side="left", padx=4)
                    tk.Button(btn1, text="✔ 应用选中配置", width=16,
                            command=self._apply_selected_server).pack(side="left", padx=4)

                    scan_row = tk.Frame(srv_frame)
                    scan_row.grid(row=2, column=0, columnspan=6, sticky="we", padx=6, pady=(2, 6))

                    tk.Label(scan_row, text="扫描目录：").pack(side="left")
                    self.scan_dir_var = tk.StringVar(value=os.getcwd())
                    tk.Entry(scan_row, textvariable=self.scan_dir_var, width=40).pack(
                        side="left", padx=4)
                    tk.Button(scan_row, text="浏览", command=self._choose_scan_dir).pack(
                        side="left", padx=2)
                    tk.Button(scan_row, text="扫描", command=self._start_scan).pack(
                        side="left", padx=2)

                    self.scan_combo = ttk.Combobox(scan_row, state="readonly", width=30)
                    self.scan_combo.pack(side="left", padx=4)
                    tk.Button(scan_row, text="加入列表", command=self._add_from_scan).pack(
                        side="left", padx=2)

                    self.scan_status_var = tk.StringVar(value="尚未扫描")
                    tk.Label(srv_frame, textvariable=self.scan_status_var, fg="#555555",
                            anchor="w").grid(row=3, column=0, columnspan=6,
                                            sticky="we", padx=10, pady=(0, 4))

                    srv_frame.columnconfigure(0, weight=1)

                                      
                    run_frame = tk.LabelFrame(main, text="启动配置")
                    run_frame.pack(fill="x", padx=10, pady=5)

                    tk.Label(run_frame, text="服务端 JAR：").grid(row=0, column=0, sticky="w", **pad)
                    tk.Entry(run_frame, textvariable=self.path_var, width=62).grid(
                        row=0, column=1, columnspan=3, sticky="we", **pad)
                    tk.Button(run_frame, text="浏览...", command=self._choose_path).grid(
                        row=0, column=4, **pad)

                    tk.Label(run_frame, text="Java 路径：").grid(row=1, column=0, sticky="w", **pad)
                    self.java_var = tk.StringVar()
                    tk.Entry(run_frame, textvariable=self.java_var, width=62).grid(
                        row=1, column=1, columnspan=3, sticky="we", **pad)
                    tk.Button(run_frame, text="浏览...", command=self._choose_java).grid(
                        row=1, column=4, **pad)

                    tk.Label(run_frame, text="工作目录：").grid(row=2, column=0, sticky="w", **pad)
                    self.workdir_var = tk.StringVar()
                    tk.Entry(run_frame, textvariable=self.workdir_var, width=62).grid(
                        row=2, column=1, columnspan=3, sticky="we", **pad)
                    tk.Button(run_frame, text="浏览...", command=self._choose_workdir).grid(
                        row=2, column=4, **pad)

                    tk.Label(run_frame, text="日志文件：").grid(row=3, column=0, sticky="w", **pad)
                    self.logfile_var = tk.StringVar()
                    tk.Entry(run_frame, textvariable=self.logfile_var, width=62).grid(
                        row=3, column=1, columnspan=3, sticky="we", **pad)
                    tk.Button(run_frame, text="浏览...", command=self._choose_logfile).grid(
                        row=3, column=4, **pad)

                    tk.Label(run_frame, text="最小内存 (MB)：").grid(row=4, column=0, sticky="w", **pad)
                    self.min_mem_var = tk.StringVar(value="1024")
                    tk.Entry(run_frame, textvariable=self.min_mem_var, width=10).grid(
                        row=4, column=1, sticky="w", **pad)

                    tk.Label(run_frame, text="最大内存 (MB)：").grid(row=4, column=2, sticky="w", **pad)
                    self.max_mem_var = tk.StringVar(value="2048")
                    tk.Entry(run_frame, textvariable=self.max_mem_var, width=10).grid(
                        row=4, column=3, sticky="w", **pad)

                    self.nogui_var = tk.BooleanVar(value=True)
                    tk.Checkbutton(run_frame, text="使用 nogui",
                                variable=self.nogui_var).grid(
                        row=4, column=4, sticky="w", **pad)

                    self.auto_eula_var = tk.BooleanVar(value=True)
                    tk.Checkbutton(run_frame, text="自动填写 eula.txt（eula=true）",
                                variable=self.auto_eula_var).grid(
                        row=5, column=0, columnspan=3, sticky="w", **pad)

                    btn_frame = tk.Frame(run_frame)
                    btn_frame.grid(row=6, column=0, columnspan=5, pady=8)
                    self.start_btn = tk.Button(btn_frame, text="▶ 启动服务端", width=16,
                                            bg="#4CAF50", fg="white",
                                            command=self._start_server)
                    self.start_btn.pack(side="left", padx=6)
                    self.stop_btn = tk.Button(btn_frame, text="■ 关闭服务端", width=16,
                                            bg="#f44336", fg="white",
                                            command=self._stop_server, state="disabled")
                    self.stop_btn.pack(side="left", padx=6)
                    self.kill_btn = tk.Button(btn_frame, text="✖ 强制结束", width=16,
                                            command=self._kill_server, state="disabled")
                    self.kill_btn.pack(side="left", padx=6)
                    self.open_dir_btn = tk.Button(btn_frame, text="📂 打开工作目录", width=16,
                                                command=self._open_workdir)
                    self.open_dir_btn.pack(side="left", padx=6)

                    run_frame.columnconfigure(1, weight=1)

                                      
                    cmd_frame = tk.LabelFrame(main, text="向服务器发送命令")
                    cmd_frame.pack(fill="x", padx=10, pady=5)

                    tk.Label(cmd_frame, text="命令：").grid(row=0, column=0, sticky="w", **pad)
                    self.cmd_var = tk.StringVar()
                    self.cmd_entry = tk.Entry(cmd_frame, textvariable=self.cmd_var, width=58)
                    self.cmd_entry.grid(row=0, column=1, columnspan=3, sticky="we", **pad)
                    self.cmd_entry.bind("<Return>", lambda e: self._send_command())
                    tk.Button(cmd_frame, text="发送", width=10,
                            command=self._send_command).grid(row=0, column=4, **pad)

                    quick_row = tk.Frame(cmd_frame)
                    quick_row.grid(row=1, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 6))
                    tk.Label(quick_row, text="快捷：").pack(side="left")
                    for text, cmd in [
                        ("列出玩家", "list"),
                        ("保存世界", "save-all"),
                        ("重载白名单", "whitelist reload"),
                        ("切换白天", "time set day"),
                        ("切换夜晚", "time set night"),
                        ("晴天", "weather clear"),
                        ("雷雨", "weather thunder"),
                    ]:
                        tk.Button(quick_row, text=text, width=10,
                                command=lambda c=cmd: self._quick_command(c)
                                ).pack(side="left", padx=2)

                    cmd_frame.columnconfigure(1, weight=1)

                                         
                    ban_frame = tk.LabelFrame(main, text="封禁 / 解封玩家")
                    ban_frame.pack(fill="x", padx=10, pady=5)

                    tk.Label(ban_frame, text="玩家 ID：").grid(row=0, column=0, sticky="w", **pad)
                    self.ban_player_var = tk.StringVar()
                    tk.Entry(ban_frame, textvariable=self.ban_player_var, width=20).grid(
                        row=0, column=1, sticky="w", **pad)

                    tk.Label(ban_frame, text="时长：").grid(row=0, column=2, sticky="w", **pad)
                    self.ban_amount_var = tk.StringVar(value="1")
                    tk.Entry(ban_frame, textvariable=self.ban_amount_var, width=8).grid(
                        row=0, column=3, sticky="w", **pad)

                    self.ban_unit_var = tk.StringVar(value="天")
                    unit_combo = ttk.Combobox(ban_frame, textvariable=self.ban_unit_var,
                                            values=["分钟", "小时", "天", "年", "永久"],
                                            state="readonly", width=8)
                    unit_combo.grid(row=0, column=4, sticky="w", **pad)

                    tk.Label(ban_frame, text="原因：").grid(row=1, column=0, sticky="w", **pad)
                    self.ban_reason_var = tk.StringVar(value="违反服务器规则")
                    tk.Entry(ban_frame, textvariable=self.ban_reason_var, width=52).grid(
                        row=1, column=1, columnspan=4, sticky="we", **pad)

                    ban_btn_row = tk.Frame(ban_frame)
                    ban_btn_row.grid(row=2, column=0, columnspan=8, pady=6)
                    tk.Button(ban_btn_row, text="🚫 封禁", width=12, bg="#d32f2f", fg="white",
                            command=self._ban_player).pack(side="left", padx=6)
                    tk.Button(ban_btn_row, text="✔ 解封", width=12, bg="#388e3c", fg="white",
                            command=self._unban_player).pack(side="left", padx=6)
                    tk.Button(ban_btn_row, text="📋 查看封禁列表", width=16,
                            command=self._show_banned_list).pack(side="left", padx=6)
                    tk.Button(ban_btn_row, text="🧹 清理过期封禁", width=16,
                            command=self._purge_expired_bans).pack(side="left", padx=6)
                    tk.Button(ban_btn_row, text="🔄 立即检查自动解封", width=18,
                            command=self._check_and_unban_expired).pack(side="left", padx=6)

                    self.ban_status_var = tk.StringVar(value="未操作")
                    tk.Label(ban_frame, textvariable=self.ban_status_var, fg="#555555",
                            anchor="w").grid(row=3, column=0, columnspan=8,
                                            sticky="we", padx=10, pady=(2, 6))

                    ban_frame.columnconfigure(1, weight=1)

                                    
                    log_frame = tk.LabelFrame(main, text="服务器日志")
                    log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

                    self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word",
                                                            height=12, state="disabled",
                                                            font=("Consolas", 9))
                    self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

                    self.log_text.tag_config("info", foreground="#333333")
                    self.log_text.tag_config("warn", foreground="#b58900")
                    self.log_text.tag_config("error", foreground="#d32f2f")
                    self.log_text.tag_config("system", foreground="#1976d2")

                                                                                  
                       
                                                                                  
                def _refresh_server_listbox(self):
                    self.server_listbox.delete(0, "end")
                    for i, srv in enumerate(self.servers):
                        name = srv.get("name") or os.path.basename(srv.get("path", ""))
                        path = srv.get("path", "")
                        exists = "✔" if os.path.isfile(path) else "✘"
                        self.server_listbox.insert("end", f"{exists} {name}  —  {path}")
                    if 0 <= self.current_server_index < len(self.servers):
                        self.server_listbox.selection_set(self.current_server_index)
                        self.server_listbox.activate(self.current_server_index)

                def _on_listbox_select(self):
                    sel = self.server_listbox.curselection()
                    if not sel:
                        return
                    self.current_server_index = sel[0]

                def _apply_selected_server(self):
                    sel = self.server_listbox.curselection()
                    if not sel:
                        messagebox.showinfo("提示", "请先在列表中选中一个服务端")
                        return
                    idx = sel[0]
                    srv = self.servers[idx]
                    self.current_server_index = idx

                    self.path_var.set(srv.get("path", ""))
                    self.java_var.set(srv.get("java") or self.java_var.get())
                    self.workdir_var.set(srv.get("workdir") or
                                        os.path.dirname(os.path.abspath(srv.get("path", ""))))
                    self.logfile_var.set(srv.get("logfile") or
                                        os.path.join(self.workdir_var.get(), "server-console.log"))
                    self.min_mem_var.set(str(srv.get("min_mem", "1024")))
                    self.max_mem_var.set(str(srv.get("max_mem", "2048")))
                    self.nogui_var.set(bool(srv.get("nogui", True)))
                    self._set_status(f"已应用：{srv.get('name')}")

                def _update_current_server_from_ui(self):
                    if not (0 <= self.current_server_index < len(self.servers)):
                        return
                    srv = self.servers[self.current_server_index]
                    srv["path"] = self.path_var.get().strip()
                    srv["java"] = self.java_var.get().strip()
                    srv["workdir"] = self.workdir_var.get().strip()
                    srv["logfile"] = self.logfile_var.get().strip()
                    srv["min_mem"] = self.min_mem_var.get().strip()
                    srv["max_mem"] = self.max_mem_var.get().strip()
                    srv["nogui"] = bool(self.nogui_var.get())

                def _add_server_by_file(self):
                    path = filedialog.askopenfilename(
                        title="选择服务端 JAR",
                        filetypes=[("JAR 文件", "*.jar"), ("所有文件", "*.*")]
                    )
                    if not path:
                        return
                    self._add_server_entry(path)

                def _add_server_entry(self, path, name=None):
                    path = os.path.abspath(path)
                    for srv in self.servers:
                        if os.path.abspath(srv.get("path", "")) == path:
                            messagebox.showinfo("提示", "该服务端已在列表中")
                            return
                    if name is None:
                        name = os.path.splitext(os.path.basename(path))[0]

                    jar_dir = os.path.dirname(path)
                    entry = {
                        "name": name,
                        "path": path,
                        "java": self.java_var.get().strip(),
                        "workdir": jar_dir,
                        "logfile": os.path.join(jar_dir, "server-console.log"),
                        "min_mem": self.min_mem_var.get().strip() or "1024",
                        "max_mem": self.max_mem_var.get().strip() or "2048",
                        "nogui": True,
                    }
                    self.servers.append(entry)
                    self.current_server_index = len(self.servers) - 1
                    self._refresh_server_listbox()
                    self._set_status(f"已添加：{name}")

                def _remove_selected_server(self):
                    sel = self.server_listbox.curselection()
                    if not sel:
                        messagebox.showinfo("提示", "请先选中一个服务端")
                        return
                    idx = sel[0]
                    name = self.servers[idx].get("name", "")
                    if not messagebox.askyesno("确认", f"从列表中删除“{name}”？（不会删除磁盘上的文件）"):
                        return
                    self.servers.pop(idx)
                    if self.current_server_index >= len(self.servers):
                        self.current_server_index = len(self.servers) - 1
                    self._refresh_server_listbox()

                def _rename_selected_server(self):
                    sel = self.server_listbox.curselection()
                    if not sel:
                        messagebox.showinfo("提示", "请先选中一个服务端")
                        return
                    idx = sel[0]
                    old = self.servers[idx].get("name", "")
                    new = simpledialog.askstring("重命名", "输入新名称：", initialvalue=old)
                    if new:
                        self.servers[idx]["name"] = new.strip()
                        self._refresh_server_listbox()
                        self.server_listbox.selection_set(idx)

                def _move_server(self, delta):
                    sel = self.server_listbox.curselection()
                    if not sel:
                        return
                    idx = sel[0]
                    new_idx = idx + delta
                    if new_idx < 0 or new_idx >= len(self.servers):
                        return
                    self.servers[idx], self.servers[new_idx] = \
                        self.servers[new_idx], self.servers[idx]
                    self.current_server_index = new_idx
                    self._refresh_server_listbox()
                    self.server_listbox.selection_set(new_idx)

                                                                                  
                    
                                                                                  
                def _choose_scan_dir(self):
                    path = filedialog.askdirectory(title="选择要扫描的目录")
                    if path:
                        self.scan_dir_var.set(path)

                def _start_scan(self):
                    scan_dir = self.scan_dir_var.get().strip()
                    if not scan_dir or not os.path.isdir(scan_dir):
                        messagebox.showwarning("提示", "请选择有效的扫描目录")
                        return
                    self.scan_status_var.set(f"正在扫描 {scan_dir} ...")
                    self.scan_combo.set("")
                    self.scan_results = []
                    threading.Thread(target=self._scan_worker, args=(scan_dir,), daemon=True).start()

                def _scan_worker(self, scan_dir):
                    found = []
                    try:
                        base_depth = scan_dir.rstrip(os.sep).count(os.sep)
                        for root_dir, dirs, files in os.walk(scan_dir):
                            dirs[:] = [d for d in dirs
                                    if d.lower() not in (".git", "node_modules", "__pycache__",
                                                            "logs", "world", "world_nether",
                                                            "world_the_end", "cache", "libraries",
                                                            "versions", "mods", "plugins")]
                            depth = root_dir.rstrip(os.sep).count(os.sep) - base_depth
                            if depth >= MAX_SCAN_DEPTH:
                                dirs[:] = []
                            for name in files:
                                if not name.lower().endswith(".jar"):
                                    continue
                                full = os.path.join(root_dir, name)
                                if self._looks_like_server_jar(name, full):
                                    found.append(full)
                    except Exception as e:
                        self.log_queue.put(f"[系统] 扫描出错：{e}\n")

                    found = sorted(set(found), key=lambda p: p.lower())
                    labeled = [(f"{self._guess_server_kind(p)} | {p}", p) for p in found]
                    self.root.after(0, lambda: self._on_scan_done(labeled))

                def _looks_like_server_jar(self, name, full_path):
                    for pat in SERVER_JAR_PATTERNS:
                        if pat.match(name):
                            return True
                    try:
                        with zipfile.ZipFile(full_path) as z:
                            if "META-INF/MANIFEST.MF" not in z.namelist():
                                return False
                            with z.open("META-INF/MANIFEST.MF") as f:
                                content = f.read().decode("utf-8", errors="replace")
                            keywords = ["net.minecraft.server", "net.minecraftforge",
                                        "net.fabricmc", "io.papermc", "org.bukkit",
                                        "org.spigotmc", "com.destroystokyo"]
                            low = content.lower()
                            return any(k.lower() in low for k in keywords)
                    except Exception:
                        return False

                def _guess_server_kind(self, path):
                    name = os.path.basename(path).lower()
                    for key, label in [("paper", "Paper"), ("purpur", "Purpur"),
                                    ("spigot", "Spigot"), ("bukkit", "Bukkit"),
                                    ("forge", "Forge"), ("fabric", "Fabric"),
                                    ("velocity", "Velocity")]:
                        if key in name:
                            return label
                    return "原版/未知"

                def _on_scan_done(self, labeled):
                    self.scan_results = labeled
                    if not labeled:
                        self.scan_combo["values"] = []
                        self.scan_status_var.set("未找到服务端 JAR")
                        return
                    self.scan_combo["values"] = [x[0] for x in labeled]
                    self.scan_combo.current(0)
                    self.scan_status_var.set(f"找到 {len(labeled)} 个可能的服务端")

                def _add_from_scan(self):
                    idx = self.scan_combo.current()
                    if idx < 0 or idx >= len(self.scan_results):
                        messagebox.showinfo("提示", "请先扫描并选择一个结果")
                        return
                    _, path = self.scan_results[idx]
                    self._add_server_entry(path)

                                                                                  
                         
                                                                                  
                def _on_type_change(self):
                    self.server_type = self.type_var.get()
                    if self.server_type == "Vanilla":
                        self.channel_combo.config(state="readonly")
                    else:
                        self.channel_var.set("正式版")
                        self.channel_combo.config(state="disabled")
                    self._reload_versions()

                def _on_channel_change(self):
                    if self.server_type != "Vanilla":
                        return
                    self._reload_versions()

                def _reload_versions(self):
                    self.version_combo.set("")
                    self.version_list = []
                    self.download_btn.config(state="disabled")
                    self._set_status(f"正在加载 {self.server_type} 版本列表...")
                    threading.Thread(target=self._load_versions, daemon=True).start()

                def _load_versions(self):
                    try:
                        if self.server_type == "Vanilla":
                            data = self._fetch_vanilla_versions()
                        elif self.server_type == "Forge":
                            data = self._fetch_forge_versions()
                        elif self.server_type == "Fabric":
                            data = self._fetch_fabric_versions()
                        elif self.server_type == "Paper":
                            data = self._fetch_paper_versions()
                        else:
                            data = []

                        self.version_list = data
                        display = [item[0] for item in data]
                        self.root.after(0, lambda: self.version_combo.configure(values=display))

                        if display:
                            want = getattr(self, "_pending_version", "") or ""
                            idx = 0
                            if want:
                                for i, txt in enumerate(display):
                                    if txt == want or txt.startswith(want + " ") or txt.startswith(want):
                                        idx = i
                                        break
                                self._pending_version = ""
                            self.root.after(0, lambda i=idx: self.version_combo.current(i))
                            self.root.after(0, lambda: self.download_btn.config(state="normal"))

                        self._set_status(f"{self.server_type}：已加载 {len(data)} 个版本")
                    except Exception as e:
                        self._set_status(f"加载版本失败：{e}")
                        self.root.after(0, lambda: messagebox.showerror("错误", f"获取版本列表失败：\n{e}"))

                                                                                  
                      
                                                                                  
                def _fetch_vanilla_versions(self):
                    r = SESSION.get(MOJANG_MANIFEST, timeout=15)
                    r.raise_for_status()
                    manifest = r.json()
                    want_type = "release" if self.channel_var.get() == "正式版" else "snapshot"
                    result = []
                    for v in manifest["versions"]:
                        if v["type"] != want_type:
                            continue
                        label = v["id"] if want_type == "release" else f"{v['id']}  (快照)"
                        result.append((label, {"id": v["id"], "url": v["url"]}))
                    return result

                def _fetch_forge_versions(self):
                    mc_versions = [
                        "1.21.4", "1.21.3", "1.21.1", "1.21", "1.20.6", "1.20.4",
                        "1.20.2", "1.20.1", "1.20", "1.19.4", "1.19.2", "1.18.2",
                        "1.17.1", "1.16.5", "1.15.2", "1.14.4", "1.12.2", "1.8.9"
                    ]
                    return [(mc, {"mc": mc}) for mc in mc_versions]

                def _fetch_fabric_versions(self):
                    r = SESSION.get(f"{FABRIC_META}/game", timeout=15)
                    r.raise_for_status()
                    games = r.json()
                    r2 = SESSION.get(f"{FABRIC_META}/loader", timeout=15)
                    r2.raise_for_status()
                    loaders = r2.json()
                    latest_loader = loaders[0]["version"] if loaders else ""
                    result = []
                    for g in games:
                        if not g.get("stable", False):
                            continue
                        result.append((g["version"], {"mc": g["version"], "loader": latest_loader}))
                    return result[:30]

                def _fetch_paper_versions(self):
                    r = SESSION.get(f"{PAPER_API}", timeout=20)
                    r.raise_for_status()
                    data = r.json()
                    versions = list(reversed(data["versions"]))
                    return [(v, {"mc": v}) for v in versions]

                                                                                  
                      
                                                                                  
                def _choose_path(self):
                    path = filedialog.asksaveasfilename(
                        defaultextension=".jar",
                        filetypes=[("JAR 文件", "*.jar"), ("所有文件", "*.*")],
                        initialfile=self._default_filename()
                    )
                    if path:
                        self.path_var.set(path)
                        self._sync_run_paths_from_jar(path)

                def _default_filename(self):
                    return {
                        "Vanilla": "server.jar",
                        "Forge": "forge-installer.jar",
                        "Fabric": "fabric-installer.jar",
                        "Paper": "paper.jar",
                    }.get(self.server_type, "server.jar")

                def _choose_java(self):
                    path = filedialog.askopenfilename(
                        title="选择 Java 可执行文件",
                        filetypes=[("Java 可执行文件", "java.exe java"), ("所有文件", "*.*")]
                    )
                    if path:
                        self.java_var.set(path)

                def _choose_workdir(self):
                    path = filedialog.askdirectory(title="选择服务器工作目录")
                    if path:
                        self.workdir_var.set(path)

                def _choose_logfile(self):
                    path = filedialog.asksaveasfilename(
                        title="选择日志输出文件",
                        defaultextension=".log",
                        filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
                        initialfile="server-console.log"
                    )
                    if path:
                        self.logfile_var.set(path)

                def _open_workdir(self):
                    path = self.workdir_var.get().strip()
                    if not path or not os.path.isdir(path):
                        messagebox.showinfo("提示", "工作目录尚未设置或不存在")
                        return
                    try:
                        if os.name == "nt":
                            os.startfile(path)
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", path])
                        else:
                            subprocess.Popen(["xdg-open", path])
                    except Exception as e:
                        messagebox.showerror("错误", f"无法打开目录：{e}")

                def _sync_run_paths_from_jar(self, jar_path):
                    jar_dir = os.path.dirname(os.path.abspath(jar_path))
                    if not self.workdir_var.get().strip():
                        self.workdir_var.set(jar_dir)
                    if not self.logfile_var.get().strip():
                        self.logfile_var.set(os.path.join(jar_dir, "server-console.log"))

                def _auto_detect_java_if_needed(self):
                    if self.java_var.get().strip() and os.path.isfile(self.java_var.get().strip()):
                        return
                    java_home = os.environ.get("JAVA_HOME")
                    if java_home:
                        candidate = os.path.join(java_home, "bin",
                                                "java.exe" if os.name == "nt" else "java")
                        if os.path.isfile(candidate):
                            self.java_var.set(candidate)
                            return
                    java = shutil.which("java")
                    if java:
                        self.java_var.set(java)
                        return
                    if os.name == "nt":
                        program_dirs = [d for d in (
                            os.environ.get("ProgramFiles", ""),
                            os.environ.get("ProgramFiles(x86)", ""),
                        ) if d]
                        for program_dir in program_dirs:
                            for sub in ("Java", "Eclipse Adoptium", "Microsoft"):
                                base = os.path.join(program_dir, sub)
                                if not os.path.isdir(base):
                                    continue
                                for name in os.listdir(base):
                                    candidate = os.path.join(base, name, "bin", "java.exe")
                                    if os.path.isfile(candidate):
                                        self.java_var.set(candidate)
                                        return
                    self._log("[系统] 未自动检测到 Java，请手动选择 java 可执行文件。\n", "warn")

                                                                                  
                    
                                                                                  
                def _start_download(self):
                    if self.downloading:
                        return
                    idx = self.version_combo.current()
                    if idx < 0 or idx >= len(self.version_list):
                        messagebox.showwarning("提示", "请选择一个版本")
                        return
                    save_path = self.path_var.get().strip()
                    if not save_path:
                        messagebox.showwarning("提示", "请选择保存路径")
                        return

                    self.downloading = True
                    self.download_btn.config(state="disabled")
                    self.progress["value"] = 0
                    threading.Thread(target=self._download, args=(idx, save_path), daemon=True).start()

                def _download(self, idx, save_path):
                    try:
                        display, meta = self.version_list[idx]
                        self._set_status(f"正在解析 {self.server_type} {display} 下载地址...")

                        if self.server_type == "Vanilla":
                            url, total, source = self._resolve_vanilla(meta)
                        elif self.server_type == "Forge":
                            url, total, source = self._resolve_forge(meta)
                        elif self.server_type == "Fabric":
                            url, total, source = self._resolve_fabric(meta)
                        elif self.server_type == "Paper":
                            url, total, source = self._resolve_paper(meta)
                        else:
                            raise ValueError("未知服务端类型")

                        self._set_status(f"正在从 {source} 下载 {display} ...")
                        self._stream_download(url, save_path, total)

                        self.root.after(0, lambda: self.progress.configure(value=100))
                        self._set_status(f"✅ 下载完成：{save_path}")
                        self.root.after(0, lambda p=save_path: self._sync_run_paths_from_jar(p))
                        self.root.after(0, lambda: messagebox.showinfo(
                            "完成", f"{self.server_type} {display} 已保存到：\n{save_path}\n\n"
                                    f"可点击“➕ 添加服务端文件”把它加入列表。"))
                    except Exception as e:
                        self._set_status(f"❌ 下载失败：{e}")
                        self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
                    finally:
                        self.downloading = False
                        self.root.after(0, lambda: self.download_btn.config(state="normal"))

                def _resolve_vanilla(self, meta):
                    version = meta["id"]
                    if self.use_mirror_var.get():
                        mirror = f"{BMCLAPI}/version/{version}/server"
                        try:
                            r = SESSION.head(mirror, timeout=8, allow_redirects=True)
                            if r.status_code == 200:
                                total = int(r.headers.get("Content-Length", 0))
                                return mirror, total, "BMCLAPI 镜像"
                        except Exception:
                            pass
                    detail = SESSION.get(meta["url"], timeout=15).json()
                    server = detail["downloads"].get("server")
                    if not server:
                        raise ValueError(f"{version} 没有服务端 JAR")
                    return server["url"], server.get("size", 0), "Mojang 官方"

                def _resolve_forge(self, meta):
                    mc = meta["mc"]
                    r = SESSION.get(f"{FORGE_MAVEN}/maven-metadata.xml", timeout=15)
                    r.raise_for_status()
                    versions = re.findall(r"<version>([^<]+)</version>", r.text)
                    candidates = [v for v in versions if v.startswith(f"{mc}-")]
                    if not candidates:
                        raise ValueError(f"找不到 {mc} 对应的 Forge 版本")
                    candidates.sort(key=lambda x: [int(p) if p.isdigit() else p
                                                    for p in x.replace("-", ".").split(".")])
                    forge_ver = candidates[-1]
                    url = f"{FORGE_MAVEN}/{forge_ver}/forge-{forge_ver}-installer.jar"
                    return url, 0, "Forge Maven"

                def _resolve_fabric(self, meta):
                    installer_ver = "1.1.2"
                    try:
                        r = SESSION.get(f"{FABRIC_META}/installer", timeout=30)
                        r.raise_for_status()
                        inst = r.json()
                        if inst:
                            installer_ver = inst[0]["version"]
                    except Exception:
                        pass
                    url = (f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/"
                        f"{installer_ver}/fabric-installer-{installer_ver}.jar")
                    return url, 0, "Fabric Maven"

                def _resolve_paper(self, meta):
                    mc = meta["mc"]
                    r = SESSION.get(f"{PAPER_API}/versions/{mc}/builds", timeout=20)
                    r.raise_for_status()
                    builds = r.json()
                    if not builds:
                        raise ValueError(f"Paper 没有 {mc} 的构建版本")
                    stable = [b for b in builds if b.get("channel") == "STABLE"]
                    build = stable[-1] if stable else builds[-1]
                    build_num = build["build"]
                    url = (f"{PAPER_API}/versions/{mc}/builds/{build_num}/downloads/"
                        f"{build['downloads']['application']['name']}")
                    return url, build["downloads"]["application"].get("size", 0), "PaperMC"

                def _stream_download(self, url, save_path, total):
                    chunk_size = 1024 * 1024
                    last_ui = 0.0
                    downloaded = 0
                    start = time.time()

                    with SESSION.get(url, stream=True, timeout=(10, 60)) as r:
                        r.raise_for_status()
                        if not total:
                            total = int(r.headers.get("Content-Length", 0))

                        with open(save_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=chunk_size):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                downloaded += len(chunk)

                                now = time.time()
                                if now - last_ui < 0.3:
                                    continue
                                last_ui = now

                                elapsed = now - start + 1e-6
                                speed = downloaded / elapsed / 1024 / 1024
                                if total:
                                    percent = downloaded / total * 100
                                    self.root.after(0, lambda p=percent: self.progress.configure(value=p))
                                    self._set_status(
                                        f"下载中 {downloaded/1024/1024:.1f} MB / "
                                        f"{total/1024/1024:.1f} MB  ({speed:.2f} MB/s)")
                                else:
                                    self._set_status(
                                        f"下载中 {downloaded/1024/1024:.1f} MB  ({speed:.2f} MB/s)")

                    elapsed = time.time() - start + 1e-6
                    avg = downloaded / elapsed / 1024 / 1024
                    if total:
                        self.root.after(0, lambda: self.progress.configure(value=100))
                    self._set_status(
                        f"下载完成：{downloaded/1024/1024:.1f} MB，平均 {avg:.2f} MB/s")

                                                                                  
                      
                                                                                  
                def _ensure_eula(self, work_dir):
                    if not self.auto_eula_var.get():
                        return True
                    eula_path = os.path.join(work_dir, "eula.txt")
                    try:
                        if os.path.isfile(eula_path):
                            with open(eula_path, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                            new_content = re.sub(r"(?im)^\s*eula\s*=\s*.*$", "eula=true", content)
                            if "eula=" not in content.lower():
                                new_content = content.rstrip() + "\neula=true\n"
                            if new_content != content:
                                with open(eula_path, "w", encoding="utf-8") as f:
                                    f.write(new_content)
                                self._log(f"[系统] 已更新 eula.txt：{eula_path}\n", "system")
                        else:
                            with open(eula_path, "w", encoding="utf-8") as f:
                                f.write(
                                    "#By changing the setting below to TRUE you are indicating "
                                    "your agreement to our EULA "
                                    "(https://aka.ms/MinecraftEULA).\n"
                                    f"#生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                    "eula=true\n"
                                )
                            self._log(f"[系统] 已创建 eula.txt：{eula_path}\n", "system")
                        return True
                    except Exception as e:
                        self._log(f"[系统] 自动填写 eula.txt 失败：{e}\n", "warn")
                        return False

                                                                                  
                       
                                                                                  
                def _start_server(self):
                    if self.server_proc is not None and self.server_proc.poll() is None:
                        messagebox.showinfo("提示", "服务器已在运行中")
                        return

                    java = self.java_var.get().strip()
                    if not java or not os.path.isfile(java):
                        messagebox.showwarning("提示", "请先选择有效的 Java 可执行文件")
                        return

                    jar = self.path_var.get().strip()
                    if not jar or not os.path.isfile(jar):
                        messagebox.showwarning("提示", "找不到服务端 JAR，请先下载或从列表里选择")
                        return

                    work_dir = self.workdir_var.get().strip()
                    if not work_dir:
                        work_dir = os.path.dirname(os.path.abspath(jar))
                        self.workdir_var.set(work_dir)
                    if not os.path.isdir(work_dir):
                        try:
                            os.makedirs(work_dir, exist_ok=True)
                        except Exception as e:
                            messagebox.showerror("错误", f"无法创建工作目录：{e}")
                            return

                    log_path = self.logfile_var.get().strip()
                    if not log_path:
                        log_path = os.path.join(work_dir, "server-console.log")
                        self.logfile_var.set(log_path)

                    try:
                        min_mem = int(self.min_mem_var.get())
                        max_mem = int(self.max_mem_var.get())
                        if min_mem <= 0 or max_mem <= 0:
                            raise ValueError
                        if min_mem > max_mem:
                            raise ValueError("最小内存不能大于最大内存")
                    except ValueError as e:
                        messagebox.showwarning("提示", f"内存参数不合法：{e}")
                        return

                    self._ensure_eula(work_dir)

                    cmd = [
                        java,
                        f"-Xms{min_mem}M",
                        f"-Xmx{max_mem}M",
                        "-jar", os.path.abspath(jar),
                    ]
                    if self.nogui_var.get():
                        cmd.append("nogui")

                    self._log(f"[系统] 工作目录：{work_dir}\n", "system")
                    self._log(f"[系统] 日志文件：{log_path}\n", "system")
                    self._log(f"[系统] 启动命令：{' '.join(cmd)}\n", "system")

                    try:
                        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
                        self.log_file = open(log_path, "a", encoding="utf-8", errors="replace")
                        self.log_file.write(f"\n===== 启动于 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                        self.log_file.flush()
                    except Exception as e:
                        self._log(f"[系统] 无法打开日志文件：{e}\n", "warn")
                        self.log_file = None

                    try:
                        self.server_proc = subprocess.Popen(
                            cmd,
                            cwd=work_dir,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                    except Exception as e:
                        self._log(f"[系统] 启动失败：{e}\n", "error")
                        messagebox.showerror("错误", f"启动服务端失败：\n{e}")
                        self.server_proc = None
                        if self.log_file:
                            try:
                                self.log_file.close()
                            except Exception:
                                pass
                            self.log_file = None
                        return

                    self.start_btn.config(state="disabled")
                    self.stop_btn.config(state="normal")
                    self.kill_btn.config(state="normal")
                    self._log("[系统] 服务端已启动。\n", "system")

                    threading.Thread(target=self._read_server_output, daemon=True).start()
                    threading.Thread(target=self._watch_process, daemon=True).start()

                def _read_server_output(self):
                    proc = self.server_proc
                    try:
                        for line in iter(proc.stdout.readline, ""):
                            if not line:
                                break
                            self.log_queue.put(line)
                            if self.log_file:
                                try:
                                    self.log_file.write(line)
                                    self.log_file.flush()
                                except Exception:
                                    pass
                    except Exception as e:
                        self.log_queue.put(f"[系统] 读取输出异常：{e}\n")

                def _watch_process(self):
                    proc = self.server_proc
                    if proc is None:
                        return
                    proc.wait()
                    code = proc.returncode
                    self.log_queue.put(f"[系统] 服务端已退出，返回码 {code}\n")
                    self.root.after(0, self._on_server_exit)

                def _on_server_exit(self):
                    self.server_proc = None
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self.kill_btn.config(state="disabled")
                    if self.log_file:
                        try:
                            self.log_file.write(
                                f"===== 退出于 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n\n")
                            self.log_file.flush()
                            self.log_file.close()
                        except Exception:
                            pass
                        self.log_file = None

                def _stop_server(self):
                    proc = self.server_proc
                    if proc is None or proc.poll() is not None:
                        return
                    try:
                        self._log("[系统] 正在发送 stop 命令...\n", "system")
                        proc.stdin.write("stop\n")
                        proc.stdin.flush()
                    except Exception as e:
                        self._log(f"[系统] 发送 stop 失败：{e}\n", "error")
                        messagebox.showwarning("提示", f"无法发送 stop 命令：\n{e}\n可尝试“强制结束”。")

                def _kill_server(self):
                    proc = self.server_proc
                    if proc is None or proc.poll() is not None:
                        return
                    if not messagebox.askyesno("确认", "强制结束可能导致世界数据损坏，确定继续？"):
                        return
                    try:
                        proc.kill()
                        self._log("[系统] 已强制结束服务端进程。\n", "warn")
                    except Exception as e:
                        self._log(f"[系统] 强制结束失败：{e}\n", "error")

                                                                                  
                      
                                                                                  
                def _send_command(self):
                    cmd = self.cmd_var.get().strip()
                    if not cmd:
                        return
                    self._send_raw_command(cmd)
                    self.cmd_var.set("")

                def _quick_command(self, cmd):
                    self._send_raw_command(cmd)

                def _send_raw_command(self, cmd):
                    proc = self.server_proc
                    if proc is None or proc.poll() is not None:
                        messagebox.showinfo("提示", "服务器未运行，无法发送命令")
                        return False
                    try:
                        proc.stdin.write(cmd + "\n")
                        proc.stdin.flush()
                        self._log(f"> {cmd}\n", "system")
                        return True
                    except Exception as e:
                        self._log(f"[系统] 发送命令失败：{e}\n", "error")
                        return False

                                                                                  
                         
                                                                                  
                def _ban_player(self):
                    player = self.ban_player_var.get().strip()
                    if not player:
                        messagebox.showwarning("提示", "请输入玩家 ID")
                        return
                    unit = self.ban_unit_var.get()
                    amount_str = self.ban_amount_var.get().strip()
                    reason = self.ban_reason_var.get().strip() or "违反服务器规则"

                    now = datetime.datetime.now()

                    if unit == "永久":
                                      
                        cmd_reason = f"{reason} (permanent)"
                        self._send_raw_command(f'ban {player} "{cmd_reason}"')
                        self._record_ban(player, now, None, None, reason, permanent=True)
                        self.ban_status_var.set(f"已永久封禁 {player}")
                        self._log(f"[系统] 已永久封禁玩家：{player}，原因：{reason}\n", "system")
                        return

                    try:
                        amount = float(amount_str)
                        if amount <= 0:
                            raise ValueError
                    except ValueError:
                        messagebox.showwarning("提示", "封禁时长必须是正数")
                        return

                    duration_seconds = int(amount * TIME_UNITS[unit])
                    expire_dt = now + datetime.timedelta(seconds=duration_seconds)
                    expire_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")

                                                                             
                    cmd_reason = f"{reason} (until {expire_str})"
                    self._send_raw_command(f'ban {player} "{cmd_reason}"')

                    self._record_ban(player, now, duration_seconds, expire_dt, reason)
                    self.ban_status_var.set(
                        f"已封禁 {player}，开始：{now.strftime('%Y-%m-%d %H:%M:%S')}，"
                        f"到期：{expire_str}")
                    self._log(
                        f"[系统] 已封禁玩家：{player}\n"
                        f"        开始时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"        封禁时长：{amount} {unit}（{duration_seconds} 秒）\n"
                        f"        到期时间：{expire_str}\n"
                        f"        封禁原因：{reason}\n",
                        "system")

                def _unban_player(self):
                    player = self.ban_player_var.get().strip()
                    if not player:
                        messagebox.showwarning("提示", "请输入要解封的玩家 ID")
                        return
                    self._send_raw_command(f"pardon {player}")
                    self._remove_ban_record(player)
                    self._log(f"[系统] 已解封玩家：{player}\n", "system")
                    self.ban_status_var.set(f"已解封 {player}")

                def _banned_players_path(self):
                    work_dir = self.workdir_var.get().strip()
                    if not work_dir:
                        messagebox.showinfo("提示", "请先设置工作目录")
                        return None
                    return os.path.join(work_dir, "banned-players.json")

                def _show_banned_list(self):
                    """显示本地记录的封禁（含开始时间、剩余时间、原因）"""
                    bans = self.config.get("local_bans", {})
                    if not bans:
                        messagebox.showinfo("封禁列表", "本地暂无封禁记录")
                        return

                    now = datetime.datetime.now()
                    lines = []
                    for key, info in bans.items():
                        player = info.get("player", key)
                        start = info.get("ban_time", "?")
                        reason = info.get("reason", "?")
                        if info.get("permanent"):
                            left = "永久"
                            expire = "永久"
                        else:
                            expire = info.get("expire_time", "?")
                            try:
                                dt = datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
                                if dt <= now:
                                    left = "已到期（等待自动解封）"
                                else:
                                    left = str(dt - now).split(".")[0]
                            except Exception:
                                left = "?"
                        lines.append(
                            f"玩家：{player}\n"
                            f"  开始时间：{start}\n"
                            f"  到期时间：{expire}\n"
                            f"  剩余时间：{left}\n"
                            f"  封禁原因：{reason}\n"
                        )

                    text = "\n".join(lines) if lines else "暂无封禁记录"
                    messagebox.showinfo("封禁列表", text)

                def _purge_expired_bans(self):
                    """手动清理本地记录里已过期的封禁"""
                    count = self._check_and_unban_expired(manual=True)
                    if count:
                        messagebox.showinfo("完成", f"已清理 {count} 个到期封禁")
                    else:
                        messagebox.showinfo("完成", "没有需要清理的到期封禁")

                def _record_ban(self, player, start_dt, duration_seconds, expire_dt,
                                reason, permanent=False):
                    bans = self.config.setdefault("local_bans", {})
                    entry = {
                        "player": player,
                        "ban_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "reason": reason,
                        "permanent": bool(permanent),
                    }
                    if not permanent and expire_dt is not None:
                        entry["duration_seconds"] = duration_seconds
                        entry["expire_time"] = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
                    bans[player.lower()] = entry
                    self._save_config_silent()

                def _remove_ban_record(self, player):
                    bans = self.config.setdefault("local_bans", {})
                    if player.lower() in bans:
                        del bans[player.lower()]
                        self._save_config_silent()

                                                                                  
                        
                                                                                  
                def _start_ban_monitor(self):
                    self.ban_monitor_stop.clear()
                    threading.Thread(target=self._ban_monitor_loop, daemon=True).start()

                def _ban_monitor_loop(self):
                    while not self.ban_monitor_stop.is_set():
                        try:
                            self._check_and_unban_expired()
                        except Exception as e:
                            self.log_queue.put(f"[系统] 自动解封检查出错：{e}\n")
                        self.ban_monitor_stop.wait(BAN_CHECK_INTERVAL)

                def _check_and_unban_expired(self, manual=False):
                    """检查本地记录里所有非永久封禁，如果 now >= expire_time 就解封。
                    返回本次解封的玩家数量。"""
                    bans = self.config.get("local_bans", {})
                    if not bans:
                        return 0

                    now = datetime.datetime.now()
                    to_remove = []
                    count = 0

                    for key, info in list(bans.items()):
                        if info.get("permanent"):
                            continue
                        expire_str = info.get("expire_time")
                        if not expire_str:
                            to_remove.append(key)
                            continue
                        try:
                            expire_dt = datetime.datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            to_remove.append(key)
                            continue

                                     
                        if now >= expire_dt:
                            player = info.get("player", key)
                            proc = self.server_proc
                            if proc is not None and proc.poll() is None:
                                try:
                                    proc.stdin.write(f"pardon {player}\n")
                                    proc.stdin.flush()
                                    self.log_queue.put(
                                        f"[系统] 自动解封到期玩家：{player}"
                                        f"（到期时间：{expire_str}）\n")
                                except Exception as e:
                                    self.log_queue.put(f"[系统] 自动解封失败：{e}\n")
                            else:
                                self.log_queue.put(
                                    f"[系统] 玩家 {player} 封禁已到期"
                                    f"（到期时间：{expire_str}），但服务器未运行，"
                                    f"仅从本地记录移除。\n")
                            to_remove.append(key)
                            count += 1

                    if to_remove:
                        for key in to_remove:
                            bans.pop(key, None)
                        self._save_config_silent()

                    if count and manual:
                        self.root.after(0, lambda c=count: self.ban_status_var.set(
                            f"已清理 {c} 个到期封禁"))
                    return count

                                                                                  
                    
                                                                                  
                def _poll_log_queue(self):
                    try:
                        while True:
                            line = self.log_queue.get_nowait()
                            self._log(line)
                    except queue.Empty:
                        pass
                    self.root.after(100, self._poll_log_queue)

                def _log(self, text, tag=None):
                    if tag is None:
                        low = text.lower()
                        if "error" in low or "exception" in low or "severe" in low:
                            tag = "error"
                        elif "warn" in low:
                            tag = "warn"
                        else:
                            tag = "info"
                    self.log_text.config(state="normal")
                    self.log_text.insert("end", text, tag)
                    self.log_text.see("end")
                    self.log_text.config(state="disabled")

                def _set_status(self, text):
                    self.root.after(0, lambda: self.status_var.set(text))

                                                                                  
                    
                                                                                  
                def _on_close(self):
                    proc = self.server_proc
                    if proc is not None and proc.poll() is None:
                        if not messagebox.askyesno("确认退出",
                                                "服务端仍在运行，退出将强制结束它。确定继续？"):
                            return
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    if self.log_file:
                        try:
                            self.log_file.close()
                        except Exception:
                            pass
                        self.log_file = None

                    self.ban_monitor_stop.set()
                    self._save_config()
                    self.root.destroy()


        ttk.Button(r5_tools, text="下载Java☕", command=lambda: java_downloader(self.root)).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_tools, text="服务器", command=Service).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_files, text="📁 打开版本目录", command=self.open_version_dir).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_files, text="🗑️ 删除版本", command=self.delete_version).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_files, text="📂 打开 Mods 文件夹", command=self.open_mods_dir).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_files, text="💾 存档管理", command=self.open_saves_manager).pack(side=tk.LEFT, padx=5)
        ttk.Button(r5_files, text="🗺️ 种子地图", command=self.open_seed_map).pack(side=tk.LEFT, padx=5)
                              
        f_monitor = ttk.LabelFrame(tab, text="📊 性能监控")
        f_monitor.pack(fill=tk.X, pady=5, padx=5)
        r_mon = ttk.Frame(f_monitor)
        r_mon.pack(fill=tk.X, padx=5, pady=5)
        def go_mc_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('Minecraft官网', 'https://www.minecraft.net/zh-hans'); webview.start()"
            ])
        def go_skin_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('更改皮肤', 'https://www.minecraft.net/zh-hans/msaprofile/mygames/editskin'); webview.start()"
            ])
        def go_name_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('更改Java档案', 'https://www.minecraft.net/zh-hans/msaprofile/mygames'); webview.start()"
            ])
        def go_buy_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('购买正版', 'https://www.minecraft.net/zh-hans/store/minecraft-java-edition'); webview.start()"
            ])
        def go_download_launcher_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('下载启动器', 'https://www.minecraft.net/zh-hans/download'); webview.start()"
            ])
        def go_download_pcl_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('下载PCL', 'https://ltcat.lanzouv.com/b0aj6gsid'); webview.start()"
            ])
        def go_download_java_server_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('下载 Minecraft:Java 版服务器', 'https://www.minecraft.net/zh-hans/download/server'); webview.start()"
            ])
        def go_wiki_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('Minecraft Wiki', 'https://zh.minecraft.wiki/'); webview.start()"
            ])
        def go_bugs_():
                                     
            subprocess.Popen([
                sys.executable, "-c",
                "import webview; webview.create_window('反馈bug(Minecraft)', 'https://bugs.mojang.com/'); webview.start()"
            ])
        f_links = ttk.LabelFrame(tab, text="🔗 快捷链接")
        f_links.pack(fill=tk.X, pady=5, padx=5)
        links1 = ttk.Frame(f_links)
        links1.pack(fill=tk.X, padx=5, pady=(5, 3))
        links2 = ttk.Frame(f_links)
        links2.pack(fill=tk.X, padx=5, pady=(3, 5))
        ttk.Button(links1, text="去Minecraft官网", command=go_mc_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links1, text="去更改皮肤", command=go_skin_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links1, text="去更改Java档案", command=go_name_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links1, text="去购买正版", command=go_buy_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links1, text="去下载启动器", command=go_download_launcher_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links2, text="下载PCL", command=go_download_pcl_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links2, text="下载 Minecraft:Java 版服务器", command=go_download_java_server_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links2, text="Minecraft Wiki", command=go_wiki_).pack(side=tk.LEFT, padx=5)
        ttk.Button(links2, text="反馈bug(Minecraft)", command=go_bugs_).pack(side=tk.LEFT, padx=5)
        
        self.monitor_cpu_var = tk.StringVar(value="CPU: --")
        self.monitor_mem_var = tk.StringVar(value="内存: --")
        self.monitor_time_var = tk.StringVar(value="运行: --")
        self.monitor_status_var = tk.StringVar(value="游戏未运行")

        ttk.Label(r_mon, textvariable=self.monitor_status_var,
                  foreground="gray", width=12).pack(side=tk.LEFT, padx=5)
        ttk.Label(r_mon, textvariable=self.monitor_cpu_var,
                  font=('Consolas', 10), width=15).pack(side=tk.LEFT, padx=5)
        ttk.Label(r_mon, textvariable=self.monitor_mem_var,
                  font=('Consolas', 10), width=20).pack(side=tk.LEFT, padx=5)
        ttk.Label(r_mon, textvariable=self.monitor_time_var,
                  font=('Consolas', 10), width=15).pack(side=tk.LEFT, padx=5)
        self.monitor_mem_bar = ttk.Progressbar(r_mon, length=120, maximum=100)
        self.monitor_mem_bar.pack(side=tk.LEFT, padx=5)
    def setup_auth_tab(self):
        tab = self.auth_tab
        f1 = ttk.LabelFrame(tab, text="登录状态")
        f1.pack(fill=tk.X, pady=5, padx=5)
        r1 = ttk.Frame(f1)
        r1.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r1, text="当前状态:").pack(side=tk.LEFT)
        self.auth_status_label = tk.Label(r1, text=self.auth_status_value, fg="blue")
        self.auth_status_label.pack(side=tk.LEFT, padx=5)

        f4 = ttk.LabelFrame(tab, text="正版登录")
        f4.pack(fill=tk.X, pady=5, padx=5)
        r4 = ttk.Frame(f4)
        r4.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(r4, text="🔑 登录 Microsoft 账号", command=self.login_microsoft).pack(side=tk.LEFT, padx=5)
        ttk.Button(r4, text="📂 从官方启动器导入", command=self.import_from_official).pack(side=tk.LEFT, padx=5)
        ttk.Button(r4, text="📝 手动输入 Token", command=self.manual_token_login).pack(side=tk.LEFT, padx=5)
        ttk.Button(r4, text="🔄 刷新令牌", command=self.refresh_auth_token).pack(side=tk.LEFT, padx=5)
        ttk.Button(r4, text="🚪 退出登录", command=self.logout_auth).pack(side=tk.LEFT, padx=5)
        f2 = ttk.LabelFrame(tab, text="登录方式")
        f2.pack(fill=tk.X, pady=5, padx=5)
        r2 = ttk.Frame(f2)
        r2.pack(fill=tk.X, padx=5, pady=5)
        self.offline_radio = ttk.Radiobutton(r2, text="离线模式", value="offline", command=self.on_login_mode_change)
        self.offline_radio.pack(side=tk.LEFT, padx=5)
        self.microsoft_radio = ttk.Radiobutton(r2, text="Microsoft 正版登录", value="microsoft", command=self.on_login_mode_change)
        self.microsoft_radio.pack(side=tk.LEFT, padx=5)
        
        f3 = ttk.LabelFrame(tab, text="离线设置")
        f3.pack(fill=tk.X, pady=5, padx=5)
        r3 = ttk.Frame(f3)
        r3.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r3, text="玩家名称:").pack(side=tk.LEFT)
        self.username_entry = ttk.Entry(r3, width=25)
        self.username_entry.pack(side=tk.LEFT, padx=5)
        tk.Label(r3, text="(离线模式使用)", fg="gray").pack(side=tk.LEFT, padx=5)
        
        f5a = ttk.LabelFrame(tab, text="多账户切换")
        f5a.pack(fill=tk.X, pady=5, padx=5)
        r5a = ttk.Frame(f5a)
        r5a.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(r5a, text="已保存账号:").pack(side=tk.LEFT, padx=5)
        self.account_combo_var = tk.StringVar()
        self.account_combo = ttk.Combobox(r5a, textvariable=self.account_combo_var, state="readonly", width=28)
        self.account_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(r5a, text="切换", command=self.switch_saved_account).pack(side=tk.LEFT, padx=3)
        ttk.Button(r5a, text="删除", command=self.delete_saved_account).pack(side=tk.LEFT, padx=3)
        ttk.Button(r5a, text="刷新列表", command=self.refresh_account_list).pack(side=tk.LEFT, padx=3)
        self.refresh_account_list()
        f5 = ttk.LabelFrame(tab, text="账号信息")
        f5.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        r5 = ttk.Frame(f5)
        r5.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.auth_avatar_label = tk.Label(r5, text="User", font=("Microsoft YaHei", 14))
        self.auth_avatar_label.pack(pady=(0, 5))
        self.auth_info_text = tk.Text(r5, height=6, wrap=tk.WORD, state=tk.DISABLED)
        self.auth_info_text.pack(fill=tk.BOTH, expand=True)
    
    def on_auto_chinese_change(self):
        self.save_config()
        if self.auto_chinese.get():
            self.log("🇨🇳 自动设置简体中文已启用")
        else:
            self.log("🇨🇳 自动设置简体中文已禁用")
    
    def set_game_language_to_chinese(self, game_dir):
        options_file = game_dir / "options.txt"
        try:
            if not options_file.exists():
                with open(options_file, 'w', encoding='utf-8') as f:
                    f.write("lang:zh_cn\n")
                self.log("✅ 已创建 options.txt 并设置语言为简体中文")
                return True
            with open(options_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith('lang:'):
                    new_lines.append('lang:zh_cn\n')
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append('lang:zh_cn\n')
            with open(options_file, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            self.log("✅ 已将游戏语言设置为简体中文")
            return True
        except Exception as e:
            self.log(f"⚠️ 设置语言失败: {e}")
            return False
    
    def on_demo_mode_change(self):
        self.save_config()
        if self.demo_mode.get():
            self.log("🎮 试玩模式已启用 (--demo)")
        else:
            self.log("🎮 试玩模式已禁用")
    
    def on_demo_mode_changed(self, *args):
        self.save_config()
    
    def on_authlib_change(self):
        self.save_config()
        if self.authlib_enabled.get():
            self.log("🔐 外置登录已启用")
        else:
            self.log("🔐 外置登录已禁用")
    
    def on_authlib_changed(self, *args):
        self.save_config()
    
    def download_authlib(self):
        self.log("📥 开始下载 Authlib-Injector...")
        threading.Thread(target=self._do_download_authlib, daemon=True).start()
    
    def _do_download_authlib(self):
        if self.authlib_manager.download_authlib():
            self.root.after(0, lambda: messagebox.showinfo("成功", "Authlib-Injector 下载完成"))
        else:
            self.root.after(0, lambda: messagebox.showerror("失败", "Authlib-Injector 下载失败"))
    
    def update_login_display(self):
        if self.auth.is_authenticated():
            self.login_display_label.config(text=f"👤 {self.auth.username} (正版)")
            if hasattr(self, 'banner'):
                try:
                    self.banner.set_user_text(f"👤 {self.auth.username}")
                except Exception:
                    pass
        else:
            self.login_display_label.config(text="👤 离线模式")
            if hasattr(self, 'banner'):
                try:
                    self.banner.set_user_text("👤 离线模式")
                except Exception:
                    pass
    
    def on_closing(self):
        """关闭按钮：询问是退出应用，还是最小化到系统托盘"""
        try:
            choice = messagebox.askyesnocancel(
                "关闭启动器",
                "请选择关闭时的动作：\n\n"
                "是：退出应用\n"
                "否：最小化到系统托盘\n"
                "取消：保持窗口打开"
            )
        except Exception:
            choice = True

        if choice is None:
            return
        if choice:
            self._exit_application()
        else:
            self._minimize_to_tray()

    def _make_tray_icon_image(self):
        """创建托盘图标图片"""
        from PIL import Image as PilImage
        img = PilImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, 60, 60), fill=(46, 125, 50, 255))
        try:
            draw.text((20, 18), "MC", fill="white")
        except Exception:
            pass
        return img

    def _start_tray_icon(self):
        """启动系统托盘图标"""
        if not HAS_TRAY or pystray is None:
            return
        if getattr(self, "tray_icon", None) is not None:
            return

        image = self._make_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda icon, item: self.root.after(0, self._restore_from_tray)),
            pystray.MenuItem("退出应用", lambda icon, item: self.root.after(0, self._exit_application)),
        )
        icon = pystray.Icon("MinecraftLauncher", image, "Minecraft启动器", menu)
        self.tray_icon = icon

        def runner():
            try:
                icon.run()
            except Exception:
                pass
            finally:
                if getattr(self, "tray_icon", None) is icon:
                    self.tray_icon = None

        threading.Thread(target=runner, daemon=True).start()

    def _minimize_to_tray(self):
        """窗口最小化到系统托盘"""
        if not HAS_TRAY:
            # 没有 pystray，退化为普通最小化，不退出
            try:
                self.root.iconify()
            except Exception:
                pass
            return

               
        self.save_config()
        try:
            if self.console_window is not None and self.console_window.winfo_exists():
                self.console_window.withdraw()
        except Exception:
            pass

        self.root.withdraw()
        self._start_tray_icon()
        try:
            self.log("🫥 已最小化到系统托盘", level="info")
        except Exception:
            pass

    def _restore_from_tray(self):
        """从系统托盘恢复主窗口"""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root.state("zoomed")
        except Exception:
            pass
        try:
            if self.console_window is not None and self.console_window.winfo_exists():
                self.console_window.deiconify()
        except Exception:
            pass

    def _exit_application(self):
        """真正退出应用并清理托盘"""
        try:
            if getattr(self, "tray_icon", None) is not None:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass
                self.tray_icon = None
        except Exception:
            pass

        self.save_config()
        try:
            if self.console_window is not None and self.console_window.winfo_exists():
                self.console_window.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
    
    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=str(self.base_dir))
        if d:
            self.base_dir = Path(d)
            self.dir_var.set(str(d))
            self.save_config()
            self.ensure_directories()
            self.refresh_versions()
            self.refresh_local_versions()
    
    def open_dir(self):
        if platform.system() == 'Windows':
            os.startfile(str(self.base_dir))
        else:
            subprocess.run(['xdg-open', str(self.base_dir)])
    
    def open_version_dir(self):
        version = self.get_selected_launch_version()
        if version:
            vdir = self.version_dir / version
            if vdir.exists():
                if platform.system() == 'Windows':
                    os.startfile(str(vdir))
                else:
                    subprocess.run(['xdg-open', str(vdir)])
            else:
                messagebox.showerror("错误", f"版本目录不存在: {vdir}")
        else:
            messagebox.showerror("错误", "请选择版本")
    
    def delete_version(self):
        version = self.get_selected_launch_version()
        if not version:
            messagebox.showerror("错误", "请选择版本")
            return

                                       
        is_fabric = '-Fabric-' in version or 'fabric' in version.lower()

                              
        if is_fabric:
            parts = version.split('-Fabric-')
            parent_version = parts[0] if len(parts) >= 2 else version.split('-')[0]
            confirm_msg = (
                f"确定要删除版本 {version} 吗？\n\n"
                f"ℹ️ 检测到这是 Fabric 版本，原版 {parent_version} 会保留。\n"
                f"此操作不可恢复！"
            )
        else:
            confirm_msg = f"确定要删除版本 {version} 吗？\n此操作不可恢复！"

        d = tk.Toplevel(self.root)
        d.title("⚠️ 永久删除确认")
        d.geometry("450x250")
        d.transient(self.root)
        d.grab_set()
        d.resizable(False, False)

        tk.Label(
            d,
            text="⚠️ 危险操作 ⚠️",
            font=("Microsoft YaHei", 14, "bold"),
            fg="red"
        ).pack(pady=(15, 5))

        tk.Label(
            d,
            text=confirm_msg,
            font=("Microsoft YaHei", 10),
            justify=tk.LEFT,
            wraplength=400
        ).pack(padx=20, pady=5, anchor=tk.W)
        random_number = r.randint(100000,999999)
        tk.Label(
            d,
            text=f"请输入 {random_number} 以确认永久删除：",
            font=("Microsoft YaHei", 10),
            fg="#CC0000"
        ).pack(pady=(10, 5))

        confirm_var = tk.StringVar()
        entry = ttk.Entry(d, textvariable=confirm_var, width=30, font=("Consolas", 12))
        entry.pack(pady=5)
        entry.focus_set()

        result = {"ok": False}

        def on_confirm():
            if confirm_var.get().strip() == str(random_number):
                result["ok"] = True
                d.destroy()
            else:
                messagebox.showerror("错误", "输入错误，删除已取消")
                d.destroy()

        def on_cancel():
            result["ok"] = False
            d.destroy()

        btn_frame = ttk.Frame(d)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="确认删除", command=on_confirm, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel, width=12).pack(side=tk.LEFT, padx=5)

                
        entry.bind('<Return>', lambda e: on_confirm())

        self.root.wait_window(d)

        if not result["ok"]:
            self.log("ℹ️ 已取消删除")
            return

                            
        deleted = []
        failed = []

        vdir = self.version_dir / version
        if vdir.exists():
            try:
                shutil.rmtree(vdir)
                deleted.append(version)
            except Exception as e:
                failed.append(f"{version}: {e}")
        else:
            self.log(f"ℹ️ 版本目录不存在: {vdir}", level='warning')

                            
        if deleted:
            self.log(f"🗑️ 已删除: {', '.join(deleted)}")
        if failed:
            for f in failed:
                self.log(f"❌ 删除失败: {f}")
            messagebox.showerror("错误", f"部分删除失败:\n" + "\n".join(failed))

                            
        self.refresh_local_versions()
        self.refresh_versions()
    def browse_java(self):
        if platform.system() == 'Windows':
            f = filedialog.askopenfilename(filetypes=[("Java", "java.exe"), ("Java(无窗口)", "javaw.exe"), ("所有文件", "*.*")])
        else:
            f = filedialog.askopenfilename()
        if f:
            self.java_path_var.set(f)
            self.save_config()

    def _auto_search_java_button(self):
        version = None
        try:
            version = self.get_selected_launch_version()
        except Exception:
            version = None
        if not version or version in ("--- 暂无本地版本 ---", "═══════ 本地版本 ═══════"):
            version = None
        path, major = self.auto_find_java(version, apply=True, log_result=True)
        try:
            self.refresh_java_list()
        except Exception:
            pass
        if major is not None:
            messagebox.showinfo("自动搜索 Java", f"已选择 Java {major}:\n{path}")
        else:
            messagebox.showwarning("自动搜索 Java", f"未找到合适的 Java，当前：\n{path}")

    def refresh_java_list(self):
        try:
            candidates = self.collect_java_candidates()
        except Exception as e:
            self.log(f"⚠️ 刷新 Java 列表失败: {e}")
            candidates = []
        self._java_list_paths = list(candidates)
        values = []
        for p in candidates:
            major = self.java_major_version(p)
            values.append(f"Java {major if major is not None else '?'} · {p}")
        if hasattr(self, "java_list_combo"):
            self.java_list_combo["values"] = values
            if values:
                self.java_list_combo.current(0)
                self.java_list_var.set(values[0])
            else:
                self.java_list_var.set("")
                             
        try:
            current = self.java_path_var.get().strip()
            if current in candidates:
                idx = candidates.index(current)
                self.java_list_combo.current(idx)
                self.java_list_var.set(values[idx])
        except Exception:
            pass

    def _on_java_list_select(self, event=None):
        try:
            idx = self.java_list_combo.current()
            if 0 <= idx < len(self._java_list_paths):
                self.java_path_var.set(self._java_list_paths[idx])
        except Exception:
            pass

    def use_selected_java(self):
        self._on_java_list_select()
        self.save_config()
        selected = self.java_path_var.get().strip()
        self.log(f"☕ 已选择 Java：{selected}")

    def _java_list_prefs(self):
        custom = []
        ignored = []
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                custom = cfg.get("custom_java_paths", []) or []
                ignored = cfg.get("ignored_java_paths", []) or []
        except Exception:
            pass
        return custom, ignored

    def _save_java_list_prefs(self, custom, ignored):
        try:
            cfg = {}
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["custom_java_paths"] = custom
            cfg["ignored_java_paths"] = ignored
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"⚠️ 保存 Java 列表偏好失败: {e}")

    def manually_add_java(self):
        if platform.system() == "Windows":
            path = filedialog.askopenfilename(
                title="选择 Java 可执行文件",
                filetypes=[("Java", "java.exe"), ("所有文件", "*.*")]
            )
        else:
            path = filedialog.askopenfilename(title="选择 Java 可执行文件")
        if not path:
            return
        major = self.java_major_version(path)
        if major is None:
            if not messagebox.askyesno("提示", "无法识别该文件的 Java 版本，仍要添加吗？"):
                return
        custom, ignored = self._java_list_prefs()
        p = str(Path(path))
        if p not in custom:
            custom.append(p)
                      
        ignored = [x for x in ignored if str(Path(x)) != p]
        self._save_java_list_prefs(custom, ignored)
        self.refresh_java_list()
                   
        try:
            if p in self._java_list_paths:
                idx = self._java_list_paths.index(p)
                self.java_list_combo.current(idx)
                self.java_list_var.set(self.java_list_combo["values"][idx])
        except Exception:
            pass
        self.log(f"➕ 已手动添加 Java：{path}" + (f" (Java {major})" if major is not None else ""))

    def remove_selected_java(self):
        try:
            idx = self.java_list_combo.current()
        except Exception:
            idx = -1
        if idx < 0 or idx >= len(self._java_list_paths):
            messagebox.showinfo("提示", "请先在列表中选择一个 Java")
            return
        path = self._java_list_paths[idx]
        norm_path = str(Path(path))
        custom, ignored = self._java_list_prefs()
        remaining = [p for p in self._java_list_paths if str(Path(p)) != norm_path]
        try:
            current_norm = str(Path(self.java_path_var.get().strip()))
        except Exception:
            current_norm = ""
        if current_norm == norm_path:
            if remaining:
                self.java_path_var.set(remaining[0])
            else:
                self.java_path_var.set("java")
        if not remaining:
            if not messagebox.askyesno("确认", "这是列表中唯一的 Java，删除后列表将为空。是否继续？"):
                return
        if any(str(Path(x)) == norm_path for x in custom):
            custom = [x for x in custom if str(Path(x)) != norm_path]
        else:
            ignored_norms = [str(Path(x)) for x in ignored]
            if norm_path not in ignored_norms:
                ignored.append(norm_path)
        self._save_java_list_prefs(custom, ignored)
        try:
            self.save_config()
        except Exception:
            pass
        self.refresh_java_list()
        self.log(f"➖ 已从 Java 列表移除：{path}")

    def restore_ignored_java(self):
        custom, _ = self._java_list_prefs()
        self._save_java_list_prefs(custom, [])
        self.refresh_java_list()
        self.log("♻️ 已恢复被隐藏的 Java 列表")


    def clear_console(self):
        if self.console_text is None:
            messagebox.showinfo("提示", "控制台窗口还没打开")
            return
        self.console_text.config(state=tk.NORMAL)
        self.console_text.delete(1.0, tk.END)
        self.console_text.config(state=tk.DISABLED)
    
    def show_version_info(self):
        info = f"正式版: {len(self.release_versions)} 个\n"
        info += f"快照版: {len(self.snapshot_versions)} 个\n"
        info += f"愚人节版: {len(self.april_fools_versions)} 个\n"
        info += f"本地已安装: {len(self.local_versions)} 个\n\n"
        if self.local_versions:
            info += "已安装版本:\n"
            for v in self.local_versions[:10]:
                info += f"  • {v}\n"
            if len(self.local_versions) > 10:
                info += f"  ... 还有 {len(self.local_versions) - 10} 个\n"
        messagebox.showinfo("版本分类信息", info)
    
    def refresh_versions(self):
        self.log("🔄 正在从Mojang获取版本列表...")
        threading.Thread(target=self._fetch_versions_online, daemon=True).start()
    
    def refresh_local_versions(self):
        self.log("🔄 刷新本地版本列表...")
        threading.Thread(target=self._load_local_versions_only, daemon=True).start()
    def _is_april_fools_by_date(self, release_time_str, version_id):
        """4月1日发布 + 周快照格式 = 愚人节版本"""
        try:
            if not release_time_str:
                return False
            date_part = release_time_str[:10]
            month_day = date_part[5:]
            if month_day != "04-01":
                return False
                                                     
            if not re.match(r'^\d{2}w\d{2}[a-z]?$', version_id.lower()):
                return False
            return True
        except:
            return False
    def _fetch_versions_online(self):
        try:
            manifest = None
            if getattr(self, '_version_cache', None) and time.time() - getattr(self, '_version_cache_time', 0) < 300:
                manifest = self._version_cache
            else:
                r = self.session.get(
                    "https://launchermeta.mojang.com/mc/game/version_manifest.json",
                    timeout=30
                )
                r.raise_for_status()
                manifest = r.json()
                self._version_cache = manifest
                self._version_cache_time = time.time()

            self.release_versions = []
            self.snapshot_versions = []
            self.april_fools_versions = []
            self.old_versions = []
            self.local_versions = []
            self.all_versions = []

            for v in manifest.get('versions', []):
                version_id = v['id']
                version_type = v.get('type', 'unknown')
                release_time = v.get('releaseTime', '') or v.get('time', '')

                                                      
                is_april_fools = False
                if version_type == 'snapshot' and release_time:
                    try:
                                                                     
                        month_day = release_time[5:10]           
                        if month_day == "04-01":
                            is_april_fools = True
                    except:
                        pass

                if is_april_fools:
                    self.april_fools_versions.append(version_id)
                elif version_type == 'release':
                    self.release_versions.append(version_id)
                elif version_type == 'snapshot':
                    self.snapshot_versions.append(version_id)
                elif version_type in ('old_beta', 'old_alpha'):
                    self.old_versions.append(version_id)
                else:
                    self.snapshot_versions.append(version_id)

                self.all_versions.append(version_id)

                                     
            if self.version_dir.exists():
                for v in self.version_dir.iterdir():
                    if v.is_dir():
                        has_json = any(v.glob("*.json"))
                        has_jar = any(v.glob("*.jar"))
                        if has_json or has_jar:
                            self.local_versions.append(v.name)

                              
            def sort_key(v):
                try:
                    parts = re.findall(r'\d+', v)
                    if parts:
                        return [int(p) for p in parts[:3]]
                except:
                    pass
                return [0]

            self.release_versions.sort(key=sort_key, reverse=True)
            self.snapshot_versions.sort(key=sort_key, reverse=True)
            self.april_fools_versions.sort(key=sort_key, reverse=True)
            self.old_versions.sort(key=sort_key, reverse=True)
            self.local_versions.sort(key=sort_key, reverse=True)

                                  
            self.local_versions = self._filter_local_versions(self.local_versions)

                                 
            self.root.after(0, self._refresh_download_version_list)
            self.root.after(0, self._update_local_combo)

            self.log(
                f"✅ 获取版本列表成功: "
                f"正式版 {len(self.release_versions)} 个, "
                f"快照版 {len(self.snapshot_versions)} 个, "
                f"愚人节版 {len(self.april_fools_versions)} 个, "
                f"本地版 {len(self.local_versions)} 个"
            )

        except Exception as e:
            self.log(f"❌ 获取版本列表失败: {e}")
            self.log("📁 切换到本地模式...")
            self._load_local_versions()
    def _load_local_versions_only(self):
        """只刷新本地版本列表（启动页），不动下载页"""
        try:
            versions = []
            if self.version_dir.exists():
                for v in self.version_dir.iterdir():
                    if v.is_dir():
                        has_json = any(v.glob("*.json"))
                        has_jar = any(v.glob("*.jar"))
                        if has_json or has_jar:
                            versions.append(v.name)

            def sort_key(v):
                try:
                    parts = re.findall(r'\d+', v)
                    if parts:
                        return [int(p) for p in parts[:3]]
                except:
                    pass
                return [0]

            versions.sort(key=sort_key, reverse=True)

                    
            filtered = self._filter_local_versions(versions)
            self.local_versions = filtered

            self.root.after(0, self._update_local_combo)
            if len(filtered) < len(versions):
                self.log(f"📁 本地版本: {len(filtered)} 个（已隐藏 {len(versions) - len(filtered)} 个被 Fabric 覆盖的原版）")
            else:
                self.log(f"📁 本地版本: {len(filtered)} 个")
        except Exception as e:
            self.log(f"❌ 加载本地版本失败: {e}")
    def _load_local_versions(self):
        try:
            versions = []
            if self.version_dir.exists():
                for v in self.version_dir.iterdir():
                    if v.is_dir():
                        has_json = any(v.glob("*.json"))
                        has_jar = any(v.glob("*.jar"))
                        if has_json or has_jar:
                            versions.append(v.name)
            def sort_key(v):
                try:
                    parts = re.findall(r'\d+', v)
                    if parts:
                        return [int(p) for p in parts[:3]]
                except:
                    pass
                return [0]
            versions.sort(key=sort_key, reverse=True)
                                                    
            filtered = self._filter_local_versions(versions)
            self.local_versions = filtered
            self.root.after(0, self._refresh_download_version_list)
            self.root.after(0, self._update_local_combo)
            if len(filtered) < len(versions):
                self.log(f"📁 加载本地版本完成，{len(filtered)} 个可用（已隐藏 {len(versions) - len(filtered)} 个被 Fabric 覆盖的原版）")
            else:
                self.log(f"📁 加载本地版本完成，找到 {len(filtered)} 个版本")
        except Exception as e:
            self.log(f"❌ 加载本地版本失败: {e}")
    def _update_local_combo(self):
        if self.local_versions:
            display_list = ["═══════ 本地版本 ═══════"]
            display_list.extend(self.local_versions)
            self.launch_version_combo.config(values=display_list)

                         
            if self.last_launch_version and self.last_launch_version in self.local_versions:
                default = self.last_launch_version
            else:
                default = next((v for v in self.local_versions if " [原版]" not in v),
                               self.local_versions[0])
            self.launch_version_var.set(default)
            self.local_count_label.config(text=f"({len(self.local_versions)} 个版本)")
        else:
            self.launch_version_combo.config(values=["--- 暂无本地版本 ---"])
            self.launch_version_var.set("--- 暂无本地版本 ---")
            self.local_count_label.config(text="(0 个版本)")
        self.update_login_display()
    def _on_launch_version_change(self, event=None):
        """用户从下拉框切换版本时调用：记住选择并保存到配置"""
        v = self.launch_version_var.get()

                       
        if v.endswith(" [原版]"):
            v = v[:-len(" [原版]")]

                   
        if v in ("═══════ 本地版本 ═══════", "--- 暂无本地版本 ---", ""):
            return

        self.last_launch_version = v
        self.save_config()
    def _get_cat_versions(self, cat_name):
        """按分类名取版本列表（不含已安装标记，纯版本号）"""
        if cat_name == "正式版":
            return list(self.release_versions)
        elif cat_name == "快照版":
            return list(self.snapshot_versions)
        elif cat_name == "愚人节版":
            return list(self.april_fools_versions)
        elif cat_name == "远古版本":
            return list(getattr(self, "old_versions", []))
        elif cat_name == "本地已安装":
                                    
            return [v.replace(" [原版]", "") for v in self.local_versions]
        return []

    def _refresh_download_version_list(self, restore=True):
        """根据当前选中的分类刷新右侧版本列表

        restore=True: 尝试恢复 last_download_category（用于程序化刷新）
        restore=False: 使用用户当前选中的分类（用于用户点击事件）
        """
        if not hasattr(self, "download_cat_list"):
            return

        if restore:
            all_cats = list(self.download_cat_list.get(0, tk.END))
            if self.last_download_category in all_cats:
                idx = all_cats.index(self.last_download_category)
                self.download_cat_list.selection_clear(0, tk.END)
                self.download_cat_list.selection_set(idx)
                self.download_cat_list.activate(idx)
            else:
                sel = self.download_cat_list.curselection()
                if not sel:
                    self.download_cat_list.selection_set(0)
                    self.download_cat_list.activate(0)

        sel = self.download_cat_list.curselection()
        if not sel:
                          
            self.download_cat_list.selection_set(0)
            self.download_cat_list.activate(0)
            sel = self.download_cat_list.curselection()
        if not sel:
            return
        cat_name = self.download_cat_list.get(sel[0])
        versions = self._get_cat_versions(cat_name)

        self.download_version_list.delete(0, tk.END)
        for v in versions:
            self.download_version_list.insert(tk.END, v)

        if versions:
            if self.last_download_version and self.last_download_version in versions:
                pick = versions.index(self.last_download_version)
            else:
                pick = 0
            self.download_version_list.selection_set(pick)
            self.download_version_list.activate(pick)
            self.download_version_var.set(versions[pick])
        else:
            self.download_version_var.set("")

    def _on_download_cat_select(self, event=None):
                     
        sel = self.download_cat_list.curselection()
        if sel:
            self.last_download_category = self.download_cat_list.get(sel[0])

                                     
        self._refresh_download_version_list(restore=False)
        self.save_config()


    def _on_download_version_select(self, event=None):
        sel = self.download_version_list.curselection()
        if sel:
            v = self.download_version_list.get(sel[0])
            self.download_version_var.set(v)
            self.last_download_version = v
            self.save_config()
    def get_selected_download_version(self):
        sel = self.download_version_list.curselection()
        if sel:
            return self.download_version_list.get(sel[0])
                    
        v = self.download_version_var.get()
        return v if v else None
    
    def get_selected_launch_version(self):
        selected = self.launch_version_var.get()

                    
        if selected.endswith(" [原版]"):
            selected = selected[:-len(" [原版]")]
            self.launch_version_var.set(selected)

        if selected in ["═══════ 本地版本 ═══════", "--- 暂无本地版本 ---"]:
            values = list(self.launch_version_combo.cget('values'))
            idx = values.index(selected) if selected in values else -1
            if idx != -1 and idx + 1 < len(values):
                next_val = values[idx + 1]
                      
                if next_val.endswith(" [原版]"):
                    next_val = next_val[:-len(" [原版]")]
                if next_val not in ["═══════ 本地版本 ═══════", "--- 暂无本地版本 ---"]:
                    self.launch_version_var.set(next_val)
                    return next_val
            return None
        return selected
    
    def is_snapshot_version(self, version):
        if self.snapshot_versions:
            return version in self.snapshot_versions
                     
        if version in getattr(self, 'april_fools_versions', []):
            return True
        if re.match(r'^\d+[a-z]\d+[a-z]?$', version.lower()):
            return True
        if re.match(r'^\d+\.\d+-(?:rc|pre|beta)', version.lower()):
            return True
        if re.match(r'^\d+\.\d+\.\d+-(?:rc|pre|beta)', version.lower()):
            return True
        return False
    
    def uses_old_lwjgl(self, vjson):
        if 'libraries' in vjson:
            for lib in vjson['libraries']:
                if 'name' in lib and 'lwjgl' in lib['name'] and '3.2' in lib['name']:
                    return True
        return False
    
    def download_release_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("下载Minecraft正式版")
        d.geometry("400x200")
        d.transient(self.root)
        d.grab_set()
        ttk.Label(d, text="请输入要下载的版本号:").pack(pady=10)
        e = ttk.Entry(d, width=30)
        e.pack(pady=5)
        e.insert(0, "1.20.1")
        tk.Label(d, text="例如: 1.20.1, 1.19.4, 1.16.5", fg="gray").pack(pady=2)
        def start():
            v = e.get().strip()
            if not v:
                return
            d.destroy()
            threading.Thread(target=self.download_version, args=(v,), daemon=True).start()
        ttk.Button(d, text="开始下载", command=start).pack(pady=10)
    
    def download_snapshot_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("下载Minecraft快照版")
        d.geometry("400x220")
        d.transient(self.root)
        d.grab_set()
        ttk.Label(d, text="请输入要下载的快照版本号:").pack(pady=10)
        e = ttk.Entry(d, width=30)
        e.pack(pady=5)
        e.insert(0, "24w14a")
        tk.Label(d, text="例如: 24w14a, 23w04a, 1.20-pre1", fg="gray").pack(pady=2)
        def start():
            v = e.get().strip()
            if not v:
                return
            d.destroy()
            threading.Thread(target=self.download_version, args=(v,), daemon=True).start()
        ttk.Button(d, text="开始下载", command=start).pack(pady=10)
    
    def download_april_fools_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("下载Minecraft愚人节版")
        d.geometry("450x250")
        d.transient(self.root)
        d.grab_set()
        ttk.Label(d, text="请输入要下载的愚人节版本号:").pack(pady=10)
        e = ttk.Entry(d, width=30)
        e.pack(pady=5)
        e.insert(0, "24w14potato")
        tk.Label(d, text="例如: 24w14potato, 23w13a_or_b, 22w13oneblockatatime", fg="gray").pack(pady=2)
        tk.Label(d, text="💡 愚人节版是 Mojang 每年愚人节发布的搞笑版本", fg="gray").pack(pady=2)
        def start():
            v = e.get().strip()
            if not v:
                return
            d.destroy()
            threading.Thread(target=self.download_version, args=(v,), daemon=True).start()
        ttk.Button(d, text="开始下载", command=start).pack(pady=10)
    
    def download_selected_version(self):
        version = self.get_selected_download_version()
        if not version:
            messagebox.showerror("错误", "请选择版本")
            return
        threading.Thread(target=self.download_version, args=(version,), daemon=True).start()
    
    def cancel_download(self):
        self.is_downloading = False
        self.download_cancel_btn.config(state="disabled")
        self.log("已取消下载")
    
    def download_version(self, version):
        if self.is_downloading:
            self.log("已有下载任务")
            return
        self.is_downloading = True
        self.root.after(0, lambda: self.download_cancel_btn.config(state="normal"))
        self.update_progress(0, 0, "", "开始下载...")
        try:
            self.log(f"开始下载 {version}...")
            self.update_progress(5, 0, "", "获取版本清单...")
            r = self.session.get("https://launchermeta.mojang.com/mc/game/version_manifest.json")
            r.raise_for_status()
            manifest = r.json()
            vdata = None
            for v in manifest['versions']:
                if v['id'] == version:
                    vdata = v
                    break
            if not vdata:
                self.log(f"❌ 未找到版本: {version}")
                return
            self.log(f"下载版本JSON: {version}")
            r = self.session.get(vdata['url'])
            r.raise_for_status()
            vjson = r.json()
            vdir = self.version_dir / version
            vdir.mkdir(exist_ok=True)
            with open(vdir / f"{version}.json", 'w', encoding='utf-8') as f:
                json.dump(vjson, f, indent=2, ensure_ascii=False)
            if 'downloads' in vjson and 'client' in vjson['downloads']:
                self.log("下载客户端JAR...")
                self.update_progress(15, 0, "客户端JAR", "下载客户端...")
                self.download_file(vjson['downloads']['client']['url'], vdir / f"{version}.jar")
            self.download_libraries(vjson)
            self.download_assets(vjson)
            self.update_progress(100, 100, "", "下载完成!")
            self.log(f"✅ {version} 下载完成!")
            self.refresh_versions()
            self.refresh_local_versions()
        except Exception as e:
            self.log(f"❌ 下载失败: {e}")
        finally:
            self.is_downloading = False
            self.root.after(0, lambda: self.download_cancel_btn.config(state="disabled"))
    
    def download_libraries(self, vjson):
        if 'libraries' not in vjson:
            return
        self.log("开始下载依赖库...")
        tasks = []
        for lib in vjson['libraries']:
            if 'downloads' not in lib:
                continue
            if 'artifact' in lib['downloads']:
                a = lib['downloads']['artifact']
                path = a['path']
                filepath = self.libraries_dir / path
                if not filepath.exists():
                    tasks.append((a['url'], filepath, a.get('sha1')))
            if 'natives' in lib and 'classifiers' in lib['downloads']:
                natives = lib['natives']
                classifier = natives.get('windows')
                if classifier and classifier in lib['downloads']['classifiers']:
                    a = lib['downloads']['classifiers'][classifier]
                    path = a['path']
                    filepath = self.libraries_dir / path
                    if not filepath.exists():
                        tasks.append((a['url'], filepath, a.get('sha1')))
        if tasks:
            self.log(f"需要下载 {len(tasks)} 个库文件")
            self.download_files(tasks, "库文件", 20, 60)
        else:
            self.log("所有库文件已存在")
    
    def download_assets(self, vjson):
        if 'assetIndex' not in vjson:
            return
        self.log("开始下载资源文件...")
        idx = vjson['assetIndex']
        idx_file = self.assets_dir / "indexes" / f"{idx['id']}.json"
        idx_file.parent.mkdir(parents=True, exist_ok=True)
        if not idx_file.exists():
            self.log(f"下载资源索引: {idx['id']}")
            self.download_file(idx['url'], idx_file)
        with open(idx_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if 'objects' not in data:
            return
        tasks = []
        for key, value in data['objects'].items():
            h = value['hash']
            sub = h[:2]
            obj_file = self.assets_dir / "objects" / sub / h
            if not obj_file.exists():
                url = f"https://resources.download.minecraft.net/{sub}/{h}"
                tasks.append((url, obj_file))
        if tasks:
            self.log(f"需要下载 {len(tasks)} 个资源文件")
            self.download_files(tasks, "资源文件", 60, 95)
        else:
            self.log("所有资源文件已存在")
    def download_file(self, url, path, sha1=None):
        """下载单个文件，可选 SHA1 校验（不匹配会自动重下一次）"""
        import hashlib
        path.parent.mkdir(parents=True, exist_ok=True)
        def _sha1_of(p):
            h = hashlib.sha1()
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest().lower()
        last_err = None
        for attempt in range(2):
            try:
                r = self.session.get(url, stream=True, timeout=30)
                r.raise_for_status()
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                if sha1:
                    real = _sha1_of(path)
                    if real != str(sha1).lower():
                        last_err = f"SHA1 不匹配 (期望 {sha1[:10]}... 实际 {real[:10]}...)"
                        try: path.unlink()
                        except Exception: pass
                        continue
                return
            except Exception as e:
                last_err = e
                if attempt == 0: continue
        msg = f"下载失败: {url} - {last_err}"
        try:
            self.log(f"?? {msg}", level='warning')
        except Exception:
            pass
        raise RuntimeError(msg)

    def download_files(self, tasks, name, start_pct, end_pct):
        total = len(tasks)
        done = 0
        with ThreadPoolExecutor(max_workers=16) as ex:
            futures = []
            for item in tasks:
                if len(item) == 2:
                    url, path = item; sha1 = None
                else:
                    url, path, sha1 = item
                futures.append(ex.submit(self.download_file, url, path, sha1))
            for f in as_completed(futures):
                done += 1
                pct = start_pct + (done / total) * (end_pct - start_pct)
                self.update_progress(total=pct, status=f"{name}: {done}/{total}")
                try:
                    f.result()
                except Exception as e:
                    self.log(f"下载失败: {e}")

    def java_executable_name(self):
        return "java.exe" if platform.system() == "Windows" else "java"

    def ensure_lwjgl_unsafe_agent(self, version):
        """自动下载并缓存 HMCL 官方 LWJGL Unsafe Agent，用于缓解 Java 25 + LWJGL 3.4.x 的 AMD/OpenGL 崩溃。"""
        dest = self.base_dir / "agent" / "lwjgl-unsafe-agent.jar"
        if dest.exists() and dest.stat().st_size > 1024:
            try:
                with open(dest, "rb") as _af:
                    _head = _af.read(4)
                if _head[:2] == b"PK":
                    return dest
                else:
                    self.log("?? 检测到损坏的 LWJGL Unsafe Agent 缓存（非 ZIP），将重新下载", level="warning")
                    dest.unlink()
            except Exception as _e:
                self.log(f"?? 校验 LWJGL Unsafe Agent 缓存失败，将重新下载: {_e}", level="warning")
                try:
                    dest.unlink()
                except Exception:
                    pass

        dest.parent.mkdir(parents=True, exist_ok=True)
        candidates = []

        try:
            api_url = "https://api.github.com/repos/HMCL-dev/lwjgl-unsafe-agent/releases/latest"
            resp = requests.get(api_url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            payload = resp.json()
            assets = payload.get("assets") or []
            for asset in assets:
                name = (asset.get("name") or "").lower()
                url = asset.get("browser_download_url")
                if not url:
                    continue
                if "lwjgl-unsafe-agent" in name or "unsafe-agent" in name:
                    candidates.append(url)
        except Exception:
            pass

        candidates.extend([
            "https://github.com/HMCL-dev/lwjgl-unsafe-agent/releases/latest/download/lwjgl-unsafe-agent.jar",
            "https://github.com/HMCL-dev/lwjgl-unsafe-agent/releases/download/2.0/lwjgl-unsafe-agent-2.0.jar",
        ])

        seen = set()
        last_error = None

        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                if tmp.exists() and tmp.stat().st_size > 1024:
                    os.replace(tmp, dest)
                    self.log(f"✅ LWJGL Unsafe Agent 已自动下载并缓存: {dest}", level="info")
                    return dest
            except Exception as e:
                last_error = e
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                self.log(f"⚠️ LWJGL Unsafe Agent 下载失败 ({url}): {e}", level="warning")

        self.log(
            "⚠️ LWJGL Unsafe Agent 下载失败，启动器将继续运行，但 AMD/OpenGL 驱动崩溃风险可能保留。",
            level="warning",
        )
        if last_error is not None:
            self.log(f"  详细信息: {last_error}", level="warning")
        return None
    def required_java_major(self, version: str):
        text = (version or "").strip()
        base = text.split("-")[0].split("_")[0]
        m = re.match(r"^(\d+)\.(\d+)(?:.(\d+))?", base)
        if not m:
            sm = re.match(r"^(\d{2})w", base.lower())
            if sm:
                year = int(sm.group(1))
                return 21 if year >= 24 else 17
            return 17
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3) or 0)
        if major != 1:
            return 21 if major >= 26 else 17
        if minor > 20 or (minor == 20 and patch >= 5):
            return 21
        if minor >= 18:
            return 17
        if minor == 17:
            return 16
        return 8

    def version_required_java(self, version: str):
        text = (version or "").strip()
        if text:
            vdir = self.version_dir / text
            try:
                jsons = sorted(vdir.glob("*.json"))
            except Exception:
                jsons = []
            for js in jsons:
                try:
                    with open(js, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                jv = data.get("javaVersion") or {}
                need = jv.get("majorVersion")
                if isinstance(need, int) and need > 0:
                    return need
                parent = data.get("inheritsFrom")
                if parent:
                    return self.version_required_java(parent)
        return self.required_java_major(text)


    def java_major_version(self, java_path: str):
        """运行 java -version，解析主版本号。"""
        try:
            p = Path(java_path)
            if not p.exists():
                return None
            proc = subprocess.run(
                [str(p), "-version"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
            )
            text = (proc.stderr or "") + "\n" + (proc.stdout or "")
            m = re.search(r'version\s+"([^"]+)"', text)
            if not m:
                return None
            ver = m.group(1)
            if ver.startswith("1."):
                parts = ver.split(".")
                return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            first = ver.split(".")[0]
            return int(first) if first.isdigit() else None
        except Exception:
            return None

    def collect_java_candidates(self):
        """收集所有可能的 Java 可执行文件候选。"""
        exe = self.java_executable_name()
        candidates = []
        seen = set()

        def add(path):
            if not path:
                return
            try:
                p = Path(path)
                if p.is_dir():
                    p = p / "bin" / exe
                if p.exists() and str(p) not in seen:
                    seen.add(str(p))
                    candidates.append(str(p))
            except Exception:
                pass

                       
        try:
            add(self.java_path_var.get().strip())
        except Exception:
            add(getattr(self, "java_path", ""))

                                      
        add(self.base_dir / "runtime" / "java-runtime-epsilon" / "bin" / exe)
        add(self.base_dir / "runtime" / "java-runtime-delta" / "bin" / exe)
        add(self.base_dir / "runtime" / "java-runtime-gamma" / "bin" / exe)
        add(self.base_dir / "runtime" / "java-runtime-beta" / "bin" / exe)
        add(self.base_dir / "runtime" / "java-runtime-alpha" / "bin" / exe)
        if self.version_dir.exists():
            for vdir in self.version_dir.iterdir():
                if vdir.is_dir():
                    for runtime in vdir.glob(f"runtime/*/bin/{exe}"):
                        add(runtime)

                                           
        java_root = Path.home() / "Java"
        if java_root.exists():
            for d in java_root.iterdir():
                if d.is_dir():
                    add(d / "bin" / exe)
                               
                    for nested in d.rglob(exe):
                        add(nested)

                            
        if platform.system() == "Windows":
            for root in [
                Path("C:/Program Files/Java"),
                Path("C:/Program Files/Eclipse Adoptium"),
                Path("C:/Program Files/Microsoft"),
                Path("C:/Program Files/Amazon Corretto"),
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium",
            ]:
                if root.exists():
                    for p in root.rglob(exe):
                        add(p)
        else:
            for root in [Path("/usr/lib/jvm"), Path("/opt"), Path.home() / ".sdkman" / "candidates" / "java"]:
                if root.exists():
                    for p in root.rglob(exe):
                        add(p)

                         
        which_java = shutil.which(exe)
        add(which_java)

                                          
        custom_paths, ignored_paths = self._java_list_prefs()
        ignored_set = {str(Path(p)) for p in ignored_paths if p}
                        
        candidates = [c for c in candidates if str(Path(c)) not in ignored_set]
        seen = {str(Path(c)) for c in candidates}
                        
        for p in custom_paths:
            try:
                pp = Path(p)
                if pp.is_dir():
                    pp = pp / "bin" / exe
                if pp.exists() and str(pp) not in seen:
                    seen.add(str(pp))
                    candidates.append(str(pp))
            except Exception:
                pass

        return candidates

    def find_bundled_java(self):
                                         
        for p in self.collect_java_candidates():
            if "java-runtime" in p:
                return p
        return self.java_path_var.get().strip() if hasattr(self, "java_path_var") else getattr(self, "java_path", "java")

    def auto_find_java(self, mc_version=None, apply=True, log_result=True):
        required = self.version_required_java(mc_version) if mc_version else None
        candidates = self.collect_java_candidates()

        scored = []
        for path in candidates:
            major = self.java_major_version(path)
            if major is None:
                continue
            if required is None:
                score = (0, 0, major)
            elif major < required:
                score = (3, required - major, major)
            else:
                score = (0, major - required, major)
            scored.append((score, path, major))

        if not scored:
            fallback = shutil.which(self.java_executable_name()) or "java"
            if log_result:
                self.log(f"⚠️ 未找到可用 Java，回退到: {fallback}")
            if apply:
                try:
                    self.java_path_var.set(fallback)
                    self.java_path = fallback
                    self.save_config()
                except Exception:
                    pass
            return fallback, None

        scored.sort(key=lambda x: (x[0], x[2]))
        best_path = scored[0][1]
        best_major = scored[0][2]

        if log_result:
            self.log(f"☕ 自动搜索 Java 完成：{best_path} (Java {best_major})")
            if required is not None:
                if best_major == required:
                    self.log(f"✅ {mc_version} 需要 Java {required}，已匹配")
                elif best_major > required:
                    self.log(f"ℹ️ 未找到 Java {required}，使用更高版本 Java {best_major}")
                else:
                    self.log(f"⚠️ {mc_version} 需要 Java {required}，当前只有 Java {best_major}")
                    self.log("❌ Java 版本过低会导致「找不到或无法加载主类」，请安装对应 Java")

        if apply:
            try:
                self.java_path_var.set(best_path)
                self.java_path = best_path
                self.save_config()
            except Exception:
                pass
        return best_path, best_major

    def resolve_launch_java(self, version, vjson):
        required = None
        jv = (vjson or {}).get("javaVersion") or {}
        need = jv.get("majorVersion")
        if isinstance(need, int) and need > 0:
            required = need
        else:
            required = self.version_required_java(version)

        candidates = []
        try:
            configured = self.java_path_var.get().strip()
        except Exception:
            configured = getattr(self, "java_path", "")
        if configured:
            candidates.append(configured)
        if version:
            candidates.append(str(self.base_dir / "runtime" / "java-runtime-epsilon" / "bin" / self.java_executable_name()))
            candidates.append(str(self.base_dir / "runtime" / "java-runtime-delta" / "bin" / self.java_executable_name()))

        chosen = None
        chosen_major = None
        for path in candidates:
            if not path:
                continue
            if not Path(path).exists():
                continue
            major = self.java_major_version(path)
            if major is None:
                continue
            if major >= (required or 0):
                chosen = path
                chosen_major = major
                break

        if chosen is None:
            chosen, chosen_major = self.auto_find_java(version, apply=True, log_result=False)
            if not chosen or not Path(chosen).exists():
                chosen = shutil.which(self.java_executable_name()) or "java"
                chosen_major = self.java_major_version(chosen)

        if required is not None:
            if chosen_major is None or chosen_major < required:
                self.log(f"❌ {version} 需要 Java {required}，当前仅为 Java {chosen_major}，启动必然失败")
                self.log(f"💡 请在设置中指定 Java {required} 路径，或安装对应版本 Java")
            else:
                self.log(f"☕ 使用 Java {chosen_major}（需要 Java {required}）")
        return chosen, chosen_major, required


    def extract_natives(self, vdir, vjson):
        natives_dir = vdir / "natives"
        natives_dir.mkdir(parents=True, exist_ok=True)
        has_natives = False
        if 'libraries' in vjson:
            for lib in vjson['libraries']:
                if 'natives' in lib and 'classifiers' in lib.get('downloads', {}):
                    has_natives = True
                    break
        if not has_natives:
            return
        self.log("提取原生库文件...")
        for lib in vjson['libraries']:
            if 'natives' in lib and 'classifiers' in lib.get('downloads', {}):
                natives = lib['natives']
                classifier = natives.get('windows')
                if classifier and classifier in lib['downloads']['classifiers']:
                    artifact = lib['downloads']['classifiers'][classifier]
                    lib_path = self.libraries_dir / artifact['path']
                    if lib_path.exists():
                        try:
                            with zipfile.ZipFile(lib_path, 'r') as zf:
                                zf.extractall(natives_dir)
                            self.log(f"提取原生库: {lib_path.name}")
                        except:
                            try:
                                shutil.copy2(lib_path, natives_dir / lib_path.name)
                            except:
                                pass
        self.log("原生库提取完成")
    
    def get_natives_dir(self, vdir, version):
        version_natives = vdir / f"{version}-natives"
        if version_natives.exists():
            return version_natives
        return vdir / "natives"
    
    def launch_bedrock(self):
        if platform.system() != "Windows":
            messagebox.showinfo("提示", "基岩版（Windows）仅支持在 Windows 系统上运行")
            return
        launched = False
        try:
            os.startfile("minecraft://")
            launched = True
        except Exception:
            launched = False
        if not launched:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", "minecraft://"])
                launched = True
            except Exception:
                launched = False
        if not launched:
            pfn = ""
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-AppxPackage -Name Microsoft.MinecraftUWP).PackageFamilyName"],
                    capture_output=True, text=True, timeout=20
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    pfn = proc.stdout.strip().splitlines()[0].strip()
            except Exception:
                pfn = ""
            if pfn:
                try:
                    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + pfn + "!App"])
                    launched = True
                except Exception:
                    launched = False
        if launched:
            self.log("🚀 正在启动 Minecraft 基岩版")
        else:
            messagebox.showwarning("提示", "未检测到已安装的基岩版，请在 Microsoft Store 中安装 Minecraft（基岩版）")

    def _open_bedrock_store(self):
        store_url = "ms-windows-store://pdp/?productid=9NBLGGH2JHXJ"
        opened = False
        try:
            os.startfile(store_url)
            opened = True
        except Exception:
            opened = False
        if not opened:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", store_url])
                opened = True
            except Exception:
                opened = False
        if not opened:
            try:
                webbrowser.open("https://apps.microsoft.com/detail/9NBLGGH2JHXJ")
                opened = True
            except Exception:
                opened = False
        if opened:
            self.log("⬇️ 正在打开 Microsoft Store 下载 Minecraft 基岩版")
        else:
            messagebox.showwarning("提示", "无法打开 Microsoft Store，请手动访问 Minecraft 官网下载基岩版")

    def download_bedrock(self):
        if platform.system() != "Windows":
            messagebox.showinfo("提示", "基岩版（Windows）仅支持在 Windows 系统上运行")
            return
        if getattr(self, "_bedrock_download_open", False):
            messagebox.showinfo("提示", "基岩版下载窗口已打开")
            return
        try:
            probe = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-AppxPackage -Name Microsoft.MinecraftUWP).PackageFamilyName"],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            installed = bool(probe.stdout.strip())
        except Exception:
            installed = False
        if installed:
            if messagebox.askyesno("提示", "已安装 Minecraft 基岩版，是否直接启动？"):
                self.launch_bedrock()
            return
        winget = shutil.which("winget")
        if not winget:
            if messagebox.askyesno("提示", "未检测到 winget，是否打开 Microsoft Store 下载基岩版？"):
                self._open_bedrock_store()
            return
        self._bedrock_download_open = True
        d = tk.Toplevel(self.root)
        d.title("下载 Minecraft")
        d.geometry("760x420")
        d.minsize(620, 340)
        d.transient(self.root)
        top = ttk.Frame(d, padding=10)
        top.pack(fill=tk.BOTH, expand=True)
        status_var = tk.StringVar(value="正在准备 winget...")
        pct_var = tk.DoubleVar(value=0)
        ttk.Label(top, textvariable=status_var).pack(anchor=tk.W)
        bar = ttk.Progressbar(top, variable=pct_var, maximum=100, mode="indeterminate")
        bar.pack(fill=tk.X, pady=6)
        bar.start(12)
        pct_label = ttk.Label(top, text="进度：—")
        pct_label.pack(anchor=tk.W)
        detail = scrolledtext.ScrolledText(top, height=12, wrap=tk.WORD, state=tk.DISABLED)
        detail.pack(fill=tk.BOTH, expand=True, pady=6)
        bottom = ttk.Frame(top)
        bottom.pack(fill=tk.X)
        state = {"proc": None, "done": False, "cancelled": False, "pct": -1}
        q = Queue()

        def append_detail(text):
            detail.config(state=tk.NORMAL)
            line_count = int(detail.index("end-1c").split(".")[0])
            if line_count > 400:
                detail.delete("1.0", "80.0")
            detail.insert(tk.END, text + "\n")
            detail.see(tk.END)
            detail.config(state=tk.DISABLED)

        def finish(message, ok=False):
            state["done"] = True
            try:
                bar.stop()
            except Exception:
                pass
            if ok or state["pct"] >= 0:
                try:
                    bar.config(mode="determinate")
                except Exception:
                    pass
                pct_var.set(100 if ok else state["pct"])
                pct_label.config(text=f"进度：{100 if ok else state['pct']}%")
            status_var.set(message)
            self._bedrock_download_open = False
            if ok:
                self.log("✅ Minecraft 下载安装完成")
            else:
                self.log("❌ Minecraft 下载未完成：" + message)

        def on_close():
            if state["done"]:
                d.destroy()
                return
            if not messagebox.askyesno("取消下载", "确定要取消 winget 下载吗？", parent=d):
                return
            state["cancelled"] = True
            proc = state.get("proc")
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            self._bedrock_download_open = False
            d.destroy()

        d.protocol("WM_DELETE_WINDOW", on_close)
        ttk.Button(bottom, text="取消", command=on_close).pack(side=tk.RIGHT)

        def clean_line(text):
            text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
            text = text.replace("\x1b", "")
            return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32).strip()

        def handle_line(text):
            text = clean_line(text)
            if not text:
                return
            append_detail(text)
            lower = text.lower()
            matches = re.findall(r"(\d{1,3})\s*%", text)
            if matches:
                pct = min(100, max(0, int(matches[-1])))
                state["pct"] = pct
                try:
                    bar.stop()
                    bar.config(mode="determinate")
                except Exception:
                    pass
                pct_var.set(pct)
                pct_label.config(text=f"进度：{pct}%")
            if "已找到" in text or "found" in lower:
                status_var.set("已找到安装包，正在准备下载...")
            elif "正在下载" in text or "downloading" in lower:
                status_var.set("正在下载安装包...")
            elif "正在安装" in text or "installing" in lower:
                status_var.set("正在安装...")
            elif "已成功" in text or "successfully" in lower:
                status_var.set("安装完成")

        def worker():
            cmd = [
                winget, "install", "Minecraft", "--source", "winget",
                "--accept-package-agreements", "--accept-source-agreements"
            ]
            q.put(("line", "$ " + " ".join(cmd)))
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                state["proc"] = proc
                buf = ""
                while True:
                    ch = proc.stdout.read(1)
                    if not ch:
                        break
                    if ch in "\r\n":
                        if buf:
                            q.put(("line", buf))
                            buf = ""
                    else:
                        buf += ch
                if buf:
                    q.put(("line", buf))
                q.put(("done", proc.wait()))
            except Exception as e:
                q.put(("error", str(e)))

        def poll():
            try:
                if not d.winfo_exists():
                    return
            except tk.TclError:
                return
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "line":
                        handle_line(payload)
                    elif kind == "error":
                        append_detail("错误：" + payload)
                        finish("下载失败：" + payload)
                    elif kind == "done":
                        if state["cancelled"]:
                            return
                        if payload == 0:
                            finish("下载安装完成，可以启动 Minecraft 了", ok=True)
                        else:
                            finish(f"安装失败，winget 返回码 {payload}")
                            if messagebox.askyesno(
                                "提示",
                                "winget 未能完成安装，是否打开 Microsoft Store 尝试安装？",
                                parent=d
                            ):
                                self._open_bedrock_store()
            except queue.Empty:
                pass
            try:
                if d.winfo_exists():
                    d.after(80, poll)
            except tk.TclError:
                pass

        self.log("⬇️ 正在使用 winget 后台下载 Minecraft")
        threading.Thread(target=worker, daemon=True).start()
        d.after(80, poll)

    def launch_async(self):
        if self.is_launching:
            self.log("游戏正在启动中...")
            return
        version = self.get_selected_launch_version()
        if not version or version == "--- 暂无本地版本 ---":
            messagebox.showerror("错误", "请选择已安装的版本")
            return
        if self.login_mode.get() == "microsoft" and not self.auth.is_authenticated():
            messagebox.showerror("错误", "请先登录 Microsoft 账号")
            self.notebook.select(self.auth_tab)
            return
        self.save_config()
        self.is_launching = True
        self.launch_btn.config(state="disabled")
        self.open_console_window()
        self.log(f"正在启动 {version}...")
        threading.Thread(target=self.launch_game, args=(version,), daemon=True).start()
    
    def launch_game(self, version):
        # 启动前确保微软 token 有效（强制刷新，失败则不启动）
        if self.login_mode.get() == "microsoft" and self.auth.is_authenticated():
            try:
                ok = self.auth.ensure_valid_auth()
            except Exception as _e:
                self.log(f"token 预刷新异常: {_e}")
                ok = False
            if not ok:
                self.log("? 微软令牌刷新失败，请重新登录")
                self.root.after(0, lambda: messagebox.showerror(
                    "需要重新登录",
                    "微软登录令牌已失效（可能在其他设备登录过/改密码/长期未使用）。\n请到「账号」页重新登录微软账号后再启动。"
                ))
                try:
                    self.root.after(0, lambda: self.notebook.select(self.auth_tab))
                except Exception:
                    pass
                try:
                    self.is_launching = False
                    self.launch_btn.config(state="normal")
                except Exception:
                    pass
                return
        classpath_file = None
        return_code = None                                 
        if getattr(self, 'auto_backup_var', None) and self.auto_backup_var.get():
            threading.Thread(target=self._auto_backup_worlds, args=(version,), daemon=True).start()
        try:
            vdir = self.version_dir / version

                                                   
            is_virtual_vanilla = False
            if not vdir.exists() or not list(vdir.glob("*.json")):
                self.log(f"🔍 版本 {version} 本地不存在，尝试从 Fabric 版本继承...")
                fabric_dirs = [
                    d for d in self.version_dir.iterdir()
                    if d.is_dir() and d.name.startswith(f"{version}-Fabric-")
                ]
                if not fabric_dirs:
                    self.log(f"❌ 未找到版本配置文件: {version}")
                    self.root.after(0, lambda: self.launch_btn.config(state="normal"))
                    self.root.after(0, lambda: setattr(self, 'is_launching', False))
                    return

                fabric_dir = fabric_dirs[0]
                self.log(f"🔧 使用 Fabric 版本 {fabric_dir.name} 的 JSON 作为原版")
                vdir = fabric_dir
                is_virtual_vanilla = True

            json_files = list(vdir.glob("*.json"))
            if not json_files:
                self.log(f"❌ 未找到版本配置文件: {version}")
                self.root.after(0, lambda: self.launch_btn.config(state="normal"))
                self.root.after(0, lambda: setattr(self, 'is_launching', False))
                return

            with open(json_files[0], 'r', encoding='utf-8') as f:
                vjson = json.load(f)

                                                   
            if is_virtual_vanilla:
                vjson['mainClass'] = 'net.minecraft.client.main.Main'
                if 'libraries' in vjson:
                    original = vjson['libraries']
                    vjson['libraries'] = [
                        lib for lib in original
                        if not (
                            'fabric' in lib.get('name', '').lower() or
                            'fabricmc' in lib.get('url', '').lower()
                        )
                    ]
                    self.log(f"🔧 虚拟原版模式：过滤 {len(original) - len(vjson['libraries'])} 个 Fabric 库")
                self.log(f"📋 虚拟原版模式：忽略 Fabric 加载器")

            self.log(f"📋 版本: {version}")
            self.log(f"📋 类型: {vjson.get('type', 'unknown')}")

            is_snapshot = self.is_snapshot_version(version)
            uses_old_lwjgl = self.uses_old_lwjgl(vjson)

            version_lower = version.lower()
            is_fabric = "fabric" in version_lower
            is_neoforge = "neoforge" in version_lower
            is_forge = ("forge" in version_lower) and not is_fabric and not is_neoforge

            if is_neoforge:
                self.log("🔧 检测到 NeoForge 版本")
                main_class = vjson.get('mainClass', 'cpw.mods.bootstraplauncher.BootstrapLauncher')
            elif is_forge:
                self.log("🔧 检测到 Forge 版本")
                main_class = vjson.get('mainClass', 'cpw.mods.bootstraplauncher.BootstrapLauncher')
            elif is_fabric:
                self.log("🔧 检测到 Fabric 版本")
                main_class = "net.fabricmc.loader.impl.launch.knot.KnotClient"
            else:
                main_class = vjson.get('mainClass', 'net.minecraft.client.main.Main')

            self.log(f"📋 主类: {main_class}")

            self.extract_natives(vdir, vjson)
            natives_dir = self.get_natives_dir(vdir, version)

            (natives_dir / "netty").mkdir(parents=True, exist_ok=True)
            (natives_dir / "jna").mkdir(parents=True, exist_ok=True)
            (natives_dir / "lwjgl").mkdir(parents=True, exist_ok=True)

            if hasattr(self, 'auto_chinese') and self.auto_chinese.get():
                self.set_game_language_to_chinese(vdir)

            agent_path = self.ensure_lwjgl_unsafe_agent(version)
            if agent_path and Path(agent_path).exists():
                self.log(f"🛡️ LWJGL Unsafe Agent 已就绪: {agent_path}")
            else:
                self.log("⚠️ LWJGL Unsafe Agent 未就绪，启动将继续（AMD/OpenGL 风险可能保留）")

            java_path, java_major, java_required = self.resolve_launch_java(version, vjson)
            self.log(f"📌 Java路径: {java_path} (Java {java_major})")

            cmd = [java_path]
            memory = self.memory_var.get()
            cmd.extend([f"-Xmx{memory}M", f"-Xms{memory}M"])
            cmd.extend([
                "-Dfile.encoding=COMPAT",
                "-Dstderr.encoding=UTF-8",
                "-Dstdout.encoding=UTF-8"
            ])

            if hasattr(self, 'authlib_enabled') and self.authlib_enabled.get():
                authlib_manager = AuthlibInjectorManager(self.base_dir, log_callback=self.log)
                if authlib_manager.download_authlib():
                    auth_server = self.authlib_server_var.get().strip()
                    if auth_server:
                        authlib_args = authlib_manager.get_authlib_args(auth_server)
                        if authlib_args:
                            cmd.extend(authlib_args)
                            self.log(f"🔐 已启用外置登录: {auth_server}")
                        else:
                            self.log("⚠️ Authlib-Injector 加载失败")
                    else:
                        self.log("⚠️ 请输入认证服务器 URL")
                else:
                    self.log("⚠️ Authlib-Injector 下载失败")

            _is_amd_gpu = self.detect_amd_gpu()
            if uses_old_lwjgl:
                self.log("⚠️ LWJGL 3.2 旧版本，跳过 Agent（不兼容）")
            elif _is_amd_gpu:
                self.log("AMD GPU detected, skip LWJGL Unsafe Agent (causes atio6axx.dll crash)", level='warning')
            elif (not _is_amd_gpu) and agent_path and Path(agent_path).exists():
                cmd.append(f'-javaagent:{agent_path}')
                self.log(f"? 已加载 LWJGL Unsafe Agent（缓解 AMD OpenGL / atio6axx.dll 崩溃）: -javaagent:{agent_path}")
            else:
                self.log("⚠️ 未找到 LWJGL Unsafe Agent")

            cmd.extend([
                "-XX:-UseAdaptiveSizePolicy",
                "-XX:-OmitStackTraceInFastThrow",
                "-Djdk.lang.Process.allowAmbiguousCommands=true",
                "-Dfml.ignoreInvalidMinecraftCertificates=True",
                "-Dfml.ignorePatchDiscrepancies=True",
                "-Dlog4j2.formatMsgNoLookups=true",
                "-XX:HeapDumpPath=MojangTricksIntelDriversForPerformance_javaw.exe_minecraft.exe.heapdump"
            ])

            cmd.extend([
                "-Dorg.lwjgl.vulkan.allowValidation=false",
                "-Dorg.lwjgl.vulkan.libname=vulkan-1.dll",
            ])

                                      
            if hasattr(self, 'custom_jvm_args_var'):
                if _is_amd_gpu:
                    self.log("?? AMD 显卡：跳过自定义 JVM 参数（GC 调优组合会触发 atio6axx.dll 崩溃）", level='warning')
                else:
                    custom_args = self.custom_jvm_args_var.get().strip()
                    if custom_args:
                        for arg in custom_args.split():
                            if arg and arg not in cmd:
                                cmd.append(arg)
                        self.log(f"??? 已应用自定义 JVM 参数: {custom_args}", level='info')

                                                
            if getattr(self, "use_all_cores_var", None) is not None and self.use_all_cores_var.get() and not _is_amd_gpu:
                _cores = os.cpu_count() or 4
                _arg = f"-XX:ActiveProcessorCount={_cores}"
                if _arg not in cmd:
                    cmd.append(_arg)
                    self.log(f"⚡ 已启用全部 CPU 核心: {_cores}", level='info')
            if getattr(self, "gpu_acceleration_var", None) is not None and self.gpu_acceleration_var.get():
                _backend = self.render_backend_var.get() if hasattr(self, "render_backend_var") else getattr(self, "render_backend_value", "OpenGL")
                _backend_args = []
                if _backend == "Vulkan":
                    _backend_args = ["-Dorg.lwjgl.vulkan.allowValidation=false", "-Dorg.lwjgl.vulkan.libname=vulkan-1.dll"]
                else:
                    _backend_args = []
                for _ga in _backend_args:
                    if _ga not in cmd:
                        cmd.append(_ga)
                if _backend == "OpenGL":
                    self.log("⚡ OpenGL 硬件加速已禁用以避免 AMD 驱动崩溃", level='info')
                else:
                    self.log(f"⚡ 已启用显卡硬件加速: {_backend}", level='info')

            if natives_dir.exists():
                if uses_old_lwjgl:
                    cmd.append(f"-Djava.library.path={natives_dir}")
                    self.log(f"✅ 已设置原生库路径（旧版模式）: {natives_dir}")
                else:
                    if is_snapshot:
                        cmd.append(f"-Djava.library.path={natives_dir}")
                    else:
                        cmd.append(f"-Djava.library.path={natives_dir}/java")
                    cmd.append(f"-Djna.tmpdir={natives_dir}/jna")
                    cmd.append(f"-Dorg.lwjgl.system.SharedLibraryExtractPath={natives_dir}/lwjgl")
                    cmd.append(f"-Dio.netty.native.workdir={natives_dir}/netty")
                    self.log("✅ 已设置原生库路径")

            cmd.extend([
                "-Dminecraft.launcher.brand=PML",
                "-Dminecraft.launcher.version=522"
            ])

                                              
            classpath_parts = []

            if not uses_old_lwjgl and agent_path and not _is_amd_gpu:
                classpath_parts.append(str(Path(agent_path)))

            version_jar = vdir / f"{version}.jar"
            if version_jar.exists():
                classpath_parts.append(str(version_jar))

                                                 
            all_libraries = []
            if 'libraries' in vjson:
                all_libraries.extend(vjson['libraries'])

            if 'inheritsFrom' in vjson:
                parent_id = vjson['inheritsFrom']
                parent_json = self.version_dir / parent_id / f"{parent_id}.json"
                if parent_json.exists():
                    with open(parent_json, 'r', encoding='utf-8') as pf:
                        parent_vjson = json.load(pf)
                    if 'libraries' in parent_vjson:
                        all_libraries.extend(parent_vjson['libraries'])
                        self.log(f"🔗 已合并原版 {parent_id} 的 {len(parent_vjson['libraries'])} 个库")

            for lib in all_libraries:
                if 'downloads' in lib and 'artifact' in lib['downloads']:
                    path = lib['downloads']['artifact']['path']
                    lib_path = self.libraries_dir / path
                    if lib_path.exists() and str(lib_path) not in classpath_parts:
                        classpath_parts.append(str(lib_path))
                elif 'name' in lib:
                    parts = lib['name'].split(':')
                    if len(parts) >= 3:
                        group = parts[0].replace('.', '/')
                        artifact = parts[1]
                        version_id = parts[2]
                        jar_path = self.libraries_dir / group / artifact / version_id / f"{artifact}-{version_id}.jar"
                        if jar_path.exists() and str(jar_path) not in classpath_parts:
                            classpath_parts.append(str(jar_path))

            classpath_file = vdir / "classpath.txt"
            classpath_content = ";".join(classpath_parts)
            with open(classpath_file, 'w', encoding='utf-8') as f:
                f.write(classpath_content)

            self.log(f"📄 Classpath已保存到: {classpath_file}")
            self.log(f"📊 Classpath包含 {len(classpath_parts)} 个库文件")

                                                                               
            if is_forge or is_neoforge:
                                                                          
                legacy_cp = ";".join(classpath_parts)
                cmd.append(f"-DlegacyClassPath={legacy_cp}")
                self.log(f"🔧 已设置 legacyClassPath（{len(classpath_parts)} 个条目）")

                                                   
                self.log("🔧 正在从 Forge/NeoForge JSON 读取 JVM 参数...")
                try:
                    args_obj = vjson.get('arguments', {})
                    jvm_list = args_obj.get('jvm', [])
                    added = 0
                    for item in jvm_list:
                        if isinstance(item, str):
                            val = item
                            val = val.replace("${library_directory}", str(self.libraries_dir))
                            val = val.replace("${classpath_separator}", ";")
                            val = val.replace("${version_name}", version)
                            if val and val not in cmd:
                                cmd.append(val)
                                added += 1
                        elif isinstance(item, dict):
                            rules = item.get('rules', [])
                            allow = False
                            for rule in rules:
                                action = rule.get('action')
                                os_name = rule.get('os', {}).get('name')
                                if action == 'allow' and (os_name == 'windows' or os_name is None):
                                    allow = True
                                elif action == 'allow' and os_name is not None:
                                    allow = False
                            if allow:
                                value = item.get('value')
                                if isinstance(value, str):
                                    value = value.replace("${library_directory}", str(self.libraries_dir))
                                    value = value.replace("${classpath_separator}", ";")
                                    value = value.replace("${version_name}", version)
                                    if value and value not in cmd:
                                        cmd.append(value)
                                        added += 1
                                elif isinstance(value, list):
                                    for v in value:
                                        v = v.replace("${library_directory}", str(self.libraries_dir))
                                        v = v.replace("${classpath_separator}", ";")
                                        v = v.replace("${version_name}", version)
                                        if v and v not in cmd:
                                            cmd.append(v)
                                            added += 1
                    if added:
                        self.log(f"🔧 从 Forge/NeoForge JSON 添加了 {added} 个 JVM 参数")
                    else:
                        self.log("⚠️ Forge/NeoForge JSON 里没有 JVM 参数")
                except Exception as e:
                    self.log(f"⚠️ 解析 Forge/NeoForge JVM 参数失败: {e}", level='warning')

                                     
            cmd.append("-cp")
            cmd.append(classpath_content)
            cmd.append(main_class)

                                
            username = self.username_var.get().strip() or "Player"
            uuid_val = self.get_offline_uuid(username)
            access_token = f"offline:{uuid_val}"
            user_type = "mojang"

            if self.login_mode.get() == "microsoft" and self.auth.is_authenticated():
                auth_args = self.auth.get_game_args()
                if auth_args:
                    username = auth_args.get('--username', username)
                    uuid_val = auth_args.get('--uuid') or uuid_val
                    access_token = auth_args.get('--accessToken', access_token)
                    user_type = auth_args.get('--userType', user_type)
                    self.log(f"✅ 使用正版账号: {username}")
                else:
                    self.log(f"⚠️ 正版令牌无效，使用离线模式 (UUID: {uuid_val})")
            else:
                self.log(f"🎮 离线模式 (UUID: {uuid_val})")

                                                 
            asset_index_obj = None

            if 'inheritsFrom' in vjson:
                parent_id = vjson['inheritsFrom']
                parent_json_path = self.version_dir / parent_id / f"{parent_id}.json"
                if parent_json_path.exists():
                    try:
                        with open(parent_json_path, 'r', encoding='utf-8') as pf:
                            parent_vjson = json.load(pf)
                        asset_index_obj = parent_vjson.get("assetIndex")
                        if asset_index_obj:
                            self.log(f"🔗 已从原版 {parent_id} 继承资源索引: {asset_index_obj.get('id')}")
                    except Exception as e:
                        self.log(f"⚠️ 读取原版 JSON 失败: {e}", level='warning')
                else:
                    self.log(f"⚠️ 原版 JSON 不存在: {parent_json_path}", level='warning')

            if not asset_index_obj:
                asset_index_obj = vjson.get("assetIndex")
                if asset_index_obj:
                    self.log(f"⚠️ 使用 Fabric JSON 里的 assetIndex: {asset_index_obj.get('id')}", level='warning')

            if asset_index_obj:
                asset_index = asset_index_obj.get("id", "1.17")
                idx_file = self.assets_dir / "indexes" / f"{asset_index}.json"
                if not idx_file.exists():
                    self.log(f"⚠️ 资源索引文件缺失: {idx_file}", level='warning')
            else:
                if is_snapshot:
                    asset_index = "32"
                else:
                    asset_index = "1.17"
                self.log(f"⚠️ 未找到 assetIndex，使用默认值: {asset_index}", level='warning')

            use_long_token = is_snapshot and self.login_mode.get() != "microsoft"
            final_access_token = PCL2_ACCESS_TOKEN if use_long_token else access_token

                                                                 
            if is_forge or is_neoforge:
                game_args = ["--launchTarget", "forgeclient"]
            else:
                game_args = []

            win_width, win_height = self.get_game_window_size()
            game_args.extend([
                "--username", username,
                "--version", version,
                "--gameDir", str(vdir),
                "--assetsDir", str(self.assets_dir),
                "--assetIndex", str(asset_index),
                "--uuid", uuid_val,
                "--accessToken", final_access_token,
                "--clientId", "f6a6eaca-1bc1-4273-ba4b-a1677efd383b",
                "--xuid", "0",
                "--userType", user_type,
                "--versionType", "PML",
                "--width", str(win_width),
                "--height", str(win_height)
            ])

            if self.demo_mode.get():
                game_args.append("--demo")
                self.log("🎮 试玩模式已启用")

            cmd.extend(game_args)

            self.log("=" * 60)
            self.log(f"🚀 启动命令 (共 {len(cmd)} 个参数)")
            self.log(f"📌 版本: {version} ({'快照' if is_snapshot else '正式'})")
            self.log(f"📌 内存: {memory}MB")
            self.log(f"📌 窗口: {win_width} × {win_height}")
            self.log(f"📌 Asset Index: {asset_index}")
            self.log(f"📌 LWJGL: {'旧版3.2' if uses_old_lwjgl else '新版3.4+'}")
            self.log(f"📌 账号: {'正版' if self.login_mode.get() == 'microsoft' else '离线'}")
            self.log(f"📌 试玩模式: {'启用' if self.demo_mode.get() else '禁用'}")
            if is_fabric:
                self.log(f"📌 Mod加载器: Fabric")
            elif is_forge:
                self.log(f"📌 Mod加载器: Forge")
            elif is_neoforge:
                self.log(f"📌 Mod加载器: NeoForge")
            if hasattr(self, 'authlib_enabled') and self.authlib_enabled.get():
                self.log(f"📌 外置登录: 已启用")
            if hasattr(self, 'auto_chinese') and self.auto_chinese.get():
                self.log(f"📌 语言: 简体中文 (自动设置)")
            self.log("=" * 60)

            env = os.environ.copy()
            if natives_dir.exists():
                # [DISABLED] PATH 前置已移除: env['PATH'] = str(natives_dir) + os.pathsep + env.get('PATH', '')
                pass
            env['PYTHONIOENCODING'] = 'utf-8'
            # [DISABLED] env['LC_ALL'] = 'en_US.UTF-8'
            # [DISABLED] env['LANG'] = 'en_US.UTF-8'  # 移除：影响 SDL/OpenGL 初始化时序

            use_shell = is_snapshot
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' and not use_shell else 0
            # Windows: 让 java 脱离启动器进程树，避免 Python GUI 的 GPU 上下文干扰 AMD 驱动
            _detach_flags = 0
            if platform.system() == "Windows":
                _detach_flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=_detach_flags,
                env=env,
                encoding="utf-8",
                errors="replace"
            )

            self.start_performance_monitor(process)
            self.log("✅ 游戏已启动！")

            has_normal_exit = False
            crash_log_lines = []
            return_code = None
            process_start_time = time.time()
            def read_output():
                nonlocal has_normal_exit, crash_log_lines, return_code
                try:
                    for line in process.stdout:
                        if line.strip():
                            try:
                                line = line.encode("latin-1").decode("utf-8", errors="replace")
                            except Exception:
                                pass
                            self.log(f"[游戏] {line.rstrip()}")
                            crash_log_lines.append(line)
                except Exception as e:
                    self.log(f"⚠️ 读取输出时出错: {e}")
                finally:
                    self.log("游戏进程已结束")
                    if getattr(self, 'auto_backup_var', None) and self.auto_backup_var.get():
                        try:
                            self._auto_backup_worlds(version)
                        except Exception:
                            pass
                    try:
                        return_code = process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        return_code = process.poll()
                    duration = time.time() - process_start_time
                    try:
                        self._history.add(version, duration, return_code)
                    except Exception as e:
                        self.log(f"⚠️ 记录启动统计失败: {e}", level='warning')

                    if return_code is not None and return_code != 0 and not has_normal_exit:
                        self.log("=" * 60)
                        self.log("💀 Minecraft出现问题")
                        self.log(f"📌 退出代码: {return_code}")

                                         
                        results = CrashAnalyzer.analyze(return_code, crash_log_lines)
                        self.log(CrashAnalyzer.format_results(results))

                                              
                        crash_text = f"进程退出码: {return_code}\n\n" + "\n".join(crash_log_lines)
                        self.log("💥 崩溃日志已记录，可在控制台查看详情")
                    elif return_code == 0 or has_normal_exit:
                        self.log("✅ 游戏正常退出")
                    else:
                        self.log(f"⚠️ 游戏进程结束，退出码: {return_code}")

                    try:
                        if classpath_file and classpath_file.exists():
                            classpath_file.unlink()
                            self.log("🧹 已清理临时classpath文件")
                    except:
                        pass

                    self.root.after(0, lambda: self.launch_btn.config(state="normal"))
                    self.root.after(0, lambda: setattr(self, 'is_launching', False))

            threading.Thread(target=read_output, daemon=True).start()

        except Exception as e:
            self.log(f"❌ 启动失败: {e}")
            import traceback
            self.log(traceback.format_exc())
            try:
                if classpath_file and classpath_file.exists():
                    classpath_file.unlink()
            except:
                pass
            self.root.after(0, lambda: self.launch_btn.config(state="normal"))
            self.root.after(0, lambda: setattr(self, 'is_launching', False))
            messagebox.showerror("错误", f"启动失败: {e}")
    def export_bat_script(self):
        version = self.get_selected_launch_version()
        if not version or version == "--- 暂无本地版本 ---":
            messagebox.showerror("错误", "请先选择一个版本")
            return
        vdir = self.version_dir / version
        if not vdir.exists():
            messagebox.showerror("错误", f"版本 {version} 不存在")
            return
        json_files = list(vdir.glob("*.json"))
        if not json_files:
            messagebox.showerror("错误", f"版本 {version} 的配置文件不存在")
            return
        try:
            with open(json_files[0], 'r', encoding='utf-8') as f:
                vjson = json.load(f)
            self.extract_natives(vdir, vjson)
            natives_dir = self.get_natives_dir(vdir, version)
            java_path, java_major, java_required = self.resolve_launch_java(version, vjson)
            
            jvm_args = [
                "-Dfile.encoding=COMPAT",
                "-Dstderr.encoding=UTF-8",
                "-Dstdout.encoding=UTF-8"
            ]
            uses_old_lwjgl = self.uses_old_lwjgl(vjson)
            
            jvm_args.extend([
                "-Dorg.lwjgl.vulkan.allowValidation=false",
                "-Dorg.lwjgl.vulkan.libname=vulkan-1.dll",
            ])
            
                                      
            if hasattr(self, 'custom_jvm_args_var'):
                custom_args = self.custom_jvm_args_var.get().strip()
                if custom_args:
                    for arg in custom_args.split():
                        if arg and arg not in jvm_args:
                            jvm_args.append(arg)
            if getattr(self, "use_all_cores_var", None) is not None and self.use_all_cores_var.get():
                _cores = os.cpu_count() or 4
                _arg = f"-XX:ActiveProcessorCount={_cores}"
                if _arg not in jvm_args:
                    jvm_args.append(_arg)
            if getattr(self, "gpu_acceleration_var", None) is not None and self.gpu_acceleration_var.get():
                pass
            
            if not uses_old_lwjgl and not self.detect_amd_gpu():
                agent_jar = self.ensure_lwjgl_unsafe_agent(version)
                if agent_jar and Path(agent_jar).exists():
                    jvm_args.append(f'-javaagent:{agent_jar}')
                    self.log("🛡️ 已为导出启动脚本注入 LWJGL Unsafe Agent", level='info')
            
            else:
                self.log("AMD GPU: skip agent injection into exported bat", level='warning')
            jvm_args.extend([
                "-XX:-UseAdaptiveSizePolicy",
                "-XX:-OmitStackTraceInFastThrow",
                "-Djdk.lang.Process.allowAmbiguousCommands=true",
                "-Dfml.ignoreInvalidMinecraftCertificates=True",
                "-Dfml.ignorePatchDiscrepancies=True",
                "-Dlog4j2.formatMsgNoLookups=true",
                "-XX:HeapDumpPath=MojangTricksIntelDriversForPerformance_javaw.exe_minecraft.exe.heapdump"
            ])
            is_snapshot = self.is_snapshot_version(version)
            if natives_dir.exists():
                if uses_old_lwjgl or is_snapshot:
                    jvm_args.append(f'-Djava.library.path="{natives_dir}"')
                else:
                    jvm_args.append(f'-Djava.library.path="{natives_dir}/java"')
                jvm_args.append(f'-Djna.tmpdir="{natives_dir}/jna"')
                jvm_args.append(f'-Dorg.lwjgl.system.SharedLibraryExtractPath="{natives_dir}/lwjgl"')
                jvm_args.append(f'-Dio.netty.native.workdir="{natives_dir}/netty"')
            jvm_args.extend([
                "--sun-misc-unsafe-memory-access=allow",
                "--enable-native-access=ALL-UNNAMED",
                "-Dminecraft.launcher.brand=PML",
                "-Dminecraft.launcher.version=522"
            ])
            
            classpath_parts = []
            if not uses_old_lwjgl and not self.detect_amd_gpu():
                agent_jar = self.ensure_lwjgl_unsafe_agent(version)
                if agent_jar and Path(agent_jar).exists():
                    classpath_parts.append(str(agent_jar))
            version_jar = vdir / f"{version}.jar"
            if version_jar.exists():
                classpath_parts.append(str(version_jar))
            if 'libraries' in vjson:
                for lib in vjson['libraries']:
                    if 'downloads' in lib and 'artifact' in lib['downloads']:
                        path = lib['downloads']['artifact']['path']
                        lib_path = self.libraries_dir / path
                        if lib_path.exists():
                            classpath_parts.append(str(lib_path))
                    elif 'name' in lib:
                        parts = lib['name'].split(':')
                        if len(parts) >= 3:
                            group = parts[0].replace('.', '/')
                            artifact = parts[1]
                            version_id = parts[2]
                            jar_path = self.libraries_dir / group / artifact / version_id / f"{artifact}-{version_id}.jar"
                            if jar_path.exists():
                                classpath_parts.append(str(jar_path))
            classpath_file = vdir / "classpath.txt"
            classpath_content = ";".join(classpath_parts)
            with open(classpath_file, 'w', encoding='utf-8') as f:
                f.write(classpath_content)
            main_class = vjson.get('mainClass', 'net.minecraft.client.main.Main')
            
                                                               
            if self.login_mode.get() == "microsoft" and self.auth.is_authenticated():
                username = self.auth.username
                uuid_val = self.auth.uuid or self.get_offline_uuid(username)
                access_token = self.auth.access_token
            else:
                username = self.username_var.get().strip() or "Player"
                uuid_val = self.get_offline_uuid(username)
                access_token = f"offline:{uuid_val}"
            
            if is_snapshot:
                asset_index = vjson.get("assetIndex", {}).get("id", "32")
            else:
                asset_index = vjson.get("assetIndex", {}).get("id", "1.17")
            
            use_long_token = is_snapshot and self.login_mode.get() != "microsoft"

            win_width, win_height = self.get_game_window_size()
            game_args = [
                f'--username {username}',
                f'--version {version}',
                f'--gameDir "{vdir}"',
                f'--assetsDir "{self.assets_dir}"',
                f'--assetIndex {asset_index}',
                f'--uuid {uuid_val}',
            ]
            if use_long_token:
                game_args.append(f'--accessToken {PCL2_ACCESS_TOKEN}')
            else:
                game_args.append(f'--accessToken {access_token}')
            game_args.extend([
                '--clientId f6a6eaca-1bc1-4273-ba4b-a1677efd383b',
                '--xuid 0',
                '--userType mojang',
                '--versionType PML',
                f'--width {win_width}',
                f'--height {win_height}'
            ])
            if self.demo_mode.get():
                game_args.append('--demo')
            bat_content = f'''chcp 65001>nul
@echo off
title 启动 - {version}
echo 游戏正在启动，请稍候。
cd /D "{vdir}"

"{java_path}" {" ".join(jvm_args)} -cp "@{classpath_file.name}" {main_class} {" ".join(game_args)}
echo 游戏已退出.
pause'''
            save_path = filedialog.asksaveasfilename(
                title="保存启动脚本",
                defaultextension=".bat",
                filetypes=[("Windows批处理文件", "*.bat"), ("所有文件", "*.*")],
                initialfile=f"启动_{version}.bat"
            )
            if save_path:
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(bat_content)
                self.log(f"✅ 启动脚本已生成: {save_path}")
                messagebox.showinfo("成功", f"启动脚本已保存到:\n{save_path}")
        except Exception as e:
            self.log(f"❌ 生成启动脚本失败: {e}")
            messagebox.showerror("错误", f"生成失败: {e}")
    
    def load_news(self):
        self.save_config()          
        threading.Thread(target=self._load_news_thread, daemon=True).start()
    
    def _load_news_thread(self):
        news_list = self.news_manager.fetch_news()
        self.root.after(0, lambda: self._display_news(news_list))
    
    def _display_news(self, news_list):
                 
        self.news_text.config(state=tk.NORMAL)
        self.news_text.delete(1.0, tk.END)
        self.news_images = []
        self._news_image_tasks = []

                               
        self._news_token = getattr(self, '_news_token', 0) + 1
        token = self._news_token

        if not news_list:
            self.news_text.insert(tk.END, "📡 暂无新闻，请检查网络连接")
            self.news_text.config(state=tk.DISABLED)
            return

        filter_type = self.news_type_var.get()

                                 
        filtered = []
        for news in news_list:
            category = news.get('category', 'Minecraft')
            if filter_type != 'all':
                if filter_type == 'java' and 'Java' not in category:
                    continue
                elif filter_type == 'bedrock' and 'Bedrock' not in category:
                    continue
                elif filter_type == 'dungeons' and 'Dungeons' not in category:
                    continue
            filtered.append(news)
            if len(filtered) >= 30:
                break

                    
        self.news_text.tag_config('icon', font=('Segoe UI Emoji', 11))
        self.news_text.tag_config('title', font=('Microsoft YaHei', 10, 'bold'))
        self.news_text.tag_config('en_title', font=('Microsoft YaHei', 8), foreground='#999999')
        self.news_text.tag_config('desc', font=('Microsoft YaHei', 9), foreground='#555555')
        self.news_text.tag_config('meta', font=('Microsoft YaHei', 8), foreground='#888888')
        self.news_text.tag_config('cat_Minecraft Java', foreground='#00AA00')
        self.news_text.tag_config('cat_Minecraft Bedrock', foreground='#0055FF')
        self.news_text.tag_config('cat_Minecraft Dungeons', foreground='#AA00FF')
        self.news_text.tag_config('cat_Minecraft Launcher', foreground='#FF8800')
        self.news_text.tag_config('cat_Minecraft', foreground='#FF6600')

                
        self._insert_news_batch(filtered, 0, token)


    def _insert_news_batch(self, news_list, start, token):
        """每批插 5 条，避免一次性卡 Text 控件"""
        if token != getattr(self, '_news_token', 0):
            return              

        icons = {
            'Minecraft Java': '🟢',
            'Minecraft Bedrock': '🔵',
            'Minecraft Dungeons': '🟣',
            'Minecraft Launcher': '🟠',
            'Minecraft': '🟡'
        }

        batch_end = min(start + 5, len(news_list))

        for news in news_list[start:batch_end]:
            category = news.get('category', 'Minecraft')
            icon = icons.get(category, '📌')

                            
            img_url = news.get("image_url", "")
            if img_url:
                if img_url.startswith("/"):
                    img_url = "https://launchercontent.mojang.com" + img_url
                mark = self.news_text.index(tk.INSERT)
                self.news_text.insert(tk.END, "\n")
                self._news_image_tasks.append((mark, img_url))

                          
            self.news_text.insert(tk.END, f"{icon} ", 'icon')
            self.news_text.insert(tk.END, f"{news['title']}\n", ('title', f'cat_{category}'))
            if news.get('title_en') and news.get('is_translated', False):
                self.news_text.insert(tk.END, f"  📝 {news['title_en']}\n", 'en_title')
            if news.get('description'):
                desc = news['description']
                for en, zh in self.news_manager.translations.items():
                    if en.lower() in desc.lower():
                        desc = desc.replace(en, zh)
                self.news_text.insert(tk.END, f"  📄 {desc}\n", 'desc')
            date_str = news.get('date', '')
            link_str = news.get('link', '')
            if link_str:
                self.news_text.insert(tk.END, f"  📅 {date_str}  |  📂 {category}  |  🔗 {link_str[:50]}\n\n", 'meta')
            else:
                self.news_text.insert(tk.END, f"  📅 {date_str}  |  📂 {category}\n\n", 'meta')

                               
        if batch_end < len(news_list):
            self.root.after(10, lambda: self._insert_news_batch(news_list, batch_end, token))
        else:
            self.news_text.config(state=tk.DISABLED)
            self.log(f"📰 已加载 {len(news_list)} 条新闻")
                            
            if self._news_image_tasks:
                self._load_news_images_async(self._news_image_tasks, token)


    def _load_news_images_async(self, image_tasks, token):
        """并发下载新闻图片，每张下载完就插到对应位置"""
        if not hasattr(self, '_news_img_cache'):
            self._news_img_cache = {}
        if not hasattr(self, '_img_session'):
            self._img_session = requests.Session()
            self._img_session.headers.update({"User-Agent": "Mozilla/5.0 MinecraftLauncher/1.0"})

        def worker(mark, img_url):
            if img_url in self._news_img_cache:
                cached = self._news_img_cache[img_url]
                if cached is not None:
                    self.root.after(0, lambda c=cached, m=mark: self._insert_news_image(m, c.copy(), token))
                return
            try:
                resp = self._img_session.get(img_url, timeout=8)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content))
                img.thumbnail((160, 90))
                self._news_img_cache[img_url] = img
                self.root.after(0, lambda: self._insert_news_image(mark, img, token))
            except Exception as e:
                self._news_img_cache[img_url] = None
                print(f"DEBUG 图片加载失败: {img_url} - {e}")

        def run_tasks():
            with ThreadPoolExecutor(max_workers=16) as ex:
                for mark, img_url in image_tasks:
                    ex.submit(worker, mark, img_url)

                              
        threading.Thread(target=run_tasks, daemon=True).start()


    def _insert_news_image(self, mark, pil_img, token):
        """在主线程里把 PIL Image 插到 Text 指定位置"""
        if token != getattr(self, '_news_token', 0):
            return              
        try:
            photo = ImageTk.PhotoImage(pil_img)
            self.news_images.append(photo)
            self.news_text.config(state=tk.NORMAL)
            self.news_text.image_create(mark, image=photo)
            self.news_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"DEBUG 图片插入失败: {e}")
    
    def _install_fabric(self):
        mc_version = self.mod_mc_version_var.get().strip()
        loader_version = self.mod_loader_version_var.get().strip() or "latest"
        if not mc_version:
            self.log("❌ 请输入 Minecraft 版本")
            return
        self.log(f"📥 安装 Fabric {mc_version} + {loader_version}...")
        if self.mod_manager.install_fabric(mc_version, loader_version):
            self.refresh_versions()
            self.refresh_local_versions()
            messagebox.showinfo("成功", f"Fabric {mc_version} 安装完成!")
        else:
            messagebox.showerror("失败", "Fabric 安装失败")
    def _install_forge(self):
        mc_version = self.mod_mc_version_var.get().strip()
        loader_version = self.mod_loader_version_var.get().strip() or "latest"
        if not mc_version:
            self.log("❌ 请输入 Minecraft 版本")
            return
        self.log(f"📥 安装 Forge {mc_version} + {loader_version}...")
        self.open_console_window()
        if self.mod_manager.install_forge(mc_version, loader_version):
            self.refresh_versions()
            self.refresh_local_versions()
            messagebox.showinfo("成功", f"Forge {mc_version} 安装完成!")
        else:
            messagebox.showerror("失败", "Forge 安装失败，请查看控制台日志")

    def _install_neoforge(self):
        mc_version = self.mod_mc_version_var.get().strip()
        loader_version = self.mod_loader_version_var.get().strip() or "latest"
        if not mc_version:
            self.log("❌ 请输入 Minecraft 版本")
            return
        self.log(f"📥 安装 NeoForge (MC {mc_version}) + {loader_version}...")
        self.open_console_window()
        if self.mod_manager.install_neoforge(mc_version, loader_version):
            self.refresh_versions()
            self.refresh_local_versions()
            messagebox.showinfo("成功", f"NeoForge (MC {mc_version}) 安装完成!")
        else:
            messagebox.showerror("失败", "NeoForge 安装失败，请查看控制台日志")
    def login_microsoft(self):
        method = self.login_method_var.get()
        method_name = "授权代码流" if method == "auth_code" else "设备代码流"
        self.log(f"🔑 开始 Microsoft 账号登录（{method_name}）...")
        self.open_console_window()
        threading.Thread(
            target=self._do_login_microsoft,
            args=(method,),
            daemon=True
        ).start()
    
    def _do_login_microsoft(self, method="auth_code"):
        if self.auth.authenticate_microsoft(method=method):
            self.root.after(0, lambda: self.auth_status.set(f"已登录: {self.auth.username}"))
            self.root.after(0, self.update_auth_info)
            self.root.after(0, self.save_config)
            self.root.after(0, self.update_login_display)
            self.root.after(0, self.refresh_account_list)
            messagebox.showinfo("登录成功", f"欢迎, {self.auth.username}!")
        else:
            self.root.after(0, lambda: self.auth_status.set("登录失败"))
            messagebox.showerror("登录失败", "Microsoft 账号登录失败，请重试")
    
    def import_from_official(self):
        self.log("📂 正在从官方启动器导入...")
        if self.auth.import_from_official():
            self.root.after(0, lambda: self.auth_status.set(f"已登录: {self.auth.username}"))
            self.root.after(0, self.update_auth_info)
            self.root.after(0, self.save_config)
            self.root.after(0, self.update_login_display)
            self.root.after(0, self.refresh_account_list)
            messagebox.showinfo("成功", f"已导入正版账号: {self.auth.username}")
        else:
            messagebox.showerror("失败", "未找到官方启动器认证文件\n请先在官方启动器中登录")
    
    def manual_token_login(self):
        d = tk.Toplevel(self.root)
        d.title("手动输入 AccessToken")
        d.geometry("500x250")
        d.transient(self.root)
        d.grab_set()
        tk.Label(d, text="从官方启动器或 PCL2 复制 AccessToken 后粘贴：").pack(pady=10)
        token_entry = tk.Entry(d, width=60)
        token_entry.pack(pady=5)
        tk.Label(d, text="玩家名（可选）:").pack(pady=5)
        name_entry = tk.Entry(d, width=30)
        name_entry.pack(pady=5)
        def confirm():
            token = token_entry.get().strip()
            if not token:
                messagebox.showerror("错误", "请输入 AccessToken")
                return
            name = name_entry.get().strip() or "Player"
            self.auth.login_with_token(token, name)
            self.auth_status.set(f"已登录: {name}")
            self.update_auth_info()
            self.update_login_display()
            self.refresh_account_list()
            self.save_config()
            d.destroy()
            messagebox.showinfo("成功", f"已登录: {name}")
        tk.Button(d, text="登录", command=confirm, bg="#4CAF50", fg="white").pack(pady=10)
    
    def refresh_auth_token(self):
        self.log("🔄 刷新认证令牌...")
        if self.auth.refresh_auth():
            self.root.after(0, self.update_auth_info)
            messagebox.showinfo("刷新成功", "认证令牌已刷新")
        else:
            messagebox.showerror("刷新失败", "认证令牌刷新失败，请重新登录")
    
    def logout_auth(self):
        if messagebox.askyesno("确认退出", "确定要退出登录吗？"):
            self.auth.clear_auth()
            self.root.after(0, lambda: self.auth_status.set("离线模式"))
            self.root.after(0, self.update_auth_info)
            self.root.after(0, self.save_config)
            self.root.after(0, self.update_login_display)
            self.log("🚪 已退出登录")

    def _update_auth_avatar(self, uuid=None):
        if not uuid:
            try:
                self.auth_avatar_label.config(image="", text="User")
            except Exception:
                pass
            return
        def _fetch():
            try:
                url = f"https://minotar.net/helm/{uuid}/32.png"
                if not hasattr(self, "_avatar_session"):
                    self._avatar_session = requests.Session()
                    self._avatar_session.headers.update({"User-Agent": "Mozilla/5.0"})
                r = self._avatar_session.get(url, timeout=8)
                r.raise_for_status()
                img = Image.open(BytesIO(r.content)).convert("RGBA")
                img.thumbnail((32, 32), Image.LANCZOS)
                def _apply():
                    try:
                        photo = ImageTk.PhotoImage(img)
                        self._avatar_photo = photo
                        self.auth_avatar_label.config(image=photo, text="")
                    except Exception:
                        pass
                self.root.after(0, _apply)
            except Exception:
                pass
        try:
            threading.Thread(target=_fetch, daemon=True).start()
        except Exception:
            pass

    def refresh_account_list(self):
        accounts = self.auth.list_accounts()
        self.account_combo["values"] = accounts
        if accounts:
            current = self.auth.current_account if self.auth.current_account in accounts else accounts[0]
            self.account_combo_var.set(current)
        else:
            self.account_combo_var.set("")

    def switch_saved_account(self):
        username = self.account_combo_var.get().strip()
        if not username:
            return
        if self.auth.switch_account(username):
            self.auth_status.set(f"OK: {self.auth.username}")
            self.login_mode.set("microsoft")
            self.username_var.set(self.auth.username)
            self.update_login_display()
            self.update_auth_info()
            self.save_config()
            def _bg():
                try:
                    ok = self.auth.ensure_valid_auth()
                    msg = "OK" if ok else "FAIL"
                    self.root.after(0, lambda: self.log(f"switch refresh: {msg}"))
                except Exception as e:
                    self.root.after(0, lambda: self.log(f"refresh err: {e}"))
            try:
                threading.Thread(target=_bg, daemon=True).start()
            except Exception:
                pass
            try:
                self._update_auth_avatar(self.auth.uuid)
            except Exception:
                pass
        else:
            messagebox.showerror("Error", "switch failed")

    def delete_saved_account(self):
        username = self.account_combo_var.get().strip()
        if not username:
            return
        if not messagebox.askyesno("确认", f"删除账号 {username}？"):
            return
        if self.auth.remove_account(username):
            self.refresh_account_list()
            self.update_auth_info()
            self.update_login_display()
            self.save_config()
    
    def update_auth_info(self):
        self.auth_info_text.config(state=tk.NORMAL)
        self.auth_info_text.delete(1.0, tk.END)
        if self.auth.is_authenticated():
            info = f"登录状态: 已登录\n"
            info += f"玩家名称: {self.auth.username}\n"
            info += f"UUID: {self.auth.uuid}\n"
            info += f"登录方式: Microsoft 账号\n"
            info += f"令牌状态: 有效"
        else:
            info = "登录状态: 未登录\n"
            info += "请使用 Microsoft 账号登录"
        self.auth_info_text.insert(1.0, info)
        self.auth_info_text.config(state=tk.DISABLED)
    
    def on_login_mode_change(self):
        self.save_config()
        if self.login_mode.get() == "microsoft" and not self.auth.is_authenticated():
            messagebox.showinfo("提示", "请先使用 Microsoft 账号登录")
        self.update_login_display()
    
    def run(self):
        # 全局快捷键
        try:
            self.root.bind_all("<F5>", lambda e: (None if getattr(self, 'is_launching', False) else self.launch_async()))
            self.root.bind_all("<Control-l>", lambda e: self.open_console_window())
            self.root.bind_all("<Control-L>", lambda e: self.open_console_window())
            self.root.bind_all("<Control-r>", lambda e: self.refresh_versions())
        except Exception as _e:
            try:
                self.log(f"快捷键绑定失败: {_e}")
            except Exception:
                pass
        self.root.mainloop()

# === 崩溃日志钩子 ===
def _install_crash_hooks():
    import traceback, datetime, sys as _sys
    log_dir = Path.home() / ".minecraft_launcher_crashes"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _write_crash(title, exc_type, exc_value, exc_tb, extra=""):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"crash_{ts}.log"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] {title}\n")
                f.write("=" * 60 + "\n")
                if extra:
                    f.write(extra + "\n")
                f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            print(f"[CRASH] 已写入崩溃日志: {path}")
        except Exception:
            pass

    def _excepthook(t, v, tb):
        _write_crash("主线程未捕获异常", t, v, tb)
        try:
            _sys.__excepthook__(t, v, tb)
        except Exception:
            pass

    def _thread_hook(args):
        _write_crash(
            f"线程未捕获异常: {getattr(args.thread, 'name', '?')}",
            args.exc_type, args.exc_value, args.exc_traceback,
        )

    _sys.excepthook = _excepthook
    import threading as _th
    _th.excepthook = _thread_hook


# === 系统主题检测 ===
def _detect_system_theme():
    """读注册表判断系统是浅色还是深色。返回 'light' / 'dark'。失败默认 light。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(val) == 1 else "dark"
        finally:
            winreg.CloseKey(key)
    except Exception:
        return "light"
if __name__ == "__main__":
    _install_crash_hooks()
    launcher = MinecraftLauncher()
    launcher.run()
