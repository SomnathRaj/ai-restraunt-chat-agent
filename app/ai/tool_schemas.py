"""Provider-neutral tool schemas for every backend capability in PRD Section 46.

Each tool is a plain dict -- name, description, and `parameters` as a
JSON-Schema object -- with no provider SDK types. Each adapter under
app/ai/providers/ translates this list into its own provider's tool format
at call time (MULTI_AI_PROVIDER_DESIGN.md Section 3.1).

Schema only, no bound Python callables, so the model can only ever *request*
a call -- execution always goes through app/ai/tool_executor.py's
allow-listed dispatch into app/services/*, never directly (see
ARCHITECTURE.md Section 2).

set_item_instructions / set_order_notes take ONLY a string parameter --
structurally incapable of touching price, quantity, or availability. See
ARCHITECTURE.md Section 5 for why this separation matters.
"""

# Accepting the exact name saves a whole search_menu round-trip per item
# (menu_service.find_item / cart_service._find_line); still exact-only.
_ITEM_REF = {
    "type": "string",
    "description": (
        "The item's item_id, or its exact menu name as shown to the customer (e.g. 'Chicken Wrap'). "
        "When the customer names a dish, pass the name directly -- no need to call search_menu first."
    ),
}

_RETURNS_CART = " Returns the full updated cart, including subtotal and total -- no need to call get_cart afterwards."

TOOL_SCHEMAS: list[dict] = [
    dict(
        name="get_available_menu",
        description="Return all currently active and available menu items, grouped by category.",
        parameters={"type": "object", "properties": {}},
    ),
    dict(
        name="search_menu",
        description="Search available menu items by free-text query (e.g. dish name, category, 'under 300', 'spicy').",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The customer's search phrase."}},
            "required": ["query"],
        },
    ),
    dict(
        name="get_menu_item",
        description="Return full details (description, price, availability) for one menu item.",
        parameters={
            "type": "object",
            "properties": {"item_id": _ITEM_REF},
            "required": ["item_id"],
        },
    ),
    dict(
        name="check_item_availability",
        description="Check whether a specific menu item is currently available.",
        parameters={
            "type": "object",
            "properties": {"item_id": _ITEM_REF},
            "required": ["item_id"],
        },
    ),
    dict(
        name="get_alternatives",
        description="Get 2-4 relevant available alternatives for an item that is unavailable or does not exist.",
        parameters={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    ),
    dict(
        name="search_faq",
        description=(
            "Search the restaurant's FAQ for a general restaurant-level question (hours, address, "
            "payment methods, parking, delivery time, reservation policy, etc.) -- NOT for "
            "menu-item-specific questions, use get_menu_item/search_menu for those. Always relay "
            "the returned answer verbatim when matched is true; if matched is false, say you don't "
            "have that information rather than guessing."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The customer's question, verbatim."}},
            "required": ["query"],
        },
    ),
    dict(
        name="add_to_cart",
        description="Add a menu item to the customer's cart, validated against live availability and price." + _RETURNS_CART,
        parameters={
            "type": "object",
            "properties": {
                "item_id": _ITEM_REF,
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["item_id", "quantity"],
        },
    ),
    dict(
        name="remove_from_cart",
        description="Remove an item from the cart entirely." + _RETURNS_CART,
        parameters={
            "type": "object",
            "properties": {"item_id": _ITEM_REF},
            "required": ["item_id"],
        },
    ),
    dict(
        name="clear_cart",
        description=(
            "Empty the entire cart in one call -- all items and any whole-order cooking notes. "
            "Use this when the customer wants to cancel/start over their whole order (e.g. 'cancel my order', "
            "'clear my cart', 'start over'). Prefer this over removing items one by one."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    dict(
        name="update_cart_quantity",
        description="Change the quantity of an item already in the cart." + _RETURNS_CART,
        parameters={
            "type": "object",
            "properties": {
                "item_id": _ITEM_REF,
                "quantity": {"type": "integer", "minimum": 0},
            },
            "required": ["item_id", "quantity"],
        },
    ),
    dict(
        name="set_item_instructions",
        description=(
            "Attach a free-text cooking/preparation note to ONE cart item (e.g. 'extra spicy', "
            "'no onion', 'more gravy'). Never use this for requests implying more product or an "
            "extra chargeable add-on (e.g. 'extra chicken', 'extra cheese') -- those are menu/quantity "
            "changes and must use add_to_cart or update_cart_quantity instead. To add an item and its "
            "note together, call add_to_cart and this in the same turn, add_to_cart first."
        )
        + _RETURNS_CART,
        parameters={
            "type": "object",
            "properties": {
                "item_id": _ITEM_REF,
                "instructions": {"type": "string", "description": "Verbatim preparation note, e.g. 'extra spicy, no onion'."},
            },
            "required": ["item_id", "instructions"],
        },
    ),
    dict(
        name="set_order_notes",
        description="Attach a free-text cooking/preparation note to the WHOLE order (not a specific item), e.g. 'no onion or garlic in anything'.",
        parameters={
            "type": "object",
            "properties": {"instructions": {"type": "string"}},
            "required": ["instructions"],
        },
    ),
    dict(
        name="get_cart",
        description="Return the current cart contents including quantities, prices, and any cooking instructions.",
        parameters={"type": "object", "properties": {}},
    ),
    dict(
        name="calculate_order_total",
        description="Return the current cart's subtotal and total, computed from MongoDB prices.",
        parameters={"type": "object", "properties": {}},
    ),
    dict(
        name="validate_customer_information",
        description="Validate a customer name and mobile number before checkout.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mobile": {"type": "string"},
            },
            "required": ["name", "mobile"],
        },
    ),
    dict(
        name="mark_instructions_prompted",
        description=(
            "Call this once you have asked the customer whether they'd like any cooking/preparation "
            "instructions for their order and received any answer, including 'no'/'none needed'. "
            "This unlocks create_order. Not needed if the customer already volunteered instructions "
            "via set_item_instructions or set_order_notes -- that already satisfies this requirement. "
            "If create_order fails with 'instructions_not_prompted', ask about instructions, call this, "
            "then retry create_order."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    dict(
        name="create_order",
        description=(
            "Create the confirmed order. Only call this after the customer has explicitly confirmed "
            "the final order summary (including any cooking instructions) -- never before."
        ),
        parameters={
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "mobile": {"type": "string"},
            },
            "required": ["customer_name", "mobile"],
        },
    ),
    dict(
        name="get_order_status",
        description="Look up the real status of a previously placed order by its order ID.",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    ),
]
