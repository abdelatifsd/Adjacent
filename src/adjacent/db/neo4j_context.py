"""Shared Neo4j driver management for connection pooling.

This module provides a centralized way to create and manage Neo4j driver instances,
ensuring driver reuse across components and proper resource cleanup.
"""

from __future__ import annotations

import logging
from typing import Optional, Any, Dict
from contextlib import contextmanager

from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)


class Neo4jContext:
    """
    Manages a shared Neo4j driver instance for connection pooling.

    Use this to create a single driver per process and share it across
    multiple components (stores, services, etc.) to avoid the overhead
    of creating new drivers for each operation.

    Example:
        # Create once per application/process
        ctx = Neo4jContext(uri="bolt://localhost:7687", user="neo4j", password="password")

        # Share driver with components
        vector_store = Neo4jVectorStore(driver=ctx.driver)
        edge_store = Neo4jEdgeStore(config, driver=ctx.driver)

        # Clean up when done
        ctx.close()

        # Or use as context manager
        with Neo4jContext(...) as ctx:
            vector_store = Neo4jVectorStore(driver=ctx.driver)
            # ...
    """

    def __init__(self, uri: str, user: str, password: str):
        """
        Initialize Neo4j context with connection parameters.

        Args:
            uri: Neo4j connection URI (e.g., "bolt://localhost:7687")
            user: Neo4j username
            password: Neo4j password
        """
        self.uri = uri
        self.user = user
        self.password = password
        self._driver: Optional[Driver] = None

    @property
    def driver(self) -> Driver:
        """
        Get or create the shared driver instance.

        Returns:
            The Neo4j driver instance
        """
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
            )
            logger.debug("Created Neo4j driver for %s", self.uri)
        return self._driver

    def close(self) -> None:
        """Close the driver connection. Idempotent."""
        if self._driver is not None:
            self._driver.close()
            logger.debug("Closed Neo4j driver for %s", self.uri)
            self._driver = None

    def __enter__(self) -> "Neo4jContext":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - closes driver."""
        self.close()

    def fetch_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Convenience method: Fetch a product from Neo4j.

        Args:
            product_id: The product ID to fetch

        Returns:
            Product data dict, or None if not found
        """
        cypher = """
        MATCH (p:Product {id: $product_id})
        RETURN p {
            .id, .title, .description, .category, .brand, .tags,
            .price, .currency, .embedding, .last_inference_at
        } AS product
        """
        with self.driver.session() as session:
            result = session.run(cypher, product_id=product_id)
            record = result.single()
            if record:
                return dict(record["product"])
        return None

    def fetch_products(self, product_ids: list[str]) -> list[Dict[str, Any]]:
        """
        Convenience method: Fetch multiple products from Neo4j.

        Args:
            product_ids: List of product IDs to fetch

        Returns:
            List of product data dicts
        """
        cypher = """
        MATCH (p:Product)
        WHERE p.id IN $product_ids
        RETURN p {
            .id, .title, .description, .category, .brand, .tags,
            .price, .currency
        } AS product
        """
        with self.driver.session() as session:
            result = session.run(cypher, product_ids=product_ids)
            return [dict(record["product"]) for record in result]


@contextmanager
def get_neo4j_driver(uri: str, user: str, password: str):
    """
    Context manager for creating a temporary Neo4j driver.

    Use this when you need a driver for a short-lived operation
    and don't want to manage the lifecycle yourself.

    Args:
        uri: Neo4j connection URI
        user: Neo4j username
        password: Neo4j password

    Yields:
        Neo4j driver instance

    Example:
        with get_neo4j_driver(uri, user, password) as driver:
            with driver.session() as session:
                result = session.run("MATCH (n) RETURN count(n)")
    """
    ctx = Neo4jContext(uri, user, password)
    try:
        yield ctx.driver
    finally:
        ctx.close()
