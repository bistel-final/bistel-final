from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.agent.delivery_callback import (
    DeliveryCallbackService,
    DeliveryResult,
    callback_request_openapi_schema,
    get_delivery_callback_service,
    normalize_action_id,
    parse_callback_body,
    validate_json_content_type,
)
from app.common.exceptions import IdempotencyConflictError

router = APIRouter(tags=["Agent"])


@router.post(
    "/internal/actions/{action_id}/delivery",
    response_model=DeliveryResult,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": callback_request_openapi_schema(),
                }
            },
        }
    },
)
async def write_back_delivery(
    action_id: str,
    request: Request,
    service: Annotated[
        DeliveryCallbackService,
        Depends(get_delivery_callback_service),
    ],
    timestamp: Annotated[
        str | None,
        Header(alias="X-Delivery-Timestamp"),
    ] = None,
    signature: Annotated[
        str | None,
        Header(alias="X-Delivery-Signature"),
    ] = None,
) -> DeliveryResult:
    verified = service.verify_headers(timestamp, signature)
    raw = await request.body()
    service.verify_signature(verified, raw)
    normalized_action_id = normalize_action_id(action_id)
    validate_json_content_type(request.headers.get("content-type"))
    callback = parse_callback_body(raw)
    try:
        result = service.settle(
            action_id=normalized_action_id,
            callback=callback,
        )
    except IdempotencyConflictError:
        service.record_http_result(
            action_id=normalized_action_id,
            channel=callback.channel,
            result=None,
            http_status=409,
        )
        raise
    service.record_http_result(
        action_id=normalized_action_id,
        channel=callback.channel,
        result=result,
        http_status=200,
    )
    return result
