"""RecallGuard Python integration surface: guard.verify and @guarded decorator."""

import functools
import os
from typing import Callable, Any, Optional
import httpx

class ActionBlockedError(Exception):
    def __init__(self, message: str, verify_response: dict):
        super().__init__(message)
        self.verify_response = verify_response

class SimulatedExecutor:
    """Executes actions only after ALLOW decision, labeled SIMULATED."""
    @staticmethod
    def execute(action_type: str, subject: str, predicate: str, value: str) -> dict:
        return {
            "status": "SIMULATED_SUCCESS",
            "message": f"[SIMULATED] Successfully performed '{action_type}' with {subject}.{predicate} = '{value}'",
            "executed": True
        }

def verify_action(
    action_type: str,
    subject: str,
    predicate: str,
    requested_value: str,
    session_id: str = "default_session",
    api_url: Optional[str] = None
) -> dict:
    base_url = api_url or os.getenv("RECALLGUARD_API_URL", "http://localhost:8000")
    payload = {
        "action_type": action_type,
        "subject": subject,
        "predicate": predicate,
        "requested_value": requested_value,
        "session_id": session_id
    }
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{base_url}/verify", json=payload)
        resp.raise_for_status()
        return resp.json()

def guarded(action_type: str, subject: str, predicate: str, value_arg: str):
    """
    Decorator that verifies memory before executing the wrapped function.
    Raises ActionBlockedError if verification decision is not ALLOW.
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Extract requested value from args or kwargs
            requested_value = kwargs.get(value_arg)
            if requested_value is None:
                # Attempt to find by parameter name in function signature
                import inspect
                sig = inspect.signature(fn)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                requested_value = bound.arguments.get(value_arg)

            verify_res = verify_action(
                action_type=action_type,
                subject=subject,
                predicate=predicate,
                requested_value=str(requested_value)
            )

            if verify_res.get("decision") != "ALLOW":
                raise ActionBlockedError(
                    f"Action '{action_type}' BLOCKED by RecallGuard ({verify_res.get('reason_code')}): {verify_res.get('remediation')}",
                    verify_response=verify_res
                )

            return fn(*args, **kwargs)
        return wrapper
    return decorator
