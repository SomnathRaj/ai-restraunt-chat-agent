"""Gemini tool declarations for every backend capability in PRD Section 46.

Declared as plain types.FunctionDeclaration objects (schema only, no bound
Python callables) so Gemini can only ever *request* a call -- execution
always goes through app/ai/tool_executor.py's allow-listed dispatch into
app/services/*, never directly (see ARCHITECTURE.md Section 2).

set_item_instructions / set_order_notes take ONLY a string parameter --
structurally incapable of touching price, quantity, or availability. See
ARCHITECTURE.md Section 5 for why this separation matters.
"""

from google.genai import types

TOOL_DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name="get_available_menu",
        description="Return all currently active and available menu items, grouped by category.",
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="search_menu",
        description="Search available menu items by free-text query (e.g. dish name, category, 'under 300', 'spicy').",
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The customer's search phrase."}},
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="get_menu_item",
        description="Return full details (description, price, availability) for one menu item.",
        parameters_json_schema={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    ),
    types.FunctionDeclaration(
        name="check_item_availability",
        description="Check whether a specific menu item is currently available.",
        parameters_json_schema={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    ),
    types.FunctionDeclaration(
        name="get_alternatives",
        description="Get 2-4 relevant available alternatives for an item that is unavailable or does not exist.",
        parameters_json_schema={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    ),
    types.FunctionDeclaration(
        name="search_faq",
        description=(
            "Search the restaurant's FAQ for a general restaurant-level question (hours, address, "
            "payment methods, parking, delivery time, reservation policy, etc.) -- NOT for "
            "menu-item-specific questions, use get_menu_item/search_menu for those. Always relay "
            "the returned answer verbatim when matched is true; if matched is false, say you don't "
            "have that information rather than guessing."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The customer's question, verbatim."}},
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="add_to_cart",
        description="Add a menu item to the customer's cart, validated against live availability and price.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["item_id", "quantity"],
        },
    ),
    types.FunctionDeclaration(
        name="remove_from_cart",
        description="Remove an item from the cart entirely.",
        parameters_json_schema={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    ),
    types.FunctionDeclaration(
        name="clear_cart",
        description=(
            "Empty the entire cart in one call -- all items and any whole-order cooking notes. "
            "Use this when the customer wants to cancel/start over their whole order (e.g. 'cancel my order', "
            "'clear my cart', 'start over'). Prefer this over removing items one by one."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="update_cart_quantity",
        description="Change the quantity of an item already in the cart.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 0},
            },
            "required": ["item_id", "quantity"],
        },
    ),
    types.FunctionDeclaration(
        name="set_item_instructions",
        description=(
            "Attach a free-text cooking/preparation note to ONE cart item (e.g. 'extra spicy', "
            "'no onion', 'more gravy'). Never use this for requests implying more product or an "
            "extra chargeable add-on (e.g. 'extra chicken', 'extra cheese') -- those are menu/quantity "
            "changes and must use add_to_cart or update_cart_quantity instead."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "instructions": {"type": "string", "description": "Verbatim preparation note, e.g. 'extra spicy, no onion'."},
            },
            "required": ["item_id", "instructions"],
        },
    ),
    types.FunctionDeclaration(
        name="set_order_notes",
        description="Attach a free-text cooking/preparation note to the WHOLE order (not a specific item), e.g. 'no onion or garlic in anything'.",
        parameters_json_schema={
            "type": "object",
            "properties": {"instructions": {"type": "string"}},
            "required": ["instructions"],
        },
    ),
    types.FunctionDeclaration(
        name="get_cart",
        description="Return the current cart contents including quantities, prices, and any cooking instructions.",
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="calculate_order_total",
        description="Return the current cart's subtotal and total, computed from MongoDB prices.",
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="validate_customer_information",
        description="Validate a customer name and mobile number before checkout.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mobile": {"type": "string"},
            },
            "required": ["name", "mobile"],
        },
    ),
    types.FunctionDeclaration(
        name="mark_instructions_prompted",
        description=(
            "Call this once you have asked the customer whether they'd like any cooking/preparation "
            "instructions for their order and received any answer, including 'no'/'none needed'. "
            "This unlocks create_order. Not needed if the customer already volunteered instructions "
            "via set_item_instructions or set_order_notes -- that already satisfies this requirement. "
            "If create_order fails with 'instructions_not_prompted', ask about instructions, call this, "
            "then retry create_order."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="create_order",
        description=(
            "Create the confirmed order. Only call this after the customer has explicitly confirmed "
            "the final order summary (including any cooking instructions) -- never before."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "mobile": {"type": "string"},
            },
            "required": ["customer_name", "mobile"],
        },
    ),
    types.FunctionDeclaration(
        name="get_order_status",
        description="Look up the real status of a previously placed order by its order ID.",
        parameters_json_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    ),
]
