"""
Part 2: real PDF -> extracted artifacts -> validation -> staging.

This module deliberately calls the existing, trusted CET extractors and
validation gates rather than reimplementing their parsing rules. Production
fact tables are NOT modified here.
"""
from __future__ import annotations
import csv, importlib.util, json, shutil, subprocess, sys, uuid, os, time
from pathlib import Path
from .db import event, update_status
from .state import JobStatus, can_transition

BASE = Path(__file__).resolve().parents[2]
SCRIPTS = BASE / "scripts"
WORK_ROOT = BASE / "data" / "import_work"
STAGING_ROOT = BASE / "data" / "import_staging"

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load extractor: {path.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _run_validator(script: Path, folder: Path) -> tuple[bool, str]:
    p = subprocess.run(
        [sys.executable, str(script), str(folder)],
        cwd=str(BASE), capture_output=True, text=True, timeout=900
    )
    output = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, output

def _canonical_cutoff_name(family: str, year: int, round_name: str) -> str:
    return f"{family}_{str(year)[-2:]}_{round_name}_cutoffs.csv"

def _canonical_seat_name(family: str) -> str:
    return f"{family}_SM.csv"

def _page_from(row: dict):
    v = str(row.get("page", "")).strip()
    return int(v) if v.isdigit() else None

def _stage_csv(conn, job_id: int, result_type: str, path: Path,
               valid: bool, validation_message: str) -> int:
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = dict(row)
            conn.execute(
                """INSERT INTO import_staging_records
                   (job_id,result_type,source_file,source_page,raw_json,
                    normalized_json,validation_status,validation_message)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (job_id, result_type, path.name, _page_from(row),
                 json.dumps(raw, ensure_ascii=False),
                 json.dumps(raw, ensure_ascii=False),
                 "VALID" if valid else "REVIEW",
                 None if valid else validation_message[-4000:])
            )
            rows += 1
    return rows

def _insert_result(conn, job_id, result_type, raw_path, normalized_path,
                   valid, extracted, staged, quarantined, summary):
    conn.execute(
        """INSERT INTO import_results
           (job_id,result_type,raw_artifact_path,normalized_artifact_path,
            validation_status,rows_extracted,rows_validated,rows_quarantined,
            summary_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (job_id,result_type,str(raw_path.relative_to(BASE)),
         str(normalized_path.relative_to(BASE)) if normalized_path else None,
         "PASS" if valid else "REVIEW_REQUIRED",
         extracted, staged if valid else 0, quarantined,
         json.dumps(summary, ensure_ascii=False))
    )


def _compare_cutoffs(conn, path: Path, family: str, year: int, round_name: str) -> dict:
    import pandas as pd
    sys.path.insert(0, str(SCRIPTS))
    from validate_data import SECTION_MAP, STAGE_MAP, parse_category, forward_fill_block_columns
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    forward_fill_block_columns(df)
    existing = set()
    q = conn.execute("""SELECT c.institution_code,p.program_name_raw,c.base_category,
                               c.is_ladies,c.section_code,c.stage_code,c.rank_number,c.rank_suffix,c.percentile
                        FROM cutoffs c JOIN programs p ON p.program_id=c.program_id
                        WHERE c.year=? AND c.round=? AND p.program_family=?""",
                     (year, int(round_name[1:]), family))
    for r in q:
        existing.add(tuple(r))
    new_rows = changed = duplicates = 0
    seen = set()
    # A changed cutoff keeps the same institute/program/category/section/stage
    # identity but has a different rank/percentile. It must be surfaced for
    # review, never silently treated as an ordinary new row.
    identity_existing = {}
    for r in existing:
        identity = r[:6]
        identity_existing.setdefault(identity, set()).add((r[6], r[7], r[8]))
    changed_examples = []
    duplicate_in_stage = 0
    for _, row in df.iterrows():
        base, ladies, _ = parse_category(row["category"].strip())
        section = SECTION_MAP.get(row["section"].strip())
        stage = STAGE_MAP.get(row["stage"].strip())
        rs = row["rank_suffix"].strip() or None
        rn = int(row["rank_number"]) if row["rank_number"].strip().isdigit() else None
        pct = float(row["percentage"]) if row["percentage"].strip() else None
        key = (row["institution_code"].strip(), row["program_name"].strip(), base,
               int(bool(ladies)), section, stage, rn, rs, pct)
        identity = key[:6]
        if key in seen:
            duplicate_in_stage += 1
        seen.add(key)
        if key in existing:
            duplicates += 1
        elif identity in identity_existing:
            changed += 1
            if len(changed_examples) < 100:
                changed_examples.append({"identity": identity, "new": key[6:]})
        else:
            new_rows += 1
    return {"rows_compared": len(df), "already_present_exact_key": duplicates,
            "new_candidates": new_rows, "changed_candidates": changed,
            "duplicate_in_staged_input": duplicate_in_stage,
            "changed_examples": changed_examples}

def _compare_seats(conn, path: Path, family: str, year: int) -> dict:
    import pandas as pd
    from scripts.ingest_seats import load_whitelists
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    valid_cats, lane_map, alias_map = load_whitelists(BASE / "data" / "reference")
    existing = set(tuple(r) for r in conn.execute(
        """SELECT institution_code,choice_code,allocation_lane,raw_category,raw_gender
           FROM seats WHERE capture_year=? AND program_family=?""", (year,family)))
    duplicates = 0
    for _, row in df.iterrows():
        cat = row["category"].strip()
        base = alias_map.get(cat, cat)
        lane = lane_map.get(row["allocation_type"].strip(), row["allocation_type"].strip())
        key=(row["institution_code"].strip(),row["choice_code"].strip(),lane,cat,row.get("gender","").strip())
        if key in existing: duplicates += 1
    return {"rows_compared":len(df),"already_present_exact_key":duplicates,
            "new_candidates":len(df)-duplicates}

def process_import(conn, job_id: int, pdf_path: Path, *,
                   data_type: str, family: str, year: int, round_name: str):
    """Run extraction/normalization/validation and stop at STAGED/REVIEW_REQUIRED."""
    work = WORK_ROOT / f"job_{job_id}_{uuid.uuid4().hex[:8]}"
    raw = work / "raw"
    normalized = work / "normalized"
    staging = STAGING_ROOT / f"job_{job_id}"
    for d in (raw, normalized, staging):
        d.mkdir(parents=True, exist_ok=True)

    try:
        if not can_transition(JobStatus.REVIEW_REQUIRED, JobStatus.EXTRACTING):
            raise RuntimeError("Import state machine does not permit processing from review")
        update_status(conn, job_id, JobStatus.EXTRACTING.value, "Running trusted PDF extractor")
        event(conn, job_id, "EXTRACT", f"Starting {data_type.lower()} extraction", 45)

        if data_type == "CUTOFFS":
            mod = _load_module(SCRIPTS / "cutoff_extractor.py", "cet_cutoff_extractor")
            summary = mod.process_single_pdf(pdf_path=pdf_path, outdir=raw,
                                             dpi=300, lang="eng", use_camelot=False)
            extracted_path = raw / f"{pdf_path.stem}_cutoffs.csv"
            if not extracted_path.exists():
                raise RuntimeError("Cutoff extractor produced no CSV artifact")
            canonical = normalized / _canonical_cutoff_name(family, year, round_name)
            shutil.copy2(extracted_path, canonical)
            event(conn, job_id, "EXTRACT", f"Extracted {summary.get('merged_records', 0)} cutoff rows", 60)
            update_status(conn, job_id, JobStatus.NORMALIZING.value, "Canonicalizing cutoff artifact")
            event(conn, job_id, "NORMALIZE", f"Normalized artifact: {canonical.name}", 68)

            # Safe cleaner only quarantines genuinely blank categories; it does not
            # guess values. Work in a copy so production/reference data isn't changed
            # during an import job.
            clean_out = work / "cleaned"
            clean_ref = BASE / "data" / "reference"
            # clean_data.py operates on its project reference directory, so invoke it
            # only after copying the project reference into an isolated subprocess cwd.
            isolated = work / "isolated_project"
            (isolated / "data" / "reference").mkdir(parents=True)
            shutil.copytree(BASE / "data" / "reference", isolated / "data" / "reference",
                            dirs_exist_ok=True)
            shutil.copy2(canonical, isolated / "raw.csv")
            # Rather than allowing cleaner path assumptions to touch project files,
            # call its functions directly.
            cleaner = _load_module(SCRIPTS / "clean_data.py", "cet_clean_data")
            cleaner.CATEGORY_WHITELIST_PATH = isolated / "data" / "reference" / "category_whitelist.csv"
            cleaner.CATEGORY_WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BASE / "data" / "reference" / "category_whitelist.csv",
                         cleaner.CATEGORY_WHITELIST_PATH)
            clean_out.mkdir(parents=True, exist_ok=True)
            rejects = clean_out / "rejects"
            cleaner.clean_file(canonical, clean_out, rejects)
            cleaned = clean_out / canonical.name
            shutil.copy2(cleaned, canonical)
            update_status(conn, job_id, JobStatus.VALIDATING.value, "Running cutoff data quality gate")
            event(conn, job_id, "VALIDATE", "Running existing validate_data.py gate", 78)
            valid, validation = _run_validator(SCRIPTS / "validate_data.py", clean_out)
            # Validator reads project references; its only intended input is the
            # normalized/cleaned CSV. Save its exact output as an audit artifact.
            (work / "validation.txt").write_text(validation, encoding="utf-8")
            quarantine = sum(1 for _ in rejects.glob("*.csv")) if rejects.exists() else 0
            rows = _stage_csv(conn, job_id, "CUTOFFS", canonical, valid, validation)
            shutil.copy2(canonical, staging / canonical.name)
            shutil.copy2(work / "validation.txt", staging / "validation.txt")
            _insert_result(conn, job_id, "CUTOFFS", extracted_path, canonical,
                           valid, int(summary.get("merged_records", rows)), rows,
                           quarantine, {"extractor": summary, "validation": validation[-12000:]})
        else:
            mod = _load_module(SCRIPTS / "seat_matrix_extractor.py", "cet_seat_extractor")
            summary = mod.process_single_pdf(pdf_path=pdf_path, outdir=raw,
                                             dpi=300, lang="eng", use_ocr=True,
                                             force_ocr=False)
            extracted_path = raw / f"{pdf_path.stem}_seats_long.csv"
            if not extracted_path.exists():
                raise RuntimeError("Seat extractor produced no long-seat CSV artifact")
            canonical = normalized / _canonical_seat_name(family)
            shutil.copy2(extracted_path, canonical)
            event(conn, job_id, "EXTRACT", f"Extracted {summary.get('seat_rows_extracted', 0)} seat rows", 60)
            update_status(conn, job_id, JobStatus.NORMALIZING.value, "Canonicalizing seat-matrix artifact")
            event(conn, job_id, "NORMALIZE", f"Normalized artifact: {canonical.name}", 68)
            update_status(conn, job_id, JobStatus.VALIDATING.value, "Running seat-matrix data quality gate")
            event(conn, job_id, "VALIDATE", "Running existing validate_seats.py gate", 78)
            valid, validation = _run_validator(SCRIPTS / "validate_seats.py", normalized)
            (work / "validation.txt").write_text(validation, encoding="utf-8")
            rows = _stage_csv(conn, job_id, "SEATS", canonical, valid, validation)
            shutil.copy2(canonical, staging / canonical.name)
            shutil.copy2(work / "validation.txt", staging / "validation.txt")
            _insert_result(conn, job_id, "SEATS", extracted_path, canonical,
                           valid, int(summary.get("seat_rows_extracted", rows)), rows, 0,
                           {"extractor": summary, "validation": validation[-12000:]})

        update_status(conn, job_id, JobStatus.COMPARING.value, "Comparing staged rows with existing production data")
        result_row = conn.execute("SELECT * FROM import_results WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        if result_row and result_row["result_type"] == "CUTOFFS":
            comparison = _compare_cutoffs(conn, canonical, family, year, round_name)
        else:
            comparison = _compare_seats(conn, canonical, family, year)
        conn.execute("UPDATE import_results SET summary_json=? WHERE id=?",
                     (json.dumps({**json.loads(result_row["summary_json"] or "{}"), "comparison": comparison}, ensure_ascii=False),
                      result_row["id"]))
        event(conn, job_id, "COMPARE",
              f"Compared {comparison.get('rows_compared', 0)} staged rows; "
              f"{comparison.get('already_present_exact_key', 0)} exact keys already exist",
              88)
        update_status(conn, job_id, JobStatus.STAGED.value, "Extraction, normalization and validation artifacts staged")
        event(conn, job_id, "STAGED", "No production fact rows were modified", 100)
        update_status(conn, job_id, JobStatus.REVIEW_REQUIRED.value, "Waiting for admin review and approval")
        event(conn, job_id, "REVIEW", "Import is ready for Review Center", 100)
        conn.commit()
        return {"ok": True, "job_id": job_id, "status": JobStatus.REVIEW_REQUIRED.value,
                "work_dir": str(work.relative_to(BASE))}
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE import_jobs SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (JobStatus.FAILED.value, str(exc), job_id))
        event(conn, job_id, "ERROR", str(exc), None)
        conn.commit()
        return {"ok": False, "job_id": job_id, "status": JobStatus.FAILED.value, "error": str(exc)}


def _load_staged_rows(conn, job_id: int, result_type: str):
    rows = conn.execute(
        "SELECT id, normalized_json, validation_status FROM import_staging_records "
        "WHERE job_id=? AND result_type=? ORDER BY id", (job_id, result_type)
    ).fetchall()
    return rows


def _require_clean_stage(conn, job_id: int, result_type: str):
    result = conn.execute(
        "SELECT * FROM import_results WHERE job_id=? AND result_type=? ORDER BY id DESC LIMIT 1",
        (job_id, result_type)
    ).fetchone()
    if not result:
        raise ValueError("No staged result exists for this import")
    if result["validation_status"] != "PASS":
        raise ValueError("Import has validation findings and cannot be approved automatically")
    bad = conn.execute(
        "SELECT COUNT(*) AS n FROM import_staging_records "
        "WHERE job_id=? AND result_type=? AND validation_status!='VALID'",
        (job_id, result_type)
    ).fetchone()["n"]
    if bad:
        raise ValueError(f"{bad} staged rows are not VALID; resolve them before approval")
    return result


def _insert_cutoff_from_json(conn, raw, job, source_pdf):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    from validate_data import SECTION_MAP, STAGE_MAP, parse_category, parse_filename
    from . import processing as _self
    family = job["course_family"]
    program_name = str(raw.get("program_name","")).strip()
    inst = str(raw.get("institution_code","")).strip()
    if not program_name or not inst:
        raise ValueError("Missing institution_code/program_name after normalization")
    base, ladies, _ = parse_category(str(raw.get("category","")).strip())
    if base is None:
        raise ValueError(f"Unknown category: {raw.get('category')!r}")
    section = SECTION_MAP.get(str(raw.get("section","")).strip())
    stage = STAGE_MAP.get(str(raw.get("stage","")).strip())
    if not section or not stage:
        raise ValueError("Unknown section or stage")
    pct = float(raw["percentage"])
    rank = str(raw.get("rank_number","")).strip()
    rank_number = int(rank) if rank.isdigit() else None
    suffix = str(raw.get("rank_suffix","")).strip() or None

    level_map = {}
    alias_file = BASE/"data"/"reference"/"program_aliases.csv"
    if alias_file.exists():
        import pandas as pd
        df=pd.read_csv(alias_file,dtype=str,keep_default_na=False)
        level_map=dict(zip(df["raw_name"].str.strip(),df["level"].str.strip()))
    level=level_map.get(program_name)
    conn.execute("INSERT OR IGNORE INTO programs(program_family,program_name_raw,level) VALUES(?,?,?)",
                 (family,program_name,level))
    prog=conn.execute("SELECT program_id FROM programs WHERE program_family=? AND program_name_raw=?",
                      (family,program_name)).fetchone()
    if not prog: raise ValueError(f"Unable to resolve program: {program_name}")
    pid=prog["program_id"]
    vals=(int(job["year"]),int(str(job["round"])[1:]),inst,pid,base,int(bool(ladies)),
          section,stage,None,rank_number,suffix,pct,str(raw.get("category","")).strip(),
          program_name,source_pdf,int(raw["page"]) if str(raw.get("page","")).isdigit() else None)
    cur=conn.execute("""INSERT OR IGNORE INTO cutoffs
        (year,round,institution_code,program_id,base_category,is_ladies,section_code,
         stage_code,home_university,rank_number,rank_suffix,percentile,raw_category,
         raw_program_name,source_pdf,source_page)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
    if cur.rowcount:
        rid=cur.lastrowid
        after=dict(zip(["year","round","institution_code","program_id","base_category","is_ladies",
                        "section_code","stage_code","home_university","rank_number","rank_suffix",
                        "percentile","raw_category","raw_program_name","source_pdf","source_page"], vals))
        return rid, after
    return None, None


def _insert_seat_from_json(conn, raw, job, source_pdf):
    from scripts.ingest_seats import canonical_institution_code
    from scripts.validate_seats import load_whitelists, normalize_category, TOTAL_MARKER
    valid_cats, lane_map, alias_map = load_whitelists(BASE/"data"/"reference")
    inst=canonical_institution_code(str(raw.get("institution_code","")),str(raw.get("choice_code","")))
    lane_raw=str(raw.get("allocation_type","")).strip()
    lane=lane_map.get(lane_raw)
    if not lane: raise ValueError(f"Unknown allocation type: {lane_raw!r}")
    cat=str(raw.get("category","")).strip()
    total=cat==TOTAL_MARKER
    base=None if total else normalize_category(cat,alias_map)
    if not total and base not in valid_cats: raise ValueError(f"Unknown category: {cat!r}")
    gender=str(raw.get("gender","")).strip()
    is_ladies={"G":False,"L":True}.get(gender) if gender else None
    vals=(int(job["year"]),job["course_family"],inst,str(raw.get("choice_code","")).strip(),
          lane,base,int(total),is_ladies,int(raw["seats"]),cat,lane_raw,gender,
          source_pdf,int(raw["page"]) if str(raw.get("page","")).isdigit() else None)
    cur=conn.execute("""INSERT OR IGNORE INTO seats
        (capture_year,program_family,institution_code,choice_code,allocation_lane,base_category,
         is_total,is_ladies,seats,raw_category,raw_allocation_type,raw_gender,source_pdf,source_page)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
    if cur.rowcount:
        rid=cur.lastrowid
        cols=["capture_year","program_family","institution_code","choice_code","allocation_lane",
              "base_category","is_total","is_ladies","seats","raw_category","raw_allocation_type",
              "raw_gender","source_pdf","source_page"]
        return rid, dict(zip(cols,vals))
    return None,None


def approve_import(conn, job_id: int, actor_user_id: int, notes: str = ""):
    """Atomically promote a clean staged import into production and create a release."""
    job=conn.execute("SELECT * FROM import_jobs WHERE id=?",(job_id,)).fetchone()
    if not job: raise ValueError("Import not found")
    if job["status"] != JobStatus.REVIEW_REQUIRED.value:
        raise ValueError(f"Import is not awaiting approval: {job['status']}")
    dtype=job["data_type"]
    _require_clean_stage(conn,job_id,dtype)
    conn.execute("BEGIN IMMEDIATE")
    try:
        update_status(conn,job_id,JobStatus.APPROVED.value,"Approved by admin; beginning transactional production commit")
        event(conn,job_id,"APPROVAL","Production promotion approved",None)
        update_status(conn,job_id,JobStatus.COMMITTING.value,"Promoting staged rows into production")
        inserted=[]
        source_pdf=job["original_filename"]
        for r in _load_staged_rows(conn,job_id,dtype):
            raw=json.loads(r["normalized_json"])
            if dtype=="CUTOFFS":
                rid, after=_insert_cutoff_from_json(conn,raw,job,source_pdf)
                table="cutoffs"
            else:
                rid, after=_insert_seat_from_json(conn,raw,job,source_pdf)
                table="seats"
            if rid is not None:
                inserted.append((table,rid,after))
        release_key=f"REL-{job['job_key']}"
        conn.execute("""INSERT INTO data_releases
            (release_key,source_job_id,source_sha256,status,approved_by,notes)
            VALUES (?,?,?,?,?,?)""",
            (release_key,job_id,job["sha256"],"COMMITTED",actor_user_id,notes))
        release_id=conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for table,rid,after in inserted:
            conn.execute("""INSERT INTO data_release_items
                (release_id,table_name,row_id,operation,before_json,after_json)
                VALUES (?,?,?,?,?,?)""",
                (release_id,table,rid,"INSERT",None,json.dumps(after,ensure_ascii=False)))
        event(conn,job_id,"COMMIT",f"Committed {len(inserted)} new production rows",90)
        update_status(conn,job_id,JobStatus.COMMITTING.value,"Production commit completed; release created; publishing is the next phase")
        event(conn,job_id,"RELEASE",f"Release {release_key} committed",100)
        conn.execute("""INSERT INTO audit_log
            (actor_user_id,action,entity_type,entity_id,before_json,after_json,reason)
            VALUES (?,?,?,?,?,?,?)""",
            (actor_user_id,"APPROVE_IMPORT","IMPORT",str(job_id),None,
             json.dumps({"release_id":release_id,"release_key":release_key,
                         "rows_inserted":len(inserted)},ensure_ascii=False),notes or "Approved import"))
        conn.commit()
        return {"ok":True,"job_id":job_id,"status":"COMMITTING","release_key":release_key,
                "release_id":release_id,"rows_inserted":len(inserted)}
    except Exception:
        conn.rollback()
        # Preserve a durable failure state in a separate transaction.
        conn.execute("UPDATE import_jobs SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (JobStatus.FAILED.value,"Production commit failed; transaction rolled back",job_id))
        conn.execute("INSERT INTO import_events(job_id,event_type,message,progress) VALUES (?,?,?,?)",
                     (job_id,"ERROR","Production commit failed; transaction rolled back",None))
        conn.commit()
        raise


def rollback_release(conn, release_id: int, actor_user_id: int, reason: str):
    release=conn.execute("SELECT * FROM data_releases WHERE id=?",(release_id,)).fetchone()
    if not release: raise ValueError("Release not found")
    if release["status"] == "ROLLED_BACK": raise ValueError("Release is already rolled back")
    # A published release also has a filesystem backup. Restore that public tree
    # before committing the database rollback, and keep a current-site fallback
    # so a failed DB transaction can restore the public tree too.
    public_root=BASE/'site'; current_backup=None; restored=False
    if release["status"] == "PUBLISHED" and release["backup_path"]:
        backup=BASE/release["backup_path"]
        if not backup.exists(): raise ValueError("Published release backup is missing; refusing rollback")
        current_backup=BASE/'data'/'publish_work'/f'rollback_current_{release_id}_{os.getpid()}_{int(time.time())}'
        current_backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(public_root,current_backup)
        temp=BASE/'data'/'publish_work'/f'rollback_target_{release_id}_{os.getpid()}_{int(time.time())}'
        if temp.exists(): shutil.rmtree(temp)
        shutil.copytree(backup,temp)
        old=BASE/f'.site_rollback_old_{release_id}_{os.getpid()}'
        if old.exists(): shutil.rmtree(old)
        os.replace(public_root,old); os.replace(temp,public_root); shutil.rmtree(old,ignore_errors=True); restored=True
    conn.execute("BEGIN IMMEDIATE")
    try:
        items=conn.execute("SELECT * FROM data_release_items WHERE release_id=? ORDER BY id DESC",(release_id,)).fetchall()
        deleted=0
        for item in items:
            table=item["table_name"]
            if table not in {"cutoffs","seats"}: raise ValueError("Unsupported release table")
            cur=conn.execute(f"DELETE FROM {table} WHERE id=?",(item["row_id"],))
            deleted += cur.rowcount
        conn.execute("UPDATE data_releases SET status='ROLLED_BACK',notes=? WHERE id=?",
                     (f"{release['notes'] or ''}\nRollback: {reason}",release_id))
        if release["source_job_id"]:
            update_status(conn,release["source_job_id"],JobStatus.ROLLED_BACK.value,"Release rolled back")
            event(conn,release["source_job_id"],"ROLLBACK",f"Release {release['release_key']} rolled back; deleted {deleted} inserted rows",100)
        conn.execute("""INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,before_json,after_json,reason) VALUES (?,?,?,?,?,?,?)""",
            (actor_user_id,"ROLLBACK_RELEASE","RELEASE",str(release_id),json.dumps({"status":release["status"]}),json.dumps({"status":"ROLLED_BACK","deleted":deleted}),reason))
        conn.commit()
        if current_backup: shutil.rmtree(current_backup,ignore_errors=True)
        return {"ok":True,"release_id":release_id,"deleted":deleted,"status":"ROLLED_BACK"}
    except Exception:
        conn.rollback()
        if restored and current_backup and current_backup.exists():
            temp=BASE/'data'/'publish_work'/f'rollback_recover_{release_id}_{os.getpid()}_{int(time.time())}'
            if temp.exists(): shutil.rmtree(temp)
            shutil.copytree(current_backup,temp)
            old=BASE/f'.site_rollback_recover_old_{release_id}_{os.getpid()}'
            if old.exists(): shutil.rmtree(old)
            os.replace(public_root,old); os.replace(temp,public_root); shutil.rmtree(old,ignore_errors=True)
        raise
