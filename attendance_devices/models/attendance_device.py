from odoo import models, fields, api
from zk import ZK
from datetime import timedelta, datetime, time, date
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import logging

_logger = logging.getLogger(__name__)

# Sentinel: distinguishes "caller did not prefetch" from "prefetched a failed
# read (None)" in _sync_device_attendance(records=...).
_UNSET = object()


# ─────────────────────────────────────────────────────────────
#  DAY GROUPING HELPER
# ─────────────────────────────────────────────────────────────
def cutoff_time(cutoff_hours: float) -> time:
    """Convert a float cutoff in hours (e.g. 2.5 == 02:30) into a time."""
    h = int(cutoff_hours)
    m = int(round((cutoff_hours - h) * 60))
    return time(h, m, 0)


def shift_date_of(ts: datetime, cutoff_hours: float) -> date:
    if cutoff_hours and (ts.hour + ts.minute / 60) < cutoff_hours:
        return (ts - timedelta(days=1)).date()
    return ts.date()


def collapse_near_duplicates(timestamps: list, threshold_secs: int = 60) -> list:
    """Merge punches that are less than `threshold_secs` apart.

    The ZK device can emit double punches (a quick double-tap, or two readers
    firing) a few seconds apart. set() only removes punches that are identical
    to the second, so a 5–30s double-tap survives and shifts the strict
    IN/OUT/IN/OUT parity the break engine relies on — inflating the break.
    Since gaps under 60s are never counted as a real break anyway, two punches
    closer than that are treated as the same event (the first one is kept).
    """
    if not timestamps:
        return []
    ordered = sorted(set(timestamps))
    cleaned = [ordered[0]]
    for ts in ordered[1:]:
        if (ts - cleaned[-1]).total_seconds() >= threshold_secs:
            cleaned.append(ts)
    return cleaned


def compute_breaks(all_ts: list, brk_win_start=None, brk_win_end=None) -> tuple:
    """
    Given a sorted list of timestamps [CI, CO, CI, CO, ...] from the ZK device,
    compute breaks using the strict alternating CI/CO pattern.

    brk_win_start / brk_win_end: UTC float hours from shift config (break window).
    - total_break_hours = sum of ALL gaps >= 60s
    - main_break_start/end = only set if a gap falls within the shift config break window
      If no gap is in the window → None, None

    Returns: (main_break_start, main_break_end, total_break_hours)
    """
    # Guard: merge near-duplicate punches so a double-tap can't desync parity.
    all_ts = collapse_near_duplicates(all_ts)
    if len(all_ts) < 2:
        return None, None, 0.0

    shift_start = all_ts[0]

    if brk_win_start is not None and brk_win_end is not None:
        lws = shift_start.replace(
            hour=int(brk_win_start) % 24,
            minute=int((brk_win_start % 1) * 60),
            second=0, microsecond=0
        )
        lwe = shift_start.replace(
            hour=int(brk_win_end) % 24,
            minute=int((brk_win_end % 1) * 60),
            second=0, microsecond=0
        )
        use_window = True
    else:
        lws = lwe = None
        use_window = False

    total_break_secs = 0.0
    main_break_start = None
    main_break_end   = None
    main_break_secs  = 0.0
    main_chosen      = False

    n = len(all_ts)
    i = 1
    while i < n - 1:
        if n % 2 == 0 and i == n - 2:
            break
        gap_start = all_ts[i]
        gap_end   = all_ts[i + 1]
        gap_secs  = (gap_end - gap_start).total_seconds()
        if gap_secs >= 60:
            total_break_secs += gap_secs
            if use_window:
                # The break must actually FALL INSIDE the window: either the
                # punch-out (gap_start) or the punch-in (gap_end) lands within
                # [lws, lwe]. A gap that merely straddles the window (out well
                # before 12:00 and back long after 14:00) is NOT a lunch break —
                # e.g. a double check-in at 08:51/08:55 then return at 19:03.
                in_window = (lws <= gap_start < lwe) or (lws < gap_end <= lwe)
                if in_window and not main_chosen:
                    main_break_start, main_break_end = gap_start, gap_end
                    main_break_secs  = gap_secs
                    main_chosen      = True
        i += 2

    return main_break_start, main_break_end, total_break_secs / 3600.0


def compute_breaks_from_sessions(sessions: list, brk_win_start=None, brk_win_end=None) -> tuple:
    """
    Session-based break computation.
    sessions = list of (check_in, check_out) — check_out can be None.

    brk_win_start / brk_win_end: UTC float hours from shift config (break window).
    - total_break_hours = sum of ALL gaps >= 60s (always calculated)
    - main_break_start/end = only set if a gap falls within the break window
      If no gap is within the window → None, None (break_start/end not shown)

    Returns: (main_break_start, main_break_end, total_break_hours)
    """
    if not sessions:
        return None, None, 0.0

    sessions = sorted(set(sessions), key=lambda s: s[0])
    clean = [list(sessions[0])]
    for ci, co in sessions[1:]:
        prev = clean[-1]
        if prev[1] and ci <= prev[1]:
            prev[1] = max(prev[1], co) if co else prev[1]
        else:
            clean.append([ci, co])
    sessions = [(ci, co) for ci, co in clean]

    shift_start = sessions[0][0]

    # Use shift config break window if provided, else no window (None,None = never match)
    if brk_win_start is not None and brk_win_end is not None:
        lws = shift_start.replace(
            hour=int(brk_win_start) % 24,
            minute=int((brk_win_start % 1) * 60),
            second=0, microsecond=0
        )
        lwe = shift_start.replace(
            hour=int(brk_win_end) % 24,
            minute=int((brk_win_end % 1) * 60),
            second=0, microsecond=0
        )
        use_window = True
    else:
        lws = lwe = None
        use_window = False

    total_break_secs = 0.0
    main_break_start = None
    main_break_end   = None
    main_break_secs  = 0.0
    main_chosen      = False

    for i in range(len(sessions) - 1):
        _, co_i    = sessions[i]
        ci_next, _ = sessions[i + 1]
        if co_i is None:
            continue
        gap_secs = (ci_next - co_i).total_seconds()
        if gap_secs >= 60:
            total_break_secs += gap_secs
            # Only assign break_start/end if gap is within the shift config window
            if use_window:
                # Break must fall inside the window (see compute_breaks): either
                # endpoint lands within [lws, lwe]. A gap that just straddles the
                # window is not a lunch break.
                in_window = (lws <= co_i < lwe) or (lws < ci_next <= lwe)
                if in_window and not main_chosen:
                    main_break_start, main_break_end = co_i, ci_next
                    main_break_secs  = gap_secs
                    main_chosen      = True

    return main_break_start, main_break_end, total_break_secs / 3600.0


def get_dept_shift(employee):
    """Build department_shift label for an employee."""
    dept  = employee.department_type or ''
    shift = employee.shift_type or ''
    if dept == 'warehouse' and shift:
        return 'Warehouse ' + shift.capitalize()
    labels = employee.env['hr.department.type'].get_label_map()
    return labels.get(dept, dept or 'Other')


class AttendanceDevice(models.Model):
    _name        = 'attendance.device'
    _description = 'Attendance Device'

    name               = fields.Char(string='Device Name', required=True)
    ip_address         = fields.Char(string='IP Address', required=True)
    port               = fields.Integer(string='Port', default=4370)
    location           = fields.Char(string='Location')
    last_sync          = fields.Datetime(string='Last Sync', readonly=True)
    timezone           = fields.Char(string='Timezone', default='Africa/Casablanca')
    night_shift_cutoff = fields.Float(
        string='Night Shift Cutoff',
        default=6.0,
        help=(
            'Timestamps earlier than this time of day are assigned to the PREVIOUS day.\n'
            'Enter as 24h time, e.g. 02:00, 03:00, 23:00.\n'
            'Example: cutoff=06:00 means 02:00 on Mar 4 is treated as Mar 3.\n'
            'Set to 00:00 to disable.'
        )
    )
    absence_lookback_days = fields.Integer(
        string='Absence Lookback (days)',
        default=7,
        help='How many past days to scan for missing attendance when syncing. Today is always checked regardless of this value.',
    )
    late_threshold_minutes = fields.Integer(
        string='Absence Threshold (minutes)',
        default=30,
        help='How many minutes after work_start before an employee is marked absent. e.g. 30 = absent if no check-in by work_start + 30min.',
    )
    department_type = fields.Selection(
        selection=lambda self: (
            self.env['hr.department.type'].get_selection()
            + [('all', 'All Departments')]
        ),
        string='Department',
        default='all',
        required=True,
        help='Only sync employees from this department. Use "All" to sync everyone.',
    )

    # Technical flag used by the form view: only admins (Settings access) may
    # edit the connection fields. HR users edit only the absence/shift settings.
    is_device_admin = fields.Boolean(
        string='Can Edit All Device Fields',
        compute='_compute_is_device_admin',
    )

    # depends('name') is a harmless field dependency that forces the web client
    # to evaluate this compute on brand-new (unsaved) records too — without it
    # the modifier would fall back to False and lock the fields on create.
    @api.depends('name')
    @api.depends_context('uid')
    def _compute_is_device_admin(self):
        is_admin = self.env.user.has_group('base.group_system') \
            or self.env.user.has_group('attendance_devices.group_attendance_device_admin')
        for rec in self:
            rec.is_device_admin = is_admin

    # ---------------------------------------------------------
    # DEVICE COMMUNICATION
    # ---------------------------------------------------------
    def get_data_from_device(self):
        """Fetch attendance logs from the device.

        Returns:
            list  — the records on success (may be empty if the device has none)
            None  — the read FAILED (device unreachable, timeout, partial read).

        The None vs [] distinction matters: callers must NOT advance last_sync
        on a failed read, otherwise punches made during the outage are filtered
        out as "older than last_sync" and lost forever.
        """
        self.ensure_one()
        if not self.ip_address or not self.port:
            return None
        return self._read_zk_punches(self.ip_address, self.port, self.timezone, self.name)

    @staticmethod
    def _read_zk_punches(ip_address, port, timezone, name=''):
        """Pure network read of one ZK device — NO ORM / cursor access, so it is
        safe to run in a worker thread. Takes plain values, returns a list of
        {badge_id, timestamp} dicts on success, or None on a failed read."""
        if not ip_address or not port:
            return None

        TZ      = ZoneInfo(timezone)
        zk      = ZK(ip_address, port=port, timeout=10, ommit_ping=True)
        conn    = None
        records = []

        try:
            _logger.info("[ZK] Connecting to %s:%s", ip_address, port)
            conn = zk.connect()
            conn.disable_device()
            for a in (conn.get_attendance() or []):
                user_id = getattr(a, "user_id", None)
                ts      = getattr(a, "timestamp", None)
                if user_id is None or ts is None:
                    continue
                badge = str(user_id)
                ts_local = ts.replace(tzinfo=TZ) if ts.tzinfo is None else ts.astimezone(TZ)
                records.append({"badge_id": badge, "timestamp": ts_local})
            _logger.info("[ZK] %s: %s records fetched", name or ip_address, len(records))
        except Exception as e:
            _logger.exception("[ZK] Connection error for %s: %s", name or ip_address, e)
            return None
        finally:
            if conn:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except Exception:
                    pass
        return records

    @api.model
    def _parallel_read_devices(self, devices):
        """Read several devices concurrently (network-bound). Extracts plain
        connection values in the main thread, then fans the socket reads out to
        a thread pool — the workers never touch the ORM. Returns
        {device_id: records_or_None}."""
        specs = [(d.id, d.ip_address, d.port, d.timezone, d.name) for d in devices]
        if len(specs) <= 1:
            return {sid: self._read_zk_punches(ip, port, tz, nm)
                    for sid, ip, port, tz, nm in specs}

        results = {}
        with ThreadPoolExecutor(max_workers=min(len(specs), 8)) as pool:
            futures = {
                pool.submit(self._read_zk_punches, ip, port, tz, nm): sid
                for sid, ip, port, tz, nm in specs
            }
            for fut in futures:
                sid = futures[fut]
                try:
                    results[sid] = fut.result()
                except Exception:
                    _logger.exception("[ZK] Parallel read failed for device %s", sid)
                    results[sid] = None
        return results

    # ---------------------------------------------------------
    # CORE SYNC LOGIC
    # ---------------------------------------------------------
    def _sync_device_attendance(self, cron=False, generate_absences=True, records=_UNSET):
        self.ensure_one()

        TZ            = ZoneInfo("Africa/Casablanca")
        UTC           = ZoneInfo("UTC")
        now_utc_naive = fields.Datetime.now()
        now_local     = now_utc_naive.replace(tzinfo=UTC).astimezone(TZ)
        today_local   = now_local.date()
        Employee      = self.env['hr.employee']
        Attendance    = self.env['hr.attendance']
        result        = []
        cutoff        = self.night_shift_cutoff or 0

        # ``records`` may be pre-fetched by the caller (parallel read). Only hit
        # the device here when it wasn't supplied.
        if records is _UNSET:
            records = self.get_data_from_device()

        # Failed read (device unreachable / timeout / partial). Do NOT advance
        # last_sync — otherwise punches made during the outage become "older than
        # last_sync" on the next run and are silently dropped.
        if records is None:
            _logger.warning("[SYNC] Device read failed for %s — generating absences only, last_sync unchanged.", self.name)
            # A no-show doesn't depend on the device, so still generate absences
            # even when the read fails. We're inside the advisory lock the caller
            # (cron / button) holds, so there's no race. last_sync stays put so
            # punches made during the outage aren't dropped on the next run.
            if generate_absences:
                self._generate_absences(result)
            if not cron:
                return self._open_result_wizard(
                    ["⚠️ Could not read from device — punches unchanged, absences generated."] + result
                )
            return False

        # If no last_sync, set it to now and skip — don't pull historical data
        if not self.last_sync:
            self.last_sync = now_utc_naive
            _logger.info("[SYNC] No last_sync set — initializing to now, skipping historical data.")
            if generate_absences:
                self._generate_absences(result)
            if not cron:
                return self._open_result_wizard(["First sync — timestamp initialized. Sync again to pull new data."])
            return True

        last_sync_utc = self.last_sync.replace(tzinfo=UTC)

        # ── Group ALL device records by badge ─────────────────────────────────
        # For each badge:
        #   - "new" timestamps: ts > last_sync (to detect if anything changed)
        #   - "by_day" timestamps: for each day that has new data, get ALL timestamps
        #     for that day (full day picture for correct break calculation)
        today_start_local = datetime.combine(today_local, time(0, 0, 0), tzinfo=TZ)
        today_start_utc   = today_start_local.astimezone(UTC).replace(tzinfo=None)

        grouped_new   = defaultdict(list)   # ts > last_sync
        grouped_by_day = defaultdict(lambda: defaultdict(list))  # badge → day → [ts]

        last_sync_naive = last_sync_utc.replace(tzinfo=None)

        for r in records:
            ts_utc = r['timestamp'].astimezone(UTC).replace(tzinfo=None)
            badge  = r['badge_id']
            # Track new timestamps (since last sync)
            if ts_utc > last_sync_naive:
                grouped_new[badge].append(ts_utc)
            # Group ALL timestamps by their SHIFT day (respects night_shift_cutoff)
            # This ensures night shift timestamps are correctly assigned to the right day
            ts_local = ts_utc.replace(tzinfo=UTC).astimezone(TZ).replace(tzinfo=None)
            shift_day = shift_date_of(ts_local, cutoff)
            grouped_by_day[badge][shift_day].append(ts_utc)

        # ── Lightweight verification of TODAY's stored records ────────────────
        # The main path below only (re)builds badges that have NEW punches since
        # last_sync. A punch that was missed on an earlier cycle — with no newer
        # punch after it — would otherwise never be re-examined. Compare each
        # badge's stored record for the current shift-day against the device
        # punches we ALREADY hold in memory (grouped_by_day, no extra device
        # read) and flag only the ones that disagree, so the per-cycle cost
        # stays tiny even at a 10-minute cadence.
        now_local_naive = now_local.replace(tzinfo=None)
        today_sday      = shift_date_of(now_local_naive, cutoff)
        verify_badges   = self._verify_today_records(grouped_by_day, today_sday, cutoff)

        if not grouped_new and not verify_badges:
            self.last_sync = now_utc_naive
            if generate_absences:
                self.env.cr.execute('SELECT 1')
                self._generate_absences(result)
            self._audit_today_pushes(grouped_by_day, today_sday, cutoff, result)
            if not cron:
                return self._open_result_wizard(["No new records since last sync."] + result)
            return True

        # Process badges with NEW punches, plus any flagged by verification.
        badges_with_new   = set(grouped_new.keys())
        badges_to_process = badges_with_new | verify_badges

        emp_domain = [('badge_id', 'in', list(badges_to_process))]
        if self.department_type and self.department_type != 'all':
            emp_domain.append(('department_type', '=', self.department_type))
        employees    = Employee.search(emp_domain)
        emp_by_badge = {e.badge_id: e for e in employees}

        for badge in badges_to_process:
            if badge not in emp_by_badge:
                _logger.warning("[SYNC] Badge %s not linked to any employee", badge)

        # For each badge, collect ALL timestamps for the shift-days we must
        # (re)build: days that received new punches, plus today's shift-day for
        # any badge the verification flagged. Using ALL timestamps for a day
        # keeps break calculation correct.
        grouped = defaultdict(list)
        for badge in badges_to_process:
            days_to_build = set()
            for ts in grouped_new.get(badge, []):
                ts_local = ts.replace(tzinfo=UTC).astimezone(TZ).replace(tzinfo=None)
                days_to_build.add(shift_date_of(ts_local, cutoff))
            if badge in verify_badges:
                days_to_build.add(today_sday)
            all_ts_for_badge = []
            for d in days_to_build:
                all_ts_for_badge.extend(grouped_by_day[badge].get(d, []))
            grouped[badge] = sorted(set(all_ts_for_badge))

        for badge, timestamps in grouped.items():
            employee = emp_by_badge.get(badge)
            if not employee:
                continue

            timestamps = collapse_near_duplicates(timestamps)

            by_shift_day = defaultdict(list)
            for ts in timestamps:
                sday = shift_date_of(ts, cutoff)
                by_shift_day[sday].append(ts)

            _logger.info(
                "[SYNC] %s: %s timestamps → %s day(s): %s",
                employee.name, len(timestamps), len(by_shift_day),
                {str(k): len(v) for k, v in by_shift_day.items()}
            )

            # Delete absent records for days we have real data
            for sday in by_shift_day:
                window_start = datetime.combine(sday, cutoff_time(cutoff))
                window_end   = window_start + timedelta(hours=24)
                self.env.cr.execute(
                    """
                    DELETE FROM hr_attendance
                     WHERE employee_id = %s
                       AND check_in   >= %s
                       AND check_in   <  %s
                       AND is_absent  = TRUE
                    """,
                    (employee.id, window_start, window_end)
                )

            Attendance.close_open_records_for_employee(
                employee.id, by_shift_day, cutoff
            )

            for sday, day_ts in sorted(by_shift_day.items()):
                all_ts = sorted(day_ts)

                window_start = datetime.combine(sday, cutoff_time(cutoff))
                window_end   = window_start + timedelta(hours=24)

                self.env.cr.execute(
                    """
                    SELECT id, check_in, check_out
                      FROM hr_attendance
                     WHERE employee_id = %s
                       AND check_in   >= %s
                       AND check_in   <  %s
                       AND is_absent  = FALSE
                     ORDER BY check_in ASC
                     LIMIT 1
                    """,
                    (employee.id, window_start, window_end)
                )
                row = self.env.cr.fetchone()

                if not row:
                    check_in_ts = all_ts[0]

                    # Never close before shift_end
                    config_i       = employee.shift_config_id
                    shift_end_ok_i = False
                    if config_i and len(all_ts) > 1:
                        _TZ_i = ZoneInfo("Africa/Casablanca")
                        _UTC_i = ZoneInfo("UTC")
                        last_local_i  = all_ts[-1].replace(tzinfo=_UTC_i).astimezone(_TZ_i)
                        # Convert config.end_time (UTC float) to local minutes (+60 for UTC+1)
                        shift_end_min_i = int(config_i.end_time * 60) + 60
                        last_min_i    = last_local_i.hour * 60 + last_local_i.minute
                        shift_end_ok_i = last_min_i >= shift_end_min_i
                    elif not config_i:
                        shift_end_ok_i = True

                    n_i = len(all_ts)
                    # Check if past deadline (shift_end + max_extra_hours)
                    past_deadline_i = False
                    if config_i and shift_end_ok_i:
                        _TZ_i2 = ZoneInfo("Africa/Casablanca")
                        _UTC_i2 = ZoneInfo("UTC")
                        last_local_i2   = all_ts[-1].replace(tzinfo=_UTC_i2).astimezone(_TZ_i2)
                        extra_min_i     = int((config_i.max_extra_hours or 2.0) * 60)
                        # Convert config.end_time (UTC float) to local minutes (+60 for UTC+1)
                        deadline_min_i  = int(config_i.end_time * 60) + 60 + extra_min_i
                        last_min_i2     = last_local_i2.hour * 60 + last_local_i2.minute
                        past_deadline_i = last_min_i2 >= deadline_min_i
                    elif not config_i:
                        past_deadline_i = True

                    if not shift_end_ok_i or n_i == 1:
                        check_out_ts = None
                    elif n_i % 2 == 0:
                        check_out_ts = all_ts[-1]
                    elif past_deadline_i:
                        check_out_ts = check_in_ts  # missing checkout
                    else:
                        check_out_ts = None  # within extra hours — keep open

                    break_start_dt, break_end_dt, brk = compute_breaks(
                        all_ts,
                        brk_win_start=config_i.break_start if config_i else None,
                        brk_win_end=config_i.break_end if config_i else None,
                    )

                    worked = 0.0
                    if check_out_ts and check_out_ts != check_in_ts:
                        span   = (check_out_ts - check_in_ts).total_seconds() / 3600.0
                        worked = max(0.0, span - brk)

                    self.env.cr.execute(
                        """
                        INSERT INTO hr_attendance
                            (employee_id, check_in, check_out,
                             break_start, break_end, break_duration, worked_hours,
                             missing_checkout, is_absent, department_type, shift_type,
                             department_shift,
                             create_uid, write_uid, create_date, write_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s,
                                %s, FALSE, %s, %s, %s,
                                %s, %s, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            employee.id, check_in_ts, check_out_ts,
                            break_start_dt, break_end_dt, brk, worked,
                            check_out_ts == check_in_ts,
                            employee.department_type or '',
                            employee.shift_type or '',
                            get_dept_shift(employee),
                            self.env.uid, self.env.uid,
                        )
                    )
                    new_id = self.env.cr.fetchone()[0]

                    # Recompute worked_hours + multi-status after the raw INSERT
                    if check_out_ts:
                        self.env['hr.attendance'].recompute_worked_hours_for_ids([new_id])
                    self.env['hr.attendance'].recompute_statuses_for_ids([new_id])

                    if check_out_ts:
                        result.append(
                            f"✅ {employee.name}  [{sday}]  "
                            f"IN {check_in_ts.strftime('%H:%M')} → "
                            f"OUT {check_out_ts.strftime('%H:%M')}  "
                            f"brk {break_start_dt.strftime('%H:%M') if break_start_dt else '-'}"
                            f"→{break_end_dt.strftime('%H:%M') if break_end_dt else '-'}  "
                            f"({worked:.2f}h)"
                        )
                    else:
                        result.append(
                            f"➡️  {employee.name}  [{sday}]  "
                            f"IN {check_in_ts.strftime('%H:%M')}  [open]"
                        )

                else:
                    rec_id, rec_check_in, rec_check_out = row

                    # ── Existing record found — always keep ONE record per day ────
                    # Since we now fetch ALL today's timestamps from the device,
                    # all_ts already contains the complete day picture.
                    # No need for raw_timestamps — just use all_ts directly.
                    all_known_ts = sorted(set(all_ts))

                    # Ensure existing check_in is always included
                    if rec_check_in not in all_known_ts:
                        all_known_ts = sorted(set([rec_check_in] + all_known_ts))

                    latest_ts = all_known_ts[-1]

                    # Skip if nothing has changed
                    if rec_check_out and latest_ts <= rec_check_out + timedelta(seconds=60):
                        continue
                    if latest_ts <= rec_check_in + timedelta(seconds=60):
                        continue

                    # Build sessions from alternating CI/CO pairs
                    n = len(all_known_ts)

                    sessions = []
                    for idx in range(0, len(all_known_ts) - 1, 2):
                        sessions.append((all_known_ts[idx], all_known_ts[idx + 1]))
                    if n % 2 == 1:
                        sessions.append((all_known_ts[-1], None))

                    config = employee.shift_config_id

                    # ── Breaks: use REAL device punches only ─────────────────
                    # Never compute breaks from the stored check_in. When an
                    # employee was absent, a placeholder sits at the shift-start
                    # time; if that early time is prepended ahead of the real
                    # punches it flips the CI/CO parity and the gap
                    # (shift-start → real check-in) is wrongly counted as a
                    # break. Real device timestamps (all_ts) avoid that.
                    brk_ts = collapse_near_duplicates(all_ts)
                    brk_sessions = []
                    for idx in range(0, len(brk_ts) - 1, 2):
                        brk_sessions.append((brk_ts[idx], brk_ts[idx + 1]))
                    if len(brk_ts) % 2 == 1:
                        brk_sessions.append((brk_ts[-1], None))

                    bs, be, brk = compute_breaks_from_sessions(
                        brk_sessions,
                        brk_win_start=config.break_start if config else None,
                        brk_win_end=config.break_end if config else None,
                    )

                    # Determine checkout based on shift_end rule:
                    # Never close the record before shift_end — keep open until then.
                    # After shift_end, if even count → last ts is real checkout.
                    # After shift_end, if odd count + past deadline → missing checkout.
                    shift_end_ok = False
                    if config:
                        _TZ  = ZoneInfo("Africa/Casablanca")
                        _UTC = ZoneInfo("UTC")
                        last_ts_local = all_known_ts[-1].replace(tzinfo=_UTC).astimezone(_TZ)
                        last_ts_min   = last_ts_local.hour * 60 + last_ts_local.minute
                        _, eff_end = config.effective_times(sday)
                        # Convert shift end (UTC float) to local minutes (+60 for UTC+1)
                        shift_end_local_min = int(eff_end * 60) + 60
                        shift_end_ok  = last_ts_min >= shift_end_local_min
                    else:
                        shift_end_ok  = True  # no config — use timestamp as-is

                    # Determine if we're past shift_end + max_extra_hours
                    past_deadline = False
                    if config and shift_end_ok:
                        _TZ2  = ZoneInfo("Africa/Casablanca")
                        _UTC2 = ZoneInfo("UTC")
                        last_local2     = all_known_ts[-1].replace(tzinfo=_UTC2).astimezone(_TZ2)
                        extra_min       = int((config.max_extra_hours or 2.0) * 60)
                        _, eff_end2 = config.effective_times(sday)
                        # Convert shift end (UTC float) to local minutes (+60 for UTC+1)
                        deadline_min    = int(eff_end2 * 60) + 60 + extra_min
                        last_min2       = last_local2.hour * 60 + last_local2.minute
                        past_deadline   = last_min2 >= deadline_min
                    elif not config:
                        past_deadline   = True

                    if not shift_end_ok:
                        # Before shift end — keep record open
                        new_check_out = None
                    elif n % 2 == 0:
                        # Even count → last timestamp is real checkout
                        new_check_out = all_known_ts[-1]
                    elif past_deadline:
                        # Odd count + past deadline → missing checkout
                        new_check_out = rec_check_in  # check_out = check_in
                    else:
                        # Odd count but within extra hours window → keep open
                        new_check_out = None

                    worked = 0.0
                    if new_check_out and new_check_out != rec_check_in:
                        span   = (new_check_out - rec_check_in).total_seconds() / 3600.0
                        worked = max(0.0, span - brk)
                    elif not new_check_out:
                        # Open — compute worked hours from closed sessions only
                        for ci_s, co_s in sessions:
                            if co_s:
                                worked += (co_s - ci_s).total_seconds() / 3600.0

                    self.env.cr.execute(
                        """
                        UPDATE hr_attendance
                           SET check_out        = %s,
                               break_start      = %s,
                               break_end        = %s,
                               break_duration   = %s,
                               worked_hours     = %s,
                               missing_checkout = %s,
                               write_uid        = %s,
                               write_date       = NOW()
                         WHERE id = %s
                        """,
                        (new_check_out, bs, be, brk, worked,
                         new_check_out is not None and new_check_out == rec_check_in,
                         self.env.uid, rec_id)
                    )

                    # Force recompute worked_hours after raw SQL UPDATE
                    if new_check_out:
                        self.env['hr.attendance'].recompute_worked_hours_for_ids([rec_id])
                    else:
                        # Open record — update worked_hours directly in DB
                        self.env.cr.execute(
                            "UPDATE hr_attendance SET worked_hours = %s, write_date = NOW() WHERE id = %s",
                            (worked, rec_id)
                        )

                    if new_check_out:
                        result.append(
                            f"✅ {employee.name}  [{sday}]  "
                            f"IN {rec_check_in.strftime('%H:%M')} → "
                            f"OUT {new_check_out.strftime('%H:%M')}  "
                            f"brk={round(brk*60)}min  ({worked:.2f}h)"
                        )
                    else:
                        result.append(
                            f"⏳ {employee.name}  [{sday}]  "
                            f"IN {rec_check_in.strftime('%H:%M')} [open — {n} ts]"
                        )

                Attendance.invalidate_model()


        # Optionally generate absences (disabled when cron runs separately)
        if generate_absences:
            self.env.cr.execute('SELECT 1')  # force flush
            self._generate_absences(result)

        # Post-sync audit: confirm every push of the day was created & processed.
        self._audit_today_pushes(grouped_by_day, today_sday, cutoff, result)

        self.last_sync = now_utc_naive
        if not cron:
            return self._open_result_wizard(result)
        return True

    # ---------------------------------------------------------
    # VERIFY: reconcile today's stored records vs device punches
    # ---------------------------------------------------------
    def _verify_today_records(self, grouped_by_day, today_sday, cutoff):
        """Return the set of badges whose stored record for ``today_sday``
        disagrees with the device punches read this cycle.

        Deliberately cheap so it can run every sync (10-min cron) without
        loading the system: ONE employee search + ONE batched SQL over today's
        window, then an in-memory comparison. No extra device communication
        (re-uses ``grouped_by_day``) and no per-badge queries.

        A badge is flagged only when something is genuinely missing from the
        stored record:
          * the device has punches today but nothing was stored at all, or
          * the device's latest punch is newer than what the record reflects
            (a checkout / break punch that the earlier sync skipped).
        Records that already match are left untouched.
        """
        # Badges with at least one punch on the current shift-day.
        today_badges = [b for b, days in grouped_by_day.items() if days.get(today_sday)]
        if not today_badges:
            return set()

        emp_domain = [('badge_id', 'in', today_badges)]
        if self.department_type and self.department_type != 'all':
            emp_domain.append(('department_type', '=', self.department_type))
        employees = self.env['hr.employee'].search(emp_domain)
        if not employees:
            return set()
        emp_by_badge = {e.badge_id: e for e in employees}

        window_start = datetime.combine(today_sday, cutoff_time(cutoff))
        window_end   = window_start + timedelta(hours=24)

        # One query for every employee's real (non-absent) record today.
        self.env.cr.execute(
            """
            SELECT employee_id, check_in, check_out
              FROM hr_attendance
             WHERE employee_id = ANY(%s)
               AND check_in   >= %s
               AND check_in   <  %s
               AND is_absent  = FALSE
             ORDER BY check_in ASC
            """,
            (employees.ids, window_start, window_end)
        )
        stored = {}
        for emp_id, ci, co in self.env.cr.fetchall():
            stored.setdefault(emp_id, (ci, co))   # earliest record per employee

        TOL           = timedelta(seconds=60)
        verify_badges = set()
        for badge in today_badges:
            employee = emp_by_badge.get(badge)
            if not employee:
                continue
            device_ts = collapse_near_duplicates(sorted(set(grouped_by_day[badge][today_sday])))
            if not device_ts:
                continue
            dev_last = device_ts[-1]
            rec      = stored.get(employee.id)
            if rec is None:
                # Device shows punches today but nothing was stored → skipped.
                verify_badges.add(badge)
                continue
            rec_ci, rec_co = rec
            ref_last = rec_co if (rec_co and rec_co != rec_ci) else rec_ci
            if dev_last > ref_last + TOL:
                # A later device punch is not reflected in the stored record.
                verify_badges.add(badge)

        if verify_badges:
            _logger.info(
                "[VERIFY] %s: %s record(s) out of sync for %s, reconciling: %s",
                self.name, len(verify_badges), today_sday, sorted(verify_badges)
            )
        return verify_badges

    # ---------------------------------------------------------
    # AUDIT: confirm every push of the day was created & processed
    # ---------------------------------------------------------
    def _audit_today_pushes(self, grouped_by_day, today_sday, cutoff, result):
        """Post-sync self-check for TODAY's shift-day: every device push should
        now be reflected in a stored, processed record.

        Runs after the whole sync (processing + absence generation) is done, so
        anything it still flags is genuinely stuck — not just pending. Appends a
        one-line summary to ``result`` (shown in the sync wizard) and logs a
        warning per unresolved badge (visible in the cron logs).

        A badge is reported only when a push was really dropped:
          * the badge has punches today but NO record was created, or
          * the record is closed but its checkout is older than the device's
            latest punch (a checkout/break punch that never made it in).
        Records that are legitimately open (before shift end / within the extra
        hours window) or closed as a missing-checkout are counted as OK.
        """
        today_badges = [b for b, days in grouped_by_day.items() if days.get(today_sday)]
        if not today_badges:
            result.append("🔎 Vérification: aucun pointage aujourd'hui.")
            return {'punches': 0, 'badges': 0, 'ok': 0, 'issues': []}

        # Resolve badge → employee WITHOUT the device's department filter so we
        # can tell "no employee at all" (a real problem) apart from "belongs to
        # another device's department" (not this sync's job).
        employees    = self.env['hr.employee'].search([('badge_id', 'in', today_badges)])
        emp_by_badge = {e.badge_id: e for e in employees}

        window_start = datetime.combine(today_sday, cutoff_time(cutoff))
        window_end   = window_start + timedelta(hours=24)
        self.env.cr.execute(
            """
            SELECT employee_id, check_in, check_out
              FROM hr_attendance
             WHERE employee_id = ANY(%s)
               AND check_in   >= %s
               AND check_in   <  %s
               AND is_absent  = FALSE
             ORDER BY check_in ASC
            """,
            (employees.ids or [0], window_start, window_end)
        )
        stored = {}
        for emp_id, ci, co in self.env.cr.fetchall():
            stored.setdefault(emp_id, (ci, co))   # earliest real record per employee

        dept_filter   = self.department_type if (self.department_type and self.department_type != 'all') else None
        TOL           = timedelta(seconds=60)
        total_punches = 0
        n_badges      = 0
        ok            = 0
        issues        = []

        for badge in today_badges:
            device_ts = collapse_near_duplicates(sorted(set(grouped_by_day[badge][today_sday])))
            if not device_ts:
                continue

            employee = emp_by_badge.get(badge)
            if not employee:
                total_punches += len(device_ts)
                n_badges      += 1
                issues.append(f"⚠️ Badge {badge}: {len(device_ts)} pointage(s) — aucun employé lié.")
                continue

            # Managed by another device's department → not this sync's job.
            if dept_filter and employee.department_type != dept_filter:
                continue

            total_punches += len(device_ts)
            n_badges      += 1
            dev_last = device_ts[-1]
            rec      = stored.get(employee.id)
            if rec is None:
                issues.append(
                    f"❌ {employee.name}: {len(device_ts)} pointage(s) device, "
                    f"aucun enregistrement créé."
                )
                continue

            rec_ci, rec_co = rec
            # Closed = a real checkout (co present and different from the check-in).
            # co == ci is a deliberate missing-checkout; co NULL is still open —
            # both are fine, later punches there aren't a dropped checkout.
            closed = bool(rec_co and rec_co != rec_ci)
            if closed and dev_last > rec_co + TOL:
                issues.append(
                    f"❌ {employee.name}: dernier pointage "
                    f"{dev_last.strftime('%H:%M')} non reflété "
                    f"(clôturé à {rec_co.strftime('%H:%M')})."
                )
                continue

            ok += 1

        if issues:
            result.append(
                f"🔎 Vérification: {total_punches} pointage(s), {n_badges} badge(s) — "
                f"{ok} OK, {len(issues)} à revoir:"
            )
            result.extend("   " + m for m in issues)
            _logger.warning(
                "[AUDIT] %s: %s/%s badge(s) unresolved for %s: %s",
                self.name, len(issues), n_badges, today_sday, issues
            )
        else:
            result.append(
                f"🔎 Vérification: {total_punches} pointage(s), {n_badges} badge(s) — "
                f"tous créés et traités ✅"
            )
            _logger.info(
                "[AUDIT] %s: all %s badge(s) reconciled for %s.",
                self.name, n_badges, today_sday
            )
        return {'punches': total_punches, 'badges': n_badges, 'ok': ok, 'issues': issues}

    # ---------------------------------------------------------
    # ABSENCE GENERATION
    # ---------------------------------------------------------
    def _generate_absences(self, result):
        TZ        = ZoneInfo("Africa/Casablanca")
        UTC       = ZoneInfo("UTC")
        now_local = datetime.now(TZ)
        today     = now_local.date()
        now_minutes     = now_local.hour * 60 + now_local.minute
        lookback        = max(1, self.absence_lookback_days or 7)
        extra_threshold = self.late_threshold_minutes or 30

        emp_domain = [
            ('active',          '=', True),
            ('shift_config_id', '!=', False),
        ]
        if self.department_type and self.department_type != 'all':
            emp_domain.append(('department_type', '=', self.department_type))
        all_employees = self.env['hr.employee'].search(emp_domain)
        if not all_employees:
            return

        cr      = self.env.cr
        emp_ids = all_employees.ids
        inserts = []

        today_start_utc = datetime.combine(
            today, time(0, 0, 0), tzinfo=TZ
        ).astimezone(UTC).replace(tzinfo=None)

        today_end_utc = datetime.combine(
            today, time(23, 59, 59), tzinfo=TZ
        ).astimezone(UTC).replace(tzinfo=None)

        cr.execute(
            """
            SELECT employee_id, is_absent
              FROM hr_attendance
             WHERE employee_id = ANY(%s)
               AND check_in   >= %s
               AND check_in   <= %s
            """,
            (emp_ids, today_start_utc, today_end_utc)
        )
        today_real   = set()
        today_absent = set()
        for emp_id, is_absent_flag in cr.fetchall():
            if is_absent_flag:
                today_absent.add(emp_id)
            else:
                today_real.add(emp_id)

        # If employee has a real check-in today, delete any absent/timeoff record for today
        # This handles the case where sync ran on a different device and didn't clean up
        if today_real:
            cr.execute(
                """
                DELETE FROM hr_attendance
                 WHERE employee_id = ANY(%s)
                   AND check_in   >= %s
                   AND check_in   <= %s
                   AND is_absent  = TRUE
                """,
                (list(today_real), today_start_utc, today_end_utc)
            )
            today_absent -= today_real  # remove from absent set too

        # Pre-fetch approved leaves
        leave_days = set()
        try:
            cr.execute(
                """
                SELECT employee_id,
                       date_from::date,
                       date_to::date
                  FROM hr_leave
                 WHERE employee_id = ANY(%s)
                   AND state       = 'validate'
                """,
                (emp_ids,)
            )
            for emp_id, leave_from, leave_to in cr.fetchall():
                d = leave_from
                while d <= leave_to:
                    leave_days.add((emp_id, d))
                    d += timedelta(days=1)
        except Exception:
            _logger.warning("[ABSENT] Could not fetch leave data — skipping leave check")

        # PHASE 1: Today — skip Sundays
        if today.weekday() == 6:
            _logger.info("[ABSENT] Today is Sunday — skipping Phase 1 absence generation")
            all_employees_phase1 = self.env['hr.employee']  # empty
        else:
            all_employees_phase1 = all_employees

        today_is_saturday = today.weekday() == 5
        for emp in all_employees_phase1:
            config = emp.shift_config_id
            # Saturday: a shift that doesn't declare a Saturday schedule is off
            # that day → never generate an absence for it.
            if today_is_saturday and not config.works_saturday:
                continue
            eff_start, eff_end = config.effective_times(today)
            work_start_minutes = int(eff_start * 60)
            work_start_h       = work_start_minutes // 60
            work_start_m       = work_start_minutes % 60

            on_leave = (emp.id, today) in leave_days
            # Congé (approved leave) can be marked as soon as the shift starts;
            # a no-show is only marked ABSENT within 1h of shift end, so someone
            # who simply hasn't punched yet is never shown as absence early.
            if on_leave:
                threshold_m = work_start_minutes
            elif eff_end > eff_start:                            # day shift
                # A no-show becomes Absence once he's past shift start + the late
                # tolerance with still no punch. If he later punches, the real
                # record replaces this placeholder and is classed Retard (late) or,
                # past shift start + Absence matinale delay, Absence matinale.
                tolerance   = config.late_tolerance_minutes or 30
                threshold_m = work_start_minutes + tolerance
            else:                                                # night shift
                threshold_m = work_start_minutes + extra_threshold

            if now_minutes <= threshold_m:
                continue
            if emp.id in today_real:
                continue
            if emp.id in today_absent:
                continue

            expected_utc = datetime.combine(
                today,
                time(work_start_h, work_start_m, 0),
                tzinfo=TZ,
            ).astimezone(UTC).replace(tzinfo=None)

            if on_leave:
                inserts.append((emp.id, expected_utc, self.env.uid, 'timeoff'))
                result.append(f"🏖️ {emp.name}  [{today}]  TIME OFF")
            else:
                inserts.append((emp.id, expected_utc, self.env.uid, 'absent'))
                result.append(f"🔴 {emp.name}  [{today}]  ABSENT")

        # PHASE 2: Past days
        cr.execute(
            """
            SELECT DISTINCT employee_id
              FROM hr_attendance
             WHERE employee_id = ANY(%s)
               AND is_absent   = FALSE
            """,
            (emp_ids,)
        )
        has_real_checkin = {row[0] for row in cr.fetchall()}
        past_employees   = all_employees.filtered(lambda e: e.id in has_real_checkin)

        if past_employees:
            global_scan_start = today - timedelta(days=lookback)

            window_start_utc = datetime.combine(
                global_scan_start, time(0, 0, 0), tzinfo=TZ
            ).astimezone(UTC).replace(tzinfo=None)

            cr.execute(
                """
                SELECT employee_id, check_in, is_absent
                  FROM hr_attendance
                 WHERE employee_id = ANY(%s)
                   AND check_in   >= %s
                   AND check_in   <  %s
                """,
                (past_employees.ids, window_start_utc, today_start_utc)
            )
            past_rows = cr.fetchall()

            past_real   = set()
            past_absent = set()
            for emp_id, check_in_utc, is_absent_flag in past_rows:
                local_date = check_in_utc.replace(tzinfo=UTC).astimezone(TZ).date()
                if is_absent_flag:
                    past_absent.add((emp_id, local_date))
                else:
                    past_real.add((emp_id, local_date))

            for emp in past_employees:
                config = emp.shift_config_id

                d = global_scan_start
                while d < today:
                    # Skip Sundays, and Saturdays for shifts that don't work them.
                    if d.weekday() == 6 or (d.weekday() == 5 and not config.works_saturday):
                        d += timedelta(days=1)
                        continue
                    eff_start, _ = config.effective_times(d)
                    work_start_minutes = int(eff_start * 60)
                    work_start_h       = work_start_minutes // 60
                    work_start_m       = work_start_minutes % 60
                    key = (emp.id, d)
                    if key not in past_real and key not in past_absent:
                        expected_utc = datetime.combine(
                            d,
                            time(work_start_h, work_start_m, 0),
                            tzinfo=TZ,
                        ).astimezone(UTC).replace(tzinfo=None)
                        if key in leave_days:
                            inserts.append((emp.id, expected_utc, self.env.uid, 'timeoff'))
                            result.append(f"🏖️ {emp.name}  [{d}]  TIME OFF")
                        else:
                            inserts.append((emp.id, expected_utc, self.env.uid, 'absent'))
                            result.append(f"🔴 {emp.name}  [{d}]  ABSENT")
                    d += timedelta(days=1)

        if not inserts:
            _logger.info("[ABSENT] Nothing to insert.")
            return

        emp_dept       = {emp.id: emp.department_type or '' for emp in all_employees}
        emp_shift      = {emp.id: emp.shift_type or '' for emp in all_employees}
        emp_dept_shift = {emp.id: get_dept_shift(emp) for emp in all_employees}

        cr.executemany(
            """
            INSERT INTO hr_attendance
                (employee_id, check_in, check_out,
                 is_absent, missing_checkout,
                 worked_hours, break_duration,
                 department_type, shift_type, department_shift,
                 create_uid, write_uid,
                 create_date, write_date)
            VALUES (%s, %s, NULL,
                    TRUE, FALSE,
                    0.0, 0.0,
                    %s, %s, %s,
                    %s, %s,
                    NOW(), NOW())
            ON CONFLICT (employee_id, check_in) WHERE is_absent DO NOTHING
            """,
            [(emp_id, chk,
              emp_dept.get(emp_id, ''),
              emp_shift.get(emp_id, ''),
              emp_dept_shift.get(emp_id, ''),
              uid, uid)
             for emp_id, chk, uid, status in inserts]
        )

        # Fetch the ids just inserted and compute their statuses (absence_full).
        cr.execute(
            "SELECT id FROM hr_attendance WHERE is_absent = TRUE AND (employee_id, check_in) IN %s",
            (tuple((emp_id, chk) for emp_id, chk, uid, status in inserts),)
        )
        absent_ids = [r[0] for r in cr.fetchall()]

        self.env['hr.attendance'].invalidate_model()
        self.env['hr.attendance'].recompute_statuses_for_ids(absent_ids)
        _logger.info("[ABSENT] Inserted %s record(s).", len(inserts))


    # ---------------------------------------------------------
    # BUTTON: Manual sync (single device)
    # ---------------------------------------------------------
    def action_sync_device(self):
        self.ensure_one()
        # Take the SAME advisory lock the cron / "Sync All" use, so a manual
        # single-device sync can never run concurrently with them. Without this
        # an admin clicking Sync while the cron is running could insert a second
        # attendance row for the same employee/day (there is no DB uniqueness
        # guarantee on shift-day).
        self.env.cr.execute("SELECT pg_try_advisory_lock(987654321)")
        if not self.env.cr.fetchone()[0]:
            return self._open_result_wizard(
                ["⚠️ A sync is already running — please wait a moment and try again."]
            )
        try:
            return self._sync_device_attendance(cron=False)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(987654321)")

    # ---------------------------------------------------------
    # BUTTON: Sync ALL devices
    # ---------------------------------------------------------
    @api.model
    def action_sync_all_devices(self):
        # Use PostgreSQL advisory lock to prevent concurrent syncs
        self.env.cr.execute("SELECT pg_try_advisory_lock(987654321)")
        locked = self.env.cr.fetchone()[0]
        if not locked:
            return {
                'type':      'ir.actions.act_window',
                'name':      'Sync All Result',
                'res_model': 'attendance.device.sync.wizard',
                'view_mode': 'form',
                'target':    'new',
                'context': {
                    'default_message': "⚠️ Sync already running — please wait and try again.",
                },
            }
        try:
            devices = self.search([])
            result  = []
            # Read every device in parallel (network I/O), then process the
            # results serially with the same per-device logic as before.
            prefetched = self._parallel_read_devices(devices)
            for device in devices:
                try:
                    with self.env.cr.savepoint():
                        device._sync_device_attendance(
                            cron=True, records=prefetched.get(device.id))
                    result.append(f"✅ {device.name} — synced")
                except Exception as e:
                    result.append(f"❌ {device.name} — sync ERROR: {e}")
                    _logger.exception("[SYNC ALL] Failed for %s", device.name)
            return {
                'type':      'ir.actions.act_window',
                'name':      'Sync All Result',
                'res_model': 'attendance.device.sync.wizard',
                'view_mode': 'form',
                'target':    'new',
                'context': {
                    'default_message': "\n".join(result) if result else "All devices synced.",
                },
            }
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(987654321)")


    # ---------------------------------------------------------
    # BUTTON (list view): Sync only the SELECTED devices
    # ---------------------------------------------------------
    def action_sync_selected(self):
        """Sync only the devices ticked in the list view (self = selection)."""
        if not self:
            return self._open_result_wizard(["⚠️ Aucun appareil sélectionné."])
        # Same advisory lock the cron / single-device sync use, so this can never
        # run concurrently with them and insert duplicate attendance rows.
        self.env.cr.execute("SELECT pg_try_advisory_lock(987654321)")
        if not self.env.cr.fetchone()[0]:
            return self._open_result_wizard(
                ["⚠️ Une synchronisation est déjà en cours — veuillez réessayer dans un instant."]
            )
        try:
            result     = []
            prefetched = self._parallel_read_devices(self)
            for device in self:
                try:
                    with self.env.cr.savepoint():
                        device._sync_device_attendance(
                            cron=True, records=prefetched.get(device.id))
                    result.append(f"✅ {device.name} — synchronisé")
                except Exception as e:
                    result.append(f"❌ {device.name} — ERREUR : {e}")
                    _logger.exception("[SYNC SELECTED] Failed for %s", device.name)
            return self._open_result_wizard(result or ["Appareils synchronisés."])
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(987654321)")

    # ---------------------------------------------------------
    # CRON — all devices
    # ---------------------------------------------------------
    @api.model
    def cron_sync_devices(self):
        # Use advisory lock to prevent concurrent syncs
        self.env.cr.execute("SELECT pg_try_advisory_lock(987654321)")
        if not self.env.cr.fetchone()[0]:
            _logger.info("[CRON] Sync already running — skipping this cycle")
            return
        try:
            devices = self.search([])
            # Read every device in parallel (network I/O) before processing, so
            # the 5 device reads overlap instead of running back-to-back.
            prefetched = self._parallel_read_devices(devices)
            for device in devices:
                try:
                    with self.env.cr.savepoint():
                        # Same process as the manual "Sync All" button: pull punches
                        # AND generate absences in one pass, under the advisory lock.
                        device._sync_device_attendance(
                            cron=True, generate_absences=True,
                            records=prefetched.get(device.id))
                except Exception:
                    _logger.exception("[CRON] Sync failed for %s", device.name)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(987654321)")

    @api.model
    def cron_generate_absences(self):
        # Step 1: Clean up stale absent records where real check-in now exists
        try:
            TZ  = ZoneInfo("Africa/Casablanca")
            UTC = ZoneInfo("UTC")
            now_local       = datetime.now(TZ)
            today           = now_local.date()
            today_start_utc = datetime.combine(
                today, time(0, 0, 0), tzinfo=TZ
            ).astimezone(UTC).replace(tzinfo=None)
            today_end_utc   = datetime.combine(
                today, time(23, 59, 59), tzinfo=TZ
            ).astimezone(UTC).replace(tzinfo=None)

            self.env.cr.execute(
                "DELETE FROM hr_attendance a "
                "WHERE a.is_absent = TRUE "
                "AND a.check_in >= %s AND a.check_in <= %s "
                "AND EXISTS ("
                "    SELECT 1 FROM hr_attendance r "
                "    WHERE r.employee_id = a.employee_id "
                "    AND r.is_absent = FALSE "
                "    AND r.check_in >= %s AND r.check_in <= %s"
                ")",
                (today_start_utc, today_end_utc,
                 today_start_utc, today_end_utc)
            )
        except Exception:
            _logger.exception("[ABSENT CRON] Stale absent cleanup failed")

        # Step 2: Generate absent/timeoff records for all devices
        for device in self.search([]):
            try:
                with self.env.cr.savepoint():
                    result = []
                    device._generate_absences(result)
                    if result:
                        _logger.info("[ABSENT CRON] %s: %s", device.name, "; ".join(result))
            except Exception:
                _logger.exception("[ABSENT CRON] Failed for %s", device.name)

    # ---------------------------------------------------------
    # CRON — single device by ID
    # ---------------------------------------------------------
    @api.model
    def cron_sync_device_by_id(self, device_id):
        device = self.browse(device_id)
        if not device.exists():
            _logger.warning("[CRON] Device ID %s not found.", device_id)
            return
        # Serialize against the all-device cron / manual sync via the shared lock.
        self.env.cr.execute("SELECT pg_try_advisory_lock(987654321)")
        if not self.env.cr.fetchone()[0]:
            _logger.info("[CRON] Sync already running — skipping device %s", device_id)
            return
        try:
            with self.env.cr.savepoint():
                device._sync_device_attendance(cron=True)
        except Exception:
            _logger.exception("[CRON] Sync failed for %s", device.name)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(987654321)")

    # ---------------------------------------------------------
    # RESULT WIZARD
    # ---------------------------------------------------------
    def _open_result_wizard(self, result):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sync Result',
            'res_model': 'attendance.device.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_message': "\n".join(result) if result else "No new attendance records.",
            },
        }


class AttendanceDeviceSyncWizard(models.TransientModel):
    _name        = 'attendance.device.sync.wizard'
    _description = 'Attendance Device Sync Wizard'

    message = fields.Text(readonly=True)
    def _open_result_wizard(self, result):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sync Result',
            'res_model': 'attendance.device.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_message': "\n".join(result) if result else "Done.",
            },
        }