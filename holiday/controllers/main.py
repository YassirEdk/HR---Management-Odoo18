# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request

class TimeOffController(http.Controller):
    
    @http.route('/odoo/time-off-approval', type='http', auth='user')
    def time_off_approval(self, **kwargs):
        """Redirect to time-off approval"""
        try:
            action_id = request.env.ref('holiday.action_hr_leave_to_approve_v2').id
            return request.redirect(f'/web#action={action_id}')
        except Exception as e:
            return request.redirect('/web#menu_id=' + str(request.env.ref('hr_holidays.menu_hr_holidays_root').id))