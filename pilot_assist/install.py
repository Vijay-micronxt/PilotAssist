import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

ASSISTANT_FIELD = {
	"fieldname": "assistant_enabled",
	"label": "Enable Assistant Access",
	"fieldtype": "Check",
	"insert_after": "enabled",
	"default": "1",
	"description": "Allow this user to access assistant tools",
}


def after_install():
	ensure_assistant_field()


def after_migrate():
	ensure_assistant_field()


def ensure_assistant_field():
	# frappe_assistant_core (or a manual Customize Form step) may already have
	# created this field on the site; never overwrite an existing definition.
	if frappe.get_meta("User").has_field("assistant_enabled"):
		return
	create_custom_field("User", ASSISTANT_FIELD)
