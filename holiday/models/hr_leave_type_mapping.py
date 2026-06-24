# -*- coding: utf-8 -*-
from odoo import models, fields

class HrLeaveType(models.Model):
    _inherit = "hr.leave.type"

    x_timeoff_category = fields.Selection(
        [
            ("payee", "Payée"),
            ("impayee", "Impayée"),
            ("maladie", "Maladie"),
            ("autres", "Autres"),
        ],
        string="Catégorie (Mapping)",
        required=True,
        default="payee",
        copy=False,
        help="Catégorie used to map hr.leave.x_timeoff_category to the correct Leave Type.",
    )