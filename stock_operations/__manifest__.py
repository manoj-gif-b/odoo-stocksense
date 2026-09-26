# Part of StockSense. See LICENSE file for full copyright and licensing details.
{
    'name': 'StockSense - Stock Operations',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Unified operations cockpit for receipts, deliveries and '
               'internal transfers',
    'description': """
StockSense - Stock Operations
=============================

Adds a single, uniform operations cockpit on top of the standard transfers
(``stock.picking``) for the three daily warehouse operations:

* Receipts (incoming)
* Deliveries (outgoing)
* Internal Transfers (internal)

Features
--------
* Operation classification: Receipt / Delivery / Internal Transfer.
* Simplified status badges: Draft, Waiting, Ready, Done, Cancelled.
* Demand / processed quantities and a progress indicator.
* Lateness indicators (delay in days, late flag) based on the scheduled date.
* Dedicated tree, form and search views with a dedicated menu, plus a
  ``get_operation_state_summary`` helper for dashboards.

The standard Odoo transfer workflow (confirm, check availability, validate,
cancel) is reused as-is: StockSense only adds read-only helpers and views.
""",
    'author': 'StockSense',
    'website': 'https://github.com/manoj-gif-b/odoo-stocksense',
    'license': 'LGPL-3',
    'depends': [
        'stock',
    ],
    'data': [
        'views/stock_operation_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
