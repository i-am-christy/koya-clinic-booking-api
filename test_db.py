import os
import psycopg
from dotenv import load_dotenv

load_dotenv() #reads the content of the .env file
url = os.environ["DATABASE_URL"] #get the value of the DATABASE_URL variable

with psycopg.connect(url) as conn:
    row = conn.execute("SELECT 1").fetchone()
    print("Connected! Result:", row)