import argparse,getpass
from .db import connect,init_admin_schema
from .security import hash_password,ROLES
def main():
 p=argparse.ArgumentParser();p.add_argument('--username',required=True);p.add_argument('--role',choices=sorted(ROLES),default='SUPER_ADMIN');a=p.parse_args(); pw=getpass.getpass('Password: '); init_admin_schema();
 with connect() as c:c.execute('INSERT INTO admin_users(username,password_hash,role) VALUES (?,?,?)',(a.username,hash_password(pw),a.role))
 print('Admin created:',a.username)
if __name__=='__main__':main()
