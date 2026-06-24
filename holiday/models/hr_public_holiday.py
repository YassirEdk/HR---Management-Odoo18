# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class HrPublicHoliday(models.Model):
    _name        = 'hr.public.holiday'
    _description = 'Jour Férié Marocain'
    _order       = 'date asc'

    name = fields.Char(string="Nom du jour férié", required=True)
    date = fields.Date(string="Date", required=True)
    description = fields.Char(string="Description")
    active = fields.Boolean(string="Actif", default=True)

    _sql_constraints = [
        ('unique_date', 'UNIQUE(date)',
         'Un jour férié existe déjà pour cette date.'),
    ]

    @api.model
    def get_holidays_for_dates(self, date_list):
        """
        Given a list of date objects, return a dict {date: holiday_name}
        for any that fall on a public holiday.
        """
        if not date_list:
            return {}
        holidays = self.search([
            ('date', 'in', date_list),
            ('active', '=', True),
        ])
        return {h.date: h.name for h in holidays}