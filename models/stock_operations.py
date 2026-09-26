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
Odoo 16.0 and 17.0 (Community). The two version specific points are resolved
at runtime instead of being hard coded:

* the processed quantity of a move is read through
  :func:`get_done_quantity_field` (``quantity_done`` on 16.0, ``quantity``
  on 17.0) and exposed to the views as ``operation_done_qty``;
* ``_read_group`` (17.0) and ``read_group`` (16.0) do not share the same
  signature, so the dashboard summary counts the records one status at a
  time.

Everything else (``picking_type_id.code``, ``scheduled_date``, ``date_done``,
stored computed fields, ``parent_of`` domains) is common to both versions.

Internal transfers
------------------
Internal transfers (operations of type ``internal``) only move goods between
the company's own stock locations: the total quantity on hand must stay
unchanged. StockSense therefore:

* resolves the warehouse of the source and destination locations
  (``internal_source_warehouse_id`` / ``internal_dest_warehouse_id``) and
  flags warehouse to warehouse movements (``internal_is_cross_warehouse``);
* exposes the live quantity available at the source location
  (``internal_source_available_qty``);
* computes a signed ``operation_stock_impact`` (also available per move on
  ``stock.move``), which is exactly zero when stock only moves between two
  stock locations, and refuses operations mixing vendor/customer locations
  with an internal transfer.

The ``stock.move`` extension at the bottom of this file only feeds the
StockSense *Move History* ledger (operation type and signed stock impact).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

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

#: Location usages holding the company's own stock. A movement changes the
#: quantity on hand only when exactly one of its two locations is in this set,
#: which is what makes an internal transfer impact-free.
INTERNAL_LOCATION_USAGES = ('internal', 'transit')

#: Human labels used in the internal transfer validation messages.
LOCATION_USAGE_LABELS = {
    'supplier': 'vendor',
    'customer': 'customer',
    'inventory': 'inventory loss',
    'production': 'production',
    'view': 'view',
    'internal': 'internal',
    'transit': 'transit',
}


def get_done_quantity_field(model, candidates=('quantity', 'quantity_done')):
    """Name of the field holding the processed quantity of ``model``.

    That field has been renamed in Odoo 17.0: ``stock.move.quantity_done``
    became ``quantity`` and ``stock.move.line.qty_done`` became ``quantity``.
    Resolving the name at runtime keeps the module installable on 16.0 and
    17.0, whereas an ``@api.depends`` on a renamed field would break one of
    the two versions.

    :param model: a model or a recordset, e.g. ``self.env['stock.move']``.
    :param candidates: field names to try, most recent version first.
    :return: the first candidate that exists on the model.
    """
    for field_name in candidates:
        if field_name in model._fields:
            return field_name
    return candidates[-1]


class StockPicking(models.Model):
    """Add the StockSense operation abstraction on top of pickings.

    Covers the three daily operations (receipts, deliveries and internal
    transfers) and adds the helpers required to run internal transfers
    safely, i.e. without ever changing the total quantity on hand.
    """

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
        digits='Product Unit of Measure',
        help="Total quantity already processed for this operation. Read live "
             "from the moves: the field holding it is named 'quantity_done' "
             "on Odoo 16.0 and 'quantity' on Odoo 17.0.",
    )
    operation_progress = fields.Float(
        string='Progress',
        compute='_compute_operation_metrics',
        digits=(16, 2),
        help="Percentage of the demanded quantity already processed "
             "(0 - 100). Read live from the moves.",
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
    # Internal transfers
    # ------------------------------------------------------------------
    internal_source_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Source Warehouse',
        compute='_compute_internal_warehouses',
        store=True,
        help="Warehouse the source location belongs to. Empty for locations "
             "that are not part of a warehouse, such as vendor or customer "
             "locations.",
    )
    internal_dest_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Destination Warehouse',
        compute='_compute_internal_warehouses',
        store=True,
        help="Warehouse the destination location belongs to.",
    )
    internal_is_cross_warehouse = fields.Boolean(
        string='Warehouse to Warehouse',
        compute='_compute_internal_warehouses',
        store=True,
        help="Set when the source and the destination locations belong to two "
             "different warehouses.",
    )
    internal_source_available_qty = fields.Float(
        string='Available at Source',
        compute='_compute_internal_source_availability',
        digits='Product Unit of Measure',
        help="Quantity of the transferred products currently available (on "
             "hand minus reserved) at the source location and its "
             "sub-locations. Read live from the quants.",
    )
    operation_stock_impact = fields.Float(
        string='Stock Impact',
        compute='_compute_operation_stock_impact',
        digits='Product Unit of Measure',
        help="Signed effect of the operation on the quantity on hand: "
             "positive when goods enter the stock (receipt), negative when "
             "they leave it (delivery) and zero for an internal transfer, "
             "which only moves goods between two stock locations. Read live "
             "from the moves.",
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

    @api.depends('move_ids', 'move_ids.product_uom_qty', 'move_ids.state')
    def _compute_operation_metrics(self):
        """Aggregate the demand, the processed quantity and the progress.

        The processed quantity is read through
        :func:`get_done_quantity_field` because Odoo 17.0 renamed
        ``stock.move.quantity_done`` to ``quantity``. The fields derived from
        it are computed live instead of being stored, so their value is
        always accurate on both versions.
        """
        done_qty_field = get_done_quantity_field(self.env['stock.move'])
        for picking in self:
            moves = picking.move_ids.filtered(lambda move: move.state != 'cancel')
            demand_qty = sum(moves.mapped('product_uom_qty'))
            done_qty = sum(moves.mapped(done_qty_field))
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

    @api.depends('location_id', 'location_dest_id')
    def _compute_internal_warehouses(self):
        """Resolve the warehouse of the source and destination locations.

        A warehouse owns every location below its view location, so the
        lookup is done with a single ``parent_of`` search for the whole
        recordset instead of one query per record.
        """
        Warehouse = self.env['stock.warehouse']
        locations = self.mapped('location_id') | self.mapped('location_dest_id')
        warehouse_by_location = {}
        if locations:
            warehouses = Warehouse.search(
                [('view_location_id', 'parent_of', locations.ids)])
            for warehouse in warehouses:
                root_path = warehouse.view_location_id.parent_path or ''
                for location in locations:
                    location_path = location.parent_path or ''
                    if not root_path or not location_path.startswith(root_path):
                        continue
                    known = warehouse_by_location.get(location.id)
                    # deepest matching view location wins for nested setups
                    if not known or len(root_path) > len(
                            known.view_location_id.parent_path or ''):
                        warehouse_by_location[location.id] = warehouse
        empty = Warehouse.browse()
        for picking in self:
            source = warehouse_by_location.get(picking.location_id.id, empty)
            destination = warehouse_by_location.get(
                picking.location_dest_id.id, empty)
            picking.internal_source_warehouse_id = source
            picking.internal_dest_warehouse_id = destination
            picking.internal_is_cross_warehouse = bool(
                source and destination and source != destination)

    @api.depends('location_id', 'move_ids.product_id',
                 'move_ids.product_uom_qty')
    def _compute_internal_source_availability(self):
        """Quantity of the operation's products available at the source."""
        Quant = self.env['stock.quant']
        for picking in self:
            products = picking.move_ids.product_id
            if not picking.location_id or not products:
                picking.internal_source_available_qty = 0.0
                continue
            quants = Quant.search([
                ('location_id', 'child_of', picking.location_id.id),
                ('product_id', 'in', products.ids),
            ])
            picking.internal_source_available_qty = (
                sum(quants.mapped('quantity'))
                - sum(quants.mapped('reserved_quantity')))

    @api.depends('move_ids', 'move_ids.state', 'move_ids.location_id',
                 'move_ids.location_dest_id')
    def _compute_operation_stock_impact(self):
        """Sum the signed impact of the operation's moves.

        The per-move impact (see ``stock.move`` below) is derived from the
        location types, so an internal transfer between two stock locations
        always ends up with a zero impact.
        """
        for picking in self:
            picking.operation_stock_impact = sum(
                picking.move_ids.mapped('operation_stock_impact'))

    @api.constrains('location_id', 'location_dest_id', 'picking_type_id')
    def _check_internal_transfer_locations(self):
        """Keep internal transfers inside the company's own stock.

        An internal transfer must move goods from one stock location to a
        *different* one, without involving vendor or customer locations,
        otherwise the total quantity on hand would change.
        """
        for picking in self:
            if picking.picking_type_id.code != 'internal':
                continue
            if picking.location_id == picking.location_dest_id:
                raise ValidationError(_(
                    "The source and destination locations of the internal "
                    "transfer %s must be different.") % picking.display_name)
            for location in picking.location_id | picking.location_dest_id:
                if location.usage in ('supplier', 'customer'):
                    raise ValidationError(_(
                        "The internal transfer %s cannot use the %s location "
                        "%s: an internal transfer only moves stock between "
                        "stock locations, so it must never change the total "
                        "quantity on hand.") % (
                            picking.display_name,
                            LOCATION_USAGE_LABELS.get(
                                location.usage, location.usage),
                            location.display_name))

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
        # Always return exactly one entry per declared badge: the summary is a
        # fixed-size contract for the dashboard. One count per status is used
        # on purpose: _read_group (Odoo 17.0) and read_group (Odoo 16.0) do
        # not share the same signature.
        summary = {}
        for operation_state, _label in OPERATION_STATES:
            summary[operation_state] = self.search_count(
                domain + [('operation_state', '=', operation_state)])
        return summary


class StockMove(models.Model):
    """Ledger fields added to stock moves for the Move History views.

    Kept on ``stock.move`` so the ledger can be searched, filtered and
    grouped by StockSense values without any Python helper.
    """

    _inherit = 'stock.move'

    operation_type = fields.Selection(
        selection=OPERATION_TYPES,
        string='Operation Type',
        related='picking_id.operation_type',
        store=True,
        index=True,
        help="StockSense category of the operation this move belongs to: "
             "Receipt, Delivery or Internal Transfer.",
    )
    operation_stock_impact = fields.Float(
        string='Stock Impact',
        compute='_compute_operation_stock_impact',
        digits='Product Unit of Measure',
        help="Signed effect of the movement on the quantity on hand: "
             "negative when the goods leave the stock, positive when they "
             "enter it and zero when the goods only move from one stock "
             "location to another (internal transfers). "
             "Uses the same logic as the native movement analysis.",
    )
    operation_done_qty = fields.Float(
        string='Processed',
        compute='_compute_operation_done_qty',
        digits='Product Unit of Measure',
        help="Processed quantity of the movement, exposed by StockSense so "
             "that views, reports and the move history ledger work on Odoo "
             "16.0 ('quantity_done') and 17.0 ('quantity') alike.",
    )

    @api.depends('location_id.usage', 'location_dest_id.usage', 'state')
    def _compute_operation_stock_impact(self):
        """Derive the signed stock impact from the nature of the locations."""
        done_qty_field = get_done_quantity_field(self.env['stock.move'])
        for move in self:
            quantity = move[done_qty_field]
            source_in_stock = (
                move.location_id.usage in INTERNAL_LOCATION_USAGES)
            destination_in_stock = (
                move.location_dest_id.usage in INTERNAL_LOCATION_USAGES)
            if source_in_stock and not destination_in_stock:
                move.operation_stock_impact = -quantity
            elif destination_in_stock and not source_in_stock:
                move.operation_stock_impact = quantity
            else:
                # both sides hold stock (internal transfer) or neither does
                move.operation_stock_impact = 0.0

    @api.depends('state', 'product_uom_qty')
    def _compute_operation_done_qty(self):
        """Read the processed quantity under its version specific name."""
        done_qty_field = get_done_quantity_field(self.env['stock.move'])
        for move in self:
            move.operation_done_qty = move[done_qty_field]


class StockMoveLine(models.Model):
    """Expose the processed quantity of a move line under a stable name."""

    _inherit = 'stock.move.line'

    operation_done_qty = fields.Float(
        string='Processed',
        compute='_compute_operation_done_qty',
        digits='Product Unit of Measure',
        help="Processed quantity of the detailed operation, exposed by "
             "StockSense so that the views work on Odoo 16.0 ('qty_done') and "
             "17.0 ('quantity') alike.",
    )

    @api.depends('move_id', 'move_id.state')
    def _compute_operation_done_qty(self):
        """Read the processed quantity under its version specific name."""
        done_qty_field = get_done_quantity_field(
            self.env['stock.move.line'], ('quantity', 'qty_done'))
        for line in self:
            line.operation_done_qty = line[done_qty_field]
