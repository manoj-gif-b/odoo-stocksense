# Part of StockSense. See LICENSE file for full copyright and licensing details.
"""StockSense - Stock Operations.

Extends ``stock.picking`` so the three daily warehouse operations
(Receipts, Deliveries and Internal Transfers) are exposed through one
uniform representation:

* ``operation_type``  - Receipt / Delivery / Internal Transfer, derived from
  the native operation type (``incoming`` / ``outgoing`` / ``internal``);
* ``operation_state`` - Draft / Waiting / Ready / Done / Cancelled, driving
  the status badges of the StockSense user interface;
* reporting helpers used by the tree, form and dashboard views (move
  counters, progress and lateness).

The standard Odoo workflow is deliberately *not* re-implemented: every field
added here is either a stored compute field (fast to search and group by) or
a live compute field (always accurate on read), so ``button_confirm``,
``button_validate`` and the reservation logic keep working untouched.

Compatibility
-------------
Written against Odoo 17.0: uses ``stock.move.quantity`` (renamed from
``quantity_done`` in 17.0), ``stock.picking.picking_type_id.code``,
``scheduled_date`` / ``date_done`` and the ``_read_group`` ORM API.
"""

from odoo import api, fields, models

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: StockSense operation categories, shown in the "Operation Type" filters.
OPERATION_TYPES = [
    ('receipt', 'Receipt'),
    ('delivery', 'Delivery'),
    ('internal', 'Internal Transfer'),
]

#: Simplified StockSense statuses, displayed as coloured badges.
OPERATION_STATES = [
    ('draft', 'Draft'),
    ('waiting', 'Waiting'),
    ('ready', 'Ready'),
    ('done', 'Done'),
    ('cancelled', 'Cancelled'),
]

#: ``stock.picking.type.code`` -> StockSense ``operation_type``.
PICKING_TYPE_CODE_TO_OPERATION_TYPE = {
    'incoming': 'receipt',
    'outgoing': 'delivery',
    'internal': 'internal',
}

#: ``stock.picking.state`` -> StockSense ``operation_state``.
#: Native 'waiting' (waiting another operation) and 'confirmed' (waiting
#: availability) are both reported as "Waiting" to the warehouse operator.
PICKING_STATE_TO_OPERATION_STATE = {
    'draft': 'draft',
    'waiting': 'waiting',
    'confirmed': 'waiting',
    'assigned': 'ready',
    'done': 'done',
    'cancel': 'cancelled',
}

_SECONDS_PER_DAY = 86400.0


class StockPicking(models.Model):
    """Add the StockSense operation abstraction on top of pickings."""

    _inherit = 'stock.picking'

    # ------------------------------------------------------------------
    # Operation classification
    # ------------------------------------------------------------------
    operation_type = fields.Selection(
        selection=OPERATION_TYPES,
        string='Operation Type',
        compute='_compute_operation_type',
        store=True,
        index=True,
        help="StockSense category of the operation, deduced from the "
             "operation type: Incoming -> Receipt, Outgoing -> Delivery, "
             "Internal -> Internal Transfer.",
    )
    is_receipt = fields.Boolean(
        string='Receipt',
        compute='_compute_operation_type',
        store=True,
        help="Technical helper allowing receipts to be filtered or grouped "
             "without joining the operation type.",
    )
    is_delivery = fields.Boolean(
        string='Delivery',
        compute='_compute_operation_type',
        store=True,
        help="Technical helper allowing deliveries to be filtered or "
             "grouped without joining the operation type.",
    )
    is_internal_transfer = fields.Boolean(
        string='Internal Transfer',
        compute='_compute_operation_type',
        store=True,
        help="Technical helper allowing internal transfers to be filtered "
             "or grouped without joining the operation type.",
    )

    # ------------------------------------------------------------------
    # Operation status (badge)
    # ------------------------------------------------------------------
    operation_state = fields.Selection(
        selection=OPERATION_STATES,
        string='Operation Status',
        compute='_compute_operation_state',
        store=True,
        index=True,
        help="Simplified status shown as a badge in the StockSense views. "
             "The native 'Waiting Another Operation' and 'Waiting "
             "Availability' statuses are both reported as 'Waiting'.",
    )

    # ------------------------------------------------------------------
    # Operation metrics
    # ------------------------------------------------------------------
    operation_move_count = fields.Integer(
        string='Move Lines',
        compute='_compute_operation_metrics',
        store=True,
        help="Number of stock moves to process for this operation "
             "(cancelled moves excluded).",
    )
    operation_demand_qty = fields.Float(
        string='Demand',
        compute='_compute_operation_metrics',
        store=True,
        digits='Product Unit of Measure',
        help="Total demanded quantity of the operation's stock moves, "
             "expressed in their respective units of measure.",
    )
    operation_done_qty = fields.Float(
        string='Processed',
        compute='_compute_operation_metrics',
        store=True,
        digits='Product Unit of Measure',
        help="Total quantity already processed for this operation.",
    )
    operation_progress = fields.Float(
        string='Progress',
        compute='_compute_operation_metrics',
        store=True,
        digits=(16, 2),
        help="Percentage of the demanded quantity already processed "
             "(0 - 100).",
    )
    operation_delay_days = fields.Integer(
        string='Delay (Days)',
        compute='_compute_operation_delay',
        help="Number of full days between the operation's scheduled date "
             "and the current date (0 when the operation is on time).",
    )
    operation_is_late = fields.Boolean(
        string='Late',
        compute='_compute_operation_delay',
        help="Set when the scheduled date is passed and the operation is "
             "still open, or when a completed operation was validated after "
             "its scheduled date.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('picking_type_id.code')
    def _compute_operation_type(self):
        """Map the native operation type code to the StockSense category."""
        for picking in self:
            operation_type = PICKING_TYPE_CODE_TO_OPERATION_TYPE.get(
                picking.picking_type_id.code)
            picking.operation_type = operation_type or False
            picking.is_receipt = operation_type == 'receipt'
            picking.is_delivery = operation_type == 'delivery'
            picking.is_internal_transfer = operation_type == 'internal'

    @api.depends('state')
    def _compute_operation_state(self):
        """Map the native picking status to the StockSense status."""
        for picking in self:
            picking.operation_state = PICKING_STATE_TO_OPERATION_STATE.get(
                picking.state, 'draft')

    @api.depends(
        'move_ids',
        'move_ids.product_uom_qty',
        'move_ids.quantity',
        'move_ids.state',
    )
    def _compute_operation_metrics(self):
        """Aggregate the demand, the processed quantity and the progress."""
        for picking in self:
            moves = picking.move_ids.filtered(lambda move: move.state != 'cancel')
            demand_qty = sum(moves.mapped('product_uom_qty'))
            done_qty = sum(moves.mapped('quantity'))
            picking.operation_move_count = len(moves)
            picking.operation_demand_qty = demand_qty
            picking.operation_done_qty = done_qty
            picking.operation_progress = (
                done_qty / demand_qty * 100.0 if demand_qty else 0.0)

    @api.depends('state', 'scheduled_date', 'date_done')
    def _compute_operation_delay(self):
        """Compare the scheduled date with the execution (or current) date."""
        now = fields.Datetime.now()
        for picking in self:
            scheduled_date = picking.scheduled_date
            if not scheduled_date or picking.state == 'cancel':
                reference = None
            elif picking.state == 'done':
                # A completed operation is measured on its validation date.
                reference = picking.date_done or now
            else:
                reference = now
            delay_seconds = (
                (reference - scheduled_date).total_seconds()
                if reference else 0.0)
            picking.operation_delay_days = (
                int(delay_seconds // _SECONDS_PER_DAY)
                if delay_seconds > 0 else 0)
            picking.operation_is_late = delay_seconds > 0

    # ------------------------------------------------------------------
    # Reporting helper
    # ------------------------------------------------------------------
    @api.model
    def get_operation_state_summary(self, operation_types=None):
        """Count the operations of each StockSense status.

        Lightweight helper meant to be consumed by the StockSense dashboard
        and by the reporting module.

        :param operation_types: optional list of ``operation_type`` values
            restricting the summary (e.g. ``['receipt', 'delivery']``).
        :return: dict mapping every ``operation_state`` value to its count.
        """
        domain = []
        if operation_types:
            domain.append(('operation_type', 'in', list(operation_types)))
        # Always return exactly one entry per declared badge: the summary is
        # a fixed-size contract for the dashboard, so unmapped groups (e.g.
        # a picking whose operation type could not be resolved) are ignored.
        summary = {state: 0 for state, _label in OPERATION_STATES}
        for operation_state, count in self._read_group(
                domain, ['operation_state'], ['__count']):
            if operation_state in summary:
                summary[operation_state] += count
        return summary
