"""地面站 GCS（Ground Control Station）指令下发 Mock（设计07 L4）。

真实场景：调度系统经地面站把任务指令推到机长 App / 飞控，回收 ACK 与遥测。
W3 只模拟指令送达；W4 仿真器开始产生轨迹帧后，这里会是真机/仿真的统一入口。
"""
import random
import string
from datetime import datetime


def _cmd_no() -> str:
    return "GCS" + datetime.now().strftime("%Y%m%d%H%M%S") + "".join(
        random.choices(string.digits, k=6))


class MockGcsAdapter:
    def send_command(self, *, plan_no: str, command: str, payload: dict | None = None) -> dict:
        """模拟向地面站/机长终端下发指令并立即收到送达 ACK。"""
        return {
            "delivered": True,
            "commandNo": _cmd_no(),
            "planNo": plan_no,
            "command": command,
            "payload": payload or {},
            "ackAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mock": True,
        }


gcs_adapter = MockGcsAdapter()
