"""Unit tests for Lighter submission value guards."""
import unittest

from app.core.lighter_client import LighterClient


class FakeResponse:
    code = 200
    message = "accepted"
    tx_hash = "test-tx"
    predicted_execution_time_ms = 300
    volume_quota_remaining = 1


class FakeSigner:
    ORDER_TYPE_MARKET = 1
    ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL = 0
    DEFAULT_IOC_EXPIRY = -1

    def __init__(self):
        self.order = None

    async def create_order(self, **kwargs):
        self.order = kwargs
        return object(), FakeResponse(), None


class LighterOrderGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_client_uses_the_exact_displayed_limit_and_floor_quantity(self):
        client = LighterClient()
        signer = FakeSigner()
        client._get_signer = lambda: signer

        success, tx_hash, error = await client.open_snipe_order(
            side="LONG",
            size_btc=0.100019,
            limit_price=100.0,
            trade_id=7,
        )

        self.assertTrue(success)
        self.assertEqual("test-tx", tx_hash)
        self.assertIsNone(error)
        self.assertEqual(10_001, signer.order["base_amount"])
        self.assertEqual(1_000, signer.order["price"])
        self.assertFalse(signer.order["is_ask"])

    async def test_live_client_rejects_an_order_at_or_below_ten_usdc(self):
        client = LighterClient()
        signer = FakeSigner()
        client._get_signer = lambda: signer

        success, tx_hash, error = await client.open_snipe_order(
            side="LONG",
            size_btc=0.1,
            limit_price=100.0,
            trade_id=8,
        )

        self.assertFalse(success)
        self.assertIsNone(tx_hash)
        self.assertIn("strictly greater", error)
        self.assertIsNone(signer.order)


if __name__ == "__main__":
    unittest.main()
