# -*- coding: utf-8 -*-
from odoo import models, fields, api

# Fallback used before the catalog is populated (install / empty table).
# Keys here MUST match the codes seeded in data/department_type_data.xml so
# existing stored values (siege/agence/warehouse/aeroport) keep validating.
DEFAULT_DEPARTMENT_TYPES = [
    ('siege',     'Siège'),
    ('agence',    'Agence'),
    ('warehouse', 'Warehouse'),
    ('aeroport',  'Aéroport'),
]


class HrDepartmentType(models.Model):
    _name        = 'hr.department.type'
    _description = 'Department Type'
    _order       = 'sequence, name'

    name     = fields.Char(string='Name', required=True, translate=True)
    code     = fields.Char(
        string='Code',
        required=True,
        help='Technical value stored on employees / shifts / attendance. '
             'Use lowercase, no spaces (e.g. "siege"). Changing it after '
             'records exist will orphan those records.',
    )
    sequence = fields.Integer(default=10)
    active   = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_uniq', 'UNIQUE(code)', 'Department type code must be unique.'),
    ]

    # ── Invalidate caches when the catalog changes ───────────────────────────
    # department_type is a dynamic (function) Selection; clearing the registry
    # cache makes the new option available to any fresh page load / other user
    # without restarting. An already-open browser tab still needs a reload (F5).
    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        self.env.registry.clear_cache()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res

    @api.model
    def get_selection(self):
        """Dynamic selection list for every department_type field.

        Returns DB-defined types, falling back to the hardcoded defaults when
        the table is empty or not yet created (during install/upgrade).

        The search is wrapped in a SAVEPOINT: if the table does not exist yet
        (module not upgraded), the failing query is rolled back cleanly instead
        of poisoning the surrounding transaction."""
        options = []
        try:
            with self.env.cr.savepoint(flush=False):
                recs = self.sudo().search([])
                options = [(r.code, r.name) for r in recs if r.code]
        except Exception:
            options = []
        if options:
            return options
        return list(DEFAULT_DEPARTMENT_TYPES)

    @api.model
    def get_label_map(self):
        """code -> label dict, for building display strings in code."""
        return dict(self.get_selection())
