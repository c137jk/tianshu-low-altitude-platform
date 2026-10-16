"""支付渠道 Mock：不真扣款，直接回调成功（设计07 L4）。

真实对接时替换为微信/支付宝预下单 + 异步回调验签，service 层调用方式不变。
"""
import random
import string
from datetime import datetime

METHOD_NAME = {1: "微信支付", 2: "支付宝", 3: "对公转账"}


def _channel_no(prefix: str) -> str:
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + "".join(
        random.choices(string.digits, k=6)
    )


class MockPaymentAdapter:
    """统一下单 / 退款的假实现。"""

    def pay(self, *, txn_no: str, amount: float, pay_method: int, order_no: str) -> dict:
        """模拟渠道立即支付成功。"""
        return {
            "success": True,
            "channel_txn_no": _channel_no("CH"),
            "paid_at": datetime.now(),
            "raw": {
                "channel": METHOD_NAME.get(pay_method, "未知"),
                "out_trade_no": txn_no,
                "order_no": order_no,
                "amount": float(amount),
                "result_code": "SUCCESS",
                "mock": True,
            },
        }

    def refund(self, *, txn_no: str, amount: float, pay_method: int, order_no: str) -> dict:
        """模拟原路退款成功。"""
        return {
            "success": True,
            "channel_txn_no": _channel_no("RF"),
            "paid_at": datetime.now(),
            "raw": {
                "channel": METHOD_NAME.get(pay_method, "未知"),
                "out_refund_no": txn_no,
                "order_no": order_no,
                "refund_amount": float(amount),
                "result_code": "REFUND_SUCCESS",
                "mock": True,
            },
        }


payment_adapter = MockPaymentAdapter()
