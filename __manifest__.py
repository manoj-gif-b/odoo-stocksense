# -*- coding: utf-8 -*-
{
    'name': 'StockSense Core',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'StockSense core inventory foundations: product SKU, reorder rules and initial stock.',
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
        'auth_signup',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/stocksense_security.xml',
        'views/product_views.xml',
        'views/reorder_rule_views.xml',
        'views/menu.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
}
