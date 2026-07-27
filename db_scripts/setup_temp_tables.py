import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import mysql.connector
from mysql.connector import errorcode

# Configuration for MySQL connection
config = {
    'user': 'root',
    'password': 'aftab',
    'host': 'localhost',
    'database': 'tania_db'
}

TABLES = {}

TABLES['tania_temp_customers'] = (
    "CREATE TABLE IF NOT EXISTS `tania_temp_customers` ("
    "  `temp_customer_id` int(11) NOT NULL AUTO_INCREMENT,"
    "  `mobile` varchar(20) NOT NULL,"
    "  `name` varchar(255) DEFAULT NULL,"
    "  `email` varchar(255) DEFAULT NULL,"
    "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
    "  PRIMARY KEY (`temp_customer_id`),"
    "  UNIQUE KEY `mobile` (`mobile`)"
    ") ENGINE=InnoDB"
)

TABLES['tania_temp_addresses'] = (
    "CREATE TABLE IF NOT EXISTS `tania_temp_addresses` ("
    "  `temp_address_id` int(11) NOT NULL AUTO_INCREMENT,"
    "  `temp_customer_id` int(11) NOT NULL,"
    "  `address` text NOT NULL,"
    "  `map_info` varchar(500) DEFAULT '{\"latitude\":\"0.000000\",\"longitude\":\"0.000000\"}',"
    "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
    "  PRIMARY KEY (`temp_address_id`),"
    "  KEY `fk_temp_customer_id` (`temp_customer_id`),"
    "  CONSTRAINT `fk_temp_customer_id` FOREIGN KEY (`temp_customer_id`) "
    "     REFERENCES `tania_temp_customers` (`temp_customer_id`) ON DELETE CASCADE"
    ") ENGINE=InnoDB"
)

TABLES['tania_temp_orders'] = (
    "CREATE TABLE IF NOT EXISTS `tania_temp_orders` ("
    "  `temp_order_id` int(11) NOT NULL AUTO_INCREMENT,"
    "  `temp_customer_id` int(11) NOT NULL,"
    "  `temp_address_id` int(11) NOT NULL,"
    "  `items` json NOT NULL,"
    "  `total_amount` decimal(10,2) NOT NULL,"
    "  `payment_method` enum('online','cash_on_delivery') NOT NULL,"
    "  `status` enum('pending','confirmed','cancelled') DEFAULT 'confirmed',"
    "  `rating` tinyint(4) DEFAULT NULL,"
    "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
    "  PRIMARY KEY (`temp_order_id`),"
    "  KEY `fk_order_customer` (`temp_customer_id`),"
    "  KEY `fk_order_address` (`temp_address_id`),"
    "  CONSTRAINT `fk_order_customer` FOREIGN KEY (`temp_customer_id`) "
    "     REFERENCES `tania_temp_customers` (`temp_customer_id`) ON DELETE CASCADE,"
    "  CONSTRAINT `fk_order_address` FOREIGN KEY (`temp_address_id`) "
    "     REFERENCES `tania_temp_addresses` (`temp_address_id`) ON DELETE CASCADE"
    ") ENGINE=InnoDB"
)

def create_tables():
    try:
        cnx = mysql.connector.connect(**config)
        cursor = cnx.cursor()
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Something is wrong with your user name or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Database does not exist")
        else:
            print(err)
        return

    for table_name in TABLES:
        table_description = TABLES[table_name]
        try:
            print(f"Creating table {table_name}: ", end='')
            cursor.execute(table_description)
            print("OK")
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_TABLE_EXISTS_ERROR:
                print("already exists.")
            else:
                print(err.msg)
    
    cursor.close()
    cnx.close()

if __name__ == '__main__':
    create_tables()
