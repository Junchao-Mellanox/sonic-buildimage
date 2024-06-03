import queue
import redis
import time
import threading

CONFIG_DB = 4
SUBSCRIBE_PATTERN = '__keyspace@4__:LOGGER|*'
SUBSCRIBE_TIMEOUT = 1
LOGGER_TABLE_NAME = 'LOGGER'
LOGGER_FIELD_LEVEL = 'LOGLEVEL'
TABLE_SEPARATOR = '|'
MESSAGE_FIELD_CHANNEL = 'channel'
MESSAGE_FIELD_DATA = 'data'
MESSAGE_OP_HSET = 'hset'


def format_table_key(db_key):
    return '{}{}{}'.format(LOGGER_TABLE_NAME, TABLE_SEPARATOR, db_key)


class LoggerConfigHandler:
    log_registry = {}
    lock = threading.Lock()
    config_db = None

    @classmethod
    def register(cls, instance, db_key):
        """Register logger instance. A new logger entry will be added to CONFIG DB LOGGER
        table once logger configuration thread is started.

        Args:
            instance (object): logger instance.
            db_key (str): key of LOGGER table. Usually use the log identifier as the key.
        """
        try:
            with cls.lock:
                if db_key not in cls.log_registry:
                    cls.log_registry[db_key] = [instance]
                    
                    db = cls.get_db()
                    key = format_table_key(db_key)
                    log_level_in_db = db.hget(key, LOGGER_FIELD_LEVEL)
                    if log_level_in_db:
                        # Update log level according to DB data
                        instance.set_min_log_priority(instance.log_priority_from_str(log_level_in_db))
                    else:
                        # Register the db_key to redis if need
                        db.hset(key, LOGGER_FIELD_LEVEL, instance.get_min_log_priority_in_str())
                else:
                    cls.log_registry[db_key].append(instance)
        except Exception as e:
            # Probably redis server is no up, user needs to send a SIGHUP to the daemon
            # to redo the registry
            instance.log_error(f'Failed to register logger instance {db_key} - {e}')

    @classmethod
    def get_db(cls):
        from swsscommon import swsscommon
        if not cls.config_db:
            cls.config_db = swsscommon.DBConnector('CONFIG_DB', 0, False)
        return cls.config_db

    @classmethod
    def refresh_config(cls):
        try:
            with cls.lock:
                db = cls.get_db()
                for db_key, instances in cls.log_registry.items():
                    key = format_table_key(db_key)
                    log_level_in_db = db.hget(key, LOGGER_FIELD_LEVEL)
                    if not log_level_in_db:
                        for instance in instances:
                            db.hset(key, LOGGER_FIELD_LEVEL, instance.get_min_log_priority_in_str())
                    else:
                        for instance in instances:
                            instance.set_min_log_priority(instance.log_priority_from_str(log_level_in_db))

                return True, ''
        except Exception as e:
            return False, str(e)
