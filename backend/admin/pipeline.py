import hashlib,re
from pathlib import Path
from .state import JobStatus,can_transition
from .db import event,update_status
MAX_PDF_BYTES=50*1024*1024
def sha256_file(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def security_check(path):
 p=Path(path); size=p.stat().st_size
 if p.suffix.lower()!='.pdf':raise ValueError('File extension must be .pdf')
 if size>MAX_PDF_BYTES:raise ValueError('PDF exceeds 50 MiB limit')
 with open(p,'rb') as f:
  if f.read(5)!=b'%PDF-':raise ValueError('File is not a valid PDF container')
 return size,sha256_file(p)
def identify(filename):
 u=filename.upper(); typ='SEATS' if ('_SM' in u or 'SEAT' in u) else 'CUTOFFS'
 family=next((x for x in ('BCA','BBA','MBA','MCA') if x in u),None)
 ym=re.search(r'(?:^|[_\-. ])(20\d{2}|\d{2})(?=[_\-. ]|$)',u); year=(int(ym.group(1)) if ym else None); year=(year+2000 if year is not None and year < 100 else year)
 rm=re.search(r'(?<![A-Z0-9])(C\d)(?![A-Z0-9])',u); rnd=rm.group(1) if rm else None
 fields=sum(x is not None for x in (family,year,rnd)); conf=fields/3
 return typ,family,year,rnd,conf
def run_preflight(c,job_id,path,original_filename=None):
 try:
  update_status(c,job_id,JobStatus.SECURITY_CHECK.value,'Checking uploaded PDF'); event(c,job_id,'SECURITY','Valid PDF container and size check started',10)
  size,sha=security_check(path); c.execute('UPDATE import_jobs SET sha256=?,size_bytes=? WHERE id=?',(sha,size,job_id)); event(c,job_id,'SECURITY','SHA-256 fingerprint recorded',20)
  update_status(c,job_id,JobStatus.IDENTIFIED.value,'Identifying source metadata'); typ,fam,year,rnd,conf=identify(original_filename or Path(path).name)
  c.execute('UPDATE import_jobs SET data_type=?,course_family=?,year=?,round=?,confidence=? WHERE id=?',(typ,fam,year,rnd,conf,job_id)); event(c,job_id,'IDENTIFICATION',f'Identified {typ} / {fam or "unknown family"} / {year or "unknown year"} / {rnd or "unknown round"}',40)
  update_status(c,job_id,JobStatus.REVIEW_REQUIRED.value,'Preflight complete; waiting for processing approval'); event(c,job_id,'REVIEW','Safe boundary reached before extraction',100); c.commit(); return True
 except Exception as e:
  c.execute('UPDATE import_jobs SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(JobStatus.FAILED.value,str(e),job_id)); event(c,job_id,'ERROR',str(e),None); c.commit(); return False
