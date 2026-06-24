# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

MAX_SHIFTS = 5


class HrDepartmentType(models.Model):
    _inherit = 'hr.department.type'

    shift_ids = fields.One2many(
        'attendance.shift.config',
        'department_type_id',
        string='Shifts',
    )
    shift_count = fields.Integer(
        string='Shifts', compute='_compute_shift_count',
    )

    @api.depends('shift_ids')
    def _compute_shift_count(self):
        for rec in self:
            rec.shift_count = len(rec.shift_ids)

    @api.constrains('shift_ids')
    def _check_max_shifts(self):
        for rec in self:
            if len(rec.shift_ids) > MAX_SHIFTS:
                raise ValidationError(_(
                    "A department type can have at most %(max)s shifts "
                    "(\"%(name)s\" has %(n)s).",
                    max=MAX_SHIFTS, name=rec.name, n=len(rec.shift_ids),
                ))
