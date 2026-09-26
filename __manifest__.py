# -*- coding: utf-8 -*-
{
    'name': 'StockSense Core',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'StockSense inventory foundations: products, reorder rules, '
               'initial stock, stock operations, dashboard, adjustments and '
               'low stock alerts.',
    'description': """
StockSense Core
===============

Core foundation of the **StockSense** Inventory Management System.

Features
--------
* Custom **StockSense SKU** on products (indexed, audit tracked and unique per
  company).
* **Reorder rules** (reorder point / maximum quantity / multiple quantity) per
  product and, optionally, per warehouse and per source location.
* **Initial stock** captured on the product form, with a one-click application
  of the quantity on the stock location of the warehouse.
* Default reorder thresholds per **product category**, proposed when a new
  reorder rule is created on a product of the category.
* Tree and form views to manage products, product categories and reorder rules,
  reachable from the Inventory app.
* Access rights built on the standard Odoo stock groups: **Inventory
  Managers** (full rights) and **Warehouse Staff** (read/write, no deletion).
* **Stock Operations** (merged from *StockSense - Stock Operations*): the three
  daily warehouse operations on ``stock.picking`` - receipts, deliveries and
  internal transfers - exposed through one uniform list with operation
  classification, simplified status badges (Draft / Waiting / Ready / Done /
  Cancelled), demand and processed quantities, a progress indicator and
  lateness flags, plus a dedicated menu under Inventory and a
  ``get_operation_state_summary`` helper for dashboards. The native transfer
  workflow is reused untouched.
* **Dashboard** (merged from *StockSense - Dashboard & Adjustments*): a
  ``stocksense.dashboard`` cockpit with four KPIs (Total Products, Low Stock,
  Pending Receipts, Pending Deliveries), each one opening the matching records.
* **Inventory Adjustments**: multi-line ``stock.adjustment`` documents applied
  through ``stock.quant``, so valuation, stock moves and traceability stay
  intact.
* **Low stock alerts** - two complementary scheduled actions, both running as
  superuser and both kept on purpose:
  * hourly (``data/ir_cron_data.xml`` -> ``ir_cron_stocksense_low_stock``):
    compares the quantity on hand of every active reordering rule with its
    minimum, then sends a sticky in-app notification to the Inventory Managers
    and always writes a warning line in the server log (immediate visibility);
  * daily (``data/ir_cron_dashboard_data.xml`` -> ``ir_cron_low_stock_alerts``):
    emails one summary per company to the Inventory Managers and keeps a single
    open warning activity per low stock product (durable, deduplicated trail).
    Disable one of the two jobs in *Settings > Technical > Scheduled Actions* if
    a single alerting channel is preferred.
* **Dynamic filters** (``views/stock_search_views.xml``): the stock document
  lists - receipts, deliveries, internal transfers and inventory adjustments -
  are filtered by *Document Type* (Receipts / Deliveries / Internal /
  Adjustments), by *Status* (Draft / Waiting / Ready / Done / Canceled) and
  grouped by *Warehouse*, *Location* and *Product Category*.
* **Profile menu** (``views/profile_menu.xml``): a *My Account* entry in the
  backend sidebar with the *My Profile* (user preferences) and *Logout*
  shortcuts, available to every internal user.

Technical notes
---------------
* Compatible with Odoo 16.0 and 17.0 (only the manifest ``version`` prefix has to
  be adapted when deploying on 16.0).
* The addon folder must be renamed ``stocksense`` (no dash) inside the addons
  path, otherwise Odoo cannot import the Python package.
""",
    'author': 'StockSense',
    'website': 'https://github.com/manoj-gif-b/odoo-stocksense',
    'license': 'LGPL-3',
    'depends': [
        'stock',
        'mail',
        'auth_signup',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/dashboard_adjustment_access.csv',
        'security/stocksense_security.xml',
        'data/ir_cron_data.xml',
        'data/ir_cron_dashboard_data.xml',
        'data/stock_adjustment_data.xml',
        'views/product_views.xml',
        'views/reorder_rule_views.xml',
        'views/stock_operation_views.xml',
        'views/stock_adjustment_views.xml',
        'views/dashboard_views.xml',
        'views/stock_search_views.xml',
        'views/profile_menu.xml',
        'views/menu.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
}
