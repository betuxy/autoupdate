-- icingadb database is created by MYSQL_DATABASE env var; just create the user.
CREATE USER IF NOT EXISTS 'icingadb'@'%' IDENTIFIED BY 'icingadb';
GRANT ALL ON icingadb.* TO 'icingadb'@'%';

-- icingaweb2 needs its own database for preferences / auth.
CREATE DATABASE IF NOT EXISTS icingaweb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'icingaweb'@'%' IDENTIFIED BY 'icingaweb';
GRANT ALL ON icingaweb.* TO 'icingaweb'@'%';

FLUSH PRIVILEGES;
