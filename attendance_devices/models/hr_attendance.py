from odoo import models, fields, api
from datetime import timedelta, datetime, time
from zoneinfo import ZoneInfo
import logging

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    missing_checkout = fields.Boolean(
        string='Missing Checkout',
        default=False,
    )


    is_absent = fields.Boolean(
        string='Absent',
        default=False,
        help='True when auto-generated because the employee had no check-in that day.',
    )

    # Display-only labels: show a dash for absent rows instead of the
    # placeholder shift-start/shift-end timestamps.
    check_in_display = fields.Char(
        string='Check In',
        compute='_compute_check_inout_display',
    )
    check_out_display = fields.Char(
        string='Check Out',
        compute='_compute_check_inout_display',
    )

    @api.depends('check_in', 'check_out', 'is_absent', 'is_anomalie', 'missing_checkout')
    def _compute_check_inout_display(self):
        TZ  = ZoneInfo("Africa/Casablanca")
        UTC = ZoneInfo("UTC")

        def fmt(dt):
            return dt.replace(tzinfo=UTC).astimezone(TZ).strftime('%m/%d/%Y %H:%M:%S')

        for att in self:
            if att.is_absent:
                att.check_in_display  = '—'
                att.check_out_display = '—'
                continue
            if att.is_anomalie:
                # Single punch treated as a checkout; check-in shown empty.
                att.check_in_display  = ''
                att.check_out_display = fmt(att.check_out or att.check_in) if (att.check_out or att.check_in) else ''
                continue
            if att.missing_checkout and att.check_out and att.check_out == att.check_in:
                # Forgotten checkout: the punch is the check-IN. Leave the
                # checkout blank (like an absence) instead of echoing the
                # check-in time.
                att.check_in_display  = fmt(att.check_in) if att.check_in else ''
                att.check_out_display = ''
                continue
            att.check_in_display  = fmt(att.check_in) if att.check_in else ''
            att.check_out_display = fmt(att.check_out) if att.check_out else ''

    department_type = fields.Selection(
        related='employee_id.department_type',
        string='Department Type',
        store=True,
    )

    # The employee's chosen shift — used as the 2nd grouping level
    # (Department Type > Shift). Shows the shift name (day / night / Standard…).
    shift_config_id = fields.Many2one(
        'attendance.shift.config',
        related='employee_id.shift_config_id',
        string='Shift',
        store=True,
        index=True,
    )

    department_shift = fields.Char(
        string='Department / Shift',
        compute='_compute_department_shift',
        store=True,
    )

    @api.depends('employee_id.department_type', 'employee_id.shift_type',
                 'employee_id.shift_config_id')
    def _compute_department_shift(self):
        labels = self.env['hr.department.type'].get_label_map()
        ShiftConfig = self.env['attendance.shift.config']
        counts = {}  # department_type -> number of configured shifts (cached)
        for att in self:
            emp        = att.employee_id
            dept       = emp.department_type or ''
            dept_label = labels.get(dept, dept or 'Other')
            if dept not in counts:
                counts[dept] = ShiftConfig.search_count(
                    [('department_type', '=', dept)]) if dept else 0
            # Employee's actual shift name (new picker first, warehouse fallback)
            shift = ''
            if emp.shift_config_id:
                shift = emp.shift_config_id.shift_name or ''
            elif emp.shift_type:
                shift = emp.shift_type
            # Only split by shift when the department actually has several
            if shift and counts[dept] > 1:
                att.department_shift = f"{dept_label} {shift.capitalize()}"
            else:
                att.department_shift = dept_label

    shift_type = fields.Selection(
        selection=[('day', 'Day Shift'), ('night', 'Night Shift')],
        string='Shift',
        compute='_compute_shift_type',
        store=True,
        group_expand='_expand_shift_type',
    )

    @api.model
    def _expand_shift_type(self, states, domain):
        # Only expand day/night — never show None/empty group
        return ['day', 'night']

    @api.depends('employee_id.shift_type', 'employee_id.department_type')
    def _compute_shift_type(self):
        for att in self:
            # Only set shift_type for warehouse employees
            if att.employee_id.department_type == 'warehouse':
                att.shift_type = att.employee_id.shift_type or False
            else:
                att.shift_type = False

    def _recompute_shift_type_all(self):
        """Force recompute shift_type for all records — call after upgrade."""
        self.env.cr.execute(
            """
            UPDATE hr_attendance a
               SET shift_type = CASE
                   WHEN e.department_type = 'warehouse' THEN e.shift_type
                   ELSE NULL
               END
              FROM hr_employee e
             WHERE e.id = a.employee_id
            """
        )
        self.invalidate_model(['shift_type'])
        return True

    @api.model
    def _auto_init(self):
        res = super()._auto_init()
        # Recompute shift_type for all existing records on module install/upgrade
        try:
            self.env.cr.execute(
                """
                UPDATE hr_attendance a
                   SET shift_type = CASE
                       WHEN e.department_type = 'warehouse' THEN e.shift_type
                       ELSE NULL
                   END
                  FROM hr_employee e
                 WHERE e.id = a.employee_id
                """
            )
        except Exception:
            pass
        return res

    def init(self):
        # Composite index for the sync engine's hot query
        # (WHERE employee_id = ? AND check_in >= ? AND check_in < ?), run on
        # every device sync. Without it these range scans get slow as the table
        # grows past tens of thousands of rows.
        try:
            self.env.cr.execute(
                "CREATE INDEX IF NOT EXISTS hr_attendance_employee_checkin_idx "
                "ON hr_attendance (employee_id, check_in)"
            )
        except Exception:
            pass

        # Hard guarantee: at most ONE absent/time-off row per employee per
        # shift-start. Absent rows are generated by SELECT-then-INSERT, which is
        # not safe if two transactions (e.g. a manual sync and the cron) run at
        # once under REPEATABLE READ — both see "no row" and both insert. First
        # drop any duplicates that already exist (keep the lowest id), then add a
        # partial UNIQUE index so the database itself refuses a second one. The
        # absence INSERT uses ON CONFLICT ... DO NOTHING against this index.
        try:
            self.env.cr.execute(
                """
                DELETE FROM hr_attendance a
                 USING hr_attendance b
                 WHERE a.is_absent = TRUE
                   AND b.is_absent = TRUE
                   AND a.employee_id = b.employee_id
                   AND a.check_in    = b.check_in
                   AND a.id > b.id
                """
            )
            self.env.cr.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS hr_attendance_absent_uniq "
                "ON hr_attendance (employee_id, check_in) WHERE is_absent"
            )
        except Exception:
            _logger.exception("[INIT] Could not create absent uniqueness index")

        # Recompute shift_type and department_shift on every upgrade
        try:
            self.env.cr.execute(
                """
                UPDATE hr_attendance a
                   SET shift_type = CASE
                       WHEN e.department_type = 'warehouse' THEN e.shift_type
                       ELSE NULL
                   END,
                   department_shift = CASE
                       WHEN e.department_type = 'warehouse' AND e.shift_type IS NOT NULL
                           THEN 'Warehouse ' || initcap(e.shift_type)
                       WHEN e.department_type = 'siege'     THEN 'Siège'
                       WHEN e.department_type = 'agence'    THEN 'Agence'
                       WHEN e.department_type = 'warehouse' THEN 'Warehouse'
                       WHEN e.department_type = 'aeroport'  THEN 'Aéroport'
                       ELSE COALESCE(e.department_type, 'Other')
                   END
                  FROM hr_employee e
                 WHERE e.id = a.employee_id
                """
            )
        except Exception:
            pass

        # Refresh department_shift with the general multi-shift labelling
        # (uses the employee's shift_config_id, so custom department types are
        # split by their shifts too — not just warehouse).
        try:
            recs = self.search([])
            if recs:
                recs._compute_department_shift()
                recs.flush_recordset(['department_shift'])
        except Exception:
            pass

    # ── Multi-status system ──────────────────────────────────────────────────
    # A record can carry several statuses at once (e.g. [À l'heure,
    # Absence après-midi]). status_ids = the 7 detailed statuses (badges/logic);
    # status_bucket_ids = the 4 simplified buckets shown/filtered/grouped in the
    # list view (the 3 absence types fold into a single "Absence" bucket).
    is_anomalie = fields.Boolean(
        string='Anomalie',
        default=False,
        help='Single punch near shift end reinterpreted as a checkout — no valid work session.',
    )
    is_resolved = fields.Boolean(
        string='Résolut',
        default=False,
        help='Manually mark an absence as resolved — adds the Résolut status.',
    )
    has_absence = fields.Boolean(
        string='Has absence',
        compute='_compute_statuses',
        store=True,
        help='True when the record carries any absence status (drives the Résolut checkbox).',
    )
    locked_status_codes = fields.Char(
        string='Locked statuses',
        copy=False,
        help='Comma-separated accumulation of every status code this record has '
             'ever earned. Statuses are additive and permanent: a later edit or '
             'recompute can only add codes here, never remove one.',
    )
    can_edit_times = fields.Boolean(
        string='Can edit times',
        compute='_compute_can_edit_times',
        help='Drives whether check-in / check-out are editable. Administrators '
             'always can; the HR role can only once the record is marked Résolut.',
    )

    @api.depends('is_resolved')
    def _compute_can_edit_times(self):
        # Admins keep full edit rights; the HR role must tick Résolut first.
        is_admin = (self.env.user.has_group('attendance_devices.group_attendance_device_admin')
                    or self.env.user.has_group('base.group_system'))
        for att in self:
            att.can_edit_times = is_admin or att.is_resolved

    status_ids = fields.Many2many(
        'attendance.status',
        'hr_attendance_status_rel', 'attendance_id', 'status_id',
        string='Statuses',
        compute='_compute_statuses',
        store=True,
        # No group_expand: only statuses that actually have records show up as
        # groups. Empty ones (e.g. Retard de pause with 0) stay hidden until at
        # least one record carries them.
    )
    # Badges shown on the row/form. Same as status_ids, except a resolved record
    # keeps its absence status here (e.g. [Absence matinale, Résolut]) while
    # status_ids collapses to just Résolut for grouping/filtering.
    status_badge_ids = fields.Many2many(
        'attendance.status',
        'hr_attendance_status_badge_rel', 'attendance_id', 'status_id',
        string='Statuses',
        compute='_compute_statuses',
        store=True,
    )
    status_bucket_ids = fields.Many2many(
        'attendance.status.bucket',
        'hr_attendance_status_bucket_rel', 'attendance_id', 'bucket_id',
        string='Status',
        compute='_compute_statuses',
        store=True,
        group_expand='_expand_status_buckets',
    )

    # Fallback thresholds (minutes) when a shift config has none set.
    _ABSENCE_DELTA_MIN   = 150   # morning: check-in later than start + 2h30
    _AFTERNOON_GRACE_MIN = 120   # afternoon: left, +2h and shift still running

    _ABSENCE_CODES = ('absence_morning', 'absence_afternoon', 'absence_full')

    @api.model
    def _expand_status_buckets(self, buckets, domain, order=None):
        return self.env['attendance.status.bucket'].search([])

    @api.depends(
        'check_in', 'check_out', 'is_absent', 'is_anomalie', 'is_resolved', 'missing_checkout',
        'break_start', 'break_end', 'break_duration',
        'employee_id.shift_config_id',
        'employee_id.shift_config_id.start_time',
        'employee_id.shift_config_id.break_start',
        'employee_id.shift_config_id.late_tolerance_minutes',
        'employee_id.shift_config_id.absence_morning_delay',
        'employee_id.shift_config_id.absence_afternoon_grace',
        'employee_id.shift_config_id.break_max_duration',
    )
    def _compute_statuses(self):
        TZ  = ZoneInfo("Africa/Casablanca")
        UTC = ZoneInfo("UTC")
        now_utc = fields.Datetime.now()

        Status  = self.env['attendance.status']
        by_code = {s.code: s for s in Status.search([])}

        def local_min(dt):
            loc = dt.replace(tzinfo=UTC).astimezone(TZ)
            return loc.hour * 60 + loc.minute

        for att in self:
            codes = set()

            if att.is_anomalie:
                # Case 3: single punch near shift end → anomaly + full-day absence
                codes = {'anomalie', 'absence_full'}
            elif att.is_absent or not att.check_in:
                # No real punch. The placeholder is only created within 1h of
                # shift end (see _generate_absences), so by the time it exists the
                # day is effectively over → Absence (or Congé if on approved leave).
                if att._is_on_leave(att.check_in):
                    codes = {'timeoff'}
                else:
                    codes = {'absence_full'}
            else:
                config = att.employee_id.shift_config_id
                ci_min = local_min(att.check_in)
                # Effective start/end for this record's day (honours a Saturday
                # schedule when the shift works Saturdays).
                ci_day = att.check_in.replace(tzinfo=UTC).astimezone(TZ).date()
                eff_start, eff_end = config.effective_times(ci_day) if config else (0.0, 0.0)

                # Morning dimension — exactly one of on_time / late / absence_morning
                if config:
                    diff        = ci_min - int(eff_start * 60)
                    tol         = config.late_tolerance_minutes or 30
                    morning_max = int((config.absence_morning_delay or 2.5) * 60)
                    if diff <= tol:
                        codes.add('on_time')
                    elif diff <= morning_max:
                        codes.add('late')
                    else:
                        codes.add('absence_morning')
                else:
                    codes.add('on_time')

                # Missing checkout — auto-closed with checkout == checkin
                if att.missing_checkout and att.check_out and att.check_out == att.check_in:
                    codes.add('missing_checkout')

                # Break-overrun dimension — the employee punched a break start and
                # stayed out longer than the shift's allowed maximum (default 1h15):
                #   • he punched back in  → Retard de pause (late_break)
                #   • he never punched back → Absence après-midi
                # Only relevant once he has a real morning (not absence_morning).
                if 'absence_morning' not in codes and config and att.break_start:
                    brk_max = config.break_max_duration or 1.25
                    if att.break_end and att.break_end > att.break_start:
                        brk_len = (att.break_end - att.break_start).total_seconds() / 3600.0
                        if brk_len > brk_max:
                            codes.add('late_break')
                    elif att.break_start + timedelta(hours=brk_max) <= now_utc:
                        # Still out, allowed break already elapsed with no return.
                        # He never punched back → afternoon absence, and since he
                        # never gave a checkout punch either → also missing checkout.
                        codes.add('absence_afternoon')
                        codes.add('missing_checkout')

                # Afternoon dimension — the employee went out (his own break start,
                # or a real early checkout) and 2h later the shift was still on, so
                # he missed the afternoon. Independent of missing checkout: a record
                # can carry both (shown as [Absence après-midi, Missing Checkout]).
                if ('absence_morning' not in codes and 'absence_afternoon' not in codes
                        and config and eff_end):
                    real_out = att.check_out if (att.check_out and att.check_out != att.check_in) else None
                    # A break the employee punched back from is handled by the
                    # break-overrun rule above (Retard de pause) — don't also treat
                    # it as an afternoon departure. Only an unreturned break counts.
                    returned = bool(att.break_end and att.break_end > att.break_start)
                    leave    = (att.break_start if not returned else None) or real_out
                    if leave:
                        leave_min = local_min(leave)
                        end_min   = int(eff_end * 60)
                        grace     = int((config.absence_afternoon_grace or 2.0) * 60)
                        # Afternoon starts at lunch start. Leaving once lunch has
                        # begun (his own break start / early checkout) and not
                        # returning within the grace = missed afternoon. A real
                        # returned break is already excluded above (returned).
                        afternoon_start = int((config.break_start or 0) * 60)
                        if eff_end < eff_start:   # night shift
                            end_min += 1440
                            if leave_min < int(eff_start * 60):
                                leave_min += 1440
                        # Don't decide immediately: only conclude afternoon absence
                        # once the grace has actually elapsed with no return.
                        grace_passed = (leave + timedelta(minutes=grace)) <= now_utc
                        if (leave_min >= afternoon_start
                                and leave_min + grace < end_min
                                and grace_passed):
                            codes.add('absence_afternoon')

            # Saturday exception: if the shift does NOT declare a Saturday
            # schedule, it carries only weekday hours (e.g. end 17:00), so the
            # afternoon rule would wrongly flag a short Saturday. Skip afternoon
            # absence there. Shifts that DO work Saturday were already evaluated
            # against their Saturday start/end above, so leave those alone.
            if att.check_in:
                ci_date = att.check_in.replace(tzinfo=UTC).astimezone(TZ).date()
                cfg = att.employee_id.shift_config_id
                if ci_date.weekday() == 5 and not (cfg and cfg.works_saturday):
                    codes.discard('absence_afternoon')

            # ── Additive, permanent statuses ─────────────────────────────────
            # Statuses only ever ADD. A record keeps every status it has ever
            # earned; a later edit or recompute can never remove one. e.g. a
            # record classed Absence matinale at 12:30, edited to 08:30, ends up
            # [Absence matinale, À l'heure] — the morning absence is never lost.
            #
            # Derived anomalie: a hard absence (full/afternoon) that also forgot a
            # checkout reads as an Anomalie — add it too (still purely additive).
            if (codes & {'absence_afternoon', 'absence_full'}) and 'missing_checkout' in codes:
                codes.add('anomalie')

            # Presence dimension (À l'heure / Retard) is the exception: it always
            # reflects the CURRENT check-in time and the two are mutually
            # exclusive. Editing an 08:30 (À l'heure) record to 10:00 turns it
            # into Retard and drops À l'heure. Every other status is additive and
            # permanent, so a kept Absence matinale simply shows next to it
            # (e.g. [Absence matinale, Retard]).
            transient = codes & {'on_time', 'late'}

            # Accumulate the permanent statuses. 'resolved' is driven by the
            # is_resolved checkbox and on_time/late are transient, so none of
            # those are stored here.
            locked = set((att.locked_status_codes or '').split(',')) - {'', 'on_time', 'late'}
            locked |= codes - {'resolved', 'on_time', 'late'}
            new_locked = ','.join(sorted(locked))
            if (att.locked_status_codes or '') != new_locked:
                att.locked_status_codes = new_locked

            # Displayed set = permanent history + the current presence status.
            display = locked | transient

            # A record is "an absence" (drives the Résolut checkbox) if it has
            # EVER carried an absence status.
            absences = locked & set(self._ABSENCE_CODES)
            att.has_absence = bool(absences)

            # Badges and grouping both carry that set …
            badge_codes = set(display)
            group_codes = set(display)

            # … except Résolut: ticking it keeps every badge (+ Résolut) but
            # collapses grouping/filtering to the Résolut section only.
            if att.is_resolved and att.has_absence:
                badge_codes.add('resolved')
                group_codes = {'resolved'}

            def to_recs(cs):
                return Status.browse([by_code[c].id for c in cs if c in by_code])

            group_statuses = to_recs(group_codes)
            att.status_badge_ids  = [(6, 0, to_recs(badge_codes).ids)]
            att.status_ids        = [(6, 0, group_statuses.ids)]
            att.status_bucket_ids = [(6, 0, group_statuses.bucket_id.ids)]

    @api.model
    def recompute_statuses_for_ids(self, ids):
        """Recompute the stored multi-status fields after raw-SQL writes."""
        if not ids:
            return
        recs = self.browse(ids).exists()
        recs.invalidate_recordset()
        self.env.add_to_compute(self._fields['status_ids'], recs)
        self.env.add_to_compute(self._fields['status_bucket_ids'], recs)
        # locked_status_codes is pinned as a side effect of _compute_statuses;
        # flush it too so the sticky pin survives the raw-SQL sync path.
        recs.flush_recordset(['status_ids', 'status_bucket_ids', 'locked_status_codes'])

    @api.onchange('check_in')
    def _onchange_check_in_clear_absent(self):
        # When a manager manually edits the check-in of an Absence placeholder,
        # treat it as a real arrival: drop is_absent so the morning/afternoon
        # rules re-evaluate (e.g. 11:45 → Absence matinale, 09:01 → Retard).
        for att in self:
            if att.is_absent and att.check_in:
                att.is_absent = False

    def _is_on_leave(self, check_in):
        """True when the employee has approved (validated) time-off covering the
        record's day. Guarded in case hr.leave isn't installed."""
        if not check_in or 'hr.leave' not in self.env:
            return False
        day = check_in.date()
        return bool(self.env['hr.leave'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state',       '=', 'validate'),
            ('date_from',   '<=', str(day) + ' 23:59:59'),
            ('date_to',     '>=', str(day) + ' 00:00:00'),
        ], limit=1))

    @api.model
    def _is_near_shift_end(self, check_in, config):
        """True when a lone check-in landed within 2h30 before shift end —
        i.e. it is really a forgotten checkout (→ anomalie, case 3)."""
        if not check_in or not config:
            return False
        TZ  = ZoneInfo("Africa/Casablanca")
        UTC = ZoneInfo("UTC")
        ci_local = check_in.replace(tzinfo=UTC).astimezone(TZ)
        ci_min   = ci_local.hour * 60 + ci_local.minute
        eff_start, eff_end = config.effective_times(ci_local.date())
        end_min  = int(eff_end * 60)
        # Night shift ends past midnight; a late-evening punch still counts.
        if eff_end < eff_start:
            end_min += 1440
            if ci_min < int(eff_start * 60):
                ci_min += 1440
        return ci_min >= end_min - self._ABSENCE_DELTA_MIN

    # ── break_start, break_end, break_duration ───────────────────────────────
    # Plain stored fields set via raw SQL from ZK device gap analysis
    break_start = fields.Datetime(string='Break Start')
    break_end   = fields.Datetime(string='Break End')
    break_duration = fields.Float(string='Break Duration (h)')

    # ── net_worked_hours — new field (span - break) ──────────────────────────
    # A NEW field separate from Odoo's native worked_hours.
    # Computed live from check_in, check_out, break_duration via ORM.
    # No raw SQL needed — ORM handles it automatically.
    net_worked_hours = fields.Float(
        string='Net Worked Hours',
        compute='_compute_net_worked_hours',
        store=False,  # always live, never stale
    )

    def _build_attendance_name(self, att):
        emp  = att.employee_id.name or "Unknown"
        date = att.check_in.strftime('%d/%m/%Y') if att.check_in else ''
        if att.check_in and att.check_out:
            h  = int(att.net_worked_hours)
            m  = int(round((att.net_worked_hours - h) * 60))
            return f"{emp} — {date} ({h:02d}:{m:02d})"
        elif att.check_in:
            return f"{emp} — {date} (open)"
        return emp

    def name_get(self):
        return [(att.id, self._build_attendance_name(att)) for att in self]

    def _compute_display_name(self):
        for att in self:
            att.display_name = self._build_attendance_name(att)

    @api.depends('check_in', 'check_out', 'break_duration', 'worked_hours', 'is_absent')
    def _compute_net_worked_hours(self):
        for att in self:
            if not att.check_in:
                att.net_worked_hours = 0.0
                continue
            # Absent / time-off placeholders span shift-start → shift-end but the
            # employee worked nothing. Never count that as worked hours.
            if att.is_absent:
                att.net_worked_hours = 0.0
                continue
            if not att.check_out:
                # Open record — use stored worked_hours (sum of closed sessions)
                att.net_worked_hours = att.worked_hours or 0.0
                continue
            span = (att.check_out - att.check_in).total_seconds() / 3600.0
            att.net_worked_hours = max(0.0, span - (att.break_duration or 0.0))

    @api.model
    def recompute_worked_hours_for_ids(self, ids):
        """Force update worked_hours in DB after raw SQL writes."""
        if not ids:
            return
        self.env.cr.execute(
            """
            UPDATE hr_attendance
               SET worked_hours = GREATEST(
                   0.0,
                   EXTRACT(EPOCH FROM (check_out - check_in)) / 3600.0
                   - COALESCE(break_duration, 0.0)
               ),
               write_date = NOW()
             WHERE id = ANY(%s)
               AND check_in  IS NOT NULL
               AND check_out IS NOT NULL
            """,
            (list(ids),)
        )

    # ── MAINTENANCE: clear breaks on records with no real completed session ──
    @api.model
    def action_clear_invalid_breaks(self):
        """Remove break_start/break_end/break_duration from records that have
        no genuine completed work session — i.e. open records (no check_out)
        or missing-checkout records (check_out == check_in). These can never
        contain a real break; any value there is a leftover phantom break."""
        cr = self.env.cr
        cr.execute(
            """
            UPDATE hr_attendance
               SET break_start    = NULL,
                   break_end      = NULL,
                   break_duration = 0.0,
                   write_date     = NOW()
             WHERE COALESCE(break_duration, 0.0) <> 0.0
               AND (check_out IS NULL OR check_out = check_in)
            """
        )
        count = cr.rowcount
        self.invalidate_model(['break_start', 'break_end', 'break_duration'])
        _logger.info("[CLEAN-BREAK] Cleared phantom break on %s record(s).", count)
        return count

    # ── HELPER: force-close all open records for an employee ─────────────────
    def close_open_records_for_employee(self, employee_id, by_shift_day, cutoff=0):
        cr = self.env.cr
        cr.execute(
            "SELECT id, check_in FROM hr_attendance "
            "WHERE employee_id = %s AND check_out IS NULL AND is_absent = FALSE",
            (employee_id,)
        )
        open_rows = cr.fetchall()
        if not open_rows:
            return

        emp_obj = self.env['hr.employee'].browse(employee_id)
        config  = emp_obj.shift_config_id if emp_obj else False
        touched = []

        for (rec_id, rec_check_in) in open_rows:
            if not rec_check_in:
                continue
            if cutoff and (rec_check_in.hour + rec_check_in.minute / 60) < cutoff:
                open_shift_day = (rec_check_in - timedelta(days=1)).date()
            else:
                open_shift_day = rec_check_in.date()

            if open_shift_day in by_shift_day:
                day_ts = sorted(by_shift_day[open_shift_day])
                # Only use even-indexed timestamps as checkouts
                # Odd count means last ts is a check-in — don't use it as checkout
                checkouts = [day_ts[i] for i in range(1, len(day_ts), 2)]
                if not checkouts:
                    # No checkout yet — leave open
                    continue
                best_out = max(checkouts)
                if best_out > rec_check_in + timedelta(minutes=5):
                    cr.execute(
                        "UPDATE hr_attendance SET check_out = %s, missing_checkout = FALSE WHERE id = %s",
                        (best_out, rec_id)
                    )
                    touched.append(rec_id)
                    _logger.info("[CLOSE] emp=%s: closed id=%s with OUT=%s", employee_id, rec_id, best_out)
                else:
                    _logger.info("[CLOSE] emp=%s: id=%s same/close timestamp — leaving open", employee_id, rec_id)
            elif self._is_near_shift_end(rec_check_in, config):
                # Case 3: the lone punch landed near shift end → treat it as a
                # checkout, blank the break, flag anomaly (no valid session).
                cr.execute(
                    "UPDATE hr_attendance SET check_out = check_in, missing_checkout = FALSE, "
                    "is_anomalie = TRUE, break_start = NULL, break_end = NULL, break_duration = 0 "
                    "WHERE id = %s",
                    (rec_id,)
                )
                touched.append(rec_id)
                _logger.warning("[CLOSE] emp=%s: stale id=%s flagged ANOMALIE", employee_id, rec_id)
            else:
                # Stale record from previous day — no checkout punch ever came.
                # Close at check-in (0 hours) and flag missing checkout.
                cr.execute(
                    "UPDATE hr_attendance SET check_out = check_in, missing_checkout = TRUE WHERE id = %s",
                    (rec_id,)
                )
                touched.append(rec_id)
                _logger.warning("[CLOSE] emp=%s: stale id=%s flagged missing", employee_id, rec_id)

        self.invalidate_model()
        self.recompute_statuses_for_ids(touched)

    # ── CRON: auto-close missing checkouts ───────────────────────────────────
    @api.model
    def cron_close_missing_checkout(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as dt

        now_utc   = fields.Datetime.now()
        TZ        = ZoneInfo("Africa/Casablanca")
        UTC       = ZoneInfo("UTC")
        now_local = now_utc.replace(tzinfo=UTC).astimezone(TZ)

        _logger.info("[AUTO-CLOSE] Running at %s (local: %s)", now_utc, now_local)

        cr = self.env.cr

        # Fetch all open non-absent records
        cr.execute(
            """
            SELECT a.id, a.employee_id, a.check_in
              FROM hr_attendance a
             WHERE a.check_out IS NULL
               AND a.check_in  IS NOT NULL
               AND a.is_absent = FALSE
             ORDER BY a.check_in
            """
        )
        rows = cr.fetchall()

        if not rows:
            _logger.info("[AUTO-CLOSE] No open records found.")
            return

        emp_ids   = list({r[1] for r in rows})
        employees = {e.id: e for e in self.env['hr.employee'].browse(emp_ids)}

        ids_missing  = []   # forgotten checkout → check_out = check_in
        ids_anomalie = []   # lone punch near shift end → anomalie (case 3)

        for rec_id, emp_id, check_in in rows:
            emp    = employees.get(emp_id)
            config = emp.shift_config_id if emp else False

            if not config:
                # No shift config — use fallback: close if open since yesterday
                cutoff = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5)
                if check_in < cutoff:
                    ids_missing.append(rec_id)
                    _logger.warning("[AUTO-CLOSE] No shift config — closing id=%s emp=%s", rec_id, emp_id)
                continue

            # Convert check_in to local
            check_in_local     = check_in.replace(tzinfo=UTC).astimezone(TZ)
            check_in_day       = check_in_local.date()

            # Effective shift start/end for that day (Saturday schedule if set)
            eff_start, eff_end = config.effective_times(check_in_day)

            # Calculate shift end + max_extra_hours in local time
            shift_end_minutes  = int(eff_end * 60)
            shift_end_h        = shift_end_minutes // 60
            shift_end_m        = shift_end_minutes % 60
            extra_hours        = config.max_extra_hours or 2.0
            extra_minutes      = int(extra_hours * 60)

            # Deadline = shift_end + max_extra_hours (local time today)
            deadline_minutes   = shift_end_minutes + extra_minutes

            # Build deadline datetime in local time for the check_in day
            deadline_local_h   = (deadline_minutes // 60) % 24
            deadline_local_m   = deadline_minutes % 60
            deadline_local     = dt.combine(
                check_in_day,
                time(deadline_local_h, deadline_local_m, 0),
                tzinfo=TZ,
            )

            # A night shift (end_time < start_time) ends the calendar day AFTER
            # check-in, so its shift-end/deadline lives on check_in_day + 1.
            # A day shift whose deadline merely rolls past midnight (e.g. a late
            # shift-end plus extra hours) is shifted forward the same way.
            if eff_end < eff_start or deadline_minutes >= 1440:
                deadline_local = deadline_local + timedelta(days=1)

            # Close if current local time is past the deadline
            if now_local > deadline_local:
                if self._is_near_shift_end(check_in, config):
                    ids_anomalie.append(rec_id)
                    _logger.info("[AUTO-CLOSE] id=%s %s — near shift end → ANOMALIE", rec_id, emp.name)
                else:
                    ids_missing.append(rec_id)
                    _logger.info(
                        "[AUTO-CLOSE] id=%s %s — shift_end=%02d:%02d + %.1fh — closing",
                        rec_id, emp.name, shift_end_h, shift_end_m, extra_hours
                    )
            else:
                _logger.debug(
                    "[AUTO-CLOSE] id=%s %s — deadline %s not reached yet",
                    rec_id, emp.name, deadline_local
                )

        if not ids_missing and not ids_anomalie:
            _logger.info("[AUTO-CLOSE] No records past deadline.")
            return

        if ids_missing:
            cr.execute(
                """
                UPDATE hr_attendance
                   SET check_out = check_in, missing_checkout = TRUE, write_date = NOW()
                 WHERE id = ANY(%s)
                """,
                (ids_missing,)
            )
        if ids_anomalie:
            cr.execute(
                """
                UPDATE hr_attendance
                   SET check_out = check_in, missing_checkout = FALSE, is_anomalie = TRUE,
                       break_start = NULL, break_end = NULL, break_duration = 0, write_date = NOW()
                 WHERE id = ANY(%s)
                """,
                (ids_anomalie,)
            )

        self.invalidate_model()
        self.recompute_statuses_for_ids(ids_missing + ids_anomalie)
        _logger.info("[AUTO-CLOSE] Closed %s missing + %s anomalie.", len(ids_missing), len(ids_anomalie))

    # ── CRON: close absent/timeoff records at shift end ──────────────────────
    @api.model
    def cron_close_absent_records(self):
        from zoneinfo import ZoneInfo as _ZI
        from datetime import datetime as _dt
        import datetime as _datetime

        now_utc   = fields.Datetime.now()
        TZ        = _ZI("Africa/Casablanca")
        UTC       = _ZI("UTC")
        now_local = now_utc.replace(tzinfo=UTC).astimezone(TZ)

        _logger.info("[CLOSE-ABSENT] Running at %s local=%s", now_utc, now_local)

        cr = self.env.cr

        cr.execute(
            "SELECT a.id, a.employee_id, a.check_in "
            "FROM hr_attendance a "
            "WHERE a.check_out IS NULL "
            "AND a.check_in IS NOT NULL "
            "AND a.is_absent = TRUE "
            "ORDER BY a.check_in"
        )
        rows = cr.fetchall()

        if not rows:
            _logger.info("[CLOSE-ABSENT] No open absent records found.")
            return

        emp_ids   = list({r[1] for r in rows})
        employees = {e.id: e for e in self.env['hr.employee'].browse(emp_ids)}
        ids_to_close = []

        for rec_id, emp_id, check_in in rows:
            emp    = employees.get(emp_id)
            config = emp.shift_config_id if emp else False

            if not config:
                ids_to_close.append((rec_id, check_in))
                continue

            check_in_local = check_in.replace(tzinfo=UTC).astimezone(TZ)
            check_in_day   = check_in_local.date()
            eff_start, eff_end = config.effective_times(check_in_day)
            shift_end_min  = int(eff_end * 60)
            shift_end_h    = shift_end_min // 60
            shift_end_m    = shift_end_min % 60

            shift_end_local = _dt.combine(
                check_in_day,
                _datetime.time(shift_end_h, shift_end_m, 0),
                tzinfo=TZ,
            )
            if eff_end < eff_start:
                shift_end_local += timedelta(days=1)

            if now_local >= shift_end_local:
                shift_end_utc = shift_end_local.astimezone(UTC).replace(tzinfo=None)
                ids_to_close.append((rec_id, shift_end_utc))
                _logger.info("[CLOSE-ABSENT] closing id=%s emp=%s at shift_end=%s",
                             rec_id, emp.name, shift_end_utc)

        if not ids_to_close:
            _logger.info("[CLOSE-ABSENT] No records past shift end.")
            return

        for rec_id, checkout_ts in ids_to_close:
            cr.execute(
                "UPDATE hr_attendance SET check_out = %s, write_date = NOW() WHERE id = %s",
                (checkout_ts, rec_id)
            )

        self.invalidate_model()
        self.recompute_statuses_for_ids([r[0] for r in ids_to_close])
        _logger.info("[CLOSE-ABSENT] Closed %s absent record(s).", len(ids_to_close))

    # ── CRON: refresh time-based statuses on today's records ─────────────────
    @api.model
    def cron_refresh_statuses(self):
        """Absence après-midi and Retard de pause depend on elapsed time
        (now_utc) but aren't triggered by an @api.depends, so they only refresh
        when a record is otherwise touched. Recompute today's real records so
        those statuses appear as soon as the grace elapses."""
        TZ  = ZoneInfo("Africa/Casablanca")
        UTC = ZoneInfo("UTC")
        today = datetime.now(TZ).date()
        day_start = datetime.combine(today, time(0, 0, 0), tzinfo=TZ).astimezone(UTC).replace(tzinfo=None)
        day_end   = datetime.combine(today, time(23, 59, 59), tzinfo=TZ).astimezone(UTC).replace(tzinfo=None)

        cr = self.env.cr
        cr.execute(
            "SELECT id FROM hr_attendance "
            "WHERE is_absent = FALSE AND check_in >= %s AND check_in <= %s",
            (day_start, day_end)
        )
        ids = [r[0] for r in cr.fetchall()]
        if ids:
            self.recompute_statuses_for_ids(ids)
        _logger.info("[REFRESH-STATUS] Recomputed %s record(s).", len(ids))