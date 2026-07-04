from . import models
from . import wizard


def _recompute_statuses_post_init(env):
    """After install/upgrade, populate the multi-status fields for existing
    rows (the status/bucket seed data is guaranteed loaded by now)."""
    recs = env['hr.attendance'].search([])
    if recs:
        env['hr.attendance'].recompute_statuses_for_ids(recs.ids)
