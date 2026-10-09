import uuid
from flask import request, jsonify, g
from botocore.exceptions import BotoCoreError, ClientError
import logging
import traceback
import re

logger = logging.getLogger(__name__)

class APIError(Exception):
    def __init__(self, message: str, status_code: int = 500, error_code: str = "INTERNAL_ERROR", details=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details

def map_boto_error(e: Exception) -> tuple[int, str, str]:
    """Maps boto3 exceptions to (status_code, error_code, safe_message)"""
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException"):
            return 403, "ACCESS_DENIED", "AWS access denied. Please check your permissions."
        if code in ("Throttling", "ThrottlingException", "TooManyRequestsException"):
            return 429, "THROTTLED", "AWS API rate limit exceeded. Please try again later."
        # The *server's* AWS credentials are bad: that's a 503 (service unavailable), not a 401,
        # which would tell browsers/ALBs the end user must re-authenticate.
        if code in ("UnrecognizedClientException", "InvalidClientTokenId", "InvalidAccessKeyId", "AuthFailure",
                    "ExpiredToken", "ExpiredTokenException"):
            return 503, "INVALID_CREDENTIALS", "AWS credentials are invalid or missing."
        return 502, "AWS_SERVICE_ERROR", "AWS service returned an error."
        
    if isinstance(e, BotoCoreError):
        name = e.__class__.__name__
        if "Credential" in name:
            return 503, "INVALID_CREDENTIALS", "AWS credentials are invalid or missing."
        return 502, "AWS_SDK_ERROR", "An internal AWS SDK error occurred."
        
    return 500, "INTERNAL_ERROR", "An unexpected internal error occurred."

def sanitize_exception_traceback(e: Exception) -> str:
    raw_tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    
    # 1. Redact the exact exception messages from the chain
    curr_e = e
    while curr_e:
        exc_msg = str(curr_e)
        if exc_msg and len(exc_msg) > 3:
            raw_tb = raw_tb.replace(exc_msg, "<redacted for security>")
        curr_e = getattr(curr_e, '__cause__', None) or getattr(curr_e, '__context__', None)
        
    # 2. Aggressively redact any values following sensitive keywords in remaining traceback
    pattern = re.compile(r'(secret|password|token|key|credential|auth)([\s:=]+(?:is\s+)?)([^\s\'",]+)', re.IGNORECASE)
    safe_tb = pattern.sub(r'\1\2<REDACTED>', raw_tb)
    
    return safe_tb

class FlaskRequestIDFilter(logging.Filter):
    def filter(self, record):
        from flask import has_request_context, g
        if has_request_context() and hasattr(g, 'request_id'):
            record.correlation_id = g.request_id
        return True

def register_error_handlers(app):
    # Make request ID available to all application logging automatically
    for handler in logging.getLogger().handlers:
        handler.addFilter(FlaskRequestIDFilter())
        
    @app.before_request
    def ensure_request_id():
        # Preserve incoming X-Request-ID if present and safe (e.g. valid UUID or safe string)
        req_id = request.headers.get("X-Request-ID")
        if not req_id or len(req_id) > 50: # basic safety check
            req_id = str(uuid.uuid4())
        g.request_id = req_id

    @app.after_request
    def add_request_id_header(response):
        if hasattr(g, 'request_id'):
            response.headers["X-Request-ID"] = g.request_id
        return response

    @app.errorhandler(APIError)
    def handle_api_error(e):
        body = {
            "error_code": e.error_code,
            "message": e.message,
            "request_id": getattr(g, 'request_id', 'unknown')
        }
        if e.details is not None:
            body["details"] = e.details
        return jsonify(body), e.status_code

    @app.errorhandler(ClientError)
    @app.errorhandler(BotoCoreError)
    def handle_boto_error(e):
        status, code, msg = map_boto_error(e)
        logger.error(f"AWS Error: {str(e)}", extra={"correlation_id": getattr(g, 'request_id', None)})
        return jsonify({
            "error_code": code,
            "message": msg,
            "request_id": getattr(g, 'request_id', 'unknown')
        }), status

    @app.errorhandler(Exception)
    def handle_unexpected_error(e):
        # Allow standard Flask HTTP errors (like 404, 405) to pass through naturally or be mapped if needed
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify({
                "error_code": "HTTP_ERROR",
                "message": e.description,
                "request_id": getattr(g, 'request_id', 'unknown')
            }), e.code
            
        req_id = getattr(g, 'request_id', 'unknown')
        
        # Log unexpected error safely (no raw exceptions or secrets in logs)
        safe_tb = sanitize_exception_traceback(e)
        logger.error(
            f"Unexpected API Error of type {type(e).__name__}\n{safe_tb}", 
            extra={
                "correlation_id": req_id,
                "endpoint": request.endpoint,
                "method": request.method
            }
        )
        return jsonify({
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "request_id": req_id
        }), 500
