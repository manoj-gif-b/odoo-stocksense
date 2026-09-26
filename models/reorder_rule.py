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

``stock.warehouse.orderpoint`` (hourly cron)
    * ``_ss_cron_notify_low_stock``: scheduled action
      ``data/ir_cron_data.xml`` warning the Inventory Managers when a product is
      below the minimum quantity of its reordering rule.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

#: Maximum number of low stock products listed in one notification.
_SS_LOW_STOCK_LIST_LIMIT = 10


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

    # ------------------------------------------------------------------
    # Low stock alerts (hourly cron - data/ir_cron_data.xml)
    # ------------------------------------------------------------------
    @api.model
    def _ss_get_low_stock_orderpoints(self):
        """Reordering rules whose product is below the minimum quantity.

        ``qty_on_hand`` is a non-stored compute field (it is computed for the
        location of the rule), so the comparison is done in Python rather than
        as a search domain.
        """
        orderpoints = self.search([
            ('active', '=', True),
            ('product_min_qty', '>', 0.0),
        ])
        return orderpoints.filtered(
            lambda orderpoint: orderpoint.qty_on_hand < orderpoint.product_min_qty)

    @api.model
    def _ss_get_inventory_managers(self):
        """Users belonging to the Inventory Manager group."""
        group = self.env.ref('stock.group_stock_manager', raise_if_not_found=False)
        if not group:
            return self.env['res.users']
        return self.env['res.users'].search([('groups_id', 'in', group.ids)])

    @api.model
    def _ss_notify_user(self, user, title, message):
        """Send an in-app (systray) notification to ``user``.

        ``notify_warning`` is the public helper of the notification API; the
        ``bus.bus`` notification underneath is used as a fallback so the alert is
        never lost because of a missing helper.
        """
        notify_warning = getattr(user, 'notify_warning', None)
        if notify_warning:
            notify_warning(message=message, title=title, sticky=True)
            return True
        self.env['bus.bus']._sendone(user.partner_id, 'simple_notification', {
            'type': 'warning',
            'title': title,
            'message': message,
            'sticky': True,
        })
        return True

    @api.model
    def _ss_cron_notify_low_stock(self):
        """Warn the Inventory Managers about the products below their minimum.

        Entry point of the hourly scheduled action
        ``stocksense.ir_cron_stocksense_low_stock`` (data/ir_cron_data.xml).
        Always logs a trace, so the alert is traceable even when no manager is
        connected to the web client.
        """
        orderpoints = self._ss_get_low_stock_orderpoints()
        if not orderpoints:
            _logger.info(
                "StockSense low stock check: no product below its minimum quantity.")
            return 0
        lines = [
            '%s - %s: %s on hand < %s minimum' % (
                orderpoint.location_id.display_name,
                orderpoint.product_id.display_name,
                orderpoint.qty_on_hand,
                orderpoint.product_min_qty,
            )
            for orderpoint in orderpoints[:_SS_LOW_STOCK_LIST_LIMIT]
        ]
        remaining = len(orderpoints) - _SS_LOW_STOCK_LIST_LIMIT
        if remaining > 0:
            lines.append(_('... and %s more product(s).') % remaining)
        title = _('StockSense: low stock alert')
        message = '\n'.join(lines)
        _logger.warning(
            "StockSense low stock alert: %s reordering rule(s) below the minimum:\n%s",
            len(orderpoints), message)
        for manager in self._ss_get_inventory_managers():
            self._ss_notify_user(manager, title, message)
        return len(orderpoints)


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
