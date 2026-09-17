from backend.admin.state import can_transition
assert can_transition('RECEIVED','SECURITY_CHECK')
assert not can_transition('COMPLETED','RECEIVED')
