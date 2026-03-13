"""
Aave V3 adapter for Arbitrum via on-chain RPC.

This adapter fetches:
- aggregated account data (HF, LTV, collateral, debt)
- per-asset supplied balances
- per-asset borrowed balances

and returns them in the application's Position format.
"""

from __future__ import annotations

import logging
from typing import Optional, List, Tuple

from web3 import Web3

from app.adapters.base import LendingProtocolAdapter
from app.core.config import AppSettings
from app.core.types import Position, AssetPosition

logger = logging.getLogger(__name__)


class AaveV3ArbitrumAdapter(LendingProtocolAdapter):
    """Adapter for Aave V3 on Arbitrum using direct RPC calls."""

    POOL_ADDRESS = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    POOL_ADDRESSES_PROVIDER_ADDRESS = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"

    _POOL_ABI = [
        {
            "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
            "name": "getUserAccountData",
            "outputs": [
                {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
                {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
                {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
                {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
            ],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "getReservesList",
            "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
            "name": "getReserveData",
            "outputs": [
                {
                    "components": [
                        {"internalType": "uint256", "name": "configuration", "type": "uint256"},
                        {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
                        {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentStableBorrowRate", "type": "uint128"},
                        {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"},
                        {"internalType": "uint16", "name": "id", "type": "uint16"},
                        {"internalType": "address", "name": "aTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "interestRateStrategyAddress", "type": "address"},
                        {"internalType": "uint128", "name": "accruedToTreasury", "type": "uint128"},
                        {"internalType": "uint128", "name": "unbacked", "type": "uint128"},
                        {"internalType": "uint128", "name": "isolationModeTotalDebt", "type": "uint128"},
                    ],
                    "internalType": "struct DataTypes.ReserveData",
                    "name": "",
                    "type": "tuple",
                }
            ],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    _ADDRESSES_PROVIDER_ABI = [
        {
            "inputs": [],
            "name": "getPriceOracle",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]

    _AAVE_ORACLE_ABI = [
        {
            "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
            "name": "getAssetPrice",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]

    _ERC20_ABI = [
        {
            "inputs": [],
            "name": "symbol",
            "outputs": [{"internalType": "string", "name": "", "type": "string"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "decimals",
            "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.web3: Optional[Web3] = None
        self.pool_contract = None
        self.oracle_contract = None

        if not settings.web3_provider_uri:
            logger.warning("web3_provider_uri is not configured")
            return

        try:
            self.web3 = Web3(Web3.HTTPProvider(settings.web3_provider_uri))
            if not self.web3.is_connected():
                logger.warning("Web3 provider is not connected: %s", settings.web3_provider_uri)
                self.web3 = None
                return

            self.pool_contract = self.web3.eth.contract(
                address=Web3.to_checksum_address(self.POOL_ADDRESS),
                abi=self._POOL_ABI,
            )

            addresses_provider = self.web3.eth.contract(
                address=Web3.to_checksum_address(self.POOL_ADDRESSES_PROVIDER_ADDRESS),
                abi=self._ADDRESSES_PROVIDER_ABI,
            )
            oracle_address = addresses_provider.functions.getPriceOracle().call()

            self.oracle_contract = self.web3.eth.contract(
                address=Web3.to_checksum_address(oracle_address),
                abi=self._AAVE_ORACLE_ABI,
            )

            logger.info("Aave RPC adapter initialised successfully")
        except Exception as exc:
            logger.exception("Failed to initialise Aave RPC adapter: %s", exc)
            self.web3 = None
            self.pool_contract = None
            self.oracle_contract = None

    def _erc20_contract(self, token_address: str):
        return self.web3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=self._ERC20_ABI,
        )

    def _safe_symbol(self, token_address: str) -> str:
        try:
            return self._erc20_contract(token_address).functions.symbol().call()
        except Exception:
            return token_address[:6]

    def _safe_decimals(self, token_address: str) -> int:
        try:
            return int(self._erc20_contract(token_address).functions.decimals().call())
        except Exception:
            return 18

    def _safe_balance(self, token_address: str, user_address: str) -> int:
        try:
            return int(self._erc20_contract(token_address).functions.balanceOf(user_address).call())
        except Exception:
            return 0

    def _asset_price_usd(self, asset_address: str) -> float:
        try:
            raw_price = int(
                self.oracle_contract.functions.getAssetPrice(
                    Web3.to_checksum_address(asset_address)
                ).call()
            )
            return raw_price / 1e8
        except Exception:
            return 0.0

    def _fetch_asset_breakdown(
        self, user_address: str
    ) -> Tuple[List[AssetPosition], List[AssetPosition]]:
        supplied_assets: List[AssetPosition] = []
        borrowed_assets: List[AssetPosition] = []

        reserves = self.pool_contract.functions.getReservesList().call()

        for reserve in reserves:
            try:
                reserve_data = self.pool_contract.functions.getReserveData(
                    Web3.to_checksum_address(reserve)
                ).call()

                a_token_address = reserve_data[8]
                stable_debt_token_address = reserve_data[9]
                variable_debt_token_address = reserve_data[10]

                decimals = self._safe_decimals(reserve)
                symbol = self._safe_symbol(reserve)
                price_usd = self._asset_price_usd(reserve)

                supplied_raw = self._safe_balance(a_token_address, user_address)
                stable_debt_raw = self._safe_balance(stable_debt_token_address, user_address)
                variable_debt_raw = self._safe_balance(variable_debt_token_address, user_address)
                total_debt_raw = stable_debt_raw + variable_debt_raw

                if supplied_raw > 0:
                    supplied_amount = supplied_raw / (10 ** decimals)
                    supplied_usd = supplied_amount * price_usd
                    supplied_assets.append(
                        AssetPosition(
                            token_address=reserve,
                            token_symbol=symbol,
                            amount=supplied_amount,
                            usd_value=supplied_usd,
                            position_type="supply",
                        )
                    )

                if total_debt_raw > 0:
                    debt_amount = total_debt_raw / (10 ** decimals)
                    debt_usd = debt_amount * price_usd
                    borrowed_assets.append(
                        AssetPosition(
                            token_address=reserve,
                            token_symbol=symbol,
                            amount=debt_amount,
                            usd_value=debt_usd,
                            position_type="borrow",
                        )
                    )
            except Exception as exc:
                logger.debug("Skipping reserve %s due to error: %s", reserve, exc)

        supplied_assets.sort(key=lambda x: x.usd_value, reverse=True)
        borrowed_assets.sort(key=lambda x: x.usd_value, reverse=True)
        return supplied_assets, borrowed_assets

    async def get_position(self, address: str) -> Position:
        """Return aggregated Aave account data for a wallet."""
        if not self.web3 or not self.pool_contract or not self.oracle_contract:
            raise RuntimeError("Aave RPC adapter is not initialized")

        checksum_address = Web3.to_checksum_address(address)

        account_data = self.pool_contract.functions.getUserAccountData(checksum_address).call()

        total_collateral_base = int(account_data[0])
        total_debt_base = int(account_data[1])
        current_ltv_bps = int(account_data[4])
        health_factor_raw = int(account_data[5])

        collateral_usd = total_collateral_base / 1e8
        debt_usd = total_debt_base / 1e8
        health_factor = None if health_factor_raw == 0 else health_factor_raw / 1e18
        ltv = current_ltv_bps / 100.0 if current_ltv_bps else None

        collateral_ratio = None
        if debt_usd > 0:
            collateral_ratio = collateral_usd / debt_usd

        supplied_assets, borrowed_assets = self._fetch_asset_breakdown(checksum_address)
        # Risk status
        if health_factor is None:
            risk_status = "UNKNOWN"
        elif health_factor > 1.5:
            risk_status = "SAFE"
        elif health_factor > 1.3:
            risk_status = "OK"
        elif health_factor > 1.2:
            risk_status = "WARNING"
        elif health_factor >= 1.0:
            risk_status = "DANGER"
        else:
            risk_status = "LIQUIDATION"

        # Approximate liquidation distance based on HF
        liquidation_distance_pct = None
        if health_factor is not None and health_factor > 1:
            liquidation_distance_pct = (1 - (1 / health_factor)) * 100

        # Estimated liquidation price for main collateral asset (best effort)
        estimated_liquidation_price = None
        if supplied_assets and debt_usd > 0:
            main_collateral = supplied_assets[0]
            main_amount = getattr(main_collateral, "amount", 0.0) or 0.0
            main_symbol = (getattr(main_collateral, "token_symbol", "") or "").upper()

            if main_amount > 0 and main_symbol in {"WETH", "ETH"}:
                try:
                    liquidation_collateral_usd = debt_usd / (ltv / 100.0) if ltv else None
                    if liquidation_collateral_usd and liquidation_collateral_usd > 0:
                        estimated_liquidation_price = liquidation_collateral_usd / main_amount
                except Exception:
                    estimated_liquidation_price = None

        return Position(
            wallet_address=address,
            protocol="Aave V3",
            network="Arbitrum",
            supplied=supplied_assets,
            borrowed=borrowed_assets,
            collateral_value_usd=collateral_usd,
            debt_value_usd=debt_usd,
            health_factor=health_factor,
            ltv=ltv,
            collateral_ratio=collateral_ratio,
            liquidation_distance_pct=liquidation_distance_pct,
            estimated_liquidation_price=estimated_liquidation_price,
            risk_status=risk_status,
                )

    async def close(self) -> None:
        """Nothing to close for synchronous Web3 HTTP provider."""
        return
