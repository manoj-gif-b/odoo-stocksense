"""Standalone validation for the StockSense stock_operations module.

Runs without an Odoo server: the parts of the framework used by the module are
stubbed in-process, the real model file is imported, and its compute methods
are executed against fake records / recordsets.  A regression suite covers the
already delivered behaviour and a new suite covers the internal transfer logic.
"""
import datetime
import importlib.util
import os
import sys
import types

# ---------------------------------------------------------------------------
# In-process stubs for odoo, odoo.api, odoo.fields, odoo.models, odoo.exceptions
# ---------------------------------------------------------------------------
odoo = types.ModuleType('odoo')
api = types.ModuleType('odoo.api')
fields = types.ModuleType('odoo.fields')
models = types.ModuleType('odoo.models')
exceptions = types.ModuleType('odoo.exceptions')


class _Field:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.comodel_name = args[0] if args else kwargs.get('comodel_name')
        self.kwargs = kwargs

    def __repr__(self):
        return '<Field %s>' % self.kwargs.get('string', '')


for _name in ('Selection', 'Boolean', 'Integer', 'Float', 'Char', 'Text',
              'Many2one'):
    setattr(fields, _name, type(_name, (_Field,), {}))


class Datetime(_Field):
    @staticmethod
    def now():
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


fields.Datetime = Datetime
fields._Field = _Field


def depends(*args, **kwargs):
    def decorate(func):
        func._depends = args
        return func
    return decorate


def constrains(*args, **kwargs):
    def decorate(func):
        func._constrains = args
        return func
    return decorate


def model(func):
    func._api_model = True
    return func


api.depends = depends
api.constrains = constrains
api.model = model


class BaseModel:
    _inherit = None
    _name = None


class Model(BaseModel):
    pass


models.BaseModel = BaseModel
models.Model = Model


class ValidationError(Exception):
    pass


class UserError(Exception):
    pass


exceptions.ValidationError = ValidationError
exceptions.UserError = UserError
odoo._ = lambda text, *args, **kwargs: text
odoo.api = api
odoo.fields = fields
odoo.models = models
odoo.exceptions = exceptions
for _name, _module in (('odoo', odoo), ('odoo.api', api), ('odoo.fields', fields),
                       ('odoo.models', models), ('odoo.exceptions', exceptions)):
    sys.modules[_name] = _module

MODULE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'stock_operations', 'models', 'stock_operations.py')
_spec = importlib.util.spec_from_file_location('stock_operations', MODULE_FILE)
operations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(operations)

StockPicking = operations.StockPicking
StockMove = operations.StockMove


def check(label, condition):
    assert condition, 'FAILED: %s' % label


PICKING_FIELDS = {name for name, value in vars(StockPicking).items()
                  if isinstance(value, fields._Field)}
MOVE_FIELDS = {name for name, value in vars(StockMove).items()
               if isinstance(value, fields._Field)}
print('  OK  model loaded: %d stock.picking fields, %d stock.move fields'
      % (len(PICKING_FIELDS), len(MOVE_FIELDS)))

# ---------------------------------------------------------------------------
# Fake record / recordset layer (only the API subset the computes use)
# ---------------------------------------------------------------------------
class FakeRecordset(list):
    """Recordset look-alike: union, attribute access, mapped, ids and env."""

    @property
    def env(self):
        return FAKE_ENV

    def __or__(self, other):
        merged = []
        for record in list(self) + list(other):
            if record not in merged:
                merged.append(record)
        return FakeRecordset(merged)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return FakeRecordset([getattr(record, name) for record in self])

    @property
    def ids(self):
        return [record.id for record in self]

    def mapped(self, field):
        return FakeRecordset([getattr(record, field) for record in self])


class FakeRecord:
    def __init__(self, id, **values):
        self.id = id
        for key, value in values.items():
            setattr(self, key, value)

    def __or__(self, other):
        return FakeRecordset([self]) | FakeRecordset([other])

    def __repr__(self):
        return '<FakeRecord %s>' % self.id


class FakeMoves(FakeRecordset):
    def filtered(self, func):
        return FakeMoves([move for move in self if func(move)])


class FakeModel:
    def __init__(self, results=()):
        self.results = list(results)
        self.search_calls = []

    def search(self, domain, limit=None):
        self.search_calls.append((domain, limit))
        return FakeRecordset(self.results)

    def browse(self, ids=()):
        return FakeRecordset()


class FakeEnv:
    def __init__(self, warehouses=(), quants=()):
        self._models = {
            'stock.warehouse': FakeModel(warehouses),
            'stock.quant': FakeModel(quants),
        }

    def __getitem__(self, name):
        return self._models[name]


FAKE_ENV = FakeEnv()


def set_env(env):
    global FAKE_ENV
    FAKE_ENV = env
    return env


# ---------------------------------------------------------------------------
# 1. Field declarations of both models are wired to existing methods
# ---------------------------------------------------------------------------
check('StockPicking inherits stock.picking',
      StockPicking._inherit == 'stock.picking')
check('StockMove inherits stock.move', StockMove._inherit == 'stock.move')
for declared, owner in ((PICKING_FIELDS, StockPicking), (MOVE_FIELDS, StockMove)):
    for name in sorted(declared):
        compute = getattr(owner, name).kwargs.get('compute')
        if compute:
            check('%s.%s -> %s()' % (owner._inherit, name, compute),
                  callable(getattr(owner, compute, None)))
print('  OK  every compute hook of both models resolves')

check('internal transfer fields declared', {
    'internal_source_warehouse_id', 'internal_dest_warehouse_id',
    'internal_is_cross_warehouse', 'internal_source_available_qty',
    'operation_stock_impact'} <= PICKING_FIELDS)
check('ledger fields declared on stock.move',
      MOVE_FIELDS == {'operation_type', 'operation_stock_impact'})
move_type_field = getattr(StockMove, 'operation_type').kwargs
check('move operation_type is a stored related field',
      move_type_field['related'] == 'picking_id.operation_type'
      and move_type_field['store'] is True)
check('stock impact stored on both models',
      getattr(StockMove, 'operation_stock_impact').kwargs['store'] is True
      and getattr(StockPicking, 'operation_stock_impact').kwargs['store'] is True)
constrains = getattr(StockPicking, '_check_internal_transfer_locations')
check('internal transfer constraint declared',
      constrains._constrains == ('location_id', 'location_dest_id',
                                 'picking_type_id'))
print('  OK  internal transfer and ledger fields are declared as designed')

for owner in (StockPicking, StockMove):
    for name, value in vars(owner).items():
        if isinstance(value, fields.Many2one):
            check('%s.%s declares its comodel' % (owner._inherit, name),
                  isinstance(value.comodel_name, str) and value.comodel_name)
print('  OK  every Many2one field declares an explicit comodel')

# ---------------------------------------------------------------------------
# Helpers to build fake pickings / locations
# ---------------------------------------------------------------------------
_counter = [0]


def fake_id():
    _counter[0] += 1
    return _counter[0]


def location(usage='internal', parent_path='1/', name=None):
    record = FakeRecord(fake_id(), usage=usage, parent_path=parent_path)
    record.display_name = name or 'Location %s' % record.id
    return record


def build_picking(code='internal', source=None, destination=None, moves=(),
                  state='assigned'):
    picking = FakeRecord(fake_id(), display_name='WH/INT/00001', state=state)
    picking.picking_type_id = FakeRecord(fake_id(), code=code)
    picking.location_id = source or location('internal', '1/2/', 'WH/Stock')
    picking.location_dest_id = destination or location('internal', '4/5/', 'WH2/Stock')
    picking.move_ids = FakeMoves(list(moves))
    return picking


# ---------------------------------------------------------------------------
# 2. Regression: classification, metrics, lateness, dashboard summary
# ---------------------------------------------------------------------------
for code, expected in (('incoming', 'receipt'), ('outgoing', 'delivery'),
                       ('internal', 'internal'), (None, False)):
    picking = build_picking(code)
    StockPicking._compute_operation_type(FakeRecordset([picking]))
    check('type %r -> %r' % (code, expected), picking.operation_type == expected)

for state, badge in (('draft', 'draft'), ('confirmed', 'waiting'),
                     ('waiting', 'waiting'), ('assigned', 'ready'),
                     ('done', 'done'), ('cancel', 'cancelled')):
    picking = build_picking('internal', state=state)
    StockPicking._compute_operation_state(FakeRecordset([picking]))
    check('state %s -> %s' % (state, badge), picking.operation_state == badge)

moves = [
    FakeRecord(fake_id(), quantity=3.0, product_uom_qty=5.0, state='assigned'),
    FakeRecord(fake_id(), quantity=2.0, product_uom_qty=5.0, state='assigned'),
    FakeRecord(fake_id(), quantity=0.0, product_uom_qty=10.0, state='cancel'),
]
picking = build_picking('internal', moves=moves)
StockPicking._compute_operation_metrics(FakeRecordset([picking]))
check('metrics unchanged',
      (picking.operation_move_count, picking.operation_demand_qty,
       picking.operation_done_qty, picking.operation_progress)
      == (2, 10.0, 5.0, 50.0))

now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
for label, state, days, done_days, late, delay in (
        ('open overdue', 'assigned', -3.05, None, True, 3),
        ('open future', 'confirmed', 2, None, False, 0),
        ('done late', 'done', -5, -2, True, 3),
        ('cancelled', 'cancel', -9, None, False, 0)):
    picking = build_picking('internal', state=state)
    picking.scheduled_date = (
        now + datetime.timedelta(days=days) if days is not None else False)
    picking.date_done = (
        now + datetime.timedelta(days=done_days) if done_days is not None else False)
    StockPicking._compute_operation_delay(FakeRecordset([picking]))
    check('%s -> late=%s delay=%s' % (label, late, delay),
          picking.operation_is_late is late
          and picking.operation_delay_days == delay)


class FakeEnvModel:
    def __init__(self):
        self.calls = []

    def _read_group(self, domain, groupby, aggregates):
        self.calls.append((domain, groupby, aggregates))
        return [('draft', 2), (False, 1)]


fake = FakeEnvModel()
summary = StockPicking.get_operation_state_summary(fake, ['receipt'])
check('summary unchanged',
      set(summary) == set(dict(operations.OPERATION_STATES))
      and summary['draft'] == 2 and summary['done'] == 0 and False not in summary
      and fake.calls[0][0] == [('operation_type', 'in', ['receipt'])])
print('  OK  no regression on classification, metrics, lateness and summary')

# ---------------------------------------------------------------------------
# 3. Internal transfers: warehouse detection
# ---------------------------------------------------------------------------
WH_VIEW = location('view', '1/', 'WH')
WH_STOCK = location('internal', '1/2/', 'WH/Stock')
WH_OUTPUT = location('internal', '1/3/', 'WH/Output')
WH2_VIEW = location('view', '4/', 'WH2')
WH2_STOCK = location('internal', '4/5/', 'WH2/Stock')
VENDORS = location('supplier', '6/', 'Partner Locations/Vendors')
CUSTOMERS = location('customer', '7/', 'Partner Locations/Customers')
TRANSIT = location('transit', '8/', 'Inter-warehouse transit')
WAREHOUSE = FakeRecord(fake_id(), view_location_id=WH_VIEW)
WAREHOUSE2 = FakeRecord(fake_id(), view_location_id=WH2_VIEW)

env = set_env(FakeEnv(warehouses=[WAREHOUSE, WAREHOUSE2]))
batch = FakeRecordset([
    build_picking('internal', WH_STOCK, WH_OUTPUT),
    build_picking('internal', WH_STOCK, WH2_STOCK),
    build_picking('internal', VENDORS, WH_STOCK),
])
StockPicking._compute_internal_warehouses(batch)
within, cross, receipt = batch
for label, picking, expected_source, expected_dest, expected_cross in (
        ('within warehouse', within, WAREHOUSE, WAREHOUSE, False),
        ('warehouse to warehouse', cross, WAREHOUSE, WAREHOUSE2, True),
        ('receipt from a vendor', receipt, None, WAREHOUSE, False)):
    if expected_source is None:
        check('%s -> no source warehouse' % label,
              not picking.internal_source_warehouse_id)
    else:
        check('%s -> source warehouse' % label,
              picking.internal_source_warehouse_id == expected_source)
    check('%s -> destination warehouse' % label,
          picking.internal_dest_warehouse_id == expected_dest)
    check('%s -> cross warehouse %s' % (label, expected_cross),
          picking.internal_is_cross_warehouse is expected_cross)

search_calls = env['stock.warehouse'].search_calls
check('warehouse lookup is batched: one query for three records',
      len(search_calls) == 1)
domain, limit = search_calls[0]
field, operator, values = domain[0]
check('warehouse lookup walks the view location hierarchy',
      (field, operator) == ('view_location_id', 'parent_of')
      and set(values) == {WH_STOCK.id, WH_OUTPUT.id, WH2_STOCK.id, VENDORS.id})
check('warehouse lookup is unrestricted', limit is None)
print('  OK  warehouses and the cross-warehouse flag are resolved in one query')

# ---------------------------------------------------------------------------
# 4. Internal transfers: availability at the source location
# ---------------------------------------------------------------------------
PRODUCT = FakeRecord(fake_id(), name='Widget')
env = set_env(FakeEnv(warehouses=[WAREHOUSE, WAREHOUSE2], quants=[
    FakeRecord(fake_id(), quantity=10.0, reserved_quantity=3.0),
    FakeRecord(fake_id(), quantity=5.0, reserved_quantity=0.0),
]))
move = FakeRecord(fake_id(), product_id=PRODUCT, product_uom_qty=20.0,
                  quantity=0.0)
picking = build_picking('internal', WH_STOCK, WH2_STOCK, moves=[move])
StockPicking._compute_internal_source_availability(FakeRecordset([picking]))
check('available = on hand - reserved',
      picking.internal_source_available_qty == 12.0)
quant_domain = env['stock.quant'].search_calls[0][0]
check('availability looks into sub-locations',
      ('location_id', 'child_of', WH_STOCK.id) in quant_domain)
check('availability only counts the moved products',
      ('product_id', 'in', [PRODUCT.id]) in quant_domain)

empty_picking = build_picking('internal', WH_STOCK, WH2_STOCK)
StockPicking._compute_internal_source_availability(
    FakeRecordset([empty_picking]))
check('no move -> zero availability',
      empty_picking.internal_source_available_qty == 0.0)

no_source = build_picking('internal', WH_STOCK, WH2_STOCK, moves=[move])
no_source.location_id = None
StockPicking._compute_internal_source_availability(FakeRecordset([no_source]))
check('no source location -> zero availability',
      no_source.internal_source_available_qty == 0.0)
print('  OK  source availability is computed live from the quants')

# ---------------------------------------------------------------------------
# 5. Stock impact: internal transfers never change the stock on hand
# ---------------------------------------------------------------------------
for label, source, destination, quantity, expected in (
        ('receipt', VENDORS, WH_STOCK, 7.0, 7.0),
        ('delivery', WH_STOCK, CUSTOMERS, 4.0, -4.0),
        ('internal within a warehouse', WH_STOCK, WH_OUTPUT, 9.0, 0.0),
        ('internal between warehouses', WH_STOCK, WH2_STOCK, 6.0, 0.0),
        ('internal through transit', WH_STOCK, TRANSIT, 3.0, 0.0),
        ('receipt into transit', VENDORS, TRANSIT, 5.0, 5.0),
        ('vendor to customer (no stock)', VENDORS, CUSTOMERS, 2.0, 0.0)):
    fake_move = FakeRecord(fake_id(), location_id=source,
                           location_dest_id=destination, quantity=quantity)
    StockMove._compute_operation_stock_impact(FakeRecordset([fake_move]))
    check('%s -> impact %s' % (label, expected),
          fake_move.operation_stock_impact == expected)

delivery = build_picking('outgoing', WH_STOCK, CUSTOMERS, moves=[
    FakeRecord(fake_id(), operation_stock_impact=-4.0),
    FakeRecord(fake_id(), operation_stock_impact=-6.0),
])
StockPicking._compute_operation_stock_impact(FakeRecordset([delivery]))
check('operation impact sums its moves',
      delivery.operation_stock_impact == -10.0)

transfer = build_picking('internal', WH_STOCK, WH2_STOCK, moves=[
    FakeRecord(fake_id(), operation_stock_impact=0.0),
    FakeRecord(fake_id(), operation_stock_impact=0.0),
])
StockPicking._compute_operation_stock_impact(FakeRecordset([transfer]))
check('internal transfer impact is exactly zero',
      transfer.operation_stock_impact == 0.0)
print('  OK  internal transfers are impact-free, receipts/deliveries are signed')

# ---------------------------------------------------------------------------
# 6. Internal transfers: the locations must stay inside our own stock
# ---------------------------------------------------------------------------
def expect_error(label, picking):
    try:
        StockPicking._check_internal_transfer_locations(
            FakeRecordset([picking]))
    except ValidationError as error:
        check('%s -> explanatory message' % label,
              'internal transfer' in str(error))
        return str(error)
    raise AssertionError('FAILED: %s should have raised ValidationError' % label)


message = expect_error('same source and destination',
                       build_picking('internal', WH_STOCK, WH_STOCK))
check('message states the two-location rule', 'must be different' in message)
message = expect_error('vendor source',
                       build_picking('internal', VENDORS, WH_STOCK))
check('message names the vendor location', 'vendor location' in message)
expect_error('customer destination',
             build_picking('internal', WH_STOCK, CUSTOMERS))

for label, code, source, destination in (
        ('internal transfer', 'internal', WH_STOCK, WH2_STOCK),
        ('internal through transit', 'internal', WH_STOCK, TRANSIT),
        ('receipt from a vendor', 'incoming', VENDORS, WH_STOCK),
        ('delivery to a customer', 'outgoing', WH_STOCK, CUSTOMERS)):
    StockPicking._check_internal_transfer_locations(
        FakeRecordset([build_picking(code, source, destination)]))
print('  OK  the constraint blocks same-location and vendor/customer transfers')

print('\nAll StockSense stock_operations checks passed.')
