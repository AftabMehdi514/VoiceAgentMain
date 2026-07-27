import mysql.connector
from config import DBPassword

db_config = {
    'user': 'root',
    'password': DBPassword,
    'host': 'localhost',
    'database': 'tania_db'
}

def get_connection():
    try:
        cnx = mysql.connector.connect(**db_config)
        return cnx
    except mysql.connector.Error as err:
        print(f"Error connecting to MySQL: {err}")
        return None
