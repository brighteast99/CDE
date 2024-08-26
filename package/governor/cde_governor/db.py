from time import sleep

import pymysql
from cde_governor.crpyto import encrypt_with_salt, verify_with_salt


class Database:
    def __init__(self, host: str, user: str, password: str, database: str):
        self.__host = host
        self.__user = user
        self.__password = password
        self.__database = database

        self.__connection_test()
        self.__setup()

    def __get_connection(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self.__host,
            user=self.__user,
            password=self.__password,
            database=self.__database,
        )

    def __connection_test(self, maximum_retries: int = 10, wait_time: int = 3) -> None:
        tries = 0
        while True:
            tries += 1
            try:
                with self.__get_connection():
                    break
            except pymysql.OperationalError as e:
                sleep(wait_time)
                if tries >= maximum_retries:
                    raise e

    def __setup(self):
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES LIKE 'users'")
            users_table_exists = cursor.fetchone()
            if not users_table_exists:
                cursor.execute(
                    """CREATE TABLE users (
                    id          INT AUTO_INCREMENT,
                    username    TINYTEXT NOT NULL,
                    password    TINYTEXT NOT NULL,
                    PRIMARY KEY(id),
                    UNIQUE(username))"""
                )
                conn.commit()

            cursor.execute("SHOW TABLES LIKE 'servers'")
            servers_table_exists = cursor.fetchone()
            if not servers_table_exists:
                cursor.execute(
                    """CREATE TABLE servers (
                        host    INET4,
                        GPUs    TINYINT UNSIGNED NOT NULL,
                        PRIMARY KEY(host))"""
                )
                conn.commit()

            cursor.execute("SHOW TABLES LIKE 'containers'")
            containers_table_exists = cursor.fetchone()
            if not containers_table_exists:
                cursor.execute(
                    """CREATE TABLE containers (
                        id      VARCHAR(64),
                        server  INET4 NOT NULL,
                        GPU     TINYINT UNSIGNED NOT NULL,
                        user    INT NOT NULL NOT NULL,
                        type    TINYINT UNSIGNED NOT NULL,
                        PRIMARY KEY(server, id),
                        UNIQUE(user, type),
                        FOREIGN KEY(server) REFERENCES servers(host)
                        ON UPDATE CASCADE
                        ON DELETE CASCADE,
                        FOREIGN KEY(user) REFERENCES users(id)
                        ON UPDATE CASCADE
                        ON DELETE CASCADE
                        )"""
                )
                conn.commit()

    def create_user(self, username: str, password: str) -> int:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password) values (%s, %s)",
                (username, encrypt_with_salt(password)),
            )
            conn.commit()
            return cursor.lastrowid

    def auth(self, username: str, password: str) -> bool:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT password, id from users WHERE username=%s", (username,)
            )
            data = cursor.fetchone()
            if data is None:
                return None

            if verify_with_salt(salted_ciphertext=data[0], plaintext=password):
                return data[1]

            return None

    def update_pw(self, username: str, new_password: str) -> None:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET password=%s WHERE username=%s",
                (encrypt_with_salt(new_password), username),
            )
            conn.commit()

    def get_server_of_user(self, user_id: int) -> str:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT server FROM containers WHERE user=%d LIMIT=1", (user_id)
            )
            conn.commit()

    def save_container_info(
        self, id: int, host: str, gpu: int, user_id: int, container_type: int
    ) -> None:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO containrs (id, server, gpu, user, type) values (%s, %s, %d, %d, %d)",
                (id, host, gpu, user_id, container_type),
            )
            conn.commit()

    def get_container(self, user_id: int, container_type: int) -> tuple[str, str]:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT server, id from containers WHERE user=%d, type=%d",
                (user_id, container_type),
            )
            return cursor.fetchone()

    def get_containers(self) -> list[tuple[str, str]]:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT server, id from containers")
            return cursor.fetchall()

    def inspect_container_allocation(self) -> dict[tuple[str, int], int]:
        with self.__get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT server, GPU, COUNT(*) AS container_count FROM containers GROUP BY server, GPU"
            )
            container_counts = cursor.fetchall()
            allocation_info = dict()
            for server, gpu, count in container_counts:
                allocation_info[(server, gpu)] = count

            return allocation_info
