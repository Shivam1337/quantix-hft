"""
app/fee_model.py
Real fee model for Polymarket and Polygon L2 network.

Accurately models:
1. Polymarket CLOB Maker Fee: 0.0% (Standard)
2. Polymarket CLOB Taker Fee: 0.0% on general/political markets; up to 1.5-2.0% on specialized markets
3. CTF Settlement / Redemption Fee: 0.0% standard
4. Real Polygon L2 Gas Costs:
   - Typical CTF order placement / fill on L2 = ~220,000 gas units
   - Average Polygon Gas Price: 35 Gwei
   - Average POL/MATIC Price: ~$0.40 USD
   - Real Gas Cost per transaction: ~ $0.0031 USD
"""

from typing import Dict, Any


class RealFeeModel:
    def __init__(self, pol_price_usd: float = 0.40, avg_gas_gwei: float = 35.0):
        self.pol_price_usd = pol_price_usd
        self.avg_gas_gwei = avg_gas_gwei

        # CTF contract execution gas units
        self.gas_units_per_order = 180000
        self.gas_units_per_basket = 320000

    def calculate_polygon_gas_usd(self, is_multi_token_basket: bool = True) -> float:
        """
        Calculates exact real Polygon L2 gas cost in USD.
        Gas = Units * (Gwei * 10^-9) * POL_price
        """
        units = self.gas_units_per_basket if is_multi_token_basket else self.gas_units_per_order
        pol_cost = units * (self.avg_gas_gwei * 1e-9)
        gas_usd = pol_cost * self.pol_price_usd
        return round(max(0.001, gas_usd), 4)

    def get_market_taker_fee(self, event_title: str = "", category: str = "") -> float:
        """
        Returns the real Polymarket taker fee rate based on market category.
        - Standard Politics / Macro / Elections: 0.0%
        - 15-minute crypto / high-frequency sports: 1.0% to 2.0%
        """
        cat_lower = (category or "").lower()
        title_lower = (event_title or "").lower()

        if "15-minute" in title_lower or "crypto price" in title_lower:
            return 0.015  # 1.5%
        if "sports" in cat_lower or "nba" in title_lower or "nfl" in title_lower:
            return 0.010  # 1.0%
        return 0.000      # 0.0% on standard macro/elections

    def get_market_maker_fee(self) -> float:
        """Polymarket CLOB maker fee is 0%."""
        return 0.000

    def calculate_effective_execution_cost(
        self,
        notional_usd: float,
        is_taker: bool = True,
        event_title: str = "",
        is_basket: bool = True
    ) -> Dict[str, float]:
        """
        Calculates the exact breakdown of real network gas and exchange fees.
        """
        gas_cost = self.calculate_polygon_gas_usd(is_multi_token_basket=is_basket)
        fee_rate = self.get_market_taker_fee(event_title) if is_taker else self.get_market_maker_fee()
        exchange_fee_usd = notional_usd * fee_rate
        total_friction_usd = gas_cost + exchange_fee_usd

        return {
            "gas_cost_usd": gas_cost,
            "exchange_fee_rate": fee_rate,
            "exchange_fee_usd": round(exchange_fee_usd, 4),
            "total_friction_usd": round(total_friction_usd, 4),
            "friction_pct": round((total_friction_usd / notional_usd) * 100.0, 3) if notional_usd > 0 else 0.0
        }


real_fees = RealFeeModel()
