# -*- coding: utf-8 -*-
# Part of StockSense. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)


class StockSenseDashboard(models.TransientModel):
    """Read-only KPI cockpit of the inventory.

    The KPI fields are computed on read and never stored: opening the dashboard
    costs a handful of ``search_count`` queries and always shows live figures.

    This model also hosts the entry point of the Low Stock Alert scheduled
    action, so that the cron and the dashboard share one definition of what a
    low stock product is.
    """

    _name = 'stocksense.dashboard'
    _description = 'StockSense KPI Dashboard'

    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    today = fields.Date(
        string='As of', default=fields.Date.context_today, readonly=True)

    total_products = fields.Integer(
        string='Storable Products', compute='_compute_total_products')
    low_stock_count = fields.Integer(
        string='Low Stock', compute='_compute_low_stock_count')
    pending_receipts_count = fields.Integer(
        string='Pending Receipts', compute='_compute_pending_count')
    pending_deliveries_count = fields.Integer(
        string='Pending Deliveries', compute='_compute_pending_count')

    # ------------------------------------------------------------------
    # Total products
    # ------------------------------------------------------------------
    @api.depends('company_id')
    def _compute_total_products(self):
        """Count the products stock actually tracks.

        ``product.template.type`` is ``'product'`` for storable goods. That value
        is contributed by the ``stock`` module and is the very filter core
        Inventory uses on ``stock.quant`` and ``stock.warehouse.orderpoint``.
        """
        for dashboard in self:
            dashboard.total_products = self.env['product.product'].search_count([
                ('type', '=', 'product'),
                ('company_id', 'in', [dashboard.company_id.id, False]),
            ])

    def action_open_products(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Storable Products'),
            'res_model': 'product.product',
            'view_mode': 'tree,form',
            'domain': [('type', '=', 'product'),
                       ('company_id', 'in', [self.company_id.id, False])],
        }

    # ------------------------------------------------------------------
    # Pending receipts / deliveries
    # ------------------------------------------------------------------
    def _get_pending_picking_domain(self, picking_type_code):
        """Domain of the pickings that are still to be processed.

        Draft pickings are excluded on purpose: they are not confirmed yet, so
        they are not warehouse work in progress.
        """
        self.ensure_one()
        return [
            ('picking_type_id.code', '=', picking_type_code),
            ('state', 'in', ('confirmed', 'waiting', 'assigned')),
            ('company_id', '=', self.company_id.id),
        ]

    @api.depends('company_id')
    def _compute_pending_count(self):
        Picking = self.env['stock.picking']
        for dashboard in self:
            dashboard.pending_receipts_count = Picking.search_count(
                dashboard._get_pending_picking_domain('incoming'))
            dashboard.pending_deliveries_count = Picking.search_count(
                dashboard._get_pending_picking_domain('outgoing'))

    def _open_pending_pickings(self, picking_type_code, name):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'stock.picking',
            'view_mode': 'tree,form',
            'domain': self._get_pending_picking_domain(picking_type_code),
        }

    def action_open_pending_receipts(self):
        return self._open_pending_pickings('incoming', _('Pending Receipts'))

    def action_open_pending_deliveries(self):
        return self._open_pending_pickings('outgoing', _('Pending Deliveries'))

    # ------------------------------------------------------------------
    # Low stock
    # ------------------------------------------------------------------
    @api.model
    def _get_low_stock_orderpoint_domain(self):
        """Domain of the reordering rules that are below their minimum.

        Mirrors the two filters core puts on its own Replenishment report:
        ``filter_to_reorder`` (``qty_to_order > 0``) and ``filter_not_snoozed``.
        Rules of both triggers ('auto' and 'manual') count as low stock.
        """
        today = fields.Date.context_today(self)
        return [
            ('active', '=', True),
            ('qty_to_order', '>', 0),
            '|', ('snoozed_until', '=', False), ('snoozed_until', '<=', today),
        ]

    @api.model
    def _get_low_stock_orderpoints(self):
        """Every reordering rule currently below its minimum, all companies."""
        return self.env['stock.warehouse.orderpoint'].search(
            self._get_low_stock_orderpoint_domain())

    @api.depends('company_id')
    def _compute_low_stock_count(self):
        Orderpoint = self.env['stock.warehouse.orderpoint']
        for dashboard in self:
            dashboard.low_stock_count = Orderpoint.search_count(
                dashboard._get_low_stock_orderpoint_domain()
                + [('company_id', '=', dashboard.company_id.id)])

    def action_open_low_stock(self):
        """Open core's Replenishment list, narrowed down to the low stock rules."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'stock.action_orderpoint_replenish')
        action['domain'] = self._get_low_stock_orderpoint_domain() + [
            ('company_id', '=', self.company_id.id)]
        action['context'] = {
            'search_default_filter_to_reorder': True,
            'search_default_filter_not_snoozed': True,
        }
        return action

    # ------------------------------------------------------------------
    # Low Stock Alert scheduled action
    # ------------------------------------------------------------------
    @api.model
    def _cron_low_stock_alerts(self):
        """Entry point of the 'StockSense: Low Stock Alerts' scheduled action.

        Sends one summary email per company to the Inventory managers, and keeps
        a single open warning activity per low stock product that has a
        responsible user (the note is refreshed rather than duplicated, so a
        product never piles up with one activity per night).
        """
        orderpoints = self._get_low_stock_orderpoints()
        if not orderpoints:
            _logger.info("StockSense: no product below its minimum, no alert sent.")
            return False

        orderpoints_by_company = {}
        for orderpoint in orderpoints:
            orderpoints_by_company.setdefault(
                orderpoint.company_id, self.env['stock.warehouse.orderpoint'])
            orderpoints_by_company[orderpoint.company_id] |= orderpoint

        for company, company_orderpoints in orderpoints_by_company.items():
            self._send_low_stock_alert(company, company_orderpoints)
        _logger.info(
            "StockSense: low stock alerts processed for %s reordering rule(s).",
            len(orderpoints))
        return True

    @api.model
    def _get_alert_recipients(self, company):
        """Inventory managers of ``company`` that can actually receive an email."""
        managers = self.env.ref('stock.group_stock_manager', raise_if_not_found=False)
        if not managers:
            return self.env['res.users']
        return managers.users.filtered(
            lambda user: user.active
            and user.partner_id.email
            and company in user.company_ids)

    @api.model
    def _get_low_stock_summary(self):
        """Stable summary used as the deduplication key of the warning activity."""
        return _("StockSense: product below minimum stock")

    def _build_low_stock_alert_body(self, company, orderpoints):
        """Build the HTML body listing every low stock rule of one company."""
        rows = []
        for orderpoint in orderpoints:
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s %s</td></tr>" % (
                    html_escape(orderpoint.product_id.display_name),
                    html_escape(orderpoint.name),
                    html_escape(orderpoint.warehouse_id.display_name),
                    orderpoint.qty_on_hand,
                    orderpoint.qty_forecast,
                    orderpoint.qty_to_order,
                    html_escape(orderpoint.product_uom.display_name),
                ))
        return (
            "<div><p>%s</p><table class='table table-sm o_main_table'>"
            "<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table></div>"
        ) % (
            _("%(count)s reordering rule(s) are below their minimum stock:",
              count=len(orderpoints)),
            _("Product"), _("Reordering Rule"), _("Warehouse"),
            _("On Hand"), _("Forecast"), _("To Order"),
            "".join(rows),
        )

    @api.model
    def _warn_product_responsible(self, orderpoint, summary, note):
        """Keep at most one open warning activity per product template.

        Mirrors the deduplication core performs on the orderpoints it fails to
        process: an existing activity is refreshed instead of being recreated on
        every run, so a product does not pile up one activity per night.

        Returns an empty recordset when the product has no responsible user --
        the summary email still covers that product.
        """
        responsible = orderpoint.product_id.responsible_id
        if not responsible:
            return self.env['mail.activity']
        template = orderpoint.product_id.product_tmpl_id
        activity = self.env['mail.activity'].sudo().search([
            ('res_id', '=', template.id),
            ('res_model_id', '=', self.env.ref('product.model_product_template').id),
            ('summary', '=', summary),
        ], limit=1)
        if activity:
            activity.write({'note': note})
            return activity
        return template.sudo().activity_schedule(
            'mail.mail_activity_data_warning',
            user_id=responsible.id,
            summary=summary,
            note=note,
        )

    def _send_low_stock_alert(self, company, orderpoints):
        """Email the Inventory managers of ``company``, then warn product owners."""
        recipients = self._get_alert_recipients(company)
        if recipients:
            self.env['mail.mail'].sudo().create({
                'subject': _("StockSense: %s product(s) below their minimum stock")
                           % len(orderpoints),
                'body_html': self._build_low_stock_alert_body(company, orderpoints),
                'email_to': ','.join(recipients.mapped('partner_id.email')),
                'author_id': company.partner_id.id,
            }).send()
        else:
            _logger.warning(
                "StockSense: no Inventory manager with an email address on %s, "
                "no low stock email sent.", company.display_name)

        summary = self._get_low_stock_summary()
        for orderpoint in orderpoints:
            note = _(
                "%(product)s is below its minimum stock: %(to_order)s "
                "%(uom)s to order.") % {
                'product': orderpoint.product_id.display_name,
                'to_order': orderpoint.qty_to_order,
                'uom': orderpoint.product_uom.display_name,
            }
            self._warn_product_responsible(orderpoint, summary, note)
        return True
