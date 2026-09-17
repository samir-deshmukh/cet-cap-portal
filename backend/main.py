import os,uuid,json
from pathlib import Path
from fastapi import FastAPI,Request,UploadFile,File,HTTPException,Form
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from .admin.db import connect,init_admin_schema,event
from .admin.security import verify_password,make_session,read_session
from .admin.pipeline import run_preflight,MAX_PDF_BYTES
from .admin.processing import process_import,approve_import,rollback_release
from .admin.publishing import publish_release
from .admin.migrations import ensure_part4_schema
from .admin.state import JobStatus
BASE=Path(__file__).resolve().parent.parent; SITE=BASE/'site'; IMPORTS=BASE/'data'/'imports'; IMPORTS.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='CET CAP Admin API')

@app.middleware('http')
async def security_headers(request: Request, call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if os.getenv('CET_ADMIN_COOKIE_SECURE','0')=='1':
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    return response
@app.on_event('startup')
def startup():init_admin_schema();ensure_part4_schema()
def user(request):
 s=request.cookies.get('cet_admin_session'); return read_session(s) if s else None
def require(request,roles=None):
 u=user(request)
 if not u:raise HTTPException(401,'Authentication required')
 if roles and u['role'] not in roles:raise HTTPException(403,'Insufficient role')
 return u
@app.get('/admin/login',response_class=HTMLResponse)
def login():return '<h1>CET CAP Admin</h1><form method="post"><input name="username" autocomplete="username"><input name="password" type="password" autocomplete="current-password"><button>Sign in</button></form>'
@app.post('/admin/login')
def login_post(username:str=Form(...),password:str=Form(...)):
 with connect() as c:
  r=c.execute('SELECT * FROM admin_users WHERE username=? AND is_active=1',(username,)).fetchone()
  if not r or not verify_password(password,r['password_hash']):raise HTTPException(401,'Invalid credentials')
  c.execute('UPDATE admin_users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?',(r['id'],))
 resp=RedirectResponse('/admin',303); resp.set_cookie('cet_admin_session',make_session(r['id'],r['role']),httponly=True,samesite='lax',secure=os.getenv('CET_ADMIN_COOKIE_SECURE','0')=='1',max_age=28800);return resp
@app.get('/admin',response_class=HTMLResponse)
def admin(request:Request):
 require(request);return '''<h1>CET CAP Admin</h1><nav><a href='/admin'>Dashboard</a> · <a href='/admin/review'>Review Center</a> · <a href='/admin/releases'>Releases</a> · <a href='/admin/health'>Data Health</a> · <a href='/admin/audit'>Audit Log</a></nav><hr><h2>Import Center</h2>
<form action="/admin/api/imports" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept="application/pdf" required><button>Upload PDF</button></form>
<p>After upload, open the import and use <b>Start processing</b> when the detected source metadata is correct.</p>'''
@app.post('/admin/api/imports')
def upload(request:Request,file:UploadFile=File(...)):
 u=require(request,{'SUPER_ADMIN','DATA_ADMIN'}); name=Path(file.filename or '').name
 if not name.lower().endswith('.pdf'):raise HTTPException(400,'Only PDF files are accepted')
 job_key='IMPORT-'+uuid.uuid4().hex[:12].upper(); tmp=IMPORTS/(job_key+'.upload'); total=0
 try:
  with open(tmp,'wb') as out:
   while True:
    chunk=file.file.read(1024*1024)
    if not chunk:break
    total+=len(chunk)
    if total>MAX_PDF_BYTES:raise HTTPException(413,'PDF exceeds 50 MiB limit')
    out.write(chunk)
  stored=IMPORTS/(job_key+'.pdf');tmp.replace(stored)
  with connect() as c:
   cur=c.execute('INSERT INTO import_jobs(job_key,original_filename,stored_path,sha256,size_bytes,status,created_by) VALUES (?,?,?,?,?,?,?)',(job_key,name,str(stored.relative_to(BASE)), '',total,'RECEIVED',u['uid'])); jid=cur.lastrowid;event(c,jid,'RECEIVED','File received',0);c.commit()
   run_preflight(c,jid,stored,name)
  return RedirectResponse(f'/admin/imports/{jid}',303)
 except Exception:
  if tmp.exists():tmp.unlink()
  raise


@app.post('/admin/api/imports/{job_id}/process')
def process_job(request:Request, job_id:int):
    u=require(request,{'SUPER_ADMIN','DATA_ADMIN'})
    with connect() as c:
        j=c.execute('SELECT * FROM import_jobs WHERE id=?',(job_id,)).fetchone()
        if not j: raise HTTPException(404,'Import not found')
        if j['status'] != JobStatus.REVIEW_REQUIRED.value:
            raise HTTPException(409, f'Import is not ready for processing: {j["status"]}')
        if not j['data_type'] or not j['course_family'] or not j['year'] or not j['round']:
            raise HTTPException(409,'Source metadata is incomplete; identify year, round and course before processing')
        path=BASE / j['stored_path']
        if not path.exists(): raise HTTPException(404,'Stored source PDF is missing')
        result=process_import(c,job_id,path,data_type=j['data_type'],family=j['course_family'],
                              year=int(j['year']),round_name=j['round'])
        if not result['ok']: raise HTTPException(500,result['error'])
        return result

@app.get('/admin/api/imports')
def imports(request:Request):
 require(request)
 with connect() as c:return [dict(x) for x in c.execute('SELECT * FROM import_jobs ORDER BY id DESC LIMIT 100')]
@app.get('/admin/api/imports/{job_id}')
def detail(request:Request,job_id:int):
 require(request)
 with connect() as c:
  j=c.execute('SELECT * FROM import_jobs WHERE id=?',(job_id,)).fetchone()
  if not j:raise HTTPException(404,'Import not found')
  ev=c.execute('SELECT * FROM import_events WHERE job_id=? ORDER BY id',(job_id,)).fetchall(); return {'job':dict(j),'events':[dict(x) for x in ev]}
@app.get('/admin/imports/{job_id}',response_class=HTMLResponse)
def live(request:Request,job_id:int):
 require(request);return f'''<h1>Import #{job_id}</h1>
<button id="process" onclick="start()">Start processing</button>
<pre id="out">Loading...</pre>
<script>
async function start(){{
 document.querySelector('#process').disabled=true;
 let r=await fetch('/admin/api/imports/{job_id}/process',{{method:'POST'}});
 if(!r.ok) alert(await r.text());
 p();
}}
async function p(){{
 let r=await fetch('/admin/api/imports/{job_id}');let d=await r.json();
 document.querySelector('#out').textContent=d.job.status+'\\n\\n'+
 d.events.map(x=>(x.progress==null?'':x.progress+'% ')+x.message).join('\\n');
 document.querySelector('#process').style.display=d.job.status==='REVIEW_REQUIRED'?'inline-block':'none';
 if(!['COMPLETED','FAILED','CANCELLED','QUARANTINED','ROLLED_BACK'].includes(d.job.status))setTimeout(p,1500)
}}p();
</script>'''
@app.get('/admin/review', response_class=HTMLResponse)
def review_center(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    return """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review Center — CET CAP Admin</title>
<style>body{font-family:system-ui;max-width:1100px;margin:30px auto;padding:0 16px}button{padding:9px 14px;margin:4px}a{margin-right:12px}.card{border:1px solid #ccc;padding:15px;margin:12px 0}</style>
<h1>Review Center</h1><p>Only validated staged imports can be approved. Production data is changed only by the approval action.</p>
<div id="list">Loading…</div>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
async function load(){
 const r=await fetch('/admin/api/review'); const d=await r.json();
 document.querySelector('#list').innerHTML=d.map(j=>`<div class="card">
 <b>${esc(j.job_key)}</b> — ${esc(j.original_filename)} — <b>${esc(j.status)}</b>
 <p>${esc(j.data_type)} ${esc(j.course_family)} ${esc(j.year)} ${esc(j.round)}</p>
 <a href="/admin/imports/${j.id}">Timeline</a>
 <a href="/admin/api/imports/${j.id}/review" target="_blank">Review JSON</a>
 ${j.status==='REVIEW_REQUIRED'?`<button onclick="approve(${j.id})">Approve & commit</button><button onclick="reject(${j.id})">Reject</button>`:''}
 </div>`).join('')||'No imports awaiting review.';
}
async function approve(id){let notes=prompt('Approval note / reason (optional):','');if(notes===null)return;
 let r=await fetch('/admin/api/imports/'+id+'/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({notes})});
 alert(await r.text());load();}
async function reject(id){let reason=prompt('Rejection reason (required):');if(!reason)return;
 let r=await fetch('/admin/api/imports/'+id+'/reject',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});
 alert(await r.text());load();}
load();
</script>"""

@app.get('/admin/api/review')
def review_list(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM import_jobs WHERE status IN ('REVIEW_REQUIRED','COMMITTING') ORDER BY id DESC")]

@app.get('/admin/api/imports/{job_id}/review')
def review_detail(request: Request, job_id: int):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    with connect() as c:
        job=c.execute("SELECT * FROM import_jobs WHERE id=?",(job_id,)).fetchone()
        if not job: raise HTTPException(404,'Import not found')
        results=[dict(r) for r in c.execute("SELECT * FROM import_results WHERE job_id=? ORDER BY id",(job_id,))]
        rows=c.execute("SELECT id,result_type,source_file,source_page,normalized_json,validation_status,validation_message "
                       "FROM import_staging_records WHERE job_id=? ORDER BY id LIMIT 500",(job_id,)).fetchall()
        return {'job':dict(job),'results':results,'rows':[dict(r) for r in rows]}

@app.post('/admin/api/imports/{job_id}/approve')
async def approve(request: Request, job_id: int):
    u=require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER'})
    try: body=await request.json()
    except Exception: body={}
    notes=str(body.get('notes','')).strip()[:4000]
    with connect() as c:
        try: return approve_import(c,job_id,u['uid'],notes)
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/admin/api/imports/{job_id}/reject')
async def reject(request: Request, job_id: int):
    u=require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER'})
    try: body=await request.json()
    except Exception: body={}
    reason=str(body.get('reason','')).strip()[:4000]
    if not reason: raise HTTPException(400,'Rejection reason is required')
    with connect() as c:
        j=c.execute("SELECT * FROM import_jobs WHERE id=?",(job_id,)).fetchone()
        if not j: raise HTTPException(404,'Import not found')
        if j['status'] != JobStatus.REVIEW_REQUIRED.value:
            raise HTTPException(409,f"Import is not awaiting review: {j['status']}")
        c.execute("UPDATE import_jobs SET status='QUARANTINED',error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(reason,job_id))
        event(c,job_id,'REJECT',f'Import rejected/quarantined: {reason}',100)
        sql=("INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,before_json,after_json,reason) "
             "VALUES (?,?,?,?,?,?,?)")
        c.execute(sql,(u['uid'],'REJECT_IMPORT','IMPORT',str(job_id),
                       json.dumps({'status':j['status']}),json.dumps({'status':'QUARANTINED'}),reason))
        c.commit()
        return {'ok':True,'job_id':job_id,'status':'QUARANTINED'}

@app.post('/admin/api/releases/{release_id}/rollback')
async def rollback(request: Request, release_id: int):
    u=require(request, {'SUPER_ADMIN'})
    try: body=await request.json()
    except Exception: body={}
    reason=str(body.get('reason','')).strip()[:4000]
    if not reason: raise HTTPException(400,'Rollback reason is required')
    with connect() as c:
        try: return rollback_release(c,release_id,u['uid'],reason)
        except ValueError as e: raise HTTPException(409,str(e))


@app.post('/admin/api/releases/{release_id}/publish')
def publish(request: Request, release_id: int):
    u=require(request, {'SUPER_ADMIN','DATA_ADMIN'})
    with connect() as c:
        rel=c.execute('SELECT * FROM data_releases WHERE id=?',(release_id,)).fetchone()
        if not rel: raise HTTPException(404,'Release not found')
        if rel['approved_by'] != u['uid'] and u['role'] != 'SUPER_ADMIN': raise HTTPException(403,'Only the approving admin or a super admin can publish this release')
        try: return publish_release(c, int(rel['source_job_id']), release_id)
        except ValueError as e: raise HTTPException(409,str(e))
        except Exception as e: raise HTTPException(500,str(e))

@app.get('/admin/releases', response_class=HTMLResponse)
def releases_page(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    return '''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Releases — CET CAP Admin</title><style>body{font-family:system-ui;max-width:1100px;margin:30px auto;padding:0 16px}button{padding:8px 12px;margin:3px}.card{border:1px solid #ccc;padding:14px;margin:10px 0}a{margin-right:12px}</style><nav><a href="/admin">Dashboard</a><a href="/admin/review">Review</a><a href="/admin/releases">Releases</a><a href="/admin/health">Health</a><a href="/admin/audit">Audit</a></nav><h1>Release History</h1><div id="list">Loading…</div><script>function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]);}async function load(){let r=await fetch('/admin/api/releases');let d=await r.json();document.querySelector('#list').innerHTML=d.map(x=>`<div class="card"><b>${esc(x.release_key)}</b> — ${esc(x.status)}<br>Job ${esc(x.source_job_id)} · ${esc(x.created_at)}<br>${x.status==='COMMITTED'?`<button onclick="publish(${x.id})">Publish & verify</button>`:''}${x.status==='PUBLISHED'?`<button onclick="rollback(${x.id})">Rollback</button>`:''}</div>`).join('')||'No releases yet.';}async function publish(id){if(!confirm('Build, verify and atomically publish this release?'))return;let r=await fetch('/admin/api/releases/'+id+'/publish',{method:'POST'});alert(await r.text());load();}async function rollback(id){let reason=prompt('Rollback reason:');if(!reason)return;let r=await fetch('/admin/api/releases/'+id+'/rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});alert(await r.text());load();}load();</script>'''

@app.get('/admin/api/releases')
def releases_api(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    with connect() as c: return [dict(r) for r in c.execute("SELECT * FROM data_releases ORDER BY id DESC LIMIT 100")]

@app.get('/admin/health', response_class=HTMLResponse)
def health_page(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    return '''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Data Health — CET CAP Admin</title><style>body{font-family:system-ui;max-width:1000px;margin:30px auto;padding:0 16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.card{border:1px solid #ccc;padding:15px}</style><nav><a href="/admin">Dashboard</a> · <a href="/admin/review">Review</a> · <a href="/admin/releases">Releases</a> · <a href="/admin/audit">Audit</a></nav><h1>Data Health</h1><div id="out">Loading…</div><script>async function load(){let r=await fetch('/admin/api/health');let d=await r.json();document.querySelector('#out').innerHTML='<div class="grid">'+Object.entries(d.counts||{}).map(([k,v])=>'<div class="card"><b>'+k+'</b><div>'+v+'</div></div>').join('')+'</div><h2>Runtime</h2><pre>'+JSON.stringify(d.runtime,null,2)+'</pre>'; }load();</script>'''

@app.get('/admin/api/health')
def health_api(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    with connect() as c:
        counts={t:c.execute(f'SELECT COUNT(*) n FROM {t}').fetchone()['n'] for t in ['institutes','programs','cutoffs','seats','import_jobs','data_releases','audit_log']}
        latest=c.execute("SELECT release_key,status,published_at,verified_at FROM data_releases ORDER BY id DESC LIMIT 1").fetchone()
        return {'ok':True,'counts':counts,'runtime':{'site_exists':SITE.exists(),'latest_release':dict(latest) if latest else None}}

@app.get('/admin/audit', response_class=HTMLResponse)
def audit_page(request: Request):
    require(request, {'SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY'})
    with connect() as c: rows=[dict(r) for r in c.execute('SELECT a.*,u.username FROM audit_log a LEFT JOIN admin_users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT 200')]
    safe=json.dumps(rows, ensure_ascii=False).replace('</','<\\/')
    return f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Audit Log — CET CAP Admin</title><style>body{{font-family:system-ui;max-width:1100px;margin:30px auto;padding:0 16px}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:7px;text-align:left}}</style><nav><a href="/admin">Dashboard</a> · <a href="/admin/review">Review</a> · <a href="/admin/releases">Releases</a> · <a href="/admin/health">Health</a></nav><h1>Audit Log</h1><table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Entity</th><th>Reason</th></tr></thead><tbody id="b"></tbody></table><script>const rows={safe};function esc(v){{return String(v??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]);}}document.querySelector('#b').innerHTML=rows.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.username)}</td><td>${esc(x.action)}</td><td>${esc(x.entity_type)} ${esc(x.entity_id)}</td><td>${esc(x.reason)}</td></tr>`).join('');</script>'''
