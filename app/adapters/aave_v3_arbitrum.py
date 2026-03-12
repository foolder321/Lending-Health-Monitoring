"""
Adapter for Aave V3 on Arbitrum.

This adapter combines on‑chain data via Web3 and off‑chain data via the
Expand.network API to produce a comprehensive view of a user's lending
position. On‑chain calls are used to obtain the user's health factor,
while the off‑chain API provides per‑asset balances and USD prices. The
adapter computes aggregated collateral and debt values, derives risk
metrics (LTV and collateral ratio) and returns a structured
``Position`` object consumable by the rest of the application.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Dict, Any, Optional

import httpx
from web3 import Web3
from web3.exceptions import ContractLogicError

from app.core.types import Position, AssetPosition
from app.core.config import AppSettings
from app.adapters.base import LendingProtocolAdapter


logger = logging.getLogger(__name__)


class AaveV3ArbitrumAdapter(LendingProtocolAdapter):
    """Concrete adapter implementation for Aave V3 on Arbitrum.

    Parameters
    ----------
    settings: AppSettings
        Application configuration object providing API keys, network
        identifiers and RPC endpoints.
    """

    # Addresses for the Aave V3 Arbitrum deployment
    POOL_ADDRESS: str = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    UI_POOL_DATA_PROVIDER_ADDRESS: str = "0x13c833256BD767da2320d727a3691BAff3770E39"
    POOL_ADDRESSES_PROVIDER_ADDRESS: str = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"

    # Minimal ABI fragments for required contract methods
    _POOL_ABI: List[Dict[str, Any]] = [
        {
            "inputs": [
                {
                    "internalType": "address",
                    "name": "user",
                    "type": "address",
                }
            ],
            "name": "getUserAccountData",
            "outputs": [
                {
                    "internalType": "uint256",
                    "name": "totalCollateralBase",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "totalDebtBase",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "availableBorrowsBase",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "currentLiquidationThreshold",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "ltv",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "healthFactor",
                    "type": "uint256",
                },
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._http_client: Optional[httpx.AsyncClient] = None

        # Initialise Web3 if an RPC endpoint is provided
        self.web3: Optional[Web3] = None
        self.pool_contract = None
        if settings.web3_provider_uri:
            try:
                self.web3 = Web3(Web3.HTTPProvider(settings.web3_provider_uri))
                if not self.web3.is_connected():
                    logger.warning(
                        "Web3 provider not connected; on‑chain calls will be skipped"
                    )
                    self.web3 = None
                else:
                    self.pool_contract = self.web3.eth.contract(
                        address=Web3.to_checksum_address(self.POOL_ADDRESS), abi=self._POOL_ABI
                    )
            except Exception as exc:
                logger.exception(
                    "Failed to initialise Web3 provider: %s", settings.web3_provider_uri
                )
                self.web3 = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Lazily initialise an HTTP client for API calls."""
        if self._http_client is None:
            headers = {
                "X-API-KEY": self.settings.expand_network_api_key,
            }
            # A small timeout to avoid hanging indefinitely on slow responses
            timeout = httpx.Timeout(10.0, connect=5.0)
            self._http_client = httpx.AsyncClient(base_url=self.settings.expand_network_base_url, headers=headers, timeout=timeout)
        return self._http_client

    async def _fetch_positions_from_api(self, address: str) -> List[Dict[str, Any]]:
        """Fetch per‑asset position data from Expand.network with basic retry logic.

        This method will attempt to fetch data up to three times with exponential
        backoff (1s, 2s, 4s) on network‑related errors. If the final attempt
        fails or returns an unexpected payload, an empty list is returned.

        Parameters
        ----------
        address: str
            The user address to query.

        Returns
        -------
        List[dict]
            A list of position objects for each asset. If the API returns
            an error or unexpected payload, an empty list is returned.
        """
        client = await self._get_http_client()
        params = {
            "address": address,
            "lendborrowId": str(self.settings.lendborrow_id),
        }
        backoff_delays = [1, 2, 4]
        last_exception: Optional[Exception] = None
        for attempt, delay in enumerate(backoff_delays, start=1):
            try:
                response = await client.get(
                    "/lendborrow/getuserpositions", params=params
                )
                response.raise_for_status()
                payload = response.json()
                # API returns status=200 on success
                if payload.get("status") != 200:
                    logger.warning(
                        "Unexpected status in Expand.network response for %s: %s",
                        address,
                        payload,
                    )
                    return []
                data = payload.get("data", [])
                if not isinstance(data, list):
                    logger.warning(
                        "Unexpected data format in Expand.network response: %s",
                        payload,
                    )
                    return []
                return data
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                # network or HTTP error – retry after delay
                last_exception = exc
                logger.warning(
                    "Attempt %d: error fetching positions for %s: %s; retrying in %ds",
                    attempt,
                    address,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                # Unexpected error – abort retries
                logger.exception(
                    "Unexpected error fetching positions from Expand.network: %s", exc
                )
                return []
        # All retries failed
        if last_exception is not None:
            logger.error(
                "Failed to fetch positions for %s after %d attempts: %s",
                address,
                len(backoff_delays),
                last_exception,
            )
        return []

    def _get_health_factor_onchain(self, address: str) -> Optional[float]:
        """Retrieve the user's health factor directly from the Aave Pool contract.

        The health factor is returned with 18 decimals. If the RPC
        endpoint is not configured or the call fails, ``None`` is
        returned and a warning is logged.
        """
        if not self.web3 or not self.pool_contract:
            logger.debug("Web3 not initialised; skipping on‑chain health factor fetch")
            return None
        try:
            data = self.pool_contract.functions.getUserAccountData(address).call()
            # data is a tuple of six values; we only need healthFactor
            health_factor_int = data[5]
            # Convert from ray (1e18)
            if health_factor_int == 0:
                return None
            health = float(health_factor_int) / 1e18
            return health
        except ContractLogicError as e:
            logger.warning(
                "Contract reverted while fetching health factor for %s: %s", address, e
            )
            return None
        except Exception as exc:
            logger.exception(
                "Unexpected error while fetching health factor for %s: %s", address, exc
            )
            return None

    async def get_position(self, address: str) -> Position:
        """Fetch aggregated position data for a single address.

        This method combines off‑chain and on‑chain sources to build a
        complete view of the user's position including per‑asset
        breakdowns, aggregated USD values and risk metrics.

        Parameters
        ----------
        address: str
            The wallet address to query.

        Returns
        -------
        Position
            Structured position data including supplied and borrowed assets,
            aggregated values and risk metrics.
        """

        # Fetch both API positions and on‑chain health factor concurrently.
        # The on‑chain call is synchronous and can block the event loop, so run
        # it in a thread executor to avoid blocking other tasks. If no RPC
        # provider is configured, the call returns ``None`` immediately.
        loop = asyncio.get_event_loop()
        positions_task = asyncio.create_task(self._fetch_positions_from_api(address))
        health_task = asyncio.create_task(
            loop.run_in_executor(None, self._get_health_factor_onchain, address)
        )
        raw_positions = await positions_task
        # health factor may be None if RPC is misconfigured or the call failed
        try:
            health_factor = await health_task
        except Exception:
            health_factor = None

        supplied_assets: List[AssetPosition] = []
        borrowed_assets: List[AssetPosition] = []
        total_collateral_usd = 0.0
        total_debt_usd = 0.0

        for asset in raw_positions:
            # Extract price in USD; structure is {"usd": [ { token_address: price } ]}
            prices: Dict[str, Any] = asset.get("prices", {}).get("usd", [])
            price: Optional[float] = None
            if prices and isinstance(prices, list) and isinstance(prices[0], dict):
                first_entry = prices[0]
                if first_entry:
                    price_str = next(iter(first_entry.values()))
                    try:
                        price = float(price_str)
                    except (TypeError, ValueError):
                        price = None
            # Determine token decimals: if provided by API use it; otherwise default to 18
            try:
                decimals = int(asset.get("decimals", 18))
            except (TypeError, ValueError):
                decimals = 18
            # Parse current balance (supplied collateral)
            current_balance = asset.get("currentBalance")
            if current_balance and current_balance not in (None, "0"):
                try:
                    balance_int = int(current_balance)
                    amount = balance_int / (10 ** decimals)
                except (TypeError, ValueError):
                    amount = 0.0
                usd_value = amount * price if price is not None else 0.0
                supplied_assets.append(
                    AssetPosition(
                        token_address=asset.get("underlyingAsset"),
                        token_symbol=asset.get("symbol"),
                        amount=amount,
                        usd_value=usd_value,
                        position_type="supply",
                    )
                )
                total_collateral_usd += usd_value
            # Parse current debt (borrowed assets)
            current_debt = asset.get("currentDebt")
            if current_debt and current_debt not in (None, "0"):
                try:
                    debt_int = int(current_debt)
                    amount = debt_int / (10 ** decimals)
                except (TypeError, ValueError):
                    amount = 0.0
                usd_value = amount * price if price is not None else 0.0
                borrowed_assets.append(
                    AssetPosition(
                        token_address=asset.get("underlyingAsset"),
                        token_symbol=asset.get("symbol"),
                        amount=amount,
                        usd_value=usd_value,
                        position_type="borrow",
                    )
                )
                total_debt_usd += usd_value

        # Compute aggregated ratios
        ltv: Optional[float] = None
        collateral_ratio: Optional[float] = None
        if total_collateral_usd > 0:
            ltv = total_debt_usd / total_collateral_usd
        if total_debt_usd > 0:
            collateral_ratio = total_collateral_usd / total_debt_usd

        # Construct Position dataclass
        position = Position(
            wallet_address=address,
            protocol="Aave V3",
            network="Arbitrum",
            supplied=supplied_assets,
            borrowed=borrowed_assets,
            collateral_value_usd=total_collateral_usd,
            debt_value_usd=total_debt_usd,
            health_factor=health_factor,
            ltv=ltv,
            collateral_ratio=collateral_ratio,
        )
        return position

    async def close(self) -> None:
        """Close any underlying HTTP resources held by the adapter."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None