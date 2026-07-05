from odoo import models, fields, api


class AttendanceOverview(models.TransientModel):
    _name        = 'attendance.overview.wizard'
    _description = 'Attendance Overview'

    # (field prefix, department_type) — 'total' is the global (all depts) section
    _SECTIONS = [
        ('total',     None),
        ('siege',     'siege'),
        ('agence',    'agence'),
        ('warehouse', 'warehouse'),
        ('aeroport',  'aeroport'),
    ]
    # (field suffix, status bucket code) — one counter per section × bucket
    _BUCKETS = [
        ('present',    'on_time'),
        ('late',       'late'),
        ('late_break', 'late_break'),
        ('absent',     'absence'),
        ('timeoff',    'timeoff'),
        ('autres',     'missing_checkout'),
        ('anomalie',   'anomalie'),
        ('resolved',   'resolved'),
    ]

    date = fields.Date(string='Date')

    # ── Counters (section × bucket) ───────────────────────────
    total_present = fields.Integer(compute='_compute_summary')
    total_late = fields.Integer(compute='_compute_summary')
    total_late_break = fields.Integer(compute='_compute_summary')
    total_absent = fields.Integer(compute='_compute_summary')
    total_timeoff = fields.Integer(compute='_compute_summary')
    total_autres = fields.Integer(compute='_compute_summary')
    total_anomalie = fields.Integer(compute='_compute_summary')
    total_resolved = fields.Integer(compute='_compute_summary')

    siege_present = fields.Integer(compute='_compute_summary')
    siege_late = fields.Integer(compute='_compute_summary')
    siege_late_break = fields.Integer(compute='_compute_summary')
    siege_absent = fields.Integer(compute='_compute_summary')
    siege_timeoff = fields.Integer(compute='_compute_summary')
    siege_autres = fields.Integer(compute='_compute_summary')
    siege_anomalie = fields.Integer(compute='_compute_summary')
    siege_resolved = fields.Integer(compute='_compute_summary')

    agence_present = fields.Integer(compute='_compute_summary')
    agence_late = fields.Integer(compute='_compute_summary')
    agence_late_break = fields.Integer(compute='_compute_summary')
    agence_absent = fields.Integer(compute='_compute_summary')
    agence_timeoff = fields.Integer(compute='_compute_summary')
    agence_autres = fields.Integer(compute='_compute_summary')
    agence_anomalie = fields.Integer(compute='_compute_summary')
    agence_resolved = fields.Integer(compute='_compute_summary')

    warehouse_present = fields.Integer(compute='_compute_summary')
    warehouse_late = fields.Integer(compute='_compute_summary')
    warehouse_late_break = fields.Integer(compute='_compute_summary')
    warehouse_absent = fields.Integer(compute='_compute_summary')
    warehouse_timeoff = fields.Integer(compute='_compute_summary')
    warehouse_autres = fields.Integer(compute='_compute_summary')
    warehouse_anomalie = fields.Integer(compute='_compute_summary')
    warehouse_resolved = fields.Integer(compute='_compute_summary')

    aeroport_present = fields.Integer(compute='_compute_summary')
    aeroport_late = fields.Integer(compute='_compute_summary')
    aeroport_late_break = fields.Integer(compute='_compute_summary')
    aeroport_absent = fields.Integer(compute='_compute_summary')
    aeroport_timeoff = fields.Integer(compute='_compute_summary')
    aeroport_autres = fields.Integer(compute='_compute_summary')
    aeroport_anomalie = fields.Integer(compute='_compute_summary')
    aeroport_resolved = fields.Integer(compute='_compute_summary')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'date' in fields_list:
            res['date'] = fields.Date.context_today(self)
        return res

    @api.depends('date')
    def _compute_summary(self):
        for rec in self:
            records = rec.env['hr.attendance'].browse()
            if rec.date:
                date_str = rec.date.strftime('%Y-%m-%d')
                records = rec.env['hr.attendance'].search([
                    ('check_in', '>=', date_str + ' 00:00:00'),
                    ('check_in', '<=', date_str + ' 23:59:59'),
                ])
            # Count by bucket MEMBERSHIP so each counter matches exactly what its
            # stat-button drilldown shows (status_bucket_ids.code == code). A
            # record carrying several buckets is counted in each of them.
            for prefix, dept in self._SECTIONS:
                dept_recs = records if dept is None else records.filtered(
                    lambda x: x.department_type == dept)
                for suffix, code in self._BUCKETS:
                    hits = dept_recs.filtered(
                        lambda x: code in x.status_bucket_ids.mapped('code'))
                    setattr(rec, f'{prefix}_{suffix}', len(hits))

    def _attendance_action(self, date_str, extra_domain=None, group_by_shift=False):
        domain = list(extra_domain) if extra_domain else []
        domain = [
            ('check_in', '>=', date_str + ' 00:00:00'),
            ('check_in', '<=', date_str + ' 23:59:59'),
        ] + domain
        context = {
            'create':                       False,
            'search_default_group_status':  2,
        }
        # "Voir toutes les presences" mirrors the Aujourd'hui / date-wizard
        # grouping (Department / Shift); drilldown buttons keep dept type.
        if group_by_shift:
            context['search_default_group_dept_shift'] = 1
        else:
            context['search_default_group_dept_type'] = 1
        return {
            'type':           'ir.actions.act_window',
            'name':           f'Attendances — {date_str}',
            'res_model':      'hr.attendance',
            'view_mode':      'list,form',
            'views':          [[False, 'list'], [False, 'form']],
            'search_view_id': [self.env.ref('hr_attendance.hr_attendance_view_filter').id, 'search'],
            'domain':         domain,
            'context':        context,
            # 'main' (not 'current') so each date pick REPLACES the view and
            # clears the breadcrumb trail instead of stacking dates.
            'target':         'main',
        }

    def action_pick_date(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       group_by_shift=True)

    def action_view_bucket(self):
        """Drilldown shared by every status stat-button. The button passes the
        status via `bucket_code` and, for a section, the `dept` via context."""
        self.ensure_one()
        if not self.date:
            return
        code = self.env.context.get('bucket_code')
        dept = self.env.context.get('dept')
        domain = []
        if dept:
            domain.append(('department_type', '=', dept))
        if code:
            domain.append(('status_bucket_ids.code', '=', code))
        return self._attendance_action(self.date.strftime('%Y-%m-%d'), domain)
