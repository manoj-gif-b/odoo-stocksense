# -*- coding: utf-8 -*-
"""StockSense Core - reordering rules (minimum / maximum stock levels).

Extends the native ``stock.warehouse.orderpoint`` model so that the minimum and
maximum stock levels of a product can be managed from the StockSense menus and
opened directly from the product form.

``stock.warehouse.orderpoint`` (inherited)
    * ``ss_sku``: StockSense SKU of the product (stored, searchable).
    * ``ss_status`` / ``ss_shortage_qty``: availability of the product on the
      location of the rule, used for the list decorations.
    * ``ss_note``: free comment shared with the warehouse team.

``product.template`` (inherited)
    * ``ss_orderpoint_count`` and ``action_ss_open_orderpoints``: the counter and
      the action behind the stat button of the product form.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockWarehouseOrderpoint(models.Model):
    """Minimum / maximum stock level of a product, per location."""

    _inherit = 'stock.warehouse.orderpoint'

    ss_sku = fields.Char(
        string='StockSense SKU', related='product_id.ss_sku', store=True,
        readonly=True,
        help="StockSense reference of the product, searchable from the rules list.")
    ss_status = fields.Selection(
        [('ok', 'OK'),
         ('below_min', 'Below Minimum'),
         ('out_of_stock', 'Out of Stock')],
        string='Stock Status', compute='_compute_ss_status',
        help="Availability of the product on the location of the rule.")
    ss_shortage_qty = fields.Float(
        string='Shortage', compute='_compute_ss_status',
        digits='Product Unit of Measure',
        help="Quantity missing to reach the minimum stock level.")
    ss_note = fields.Text(
        string='StockSense Note',
        help="Internal note about this rule, shared with the warehouse team.")

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('qty_forecast', 'qty_on_hand', 'product_min_qty')
    def _compute_ss_status(self):
        for orderpoint in self:
            if orderpoint.qty_forecast <= 0.0:
                orderpoint.ss_status = 'out_of_stock'
            elif (orderpoint.product_min_qty
                    and orderpoint.qty_forecast < orderpoint.product_min_qty):
                orderpoint.ss_status = 'below_min'
            else:
                orderpoint.ss_status = 'ok'
            shortage = orderpoint.product_min_qty - orderpoint.qty_forecast
            orderpoint.ss_shortage_qty = shortage if shortage > 0.0 else 0.0

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('product_min_qty', 'product_max_qty')
    def _check_ss_min_max_qty(self):
        for orderpoint in self:
            if orderpoint.product_min_qty < 0.0 or orderpoint.product_max_qty < 0.0:
                raise ValidationError(_(
                    "The minimum and maximum quantities of \"%s\" cannot be "
                    "negative.") % orderpoint.product_id.display_name)
            if (orderpoint.product_max_qty
                    and orderpoint.product_max_qty < orderpoint.product_min_qty):
                raise ValidationError(_(
                    "The maximum quantity of \"%s\" must be greater than or equal "
                    "to its minimum quantity."
                ) % orderpoint.product_id.display_name)


class ProductTemplate(models.Model):
    """Expose the minimum / maximum stock rules of a product on its form."""

    _inherit = 'product.template'

    ss_orderpoint_count = fields.Integer(
        string='Reordering Rules', compute='_compute_ss_orderpoint_count')

    @api.depends()
    def _compute_ss_orderpoint_count(self):
        orderpoint_model = self.env['stock.warehouse.orderpoint']
        for template in self:
            template.ss_orderpoint_count = orderpoint_model.search_count(
                [('product_tmpl_id', '=', template.id)])

    def action_ss_open_orderpoints(self):
        """Open the reordering rules (min / max stock levels) of the product."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Reordering Rules'),
            'res_model': 'stock.warehouse.orderpoint',
            'view_mode': 'tree,form',
            'domain': [('product_tmpl_id', '=', self.id)],
            'context': {
                'default_product_id': self.product_variant_id.id or False,
                'default_detailed_type': 'product',
            },
        }
        views = self._ss_get_orderpoint_views()
        if views:
            action['views'] = views
        return action

    @api.model
    def _ss_get_orderpoint_views(self):
        """Return the StockSense ``(view_id, view_mode)`` pairs of the rules views."""
        model_data = self.env['ir.model.data']
        views = []
        for view_name, view_mode in (
                ('view_stocksense_orderpoint_tree', 'tree'),
                ('view_stocksense_orderpoint_form', 'form')):
            data = model_data.search([
                ('model', '=', 'ir.ui.view'),
                ('name', '=', view_name),
            ], limit=1)
            if data:
                views.append((data.res_id, view_mode))
        return views
