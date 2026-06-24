# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrLeave(models.Model):

    _inherit  = "hr.leave"
    _rec_name = "x_ui_title"

    # =========================================================================
    # CONSTANTS
    # =========================================================================

    DECES_QUOTA   = 3.0
    MARIAGE_QUOTA = 4.0

    # =========================================================================
    # FIELD DEFINITIONS
    # =========================================================================

    request_date_from = fields.Date(required=False)
    request_date_to   = fields.Date(required=False)

    state = fields.Selection(
        selection_add=[("unconfirmed", "Unconfirmed")],
        ondelete={"unconfirmed": "set default"},
    )

    x_plage_complete = fields.Boolean(string="Ajouter une plage complète", default=False, copy=False)
    x_demie_journee  = fields.Boolean(string="Ajouter une demie journée",  default=False, copy=False)

    x_date_debut = fields.Date(string="Date début", copy=False)
    x_date_fin   = fields.Date(string="Date fin",   copy=False)

    x_demie_journee_date = fields.Date(string="Date (demie journée)", copy=False)
    x_time_from          = fields.Float(string="From (Hour)", copy=False, default=False)
    x_time_to            = fields.Float(string="To (Hour)",   copy=False, default=False)

    x_timeoff_category = fields.Selection(
        [("payee", "Payée"), ("impayee", "Impayée"), ("maladie", "Maladie"), ("autres", "Autres")],
        string="Type", required=True, default="payee", copy=False,
    )

    x_conge_maternite = fields.Boolean(string="Congé Maternité",  default=False, copy=False)
    x_conge_deces     = fields.Boolean(string="Congé Décès",      default=False, copy=False)
    x_conge_mariage   = fields.Boolean(string="Congé de Mariage", default=False, copy=False)

    x_maternite_date_debut = fields.Date(string="Date de début (Maternité)", copy=False)
    x_maternite_date_fin = fields.Date(
        string="Date de fin (Maternité)",
        compute="_compute_maternite_date_fin", store=True, readonly=True, copy=False,
    )
    x_mat_male_date_from   = fields.Date(string="Début maternité (homme)", copy=False)
    x_mat_male_date_to     = fields.Date(string="Fin maternité (homme)",   copy=False)
    x_mat_quota_window_end = fields.Date(
        compute="_compute_mat_quota_window", store=False, readonly=True,
        string="Fin fenêtre quota maternité",
    )

    x_mariage_date_debut = fields.Date(string="Date de début (Mariage)", copy=False)
    x_mariage_date_fin = fields.Date(
        string="Date de fin (Mariage)",
        compute="_compute_mariage_date_fin", store=True, readonly=True, copy=False,
    )

    x_mariage_days_stored = fields.Float(
        string="Jours mariage consommés", default=0.0, copy=False, readonly=True,
    )
    x_mariage_days_used = fields.Float(
        string="Jours mariage déjà utilisés",
        compute="_compute_mariage_days_used", store=False, readonly=True,
    )
    x_mariage_days_remaining = fields.Float(
        string="Jours mariage restants",
        compute="_compute_mariage_days_used", store=False, readonly=True,
    )

    x_for_other_employee = fields.Boolean(
        string="Congé pour un autre employé", default=False, copy=False,
    )
    x_target_employee_id = fields.Many2one(
        'hr.employee', string="Employé concerné", copy=False,
    )

    x_remplacant_id = fields.Many2one(
        'hr.employee', string='Remplaçant', copy=False,
        domain="[('department_id', '=', department_id), ('id', '!=', employee_id)]",
    )

    solde_conge = fields.Float(
        string="Solde congé",
        compute="_compute_solde_conge",
        compute_sudo=True, store=False, readonly=True,
    )
    x_deducted_from_solde = fields.Boolean(default=False, copy=False, readonly=True)
    x_deducted_days       = fields.Float(default=0.0,   copy=False, readonly=True)

    x_employee_confirmed = fields.Boolean(
        string="Employee Confirmed", default=False, copy=False, readonly=True,
    )

    x_public_holiday_info = fields.Char(
        compute="_compute_public_holiday_info",
        store=False, readonly=True,
    )

    x_less_than_6_months = fields.Boolean(
        compute="_compute_less_than_6_months",
        store=False,
    )

    x_maternite_allocated_days = fields.Float(
        compute="_compute_maternite_breakdown", store=False, readonly=True,
    )
    x_maternite_from_solde = fields.Float(
        compute="_compute_maternite_breakdown", store=False, readonly=True,
    )
    x_maternite_total_days = fields.Float(
        compute="_compute_maternite_breakdown", store=False, readonly=True,
    )
    x_maternite_gender = fields.Selection(
        selection=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
        compute="_compute_maternite_gender",
        store=False, readonly=True,
        string="Genre employé (maternité)",
    )
    x_maternite_free_days = fields.Float(
        compute="_compute_maternite_breakdown", store=False, readonly=True,
    )

    x_maternite_days_stored = fields.Float(
        string="Jours maternité consommés", default=0.0, copy=False, readonly=True,
    )
    x_maternite_days_used = fields.Float(
        string="Jours maternité déjà utilisés",
        compute="_compute_maternite_days_used", store=False, readonly=True,
    )
    x_maternite_days_remaining = fields.Float(
        string="Jours maternité restants",
        compute="_compute_maternite_days_used", store=False, readonly=True,
    )

    x_deces_date_debut = fields.Date(string="Date de début (Décès)", copy=False)
    x_deces_date_fin = fields.Date(
        string="Date de fin (Décès)",
        compute="_compute_deces_date_fin", store=True, readonly=True, copy=False,
    )

    x_deces_days_stored = fields.Float(
        string="Jours décès consommés", default=0.0, copy=False, readonly=True,
    )
    x_deces_days_used = fields.Float(
        string="Jours décès déjà utilisés",
        compute="_compute_deces_days_used", store=False, readonly=True,
    )
    x_deces_days_remaining = fields.Float(
        string="Jours décès restants",
        compute="_compute_deces_days_used", store=False, readonly=True,
    )

    x_days_plus2             = fields.Float(compute="_compute_duration_fields", store=False)
    x_duration_display_plus2 = fields.Char(compute="_compute_duration_fields",  store=False)
    duration_days_display    = fields.Char(
        compute="_compute_duration_fields", store=False, readonly=True,
    )
    show_cancel_button    = fields.Boolean(compute="_compute_show_cancel_button", store=False)
    can_cancel            = fields.Boolean(compute="_compute_can_cancel",         store=False)
    x_ui_title            = fields.Char(compute="_compute_x_ui_title",            store=False)
    x_show_time_fields    = fields.Boolean(compute="_compute_show_time_fields",    store=False)
    x_show_autres_fields  = fields.Boolean(compute="_compute_show_autres_fields",  store=False)
    x_show_plage_complete = fields.Boolean(compute="_compute_show_plage_complete", store=False)
    x_show_maternite      = fields.Boolean(compute="_compute_show_maternite",      store=False)
    x_show_deces          = fields.Boolean(compute="_compute_show_deces",          store=False)
    x_show_mariage        = fields.Boolean(compute="_compute_show_mariage",        store=False)
    x_is_owner            = fields.Boolean(compute="_compute_x_is_owner",          store=False)

    x_type_display = fields.Char(
        string="Type",
        compute="_compute_x_type_display",
        store=False,
        help="Category label, refined with the special sub-type "
             "(Décès / Mariage / Maternité) when the category is Autres.",
    )

    @api.depends("x_timeoff_category", "x_conge_deces",
                 "x_conge_mariage", "x_conge_maternite")
    def _compute_x_type_display(self):
        cat_labels = dict(self._fields["x_timeoff_category"].selection)
        for rec in self:
            label = cat_labels.get(rec.x_timeoff_category,
                                   rec.x_timeoff_category or "")
            if rec.x_timeoff_category == "autres":
                sub = False
                if rec.x_conge_deces:
                    sub = "Décès"
                elif rec.x_conge_mariage:
                    sub = "Mariage"
                elif rec.x_conge_maternite:
                    sub = "Maternité"
                if sub:
                    label = f"{label} - {sub}"
            rec.x_type_display = label

    # =========================================================================
    # UTILITY
    # =========================================================================

    @staticmethod
    def _count_days_with_saturday(date_from, date_to, public_holiday_dates=None):
        if not date_from or not date_to:
            return 0.0
        if hasattr(date_from, "date"): date_from = date_from.date()
        if hasattr(date_to,   "date"): date_to   = date_to.date()
        if date_to < date_from:
            return 0.0
        if public_holiday_dates is None:
            public_holiday_dates = set()
        total = 0.0
        current = date_from
        while current <= date_to:
            if current.weekday() != 6 and current not in public_holiday_dates:
                total += 1.0
            current += timedelta(days=1)
        return total

    @staticmethod
    def _count_all_days(date_from, date_to):
        if not date_from or not date_to:
            return 0.0
        if hasattr(date_from, "date"): date_from = date_from.date()
        if hasattr(date_to,   "date"): date_to   = date_to.date()
        if date_to < date_from:
            return 0.0
        return float((date_to - date_from).days + 1)

    def _get_public_holidays_in_range(self, date_from, date_to):
        if not date_from or not date_to:
            return set()
        if hasattr(date_from, 'date'): date_from = date_from.date()
        if hasattr(date_to,   'date'): date_to   = date_to.date()
        from datetime import datetime as dt, time as dtime
        leaves = self.env['resource.calendar.leaves'].sudo().search([
            ('resource_id', '=', False),
            ('date_from', '<=', dt.combine(date_to,   dtime.max)),
            ('date_to',   '>=', dt.combine(date_from, dtime.min)),
        ])
        holiday_dates = set()
        for leave in leaves:
            d   = leave.date_from.date()
            end = leave.date_to.date()
            while d <= end:
                if date_from <= d <= date_to:
                    holiday_dates.add(d)
                d += timedelta(days=1)
        return holiday_dates

    def _get_maternite_quota(self):
        self.ensure_one()
        return 14.0 if (self.employee_id.gender == 'female') else 3.0

    def _get_maternite_used_by_others(self):
        self.ensure_one()
        if not self.employee_id:
            return 0.0
        ref_date = self.x_mat_male_date_from or self.x_mat_male_date_to
        if not ref_date:
            return 0.0
        from dateutil.relativedelta import relativedelta
        domain_all = [
            ('employee_id',             '=', self.employee_id.id),
            ('x_conge_maternite',       '=', True),
            ('x_timeoff_category',      '=', 'autres'),
            ('state',                   'not in', ['refuse', 'cancel']),
            ('x_maternite_days_stored', '>',  0),
        ]
        if self.id and not isinstance(self.id, models.NewId):
            domain_all.append(('id', '!=', self.id))
        all_others = self.sudo().search(domain_all, order='x_mat_male_date_from asc')
        window_start = None
        for rec in all_others:
            r_date = rec.x_mat_male_date_from or rec.x_mat_male_date_to
            if not r_date:
                continue
            if window_start is None:
                window_start = r_date
            if r_date > window_start + relativedelta(months=9):
                if ref_date > window_start + relativedelta(months=9):
                    window_start = r_date
                else:
                    break
        if window_start is None:
            return 0.0
        window_end = window_start + relativedelta(months=9)
        total = 0.0
        for rec in all_others:
            r_date = rec.x_mat_male_date_from or rec.x_mat_male_date_to
            if r_date and window_start <= r_date <= window_end:
                total += rec.x_maternite_days_stored
        return total

    def _get_deces_used_by_others(self):
        self.ensure_one()
        domain = [
            ('employee_id',         '=', self.employee_id.id),
            ('x_conge_deces',       '=', True),
            ('x_timeoff_category',  '=', 'autres'),
            ('state',               'not in', ['refuse', 'cancel']),
            ('x_deces_days_stored', '>',  0),
        ]
        if self.id:
            domain.append(('id', '!=', self.id))
        return sum(self.sudo().search(domain).mapped('x_deces_days_stored'))

    def _get_mariage_used_by_others(self):
        self.ensure_one()
        domain = [
            ('employee_id',           '=', self.employee_id.id),
            ('x_conge_mariage',       '=', True),
            ('x_timeoff_category',    '=', 'autres'),
            ('state',                 'not in', ['refuse', 'cancel']),
            ('x_mariage_days_stored', '>', 0),
        ]
        if self.id:
            domain.append(('id', '!=', self.id))
        return sum(self.sudo().search(domain).mapped('x_mariage_days_stored'))

    # =========================================================================
    # COMPUTE METHODS
    # =========================================================================

    @api.depends("employee_id")
    def _compute_solde_conge(self):
        for rec in self:
            rec.solde_conge = rec.employee_id.solde_conge if rec.employee_id else 0.0

    @api.depends("employee_id", "x_for_other_employee")
    def _compute_less_than_6_months(self):
        from dateutil.relativedelta import relativedelta
        is_hr = self.env.user.has_group('holiday.group_holiday_hr')
        for rec in self:
            # HR role → never block (can file time off for anyone, any seniority)
            if is_hr:
                rec.x_less_than_6_months = False
                continue
            # HR creating for another employee → never block
            if rec.x_for_other_employee:
                rec.x_less_than_6_months = False
                continue
            if not rec.employee_id or not rec.employee_id.date_embauche:
                rec.x_less_than_6_months = False
                continue
            delta = relativedelta(
                fields.Date.today(),
                rec.employee_id.date_embauche
            )
            total_months = delta.years * 12 + delta.months
            rec.x_less_than_6_months = total_months < 6

    @api.depends("x_plage_complete")
    def _compute_show_plage_complete(self):
        for rec in self:
            rec.x_show_plage_complete = bool(rec.x_plage_complete)

    @api.depends("x_timeoff_category", "x_conge_maternite")
    def _compute_show_maternite(self):
        for rec in self:
            rec.x_show_maternite = (
                rec.x_timeoff_category == "autres" and rec.x_conge_maternite
            )

    @api.depends("x_timeoff_category", "x_conge_deces")
    def _compute_show_deces(self):
        for rec in self:
            rec.x_show_deces = (
                rec.x_timeoff_category == "autres" and rec.x_conge_deces
            )

    @api.depends("x_timeoff_category", "x_conge_mariage")
    def _compute_show_mariage(self):
        for rec in self:
            rec.x_show_mariage = (
                rec.x_timeoff_category == "autres" and rec.x_conge_mariage
            )

    @api.depends("x_mariage_date_debut", "x_conge_mariage", "x_timeoff_category")
    def _compute_mariage_date_fin(self):
        for rec in self:
            is_mariage = rec.x_timeoff_category == "autres" and rec.x_conge_mariage
            if is_mariage and rec.x_mariage_date_debut:
                rec.x_mariage_date_fin = rec.x_mariage_date_debut + timedelta(days=3)
            else:
                rec.x_mariage_date_fin = False

    @api.depends("employee_id", "x_conge_mariage", "x_timeoff_category", "x_mariage_days_stored")
    def _compute_mariage_days_used(self):
        for rec in self:
            is_mariage = rec.x_timeoff_category == "autres" and rec.x_conge_mariage
            if not is_mariage or not rec.employee_id:
                rec.x_mariage_days_used      = 0.0
                rec.x_mariage_days_remaining = 0.0
                continue
            # Mariage quota resets for every marriage event: each request gets
            # the full quota, independent of previous mariage leaves.
            used_current = rec.x_mariage_days_stored
            rec.x_mariage_days_used      = used_current
            rec.x_mariage_days_remaining = max(0.0, self.MARIAGE_QUOTA - used_current)

    @api.depends("employee_id", "create_uid", "x_for_other_employee")
    def _compute_x_is_owner(self):
        for rec in self:
            if self.env.user.has_group('holiday.group_holiday_hr'):
                rec.x_is_owner = True
                continue
            owner_user = rec.employee_id.user_id or rec.create_uid
            rec.x_is_owner = bool(owner_user and owner_user.id == self.env.uid)

    @api.depends("x_timeoff_category", "x_demie_journee")
    def _compute_show_time_fields(self):
        for rec in self:
            rec.x_show_time_fields = (
                rec.x_timeoff_category in ("payee", "impayee", "autres")
                and rec.x_demie_journee
            )

    @api.depends("x_timeoff_category")
    def _compute_show_autres_fields(self):
        for rec in self:
            rec.x_show_autres_fields = rec.x_timeoff_category == "autres"

    @api.depends("x_deces_date_debut", "x_conge_deces", "x_timeoff_category")
    def _compute_deces_date_fin(self):
        for rec in self:
            is_deces = rec.x_timeoff_category == "autres" and rec.x_conge_deces
            if is_deces and rec.x_deces_date_debut:
                rec.x_deces_date_fin = rec.x_deces_date_debut + timedelta(days=2)
            else:
                rec.x_deces_date_fin = False

    @api.depends("x_maternite_date_debut", "x_conge_maternite", "x_timeoff_category", "employee_id")
    def _compute_maternite_date_fin(self):
        for rec in self:
            is_mat    = rec.x_timeoff_category == "autres" and rec.x_conge_maternite
            is_female = rec.employee_id and rec.employee_id.gender == 'female'
            if is_mat and is_female and rec.x_maternite_date_debut:
                rec.x_maternite_date_fin = rec.x_maternite_date_debut + timedelta(days=97)
            else:
                rec.x_maternite_date_fin = False

    @api.depends("employee_id", "x_conge_deces", "x_timeoff_category", "x_deces_days_stored")
    def _compute_deces_days_used(self):
        for rec in self:
            is_deces = rec.x_timeoff_category == "autres" and rec.x_conge_deces
            if not is_deces or not rec.employee_id:
                rec.x_deces_days_used      = 0.0
                rec.x_deces_days_remaining = 0.0
                continue
            # Décès quota resets for every bereavement event: each request
            # gets the full quota, independent of previous décès leaves.
            used_current = rec.x_deces_days_stored
            rec.x_deces_days_used      = used_current
            rec.x_deces_days_remaining = max(0.0, self.DECES_QUOTA - used_current)

    @api.depends("employee_id")
    def _compute_maternite_gender(self):
        for rec in self:
            rec.x_maternite_gender = rec.employee_id.sudo().gender or False

    @api.depends("employee_id", "x_conge_maternite", "x_timeoff_category",
                 "x_mat_male_date_from", "x_maternite_days_stored")
    def _compute_mat_quota_window(self):
        from dateutil.relativedelta import relativedelta
        for rec in self:
            if not (rec.x_timeoff_category == 'autres' and rec.x_conge_maternite
                    and rec.employee_id and rec.employee_id.gender != 'female'):
                rec.x_mat_quota_window_end = False
                continue
            domain = [
                ('employee_id',             '=', rec.employee_id.id),
                ('x_conge_maternite',       '=', True),
                ('x_timeoff_category',      '=', 'autres'),
                ('state',                   'not in', ['refuse', 'cancel']),
                ('x_maternite_days_stored', '>',  0),
            ]
            first = rec.sudo().search(domain, order='x_mat_male_date_from asc', limit=1)
            if first and first.x_mat_male_date_from:
                rec.x_mat_quota_window_end = first.x_mat_male_date_from + relativedelta(months=9)
            else:
                rec.x_mat_quota_window_end = False

    @api.depends(
        "employee_id", "x_conge_maternite", "x_timeoff_category",
        "request_date_from", "request_date_to",
        "x_plage_complete", "x_demie_journee", "x_demie_journee_date",
        "x_mat_male_date_from", "x_mat_male_date_to", "x_maternite_date_debut",
    )
    def _compute_maternite_breakdown(self):
        for rec in self:
            is_mat = rec.x_timeoff_category == "autres" and rec.x_conge_maternite
            if not is_mat:
                rec.x_maternite_allocated_days = 0.0
                rec.x_maternite_from_solde     = 0.0
                rec.x_maternite_total_days     = 0.0
                rec.x_maternite_free_days      = 0.0
                continue
            gender = rec.employee_id.gender if rec.employee_id else False
            quota  = 14.0 if gender == 'female' else 3.0
            used_elsewhere = rec._get_maternite_used_by_others()
            free_remaining = max(0.0, quota - used_elsewhere)
            total     = 0.0
            is_female = gender == 'female'
            if is_female:
                if rec.x_maternite_date_debut:
                    total = 98.0
            else:
                if rec.x_mat_male_date_from and rec.x_mat_male_date_to:
                    total = float((rec.x_mat_male_date_to - rec.x_mat_male_date_from).days + 1)
                elif rec.x_mat_male_date_from:
                    total = 1.0
            if rec.x_demie_journee and rec.x_demie_journee_date and is_female:
                total += 0.5
            allocated  = min(total, free_remaining)
            from_solde = max(0.0, total - free_remaining)
            rec.x_maternite_total_days     = total
            rec.x_maternite_free_days      = quota
            rec.x_maternite_allocated_days = allocated
            rec.x_maternite_from_solde     = from_solde

    @api.depends(
        "employee_id", "x_conge_maternite", "x_timeoff_category",
        "x_maternite_days_stored", "x_mat_male_date_from", "x_mat_male_date_to",
        "x_maternite_date_debut",
    )
    def _compute_maternite_days_used(self):
        for rec in self:
            is_mat = rec.x_timeoff_category == "autres" and rec.x_conge_maternite
            if not is_mat or not rec.employee_id:
                rec.x_maternite_days_used      = 0.0
                rec.x_maternite_days_remaining = 0.0
                continue
            quota       = rec._get_maternite_quota()
            used_others = rec._get_maternite_used_by_others()
            if rec.x_maternite_days_stored:
                used_current = rec.x_maternite_days_stored
            else:
                is_female = rec.employee_id.gender == 'female'
                if is_female:
                    used_current = 98.0 if rec.x_maternite_date_debut else 0.0
                else:
                    if rec.x_mat_male_date_from and rec.x_mat_male_date_to:
                        used_current = float((rec.x_mat_male_date_to - rec.x_mat_male_date_from).days + 1)
                    elif rec.x_mat_male_date_from:
                        used_current = 1.0
                    else:
                        used_current = 0.0
            total_used = used_others + used_current
            remaining  = max(0.0, quota - total_used)
            rec.x_maternite_days_used      = total_used
            rec.x_maternite_days_remaining = remaining

    @api.depends(
        "request_date_from", "request_date_to", "date_from", "date_to",
        "x_plage_complete", "x_demie_journee",
        "x_demie_journee_date", "x_time_from", "x_time_to",
        "x_timeoff_category", "x_conge_maternite", "x_conge_deces", "x_conge_mariage",
        "x_deces_date_debut", "x_mariage_date_debut", "x_maternite_date_debut",
        "x_mat_male_date_from", "x_mat_male_date_to",
        "number_of_days",
    )
    def _compute_duration_fields(self):
        for rec in self:
            days = 0.0

            if rec.x_timeoff_category == "autres" and rec.x_conge_maternite:
                is_female = rec.employee_id and rec.employee_id.gender == 'female'
                if is_female:
                    days = 98.0
                else:
                    if rec.x_mat_male_date_from and rec.x_mat_male_date_to:
                        days = float((rec.x_mat_male_date_to - rec.x_mat_male_date_from).days + 1)
                    elif rec.x_mat_male_date_from:
                        days = 1.0
            elif rec.x_timeoff_category == "autres" and rec.x_conge_deces:
                days = self.DECES_QUOTA
            elif rec.x_timeoff_category == "autres" and rec.x_conge_mariage:
                days = self.MARIAGE_QUOTA
            elif rec.x_plage_complete:
                dfrom = rec.request_date_from or rec.date_from
                dto   = rec.request_date_to   or rec.date_to
                if dfrom and hasattr(dfrom, "date"): dfrom = dfrom.date()
                if dto   and hasattr(dto,   "date"): dto   = dto.date()
                if dfrom and dto and dto >= dfrom:
                    ph_dates = rec._get_public_holidays_in_range(dfrom, dto)
                    days += self._count_days_with_saturday(dfrom, dto, ph_dates)

            is_special = (
                (rec.x_timeoff_category == "autres" and rec.x_conge_deces) or
                (rec.x_timeoff_category == "autres" and rec.x_conge_mariage) or
                (rec.x_timeoff_category == "autres" and rec.x_conge_maternite)
            )
            if rec.x_demie_journee \
                    and rec.x_timeoff_category in ("payee", "impayee", "autres") \
                    and not is_special \
                    and rec.x_demie_journee_date:
                dj = rec.x_demie_journee_date
                if hasattr(dj, 'date'): dj = dj.date()
                dj_ph = rec._get_public_holidays_in_range(dj, dj)
                dj_is_holiday = dj in dj_ph
                if not dj_is_holiday:
                    if rec.x_plage_complete and rec.request_date_from and rec.request_date_to:
                        dfrom = rec.request_date_from
                        dto   = rec.request_date_to
                        if hasattr(dfrom, 'date'): dfrom = dfrom.date()
                        if hasattr(dto,   'date'): dto   = dto.date()
                        if not (dfrom <= dj <= dto):
                            days += 0.5
                    else:
                        days += 0.5

            rec.x_days_plus2 = days
            if days == int(days):
                text = f"{int(days)} {'day' if days == 1 else 'days'}"
            else:
                text = f"{days} days"
            rec.x_duration_display_plus2 = text
            rec.duration_days_display    = text

    @api.depends(
        "request_date_from", "request_date_to",
        "x_plage_complete", "x_demie_journee", "x_demie_journee_date",
        "x_timeoff_category", "x_conge_deces", "x_conge_mariage", "x_conge_maternite",
    )
    def _compute_public_holiday_info(self):
        from datetime import datetime as dt, time as dtime
        for rec in self:
            hits = []

            def _get_holiday_map(date_from, date_to):
                leaves = rec.env['resource.calendar.leaves'].sudo().search([
                    ('resource_id', '=', False),
                    ('date_from', '<=', dt.combine(date_to,   dtime.max)),
                    ('date_to',   '>=', dt.combine(date_from, dtime.min)),
                ])
                result = {}
                for leave in leaves:
                    d   = leave.date_from.date()
                    end = leave.date_to.date()
                    while d <= end:
                        if date_from <= d <= date_to:
                            result[d] = leave.name
                        d += timedelta(days=1)
                return result

            if rec.x_plage_complete and rec.request_date_from and rec.request_date_to:
                dfrom = rec.request_date_from
                dto   = rec.request_date_to
                if hasattr(dfrom, 'date'): dfrom = dfrom.date()
                if hasattr(dto,   'date'): dto   = dto.date()
                holiday_map = _get_holiday_map(dfrom, dto)
                for d in sorted(holiday_map):
                    hits.append(f"{d.strftime('%d/%m/%Y')} : {holiday_map[d]}")

            if rec.x_demie_journee and rec.x_demie_journee_date:
                dj = rec.x_demie_journee_date
                if hasattr(dj, 'date'): dj = dj.date()
                dj_map = _get_holiday_map(dj, dj)
                if dj in dj_map:
                    label = f"{dj.strftime('%d/%m/%Y')} : {dj_map[dj]}"
                    if label not in hits:
                        hits.append(label)

            if hits:
                rec.x_public_holiday_info = "⚠️ Jour(s) férié(s) : " + " | ".join(hits)
            else:
                rec.x_public_holiday_info = False

    @api.depends("state")
    def _compute_show_cancel_button(self):
        for rec in self:
            rec.show_cancel_button = rec.state == "unconfirmed"

    @api.depends("employee_id", "create_uid", "state")
    def _compute_can_cancel(self):
        for rec in self:
            owner_user = rec.employee_id.user_id or rec.create_uid
            rec.can_cancel = bool(
                owner_user and owner_user == self.env.user and rec.state == "unconfirmed"
            )

    @api.depends(
        "employee_id", "x_timeoff_category", "x_days_plus2",
        "x_plage_complete", "x_demie_journee",
        "request_date_from", "request_date_to",
        "x_conge_deces", "x_conge_maternite", "x_conge_mariage",
        "x_deces_date_debut",
    )
    def _compute_x_ui_title(self):
        category_labels = dict(self._fields["x_timeoff_category"].selection)
        for rec in self:
            emp = rec.employee_id.name or _("No Employee")
            cat = category_labels.get(rec.x_timeoff_category, rec.x_timeoff_category or "")
            if rec.x_timeoff_category == "autres" and rec.x_conge_deces:
                days = rec.DECES_QUOTA
            elif rec.x_timeoff_category == "autres" and rec.x_conge_mariage:
                days = rec.MARIAGE_QUOTA
            else:
                days = rec.x_days_plus2
            if days == 0.0:
                dur = _("0 jour")
            elif days == int(days):
                dur = f"{int(days)} {_('jour') if days == 1 else _('jours')}"
            else:
                dur = f"{days} {_('jours')}"
            rec.x_ui_title = f"{emp} - Congé {cat} : {dur}"

    # =========================================================================
    # ONCHANGE
    # =========================================================================

    @api.onchange("x_for_other_employee", "x_target_employee_id")
    def _onchange_for_other_employee(self):
        if self.x_for_other_employee and self.x_target_employee_id:
            self.employee_id          = self.x_target_employee_id
            self.x_employee_confirmed = False
            self.state                = 'confirm'
        elif not self.x_for_other_employee:
            self.x_target_employee_id = False
            self.x_employee_confirmed = False
            self.state                = 'confirm'
            # ── Reset to current signed-in user's employee ────────────
            employee = self.env['hr.employee'].search(
                [('user_id', '=', self.env.uid)], limit=1
            )
            if employee:
                self.employee_id = employee
            else:
                self.employee_id = False

    @api.onchange("request_date_from")
    def _onchange_request_date_from(self):
        if self.request_date_from:
            self.x_date_debut = self.request_date_from
            if self.x_plage_complete:
                if not self.request_date_to or self.request_date_to < self.request_date_from:
                    self.request_date_to = self.request_date_from
                    self.x_date_fin = self.request_date_from
            elif self.request_date_to and self.request_date_from > self.request_date_to:
                self.request_date_to = self.request_date_from
                self.x_date_fin = self.request_date_from

    @api.onchange("request_date_to")
    def _onchange_request_date_to(self):
        if self.request_date_to:
            self.x_date_fin = self.request_date_to
            if self.request_date_from and self.request_date_to < self.request_date_from:
                self.request_date_to = self.request_date_from
                self.x_date_fin = self.request_date_from

    def _reset_native_dates(self):
        self.date_from         = False
        self.date_to           = False
        self.request_date_from = False
        self.request_date_to   = False
        self.number_of_days    = 0

    @api.onchange("x_plage_complete")
    def _onchange_plage_complete(self):
        if not self.x_plage_complete:
            if not self.x_demie_journee and not self.x_conge_maternite \
                    and not self.x_conge_deces and not self.x_conge_mariage:
                self._reset_native_dates()

    @api.onchange("x_demie_journee")
    def _onchange_demie_journee(self):
        if not self.x_demie_journee:
            if not self.x_plage_complete and not self.x_conge_maternite \
                    and not self.x_conge_deces and not self.x_conge_mariage:
                self._reset_native_dates()

    @api.onchange("x_conge_maternite")
    def _onchange_conge_maternite(self):
        from datetime import datetime, date as ddate, time as dtime
        if self.x_conge_maternite:
            self.x_conge_deces   = False
            self.x_conge_mariage = False
            is_female = self.employee_id and self.employee_id.gender == 'female'
            if is_female:
                debut = self.x_maternite_date_debut or ddate.today()
                fin   = debut + timedelta(days=97)
                self.x_maternite_date_debut = debut
                self.request_date_from      = debut
                self.request_date_to        = fin
                self.date_from = datetime.combine(debut, dtime(8,  0, 0))
                self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))
            else:
                debut = self.x_mat_male_date_from or ddate.today()
                fin   = self.x_mat_male_date_to   or debut
                self.x_mat_male_date_from = debut
                self.x_mat_male_date_to   = fin
                self.request_date_from    = debut
                self.request_date_to      = fin
                self.date_from = datetime.combine(debut, dtime(8,  0, 0))
                self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))
        else:
            if not self.x_conge_deces and not self.x_conge_mariage \
                    and not self.x_plage_complete and not self.x_demie_journee:
                self._reset_native_dates()

    @api.onchange("x_maternite_date_debut")
    def _onchange_maternite_date_debut(self):
        from datetime import datetime, time as dtime
        if self.x_conge_maternite and self.x_maternite_date_debut:
            debut = self.x_maternite_date_debut
            fin   = debut + timedelta(days=97)
            self.request_date_from = debut
            self.request_date_to   = fin
            self.date_from = datetime.combine(debut, dtime(8,  0, 0))
            self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))

    @api.onchange("x_mat_male_date_from", "x_mat_male_date_to")
    def _onchange_mat_male_dates(self):
        from datetime import datetime, time as dtime
        if not self.x_conge_maternite:
            return
        debut = self.x_mat_male_date_from
        if not debut:
            return
        fin = self.x_mat_male_date_to
        if not fin or fin < debut:
            self.x_mat_male_date_to = debut
            fin = debut
        nb = (fin - debut).days + 1
        if nb > 3:
            self.x_mat_male_date_to = debut + timedelta(days=2)
            fin = debut + timedelta(days=2)
            self.request_date_from = debut
            self.request_date_to   = fin
            self.date_from = datetime.combine(debut, dtime(8,  0, 0))
            self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))
            return {'warning': {
                'title':   _("Limite dépassée"),
                'message': _("Le congé maternité (homme) est limité à 3 jours maximum.\nLa date de fin a été ajustée automatiquement."),
            }}
        self.request_date_from = debut
        self.request_date_to   = fin
        self.date_from = datetime.combine(debut, dtime(8,  0, 0))
        self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))

    @api.onchange("x_conge_deces")
    def _onchange_conge_deces(self):
        from datetime import datetime, date as ddate, time as dtime
        if self.x_conge_deces:
            self.x_conge_maternite = False
            self.x_conge_mariage   = False
            debut = self.x_deces_date_debut or ddate.today()
            fin   = debut + timedelta(days=2)
            self.x_deces_date_debut = debut
            self.request_date_from  = debut
            self.request_date_to    = fin
            self.date_from = datetime.combine(debut, dtime(8,  0, 0))
            self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))
        else:
            if not self.x_conge_maternite and not self.x_conge_mariage \
                    and not self.x_plage_complete and not self.x_demie_journee:
                self._reset_native_dates()

    @api.onchange("x_conge_mariage")
    def _onchange_conge_mariage(self):
        from datetime import datetime, date as ddate, time as dtime
        if self.x_conge_mariage:
            self.x_conge_maternite = False
            self.x_conge_deces     = False
            debut = self.x_mariage_date_debut or ddate.today()
            fin   = debut + timedelta(days=3)
            self.x_mariage_date_debut = debut
            self.request_date_from    = debut
            self.request_date_to      = fin
            self.date_from = datetime.combine(debut, dtime(8,  0, 0))
            self.date_to   = datetime.combine(fin,   dtime(17, 0, 0))
        else:
            if not self.x_conge_maternite and not self.x_conge_deces \
                    and not self.x_plage_complete and not self.x_demie_journee:
                self._reset_native_dates()

    @api.onchange("x_mariage_date_debut")
    def _onchange_mariage_date_debut(self):
        from datetime import datetime, time as dtime
        if self.x_conge_mariage and self.x_mariage_date_debut:
            debut   = self.x_mariage_date_debut
            date_to = debut + timedelta(days=3)
            self.request_date_from = debut
            self.request_date_to   = date_to
            self.date_from = datetime.combine(debut,   dtime(8,  0, 0))
            self.date_to   = datetime.combine(date_to, dtime(17, 0, 0))

    @api.onchange("x_deces_date_debut")
    def _onchange_deces_date_debut(self):
        from datetime import datetime, time as dtime
        if self.x_conge_deces and self.x_deces_date_debut:
            debut   = self.x_deces_date_debut
            date_to = debut + timedelta(days=2)
            self.request_date_from = debut
            self.request_date_to   = date_to
            self.date_from = datetime.combine(debut,   dtime(8,  0, 0))
            self.date_to   = datetime.combine(date_to, dtime(17, 0, 0))

    # =========================================================================
    # CRUD
    # =========================================================================

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "state" in fields_list and not self.env.context.get("default_state"):
            res["state"] = "confirm"
        res.setdefault("x_employee_confirmed", False)
        res.setdefault("x_demie_journee",      False)
        res.setdefault("x_plage_complete",     False)
        if not res.get("employee_id"):
            employee = self.env['hr.employee'].search(
                [('user_id', '=', self.env.uid)], limit=1
            )
            if employee:
                res["employee_id"] = employee.id
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.pop("solde_conge", None)
            if not vals.get("employee_id") and not vals.get("x_for_other_employee"):
                employee = self.env['hr.employee'].search(
                    [('user_id', '=', self.env.uid)], limit=1
                )
                if employee:
                    vals["employee_id"] = employee.id
            vals.setdefault("x_employee_confirmed",    False)
            vals.setdefault("x_deducted_from_solde",   False)
            vals.setdefault("x_deducted_days",         0.0)
            vals.setdefault("x_demie_journee",         False)
            vals.setdefault("x_plage_complete",        False)
            vals.setdefault("x_maternite_days_stored", 0.0)
            vals.setdefault("x_deces_days_stored",     0.0)
            vals.setdefault("x_mariage_days_stored",   0.0)
            vals["state"]        = "confirm"
            vals["x_date_debut"] = vals.get("request_date_from") or False
            vals["x_date_fin"]   = vals.get("request_date_to")   or False
        return super().create(vals_list)

    _MANAGER_ALLOWED_FIELDS = frozenset({
        "state",
        "x_employee_confirmed",
        "x_deducted_from_solde",
        "x_deducted_days",
        "x_maternite_days_stored",
        "x_deces_days_stored",
        "x_mariage_days_stored",
        "x_deces_date_fin",
        "x_mariage_date_fin",
        "activity_ids",
        "message_ids",
        "date_from",
        "date_to",
        "number_of_days",
        "number_of_hours_display",
        "duration_display",
        "payslip_state",
        "first_approver_id",
        "second_approver_id",
    })

    def write(self, vals):
        vals = dict(vals)
        vals.pop("solde_conge", None)

        # ── When toggling off for_other_employee → reset to current user ──
        resetting_for_other = False
        if 'x_for_other_employee' in vals and not vals.get('x_for_other_employee'):
            employee = self.env['hr.employee'].search(
                [('user_id', '=', self.env.uid)], limit=1
            )
            if employee:
                vals['employee_id']          = employee.id
                vals['x_target_employee_id'] = False
            resetting_for_other = True

        if vals.get("x_for_other_employee") and vals.get("x_target_employee_id"):
            vals["employee_id"] = vals["x_target_employee_id"]
        elif "x_target_employee_id" in vals and vals.get("x_target_employee_id"):
            if self[:1].x_for_other_employee or vals.get("x_for_other_employee"):
                vals["employee_id"] = vals["x_target_employee_id"]

        if vals.get("x_conge_maternite"):
            vals.update(x_conge_deces=False, x_conge_mariage=False)
        if vals.get("x_conge_deces"):
            vals.update(x_conge_maternite=False, x_conge_mariage=False)
        if vals.get("x_conge_mariage"):
            vals.update(x_conge_maternite=False, x_conge_deces=False)

        skip_ownership = (
            self.env.uid == 1
            or self.env.user.has_group('holiday.group_holiday_hr')
            or any(self.env.context.get(k) for k in (
                "skip_state_check", "from_action_draft", "from_refuse",
                "from_cancel", "from_employee_confirm", "from_hr_validate",
            ))
        )
        if not skip_ownership:
            editing_content_fields = set(vals.keys()) - self._MANAGER_ALLOWED_FIELDS
            if editing_content_fields:
                for rec in self:
                    owner_user = rec.employee_id.user_id or rec.create_uid
                    if owner_user and owner_user.id != self.env.uid:
                        raise UserError(_(
                            "You cannot edit a Time Off request that does not belong to you.\n"
                            "Only the employee who created the request may modify it."
                        ))

        if "x_deces_date_debut" in vals and vals.get("x_conge_deces") is not False:
            debut = vals["x_deces_date_debut"]
            if debut:
                from datetime import datetime, time as dtime
                if hasattr(debut, 'strftime'):
                    vals["request_date_from"] = debut
                    vals["request_date_to"]   = debut + timedelta(days=2)
                    vals["date_from"] = datetime.combine(debut, dtime(8, 0, 0))
                    vals["date_to"]   = datetime.combine(debut + timedelta(days=2), dtime(17, 0, 0))
                    vals["number_of_days"] = 3.0
            else:
                vals.setdefault("request_date_from", False)
                vals.setdefault("request_date_to",   False)

        if "request_date_from" in vals:
            vals["x_date_debut"] = vals["request_date_from"]
        if "request_date_to" in vals:
            vals["x_date_fin"] = vals["request_date_to"]
        if vals.get("x_timeoff_category") == "maladie":
            vals.update(x_demie_journee=False, x_time_from=False,
                        x_time_to=False, x_demie_journee_date=False)
        if "x_timeoff_category" in vals and vals["x_timeoff_category"] != "autres":
            vals.update(x_conge_maternite=False, x_conge_deces=False, x_conge_mariage=False)

        if "x_maternite_date_debut" in vals:
            debut = vals["x_maternite_date_debut"]
            if debut and hasattr(debut, 'strftime'):
                from datetime import datetime, time as dtime
                fin = debut + timedelta(days=97)
                vals["request_date_from"] = debut
                vals["request_date_to"]   = fin
                vals["date_from"] = datetime.combine(debut, dtime(8,  0, 0))
                vals["date_to"]   = datetime.combine(fin,   dtime(17, 0, 0))
                vals["number_of_days"] = 98.0

        if "x_mat_male_date_from" in vals or "x_mat_male_date_to" in vals:
            from datetime import datetime, time as dtime, date as ddate
            def _parse_date(v):
                if not v:
                    return None
                if isinstance(v, ddate):
                    return v
                if isinstance(v, str):
                    try:
                        return datetime.strptime(v[:10], "%Y-%m-%d").date()
                    except Exception:
                        return None
                return None
            debut = _parse_date(vals.get("x_mat_male_date_from")) or \
                    (self[:1].x_mat_male_date_from if self else None)
            fin   = _parse_date(vals.get("x_mat_male_date_to")) or \
                    (self[:1].x_mat_male_date_to if self else None) or debut
            if debut:
                if fin and fin < debut:
                    fin = debut
                    vals["x_mat_male_date_to"] = debut
                if fin:
                    nb = (fin - debut).days + 1
                    if nb > 3:
                        fin = debut + timedelta(days=2)
                        vals["x_mat_male_date_to"] = fin
                vals["request_date_from"] = debut
                vals["request_date_to"]   = fin
                vals["date_from"] = datetime.combine(debut, dtime(8,  0, 0))
                vals["date_to"]   = datetime.combine(fin,   dtime(17, 0, 0))
                vals["number_of_days"] = float((fin - debut).days + 1)

        if "x_mariage_date_debut" in vals:
            debut = vals["x_mariage_date_debut"]
            if debut:
                from datetime import datetime, time as dtime
                if hasattr(debut, 'strftime'):
                    vals["request_date_from"] = debut
                    vals["request_date_to"]   = debut + timedelta(days=3)
                    vals["date_from"] = datetime.combine(debut, dtime(8, 0, 0))
                    vals["date_to"]   = datetime.combine(debut + timedelta(days=3), dtime(17, 0, 0))
                    vals["number_of_days"] = 4.0

        skip_state_validation = any(self.env.context.get(k) for k in (
            "skip_state_check", "from_action_draft", "from_refuse",
            "from_cancel", "from_hr_validate",
        ))

        for rec in self:
            if "employee_id" in vals:
                is_hr = self.env.user.has_group('holiday.group_holiday_hr')
                # Allow reset when toggling off x_for_other_employee
                if not is_hr and not rec.x_for_other_employee and not resetting_for_other:
                    raise UserError(_("You cannot change the employee on a Time Off request."))
            if not skip_state_validation:
                if vals.get("state") in ("confirm", "validate1", "validate"):
                    if not rec.x_employee_confirmed and not self.env.context.get("from_employee_confirm"):
                        vals["state"] = "confirm"

        return super().write(vals)

    def _check_validity(self):
        valid = self.filtered(lambda r: r.date_from and r.date_to and r.date_from is not False and r.date_to is not False)
        if valid:
            super(HrLeave, valid)._check_validity()

    # =========================================================================
    # CONSTRAINTS
    # =========================================================================

    def _get_number_of_days(self, date_from, date_to, employee):
        nothing_active = (
            not self.x_plage_complete
            and not self.x_demie_journee
            and not self.x_conge_maternite
            and not self.x_conge_deces
            and not self.x_conge_mariage
        )
        if nothing_active:
            return {'days': 0, 'hours': 0}
        is_special = self.x_conge_maternite or self.x_conge_deces or self.x_conge_mariage
        if is_special and date_from and date_to:
            from datetime import datetime
            df = date_from.date() if hasattr(date_from, 'date') else date_from
            dt = date_to.date()   if hasattr(date_to,   'date') else date_to
            nb = max(1, (dt - df).days + 1)
            return {'days': float(nb), 'hours': float(nb) * 8}
        return super()._get_number_of_days(date_from, date_to, employee)

    def _check_date_constraints(self):
        for rec in self:
            if not rec.x_employee_confirmed:
                continue
            if not rec.date_from or not rec.date_to:
                raise UserError(_("Veuillez saisir les dates de début et de fin du congé."))

    def _check_holidays(self):
        valid = self.filtered(lambda r: r.date_from and r.date_to)
        if valid:
            super(HrLeave, valid)._check_holidays()

    def _onchange_request_parameters(self):
        if not self.request_date_from or not self.request_date_to:
            return
        return super()._onchange_request_parameters()

    @api.onchange("request_date_from", "request_date_to", "request_unit_half",
                  "request_unit_hours", "request_date_from_period")
    def _onchange_request_dates(self):
        if not self.request_date_from or not self.request_date_to:
            return

    @api.constrains("x_conge_maternite", "x_conge_deces", "x_conge_mariage", "x_timeoff_category")
    def _check_autres_booleans(self):
        for rec in self:
            if rec.x_timeoff_category == "autres":
                active = sum([bool(rec.x_conge_maternite), bool(rec.x_conge_deces), bool(rec.x_conge_mariage)])
                if active > 1:
                    raise UserError(_("You can only select one type of special leave at a time."))

    @api.constrains(
        "x_plage_complete", "x_demie_journee", "x_demie_journee_date",
        "request_date_from", "request_date_to",
    )
    def _check_demie_journee_adjacency(self):
        for rec in self:
            if not (rec.x_plage_complete and rec.x_demie_journee):
                continue
            if not rec.x_demie_journee_date:
                continue
            if not rec.request_date_from or not rec.request_date_to:
                continue
            dfrom = rec.request_date_from
            dto   = rec.request_date_to
            if hasattr(dfrom, 'date'): dfrom = dfrom.date()
            if hasattr(dto,   'date'): dto   = dto.date()
            dj = rec.x_demie_journee_date
            if hasattr(dj, 'date'): dj = dj.date()
            allowed_min = dfrom - timedelta(days=1)
            allowed_max = dto   + timedelta(days=1)
            if not (allowed_min <= dj <= allowed_max):
                raise UserError(_(
                    "La date de la demi-journée (%s) doit être comprise dans la plage sélectionnée "
                    "ou le jour immédiatement avant/après.\n"
                    "Plage autorisée : du %s au %s."
                ) % (dj, allowed_min, allowed_max))

    @api.constrains(
        "x_demie_journee", "x_demie_journee_date", "x_plage_complete",
        "request_date_from", "request_date_to",
        "x_time_from", "x_time_to", "x_timeoff_category",
    )
    def _check_demie_journee(self):
        for rec in self:
            if not (rec.x_demie_journee and rec.x_timeoff_category in ("payee", "impayee", "autres")):
                continue
            if not rec.x_demie_journee_date:
                raise UserError(_("Please select a date for the half-day leave."))
            if rec.x_time_from is False or rec.x_time_to is False:
                raise UserError(_("Please specify the time range for the half-day leave."))
            if not (0 <= rec.x_time_from < 24):
                raise UserError(_("Start time must be between 0:00 and 23:59."))
            if not (0 <= rec.x_time_to < 24):
                raise UserError(_("End time must be between 0:00 and 23:59."))
            if rec.x_time_from >= rec.x_time_to:
                raise UserError(_("End time must be after start time."))

    @api.constrains("x_conge_maternite", "x_timeoff_category", "x_maternite_date_debut",
                    "x_mat_male_date_from", "x_mat_male_date_to")
    def _check_maternite_dates(self):
        for rec in self:
            if not (rec.x_timeoff_category == "autres" and rec.x_conge_maternite):
                continue
            is_female = rec.employee_id and rec.employee_id.gender == 'female'
            if rec.x_employee_confirmed:
                if is_female and not rec.x_maternite_date_debut:
                    raise UserError(_("Veuillez saisir la date de début du congé maternité."))
                if not is_female and not rec.x_mat_male_date_from:
                    raise UserError(_("Veuillez saisir la date de début du congé maternité."))
            if not is_female and rec.x_mat_male_date_from and rec.x_mat_male_date_to:
                nb = (rec.x_mat_male_date_to - rec.x_mat_male_date_from).days + 1
                if nb > 3:
                    raise UserError(_("Le congé maternité (homme) est limité à 3 jours maximum."))

    @api.constrains("x_conge_deces", "x_timeoff_category", "x_deces_date_debut")
    def _check_deces_dates(self):
        for rec in self:
            if rec.x_timeoff_category == "autres" and rec.x_conge_deces:
                if rec.x_employee_confirmed and not rec.x_deces_date_debut:
                    raise UserError(_("Veuillez saisir la date de début du congé décès."))

    @api.constrains("x_conge_mariage", "x_timeoff_category", "x_mariage_date_debut")
    def _check_mariage_dates(self):
        for rec in self:
            if rec.x_timeoff_category == "autres" and rec.x_conge_mariage:
                if rec.x_employee_confirmed and not rec.x_mariage_date_debut:
                    raise UserError(_("Veuillez saisir la date de début du congé de mariage."))

    # =========================================================================
    # EMPLOYEE CONFIRMATION
    # =========================================================================

    def action_confirm_employee(self):
        from dateutil.relativedelta import relativedelta
        for rec in self:
            if not rec.employee_id:
                raise UserError(_("Please select an employee."))
            if not rec.id:
                raise UserError(_("Please save the request first."))
            if rec.x_employee_confirmed:
                continue

            # ── 6 months seniority check ──────────────────────────────────
            is_hr = self.env.user.has_group('holiday.group_holiday_hr')
            if not is_hr and not rec.x_for_other_employee \
                    and rec.employee_id.date_embauche:
                delta = relativedelta(
                    fields.Date.today(),
                    rec.employee_id.date_embauche
                )
                total_months = delta.years * 12 + delta.months
                if total_months < 6:
                    raise UserError(_(
                        "Vous ne pouvez pas encore soumettre une demande de congé.\n"
                        "Vous devez avoir au moins 6 mois d'ancienneté.\n"
                        "Date d'embauche : %s\n"
                        "Ancienneté actuelle : %s mois — encore %s mois restant(s)."
                    ) % (
                        rec.employee_id.date_embauche.strftime('%d/%m/%Y'),
                        total_months,
                        6 - total_months,
                    ))
            # ─────────────────────────────────────────────────────────────

            is_deces     = rec.x_timeoff_category == "autres" and rec.x_conge_deces
            is_mariage   = rec.x_timeoff_category == "autres" and rec.x_conge_mariage
            is_maternite = rec.x_timeoff_category == "autres" and rec.x_conge_maternite

            if is_maternite:
                is_female = rec.employee_id and rec.employee_id.gender == 'female'
                if is_female:
                    if not rec.x_maternite_date_debut:
                        raise UserError(_("Veuillez saisir la date de début du congé maternité."))
                    date_debut = rec.x_maternite_date_debut
                    date_fin   = date_debut + timedelta(days=97)
                    rec.sudo().with_context(skip_state_check=True).write({
                        "request_date_from": date_debut,
                        "request_date_to":   date_fin,
                    })
                else:
                    if not rec.x_mat_male_date_from:
                        raise UserError(_("Veuillez saisir la date de début du congé maternité."))
                    date_debut = rec.x_mat_male_date_from
                    date_fin   = rec.x_mat_male_date_to or date_debut
                    nb_days    = (date_fin - date_debut).days + 1
                    if nb_days > 3:
                        raise UserError(_("Le congé maternité (homme) est limité à 3 jours maximum."))
                    used_others = rec._get_maternite_used_by_others()
                    if used_others + nb_days > 3:
                        raise UserError(_(
                            "Quota maternité dépassé.\n"
                            "Jours déjà utilisés : %s / 3\n"
                            "Jours demandés : %s"
                        ) % (used_others, nb_days))
                    rec.sudo().with_context(skip_state_check=True).write({
                        "request_date_from": date_debut,
                        "request_date_to":   date_fin,
                    })

            elif is_deces:
                if not rec.x_deces_date_debut:
                    raise UserError(_("Veuillez saisir la date de début du congé décès."))
                date_debut = rec.x_deces_date_debut
                date_fin   = date_debut + timedelta(days=2)
                rec.sudo().with_context(skip_state_check=True).write({
                    "request_date_from": date_debut,
                    "request_date_to":   date_fin,
                })

            elif is_mariage:
                if not rec.x_mariage_date_debut:
                    raise UserError(_("Veuillez saisir la date de début du congé de mariage."))
                date_debut = rec.x_mariage_date_debut
                date_fin   = date_debut + timedelta(days=3)
                rec.sudo().with_context(skip_state_check=True).write({
                    "request_date_from": date_debut,
                    "request_date_to":   date_fin,
                })

            else:
                if not rec.x_plage_complete and not rec.x_demie_journee:
                    raise UserError(_(
                        "Please check at least one option: "
                        "'Ajouter une plage complète' or 'Ajouter une demie journée'."
                    ))
                if rec.x_plage_complete:
                    if not rec.request_date_from or not rec.request_date_to:
                        raise UserError(_("Please select the start and end dates."))
                    dfrom = rec.request_date_from
                    dto   = rec.request_date_to
                    if hasattr(dfrom, "date"): dfrom = dfrom.date()
                    if hasattr(dto,   "date"): dto   = dto.date()
                    if dto < dfrom:
                        raise UserError(_("End date must be after or equal to start date."))
                if rec.x_demie_journee and rec.x_timeoff_category in ("payee", "impayee", "autres"):
                    if not rec.x_demie_journee_date:
                        raise UserError(_("Please select a date for the half-day leave."))
                    if rec.x_time_from is False or rec.x_time_to is False:
                        raise UserError(_("Please specify the time range."))
                    if rec.x_time_from >= rec.x_time_to:
                        raise UserError(_("End time must be after start time."))
                if rec.x_plage_complete and rec.x_demie_journee and rec.x_demie_journee_date:
                    dfrom = rec.request_date_from
                    dto   = rec.request_date_to
                    if hasattr(dfrom, "date"): dfrom = dfrom.date()
                    if hasattr(dto,   "date"): dto   = dto.date()
                    dj = rec.x_demie_journee_date
                    if hasattr(dj, "date"): dj = dj.date()
                    allowed_min = dfrom - timedelta(days=1)
                    allowed_max = dto   + timedelta(days=1)
                    if not (allowed_min <= dj <= allowed_max):
                        raise UserError(_(
                            "La date de la demi-journée (%s) doit être dans la plage "
                            "ou le jour immédiatement avant/après (du %s au %s)."
                        ) % (dj, allowed_min, allowed_max))

            owner_user = rec.employee_id.user_id or rec.create_uid
            is_hr = self.env.user.has_group('holiday.group_holiday_hr')
            if not is_hr and owner_user and owner_user != self.env.user:
                raise UserError(_("You can only confirm your own time off request."))

            rec._handle_balance_adjustments()
            rec._mark_employee_confirmed()

        return True

    # ── Balance helpers ───────────────────────────────────────────────────────

    def _handle_balance_adjustments(self):
        self.ensure_one()
        is_maternite = self.x_timeoff_category == "autres" and self.x_conge_maternite
        is_deces     = self.x_timeoff_category == "autres" and self.x_conge_deces
        is_mariage   = self.x_timeoff_category == "autres" and self.x_conge_mariage

        if self.x_deducted_from_solde and self.x_timeoff_category != "payee" \
                and not is_maternite and not is_deces and not is_mariage:
            self._refund_if_needed()
        elif self.x_timeoff_category == "payee" and not self.x_deducted_from_solde:
            self._deduct_balance()
        elif is_maternite:
            self._handle_maternite_balance()
        elif is_deces:
            self._handle_deces_balance()
        elif is_mariage:
            self._handle_mariage_balance()

    def _handle_maternite_balance(self):
        self.ensure_one()
        if self.x_deducted_from_solde:
            self._refund_if_needed()
        quota          = self._get_maternite_quota()
        used_others    = self._get_maternite_used_by_others()
        free_remaining = max(0.0, quota - used_others)
        is_female = self.employee_id.gender == 'female'
        if is_female:
            total_days = 98.0 if self.x_maternite_date_debut else self.x_maternite_total_days
        else:
            if self.x_mat_male_date_from and self.x_mat_male_date_to:
                total_days = float((self.x_mat_male_date_to - self.x_mat_male_date_from).days + 1)
            elif self.x_mat_male_date_from:
                total_days = 1.0
            else:
                total_days = self.x_maternite_total_days
        if total_days <= 0:
            raise UserError(_("Duration is invalid (0 days)."))
        free_portion  = min(total_days, free_remaining)
        solde_portion = max(0.0, total_days - free_remaining)
        if solde_portion > 0:
            balance = self.employee_id.solde_conge or 0.0
            if solde_portion > balance:
                raise UserError(_(
                    "Solde insuffisant.\n"
                    "Quota maternité restant : %s jours offerts\n"
                    "Jours demandés : %s\n"
                    "Jours à déduire du solde : %s\n"
                    "Solde disponible : %s"
                ) % (free_remaining, total_days, solde_portion, balance))
            self.employee_id.sudo().with_context(allow_solde_conge_update=True).write(
                {"solde_conge": balance - solde_portion}
            )
            self.sudo().write({"x_deducted_from_solde": True, "x_deducted_days": solde_portion})
        self.sudo().write({"x_maternite_days_stored": free_portion})

    def _handle_deces_balance(self):
        self.ensure_one()
        if self.x_deducted_from_solde:
            self._refund_if_needed()
        if not self.x_deces_date_debut:
            raise UserError(_("Veuillez saisir la date de début du congé décès."))
        # Quota resets per bereavement event — the full quota is free every time,
        # so previous décès leaves are not subtracted.
        free_remaining = self.DECES_QUOTA
        total_days     = self.DECES_QUOTA
        free_portion   = min(total_days, free_remaining)
        solde_portion  = max(0.0, total_days - free_remaining)
        if solde_portion > 0:
            balance = self.employee_id.solde_conge or 0.0
            if solde_portion > balance:
                raise UserError(_(
                    "Solde insuffisant.\n"
                    "Quota décès restant : %s jours offerts\n"
                    "Jours demandés : %s\n"
                    "Jours à déduire du solde : %s\n"
                    "Solde disponible : %s"
                ) % (free_remaining, total_days, solde_portion, balance))
            self.employee_id.sudo().with_context(allow_solde_conge_update=True).write(
                {"solde_conge": balance - solde_portion}
            )
            self.sudo().write({"x_deducted_from_solde": True, "x_deducted_days": solde_portion})
        self.sudo().write({"x_deces_days_stored": free_portion})

    def _handle_mariage_balance(self):
        self.ensure_one()
        if self.x_deducted_from_solde:
            self._refund_if_needed()
        if not self.x_mariage_date_debut:
            raise UserError(_("Veuillez saisir la date de début du congé de mariage."))
        # Quota resets per marriage event — the full quota is free every time,
        # so previous mariage leaves are not subtracted.
        free_remaining = self.MARIAGE_QUOTA
        total_days     = self.MARIAGE_QUOTA
        free_portion   = min(total_days, free_remaining)
        solde_portion  = max(0.0, total_days - free_remaining)
        if solde_portion > 0:
            balance = self.employee_id.solde_conge or 0.0
            if solde_portion > balance:
                raise UserError(_(
                    "Solde insuffisant.\n"
                    "Quota mariage restant : %s jours offerts\n"
                    "Jours demandés : %s\n"
                    "Jours à déduire du solde : %s\n"
                    "Solde disponible : %s"
                ) % (free_remaining, total_days, solde_portion, balance))
            self.employee_id.sudo().with_context(allow_solde_conge_update=True).write(
                {"solde_conge": balance - solde_portion}
            )
            self.sudo().write({"x_deducted_from_solde": True, "x_deducted_days": solde_portion})
        self.sudo().write({"x_mariage_days_stored": free_portion})

    def _deduct_balance(self):
        self.ensure_one()
        days = self.x_days_plus2 or 0.0
        if days <= 0:
            raise UserError(_("Duration is invalid (0 days)."))
        balance = self.employee_id.solde_conge or 0.0
        if days > balance:
            raise UserError(_(
                "Not enough balance.\nBalance: %s days\nRequested: %s days"
            ) % (balance, days))
        self.employee_id.sudo().with_context(allow_solde_conge_update=True).write(
            {"solde_conge": balance - days}
        )
        self.sudo().write({"x_deducted_from_solde": True, "x_deducted_days": days})

    def _refund_if_needed(self):
        for rec in self:
            if rec.x_deducted_from_solde and rec.employee_id and rec.x_deducted_days:
                rec.employee_id.sudo().with_context(allow_solde_conge_update=True).write({
                    "solde_conge": (rec.employee_id.solde_conge or 0.0) + rec.x_deducted_days
                })
                rec.sudo().write({"x_deducted_from_solde": False, "x_deducted_days": 0.0})
            if rec.x_maternite_days_stored:
                rec.sudo().write({"x_maternite_days_stored": 0.0})
            if rec.x_deces_days_stored:
                rec.sudo().write({"x_deces_days_stored": 0.0})
            if rec.x_mariage_days_stored:
                rec.sudo().write({"x_mariage_days_stored": 0.0})

    def _mark_employee_confirmed(self):
        self.ensure_one()
        self.sudo().with_context(
            from_employee_confirm=True, mail_notrack=True, tracking_disable=True,
        ).write({"x_employee_confirmed": True, "state": "unconfirmed"})

    # =========================================================================
    # STATUS ACTIONS
    # =========================================================================

    def action_hr_validate_for_other(self):
        if not self.env.user.has_group('holiday.group_holiday_hr'):
            raise UserError(_("Only HR users can perform this action."))
        for rec in self:
            if not rec.x_for_other_employee:
                raise UserError(_(
                    "This action is only available for leaves created "
                    "on behalf of another employee."
                ))
            if not rec.date_from or not rec.date_to:
                raise UserError(_(
                    "Veuillez d'abord renseigner les dates du congé avant de valider."
                ))
            if not rec.x_employee_confirmed:
                rec._handle_balance_adjustments()
            rec.sudo().with_context(
                from_hr_validate=True,
                mail_notrack=True,
                tracking_disable=True,
            ).write({
                "x_employee_confirmed": True,
                "state": "validate",
            })
            rec.message_post(body=_("Congé validé définitivement par RH."))
        return True

    def action_cancel_to_unconfirmed(self):
        for rec in self.sudo().with_context(mail_notrack=True, tracking_disable=True):
            rec._refund_if_needed()
            rec.write({"state": "confirm", "x_employee_confirmed": False})
        return True

    def action_refuse(self):
        self._refund_if_needed()
        ctx = dict(mail_notrack=True, tracking_disable=True, from_refuse=True)
        for rec in self:
            if rec.state == "unconfirmed":
                rec.sudo().with_context(**ctx).write({
                    "state": "refuse", "x_employee_confirmed": False,
                    "x_deducted_from_solde": False, "x_deducted_days": 0.0,
                    "x_maternite_days_stored": 0.0,
                    "x_deces_days_stored": 0.0,
                    "x_mariage_days_stored": 0.0,
                })
                rec.message_post(body=_("Time Off request refused"))
                return True
        for rec in self:
            rec.sudo().with_context(**ctx).write({
                "x_employee_confirmed": False,
                "x_deducted_from_solde": False, "x_deducted_days": 0.0,
                "x_maternite_days_stored": 0.0,
                "x_deces_days_stored": 0.0,
                "x_mariage_days_stored": 0.0,
            })
        return super().action_refuse()

    def action_cancel(self):
        self._refund_if_needed()
        ctx = dict(mail_notrack=True, tracking_disable=True, from_cancel=True)
        for rec in self:
            rec.sudo().with_context(**ctx).write({
                "x_employee_confirmed": False,
                "x_deducted_from_solde": False, "x_deducted_days": 0.0,
                "x_maternite_days_stored": 0.0,
                "x_deces_days_stored": 0.0,
                "x_mariage_days_stored": 0.0,
            })
        return super().action_cancel()

    def action_draft(self):
        for rec in self:
            if rec.state in ("refuse", "cancel"):
                rec._refund_if_needed()
                rec.sudo().with_context(
                    mail_notrack=True, tracking_disable=True, from_action_draft=True,
                ).write({
                    "state": "confirm", "x_employee_confirmed": False,
                    "x_deducted_from_solde": False, "x_deducted_days": 0.0,
                    "x_maternite_days_stored": 0.0,
                    "x_deces_days_stored": 0.0,
                    "x_mariage_days_stored": 0.0,
                })
                rec.message_post(body=_("Time Off request reset to draft"))
        self.env.invalidate_all()
        return True

    def action_approve(self, check_state=True):
        for rec in self:
            if rec.state == "unconfirmed":
                rec.sudo().write({"state": "validate1"})
                rec.message_post(body=_("Time Off approved by Manager"))
                return True
        return super().action_approve(check_state=check_state)

    def action_validate1(self):
        for rec in self:
            if rec.state == "validate1":
                super(HrLeave, rec).action_validate()
        return True

    # =========================================================================
    # DB REPAIR
    # =========================================================================

    def init(self):
        try:
            self.env.cr.execute("""
                UPDATE hr_leave hl
                SET
                    date_from         = (hl.x_deces_date_debut::date + TIME '08:00:00')::timestamp,
                    date_to           = (hl.x_deces_date_debut::date + INTERVAL '2 days' + TIME '17:00:00')::timestamp,
                    request_date_from = hl.x_deces_date_debut,
                    request_date_to   = hl.x_deces_date_debut::date + INTERVAL '2 days'
                WHERE
                    hl.x_conge_deces = TRUE
                    AND hl.x_deces_date_debut IS NOT NULL
                    AND (hl.date_from IS NULL OR hl.date_to IS NULL)
            """)
        except Exception:
            pass
        try:
            self.env.cr.execute("""
                UPDATE hr_leave hl
                SET
                    date_from         = (hl.x_mariage_date_debut::date + TIME '08:00:00')::timestamp,
                    date_to           = (hl.x_mariage_date_debut::date + INTERVAL '3 days' + TIME '17:00:00')::timestamp,
                    request_date_from = hl.x_mariage_date_debut,
                    request_date_to   = hl.x_mariage_date_debut::date + INTERVAL '3 days'
                WHERE
                    hl.x_conge_mariage = TRUE
                    AND hl.x_mariage_date_debut IS NOT NULL
                    AND (hl.date_from IS NULL OR hl.date_to IS NULL)
            """)
        except Exception:
            pass

    # =========================================================================
    # ORG CHART FILTER
    # =========================================================================

    @api.model
    def _get_subordinate_employee_ids(self):
        manager_employees = self.env['hr.employee'].search([('user_id', '=', self.env.uid)])
        if not manager_employees:
            return []
        return self.env['hr.employee'].search([
            ('id', 'child_of', manager_employees.ids)
        ]).ids

    def _search(self, domain, offset=0, limit=None, order=None):
        if self.env.context.get('filter_by_org_chart'):
            sub_ids = self._get_subordinate_employee_ids()
            domain = list(domain) + [('employee_id', 'in', sub_ids)]
        return super()._search(domain, offset=offset, limit=limit, order=order)

    # =========================================================================
    # FIELD OVERRIDES
    # =========================================================================

    @api.model
    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        res = super().fields_view_get(view_id=view_id, view_type=view_type,
                                      toolbar=toolbar, submenu=submenu)
        if view_type == "form":
            from lxml import etree
            doc = etree.XML(res["arch"])
            for node in doc.xpath("//field[@name='employee_id']"):
                node.set("readonly", "x_for_other_employee == False")
                node.set("force_save", "1")
            for node in doc.xpath("//button[@name='action_draft']"):
                node.set("invisible", "1")
            for node in doc.xpath("//field[@name='name']"):
                node.set("invisible", "1")
            if self.env.user.has_group('holiday.group_holiday_hr'):
                form_nodes = doc.xpath("//form")
                for node in form_nodes:
                    node.set("edit", "1")
            res["arch"] = etree.tostring(doc, encoding="unicode")
        return res

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        res = super().fields_get(allfields=allfields, attributes=attributes)
        if "state" not in res or not res["state"].get("selection"):
            return res
        rename = {
            "confirm":     _("Unconfirmed"),
            "unconfirmed": _("To Approve"),
            "validate1":   _("Manager Approve"),
            "validate":    _("Valide"),
        }
        desired_order = ["confirm", "unconfirmed", "validate1", "validate"]
        original      = res["state"]["selection"]
        original_map  = dict(original)
        new_selection = [
            (k, rename.get(k, original_map[k])) for k in desired_order if k in original_map
        ]
        for k, label in original:
            if k not in desired_order and k != "cancel":
                new_selection.append((k, label))
        res["state"]["selection"] = new_selection
        return res

    def name_get(self):
        result = []
        for rec in self:
            name = rec.x_ui_title or _("Congé")
            result.append((rec.id, name))
        return result

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.x_ui_title or _("Congé")

    # =========================================================================
    # UTILITY
    # =========================================================================

    def _format_time_float(self, value):
        if value is False: return "00:00"
        h = int(value); m = int((value - h) * 60)
        return f"{h:02d}:{m:02d}"

    def get_time_display(self):
        self.ensure_one()
        if self.x_demie_journee and self.x_time_from is not False and self.x_time_to is not False:
            return f"{self._format_time_float(self.x_time_from)} - {self._format_time_float(self.x_time_to)}"
        return ""


class HrEmployeeBase(models.AbstractModel):
    _inherit = "hr.employee.base"

    def _get_consumed_leaves(self, leave_types, target_date=None):
        from datetime import datetime, time as dtime
        bad_leaves = self.env["hr.leave"].sudo().search([("date_from", "=", False)])
        for rec in bad_leaves:
            try:
                if rec.x_conge_deces and rec.x_deces_date_debut:
                    debut = rec.x_deces_date_debut
                    fin   = debut + timedelta(days=2)
                    rec.with_context(skip_state_check=True, mail_notrack=True, tracking_disable=True).write({
                        "date_from":         datetime.combine(debut, dtime(8,  0, 0)),
                        "date_to":           datetime.combine(fin,   dtime(17, 0, 0)),
                        "request_date_from": debut,
                        "request_date_to":   fin,
                        "number_of_days":    3.0,
                    })
                elif rec.x_conge_mariage and rec.x_mariage_date_debut:
                    debut = rec.x_mariage_date_debut
                    fin   = debut + timedelta(days=3)
                    rec.with_context(skip_state_check=True, mail_notrack=True, tracking_disable=True).write({
                        "date_from":         datetime.combine(debut, dtime(8,  0, 0)),
                        "date_to":           datetime.combine(fin,   dtime(17, 0, 0)),
                        "request_date_from": debut,
                        "request_date_to":   fin,
                        "number_of_days":    4.0,
                    })
                else:
                    rec.with_context(skip_state_check=True, mail_notrack=True, tracking_disable=True).write({
                        "date_from": datetime(1970, 1, 1, 8,  0, 0),
                        "date_to":   datetime(1970, 1, 1, 17, 0, 0),
                    })
            except Exception:
                pass
        return super()._get_consumed_leaves(leave_types, target_date=target_date)