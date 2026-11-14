# plugins/system_info.py
from tools.base import BaseTool
from typing import Any, Dict
import platform
import psutil
import shutil


class SystemInfoTool(BaseTool):
    @property
    def name(self) -> str:
        return "system_info"

    @property
    def description(self) -> str:
        return "Get system information: OS, CPU, RAM, Disk usage, Battery."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            uname = platform.uname()
            cpu_percent = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = shutil.disk_usage("/")
            battery = psutil.sensors_battery()

            info = [
                f"System: {uname.system} {uname.release}",
                f"Node: {uname.node}",
                f"Machine: {uname.machine}",
                f"Processor: {uname.processor}",
                f"CPU Usage: {cpu_percent}%",
                f"RAM: {mem.used // (1024**3)} GB / {mem.total // (1024**3)} GB ({mem.percent}%)",
                f"Disk: {disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB",
            ]
            if battery:
                info.append(f"Battery: {battery.percent}% ({'Charging' if battery.power_plugged else 'Not Charging'})")

            return {"content": "\n".join(info), "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}