# src/adjacent/api/routes.py
"""
API route handlers for Adjacent API.

Separated from app.py to keep concerns focused.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from adjacent.async_inference.query_service import QueryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> Dict[str, str]:
    """
    Health check endpoint.

    Returns:
        Status indicator
    """
    return {"status": "ok"}


@router.get("/v1/query/{product_id}")
async def query(
    request: Request,
    product_id: str,
    top_k: int = Query(
        10, ge=1, le=100, description="Number of recommendations to return"
    ),
    skip_inference: bool = Query(False, description="Skip async inference"),
    x_trace_id: str | None = Header(None, alias="X-Trace-Id"),
) -> Dict[str, Any]:
    """
    Get recommendations for a product.

    Args:
        request: FastAPI request object (provides access to app.state)
        product_id: Anchor product ID
        top_k: Number of recommendations to return (1-100)
        skip_inference: If True, skip async inference
        x_trace_id: Optional trace ID header for request correlation

    Returns:
        QueryResult with recommendations and metadata

    Raises:
        404: Product not found
        503: Neo4j or Redis unavailable
        500: Unexpected internal error
    """
    query_service: QueryService = request.app.state.query_service

    # Use provided trace_id or generate new one
    trace_id = x_trace_id or str(uuid.uuid4())

    try:
        result = query_service.query(
            product_id=product_id,
            top_k=top_k,
            skip_inference=skip_inference,
            trace_id=trace_id,
        )

        # Convert to dict and add trace_id
        response = result.to_dict()
        response["trace_id"] = trace_id

        return response

    except ValueError as e:
        # Product not found or invalid input
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return JSONResponse(
                status_code=404,
                content={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "error_type": "product_not_found",
                },
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "error_type": "invalid_input",
                },
            )

    except ConnectionError as e:
        # Neo4j or Redis connection error
        logger.exception("Connection error during query: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service temporarily unavailable",
                "trace_id": trace_id,
                "error_type": "service_unavailable",
            },
        )

    except Exception as e:
        # Unexpected error
        logger.exception("Unexpected error during query: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "trace_id": trace_id,
                "error_type": "internal_error",
            },
        )


@router.get("/v1/perf/query/{product_id}")
async def perf_query(
    request: Request,
    product_id: str,
    top_k: int = Query(
        10, ge=1, le=100, description="Number of recommendations to return"
    ),
    skip_inference: bool = Query(False, description="Skip async inference"),
    x_trace_id: str | None = Header(None, alias="X-Trace-Id"),
) -> Dict[str, Any]:
    """
    Get recommendations with performance timings.

    Same as /v1/query but includes request_total_ms.

    Args:
        request: FastAPI request object
        product_id: Anchor product ID
        top_k: Number of recommendations to return (1-100)
        skip_inference: If True, skip async inference
        x_trace_id: Optional trace ID header for request correlation

    Returns:
        QueryResult with recommendations, metadata, and timings

    Raises:
        404: Product not found
        503: Neo4j or Redis unavailable
        500: Unexpected internal error
    """
    query_service: QueryService = request.app.state.query_service

    # Use provided trace_id or generate new one
    trace_id = x_trace_id or str(uuid.uuid4())

    # Measure total request time
    start_time = time.perf_counter()

    try:
        result = query_service.query(
            product_id=product_id,
            top_k=top_k,
            skip_inference=skip_inference,
            trace_id=trace_id,
        )

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Convert to dict and add trace_id and timing
        response = result.to_dict()
        response["trace_id"] = trace_id
        response["request_total_ms"] = round(duration_ms, 2)

        return response

    except ValueError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_msg = str(e)

        if "not found" in error_msg.lower():
            return JSONResponse(
                status_code=404,
                content={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "error_type": "product_not_found",
                    "request_total_ms": round(duration_ms, 2),
                },
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "error_type": "invalid_input",
                    "request_total_ms": round(duration_ms, 2),
                },
            )

    except ConnectionError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("Connection error during query: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service temporarily unavailable",
                "trace_id": trace_id,
                "error_type": "service_unavailable",
                "request_total_ms": round(duration_ms, 2),
            },
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("Unexpected error during query: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "trace_id": trace_id,
                "error_type": "internal_error",
                "request_total_ms": round(duration_ms, 2),
            },
        )


@router.get("/v1/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> Dict[str, Any]:
    """
    Get status of an async inference job.

    Args:
        request: FastAPI request object
        job_id: Job ID from query response

    Returns:
        Job status information

    Raises:
        503: Redis unavailable
        500: Unexpected error
    """
    query_service: QueryService = request.app.state.query_service

    try:
        status = query_service.get_job_status(job_id)
        return status

    except ConnectionError as e:
        logger.exception("Redis connection error: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Job queue temporarily unavailable",
                "error_type": "redis_unavailable",
            },
        )

    except Exception as e:
        logger.exception("Failed to get job status: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Failed to retrieve job status",
                "error_type": "internal_error",
            },
        )


@router.get("/v1/system/status")
async def system_status(request: Request) -> Dict[str, Any]:
    """
    Get system status snapshot.

    Returns health and configuration status for Neo4j, Redis, and the inference system.
    Fast, read-only endpoint that degrades gracefully.

    Args:
        request: FastAPI request object

    Returns:
        System status including Neo4j, Redis, and dynamics information

    Raises:
        503: Neo4j is unreachable (critical dependency)
    """
    query_service: QueryService = request.app.state.query_service

    # Initialize response structure
    response: Dict[str, Any] = {
        "status": "ok",
        "neo4j": {
            "connected": False,
            "product_count": 0,
            "inferred_edge_count": 0,
            "vector_index": {
                "present": False,
                "state": None,
                "name": None,
            },
        },
        "inference": {
            "redis_connected": False,
            "queue_enabled": False,
            "queue_name": query_service.config.queue_name,
            "pending_jobs": None,
        },
        "dynamics": {
            "graph_coverage_pct": None,
            "notes": [
                "Cold start is expected: vector recommendations dominate until async inference creates edges.",
                "Inferred edges and graph coverage increase over time as the worker runs.",
            ],
        },
    }

    # Check Neo4j connectivity and get stats
    try:
        driver = query_service._neo4j_ctx.driver

        with driver.session() as session:
            # Test connectivity
            session.run("RETURN 1").single()
            response["neo4j"]["connected"] = True

            # Get product count
            result = session.run("MATCH (p:Product) RETURN count(p) AS count")
            product_count = result.single()["count"]
            response["neo4j"]["product_count"] = product_count

            # Get inferred edge count
            result = session.run(
                "MATCH ()-[r:RECOMMENDATION]->() RETURN count(r) AS count"
            )
            edge_count = result.single()["count"]
            response["neo4j"]["inferred_edge_count"] = edge_count

            # Calculate graph coverage percentage
            if product_count > 0:
                result = session.run(
                    "MATCH (p:Product)-[:RECOMMENDATION]-() "
                    "RETURN count(DISTINCT p) AS products_with_edges"
                )
                products_with_edges = result.single()["products_with_edges"]
                coverage_pct = round((products_with_edges / product_count) * 100, 1)
                response["dynamics"]["graph_coverage_pct"] = coverage_pct

            # Check vector index status
            try:
                result = session.run("SHOW INDEXES")
                for record in result:
                    index_name = record.get("name", "")
                    index_type = record.get("type", "")

                    # Look for vector index (either by name or type)
                    if (
                        "vector" in index_type.lower()
                        or index_name == "product_embedding"
                    ):
                        response["neo4j"]["vector_index"] = {
                            "present": True,
                            "state": record.get("state", "ONLINE"),
                            "name": index_name,
                        }
                        break
            except Exception as e:
                logger.warning("Failed to check vector index status: %s", e)
                # Leave vector_index as default values

    except Exception as e:
        logger.exception("Neo4j connection failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Neo4j unavailable",
                "error_type": "neo4j_unavailable",
            },
        )

    # Check Redis/RQ status (degrade gracefully)
    try:
        # Test Redis connectivity
        query_service._redis.ping()
        response["inference"]["redis_connected"] = True
        response["inference"]["queue_enabled"] = True

        # Get queue length
        try:
            queue_length = len(query_service._queue)
            response["inference"]["pending_jobs"] = queue_length
        except Exception as e:
            logger.warning("Failed to get queue length: %s", e)

    except Exception as e:
        logger.warning("Redis connection failed: %s", e)
        # Leave redis_connected as False, queue_enabled as False

    return response
