"""Canonical organization role + permission blueprint.

Used to provision default roles for new organizations and to backfill existing
ones. Keep in sync with the frontend ROLE_PERMISSIONS map.
"""

ALL_PERMISSIONS = [
    "view_overview",
    "view_orders",
    "manage_orders",
    "view_fleet",
    "manage_fleet",
    "view_staff",
    "manage_staff",
    "view_customers",
    "manage_customers",
    "view_analytics",
    "view_settings",
    "manage_settings",
]

# (key, label, description, permission_codes)
ROLE_BLUEPRINTS = [
    ("owner", "Organization Owner", "Full access to the business workspace.", list(ALL_PERMISSIONS)),
    (
        "branch_manager",
        "Branch Manager",
        "Runs day-to-day operations for a single branch.",
        [
            "view_overview",
            "view_orders",
            "manage_orders",
            "view_fleet",
            "manage_fleet",
            "view_customers",
            "manage_customers",
            "view_analytics",
            "view_staff",
        ],
    ),
    (
        "fleet_manager",
        "Fleet Manager",
        "Manages couriers and assigns deliveries to them.",
        ["view_overview", "view_orders", "manage_orders", "view_fleet", "manage_fleet", "view_customers", "view_analytics"],
    ),
    (
        "customer_support",
        "Customer Support",
        "Customer operations only.",
        ["view_overview", "view_orders", "view_customers", "manage_customers"],
    ),
    ("analyst", "Analyst", "Read-only analytics access.", ["view_overview", "view_analytics"]),
]
