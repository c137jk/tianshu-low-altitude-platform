"""空管 UTM 飞行计划申报 Mock（设计07 L4；对应外部 UTM 统一流量管理系统）。

真实系统要把飞行计划报文推给空管 UTM 平台并等待批复；本项目审批动作由
平台内"空管监管员"在 M-34 审批台完成，适配器只模拟"申报报文已签收"。
"""
import random
import string
from datetime import datetime


def _apply_no() -> str:
    return "UTM" + datetime.now().strftime("%Y%m%d%H%M%S") + "".join(
        random.choices(string.digits, k=6))


class MockUtmAdapter:
    def declare(self, *, plan_no: str, route_points: list, altitude_m: int,
                aircraft_no: str, pilot_name: str) -> dict:
        """模拟空管系统签收飞行计划申报报文。"""
        return {
            "accepted": True,
            "applyNo": _apply_no(),
            "planNo": plan_no,
            "waypointCount": len(route_points),
            "altitudeM": altitude_m,
            "aircraftNo": aircraft_no,
            "pilotName": pilot_name,
            "receivedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": "申报报文已签收（Mock），等待空管监管员审批",
            "mock": True,
        }


utm_adapter = MockUtmAdapter()
