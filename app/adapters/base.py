"""
Abstract base classes for lending protocol adapters.

An adapter encapsulates all protocol‑specific logic needed to fetch a
user's position from a DeFi lending platform. Different protocols
(e.g. Aave, Compound, Morpho) and networks (Ethereum, Arbitrum, etc.)
will have their own concrete implementations of this interface.

The monitor service depends only on this interface and therefore
remains decoupled from the underlying data sources. This design
enables the application to support additional protocols by simply
adding new adapter classes without modifying the core logic.
"""

import abc
from typing import Protocol

from app.core.types import Position


class LendingProtocolAdapter(Protocol):  # pragma: no cover
    """Protocol defining the interface for all lending protocol adapters."""

    @abc.abstractmethod
    async def get_position(self, address: str) -> Position:
        """Fetch the aggregated position for a user address.

        Parameters
        ----------
        address: str
            The blockchain address to query.

        Returns
        -------
        Position
            The aggregated position containing supplied/borrowed assets,
            total values and risk metrics for the given address.
        """
        raise NotImplementedError