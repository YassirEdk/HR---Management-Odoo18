# -*- coding: utf-8 -*-
from odoo import models, fields, api


class TotalSoldeCongeWizard(models.TransientModel):
    _name        = 'total.solde.conge.wizard'
    _description = 'Total Solde Congé'
    _rec_name    = 'display_name'

    display_name = fields.Char(
        string="Titre",
        default='Total Solde Congé',
        readonly=True,
    )

    total_solde_conge = fields.Float(
        string="Total Solde Congé",
        readonly=True,
        default=0.0,
    )
    employee_count = fields.Integer(
        string="Nombre d'employés",
        readonly=True,
        default=0,
    )
    average_solde = fields.Float(
        string="Moyenne par employé",
        readonly=True,
        default=0.0,
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        'total_solde_conge_wizard_employee_rel',
        'wizard_id',
        'employee_id',
        string="Employés",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Load all employees ordered by solde descending
        employees = self.env['hr.employee'].sudo().search(
            [], order='solde_conge desc'
        )
        total = sum(employees.mapped('solde_conge'))
        count = len(employees)
        res['display_name']      = 'Total Solde Congé'
        res['employee_ids']      = [(6, 0, employees.ids)]
        res['total_solde_conge'] = total
        res['employee_count']    = count
        res['average_solde']     = round(total / count, 1) if count else 0.0
        return res