# -*- coding: utf-8 -*-
from odoo import models, api


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _update_solde_conge(self, new_value):
        """Bypass method to update solde_conge with sudo privileges"""
        self.ensure_one()
        self.sudo().write({"solde_conge": new_value})
        return True