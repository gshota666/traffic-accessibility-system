import json
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

from utils.paths import resource

SCHEMA = '''
CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,name TEXT,created TEXT,columns_json TEXT,crs TEXT,stats TEXT);
CREATE TABLE IF NOT EXISTS points(id INTEGER PRIMARY KEY,dataset_id TEXT REFERENCES datasets(id) ON DELETE CASCADE,row_no INTEGER,raw TEXT,point_id TEXT,name TEXT,lon REAL,lat REAL,crs TEXT,wlon REAL,wlat REAL,valid INTEGER,issues TEXT,category TEXT NOT NULL DEFAULT '',institution_id TEXT NOT NULL DEFAULT '',institution_name TEXT NOT NULL DEFAULT '',entrance_id TEXT NOT NULL DEFAULT '',source_id TEXT DEFAULT '',source_name TEXT DEFAULT '',province TEXT DEFAULT '',city TEXT DEFAULT '',county TEXT DEFAULT '',population REAL,population_year INTEGER,population_scope TEXT DEFAULT '',population_source TEXT DEFAULT '',notes TEXT DEFAULT '',village_name TEXT DEFAULT '',entrance_name TEXT DEFAULT '',coordinate_source TEXT DEFAULT '',verification_status TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS points_dataset ON points(dataset_id,valid,id);
CREATE TABLE IF NOT EXISTS targets(id INTEGER PRIMARY KEY,target_id TEXT DEFAULT '',name TEXT,lon REAL,lat REAL,crs TEXT,note TEXT,category TEXT NOT NULL DEFAULT '',institution_id TEXT NOT NULL DEFAULT '',institution_name TEXT NOT NULL DEFAULT '',entrance_id TEXT NOT NULL DEFAULT '',province TEXT DEFAULT '',city TEXT DEFAULT '',county TEXT DEFAULT '',village_name TEXT DEFAULT '',entrance_name TEXT DEFAULT '',coordinate_source TEXT DEFAULT '',verification_status TEXT DEFAULT '');
-- Keep a lossless link from an imported target row to the dataset it came
-- from.  This is intentionally separate from ``targets`` so existing target
-- and job schemas remain compatible while new task snapshots can preserve
-- every original destination field.
CREATE TABLE IF NOT EXISTS target_sources(target_id INTEGER PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,dataset_id TEXT,row_no INTEGER,raw TEXT);
CREATE INDEX IF NOT EXISTS target_sources_dataset ON target_sources(dataset_id,target_id);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,name TEXT,created TEXT,status TEXT,dataset_id TEXT REFERENCES datasets(id),config TEXT,total INTEGER DEFAULT 0,prepared INTEGER DEFAULT 0,success INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,message TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY,job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,point_pk INTEGER,target_id INTEGER,departure TEXT,point_id TEXT,point_name TEXT,source_id TEXT DEFAULT '',destination_id TEXT DEFAULT '',origin_id TEXT DEFAULT '',route_target_id TEXT DEFAULT '',origin_lon REAL,origin_lat REAL,origin_crs TEXT,wlon REAL,wlat REAL,target_name TEXT,target_lon REAL,target_lat REAL,target_crs TEXT,twlon REAL,twlat REAL,straight_km REAL,distance_meters REAL,duration_seconds REAL,traffic_type TEXT,provider TEXT,status TEXT DEFAULT 'pending',error TEXT,request_coords TEXT,raw_status TEXT,provider_error_code TEXT DEFAULT '',calculated_at TEXT,fetched_at TEXT,cache_hit INTEGER DEFAULT 0,requested_at TEXT,query_order TEXT,last_requested_at TEXT,attempts INTEGER DEFAULT 0,direction TEXT DEFAULT 'outbound',study_id TEXT DEFAULT '',batch_id TEXT DEFAULT '',observation_id TEXT DEFAULT '',planned_datetime TEXT,window_end_datetime TEXT,route_selection_rule TEXT DEFAULT 'first',route_index INTEGER DEFAULT 0,route_id TEXT DEFAULT '',api_version TEXT DEFAULT '',raw_response_path TEXT DEFAULT '',traffic_coverage REAL,request_started_at TEXT,response_received_at TEXT,saved_at TEXT,request_delay_seconds REAL,response_crossed_window INTEGER DEFAULT 0,window_miss_reason TEXT DEFAULT '',inflight INTEGER DEFAULT 0,inflight_started_at TEXT,UNIQUE(job_id,point_pk,target_id,departure,direction));
CREATE INDEX IF NOT EXISTS result_pending ON results(job_id,status,id);
CREATE INDEX IF NOT EXISTS result_sort ON results(job_id,duration_seconds);
CREATE INDEX IF NOT EXISTS result_point ON results(job_id,point_pk,departure);
CREATE INDEX IF NOT EXISTS result_schedule_order ON results(job_id,status,departure,query_order,id);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,value TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS usage(day TEXT,provider TEXT,calls INTEGER DEFAULT 0,errors INTEGER DEFAULT 0,PRIMARY KEY(day,provider));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,created TEXT,job_id TEXT,level TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS request_attempts(id INTEGER PRIMARY KEY,attempt_id TEXT UNIQUE,observation_id TEXT,job_id TEXT,started_at TEXT,finished_at TEXT,http_status INTEGER,business_status TEXT,provider_error_code TEXT DEFAULT '',error TEXT,retry_reason TEXT,request_params TEXT,raw_response_path TEXT,success INTEGER DEFAULT 0,rate_wait_seconds REAL,provider_duration_seconds REAL,raw_persistence_duration_seconds REAL,segment_persistence_duration_seconds REAL,database_duration_seconds REAL,persistence_duration_seconds REAL,total_duration_seconds REAL,max_concurrency INTEGER);
CREATE INDEX IF NOT EXISTS attempts_job ON request_attempts(job_id,observation_id);
CREATE TABLE IF NOT EXISTS traffic_segments(id INTEGER PRIMARY KEY,observation_id TEXT,segment_index INTEGER,status_raw TEXT,status_normalized TEXT,length_m REAL,polyline TEXT,raw_json TEXT);
CREATE INDEX IF NOT EXISTS segments_observation ON traffic_segments(observation_id);
'''


class Database:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'traffic.db'
        self._backup_before_migration()
        with self.connect() as db:
            # Do not create the new direction index until the compatibility
            # rebuild below has run; old databases have no direction column.
            # Result indexes reference columns introduced over several schema
            # versions.  Create the tables first, migrate/add columns, then
            # create those indexes below; otherwise an old database fails
            # before its migration can run.
            schema_without_result_indexes=SCHEMA
            for index_sql in (
                'CREATE INDEX IF NOT EXISTS result_pending ON results(job_id,status,id);',
                'CREATE INDEX IF NOT EXISTS result_sort ON results(job_id,duration_seconds);',
                'CREATE INDEX IF NOT EXISTS result_point ON results(job_id,point_pk,departure);',
                'CREATE INDEX IF NOT EXISTS result_schedule_order ON results(job_id,status,departure,query_order,id);',
            ):
                schema_without_result_indexes=schema_without_result_indexes.replace(index_sql,'')
            db.executescript(schema_without_result_indexes)
            self._migrate_results(db)
            self._add_columns(db)
            db.execute('CREATE INDEX IF NOT EXISTS result_direction ON results(job_id,direction,departure)')
            db.execute("PRAGMA user_version=12")
            if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
                cities = json.loads(resource('resources/capitals.json').read_text(encoding='utf-8'))
                db.executemany('INSERT INTO targets(name,lon,lat,crs,note) VALUES(:name,:lon,:lat,:crs,:note)', cities)
                db.execute("INSERT INTO settings VALUES('seeded','true')")

    def _backup_before_migration(self):
        """Make a SQLite-consistent backup before changing an existing schema."""
        if not self.path.exists():
            return
        probe = sqlite3.connect(self.path)
        try:
            version = int(probe.execute('PRAGMA user_version').fetchone()[0] or 0)
            if version >= 12:
                return
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = self.path.with_name(f'{self.path.stem}.pre-migration-{stamp}.db')
            # The backup API includes WAL contents and is safer than copying a
            # live file byte-for-byte.  Never overwrite an earlier backup.
            if not backup.exists():
                target = sqlite3.connect(backup)
                try:
                    probe.backup(target)
                finally:
                    target.close()
        finally:
            probe.close()

    @staticmethod
    def _migrate_results(db):
        columns = {r[1] for r in db.execute('PRAGMA table_info(results)')}
        if 'direction' in columns:
            return
        old = [r[1] for r in db.execute('PRAGMA table_info(results)')]
        db.execute('ALTER TABLE results RENAME TO results_legacy')
        db.execute('''CREATE TABLE results(id INTEGER PRIMARY KEY,job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,point_pk INTEGER,target_id INTEGER,departure TEXT,point_id TEXT,point_name TEXT,source_id TEXT DEFAULT '',destination_id TEXT DEFAULT '',origin_id TEXT DEFAULT '',route_target_id TEXT DEFAULT '',origin_lon REAL,origin_lat REAL,origin_crs TEXT,wlon REAL,wlat REAL,target_name TEXT,target_lon REAL,target_lat REAL,target_crs TEXT,twlon REAL,twlat REAL,straight_km REAL,distance_meters REAL,duration_seconds REAL,traffic_type TEXT,provider TEXT,status TEXT DEFAULT 'pending',error TEXT,request_coords TEXT,raw_status TEXT,provider_error_code TEXT DEFAULT '',calculated_at TEXT,fetched_at TEXT,cache_hit INTEGER DEFAULT 0,requested_at TEXT,query_order TEXT,last_requested_at TEXT,attempts INTEGER DEFAULT 0,direction TEXT DEFAULT 'outbound',study_id TEXT DEFAULT '',batch_id TEXT DEFAULT '',observation_id TEXT DEFAULT '',planned_datetime TEXT,window_end_datetime TEXT,route_selection_rule TEXT DEFAULT 'first',route_index INTEGER DEFAULT 0,route_id TEXT DEFAULT '',api_version TEXT DEFAULT '',raw_response_path TEXT DEFAULT '',traffic_coverage REAL,request_started_at TEXT,response_received_at TEXT,saved_at TEXT,request_delay_seconds REAL,response_crossed_window INTEGER DEFAULT 0,window_miss_reason TEXT DEFAULT '',UNIQUE(job_id,point_pk,target_id,departure,direction))''')
        keep = ','.join(old)
        db.execute(f"INSERT INTO results({keep},direction) SELECT {keep},'outbound' FROM results_legacy")
        db.execute('DROP TABLE results_legacy')
        for stmt in ('CREATE INDEX IF NOT EXISTS result_pending ON results(job_id,status,id)', 'CREATE INDEX IF NOT EXISTS result_sort ON results(job_id,duration_seconds)', 'CREATE INDEX IF NOT EXISTS result_point ON results(job_id,point_pk,departure)', 'CREATE INDEX IF NOT EXISTS result_schedule_order ON results(job_id,status,departure,query_order,id)'):
            db.execute(stmt)

    @staticmethod
    def _add_columns(db):
        additions = {
            'points': [('source_id', "TEXT DEFAULT ''"), ('source_name', "TEXT DEFAULT ''"), ('province', "TEXT DEFAULT ''"), ('city', "TEXT DEFAULT ''"), ('county', "TEXT DEFAULT ''"), ('population', 'REAL'), ('population_year', 'INTEGER'), ('population_scope', "TEXT DEFAULT ''"), ('population_source', "TEXT DEFAULT ''"), ('notes', "TEXT DEFAULT ''"), ('village_name', "TEXT DEFAULT ''"), ('entrance_name', "TEXT DEFAULT ''"), ('coordinate_source', "TEXT DEFAULT ''"), ('verification_status', "TEXT DEFAULT ''")],
            'targets': [('target_id', "TEXT DEFAULT ''"), ('province', "TEXT DEFAULT ''"), ('city', "TEXT DEFAULT ''"), ('county', "TEXT DEFAULT ''"), ('village_name', "TEXT DEFAULT ''"), ('entrance_name', "TEXT DEFAULT ''"), ('coordinate_source', "TEXT DEFAULT ''"), ('verification_status', "TEXT DEFAULT ''")],
            'results': [('requested_at', 'TEXT'), ('query_order', 'TEXT'), ('last_requested_at', 'TEXT'), ('attempts', 'INTEGER DEFAULT 0'), ('direction', "TEXT DEFAULT 'outbound'"), ('study_id', "TEXT DEFAULT ''"), ('batch_id', "TEXT DEFAULT ''"), ('observation_id', "TEXT DEFAULT ''"), ('planned_datetime', 'TEXT'), ('window_end_datetime', 'TEXT'), ('route_selection_rule', "TEXT DEFAULT 'first'"), ('route_index', 'INTEGER DEFAULT 0'), ('route_id', "TEXT DEFAULT ''"), ('api_version', "TEXT DEFAULT ''"), ('raw_response_path', "TEXT DEFAULT ''"), ('traffic_coverage', 'REAL'), ('origin_id', "TEXT DEFAULT ''"), ('route_target_id', "TEXT DEFAULT ''"), ('source_id', "TEXT DEFAULT ''"), ('destination_id', "TEXT DEFAULT ''"), ('provider_error_code', "TEXT DEFAULT ''"), ('request_started_at', 'TEXT'), ('response_received_at', 'TEXT'), ('saved_at', 'TEXT'), ('request_delay_seconds', 'REAL'), ('response_crossed_window', 'INTEGER DEFAULT 0'), ('window_miss_reason', "TEXT DEFAULT ''"), ('inflight', 'INTEGER DEFAULT 0'), ('inflight_started_at', 'TEXT')],
            'request_attempts': [('provider_error_code', "TEXT DEFAULT ''"), ('rate_wait_seconds', 'REAL'), ('provider_duration_seconds', 'REAL'), ('raw_persistence_duration_seconds', 'REAL'), ('segment_persistence_duration_seconds', 'REAL'), ('database_duration_seconds', 'REAL'), ('persistence_duration_seconds', 'REAL'), ('total_duration_seconds', 'REAL'), ('max_concurrency', 'INTEGER')],
        }
        for table, specs in additions.items():
            existing = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            for name, kind in specs:
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
            db.execute("UPDATE results SET direction='outbound' WHERE direction IS NULL OR direction=''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).lastrowid
