from odoo import models, fields, api
from odoo.exceptions import UserError
from psycopg2.extras import execute_values
from zk import ZK
from datetime import timedelta, datetime, time, date
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import logging

from odoo.addons.base.models.res_partner import _tz_get

from .tz_utils import (
    get_attendance_tz, get_attendance_tz_name, float_to_time,
    local_to_utc, utc_to_local,
)

_logger = logging.getLogger(__name__)

# Sentinel: distinguishes "caller did not prefetch" from "prefetched a failed
# read" in _sync_device_attendance(read=...).
_UNSET = object()

# Advisory lock shared by every sync entry point (cron, buttons).
SYNC_LOCK_KEY = 987654321

# With an ODD number of punches, a gap shorter than this is treated as an
# accidental double punch and dropped to restore the IN/OUT parity.
DOUBLE_PUNCH_SECS = 5 * 60

# Statuses the old sync set wrongly when it lost the checkout. They are cleared
# from the sticky locked_status_codes when a rebuild finds the real checkout.
STALE_CODES = {'missing_checkout', 'anomalie'}


# ─────────────────────────────────────────────────────────────
#  DAY GROUPING HELPERS
# ─────────────────────────────────────────────────────────────
def cutoff_time(cutoff_hours: float) -> time:
    """Convert a float cutoff in hours (e.g. 2.5 == 02:30) into a time."""
    return float_to_time(cutoff_hours)


def shift_date_of(ts: datetime, cutoff_hours: float) -> date:
    """Shift-day of a LOCAL naive datetime (before the cutoff = previous day)."""
    if cutoff_hours and (ts.hour + ts.minute / 60) < cutoff_hours:
        return (ts - timedelta(days=1)).date()
    return ts.date()


def shift_day_of_utc(ts_utc: datetime, cutoff_hours: float, tz) -> date:
    """Shift-day of a naive UTC datetime, evaluated in the local timezone."""
    return shift_date_of(utc_to_local(ts_utc, tz), cutoff_hours)


def shift_day_window(sday: date, cutoff_hours: float, tz) -> tuple:
    """[start, end) of a shift-day as naive UTC datetimes (DST-safe)."""
    ct = cutoff_time(cutoff_hours)
    return (local_to_utc(sday, ct, tz),
            local_to_utc(sday + timedelta(days=1), ct, tz))


def collapse_near_duplicates(timestamps: list, threshold_secs: int = 60) -> list:
    """Merge punches that are less than `threshold_secs` apart.

    The ZK device can emit double punches (a quick double-tap, or two readers
    firing) a few seconds apart. Since gaps under 60s are never counted as a
    real break anyway, two punches closer than that are treated as the same
    event (the first one is kept).
    """
    if not timestamps:
        return []
    ordered = sorted(set(timestamps))
    cleaned = [ordered[0]]
    for ts in ordered[1:]:
        if (ts - cleaned[-1]).total_seconds() >= threshold_secs:
            cleaned.append(ts)
    return cleaned


def repair_parity(ts_list: list) -> list:
    """With an odd number of punches, drop the later punch of the shortest gap
    if that gap is under DOUBLE_PUNCH_SECS (a double tap a bit slower than the
    60s collapse). Leaves the list untouched otherwise."""
    if len(ts_list) < 3 or len(ts_list) % 2 == 0:
        return ts_list
    gaps = [((ts_list[i + 1] - ts_list[i]).total_seconds(), i + 1)
            for i in range(len(ts_list) - 1)]
    secs, drop_idx = min(gaps)
    if secs < DOUBLE_PUNCH_SECS:
        return ts_list[:drop_idx] + ts_list[drop_idx + 1:]
    return ts_list


def compute_breaks(all_ts: list, window=None) -> tuple:
    """
    Given a sorted list of naive UTC timestamps [CI, CO, CI, CO, ...], compute
    breaks using the strict alternating CI/CO pattern.

    window: (start, end) of the shift break window as naive UTC datetimes, or
    None. The main break is the first gap that FALLS INSIDE the window (either
    endpoint inside it); a gap that merely straddles the window is not a lunch.

    Returns: (main_break_start, main_break_end, total_break_hours)
    """
    all_ts = collapse_near_duplicates(all_ts)
    if len(all_ts) < 2:
        return None, None, 0.0

    lws, lwe = window if window else (None, None)
    total_break_secs = 0.0
    main_break_start = main_break_end = None

    # Gaps are (CO, next CI) = (ts[1], ts[2]), (ts[3], ts[4]), ...
    for i in range(1, len(all_ts) - 1, 2):
        gap_start, gap_end = all_ts[i], all_ts[i + 1]
        gap_secs = (gap_end - gap_start).total_seconds()
        if gap_secs < 60:
            continue
        total_break_secs += gap_secs
        if lws is not None and main_break_start is None:
            if (lws <= gap_start < lwe) or (lws < gap_end <= lwe):
                main_break_start, main_break_end = gap_start, gap_end

    return main_break_start, main_break_end, total_break_secs / 3600.0


def shift_bounds(config, sday: date, tz) -> dict:
    """Shift end, close deadline and break window of ``config`` on shift-day
    ``sday``, as naive UTC datetimes. Shift times are LOCAL times (exactly as
    entered in the shift form) — no hardcoded UTC offset."""
    eff_start, eff_end = config.effective_times(sday)
    night   = eff_end <= eff_start
    end_day = sday + timedelta(days=1) if night else sday
    shift_end = local_to_utc(end_day, float_to_time(eff_end), tz)
    deadline  = shift_end + timedelta(hours=config.max_extra_hours or 2.0)

    window = None
    bs, be = config.break_start or 0.0, config.break_end or 0.0
    if be > bs:
        bs_day = sday + timedelta(days=1) if (night and bs < eff_start) else sday
        be_day = sday + timedelta(days=1) if (night and be < eff_start) else sday
        window = (local_to_utc(bs_day, float_to_time(bs), tz),
                  local_to_utc(be_day, float_to_time(be), tz))
    return {'shift_end': shift_end, 'deadline': deadline, 'break_window': window}


def decide_checkout(ts_list: list, shift_end, past_deadline: bool) -> tuple:
    """Pick the checkout of a day from its (collapsed, parity-repaired) punches.

    Returns (check_out, missing_checkout):
      * 1 punch                       → open (None); the midnight cron decides
                                        missing checkout vs anomaly.
      * even count                    → last punch is the checkout once it is at
                                        or after shift end, or once the deadline
                                        (shift end + max extra hours) has passed
                                        (early leaver). Before that the last
                                        punch may just be a break-out → open.
      * odd count, deadline passed    → the last OUT punch (ts[-2]) is the
                                        checkout if it is at/after shift end (a
                                        stray punch after leaving is ignored);
                                        otherwise missing checkout.
      * odd count, before deadline    → open.
    ``shift_end`` None means no shift config: any even count closes.
    """
    n = len(ts_list)
    if n < 2:
        return None, False
    if n % 2 == 0:
        last = ts_list[-1]
        if shift_end is None or last >= shift_end or past_deadline:
            return last, False
        return None, False
    if not past_deadline:
        return None, False
    last_out = ts_list[-2]
    if shift_end is not None and last_out >= shift_end:
        return last_out, False
    return ts_list[0], True


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
    timezone           = fields.Selection(
        _tz_get,
        string="Fuseau de l'horloge",
        help="Fuseau horaire sur lequel l'HORLOGE de la pointeuse est réglée.\n"
             "Vide = fuseau global (Paramètres > Présences > Pointeuses).\n"
             "Si l'horloge de la pointeuse reste toujours en UTC+1 (elle ne passe "
             "pas à l'heure de Ramadan), choisissez « Etc/GMT-1 » (= UTC+1 fixe), "
             "sinon les pointages seront décalés d'une heure pendant le Ramadan.",
    )
    sync_window_days   = fields.Integer(
        string='Fenêtre de récupération (jours)',
        default=3,
        help="Les pointages de la pointeuse plus récents que ce nombre de jours "
             "sont importés (sans doublon) à chaque synchronisation. Un pointage "
             "manqué lors d'une synchro précédente est ainsi rattrapé.",
    )
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

    # ─────────────────────────────────────────────────────────────
    #  METROLOGY (ISO 9001 §7.1.5)
    # ─────────────────────────────────────────────────────────────
    # The clock is a measuring instrument: its readings decide who is late and
    # how many hours get paid. §7.1.5.2 requires it to be identified, verified
    # at defined intervals against a traceable reference, and protected against
    # adjustments that would invalidate the results.
    serial_number = fields.Char(
        string='Numéro de série',
        help='Identification unique de l’instrument de mesure (ISO 9001 §7.1.5.2 b).',
    )
    device_model = fields.Char(string='Modèle')
    responsible_id = fields.Many2one(
        'res.users',
        string='Responsable',
        help='Personne responsable du maintien en état de la pointeuse.',
    )
    device_state = fields.Selection(
        selection=[
            ('in_service',  'En service'),
            ('maintenance', 'En maintenance'),
            ('out',         'Hors service'),
        ],
        string='État',
        default='in_service',
        required=True,
        help='État de l’instrument. Une pointeuse hors service n’est plus '
             'synchronisée, mais les absences continuent d’être générées.',
    )
    clock_drift_tolerance = fields.Integer(
        string='Tolérance de dérive (s)',
        default=60,
        help='Écart maximal admis entre l’horloge de la pointeuse et l’heure de '
             'référence du serveur. Au-delà, la pointeuse est déclarée non '
             'conforme et le sync est journalisé en écart.',
    )
    last_clock_check = fields.Datetime(
        string='Dernière vérification d’horloge',
        readonly=True,
    )
    last_clock_drift = fields.Float(
        string='Dernière dérive (s)',
        digits=(16, 1),
        readonly=True,
        help='Positif = la pointeuse avance sur l’heure de référence.',
    )
    clock_status = fields.Selection(
        selection=[
            ('ok',      'Conforme'),
            ('drift',   'Dérive hors tolérance'),
            ('unknown', 'Non vérifiée'),
        ],
        string='État de l’horloge',
        compute='_compute_clock_status',
        store=True,
    )
    sync_log_ids = fields.One2many(
        'attendance.device.sync.log',
        'device_id',
        string='Journal de synchronisation',
        readonly=True,
    )

    @api.depends('last_clock_check', 'last_clock_drift', 'clock_drift_tolerance')
    def _compute_clock_status(self):
        for rec in self:
            if not rec.last_clock_check:
                rec.clock_status = 'unknown'
            elif abs(rec.last_clock_drift or 0.0) > (rec.clock_drift_tolerance or 60):
                rec.clock_status = 'drift'
            else:
                rec.clock_status = 'ok'

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
        return self._read_zk_device(
            self.ip_address, self.port, self._device_tz_name(), self.name)['records']

    @staticmethod
    def _read_zk_device(ip_address, port, timezone, name=''):
        """Pure network read of one ZK device — NO ORM / cursor access, so it is
        safe to run in a worker thread. Takes plain values, returns a dict:

            records     list of {badge_id, timestamp} on success, None on a
                        FAILED read (unreachable / timeout / partial).
            drift       device clock minus server reference, in seconds
                        (positive = the device runs ahead). None if unread.
            device_time the device's own clock, as naive UTC.
            error       human-readable reason when the read failed.

        The clock is read on every connection because the device is a measuring
        instrument: ISO 9001 §7.1.5.2 requires it to be verified against a
        traceable reference (here the NTP-synced server) at defined intervals,
        and an undetected drift silently falsifies every Retard/Absence status.
        """
        result = {'records': None, 'drift': None, 'device_time': None, 'error': ''}

        if not ip_address or not port:
            result['error'] = "Adresse IP ou port non renseigné."
            return result

        TZ      = ZoneInfo(timezone)
        UTC     = ZoneInfo("UTC")
        zk      = ZK(ip_address, port=port, timeout=10, ommit_ping=True)
        conn    = None
        records = []

        try:
            _logger.info("[ZK] Connecting to %s:%s", ip_address, port)
            conn = zk.connect()
            conn.disable_device()

            # ── Clock verification (ISO 9001 §7.1.5.2) ───────────────────────
            # Best-effort: a device that refuses get_time() must not abort the
            # punch read — the drift simply stays unknown for this cycle.
            try:
                dev_time = conn.get_time()
            except Exception:
                dev_time = None
                _logger.warning("[ZK] %s: clock could not be read", name or ip_address)
            if dev_time is not None:
                dev_aware = (dev_time.replace(tzinfo=TZ)
                             if dev_time.tzinfo is None else dev_time)
                result['device_time'] = dev_aware.astimezone(UTC).replace(tzinfo=None)
                result['drift'] = (dev_aware - datetime.now(UTC)).total_seconds()

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
            result['error'] = str(e) or e.__class__.__name__
            return result
        finally:
            if conn:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except Exception:
                    pass

        result['records'] = records
        return result

    @api.model
    def _parallel_read_devices(self, devices):
        """Read several devices concurrently (network-bound). Extracts plain
        connection values in the main thread, then fans the socket reads out to
        a thread pool — the workers never touch the ORM. Returns
        {device_id: read_result_dict} (see _read_zk_device)."""
        specs = [(d.id, d.ip_address, d.port, d._device_tz_name(), d.name) for d in devices]
        if len(specs) <= 1:
            return {sid: self._read_zk_device(ip, port, tz, nm)
                    for sid, ip, port, tz, nm in specs}

        results = {}
        with ThreadPoolExecutor(max_workers=min(len(specs), 8)) as pool:
            futures = {
                pool.submit(self._read_zk_device, ip, port, tz, nm): sid
                for sid, ip, port, tz, nm in specs
            }
            for fut in futures:
                sid = futures[fut]
                try:
                    results[sid] = fut.result()
                except Exception as e:
                    _logger.exception("[ZK] Parallel read failed for device %s", sid)
                    results[sid] = {'records': None, 'drift': None,
                                    'device_time': None, 'error': str(e)}
        return results

    # ---------------------------------------------------------
    # SYNC EVIDENCE (ISO 9001 §9.1.1 / §7.1.5.2)
    # ---------------------------------------------------------
    def _record_clock_check(self, read):
        """Store this cycle's clock verification on the device and warn when the
        drift exceeds the tolerance — the device is then a non-conforming
        measuring instrument and every timestamp it produced is suspect."""
        self.ensure_one()
        drift = read.get('drift')
        if drift is None:
            return
        self.sudo().write({
            'last_clock_check': fields.Datetime.now(),
            'last_clock_drift': drift,
        })
        if abs(drift) > (self.clock_drift_tolerance or 60):
            _logger.warning(
                "[METROLOGY] %s: clock drift %.1fs exceeds the %ss tolerance — "
                "timestamps produced by this device are suspect.",
                self.name, drift, self.clock_drift_tolerance or 60,
            )

    def _log_sync(self, read, audit=None, unlinked=None, trigger='cron'):
        """Persist one sync cycle as an evidence record.

        Written with sudo() so an HR user can trigger a sync without being
        granted create rights on the journal — the evidence stays read-only for
        everyone but Settings (ISO 9001 §7.5.3.2: records protected against
        unintended alteration).
        """
        self.ensure_one()
        audit    = audit or {}
        unlinked = sorted(unlinked or [])
        drift    = read.get('drift')
        tol      = self.clock_drift_tolerance or 60

        if drift is None:
            clock_status = 'unknown'
        elif abs(drift) > tol:
            clock_status = 'drift'
        else:
            clock_status = 'ok'

        if read.get('records') is None:
            state = 'failed'
        elif audit.get('issues') or unlinked or clock_status == 'drift':
            state = 'partial'
        else:
            state = 'success'

        return self.env['attendance.device.sync.log'].sudo().create({
            'device_id':           self.id,
            'sync_date':           fields.Datetime.now(),
            'state':               state,
            'trigger':             trigger,
            'user_id':             self.env.uid,
            'records_read':        len(read.get('records') or []),
            'badges_seen':         audit.get('badges', 0),
            'unlinked_badges':     len(unlinked),
            'unlinked_badge_ids':  ', '.join(unlinked),
            'audit_ok':            audit.get('ok', 0),
            'audit_issues':        len(audit.get('issues') or []),
            'audit_detail':        '\n'.join(audit.get('issues') or []),
            'clock_drift_seconds': drift or 0.0,
            'clock_status':        clock_status,
            'device_time':         read.get('device_time'),
            'error_message':       read.get('error') or '',
        })

    # ---------------------------------------------------------
    # CORE SYNC LOGIC
    # ---------------------------------------------------------
    def _sync_device_attendance(self, cron=False, generate_absences=True, read=_UNSET,
                                trigger=None):
        """One sync cycle for this device.

        1. Store every punch of the import window in attendance.device.punch.
           The INSERT is idempotent and RETURNS only the really new punches, so
           nothing depends on last_sync or on the device clock being exact.
        2. Rebuild each (employee, shift-day) that received a new punch, from
           ALL the stored punches of that employee (every device) — check-in
           and checkout can come from different devices.
        3. Re-evaluate every still-open record of the device's scope, so a
           record closes as soon as shift end / deadline is reached even when
           no new punch arrives.
        All database work is batched (a handful of queries whatever the number
        of punches), so a large log syncs in one pass.
        """
        self.ensure_one()

        # ``cron`` drives the RETURN VALUE (wizard vs bool); ``trigger`` records
        # in the journal who actually initiated the cycle.
        if trigger is None:
            trigger = 'cron' if cron else 'manual'

        TZ         = get_attendance_tz(self.env)
        now_utc    = fields.Datetime.now()
        cutoff     = self.night_shift_cutoff or 0
        today_sday = shift_day_of_utc(now_utc, cutoff, TZ)
        result     = []

        if read is _UNSET:
            read = self._read_zk_device(
                self.ip_address, self.port, self._device_tz_name(), self.name)
        if not read:
            read = {'records': None, 'drift': None, 'device_time': None,
                    'error': "Aucun résultat de lecture pour cette pointeuse."}
        records = read.get('records')

        self._record_clock_check(read)
        if self.clock_status == 'drift':
            result.append(
                f"⏰ Horloge hors tolérance : dérive de {self.last_clock_drift:.0f}s "
                f"(tolérance {self.clock_drift_tolerance}s) — pointages à vérifier."
            )

        if records is None:
            _logger.warning("[SYNC] Device read failed for %s — generating absences only.", self.name)
            # Open records can still be closed from punches already stored.
            self._rebuild_attendance(self._open_record_pairs(cutoff, TZ), cutoff, TZ, now_utc, result)
            if generate_absences:
                self._generate_absences(result)
            self._log_sync(read, trigger=trigger)
            if not cron:
                return self._open_result_wizard(
                    ["⚠️ Lecture impossible — pointages inchangés, absences générées."] + result
                )
            return False

        # 1. Persist punches. First run for this device = seed the table without
        #    rewriting past days (only today is rebuilt).
        seeding = not self._has_stored_punches()
        new_punches, unlinked = self._store_punches(records, now_utc)
        new_punches += self._relink_orphan_punches(now_utc)

        # 2. Days to rebuild: every day touched by a new punch …
        pairs = set()
        for emp_id, ts in new_punches:
            sday = shift_day_of_utc(ts, cutoff, TZ)
            if seeding and sday < today_sday:
                continue
            pairs.add((emp_id, sday))
        # 3. … plus every still-open record (closes at shift end / deadline).
        pairs |= self._open_record_pairs(cutoff, TZ)

        stats = self._rebuild_attendance(pairs, cutoff, TZ, now_utc, result)

        for badge in sorted(unlinked):
            _logger.warning("[SYNC] Badge %s not linked to any employee", badge)
            result.append(f"⚠️ Badge {badge} : pointages ignorés — aucun employé rattaché.")

        if generate_absences:
            self._generate_absences(result)

        audit = self._audit_today_pushes(today_sday, cutoff, TZ, result)

        summary = (f"📥 {len(records)} pointage(s) lus, {len(new_punches)} nouveau(x) — "
                   f"{stats['created']} créé(s), {stats['updated']} mis à jour, "
                   f"{stats['unchanged']} inchangé(s).")
        if seeding:
            summary += " (1re synchro : historique importé, seul aujourd'hui est recalculé)"
        result.insert(0, summary)

        self.last_sync = now_utc
        self._log_sync(read, audit=audit, unlinked=unlinked, trigger=trigger)
        if not cron:
            return self._open_result_wizard(result)
        return True

    # ---------------------------------------------------------
    # PUNCH STORAGE
    # ---------------------------------------------------------
    def _device_tz_name(self):
        """Timezone the device CLOCK is set to (falls back to the global one)."""
        self.ensure_one()
        return self.timezone or get_attendance_tz_name(self.env)

    def _scope_employee_domain(self, badges=None):
        domain = []
        if badges is not None:
            domain.append(('badge_id', 'in', list(badges)))
        if self.department_type and self.department_type != 'all':
            domain.append(('department_type', '=', self.department_type))
        return domain

    def _has_stored_punches(self):
        self.env.cr.execute(
            "SELECT 1 FROM attendance_device_punch WHERE device_id = %s LIMIT 1", (self.id,))
        return bool(self.env.cr.fetchone())

    def _import_window_start(self, now_utc, days=None):
        return now_utc - timedelta(days=max(1, days or self.sync_window_days or 3))

    def _store_punches(self, records, now_utc, days=None):
        """Insert the device punches of the import window. Returns
        ([(employee_id, ts_utc)] of NEW linked punches, {unlinked badges})."""
        window_start = self._import_window_start(now_utc, days)
        UTC = ZoneInfo('UTC')
        rows = set()
        for r in records:
            ts = r['timestamp'].astimezone(UTC).replace(tzinfo=None)
            if ts >= window_start:
                rows.add((r['badge_id'], ts))
        if not rows:
            return [], set()

        badges = {b for b, _ in rows}
        employees = self.env['hr.employee'].search(self._scope_employee_domain(badges))
        emp_by_badge = {e.badge_id: e.id for e in employees}

        uid = self.env.uid
        inserted = execute_values(
            self.env.cr,
            """
            INSERT INTO attendance_device_punch
                (device_id, badge_id, employee_id, timestamp,
                 create_uid, write_uid, create_date, write_date)
            VALUES %s
            ON CONFLICT (device_id, badge_id, timestamp) DO NOTHING
            RETURNING employee_id, badge_id, timestamp
            """,
            [(self.id, b, emp_by_badge.get(b), ts, uid, uid) for b, ts in sorted(rows)],
            template="(%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')",
            page_size=1000,
            fetch=True,
        )
        new_linked = [(emp_id, ts) for emp_id, _, ts in inserted if emp_id]
        unlinked   = {b for emp_id, b, _ in inserted if not emp_id}
        _logger.info("[SYNC] %s: %s punch(es) in window, %s new, %s unlinked badge(s)",
                     self.name, len(rows), len(inserted), len(unlinked))
        return new_linked, unlinked

    def _relink_orphan_punches(self, now_utc):
        """Punches of a badge that was not linked to an employee yet are kept.
        Once HR links the badge, attach them and rebuild those days."""
        window_start = self._import_window_start(now_utc)
        cr = self.env.cr
        cr.execute(
            """
            SELECT DISTINCT badge_id FROM attendance_device_punch
             WHERE device_id = %s AND employee_id IS NULL AND timestamp >= %s
            """, (self.id, window_start))
        badges = [r[0] for r in cr.fetchall()]
        if not badges:
            return []
        employees = self.env['hr.employee'].search(self._scope_employee_domain(badges))
        relinked = []
        for emp in employees:
            cr.execute(
                """
                UPDATE attendance_device_punch SET employee_id = %s
                 WHERE device_id = %s AND badge_id = %s
                   AND employee_id IS NULL AND timestamp >= %s
             RETURNING employee_id, timestamp
                """, (emp.id, self.id, emp.badge_id, window_start))
            relinked += cr.fetchall()
        if relinked:
            _logger.info("[SYNC] %s: re-linked %s orphan punch(es)", self.name, len(relinked))
        return relinked

    def _open_record_pairs(self, cutoff, TZ):
        """(employee, shift-day) of every still-open real record of the scope."""
        employees = self.env['hr.employee'].search(self._scope_employee_domain())
        if not employees:
            return set()
        since = fields.Datetime.now() - timedelta(days=max(1, self.sync_window_days or 3) + 1)
        self.env.cr.execute(
            """
            SELECT employee_id, check_in FROM hr_attendance
             WHERE employee_id = ANY(%s) AND check_out IS NULL
               AND is_absent = FALSE AND check_in >= %s
            """, (employees.ids, since))
        return {(emp_id, shift_day_of_utc(ci, cutoff, TZ))
                for emp_id, ci in self.env.cr.fetchall()}

    # ---------------------------------------------------------
    # REBUILD: one record per (employee, shift-day) from stored punches
    # ---------------------------------------------------------
    def _rebuild_attendance(self, pairs, cutoff, TZ, now_utc, result):
        stats = {'created': 0, 'updated': 0, 'unchanged': 0}
        if not pairs:
            return stats

        # Push any pending ORM write before raw SQL on the same rows.
        self.env.flush_all()
        cr = self.env.cr
        emp_ids   = sorted({e for e, _ in pairs})
        employees = self.env['hr.employee'].browse(emp_ids)
        emp_map   = {e.id: e for e in employees}

        windows = {sday: shift_day_window(sday, cutoff, TZ) for _, sday in pairs}
        lo = min(w[0] for w in windows.values())
        hi = max(w[1] for w in windows.values())

        # All punches of those employees in the span — every device.
        cr.execute(
            """
            SELECT employee_id, timestamp FROM attendance_device_punch
             WHERE employee_id = ANY(%s) AND timestamp >= %s AND timestamp < %s
            """, (emp_ids, lo, hi))
        punches = defaultdict(list)
        for emp_id, ts in cr.fetchall():
            key = (emp_id, shift_day_of_utc(ts, cutoff, TZ))
            if key in pairs:
                punches[key].append(ts)

        # Existing real records — earliest per (employee, shift-day).
        cr.execute(
            """
            SELECT id, employee_id, check_in, check_out, break_start, break_end,
                   break_duration, worked_hours, missing_checkout, is_anomalie,
                   locked_status_codes
              FROM hr_attendance
             WHERE employee_id = ANY(%s) AND check_in >= %s AND check_in < %s
               AND is_absent = FALSE
             ORDER BY check_in, id
            """, (emp_ids, lo, hi))
        existing   = {}
        duplicates = defaultdict(list)   # extra real records on the same day
        for row in cr.fetchall():
            key = (row[1], shift_day_of_utc(row[2], cutoff, TZ))
            if key not in pairs:
                continue
            if key in existing:
                duplicates[key].append(row[0])
            else:
                existing[key] = row

        # Days with real punches no longer need an absence placeholder.
        to_clear = [(e, windows[d][0], windows[d][1]) for (e, d) in pairs if punches.get((e, d))]
        if to_clear:
            execute_values(
                cr,
                """
                DELETE FROM hr_attendance a
                 USING (VALUES %s) AS v(emp, ws, we)
                 WHERE a.employee_id = v.emp AND a.check_in >= v.ws
                   AND a.check_in < v.we AND a.is_absent = TRUE
                """,
                to_clear, template="(%s, %s::timestamp, %s::timestamp)", page_size=1000)

        bounds_cache = {}
        inserts, updates, to_delete = [], [], []
        for key in sorted(pairs):
            emp_id, sday = key
            ts_list = repair_parity(collapse_near_duplicates(punches.get(key, [])))
            if not ts_list:
                continue        # no stored punch: leave the record to the crons
            # The old sync could split one day into two records (one holding the
            # check-in, another the checkout). The day is rebuilt from its
            # punches into the earliest record; the extra ones are removed.
            to_delete.extend(duplicates.get(key, []))
            emp = emp_map[emp_id]
            config = emp.shift_config_id
            if config:
                bkey = (config.id, sday)
                if bkey not in bounds_cache:
                    bounds_cache[bkey] = shift_bounds(config, sday, TZ)
                b = bounds_cache[bkey]
                shift_end, deadline, window = b['shift_end'], b['deadline'], b['break_window']
            else:
                shift_end, window = None, None
                deadline = windows[sday][1]     # end of the shift-day

            rec = existing.get(key)
            vals = self._day_values(ts_list, rec, shift_end, deadline, window, now_utc)
            if vals is None:
                stats['unchanged'] += 1
                continue

            if rec:
                current = (rec[2], rec[3], rec[4], rec[5],
                           round(rec[6] or 0.0, 4), round(rec[7] or 0.0, 4),
                           bool(rec[8]), bool(rec[9]))
                new = (vals['check_in'], vals['check_out'], vals['break_start'],
                       vals['break_end'], round(vals['break_duration'], 4),
                       round(vals['worked_hours'], 4), vals['missing_checkout'],
                       vals['is_anomalie'])
                # A wrong "Missing checkout"/"Anomalie" left by the old sync is
                # sticky (locked_status_codes): force the update to clear it.
                stale = (vals['check_out'] is not None and not vals['missing_checkout']
                         and bool(STALE_CODES & set((rec[10] or '').split(','))))
                if current == new and not stale:
                    stats['unchanged'] += 1
                    continue
                updates.append((rec[0], vals))
            else:
                inserts.append((emp, vals))
            result.append(self._day_line(emp, sday, vals, ts_list))

        uid = self.env.uid
        touched = []
        for rec_id, v in updates:
            cr.execute(
                """
                UPDATE hr_attendance
                   SET check_in = %s, check_out = %s, break_start = %s, break_end = %s,
                       break_duration = %s, worked_hours = %s, missing_checkout = %s,
                       is_anomalie = %s, write_uid = %s, write_date = NOW(),
                       locked_status_codes = CASE WHEN %s THEN array_to_string(
                           array_remove(array_remove(
                               string_to_array(COALESCE(locked_status_codes, ''), ','),
                               'missing_checkout'), 'anomalie'), ',')
                           ELSE locked_status_codes END
                 WHERE id = %s
                """,
                (v['check_in'], v['check_out'], v['break_start'], v['break_end'],
                 v['break_duration'], v['worked_hours'], v['missing_checkout'],
                 v['is_anomalie'], uid,
                 v['check_out'] is not None and not v['missing_checkout'], rec_id))
            touched.append(rec_id)

        if to_delete:
            cr.execute("DELETE FROM hr_attendance WHERE id = ANY(%s) AND is_absent = FALSE",
                       (to_delete,))
            _logger.info("[REBUILD] %s: removed %s duplicate record(s): %s",
                         self.name, len(to_delete), to_delete)
            result.append(f"🧹 {len(to_delete)} doublon(s) supprimé(s) (journée scindée en deux).")

        if inserts:
            new_ids = execute_values(
                cr,
                """
                INSERT INTO hr_attendance
                    (employee_id, check_in, check_out, break_start, break_end,
                     break_duration, worked_hours, missing_checkout, is_anomalie,
                     is_absent, department_type, shift_type, department_shift,
                     create_uid, write_uid, create_date, write_date)
                VALUES %s
                RETURNING id
                """,
                [(emp.id, v['check_in'], v['check_out'], v['break_start'], v['break_end'],
                  v['break_duration'], v['worked_hours'], v['missing_checkout'],
                  v['is_anomalie'], emp.department_type or '', emp.shift_type or '',
                  get_dept_shift(emp), uid, uid)
                 for emp, v in inserts],
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, %s, %s, "
                         "NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')",
                page_size=500,
                fetch=True,
            )
            touched += [r[0] for r in new_ids]

        stats['created'] = len(inserts)
        stats['updated'] = len(updates)
        if touched:
            Attendance = self.env['hr.attendance']
            Attendance.invalidate_model()
            Attendance.recompute_statuses_for_ids(touched)
        return stats

    @staticmethod
    def _day_values(ts_list, rec, shift_end, deadline, window, now_utc):
        """Values of the day's record from its punches, or None to leave the
        existing record untouched."""
        past_deadline = now_utc >= deadline
        check_out, missing = decide_checkout(ts_list, shift_end, past_deadline)

        # Never reopen a record that is already closed once the day is over
        # (e.g. lone punch already closed as missing / anomaly by the cron).
        if check_out is None and rec and rec[3] and past_deadline:
            return None

        check_in = ts_list[0]
        # Keep an earlier stored check-in (manual correction / other scope).
        if rec and rec[2] and rec[2] < check_in:
            check_in = rec[2]

        # Punches the break / worked computation is based on.
        work_ts = ts_list
        if check_out is not None and not missing and check_out != ts_list[-1]:
            work_ts = ts_list[:ts_list.index(check_out) + 1]   # trailing stray punch ignored

        if missing:
            bs = be = None
            brk = worked = 0.0
        else:
            bs, be, brk = compute_breaks(work_ts, window)
            if check_out is not None:
                span   = (check_out - check_in).total_seconds() / 3600.0
                worked = max(0.0, span - brk)
            else:
                worked = sum((work_ts[i + 1] - work_ts[i]).total_seconds() / 3600.0
                             for i in range(0, len(work_ts) - 1, 2))

        real_checkout = check_out is not None and not missing
        return {
            'check_in':         check_in,
            'check_out':        check_out,
            'break_start':      bs,
            'break_end':        be,
            'break_duration':   brk,
            'worked_hours':     worked,
            'missing_checkout': missing,
            # A real checkout clears a previous lone-punch anomaly.
            'is_anomalie':      False if real_checkout else bool(rec and rec[9]),
        }

    def _day_line(self, emp, sday, v, ts_list):
        TZ = get_attendance_tz(self.env)
        fmt = lambda dt: utc_to_local(dt, TZ).strftime('%H:%M') if dt else '-'
        if v['missing_checkout']:
            return f"⚠️ {emp.name}  [{sday}]  IN {fmt(v['check_in'])} → sortie manquante ({len(ts_list)} pointages)"
        if v['check_out']:
            return (f"✅ {emp.name}  [{sday}]  IN {fmt(v['check_in'])} → OUT {fmt(v['check_out'])}  "
                    f"pause {fmt(v['break_start'])}→{fmt(v['break_end'])}  ({v['worked_hours']:.2f}h)")
        return f"⏳ {emp.name}  [{sday}]  IN {fmt(v['check_in'])}  [en cours — {len(ts_list)} pointages]"

    # ---------------------------------------------------------
    # BUTTON: recompute recent days from stored punches
    # ---------------------------------------------------------
    def action_rebuild_attendance(self):
        """Recompute every day of the import window for this device's scope
        from the stored punches. Fixes records damaged by the old sync."""
        self.ensure_one()
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        if not self.env.cr.fetchone()[0]:
            return self._open_result_wizard(
                ["⚠️ Une synchronisation est en cours — veuillez réessayer dans un instant."])
        try:
            if not self.env['hr.employee'].search_count(self._scope_employee_domain()):
                raise UserError("Aucun employé dans le périmètre de cette pointeuse.")
            return self._open_result_wizard(self._rebuild_window())
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

    def _rebuild_window(self, days=None):
        """Rebuild every (employee, shift-day) of the last ``days`` days of this
        device's scope from stored punches. Caller holds the sync lock."""
        self.ensure_one()
        TZ      = get_attendance_tz(self.env)
        cutoff  = self.night_shift_cutoff or 0
        now_utc = fields.Datetime.now()
        employees = self.env['hr.employee'].search(self._scope_employee_domain())
        if not employees:
            return ["Aucun employé dans le périmètre."]
        self.env.cr.execute(
            """
            SELECT DISTINCT employee_id, timestamp FROM attendance_device_punch
             WHERE employee_id = ANY(%s) AND timestamp >= %s
            """, (employees.ids, self._import_window_start(now_utc, days)))
        pairs = {(e, shift_day_of_utc(ts, cutoff, TZ)) for e, ts in self.env.cr.fetchall()}
        result = []
        stats = self._rebuild_attendance(pairs, cutoff, TZ, now_utc, result)
        result.insert(0, f"🔁 {len(pairs)} journée(s) recalculée(s) — {stats['created']} créée(s), "
                         f"{stats['updated']} corrigée(s), {stats['unchanged']} déjà correcte(s).")
        return result

    # ---------------------------------------------------------
    # CRON: nightly repair (00:00)
    # ---------------------------------------------------------
    @api.model
    def cron_daily_repair(self):
        """Every night: re-import the last N days of punches from every device
        (catches anything a 10-min sync missed) and rebuild those days.
        N = system parameter ``attendance_devices.daily_repair_days`` (default 3).
        Warning: rebuilt days replace manual corrections made on them."""
        try:
            days = int(self.env['ir.config_parameter'].sudo().get_param(
                'attendance_devices.daily_repair_days', 3))
        except (TypeError, ValueError):
            days = 3
        days = max(1, days)
        cr = self.env.cr
        # Wait for a running sync to finish instead of skipping the night.
        cr.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        try:
            devices = self.search([('device_state', '!=', 'out')])
            reads = self._parallel_read_devices(devices)
            now_utc = fields.Datetime.now()
            for d in devices:
                try:
                    with cr.savepoint():
                        recs = (reads.get(d.id) or {}).get('records')
                        if recs is None:
                            _logger.warning("[REPAIR] %s: device unreachable — "
                                            "rebuilding from stored punches only", d.name)
                            new = []
                        else:
                            new, _ = d._store_punches(recs, now_utc, days)
                        lines = d._rebuild_window(days)
                    _logger.info("[REPAIR] %s: %s new punch(es) — %s", d.name, len(new), lines[0])
                    cr.commit()
                except Exception:
                    _logger.exception("[REPAIR] Failed for %s", d.name)
        finally:
            cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

    @api.model
    def _finalize_open_records(self):
        """Close open records past their deadline from stored punches (called by
        the midnight cron before it falls back to 'missing checkout')."""
        TZ = get_attendance_tz(self.env)
        now_utc = fields.Datetime.now()
        for device in self.search([]):
            try:
                with self.env.cr.savepoint():
                    cutoff = device.night_shift_cutoff or 0
                    device._rebuild_attendance(
                        device._open_record_pairs(cutoff, TZ), cutoff, TZ, now_utc, [])
            except Exception:
                _logger.exception("[FINALIZE] Failed for %s", device.name)

    # ---------------------------------------------------------
    # AUDIT: confirm every push of the day was created & processed
    # ---------------------------------------------------------
    def _audit_today_pushes(self, today_sday, cutoff, TZ, result):
        """Post-sync self-check for TODAY's shift-day: every employee who punched
        on this device today must have a record reflecting his latest punch.

        A badge is reported only when a push was really dropped:
          * punches today but NO record, or
          * the record is closed with a real checkout older than the latest
            punch (and that punch isn't a stray one after the checkout).
        """
        ws, we = shift_day_window(today_sday, cutoff, TZ)
        cr = self.env.cr
        cr.execute(
            """
            SELECT badge_id, employee_id, timestamp FROM attendance_device_punch
             WHERE device_id = %s AND timestamp >= %s AND timestamp < %s
            """, (self.id, ws, we))
        by_badge = defaultdict(list)
        emp_of_badge = {}
        for badge, emp_id, ts in cr.fetchall():
            by_badge[badge].append(ts)
            if emp_id:
                emp_of_badge[badge] = emp_id

        if not by_badge:
            result.append("🔎 Vérification: aucun pointage aujourd'hui.")
            return {'punches': 0, 'badges': 0, 'ok': 0, 'issues': []}

        emp_ids = list(set(emp_of_badge.values()))
        employees = {e.id: e for e in self.env['hr.employee'].browse(emp_ids)}
        stored = {}
        all_punches = defaultdict(list)
        if emp_ids:
            cr.execute(
                """
                SELECT employee_id, check_in, check_out FROM hr_attendance
                 WHERE employee_id = ANY(%s) AND check_in >= %s AND check_in < %s
                   AND is_absent = FALSE
                 ORDER BY check_in, id
                """, (emp_ids, ws, we))
            for emp_id, ci, co in cr.fetchall():
                stored.setdefault(emp_id, (ci, co))
            cr.execute(
                """
                SELECT employee_id, timestamp FROM attendance_device_punch
                 WHERE employee_id = ANY(%s) AND timestamp >= %s AND timestamp < %s
                """, (emp_ids, ws, we))
            for emp_id, ts in cr.fetchall():
                all_punches[emp_id].append(ts)

        TOL = timedelta(seconds=60)
        total_punches = n_badges = ok = 0
        issues = []
        fmt = lambda dt: utc_to_local(dt, TZ).strftime('%H:%M')
        for badge, ts_list in sorted(by_badge.items()):
            device_ts = collapse_near_duplicates(ts_list)
            total_punches += len(device_ts)
            n_badges += 1
            emp_id = emp_of_badge.get(badge)
            if not emp_id:
                issues.append(f"⚠️ Badge {badge}: {len(device_ts)} pointage(s) — aucun employé lié.")
                continue
            employee = employees[emp_id]
            rec = stored.get(emp_id)
            if rec is None:
                issues.append(f"❌ {employee.name}: {len(device_ts)} pointage(s), aucun enregistrement créé.")
                continue
            rec_ci, rec_co = rec
            emp_ts = repair_parity(collapse_near_duplicates(all_punches[emp_id]))
            dev_last = emp_ts[-1]
            closed = bool(rec_co and rec_co != rec_ci)
            stray_after_checkout = (closed and len(emp_ts) >= 3 and len(emp_ts) % 2 == 1
                                    and emp_ts[-2] <= rec_co + TOL)
            if closed and dev_last > rec_co + TOL and not stray_after_checkout:
                issues.append(f"❌ {employee.name}: dernier pointage {fmt(dev_last)} "
                              f"non reflété (clôturé à {fmt(rec_co)}).")
                continue
            ok += 1

        if issues:
            result.append(f"🔎 Vérification: {total_punches} pointage(s), {n_badges} badge(s) — "
                          f"{ok} OK, {len(issues)} à revoir:")
            result.extend("   " + m for m in issues)
            _logger.warning("[AUDIT] %s: %s/%s badge(s) unresolved for %s: %s",
                            self.name, len(issues), n_badges, today_sday, issues)
        else:
            result.append(f"🔎 Vérification: {total_punches} pointage(s), {n_badges} badge(s) — "
                          f"tous créés et traités ✅")
        return {'punches': total_punches, 'badges': n_badges, 'ok': ok, 'issues': issues}

    # ---------------------------------------------------------
    # ABSENCE GENERATION
    # ---------------------------------------------------------
    def _generate_absences(self, result):
        TZ        = get_attendance_tz(self.env)
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


    def _sync_outcome_line(self):
        """One result line for the multi-device wizards, built from the journal
        entry the sync just wrote. A device that could not be read does NOT
        raise (absences are still generated), so the absence of an exception
        is not a success."""
        self.ensure_one()
        log = self.env['attendance.device.sync.log'].sudo().search(
            [('device_id', '=', self.id)], order='id desc', limit=1)
        if not log or log.state == 'failed':
            reason = (log.error_message if log else '') or "lecture impossible"
            return f"❌ {self.name} — ÉCHEC : {reason}"
        if log.state == 'partial':
            details = []
            if log.clock_status == 'drift':
                details.append(f"dérive horloge {log.clock_drift_seconds:.0f}s")
            if log.unlinked_badges:
                details.append(f"{log.unlinked_badges} badge(s) non rattaché(s)")
            if log.audit_issues:
                details.append(f"{log.audit_issues} écart(s) d’auto-contrôle")
            return f"⚠️ {self.name} — synchronisé avec avertissements : {', '.join(details)}"
        return f"✅ {self.name} — synchronisé ({log.records_read} pointage(s) lus)"

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
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        if not self.env.cr.fetchone()[0]:
            return self._open_result_wizard(
                ["⚠️ A sync is already running — please wait a moment and try again."]
            )
        try:
            return self._sync_device_attendance(cron=False)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

    # ---------------------------------------------------------
    # BUTTON: Sync ALL devices
    # ---------------------------------------------------------
    @api.model
    def action_sync_all_devices(self):
        # Use PostgreSQL advisory lock to prevent concurrent syncs
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
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
            # A device declared out of service is not read (its clock is not a
            # valid measuring reference). Absences keep being generated for its
            # department by cron_generate_absences.
            devices = self.search([('device_state', '!=', 'out')])
            result  = []
            # Read every device in parallel (network I/O), then process the
            # results serially with the same per-device logic as before.
            prefetched = self._parallel_read_devices(devices)
            for device in devices:
                try:
                    with self.env.cr.savepoint():
                        device._sync_device_attendance(
                            cron=True, trigger='manual',
                            read=prefetched.get(device.id))
                    result.append(device._sync_outcome_line())
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
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))


    # ---------------------------------------------------------
    # BUTTON (list view): Sync only the SELECTED devices
    # ---------------------------------------------------------
    def action_sync_selected(self):
        """Sync only the devices ticked in the list view (self = selection)."""
        if not self:
            return self._open_result_wizard(["⚠️ Aucun appareil sélectionné."])
        # Same advisory lock the cron / single-device sync use, so this can never
        # run concurrently with them and insert duplicate attendance rows.
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
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
                            cron=True, trigger='manual',
                            read=prefetched.get(device.id))
                    result.append(device._sync_outcome_line())
                except Exception as e:
                    result.append(f"❌ {device.name} — ERREUR : {e}")
                    _logger.exception("[SYNC SELECTED] Failed for %s", device.name)
            return self._open_result_wizard(result or ["Appareils synchronisés."])
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

    # ---------------------------------------------------------
    # CRON — all devices
    # ---------------------------------------------------------
    @api.model
    def cron_sync_devices(self):
        # Use advisory lock to prevent concurrent syncs
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        if not self.env.cr.fetchone()[0]:
            _logger.info("[CRON] Sync already running — skipping this cycle")
            return
        try:
            devices = self.search([('device_state', '!=', 'out')])
            # Read every device in parallel (network I/O) before processing, so
            # the 5 device reads overlap instead of running back-to-back.
            prefetched = self._parallel_read_devices(devices)
            for device in devices:
                try:
                    with self.env.cr.savepoint():
                        # Same process as the manual "Sync All" button: pull punches
                        # AND generate absences in one pass, under the advisory lock.
                        device._sync_device_attendance(
                            cron=True, generate_absences=True, trigger='cron',
                            read=prefetched.get(device.id))
                except Exception:
                    _logger.exception("[CRON] Sync failed for %s", device.name)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

    @api.model
    def cron_generate_absences(self):
        # Step 1: Clean up stale absent records where real check-in now exists
        try:
            TZ  = get_attendance_tz(self.env)
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
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        if not self.env.cr.fetchone()[0]:
            _logger.info("[CRON] Sync already running — skipping device %s", device_id)
            return
        try:
            with self.env.cr.savepoint():
                device._sync_device_attendance(cron=True)
        except Exception:
            _logger.exception("[CRON] Sync failed for %s", device.name)
        finally:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))

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