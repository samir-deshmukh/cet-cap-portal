from backend.admin.security import hash_password,verify_password

def test_password():
 h=hash_password('a-strong-password')
 assert verify_password('a-strong-password',h)
 assert not verify_password('wrong-password',h)
