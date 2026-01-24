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
    top_k: int = Query(10, ge=1, le=100, description="Number of recommendations to return"),
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
    top_k: int = Query(10, ge=1, le=100, description="Number of recommendations to return"),
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
