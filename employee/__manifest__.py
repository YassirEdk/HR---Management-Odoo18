{
    "name": "Employee_custom",
    "version": "18.0.1.0.0",
    "depends": ["base", "hr", "hr_holidays"],
    "data": [
        'security/ir_groups.xml',
        'security/ir.model.access.csv',
        "data/department_type_data.xml",
        "views/hr_department_type_views.xml",
        "views/hr_employee_views.xml",
        "data/cron.xml",
        "data/ir_cron.xml",
        "data/mail_template.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
