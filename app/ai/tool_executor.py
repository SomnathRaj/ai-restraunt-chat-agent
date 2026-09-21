"""Allow-listed dispatch from a Gemini tool call to the real service-layer function.

This is the enforcement point for "every tool call flows through Flask's
own validated business logic" (ARCHITECTURE.md Section 2): only names in
_HANDLERS can ever run, each is a thin lambda binding session_id + args to
a services.* call, and nothing here touches MongoDB or Gemini directly.
"""

import logging

from app.services import cart_service, faq_service, menu_service, order_service, session_service
from app.utils.errors import AppError
from app.utils.validators import validate_customer_name, validate_mobile_number


def _mark_instructions_prompted(session_id, **args):
    session_service.mark_instructions_prompted(session_id)
    return {"acknowledged": True}

log = logging.getLogger(__name__)

# name -> callable(session_id, **args) -> JSON-safe dict/list
_HANDLERS = {
    "get_available_menu": lambda session_id, **args: menu_service.get_available_menu(),
    "search_menu": lambda session_id, **args: menu_service.search_menu(args["query"]),
    "get_menu_item": lambda session_id, **args: menu_service.get_menu_item(args["item_id"]),
    "check_item_availability": lambda session_id, **args: menu_service.check_item_availability(args["item_id"]),
    "get_alternatives": lambda session_id, **args: menu_service.get_alternatives(args["item_id"]),
    "search_faq": lambda session_id, **args: faq_service.search_faq(args["query"]),
    "add_to_cart": lambda session_id, **args: cart_service.add_to_cart(session_id, args["item_id"], int(args["quantity"])),
    "remove_from_cart": lambda session_id, **args: cart_service.remove_from_cart(session_id, args["item_id"]),
    "clear_cart": lambda session_id, **args: cart_service.clear_cart(session_id),
    "update_cart_quantity": lambda session_id, **args: cart_service.update_cart_quantity(
        session_id, args["item_id"], int(args["quantity"])
    ),
    "set_item_instructions": lambda session_id, **args: cart_service.set_item_instructions(
        session_id, args["item_id"], args["instructions"]
    ),
    "set_order_notes": lambda session_id, **args: cart_service.set_order_notes(session_id, args["instructions"]),
    "get_cart": lambda session_id, **args: cart_service.get_cart(session_id),
    "calculate_order_total": lambda session_id, **args: cart_service.calculate_order_total(session_id),
    "validate_customer_information": lambda session_id, **args: {
        "name": validate_customer_name(args["name"]),
        "mobile": validate_mobile_number(args["mobile"]),
    },
    "mark_instructions_prompted": _mark_instructions_prompted,
    "create_order": lambda session_id, **args: order_service.create_order(
        session_id, args["customer_name"], args["mobile"]
    ),
    "get_order_status": lambda session_id, **args: order_service.get_order_status(args["order_id"]),
}


class UnknownToolError(Exception):
    pass


class ToolExecutor:
    def execute(self, tool_name: str, args: dict, session_id: str):
        handler = _HANDLERS.get(tool_name)
        if handler is None:
            log.warning("gemini_requested_unknown_tool", extra={"tool_name": tool_name})
            raise UnknownToolError(f"Unknown tool: {tool_name}")
        try:
            return handler(session_id, **args)
        except NotImplementedError:
            # Service not implemented yet -- surface a structured result Gemini can
            # relay honestly, rather than letting a raw traceback reach the model.
            return {"error": "not_implemented", "tool": tool_name}
        except AppError as err:
            # Expected validation/business failure -- give Gemini a structured
            # result to relay, not a crash of the tool loop.
            return {"error": err.code, "message": err.message}
