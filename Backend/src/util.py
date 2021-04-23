import pyodbc
import uuid
import json
import yaml
import os
from mysql.connector import connect, Error
from datetime import datetime, timedelta
import ipaddress

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
        
cfg = None
api_token = None

def create_ssl_cert(
    ip_addresses=None,
    KEY_FILE = "../webroot/ssl/key.pem",
    CERT_FILE = "../webroot/ssl/cert.crt"):

    if not os.path.isfile(CERT_FILE) and not os.path.isfile(KEY_FILE):
        # Generate our key
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, cfg['backendHostname'])
        ])
    
        # best practice seem to be to include the hostname in the SAN, which *SHOULD* mean COMMON_NAME is ignored.    
        alt_names = [x509.DNSName(cfg['backendHostname']), x509.DNSName('localhost')]
        
        # allow addressing by IP, for when you don't have real DNS (common in most testing scenarios 
        if ip_addresses:
            for addr in ip_addresses:
                # openssl wants DNSnames for ips...
                alt_names.append(x509.DNSName(addr))
                # ... whereas golang's crypto/tls is stricter, and needs IPAddresses
                # note: older versions of cryptography do not understand ip_address objects
                alt_names.append(x509.IPAddress(ipaddress.ip_address(addr)))
        
        san = x509.SubjectAlternativeName(alt_names)
        
        # path_len=0 means this cert can only sign itself, not other certs.
        basic_contraints = x509.BasicConstraints(ca=True, path_length=0)
        now = datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1000)
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=10*365))
            .add_extension(basic_contraints, False)
            .add_extension(san, False)
            .sign(key, hashes.SHA256(), default_backend())
        )
        cert_pem = cert.public_bytes(encoding=serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        open(KEY_FILE, 'wb').write(key_pem)
        open(CERT_FILE, 'wb').write(cert_pem)

def create_web_config():
    web_json = "../webroot/settings/settings.json"
    web_cfg = {"useSSL": cfg['useSSL'], "backendIP": cfg['backendIP'], "backendPort": cfg['backendPort'], "backendToken": api_token, "language": cfg['backendLanguage']}
    f = open(web_json, "w")
    f.write(json.dumps(web_cfg))
    f.close()    

def check_existing_token():
    if not os.path.isfile(r'.api_token'):
        create_token()
    else:
        read_token()

    return api_token

def read_token():
    global api_token
    if not api_token:
        with open(r'.api_token') as f:
            api_token = f.readline()

def create_token():
    global api_token
    new_token = str(uuid.uuid4())[:8]
    f = open(".api_token", "w")
    f.write(new_token)
    api_token = new_token
    f.close()    

def load_conf():
    global cfg
    run_in_docker = os.environ.get('RUN_IN_DOCKER', False)
    if not cfg:
        if not run_in_docker:
            print("Running in normal mode!")
            with open("../config.yaml", "r") as ymlfile:
                cfg = yaml.load(ymlfile, Loader=yaml.FullLoader)
        else:
            print("Running in docker mode!")
            use_ssl = os.environ.get('useSSL')
            backend_hostname = os.environ.get('backendHostname', "")
            backend_ip = os.environ.get('backendIP', "")
            backend_port = os.environ.get('backendPort', "")
            backend_language = os.environ.get('backendLanguage', "")
            parser_ip = os.environ.get('parserIP', "")
            parser_port = os.environ.get('parserPort', "")
            parser_token = os.environ.get('parserToken', "")
            dbMode = os.environ.get('dbMode', "")
            sql_server_ip = os.environ.get('sqlServerIP', "")
            sql_database = os.environ.get('sqlDatabase', "")
            sql_username = os.environ.get('sqlUsername', "")
            sql_password = os.environ.get('sqlPassword', "")

            tmpCfg = {"useSSL": use_ssl, "backendHostname": backend_hostname, "backendIP": backend_ip, "backendPort": backend_port, "backendLanguage": backend_language, "parserIP": parser_ip, "parserPort": parser_port, "parserToken": parser_token, "dbMode": dbMode, "sqlServerIP": sql_server_ip, "sqlDatabase": sql_database, "sqlUsername": sql_username, "sqlPassword": sql_password}

            jsonCfg = json.dumps(tmpCfg)
            cfg = json.loads(jsonCfg)

    return cfg

def delete_from_DB(table_name, id):
    conn, cur = load_db_conn()

    sql_query = "DELETE FROM " + table_name + " WHERE id = ?"
    if cfg['dbMode'] == "mysql":
        sql_query = convert_to_mysql_query(sql_query)

    cur.execute(sql_query, [id])
    conn.commit()
    conn.close()

def add_or_update_to_db(to_add_table, id, to_add_value):
    conn, cur = load_db_conn()

    if to_add_table == "categories":
        name_col = "categoryName"
    elif to_add_table == "stores":
        name_col = "storeName"

    if id:
        sql_update = ''' UPDATE ''' + to_add_table + ''' SET ''' + name_col + ''' = ? WHERE id = ?'''
        if cfg['dbMode'] == "mysql":
            sql_update = convert_to_mysql_query(sql_update)

        cur.execute(sql_update, [to_add_value, id])
    else:
        id = int(str(uuid.uuid1().int)[:6])
        sql_insert = ''' INSERT INTO ''' + to_add_table +  ''' VALUES (?, ?)'''

        if cfg['dbMode'] == "mysql":
            sql_insert = convert_to_mysql_query(sql_insert)

        cur.execute(sql_insert,  [id, to_add_value]) 

    conn.commit()
    conn.close()
    return id

def get_data_from_db(tableName):
    conn, cur = load_db_conn()

    if tableName == "categories":
        orderby = 'categoryName'
    elif tableName == "stores":
        orderby = 'storeName'

    sql_select = ''' SELECT * from ''' + tableName + ''' order by ''' + orderby
    cur.execute(sql_select)
    rows = cur.fetchall()
    conn.close()

    ret_array = json.dumps({"values": []})
    ret_json = json.loads(ret_array)

    for row in rows:
        add_array = {"name": row[1], "id": row[0]}
        ret_json["values"].append(add_array)

    return ret_json

def load_db_conn():
    if cfg['dbMode'] == "mssql":
        conn, cur = create_ms_db_conn()
    elif cfg['dbMode'] == "mysql":
        conn, cur = create_mysql_db_conn()
    else:
        print ("Error! No valid db mode found. Please use mssql or mysql")

    return conn, cur
def create_ms_db_conn():
        global cfg
        if not cfg:
            cfg = load_conf()

        conn = pyodbc.connect(Driver="{ODBC Driver 17 for SQL Server}", Server=cfg['sqlServerIP'],Database=cfg['sqlDatabase'], user=cfg['sqlUsername'], password=cfg['sqlPassword'])
        cur = conn.cursor()

        return conn, cur

def convert_to_mysql_query(sql_query):
    sql_query = sql_query.replace('?','%s')
    return sql_query

def create_mysql_db_conn():
    global cfg
    if not cfg:
        cfg = load_conf()

    try:
        conn = connect(
            host=cfg['sqlServerIP'],
            user=cfg['sqlUsername'],
            password=cfg['sqlPassword'],
            database=cfg['sqlDatabase'])
    except Error as e:
        print(e)

    cur = conn.cursor()
    return conn, cur

def init_mysql_db (conn):
    create_receipts_tags = "CREATE TABLE IF NOT EXISTS tags (id int, tagName nvarchar(50), PRIMARY KEY(id)); " 
    create_receipts_stores = "CREATE TABLE IF NOT EXISTS stores (id int, storeName nvarchar(50), PRIMARY KEY(id));" 
    create_receipts_categories = "CREATE TABLE IF NOT EXISTS categories (id int, categoryName nvarchar(50), PRIMARY KEY(id)); "
    create_receipts_items = "CREATE TABLE IF NOT EXISTS items (id int, itemName nvarchar(100), itemTotal decimal(15,2), categoryId int, FOREIGN KEY (categoryId) REFERENCES categories(id), PRIMARY KEY(id));"
    create_receipts_purchasesArticles = " CREATE TABLE IF NOT EXISTS purchasesArticles (id int, itemid int, FOREIGN KEY (itemid) REFERENCES items(id));"
    create_receipts_receipts = " CREATE TABLE IF NOT EXISTS receipts (id int, storeId int, `date` date, total decimal(15,2), tagId int, FOREIGN KEY (tagId) REFERENCES tags(id), purchaseId int, PRIMARY KEY(id));"
                             

    create_receipts_view = """
                                CREATE OR REPLACE VIEW purchaseData AS
                                select i.itemName article_name, 1 amount, itemTotal total, c.categoryName category, storeName location, date timestamp, CONVERT(r.id, char) id from receipts r
                                    JOIN stores s ON r.storeId = s.id
                                    JOIN purchasesArticles pa ON r.purchaseId = pa.id
                                    JOIN items i on pa.itemid = i.id
                                    JOIN categories c on c.id = i.categoryId
                           """

    if conn:
        create_mysql_table(conn, create_receipts_tags)
        create_mysql_table(conn, create_receipts_stores)
        create_mysql_table(conn, create_receipts_categories)
        create_mysql_table(conn, create_receipts_items)
        create_mysql_table(conn, create_receipts_purchasesArticles)
        create_mysql_table(conn, create_receipts_receipts)
        create_mysql_table(conn, create_receipts_view)
        conn.close()
    else:
        print ("Error! cannot create the database connection.")

def init_mssql_db (conn):
    create_receipts_tables = """ IF object_id('tags', 'U') is null
                                    CREATE TABLE tags (id int PRIMARY KEY, tagName nvarchar(50))
                                IF object_id('stores', 'U') is null
                                    CREATE TABLE stores (id int PRIMARY KEY, storeName nvarchar(50))
                                IF object_id('categories', 'U') is null
                                    CREATE TABLE categories (id int PRIMARY KEY, categoryName nvarchar(50))
                                IF object_id('items', 'U') is null
                                    CREATE TABLE items (id int PRIMARY KEY, itemName nvarchar(100), itemTotal decimal(15,2), categoryId int FOREIGN KEY REFERENCES categories(id))
                                IF object_id('purchasesArticles', 'U') is null
                                    CREATE TABLE purchasesArticles (id int, itemid int FOREIGN KEY REFERENCES items(id))
                                IF object_id('receipts', 'U') is null
                                    CREATE TABLE receipts (id int PRIMARY KEY, storeId int, [date] date, total decimal(15,2), tagId int FOREIGN KEY REFERENCES tags(id), purchaseId int) 
                           """
    create_receipts_view = """  IF object_id('purchaseData', 'V') is null
                                EXEC('CREATE VIEW purchaseData AS
                                    select i.itemName article_name, 1 amount, itemTotal total, c.categoryName category, storeName location, date timestamp, CONVERT(varchar, r.id) id from receipts r
                                        JOIN stores s ON r.storeId = s.id
                                        JOIN purchasesArticles pa ON r.purchaseId = pa.id
                                        JOIN items i on pa.itemid = i.id
                                        JOIN categories c on c.id = i.categoryId')
                            """
    if conn:
        create_mssql_table(conn, create_receipts_tables)
        create_mssql_table(conn, create_receipts_view)
        conn.close()
    else:
        print ("Error! cannot create the database connection.")

def create_mysql_table(conn, sql_query):
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()

    except Error as e:
        print(e)

def create_mssql_table(conn, sql_query):
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()

    except pyodbc.Error as e:
        print(e)

def get_category_id(category_name):
    conn, cursor = load_db_conn()

    sql_query = "select id from categories where categoryName = ?"
    if cfg['dbMode'] == "mysql":
        sql_query = convert_to_mysql_query(sql_query)

    cursor.execute(sql_query, [category_name])
    rows = cursor.fetchone()
    conn.close()

    category_id = rows[0]

    return category_id

def get_store_id(store_name):
    conn, cursor = load_db_conn()
    
    sql_query = "select id from stores where storeName = ?"
    if cfg['dbMode'] == "mysql":
        sql_query = convert_to_mysql_query(sql_query)

    cursor.execute(sql_query, [store_name])
    rows = cursor.fetchone()
    conn.close()

    if rows:
        store_id = rows[0]
    else:
        store_id = add_or_update_to_db("stores", None, store_name)

    return store_id