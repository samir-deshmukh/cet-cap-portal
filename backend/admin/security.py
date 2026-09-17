import base64, hashlib, hmac, os, time
from itsdangerous import URLSafeTimedSerializer
PASSWORD_MIN=12
ROLES={'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'}
def hash_password(password):
 if len(password)<PASSWORD_MIN: raise ValueError(f'Password must be at least {PASSWORD_MIN} characters')
 salt=os.urandom(16); return 'scrypt$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)).decode()
def verify_password(password, encoded):
 try:
  _,s,d=encoded.split('$'); salt=base64.urlsafe_b64decode(s); expected=base64.urlsafe_b64decode(d); actual=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1); return hmac.compare_digest(actual,expected)
 except Exception:return False
def session_serializer():
 secret=os.environ.get('CET_ADMIN_SESSION_SECRET')
 if not secret: raise RuntimeError('CET_ADMIN_SESSION_SECRET is required')
 return URLSafeTimedSerializer(secret,salt='cet-cap-admin-session')
def make_session(user_id,role): return session_serializer().dumps({'uid':int(user_id),'role':role})
def read_session(value,max_age=8*3600):
 try:return session_serializer().loads(value,max_age=max_age)
 except Exception:return None
def role_allowed(role,allowed): return role in allowed
